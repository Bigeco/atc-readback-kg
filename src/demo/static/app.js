// ATC-KG strip board — vanilla JS client for src/demo/server.py
const S = {
  summary: null, hist: null, t: null, flight: null, alerts: [], alertFilter: new Set(),
  playing: null, cy: null, selectedAlert: null,
};
const $ = (sel) => document.querySelector(sel);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const hhmmss = (ep) => new Date(ep * 1000).toISOString().slice(11, 19);
const ICON_PLAY = '<svg viewBox="0 0 16 16" aria-hidden="true"><path d="M5 3.5v9l7-4.5z" fill="currentColor"/></svg>';
const STATIC = Boolean(window.ATC_STATIC);
const api = async (path) => {
  if (STATIC) return window.staticApi(path);
  const r = await fetch(path); if (!r.ok) throw new Error(await r.text()); return r.json();
};
const shortType = (t) => ({ TAXI_VIA: "TAXI", HOLD_SHORT: "HOLD SHORT", CROSS_RUNWAY: "CROSS", MONITOR: "MONITOR", CONTACT: "CONTACT",
  GIVE_WAY: "GIVE WAY", FOLLOW: "FOLLOW", HOLD_POSITION: "HOLD POS", LINE_UP_AND_WAIT: "LUAW", CLEARED_FOR_TAKEOFF: "CTO", CLEARED_TO_LAND: "CTL" }[t] || t);

// Public site: transcripts are not redistributed (LiveATC terms), so the extracted instructions stand in for the text.
function txText(m) {
  if (m.text) return `<div class="text">${esc(m.text)}</div>`;
  const ins = (m.instructions || []).filter((i) => !["REQUEST", "ACK"].includes(i.type)).map(insText);
  return `<div class="text redacted">${ins.length ? esc(ins.join(" · ")) : "no instruction"}<span class="redacted-note">transcript withheld</span></div>`;
}

function insText(i) {
  if (!i) return "";
  const parts = [shortType(i.type)];
  if (i.route && i.route.length) parts.push(i.route.join("→"));
  if (i.target) parts.push(i.type === "TAXI_VIA" ? `to ${i.target}` : i.target);
  if (i.value && ["MONITOR", "CONTACT"].includes(i.type)) parts.push(i.value);
  return parts.join(" ");
}

// ------------------------------------------------------------------ audio
// Local server mode only: /audio/clip cuts one transmission out of the archive with ffmpeg.
// The public static site carries no audio (LiveATC terms).
const player = $("#player");
let queue = [], playingTx = null;
function markPlaying() {
  document.querySelectorAll(".play-btn").forEach((b) => b.classList.toggle("on", b.dataset.tx === playingTx));
}
function playTx(txId, rest = []) {
  if (playingTx === txId && !player.paused) { player.pause(); queue = []; playingTx = null; markPlaying(); return; }
  queue = rest; playingTx = txId;
  player.src = `/audio/clip?tx=${encodeURIComponent(txId)}`;
  player.play().catch((e) => { toast(`Cannot play audio: ${e.message}`); playingTx = null; markPlaying(); });
  markPlaying();
}
player.addEventListener("ended", () => { const next = queue.shift(); next ? playTx(next, queue) : ((playingTx = null), markPlaying()); });
player.addEventListener("error", () => { toast("Audio not found (the source mp3 must be in data/raw/liveatc)."); playingTx = null; markPlaying(); });
const playBtn = (tx) => STATIC
  ? `<button class="play-btn off" title="No audio on the public site (LiveATC terms). Audio plays in the local server (scripts/run_demo.sh)." disabled>${ICON_PLAY}</button>`
  : `<button class="play-btn" data-tx="${esc(tx.tx_id)}" title="Play audio" onclick='playTx(${JSON.stringify(tx.tx_id)}); event.stopPropagation();'>${ICON_PLAY}</button>`;
function toast(msg) {
  const el = $("#toast"); el.textContent = msg; el.classList.add("show");
  clearTimeout(toast.t); toast.t = setTimeout(() => el.classList.remove("show"), 4000);
}

// ------------------------------------------------------------------ header + scrubber
function renderKpis(sm) {
  const rb = sm.readback_status || {};
  const items = [
    ["Transmissions", sm.transmissions ?? sm.turns], ["Flights", sm.flights], ["ATCO instructions", sm.atco_instructions],
    ["Match", rb.MATCH || 0, "match"], ["Partial", rb.PARTIAL || 0, "partial"], ["Mismatch", rb.MISMATCH || 0, "mismatch"],
    ["Alerts", Object.values(sm.alerts || {}).reduce((a, b) => a + b, 0)],
  ];
  const fmt = (v) => (v ?? "-").toLocaleString("en-US");
  $("#kpis").innerHTML = items.map(([k, v, c]) => `<div class="kpi ${c || ""}"><b>${fmt(v)}</b><span>${k}</span></div>`).join("");
}

function drawHistogram() {
  const cv = $("#histogram"), h = S.hist;
  const w = cv.clientWidth; cv.width = w * devicePixelRatio; cv.height = 56 * devicePixelRatio;
  const ctx = cv.getContext("2d"); ctx.scale(devicePixelRatio, devicePixelRatio);
  ctx.clearRect(0, 0, w, 56);
  const n = h.tx.length, bw = w / n, max = Math.max(...h.tx, 1);
  h.tx.forEach((v, i) => {
    const bh = (v / max) * 40;
    ctx.fillStyle = "#c3c2b7"; ctx.fillRect(i * bw, 46 - bh, Math.max(bw - 1, 1), bh);
    if (h.alerts[i]) { ctx.fillStyle = "#eb6834"; ctx.fillRect(i * bw, 49, Math.max(bw - 1, 1), Math.min(6, 2 + h.alerts[i])); }
  });
  ctx.fillStyle = "#898781"; ctx.font = "10px JetBrains Mono, monospace";
  for (let k = 0; k <= 6; k++) { const x = (k / 6) * w; ctx.fillText(hhmmss(h.start + (k / 6) * (h.end - h.start)).slice(0, 5), Math.min(x, w - 32), 10); }
  if (S.t !== null) {
    const x = ((S.t - h.start) / (h.end - h.start)) * w;
    ctx.strokeStyle = "#2a78d6"; ctx.lineWidth = 2; ctx.beginPath(); ctx.moveTo(x, 12); ctx.lineTo(x, 56); ctx.stroke();
  }
}

let timeDebounce = null;
function setTime(t, { refresh = true } = {}) {
  S.t = t;
  $("#clock").textContent = hhmmss(t);
  $("#time").value = Math.round(t - S.hist.start);
  drawHistogram();
  if (!refresh) return;
  clearTimeout(timeDebounce);
  timeDebounce = setTimeout(() => {
    loadStrips(); loadSnapshot(); if ($("#near-only").checked) renderAlerts();
    if ($("#tab-kg").classList.contains("active")) loadKg();
  }, 120);
}

// ------------------------------------------------------------------ strips
function worstStatus(rb) { return rb.MISMATCH ? "MISMATCH" : rb.PARTIAL ? "PARTIAL" : rb.MATCH ? "MATCH" : ""; }
async function loadStrips() {
  const q = $("#search").value.trim();
  const rows = await api(`/api/flights?t=${S.t}&window=600&q=${encodeURIComponent(q)}`);
  $("#bay-note").textContent = q ? `"${q}" ${rows.length}` : `±10 min · ${rows.length}`;
  $("#strips").innerHTML = rows.map((r) => {
    const rb = r.readback || {};
    const alerts = Object.values(r.alerts || {}).reduce((a, b) => a + b, 0);
    return `<div class="strip ${r.flight === S.flight ? "selected" : ""}" data-f="${esc(r.flight)}">
      <div class="band ${worstStatus(rb)}"></div>
      <div class="main"><div class="cs">${esc(r.flight)}</div>
        <div class="sub">${esc((r.surfaces || []).slice(0, 2).join(" · "))}</div>
        <div class="last">${esc(insText(r.last_instruction))}</div></div>
      <div class="stats"><span>${r.n_tx} tx</span>
        ${rb.MATCH ? `<span class="pill MATCH">✓${rb.MATCH}</span>` : ""}
        ${rb.PARTIAL ? `<span class="pill PARTIAL">◐${rb.PARTIAL}</span>` : ""}
        ${rb.MISMATCH ? `<span class="pill MISMATCH">✕${rb.MISMATCH}</span>` : ""}
        ${alerts ? `<span class="pill flag">${alerts} alerts</span>` : ""}</div></div>`;
  }).join("") || `<p class="hint" style="padding:8px">No transmissions around this time.</p>`;
  document.querySelectorAll(".strip").forEach((el) => el.addEventListener("click", () => selectFlight(el.dataset.f)));
}

// ------------------------------------------------------------------ airfield snapshot
const LANES = [
  ["TAXI_VIA", "Taxiing"], ["HOLD_SHORT", "Holding short"], ["HOLD_POSITION", "Holding position"], ["CROSS_RUNWAY", "Crossing"],
  ["LINE_UP_AND_WAIT", "Line up & wait"], ["CLEARED_FOR_TAKEOFF", "Cleared takeoff"], ["CLEARED_TO_LAND", "Cleared to land"],
  ["MONITOR", "Handed off (monitor)"], ["CONTACT", "Handed off (contact)"],
];
async function loadSnapshot() {
  const snap = await api(`/api/snapshot?t=${S.t}`);
  const total = Object.values(snap).reduce((a, v) => a + v.length, 0);
  const lanes = LANES.filter(([k]) => snap[k] && snap[k].length).map(([k, title]) => `
    <div class="lane"><h3>${title}<span>${snap[k].length}</span></h3><ul>
      ${snap[k].map((r) => `<li data-f="${esc(r.flight)}"><b>${esc(r.flight || "?")}</b>
        <span class="what">${esc([r.route && r.route.length ? r.route.join("→") : "", r.target ? (k === "TAXI_VIA" ? "to " + r.target : r.target) : "", r.value || ""].filter(Boolean).join(" ") || "—")}</span>
        <span title="since ${hhmmss(r.since)}"><span class="dot"></span></span></li>`).join("")}
    </ul></div>`).join("");
  $("#tab-airfield").innerHTML = `<div class="snap-title"><b>${hhmmss(S.t)}Z</b><small>${total} controller instructions valid at this time</small></div>
    <div class="lanes">${lanes || '<p class="hint">No state-setting instruction is valid at this time.</p>'}</div>`;
  document.querySelectorAll(".lane li").forEach((el) => el.addEventListener("click", () => el.dataset.f && selectFlight(el.dataset.f)));
}

// ------------------------------------------------------------------ flight timeline + graph
async function selectFlight(fid, { tab = "flight" } = {}) {
  S.flight = fid;
  document.querySelectorAll(".strip").forEach((el) => el.classList.toggle("selected", el.dataset.f === fid));
  const data = await api(`/api/flight/timeline?id=${encodeURIComponent(fid)}`);
  const c = data.card;
  $("#tab-flight").innerHTML = `
    <div class="flight-head"><span class="cs">${esc(fid)}</span>
      <span class="hint">${esc((c.surfaces || []).join(" · "))}</span>
      <span class="hint">${hhmmss(c.first)}–${hhmmss(c.last)}Z · ${c.n_tx} transmissions</span>
      ${Object.entries(c.readback || {}).map(([k, v]) => `<span class="pill ${k}">${k} ${v}</span>`).join(" ")}
      ${STATIC ? "" : `<button class="btn" id="play-all">${ICON_PLAY} Play all</button>`}</div>
    <div class="radio">${data.timeline.map(renderMsg).join("")}</div>`;
  document.querySelectorAll(".msg").forEach((el) => el.addEventListener("click", () => showEvidence(el.dataset.tx)));
  const all = data.timeline.map((m) => m.tx_id);
  if ($("#play-all")) $("#play-all").addEventListener("click", () => playTx(all[0], all.slice(1)));
  switchTab(tab);
  loadGraph(fid);
}

function renderMsg(m) {
  const rb = m.readback;
  const rbTag = rb ? `<span class="pill ${esc(rb.status)}">${m.speaker === "ATCO" ? "readback" : "reads back"} ${esc(rb.status)}</span>` : "";
  const flags = (m.flags || []).filter((f) => f !== "NO_CALLSIGN" || m.speaker === "PILOT").map((f) => `<span class="pill flag">${esc(f)}</span>`).join(" ");
  const chips = (m.instructions || []).filter((i) => !["REQUEST", "ACK"].includes(i.type))
    .map((i) => `<span class="chip ${m.speaker === "ATCO" ? "req" : ""}">${esc(insText(i))}</span>`).join("");
  return `<div class="msg ${esc(m.speaker)}" data-tx="${esc(m.tx_id)}">
    <div class="meta">${playBtn(m)}<span class="who">${esc(m.speaker)}</span><span>${hhmmss(m.utc_start_epoch)}Z</span>${rbTag}${flags}</div>
    ${txText(m)}${chips && m.text ? `<div class="chips">${chips}</div>` : ""}</div>`;
}

const NODE_COLORS = { Flight: "#eb6834", Transmission: "#2a78d6", Instruction: "#1baf7a", Runway: "#52514e", Taxiway: "#eda100",
  Ramp: "#e87ba4", Facility: "#6250d6", Airline: "#008300", CallsignMention: "#c3c2b7" };
async function loadGraph(fid) {
  const g = await api(`/api/flight/graph?id=${encodeURIComponent(fid)}`);
  const label = (n) => n.label === "Transmission" ? `${n.props.speaker} ${hhmmss(n.props.utc_start_epoch)}`
    : n.label === "Instruction" ? shortType(n.props.type) : n.label === "CallsignMention" ? `"${n.props.surface}"` : n.id.split(":").pop();
  const elements = [
    ...g.nodes.map((n) => ({ data: { id: n.id, label: label(n), kind: n.label } })),
    ...g.edges.map((e) => ({ data: { id: e.id, source: e.src, target: e.dst, label: e.type === "READBACK_OF" ? `READBACK ${e.props.status}` : e.type, type: e.type } })),
  ];
  if (S.cy) S.cy.destroy();
  S.cy = cytoscape({
    container: $("#cy"), elements,
    style: [
      { selector: "node", style: { label: "data(label)", "font-family": "JetBrains Mono, monospace", "font-size": 9, "text-valign": "bottom", "text-margin-y": 3,
        "background-color": (n) => NODE_COLORS[n.data("kind")] || "#999", width: 16, height: 16, color: "#0b0b0b" } },
      { selector: "node[kind = 'Flight']", style: { width: 30, height: 30, "font-size": 12, "font-weight": 600 } },
      { selector: "node[kind = 'Transmission']", style: { shape: "round-rectangle", width: 22, height: 12 } },
      { selector: "edge", style: { width: 1, "line-color": "#d6d5cf", "target-arrow-shape": "triangle", "target-arrow-color": "#d6d5cf", "curve-style": "bezier",
        label: "data(label)", "font-size": 7, color: "#898781", "text-rotation": "autorotate" } },
      { selector: "edge[type = 'READBACK_OF']", style: { width: 2.5, "line-color": "#eb6834", "target-arrow-color": "#eb6834", color: "#a8461f" } },
      { selector: "edge[type = 'NEXT']", style: { "line-style": "dashed", "line-color": "#2a78d6", "target-arrow-color": "#2a78d6" } },
    ],
    layout: { name: "cose", animate: false, nodeRepulsion: 9000, idealEdgeLength: 70 },
  });
  S.cy.on("tap", "node[kind = 'Transmission']", (ev) => showEvidence(ev.target.id()));
  $("#legend").innerHTML = Object.entries(NODE_COLORS).map(([k, c]) => `<span><i style="background:${c}"></i>${k}</span>`).join("") +
    `<span><i class="ln" style="background:#eb6834"></i>READBACK_OF</span><span><i class="ln dash" style="color:#2a78d6"></i>NEXT (time order)</span>`;
}

// ------------------------------------------------------------------ KG explorer (whole graph, one 10-min window)
const KG_BIN = 600;
const KG_COLORS = { ...NODE_COLORS, Airport: "#0b0b0b" };
const STATUS_COLORS = { MATCH: "#0a7d3c", PARTIAL: "#e0a100", MISMATCH: "#c93434" };
const KG = { mode: "entity", bin: null, cy: null, hidden: new Set(), data: null };
function kgLabel(n) {
  const p = n.props;
  if (n.label === "Transmission") return `${p.speaker} ${hhmmss(p.utc_start_epoch)}`;
  if (n.label === "Instruction") return shortType(p.type);
  if (n.label === "CallsignMention") return `"${p.surface}"`;
  if (n.label === "Airport") return p.location_indicator || n.id.split(":").pop();
  return n.id.split(":").pop();
}
async function loadKg(force = false) {
  if (!S.hist || S.t === null) return;
  const bin = Math.floor((S.t - S.hist.start) / KG_BIN);
  if (!force && bin === KG.bin && KG.cy) return;
  KG.bin = bin;
  const t0 = S.hist.start + bin * KG_BIN;
  $("#kg-window").textContent = `${hhmmss(t0).slice(0, 5)}–${hhmmss(t0 + KG_BIN).slice(0, 5)}Z`;
  const g = await api(`/api/kg?bin=${bin}&mode=${KG.mode}`);
  KG.data = g;
  const deg = {};
  g.edges.forEach((e) => { deg[e.src] = (deg[e.src] || 0) + 1; deg[e.dst] = (deg[e.dst] || 0) + 1; });
  const elements = [
    ...g.nodes.map((n) => ({ data: { id: n.id, label: kgLabel(n), kind: n.label, deg: deg[n.id] || 0 } })),
    ...g.edges.map((e) => ({ data: { id: e.id, source: e.src, target: e.dst, type: e.type,
      status: e.props.status || "", label: e.type === "INSTRUCTED" ? e.props.types.map(shortType).join(", ")
        : e.type === "READBACK_OF" ? `READBACK ${e.props.status}` : e.type } })),
  ];
  const positions = KG.mode === "event" ? eventPositions(g, t0, elements) : null;
  if (KG.mode === "event" && !KG.touched) ["Runway", "Taxiway", "Ramp", "Facility"].forEach((k) => KG.hidden.add(k));
  if (KG.cy) KG.cy.destroy();
  KG.cy = cytoscape({
    container: $("#kgcy"), elements, wheelSensitivity: 0.3, minZoom: 0.1, maxZoom: 3,
    style: [
      { selector: "node", style: { label: "data(label)", "font-family": "JetBrains Mono, monospace", "font-size": 8, color: "#52514e",
        "text-valign": "bottom", "text-margin-y": 2, "background-color": (n) => KG_COLORS[n.data("kind")] || "#999", "min-zoomed-font-size": 7,
        width: (n) => 8 + Math.min(18, Math.sqrt(n.data("deg")) * 3), height: (n) => 8 + Math.min(18, Math.sqrt(n.data("deg")) * 3),
        "border-width": 1.5, "border-color": "#fff" } },
      { selector: "node[kind = 'Flight']", style: { "font-size": 9, "font-weight": 600, color: "#0b0b0b" } },
      { selector: "node[kind = 'Airport']", style: { width: 34, height: 34, "font-size": 11, color: "#0b0b0b", "font-weight": 600 } },
      { selector: "node[kind = 'Transmission']", style: { shape: "round-rectangle" } },
      { selector: "node[kind = 'CallsignMention']", style: { shape: "diamond", "font-size": 7, "min-zoomed-font-size": 9, color: "#898781" } },
      { selector: "node[kind = 'Runway'], node[kind = 'Taxiway'], node[kind = 'Ramp'], node[kind = 'Facility']", style: { "font-size": 10, "font-weight": 600, color: "#0b0b0b" } },
      { selector: "edge", style: { width: 1, "line-color": "#dcdbd5", "curve-style": "bezier", "target-arrow-shape": "triangle",
        "target-arrow-color": "#dcdbd5", "arrow-scale": 0.6, label: "", "font-size": 7, color: "#52514e", "text-rotation": "autorotate",
        "text-background-color": "#fff", "text-background-opacity": 0.85, "text-background-padding": 1 } },
      { selector: "edge[type = 'PART_OF']", style: { "line-color": "#eeede8", "target-arrow-shape": "none" } },
      { selector: "edge[type = 'ALIAS']", style: { "line-style": "dotted", "line-color": "#b8b6ae", "target-arrow-shape": "none" } },
      { selector: "edge[type = 'INSTRUCTED']", style: { width: 1.6, "line-color": "#9ab8de", "target-arrow-color": "#9ab8de" } },
      ...Object.entries(STATUS_COLORS).map(([k, c]) => ({ selector: `edge[status = '${k}']`, style: { width: 2, "line-color": c, "target-arrow-color": c } })),
      { selector: "edge[type = 'NEXT']", style: { "line-style": "dashed", "line-color": "#2a78d6", "target-arrow-color": "#2a78d6" } },
      { selector: "node[kind = 'Tick']", style: { shape: "rectangle", width: 1, height: 8, "background-color": "#c3c2b7", "border-width": 0,
        "text-valign": "top", "text-margin-y": -3, "font-size": 9, color: "#898781", "min-zoomed-font-size": 0 } },
      { selector: "node[kind = 'Flight']", style: KG.mode === "event" ? { "text-valign": "center", "text-halign": "left", "text-margin-x": -6, "text-margin-y": 0 } : {} },
      { selector: "edge[hide = 1]", style: { display: "none" } },
      ...(KG.mode === "event" ? [   // the axis gives the time: transmissions unlabeled, instruction types small
        { selector: "node[kind = 'Transmission']", style: { label: "", width: 9, height: 9 } },
        { selector: "node[kind = 'Instruction']", style: { "font-size": 6, width: 7, height: 7, "text-margin-y": 1, "min-zoomed-font-size": 5 } },
      ] : []),
      { selector: ".faded", style: { opacity: 0.12 } },
      { selector: "edge.hl", style: { label: "data(label)", opacity: 1 } },
      { selector: "node.hl", style: { opacity: 1, "font-size": 10, color: "#0b0b0b", "min-zoomed-font-size": 0, label: "data(label)" } },
      { selector: "node:selected", style: { "border-color": "#2a78d6", "border-width": 3 } },
    ],
    layout: positions ? { name: "preset", positions: (n) => positions[n.id()], fit: false } : kgLayout(),
  });
  applyKgFilter();
  kgFit();
  KG.cy.on("tap", "node[kind != 'Tick']", (ev) => focusKgNode(ev.target));
  KG.cy.on("tap", (ev) => { if (ev.target === KG.cy) clearKgFocus(); });
  KG.cy.on("dbltap", "node", (ev) => openKgNode(ev.target.id(), ev.target.data("kind")));
  renderKgLegend();
}
// Event layer on a time axis: x = time in the 10-min window, one row per flight (instructions under their transmission).
// The rows carry ADDRESSES, so those edges are not drawn; places are hidden by default (toggle in the legend).
function eventPositions(g, t0, elements) {
  const X = 1.6, ROW = 34, pos = {};
  const tx = g.nodes.filter((n) => n.label === "Transmission");
  const firstT = {};
  tx.forEach((n) => { const f = n.props.flight_id || "-"; firstT[f] = Math.min(firstT[f] ?? Infinity, n.props.utc_start_epoch); });
  const rows = Object.keys(firstT).sort((a, b) => firstT[a] - firstT[b]);
  const rowOf = Object.fromEntries(rows.map((f, i) => [f, i * ROW + 40]));
  rows.forEach((f) => { if (f !== "-") pos[`Flight:${f}`] = { x: -30, y: rowOf[f] }; });
  const insOf = {};
  g.edges.filter((e) => e.type === "CONTAINS").forEach((e) => (insOf[e.src] ||= []).push(e.dst));
  tx.forEach((n) => {
    const x = (n.props.utc_start_epoch - t0) * X, y = rowOf[n.props.flight_id || "-"];
    pos[n.id] = { x, y };
    (insOf[n.id] || []).forEach((i, k) => (pos[i] = { x: x + k * 12, y: y + 12 }));
  });
  const places = g.nodes.filter((n) => ["Runway", "Taxiway", "Ramp", "Facility"].includes(n.label));
  places.forEach((n, i) => (pos[n.id] = { x: KG_BIN * X + 90 + (i % 3) * 60, y: 40 + Math.floor(i / 3) * 36 }));
  g.nodes.forEach((n) => (pos[n.id] ||= { x: -140, y: 0 }));
  for (let m = 0; m <= KG_BIN / 60; m++) {   // minute ticks along the top
    const id = `tick:${m}`;
    elements.push({ data: { id, label: hhmmss(t0 + m * 60).slice(0, 5), kind: "Tick", deg: 0 } });
    pos[id] = { x: m * 60 * X, y: 0 };
  }
  elements.forEach((el) => { if (el.data.type === "ADDRESSES") el.data.hide = 1; });
  return pos;
}

// fcose (spectral + force) separates hubs and clusters far better than plain cose; fall back if the CDN is blocked
const HAS_FCOSE = typeof cytoscapeFcose !== "undefined" && (cytoscape.use(cytoscapeFcose), true);
function kgLayout() {
  const len = { ALIAS: 18, OPERATED_BY: 70, PART_OF: 140, INSTRUCTED: 60, CONTAINS: 22, ADDRESSES: 45, TARGET: 50, VIA: 50, NEXT: 40, READBACK_OF: 35 };
  if (!HAS_FCOSE) return { name: "cose", animate: false, nodeRepulsion: 9000, idealEdgeLength: 55, gravity: 0.6, numIter: 1500 };
  return { name: "fcose", quality: "proof", animate: false, randomize: true, packComponents: true, nodeSeparation: 60,
    nodeRepulsion: () => (KG.mode === "entity" ? 9000 : 5000), idealEdgeLength: (e) => len[e.data("type")] || 50,
    edgeElasticity: (e) => (e.data("type") === "PART_OF" ? 0.05 : 0.45), gravity: 0.25, gravityRange: 3.8, numIter: 2500 };
}
function focusKgNode(n) {
  const hood = n.closedNeighborhood();
  KG.cy.elements().addClass("faded").removeClass("hl");
  hood.removeClass("faded").addClass("hl");
  const node = KG.data.nodes.find((x) => x.id === n.id());
  const rows = Object.entries(node.props).filter(([k, v]) => v !== null && v !== "" && !(Array.isArray(v) && !v.length) && !["layer", "iri", "backend"].includes(k))
    .map(([k, v]) => `<tr><td>${esc(k)}</td><td>${esc(typeof v === "number" && k.includes("epoch") ? hhmmss(v) + "Z" : Array.isArray(v) ? v.map((x) => String(x).split(":").pop()).join(", ") : String(v).split(":").pop())}</td></tr>`).join("");
  const links = n.connectedEdges().map((e) => {
    const other = e.source().id() === n.id() ? e.target() : e.source();
    return `<li><span class="et">${esc(e.data("type") === "INSTRUCTED" ? e.data("label") : e.data("type"))}</span>${e.source().id() === n.id() ? "→" : "←"} <b>${esc(other.data("label"))}</b> <small>${esc(other.data("kind"))}</small></li>`;
  }).join("");
  const act = node.label === "Flight" ? `<button class="btn" onclick="openKgNode('${esc(node.id)}','Flight')">Open flight timeline</button>`
    : node.label === "Transmission" ? `<button class="btn" onclick="openKgNode('${esc(node.id)}','Transmission')">Show evidence</button>` : "";
  $("#kg-detail").innerHTML = `<div class="kd-head"><span class="kd-kind" style="background:${KG_COLORS[node.label] || "#999"}"></span>
      <b>${esc(kgLabel(node))}</b><small>${esc(node.label)}</small><button class="close" onclick="clearKgFocus()">✕</button></div>
    <table>${rows}</table><h4>${n.connectedEdges().length} links</h4><ul>${links}</ul>${act}`;
  $("#kg-detail").classList.remove("hidden");
}
function clearKgFocus() {
  if (KG.cy) KG.cy.elements().removeClass("faded hl");
  $("#kg-detail").classList.add("hidden");
}
function openKgNode(id, kind) {
  if (kind === "Flight") selectFlight(id.split(":").slice(1).join(":"));
  else if (kind === "Transmission") showEvidence(id);
}
function applyKgFilter() {
  if (!KG.cy) return;
  KG.cy.nodes().forEach((n) => n.style("display", KG.hidden.has(n.data("kind")) ? "none" : "element"));
}
function renderKgLegend() {
  const kinds = [...new Set(KG.data.nodes.map((n) => n.label))].sort();
  const count = (k) => KG.data.nodes.filter((n) => n.label === k).length;
  const edgeKeys = KG.mode === "entity"
    ? [["MATCH", STATUS_COLORS.MATCH, "instructed · read back"], ["PARTIAL", STATUS_COLORS.PARTIAL, "partial"], ["MISMATCH", STATUS_COLORS.MISMATCH, "mismatch"], ["", "#9ab8de", "no readback link"]]
    : [["", STATUS_COLORS.MATCH, "READBACK_OF match"], ["", STATUS_COLORS.PARTIAL, "partial"], ["", STATUS_COLORS.MISMATCH, "mismatch"], ["", "#2a78d6", "NEXT"]];
  $("#kg-legend").innerHTML = kinds.map((k) => `<button data-k="${k}" class="${KG.hidden.has(k) ? "off" : ""}" title="Show / hide">
      <i style="background:${KG_COLORS[k] || "#999"}"></i>${k} <small>${count(k)}</small></button>`).join("") +
    `<span class="sep"></span>` + edgeKeys.map(([, c, t]) => `<span><i class="ln" style="background:${c}"></i>${t}</span>`).join("") +
    `<span class="hint">Click: neighbours · double-click: open</span>`;
  document.querySelectorAll("#kg-legend button").forEach((b) => b.addEventListener("click", () => {
    KG.hidden.has(b.dataset.k) ? KG.hidden.delete(b.dataset.k) : KG.hidden.add(b.dataset.k);
    KG.touched = true;
    b.classList.toggle("off"); applyKgFilter();
  }));
}
document.querySelectorAll("#kg-mode button").forEach((b) => b.addEventListener("click", () => {
  KG.mode = b.dataset.mode;
  document.querySelectorAll("#kg-mode button").forEach((x) => x.classList.toggle("on", x === b));
  KG.hidden.clear(); KG.touched = false; clearKgFocus(); loadKg(true);
}));
$("#kg-fit").addEventListener("click", kgFit);
// event layer: fit the time axis to the panel width and start at the top (scroll down by dragging the background)
function kgFit() {
  if (!KG.cy) return;
  if (KG.mode !== "event") { KG.cy.fit(undefined, 30); return; }
  const bb = KG.cy.elements(":visible").boundingBox(), z = Math.min(1.5, (KG.cy.width() - 60) / bb.w);
  KG.cy.zoom(z); KG.cy.pan({ x: 30 - bb.x1 * z, y: 24 - bb.y1 * z });
}
$("#kg-find").addEventListener("keydown", (e) => {
  if (e.key !== "Enter" || !KG.cy) return;
  const q = e.target.value.trim().toUpperCase();
  const hit = KG.cy.nodes().filter((n) => String(n.data("label")).toUpperCase().replace(/"/g, "") === q || n.id().toUpperCase().endsWith(":" + q))[0]
    || KG.cy.nodes().filter((n) => String(n.data("label")).toUpperCase().includes(q))[0];
  if (!hit) { toast(`"${e.target.value}" is not in this 10-min window`); return; }
  KG.cy.animate({ center: { eles: hit }, zoom: 1.6 }, { duration: 300 });
  hit.select(); focusKgNode(hit);
});

// ------------------------------------------------------------------ alerts + evidence
async function loadAlerts() {
  S.alerts = await api("/api/alerts");
  const counts = {};
  S.alerts.forEach((a) => (counts[a.label] = (counts[a.label] || 0) + 1));
  $("#alert-filters").innerHTML = Object.entries(counts).sort((a, b) => b[1] - a[1])
    .map(([k, v]) => `<button data-l="${k}">${k} ${v}</button>`).join("");
  document.querySelectorAll("#alert-filters button").forEach((b) => b.addEventListener("click", () => {
    S.alertFilter.has(b.dataset.l) ? S.alertFilter.delete(b.dataset.l) : S.alertFilter.add(b.dataset.l);
    b.classList.toggle("on"); renderAlerts();
  }));
  renderAlerts();
}

function renderAlerts() {
  const near = $("#near-only").checked;
  const rows = S.alerts.filter((a) => (!S.alertFilter.size || S.alertFilter.has(a.label)) && (!near || Math.abs(a.utc_epoch - S.t) <= 900));
  $("#alerts").innerHTML = rows.slice(0, 400).map((a) => `
    <div class="alert ${a.alert_id === S.selectedAlert ? "selected" : ""}" data-id="${a.alert_id}">
      <div class="t">${hhmmss(a.utc_epoch)}<br>${esc(a.flight || "—")}</div>
      <div><div class="lbl ${esc(a.label)}">${esc(a.label)} <span class="hint">${esc(a.rule)}</span></div>
        <div class="m" title="${esc(a.message)}">${esc(a.message)}</div></div></div>`).join("") ||
    `<p class="hint" style="padding:8px">No alerts match the filter.</p>`;
  document.querySelectorAll(".alert").forEach((el) => el.addEventListener("click", () => openAlert(el.dataset.id)));
}

async function openAlert(id) {
  const a = S.alerts.find((x) => x.alert_id === id);
  S.selectedAlert = id;
  renderAlerts();
  setTime(a.utc_epoch);
  const txId = a.evidence.find((e) => e.startsWith("Tx:")) || null;
  if (txId) await showEvidence(txId, a);
  if (a.flight && !a.flight.startsWith("UNK")) selectFlight(a.flight).catch(() => {});
}

async function showEvidence(txId, alert = null) {
  const tx = await api(`/api/tx?id=${encodeURIComponent(txId)}`);
  const pair = [tx, tx.counterpart].filter(Boolean).sort((x, y) => x.utc_start_epoch - y.utc_start_epoch);
  const rb = tx.readback ? tx.readback.props : null;
  const block = (t) => `<div class="ev-tx ${esc(t.speaker)}"><div class="meta">${playBtn(t)}<span>${hhmmss(t.utc_start_epoch)}</span>
      <span>${esc(t.speaker)}</span><span>${esc(t.flight || "")}</span></div>
      ${txText(t)}
      <div class="chips">${(t.instructions || []).filter((i) => !["REQUEST", "ACK"].includes(i.type)).map((i) => `<span class="chip">${esc(insText(i))}</span>`).join("")}
      ${(t.flags || []).map((f) => `<span class="pill flag">${esc(f)}</span>`).join("")}</div></div>`;
  let diff = "";
  if (rb) {
    const name = (id) => (id || "").split(":").pop();
    const rows = [
      ...rb.matched.map((m) => `<tr><td>${esc(m.type)}</td><td class="ok">matched</td><td></td></tr>`),
      ...rb.missing.map((m) => `<tr><td>${esc(m.type)}</td><td class="miss">not read back</td><td>${esc(name(m.target_id))}</td></tr>`),
      ...rb.conflicts.map((c) => `<tr><td>${esc(c.type)}</td><td class="bad">mismatch (${esc(c.reasons.join(","))})</td>
        <td>${esc(name(c.atco_target) || (c.atco_route || []).map(name).join("→"))} ≠ ${esc(name(c.pilot_target) || (c.pilot_route || []).map(name).join("→"))}</td></tr>`),
    ].join("");
    diff = `<table class="diff"><tr><th>Required</th><th>Result: <span class="pill ${esc(rb.status)}">${esc(rb.status)}</span></th><th>Detail</th></tr>${rows}</table>`;
  }
  $("#evidence").innerHTML = `<h3><span>${alert ? esc(alert.label) + " · " : ""}Evidence</span><button class="close" onclick="$('#evidence').classList.add('hidden')">✕</button></h3>
    ${alert ? `<div class="hint" style="font-size:12px">${esc(alert.message)}</div>` : ""}
    ${pair.map(block).join("")}${diff}`;
  $("#evidence").classList.remove("hidden");
}

// ------------------------------------------------------------------ evaluation tab
const CQ_EN = {
  CQ1: (p) => `Instructions given to ${p.flight || "a flight"}, in time order`,
  CQ2: (p) => `Flights told to hold short of ${p.target || "a place"}`,
  CQ3: () => "Instructions without a readback, or read back without a callsign",
  CQ4: () => "Readbacks whose taxiway or runway differs from the instruction",
  CQ5: () => "Callsigns repaired from the transcript, with the source transmission",
  CQ6: () => "Transmissions naming a taxiway or runway that is not in the airport KB",
  CQ7: () => "Flights still receiving ground instructions after hand-off to the tower",
  CQ8: (p) => `Ground state valid at ${p.t_utc || "time t"} (taxiing, holding, handed off)`,
};
const cqText = (r) => (CQ_EN[r.cq] ? CQ_EN[r.cq](r.params || {}) : r.question);
function renderEval(ev, sm) {
  const pct = (x) => (x === null || x === undefined ? "-" : `${(x * 100).toFixed(1)}%`);
  const cards = [];
  if (ev.e1_competency) {
    const e = ev.e1_competency;
    cards.push(`<div class="card"><h3>E1 · Competency questions</h3>
      <div class="big">${e.non_empty}/${e.n_cq}</div><div class="hint">answerable ${e.answerable} · traceable to source ${e.traceable}</div>
      <table><tr><th>CQ</th><th>Question</th><th>Answers</th><th>Traceable</th></tr>
      ${e.results.map((r) => `<tr><td>${r.cq}</td><td>${esc(cqText(r))}</td><td>${r.n_answers}</td><td>${r.traceable ? "✓" : "–"}</td></tr>`).join("")}</table></div>`);
  }
  if (ev.e3_injection) {
    const e = ev.e3_injection;
    cards.push(`<div class="card"><h3>E3 · Injected readback errors (n=${e.n_pairs} MATCH pairs)</h3>
      <div class="hint">False alarms on unperturbed pairs ${pct(e.control_false_alarm.rate)} (95% CI ${e.control_false_alarm.ci95.map(pct).join("–")})</div>
      <table><tr><th>Injected error</th><th>Detected</th><th></th><th>n</th></tr>
      ${Object.entries(e.detection).map(([k, r]) => `<tr><td>${esc(k)}</td><td>${pct(r.rate)}</td>
        <td style="width:40%"><div class="bar"><span style="width:${(r.rate || 0) * 100}%"></span></div></td><td>${r.n}</td></tr>`).join("")}</table></div>`);
  }
  if (ev.e2_precision && (ev.e2_precision.label || ev.e2_precision.label_llm)) {
    const e = ev.e2_precision.label || ev.e2_precision.label_llm;
    const who = ev.e2_precision.label ? "human labels" : "LLM labels, provisional";
    cards.push(`<div class="card"><h3>E2 · Precision against the source (${who})</h3>
      <div class="big">${pct(e.overall.strict.rate)}</div><div class="hint">95% CI ${e.overall.strict.ci95.map(pct).join("–")} · n=${e.overall.strict.n}</div>
      <table><tr><th>Relation</th><th>Precision</th><th>n</th></tr>${Object.entries(e.by_relation).map(([k, v]) => `<tr><td>${esc(k)}</td><td>${pct(v.strict.rate)}</td><td>${v.strict.n}</td></tr>`).join("")}</table></div>`);
  }
  cards.push(`<div class="card"><h3>KG contents</h3><table>
    ${Object.entries(sm.nodes || {}).map(([k, v]) => `<tr><td>${k}</td><td>${v}</td></tr>`).join("")}</table></div>`);
  $("#tab-eval").innerHTML = `<div class="eval-grid">${cards.join("")}</div>`;
}

// ------------------------------------------------------------------ tabs, play, boot
function switchTab(name) {
  document.querySelectorAll("#tabs button").forEach((b) => b.classList.toggle("active", b.dataset.tab === name));
  document.querySelectorAll(".tab").forEach((t) => t.classList.toggle("active", t.id === `tab-${name}`));
  if (name === "graph" && S.cy) S.cy.resize();
  if (name === "kg") { if (KG.cy) KG.cy.resize(); loadKg(); }
}
document.querySelectorAll("#tabs button").forEach((b) => b.addEventListener("click", () => switchTab(b.dataset.tab)));
$("#time").addEventListener("input", (e) => setTime(S.hist.start + Number(e.target.value)));
$("#search").addEventListener("input", () => { clearTimeout(timeDebounce); timeDebounce = setTimeout(loadStrips, 200); });
$("#near-only").addEventListener("change", renderAlerts);
$("#play").addEventListener("click", () => {
  if (S.playing) { clearInterval(S.playing); S.playing = null; $("#play").classList.remove("on"); return; }
  $("#play").classList.add("on");
  S.playing = setInterval(() => { const t = S.t + 20; if (t > S.hist.end) { $("#play").click(); return; } setTime(t); }, 400);
});
window.addEventListener("resize", drawHistogram);

(async function boot() {
  const data = await api("/api/summary");
  S.summary = data.summary; S.hist = data.histogram;
  const sm = data.summary;
  if (STATIC) document.body.classList.add("static-mode");
  const day = new Date(S.hist.start * 1000).toISOString().slice(0, 10);
  $("#run-label").textContent = `${sm.airport} · ${day} · ${hhmmss(S.hist.start).slice(0, 5)}–${hhmmss(S.hist.end).slice(0, 5)}Z`;
  if (STATIC) { $("#mode-badge").hidden = false; $("#download").hidden = false; }
  renderKpis(sm);
  $("#time").max = Math.round(S.hist.end - S.hist.start);
  const busiest = S.hist.tx.indexOf(Math.max(...S.hist.tx));
  setTime(S.hist.start + busiest * S.hist.bin_sec);
  renderEval(data.eval || {}, sm);
  loadAlerts();
})();
