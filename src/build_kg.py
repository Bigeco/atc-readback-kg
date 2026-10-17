"""Stage 2 (M3 -> M7): turn splitting, extraction, canonicalization, temporal KG and anomaly rules.

Input : results/<run>/stt/<feed>/*.jsonl   (from run_asr.py)
Output: results/<run>/{turns,extraction,canonical}.jsonl, kg/graph.json, kg/neo4j/, alerts.json, summary.json
"""
import argparse
import sys
import time
from collections import Counter
from pathlib import Path

from tqdm import tqdm

SRC_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(SRC_ROOT))

from common.atc_text import build_callsign_regex
from common.io_utils import iter_jsonl, load_json, load_jsonl, save_json, write_jsonl
from common.paths import AIRLINES_PATH, ONTOLOGY_PATH, RULES_PATH, STATIC_KB_ROOT, display_path, run_dir
from pipeline.m3_role import split_turns
from pipeline.m4_extract import LLMExtractor, RuleExtractor
from pipeline.m5_canonicalize import Canonicalizer
from pipeline.m6_graph import GraphBuilder
from reasoning.queries import readback_status_counts
from reasoning.rules import detect_anomalies


def load_stt_rows(stt_root: Path):
    rows = []
    for path in sorted(stt_root.rglob("*.jsonl")):
        rows.extend(iter_jsonl(path))
    rows.sort(key=lambda r: r["utc_start_epoch"])
    return rows


def dedupe_overlaps(rows, tolerance: float = 1.5):
    """LiveATC 30-min archives overlap by ~2 minutes; drop segments repeated across consecutive files."""
    kept, last_by_feed = [], {}
    for row in rows:
        prev = last_by_feed.get(row["feed"])
        if prev and row["file"] != prev["file"] and row["utc_start_epoch"] < prev["utc_end_epoch"] - tolerance:
            continue
        kept.append(row)
        last_by_feed[row["feed"]] = row
    return kept


def build_extractor(args, ontology, callsign_re):
    if args.extractor == "rule":
        return RuleExtractor(callsign_re)
    from pipeline.llm import load_llm
    llm = load_llm(args.llm_backend, args.llm_model, args.llm_base_url)
    return LLMExtractor(llm, ontology, callsign_re, batch_size=args.batch_size)


def main():
    parser = argparse.ArgumentParser(description="Build the event-centric temporal KG from ASR output.")
    parser.add_argument("--run-name", default="katl_gnd")
    parser.add_argument("--stt-from", default=None, help="run name whose stt/ to read (default: --run-name)")
    parser.add_argument("--extractor", choices=["llm", "rule"], default="llm")
    parser.add_argument("--llm-backend", choices=["hf", "openai"], default="hf")
    parser.add_argument("--llm-model", default="Qwen/Qwen3-4B-Instruct-2507")
    parser.add_argument("--llm-base-url", default=None)
    parser.add_argument("--batch-size", type=int, default=24)
    parser.add_argument("--region", default="US", choices=["US", "KR", "JP"])
    parser.add_argument("--airport", default="katl", help="static KB name under data/static_kb")
    parser.add_argument("--rb-window", type=float, default=20.0, help="readback window in seconds")
    parser.add_argument("--reuse-extraction", action="store_true", help="skip M4 if extraction.jsonl exists")
    parser.add_argument("--revalidate", action="store_true", help="re-apply M4 validation to stored raw LLM output")
    parser.add_argument("--limit", type=int, default=0, help="debug: only the first N segments")
    parser.add_argument("--m4-shard", default=None, help="'i/n': run M4 on every n-th turn starting at i, write a part file, exit")
    parser.add_argument("--merge-shards", type=int, default=0, help="merge n M4 part files instead of running M4")
    args = parser.parse_args()

    out = run_dir(args.run_name)
    previous_summary = load_json(out / "summary.json") if (out / "summary.json").exists() else {}
    ontology = load_json(ONTOLOGY_PATH)
    rules = load_json(RULES_PATH)
    telephony = load_json(AIRLINES_PATH)["telephony"]
    kb = load_json(STATIC_KB_ROOT / f"{args.airport.lower()}.json")
    callsign_re = build_callsign_regex(telephony)
    timings = {}

    # ---------------- M3: turns
    t0 = time.perf_counter()
    rows = load_stt_rows(run_dir(args.stt_from or args.run_name) / "stt")
    n_raw = len(rows)
    rows = [r for r in rows if not r.get("hallucination")]
    n_hall = n_raw - len(rows)
    rows = dedupe_overlaps(rows)
    if args.limit:
        rows = rows[: args.limit]
    turns = [turn for row in rows for turn in split_turns(row, callsign_re)]
    row_by_tx = {r["tx_id"]: r for r in rows}
    for turn in turns:
        src = row_by_tx[turn["tx_id"]]
        turn.update({k: src[k] for k in ("feed", "icao", "facility_class", "file", "avg_logprob")})
        turn["unsupported_lang"] = src.get("lang", "en") != "en" and src.get("lang_prob", 0) >= 0.5
    write_jsonl(turns, out / "turns.jsonl")
    timings["m3_sec"] = round(time.perf_counter() - t0, 1)
    print(f"[M3] {n_raw} segments ({n_hall} hallucination-flagged) -> {len(rows)} kept -> {len(turns)} turns "
          f"(roles: {dict(Counter(t['role'] for t in turns))})")

    # ---------------- M4: extraction
    t0 = time.perf_counter()
    ext_path = out / "extraction.jsonl"
    extractor_usage = {}
    if args.m4_shard:
        i, n = (int(x) for x in args.m4_shard.split("/"))
        part = [t for k, t in enumerate(turns) if k % n == i and not t["unsupported_lang"]]
        extractor = build_extractor(args, ontology, callsign_re)
        rows = []
        for j in tqdm(range(0, len(part), 256), desc=f"M4 shard {i}/{n}"):
            batch = part[j:j + 256]
            for turn, ext in zip(batch, extractor.extract(batch)):
                rows.append({"turn_id": turn["turn_id"], **ext})
        write_jsonl(rows, out / f"extraction.part{i}-of-{n}.jsonl")
        save_json({"usage": getattr(getattr(extractor, "llm", None), "usage", {}),
                   "sec": round(time.perf_counter() - t0, 1)}, out / f"extraction.part{i}-of-{n}.meta.json")
        print(f"[M4] shard {i}/{n}: {len(rows)} turns -> extraction.part{i}-of-{n}.jsonl")
        return

    if args.merge_shards:
        by_turn = {}
        usage = {"calls": 0, "input_tokens": 0, "output_tokens": 0, "latency_sec": 0.0}
        shard_secs = []
        for i in range(args.merge_shards):
            for row in iter_jsonl(out / f"extraction.part{i}-of-{args.merge_shards}.jsonl"):
                by_turn[row["turn_id"]] = row
            meta = load_json(out / f"extraction.part{i}-of-{args.merge_shards}.meta.json")
            for k in usage:
                usage[k] += meta["usage"].get(k, 0)
            shard_secs.append(meta["sec"])
        extractions = []
        for turn in turns:
            extractions.append(by_turn.get(turn["turn_id"]) or {
                "turn_id": turn["turn_id"], "speaker": turn["role"], "callsign": None, "instructions": [],
                "normalized_text": turn["text"], "edits": [], "backend": "unsupported_lang", "valid_json": True})
        write_jsonl(extractions, ext_path)
        extractor_usage = {**usage, "shards": args.merge_shards, "shard_wall_sec": shard_secs}
        print(f"[M4] merged {args.merge_shards} shards ({len(by_turn)} turns)")
    elif args.reuse_extraction and ext_path.exists():
        extractions = load_jsonl(ext_path)
        if len(extractions) != len(turns):
            raise ValueError("extraction.jsonl does not match the current turns; rerun without --reuse-extraction")
        if args.revalidate:
            validator = LLMExtractor(None, ontology, callsign_re)
            extractions = [{"turn_id": t["turn_id"], **e} for t, e in zip(turns, validator.revalidate(turns, extractions))]
            write_jsonl(extractions, ext_path)
        print(f"[M4] reused {display_path(ext_path)}{' (revalidated)' if args.revalidate else ''}")
        extractor_usage = previous_summary.get("llm_usage", {})
    else:
        extractor = build_extractor(args, ontology, callsign_re)
        supported = [t for t in turns if not t["unsupported_lang"]]
        by_turn = {}
        chunk = 256
        for i in tqdm(range(0, len(supported), chunk), desc=f"M4 ({extractor.name})"):
            batch = supported[i:i + chunk]
            for turn, ext in zip(batch, extractor.extract(batch)):
                by_turn[turn["turn_id"]] = ext
        extractions = []
        for turn in turns:
            ext = by_turn.get(turn["turn_id"]) or {
                "speaker": turn["role"], "callsign": None, "instructions": [], "normalized_text": turn["text"],
                "edits": [], "backend": "unsupported_lang", "valid_json": True}
            extractions.append({"turn_id": turn["turn_id"], **ext})
        write_jsonl(extractions, ext_path)
        extractor_usage = getattr(getattr(extractor, "llm", None), "usage", {})
    timings["m4_sec"] = round(time.perf_counter() - t0, 1)
    if args.reuse_extraction:
        timings["m4_sec"] = previous_summary.get("timings", {}).get("m4_sec", timings["m4_sec"])
    if args.merge_shards:
        timings["m4_sec"] = max(extractor_usage["shard_wall_sec"])
    print(f"[M4] backends: {dict(Counter(e['backend'] for e in extractions))}")

    # ---------------- M5 + M6: canonicalize and build graph (UTC order)
    t0 = time.perf_counter()
    canon = Canonicalizer(kb, telephony)
    builder = GraphBuilder(kb, ontology, rules, args.region, args.rb_window)
    canonical = []
    for turn, ext in zip(turns, extractions):
        rec = canon.canonicalize(turn, ext)
        canonical.append(rec)
        builder.add_turn(rec)
    graph = builder.finalize()
    write_jsonl(canonical, out / "canonical.jsonl")
    graph.save(out / "kg" / "graph.json")
    graph.export_neo4j(out / "kg" / "neo4j")
    timings["m5_m6_sec"] = round(time.perf_counter() - t0, 1)

    # ---------------- M7: rules
    alerts = detect_anomalies(graph, builder.required, set(rules["runway_state_types"]), args.rb_window)
    save_json(alerts, out / "alerts.json")

    labels = Counter(n["label"] for n in graph.nodes.values())
    edge_types = Counter(e["type"] for e in graph.edges)
    ins = [n["props"] for n in graph.nodes_by_label("Instruction").values() if n["props"]["speaker"] == "ATCO"]
    summary = {
        "run_name": args.run_name,
        "airport": kb.get("icao"),
        "region": args.region,
        "extractor": args.extractor,
        "llm_model": args.llm_model if args.extractor == "llm" else None,
        "segments_raw": n_raw,
        "segments_hallucination": n_hall,
        "segments_kept": len(rows),
        "turns": len(turns),
        "roles": dict(Counter(t["speaker"] for t in canonical)),
        "utc_range": [canonical[0]["utc_start_epoch"], canonical[-1]["utc_end_epoch"]] if canonical else None,
        "nodes": dict(labels),
        "edges": dict(edge_types),
        "flights": labels.get("Flight", 0),
        "atco_instructions": len(ins),
        "atco_instruction_types": dict(Counter(p["type"] for p in ins)),
        "confidence_labels": dict(Counter(p["conf_label"] for p in ins)),
        "readback_status": readback_status_counts(graph),
        "alerts": dict(Counter(a["label"] for a in alerts)),
        "callsign_flags": dict(Counter(f for r in canonical for f in r["callsign"]["flags"])),
        "llm_usage": extractor_usage,
        "timings": timings,
    }
    save_json(summary, out / "summary.json")
    print(f"[M6] {sum(labels.values())} nodes, {len(graph.edges)} edges; flights={summary['flights']}")
    print(f"[M7] readback: {summary['readback_status']} | alerts: {summary['alerts']}")
    print(f"[done] -> {display_path(out)}")


if __name__ == "__main__":
    main()
