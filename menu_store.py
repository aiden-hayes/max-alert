"""Caches the week of menus in memory and on disk (data/menu_cache.json).

- Page loads never hit Sodexo directly: they read the cache.
- When the cache is older than REFRESH_HOURS, a background refresh starts
  (the page still gets the cached copy immediately).
- If a download fails, the old data for that day is kept and the error is
  reported to the frontend, which shows a friendly message + "last updated".
- The disk copy means the last good menu survives a server restart.
"""
from __future__ import annotations

import datetime as dt
import json
import logging
import threading
from concurrent.futures import ThreadPoolExecutor

import config
import demo_data
import sodexo

log = logging.getLogger("max-alert")
CACHE_FILE = config.DATA_DIR / "menu_cache.json"
RETRY_AFTER_FAILURE_MIN = 15
# Bump when the normalized item shape changes, so old caches get re-downloaded once.
SCHEMA_VERSION = 2  # v2: added carbs_g / fat_g nutrition fields


def campus_now() -> dt.datetime:
    return dt.datetime.now(config.CAMPUS_TZ)


def _iso(ts: dt.datetime | None) -> str | None:
    return ts.isoformat() if ts else None


class MenuStore:
    def __init__(self, fetch=sodexo.fetch_raw):
        self._fetch = fetch
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

    def _do_refresh(self) -> None:
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

    def ensure_fresh(self) -> None:
        """Refresh in the background if stale; block only if we have nothing at all."""
        if not self.is_stale():
            return
        if self._mismatch() or not self.data.get("days"):
            self.refresh()
        else:
            threading.Thread(target=self.refresh, daemon=True).start()

    def snapshot(self) -> dict:
        with self._lock:
            return json.loads(json.dumps(self.data))

    @property
    def refreshing(self) -> bool:
        return self._refreshing.is_set()
