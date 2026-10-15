"""E2: precision of KG facts against their evidence transcripts (Team 2-style precision audit).

  sample : stratified sample of facts -> CSV sheet (human fills `label`)
  judge  : optional LLM second opinion -> column `label_llm`
  score  : precision overall / by relation / by confidence label, with Wilson 95% CI

Labels: explicit | partial | unsupported   (precision counts `explicit`; lenient also counts `partial`)
"""
import argparse
import csv
import random
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

SRC_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SRC_ROOT))

from common.io_utils import save_json
from common.paths import display_path, run_dir
from evaluation.metrics import rate
from pipeline.m6_graph import TemporalEventGraph


FIELDS = ["claim_id", "relation", "conf_label", "claim", "evidence", "utc", "audio_file", "offset_start",
          "offset_end", "label", "label_llm", "note"]


def utc(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, tz=timezone.utc).strftime("%H:%M:%SZ")


def _name(node_id):
    return node_id.split(":")[-1] if node_id else None


def collect_claims(graph) -> dict:
    claims = defaultdict(list)
    for node_id, node in graph.nodes_by_label("Instruction").items():
        p = node["props"]
        if p["speaker"] != "ATCO" or p["type"] in ("REQUEST", "ACK"):
            continue
        tx = graph.nodes[p["tx_node"]]["props"]
        base = {"conf_label": p.get("conf_label"), "tx": tx}
        claims["INSTRUCTION_TYPE"].append({**base, "claim": f"The controller issues a {p['type']} instruction."})
        if p.get("target_id"):
            claims["TARGET"].append({**base, "claim": f"{p['type']} refers to {_name(p['target_id'])}."})
        if p.get("route_ids"):
            claims["VIA"].append({**base, "claim": f"The taxi route includes {', '.join(_name(r) for r in p['route_ids'])}."})
    for edge in graph.edges_by_type("ADDRESSES"):
        tx = graph.nodes[edge["src"]]["props"]
        claims["ADDRESSES"].append({"conf_label": "LOW" if set(tx.get("flags", [])) & {"ASR_CORRECTED", "LINKED_BY_CONTEXT"} else None,
                                    "tx": tx, "claim": f"This transmission involves flight {_name(edge['dst'])} "
                                                       f"(spoken as {tx.get('callsign_surface') or 'no callsign'})."})
    for edge in graph.edges_by_type("READBACK_OF"):
        pilot = graph.nodes[edge["src"]]["props"]
        atco = graph.nodes[edge["dst"]]["props"]
        claims["READBACK_OF"].append({
            "conf_label": None, "tx": pilot, "tx2": atco,
            "claim": f"The second transmission is the pilot's readback of the first, and its status is {edge['props']['status']}.",
        })
    return claims


def sample(args):
    graph = TemporalEventGraph.load(run_dir(args.run_name) / "kg" / "graph.json")
    claims = collect_claims(graph)
    rng = random.Random(args.seed)
    rows = []
    for relation, items in sorted(claims.items()):
        picked = rng.sample(items, min(args.per_relation, len(items)))
        for k, c in enumerate(picked):
            tx = c["tx"]
            evidence = f"[{tx['speaker']}] {tx['text']}"
            if c.get("tx2"):
                evidence = f"[{c['tx2']['speaker']}] {c['tx2']['text']}  ||  " + evidence
            rows.append({
                "claim_id": f"{relation}-{k:03d}", "relation": relation, "conf_label": c.get("conf_label") or "",
                "claim": c["claim"], "evidence": evidence, "utc": utc(tx["utc_start_epoch"]),
                "audio_file": tx["file"], "offset_start": tx["offset_start"], "offset_end": tx["offset_end"],
                "label": "", "label_llm": "", "note": "",
            })
    out = run_dir(args.run_name) / "eval" / "e2_audit_sheet.csv"
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    print(f"[E2] {len(rows)} claims ({args.per_relation}/relation) -> {display_path(out)}; fill the `label` column")


def judge(args):
    from pipeline.llm import extract_json_block, load_llm
    from prompt.prompt import EVIDENCE_JUDGE_PROMPT, EVIDENCE_JUDGE_SYS_PROMPT

    path = Path(args.sheet) if args.sheet else run_dir(args.run_name) / "eval" / "e2_audit_sheet.csv"
    rows = list(csv.DictReader(open(path, encoding="utf-8")))
    llm = load_llm(args.llm_backend, args.llm_model, args.llm_base_url)
    prompts = [EVIDENCE_JUDGE_PROMPT.format(claim=r["claim"], evidence=r["evidence"]) for r in rows]
    outputs = llm.generate(EVIDENCE_JUDGE_SYS_PROMPT, prompts, max_new_tokens=200, batch_size=12)
    for row, out in zip(rows, outputs):
        label = extract_json_block(out, {}).get("label", "")
        row["label_llm"] = label if label in ("explicit", "partial", "unsupported") else ""
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    print(f"[E2] LLM judge ({args.llm_model}) labeled {sum(bool(r['label_llm']) for r in rows)}/{len(rows)} -> {display_path(path)}")


def score(args):
    path = Path(args.sheet) if args.sheet else run_dir(args.run_name) / "eval" / "e2_audit_sheet.csv"
    rows = list(csv.DictReader(open(path, encoding="utf-8")))
    report = {"sheet": display_path(path)}
    for column in ("label", "label_llm"):
        labeled = [r for r in rows if r[column] in ("explicit", "partial", "unsupported")]
        if not labeled:
            continue

        def block(subset):
            return {"strict": rate(sum(r[column] == "explicit" for r in subset), len(subset)),
                    "lenient": rate(sum(r[column] in ("explicit", "partial") for r in subset), len(subset))}

        by_relation = defaultdict(list)
        by_conf = defaultdict(list)
        for r in labeled:
            by_relation[r["relation"]].append(r)
            if r["conf_label"]:
                by_conf[r["conf_label"]].append(r)
        report[column] = {"overall": block(labeled),
                          "by_relation": {k: block(v) for k, v in sorted(by_relation.items())},
                          "by_confidence": {k: block(v) for k, v in sorted(by_conf.items())}}
        if column == "label":
            both = [r for r in rows if r["label"] and r["label_llm"]]
            if both:
                report["human_llm_agreement"] = rate(sum(r["label"] == r["label_llm"] for r in both), len(both))

    out = run_dir(args.run_name) / "eval" / "e2_precision.json"
    save_json(report, out)
    for column in ("label", "label_llm"):
        if column in report:
            o = report[column]["overall"]["strict"]
            print(f"[E2] {column}: precision {o['rate']} (95% CI {o['ci95']}, n={o['n']})")
    print(f"[E2] -> {display_path(out)}")


def main():
    parser = argparse.ArgumentParser(description="E2: precision audit against evidence.")
    parser.add_argument("command", choices=["sample", "judge", "score"])
    parser.add_argument("--run-name", default="katl_gnd")
    parser.add_argument("--per-relation", type=int, default=12)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--sheet", default=None)
    parser.add_argument("--llm-backend", default="hf")
    parser.add_argument("--llm-model", default="Qwen/Qwen3.5-9B", help="use a different model from the extractor")
    parser.add_argument("--llm-base-url", default=None)
    args = parser.parse_args()
    {"sample": sample, "judge": judge, "score": score}[args.command](args)


if __name__ == "__main__":
    main()
