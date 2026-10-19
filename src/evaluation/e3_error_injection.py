"""E3: detection of injected readback errors (anomaly recall) and false alarms on clean pairs.

Real readback errors occur in under 1% of transmissions (Cardosi 1994), so controlled errors are
injected into pilot readbacks that the pipeline judged MATCH (cf. artificial samples in Helmke et al. 2022).
Text-level injection measures the reasoning layer (M2->M5) without ASR noise, i.e. an upper bound.
"""
import argparse
import random
import re
import sys
from collections import defaultdict
from pathlib import Path

SRC_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SRC_ROOT))

from common.atc_text import LETTER_TO_WORD, PHONETIC, build_callsign_regex
from common.io_utils import load_json, load_jsonl, save_json
from common.paths import AIRLINES_PATH, ONTOLOGY_PATH, RULES_PATH, STATIC_KB_ROOT, display_path, run_dir
from evaluation.metrics import rate
from pipeline.m2_extract import LLMExtractor, RuleExtractor
from pipeline.m3_canonicalize import Canonicalizer
from pipeline.m4_graph import TemporalEventGraph
from common.atc_text import digit_words_to_digits
from reasoning.rules import compare_readback


def swap_side(text, rng, kb):
    m = re.search(r"\b\d{1,2}\s*(?:or\s+)?(left|right)\b", text, re.I)
    if not m:
        return None
    other = "right" if m.group(1).lower() == "left" else "left"
    return text[: m.start(1)] + other + text[m.end(1):]


def swap_taxiway(text, rng, kb):
    words = "|".join(sorted(PHONETIC, key=len, reverse=True))
    # skip phonetic words that start a callsign ("Delta 1848")
    m = next((x for x in re.finditer(r"\b(" + words + r")(?:[\s-]*(\d{1,2}))?\b", text, re.I)
              if not re.match(r"\s*\d{3,}", text[x.end(1):])), None)
    if not m:
        return None
    letter = PHONETIC[m.group(1).lower()]
    taxiways = set(kb.get("taxiways", []))
    if m.group(2):
        number = int(m.group(2))
        for delta in (1, -1, 2):
            candidate = f"{letter}{number + delta}"
            if candidate in taxiways:
                return text[: m.start(2)] + str(number + delta) + text[m.end(2):]
        return text[: m.start(2)] + str(number + 1) + text[m.end(2):]
    choices = sorted({t for t in taxiways if len(t) == 1 and t != letter and t in LETTER_TO_WORD})
    if not choices:
        return None
    return text[: m.start(1)] + LETTER_TO_WORD[rng.choice(choices)].title() + text[m.end(1):]


def change_callsign_digit(text, rng, kb):
    m = re.search(r"(\d)(\D*)$", text)
    if not m:
        return None
    digit = (int(m.group(1)) + rng.randint(1, 8)) % 10
    return text[: m.start(1)] + str(digit) + text[m.end(1):]


def drop_hold_short(text, rng, kb):
    if not re.search(r"hold short", text, re.I):
        return None
    return re.sub(r"hold short(?: of)?[^,.]*[,.]?\s*", "", text, count=1, flags=re.I)


def drop_callsign(text, rng, kb):
    new = re.sub(r",?\s*(?:[A-Za-z]+\s+)?[\d][\d\s-]*(?:heavy)?\W*$", ".", text)
    return new if new != text else None


def change_heading(text, rng, kb):
    m = re.search(r"\b(heading|left|right|turn)[\s,]+([0-3]\d{2})\b", text, re.I)
    if not m:
        return None
    new = (int(m.group(2)) + 20 - 1) % 360 + 1
    return text[: m.start(2)] + f"{new:03d}" + text[m.end(2):]


def change_altitude(text, rng, kb):
    m = re.search(r"\b(\d{1,2})(\s*thousand|,?000)\b", text, re.I)
    if not m:
        return None
    return text[: m.start(1)] + str(int(m.group(1)) + 1) + text[m.end(1):]


PERTURBATIONS = {
    "lr_swap": (swap_side, "MISMATCH"),
    "taxiway_swap": (swap_taxiway, "MISMATCH"),
    "callsign_digit": (change_callsign_digit, "WRONG_FLIGHT"),
    "drop_hold_short": (drop_hold_short, "PARTIAL"),
    "drop_callsign": (drop_callsign, "NO_CALLSIGN"),
    "heading_change": (change_heading, "MISMATCH"),
    "altitude_change": (change_altitude, "MISMATCH"),
}


def evaluate_batch(items, extractor, canon_factory, required):
    """items: list of (pair, text). Extraction is batched so the LLM backend stays fast."""
    extractions = extractor.extract([{"text": text, "role": "PILOT", "tx_id": "inj"} for _, text in items])
    outcomes = []
    for (pair, text), ext in zip(items, extractions):
        canon = canon_factory(pair["atco_flight"], pair["t"])
        rec = canon.canonicalize({**pair["turn"], "text": text, "tx_id": "inj"}, ext)
        result = compare_readback(pair["atco_instructions"], rec["instructions"], required, pair.get("atco_text"))
        outcomes.append({"status": result["status"], "flight": rec["flight_id"], "callsign_flags": rec["callsign"]["flags"]})
    return outcomes


def detected(kind_expect: str, outcome: dict, pair: dict) -> bool:
    if kind_expect == "WRONG_FLIGHT":
        return outcome["flight"] != pair["atco_flight"] or "SIMILAR_CALLSIGN" in outcome["callsign_flags"]
    if kind_expect == "NO_CALLSIGN":
        return "NO_CALLSIGN" in outcome["callsign_flags"]
    if kind_expect == "PARTIAL":
        return outcome["status"] in ("PARTIAL", "MISMATCH")
    return outcome["status"] in ("MISMATCH", "PARTIAL")


def main():
    parser = argparse.ArgumentParser(description="E3: injected readback-error detection.")
    parser.add_argument("--run-name", default="katl_gnd")
    parser.add_argument("--airport", default="katl")
    parser.add_argument("--extractor", choices=["rule", "llm"], default=None,
                        help="default: the extractor used to build the run (from summary.json)")
    parser.add_argument("--llm-model", default="Qwen/Qwen3-4B-Instruct-2507")
    parser.add_argument("--max-pairs", type=int, default=200)
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--only", default=None, help="comma-separated perturbation kinds; skips the control run")
    args = parser.parse_args()

    out = run_dir(args.run_name)
    rng = random.Random(args.seed)
    ontology = load_json(ONTOLOGY_PATH)
    rules = load_json(RULES_PATH)
    telephony = load_json(AIRLINES_PATH)["telephony"]
    kb = load_json(STATIC_KB_ROOT / f"{args.airport}.json")
    callsign_re = build_callsign_regex(telephony)
    required = set(rules["readback_required"][rules["region_rules"]["US"]])
    graph = TemporalEventGraph.load(out / "kg" / "graph.json")
    turns = {t["tx_id"]: t for t in load_jsonl(out / "turns.jsonl")}

    args.extractor = args.extractor or load_json(out / "summary.json").get("extractor", "rule")
    if args.extractor == "rule":
        extractor = RuleExtractor(callsign_re)
    else:
        from pipeline.llm import load_llm
        extractor = LLMExtractor(load_llm("hf", args.llm_model), ontology, callsign_re)

    def canon_factory(atco_flight, t):
        canon = Canonicalizer(kb, telephony)
        canon.mark_active(atco_flight, t - 5)
        return canon

    pairs = []
    for edge in graph.edges_by_type("READBACK_OF"):
        if edge["props"]["status"] != "MATCH":
            continue
        pilot = graph.nodes[edge["src"]]["props"]
        atco_tx = edge["dst"]
        pairs.append({
            "pilot_tx": edge["src"], "atco_tx": atco_tx,
            # MOSS writes numbers as words; perturbations work on digits ("8 left", "Delta 2425")
            "text": digit_words_to_digits(pilot["text"]), "t": pilot["utc_start_epoch"],
            "atco_flight": graph.nodes[atco_tx]["props"]["flight_id"], "atco_text": graph.nodes[atco_tx]["props"]["text"],
            "turn": turns[pilot["tx_id"]],
            "atco_instructions": [{**n["props"], "ins_id": n["id"]} for n in graph.instructions_of(atco_tx)],
        })
    rng.shuffle(pairs)
    pairs = pairs[: args.max_pairs]
    print(f"[E3] {len(pairs)} clean MATCH pairs available")

    kinds = args.only.split(",") if args.only else list(PERTURBATIONS)
    control = [] if args.only else evaluate_batch([(p, p["text"]) for p in pairs], extractor, canon_factory, required)
    false_alarm = sum(c["status"] != "MATCH" for c in control)

    jobs = []
    def applicable(kind, pair):
        # inject only where the controller instruction has the element: a runway side or a taxiway
        ins = pair["atco_instructions"]
        if kind == "lr_swap":
            return any((i.get("target_id") or "").startswith("Runway:") and i["target_id"][-1] in "LR" for i in ins)
        if kind == "taxiway_swap":
            return any(i.get("target_kind") == "Taxiway" or i.get("route_ids") for i in ins)
        return True

    for kind, (fn, expect) in PERTURBATIONS.items():
        if kind not in kinds:
            continue
        for pair in pairs:
            if not applicable(kind, pair):
                continue
            text = fn(pair["text"], rng, kb)
            if text and text != pair["text"]:
                jobs.append((kind, pair, text))
    outcomes = evaluate_batch([(pair, text) for _, pair, text in jobs], extractor, canon_factory, required)

    results, examples, tally = {}, defaultdict(list), defaultdict(lambda: [0, 0])
    for (kind, pair, text), outcome in zip(jobs, outcomes):
        ok = detected(PERTURBATIONS[kind][1], outcome, pair)
        tally[kind][0] += ok
        tally[kind][1] += 1
        if len(examples[kind]) < 4:
            examples[kind].append({"original": pair["text"], "perturbed": text, "outcome": outcome, "detected": ok})
    for kind, (_, expect) in PERTURBATIONS.items():
        if kind in kinds:
            results[kind] = {"expected": expect, **rate(*tally[kind])}

    report = {
        "run_name": args.run_name, "extractor": args.extractor, "n_pairs": len(pairs),
        "control_false_alarm": rate(false_alarm, len(control)),
        "detection": results, "examples": examples,
        "note": "text-level injection on pilot readbacks judged MATCH; upper bound without ASR noise",
    }
    report["control_examples"] = [{"text": p["text"], "outcome": c} for p, c in zip(pairs, control) if c["status"] != "MATCH"][:8]
    report_path = out / "eval" / ("e3_injection.json" if not args.only else f"e3_injection_{'_'.join(kinds)}.json")
    save_json(report, report_path)
    print(f"[E3] control false alarm {report['control_false_alarm']['rate']} (n={len(control)})")
    for kind, r in results.items():
        print(f"[E3] {kind:16s} detection {r['rate']} (95% CI {r['ci95']}, n={r['n']})")
    print(f"[E3] -> {display_path(report_path)}")


if __name__ == "__main__":
    main()
