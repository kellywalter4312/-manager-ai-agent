/* The one renderer for live data-bound views (GM-06).
 *
 * A view is resolved by the server to plain JSON (GET live/<view>.json:
 * src/services/views/live_views.py); this draws that JSON - value cards,
 * history sparklines with what a technique found marked on them, a group's
 * members and aggregates, the events conditions raised, what a camera saw,
 * a connection read live. Every surface that shows a view uses this: the
 * workspace panel live:<view> on the main page and the served page
 * /live/<view>. One poller per view, whatever number of places show it; each
 * poll redraws every element bound to that view in place.
 *
 * No library, no network beyond the view's own JSON and pictures.
 */
(function(){
  "use strict";
  const latest = new Map();   // view id -> last resolved JSON
  const pollers = new Map();  // view id -> timer

  const esc = s => String(s == null ? "" : s).replace(/[&<>"']/g,
    c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));

  function num(v){
    if(typeof v !== "number" || !isFinite(v)) return esc(v);
    const a = Math.abs(v);
    return esc(a !== 0 && (a >= 1e6 || a < 1e-3) ? v.toExponential(2) : +v.toFixed(a >= 100 ? 1 : 3));
  }

  function ago(seconds){
    if(seconds == null) return "";
    if(seconds < 90) return Math.round(seconds) + "s ago";
    if(seconds < 5400) return Math.round(seconds / 60) + " min ago";
    return Math.round(seconds / 3600) + " h ago";
  }

  function sparkline(w){
    const pts = w.points || [];
    if(pts.length < 2) return `<div class="lv-note">${pts.length ? "one reading so far" : ""}</div>`;
    const W = 280, H = 64, pad = 3;
    const t0 = Date.parse(pts[0][0]), t1 = Date.parse(pts[pts.length - 1][0]);
    const lo = w.min, hi = w.max === w.min ? w.min + 1 : w.max;
    const x = t => pad + (W - 2 * pad) * ((Date.parse(t) - t0) / Math.max(1, t1 - t0));
    const y = v => H - pad - (H - 2 * pad) * ((v - lo) / (hi - lo));
    const line = pts.map(p => `${x(p[0]).toFixed(1)},${y(p[1]).toFixed(1)}`).join(" ");
    const marks = (w.markers || []).filter(m => typeof m.value === "number")
      .map(m => `<circle cx="${x(m.at).toFixed(1)}" cy="${y(Math.min(hi, Math.max(lo, m.value))).toFixed(1)}" r="3"><title>${esc(m.what)} ${num(m.value)}</title></circle>`).join("");
    return `<svg class="lv-spark" viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" role="img"
        aria-label="${esc(w.label)} history">
        <polyline fill="none" stroke="currentColor" stroke-width="1.6" points="${line}"/>${marks}</svg>
      <div class="lv-range"><span>min ${num(w.min)}</span><span>last <b data-lv-last>${num(w.last)}</b> ${esc(w.unit || "")}</span><span>max ${num(w.max)}</span></div>`;
  }

  const WIDGETS = {
    value: (w) => w.state === "ok"
      ? `<div class="lv-value" data-lv-value>${num(w.value)}<span class="lv-unit">${esc(w.unit || "")}</span></div>
         <div class="lv-meta">${esc(ago(w.age_seconds))}</div>`
      : "",
    series: (w, base) => `${sparkline(w)}${w.technique ? `<div class="lv-meta">${esc(w.technique.label)}: ${esc(w.technique.detail)}</div>` : ""}
         ${w.count != null ? `<div class="lv-meta">${esc(w.count)} reading(s)</div>` : ""}`,
    group: (w) => w.state === "ok" ? `<table class="lv-table">${(w.members || []).map(m => `<tr><td>${esc(m.entity)}</td><td>${
        m.observed ? Object.entries(m.latest || {}).map(([k, r]) => `${esc(k)} <b>${num(r.value)}</b>${esc(r.unit ? " " + r.unit : "")}`).join(", ")
                   : "nothing observed yet"}</td></tr>`).join("")}</table>
        ${Object.entries(w.aggregates || {}).map(([k, a]) => `<div class="lv-meta">${esc(k)}: mean <b>${num(a.mean)}</b>, lowest ${num(a.min)} (${esc(a.lowest)}), highest ${num(a.max)} (${esc(a.highest)})</div>`).join("")}
        ${(w.missing || []).length ? `<div class="lv-note">not found now: ${w.missing.map(m => esc(m.name)).join(", ")}</div>` : ""}` : "",
    events: (w) => (w.events || []).length ? `<ul class="lv-list">${w.events.map(e => `<li><span class="lv-when">${esc(String(e.at).slice(11, 16))}</span> ${esc(e.text || e.label)} <span class="lv-chip">${esc(e.status)}</span></li>`).join("")}</ul>` : "",
    look: (w, base) => `${w.image ? `<img class="lv-look" alt="${esc(w.label)}" src="${esc(base + w.image)}">` : ""}
        ${w.in_view != null ? `<div class="lv-meta">in view: <b data-lv-inview>${num(w.in_view)}</b></div>` : ""}`,
    reading: (w) => w.fields ? `<table class="lv-table">${Object.entries(w.fields).map(([k, v]) => `<tr><td>${esc(k)}</td><td><b>${num(v)}</b></td></tr>`).join("")}</table>`
        : (w.text ? `<div class="lv-meta">${esc(w.text)}</div>` : ""),
  };

  function html(view, base){
    base = base || "";
    if(!view) return `<div class="lv-note">Reading the view…</div>`;
    if(view.error) return `<div class="lv-note">${esc(view.error)}</div>`;
    const cards = (view.widgets || []).map(w => {
      const draw = WIDGETS[w.kind];
      const body = draw ? draw(w, base) : "";
      return `<div class="lv-card lv-kind-${esc(w.kind)} lv-state-${esc(w.state)}" data-lv-kind="${esc(w.kind)}" data-lv-widget="${esc(w.label)}">
          <div class="lv-label">${esc(w.label)}</div>${body}${w.note ? `<div class="lv-note">${esc(w.note)}</div>` : ""}</div>`;
    }).join("");
    return `<div class="lv-head"><span class="lv-title">${esc(view.title)}</span>
        <span class="lv-meta" data-lv-resolved="${esc(view.resolved_at)}">updated ${esc(String(view.resolved_at).slice(11, 19))} UTC · every ${esc(view.refresh_seconds)}s</span></div>
      <div class="lv-grid">${cards || `<div class="lv-note">This view has nothing on it yet.</div>`}</div>`;
  }

  function paint(viewId){
    const data = latest.get(viewId);
    document.querySelectorAll(`[data-live-view="${CSS.escape(viewId)}"]`).forEach(el => {
      el.innerHTML = html(data, el.dataset.liveBase || "");
    });
  }

  async function poll(viewId, base){
    try{
      const res = await fetch(`${base}live/${encodeURIComponent(viewId)}.json`, {cache: "no-store"});
      const data = await res.json();
      latest.set(viewId, res.ok ? data : {error: data.error || "could not read the view"});
    }catch(e){
      latest.set(viewId, {error: "the server could not be reached - showing nothing rather than old numbers"});
    }
    paint(viewId);
    const every = Math.max(2, (latest.get(viewId) || {}).refresh_seconds || 10) * 1000;
    pollers.set(viewId, setTimeout(() => poll(viewId, base), every));
  }

  function watch(viewId, base){
    if(pollers.has(viewId)) return;
    pollers.set(viewId, null);
    poll(viewId, base || "");
  }

  const STYLE = `.lv-head{display:flex;justify-content:space-between;gap:8px;align-items:baseline;margin:4px 0 6px}
    .lv-title{font-weight:600}.lv-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(220px,1fr));gap:8px}
    .lv-card{border:1px solid rgba(127,127,127,.35);border-radius:8px;padding:8px;min-width:0}
    .lv-label{font-size:12px;opacity:.8;margin-bottom:4px;overflow-wrap:anywhere}
    .lv-value{font-size:28px;font-weight:600;line-height:1.1}.lv-unit{font-size:13px;margin-left:4px;opacity:.75}
    .lv-meta,.lv-note{font-size:11px;opacity:.75;overflow-wrap:anywhere}.lv-note{font-style:italic}
    .lv-spark{width:100%;height:64px;display:block}.lv-spark circle{fill:#e63946}
    .lv-range{display:flex;justify-content:space-between;font-size:11px;opacity:.8}
    .lv-table{width:100%;font-size:12px;border-collapse:collapse}.lv-table td{padding:2px 4px;border-top:1px solid rgba(127,127,127,.2);vertical-align:top}
    .lv-list{margin:0;padding-left:16px;font-size:12px}.lv-when{opacity:.7}.lv-chip{font-size:10px;opacity:.7}
    .lv-look{max-width:100%;border-radius:6px;display:block}`;
  if(!document.getElementById("alfred-live-style")){
    const style = document.createElement("style");
    style.id = "alfred-live-style";
    style.textContent = STYLE;
    document.head.appendChild(style);
  }

  window.AlfredLive = {html, watch, paint, cached: id => latest.get(id) || null};
})();
