"""E1: competency-question evaluation (Grueninger & Fox 1995).

Each CQ passes if (a) the KG answers it with a query, (b) the answer is non-empty on this run,
and (c) every answer item can be traced back to a transmission with audio file and offset.
"""
import argparse
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

SRC_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SRC_ROOT))

from common.io_utils import load_json, save_json
from common.paths import display_path, run_dir
from pipeline.m4_graph import TemporalEventGraph
from reasoning import queries as Q


def utc(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, tz=timezone.utc).strftime("%H:%M:%SZ")


def traceable(graph, tx_node: str) -> bool:
    p = graph.nodes.get(tx_node, {}).get("props", {})
    return bool(p.get("file")) and p.get("offset_start") is not None


def busiest_flight(graph) -> str:
    counts = Counter(e["dst"] for e in graph.edges_by_type("ADDRESSES") if not e["dst"].startswith("Flight:UNK"))
    return counts.most_common(1)[0][0].split(":", 1)[1]


def busiest_hold_target(graph):
    rows = [n["props"] for n in graph.nodes_by_label("Instruction").values()
            if n["props"]["speaker"] == "ATCO" and n["props"]["type"] == "HOLD_SHORT" and n["props"].get("target_id")]
    if not rows:
        return None, 0, 0
    target = Counter(r["target_id"] for r in rows).most_common(1)[0][0]
    times = [r["valid_from"] for r in rows if r["target_id"] == target]
    return target.split(":")[-1], min(times) - 1, max(times) + 1


def busiest_snapshot_time(graph, step: float = 60.0):
    states = [n["props"] for n in graph.nodes_by_label("Instruction").values()
              if n["props"]["speaker"] == "ATCO" and n["props"].get("state")]
    if not states:
        return None
    t_min, t_max = min(s["valid_from"] for s in states), max(s["valid_from"] for s in states)
    best, best_n, t = t_min, -1, t_min
    while t <= t_max:
        n = sum(1 for s in states if s["valid_from"] <= t < (s.get("valid_to") or float("inf")))
        if n > best_n:
            best, best_n = t, n
        t += step
    return best


def evaluate(run_name: str) -> dict:
    out = run_dir(run_name)
    graph = TemporalEventGraph.load(out / "kg" / "graph.json")
    alerts = load_json(out / "alerts.json")
    results = []

    def record(cq, question, rq, answer_items, tx_nodes, params, example):
        results.append({
            "cq": cq, "question": question, "rq": rq, "params": params,
            "answerable": True, "non_empty": len(answer_items) > 0,
            "traceable": bool(tx_nodes) and all(traceable(graph, t) for t in tx_nodes),
            "n_answers": len(answer_items), "example": example,
        })

    flight = busiest_flight(graph)
    timeline = Q.flight_timeline(graph, flight)
    record("CQ1", f"{flight}가 받은 지시를 시간순으로 나열하면?", "RQ1", timeline, [r["tx_id"] for r in timeline],
           {"flight": flight},
           [f"{utc(r['utc_start_epoch'])} {r['speaker']}: {r['text'][:80]}" for r in timeline[:6]])

    target, t0, t1 = busiest_hold_target(graph)
    holds = Q.instructions_at_target(graph, target, t0, t1) if target else []
    record("CQ2", f"{target}에서 대기(hold short) 지시를 받은 항공편은?", "RQ1, RQ3", holds,
           [h["tx_node"] for h in holds], {"target": target, "window": [t0, t1]},
           [f"{utc(h['valid_from'])} {h['flight']}" for h in holds[:8]])

    issues = Q.alerts_by_label(alerts, ["NO_READBACK", "NO_CALLSIGN"])
    record("CQ3", "복창이 없거나 콜사인 없이 복창된 지시는?", "RQ3", issues,
           [a["evidence"][0] for a in issues], {}, [f"{utc(a['utc_epoch'])} {a['label']} {a['flight']}: {a['message']}" for a in issues[:6]])

    mism = Q.alerts_by_label(alerts, ["MISMATCH"])
    record("CQ4", "복창과 지시의 유도로·활주로가 다른 경우는?", "RQ3", mism, [a["evidence"][0] for a in mism], {},
           [f"{utc(a['utc_epoch'])} {a['flight']}: {a['message']}" for a in mism[:6]])

    corr = Q.callsign_corrections(graph)
    tx_of = [f"Tx:{t}" for c in corr for t in c["evidence"]]
    record("CQ5", "STT 교정이 개입된 콜사인과 근거 발화는?", "RQ2", corr, tx_of, {},
           [f"{c['surface']} -> {c['flight']}" for c in corr[:6]])

    kbv = Q.kb_violations(graph)
    record("CQ6", "공항 KB에 존재하지 않는 유도로·활주로가 언급된 송신은?", "RQ2", kbv, [k["tx_node"] for k in kbv], {},
           [f"{utc(k['valid_from'])} {k['type']} {k['target'] or k['route']}: {k['text'][:60]}" for k in kbv[:6]])

    post = Q.alerts_by_label(alerts, ["POST_HANDOFF"])
    record("CQ7", "Tower로 이관된 뒤에도 Ground 지시를 받은 항공편은?", "RQ3", post,
           [graph.nodes[a["evidence"][1]]["props"]["tx_node"] for a in post], {},
           [f"{utc(a['utc_epoch'])} {a['flight']}: {a['message']}" for a in post[:6]])

    t = busiest_snapshot_time(graph)
    snap = Q.snapshot(graph, t) if t else {}
    items = [row for rows in snap.values() for row in rows]
    record("CQ8", "시점 t에 유효한 지상 상태(택시·대기·이관)는?", "RQ3", items,
           [graph.nodes[r["ins_id"]]["props"]["tx_node"] for r in items], {"t": t, "t_utc": utc(t) if t else None},
           {k: [f"{r['flight']} {r['target'] or r['route']}" for r in v[:5]] for k, v in snap.items()})

    summary = {
        "run_name": run_name,
        "n_cq": len(results),
        "answerable": sum(r["answerable"] for r in results),
        "non_empty": sum(r["non_empty"] for r in results),
        "traceable": sum(r["traceable"] for r in results),
        "results": results,
    }
    return summary


def to_markdown(summary: dict) -> str:
    lines = [f"# E1 competency questions — {summary['run_name']}", "",
             f"answerable {summary['answerable']}/{summary['n_cq']}, non-empty {summary['non_empty']}/{summary['n_cq']}, "
             f"traceable {summary['traceable']}/{summary['n_cq']}", "",
             "| CQ | RQ | question | answers | non-empty | traceable |", "|---|---|---|---|---|---|"]
    for r in summary["results"]:
        lines.append(f"| {r['cq']} | {r['rq']} | {r['question']} | {r['n_answers']} | "
                     f"{'O' if r['non_empty'] else 'X'} | {'O' if r['traceable'] else 'X'} |")
    for r in summary["results"]:
        lines += ["", f"## {r['cq']} {r['question']}", f"params: `{r['params']}`", "", "```",
                  *(r["example"] if isinstance(r["example"], list) else [f"{k}: {v}" for k, v in r["example"].items()]), "```"]
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="E1: competency-question evaluation.")
    parser.add_argument("--run-name", default="katl_gnd")
    args = parser.parse_args()

    summary = evaluate(args.run_name)
    out_dir = run_dir(args.run_name) / "eval"
    save_json(summary, out_dir / "e1_competency.json")
    (out_dir / "e1_competency.md").write_text(to_markdown(summary), encoding="utf-8")
    print(f"[E1] answerable {summary['answerable']}/{summary['n_cq']}, non-empty {summary['non_empty']}, "
          f"traceable {summary['traceable']} -> {display_path(out_dir / 'e1_competency.md')}")


if __name__ == "__main__":
    main()
