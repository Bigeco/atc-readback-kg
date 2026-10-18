"""M2 (step 2): ontology-grounded extraction of ATC instructions from one speaker turn.

Two backends share one output schema:
- RuleExtractor: deterministic phraseology grammar (baseline, no GPU, used by E3 for speed).
- LLMExtractor : few-shot LLM with JSON output validated against the ontology (default).
"""
import json
import re
from typing import Dict, List, Optional

from common.atc_text import (
    PHONETIC,
    canonical_frequency,
    find_callsigns,
    find_runways,
    find_taxiways,
    normalize_text,
)
from pipeline.llm import extract_json_block
from prompt.prompt import EXTRACTION_EXAMPLES, EXTRACTION_PROMPT, EXTRACTION_SYS_PROMPT


STOP_WORDS = r"(?:hold short|hold position|give way|monitor|contact|follow|pass behind|behind|cross|turn|then|and then)"
FACILITY_RE = re.compile(r"\b(tower|ground|departure|approach|ramp|clearance)\b", re.I)


def _empty_instruction(kind: str) -> dict:
    return {"type": kind, "target": None, "route": [], "value": None, "qualifier": None}


def _first_target(fragment: str) -> Optional[str]:
    runways = find_runways(fragment)
    if runways:
        return runways[0][0]
    ramp = re.search(r"\bramp\s+(\d+)", fragment, re.I)
    if ramp:
        return f"Ramp {ramp.group(1)}"
    taxiways = find_taxiways(fragment)
    if taxiways:
        return taxiways[0][0]
    return None


def _clause(text: str, start: int) -> str:
    rest = text[start:]
    m = re.search(r"[,.;]|\b" + STOP_WORDS + r"\b", rest[1:], re.I)
    return rest if not m else rest[: m.start() + 1]


class RuleExtractor:
    name = "rule"

    def __init__(self, callsign_re):
        self.callsign_re = callsign_re

    def extract_one(self, text: str, role: str) -> dict:
        norm, edits = normalize_text(text)
        hits = find_callsigns(norm, self.callsign_re, allow_bare=True)
        callsign = f"{hits[0]['telephony'].title()} {hits[0]['number']}".strip() if hits else None
        masked = norm
        for hit in sorted(hits, key=lambda h: -h["start"]):
            masked = masked[: hit["start"]] + "CALLSIGN" + masked[hit["end"]:]
        low = masked.lower()

        instructions: List[dict] = []
        consumed = []

        for m in re.finditer(r"\b(?:with\s+)?(?:information|info)[\s,]+([a-z]+)|\bwith\s+(" + "|".join(PHONETIC) + r")\b", low):
            consumed.append((m.start(), m.end()))

        for m in re.finditer(r"\bhold short(?: of)?\s*", low):
            frag = _clause(low, m.end())
            inst = _empty_instruction("HOLD_SHORT")
            inst["target"] = _first_target(frag)
            instructions.append(inst)
            consumed.append((m.start(), m.end() + len(frag)))

        for m in re.finditer(r"\bcross(?:ing)?\s*(?:runway\s*)?", low):
            frag = _clause(low, m.end())
            target = _first_target("runway " + frag if re.match(r"\s*\d", frag) else frag)
            if target and target[0].isdigit():
                inst = _empty_instruction("CROSS_RUNWAY")
                inst["target"] = target
                instructions.append(inst)
                consumed.append((m.start(), m.end() + len(frag)))

        for m in re.finditer(r"\bturn (left|right)\s*(?:on|onto|at|into)?\s*", low):
            frag = _clause(low, m.end())
            inst = _empty_instruction("TURN")
            inst["qualifier"] = m.group(1).upper()
            inst["target"] = _first_target(frag)
            instructions.append(inst)
            consumed.append((m.start(), m.end() + len(frag)))

        if re.search(r"\bhold position\b", low):
            instructions.append(_empty_instruction("HOLD_POSITION"))

        for m in re.finditer(r"\b(monitor|contact)\s+(?:the\s+)?", low):
            frag = low[m.end(): m.end() + 40]
            inst = _empty_instruction(m.group(1).upper())
            fac = FACILITY_RE.search(frag)
            inst["target"] = fac.group(1).title() if fac else None
            inst["value"] = canonical_frequency(frag)
            instructions.append(inst)
            consumed.append((m.start(), m.end() + len(frag)))

        for m in re.finditer(r"\bgive way(?: to)?\s*", low):
            frag = _clause(low, m.end())
            inst = _empty_instruction("GIVE_WAY")
            inst["value"] = frag.strip(" ,.") or None
            instructions.append(inst)
            consumed.append((m.start(), m.end() + len(frag)))

        for m in re.finditer(r"\b(follow|pass behind|behind)\s+", low):
            frag = _clause(low, m.end())
            inst = _empty_instruction("FOLLOW")
            inst["value"] = frag.strip(" ,.") or None
            instructions.append(inst)
            consumed.append((m.start(), m.end() + len(frag)))

        for pattern, kind in [(r"\bline up and wait\b", "LINE_UP_AND_WAIT"),
                              (r"\bclear(?:ed)? (?:for )?take ?off\b", "CLEARED_FOR_TAKEOFF"),
                              (r"\bclear(?:ed)? to land\b", "CLEARED_TO_LAND")]:
            m = re.search(pattern, low)
            if m:
                inst = _empty_instruction(kind)
                runways = find_runways(low)
                inst["target"] = runways[0][0] if runways else None
                instructions.append(inst)

        # TAXI_VIA: taxiway names outside the consumed clauses form the route, a runway/ramp is the destination.
        def outside(pos: int) -> bool:
            return all(not (a <= pos < b) for a, b in consumed)

        route = []
        for tw, start, _ in find_taxiways(low):
            if outside(start) and tw != "CALLSIGN" and (not route or route[-1] != tw):
                route.append(tw)
        destination = None
        for rw, start, _ in find_runways(low):
            if outside(start):
                destination = rw
                break
        if destination is None:
            ramp = re.search(r"\bramp\s+(\d+)", low)
            if ramp and outside(ramp.start()):
                destination = f"Ramp {ramp.group(1)}"
        taxi_cue = re.search(r"\b(taxi|via|join|continue|ramp)\b", low)
        if route or (destination and taxi_cue):
            inst = _empty_instruction("TAXI_VIA")
            inst["route"] = route
            inst["target"] = destination
            instructions.append(inst)

        if re.search(r"\btraffic\b|\bfrom your (left|right)\b|\bat your \d+ o'?clock\b", low):
            instructions.append(_empty_instruction("TRAFFIC_INFO"))
        if re.search(r"\bwake turbulence\b|\bheavy departed\b", low):
            instructions.append(_empty_instruction("WAKE_ADVISORY"))
        if not instructions:
            if re.search(r"\bready to taxi\b|\brequest\b|\binformation [a-z]+\b|\bwith [a-z]+\b", low):
                instructions.append(_empty_instruction("REQUEST"))
            elif re.search(r"\b(roger|wilco|copy|will do)\b", low):
                instructions.append(_empty_instruction("ACK"))

        speaker = role if role in ("ATCO", "PILOT") else ("PILOT" if hits and hits[-1]["end_ratio"] > 0.85 else "UNKNOWN")
        return {"speaker": speaker, "callsign": callsign, "instructions": instructions,
                "edits": edits, "backend": self.name, "valid_json": True}

    def extract(self, turns: List[dict]) -> List[dict]:
        return [self.extract_one(t["text"], t["role"]) for t in turns]


# M2b output (extraction.jsonl) keeps what M3 reads; the extraction record (extraction_log.jsonl) keeps what is
# only needed to audit or re-validate M2b without calling the LLM again (--revalidate reads raw_generation).
RESULT_KEYS = ("tx_id", "callsign", "instructions", "speaker", "backend")
LOG_KEYS = ("tx_id", "edits", "dropped", "rule_added", "valid_json", "raw_generation")


def split_extraction(rec: dict):
    return ({k: rec[k] for k in RESULT_KEYS if k in rec}, {k: rec[k] for k in LOG_KEYS if k in rec})


class LLMExtractor:
    name = "llm"

    def __init__(self, llm, ontology: dict, callsign_re, batch_size: int = 16, max_new_tokens: int = 320):
        self.llm = llm
        self.ontology = ontology
        self.callsign_re = callsign_re
        self.batch_size = batch_size
        self.max_new_tokens = max_new_tokens
        self.allowed = set(ontology["command_types"])
        self.cues = {k: re.compile(v, re.I) for k, v in ontology.get("lexical_cues", {}).items()}
        self.fallback = RuleExtractor(callsign_re)
        types = "\n".join(f"- {k}: {v['description']}" for k, v in ontology["command_types"].items())
        examples = "\n".join(
            f"speaker hint: {ex['role_hint']}\ntransmission: {ex['text']}\noutput: {json.dumps(ex['output'])}"
            for ex in EXTRACTION_EXAMPLES
        )
        self.template = EXTRACTION_PROMPT.replace("{command_types}", types).replace("{examples}", examples)

    def _prompt(self, turn: dict) -> str:
        return self.template.replace("{role_hint}", turn["role"]).replace("{text}", turn["text"])

    def _validate(self, raw: dict, turn: dict) -> dict:
        norm, edits = normalize_text(turn["text"])
        haystack = norm.lower()
        speaker = raw.get("speaker") if raw.get("speaker") in ("ATCO", "PILOT") else turn["role"]
        callsign = raw.get("callsign") if isinstance(raw.get("callsign"), (str, int)) else None
        if callsign is not None:
            callsign = re.sub(r"[^A-Za-z0-9 ]", " ", str(callsign)).strip()
            callsign = re.sub(r"(?<=\d) (?=\d)", "", callsign)
            callsign = re.sub(r"(\d+)[A-Za-z]+$", r"\1", callsign)
            if not re.search(r"\d{2,}", callsign) or callsign.split()[0].lower() in PHONETIC and callsign.split()[0].lower() not in ("alpha", "alfa", "delta"):
                callsign = None

        instructions, dropped = [], []
        for item in raw.get("instructions") or []:
            if not isinstance(item, dict) or item.get("type") not in self.allowed:
                dropped.append({"reason": "unknown_type", "item": item})
                continue
            cue = self.cues.get(item["type"])
            if cue is not None and not cue.search(haystack):
                dropped.append({"reason": "no_lexical_cue", "item": item})
                continue
            inst = _empty_instruction(item["type"])
            for key in ("target", "value", "qualifier"):
                value = item.get(key)
                inst[key] = str(value) if value not in (None, "", []) else None
            inst["route"] = [canonical_taxiway(r) for r in (item.get("route") or []) if r]
            inst["route"] = [r for r in inst["route"] if r]
            if inst["target"] and not (valid_target_shape(inst["target"], haystack)
                                       and grounded_in_text(inst["target"], haystack)):
                dropped.append({"reason": "ungrounded_target", "item": item})
                inst["target"] = None
            inst["route"] = [r for r in inst["route"]
                             if re.fullmatch(r"[A-Z]{1,2}\d{0,2}", r) and grounded_in_text(r, haystack)]
            instructions.append(inst)

        return {"speaker": speaker, "callsign": callsign, "instructions": instructions,
                "edits": edits, "dropped": dropped,
                "backend": self.name, "valid_json": True}

    def _merge_with_rules(self, result: dict, turn: dict) -> dict:
        """Hybrid: keep LLM concepts, add rule-grammar concepts of types the LLM did not produce."""
        rule = self.fallback.extract_one(turn["text"], result["speaker"])
        llm_types = {i["type"] for i in result["instructions"] if i["type"] not in ("REQUEST", "ACK")}
        added = [i for i in rule["instructions"] if i["type"] not in llm_types and i["type"] not in ("REQUEST", "ACK")]
        if added:
            result["instructions"] = [i for i in result["instructions"] if i["type"] not in ("REQUEST", "ACK")] + added
            result["rule_added"] = [i["type"] for i in added]
        if not result.get("callsign") and rule.get("callsign"):
            result["callsign"] = rule["callsign"]
        return result

    def extract(self, turns: List[dict]) -> List[dict]:
        prompts = [self._prompt(t) for t in turns]
        generations = self.llm.generate(EXTRACTION_SYS_PROMPT, prompts, self.max_new_tokens, self.batch_size)
        results = []
        for turn, gen in zip(turns, generations):
            raw = extract_json_block(gen, default={})
            if not raw or "instructions" not in raw:
                fallback = self.fallback.extract_one(turn["text"], turn["role"])
                fallback.update({"backend": "rule_fallback", "valid_json": False, "raw_generation": gen[:400]})
                results.append(fallback)
                continue
            result = self._merge_with_rules(self._validate(raw, turn), turn)
            result["raw_generation"] = gen
            results.append(result)
        return results

    def revalidate(self, turns: List[dict], previous: List[dict]) -> List[dict]:
        """Re-apply validation and hybrid merging to stored raw generations (no LLM call)."""
        results = []
        for turn, prev in zip(turns, previous):
            gen = prev.get("raw_generation", "")
            raw = extract_json_block(gen, default={})
            if not raw or "instructions" not in raw:
                fallback = self.fallback.extract_one(turn["text"], turn["role"])
                fallback.update({"backend": "rule_fallback", "valid_json": False, "raw_generation": gen})
                results.append(fallback)
                continue
            result = self._merge_with_rules(self._validate(raw, turn), turn)
            result["raw_generation"] = gen
            results.append(result)
        return results


def canonical_taxiway(value) -> Optional[str]:
    m = re.fullmatch(r"\s*(?:taxiway\s+)?([A-Za-z]+)[\s-]*(\d{0,2})\s*", str(value))
    if not m:
        return None
    head = m.group(1)
    letters = PHONETIC.get(head.lower()) or (head.upper() if len(head) <= 2 else None)
    return letters + m.group(2) if letters else None


def valid_target_shape(value: str, haystack: str) -> bool:
    v = value.strip()
    rw = re.fullmatch(r"(?:runway\s*)?(\d{1,2})\s*([LRC]|left|right|center)?", v, re.I)
    if rw:
        return int(rw.group(1)) <= 36 and (rw.group(2) is not None or "runway" in haystack)
    if re.fullmatch(r"ramp\s*\d+", v, re.I):
        return True
    if v.lower() in ("tower", "ground", "departure", "approach", "clearance", "ramp"):
        return True
    m = re.fullmatch(r"([A-Za-z]+)[\s-]*(\d{0,2})", v)
    return bool(m) and (len(m.group(1)) <= 2 or m.group(1).lower() in PHONETIC)


def grounded_in_text(value: str, haystack: str) -> bool:
    """True if a canonical target (runway/taxiway/ramp/facility/frequency) is supported by the transcript."""
    v = value.strip()
    if not v:
        return False
    if v.lower() in haystack:
        return True
    if re.fullmatch(r"\d{1,2}[LRC]?", v, re.I):
        return any(rw.upper() == v.upper() or rw.rstrip("LRC") == v.rstrip("LRC").upper()
                   for rw, _, _ in find_runways(haystack)) or v.rstrip("LRC") in re.findall(r"\d+", haystack)
    if re.fullmatch(r"ramp \d+", v, re.I):
        return v.lower() in haystack
    if re.fullmatch(r"[A-Z]{1,2}\d{0,2}", v, re.I):
        return any(tw.upper() == v.upper() for tw, _, _ in find_taxiways(haystack)) or \
            v.upper() in {tw.upper() for tw, _, _ in find_taxiways(haystack)} or \
            v[0].upper() in {PHONETIC[w] for w in PHONETIC if w in haystack}
    if re.fullmatch(r"1?\d{2}\.\d{1,3}", v):
        return canonical_frequency(haystack) == v or v.split(".")[-1] in haystack
    return v.split()[0].lower() in haystack
