"""Stage 1 (M1 + M2): archive discovery, VAD segmentation and context-biased Whisper ASR.

Output: results/<run>/stt/<feed>/<stem>.jsonl, one row per VAD segment.
"""
import argparse
import os
import sys
import time
from collections import Counter
from pathlib import Path

from tqdm import tqdm

SRC_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(SRC_ROOT))

from common.atc_text import build_callsign_regex, find_callsigns
from common.io_utils import load_json, save_json, write_jsonl
from common.paths import AIRLINES_PATH, STATIC_KB_ROOT, display_path, run_dir
from pipeline.m1_ingest import DEFAULT_FACILITIES, discover_archives
from pipeline.m2_asr import WhisperTranscriber, build_domain_prompt


def load_kb(icao: str) -> dict:
    path = STATIC_KB_ROOT / f"{icao.lower()}.json"
    if not path.exists():
        print(f"[M2] warning: no static KB for {icao} ({display_path(path)}); prompt falls back to generic vocabulary")
        return {"icao": icao}
    return load_json(path)


def recent_callsigns(rows, callsign_re, top_k: int = 10):
    counter = Counter()
    for row in rows:
        for hit in find_callsigns(row["text"], callsign_re):
            counter[f"{hit['telephony'].title()} {hit['number']}"] += 1
    return [cs for cs, _ in counter.most_common(top_k)]


def main():
    parser = argparse.ArgumentParser(description="Run M1 (ingest) + M2 (ASR) over LiveATC archives.")
    parser.add_argument("--run-name", default="katl_gnd")
    parser.add_argument("--airports", nargs="+", default=["katl"], help="folder names under data/raw/liveatc")
    parser.add_argument("--facilities", nargs="+", default=list(DEFAULT_FACILITIES))
    parser.add_argument("--model", default="large-v3")
    parser.add_argument("--device", default="cuda")
    parser.add_argument("--compute-type", default="float16")
    parser.add_argument("--language", default="en", help="'auto' enables Whisper language detection (RQ4)")
    parser.add_argument("--segment-lang", action="store_true", help="per-segment language id (RQ4)")
    parser.add_argument("--no-context-carry", action="store_true", help="disable active-callsign prompt feedback")
    parser.add_argument("--limit", type=int, default=0, help="process only the first N archives")
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()

    out_root = run_dir(args.run_name) / "stt"
    archives = discover_archives(args.airports, args.facilities)
    if args.limit:
        archives = archives[: args.limit]
    if not archives:
        print("[M1] nothing to transcribe; check data/raw/liveatc/<airport>/ and --facilities")
        return

    telephony = load_json(AIRLINES_PATH)["telephony"]
    callsign_re = build_callsign_regex(telephony)
    kbs = {}
    carry = {}
    transcriber = WhisperTranscriber(args.model, args.device, args.compute_type)
    language = None if args.language == "auto" else args.language

    manifest = []
    for archive in tqdm(archives, desc="ASR"):
        out_path = out_root / archive["feed"] / f"{archive['stem']}.jsonl"
        manifest.append({**archive, "stt_path": display_path(out_path)})
        if out_path.exists() and not args.overwrite:
            continue

        kb = kbs.setdefault(archive["icao"], load_kb(archive["icao"]))
        active = [] if args.no_context_carry else carry.get(archive["feed"], [])
        prompt = build_domain_prompt(kb, telephony.keys(), active)

        t0 = time.perf_counter()
        result = transcriber.transcribe(archive["path"], prompt, language, args.segment_lang)
        elapsed = time.perf_counter() - t0

        rows = []
        for seg in result["segments"]:
            rows.append({
                "tx_id": f"{archive['stem']}#{seg['seg_idx']:04d}",
                "feed": archive["feed"],
                "icao": archive["icao"],
                "facility_class": archive["facility_class"],
                "file": os.path.relpath(archive["path"], SRC_ROOT.parent),
                "utc_start_epoch": round(archive["utc_start_epoch"] + seg["start"], 2),
                "utc_end_epoch": round(archive["utc_start_epoch"] + seg["end"], 2),
                **seg,
            })
        write_jsonl(rows, out_path)
        carry[archive["feed"]] = recent_callsigns(rows, callsign_re)
        tqdm.write(f"[M2] {archive['file']}: {len(rows)} segments, {result['duration_sec']/60:.1f} min audio, "
                   f"{elapsed:.0f}s, hallucination-flagged {sum(r['hallucination'] for r in rows)}")

    save_json({"run_name": args.run_name, "model": args.model, "language": args.language,
               "context_carry": not args.no_context_carry, "archives": manifest},
              out_root / "manifest.json")
    print(f"[M2] done -> {display_path(out_root)}")


if __name__ == "__main__":
    main()
