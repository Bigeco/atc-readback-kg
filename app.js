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
  if (document.activeElement !== $("#clock")) $("#clock").value = hhmmss(t);
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

// ------------------------------------------------------------------ KG explorer (whole graph, one 10-min window)
const KG_BIN = 600;
const KG_COLORS = { ...NODE_COLORS, Airport: "#0b0b0b" };
const STATUS_COLORS = { MATCH: "#0a7d3c", PARTIAL: "#e0a100", MISMATCH: "#c93434" };
// Relation styles: edges carry no text; the key in the top-right corner explains each line style.
const EDGE_STYLES = {
  entity: [
    { type: "INSTRUCTED", status: "MATCH", color: STATUS_COLORS.MATCH, width: 2.4, name: "Instructed to · read back" },
    { type: "INSTRUCTED", status: "PARTIAL", color: STATUS_COLORS.PARTIAL, width: 2.4, name: "Instructed to · partial readback" },
    { type: "INSTRUCTED", status: "MISMATCH", color: STATUS_COLORS.MISMATCH, width: 2.4, name: "Instructed to · mismatch" },
    { type: "INSTRUCTED", status: "", color: "#8fb3e3", width: 1.6, name: "Instructed to · no readback link" },
    { type: "ALIAS", color: "#a9a79f", width: 1.2, dash: "dotted", name: "ALIAS · spoken as" },
    { type: "OPERATED_BY", color: "#7fbf7f", width: 1.2, name: "OPERATED_BY · airline" },
    { type: "PART_OF", color: "#dddcd6", width: 1, name: "PART_OF · airport" },
  ],
  event: [
    { type: "READBACK_OF", status: "MATCH", color: STATUS_COLORS.MATCH, width: 2.6, name: "READBACK_OF · match" },
    { type: "READBACK_OF", status: "PARTIAL", color: STATUS_COLORS.PARTIAL, width: 2.6, name: "READBACK_OF · partial" },
    { type: "READBACK_OF", status: "MISMATCH", color: STATUS_COLORS.MISMATCH, width: 2.6, name: "READBACK_OF · mismatch" },
    { type: "NEXT", color: "#2a78d6", width: 1.2, dash: "dashed", name: "NEXT · flight's next instruction" },
    { type: "CONTAINS", color: "#c3c2b7", width: 1, name: "CONTAINS · instruction in transmission" },
    { type: "TARGET", color: "#e0b45c", width: 1, name: "TARGET / VIA · place" },
    { type: "VIA", color: "#e0b45c", width: 1, name: null },
  ],
};
const KG = { mode: "entity", bin: null, cy: null, hidden: new Set(), data: null, focus: null, touched: false };
function kgLabel(n) {
  const p = n.props;
  if (n.label === "Transmission") return `${p.speaker} ${hhmmss(p.utc_start_epoch)}`;
  if (n.label === "Instruction") return shortType(p.type);
  if (n.label === "CallsignMention") return `"${p.surface}"`;
  if (n.label === "Airport") return p.location_indicator || n.id.split(":").pop();
  return n.id.split(":").pop();
}
function edgeText(d) {
  if (d.type === "INSTRUCTED") return `${d.types} → readback ${d.status || "none"}`;
  if (d.type === "READBACK_OF") return `READBACK_OF · ${d.status}`;
  return d.type;
}
function kgStyle() {
  const s = [
    { selector: "node", style: { label: "data(label)", "font-family": "Inter, sans-serif", "font-size": 9, "font-weight": 500, color: "#3d3c39",
      "text-valign": "bottom", "text-margin-y": 3, "text-outline-color": "#fff", "text-outline-width": 2.5, "text-outline-opacity": 0.95,
      "background-color": (n) => KG_COLORS[n.data("kind")] || "#999", "min-zoomed-font-size": 6,
      width: (n) => 10 + Math.min(20, Math.sqrt(n.data("deg")) * 3.2), height: (n) => 10 + Math.min(20, Math.sqrt(n.data("deg")) * 3.2),
      "border-width": 2, "border-color": "#fff", "transition-property": "opacity, width, height, border-width", "transition-duration": "180ms" } },
    // overview shows the names of well-connected flights; the rest appear when zooming in
    { selector: "node[kind = 'Flight']", style: { "font-family": "JetBrains Mono, monospace", "font-size": 10, "font-weight": 600, color: "#0b0b0b",
      "min-zoomed-font-size": (n) => (n.data("deg") >= 5 ? 5 : 9) } },
    { selector: "node[kind = 'Runway'], node[kind = 'Taxiway'], node[kind = 'Ramp'], node[kind = 'Facility']",
      style: { "font-size": 12, "font-weight": 700, color: "#0b0b0b", shape: "round-rectangle" } },
    { selector: "node[kind = 'Airport']", style: { width: 38, height: 38, "font-size": 13, "font-weight": 700, color: "#0b0b0b" } },
    { selector: "node[kind = 'Airline']", style: { shape: "hexagon", "font-size": 10, "font-weight": 600 } },
    { selector: "node[kind = 'CallsignMention']", style: { shape: "diamond", width: 8, height: 8, "font-size": 8, color: "#6f6e69", "min-zoomed-font-size": 11 } },
    { selector: "node[kind = 'Transmission']", style: { shape: "round-rectangle" } },
    { selector: "edge", style: { width: 1, "line-color": "#dcdbd5", "curve-style": "bezier", "target-arrow-shape": "triangle",
      "target-arrow-color": "#dcdbd5", "arrow-scale": 0.7, "transition-property": "opacity, width", "transition-duration": "180ms" } },
    ...EDGE_STYLES[KG.mode].map((e) => ({
      selector: `edge[type = '${e.type}']${e.status !== undefined ? `[status = '${e.status}']` : ""}`,
      style: { "line-color": e.color, "target-arrow-color": e.color, width: e.width, "line-style": e.dash || "solid",
        "target-arrow-shape": ["ALIAS", "PART_OF"].includes(e.type) ? "none" : "triangle" } })),
    { selector: "edge[hide = 1]", style: { display: "none" } },
    { selector: "node[kind = 'Tick']", style: { shape: "rectangle", width: 1, height: 8, "background-color": "#c3c2b7", "border-width": 0,
      "text-valign": "top", "text-margin-y": -3, "font-size": 9, color: "#898781", "min-zoomed-font-size": 0, events: "no" } },
  ];
  if (KG.mode === "event") s.push(
    { selector: "node[kind = 'Flight']", style: { "text-valign": "center", "text-halign": "left", "text-margin-x": -7, "text-margin-y": 0, "min-zoomed-font-size": 0 } },
    { selector: "node[kind = 'Transmission']", style: { label: "", width: 10, height: 10 } },
    // instruction names only on hover / focus, or once zoomed in far enough that they no longer collide (~2x)
    { selector: "node[kind = 'Instruction']", style: { "font-size": 7, width: 8, height: 8, "text-margin-y": 1, "min-zoomed-font-size": 14 } });
  s.push(
    { selector: ".dim", style: { opacity: 0.18 } },
    { selector: ".faded", style: { opacity: 0.08 } },
    { selector: "node.hl, node.hover", style: { opacity: 1, "min-zoomed-font-size": 0, label: "data(label)", "z-index": 10 } },
    { selector: "edge.hl, edge.hover", style: { opacity: 1, "z-index": 9 } },
    { selector: "node.ring", style: { "underlay-color": "#2a78d6", "underlay-opacity": 0.18, "underlay-padding": 6, "underlay-shape": "ellipse" } },
    { selector: "node.core", style: KG.mode === "event"
      ? { "border-color": "#2a78d6", "border-width": 3, width: 14, height: 14, "min-zoomed-font-size": 0 }
      : { "border-color": "#2a78d6", "border-width": 4, width: 34, height: 34, "font-size": 13 } });
  return s;
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
    ...g.edges.map((e) => ({ data: { id: e.id, source: e.src, target: e.dst, type: e.type, status: e.props.status || "",
      types: e.type === "INSTRUCTED" ? e.props.types.map(shortType).join(", ") : "" } })),
  ];
  const positions = KG.mode === "event" ? eventPositions(g, t0, elements) : null;
  if (KG.mode === "event" && !KG.touched) ["Runway", "Taxiway", "Ramp", "Facility"].forEach((k) => KG.hidden.add(k));
  if (KG.cy) KG.cy.destroy();
  KG.focus = null;
  $("#kg-detail").classList.add("hidden");
  $("#kg-tip").classList.remove("show");
  KG.cy = cytoscape({ container: $("#kgcy"), elements, style: kgStyle(), wheelSensitivity: 0.3, minZoom: 0.1, maxZoom: 3,
    autoungrabify: true,   // nodes are not draggable: positions carry meaning (time axis) and stay as laid out
    userZoomingEnabled: KG.mode !== "event", layout: { name: "null" } });
  KG.prevView = null;
  applyKgFilter();
  KG.cy.on("viewport", clampKgViewport);
  KG.cy.on("layoutstop", () => { setKgBounds(); clampKgViewport(); });
  if (positions) {   // nodes slide from the centre onto the time axis
    const c = { x: KG_BIN * 0.8, y: 200 };
    KG.cy.nodes().positions(() => c);
    KG.bounds = null;
    kgFit(positions);
    KG.cy.layout({ name: "preset", positions: (n) => positions[n.id()], fit: false, animate: true, animationDuration: 650, animationEasing: "ease-out-cubic" }).run();
  } else {
    KG.bounds = null;
    KG.cy.layout(kgLayout()).run();
  }
  bindKgEvents();
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

// fcose (spectral + force) separates hubs and clusters far better than plain cose; fall back if the CDN is blocked.
// The layout is animated so the graph settles in front of the viewer; "Re-layout" runs it again.
const HAS_FCOSE = typeof cytoscapeFcose !== "undefined" && (cytoscape.use(cytoscapeFcose), true);
function kgLayout() {
  const len = { ALIAS: 22, OPERATED_BY: 80, PART_OF: 150, INSTRUCTED: 70, CONTAINS: 22, TARGET: 50, VIA: 50, NEXT: 40, READBACK_OF: 35 };
  const base = { animate: true, animationDuration: 900, animationEasing: "ease-out-cubic", fit: true, padding: 40 };
  if (!HAS_FCOSE) return { ...base, name: "cose", nodeRepulsion: 9000, idealEdgeLength: 60, gravity: 0.6, numIter: 1500 };
  return { ...base, name: "fcose", quality: "proof", randomize: true, packComponents: true, nodeSeparation: 70,
    nodeRepulsion: () => 11000, idealEdgeLength: (e) => len[e.data("type")] || 55,
    edgeElasticity: (e) => (e.data("type") === "PART_OF" ? 0.05 : 0.45), gravity: 0.25, gravityRange: 3.8, numIter: 2500 };
}

// hover: light up a node's neighbourhood and show a tooltip; click: keep it lit, zoom to it and open the detail card
function bindKgEvents() {
  const cy = KG.cy, tip = $("#kg-tip");
  const showTip = (ev, html) => {
    const p = ev.renderedPosition || ev.target.renderedMidpoint();
    tip.innerHTML = html; tip.style.left = `${p.x + 14}px`; tip.style.top = `${p.y + 10}px`; tip.classList.add("show");
  };
  cy.on("mouseover", "node[kind != 'Tick']", (ev) => {
    const n = ev.target;
    $("#kgcy").style.cursor = "pointer";
    showTip(ev, `<b>${esc(n.data("label"))}</b><span>${esc(n.data("kind"))} · ${n.degree()} links</span>`);
    n.addClass("ring");
    if (KG.focus) return;
    cy.elements().addClass("dim");
    n.closedNeighborhood().removeClass("dim").addClass("hover");
  });
  cy.on("mouseover", "edge", (ev) => {
    const e = ev.target;
    showTip(ev, `<b>${esc(edgeText(e.data()))}</b><span>${esc(e.source().data("label"))} → ${esc(e.target().data("label"))}</span>`);
    if (!KG.focus) { cy.elements().addClass("dim"); e.union(e.connectedNodes()).removeClass("dim").addClass("hover"); }
  });
  cy.on("mouseout", "node, edge", () => {
    $("#kgcy").style.cursor = ""; tip.classList.remove("show"); cy.nodes(".ring").removeClass("ring");
    if (!KG.focus) cy.elements().removeClass("dim hover");
  });
  cy.on("tap", "node[kind != 'Tick']", (ev) => focusKgNode(ev.target, true));
  cy.on("tap", (ev) => { if (ev.target === cy) clearKgFocus(); });
  cy.on("dbltap", "node", (ev) => openKgNode(ev.target.id(), ev.target.data("kind")));
}
function focusKgNode(n, zoom = false) {
  const cy = KG.cy, hood = n.closedNeighborhood();
  KG.focus = n.id();
  cy.elements().removeClass("dim hover core").addClass("faded").removeClass("hl");
  hood.removeClass("faded").addClass("hl");
  n.addClass("core");
  if (zoom) zoomToKgNode(n, hood);
  ripple(n);
  const node = KG.data.nodes.find((x) => x.id === n.id());
  const rows = Object.entries(node.props).filter(([k, v]) => v !== null && v !== "" && !(Array.isArray(v) && !v.length) && !["layer", "iri", "backend"].includes(k))
    .map(([k, v]) => `<tr><td>${esc(k)}</td><td>${esc(typeof v === "number" && k.includes("epoch") ? hhmmss(v) + "Z" : Array.isArray(v) ? v.map((x) => String(x).split(":").pop()).join(", ") : String(v).split(":").pop())}</td></tr>`).join("");
  const links = n.connectedEdges().filter((e) => e.data("hide") !== 1).map((e) => {
    const out = e.source().id() === n.id(), other = out ? e.target() : e.source();
    const st = EDGE_STYLES[KG.mode].find((s) => s.type === e.data("type") && (s.status === undefined || s.status === e.data("status")));
    return `<li data-id="${esc(other.id())}"><i class="sw" style="background:${st ? st.color : "#ccc"}"></i><span class="et">${esc(e.data("type") === "INSTRUCTED" ? e.data("types") : e.data("type"))}</span>${out ? "→" : "←"} <b>${esc(other.data("label"))}</b> <small>${esc(other.data("kind"))}</small></li>`;
  }).join("");
  const act = node.label === "Flight" ? `<button class="btn" onclick="openKgNode('${esc(node.id)}','Flight')">Open flight timeline</button>`
    : node.label === "Transmission" ? `<button class="btn" onclick="openKgNode('${esc(node.id)}','Transmission')">Show evidence</button>` : "";
  $("#kg-detail").innerHTML = `<div class="kd-head"><span class="kd-kind" style="background:${KG_COLORS[node.label] || "#999"}"></span>
      <b>${esc(kgLabel(node))}</b><small>${esc(node.label)}</small><button class="close" onclick="clearKgFocus()">✕</button></div>
    <table>${rows}</table><h4>${n.connectedEdges().length} links <small>(click to jump)</small></h4><ul>${links}</ul>${act}`;
  $("#kg-detail").querySelectorAll("li[data-id]").forEach((li) => li.addEventListener("click", () => {
    const m = KG.cy.getElementById(li.dataset.id); if (m.nonempty()) focusKgNode(m, true);
  }));
  $("#kg-detail").classList.remove("hidden");
}
function clearKgFocus() {
  KG.focus = null;
  if (KG.cy) {
    KG.cy.elements().removeClass("faded hl dim hover core");
    if (KG.prevView) {   // fly back to where the viewer was before the first click
      KG.cy.stop(); KG.cy.animate({ zoom: KG.prevView.zoom, pan: KG.prevView.pan }, { duration: 500, easing: "ease-in-out-cubic", complete: clampKgViewport });
      KG.prevView = null;
    }
  }
  $("#kg-detail").classList.add("hidden");
}
// Click = fly to the node. Entity view: frame the node with its neighbours (at most 1.8x).
// Event layer: centre the node at a zoom where instruction names are readable, keeping the time axis.
function zoomToKgNode(n, hood) {
  const cy = KG.cy;
  if (!KG.prevView) KG.prevView = { zoom: cy.zoom(), pan: { ...cy.pan() } };
  const clampZ = (z) => Math.min(cy.maxZoom(), Math.max(cy.minZoom(), z));
  let level;
  if (KG.mode === "event") level = clampZ(Math.max(cy.zoom() * 1.6, 2.2));
  else {
    const bb = hood.boundingBox();
    level = clampZ(Math.min(1.8, (cy.width() - 180) / Math.max(bb.w, 1), (cy.height() - 180) / Math.max(bb.h, 1)));
  }
  const target = KG.mode === "event" ? n.position() : { x: (hood.boundingBox().x1 + hood.boundingBox().x2) / 2, y: (hood.boundingBox().y1 + hood.boundingBox().y2) / 2 };
  // centre in the free area right of the detail card
  const left = 320, cx = left + (cy.width() - left) / 2;
  cy.stop();
  cy.animate({ zoom: level, pan: { x: cx - target.x * level, y: cy.height() / 2 - target.y * level } },
    { duration: 650, easing: "ease-in-out-cubic", complete: clampKgViewport });
}
// a ring that expands and fades around the clicked node
function ripple(n) {
  n.stop(true);
  n.style({ "overlay-color": "#2a78d6", "overlay-opacity": 0.35, "overlay-padding": 3, "overlay-shape": "ellipse" });
  n.animate({ style: { "overlay-opacity": 0, "overlay-padding": 22 } }, { duration: 650, easing: "ease-out-cubic",
    complete: () => n.removeStyle("overlay-color overlay-opacity overlay-padding overlay-shape") });
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
  $("#kg-legend").innerHTML = kinds.map((k) => `<button data-k="${k}" class="${KG.hidden.has(k) ? "off" : ""}" title="Show / hide">
      <i style="background:${KG_COLORS[k] || "#999"}"></i>${k} <small>${count(k)}</small></button>`).join("") +
    `<span class="hint">${KG.mode === "event" ? "Scroll: move · Ctrl/⌘ + scroll: zoom" : "Scroll: zoom"} · click a node: fly to it · click empty space: back · double-click: open</span>`;
  document.querySelectorAll("#kg-legend button").forEach((b) => b.addEventListener("click", () => {
    KG.hidden.has(b.dataset.k) ? KG.hidden.delete(b.dataset.k) : KG.hidden.add(b.dataset.k);
    KG.touched = true;
    b.classList.toggle("off"); applyKgFilter(); setKgBounds(); clampKgViewport();
  }));
  // relation key (top-right of the canvas): one line sample per relation style present in this window
  const present = new Set(KG.data.edges.map((e) => `${e.type}|${e.props.status || ""}`));
  const keys = EDGE_STYLES[KG.mode].filter((s) => s.name && [...present].some((p) => p.startsWith(s.type + "|") && (s.status === undefined || p === `${s.type}|${s.status}`)));
  const line = (s) => `<svg viewBox="0 0 30 8" aria-hidden="true"><line x1="1" y1="4" x2="${["ALIAS", "PART_OF"].includes(s.type) ? 29 : 23}" y2="4" stroke="${s.color}" stroke-width="${s.width}"
      ${s.dash === "dotted" ? 'stroke-dasharray="1.5 2.5" stroke-linecap="round"' : s.dash === "dashed" ? 'stroke-dasharray="4 3"' : ""}/>${["ALIAS", "PART_OF"].includes(s.type) ? "" : `<path d="M23 1 29 4 23 7z" fill="${s.color}"/>`}</svg>`;
  $("#kg-edgekey").innerHTML = `<h4 title="Show / hide">Relations<span class="chev">▾</span></h4><div class="keys">` +
    keys.map((s) => `<div>${line(s)}<span>${esc(s.name)}</span></div>`).join("") + "</div>";
  $("#kg-edgekey h4").onclick = () => $("#kg-edgekey").classList.toggle("collapsed");
}
document.querySelectorAll("#kg-mode button").forEach((b) => b.addEventListener("click", () => {
  KG.mode = b.dataset.mode;
  document.querySelectorAll("#kg-mode button").forEach((x) => x.classList.toggle("on", x === b));
  KG.hidden.clear(); KG.touched = false; clearKgFocus(); loadKg(true);
}));
$("#kg-fit").addEventListener("click", () => kgFit());
$("#kg-relayout").addEventListener("click", () => { if (KG.cy && KG.mode === "entity") { clearKgFocus(); KG.cy.layout(kgLayout()).run(); } else kgFit(); });
// Viewport limits. Event layer: never narrower than the full time axis (min zoom = fit to width) and never panned past the
// content, so there is no empty canvas. Entity view: zoom out at most a little beyond "fit", and the graph stays in view.
const KG_PAD = 28;
function setKgBounds() {
  const cy = KG.cy;
  if (!cy) return;
  const bb = cy.elements(":visible").boundingBox();
  if (!bb.w) return;
  const w = cy.width() - 2 * KG_PAD, h = cy.height() - 2 * KG_PAD;
  const fitW = w / bb.w, fit = Math.min(fitW, h / Math.max(bb.h, 1));
  if (KG.mode === "event") { cy.minZoom(Math.min(fitW, 1.5)); cy.maxZoom(Math.max(3, fitW * 4)); }
  else { cy.minZoom(fit * 0.85); cy.maxZoom(Math.max(2.5, fit * 6)); }
  KG.bounds = bb;
  if (cy.zoom() < cy.minZoom()) {   // e.g. the panel got narrower: snap back to the smallest allowed view
    const z = cy.minZoom(), pan = cy.pan(), k = z / cy.zoom();
    cy.viewport({ zoom: z, pan: { x: pan.x * k, y: pan.y * k } });
  }
}
let clamping = false;
function clampKgViewport() {
  const cy = KG.cy;
  if (!cy || clamping || !KG.bounds || cy.animated() || KG.focus) return;   // a focused node may sit near an edge
  const bb = KG.bounds, z = cy.zoom(), pan = cy.pan(), W = cy.width(), H = cy.height();
  const axis = (lo, hi, size, p, alignStart) => {
    const len = (hi - lo) * z;
    if (len + 2 * KG_PAD <= size) return alignStart ? KG_PAD - lo * z : (size - len) / 2 - lo * z;
    return Math.min(KG_PAD - lo * z, Math.max(size - KG_PAD - hi * z, p));
  };
  const x = axis(bb.x1, bb.x2, W, pan.x, false), y = axis(bb.y1, bb.y2, H, pan.y, KG.mode === "event");
  if (Math.abs(x - pan.x) > 0.5 || Math.abs(y - pan.y) > 0.5) { clamping = true; cy.pan({ x, y }); clamping = false; }
}
// Event layer reads like a timeline: the wheel scrolls (shift = sideways), ctrl/cmd + wheel or a pinch zooms at the cursor.
$("#kgcy").addEventListener("wheel", (e) => {
  if (!KG.cy || KG.mode !== "event") return;
  e.preventDefault();
  const r = $("#kgcy").getBoundingClientRect();
  if (e.ctrlKey || e.metaKey) {
    const level = Math.min(KG.cy.maxZoom(), Math.max(KG.cy.minZoom(), KG.cy.zoom() * Math.exp(-e.deltaY * 0.0025)));
    KG.cy.zoom({ level, renderedPosition: { x: e.clientX - r.left, y: e.clientY - r.top } });
  } else {
    KG.cy.panBy(e.shiftKey ? { x: -e.deltaY, y: 0 } : { x: -e.deltaX, y: -e.deltaY });
  }
}, { passive: false });
window.addEventListener("resize", () => { if (KG.cy) { setKgBounds(); clampKgViewport(); } });

// event layer: fit the time axis to the panel width and start at the top (scroll down by dragging the background)
function kgFit(positions = null) {
  if (!KG.cy) return;
  if (KG.mode !== "event") { KG.cy.animate({ fit: { eles: KG.cy.elements(":visible"), padding: KG_PAD } }, { duration: 350 }); return; }
  const pts = positions ? Object.values(positions) : KG.cy.nodes(":visible").map((n) => n.position());
  const x1 = Math.min(...pts.map((p) => p.x)) - 60, x2 = Math.max(...pts.map((p) => p.x)) + 20, y1 = Math.min(...pts.map((p) => p.y)) - 20;
  const z = Math.min(1.5, (KG.cy.width() - 40) / (x2 - x1));
  KG.cy.zoom(z); KG.cy.pan({ x: 20 - x1 * z, y: 24 - y1 * z });
}
$("#kg-find").addEventListener("keydown", (e) => {
  if (e.key !== "Enter" || !KG.cy) return;
  const q = e.target.value.trim().toUpperCase().replace(/"/g, "");
  const nodes = KG.cy.nodes("[kind != 'Tick']");
  const hit = nodes.filter((n) => String(n.data("label")).toUpperCase().replace(/"/g, "") === q || n.id().toUpperCase().endsWith(":" + q))[0]
    || nodes.filter((n) => String(n.data("label")).toUpperCase().includes(q))[0];
  if (!hit) { toast(`"${e.target.value}" is not in this 10-min window`); return; }
  focusKgNode(hit, true);
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
// the clock is editable: "0133", "1:33", "01:33:20" + Enter jumps there (Esc cancels)
$("#clock").addEventListener("focus", (e) => e.target.select());
$("#clock").addEventListener("blur", (e) => { e.target.value = hhmmss(S.t); e.target.classList.remove("bad"); });
$("#clock").addEventListener("keydown", (e) => {
  if (e.key === "Escape") { e.target.blur(); return; }
  if (e.key !== "Enter") return;
  const m = e.target.value.trim().match(/^(\d{1,2}):?(\d{2})(?::?(\d{2}))?$/);
  const ok = m && +m[1] < 24 && +m[2] < 60 && +(m[3] || 0) < 60;
  if (!ok) { e.target.classList.add("bad"); toast("Enter a UTC time as HH:MM or HH:MM:SS"); return; }
  const day = Math.floor(S.hist.start / 86400) * 86400;
  let t = day + +m[1] * 3600 + +m[2] * 60 + +(m[3] || 0);
  if (t < S.hist.start || t > S.hist.end) {
    toast(`Data covers ${hhmmss(S.hist.start).slice(0, 5)}–${hhmmss(S.hist.end).slice(0, 5)}Z; moved to the nearest end`);
    t = Math.min(S.hist.end, Math.max(S.hist.start, t));
  }
  e.target.classList.remove("bad");
  setTime(t);
  e.target.blur();
});

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
