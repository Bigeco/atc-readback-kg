"""M4: incremental event-centric temporal knowledge graph.

- Event reification: Transmission and Instruction nodes (W3C n-ary pattern).
- Bi-temporal edges: valid_from / valid_to (when a fact holds) and ingested_at (when we learned it).
- Invalidation instead of deletion: superseded states get valid_to and invalidated_by.
- Readback linking: PILOT transmission -> ATCO transmission within the readback window.
"""
import csv
import time
from collections import defaultdict
from pathlib import Path
from typing import Dict, List, Optional, Set

from common.io_utils import load_json, save_json
from reasoning.rules import compare_readback


class TemporalEventGraph:
    def __init__(self):
        self.nodes: Dict[str, dict] = {}
        self.edges: List[dict] = []
        self._edge_index: Dict[str, dict] = {}
        self._out: Dict[str, List[dict]] = defaultdict(list)
        self._in: Dict[str, List[dict]] = defaultdict(list)

    # ---------------------------------------------------------------- primitives
    def add_node(self, node_id: str, label: str, **props) -> dict:
        if node_id in self.nodes:
            self.nodes[node_id]["props"].update({k: v for k, v in props.items() if v is not None})
        else:
            self.nodes[node_id] = {"id": node_id, "label": label, "props": props}
        return self.nodes[node_id]

    def add_edge(self, etype: str, src: str, dst: str, **props) -> dict:
        edge_id = f"{etype}:{src}->{dst}"
        if edge_id in self._edge_index:
            self._edge_index[edge_id]["props"].update(props)
            return self._edge_index[edge_id]
        props.setdefault("ingested_at", time.time())
        edge = {"id": edge_id, "type": etype, "src": src, "dst": dst, "props": props}
        self.edges.append(edge)
        self._edge_index[edge_id] = edge
        self._out[src].append(edge)
        self._in[dst].append(edge)
        return edge

    def nodes_by_label(self, label: str) -> Dict[str, dict]:
        return {nid: n for nid, n in self.nodes.items() if n["label"] == label}

    def edges_by_type(self, etype: str) -> List[dict]:
        return [e for e in self.edges if e["type"] == etype]

    def out_edges(self, node_id: str, etype: Optional[str] = None) -> List[dict]:
        return [e for e in self._out.get(node_id, []) if etype is None or e["type"] == etype]

    def in_edges(self, node_id: str, etype: Optional[str] = None) -> List[dict]:
        return [e for e in self._in.get(node_id, []) if etype is None or e["type"] == etype]

    def instructions_of(self, tx_id: str) -> List[dict]:
        return [self.nodes[e["dst"]] for e in self.out_edges(tx_id, "CONTAINS")]

    # ---------------------------------------------------------------- persistence
    def to_dict(self) -> dict:
        return {"nodes": list(self.nodes.values()), "edges": self.edges}

    def save(self, path: Path):
        save_json(self.to_dict(), path, indent=None)

    @classmethod
    def load(cls, path: Path) -> "TemporalEventGraph":
        data = load_json(path)
        graph = cls()
        for node in data["nodes"]:
            graph.nodes[node["id"]] = node
        for edge in data["edges"]:
            graph.edges.append(edge)
            graph._edge_index[edge["id"]] = edge
            graph._out[edge["src"]].append(edge)
            graph._in[edge["dst"]].append(edge)
        return graph

    def export_neo4j(self, out_dir: Path):
        """Write nodes.csv / relationships.csv and a LOAD CSV script for Neo4j."""
        out_dir.mkdir(parents=True, exist_ok=True)
        with open(out_dir / "nodes.csv", "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(["id", "label", "props_json"])
            for node in self.nodes.values():
                writer.writerow([node["id"], node["label"], _json(node["props"])])
        with open(out_dir / "relationships.csv", "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(["src", "dst", "type", "props_json"])
            for edge in self.edges:
                writer.writerow([edge["src"], edge["dst"], edge["type"], _json(edge["props"])])
        (out_dir / "import.cypher").write_text(NEO4J_IMPORT, encoding="utf-8")


def _json(obj) -> str:
    import json
    return json.dumps(obj, ensure_ascii=False)


NEO4J_IMPORT = """// Requires APOC. Copy nodes.csv / relationships.csv into the Neo4j import directory.
LOAD CSV WITH HEADERS FROM 'file:///nodes.csv' AS row
CALL apoc.create.node([row.label], apoc.map.merge({id: row.id}, apoc.convert.fromJsonMap(row.props_json))) YIELD node
RETURN count(node);

CREATE INDEX node_id IF NOT EXISTS FOR (n:Transmission) ON (n.id);

LOAD CSV WITH HEADERS FROM 'file:///relationships.csv' AS row
MATCH (a {id: row.src}), (b {id: row.dst})
CALL apoc.create.relationship(a, row.type, apoc.convert.fromJsonMap(row.props_json), b) YIELD rel
RETURN count(rel);
"""


class GraphBuilder:
    def __init__(self, kb: dict, ontology: dict, rules: dict, build_cfg: dict, region: str = "US",
                 rb_window: float = 20.0, context_link_window: float = 8.0, airline_country: Optional[dict] = None):
        self.kb = kb
        self.airline_country = airline_country or {}  # ICAO designator -> ISO country (airlines.json), for RQ4
        self.ontology = ontology
        self.icao = kb.get("icao", "UNKNOWN")
        rule_set = rules["region_rules"][region]
        self.required: Set[str] = set(rules["readback_required"][rule_set])
        self.state_types = {k for k, v in ontology["command_types"].items() if v["state"]}
        self.supersedes = rules["supersedes"]
        self.max_validity = build_cfg["max_validity_sec"]
        self.default_validity = build_cfg["default_max_validity_sec"]
        self.rb_window = rb_window
        self.context_link_window = context_link_window
        self.graph = TemporalEventGraph()
        self.open_states: Dict[str, List[str]] = defaultdict(list)
        self.last_instruction: Dict[str, str] = {}
        self.recent_atco: Dict[str, List[str]] = defaultdict(list)
        self.read_back: Set[str] = set()
        self._add_static_layer()

    # ---------------------------------------------------------------- static layer (M0)
    def _add_static_layer(self):
        g = self.graph
        airport = f"Airport:{self.icao}"
        g.add_node(airport, "Airport", location_indicator=self.icao, name=self.kb.get("name"), country=self.kb.get("country"), layer="static",
                   iri="atm:Airport")
        for end in self.kb.get("runway_ends", []):
            nid = f"Runway:{self.icao}:{end}"
            g.add_node(nid, "Runway", name=end, layer="static", iri="atm:Runway")
            g.add_edge("PART_OF", nid, airport)
        for tw in self.kb.get("taxiways", []):
            nid = f"Taxiway:{self.icao}:{tw}"
            g.add_node(nid, "Taxiway", name=tw, layer="static", iri="atm:Taxiway")
            g.add_edge("PART_OF", nid, airport)
        for fac in self.kb.get("facilities", []):
            nid = f"Facility:{self.icao}:{fac['type']}"
            g.add_node(nid, "Facility", name=fac["name"], type=fac["type"], layer="static", iri="atm:ATCFacility")
            freqs = g.nodes[nid]["props"].setdefault("frequencies", [])
            if fac["frequency"] not in freqs:
                freqs.append(fac["frequency"])
            g.add_edge("PART_OF", nid, airport)
        for role in ("ATCO", "PILOT"):
            g.add_node(f"Role:{role}", "Role", name=role, layer="static")

    def _ensure_target(self, target_id: Optional[str], kind: Optional[str], text: Optional[str]):
        if target_id and target_id not in self.graph.nodes:
            self.graph.add_node(target_id, kind or "Unknown", name=target_id.split(":")[-1], layer="dynamic",
                                in_kb=False, surface=text)
            self.graph.add_edge("PART_OF", target_id, f"Airport:{self.icao}")

    # ---------------------------------------------------------------- dynamic layer
    def add_turn(self, rec: dict):
        g = self.graph
        tx_id = f"Tx:{rec['tx_id']}"
        speaker = rec["speaker"] if rec["speaker"] in ("ATCO", "PILOT") else "UNKNOWN"
        flags = list(rec["callsign"]["flags"])
        flight_id = rec["flight_id"]
        t0, t1 = rec["utc_start_epoch"], rec["utc_end_epoch"]

        # Pilot without callsign: link to the controller transmission it most likely answers.
        linked_from = None
        if speaker == "PILOT" and not flight_id:
            linked_from = self._context_link(rec)
            if linked_from:
                flight_id = g.nodes[linked_from]["props"]["flight_id"]
                flags.append("LINKED_BY_CONTEXT")

        g.add_node(tx_id, "Transmission", tx_id=rec["tx_id"], speaker_id=rec.get("speaker_id"), speaker=speaker, text=rec["text"],
                   feed=rec["feed"], facility_class=rec["facility_class"],
                   file=rec["file"], offset_start=rec["start"], offset_end=rec["end"],
                   utc_start_epoch=t0, utc_end_epoch=t1, flight_id=flight_id, callsign_surface=rec["callsign"]["surface"],
                   callsign_candidates=rec["callsign"].get("candidates", []), flags=sorted(set(flags)),
                   backend=rec.get("backend"),
                   layer="dynamic")
        if speaker in ("ATCO", "PILOT"):
            g.add_edge("SPOKEN_BY", tx_id, f"Role:{speaker}", valid_from=t0, valid_to=t1, prov=[rec["tx_id"]])

        if flight_id:
            fid = f"Flight:{flight_id}"
            airline = rec["callsign"].get("airline")
            g.add_node(fid, "Flight", callsign=flight_id, airline=airline, layer="dynamic")
            g.add_edge("ADDRESSES", tx_id, fid, valid_from=t0, valid_to=t1, prov=[rec["tx_id"]])
            if airline and airline not in ("N",):
                g.add_node(f"Airline:{airline}", "Airline", designator=airline, country=self.airline_country.get(airline),
                           layer="static")
                g.add_edge("OPERATED_BY", fid, f"Airline:{airline}")
            if rec["callsign"]["surface"]:
                cs_id = f"CallsignMention:{rec['callsign']['surface']}"
                g.add_node(cs_id, "CallsignMention", surface=rec["callsign"]["surface"], layer="dynamic")
                alias = g.add_edge("ALIAS", fid, cs_id, prov=[], flags=[])
                alias["props"]["prov"] = sorted(set(alias["props"]["prov"]) | {rec["tx_id"]})
                if "ASR_CORRECTED" in flags:
                    alias["props"]["flags"] = ["ASR_CORRECTED"]

        instructions = []
        for inst in rec["instructions"]:
            ins_id = f"Ins:{inst['ins_id']}"
            is_state = speaker == "ATCO" and inst["type"] in self.state_types
            g.add_node(ins_id, "Instruction", type=inst["type"], speaker=speaker, flight_id=flight_id,
                       target_id=inst["target_id"], target_text=inst["target_text"], route_ids=inst["route_ids"],
                       value=inst["value"], qualifier=inst["qualifier"], flags=inst["flags"], feed=rec["feed"],
                       valid_from=t0, valid_to=None if is_state else t1, state=is_state, tx_node=tx_id,
                       layer="dynamic")
            g.add_edge("CONTAINS", tx_id, ins_id, valid_from=t0, valid_to=t1, prov=[rec["tx_id"]])
            if inst["target_id"]:
                self._ensure_target(inst["target_id"], inst["target_kind"], inst["target_text"])
                g.add_edge("TARGET", ins_id, inst["target_id"], valid_from=t0, prov=[rec["tx_id"]])
            for rid in inst["route_ids"]:
                self._ensure_target(rid, "Taxiway", None)
                g.add_edge("VIA", ins_id, rid, valid_from=t0, prov=[rec["tx_id"]])
            instructions.append({**inst, "ins_id": ins_id})

            if speaker == "ATCO" and flight_id:
                self._supersede(flight_id, inst["type"], ins_id, t0)
                if is_state:
                    self.open_states[flight_id].append(ins_id)
                prev = self.last_instruction.get(flight_id)
                if prev:
                    g.add_edge("NEXT", prev, ins_id, dt=round(t0 - g.nodes[prev]["props"]["valid_from"], 2))
                self.last_instruction[flight_id] = ins_id

        if speaker == "ATCO":
            self.recent_atco[rec["feed"]].append(tx_id)
            self.recent_atco[rec["feed"]] = self.recent_atco[rec["feed"]][-30:]
        elif speaker == "PILOT" and flight_id:
            self._link_readback(tx_id, flight_id, rec["feed"], t0, instructions, linked_from)

    def _supersede(self, flight_id: str, new_type: str, new_id: str, t: float):
        rules = self.supersedes.get(new_type, [])
        if not rules:
            return
        still_open = []
        for sid in self.open_states[flight_id]:
            s = self.graph.nodes[sid]["props"]
            if "*" in rules or s["type"] in rules:
                s["valid_to"] = t
                s["invalidated_by"] = new_id
            else:
                still_open.append(sid)
        self.open_states[flight_id] = still_open

    def _context_link(self, rec: dict) -> Optional[str]:
        """Most recent ATCO transmission in the same feed whose concepts overlap the pilot's (for no-callsign readbacks)."""
        t0 = rec["utc_start_epoch"]
        pilot_concepts = {(i["type"], i["target_id"]) for i in rec["instructions"]} | \
                         {("ROUTE", r) for i in rec["instructions"] for r in i["route_ids"]}
        for tx_id in reversed(self.recent_atco[rec["feed"]]):
            p = self.graph.nodes[tx_id]["props"]
            gap = t0 - p["utc_end_epoch"]
            if gap > self.rb_window:
                break
            if gap < -1 or not p.get("flight_id") or tx_id in self.read_back:
                continue
            atco_concepts = {(i["props"]["type"], i["props"]["target_id"]) for i in self.graph.instructions_of(tx_id)} | \
                            {("ROUTE", r) for i in self.graph.instructions_of(tx_id) for r in i["props"]["route_ids"]}
            if pilot_concepts & atco_concepts:
                return tx_id
        return None

    def _link_readback(self, pilot_tx: str, flight_id: str, feed: str, t0: float, pilot_instrs: List[dict],
                       linked_from: Optional[str]):
        g = self.graph
        target = linked_from
        if target is None:
            for tx_id in reversed(self.recent_atco[feed]):
                p = g.nodes[tx_id]["props"]
                gap = t0 - p["utc_end_epoch"]
                if gap > self.rb_window:
                    break
                if gap >= -1 and p.get("flight_id") == flight_id:
                    target = tx_id
                    break
        if target is None:
            return

        g.nodes[target]["props"]["pilot_reply_in_window"] = True
        atco_instrs = [{**n["props"], "ins_id": n["id"]} for n in g.instructions_of(target)]
        result = compare_readback(atco_instrs, pilot_instrs, self.required)
        if result["status"] == "NO_REQUIRED":
            return
        concepts = [i for i in pilot_instrs if i["type"] not in ("ACK", "REQUEST")]
        is_ack = any(i["type"] == "ACK" for i in pilot_instrs)
        if not concepts and not is_ack:
            return  # chatter in the window (e.g. "thanks"): neither a readback nor an acknowledgement

        g.add_edge("READBACK_OF", pilot_tx, target, valid_from=t0, prov=[pilot_tx, target], **result)
        self.read_back.add(target)

        matched = {m["ins_id"] for m in result["matched"]}
        for inst in atco_instrs:
            node = g.nodes[inst["ins_id"]]["props"]
            if inst["ins_id"] in matched:
                node["corroborated_by"] = pilot_tx

    def finalize(self):
        """Close states that were never superseded at their maximum validity."""
        for node in self.graph.nodes_by_label("Instruction").values():
            p = node["props"]
            if p["state"] and p["valid_to"] is None:
                p["valid_to"] = p["valid_from"] + self.max_validity.get(p["type"], self.default_validity)
                p["expired"] = True
        return self.graph
