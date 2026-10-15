"""M1: discover LiveATC archives, parse context from file names, and filter facilities.

File name pattern (LiveATC archive): {FEED}-{Mon}-{DD}-{YYYY}-{HHMM}Z.mp3
e.g. KATL-Gnd-All-Aug-26-2024-0230Z.mp3 -> icao=KATL, facility=Gnd-All, class=GND
"""
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, List, Optional

from common.paths import RAW_AUDIO_ROOT


FILE_RE = re.compile(
    r"^(?P<feed>.+?)-(?P<mon>[A-Z][a-z]{2})-(?P<day>\d{2})-(?P<year>\d{4})-(?P<hhmm>\d{4})Z\.mp3$"
)
FACILITY_KEYWORDS = [
    ("CTAF", "CTAF"), ("ATIS", "ATIS"), ("COMPANY", "COMPANY"),
    ("GND", "GND"), ("GROUND", "GND"), ("DEL", "DEL"), ("CLNC", "DEL"),
    ("TWR", "TWR"), ("TOWER", "TWR"), ("DEP", "DEP"),
    ("APP", "APP"), ("FINAL", "APP"), ("CTR", "CTR"), ("CENTER", "CTR"),
]
DEFAULT_FACILITIES = ("GND", "DEL", "TWR", "DEP", "APP")


def facility_class(facility: str) -> str:
    upper = facility.upper()
    for keyword, cls in FACILITY_KEYWORDS:
        if keyword in upper:
            return cls
    return "OTHER"


def parse_archive_name(path: Path) -> Optional[dict]:
    m = FILE_RE.match(path.name)
    if not m:
        return None

    feed = m.group("feed")
    head, _, facility = feed.partition("-")
    icao = re.sub(r"\d+$", "", head).upper() if len(head) > 4 else head.upper()
    start = datetime.strptime(
        f"{m.group('year')}-{m.group('mon')}-{m.group('day')} {m.group('hhmm')}", "%Y-%b-%d %H%M"
    ).replace(tzinfo=timezone.utc)

    return {
        "path": str(path),
        "file": path.name,
        "stem": path.stem,
        "feed": feed,
        "icao": icao,
        "facility": facility or "UNKNOWN",
        "facility_class": facility_class(facility or feed),
        "utc_start": start.isoformat().replace("+00:00", "Z"),
        "utc_start_epoch": start.timestamp(),
    }


def discover_archives(
    airports: Iterable[str],
    facilities: Iterable[str] = DEFAULT_FACILITIES,
    root: Path = RAW_AUDIO_ROOT,
    min_bytes: int = 1024,
) -> List[dict]:
    """Return archive records for the requested airports, skipping empty or excluded feeds."""
    wanted_airports = {a.lower() for a in airports}
    wanted_facilities = {f.upper() for f in facilities}
    records, skipped = [], {"empty": 0, "facility": 0, "unparsed": 0}

    for airport in sorted(wanted_airports):
        for path in sorted((root / airport).rglob("*.mp3")):
            if path.stat().st_size < min_bytes:
                skipped["empty"] += 1
                continue
            record = parse_archive_name(path)
            if record is None:
                skipped["unparsed"] += 1
                continue
            if record["facility_class"] not in wanted_facilities:
                skipped["facility"] += 1
                continue
            records.append(record)

    records.sort(key=lambda r: (r["utc_start_epoch"], r["feed"]))
    print(f"[M1] {len(records)} archives selected; skipped {skipped}")
    return records
