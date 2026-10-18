"""Build configs/ontology.json (vocabulary) and configs/rules.json (rule base) from the reference documents in data/ontology/.

    python src/kb/build_ontology.py                    # writes configs/ontology.json and configs/rules.json
    python src/kb/build_ontology.py --out-dir /tmp/kb  # dry run elsewhere

Split (knowledge-based system layout)
- ontology.json: entity/relation types, 20 command types (DLR rows, FAA phraseology), lexical cues -> used by M4, M6
- rules.json:    region rule sets, read-back levels (FAA 2-4-3 / ICAO), supersession, validity -> used by M6, M7, E3

Inputs (not redistributed; see .gitignore)
- OntologyATCCommandsDescription_v20250704.pdf  DLR-IB-FL-BS-2025-107 v3.00: instruction elements and JSON keys (ch. 2, 5.1)
- OntologyATCCommandsTable_v20250704.pdf        DLR command table: p.1 types/values/qualifiers, p.2 parameter formats
- FAA규정.pdf                                    FAA Order JO 7110.65BB: phraseology and read-back rules

Strategy
1. Our design decisions (20 command types, KG state semantics, supersession, lexical cues) are curated below.
2. Every claim tied to a document is *verified against the PDF text* at build time:
   - each type's DLR (Type Main, Type Sub) must exist as a row of the command table; its value/unit/qualifier/condition
     cells and the referenced parameter formats are copied from the table;
   - each FAA reference must be a real section heading whose body contains the quoted phraseology;
   - each DLR JSON key must appear in the description document.
   A missing match aborts the build, so the JSON never cites something the documents do not say.
3. Runtime keys read by the pipeline keep their names and meaning; they only moved between the two files.
"""
import argparse
import difflib
import hashlib
import re
import subprocess
import sys
from datetime import date
from pathlib import Path

SRC_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SRC_ROOT))

from common.io_utils import save_json
from common.paths import CONFIG_ROOT, DATA_ROOT, ONTOLOGY_PATH, RULES_PATH, display_path


REF_ROOT = DATA_ROOT / "ontology"
DLR_DESCRIPTION = REF_ROOT / "OntologyATCCommandsDescription_v20250704.pdf"
DLR_TABLE = REF_ROOT / "OntologyATCCommandsTable_v20250704.pdf"
FAA_ORDER = REF_ROOT / "FAA규정.pdf"

# DLR instruction structure (Description Figure 2-1, p.7; JSON keys from table 5.1, p.20), mapped to our M4 fields.
# "verify" lists terms that must appear in the Description text; json keys are verified as well.
DLR_INSTRUCTION_SCHEMA = {
    "source": "DLR-IB-FL-BS-2025-107 v3.00, Figure 2-1 (p.7) and key-value table 5.1 (p.20)",
    "Instruction": {
        "description": "한 항공기에게 내려진 지시 하나. 한 번의 교신에 지시가 여러 개면 Instruction도 여러 개로 나눈다.",
        "Callsign": {
            "description": "지시를 받는(또는 말하는) 항공기의 호출부호. 항공사 코드와 편명 번호를 붙여 적고, 말하지 않았으면 NO_CALLSIGN.",
            "json_key": "csgn", "required": True, "empty_value": "NO_CALLSIGN", "example": "DLH498",
            "ours": "callsign", "verify": ["Callsign", "NO_CALLSIGN"],
        },
        "Command": {
            "description": "항공기에게 시키는 행동 자체를 담는 묶음. 아래 6개 칸으로 구성된다.",
            "Speaker": {
                "description": "말한 사람이 관제사(ATCO)인지 조종사(PILOT)인지. 비워 두면 ATCO.",
                "json_key": "speaker", "values": ["ATCO", "PILOT"], "default": "ATCO",
                "ours": "speaker", "verify": ["Speaker", "ATCO", "PILOT"],
                "note": "Figure 2-1에서는 Command 안에 있지만 5.1 JSON에서는 commands 바깥(화자 단위)에 한 번 적는다. 우리도 송신 단위로 한 번 기록한다.",
            },
            "Reason": {
                "description": "조종사가 말한 경우에만 쓰며, 요청(REQUEST)인지 상태 보고(REPORTING)인지 구분한다.",
                "json_key": "reas", "values": ["REQUEST", "REPORTING"], "json_values": ["REQ", "REP"],
                "ours": None, "verify": ["Reason", "REQUEST", "REPORTING"],
                "note": "우리는 요청을 명령 종류 REQUEST로 단순화했고 REPORTING은 구분하지 않는다.",
            },
            "Type": {
                "description": "어떤 행동을 시키는지 나타내는 명령 이름. 필요하면 큰 분류(Main)와 세부 분류(Sub) 두 단어로 적는다.",
                "json_key": ["type", "sndT"], "required": True, "empty_value": "NO_CONCEPT", "example": "TAXI VIA",
                "ours": "type", "verify": ["Type", "NO_CONCEPT"],
                "note": "우리는 Main+Sub를 이름 하나로 합친다 (CLEARED + TAKEOFF -> CLEARED_FOR_TAKEOFF). 대응은 command_types.*.dlr.",
            },
            "Value(s)": {
                "description": "명령이 적용되는 대상이나 숫자. 명령에 따라 없거나 하나거나 여러 개다.",
                "json_key": ["valu", "vare"], "example": "L N7, RW26R, 180",
                "ours": "target | value | route", "verify": ["Value(s)"],
                "note": "DLR은 TAXI TO(목적지)와 TAXI VIA(경로)를 별개 Instruction으로 나누지만, 우리 TAXI_VIA는 하나에 route(VIA 값)와 target(TO 값)을 함께 담는다.",
            },
            "Unit": {
                "description": "값이 숫자일 때 그 단위.",
                "json_key": "unit", "example": "kt, FL", "ours": None, "verify": ["Unit"],
                "note": "지상관제 명령에는 거의 쓰이지 않아 추출하지 않는다.",
            },
            "Qualifier": {
                "description": "방향이나 방식처럼 값만으로는 부족한 정보를 덧붙인다.",
                "json_key": "qual", "example": "LEFT, RIGHT", "ours": "qualifier", "verify": ["Qualifier"],
            },
        },
        "Condition(s)": {
            "description": "명령이 언제부터 또는 언제까지 유효한지 정하는 조건. 말해진 경우에만 채운다.",
            "json_key": "cond", "ours": None, "verify": ["Condition", "Conjunction", "Requirement"],
            "note": "조건부 허가는 이번 범위에서 제외했다.",
            "Conjunction": {
                "description": "조건의 종류: UNTIL(~까지), WHEN(~일 때), IF(~이면), AFTER(~ 이후에).",
                "values": ["UNTIL", "WHEN", "IF", "AFTER"], "verify": ["UNTIL", "WHEN", "IF", "AFTER"],
            },
            "Requirement": {
                "description": "조건이 만족되었는지 판단하는 기준이 되는 사건이나 위치 (명령 또는 Conditional-Keyword).",
                "example": "5 NM FINAL",
            },
        },
    },
}


def schema_terms(node) -> set:
    """All json keys and verify terms in the schema tree."""
    terms = set()
    if isinstance(node, dict):
        for key in ("json_key", "verify", "json_values"):
            value = node.get(key)
            terms.update([value] if isinstance(value, str) else value or [])
        for child in node.values():
            terms |= schema_terms(child)
    return terms


# FAA JO 7110.65BB 2-4-3 read-back levels. Each level is verified against the section text.
FAA_READBACK_BASIS = {
    "acknowledge": ("2-4-3", r"Ensure pilots acknowledge all Air Traffic Clearances"),
    "mandatory": ("2-4-3", r"Ensure the read back of hold short instructions"),
    "runway_assignment": ("2-4-3", r"Initial read back of a taxi, departure or landing clearance should include the runway assignment"),
    "numbers": ("2-4-3", r"pilots should read back the .numbers"),
}

# Curated command types. dlr: (Type Main, Type Sub) rows of the DLR table; sub None = empty cell, "*" = any.
# faa: (section, phraseology regex found in that section). faa_readback: level from FAA_READBACK_BASIS, "design" when
# 7110.65 does not state it and we require it for runway safety, None when an acknowledgement suffices.
COMMAND_TYPES = [
    {"name": "TAXI_VIA", "description": "taxi instruction with a route (VIA) and optional destination", "state": True,
     "targets": ["Runway", "Ramp", "Taxiway"], "dlr": [("TAXI", "Preposition")],
     "faa": [("3-7-2", r"taxi clearance/route must include the specific route")],
     "faa_readback": "runway_assignment", "icao_readback": True},
    {"name": "HOLD_SHORT", "description": "hold short of a runway or taxiway", "state": True,
     "targets": ["Runway", "Taxiway"], "dlr": [("HOLD_SHORT", None)],
     "faa": [("3-7-2", r"READ BACK HOLD INSTRUCTIONS"), ("2-4-3", r"read back of hold short instructions")],
     "faa_readback": "mandatory", "icao_readback": True},
    {"name": "CROSS_RUNWAY", "description": "runway crossing clearance", "state": True,
     "targets": ["Runway"], "dlr": [("CROSS", None)],
     "faa": [("3-7-2", r"CROSS \(runway\)")],
     "faa_readback": "design", "icao_readback": True},
    {"name": "HOLD_POSITION", "description": "hold position", "state": True,
     "targets": [], "dlr": [("HOLD_POSITION", None)],
     "faa": [("3-7-2", r"HOLD POSITION")],
     "faa_readback": "design", "icao_readback": True},
    {"name": "GIVE_WAY", "description": "give way to traffic", "state": False,
     "targets": [], "dlr": [("GIVE_WAY", None)], "faa": [],
     "faa_readback": None, "icao_readback": False,
     "note": "No GIVE WAY phraseology in JO 7110.65 (only the light-gun signal); kept because it occurs in US ground audio."},
    {"name": "FOLLOW", "description": "follow or pass behind traffic", "state": False,
     "targets": [], "dlr": [("FOLLOW", None)],
     "faa": [("3-7-2", r"FOLLOW \(traffic\)")],
     "faa_readback": None, "icao_readback": False},
    {"name": "CONTACT", "description": "frequency change, contact", "state": True,
     "targets": ["Facility"], "dlr": [("CONTACT", None), ("CONTACT_FREQUENCY", "*")],
     "faa": [("2-1-17", r"CONTACT \(facility name")],
     "faa_readback": "numbers", "icao_readback": True},
    {"name": "MONITOR", "description": "frequency change, monitor", "state": True,
     "targets": ["Facility"], "dlr": [],
     "faa": [("2-1-17", r"Monitor Tower")],
     "faa_readback": "numbers", "icao_readback": False,
     "note": "US extension: no DLR type; JO 7110.65 2-1-17 examples (\"Monitor Tower\", \"Monitor Ground\")."},
    {"name": "LINE_UP_AND_WAIT", "description": "line up and wait", "state": True,
     "targets": ["Runway"], "dlr": [("LINEUP", None)],
     "faa": [("3-9-4", r"LINE UP AND WAIT")],
     "faa_readback": "design", "icao_readback": True},
    {"name": "CLEARED_FOR_TAKEOFF", "description": "takeoff clearance", "state": True,
     "targets": ["Runway"], "dlr": [("CLEARED", "TAKEOFF")],
     "faa": [("3-9-10", r"CLEARED FOR TAKEOFF")],
     "faa_readback": "runway_assignment", "icao_readback": True},
    {"name": "CLEARED_TO_LAND", "description": "landing clearance", "state": True,
     "targets": ["Runway"], "dlr": [("CLEARED", "LANDING")],
     "faa": [("3-10-5", r"CLEARED TO LAND")],
     "faa_readback": "runway_assignment", "icao_readback": True},
    {"name": "TURN", "description": "turn left/right", "state": False,
     "targets": ["Taxiway"], "dlr": [("TURN", None)],
     "faa": [("3-10-9", r"TURN LEFT/RIGHT")],
     "faa_readback": None, "icao_readback": False},
    {"name": "CLIMB", "description": "climb", "state": False,
     "targets": [], "dlr": [("CLIMB", "*")],
     "faa": [("4-3-2", r"CLIMB AND MAINTAIN")],
     "faa_readback": "numbers", "icao_readback": True},
    {"name": "DESCEND", "description": "descend", "state": False,
     "targets": [], "dlr": [("DESCEND", "*")],
     "faa": [("4-5-7", r"DESCEND AND MAINTAIN")],
     "faa_readback": "numbers", "icao_readback": True},
    {"name": "HEADING", "description": "heading", "state": False,
     "targets": [], "dlr": [("HEADING", None)],
     "faa": [("5-6-2", r"TURN LEFT/RIGHT HEADING")],
     "faa_readback": "numbers", "icao_readback": True},
    {"name": "SQUAWK", "description": "transponder code", "state": False,
     "targets": [], "dlr": [("SQUAWK", None)],
     "faa": [("5-2-1", r"SQUAWK \(code\)")],
     "faa_readback": "numbers", "icao_readback": True},
    {"name": "TRAFFIC_INFO", "description": "traffic information (advisory)", "state": False,
     "targets": [], "dlr": [("INFORMATION", "TRAFFIC")],
     "faa": [("2-1-21", r"TRAFFIC, \(number\) O.CLOCK")],
     "faa_readback": None, "icao_readback": False, "advisory": True},
    {"name": "WAKE_ADVISORY", "description": "wake turbulence advisory", "state": False,
     "targets": [], "dlr": [("CAUTION", "WAKE_TURBULENCE")],
     "faa": [("2-1-20", r"CAUTION WAKE TURBULENCE")],
     "faa_readback": None, "icao_readback": False, "advisory": True},
    {"name": "ACK", "description": "roger / wilco / copy", "state": False,
     "targets": [], "dlr": [("AFFIRM", None)],
     "faa": [("2-4-3", r"Wilco.{0,6}Roger")],
     "faa_readback": None, "icao_readback": False, "non_instruction": True,
     "note": "DLR models AFFIRM/NEGATIVE only; roger/wilco/copy have no DLR type."},
    {"name": "REQUEST", "description": "pilot request (ready to taxi, request ...)", "state": False,
     "targets": [], "dlr": [], "faa": [],
     "faa_readback": None, "icao_readback": False, "non_instruction": True,
     "note": "DLR encodes this as Speaker=PILOT with Reason=REQUEST (JSON reas=REQ), not as a command type."},
]

# The DLR table (p.1) has 16 group headings; we count groups by these headings. The Description (p.9) lists the same
# groups as 15 classes because it merges "Cleared types" and "Cancel types". Heading -> (Description number, name).
DLR_GROUPS = {
    "Cleared types": (5, "Cleared and Cancel types"),
    "Cancel types": (5, "Cleared and Cancel types"),
    "Pushback and startup types": (13, "Pushback and start-up types"),
    "Ground hold types": (14, "Ground hold types"),
    "Airborne holding types": (8, "Airborne holding types"),
    "Frequency changing and squawk types": (6, "Frequency changing and Squawk types"),
    "Direction changing types": (2, "Direction changing types"),
    "Informamtion types": (7, "Information types"),  # typo in the table, kept verbatim
    "Miscellaneous types": (15, "Miscellaneous types"),
    "VFR clearance types": (9, "VFR clearance types"),
    "Reporting types": (10, "Reporting types"),
    "Speed types": (4, "Speed types"),
    "Taxi types": (12, "Taxi types"),
    "Movement progress rules types": (11, "Movement progress rules types"),
    "Vertical types": (1, "Vertical types"),
    "Vertical speed types": (3, "Vertical speed types"),
}

ICAO_READBACK_SOURCE = ("ICAO Doc 4444 PANS-ATM, read-back requirements (runway, level, heading, SSR code, frequency). "
                        "Not among data/ontology inputs; not verified by this script.")

ENTITY_TYPES = ["Airport", "Runway", "Taxiway", "Ramp", "Facility", "Airline", "Flight", "CallsignMention",
                "Transmission", "Instruction", "Role"]
RELATION_TYPES = {
    "SPOKEN_BY": ["Transmission", "Role"],
    "ADDRESSES": ["Transmission", "Flight"],
    "CONTAINS": ["Transmission", "Instruction"],
    "TARGET": ["Instruction", "Runway|Taxiway|Ramp|Facility"],
    "VIA": ["Instruction", "Taxiway"],
    "READBACK_OF": ["Transmission", "Transmission"],
    "NEXT": ["Instruction", "Instruction"],
    "OPERATED_BY": ["Flight", "Airline"],
    "ALIAS": ["Flight", "CallsignMention"],
    "PART_OF": ["Runway|Taxiway|Ramp|Facility", "Airport"],
}
REGION_RULES = {"US": "FAA", "KR": "ICAO", "JP": "ICAO"}

# KG design values (not regulatory): supersession and maximum validity of state-type instructions.
SUPERSEDES = {
    "CONTACT": ["*"], "MONITOR": ["*"],
    "TAXI_VIA": ["TAXI_VIA", "HOLD_POSITION"],
    "CROSS_RUNWAY": ["HOLD_SHORT", "HOLD_POSITION"],
    "HOLD_SHORT": ["HOLD_SHORT"], "HOLD_POSITION": ["HOLD_POSITION"],
    "LINE_UP_AND_WAIT": ["HOLD_SHORT", "LINE_UP_AND_WAIT"],
    "CLEARED_FOR_TAKEOFF": ["LINE_UP_AND_WAIT", "HOLD_SHORT", "CLEARED_FOR_TAKEOFF"],
    "CLEARED_TO_LAND": ["CLEARED_TO_LAND"],
}
MAX_VALIDITY_SEC = {
    "TAXI_VIA": 900, "HOLD_SHORT": 900, "CROSS_RUNWAY": 180, "HOLD_POSITION": 600, "CONTACT": 1800,
    "MONITOR": 1800, "LINE_UP_AND_WAIT": 300, "CLEARED_FOR_TAKEOFF": 180, "CLEARED_TO_LAND": 600,
}
RUNWAY_STATE_TYPES = ["CROSS_RUNWAY", "LINE_UP_AND_WAIT", "CLEARED_FOR_TAKEOFF", "CLEARED_TO_LAND"]

# An extracted concept is kept only if its cue matches the transcript (guards against LLM hallucination, C13).
LEXICAL_CUES = {
    "TAXI_VIA": r"\b(taxi|via|join|continue|ramp|down|on|to)\b|\b(alpha|bravo|charlie|delta|echo|fox\w*|golf|hotel|juliet\w*|kilo|lima|mike|november|papa|romeo|sierra|tango|uniform|victor|whiskey|yankee|zulu)\b",
    "HOLD_SHORT": r"hold\s+short|hold\s+for|short\s+of",
    "CROSS_RUNWAY": r"\bcross",
    "HOLD_POSITION": r"hold\s+(your\s+)?position",
    "GIVE_WAY": r"give\s+way|get\s+away|gave\s+way|give\s+away",
    "FOLLOW": r"follow|behind",
    "CONTACT": r"contact",
    "MONITOR": r"monitor",
    "LINE_UP_AND_WAIT": r"line\s+up",
    "CLEARED_FOR_TAKEOFF": r"take\s?off",
    "CLEARED_TO_LAND": r"clear\w*\s+to\s+land",
    "TURN": r"\bturn|\bleft\b|\bright\b",
    "CLIMB": r"\bclimb",
    "DESCEND": r"\bdescend",
    "HEADING": r"\bheading\b|\bfly\s+\d",
    "SQUAWK": r"squawk",
    "TRAFFIC_INFO": r"traffic|o'?clock|from\s+your|company",
    "WAKE_ADVISORY": r"wake|heavy\s+departed|caution",
}


def pdf_text(path: Path, first: int = None, last: int = None) -> str:
    if not path.exists():
        sys.exit(f"[onto] missing input: {display_path(path)}")
    cmd = ["pdftotext", "-layout", "-q"]
    if first:
        cmd += ["-f", str(first), "-l", str(last or first)]
    return subprocess.run(cmd + [str(path), "-"], capture_output=True, text=True, check=True).stdout


def md5(path: Path) -> str:
    return hashlib.md5(path.read_bytes()).hexdigest()


def split_cells(line: str):
    """Yield (start column, text) for runs separated by two or more spaces."""
    for m in re.finditer(r"\S+(?: \S+)*", line):
        yield m.start(), m.group()


def assign(start: int, columns):
    """Index of the column a cell belongs to (layout output shifts cells by a character or two)."""
    idx = 0
    for k, (_, col_start) in enumerate(columns):
        if start + 3 >= col_start:
            idx = k
    return columns[idx][0]


def parse_command_table(text: str):
    """DLR table p.1 -> rows {category, main, sub, value, unit, qualifier, condition}."""
    lines = text.splitlines()
    head = next(i for i, l in enumerate(lines) if "Type (Main)" in l)
    labels = [("main", "Type (Main)"), ("sub", "Type (Sub)"), ("value", "Value(s)"), ("unit", "Unit"),
              ("qualifier", "Qualifier(s)"), ("condition", "Condition(s)")]
    columns = [("pre", 0)] + [(key, lines[head].index(label)) for key, label in labels]

    rows, pending, category = [], [], None
    for line in lines[head + 1:]:
        cells = [(assign(s, columns), t) for s, t in split_cells(line)]
        if not cells:
            continue
        if len(cells) == 1 and cells[0][0] == "main" and cells[0][1].lower().endswith("types"):
            category = cells[0][1]
            pending = []
            continue
        if not any(c == "main" for c, _ in cells):
            pending.append(cells)  # wrapped cell text; the table centres it around the row it belongs to
            continue
        row = {"category": category, "main": "", "sub": "", "value": "", "unit": "", "qualifier": "", "condition": ""}
        for cells_ in pending + [cells]:
            for col, txt in cells_:
                if col in row:
                    row[col] = f"{row[col]} {txt}".strip()
        pending = []
        rows.append(row)
    return rows


def parse_parameter_table(text: str):
    """DLR table p.2 -> {parameter name: allowed values / format}."""
    lines = text.splitlines()
    head = next(i for i, l in enumerate(lines) if "Parameter_Type" in l and "Parameter_Value" in l)
    value_col = lines[head].index("Parameter_Value")
    comment_col = lines[head].index("Comments")
    params, current = {}, None
    for line in lines[head + 1:]:
        cells = list(split_cells(line))
        if not cells:
            continue
        name = [t for s, t in cells if s + 3 < value_col]
        values = [t for s, t in cells if value_col <= s + 3 < comment_col]
        if name:
            current = name[0]
            params[current] = " ".join(values)
        elif current and values:
            params[current] = f"{params[current]} {' '.join(values)}".strip()
    return params


def referenced_parameters(row: dict, params: dict) -> dict:
    """Resolve non-terminal names (e.g. 'TX-Point', 'APTR_COLOR') used in a row against the parameter table.

    Bold (terminal) vs non-bold (parameter) is lost in text extraction, so a token counts as a parameter when it names
    a row of the parameter table. Exact names may be upper case (APTR_COLOR); fuzzy matching (table typos such as
    'Alt-Qualfier', suffixes such as 'GND_DIRECTION(only') is limited to tokens with lower-case letters.
    """
    norm = {re.sub(r"[^a-z0-9]", "", k.lower()): k for k in params}
    found = {}
    for key in ("sub", "value", "qualifier", "condition"):
        for token in re.split(r"[/,]|\s+", row[key]):
            token = token.strip(" ()")
            if len(token) < 3:
                continue
            probe = re.sub(r"[^a-z0-9]", "", token.lower())
            hit = params.get(token) and token or norm.get(probe)
            if not hit and not token.isupper():
                close = difflib.get_close_matches(probe, norm, n=1, cutoff=0.85)
                hit = norm[close[0]] if close else None
            if hit:
                found[hit] = params[hit]
    return found


def parse_faa_sections(text: str):
    """JO 7110.65 -> {'3-7-2': {'title', 'body'}} from body headings such as '3−7−2. TAXI AND ...'."""
    heading = re.compile(r"^(\d+)[−-](\d+)[−-](\d+)\.\s+(\S.*)$")
    sections, current = {}, None
    for line in text.splitlines():
        m = heading.match(line.strip())
        if m and ". . ." not in line:
            sec = f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
            if sec in sections:  # index pages repeat headings; keep the first (body) occurrence
                current = None
                continue
            current = sec
            sections[sec] = {"title": m.group(4).strip(), "lines": []}
        elif current:
            sections[current]["lines"].append(line)
    return {k: {"title": v["title"], "body": re.sub(r"\s+", " ", " ".join(v["lines"]))} for k, v in sections.items()}


def faa_evidence(sections: dict, sec: str, pattern: str) -> dict:
    if sec not in sections:
        raise ValueError(f"FAA section {sec} not found")
    m = re.search(pattern, sections[sec]["body"], re.I)
    if not m:
        where = [k for k, v in sections.items() if re.search(pattern, v["body"], re.I)]
        raise ValueError(f"FAA {sec} ({sections[sec]['title']}) lacks /{pattern}/; found in {where[:8]}")
    a, b = max(0, m.start() - 60), min(len(sections[sec]["body"]), m.end() + 60)
    return {"section": sec, "title": sections[sec]["title"], "quote": sections[sec]["body"][a:b].strip()}


def match_rows(rows, main: str, sub):
    hits = [r for r in rows if r["main"] == main and (sub == "*" or r["sub"] == (sub or ""))]
    if not hits:
        near = sorted({f"{r['main']} {r['sub']}".strip() for r in rows if r["main"] == main})
        raise ValueError(f"DLR table has no row {main} {sub or ''} (rows with that main type: {near})")
    return hits


def build_catalog(rows, description: str, command_types: dict) -> dict:
    """16 DLR groups in table order with their Main types; verified against the Description's class list (p.9)."""
    used = {}
    for name, ct in command_types.items():
        for r in ct["dlr"] or []:
            used.setdefault((r["category"], r["main"]), []).append(name)
    groups = []
    for row in rows:
        if not groups or groups[-1]["heading"] != row["category"]:
            if row["category"] not in DLR_GROUPS:
                sys.exit(f"[onto] unknown DLR table heading: {row['category']}")
            number, name = DLR_GROUPS[row["category"]]
            if not re.search(rf"{number}\.\s+{re.escape(name)}", description, re.I):
                sys.exit(f"[onto] Description p.9 does not list '{number}. {name}'")
            groups.append({"order": len(groups) + 1, "heading": row["category"], "description_class": number,
                           "description_name": name, "main_types": [], "rows": 0, "used_by": []})
        g = groups[-1]
        g["rows"] += 1
        if row["main"] not in g["main_types"]:
            g["main_types"].append(row["main"])
        for name in used.get((row["category"], row["main"]), []):
            if name not in g["used_by"]:
                g["used_by"].append(name)
    if len(groups) != len(DLR_GROUPS):
        sys.exit(f"[onto] expected {len(DLR_GROUPS)} DLR groups, table has {len(groups)}")
    mains = {m for g in groups for m in g["main_types"]}
    return {
        "source": "OntologyATCCommandsTable_v20250704.pdf p.1 (group headings in table order); "
                  "description_class = class number in the Description p.9",
        "counts": {"groups": len(groups), "main_types": len(mains), "table_rows": len(rows),
                   "main_types_used": len({main for _, main in used})},
        "note": "We count 16 groups by the table headings. The Description p.9 lists 15 classes because it merges "
                "'Cleared types' and 'Cancel types' (class 5). Main names such as MAINTAIN, CONTINUE and INFORMATION "
                "appear in several groups, so the per-group lists sum to more than the distinct count.",
        "groups": groups,
    }


def build(out_dir: Path):
    description = pdf_text(DLR_DESCRIPTION)
    table_rows = parse_command_table(pdf_text(DLR_TABLE, 1))
    params = parse_parameter_table(pdf_text(DLR_TABLE, 2))
    faa = parse_faa_sections(pdf_text(FAA_ORDER))
    print(f"[onto] DLR table: {len(table_rows)} command rows, {len(params)} parameters; FAA: {len(faa)} sections")

    missing = sorted(t for t in schema_terms(DLR_INSTRUCTION_SCHEMA)
                     if not re.search(rf"(?<!\w){re.escape(t)}(?!\w)", description))
    if missing:
        sys.exit(f"[onto] DLR description does not contain: {missing}")
    readback_basis = {level: faa_evidence(faa, sec, pat) for level, (sec, pat) in FAA_READBACK_BASIS.items()}

    command_types, readback, errors = {}, {}, []
    for ct in COMMAND_TYPES:
        try:
            dlr = []
            for main, sub in ct["dlr"]:
                for row in match_rows(table_rows, main, sub):
                    dlr.append({**{k: v for k, v in row.items() if v}, "parameters": referenced_parameters(row, params)})
            faa_refs = [faa_evidence(faa, sec, pat) for sec, pat in ct["faa"]]
        except ValueError as exc:
            errors.append(f"{ct['name']}: {exc}")
            continue
        command_types[ct["name"]] = {
            "state": ct["state"],
            "targets": ct["targets"],
            "description": ct["description"],
            "dlr": dlr or None,
            "faa_phraseology": faa_refs,
            **({"note": ct["note"]} if "note" in ct else {}),
        }
        readback[ct["name"]] = {"FAA": ct["faa_readback"], "ICAO": ct["icao_readback"]}
    if errors:
        sys.exit("[onto] verification failed:\n  " + "\n  ".join(errors))

    generated = {"by": "src/kb/build_ontology.py", "date": date.today().isoformat(),
                 "inputs": {display_path(p): md5(p) for p in (DLR_DESCRIPTION, DLR_TABLE, FAA_ORDER)}}

    # Vocabulary and structure: what can be said (DLR), and that the US says it (FAA phraseology).
    ontology = {
        "version": "1.1",
        "generated": generated,
        "sources": [
            "Structure and command names: Helmke, Kleinert, Ohneiser. Ontology for Air Traffic Control Command Annotation "
            "- Description, v3.00, DLR-IB-FL-BS-2025-107, 2025-07-04, with OntologyATCCommandsTable_v20250704 "
            "(https://elib.dlr.de/214852). Each command type lists the matching table rows under 'dlr'.",
            "US usage: FAA Order JO 7110.65BB (effective 2025-02-20). Each command type lists the verified phraseology "
            "under 'faa_phraseology'.",
            "Transfer of the European ontology to US ground control: Helmke, Clarke et al. 2025 (DASC), command-type loss "
            "2.6% absolute (4.6% with all attributes) on US ground transmissions.",
            "Static classes (Airport, Runway, Taxiway, Facility) follow the NASA ATM Ontology (Keller 2017); hold-short "
            "points correspond to AIXM 5.1 TaxiHoldingPosition.",
        ],
        "scope_notes": [
            "Subset of DLR v3.00 for US ground/tower audio plus two US-only types (MONITOR, ACK).",
            "DLR Condition (UNTIL/WHEN/IF/AFTER) and Unit are not extracted.",
            "Read-back, supersession and validity rules live in configs/rules.json.",
        ],
        "dlr_instruction_schema": DLR_INSTRUCTION_SCHEMA,
        "dlr_catalog": build_catalog(table_rows, description, command_types),
        "entity_types": ENTITY_TYPES,
        "relation_types": RELATION_TYPES,
        "command_types": command_types,
        "advisory_types": [c["name"] for c in COMMAND_TYPES if c.get("advisory")],
        "non_instruction_types": [c["name"] for c in COMMAND_TYPES if c.get("non_instruction")],
        "lexical_cues": LEXICAL_CUES,
    }

    # Rule base: what must hold (regulation per region) and KG temporal design values.
    rules = {
        "version": "1.1",
        "generated": generated,
        "sources": [
            "FAA Order JO 7110.65BB 2-4-3 (read-back levels, quoted under readback_basis.FAA) and 3-7-2.",
            ICAO_READBACK_SOURCE,
            "supersedes / max_validity_sec / runway_state_types are KG design values, not regulation.",
        ],
        "region_rules": REGION_RULES,
        "readback": readback,
        "readback_required": {
            "FAA": [c["name"] for c in COMMAND_TYPES if c["faa_readback"]],
            "ICAO": [c["name"] for c in COMMAND_TYPES if c["icao_readback"]],
        },
        "readback_basis": {
            "FAA": {**readback_basis,
                    "design": "Not stated in JO 7110.65; required here because the instruction authorizes runway "
                              "entry or a stop on the movement area."},
            "ICAO": ICAO_READBACK_SOURCE,
        },
        "supersedes": SUPERSEDES,
        "max_validity_sec": MAX_VALIDITY_SEC,
        "runway_state_types": RUNWAY_STATE_TYPES,
    }
    unknown = {k for k in [*SUPERSEDES, *MAX_VALIDITY_SEC, *RUNWAY_STATE_TYPES] if k not in command_types}
    if unknown:
        sys.exit(f"[onto] rules refer to undefined command types: {sorted(unknown)}")

    out_dir.mkdir(parents=True, exist_ok=True)
    save_json(ontology, out_dir / ONTOLOGY_PATH.name)
    save_json(rules, out_dir / RULES_PATH.name)
    print(f"[onto] {len(command_types)} command types ({sum(1 for c in command_types.values() if c['dlr'])} mapped "
          f"to DLR) -> {display_path(out_dir / ONTOLOGY_PATH.name)}, {display_path(out_dir / RULES_PATH.name)}")


def main():
    parser = argparse.ArgumentParser(description="Build the ATC command ontology JSON from the DLR and FAA documents.")
    parser.add_argument("--out-dir", type=Path, default=CONFIG_ROOT)
    args = parser.parse_args()
    build(args.out_dir)


if __name__ == "__main__":
    main()
