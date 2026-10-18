"""KG construction (M2 -> M5): speaker role decision and extraction (M2), canonicalization, temporal KG and anomaly rules.

Input : results/<run>/stt/<feed>/*.jsonl (run_asr.py, Whisper) or --stt-dir data/raw/text (run_asr_moss.py, MOSS)
Output: results/<run>/{turns,extraction,extraction_log,canonical}.jsonl, speaker_roles.json, kg/graph.json, kg/neo4j/,
        alerts.json, summary.json
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
from common.paths import AIRLINES_PATH, KG_BUILD_PATH, ONTOLOGY_PATH, RULES_PATH, STATIC_KB_ROOT, display_path, run_dir
from pipeline.m2_role import decide_roles
from pipeline.m2_extract import LLMExtractor, RuleExtractor, split_extraction
from pipeline.m3_canonicalize import Canonicalizer
from pipeline.m4_graph import GraphBuilder
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


_LLM = {}


def get_llm(args):
    if "llm" not in _LLM:
        from pipeline.llm import load_llm
        _LLM["llm"] = load_llm(args.llm_backend, args.llm_model, args.llm_base_url)
    return _LLM["llm"]


def save_extractions(extractions, out: Path):
    """M2b output -> extraction.jsonl (read by M3) + extraction_log.jsonl (audit / --revalidate)."""
    pairs = [split_extraction(e) for e in extractions]
    write_jsonl([r for r, _ in pairs], out / "extraction.jsonl")
    write_jsonl([log for _, log in pairs], out / "extraction_log.jsonl")


def unsupported(turn):
    return {"tx_id": turn["tx_id"], "speaker": turn["role"], "callsign": None, "instructions": [],
            "backend": "unsupported_lang"}


def build_extractor(args, ontology, callsign_re):
    if args.extractor == "rule":
        return RuleExtractor(callsign_re)
    return LLMExtractor(get_llm(args), ontology, callsign_re, batch_size=args.batch_size)


def main():
    parser = argparse.ArgumentParser(description="Build the event-centric temporal KG from ASR output.")
    parser.add_argument("--run-name", default="katl_gnd")
    parser.add_argument("--stt-from", default=None, help="run name whose stt/ to read (default: --run-name)")
    parser.add_argument("--stt-dir", type=Path, default=None, help="read transcripts from this folder instead (e.g. data/raw/text)")
    parser.add_argument("--role", choices=["llm", "rule"], default="llm",
                        help="M2a for rows with speaker IDs: LLM per speaker (rule vote as fallback) or rule vote only")
    parser.add_argument("--extractor", choices=["llm", "rule"], default="llm")
    parser.add_argument("--llm-backend", choices=["hf", "openai"], default="hf")
    parser.add_argument("--llm-model", default="Qwen/Qwen3-4B-Instruct-2507")
    parser.add_argument("--llm-base-url", default=None)
    parser.add_argument("--batch-size", type=int, default=24)
    parser.add_argument("--region", default="US", choices=["US", "KR", "JP"])
    parser.add_argument("--airport", default="katl", help="static KB name under data/static_kb")
    parser.add_argument("--rb-window", type=float, default=None,
                        help="readback window in seconds (default: configs/kg_build.json)")
    parser.add_argument("--reuse-extraction", action="store_true", help="skip M2 if extraction.jsonl exists")
    parser.add_argument("--revalidate", action="store_true", help="re-apply M2 validation to stored raw LLM output")
    parser.add_argument("--limit", type=int, default=0, help="debug: only the first N segments")
    parser.add_argument("--m2-shard", "--m4-shard", dest="m2_shard", default=None, help="'i/n': run M2 on every n-th turn starting at i, write a part file, exit")
    parser.add_argument("--merge-shards", type=int, default=0, help="merge n M2 part files instead of running M2")
    args = parser.parse_args()

    out = run_dir(args.run_name)
    previous_summary = load_json(out / "summary.json") if (out / "summary.json").exists() else {}
    ontology = load_json(ONTOLOGY_PATH)
    rules = load_json(RULES_PATH)
    build_cfg = load_json(KG_BUILD_PATH)
    args.rb_window = args.rb_window or build_cfg["readback_window_sec"]
    telephony = load_json(AIRLINES_PATH)["telephony"]
    kb = load_json(STATIC_KB_ROOT / f"{args.airport.lower()}.json")
    callsign_re = build_callsign_regex(telephony)
    timings = {}

    # ---------------- M2a: speaker role decision
    t0 = time.perf_counter()
    rows = load_stt_rows(args.stt_dir or (run_dir(args.stt_from or args.run_name) / "stt"))
    n_raw = len(rows)
    rows = [r for r in rows if not r.get("hallucination")]
    n_hall = n_raw - len(rows)
    rows = dedupe_overlaps(rows)
    if args.limit:
        rows = rows[: args.limit]
    # Whisper rows carry a language guess; non-English transmissions skip M2b (MOSS rows have no such field).
    foreign = {r["tx_id"] for r in rows if r.get("lang", "en") != "en" and r.get("lang_prob", 0) >= 0.5}
    role_llm = get_llm(args) if args.role == "llm" and any(r.get("speaker") for r in rows) else None
    turns, speaker_roles = decide_roles(rows, callsign_re, role_llm)
    for t in turns:
        t["_foreign"] = t["tx_id"].split(".")[0] in foreign
    write_jsonl([{k: v for k, v in t.items() if not k.startswith("_")} for t in turns], out / "turns.jsonl")
    if speaker_roles:
        save_json(speaker_roles, out / "speaker_roles.json")
    timings["m2_role_sec"] = round(time.perf_counter() - t0, 1)
    print(f"[M2a] {n_raw} segments ({n_hall} hallucination-flagged) -> {len(rows)} kept -> {len(turns)} transmissions "
          f"(roles: {dict(Counter(t['role'] for t in turns))}, sources: {dict(Counter(t['role_source'].split(':')[0] for t in turns))})")

    # ---------------- M2b: instruction extraction
    t0 = time.perf_counter()
    ext_path = out / "extraction.jsonl"
    extractor_usage = {}
    if args.m2_shard:
        i, n = (int(x) for x in args.m2_shard.split("/"))
        part = [t for k, t in enumerate(turns) if k % n == i and not t["_foreign"]]
        extractor = build_extractor(args, ontology, callsign_re)
        rows = []
        for j in tqdm(range(0, len(part), 256), desc=f"M2 shard {i}/{n}"):
            batch = part[j:j + 256]
            for turn, ext in zip(batch, extractor.extract(batch)):
                rows.append({"tx_id": turn["tx_id"], **ext})
        write_jsonl(rows, out / f"extraction.part{i}-of-{n}.jsonl")
        save_json({"usage": getattr(getattr(extractor, "llm", None), "usage", {}),
                   "sec": round(time.perf_counter() - t0, 1)}, out / f"extraction.part{i}-of-{n}.meta.json")
        print(f"[M2b] shard {i}/{n}: {len(rows)} turns -> extraction.part{i}-of-{n}.jsonl")
        return

    if args.merge_shards:
        by_turn = {}
        usage = {"calls": 0, "input_tokens": 0, "output_tokens": 0, "latency_sec": 0.0}
        shard_secs = []
        for i in range(args.merge_shards):
            for row in iter_jsonl(out / f"extraction.part{i}-of-{args.merge_shards}.jsonl"):
                by_turn[row.get("tx_id") or row["turn_id"]] = row
            meta = load_json(out / f"extraction.part{i}-of-{args.merge_shards}.meta.json")
            for k in usage:
                usage[k] += meta["usage"].get(k, 0)
            shard_secs.append(meta["sec"])
        extractions = []
        for turn in turns:
            extractions.append(by_turn.get(turn["tx_id"]) or unsupported(turn))
        save_extractions(extractions, out)
        extractor_usage = {**usage, "shards": args.merge_shards, "shard_wall_sec": shard_secs}
        print(f"[M2b] merged {args.merge_shards} shards ({len(by_turn)} turns)")
    elif args.reuse_extraction and ext_path.exists():
        extractions = load_jsonl(ext_path)
        if len(extractions) != len(turns):
            raise ValueError("extraction.jsonl does not match the current turns; rerun without --reuse-extraction")
        log_path = out / "extraction_log.jsonl"
        if log_path.exists():  # older runs kept the log fields inside extraction.jsonl
            extractions = [{**log, **e} for e, log in zip(extractions, load_jsonl(log_path))]
        if args.revalidate:
            validator = LLMExtractor(None, ontology, callsign_re)
            extractions = [{"tx_id": t["tx_id"], **e} for t, e in zip(turns, validator.revalidate(turns, extractions))]
        save_extractions(extractions, out)
        print(f"[M2b] reused {display_path(ext_path)}{' (revalidated)' if args.revalidate else ''}")
        extractor_usage = previous_summary.get("llm_usage", {})
    else:
        extractor = build_extractor(args, ontology, callsign_re)
        supported = [t for t in turns if not t["_foreign"]]
        by_turn = {}
        chunk = 256
        for i in tqdm(range(0, len(supported), chunk), desc=f"M2 ({extractor.name})"):
            batch = supported[i:i + chunk]
            for turn, ext in zip(batch, extractor.extract(batch)):
                by_turn[turn["tx_id"]] = ext
        extractions = []
        for turn in turns:
            ext = by_turn.get(turn["tx_id"])
            extractions.append({"tx_id": turn["tx_id"], **ext} if ext else unsupported(turn))
        save_extractions(extractions, out)
        extractor_usage = getattr(getattr(extractor, "llm", None), "usage", {})
    timings["m2_extract_sec"] = round(time.perf_counter() - t0, 1)
    if args.reuse_extraction:
        timings["m2_extract_sec"] = previous_summary.get("timings", {}).get("m2_extract_sec", previous_summary.get("timings", {}).get("m4_sec", timings["m2_extract_sec"]))
    if args.merge_shards:
        timings["m2_extract_sec"] = max(extractor_usage["shard_wall_sec"])
    print(f"[M2b] backends: {dict(Counter(e['backend'] for e in extractions))}")

    # ---------------- M3 + M4: canonicalize and build graph (UTC order)
    t0 = time.perf_counter()
    canon = Canonicalizer(kb, telephony)
    builder = GraphBuilder(kb, ontology, rules, build_cfg, args.region, args.rb_window,
                           airline_country=load_json(AIRLINES_PATH).get("country"))
    canonical = []
    for turn, ext in zip(turns, extractions):
        rec = canon.canonicalize(turn, ext)
        canonical.append(rec)
        builder.add_turn(rec)
    graph = builder.finalize()
    write_jsonl(canonical, out / "canonical.jsonl")
    graph.save(out / "kg" / "graph.json")
    graph.export_neo4j(out / "kg" / "neo4j")
    timings["m3_m4_sec"] = round(time.perf_counter() - t0, 1)

    # ---------------- M5: rules
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
        "transmissions": len(turns),
        "role_sources": dict(Counter(t["role_source"].split(":")[0] for t in turns)),
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
    print(f"[M4] {sum(labels.values())} nodes, {len(graph.edges)} edges; flights={summary['flights']}")
    print(f"[M5] readback: {summary['readback_status']} | alerts: {summary['alerts']}")
    print(f"[done] -> {display_path(out)}")


if __name__ == "__main__":
    main()
