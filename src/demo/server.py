"""KG demo server: strip board, time scrubber, readback evidence with audio playback.

    python src/demo/server.py --run-name katl_gnd --port 8800
"""
import argparse
import shutil
import subprocess
import sys
from functools import lru_cache
from collections import Counter, defaultdict
from pathlib import Path

import uvicorn
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import Response
from fastapi.staticfiles import StaticFiles

SRC_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SRC_ROOT))

from common.io_utils import load_json
from common.paths import PROJECT_ROOT, RAW_AUDIO_ROOT, run_dir
from pipeline.m4_graph import TemporalEventGraph
from reasoning import queries as Q


STATIC_DIR = Path(__file__).resolve().parent / "static"


class DemoState:
    def __init__(self, run_name: str):
        out = run_dir(run_name)
        self.run_name = run_name
        self.graph = TemporalEventGraph.load(out / "kg" / "graph.json")
        self.alerts = load_json(out / "alerts.json")
        self.summary = load_json(out / "summary.json")
        self.eval = {}
        for name in ("e1_competency", "e2_precision", "e3_injection"):
            path = out / "eval" / f"{name}.json"
            if path.exists():
                self.eval[name] = load_json(path)

        self.tx = {nid: n["props"] for nid, n in self.graph.nodes_by_label("Transmission").items()}
        self.flight_tx = defaultdict(list)
        for e in self.graph.edges_by_type("ADDRESSES"):
            self.flight_tx[e["dst"].split(":", 1)[1]].append(e["src"])
        for fid in self.flight_tx:
            self.flight_tx[fid].sort(key=lambda t: self.tx[t]["utc_start_epoch"])

        self.readback_by_tx = {}
        for e in self.graph.edges_by_type("READBACK_OF"):
            self.readback_by_tx[e["dst"]] = e
            self.readback_by_tx[e["src"]] = e
        self.alerts_by_flight = defaultdict(list)
        for a in self.alerts:
            self.alerts_by_flight[a.get("flight")].append(a)

    def histogram(self, bin_sec: int = 60):
        start, end = self.summary["utc_range"]
        n_bins = int((end - start) // bin_sec) + 1
        tx_bins, alert_bins = [0] * n_bins, [0] * n_bins
        for p in self.tx.values():
            tx_bins[int((p["utc_start_epoch"] - start) // bin_sec)] += 1
        for a in self.alerts:
            idx = int((a["utc_epoch"] - start) // bin_sec)
            if 0 <= idx < n_bins:
                alert_bins[idx] += 1
        return {"start": start, "end": end, "bin_sec": bin_sec, "tx": tx_bins, "alerts": alert_bins}

    def flight_card(self, fid: str) -> dict:
        txs = self.flight_tx.get(fid, [])
        statuses = Counter(self.readback_by_tx[t]["props"]["status"] for t in txs
                           if t in self.readback_by_tx and self.tx[t]["speaker"] == "ATCO")
        last_ins = None
        for t in reversed(txs):
            if self.tx[t]["speaker"] == "ATCO":
                ins = self.graph.instructions_of(t)
                if ins:
                    last_ins = Q.instruction_view(self.graph, ins[-1]["id"])
                    break
        node = self.graph.nodes.get(f"Flight:{fid}", {}).get("props", {})
        return {
            "flight": fid, "airline": node.get("airline"), "n_tx": len(txs),
            "first": self.tx[txs[0]]["utc_start_epoch"] if txs else None,
            "last": self.tx[txs[-1]]["utc_start_epoch"] if txs else None,
            "readback": dict(statuses), "alerts": Counter(a["label"] for a in self.alerts_by_flight.get(fid, [])),
            "last_instruction": last_ins,
            "surfaces": sorted({self.tx[t].get("callsign_surface") for t in txs if self.tx[t].get("callsign_surface")}),
        }


def create_app(state: DemoState) -> FastAPI:
    app = FastAPI(title="ATC-KG demo")

    @app.get("/api/summary")
    def summary():
        return {"run_name": state.run_name, "summary": state.summary, "histogram": state.histogram(),
                "alert_labels": Counter(a["label"] for a in state.alerts), "eval": state.eval}

    @app.get("/api/flights")
    def flights(t: float = Query(None), window: float = 600, q: str = ""):
        q = q.strip().upper()
        rows = []
        for fid, txs in state.flight_tx.items():
            if q and q not in fid and not any(q in (state.tx[x].get("callsign_surface") or "").upper() for x in txs):
                continue
            if t is not None and not q:
                if not any(abs(state.tx[x]["utc_start_epoch"] - t) <= window for x in txs):
                    continue
            rows.append(state.flight_card(fid))
        rows.sort(key=lambda r: (-(r["last"] or 0) if t is None else abs((r["last"] or 0) - t)))
        return rows[:120]

    @app.get("/api/flight/timeline")
    def flight_timeline(id: str):
        rows = Q.flight_timeline(state.graph, id)
        if not rows:
            raise HTTPException(404, f"unknown flight {id}")
        return {"card": state.flight_card(id), "timeline": rows, "alerts": state.alerts_by_flight.get(id, [])}

    @app.get("/api/flight/graph")
    def flight_graph(id: str):
        return Q.flight_subgraph(state.graph, id)

    @app.get("/api/snapshot")
    def snapshot(t: float):
        return Q.snapshot(state.graph, t)

    @app.get("/api/alerts")
    def alerts(label: str = "", t0: float = 0, t1: float = 1e12):
        labels = set(label.split(",")) if label else None
        return [a for a in state.alerts if (labels is None or a["label"] in labels) and t0 <= a["utc_epoch"] <= t1]

    @app.get("/api/tx")
    def transmission(id: str):
        if id not in state.tx:
            raise HTTPException(404, f"unknown transmission {id}")
        view = Q.transmission_view(state.graph, id)
        edge = state.readback_by_tx.get(id)
        view["readback"] = edge
        if edge:
            other = edge["dst"] if edge["src"] == id else edge["src"]
            view["counterpart"] = Q.transmission_view(state.graph, other)
        return view

    @lru_cache(maxsize=256)
    def cut_clip(path: str, start: float, end: float) -> bytes:
        # ffmpeg cuts the transmission out of the 30-min archive: the browser gets a small, seek-free mp3
        # (LiveATC archives start with non-MP3 bytes, which breaks seeking in some browsers)
        cmd = ["ffmpeg", "-v", "error", "-ss", f"{max(0.0, start - 0.3):.2f}", "-t", f"{end - start + 0.7:.2f}",
               "-i", path, "-ac", "1", "-b:a", "64k", "-f", "mp3", "-"]
        return subprocess.run(cmd, capture_output=True, check=True).stdout

    @app.get("/audio/clip")
    def audio_clip(tx: str):
        p = state.tx.get(tx)
        if not p:
            raise HTTPException(404, f"unknown transmission {tx}")
        target = (PROJECT_ROOT / p["file"]).resolve()
        if RAW_AUDIO_ROOT.resolve() not in target.parents or not target.exists():
            raise HTTPException(404, "audio file not found (data/raw/liveatc is not shared; download it locally)")
        if not shutil.which("ffmpeg"):
            raise HTTPException(500, "ffmpeg is not installed")
        return Response(cut_clip(str(target), p["offset_start"], p["offset_end"]), media_type="audio/mpeg",
                        headers={"Cache-Control": "max-age=3600"})

    app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
    return app


def main():
    parser = argparse.ArgumentParser(description="Serve the ATC-KG demo.")
    parser.add_argument("--run-name", default="katl_gnd")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8800)
    args = parser.parse_args()

    state = DemoState(args.run_name)
    print(f"[demo] {args.run_name}: {len(state.tx)} transmissions, {len(state.flight_tx)} flights, "
          f"{len(state.alerts)} alerts -> http://{args.host}:{args.port}")
    uvicorn.run(create_app(state), host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
