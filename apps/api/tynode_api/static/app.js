const view = document.getElementById("view");
const esc = s => String(s ?? "").replace(/[&<>"]/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));
const F = {
  ms: v => v < 1000 ? Math.round(v) + " ms" : (v / 1000).toFixed(2) + " s",
  usd: v => !v ? "$0" : v < 0.01 ? "$" + v.toFixed(5) : v < 1 ? "$" + v.toFixed(3) : "$" + v.toFixed(2),
  n: v => v >= 1e6 ? (v / 1e6).toFixed(1) + "M" : v >= 1e3 ? (v / 1e3).toFixed(1) + "k" : String(Math.round(v)),
  pct: v => (v * 100).toFixed(1) + "%",
  ago: t => { const s = Date.now() / 1000 - t;
    return s < 60 ? Math.floor(s) + "s ago" : s < 3600 ? Math.floor(s / 60) + "m ago" :
      s < 86400 ? Math.floor(s / 3600) + "h ago" : Math.floor(s / 86400) + "d ago"; }
};

async function api(path) {
  const key = localStorage.getItem("tynode_key") || "";
  const r = await fetch(path, { headers: key ? { "X-API-Key": key } : {} });
  if (r.status === 401) {
    const k = prompt("API key");
    if (k) { localStorage.setItem("tynode_key", k); return api(path); }
    throw new Error("Unauthorized");
  }
  if (!r.ok) throw new Error("HTTP " + r.status);
  return r.json();
}

function bars(vals, cls = "") {
  const max = Math.max(...vals, 1e-9);
  const rects = vals.map((v, i) => {
    const h = Math.max(2, v / max * 56);
    return `<rect x="${i * 10 + 1}" y="${60 - h}" width="8" height="${h}" rx="2"/>`;
  }).join("");
  return `<svg class="bars ${cls}" viewBox="0 0 ${vals.length * 10} 60" preserveAspectRatio="none">
    <defs><linearGradient id="g" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#22d3ee"/><stop offset="1" stop-color="#7c5cff"/></linearGradient></defs>${rects}</svg>`;
}

function traceTable(t) {
  if (!t.length) return "";
  return `<table><thead><tr><th>Trace</th><th>Status</th><th class="num">Latency</th><th class="num">Spans</th>
    <th class="num">Tokens</th><th class="num">Cost</th><th class="num">When</th></tr></thead><tbody>` +
    t.map(x => `<tr data-id="${esc(x.trace_id)}"><td><b>${esc(x.name)}</b> <span class="mute mono">${esc(x.trace_id.slice(0, 8))}</span></td>
    <td><span class="pill ${x.status}">${x.status}</span></td><td class="num">${F.ms(x.duration_ms)}</td>
    <td class="num">${x.spans}</td><td class="num">${F.n(x.tokens)}</td><td class="num">${F.usd(x.cost)}</td>
    <td class="num mute">${F.ago(x.start)}</td></tr>`).join("") + `</tbody></table>`;
}

const onboarding = `<div class="empty"><b style="color:var(--text);font-size:16px">No traces yet</b><br>Add two lines to your agent:
<br><pre class="mono">pip install tynode

import tynode
tynode.init()</pre></div>`;

function modelTable(m) {
  if (!m.length) return `<div class="empty">No LLM calls recorded yet.</div>`;
  return `<table><thead><tr><th>Model</th><th class="num">Calls</th><th class="num">Tokens</th>
    <th class="num">Avg latency</th><th class="num">Cost</th></tr></thead><tbody>` +
    m.map(x => `<tr><td><b>${esc(x.model)}</b></td><td class="num">${x.calls}</td><td class="num">${F.n(x.tokens)}</td>
    <td class="num">${F.ms(x.avg_latency_ms)}</td><td class="num">${F.usd(x.cost)}</td></tr>`).join("") + `</tbody></table>`;
}

async function pageOverview() {
  const [o, t] = await Promise.all([api("/api/v1/overview?hours=24"), api("/api/v1/traces?limit=8")]);
  if (!o.traces && !t.length) { view.innerHTML = `<h1>Overview</h1><div class="sub">Last 24 hours</div><div class="panel">${onboarding}</div>`; return; }
  view.innerHTML = `<h1>Overview</h1><div class="sub">Last 24 hours</div>
  <div class="grid">
    <div class="card"><div class="k">Traces</div><div class="v">${F.n(o.traces)}</div></div>
    <div class="card"><div class="k">Cost</div><div class="v">${F.usd(o.cost)}</div></div>
    <div class="card"><div class="k">Tokens</div><div class="v">${F.n(o.tokens)}</div></div>
    <div class="card"><div class="k">p95 latency</div><div class="v">${F.ms(o.p95_ms)}</div></div>
    <div class="card"><div class="k">Error rate</div><div class="v" style="color:${o.error_rate > 0.05 ? "var(--bad)" : "var(--ok)"}">${F.pct(o.error_rate)}</div></div>
  </div>
  <div class="grid" style="grid-template-columns:1fr 1fr">
    <div class="panel"><h3>Traces over time</h3><div class="chartbox">${bars(o.series.map(s => s.traces))}</div></div>
    <div class="panel"><h3>Cost over time</h3><div class="chartbox">${bars(o.series.map(s => s.cost))}</div></div>
  </div>
  <div class="panel"><h3>Recent traces <a class="mute" href="#/traces" style="text-decoration:none">View all</a></h3>${traceTable(t)}</div>
  <div class="panel"><h3>Models</h3>${modelTable(o.models)}</div>`;
}

async function pageTraces() {
  const q = window.__q || "";
  const t = await api("/api/v1/traces?limit=200" + (q ? "&q=" + encodeURIComponent(q) : ""));
  view.innerHTML = `<h1>Traces</h1><div class="sub">Every agent run, newest first</div>
  <div class="toolbar"><input class="search" id="q" placeholder="Search by name..." value="${esc(q)}"></div>
  <div class="panel">${t.length ? traceTable(t) : onboarding}</div>`;
  const el = document.getElementById("q");
  el.onkeydown = e => { if (e.key === "Enter") { window.__q = el.value; pageTraces(); } };
}

async function pageModels() {
  const o = await api("/api/v1/overview?hours=168");
  view.innerHTML = `<h1>Models</h1><div class="sub">Last 7 days</div><div class="panel">${modelTable(o.models)}</div>`;
}

async function pageTrace(id) {
  const spans = await api("/api/v1/traces/" + id);
  const t0 = Math.min(...spans.map(s => s.start));
  const total = Math.max(0.001, Math.max(...spans.map(s => s.end)) - t0);
  const depth = {}, byId = {};
  spans.forEach(s => byId[s.id] = s);
  const d = s => s.parent_id && byId[s.parent_id] ? (depth[s.id] ??= d(byId[s.parent_id]) + 1) : 0;
  spans.forEach(s => depth[s.id] = d(s));
  const root = spans.find(s => !s.parent_id) || spans[0];
  const cost = spans.reduce((a, s) => a + s.cost, 0), tok = spans.reduce((a, s) => a + s.tokens_in + s.tokens_out, 0);
  view.innerHTML = `<a class="back" href="#/traces">&larr; Traces</a><h1>${esc(root.name)}</h1>
  <div class="sub mono">${esc(id)} &middot; ${F.ms(total * 1000)} &middot; ${spans.length} spans &middot; ${F.usd(cost)} &middot; ${F.n(tok)} tokens</div>
  <div class="panel"><h3>Timeline</h3><div class="wf">` + spans.map((s, i) => {
    const left = (s.start - t0) / total * 100, w = Math.max(0.4, (s.end - s.start) / total * 100);
    return `<div class="wrow" data-i="${i}"><div class="wname" style="padding-left:${depth[s.id] * 18}px">${esc(s.name)}</div>
      <div class="track"><div class="bar k-${esc(s.kind)} ${s.error ? "bad" : ""}" style="left:${left}%;width:${w}%"></div></div>
      <div class="dur">${F.ms(s.duration_ms)}</div></div>`;
  }).join("") + `</div><div id="detail"></div></div>`;
  const show = i => {
    const s = spans[i];
    document.querySelectorAll(".wrow").forEach((r, j) => r.classList.toggle("sel", j === i));
    document.getElementById("detail").innerHTML = `<div class="detail">
      <div class="meta"><span>kind <b>${esc(s.kind)}</b></span>${s.model ? `<span>model <b>${esc(s.model)}</b></span>` : ""}
      <span>tokens <b>${s.tokens_in} in / ${s.tokens_out} out</b></span><span>cost <b>${F.usd(s.cost)}</b></span>
      <span>duration <b>${F.ms(s.duration_ms)}</b></span>${s.session_id ? `<span>session <b>${esc(s.session_id)}</b></span>` : ""}</div>
      ${s.error ? `<div class="errbox mono">${esc(s.error)}</div>` : ""}
      <div><h4>Input</h4><pre class="mono">${esc(s.input || "-")}</pre></div>
      <div><h4>Output</h4><pre class="mono">${esc(s.output || "-")}</pre></div></div>`;
  };
  view.querySelectorAll(".wrow").forEach(r => r.onclick = () => show(+r.dataset.i));
  const firstErr = spans.findIndex(s => s.error);
  show(firstErr >= 0 ? firstErr : 0);
}

view.addEventListener("click", e => {
  const tr = e.target.closest("tr[data-id]");
  if (tr) location.hash = "#/trace/" + tr.dataset.id;
});

async function route() {
  const h = location.hash || "#/";
  const name = h.startsWith("#/trace/") ? "traces" : h.startsWith("#/traces") ? "traces" : h.startsWith("#/models") ? "models" : "overview";
  document.querySelectorAll("nav a").forEach(a => a.classList.toggle("on", a.dataset.nav === name));
  try {
    if (h.startsWith("#/trace/")) await pageTrace(h.slice(8));
    else if (h.startsWith("#/traces")) await pageTraces();
    else if (h.startsWith("#/models")) await pageModels();
    else await pageOverview();
  } catch (e) {
    view.innerHTML = `<div class="empty">Could not load data (${esc(e.message)})</div>`;
  }
}
window.addEventListener("hashchange", route);
route();
setInterval(() => { if (!(location.hash || "").startsWith("#/trace/") && document.activeElement.id !== "q") route(); }, 5000);