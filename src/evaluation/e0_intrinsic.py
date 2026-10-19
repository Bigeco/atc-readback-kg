"""E0: intrinsic KG quality without gold labels.

Text2KGBench (Mihindukulasooriya et al., ISWC 2023) style metrics on the M2b extraction:
- OC  ontology conformance: share of LLM output instructions whose type is one of the ontology's command types and,
      if a target is given, whose target kind is an allowed target class of that type (domain/range check).
- RH  relation hallucination: LLM instruction types outside the ontology.
- SH  subject hallucination: LLM callsign whose digits do not occur in the transmission.
- OH  object hallucination: LLM target/route values not found in the transmission (after number normalization).
All four are computed on the raw LLM output (extraction_log.raw_generation), i.e. before M2b validation.

Graph-level checks on the built KG:
- schema validity: every edge's source/target labels match ontology.relation_types (domain/range)
- KB grounding: share of TARGET/VIA edges that point to an entity listed in the static airport KB (in_kb)
- temporal consistency: valid_from <= valid_to on every instruction; READBACK_OF goes from a later PILOT transmission
  to an earlier ATCO transmission of the same flight within the readback window; no flight holds two open states of
  types that supersede each other at the same time.
"""
import argparse
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

SRC_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SRC_ROOT))

from common.atc_text import normalize_text
from common.io_utils import load_json, load_jsonl, save_json
from common.paths import KG_BUILD_PATH, ONTOLOGY_PATH, RULES_PATH, display_path, run_dir
from pipeline.llm import extract_json_block
from pipeline.m2_extract import canonical_taxiway, grounded_in_text
from pipeline.m3_canonicalize import Canonicalizer
from pipeline.m4_graph import TemporalEventGraph


def pct(a, b):
    return {"n": b, "k": a, "rate": round(a / b, 4) if b else None}


def extraction_metrics(turns, logs, ontology, kb):
    allowed = ontology["command_types"]
    canon = Canonicalizer(kb, {})
    n_ins = conform = rel_hall = 0
    n_obj = obj_hall = n_cs = cs_hall = 0
    for t, log in zip(turns, logs):
        raw = extract_json_block(log.get("raw_generation", ""), default={})
        if not raw:
            continue
        # punctuation removed first so "two." counts as a number word
        hay = normalize_text(re.sub(r"[^\w\s]", " ", t["text"]))[0].lower()
        cs = raw.get("callsign")
        if cs:
            n_cs += 1
            digits = re.sub(r"\D", "", str(cs))
            if digits and digits not in re.sub(r"\D", "", hay):
                cs_hall += 1
        for item in raw.get("instructions") or []:
            if not isinstance(item, dict):
                continue
            n_ins += 1
            typ = item.get("type")
            if typ not in allowed:
                rel_hall += 1
                continue
            ok = True
            tgt = item.get("target")
            if tgt:
                kind = canon.ground_target(str(tgt)).get("kind")
                targets = allowed[typ]["targets"]
                if kind and targets and kind not in targets:
                    ok = False
                n_obj += 1
                obj_hall += not grounded_in_text(str(tgt), hay)
            route = item.get("route") or []
            if route and typ != "TAXI_VIA":
                ok = False
            for r in route:
                n_obj += 1
                c = canonical_taxiway(r)
                obj_hall += not (c and grounded_in_text(c, hay))
            conform += ok
    return {"OC": pct(conform, n_ins), "RH": pct(rel_hall, n_ins), "SH": pct(cs_hall, n_cs), "OH": pct(obj_hall, n_obj)}


def graph_metrics(g, ontology, rules, rb_window):
    rel = ontology["relation_types"]
    bad = Counter()
    for e in g.edges:
        dom, rng = rel.get(e["type"], [None, None])
        s, d = g.nodes[e["src"]]["label"], g.nodes[e["dst"]]["label"]
        if dom is None or s not in dom.split("|") or d not in rng.split("|"):
            bad[e["type"]] += 1
    tv = [e for e in g.edges if e["type"] in ("TARGET", "VIA")]
    in_kb = sum(1 for e in tv if g.nodes[e["dst"]]["props"].get("in_kb", True))

    ins = [n["props"] for n in g.nodes_by_label("Instruction").values()]
    bad_interval = sum(1 for p in ins if p.get("valid_to") is not None and p["valid_to"] < p["valid_from"])
    rb = g.edges_by_type("READBACK_OF")
    bad_rb = 0
    for e in rb:
        p, a = g.nodes[e["src"]]["props"], g.nodes[e["dst"]]["props"]
        gap = p["utc_start_epoch"] - a["utc_end_epoch"]
        if not (p["speaker"] == "PILOT" and a["speaker"] == "ATCO" and p.get("flight_id") == a.get("flight_id")
                and -1 <= gap <= rb_window):
            bad_rb += 1
    sup = rules["supersedes"]
    by_flight = defaultdict(list)
    for p in ins:
        if p.get("state") and p.get("flight_id"):
            by_flight[p["flight_id"]].append(p)
    overlap = 0
    for items in by_flight.values():
        items.sort(key=lambda p: p["valid_from"])
        for i, a in enumerate(items):
            for b in items[i + 1:]:
                if b["valid_from"] >= a["valid_to"]:
                    break
                if a["type"] in sup.get(b["type"], []) or "*" in sup.get(b["type"], []):
                    overlap += 1
    n_state = sum(len(v) for v in by_flight.values())
    return {
        "schema_valid_edges": pct(len(g.edges) - sum(bad.values()), len(g.edges)),
        "schema_violations_by_type": dict(bad),
        "kb_grounded_target_via": pct(in_kb, len(tv)),
        "valid_interval": pct(len(ins) - bad_interval, len(ins)),
        "readback_edge_consistent": pct(len(rb) - bad_rb, len(rb)),
        "superseded_state_overlap": {"pairs": overlap, "state_instructions": n_state},
    }


def main():
    parser = argparse.ArgumentParser(description="E0: intrinsic KG quality (Text2KGBench-style + graph checks).")
    parser.add_argument("--run-name", required=True)
    parser.add_argument("--airport", default="katl")
    args = parser.parse_args()
    out = run_dir(args.run_name)
    ontology, rules = load_json(ONTOLOGY_PATH), load_json(RULES_PATH)
    from common.paths import STATIC_KB_ROOT
    kb = load_json(STATIC_KB_ROOT / f"{args.airport}.json")
    turns, logs = load_jsonl(out / "turns.jsonl"), load_jsonl(out / "extraction_log.jsonl")
    g = TemporalEventGraph.load(out / "kg" / "graph.json")
    report = {"extraction_raw_llm": extraction_metrics(turns, logs, ontology, kb),
              "graph": graph_metrics(g, ontology, rules, load_json(KG_BUILD_PATH)["readback_window_sec"])}
    save_json(report, out / "eval" / "e0_intrinsic.json")
    x, gm = report["extraction_raw_llm"], report["graph"]
    print(f"[E0] OC {x['OC']['rate']} RH {x['RH']['rate']} SH {x['SH']['rate']} OH {x['OH']['rate']} | "
          f"schema {gm['schema_valid_edges']['rate']} KB-grounded {gm['kb_grounded_target_via']['rate']} "
          f"interval {gm['valid_interval']['rate']} readback-consistent {gm['readback_edge_consistent']['rate']} "
          f"overlap {gm['superseded_state_overlap']} -> {display_path(out / 'eval' / 'e0_intrinsic.json')}")


if __name__ == "__main__":
    main()
