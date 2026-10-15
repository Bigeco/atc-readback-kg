"""M2: context-biased Whisper ASR with hallucination filtering (and VAD-based segmentation, M1).

Segmentation uses the Silero VAD bundled with faster-whisper. LiveATC raw archives
contain no digital silence between transmissions, so the VAD is the primary splitter.
"""
import subprocess
from typing import Iterable, List, Optional

import numpy as np


SAMPLE_RATE = 16000
HALLUCINATION_PHRASES = {
    "thank you.", "thank you", "thanks for watching.", "thanks for watching", "you", "you.",
    "bye.", "bye", "subtitles by the amara.org community",
}


def load_audio(path: str) -> np.ndarray:
    pcm = subprocess.run(
        ["ffmpeg", "-loglevel", "error", "-i", path, "-ac", "1", "-ar", str(SAMPLE_RATE), "-f", "s16le", "-"],
        capture_output=True, check=True,
    ).stdout
    return np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0


def build_domain_prompt(kb: dict, telephony: Iterable[str], active_callsigns: Iterable[str] = (), max_chars: int = 600) -> str:
    """Initial prompt that biases decoding toward airport vocabulary (lightweight contextual biasing)."""
    from common.atc_text import LETTER_TO_WORD

    facility = {"GND": "Ground", "TWR": "Tower", "DEL": "Clearance", "DEP": "Departure", "APP": "Approach"}
    city = kb.get("city") or (kb.get("name", "").split()[0] if kb.get("name") else "")
    parts = [f"{city} Ground, {city} Tower." if city else "Ground, Tower."]

    airlines = [t.title() for t in telephony][:14]
    parts.append(", ".join(airlines) + ".")

    active = list(active_callsigns)[:10]
    if active:
        parts.append(", ".join(active) + ".")

    letters = sorted({tw[0] for tw in kb.get("taxiways", []) if tw[:1].isalpha()})
    parts.append("Taxi via " + ", ".join(LETTER_TO_WORD[l].title() for l in letters if l in LETTER_TO_WORD) + ".")

    runways = []
    for end in kb.get("runway_ends", []):
        side = {"L": " left", "R": " right", "C": " center"}.get(end[-1], "")
        runways.append(end.rstrip("LRC") + side)
    if runways:
        parts.append("Runway " + ", ".join(runways) + ". Hold short. Give way. Monitor tower.")

    order = {"GND": 0, "TWR": 1, "DEL": 2, "DEP": 3, "APP": 4}
    ranked = sorted((f for f in kb.get("facilities", []) if f.get("type") in order), key=lambda f: order[f["type"]])
    freqs = [f["frequency"] for f in ranked]
    if freqs:
        parts.append(" ".join(freqs[:4]) + ".")

    prompt = " ".join(parts)
    return prompt[:max_chars]


class WhisperTranscriber:
    def __init__(self, model_name: str = "large-v3", device: str = "cuda", compute_type: str = "float16",
                 beam_size: int = 5, min_silence_ms: int = 300, speech_pad_ms: int = 150):
        from faster_whisper import WhisperModel

        self.model = WhisperModel(model_name, device=device, compute_type=compute_type)
        self.beam_size = beam_size
        self.vad_parameters = {"min_silence_duration_ms": min_silence_ms, "speech_pad_ms": speech_pad_ms}

    def transcribe(self, path: str, prompt: str = "", language: Optional[str] = "en",
                   segment_language_id: bool = False) -> dict:
        audio = load_audio(path)
        segments, info = self.model.transcribe(
            audio,
            language=language,
            vad_filter=True,
            vad_parameters=self.vad_parameters,
            beam_size=self.beam_size,
            initial_prompt=prompt or None,
            condition_on_previous_text=False,
        )

        rows: List[dict] = []
        for idx, seg in enumerate(segments):
            text = seg.text.strip()
            row = {
                "seg_idx": idx,
                "start": round(seg.start, 2),
                "end": round(seg.end, 2),
                "text": text,
                "avg_logprob": round(seg.avg_logprob, 3),
                "no_speech_prob": round(seg.no_speech_prob, 3),
                "compression_ratio": round(seg.compression_ratio, 3),
                "lang": info.language,
                "lang_prob": round(info.language_probability, 3),
            }
            if segment_language_id:
                row.update(self._segment_language(audio, seg.start, seg.end))
            row["hallucination"] = is_hallucination(row)
            rows.append(row)

        return {"duration_sec": round(len(audio) / SAMPLE_RATE, 2), "language": info.language, "segments": rows}

    def _segment_language(self, audio: np.ndarray, start: float, end: float) -> dict:
        clip = audio[int(start * SAMPLE_RATE): int(end * SAMPLE_RATE)]
        if len(clip) < SAMPLE_RATE // 2:
            return {}
        try:
            lang, prob, _ = self.model.detect_language(clip)
        except Exception:
            return {}
        return {"lang": lang, "lang_prob": round(prob, 3)}


def is_hallucination(row: dict) -> bool:
    text = row["text"].strip().lower()
    if not text or text in HALLUCINATION_PHRASES:
        return True
    if row["no_speech_prob"] > 0.6 and row["avg_logprob"] < -0.8:
        return True
    if row["compression_ratio"] > 2.6:
        return True
    return False
