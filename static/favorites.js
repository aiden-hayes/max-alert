// Favorite dishes: the ☆ on every dish, and the "Favorite dish alerts" card in Settings.
// Starring needs an account (favorites live on the server so the morning push can use
// them). Loaded after account.js and plate.js, before app.js.
const Favorites = (() => {
  const $ = (id) => document.getElementById(id);
  const h = (s) => String(s ?? "").replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  // Same normalizing as the server (ratings.dish_key): collapse spaces, ignore capitals.
  const key = (name) => String(name || "").trim().split(/\s+/).join(" ").toLowerCase().slice(0, 120);
  const IOS_APP = "https://apps.apple.com/us/app/ntfy/id1625396347";
  const ANDROID_APP = "https://play.google.com/store/apps/details?id=io.heckel.ntfy";
  const TIPPED = "maxalert.favTip";
  const STAR = `<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M11.53 2.3a.53.53 0 0 1 .95 0l2.31 4.68a2.12 2.12 0 0 0 1.6 1.16l5.16.76a.53.53 0 0 1 .3.9l-3.74 3.64a2.12 2.12 0 0 0-.61 1.88l.88 5.14a.53.53 0 0 1-.77.56l-4.62-2.43a2.12 2.12 0 0 0-1.97 0L6.4 21.01a.53.53 0 0 1-.77-.56l.88-5.14a2.12 2.12 0 0 0-.61-1.88L2.16 9.8a.53.53 0 0 1 .3-.9l5.16-.76a2.12 2.12 0 0 0 1.6-1.16z"/></svg>`;

  let list = [], keys = new Set(), alerts = null, error = null;
  const rerender = () => { if (window.MaxMenu) window.MaxMenu.render(); if (isSettings()) Plate.renderSettings(); };
  const isSettings = () => !$("settingsView").hidden;

  async function api(method, path = "", body) {
    const res = await fetch(`/api/favorites${path}`, {
      method, headers: body ? { "Content-Type": "application/json" } : {}, body: body ? JSON.stringify(body) : undefined });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.error || `Something went wrong (${res.status}).`);
    return data;
  }
  function apply(data) {
    if (data.favorites) { list = data.favorites; keys = new Set(list.map((f) => f.dish)); }
    if (data.alerts) alerts = data.alerts;
  }
  async function load() {
    try { apply(await api("GET")); error = null; } catch (e) { error = e.message; }
    rerender();
  }

  // ---------- the star on each dish ----------
  const has = (name) => keys.has(key(name));
  function starHtml(item) {
    const on = has(item.name), n = h(item.name);
    return `<button type="button" class="star" data-fav="${n}" aria-pressed="${on}" ` +
      `aria-label="${on ? "Unstar" : "Star"} ${n}" title="${on ? "Starred: you'll get a heads-up when it's on the menu" : "Star to get a heads-up when it's on the menu"}">${STAR}</button>`;
  }
  async function toggle(name) {
    if (!Account.user) {
      return Account.requireLogin("Log in to star dishes and get a heads-up on days they're served.", () => toggle(name));
    }
    const on = !has(name), k = key(name), before = [list, keys];
    keys = new Set(keys); on ? keys.add(k) : keys.delete(k);  // show it right away
    rerender();
    try {
      apply(await api("POST", "", { dish: name, on }));
      let tipped = false; try { tipped = !!localStorage.getItem(TIPPED); } catch { /* ignore */ }
      if (on && !tipped) {
        try { localStorage.setItem(TIPPED, "1"); } catch { /* ignore */ }
        Plate.toast("★ Starred. Turn on phone alerts in Settings.");
      } else Plate.toast(on ? `★ Starred ${name}` : `Unstarred ${name}`);
    } catch (e) {
      [list, keys] = before;
      Plate.toast(e.message, true);
    }
    rerender();
  }

  // ---------- Settings card ----------
  function nextServed(dishKey) {
    const d = window.MaxMenu && window.MaxMenu.data;
    if (!d) return "";
    for (const [i, date] of d.dates.entries()) {
      const meals = [];
      for (const ms of Object.values(d.days[date] || {})) for (const m of ms)
        if (m.stations.some((s) => s.items.some((it) => key(it.name) === dishKey)) && !meals.includes(m.name)) meals.push(m.name);
      if (meals.length) {
        const day = i === 0 ? "Today" : i === 1 ? "Tomorrow" : new Date(date + "T12:00:00").toLocaleDateString(undefined, { weekday: "long" });
        return `${day} · ${meals.join(", ")}`;
      }
    }
    return "Not on this week's menu";
  }
  function settingsHtml() {
    if (error) return `<section class="card settings"><h3>Favorite dish alerts</h3><p class="q-err">${h(error)}</p></section>`;
    if (!alerts) return `<section class="card settings"><h3>Favorite dish alerts</h3><p class="muted">Loading…</p></section>`;
    const favs = list.length
      ? `<ul class="fav-list">${list.map((f) => `<li><span><b>${h(f.name)}</b><small>${h(nextServed(f.dish))}</small></span>` +
          `<button type="button" class="ghost small" data-fav="${h(f.name)}" aria-label="Unstar ${h(f.name)}">Remove</button></li>`).join("")}</ul>`
      : `<p class="muted">No favorites yet. Tap ☆ on any dish in the menu.</p>`;
    return `<section class="card settings" id="favAlerts">
      <h3>Favorite dish alerts</h3>
      <p class="muted">On mornings when a dish you starred is on the menu, you get one notification around ${h(alerts.send_at)} saying which meals it's at.</p>
      <label class="switch"><input type="checkbox" data-favalerts ${alerts.enabled ? "checked" : ""}> Send me alerts</label>
      <details class="fav-setup"${list.length && alerts.enabled ? "" : " open"}>
        <summary>Set up notifications on your phone</summary>
        <ol>
          <li>Install the free <b>ntfy</b> app (<a href="${IOS_APP}" target="_blank" rel="noopener">iPhone</a> ·
            <a href="${ANDROID_APP}" target="_blank" rel="noopener">Android</a>).</li>
          <li>In ntfy, tap <b>+</b> and subscribe to your private topic:
            <div class="topic"><code>${h(alerts.topic)}</code><button type="button" class="ghost small" data-favcopy>Copy</button></div>
            <small class="muted">Keep it to yourself. Anyone with this name could read your alerts.</small></li>
          <li><button type="button" class="ghost small" data-favtest>Send a test notification</button></li>
        </ol>
      </details>
      <h3 class="fav-h">Your favorites</h3>
      ${favs}
    </section>`;
  }

  // ---------- events ----------
  document.addEventListener("click", async (e) => {
    const b = e.target.closest("[data-fav],[data-favcopy],[data-favtest]");
    if (!b) return;
    if (b.dataset.fav !== undefined) return toggle(b.dataset.fav);
    if (b.hasAttribute("data-favcopy")) {
      try { await navigator.clipboard.writeText(alerts.topic); Plate.toast("Topic copied"); }
      catch { Plate.toast("Couldn't copy. Select the topic and copy it by hand.", true); }
      return;
    }
    if (b.hasAttribute("data-favtest")) {
      b.disabled = true;
      try { await api("POST", "/alerts/test", {}); Plate.toast("Test sent. Check your phone."); }
      catch (x) { Plate.toast(x.message, true); }
      finally { b.disabled = false; }
    }
  });
  document.addEventListener("change", async (e) => {
    if (!e.target.matches("[data-favalerts]")) return;
    const enabled = e.target.checked;
    try { apply(await api("PUT", "/alerts", { enabled })); Plate.toast(enabled ? "Alerts on" : "Alerts off"); }
    catch (x) { e.target.checked = !enabled; Plate.toast(x.message, true); }
  });
  Account.onChange((u) => {
    list = []; keys = new Set(); alerts = null; error = null;
    if (u) load(); else rerender();
  });

  return { starHtml, has, settingsHtml, load };
})();
