"""M3: callsign resolution, value normalization and grounding to the static KB.

Turns are processed in UTC order so that the set of recently active flights
(a lightweight stand-in for surveillance data) can repair callsign errors.
"""
import re
from collections import OrderedDict
from typing import Dict, List, Optional

from rapidfuzz import fuzz, process
from rapidfuzz.distance import Levenshtein

from common.atc_text import PHONETIC, canonical_frequency


FACILITY_ALIASES = {"tower": "TWR", "ground": "GND", "departure": "DEP", "approach": "APP",
                    "clearance": "DEL", "delivery": "DEL", "ramp": "RAMP"}


class Canonicalizer:
    def __init__(self, kb: dict, telephony: Dict[str, str], active_window_sec: int = 900,
                 similar_window_sec: int = 180):
        self.kb = kb
        self.icao = kb.get("icao", "UNKNOWN")
        self.telephony = {k.upper(): v for k, v in telephony.items()}
        self.runway_ends = set(kb.get("runway_ends", []))
        self.taxiways = set(kb.get("taxiways", []))
        self.facilities = kb.get("facilities", [])
        self.active_window = active_window_sec
        self.similar_window = similar_window_sec
        self.active: "OrderedDict[str, float]" = OrderedDict()

    # ---------------------------------------------------------------- callsigns
    def _airline(self, telephony: str) -> Optional[str]:
        telephony = telephony.upper().strip()
        if telephony in self.telephony:
            return self.telephony[telephony]
        match = process.extractOne(telephony, self.telephony.keys(), scorer=fuzz.ratio, score_cutoff=85)
        return self.telephony[match[0]] if match else None

    def _recent(self, now: float, window: float) -> List[str]:
        return [fid for fid, seen in self.active.items() if 0 <= now - seen <= window]

    def resolve_callsign(self, surface: Optional[str], now: float) -> dict:
        result = {"surface": surface, "flight_id": None, "airline": None, "number": None, "flags": [], "candidates": []}
        if not surface:
            result["flags"].append("NO_CALLSIGN")
            return result

        bare = re.match(r"^\s*(\d{2,5})\s*(heavy|super)?\s*$", surface, re.I)
        if bare:
            number = bare.group(1)
            result["number"] = number
            same = [fid for fid in self._recent(now, self.active_window) if re.sub(r"^[A-Z]+", "", fid) == number
                    or fid.endswith(number)]
            if same:
                result["flight_id"] = same[-1]
                result["airline"] = re.sub(r"\d+$", "", same[-1])
                result["candidates"] = same
            else:
                result["flight_id"] = f"UNK{number}"
                result["flags"].append("UNRESOLVED_AIRLINE")
            result["flags"].append("ABBREVIATED")
            return result

        m = re.match(r"^\s*([A-Za-z][A-Za-z ]*?)\s*(\d{1,5})\s*(heavy|super)?\s*$", surface, re.I)
        if not m:
            result["flags"].append("UNPARSED_CALLSIGN")
            return result
        telephony, number = m.group(1).strip(), m.group(2)
        result["number"] = number
        airline = self._airline(telephony)

        if airline is None or telephony.lower() in ("alpha", "alfa"):
            # Misrecognized airline (e.g. "Alpha 265"): recover from recently active flights with the same number.
            same_number = [fid for fid in self._recent(now, self.active_window) if fid.endswith(number)]
            if same_number:
                result["flight_id"] = same_number[-1]
                result["airline"] = re.sub(r"\d+$", "", same_number[-1])
                result["flags"].append("ASR_CORRECTED")
                result["candidates"] = same_number
            else:
                result["flight_id"] = f"UNK{number}"
                result["flags"].append("UNRESOLVED_AIRLINE")
            return result

        result["airline"] = airline
        result["flight_id"] = f"{airline}{number}" if airline != "N" else f"N{number}"

        similar = [fid for fid in self._recent(now, self.similar_window)
                   if fid != result["flight_id"] and fid.startswith(airline)
                   and Levenshtein.distance(fid[len(airline):], number) == 1]
        if similar:
            result["flags"].append("SIMILAR_CALLSIGN")
            result["candidates"] = similar
        return result

    def mark_active(self, flight_id: Optional[str], now: float):
        if flight_id and not flight_id.startswith("UNK"):
            self.active.pop(flight_id, None)
            self.active[flight_id] = now

    # ---------------------------------------------------------------- targets
    def ground_target(self, value: Optional[str], kind_hint: Optional[str] = None) -> dict:
        if not value:
            return {"id": None, "kind": None, "text": None, "flags": []}
        text = str(value).strip()
        compact = re.sub(r"\b(left|right|center)\b", lambda m: m.group(1)[0].upper(), text, flags=re.I)

        rw = re.fullmatch(r"(?:runway\s*)?(\d{1,2})\s*([LRC])?", compact, re.I)
        if rw:
            end = str(int(rw.group(1))) + (rw.group(2) or "").upper()
            flags = [] if end in self.runway_ends else ["KB_VIOLATION"]
            return {"id": f"Runway:{self.icao}:{end}", "kind": "Runway", "text": text, "flags": flags}

        ramp = re.fullmatch(r"ramp\s*(\d+)", text, re.I)
        if ramp:
            return {"id": f"Ramp:{self.icao}:{ramp.group(1)}", "kind": "Ramp", "text": text, "flags": []}

        fac = FACILITY_ALIASES.get(text.lower())
        if fac:
            return {"id": f"Facility:{self.icao}:{fac}", "kind": "Facility", "text": text, "flags": []}

        tw = re.fullmatch(r"(?:taxiway\s+)?([A-Za-z]+)\s*-?\s*(\d{0,2})", text, re.I)
        if tw:
            head = tw.group(1)
            letters = PHONETIC.get(head.lower()) or (head.upper() if len(head) <= 2 else None)
            if letters:
                name = letters + tw.group(2)
                flags = [] if name in self.taxiways else ["KB_VIOLATION"]
                return {"id": f"Taxiway:{self.icao}:{name}", "kind": "Taxiway", "text": text, "flags": flags}

        return {"id": None, "kind": None, "text": text, "flags": ["UNGROUNDED"]}

    def facility_for(self, inst: dict) -> dict:
        target = self.ground_target(inst.get("target")) if inst.get("target") else {"id": None, "flags": []}
        freq = canonical_frequency(inst.get("value") or "") or (inst.get("value") if re.fullmatch(r"1\d{2}\.\d+", inst.get("value") or "") else None)
        fac_type = target["id"].split(":")[-1] if target.get("id") else None
        known = [f for f in self.facilities if freq and f["frequency"].startswith(freq)]
        if known:
            fac_type = known[0]["type"]
        if not fac_type:
            return {"id": None, "kind": None, "text": inst.get("target"), "flags": ["UNGROUNDED"], "frequency": freq}
        flags = [] if (not freq or known) else ["FREQ_NOT_IN_KB"]
        return {"id": f"Facility:{self.icao}:{fac_type}", "kind": "Facility", "text": inst.get("target"),
                "flags": flags, "frequency": freq}

    # ---------------------------------------------------------------- turns
    def canonicalize(self, turn: dict, extraction: dict) -> dict:
        now = turn["utc_start_epoch"]
        callsign = self.resolve_callsign(extraction.get("callsign"), now)
        if extraction.get("backend") == "unsupported_lang":
            callsign["flags"].append("UNSUPPORTED_LANG")

        instructions = []
        for k, inst in enumerate(extraction.get("instructions", [])):
            if inst["type"] in ("CONTACT", "MONITOR"):
                target = self.facility_for(inst)
            else:
                target = self.ground_target(inst.get("target"))
            route = [self.ground_target(r) for r in inst.get("route", [])]
            flags = list(target.get("flags", [])) + [f for r in route for f in r["flags"]]
            instructions.append({
                "ins_id": f"{turn['turn_id']}:{k}",
                "type": inst["type"],
                "target_id": target.get("id"),
                "target_kind": target.get("kind"),
                "target_text": inst.get("target"),
                "route_ids": [r["id"] for r in route if r["id"]],
                "value": target.get("frequency") or inst.get("value"),
                "qualifier": inst.get("qualifier"),
                "flags": sorted(set(flags)),
            })

        speaker = extraction.get("speaker") if extraction.get("speaker") in ("ATCO", "PILOT") else turn["role"]
        record = {
            **{k: turn[k] for k in ("turn_id", "tx_id", "feed", "icao", "facility_class", "file", "start", "end",
                                     "utc_start_epoch", "utc_end_epoch", "text", "role_source", "avg_logprob")},
            "speaker": speaker,
            "normalized_text": extraction.get("normalized_text"),
            "edits": extraction.get("edits", []),
            "atis": extraction.get("atis"),
            "backend": extraction.get("backend"),
            "callsign": callsign,
            "flight_id": callsign["flight_id"],
            "instructions": instructions,
        }
        self.mark_active(callsign["flight_id"], now)
        return record
