// Max Alert frontend: plain JS, no build step.
const STANDARD_MEALS = ["Breakfast", "Lunch", "Dinner", "Late Night"];
const TAG_LABELS = { vegetarian: "Vegetarian", vegan: "Vegan", "plant-based": "Plant-based",
  "gluten-free": "Gluten-free", mindful: "Mindful" };

const state = { data: null, date: null, meal: null, location: "all", diets: new Set(), q: "", aiKey: null, proteins: false, ratings: {} };
const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

// ---------- station sections (collapsible) ----------
// Sections start collapsed except the one you opened last (remembered on this device).
const LAST_STATION = "maxalert.lastStation";
const lastStation = {
  get() { try { return localStorage.getItem(LAST_STATION); } catch { return null; } },
  set(v) { try { v ? localStorage.setItem(LAST_STATION, v) : localStorage.removeItem(LAST_STATION); } catch { /* ignore */ } },
};
let openStations = new Set();
const resetOpenStations = () => { const l = lastStation.get(); openStations = new Set(l ? [l] : []); };
resetOpenStations();

// ---------- Proteins filter ----------
// "Protein-focused" = the name or description mentions a protein food (chicken, beef, fish,
// eggs, tofu...). Items with no protein number are left out. Edit these lists to tune it.
const PROTEIN_WORDS = /(chicken|beef|steak|burger|brisket|pork|bacon|sausage|turkey|meatball|meatloaf|lamb|gyro|carne|pastrami|fish|salmon|tuna|ahi|tilapia|catfish|pollock|shrimp|crab|seafood|omelet|frittata|tofu|tempeh|seitan|lentil|chickpea|edamame|wing|tender|nugget|carnitas|barbacoa|chorizo|jerky|greek yogurt|cottage cheese)|\b(ham|cod|eggs?|beans?)\b/i;
const NOT_PROTEIN = /green beans?|jelly beans?|coffee beans?|egg noodles?|egg rolls?|chicken broth|beef broth|\bbuns?\b/i;
function isProteinPick(item) {
  if (item.protein_g == null) return false;
  const text = `${item.name} ${item.description}`;
  return PROTEIN_WORDS.test(text) && !NOT_PROTEIN.test(text);
}

// ---------- data ----------
let follow = true, lastCurrent = null, switchTimer = null;
function scheduleMealSwitch(iso) {
  clearTimeout(switchTimer);
  const ms = iso ? new Date(iso) - Date.now() + 5000 : -1;  // 5s after the change
  if (ms > 0 && ms < 24 * 3600 * 1000) switchTimer = setTimeout(load, ms);
}

// ---------- thumbs up / down ----------
async function loadRatings() {
  try {
    const res = await fetch("/api/ratings");
    if (!res.ok) return;
    state.ratings = (await res.json()).ratings || {};
    render();
  } catch { /* ratings are optional */ }
}

function ratingHtml(item) {
  const r = state.ratings[item.name] || { up: 0, down: 0, mine: 0 };
  const total = r.up + r.down;
  const n = esc(item.name);
  return `<div class="rate">` + Favorites.starHtml(item) +
    `<button type="button" class="vote" data-vote="1" data-dish="${n}" aria-pressed="${r.mine === 1}" aria-label="Thumbs up ${n}"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M7 10v12"/><path d="M15 5.88 14 10h5.83a2 2 0 0 1 1.92 2.56l-2.33 8A2 2 0 0 1 17.5 22H4a2 2 0 0 1-2-2v-8a2 2 0 0 1 2-2h2.76a2 2 0 0 0 1.79-1.11L12 2a3.13 3.13 0 0 1 3 3.88Z"/></svg><span>${r.up || ""}</span></button>` +
    `<button type="button" class="vote" data-vote="-1" data-dish="${n}" aria-pressed="${r.mine === -1}" aria-label="Thumbs down ${n}"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M7 10v12"/><path d="M15 5.88 14 10h5.83a2 2 0 0 1 1.92 2.56l-2.33 8A2 2 0 0 1 17.5 22H4a2 2 0 0 1-2-2v-8a2 2 0 0 1 2-2h2.76a2 2 0 0 0 1.79-1.11L12 2a3.13 3.13 0 0 1 3 3.88Z"/></svg><span>${r.down || ""}</span></button>` +
    (total >= 3 ? `<span class="liked">${Math.round((100 * r.up) / total)}% liked</span>` : "") + `</div>`;
}

async function vote(btn) {
  const dish = btn.dataset.dish, value = +btn.dataset.vote;
  const before = state.ratings[dish] || { up: 0, down: 0, mine: 0 };
  const next = before.mine === value ? 0 : value;  // tap again to take it back
  const r = { ...before, mine: next };              // update right away, then confirm with the server
  if (before.mine === 1) r.up--; if (before.mine === -1) r.down--;
  if (next === 1) r.up++; if (next === -1) r.down++;
  state.ratings[dish] = r; render();
  try {
    const res = await fetch("/api/ratings", { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ dish, vote: next }) });
    if (!res.ok) throw new Error((await res.json().catch(() => ({}))).error || "Couldn't save your vote.");
    state.ratings[dish] = await res.json();
  } catch (e) {
    state.ratings[dish] = before;
    Plate.toast(e.message, true);
  }
  render();
}

async function load() {
  try {
    const res = await fetch("/api/menus");
    if (!res.ok) throw new Error(res.status);
    const data = await res.json();
    const first = !state.data;
    state.data = data;
    // The service worker (sw.js) answered from the copy saved on this phone.
    state.saved = res.headers.get("X-MaxAlert-Offline") === "1";
    state.offline = false;
    if (state.saved) pickTodayFromSaved(data);
    // Follow the clock: show the meal being served now (or the next one). Tapping another
    // meal/day stops following until the served meal changes, then it jumps to the new one.
    const cur = `${data.current.date}|${data.current.meal}`;
    if (cur !== lastCurrent) { follow = true; lastCurrent = cur; }
    if (first || follow || !data.dates.includes(state.date)) {
      if (!first && (state.date !== data.current.date || state.meal !== data.current.meal)) resetOpenStations();
      state.date = data.current.date;
      state.meal = data.current.meal;
    }
    scheduleMealSwitch(data.current.changes_at);
    loadRatings();
    if (data.refreshing && !Object.keys(data.days).length) setTimeout(load, 3000);
  } catch (e) {
    if (!state.data) {
      $("content").innerHTML = emptyState("Can't reach Max Alert",
        "The app server isn't responding. Check your connection and try again.");
      $("updated").textContent = "Offline";
      return;
    }
    state.offline = true;  // keep showing what we already have
  }
  render();
}

// A saved menu says what was "current" when it was saved, which may be yesterday.
// Move it to today (by this phone's clock) so the page doesn't open on an old day.
function pickTodayFromSaved(data) {
  const now = new Date();
  const today = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}-${String(now.getDate()).padStart(2, "0")}`;
  if (data.current.date >= today || !data.dates.includes(today)) return;
  data.dates = data.dates.filter((d) => d >= today);
  data.current = { date: today, meal: null, status: "next", label: "", changes_at: null };
}

// ---------- helpers ----------
function mealsFor(date, loc) {
  const day = state.data.days[date] || {};
  const locs = loc === "all" ? Object.keys(day) : [loc];
  const out = {};  // meal -> [{loc, stations}]
  for (const l of locs) for (const m of day[l] || []) (out[m.name] ||= []).push({ loc: l, stations: m.stations });
  return out;
}
const passesDiet = (item) => [...state.diets].every((t) => item.tags.includes(t));
const locName = (slug) => (state.data.locations.find((l) => l.slug === slug) || {}).name || slug;

function dateLabel(iso, i) {
  const d = new Date(iso + "T12:00:00");
  const top = i === 0 ? "Today" : i === 1 ? "Tomorrow" : d.toLocaleDateString(undefined, { weekday: "short" });
  return { top, bottom: d.toLocaleDateString(undefined, { month: "short", day: "numeric" }) };
}
function timeAgo(iso) {
  if (!iso) return "never";
  const d = new Date(iso);
  const sameDay = d.toDateString() === new Date().toDateString();
  return (sameDay ? "today " : d.toLocaleDateString(undefined, { month: "short", day: "numeric" }) + " ") +
    d.toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" });
}
const emptyState = (title, body) => `<div class="empty-state"><b>${esc(title)}</b>${esc(body)}</div>`;

// Items rendered in the current pass, so each "+" button can find its dish.
const addable = [];
const NUTRIENTS = ["calories", "protein_g", "carbs_g", "fat_g"];
const g1 = (n) => (Number.isInteger(n) ? n : n.toFixed(1));

function nutritionLine(item) {
  if (!NUTRIENTS.some((k) => item[k] != null)) return "Nutrition info not available";
  return [item.calories != null ? `${item.calories} cal` : "cal —",
    item.protein_g != null ? `${g1(item.protein_g)}g protein` : "",
    item.carbs_g != null ? `${g1(item.carbs_g)}g carbs` : "",
    item.fat_g != null ? `${g1(item.fat_g)}g fat` : ""].filter(Boolean).join(" · ");
}

function itemHtml(item, extra = "", ctx = null, proteinPill = false) {
  const badges = item.tags.map((t) => `<span class="badge">${esc(TAG_LABELS[t] || t)}</span>`).join("");
  const nut = nutritionLine(item) + (item.portion ? ` · serving: ${item.portion}` : "");
  const contains = item.allergens.length ? `Contains: ${item.allergens.join(", ")}` : "";
  const add = ctx ? `<button type="button" class="add" data-add="${addable.push({ item, ctx }) - 1}" aria-label="Add ${esc(item.name)} to my plate">+</button>` : "";
  return `<div class="item"><div class="item-main"><span class="name">${esc(item.name)}</span>` +
    (badges ? `<span class="badges">${badges}</span>` : "") +
    (item.description ? `<div class="desc">${esc(item.description)}</div>` : "") +
    (extra ? `<div class="meta">${extra}</div>` : "") +
    `<div class="meta nut${nut.startsWith("Nutrition info") ? " na" : ""}">${esc(nut)}</div>` +
    (contains ? `<div class="meta">${esc(contains)}</div>` : "") + ratingHtml(item) + `</div>` +
    (proteinPill ? `<span class="pro-g" title="Protein per serving"><b>${g1(item.protein_g)}g</b>protein</span>` : "") +
    `${add}</div>`;
}

// ---------- render ----------
function render() {
  const d = state.data;
  addable.length = 0;
  renderStatus(d);
  renderDiets(d);
  const searching = state.q.trim().length > 0;
  $("browse").hidden = searching;
  if (searching) return renderSearch();
  renderDates(d); renderLocations(d); renderMeals(); renderHero(d); renderMenu();
}

function renderStatus(d) {
  $("updated").textContent = d.last_success ? `Updated ${timeAgo(d.last_success)}` :
    d.refreshing ? "Fetching menus…" : "No menu data yet";
  const b = [];
  if (state.saved) b.push(`<div class="banner"><b>No connection.</b> Showing the menu saved on this phone` +
    (d.last_success ? ` (updated ${esc(timeAgo(d.last_success))})` : "") + `. It may have changed.</div>`);
  if (d.mode === "demo") b.push(`<div class="banner info"><b>Demo data.</b> These menus are made up. Add your Sodexo API key (see README) to see the real MAX menu.</div>`);
  if (d.mode === "unconfigured") b.push(`<div class="banner">No dining locations are set up yet. Run <code>python discover.py</code> (see README).</div>`);
  if (d.stale_since && !state.offline) {
    b.push(`<div class="banner"><b>Menus may be out of date.</b> Max Alert hasn't been able to download new menus from the dining site ` +
      (d.last_success ? `since ${esc(timeAgo(d.last_success))}` : `yet`) + `. Check the posted menu at The MAX to be sure.</div>`);
  } else if (state.saved) {
    // already explained above
  } else if ((d.last_error && d.mode === "live") || state.offline) {
    b.push(d.last_success
      ? `<div class="banner">We couldn't reach the dining site just now, so this is the last menu we saved (updated ${esc(timeAgo(d.last_success))}). It may have changed.</div>`
      : `<div class="banner">We couldn't reach the dining site and don't have a saved menu yet. Try again in a few minutes.</div>`);
  }
  $("banners").innerHTML = b.join("");
}

function renderDates(d) {
  $("dates").innerHTML = d.dates.map((iso, i) => {
    const { top, bottom } = dateLabel(iso, i);
    return `<button class="chip" role="tab" data-date="${iso}" aria-selected="${iso === state.date}">${top}<small>${bottom}</small></button>`;
  }).join("");
}

function renderLocations(d) {
  const sel = $("location");
  sel.hidden = d.locations.length < 2;
  sel.innerHTML = `<option value="all">All locations</option>` +
    d.locations.map((l) => `<option value="${esc(l.slug)}">${esc(l.name)}</option>`).join("");
  sel.value = state.location;
}

function renderMeals() {
  const available = mealsFor(state.date, state.location);
  const names = [...STANDARD_MEALS];
  for (const n of Object.keys(available)) if (!names.includes(n)) names.splice(n === "Brunch" ? 1 : names.length, 0, n);
  // Hide weekend-only "Brunch" etc. unless present; always show the 4 standard tabs.
  if (!state.meal || !names.includes(state.meal)) state.meal = Object.keys(available)[0] || names[0];
  $("meals").innerHTML = names.map((n) =>
    `<button class="tab ${available[n] ? "" : "empty"}" role="tab" data-meal="${esc(n)}" aria-selected="${n === state.meal}">${esc(n)}</button>`).join("");
}

function renderHero(d) {
  const c = d.current, hero = $("hero");
  if (!c.meal || c.date !== state.date || c.meal !== state.meal) { hero.hidden = true; return; }
  hero.hidden = false;
  const max = d.max_status;
  if (c.status !== "now" && max && !max.open) {  // closed right now: say when it opens
    hero.innerHTML = `<div class="k"><span class="dot off"></span>The MAX is closed</div>` +
      `<div class="v">${esc(max.text)}</div>` +
      `<div class="t">Up next: ${esc(c.meal)}${c.label ? ` · ${esc(c.label)}` : ""}</div>`;
    return;
  }
  hero.innerHTML = `<div class="k">${c.status === "now" ? '<span class="dot"></span>Serving now' : "Up next"}</div>` +
    `<div class="v">${esc(c.meal)}</div>` + (c.label ? `<div class="t">${esc(c.label)}</div>` : "");
}

function renderMenu() {
  const entries = mealsFor(state.date, state.location)[state.meal];
  const hours = (state.data.hours[state.date] || {})[state.meal];
  if (!entries) {
    $("content").innerHTML = emptyState("No menu posted yet",
      `Nothing's up for ${state.meal} on this day. Check back later or try another meal.`);
    return;
  }
  const multi = entries.length > 1;
  let html = hours && state.data.current.meal !== state.meal
    ? `<p class="meta" style="margin:0 0 8px;color:var(--muted);font-size:.85rem">${esc(state.meal)}: ${esc(hours[0])} – ${esc(hours[1])}</p>` : "";
  html += `<div class="menu-tools">
    <button type="button" class="chip pro-btn" id="proteinsBtn" aria-pressed="${state.proteins}">Proteins</button>
    ${state.proteins ? "" : `<span class="bulk"><button type="button" class="linkish" data-bulk="expand">Expand all</button>
      <span aria-hidden="true">·</span><button type="button" class="linkish" data-bulk="collapse">Collapse all</button></span>`}
  </div>`;
  if (state.proteins) return renderProteins(entries, html);

  let shown = 0;
  for (const { loc, stations } of entries) {
    if (multi) html += `<div class="result-day">${esc(locName(loc))}</div>`;
    for (const st of stations) {
      const items = st.items.filter(passesDiet);
      if (!items.length) continue;
      shown += items.length;
      const ctx = { date: state.date, meal: state.meal, station: st.name, location: locName(loc) };
      const open = openStations.has(st.name);
      html += `<section class="station coll${open ? " open" : ""}">
        <h2><button type="button" class="st-head" data-st="${esc(st.name)}" aria-expanded="${open}">
          <span>${esc(st.name)}<span class="st-count">${items.length}</span></span><span class="chev" aria-hidden="true"></span></button></h2>
        <div class="st-body"${open ? "" : " hidden"}>${items.map((i) => itemHtml(i, "", ctx)).join("")}</div></section>`;
    }
  }
  $("content").innerHTML = shown ? html : html + emptyState("Nothing matches your filters",
    "Try turning off a dietary filter.");
}

function renderProteins(entries, html) {
  const picks = [];
  for (const { loc, stations } of entries) for (const st of stations) for (const item of st.items)
    if (passesDiet(item) && isProteinPick(item))
      picks.push({ item, ctx: { date: state.date, meal: state.meal, station: st.name, location: locName(loc) } });
  picks.sort((a, b) => b.item.protein_g - a.item.protein_g || a.item.name.localeCompare(b.item.name));
  const multi = entries.length > 1;
  if (!picks.length) {
    $("content").innerHTML = html + emptyState("No protein picks here",
      state.diets.size ? "Nothing protein-focused with protein info matches your filters. Try turning one off."
        : "Nothing protein-focused with protein info is posted for this meal.");
    return;
  }
  html += `<section class="station"><h2>Highest protein first (${picks.length})</h2>` +
    picks.map(({ item, ctx }) => itemHtml(item, `<b>${esc(multi ? `${ctx.station} · ${ctx.location}` : ctx.station)}</b>`, ctx, true)).join("") +
    `</section>`;
  $("content").innerHTML = html;
}

function toggleStation(btn) {
  const name = btn.dataset.st, open = btn.getAttribute("aria-expanded") !== "true";
  if (open) { openStations.add(name); lastStation.set(name); }
  else { openStations.delete(name); if (lastStation.get() === name) lastStation.set(null); }
  // Update in place (no full re-render) so the page doesn't jump.
  btn.setAttribute("aria-expanded", String(open));
  btn.closest(".station").classList.toggle("open", open);
  btn.closest(".station").querySelector(".st-body").hidden = !open;
}

function bulkStations(expand) {
  document.querySelectorAll("#content .st-head").forEach((b) => {
    const open = b.getAttribute("aria-expanded") === "true";
    if (open !== expand) {
      b.setAttribute("aria-expanded", String(expand));
      b.closest(".station").classList.toggle("open", expand);
      b.closest(".station").querySelector(".st-body").hidden = !expand;
    }
    expand ? openStations.add(b.dataset.st) : openStations.delete(b.dataset.st);
  });
  if (!expand) lastStation.set(null);
}

function renderDiets(d) {
  const tags = new Set();
  for (const day of Object.values(d.days)) for (const meals of Object.values(day))
    for (const m of meals) for (const s of m.stations) for (const i of s.items) i.tags.forEach((t) => tags.add(t));
  $("diets").innerHTML = [...tags].sort().map((t) =>
    `<button class="chip" data-diet="${esc(t)}" aria-pressed="${state.diets.has(t)}">${esc(TAG_LABELS[t] || t)}</button>`).join("");
}

function renderSearch() {
  const q = state.q.trim().toLowerCase();
  const d = state.data;
  let html = "", count = 0;
  d.dates.forEach((date, idx) => {
    const day = d.days[date] || {};
    let block = "";
    for (const [loc, meals] of Object.entries(day)) for (const m of meals) for (const st of m.stations)
      for (const item of st.items) {
        if (!passesDiet(item)) continue;
        if (!(item.name.toLowerCase().includes(q) || item.description.toLowerCase().includes(q))) continue;
        count++;
        const where = [m.name, st.name, d.locations.length > 1 ? locName(loc) : ""].filter(Boolean).join(" · ");
        block += itemHtml(item, `<b>${esc(where)}</b>`, { date, meal: m.name, station: st.name, location: locName(loc) });
      }
    if (block) {
      const { top, bottom } = dateLabel(date, idx);
      html += `<div class="result-day">${top} · ${bottom}</div><section class="station">${block}</section>`;
    }
  });
  $("content").innerHTML = count ? `<p style="margin:0;color:var(--muted);font-size:.85rem">${count} result${count === 1 ? "" : "s"} this week</p>` + html
    : emptyState(`No "${state.q.trim()}" this week`, "Nothing on the posted menus matches. Try a different word.");
}

// ---------- AI summary ----------
async function anythingGood() {
  // Judges whatever's being served now (or next), not the day picked above.
  const btn = $("good"), box = $("ai");
  btn.disabled = true; btn.textContent = "Checking…";
  box.hidden = false; box.className = "ai"; box.textContent = "Peeking at the menu…";
  try {
    const res = await fetch("/api/summary", { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ location: state.location }) });
    const body = await res.json();
    if (!res.ok) throw new Error(body.error || "Something went wrong.");
    box.innerHTML = (body.context ? `<span class="ai-k">${esc(body.context)}</span>` : "") + `<p>${esc(body.summary)}</p>`;
  } catch (e) {
    box.className = "ai err"; box.textContent = e.message;
  } finally {
    btn.disabled = false; btn.textContent = "Anything good?";
  }
}

// ---------- events ----------
document.addEventListener("click", (e) => {
  const t = e.target.closest("button"); if (!t) return;
  if (t.dataset.add) {
    const entry = addable[+t.dataset.add];
    if (entry) { Plate.add(entry); t.classList.add("added"); setTimeout(() => t.classList.remove("added"), 700); }
    return;
  }
  if (t.dataset.vote) return vote(t);
  if (t.dataset.st) return toggleStation(t);
  if (t.dataset.bulk) return bulkStations(t.dataset.bulk === "expand");
  if (t.id === "proteinsBtn") { state.proteins = !state.proteins; render(); return; }
  if (t.dataset.date) { state.date = t.dataset.date; state.meal = null; resetOpenStations();
    const cur = state.data.current; if (cur.date === state.date) state.meal = cur.meal;
    follow = cur.date === state.date && cur.meal === state.meal; render(); }
  else if (t.dataset.meal) { state.meal = t.dataset.meal; resetOpenStations();
    const cur = state.data.current; follow = cur.date === state.date && cur.meal === state.meal; render(); }
  else if (t.dataset.diet) { const x = t.dataset.diet; state.diets.has(x) ? state.diets.delete(x) : state.diets.add(x); render(); }
  else if (t.id === "good") anythingGood();
});
$("location").addEventListener("change", (e) => { state.location = e.target.value; render(); });
$("q").addEventListener("input", (e) => { state.q = e.target.value; render(); });

// For favorites.js: the current menu data, and a way to redraw after starring.
window.MaxMenu = { get data() { return state.data; }, render: () => state.data && render() };

load();
setInterval(load, 10 * 60 * 1000);  // pick up background refreshes while the tab is open
// Phones pause timers in background tabs, so check again whenever the page is shown.
document.addEventListener("visibilitychange", () => { if (!document.hidden) load(); });
