// Max Alert frontend: plain JS, no build step.
const STANDARD_MEALS = ["Breakfast", "Lunch", "Dinner", "Late Night"];
const TAG_LABELS = { vegetarian: "Vegetarian", vegan: "Vegan", "plant-based": "Plant-based",
  "gluten-free": "Gluten-free", mindful: "Mindful" };

const state = { data: null, date: null, meal: null, location: "all", diets: new Set(), q: "", aiKey: null, proteins: false };
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
async function load() {
  try {
    const res = await fetch("/api/menus");
    if (!res.ok) throw new Error(res.status);
    const data = await res.json();
    const first = !state.data;
    state.data = data;
    if (first || !data.dates.includes(state.date)) {
      state.date = data.current.date;
      state.meal = data.current.meal;
    }
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
    (contains ? `<div class="meta">${esc(contains)}</div>` : "") + `</div>` +
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
  if (d.mode === "demo") b.push(`<div class="banner info"><b>Demo data.</b> These menus are made up. Add your Sodexo API key (see README) to see the real MAX menu.</div>`);
  if (d.mode === "unconfigured") b.push(`<div class="banner">No dining locations are set up yet. Run <code>python discover.py</code> (see README).</div>`);
  if ((d.last_error && d.mode === "live") || state.offline) {
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
          <span>${esc(st.name)} (${items.length})</span><span class="chev" aria-hidden="true"></span></button></h2>
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
  const btn = $("good"), box = $("ai");
  btn.disabled = true; btn.textContent = "Thinking…";
  box.hidden = false; box.className = "ai"; box.textContent = "Checking out the menu…";
  const today = state.data.dates[0];
  try {
    const res = await fetch("/api/summary", { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ date: today, location: state.location }) });
    const body = await res.json();
    if (!res.ok) throw new Error(body.error || "Something went wrong.");
    box.textContent = body.summary;
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
  if (t.dataset.st) return toggleStation(t);
  if (t.dataset.bulk) return bulkStations(t.dataset.bulk === "expand");
  if (t.id === "proteinsBtn") { state.proteins = !state.proteins; render(); return; }
  if (t.dataset.date) { state.date = t.dataset.date; state.meal = null; resetOpenStations();
    const cur = state.data.current; if (cur.date === state.date) state.meal = cur.meal; render(); }
  else if (t.dataset.meal) { state.meal = t.dataset.meal; resetOpenStations(); render(); }
  else if (t.dataset.diet) { const x = t.dataset.diet; state.diets.has(x) ? state.diets.delete(x) : state.diets.add(x); render(); }
  else if (t.id === "good") anythingGood();
});
$("location").addEventListener("change", (e) => { state.location = e.target.value; render(); });
$("q").addEventListener("input", (e) => { state.q = e.target.value; render(); });

load();
setInterval(load, 10 * 60 * 1000);  // pick up background refreshes while the tab is open
