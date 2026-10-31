// Build Your Plate + calorie log (My Calories / Settings views). Loaded after account.js
// and before app.js. Anyone can build a plate; saving it needs an account (Account.requireLogin).
const Plate = (() => {
  const MEALS = ["Breakfast", "Brunch", "Lunch", "Dinner", "Late Night", "Snack"];
  const N = ["calories", "protein_g", "carbs_g", "fat_g"];
  const LS_PLATE = "maxalert.plate";
  const $ = (id) => document.getElementById(id);
  const h = (s) => String(s ?? "").replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

  // ---------- storage (the plate draft lives in the browser; the log is in the account) ----------
  const store = {
    get(k) { try { return localStorage.getItem(k); } catch { return null; } },
    set(k, v) { try { localStorage.setItem(k, v); } catch { /* private mode etc. */ } },
  };
  const blank = () => ({ items: [], meal: null, editing: null, stash: null });
  let plate = (() => { try { return { ...blank(), ...JSON.parse(store.get(LS_PLATE)) }; } catch { return blank(); } })();
  if (!Array.isArray(plate.items)) plate = blank();
  let log = null, logError = null, view = "menu";
  const save = () => store.set(LS_PLATE, JSON.stringify(plate));

  // ---------- math ----------
  // app.js's `state` is a top-level const (shared global scope, but not on window)
  const appData = () => (typeof state !== "undefined" && state.data) || null;
  const today = () => (appData() && appData().dates[0]) ||
    new Date().toLocaleDateString("en-CA", { timeZone: "America/New_York" });
  const r1 = (n) => Math.round(n * 10) / 10;
  const fmt = (n, unit = "") => (n == null ? "—" : `${Math.round(n).toLocaleString()}${unit}`);
  const hasAny = (i) => N.some((k) => i[k] != null);
  function totals(items) {
    const t = { calories: 0, protein_g: 0, carbs_g: 0, fat_g: 0, incomplete: 0, none: 0 };
    for (const i of items) {
      const s = i.servings || 1;
      for (const k of N) if (i[k] != null) t[k] += i[k] * s;
      if (!hasAny(i)) t.none++;
      else if (N.some((k) => i[k] == null)) t.incomplete++;
    }
    return t;
  }
  function lineNutrition(i) {
    if (!hasAny(i)) return "Nutrition info not available";
    const s = i.servings || 1;
    const parts = [i.calories != null ? `${Math.round(i.calories * s)} cal` : "cal —"];
    for (const [k, label] of [["protein_g", "protein"], ["carbs_g", "carbs"], ["fat_g", "fat"]])
      parts.push(i[k] != null ? `${r1(i[k] * s)}g ${label}` : `— ${label}`);
    return parts.join(" · ");
  }
  const tiles = (t) => `<div class="totals">` +
    [["Calories", fmt(t.calories)], ["Protein", fmt(t.protein_g, "g")], ["Carbs", fmt(t.carbs_g, "g")], ["Fat", fmt(t.fat_g, "g")]]
      .map(([k, v]) => `<div class="tile"><b>${v}</b><span>${k}</span></div>`).join("") + `</div>`;
  function missingNote(t) {
    const parts = [];
    if (t.none) parts.push(`${t.none} item${t.none > 1 ? "s have" : " has"} no nutrition info`);
    if (t.incomplete) parts.push(`${t.incomplete} item${t.incomplete > 1 ? "s are" : " is"} missing some values`);
    return parts.length ? `<p class="note">${parts.join(" and ")}, so the totals leave those out.</p>` : "";
  }
  function defaultMeal(m) {
    if (MEALS.includes(m)) return m;
    const cur = appData() && appData().current;
    return cur && MEALS.includes(cur.meal) ? cur.meal : "Snack";
  }

  // ---------- API ----------
  async function api(method, path, body) {
    const res = await fetch(`/api/log${path}`, {
      method, headers: body ? { "Content-Type": "application/json" } : {},
      body: body ? JSON.stringify(body) : undefined,
    });
    if (res.status === 204) return null;
    const data = await res.json().catch(() => ({}));
    if (res.status === 401 && data.login_required) Account.openDialog("login", "Your session ended. Log in again to keep tracking.");
    if (!res.ok) throw new Error(data.error || `Something went wrong (${res.status}).`);
    return data;
  }
  async function loadLog() {
    if (!Account.user) { log = null; return; }
    try { log = await api("GET", `?date=${today()}`); logError = null; }
    catch (e) { logError = e.message; }
    if (view === "calories") renderCalories();
    if (view === "settings") renderSettings();
  }
  const payload = (items) => items.map(({ uid, ...rest }) => rest);

  // ---------- plate actions ----------
  function add({ item, ctx }) {
    const same = plate.items.find((p) => p.name === item.name && p.station === ctx.station && p.location === ctx.location);
    if (same) same.servings = Math.min(10, same.servings + 1);
    else plate.items.push({
      uid: Math.random().toString(36).slice(2), name: item.name, station: ctx.station, location: ctx.location,
      portion: item.portion || "", source: "menu", servings: 1,
      calories: item.calories ?? null, protein_g: item.protein_g ?? null, carbs_g: item.carbs_g ?? null, fat_g: item.fat_g ?? null,
    });
    if (!plate.meal) plate.meal = defaultMeal(ctx.meal);
    save(); renderBar(); if (isOpen()) renderSheet();
    toast(same ? `${item.name}: ${same.servings} servings` : `Added ${item.name}`);
  }
  function resetPlate() {
    const back = plate.stash;
    plate = blank();
    if (back) { plate.items = back.items; plate.meal = back.meal; }
    save();
  }
  async function logPlate() {
    if (!plate.items.length) return;
    if (!Account.user)
      return Account.requireLogin("Log in or sign up to save this meal to your calorie log. Your plate stays on this device.", logPlate);
    const btn = $("sheet").querySelector("[data-act=log],[data-act=save]");
    if (btn) btn.disabled = true;
    try {
      const t = totals(plate.items);
      if (plate.editing) {
        await api("PUT", `/meals/${plate.editing.id}`, { meal: plate.meal, items: payload(plate.items) });
        toast(`${plate.meal} updated`);
      } else {
        await api("POST", "/meals", { date: today(), meal: plate.meal, items: payload(plate.items) });
        toast(`${plate.meal} logged · ${fmt(t.calories)} cal`);
      }
      resetPlate(); close(); renderBar(); loadLog();
    } catch (e) {
      toast(e.message, true);
      if (btn) btn.disabled = false;
    }
  }
  function editLogged(id) {
    const m = log && log.meals.find((x) => x.id === id);
    if (!m) return;
    if (plate.editing) resetPlate();
    plate.stash = plate.items.length ? { items: plate.items, meal: plate.meal } : null;
    plate.items = m.items.map((i) => ({ ...i, uid: Math.random().toString(36).slice(2) }));
    plate.meal = m.meal;
    plate.editing = { id: m.id, meal: m.meal, logged_at: m.logged_at };
    save(); renderBar(); open();
  }
  async function deleteLogged(id) {
    const m = log && log.meals.find((x) => x.id === id);
    if (!m || !confirm(`Delete your logged ${m.meal}?`)) return;
    try {
      await api("DELETE", `/meals/${id}`);
      if (plate.editing && plate.editing.id === id) { resetPlate(); renderBar(); }
      toast(`${m.meal} deleted`);
      loadLog();
    } catch (e) { toast(e.message, true); }
  }

  // ---------- bottom bar + sheet ----------
  function renderBar() {
    const bar = $("plateBar");
    const show = plate.items.length > 0 || !!plate.editing;
    bar.hidden = !show;
    document.body.classList.toggle("has-plate", show);
    if (!show) return;
    const t = totals(plate.items), n = plate.items.length;
    const unknown = t.none || t.incomplete ? "+" : "";
    bar.innerHTML = `<button type="button" data-act="open" class="bar-btn">
      <span class="bar-title">${plate.editing ? `Editing ${h(plate.editing.meal)}` : "My Plate"}</span>
      <span class="bar-sum">${n} item${n === 1 ? "" : "s"} · ${fmt(t.calories)}${unknown} cal</span>
      <span class="bar-go">View</span></button>`;
  }
  const isOpen = () => !$("sheet").hidden;
  function open() {
    renderSheet();
    $("sheet").hidden = false; $("backdrop").hidden = false;
    document.body.classList.add("sheet-open");
  }
  function close() {
    $("sheet").hidden = true; $("backdrop").hidden = true;
    document.body.classList.remove("sheet-open");
  }
  function renderSheet() {
    const t = totals(plate.items), ed = plate.editing;
    const when = ed ? new Date(ed.logged_at).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" }) : "";
    const items = plate.items.map((i) => `
      <li class="p-item">
        <div class="p-main">
          <div class="p-name">${h(i.name)}</div>
          <div class="p-where">${i.source === "quick" ? "Added manually" : h([i.station, i.location].filter(Boolean).join(" · "))}${i.portion ? ` · serving: ${h(i.portion)}` : ""}</div>
          <div class="p-nut ${hasAny(i) ? "" : "na"}">${h(lineNutrition(i))}</div>
        </div>
        <div class="p-ctl">
          <div class="stepper" role="group" aria-label="Servings of ${h(i.name)}">
            <button type="button" data-act="dec" data-uid="${i.uid}" aria-label="Fewer servings" ${i.servings <= 0.5 ? "disabled" : ""}>−</button>
            <span><b>${i.servings}</b><small>serv</small></span>
            <button type="button" data-act="inc" data-uid="${i.uid}" aria-label="More servings" ${i.servings >= 10 ? "disabled" : ""}>+</button>
          </div>
          <button type="button" class="x" data-act="remove" data-uid="${i.uid}" aria-label="Remove ${h(i.name)}">Remove</button>
        </div>
      </li>`).join("");
    $("sheet").innerHTML = `
      <div class="grab" aria-hidden="true"></div>
      <div class="sheet-head">
        <h2>${ed ? `Editing ${h(ed.meal)} <small>logged ${h(when)}</small>` : "My Plate"}</h2>
        <button type="button" class="icon" data-act="close" aria-label="Close">×</button>
      </div>
      ${tiles(t)}${missingNote(t)}
      ${plate.items.length ? `<ul class="p-list">${items}</ul>`
        : `<p class="p-empty">Your plate is empty. Tap <b>+</b> next to any menu item, or use Quick add.</p>`}
      <div class="p-row">
        <label class="p-meal">Meal
          <select data-act="meal">${MEALS.map((m) => `<option ${m === (plate.meal || defaultMeal()) ? "selected" : ""}>${m}</option>`).join("")}</select>
        </label>
        <button type="button" class="ghost" data-act="quick">+ Quick add</button>
      </div>
      <div class="p-actions">
        ${ed ? `<button type="button" class="ghost" data-act="cancel">Cancel</button>
                <button type="button" class="primary" data-act="save" ${plate.items.length ? "" : "disabled"}>Save changes</button>`
             : `<button type="button" class="ghost" data-act="clear" ${plate.items.length ? "" : "disabled"}>Clear plate</button>
                <button type="button" class="primary" data-act="log" ${plate.items.length ? "" : "disabled"}>Log this meal</button>`}
      </div>
      ${ed && !plate.items.length ? `<p class="note">To remove this whole meal, delete it from My Calories.</p>` : ""}
      ${!Account.user && plate.items.length ? `<p class="note login-note"><button type="button" class="linkish" data-acct="login">Log in</button> to save meals and track calories. Your plate stays on this device until then.</p>` : ""}`;
  }

  // ---------- quick add ----------
  function openQuick() {
    const d = $("quickAdd");
    d.querySelector("form").reset();
    d.querySelector(".q-err").textContent = "";
    d.showModal();
    d.querySelector("[name=name]").focus();
  }
  function submitQuick(e) {
    e.preventDefault();
    const f = new FormData(e.target);
    const num = (k) => (f.get(k) === "" ? null : Number(f.get(k)));
    const name = String(f.get("name")).trim(), cal = num("calories");
    const err = $("quickAdd").querySelector(".q-err");
    if (!name) { err.textContent = "Give it a name."; return; }
    if (cal == null || !(cal >= 0 && cal <= 5000)) { err.textContent = "Enter calories (0–5000)."; return; }
    for (const k of ["protein_g", "carbs_g", "fat_g"]) {
      const v = num(k);
      if (v != null && !(v >= 0 && v <= 500)) { err.textContent = "Protein, carbs and fat must be 0–500g."; return; }
    }
    plate.items.push({ uid: Math.random().toString(36).slice(2), name: name.slice(0, 120), source: "quick",
      station: "", location: "", portion: "", servings: 1,
      calories: cal, protein_g: num("protein_g"), carbs_g: num("carbs_g"), fat_g: num("fat_g") });
    if (!plate.meal) plate.meal = defaultMeal();
    save(); $("quickAdd").close(); renderBar(); open();
  }

  // ---------- My Calories + Settings views (logged in) ----------
  const back = `<button type="button" class="back" data-view="menu">‹ Menu</button>`;
  function renderCalories() {
    const el = $("caloriesView");
    const d = new Date(today() + "T12:00:00").toLocaleDateString(undefined, { weekday: "long", month: "short", day: "numeric" });
    const title = `${back}<h2 class="view-title">My Calories <small>Today · ${d}</small></h2>`;
    if (logError) { el.innerHTML = `${title}<div class="banner">Couldn't load your log: ${h(logError)}</div>`; return; }
    if (!log) { el.innerHTML = `${title}<p class="muted">Loading…</p>`; return; }
    const all = log.meals.flatMap((m) => m.items), t = totals(all), goal = log.goal;
    let goalHtml;
    if (goal) {
      const pct = Math.min(100, (t.calories / goal) * 100), diff = Math.round(goal - t.calories);
      goalHtml = `<div class="card goal">
        <div class="goal-top"><b>${fmt(t.calories)}</b> / ${goal.toLocaleString()} cal</div>
        <div class="progress ${diff < 0 ? "over" : ""}" role="progressbar" aria-valuemin="0" aria-valuemax="${goal}" aria-valuenow="${Math.round(t.calories)}"><span style="width:${pct}%"></span></div>
        <div class="goal-sub">${diff >= 0 ? `${diff.toLocaleString()} left` : `${(-diff).toLocaleString()} over goal`}</div></div>`;
    } else goalHtml = `<p class="muted goal-hint">Want a daily target? <button type="button" class="linkish" data-view="settings">Set a calorie goal</button></p>`;
    const meals = log.meals.map((m) => {
      const mt = totals(m.items);
      const time = new Date(m.logged_at).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
      return `<section class="station log-meal">
        <div class="lm-head"><h2>${h(m.meal)}</h2><span>${time} · ${fmt(mt.calories)}${mt.none || mt.incomplete ? "+" : ""} cal</span></div>
        ${m.items.map((i) => `<div class="item"><div class="item-main"><span class="name">${i.servings !== 1 ? `${i.servings} × ` : ""}${h(i.name)}</span>
          <div class="meta">${h(lineNutrition(i))}</div></div></div>`).join("")}
        <div class="lm-actions"><button type="button" class="ghost" data-act="edit" data-id="${m.id}">Edit</button>
          <button type="button" class="ghost danger" data-act="delete" data-id="${m.id}">Delete</button></div>
      </section>`;
    }).join("");
    el.innerHTML = `
      ${title}
      ${goalHtml}
      ${tiles(t)}${missingNote(t)}
      ${meals || `<div class="empty-state"><b>Nothing logged yet today</b>Build a plate from the menu, then tap “Log this meal”.</div>`}
      <button type="button" class="ghost wide" data-act="quick">+ Quick add a snack or drink</button>`;
  }
  function renderSettings() {
    const el = $("settingsView"), u = Account.user;
    if (!u) { el.innerHTML = ""; return; }
    const goal = log ? log.goal : u.goal;
    el.innerHTML = `
      ${back}<h2 class="view-title">Settings</h2>
      <section class="card settings">
        <form data-form="goal">
          <label for="goalInput">Daily calorie goal <small>(optional)</small></label>
          <div class="inline"><input id="goalInput" name="goal" type="number" inputmode="numeric" min="500" max="10000" step="50" placeholder="e.g. 2200" value="${goal ?? ""}">
          <button class="primary" type="submit">Save</button></div>
          <small class="muted">Leave it blank to just see totals.</small>
        </form>
        <p class="q-err" id="settingsErr"></p>
      </section>
      <section class="card settings">
        <h3>Account</h3>
        <p class="muted acct-line">Logged in as <b>${h(u.email)}</b></p>
        <button type="button" class="ghost" data-acct="logout">Log out</button>
      </section>`;
  }
  async function submitSettings(e) {
    const form = e.target.closest("form[data-form=goal]");
    if (!form) return;
    e.preventDefault();
    const err = $("settingsErr");
    err.textContent = "";
    try {
      const res = await api("PUT", "/settings", { goal: form.goal.value });
      if (Account.user) Account.user.goal = res.goal;
      toast(res.goal ? `Goal set to ${res.goal.toLocaleString()} cal` : "Goal cleared");
      loadLog();
    } catch (x) { err.textContent = x.message; }
  }

  // ---------- views + toast ----------
  function setView(v) {
    if (v !== "menu" && !Account.user) v = "menu";
    view = v;
    $("menuView").hidden = v !== "menu";
    $("caloriesView").hidden = v !== "calories";
    $("settingsView").hidden = v !== "settings";
    if (v === "calories") renderCalories();
    if (v === "settings") renderSettings();
    if (v !== "menu") loadLog();
    window.scrollTo(0, 0);
  }
  Account.onChange((u) => {
    log = null; logError = null;
    if (!u) {
      if (plate.editing) resetPlate();
      if (view !== "menu") setView("menu");
    } else if (view !== "menu") {
      setView(view);
    }
    renderBar();
    if (isOpen()) renderSheet();
  });
  let toastTimer;
  function toast(msg, bad = false) {
    const t = $("toast");
    t.textContent = msg; t.className = "toast" + (bad ? " bad" : ""); t.hidden = false;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => { t.hidden = true; }, bad ? 4000 : 1800);
  }

  // ---------- events ----------
  document.addEventListener("click", (e) => {
    const b = e.target.closest("[data-act],[data-view]");
    if (!b) { if (e.target.id === "backdrop") close(); return; }
    if (b.dataset.view) return setView(b.dataset.view);
    if (!b.dataset.act) return;
    const it = plate.items.find((i) => i.uid === b.dataset.uid);
    switch (b.dataset.act) {
      case "open": return open();
      case "close": return close();
      case "inc": if (it) it.servings = Math.min(10, it.servings + 0.5); break;
      case "dec": if (it) it.servings = Math.max(0.5, it.servings - 0.5); break;
      case "remove": plate.items = plate.items.filter((i) => i !== it); break;
      case "clear": if (!confirm("Clear everything on your plate?")) return; plate = blank(); break;
      case "cancel": resetPlate(); renderBar(); return close();
      case "log": case "save": return logPlate();
      case "quick": return openQuick();
      case "edit": return editLogged(b.dataset.id);
      case "delete": return deleteLogged(b.dataset.id);
      default: return;
    }
    save(); renderBar(); renderSheet();
  });
  document.addEventListener("change", (e) => {
    if (e.target.matches("select[data-act=meal]")) { plate.meal = e.target.value; save(); }
  });
  document.addEventListener("keydown", (e) => { if (e.key === "Escape" && isOpen()) close(); });
  document.addEventListener("submit", submitSettings);
  document.addEventListener("DOMContentLoaded", () => {
    $("quickAdd").querySelector("form").addEventListener("submit", submitQuick);
    $("quickAdd").querySelector("[data-q=cancel]").addEventListener("click", () => $("quickAdd").close());
    renderBar();
  });

  return { add, totals, toast, setView };
})();
