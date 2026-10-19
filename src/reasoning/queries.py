"""M6: time-aware queries over the temporal KG (also the competency questions of E1)."""
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
        "invalidated_by": p.get("invalidated_by"), "flags": p.get("flags", []),
        "tx_node": p["tx_node"], "readback_status": status,
    }


def transmission_view(graph, tx_id: str) -> dict:
    p = graph.nodes[tx_id]["props"]
    return {
        "tx_id": tx_id, "speaker": p["speaker"], "text": p.get("text"), "flight": p.get("flight_id"),
        "utc_start_epoch": p["utc_start_epoch"], "utc_end_epoch": p["utc_end_epoch"], "file": p["file"],
        "offset_start": p["offset_start"], "offset_end": p["offset_end"], "flags": p.get("flags", []),
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
            view["text"] = graph.nodes[node["props"]["tx_node"]]["props"].get("text")
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
                "since": p["valid_from"], "ins_id": nid,
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


def window_subgraph(graph, t0: float, t1: float, mode: str = "entity") -> dict:
    """Node-link view of the KG for transmissions starting in [t0, t1).

    entity: the linking result (M3) - CallsignMention -ALIAS- Flight -OPERATED_BY- Airline, and Flight -> place edges
            aggregated from the instructions (label = instruction types, status = worst readback status),
            places PART_OF the airport.
    event:  the temporal layer - Transmission, Instruction, READBACK_OF, NEXT, CONTAINS, ADDRESSES, TARGET, VIA.
    """
    txs = [nid for nid, n in graph.nodes.items()
           if n["label"] == "Transmission" and t0 <= n["props"]["utc_start_epoch"] < t1]
    keep, edges = set(), []

    def node(nid):
        n = graph.nodes[nid]
        return {"id": nid, "label": n["label"], "props": n["props"]}

    if mode == "event":
        for tx in txs:
            keep.add(tx)
            for e in graph.out_edges(tx):
                if e["type"] in ("ADDRESSES", "CONTAINS", "READBACK_OF"):
                    keep.add(e["dst"])
                    if e["type"] == "CONTAINS":
                        for t in graph.out_edges(e["dst"], "TARGET") + graph.out_edges(e["dst"], "VIA"):
                            keep.add(t["dst"])
        edges = [e for e in graph.edges if e["src"] in keep and e["dst"] in keep and e["type"] not in ("SPOKEN_BY", "PART_OF")]
        return {"nodes": [node(n) for n in keep], "edges": edges}

    rank = {None: 0, "MATCH": 1, "PARTIAL": 2, "MISMATCH": 3}
    links = {}
    for tx in txs:
        for e in graph.out_edges(tx, "ADDRESSES"):
            fid = e["dst"]
            keep.add(fid)
            for a in graph.out_edges(fid, "ALIAS") + graph.out_edges(fid, "OPERATED_BY"):
                keep.add(a["dst"])
        for ins in graph.instructions_of(tx):
            p = ins["props"]
            fid = f"Flight:{p['flight_id']}" if p.get("flight_id") else None
            if not fid or fid not in graph.nodes:
                continue
            rb = graph.in_edges(p["tx_node"], "READBACK_OF")
            status = rb[0]["props"]["status"] if rb else None
            for t in graph.out_edges(ins["id"], "TARGET") + graph.out_edges(ins["id"], "VIA"):
                key = (fid, t["dst"])
                link = links.setdefault(key, {"types": set(), "status": None, "n": 0})
                link["types"].add(p["type"])
                link["n"] += 1
                if rank[status] > rank[link["status"]]:
                    link["status"] = status
                keep.update(key)
    places = {n for n in keep if graph.nodes[n]["label"] in ("Runway", "Taxiway", "Ramp", "Facility")}
    for e in graph.edges:
        if e["src"] in keep and e["dst"] in keep and e["type"] in ("ALIAS", "OPERATED_BY"):
            edges.append(e)
    for pl in places:
        for e in graph.out_edges(pl, "PART_OF"):
            keep.add(e["dst"])
            edges.append(e)
    for (fid, dst), link in links.items():
        edges.append({"id": f"L:{fid}>{dst}", "type": "INSTRUCTED", "src": fid, "dst": dst,
                      "props": {"types": sorted(link["types"]), "status": link["status"], "n": link["n"]}})
    return {"nodes": [node(n) for n in keep], "edges": edges}
