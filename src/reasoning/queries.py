"""M8: time-aware queries over the temporal KG (also the competency questions of E1)."""
from collections import defaultdict
from typing import Dict, List, Optional


def _name(node_id: Optional[str]) -> Optional[str]:
    return node_id.split(":")[-1] if node_id else None


def instruction_view(graph, ins_id: str) -> dict:
    p = graph.nodes[ins_id]["props"]
    rb = graph.in_edges(p["tx_node"], "READBACK_OF")
    status = rb[0]["props"]["status"] if rb else None
    return {
        "ins_id": ins_id, "type": p["type"], "flight": p.get("flight_id"), "target": _name(p.get("target_id")),
        "route": [_name(r) for r in p.get("route_ids", [])], "value": p.get("value"), "qualifier": p.get("qualifier"),
        "valid_from": p["valid_from"], "valid_to": p.get("valid_to"), "state": p.get("state"),
        "invalidated_by": p.get("invalidated_by"), "conf_label": p.get("conf_label"), "flags": p.get("flags", []),
        "tx_node": p["tx_node"], "readback_status": status,
    }


def transmission_view(graph, tx_id: str) -> dict:
    p = graph.nodes[tx_id]["props"]
    return {
        "tx_id": tx_id, "speaker": p["speaker"], "text": p["text"], "flight": p.get("flight_id"),
        "utc_start_epoch": p["utc_start_epoch"], "utc_end_epoch": p["utc_end_epoch"], "file": p["file"],
        "offset_start": p["offset_start"], "offset_end": p["offset_end"], "flags": p.get("flags", []),
        "asr_conf": p.get("asr_conf"), "atis": p.get("atis"), "edits": p.get("edits", []),
        "instructions": [instruction_view(graph, n["id"]) for n in graph.instructions_of(tx_id)],
    }


def flight_timeline(graph, flight_id: str) -> List[dict]:
    """CQ1: transmissions addressed to / spoken by a flight in time order, with readback links."""
    fid = f"Flight:{flight_id}" if not flight_id.startswith("Flight:") else flight_id
    rows = []
    for edge in graph.in_edges(fid, "ADDRESSES"):
        view = transmission_view(graph, edge["src"])
        rb_out = graph.out_edges(edge["src"], "READBACK_OF")
        rb_in = graph.in_edges(edge["src"], "READBACK_OF")
        view["readback_of"] = rb_out[0]["dst"] if rb_out else None
        view["readback_by"] = rb_in[0]["src"] if rb_in else None
        view["readback"] = (rb_out or rb_in)[0]["props"] if (rb_out or rb_in) else None
        rows.append(view)
    rows.sort(key=lambda r: r["utc_start_epoch"])
    return rows


def instructions_at_target(graph, target: str, t0: float, t1: float, types=("HOLD_SHORT",)) -> List[dict]:
    """CQ2: ATCO instructions of the given types toward a target, issued in [t0, t1]."""
    out = []
    for nid, node in graph.nodes_by_label("Instruction").items():
        p = node["props"]
        if p["speaker"] == "ATCO" and p["type"] in types and _name(p.get("target_id")) == target \
                and t0 <= p["valid_from"] <= t1:
            out.append(instruction_view(graph, nid))
    return sorted(out, key=lambda r: r["valid_from"])


def readback_status_counts(graph) -> Dict[str, int]:
    counts = defaultdict(int)
    for e in graph.edges_by_type("READBACK_OF"):
        counts[e["props"]["status"]] += 1
    return dict(counts)


def alerts_by_label(alerts: List[dict], labels) -> List[dict]:
    """CQ3/CQ4/CQ7: alerts filtered by label."""
    labels = set(labels)
    return [a for a in alerts if a["label"] in labels]


def callsign_corrections(graph) -> List[dict]:
    """CQ5: flights whose callsign was repaired, with the surface forms and evidence transmissions."""
    out = []
    for e in graph.edges_by_type("ALIAS"):
        if "ASR_CORRECTED" in e["props"].get("flags", []):
            out.append({"flight": _name(e["src"]), "surface": graph.nodes[e["dst"]]["props"]["surface"],
                        "evidence": e["props"].get("prov", [])})
    return out


def kb_violations(graph) -> List[dict]:
    """CQ6: instructions whose target is not in the static airport KB."""
    out = []
    for nid, node in graph.nodes_by_label("Instruction").items():
        if "KB_VIOLATION" in node["props"].get("flags", []):
            view = instruction_view(graph, nid)
            view["text"] = graph.nodes[node["props"]["tx_node"]]["props"]["text"]
            out.append(view)
    return sorted(out, key=lambda r: r["valid_from"])


def snapshot(graph, t: float) -> dict:
    """CQ8: airfield state at time t (only ATCO state instructions whose validity contains t)."""
    state = defaultdict(list)
    for nid, node in graph.nodes_by_label("Instruction").items():
        p = node["props"]
        if p["speaker"] != "ATCO" or not p.get("state"):
            continue
        if p["valid_from"] <= t < (p.get("valid_to") or float("inf")):
            key = p["type"]
            state[key].append({
                "flight": p.get("flight_id"), "target": _name(p.get("target_id")),
                "route": [_name(r) for r in p.get("route_ids", [])], "value": p.get("value"),
                "since": p["valid_from"], "conf_label": p.get("conf_label"), "ins_id": nid,
            })
    for rows in state.values():
        rows.sort(key=lambda r: r["since"])
    return dict(state)


def flight_subgraph(graph, flight_id: str, max_tx: int = 40) -> dict:
    """Nodes/edges around one flight for visualization (Cytoscape elements)."""
    fid = f"Flight:{flight_id}"
    if fid not in graph.nodes:
        return {"nodes": [], "edges": []}
    keep = {fid}
    tx_ids = sorted((e["src"] for e in graph.in_edges(fid, "ADDRESSES")),
                    key=lambda t: graph.nodes[t]["props"]["utc_start_epoch"])[:max_tx]
    keep.update(tx_ids)
    for tx in tx_ids:
        for e in graph.out_edges(tx, "CONTAINS"):
            keep.add(e["dst"])
            for t in graph.out_edges(e["dst"], "TARGET") + graph.out_edges(e["dst"], "VIA"):
                keep.add(t["dst"])
    for e in graph.out_edges(fid):
        keep.add(e["dst"])

    nodes = [{"id": n, "label": graph.nodes[n]["label"], "props": graph.nodes[n]["props"]} for n in keep]
    edges = [e for e in graph.edges if e["src"] in keep and e["dst"] in keep and e["type"] != "PART_OF"]
    return {"nodes": nodes, "edges": edges}
