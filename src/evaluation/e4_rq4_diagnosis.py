"""E4 (RQ4): cross-region diagnosis with a small hand-labeled gold set.

  template : sample N turns of a run -> gold template JSONL (predictions prefilled, gold fields empty)
  score    : CSR (callsign recognition), CmdF1 (concept-level command F1), WER (if gold_text given)
             and the distribution of m_dagger = first failing module (M1 STT / M2-role / M2-extract / M3 grounding)
  compare  : side-by-side table of several scored runs (e.g. US vs JP vs KR, condition a vs b)

Gold row: {"tx_id", "gold_text", "gold_speaker", "gold_flight", "gold_instructions": [{"type", "target"}]}
"""
import argparse
import random
import sys
from collections import Counter
from pathlib import Path

SRC_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SRC_ROOT))

from common.io_utils import load_json, load_jsonl, save_json, write_jsonl
from common.paths import GOLD_ROOT, display_path, run_dir
from evaluation.metrics import rate, set_f1, word_error_rate


WER_FAIL = 0.3


def _concepts(instructions, key_target="target"):
    out = []
    for inst in instructions:
        if inst.get("type") in ("REQUEST", "ACK"):
            continue
        target = inst.get(key_target)
        if target and ":" in str(target):
            target = str(target).split(":")[-1]
        out.append((inst["type"], (target or "").upper() or None))
    return out


def template(args):
    canonical = load_jsonl(run_dir(args.run_name) / "canonical.jsonl")
    rng = random.Random(args.seed)
    candidates = [c for c in canonical if len(c["text"].split()) >= 3]
    picked = sorted(rng.sample(candidates, min(args.n, len(candidates))), key=lambda c: c["utc_start_epoch"])
    rows = [{
        "tx_id": c["tx_id"], "file": c["file"], "offset_start": c["start"], "offset_end": c["end"],
        "pred_text": c["text"], "pred_speaker": c["speaker"], "pred_flight": c["flight_id"],
        "pred_instructions": [{"type": i["type"], "target": (i["target_id"] or "").split(":")[-1] or None}
                              for i in c["instructions"]],
        "gold_text": "", "gold_speaker": "", "gold_flight": "", "gold_instructions": [],
    } for c in picked]
    out = GOLD_ROOT / f"{args.run_name}_gold_template.jsonl"
    write_jsonl(rows, out)
    print(f"[E4] {len(rows)} turns -> {display_path(out)}; listen to each clip and fill the gold_* fields")


def score(args):
    gold_rows = [g for g in load_jsonl(Path(args.gold)) if g.get("gold_speaker")]
    canonical = {c["tx_id"]: c for c in load_jsonl(run_dir(args.run_name) / "canonical.jsonl")}
    turns = {t["tx_id"]: t for t in load_jsonl(run_dir(args.run_name) / "turns.jsonl")}

    csr_hits, wers, pred_all, gold_all = 0, [], [], []
    failures = Counter()
    per_tx = []
    for g in gold_rows:
        c = canonical.get(g["tx_id"])
        if c is None:
            failures["missing_turn"] += 1
            continue
        pred_concepts = _concepts(c["instructions"], "target_id")
        gold_concepts = _concepts(g.get("gold_instructions", []))
        pred_all += pred_concepts
        gold_all += gold_concepts
        flight_ok = (c["flight_id"] or "") == (g.get("gold_flight") or "")
        csr_hits += flight_ok

        wer = word_error_rate(g["gold_text"], c["text"]) if g.get("gold_text") else None
        if wer is not None:
            wers.append(wer)

        if wer is not None and wer > WER_FAIL:
            stage = "M1-STT"
        elif c["speaker"] != g["gold_speaker"] and turns[g["tx_id"]]["role"] != g["gold_speaker"]:
            stage = "M2-role"
        elif sorted(t for t, _ in pred_concepts) != sorted(t for t, _ in gold_concepts):
            stage = "M2-extract"
        elif not flight_ok or sorted(pred_concepts, key=str) != sorted(gold_concepts, key=str):
            stage = "M3-ground"
        else:
            stage = "OK"
        if c.get("normalized_text") is not None and "UNSUPPORTED_LANG" in c.get("callsign", {}).get("flags", []):
            stage = "M2_LANG"
        failures[stage] += 1
        per_tx.append({"tx_id": g["tx_id"], "m_dagger": stage, "wer": wer, "flight_ok": flight_ok})

    n = len(per_tx)
    report = {
        "run_name": args.run_name, "region": args.region, "condition": args.condition, "gold": display_path(Path(args.gold)),
        "n": n, "CSR": rate(csr_hits, n), "CmdF1": set_f1(pred_all, gold_all),
        "WER_mean": round(sum(wers) / len(wers), 4) if wers else None,
        "m_dagger": {k: round(v / n, 4) for k, v in failures.items()} if n else {},
        "m_dagger_counts": dict(failures), "per_tx": per_tx,
    }
    out = run_dir(args.run_name) / "eval" / f"e4_rq4_{args.region}_{args.condition}.json"
    save_json(report, out)
    print(f"[E4] {args.region}/{args.condition}: CSR {report['CSR']['rate']} CmdF1 {report['CmdF1']['f1']} "
          f"WER {report['WER_mean']} m_dagger {report['m_dagger_counts']} -> {display_path(out)}")


def compare(args):
    rows = []
    for path in args.reports:
        r = load_json(Path(path))
        rows.append(f"| {r['region']} | {r['condition']} | {r['n']} | {r['CSR']['rate']} | {r['CmdF1']['f1']} | "
                    f"{r['WER_mean']} | {r['m_dagger_counts']} |")
    print("| region | condition | n | CSR | CmdF1 | WER | first failing module |\n|---|---|---|---|---|---|---|")
    print("\n".join(rows))


def main():
    parser = argparse.ArgumentParser(description="E4: RQ4 cross-region pipeline diagnosis.")
    parser.add_argument("command", choices=["template", "score", "compare"])
    parser.add_argument("--run-name", default="katl_gnd")
    parser.add_argument("--n", type=int, default=20)
    parser.add_argument("--seed", type=int, default=3)
    parser.add_argument("--gold", default=None)
    parser.add_argument("--region", default="US")
    parser.add_argument("--condition", default="a", choices=["a", "b"], help="a: as-is, b: regional resources swapped")
    parser.add_argument("--reports", nargs="*", default=[])
    args = parser.parse_args()
    {"template": template, "score": score, "compare": compare}[args.command](args)


if __name__ == "__main__":
    main()
