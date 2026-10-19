"""Public release of the KG: everything except what reproduces the recordings.

LiveATC terms allow personal, non-commercial use and forbid redistribution, so the public KG drops
  - Transmission.text (the transcript) and the local audio path (only the archive file name is kept),
  - free-text instruction fields: qualifier, target_text, and a value that is not a plain number/code,
  - transcript examples inside the evaluation reports.
It keeps speakers, times, flights, callsign surface forms, instructions, readback judgements, alerts and
the archive name + offsets, so anyone holding the same LiveATC archive can locate each transmission.

    python src/demo/public_kg.py --run-name katl_moss38   ->  results/katl_moss38/public_kg/
"""
import argparse
import re
import shutil
import sys
from pathlib import Path

SRC_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SRC_ROOT))

from common.io_utils import load_json, save_json
from common.paths import display_path, run_dir
from pipeline.m4_graph import TemporalEventGraph

# frequency 119.1, heading 270, altitude 4000 / FL240, squawk 4512, runway 8L, speed 250
STRUCTURED_VALUE = re.compile(r"^(FL\s?\d{2,3}|\d{1,6}(\.\d{1,3})?|\d{1,2}[LRC])$", re.I)


def redact_props(label: str, props: dict) -> dict:
    p = dict(props)
    if label == "Transmission":
        p.pop("text", None)
        if p.get("file"):
            p["file"] = Path(p["file"]).name
    elif label == "Instruction":
        p.pop("qualifier", None)
        p.pop("target_text", None)
        if p.get("value") is not None and not STRUCTURED_VALUE.match(str(p["value"]).strip()):
            p["value"] = None
    return p


def redact_graph(graph: TemporalEventGraph) -> TemporalEventGraph:
    for node in graph.nodes.values():
        node["props"] = redact_props(node["label"], node["props"])
    return graph


def redact_alerts(alerts: list) -> list:
    """POST_HANDOFF messages end with the hand-off value, which can be free text ("73, two o'clock, ...")."""
    out = []
    for a in alerts:
        a = dict(a)
        m = re.match(r"^(.* issued after \S+ \S*) (.*)$", a["message"]) if a["label"] == "POST_HANDOFF" else None
        if m and not STRUCTURED_VALUE.match(m.group(2).strip()):
            a["message"] = m.group(1).rstrip()
        out.append(a)
    return out


def redact_eval(ev: dict) -> dict:
    out = {}
    for name, report in ev.items():
        report = {k: v for k, v in report.items() if k not in ("examples", "control_examples")}
        if "results" in report:
            report["results"] = [{k: v for k, v in r.items() if k != "example"} for r in report["results"]]
        out[name] = report
    return out


README = """# ATC readback temporal KG (public release)

Run `{run}`: KATL (Atlanta) LiveATC archives of 2024-08-26, {n_tx:,} transmissions, {n_flights:,} flights.

Transcripts and audio are **not included**: LiveATC.net terms allow personal, non-commercial use only and forbid
redistribution. Each transmission keeps the archive file name and the offsets (`offset_start`, `offset_end`, seconds),
so the source can be located in the same LiveATC archive (e.g. the Hugging Face dataset `nyuuzyou/liveatc`).

## Files
| file | content |
|---|---|
| `graph.json` | `{{"nodes": [...], "edges": [...]}}`; node = `{{id, label, props}}`, edge = `{{id, type, src, dst, props}}` |
| `neo4j/` | the same graph as CSV + `import.cypher` |
| `alerts.json` | rule alerts (M5) with evidence node ids |
| `summary.json` | node/edge counts, readback status, alert counts |

## Removed fields
- `Transmission.text` (transcript); `Transmission.file` reduced to the archive name
- `Instruction.qualifier`, `Instruction.target_text`, and `Instruction.value` unless it is a number or code
  (frequency, heading, altitude, squawk, runway); the same value is cut from POST_HANDOFF alert messages

## Node types
Transmission, Instruction (time-stamped events); Flight, CallsignMention (entities); Role, Airline, Airport,
Runway, Taxiway, Ramp, Facility (static KB). Edge types: SPOKEN_BY, CONTAINS, ADDRESSES, ALIAS, OPERATED_BY,
TARGET, VIA, PART_OF, NEXT, READBACK_OF (props `status` = MATCH / PARTIAL / MISMATCH).
Times are UTC epoch seconds: `valid_from`/`valid_to` on instructions, `ingested_at` on edges.
"""


def export(run_name: str, out_dir: Path):
    src = run_dir(run_name)
    graph = redact_graph(TemporalEventGraph.load(src / "kg" / "graph.json"))
    if out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True)
    graph.save(out_dir / "graph.json")
    graph.export_neo4j(out_dir / "neo4j")
    save_json(redact_alerts(load_json(src / "alerts.json")), out_dir / "alerts.json")
    summary = load_json(src / "summary.json")
    save_json({k: v for k, v in summary.items() if k in ("airport", "utc_range", "turns", "flights", "nodes", "edges",
                                                          "readback_status", "alerts")}, out_dir / "summary.json")
    n_tx = len(graph.nodes_by_label("Transmission"))
    n_flights = len(graph.nodes_by_label("Flight"))
    (out_dir / "README.md").write_text(README.format(run=run_name, n_tx=n_tx, n_flights=n_flights), encoding="utf-8")
    size = sum(f.stat().st_size for f in out_dir.rglob("*") if f.is_file()) / 1e6
    print(f"[public-kg] {n_tx} transmissions (text removed), {n_flights} flights, {size:.1f} MB -> {display_path(out_dir)}")


def main():
    parser = argparse.ArgumentParser(description="Export the KG without transcripts or audio paths.")
    parser.add_argument("--run-name", default="katl_moss38")
    parser.add_argument("--out-dir", type=Path, default=None)
    args = parser.parse_args()
    export(args.run_name, args.out_dir or run_dir(args.run_name) / "public_kg")


if __name__ == "__main__":
    main()
