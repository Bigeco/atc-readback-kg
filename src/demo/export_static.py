"""Export the demo as a static site (GitHub Pages): every API response is precomputed into JSON.

    python src/demo/export_static.py --run-name katl_gnd --out-dir results/katl_gnd/site

The site is public, so it is built from the public KG (demo/public_kg.py): no audio, no transcripts
(LiveATC terms forbid redistribution); the page shows the extracted instructions instead of the text.
"""
import argparse
import json
import shutil
import sys
from collections import Counter
from pathlib import Path

SRC_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SRC_ROOT))

from common.paths import display_path, run_dir
from demo.server import STATIC_DIR, DemoState
from reasoning import queries as Q


def dump(obj, path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, separators=(",", ":"))


def strip_paths(obj):
    """Drop local file paths from exported payloads (only offsets are kept)."""
    if isinstance(obj, dict):
        return {k: strip_paths(v) for k, v in obj.items() if k != "file"}
    if isinstance(obj, list):
        return [strip_paths(v) for v in obj]
    return obj


def export(run_name: str, out_dir: Path):
    state = DemoState(run_name, public=True)
    if out_dir.exists():
        shutil.rmtree(out_dir)
    data = out_dir / "data"

    summary = {"run_name": state.run_name, "summary": state.summary, "histogram": state.histogram(),
               "alert_labels": dict(Counter(a["label"] for a in state.alerts)), "eval": state.eval}
    dump(summary, data / "summary.json")

    flights = []
    for k, fid in enumerate(sorted(state.flight_tx)):
        card = state.flight_card(fid)
        card["alerts"] = dict(card["alerts"])
        card["tx_times"] = [state.tx[t]["utc_start_epoch"] for t in state.flight_tx[fid]]
        card["file"] = f"f{k:04d}.json"
        flights.append(card)
        timeline = {"card": {k2: v for k2, v in card.items() if k2 not in ("tx_times", "file")},
                    "timeline": Q.flight_timeline(state.graph, fid), "alerts": state.alerts_by_flight.get(fid, [])}
        dump(strip_paths({"timeline": timeline, "graph": Q.flight_subgraph(state.graph, fid)}), data / "flight" / card["file"])
    dump(flights, data / "flights.json")

    states = []
    for nid, node in state.graph.nodes_by_label("Instruction").items():
        p = node["props"]
        if p["speaker"] == "ATCO" and p.get("state"):
            states.append({"type": p["type"], "flight": p.get("flight_id"), "target": (p.get("target_id") or "").split(":")[-1] or None,
                           "route": [r.split(":")[-1] for r in p.get("route_ids", [])], "value": p.get("value"),
                           "since": p["valid_from"], "valid_to": p.get("valid_to"), "ins_id": nid})
    dump(states, data / "states.json")
    dump(state.alerts, data / "alerts.json")

    tx = {}
    for tx_id in state.tx:
        view = Q.transmission_view(state.graph, tx_id)
        edge = state.readback_by_tx.get(tx_id)
        view["readback"] = edge
        if edge:
            other = edge["dst"] if edge["src"] == tx_id else edge["src"]
            view["counterpart"] = Q.transmission_view(state.graph, other)
        tx[tx_id] = view
    dump(strip_paths(tx), data / "tx.json")
    # downloadable public KG (graph, Neo4j CSV, alerts, README)
    from demo.public_kg import export as export_public_kg
    export_public_kg(run_name, out_dir / "kg")
    shutil.make_archive(str(out_dir / "atc_readback_kg_public"), "zip", out_dir / "kg")
    shutil.rmtree(out_dir / "kg")

    for name in ("style.css", "app.js", "static_api.js"):
        shutil.copy(STATIC_DIR / name, out_dir / name)
    html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
    html = html.replace('<script src="app.js"></script>',
                        '<script>window.ATC_STATIC = true;</script>\n  <script src="static_api.js"></script>\n  <script src="app.js"></script>')
    (out_dir / "index.html").write_text(html, encoding="utf-8")
    (out_dir / ".nojekyll").write_text("", encoding="utf-8")

    size = sum(f.stat().st_size for f in out_dir.rglob("*") if f.is_file()) / 1e6
    print(f"[export] {len(flights)} flights, {len(tx)} transmissions, {len(state.alerts)} alerts, "
          f"{size:.1f} MB -> {display_path(out_dir)}")


def main():
    parser = argparse.ArgumentParser(description="Export the demo as a static site.")
    parser.add_argument("--run-name", default="katl_gnd")
    parser.add_argument("--out-dir", type=Path, default=None)
    args = parser.parse_args()
    export(args.run_name, args.out_dir or run_dir(args.run_name) / "site")


if __name__ == "__main__":
    main()
