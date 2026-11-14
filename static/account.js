// Accounts: "Log in" button / profile menu, the log in + sign up dialog, and the
// one-time offer to import the calorie log saved before accounts existed.
// Loaded first. Plate (plate.js) asks Account.requireLogin(...) before saving anything.
const Account = (() => {
  const $ = (id) => document.getElementById(id);
  const h = (s) => String(s ?? "").replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  // Before accounts, the browser remembered a "log name" for the server-side log file.
  const LEGACY_KEY = "maxalert.user";
  const legacyName = () => { try { return localStorage.getItem(LEGACY_KEY); } catch { return null; } };

  let user = null, mode = "login", pending = null;
  const listeners = [];

  async function post(path, body = {}) {
    const res = await fetch(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.error || `Something went wrong (${res.status}).`);
    return data;
  }
  function setUser(u) {
    user = u;
    renderCorner();
    listeners.forEach((fn) => fn(user));
  }

  // ---------- top-right corner ----------
  function renderCorner() {
    const el = $("acct");
    if (!user) {
      el.innerHTML = `<button type="button" class="login-btn" data-acct="login">Log in</button>`;
      return;
    }
    el.innerHTML = `
      <button type="button" class="avatar" data-acct="menu" aria-haspopup="menu" aria-expanded="false" aria-label="Account menu">${h(user.email[0].toUpperCase())}</button>
      <div class="acct-menu" role="menu" hidden>
        <div class="acct-email">${h(user.email)}</div>
        <button type="button" role="menuitem" data-view="calories">My Calories</button>
        <button type="button" role="menuitem" data-view="settings">Settings</button>
        <button type="button" role="menuitem" data-acct="logout">Log out</button>
      </div>`;
  }
  function toggleMenu(show) {
    const menu = document.querySelector(".acct-menu"), btn = document.querySelector(".avatar");
    if (!menu) return;
    const open = show ?? menu.hidden;
    menu.hidden = !open;
    btn.setAttribute("aria-expanded", String(open));
    if (open) menu.querySelector("button").focus();
  }

  // ---------- log in / sign up / forgot password dialog ----------
  // Each is its own <form> in index.html with autocomplete="username" + "current-password"
  // or "new-password", which is what iPhone Keychain and other password managers look for.
  const form = (m) => $("authDialog").querySelector(`[data-form=${m}]`);
  function openDialog(which = "login", reason = "") {
    const d = $("authDialog");
    d.querySelectorAll("form").forEach((f) => f.reset());
    d.querySelector(".auth-reason").textContent = reason;
    d.querySelector(".auth-reason").hidden = !reason;
    if (!d.open) d.showModal();
    showMode(which);
  }
  function showMode(which) {
    const d = $("authDialog"), email = form(mode)?.email.value;
    mode = which;
    d.querySelector(".auth-tabs").hidden = mode === "forgot";
    d.querySelectorAll("[role=tab][data-mode]").forEach((b) => b.setAttribute("aria-selected", String(b.dataset.mode === mode)));
    d.querySelectorAll("form").forEach((f) => { f.hidden = f.dataset.form !== mode; f.querySelector(".q-err").textContent = ""; });
    const f = form(mode);
    if (email && !f.email.value) f.email.value = email;  // carry the typed email across
    if (mode === "forgot") f.querySelector(".q-ok").hidden = true;
    (f.email.value ? f.password || f.email : f.email).focus();
  }
  // Android/Chrome: offer to save the password (Safari decides on its own when the form closes).
  async function savePassword(email, password) {
    if (!window.PasswordCredential || !navigator.credentials) return;
    try { await navigator.credentials.store(new PasswordCredential({ id: email, password, name: email })); }
    catch { /* not supported or turned off */ }
  }
  async function loggedIn(u, email, password, msg) {
    setUser(u);
    Plate.toast(msg);
    savePassword(email, password);
    const next = pending; pending = null;
    if (next) await next();
    offerImport();
  }
  async function submit(e) {
    e.preventDefault();
    const f = e.target, which = f.dataset.form, err = f.querySelector(".q-err"), btn = f.querySelector("button[type=submit]");
    const email = f.email.value.trim(), password = f.password ? f.password.value : "";
    err.textContent = "";
    if (which === "signup" && password !== f.confirm.value) { err.textContent = "Passwords don't match."; return; }
    btn.disabled = true;
    try {
      if (which === "forgot") {
        const r = await post("/api/auth/forgot", { email });
        const ok = f.querySelector(".q-ok");
        ok.textContent = r.message; ok.hidden = false;
        return;
      }
      const { user: u } = await post(`/api/auth/${which}`, { email, password });
      $("authDialog").close();
      await loggedIn(u, email, password, which === "signup" ? "Account created. You're logged in." : "Logged in");
    } catch (x) {
      err.textContent = x.message;
    } finally {
      btn.disabled = false;
    }
  }

  // ---------- reset link from the email: https://site/#reset=TOKEN ----------
  let resetToken = null;
  async function openResetFromLink() {
    const m = location.hash.match(/^#reset=([\w.-]+)$/);
    if (!m) return;
    resetToken = m[1];
    history.replaceState(null, "", location.pathname + location.search);  // keep the token out of history
    try {
      const { email } = await post("/api/auth/reset/check", { token: resetToken });
      const d = $("resetDialog"), f = d.querySelector("form");
      f.reset(); f.email.value = email; f.querySelector(".q-err").textContent = "";
      d.showModal();
      f.password.focus();
    } catch (x) {
      resetToken = null;
      openDialog("forgot");
      form("forgot").querySelector(".q-err").textContent = x.message;
    }
  }
  async function submitReset(e) {
    e.preventDefault();
    const f = e.target, err = f.querySelector(".q-err"), btn = f.querySelector("button[type=submit]");
    const password = f.password.value;
    err.textContent = "";
    if (password !== f.confirm.value) { err.textContent = "Passwords don't match."; return; }
    btn.disabled = true;
    try {
      const { user: u } = await post("/api/auth/reset", { token: resetToken, password });
      resetToken = null;
      $("resetDialog").close();
      await loggedIn(u, f.email.value, password, "Password changed. You're logged in.");
    } catch (x) {
      err.textContent = x.message;
    } finally {
      btn.disabled = false;
    }
  }
  /** Run `then` if logged in; otherwise ask to log in / sign up and run it afterwards. */
  function requireLogin(reason, then) {
    if (user) return then && then();
    pending = then || null;
    openDialog("login", reason);
  }
  async function logout() {
    toggleMenu(false);
    try { await post("/api/auth/logout"); } catch { /* cookie may already be gone */ }
    setUser(null);
    Plate.toast("Logged out");
  }

  // ---------- one-time import of the pre-accounts log ----------
  async function offerImport() {
    const name = legacyName();
    if (!user || user.import_offer_done || !name) return;
    let offer = null;
    try {
      const res = await fetch(`/api/log/import?legacy=${encodeURIComponent(name)}`);
      offer = res.ok ? (await res.json()).offer : null;
    } catch { return; }
    if (!offer) return;
    const d = $("importDialog");
    const goal = offer.goal ? ` and your ${Number(offer.goal).toLocaleString()} cal goal` : "";
    d.querySelector(".import-text").textContent =
      `This device has a calorie log from before accounts: ${offer.meals} meal${offer.meals === 1 ? "" : "s"} ` +
      `over ${offer.days} day${offer.days === 1 ? "" : "s"}${goal}. Add it to your account?`;
    d.querySelector(".q-err").textContent = "";
    d.showModal();
  }
  async function importChoice(accept) {
    const d = $("importDialog");
    try {
      if (accept) {
        const r = await post("/api/log/import", { legacy: legacyName() });
        Plate.toast(`Imported ${r.imported} meal${r.imported === 1 ? "" : "s"}`);
      } else {
        await post("/api/log/import/dismiss");
      }
      user.import_offer_done = true;
      d.close();
      listeners.forEach((fn) => fn(user));
    } catch (x) {
      d.querySelector(".q-err").textContent = x.message;
    }
  }

  // ---------- events ----------
  document.addEventListener("click", (e) => {
    const b = e.target.closest("[data-acct]");
    if (!b) {
      if (!e.target.closest(".acct")) toggleMenu(false);
      if (e.target.closest(".acct-menu [data-view]")) toggleMenu(false);
      return;
    }
    if (b.dataset.acct === "login") openDialog("login");
    else if (b.dataset.acct === "menu") toggleMenu();
    else if (b.dataset.acct === "logout") logout();
  });
  document.addEventListener("keydown", (e) => { if (e.key === "Escape") toggleMenu(false); });
  document.addEventListener("DOMContentLoaded", async () => {
    const d = $("authDialog");
    d.querySelectorAll("form").forEach((f) => f.addEventListener("submit", submit));
    d.querySelectorAll("[data-mode]").forEach((b) => b.addEventListener("click", () => showMode(b.dataset.mode)));
    d.querySelector("[data-auth=forgot]").addEventListener("click", () => showMode("forgot"));
    d.querySelectorAll("[data-auth=cancel]").forEach((b) => b.addEventListener("click", () => { pending = null; d.close(); }));
    const rd = $("resetDialog");
    rd.querySelector("form").addEventListener("submit", submitReset);
    rd.querySelector("[data-reset=cancel]").addEventListener("click", () => { resetToken = null; rd.close(); });
    window.addEventListener("hashchange", openResetFromLink);
    const imp = $("importDialog");
    imp.querySelector("[data-import=yes]").addEventListener("click", () => importChoice(true));
    imp.querySelector("[data-import=no]").addEventListener("click", () => importChoice(false));
    renderCorner();
    try {
      const res = await fetch("/api/auth/me");
      if (res.ok) setUser((await res.json()).user);
    } catch { /* offline: stay logged out in the UI */ }
    if (location.hash.startsWith("#reset=")) openResetFromLink();
    else offerImport();
  });

  return {
    get user() { return user; },
    onChange: (fn) => listeners.push(fn),
    requireLogin,
    openDialog,
    logout,
  };
})();
