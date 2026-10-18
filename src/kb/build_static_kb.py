"""M0: build the static airport knowledge base (runways, taxiways, facilities).

Sources
- OurAirports (public domain): runways.csv, airport-frequencies.csv, airports.csv
- OpenStreetMap via Overpass API (ODbL): aeroway=taxiway/taxilane refs inside the airport bbox
"""
import argparse
import csv
import io
import json
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

SRC_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SRC_ROOT))

from common.io_utils import save_json
from common.paths import STATIC_KB_ROOT, display_path


OURAIRPORTS_BASE = "https://davidmegginson.github.io/ourairports-data"
OVERPASS_ENDPOINTS = [
    "https://overpass-api.de/api/interpreter",
    "https://maps.mail.ru/osm/tools/overpass/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
]
USER_AGENT = "atc-kg-coursework/0.1"
FACILITY_TYPE_MAP = {
    "GND": "GND", "TWR": "TWR", "CLD": "DEL", "DEL": "DEL", "APP": "APP",
    "DEP": "DEP", "ATIS": "ATIS", "RMP": "RAMP", "RAMP": "RAMP",
}


def http_get(url: str, data: bytes = None, timeout: int = 120) -> bytes:
    req = urllib.request.Request(url, data=data, headers={"User-Agent": USER_AGENT, "Accept": "*/*"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def read_ourairports_csv(name: str):
    raw = http_get(f"{OURAIRPORTS_BASE}/{name}").decode("utf-8")
    return list(csv.DictReader(io.StringIO(raw)))


def strip_runway(designator: str) -> str:
    designator = designator.strip().upper()
    num = "".join(ch for ch in designator if ch.isdigit())
    side = "".join(ch for ch in designator if ch in "LRC")
    return (str(int(num)) if num else designator) + side


def fetch_overpass_taxiways(lat: float, lon: float, half_deg: float):
    bbox = f"{lat - half_deg},{lon - half_deg},{lat + half_deg},{lon + half_deg}"
    query = f'[out:json][timeout:90];way["aeroway"~"taxiway|taxilane"]({bbox});out tags;'
    payload = urllib.parse.urlencode({"data": query}).encode()

    last_error = None
    for endpoint in OVERPASS_ENDPOINTS:
        try:
            body = http_get(endpoint, data=payload)
            elements = json.loads(body).get("elements", [])
            refs = set()
            for element in elements:
                ref = element.get("tags", {}).get("ref")
                if not ref:
                    continue
                for part in ref.replace(",", ";").split(";"):
                    part = part.strip().upper()
                    if part and part.isalnum() and len(part) <= 4:
                        refs.add(part)
            return sorted(refs), endpoint
        except Exception as exc:
            last_error = exc
            time.sleep(2)

    raise RuntimeError(f"All Overpass endpoints failed: {last_error}")


def build_kb(icao: str, half_deg: float) -> dict:
    airports = [row for row in read_ourairports_csv("airports.csv") if row["ident"] == icao]
    if not airports:
        raise ValueError(f"Unknown airport ident: {icao}")
    airport = airports[0]
    lat, lon = float(airport["latitude_deg"]), float(airport["longitude_deg"])

    runways = []
    for row in read_ourairports_csv("runways.csv"):
        if row["airport_ident"] != icao or row.get("closed") == "1":
            continue
        ends = [strip_runway(row["le_ident"]), strip_runway(row["he_ident"])]
        runways.append({"id": "/".join(ends), "ends": ends, "length_ft": row.get("length_ft")})

    facilities = []
    for row in read_ourairports_csv("airport-frequencies.csv"):
        if row["airport_ident"] != icao:
            continue
        ftype = FACILITY_TYPE_MAP.get(row["type"].upper(), row["type"].upper())
        facilities.append({"type": ftype, "name": row["description"], "frequency": f"{float(row['frequency_mhz']):.3f}".rstrip("0").rstrip(".")})

    taxiways, endpoint = fetch_overpass_taxiways(lat, lon, half_deg)

    return {
        "icao": icao,
        "name": airport["name"],
        "city": airport.get("municipality", ""),
        "country": airport.get("iso_country", ""),
        "location": {"lat": lat, "lon": lon},
        "runways": runways,
        "runway_ends": sorted({end for rw in runways for end in rw["ends"]}),
        "taxiways": taxiways,
        "facilities": facilities,
        "provenance": {
            "runways": f"{OURAIRPORTS_BASE}/runways.csv",
            "facilities": f"{OURAIRPORTS_BASE}/airport-frequencies.csv",
            "taxiways": f"OpenStreetMap via {endpoint} (ODbL)",
            "built_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        },
    }


def main():
    parser = argparse.ArgumentParser(description="Build the static airport KB (M0).")
    parser.add_argument("--icao", nargs="+", default=["KATL"])
    parser.add_argument("--half-deg", type=float, default=0.03, help="bbox half size for taxiway lookup")
    parser.add_argument("--out-dir", type=Path, default=STATIC_KB_ROOT)
    args = parser.parse_args()

    for icao in args.icao:
        kb = build_kb(icao.upper(), args.half_deg)
        out_path = args.out_dir / f"{icao.lower()}.json"
        save_json(kb, out_path)
        print(f"[M0] {icao}: {len(kb['runways'])} runways, {len(kb['taxiways'])} taxiways, "
              f"{len(kb['facilities'])} facilities -> {display_path(out_path)}")


if __name__ == "__main__":
    main()
