"""M1 STT, alternative backend: joint transcription + speaker diarization of whole LiveATC archives with
danieledll/moss-transcribe-diarize-atc (MOSS-Transcribe-Diarize fine-tuned on US ATC radio, 35+ US airports).

Unlike run_asr.py (VAD + Whisper per segment), the model reads a full 30-minute archive and emits
"[start][Sxx] text [end]" segments, so transmission boundaries and speaker labels come from the model.

Output: <out-dir>/<feed>/<stem>.jsonl in the run_asr.py row schema minus the fields MOSS does not produce
(avg_logprob, no_speech_prob, compression_ratio, lang, lang_prob, hallucination), plus "speaker".
build_kg.py treats those fields as optional.

    python src/run_asr_moss.py --airports katl --max-files 50 --out-dir data/raw/text
"""
import argparse
import os
import random
import re
import subprocess
import sys
import time
import zlib
from collections import defaultdict
from pathlib import Path

import numpy as np

SRC_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(SRC_ROOT))

from common.io_utils import save_json, write_jsonl
from common.paths import PROJECT_ROOT, display_path
from pipeline.m1_ingest import DEFAULT_FACILITIES, discover_archives


REPO = "danieledll/moss-transcribe-diarize-atc"
BASE = "OpenMOSS-Team/MOSS-Transcribe-Diarize"
BASE_REV = "e8681d68e7042738ffca8ac8212bc8fcb1131ab8"
# The exact prompt the checkpoint was trained with (model card).
PROMPT = ("Transcribe the audio. For each segment, start with the timestamp and speaker ID "
          "([S01], [S02], [S03], ...), then the spoken text, and end with the segment timestamp.")
# Denoise chain the checkpoint was trained on (model card, section 1).
DENOISE = "highpass=f=200,lowpass=f=3600,afftdn=nr=12:nf=-25,dynaudnorm=f=150:g=15"
SR = 16000
TEMPERATURES = (0.2, 0.5, 0.7, 0.3, 0.6, 0.8)

TS = re.compile(r"\[(\d+(?:\.\d+)?)\]")
SEGMENT = re.compile(r"\[(\d+(?:\.\d+)?)\]\[(S\d+)\](.*?)\[(\d+(?:\.\d+)?)\]", re.DOTALL)


def load_audio(path: str) -> np.ndarray:
    pcm = subprocess.run(["ffmpeg", "-loglevel", "error", "-i", path, "-af", DENOISE, "-ac", "1", "-ar", str(SR),
                          "-f", "s16le", "-"], capture_output=True, check=True).stdout
    return np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0


# ---- loop detection and recovery (model card, section 4)
def has_token_loop(generated, ngram=6, window=240, min_repeats=6):
    if len(generated) < ngram * min_repeats:
        return False
    tail = list(generated[-window:])
    probe = tail[-ngram:]
    return sum(tail[i:i + ngram] == probe for i in range(len(tail) - ngram, -1, -1)) >= min_repeats


def text_stalled(tail: str) -> bool:
    stamps = TS.findall(tail)
    if len(stamps) >= 6 and len(set(stamps[-6:])) == 1:
        return True
    raw = tail.encode()
    return len(raw) >= 600 and len(raw) / len(zlib.compress(raw)) > 4.0


def keep_token_count(pieces):
    text = "".join(pieces)
    keep_end, prev = 0, -1.0
    for m in SEGMENT.finditer(text):
        if float(m.group(1)) < prev:
            break
        prev, keep_end = float(m.group(1)), m.end()
    total = 0
    for i, p in enumerate(pieces):
        total += len(p)
        if total >= keep_end:
            return i + 1
    return len(pieces) if keep_end else 0


class MossTranscriber:
    def __init__(self, device: str = "cuda"):
        import torch
        from transformers import AutoModelForCausalLM, AutoProcessor, StoppingCriteria

        self.torch = torch
        self.device = device
        self.model = AutoModelForCausalLM.from_pretrained(REPO, trust_remote_code=True, dtype="auto") \
            .to(torch.bfloat16).to(device).eval()
        self.processor = AutoProcessor.from_pretrained(BASE, revision=BASE_REV, trust_remote_code=True)
        self.tok = self.processor.tokenizer

        outer = self

        class Stall(StoppingCriteria):
            def __init__(self, prompt_len):
                self.prompt_len, self.stalled, self.step = prompt_len, False, 0

            def __call__(self, input_ids, scores, **kwargs):
                gen = input_ids[0, self.prompt_len:].tolist()
                self.step += 1
                stalled = has_token_loop(gen)
                if not stalled and self.step % 25 == 0:
                    stalled = text_stalled(outer.tok.decode(gen[-400:], skip_special_tokens=True))
                self.stalled = self.stalled or stalled
                return stalled

        self.Stall = Stall

    def transcribe(self, audio: np.ndarray, budget: int = 32768) -> dict:
        torch = self.torch
        messages = [{"role": "user", "content": [{"type": "audio", "audio": ""}, {"type": "text", "text": PROMPT}]}]
        text = self.processor.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)
        with torch.amp.autocast("cuda", dtype=torch.bfloat16):
            inputs = self.processor(text=text, audio=[audio], max_length=131072,
                                    audio_kwargs={"device": self.device}, return_tensors="pt").to(self.device)
        prompt_ids = inputs["input_ids"]
        kept, retries, temp_idx = [], 0, -1
        while budget >= 512:
            ids = torch.cat([prompt_ids, torch.tensor([kept], dtype=prompt_ids.dtype, device=self.device)], 1) \
                if kept else prompt_ids
            stall = self.Stall(prompt_len=prompt_ids.shape[1])
            kwargs = {"do_sample": False} if temp_idx < 0 else {"do_sample": True, "temperature": TEMPERATURES[temp_idx]}
            with torch.inference_mode(), torch.amp.autocast("cuda", dtype=torch.bfloat16):
                out = self.model.generate(
                    input_ids=ids, attention_mask=torch.ones_like(ids),
                    input_features=inputs["input_features"],
                    audio_feature_lengths=inputs["audio_feature_lengths"],
                    audio_chunk_mapping=inputs["audio_chunk_mapping"],
                    max_new_tokens=budget, stopping_criteria=[stall], **kwargs)
            gen = out[0, prompt_ids.shape[1]:].tolist()
            budget -= len(gen) - len(kept)
            if not stall.stalled:
                kept = gen
                break
            pieces = [self.tok.decode([t], skip_special_tokens=True) for t in gen]
            kept = gen[:keep_token_count(pieces)]
            retries += 1
            temp_idx += 1
            if temp_idx >= len(TEMPERATURES):
                break
        transcript = self.tok.decode(kept, skip_special_tokens=True).strip()
        return {"transcript": transcript, "retries": retries}


def parse_segments(transcript: str):
    rows = []
    for m in SEGMENT.finditer(transcript):
        start, spk, txt, end = float(m.group(1)), m.group(2), m.group(3).strip(), float(m.group(4))
        if txt:
            rows.append({"start": round(start, 2), "end": round(max(end, start), 2), "speaker": spk, "text": txt})
    return rows


def busy_enough(archive, min_segments: int, ref_run: Path) -> bool:
    """Skip quiet archives: use segment counts of an earlier STT run when available, else file size."""
    ref = ref_run / "stt" / archive["feed"] / f"{archive['stem']}.jsonl"
    if ref.exists():
        with open(ref) as f:
            return sum(1 for _ in f) >= min_segments
    return os.path.getsize(archive["path"]) > 1_000_000


def select_archives(archives, max_files: int, seed: int = 7, min_segments: int = 40,
                    ref_run: Path = PROJECT_ROOT / "results" / "katl_all"):
    """Stratified pick across facility classes (ground-heavy), skipping quiet archives."""
    quota = {"GND": 0.45, "TWR": 0.2, "DEL": 0.1, "DEP": 0.1, "APP": 0.15}
    by_cls = defaultdict(list)
    for a in archives:
        if busy_enough(a, min_segments, ref_run):
            by_cls[a["facility_class"]].append(a)
    rng = random.Random(seed)
    picked = []
    for cls, share in quota.items():
        pool = sorted(by_cls.get(cls, []), key=lambda a: a["utc_start_epoch"])
        k = min(len(pool), max(1, round(max_files * share)))
        picked += rng.sample(pool, k)
    return sorted(picked, key=lambda a: (a["utc_start_epoch"], a["feed"]))[:max_files]


def main():
    parser = argparse.ArgumentParser(description="Transcribe + diarize LiveATC archives with the MOSS US-ATC model.")
    parser.add_argument("--airports", nargs="+", default=["katl"])
    parser.add_argument("--facilities", nargs="+", default=list(DEFAULT_FACILITIES))
    parser.add_argument("--max-files", type=int, default=30)
    parser.add_argument("--min-segments", type=int, default=40, help="skip archives quieter than this (earlier run)")
    parser.add_argument("--out-dir", type=Path, default=PROJECT_ROOT / "data" / "raw" / "text")
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()

    archives = select_archives(discover_archives(args.airports, args.facilities), args.max_files,
                               min_segments=args.min_segments)
    print(f"[MOSS] {len(archives)} archives selected: "
          f"{dict(sorted(__import__('collections').Counter(a['facility_class'] for a in archives).items()))}")
    model = MossTranscriber()
    manifest = []
    for k, a in enumerate(archives, 1):
        out_path = args.out_dir / a["feed"] / f"{a['stem']}.jsonl"
        manifest.append({**a, "text_path": display_path(out_path)})
        if out_path.exists() and not args.overwrite:
            continue
        t0 = time.perf_counter()
        audio = load_audio(a["path"])
        result = model.transcribe(audio)
        segs = parse_segments(result["transcript"])
        rows = []
        for idx, s in enumerate(segs):
            rows.append({
                "tx_id": f"{a['stem']}#{idx:04d}", "feed": a["feed"], "icao": a["icao"],
                "facility_class": a["facility_class"], "file": os.path.relpath(a["path"], PROJECT_ROOT),
                "utc_start_epoch": round(a["utc_start_epoch"] + s["start"], 2),
                "utc_end_epoch": round(a["utc_start_epoch"] + s["end"], 2),
                "seg_idx": idx, "start": s["start"], "end": s["end"], "speaker": s["speaker"], "text": s["text"],
                "stt_model": REPO,
            })
        write_jsonl(rows, out_path)
        print(f"[MOSS] {k}/{len(archives)} {a['file']}: {len(rows)} segments, "
              f"{len({r['speaker'] for r in rows})} speakers, {len(audio) / SR / 60:.1f} min audio, "
              f"{time.perf_counter() - t0:.0f}s, retries {result['retries']}", flush=True)
    save_json({"model": REPO, "base": f"{BASE}@{BASE_REV}", "denoise": DENOISE, "archives": manifest},
              args.out_dir / "manifest.json")
    print(f"[MOSS] done -> {display_path(args.out_dir)}")


if __name__ == "__main__":
    main()
