"""Text normalization helpers shared by extraction (M2) and canonicalization (M3).

Every rewrite is recorded in an edit log so that the knowledge graph can keep
the raw transcript as provenance next to the normalized concept.
"""
import re
from typing import Dict, List, Optional, Tuple


PHONETIC = {
    "alpha": "A", "alfa": "A", "bravo": "B", "charlie": "C", "delta": "D", "echo": "E",
    "foxtrot": "F", "golf": "G", "hotel": "H", "india": "I", "juliet": "J", "juliett": "J",
    "kilo": "K", "lima": "L", "mike": "M", "november": "N", "oscar": "O", "papa": "P",
    "quebec": "Q", "romeo": "R", "sierra": "S", "tango": "T", "uniform": "U", "victor": "V",
    "whiskey": "W", "xray": "X", "x-ray": "X", "yankee": "Y", "zulu": "Z",
}
LETTER_TO_WORD = {}
for _word, _letter in PHONETIC.items():
    LETTER_TO_WORD.setdefault(_letter, _word)

DIGIT_WORDS = {
    "zero": "0", "one": "1", "two": "2", "three": "3", "four": "4", "five": "5",
    "six": "6", "seven": "7", "eight": "8", "nine": "9", "niner": "9", "tree": "3", "fife": "5",
}
# Group form ("Delta twenty four twenty five" = 2425, "seven thirty seven" = 737), as MOSS-ATC writes numbers.
TEENS = {w: str(10 + i) for i, w in enumerate(
    "ten eleven twelve thirteen fourteen fifteen sixteen seventeen eighteen nineteen".split())}
TENS = {w: str(i + 2) for i, w in enumerate("twenty thirty forty fifty sixty seventy eighty ninety".split())}
UNITS = "one|two|three|four|five|six|seven|eight|nine|niner"
GROUP = re.compile(rf"\b(?:({'|'.join(TENS)})(?:[\s-]+({UNITS}))?|({'|'.join(TEENS)}))\b", re.I)

# Recurrent Whisper confusions on narrow-band VHF audio, observed on KATL Ground.
ASR_FIXES = [
    (r"\bfox[\s,]*(?:shot|tractor|trot|trac)\b", "foxtrot", "foxtrot_variant"),
    (r"\bfox\b(?!\s*trot)", "foxtrot", "foxtrot_short"),
    (r"\b(\d{1,2})\s+or\s+(left|right)\b", r"\1 \2", "runway_or_side"),
    (r"\bhold\s+for\s+(?:the\s+)?(?=(?:" + "|".join(PHONETIC) + r")\b)", "hold short of ", "hold_for_to_hold_short"),
    (r"\b(?:line\s+of|on\s+the|atlanta|atlantic|lana)\s+ground\b", "atlanta ground", "ground_facility"),
    (r"\bget\s+away\b", "give way", "give_way_variant"),
    (r"\bjuliette?\b", "juliet", "juliet_spelling"),
]

FACILITY_WORDS = {
    "ground": "GND", "tower": "TWR", "departure": "DEP", "approach": "APP",
    "clearance": "DEL", "delivery": "DEL", "ramp": "RAMP", "center": "CTR",
}


def apply_asr_fixes(text: str) -> Tuple[str, List[str]]:
    edits = []
    out = text
    for pattern, repl, name in ASR_FIXES:
        new = re.sub(pattern, repl, out, flags=re.I)
        if new != out:
            edits.append(name)
            out = new
    return out, edits


def group_words_to_digits(text: str) -> str:
    """'twenty four twenty five' -> '2425'; a two-digit group directly after a number joins it ('7 37' -> '737')."""
    def two_digits(m):
        if m.group(3):
            return "\x01" + TEENS[m.group(3).lower()]
        unit = DIGIT_WORDS[m.group(2).lower()] if m.group(2) else "0"
        return "\x01" + TENS[m.group(1).lower()] + unit

    out = GROUP.sub(two_digits, text)
    out = DIGIT_RUN.sub(lambda m: m.group(0).replace(" ", ""), " ".join(
        DIGIT_WORDS.get(t.lower(), t) for t in out.split(" ")))
    prev = None
    while prev != out:
        prev, out = out, re.sub(r"(\d+)\s+\x01(\d\d)", r"\1\2", out)
    return out.replace("\x01", "")


DIGIT_RUN = re.compile(r"\b\d(?: \d){1,3}\b")


def digit_words_to_digits(text: str) -> str:
    if GROUP.search(text):
        text = group_words_to_digits(text)
    tokens = re.split(r"(\s+|,)", text)
    out = []
    for tok in tokens:
        low = tok.lower()
        out.append(DIGIT_WORDS.get(low, tok))
    joined = "".join(out)
    # Collapse runs of single digits separated by spaces ("2 2 right" -> "22 right").
    joined = re.sub(r"\b(\d)(?:\s+(\d))(?:\s+(\d))?(?:\s+(\d))?\b",
                    lambda m: "".join(g for g in m.groups() if g), joined)
    return joined


def join_hyphen_digits(text: str) -> str:
    # "Delta 26-42" -> "Delta 2642", but keep frequencies like "119.1" untouched.
    return re.sub(r"\b(\d{1,4})-(\d{1,4})\b", r"\1\2", text)


def normalize_text(text: str) -> Tuple[str, List[str]]:
    edits = []
    out = text.strip()
    out, fix_edits = apply_asr_fixes(out)
    edits.extend(fix_edits)
    new = digit_words_to_digits(out)
    if new != out:
        edits.append("digit_words")
        out = new
    out = re.sub(r"\s+", " ", out).strip()
    return out, edits


def canonical_runway(number: str, side: Optional[str] = None) -> str:
    num = str(int(number))
    side_map = {"left": "L", "right": "R", "center": "C", "l": "L", "r": "R", "c": "C"}
    return num + side_map.get((side or "").lower(), "")


RUNWAY_RE = re.compile(r"\b(?:runway\s+)?(\d{1,2})\s*(left|right|center)\b|\brunway\s+(\d{1,2})\b", re.I)


def find_runways(text: str) -> List[Tuple[str, int, int]]:
    found = []
    for m in RUNWAY_RE.finditer(text):
        if m.group(1):
            if int(m.group(1)) > 36:
                continue
            found.append((canonical_runway(m.group(1), m.group(2)), m.start(), m.end()))
        elif m.group(3) and int(m.group(3)) <= 36:
            found.append((canonical_runway(m.group(3)), m.start(), m.end()))
    return found


TAXIWAY_RE = re.compile(
    r"\b(" + "|".join(sorted(PHONETIC, key=len, reverse=True)) + r")(?:[\s-]*(\d{1,2}))?\b", re.I
)


def find_taxiways(text: str) -> List[Tuple[str, int, int]]:
    found = []
    for m in TAXIWAY_RE.finditer(text):
        letter = PHONETIC[m.group(1).lower()]
        number = m.group(2) or ""
        found.append((letter + number, m.start(), m.end()))
    return found


FREQ_RE = re.compile(r"\b(1?\d{2})[\.\-, ]\s?(\d{1,3})\b")


def canonical_frequency(text: str) -> Optional[str]:
    m = FREQ_RE.search(text)
    if not m:
        return None
    head, tail = m.group(1), m.group(2)
    if len(head) == 2:
        head = "1" + head
    if not 117 <= int(head) <= 137:
        return None
    return f"{head}.{tail}"


def build_callsign_regex(telephony: Dict[str, str]) -> re.Pattern:
    names = sorted(telephony.keys(), key=len, reverse=True)
    alt = "|".join(re.escape(n.lower()) for n in names) + "|alpha|alfa"
    return re.compile(r"\b(" + alt + r")[\s,]+(\d[\d\s-]{0,7}\d|\d)(\s+heavy|\s+super)?\b", re.I)


BARE_START_RE = re.compile(r"^\W*(\d{2,4})(?:-(\d{1,3}))?(\s+heavy)?(?=[\s,.]|$)(?![\s,]*(?:left|right|center|point|\.\d))", re.I)
BARE_END_RE = re.compile(r"(?<![\d.])(\d{2,4})(?:-(\d{1,3}))?(\s+heavy)?\W*$", re.I)


def find_bare_callsign(text: str) -> Optional[dict]:
    """Controllers and pilots often abbreviate to the flight number only ("1606, Atlanta Ground, ...")."""
    length = max(len(text), 1)
    for regex, where in ((BARE_START_RE, "start"), (BARE_END_RE, "end")):
        m = regex.search(text)
        if m:
            digits = m.group(1) + (m.group(2) or "")
            if 2 <= len(digits) <= 4:
                return {"raw": m.group(0).strip(" ,."), "telephony": "", "number": digits, "start": m.start(),
                        "end": m.end(), "pos_ratio": round(m.start() / length, 3),
                        "end_ratio": round(m.end() / length, 3), "bare": True, "where": where}
    return None


# Flight numbers have at most 4 digits (ICAO flight plan Item 7: aircraft identification <= 7 characters =
# 3-letter designator + 4 digits). US civil registrations ("November 12345") allow 5.
MAX_FLIGHT_DIGITS = {"NOVEMBER": 5}


def flight_number(m: re.Match) -> Tuple[str, int]:
    """Join the digit groups after the telephony from left to right and stop before the total exceeds the limit,
    so a following position stays text: "Delta 1489 2 south" -> 1489, "Delta 14 89" -> 1489."""
    limit = MAX_FLIGHT_DIGITS.get(m.group(1).upper(), 4)
    digits, end = "", m.start(2)
    for g in re.finditer(r"\d+", m.group(2)):
        if len(digits) + len(g.group()) > limit:
            return digits, end
        digits, end = digits + g.group(), m.start(2) + g.end()
    return digits, m.end()


def find_callsigns(text: str, callsign_re: re.Pattern, allow_bare: bool = False) -> List[dict]:
    hits = []
    length = max(len(text), 1)
    for m in callsign_re.finditer(text):
        digits, end = flight_number(m)
        if not digits:
            continue
        # "Alpha 3" is a taxiway; only long numbers after Alpha are treated as a (misrecognized) callsign.
        if m.group(1).lower() in ("alpha", "alfa") and len(digits) < 3:
            continue
        hits.append({
            "raw": text[m.start():end].strip(),
            "telephony": m.group(1).upper(),
            "number": digits,
            "start": m.start(),
            "end": end,
            "pos_ratio": round(m.start() / length, 3),
            "end_ratio": round(end / length, 3),
        })
    if not hits and allow_bare:
        bare = find_bare_callsign(text)
        if bare:
            hits.append(bare)
    return hits
