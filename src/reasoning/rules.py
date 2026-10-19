"""M5: concept-level readback comparison and rule-based anomaly detection.

Rules (system_module.pdf, M5):
  rho1 MISMATCH / PARTIAL   readback concepts differ from / omit required concepts
  rho2 NO_READBACK          required instruction without any readback in the window
  rho3 NO_CALLSIGN          readback without callsign (FAA JO 7110.65 2-4-3)
  rho4 RWY_OVERLAP          same runway, different flights, overlapping validity (hint)
  rho5 POST_HANDOFF         instruction after the flight was handed to another facility
  rho6 KB_VIOLATION         target not present in the airport static KB
  rho7 SIMILAR_CALLSIGN     two active callsigns that differ by one digit (hint)
"""
import re
from typing import Dict, List, Optional, Set, Tuple

from common.atc_text import PHONETIC, find_runways, find_taxiways, normalize_text


def said_in(place_id: str, text: str) -> bool:
    """True if the controller's transmission text names this place (runway side must match exactly)."""
    kind, name = place_id.split(":")[0], place_id.split(":")[-1]
    norm = normalize_text(re.sub(r"[^\w\s]", " ", text))[0].lower()
    if kind == "Runway":
        return name.upper() in {rw.upper() for rw, _, _ in find_runways(norm)}
    numbers = set(re.findall(r"\d+", norm))
    if kind == "Taxiway":
        names = {tw.upper() for tw, _, _ in find_taxiways(norm)}
        letters = {PHONETIC[w] for w in PHONETIC if re.search(rf"\b{w}\b", norm)}
        m = re.fullmatch(r"([A-Z]+)(\d*)", name.upper())
        # exact name, or its letter is spoken and its number appears in the controller's text (a pilot's
        # "echo nineteen one" or "echo eight right" extracted as E19 / E8 is not a different taxiway)
        return name.upper() in names or bool(m and m.group(1) in letters and (not m.group(2) or m.group(2) in numbers
                                                                             or any(n.startswith(m.group(2)) for n in numbers)))
    if kind == "Ramp":
        return name in numbers
    return name.lower() in norm


def concept_match(a: dict, p: dict, atco_text: Optional[str] = None) -> Tuple[bool, List[str]]:
    """Compare one controller instruction with one pilot concept of the same type."""
    reasons = []
    if a.get("type") == "TAXI_VIA":
        # Destination vs route is not stable in either transmission ("nine left to Mike two taxi via Mike" may give
        # target 9L or M2, route [M]), so compare places as one set: the readback may omit places but must not name
        # a place the controller did not say. A place missing from the controller's extraction but present in the
        # controller's text (extraction miss) is not a conflict.
        said = {a.get("target_id")} | set(a.get("route_ids") or [])
        read = {p.get("target_id")} | set(p.get("route_ids") or [])
        extra = (read - said) - {None}
        if atco_text:
            extra = {x for x in extra if not said_in(x, atco_text)}
        if extra:
            reasons.append("place")
        return not reasons, reasons
    if a.get("target_id") and p.get("target_id") and a["target_id"] != p["target_id"]:
        reasons.append("target")
    if a.get("route_ids") and p.get("route_ids"):
        extra, omitted = set(p["route_ids"]) - set(a["route_ids"]), set(a["route_ids"]) - set(p["route_ids"])
        # Omitting part of a route is tolerated; substituting a taxiway is a conflict.
        if extra and (omitted or not set(a["route_ids"]) & set(p["route_ids"])):
            reasons.append("route")
    if a.get("type") in ("CONTACT", "MONITOR") and a.get("value") and p.get("value") and a["value"] != p["value"]:
        reasons.append("frequency")
    return not reasons, reasons


def compare_readback(atco_instructions: List[dict], pilot_instructions: List[dict], required: Set[str],
                     atco_text: Optional[str] = None) -> dict:
    req = [a for a in atco_instructions if a["type"] in required]
    pilot = [p for p in pilot_instructions if p["type"] not in ("ACK", "REQUEST")]

    matched, missing, conflicts = [], [], []
    for a in req:
        same_type = [p for p in pilot if p["type"] == a["type"]]
        if not same_type:
            missing.append({"ins_id": a["ins_id"], "type": a["type"], "target_id": a.get("target_id")})
            continue
        if a["type"] == "TAXI_VIA" and len(same_type) > 1:
            # a pilot may split one taxi route over several readback items: compare against their union
            places = {x.get("target_id") for x in same_type} | {r for x in same_type for r in x.get("route_ids") or []}
            same_type = [{**same_type[0], "target_id": None, "route_ids": sorted(places - {None})}]
        results = [(p, *concept_match(a, p, atco_text)) for p in same_type]
        hit = next((r for r in results if r[1]), None)
        if hit:
            matched.append({"ins_id": a["ins_id"], "pilot_ins_id": hit[0]["ins_id"], "type": a["type"]})
        else:
            p, _, reasons = results[0]
            conflicts.append({"ins_id": a["ins_id"], "pilot_ins_id": p["ins_id"], "type": a["type"],
                              "atco_target": a.get("target_id"), "pilot_target": p.get("target_id"),
                              "atco_route": a.get("route_ids"), "pilot_route": p.get("route_ids"),
                              "reasons": reasons})

    if not req:
        status = "NO_REQUIRED"
    elif conflicts:
        status = "MISMATCH"
    elif missing:
        status = "PARTIAL"
    else:
        status = "MATCH"
    ack_only = bool(req) and not pilot
    return {"status": status, "matched": matched, "missing": missing, "conflicts": conflicts,
            "ack_only": ack_only, "required": [a["ins_id"] for a in req]}


def _short(target, route) -> str:
    if target:
        return target.split(":")[-1]
    return "->".join(r.split(":")[-1] for r in (route or [])) or "?"


def _alert(rule: str, label: str, t: float, flight: Optional[str], evidence: List[str],
           message: str, **extra) -> dict:
    return {"rule": rule, "label": label, "utc_epoch": t, "flight": flight, "evidence": evidence,
            "message": message, **extra}


def detect_anomalies(graph, required: Set[str], runway_state_types: Set[str], rb_window: float,
                     handoff_window: float = 600.0) -> List[dict]:
    alerts = []
    tx_nodes = graph.nodes_by_label("Transmission")
    ins_nodes = graph.nodes_by_label("Instruction")

    readback_of = {}
    for e in graph.edges_by_type("READBACK_OF"):
        readback_of.setdefault(e["dst"], []).append(e)
        tx = graph.nodes[e["src"]]["props"]
        status = e["props"]["status"]
        if status in ("MISMATCH", "PARTIAL"):
            label = "rho1"
            msg = (f"readback {status.lower()}: " +
                   "; ".join(f"{c['type']} {_short(c.get('atco_target'), c.get('atco_route'))} vs {_short(c.get('pilot_target'), c.get('pilot_route'))}"
                             for c in e["props"]["conflicts"]) if status == "MISMATCH" else
                   f"readback {status.lower()}: missing " + ", ".join(m["type"] for m in e["props"]["missing"]))
            alerts.append(_alert(label, status, tx["utc_start_epoch"], tx.get("flight_id"), [e["src"], e["dst"]], msg,
                                 edge_id=e["id"]))
        if "NO_CALLSIGN" in tx.get("flags", []):
            alerts.append(_alert("rho3", "NO_CALLSIGN", tx["utc_start_epoch"], tx.get("flight_id"), [e["src"], e["dst"]],
                                 "readback without callsign; flight linked by context", edge_id=e["id"]))

    for node_id, node in tx_nodes.items():
        p = node["props"]
        if p["speaker"] != "ATCO" or not p.get("flight_id") or p["flight_id"].startswith("UNK"):
            continue
        req = [i for i in graph.instructions_of(node_id) if i["props"]["type"] in required]
        if req and node_id not in readback_of and not p.get("pilot_reply_in_window"):
            alerts.append(_alert("rho2", "NO_READBACK", p["utc_start_epoch"], p["flight_id"], [node_id],
                                 f"no readback within {rb_window:.0f}s for " + ", ".join(i["props"]["type"] for i in req)))
        if "SIMILAR_CALLSIGN" in p.get("flags", []):
            alerts.append(_alert("rho7", "SIMILAR_CALLSIGN", p["utc_start_epoch"], p["flight_id"], [node_id],
                                 f"{p['flight_id']} is one digit away from active {', '.join(p.get('callsign_candidates', []))}"))

    runway_states: Dict[str, List[dict]] = {}
    handoffs: Dict[str, List[dict]] = {}
    for node_id, node in ins_nodes.items():
        p = node["props"]
        if p["speaker"] != "ATCO":
            continue
        if "KB_VIOLATION" in p.get("flags", []):
            alerts.append(_alert("rho6", "KB_VIOLATION", p["valid_from"], p.get("flight_id"), [p["tx_node"], node_id],
                                 f"{p['type']} target {p.get('target_text') or ', '.join(r.split(':')[-1] for r in p.get('route_ids', []))} not in airport KB"))
        if p["type"] in runway_state_types and p.get("target_id", "") and p["target_id"].startswith("Runway"):
            runway_states.setdefault(p["target_id"], []).append({"id": node_id, **p})
        if p["type"] in ("MONITOR", "CONTACT"):
            handoffs.setdefault(p.get("flight_id") or "", []).append({"id": node_id, **p})

    for runway, states in runway_states.items():
        states.sort(key=lambda s: s["valid_from"])
        for i, a in enumerate(states):
            for b in states[i + 1:]:
                if b["valid_from"] >= (a["valid_to"] or float("inf")):
                    break
                if a["type"] == b["type"] == "CROSS_RUNWAY":
                    continue  # simultaneous crossings at different points are normal operations
                if a.get("flight_id") != b.get("flight_id"):
                    alerts.append(_alert("rho4", "RWY_OVERLAP", b["valid_from"], b.get("flight_id"), [a["id"], b["id"]],
                                         f"{runway.split(':')[-1]}: {a['type']} {a.get('flight_id')} overlaps {b['type']} {b.get('flight_id')}"))

    for node_id, node in ins_nodes.items():
        p = node["props"]
        if p["speaker"] != "ATCO" or p["type"] in ("MONITOR", "CONTACT") or not p.get("flight_id"):
            continue
        for h in handoffs.get(p["flight_id"], []):
            if h["feed"] == p["feed"] and 0 < p["valid_from"] - h["valid_from"] <= handoff_window \
                    and not (h.get("target_id") or "").endswith(":GND"):
                alerts.append(_alert("rho5", "POST_HANDOFF", p["valid_from"], p["flight_id"], [h["id"], node_id],
                                     f"{p['type']} issued after {h['type']} {(h.get('target_id') or '').split(':')[-1]} {h.get('value') or ''}"))
                break

    alerts.sort(key=lambda a: a["utc_epoch"])
    for k, a in enumerate(alerts):
        a["alert_id"] = f"A{k:05d}"
    return alerts
