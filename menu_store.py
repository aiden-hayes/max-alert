"""Caches the week of menus in memory and on disk (data/menu_cache.json).

- Page loads never hit Sodexo directly: they read the cache.
- When the cache is older than REFRESH_HOURS, a background refresh starts
  (the page still gets the cached copy immediately).
- If a download fails, the old data for that day is kept and the error is
  reported to the frontend, which shows a friendly message + "last updated".
- The disk copy means the last good menu survives a server restart.
- Self-repair: if EVERY download fails (e.g. Sodexo rotated its key or started a
  new menu ID), the store looks the values up again (discover.rediscover), and if
  verified new ones work it saves them (config.save_sodexo_override) and retries.
- Alerts: after ALERT_AFTER_HOURS of nothing but failures, alerts.send() is
  called (then daily while it lasts), plus a note when menus come back.
"""
from __future__ import annotations

import datetime as dt
import json
import logging
import threading
from concurrent.futures import ThreadPoolExecutor

import alerts
import config
import demo_data
import sodexo

log = logging.getLogger("max-alert")
CACHE_FILE = config.DATA_DIR / "menu_cache.json"
RETRY_AFTER_FAILURE_MIN = 15
# Bump when the normalized item shape changes, so old caches get re-downloaded once.
SCHEMA_VERSION = 3  # v2: added carbs_g / fat_g nutrition fields; v3: renamed/reordered stations


def campus_now() -> dt.datetime:
    return dt.datetime.now(config.CAMPUS_TZ)


def _iso(ts: dt.datetime | None) -> str | None:
    return ts.isoformat() if ts else None


def _hours_since(iso: str | None, now: dt.datetime) -> float | None:
    return (now - dt.datetime.fromisoformat(iso)).total_seconds() / 3600 if iso else None


def stale_since(data: dict, now: dt.datetime | None = None) -> str | None:
    """When menus stopped updating, if every refresh has failed for ALERT_AFTER_HOURS. Else None."""
    since = data.get("failing_since")
    if data.get("source") != "live" or not since:
        return None
    return since if _hours_since(since, now or campus_now()) >= config.ALERT_AFTER_HOURS else None


def _rediscover():
    import discover  # only needed when something's broken
    return discover.rediscover(config.load_locations(), config.api_key(), config.api_url())


def _describe_fix(fix: dict) -> str:
    changed = []
    if fix["api_key"] != config.api_key():
        changed.append("API key")
    if fix["api_url"] != config.api_url():
        changed.append(f"API address (now {fix['api_url'].split('/data/')[0]})")
    old = {l["slug"]: l for l in config.load_locations()}
    for slug, ids in fix["locations"].items():
        if old.get(slug, {}).get("menu_id") != ids["menu_id"] or old.get(slug, {}).get("location_id") != ids["location_id"]:
            changed.append(f"{old.get(slug, {}).get('name', slug)} IDs (now location {ids['location_id']}, menu {ids['menu_id']})")
    return ", ".join(changed) or "settings"


class MenuStore:
    def __init__(self, fetch=sodexo.fetch_raw, rediscover=None, notify=None):
        self._fetch = fetch
        self._rediscover = rediscover or _rediscover
        self._notify = notify or alerts.send
        self._lock = threading.Lock()
        self._refreshing = threading.Event()
        self.data = {"days": {}, "last_success": None, "last_attempt": None,
                     "last_error": None, "source": None}
        self._load_disk()

    # ---- disk -----------------------------------------------------------
    def _load_disk(self) -> None:
        try:
            self.data.update(json.loads(CACHE_FILE.read_text()))
        except FileNotFoundError:
            pass
        except (OSError, ValueError) as e:
            log.warning("Ignoring unreadable cache file: %s", e)

    def _save_disk(self) -> None:
        tmp = CACHE_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.data))
        tmp.replace(CACHE_FILE)

    # ---- which data source ---------------------------------------------
    @staticmethod
    def locations() -> list[dict]:
        if config.DEMO_MODE or not config.SODEXO_API_KEY:
            return [{"slug": "the-max", "name": "The MAX"}]
        return config.load_locations()

    @staticmethod
    def mode() -> str:
        if config.DEMO_MODE or not config.SODEXO_API_KEY:
            return "demo"
        return "live" if config.load_locations() else "unconfigured"

    # ---- refresh --------------------------------------------------------
    def _mismatch(self) -> bool:
        """Cache came from a different mode (demo vs live) or an older item format."""
        return self.data.get("source") != self.mode() or self.data.get("schema") != SCHEMA_VERSION

    def is_stale(self) -> bool:
        if self._mismatch():
            return True
        last = self.data.get("last_attempt")
        if not last:
            return True
        age = campus_now() - dt.datetime.fromisoformat(last)
        limit = dt.timedelta(minutes=RETRY_AFTER_FAILURE_MIN) if self.data.get("last_error") \
            else dt.timedelta(hours=config.REFRESH_HOURS)
        return age > limit

    def refresh(self) -> None:
        """Download today + next 6 days for every location. Safe to call from a thread."""
        if self._refreshing.is_set():
            return
        self._refreshing.set()
        try:
            self._do_refresh()
        finally:
            self._refreshing.clear()

    def _do_refresh(self, allow_fix: bool = True) -> None:
        mode = self.mode()
        today = campus_now().date()
        days = [today + dt.timedelta(days=i) for i in range(config.DAYS_AHEAD)]
        locations = self.locations()
        jobs = [(loc, d) for loc in locations for d in days]
        errors: list[str] = []

        def download(job):
            loc, day = job
            if mode == "demo":
                return demo_data.demo_day(day)
            last_err = None
            for _ in range(config.ATTEMPTS_PER_DOWNLOAD):
                try:
                    return sodexo.normalize(self._fetch(loc, day))
                except Exception as e:  # network, HTTP, bad JSON
                    last_err = e
            return f"{loc['name']} {day}: {last_err}"

        if mode == "unconfigured":
            results = []
            errors.append("No dining locations configured. Run `python discover.py` (see README).")
        else:
            with ThreadPoolExecutor(max_workers=config.PARALLEL_DOWNLOADS) as pool:
                results = list(pool.map(download, jobs))

        with self._lock:
            if self.data.get("source") != mode:
                self.data["days"] = {}  # don't mix demo and real data
            store = self.data["days"]
            ok = 0
            for (loc, day), result in zip(jobs, results):
                if isinstance(result, str):
                    errors.append(result)
                    continue  # keep whatever we had for that day
                store.setdefault(day.isoformat(), {})[loc["slug"]] = result
                ok += 1
            # drop past days
            for key in [k for k in store if k < today.isoformat()]:
                del store[key]
            now = campus_now()
            self.data["last_attempt"] = _iso(now)
            if ok:
                self.data["last_success"] = _iso(now)
                self.data["failing_since"] = None
            elif mode == "live" and jobs:
                self.data["failing_since"] = self.data.get("failing_since") or _iso(now)
            self.data["last_error"] = "; ".join(errors[:3]) if errors else None
            self.data["partial_failures"] = len(errors)
            self.data["source"] = mode
            self.data["schema"] = SCHEMA_VERSION
            self.data["locations"] = [{"slug": l["slug"], "name": l["name"]} for l in locations]
            self._save_disk()
        if errors:
            log.warning("Menu refresh had %d failures, e.g. %s", len(errors), errors[0])
        else:
            log.info("Menu refresh OK (%d location-days)", ok)

        if mode == "live" and jobs and not ok and allow_fix and self._try_fix():
            self._do_refresh(allow_fix=False)  # retry with the new values (this also handles alerts)
            return
        self._check_alerts(mode, ok)

    # ---- self-repair + alerts -------------------------------------------
    def _try_fix(self) -> bool:
        """Every download failed: look the key / menu IDs up again (throttled). True if fixed."""
        now = campus_now()
        last = _hours_since(self.data.get("last_fix_attempt"), now)
        if last is not None and last < config.REDISCOVER_EVERY_HOURS:
            return False
        with self._lock:
            self.data["last_fix_attempt"] = _iso(now)
            self._save_disk()
        try:
            fix = self._rediscover()
        except Exception:
            log.exception("Looking up new Sodexo settings failed")
            return False
        if not fix:
            log.warning("All menu downloads failed and no new working Sodexo settings were found")
            return False
        what = _describe_fix(fix)
        config.save_sodexo_override(fix)
        log.warning("Sodexo settings changed (%s); using the new ones", what)
        self._notify(
            f"Sodexo changed its {what}. Max Alert found the new values and fixed itself. "
            "To make it permanent (it redoes this after each redeploy), run `python discover.py`, "
            "commit locations.json, and update SODEXO_API_KEY on your host.")
        return True

    def _check_alerts(self, mode: str, ok: int) -> None:
        now = campus_now()
        since = stale_since(self.data, now)
        sent = self.data.get("alert_sent_at")
        message = None
        if since:
            sent_age = _hours_since(sent, now)
            if sent_age is None or sent_age >= config.ALERT_REPEAT_HOURS:
                hours = int(_hours_since(since, now))
                message = (f"Menus haven't updated for {hours} hours: every download from Sodexo is failing "
                           f"and the automatic fix didn't work. Last error: {(self.data.get('last_error') or '')[:300]} "
                           "See \"If it breaks\" in the README.")
                sent = _iso(now)
        elif sent and ok and mode == "live":
            message, sent = "Menus are downloading again. All good.", None
        if message is None:
            return
        self._notify(message)
        with self._lock:
            self.data["alert_sent_at"] = sent
            self._save_disk()

    def ensure_fresh(self) -> None:
        """Refresh in the background if stale. Only demo data (instant, no network) blocks:
        a slow Sodexo response must never hold up a page request past the server's timeout."""
        if not self.is_stale():
            return
        if self.mode() == "demo" and (self._mismatch() or not self.data.get("days")):
            self.refresh()
        elif not self._refreshing.is_set():
            # Set before the thread starts so this response already says "refreshing"
            # and the page keeps polling instead of showing "No menu data yet".
            self._refreshing.set()
            threading.Thread(target=self._refresh_in_background, daemon=True).start()

    def _refresh_in_background(self) -> None:
        try:
            self._do_refresh()
        finally:
            self._refreshing.clear()

    def snapshot(self) -> dict:
        with self._lock:
            return json.loads(json.dumps(self.data))

    @property
    def refreshing(self) -> bool:
        return self._refreshing.is_set()
