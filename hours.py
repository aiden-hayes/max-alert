"""Opening hours for every GCSU dining spot (Chick-fil-A, Einstein Bros., Books & Brew, ...).

Source: the GCSU Dining hours page (HOURS_URL). It's a React page that ships its
data in `window.__PRELOADED_STATE__ = {...}`; each location there has

    openingHours: {
      standardHours: [{days: [...], hours: [{startTime, finishTime, label?, allDay}]}],
      seasonalHours: [{from: "2026-10-12T00:57:32Z", to: "...", openingHours: [same as above]}]
    }

Sodexo's editors aren't consistent, so this tolerates:
  - days as "Monday", "1 Monday", or {"value": "Monday", ...}; an empty list = every day
  - allDay as true/false or "true"/"False"
  - an entry with no times = closed (its label, e.g. "Summer Break", becomes a note)
  - finishTime "12:00 AM" = midnight (closes after the start time, past 11:59 PM)
Seasonal ranges (breaks, holidays, weather) win over standard hours on their dates.
The date in from/to is used as written (Sodexo stores local dates with a "Z").

Downloaded at most every HOURS_REFRESH_HOURS in a background thread and saved to
data/hours_cache.json; page requests never wait on GCSU's site.
"""
from __future__ import annotations

import datetime as dt
import json
import logging
import re
import threading

import requests

import config

log = logging.getLogger("max-alert")
HOURS_URL = "https://georgiacollege.sodexomyway.com/en-us/locations/hours"
CACHE_FILE = config.DATA_DIR / "hours_cache.json"
HOURS_REFRESH_HOURS = 6
RETRY_AFTER_FAILURE_MIN = 15
DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
UA = {"User-Agent": "MaxAlert student menu app (personal, non-commercial)"}


# ---- parsing --------------------------------------------------------------------
def _state(html: str) -> dict:
    marker = "window.__PRELOADED_STATE__"
    i = html.index(marker)
    i = html.index("=", i) + 1
    while html[i].isspace():
        i += 1
    return json.JSONDecoder().raw_decode(html, i)[0]


def _find_locations(obj) -> list[dict]:
    """Every object that has openingHours.standardHours, in page order."""
    out = []
    if isinstance(obj, dict):
        oh = obj.get("openingHours")
        if isinstance(oh, dict) and "standardHours" in oh and obj.get("name"):
            out.append(obj)
        else:
            for v in obj.values():
                out += _find_locations(v)
    elif isinstance(obj, list):
        for v in obj:
            out += _find_locations(v)
    return out


def _day_index(d) -> int | None:
    name = d.get("value") if isinstance(d, dict) else d
    name = re.sub(r"^\s*\d+\s*", "", str(name or "")).strip().lower()
    for i, full in enumerate(DAYS):
        if name and full.lower().startswith(name[:3]):
            return i
    return None


def _time(t) -> str | None:
    """{"hour": "05", "minute": "00", "period": "PM"} -> "17:00"."""
    if not isinstance(t, dict) or t.get("hour") in (None, ""):
        return None
    h, m = int(t["hour"]), int(t.get("minute") or 0)
    period = str(t.get("period") or "").upper()
    if period == "PM" and h != 12:
        h += 12
    elif period == "AM" and h == 12:
        h = 0
    return f"{h:02d}:{m:02d}"


def _truthy(v) -> bool:
    return v is True or str(v).strip().lower() == "true"


def _clean(text) -> str:
    return " ".join(str(text or "").split())


def _groups(entries: list[dict]) -> list[dict]:
    """[{days: {0..6}, periods: [{label, start, end}], note}]"""
    groups = []
    for g in entries or []:
        days = {i for i in map(_day_index, g.get("days") or []) if i is not None} or set(range(7))
        periods, notes = [], []
        for h in g.get("hours") or []:
            label = _clean(h.get("label"))
            if _truthy(h.get("allDay")):
                periods.append({"label": label, "start": "00:00", "end": "24:00"})
                continue
            start, end = _time(h.get("startTime")), _time(h.get("finishTime"))
            if start and end:
                periods.append({"label": label, "start": start, "end": "24:00" if end == "00:00" else end})
            elif label:
                notes.append(label)  # e.g. "Closed", "Summer Break"
        periods.sort(key=lambda p: p["start"])
        groups.append({"days": sorted(days), "periods": periods, "note": ", ".join(notes)})
    return groups


def parse(html: str) -> list[dict]:
    locations = []
    for loc in _find_locations(_state(html)):
        oh = loc["openingHours"]
        street = (loc.get("address") or {}).get("street", "") if isinstance(loc.get("address"), dict) else ""
        seasons = []
        for s in oh.get("seasonalHours") or []:
            try:
                start, end = dt.date.fromisoformat(s["from"][:10]), dt.date.fromisoformat(s["to"][:10])
            except (KeyError, TypeError, ValueError):
                continue
            seasons.append({"from": start.isoformat(), "to": end.isoformat(),
                            "groups": _groups(s.get("openingHours"))})
        locations.append({
            "slug": _clean(loc.get("slug")) or _clean(loc["name"]).lower(),
            "name": _clean(loc["name"]),
            # "231 W. Hancock St Milledgeville, GA 31061 | Maxwell Student Union" -> the building;
            # otherwise just the street ("140 West Campus Drive").
            "where": _clean(street.split("|")[-1]) if "|" in street
            else re.sub(r",?\s*Milledgeville\b.*$", "", _clean(street), flags=re.I),
            "standard": _groups(oh.get("standardHours")),
            "seasonal": seasons,
        })
    return locations


# ---- schedule math -------------------------------------------------------------
def day_schedule(loc: dict, day: dt.date) -> dict:
    """{"periods": [...], "note": str, "special": bool} for one location on one date."""
    iso, wd = day.isoformat(), day.weekday()
    seasons = [s for s in loc["seasonal"] if s["from"] <= iso <= s["to"]]
    if seasons:  # the shortest matching range is the most specific
        s = min(seasons, key=lambda s: (dt.date.fromisoformat(s["to"]) - dt.date.fromisoformat(s["from"])))
        for g in s["groups"]:
            if wd in g["days"]:
                return {"periods": g["periods"], "note": g["note"] or ("" if g["periods"] else "Closed"),
                        "special": True}
        return {"periods": [], "note": "Closed", "special": True}
    for g in loc["standard"]:
        if wd in g["days"]:
            return {"periods": g["periods"], "note": g["note"], "special": False}
    return {"periods": [], "note": "", "special": False}


def _at(day: dt.date, hhmm: str) -> dt.datetime:
    h, m = map(int, hhmm.split(":"))
    return dt.datetime.combine(day, dt.time(0, 0), tzinfo=config.CAMPUS_TZ) + dt.timedelta(hours=h, minutes=m)


def _spans(loc: dict, first: dt.date, days: int) -> list[tuple[dt.datetime, dt.datetime]]:
    """Open intervals over a range of dates, back-to-back ones merged (Dinner -> Late Night)."""
    spans = []
    for i in range(days):
        day = first + dt.timedelta(days=i)
        for p in day_schedule(loc, day)["periods"]:
            start, end = _at(day, p["start"]), _at(day, p["end"])
            if end <= start:
                end += dt.timedelta(days=1)  # closes after midnight
            spans.append((start, end))
    spans.sort()
    merged = []
    for s, e in spans:
        if merged and s <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], e))
        else:
            merged.append((s, e))
    return merged


def fmt(t: dt.datetime | str) -> str:
    if isinstance(t, str):
        if t == "24:00":
            return "12:00 AM"
        t = dt.datetime.strptime(t, "%H:%M")
    return t.strftime("%-I:%M %p")


def status(loc: dict, now: dt.datetime) -> dict:
    """{"open": bool, "text": "Open until 3:00 PM" | "Opens tomorrow at 7:30 AM" | "Closed"}"""
    for start, end in _spans(loc, now.date() - dt.timedelta(days=1), 9):
        if start <= now < end:
            return {"open": True, "text": f"Open until {fmt(end)}" + ("" if end.date() == now.date() or end.time() == dt.time(0) else " tomorrow")}
        if start > now:
            days = (start.date() - now.date()).days
            when = "" if days == 0 else "tomorrow " if days == 1 else f"{start:%a} "
            return {"open": False, "text": f"Opens {when}at {fmt(start)}"}
    return {"open": False, "text": "Closed this week"}


def week(loc: dict, today: dt.date, days: int = 7) -> list[dict]:
    out = []
    for i in range(days):
        day = today + dt.timedelta(days=i)
        sched = day_schedule(loc, day)
        out.append({
            "date": day.isoformat(),
            "day": "Today" if i == 0 else "Tomorrow" if i == 1 else f"{day:%a}",
            "periods": [{"label": p["label"], "text": f"{fmt(p['start'])} – {fmt(p['end'])}"} for p in sched["periods"]],
            "note": sched["note"],
            "special": sched["special"],
        })
    return out


# ---- download + cache ------------------------------------------------------------
class HoursStore:
    def __init__(self, fetch=None):
        self._fetch = fetch or (lambda: requests.get(HOURS_URL, headers=UA, timeout=config.REQUEST_TIMEOUT).text)
        self._lock = threading.Lock()
        self._busy = threading.Event()
        self.data = {"locations": [], "last_success": None, "last_attempt": None, "last_error": None}
        try:
            self.data.update(json.loads(CACHE_FILE.read_text()))
        except (OSError, ValueError):
            pass

    def _stale(self, now: dt.datetime) -> bool:
        last = self.data.get("last_attempt")
        if not last:
            return True
        limit = dt.timedelta(minutes=RETRY_AFTER_FAILURE_MIN) if self.data.get("last_error") \
            else dt.timedelta(hours=HOURS_REFRESH_HOURS)
        return now - dt.datetime.fromisoformat(last) > limit

    def refresh(self) -> None:
        now = dt.datetime.now(config.CAMPUS_TZ)
        try:
            locations = parse(self._fetch())
            if not locations:
                raise ValueError("no locations found on the hours page")
            update = {"locations": locations, "last_success": now.isoformat(), "last_error": None}
        except Exception as e:  # network, page changed, ...
            log.warning("Hours refresh failed: %s", e)
            update = {"last_error": str(e)[:300]}
        with self._lock:
            self.data.update(update, last_attempt=now.isoformat())
            tmp = CACHE_FILE.with_suffix(".tmp")
            tmp.write_text(json.dumps(self.data))
            tmp.replace(CACHE_FILE)

    def ensure_fresh(self) -> None:
        if self._busy.is_set() or not self._stale(dt.datetime.now(config.CAMPUS_TZ)):
            return
        self._busy.set()

        def run():
            try:
                self.refresh()
            finally:
                self._busy.clear()
        threading.Thread(target=run, daemon=True).start()

    def snapshot(self) -> dict:
        with self._lock:
            return json.loads(json.dumps(self.data))

    @property
    def loading(self) -> bool:
        return self._busy.is_set()
