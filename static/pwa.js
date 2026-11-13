// Add to Home Screen: registers the service worker (sw.js) and shows a small, dismissible
// "Install Max Alert" card on phones. Android/Chrome gets a real Install button; iPhone
// Safari has no install API, so it gets the Share → Add to Home Screen steps instead.
(() => {
  if ("serviceWorker" in navigator) {
    window.addEventListener("load", () => navigator.serviceWorker.register("/sw.js").catch(() => {}));
  }

  const DISMISSED = "maxalert.installDismissed";
  const dismissed = () => { try { return !!localStorage.getItem(DISMISSED); } catch { return false; } };
  const standalone = window.matchMedia("(display-mode: standalone)").matches || navigator.standalone === true;
  if (standalone || dismissed()) return;

  const isIOS = /iphone|ipad|ipod/i.test(navigator.userAgent) ||
    (navigator.platform === "MacIntel" && navigator.maxTouchPoints > 1);  // iPadOS reports as a Mac
  const isSafari = /safari/i.test(navigator.userAgent) && !/crios|fxios|edgios/i.test(navigator.userAgent);
  let deferred = null;

  function show(html) {
    const el = document.getElementById("install");
    if (!el) return;
    el.innerHTML = `<div class="install-text">${html}</div>
      <button type="button" class="install-x" data-install="dismiss" aria-label="Don't show again">×</button>`;
    el.hidden = false;
  }
  function hide(remember) {
    const el = document.getElementById("install");
    if (el) el.hidden = true;
    if (remember) try { localStorage.setItem(DISMISSED, "1"); } catch { /* ignore */ }
  }

  window.addEventListener("beforeinstallprompt", (e) => {
    e.preventDefault();
    deferred = e;
    show(`<b>Get Max Alert on your home screen</b><span>Opens like an app, even with bad signal.</span>
      <button type="button" class="primary small" data-install="go">Install</button>`);
  });
  window.addEventListener("appinstalled", () => hide(true));

  if (isIOS && isSafari) {
    document.addEventListener("DOMContentLoaded", () => show(
      `<b>Get Max Alert on your home screen</b><span>Tap <span class="share-ico" aria-label="Share">` +
      `<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 3v12M7 8l5-5 5 5M5 12v8h14v-8"/></svg></span>` +
      ` Share, then <b>Add to Home Screen</b>.</span>`));
  }

  document.addEventListener("click", async (e) => {
    const b = e.target.closest("[data-install]");
    if (!b) return;
    if (b.dataset.install === "dismiss") return hide(true);
    if (b.dataset.install === "go" && deferred) {
      deferred.prompt();
      const { outcome } = await deferred.userChoice;
      deferred = null;
      hide(outcome === "accepted");
    }
  });
})();
