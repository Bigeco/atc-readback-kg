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
  player.play().catch((e) => { toast(`음성을 재생할 수 없습니다: ${e.message}`); playingTx = null; markPlaying(); });
  markPlaying();
}
player.addEventListener("ended", () => { const next = queue.shift(); next ? playTx(next, queue) : ((playingTx = null), markPlaying()); });
player.addEventListener("error", () => { toast("음성 파일을 불러오지 못했습니다 (data/raw/liveatc 에 원본 mp3가 있어야 합니다)."); playingTx = null; markPlaying(); });
const playBtn = (tx) => STATIC
  ? `<button class="play-btn off" title="공개 사이트에는 원음이 없습니다 (LiveATC 약관). 로컬 서버(scripts/run_demo.sh)에서 재생됩니다." disabled>${ICON_PLAY}</button>`
  : `<button class="play-btn" data-tx="${esc(tx.tx_id)}" title="원음 재생" onclick='playTx(${JSON.stringify(tx.tx_id)}); event.stopPropagation();'>${ICON_PLAY}</button>`;
function toast(msg) {
  const el = $("#toast"); el.textContent = msg; el.classList.add("show");
  clearTimeout(toast.t); toast.t = setTimeout(() => el.classList.remove("show"), 4000);
}

// ------------------------------------------------------------------ header + scrubber
function renderKpis(sm) {
  const rb = sm.readback_status || {};
  const items = [
    ["Transmissions", sm.turns], ["Flights", sm.flights], ["ATCO instructions", sm.atco_instructions],
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
  timeDebounce = setTimeout(() => { loadStrips(); loadSnapshot(); if ($("#near-only").checked) renderAlerts(); }, 120);
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
  }).join("") || `<p class="hint" style="padding:8px">이 시점 주변에 교신이 없습니다.</p>`;
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
  $("#tab-airfield").innerHTML = `<div class="snap-title"><b>${hhmmss(S.t)}Z</b><small>유효 구간이 이 시점을 포함하는 관제 지시 ${total}건</small></div>
    <div class="lanes">${lanes || '<p class="hint">이 시점에 유효한 상태형 지시가 없습니다.</p>'}</div>`;
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
      ${STATIC ? "" : `<button class="btn" id="play-all">${ICON_PLAY} 교신 전체 듣기</button>`}</div>
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
    <div class="text">${esc(m.text)}</div>${chips ? `<div class="chips">${chips}</div>` : ""}</div>`;
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
    `<span><i class="ln" style="background:#eb6834"></i>READBACK_OF</span><span><i class="ln dash" style="color:#2a78d6"></i>NEXT (시간순)</span>`;
}

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
    `<p class="hint" style="padding:8px">조건에 맞는 경보가 없습니다.</p>`;
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
      <div>${esc(t.text)}</div>
      <div class="chips">${(t.instructions || []).filter((i) => !["REQUEST", "ACK"].includes(i.type)).map((i) => `<span class="chip">${esc(insText(i))}</span>`).join("")}
      ${(t.flags || []).map((f) => `<span class="pill flag">${esc(f)}</span>`).join("")}</div></div>`;
  let diff = "";
  if (rb) {
    const name = (id) => (id || "").split(":").pop();
    const rows = [
      ...rb.matched.map((m) => `<tr><td>${esc(m.type)}</td><td class="ok">일치</td><td></td></tr>`),
      ...rb.missing.map((m) => `<tr><td>${esc(m.type)}</td><td class="miss">복창 누락</td><td>${esc(name(m.target_id))}</td></tr>`),
      ...rb.conflicts.map((c) => `<tr><td>${esc(c.type)}</td><td class="bad">불일치 (${esc(c.reasons.join(","))})</td>
        <td>${esc(name(c.atco_target) || (c.atco_route || []).map(name).join("→"))} ≠ ${esc(name(c.pilot_target) || (c.pilot_route || []).map(name).join("→"))}</td></tr>`),
    ].join("");
    diff = `<table class="diff"><tr><th>필수 개념</th><th>판정: <span class="pill ${esc(rb.status)}">${esc(rb.status)}</span></th><th>근거</th></tr>${rows}</table>`;
  }
  $("#evidence").innerHTML = `<h3><span>${alert ? esc(alert.label) + " · " : ""}Evidence</span><button class="close" onclick="$('#evidence').classList.add('hidden')">✕</button></h3>
    ${alert ? `<div class="hint" style="font-size:12px">${esc(alert.message)}</div>` : ""}
    ${pair.map(block).join("")}${diff}`;
  $("#evidence").classList.remove("hidden");
}

// ------------------------------------------------------------------ evaluation tab
function renderEval(ev, sm) {
  const pct = (x) => (x === null || x === undefined ? "-" : `${(x * 100).toFixed(1)}%`);
  const cards = [];
  if (ev.e1_competency) {
    const e = ev.e1_competency;
    cards.push(`<div class="card"><h3>E1 · 역량 질문 (질적 평가)</h3>
      <div class="big">${e.non_empty}/${e.n_cq}</div><div class="hint">답변 가능 ${e.answerable} · 근거 추적 가능 ${e.traceable}</div>
      <table><tr><th>CQ</th><th>질문</th><th>답</th><th>근거</th></tr>
      ${e.results.map((r) => `<tr><td>${r.cq}</td><td>${esc(r.question)}</td><td>${r.n_answers}</td><td>${r.traceable ? "✓" : "–"}</td></tr>`).join("")}</table></div>`);
  }
  if (ev.e3_injection) {
    const e = ev.e3_injection;
    cards.push(`<div class="card"><h3>E3 · 주입 오류 탐지 (n=${e.n_pairs} MATCH 쌍, ${esc(e.extractor)})</h3>
      <div class="hint">대조군 오경보 ${pct(e.control_false_alarm.rate)} (95% CI ${e.control_false_alarm.ci95.map(pct).join("–")})</div>
      <table><tr><th>주입 유형</th><th>탐지율</th><th></th><th>n</th></tr>
      ${Object.entries(e.detection).map(([k, r]) => `<tr><td>${esc(k)}</td><td>${pct(r.rate)}</td>
        <td style="width:40%"><div class="bar"><span style="width:${(r.rate || 0) * 100}%"></span></div></td><td>${r.n}</td></tr>`).join("")}</table></div>`);
  }
  if (ev.e2_precision && (ev.e2_precision.label || ev.e2_precision.label_llm)) {
    const e = ev.e2_precision.label || ev.e2_precision.label_llm;
    const who = ev.e2_precision.label ? "사람 판정" : "LLM 판정 · 잠정, 사람 라벨 필요";
    cards.push(`<div class="card"><h3>E2 · 근거 대비 정밀도 (${who})</h3>
      <div class="big">${pct(e.overall.strict.rate)}</div><div class="hint">95% CI ${e.overall.strict.ci95.map(pct).join("–")} · n=${e.overall.strict.n}</div>
      <table><tr><th>관계</th><th>정밀도</th><th>n</th></tr>${Object.entries(e.by_relation).map(([k, v]) => `<tr><td>${esc(k)}</td><td>${pct(v.strict.rate)}</td><td>${v.strict.n}</td></tr>`).join("")}</table></div>`);
  }
  cards.push(`<div class="card"><h3>KG 구성</h3><table>
    ${Object.entries(sm.nodes || {}).map(([k, v]) => `<tr><td>${k}</td><td>${v}</td></tr>`).join("")}</table></div>`);
  $("#tab-eval").innerHTML = `<div class="eval-grid">${cards.join("")}</div>`;
}

// ------------------------------------------------------------------ tabs, play, boot
function switchTab(name) {
  document.querySelectorAll("#tabs button").forEach((b) => b.classList.toggle("active", b.dataset.tab === name));
  document.querySelectorAll(".tab").forEach((t) => t.classList.toggle("active", t.id === `tab-${name}`));
  if (name === "graph" && S.cy) S.cy.resize();
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
  if (STATIC) $("#mode-badge").hidden = false;
  renderKpis(sm);
  $("#time").max = Math.round(S.hist.end - S.hist.start);
  const busiest = S.hist.tx.indexOf(Math.max(...S.hist.tx));
  setTime(S.hist.start + busiest * S.hist.bin_sec);
  renderEval(data.eval || {}, sm);
  loadAlerts();
})();
