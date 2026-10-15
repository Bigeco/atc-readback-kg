"""M3: text-based speaker role detection and turn splitting.

Cues follow BERTraffic-style text diarization: controllers lead with the callsign,
pilots usually end with it, and pilot calls open with the facility name.
Undecided turns are left as UNKNOWN for the M4 LLM to resolve.
"""
import re
from typing import List

from common.atc_text import find_callsigns, find_runways, find_taxiways


ATCO_VERBS = re.compile(
    r"\b(taxi|hold short|hold position|give way|cross|monitor|contact|follow|pass behind|join|"
    r"continue|turn (left|right)|line up|cleared|expect|runway \d|you'?re number|number \d)\b", re.I)
PILOT_CUES = re.compile(
    r"\b(ready to taxi|request|we'?re|we are|we'?ll|with (information )?[a-z]+$|information [a-z]+|"
    r"roger|wilco|will do|copy|good (morning|afternoon|evening|day)|thank(s| you)|see you|have a good)\b", re.I)
CALL_OPENING = re.compile(r"^(good (morning|afternoon|evening),?\s*)?((atlanta|hartsfield)\s+)?(ground|tower|clearance|ramp)\b", re.I)
GREETING_THEN_CALLSIGN = re.compile(r"^(good (morning|afternoon|evening)|hey|hi|hello),?\s+", re.I)
CONTROLLER_GREETING = re.compile(r"^\W*[a-z ]+[\s,]+[\d\s-]+,?\s*(heavy,?\s*)?((atlanta|on the|line of)\s+)?ground\b", re.I)
SENTENCE_SPLIT = re.compile(r"(?<=[.?!])\s+")


def classify_role(sentence: str, callsign_re) -> tuple:
    text = sentence.strip()
    hits = find_callsigns(text, callsign_re, allow_bare=True)

    if CALL_OPENING.search(text):
        return "PILOT", "opens_with_facility"
    if hits and CONTROLLER_GREETING.search(text):
        return "ATCO", "callsign_then_facility"
    if hits and GREETING_THEN_CALLSIGN.search(text) and hits[0]["pos_ratio"] <= 0.35:
        return "PILOT", "greeting_then_callsign"
    if hits:
        first, last = hits[0], hits[-1]
        after = text[first["end"]:]
        if first["pos_ratio"] <= 0.15 and ATCO_VERBS.search(after):
            return "ATCO", "callsign_first_with_instruction"
        if first["pos_ratio"] <= 0.15 and last is first and (find_taxiways(after) or find_runways(after)):
            return "ATCO", "callsign_first_with_route"
        if last["end_ratio"] >= 0.85 and first["pos_ratio"] > 0.15:
            return "PILOT", "callsign_last"
        if first["pos_ratio"] <= 0.15 and PILOT_CUES.search(text):
            return "PILOT", "callsign_first_pilot_cue"
    elif PILOT_CUES.search(text) and not ATCO_VERBS.search(text):
        return "PILOT", "pilot_cue"
    return "UNKNOWN", "no_rule"


def split_turns(row: dict, callsign_re) -> List[dict]:
    """Split one ASR segment into speaker turns; timestamps are apportioned by character length."""
    sentences = [s for s in SENTENCE_SPLIT.split(row["text"].strip()) if s.strip()]
    if not sentences:
        return []

    labeled = [(s, *classify_role(s, callsign_re)) for s in sentences]
    merged = []
    for sentence, role, source in labeled:
        if merged and (role == "UNKNOWN" or role == merged[-1]["role"] or merged[-1]["role"] == "UNKNOWN"):
            prev = merged[-1]
            prev["text"] = f"{prev['text']} {sentence}"
            if prev["role"] == "UNKNOWN" and role != "UNKNOWN":
                prev["role"], prev["role_source"] = role, source
            continue
        merged.append({"text": sentence, "role": role, "role_source": source})

    total_chars = sum(len(t["text"]) for t in merged) or 1
    duration = row["end"] - row["start"]
    cursor = row["start"]
    turns = []
    for idx, turn in enumerate(merged):
        share = duration * len(turn["text"]) / total_chars
        turns.append({
            "turn_id": f"{row['tx_id']}.{idx}",
            "tx_id": row["tx_id"],
            "text": turn["text"],
            "role": turn["role"],
            "role_source": turn["role_source"],
            "start": round(cursor, 2),
            "end": round(cursor + share, 2),
            "utc_start_epoch": round(row["utc_start_epoch"] - row["start"] + cursor, 2),
            "utc_end_epoch": round(row["utc_start_epoch"] - row["start"] + cursor + share, 2),
            "split": len(merged) > 1,
        })
        cursor += share
    return turns
