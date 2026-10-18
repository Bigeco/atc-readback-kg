"""M2a speaker role decision (화자 역할 판정): label every transmission ATCO or PILOT.

Two paths, chosen by the M1 output:

- MOSS rows carry a per-file speaker ID (S01, S02 ...). Roles are decided per speaker, not per transmission:
  the LLM reads every speaker of one archive with a few of their transmissions and returns one role per
  speaker. A controller addresses many different callsigns and gives instructions; a pilot names one callsign
  (its own), usually at the end, and reads back. When the LLM gives no usable answer for a speaker, the rule
  cues below vote over that speaker's transmissions (role_source = "rule_fallback"). When the LLM says ATCO for a
  speaker who puts the callsign last more often than first, the speaker is relabeled PILOT ("rule_override").
- Whisper rows have no speaker ID. Each transmission is labeled by the rule cues and split where the role
  changes mid-segment (legacy path, kept for comparison).

Output row (one per transmission): tx_id, speaker, role, role_source, text, start, end, utc_start_epoch,
utc_end_epoch, feed, icao, facility_class, file. Speaker-level decisions go to speaker_roles.json.
"""
import json
import re
from collections import Counter, defaultdict
from typing import Dict, List

from common.atc_text import digit_words_to_digits, find_callsigns, find_runways, find_taxiways
from pipeline.llm import extract_json_block


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

ROW_KEYS = ("feed", "icao", "facility_class", "file")
SAMPLES_PER_SPEAKER = 5

ROLE_SYSTEM_PROMPT = """You label the speakers of one US air traffic control radio frequency recording.
Each speaker ID (S01, S02, ...) is one voice. Decide for every speaker whether it is the controller (ATCO) or a pilot (PILOT).
A frequency has one to three controllers and many pilots, so most speaker IDs are pilots; the controller usually has
by far the most transmissions.

Message format (FAA JO 7110.65BB 2-4-8, AIM 4-2-3):
- ATCO puts the aircraft callsign FIRST, then the instruction: "Delta 2425, taxi via F, hold short 26L".
  Over the recording it calls MANY different callsigns.
- PILOT reads back and puts its own callsign LAST: "taxi via F, hold short 26L, Delta 2425".
  Pilot calls open with the facility: "Atlanta ground, Delta 2425, ready to taxi". Pilots say roger, wilco, request,
  with information Zulu, we are, our left. One pilot uses one callsign.
- Route words (taxi, hold short, runway, Mike two) appear in BOTH, because pilots repeat them. Do not decide by them.

Decide by these, in order: callsign last > callsign first -> PILOT; opens with facility name -> PILOT;
callsign first in most transmissions and several distinct callsigns -> ATCO.

Return JSON only: {"S01": {"role": "ATCO" or "PILOT" or "UNKNOWN", "evidence": "<= 12 words"}, ...} with every speaker ID."""


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


def _row(row: dict, **override) -> dict:
    out = {"tx_id": row["tx_id"], "speaker": row.get("speaker"), "role": None, "role_source": None,
           "text": row["text"], "start": row["start"], "end": row["end"],
           "utc_start_epoch": row["utc_start_epoch"], "utc_end_epoch": row["utc_end_epoch"],
           **{k: row.get(k) for k in ROW_KEYS}}
    out.update(override)
    return out


# ------------------------------------------------------------------ MOSS path: per speaker
def speaker_profiles(rows: List[dict], callsign_re) -> Dict[str, dict]:
    """Group one archive's transmissions by speaker; count distinct callsigns and rule votes per speaker."""
    prof = defaultdict(lambda: {"texts": [], "callsigns": Counter(), "votes": Counter(), "first": 0, "last": 0})
    for r in rows:
        p = prof[r["speaker"]]
        p["texts"].append(r["text"])
        text = digit_words_to_digits(r["text"])  # MOSS writes numbers as words ("Delta twenty four twenty five")
        hits = find_callsigns(text, callsign_re, allow_bare=True)
        for h in hits:
            p["callsigns"][f"{h['telephony']} {h['number']}"] += 1
        if hits:
            p["first"] += hits[0]["pos_ratio"] <= 0.15
            p["last"] += hits[-1]["end_ratio"] >= 0.85 and hits[0]["pos_ratio"] > 0.15
        p["votes"][classify_role(text, callsign_re)[0]] += 1
    return dict(prof)


def role_prompt(file_stem: str, profiles: Dict[str, dict]) -> str:
    lines = [f"Recording: {file_stem}", ""]
    for spk in sorted(profiles):
        p = profiles[spk]
        samples = [digit_words_to_digits(t) for t in sorted(p["texts"], key=len, reverse=True)[:SAMPLES_PER_SPEAKER]]
        lines.append(f"{spk}: {len(p['texts'])} transmissions, {len(p['callsigns'])} distinct callsigns, "
                     f"callsign first in {p['first']}, callsign last in {p['last']}")
        lines += [f"  - {t[:200]}" for t in samples]
    return "\n".join(lines)


def rule_vote(p: dict) -> tuple:
    votes = {k: v for k, v in p["votes"].items() if k != "UNKNOWN"}
    if len(p["callsigns"]) >= 3 and votes.get("ATCO", 0) >= votes.get("PILOT", 0):
        return "ATCO", f"addresses {len(p['callsigns'])} callsigns"
    if not votes:
        return "UNKNOWN", "no rule cue"
    role = max(votes, key=votes.get)
    return role, f"rule votes {dict(votes)}"


def assign_speaker_roles(rows: List[dict], callsign_re, llm=None):
    """rows: transmissions of ONE archive with a 'speaker' field. Returns (labeled rows, speaker_roles)."""
    profiles = speaker_profiles(rows, callsign_re)
    answer = {}
    if llm is not None and profiles:
        raw = llm.generate(ROLE_SYSTEM_PROMPT, [role_prompt(rows[0]["file"].rsplit("/", 1)[-1], profiles)],
                           max_new_tokens=60 + 60 * len(profiles), batch_size=1)[0]
        answer = extract_json_block(raw)

    speakers = {}
    for spk, p in profiles.items():
        got = answer.get(spk) if isinstance(answer.get(spk), dict) else {}
        if got.get("role") == "ATCO" and p["last"] >= 2 and p["last"] > p["first"]:
            # The 4B LLM often calls a pilot "ATCO" because readbacks repeat instructions; the message-format
            # count (callsign last more often than first, FAA 2-4-8) overrides it.
            role, source = "PILOT", "rule_override"
            evidence = f"callsign last {p['last']} > first {p['first']} (LLM said ATCO)"
        elif got.get("role") in ("ATCO", "PILOT"):
            role, source, evidence = got["role"], "llm_speaker", str(got.get("evidence", ""))
        else:
            role, evidence = rule_vote(p)
            source = "rule_fallback"
        speakers[spk] = {"role": role, "role_source": source, "n_tx": len(p["texts"]),
                         "n_callsigns": len(p["callsigns"]), "evidence": evidence}

    labeled = [_row(r, role=speakers[r["speaker"]]["role"], role_source=speakers[r["speaker"]]["role_source"])
               for r in rows]
    return labeled, speakers


# ------------------------------------------------------------------ Whisper path: per transmission (legacy)
def split_turns(row: dict, callsign_re) -> List[dict]:
    """Label one Whisper segment by rule cues; split it where the role changes (timestamps by character share).
    A split segment yields tx_ids '<tx_id>.0', '<tx_id>.1', ..."""
    sentences = [s for s in SENTENCE_SPLIT.split(row["text"].strip()) if s.strip()]
    if not sentences:
        return []

    merged = []
    for sentence in sentences:
        role, source = classify_role(sentence, callsign_re)
        if merged and (role == "UNKNOWN" or role == merged[-1]["role"] or merged[-1]["role"] == "UNKNOWN"):
            prev = merged[-1]
            prev["text"] = f"{prev['text']} {sentence}"
            if prev["role"] == "UNKNOWN" and role != "UNKNOWN":
                prev["role"], prev["role_source"] = role, source
            continue
        merged.append({"text": sentence, "role": role, "role_source": source})

    total_chars = sum(len(t["text"]) for t in merged) or 1
    duration = row["end"] - row["start"]
    offset = row["utc_start_epoch"] - row["start"]
    cursor = row["start"]
    out = []
    for idx, turn in enumerate(merged):
        share = duration * len(turn["text"]) / total_chars
        out.append(_row(row, tx_id=row["tx_id"] if len(merged) == 1 else f"{row['tx_id']}.{idx}",
                        text=turn["text"], role=turn["role"], role_source=f"rule:{turn['role_source']}",
                        start=round(cursor, 2), end=round(cursor + share, 2),
                        utc_start_epoch=round(offset + cursor, 2), utc_end_epoch=round(offset + cursor + share, 2)))
        cursor += share
    return out


def decide_roles(rows: List[dict], callsign_re, llm=None):
    """Speaker-level decision for archives whose rows carry a speaker ID, legacy rule split otherwise.
    Output keeps the input (UTC) order."""
    by_file = defaultdict(list)
    for r in rows:
        if r.get("speaker"):
            by_file[r["file"]].append(r)
    labeled_by_tx, speaker_roles = {}, {}
    for file, file_rows in by_file.items():
        out, speakers = assign_speaker_roles(file_rows, callsign_re, llm)
        labeled_by_tx.update({t["tx_id"]: t for t in out})
        speaker_roles[file] = speakers
    labeled = []
    for r in rows:
        labeled += [labeled_by_tx[r["tx_id"]]] if r["tx_id"] in labeled_by_tx else split_turns(r, callsign_re)
    return labeled, speaker_roles
