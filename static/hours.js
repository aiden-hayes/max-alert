// Hours tab: opening hours for every dining spot, from /api/hours (GCSU Dining's hours page).
// Shown by setView("hours") in plate.js. Tap a place to see its whole week.
const Hours = (() => {
  const $ = (id) => document.getElementById(id);
  const h = (s) => String(s ?? "").replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const view = () => $("hoursView");
  let data = null, timer = null;
  const expanded = new Set();

  async function load() {
    try {
      const res = await fetch("/api/hours");
      if (!res.ok) throw new Error(res.status);
      data = await res.json();
    } catch {
      if (!data) {
        view().innerHTML = `<div class="empty-state"><b>Can't load hours</b>Check your connection and try again.</div>`;
        return;
      }
    }
    render();
    if (data.loading && !data.locations.length) setTimeout(load, 2000);
  }

  const periodsHtml = (d) => d.periods.length
    ? d.periods.map((p) => `<div>${p.label ? `<span class="h-label">${h(p.label)}</span> ` : ""}${h(p.text)}</div>`).join("")
    : `<div class="muted">${h(d.note || "Closed")}</div>`;

  function card(loc) {
    const open = expanded.has(loc.slug), today = loc.week[0];
    const rows = loc.week.map((d) => `<div class="h-row${d.date === today.date ? " today" : ""}">
        <div class="h-day">${h(d.day)}${d.special ? ` <span class="h-special" title="Special hours (break, holiday or weather)">special</span>` : ""}</div>
        <div class="h-times">${periodsHtml(d)}</div></div>`).join("");
    return `<section class="hcard${open ? " open" : ""}">
      <button type="button" class="h-head" data-hours="${h(loc.slug)}" aria-expanded="${open}">
        <span class="h-main">
          <span class="h-name">${h(loc.name)}</span>
          ${loc.where ? `<span class="h-where">${h(loc.where)}</span>` : ""}
          <span class="h-status ${loc.status.open ? "is-open" : ""}"><i class="h-dot"></i>${h(loc.status.text)}</span>
        </span>
        <span class="chev" aria-hidden="true"></span>
      </button>
      ${open ? `<div class="h-week">${rows}</div>` : `<div class="h-today"><span class="h-day">Today</span><div class="h-times">${periodsHtml(today)}</div></div>`}
    </section>`;
  }

  function render() {
    if (view().hidden || !data) return;
    let body;
    if (!data.locations.length) {
      body = data.loading
        ? `<div class="empty-state"><b>Loading hours…</b>Getting the latest from GCSU Dining.</div>`
        : `<div class="empty-state"><b>Hours aren't available right now</b>Try again in a few minutes, or check <a href="${h(data.source)}" target="_blank" rel="noopener">GCSU Dining</a>.</div>`;
    } else {
      body = data.locations.map(card).join("") +
        `<p class="muted h-src">From <a href="${h(data.source)}" target="_blank" rel="noopener">GCSU Dining</a>, including posted break and holiday hours.` +
        (data.last_error && data.last_success ? ` Couldn't check for changes just now; these are from ${h(new Date(data.last_success).toLocaleString(undefined, { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" }))}.` : "") +
        `</p>`;
    }
    view().innerHTML = body;
  }

  view().addEventListener("click", (e) => {
    const b = e.target.closest("[data-hours]");
    if (!b) return;
    const slug = b.dataset.hours;
    expanded.has(slug) ? expanded.delete(slug) : expanded.add(slug);
    render();
  });

  function show() {
    if (data) render();
    else view().innerHTML = `<div class="empty-state"><b>Loading hours…</b>Getting the latest from GCSU Dining.</div>`;
    load();
    clearInterval(timer);  // keep "Open until …" current while the tab is open
    timer = setInterval(() => { if (!view().hidden) load(); else clearInterval(timer); }, 60 * 1000);
  }
  return { show };
})();
