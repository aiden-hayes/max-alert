"""Favorite dishes and the morning "it's on the menu today" push.

    GET  /api/favorites                 -> {"favorites": [{"dish", "name"}], "alerts": {...}}
    POST /api/favorites {"dish", "on"}  star (on=true) or unstar a dish
    PUT  /api/favorites/alerts {"enabled": bool}
    POST /api/favorites/alerts/test     send a test push to your topic
    POST /api/cron/daily                the scheduled wake-up (Authorization: Bearer CRON_SECRET)

Pushes go through ntfy (https://ntfy.sh): each account gets its own random topic,
which the user subscribes to in the free ntfy app. Anyone who knows a topic name can
read it, so it's long and random and only shown to its owner.

Once a day, after FAVORITE_ALERT_TIME (campus time), send_due() looks at today's menu
and sends each user one push listing their starred dishes and the meals they're at.
Render's free plan sleeps, so something has to wake the app: a scheduled GitHub
Action calls /api/cron/daily (see .github/workflows/daily-alerts.yml), and any page
visit after the alert time runs the same check. Each user is claimed with one atomic
UPDATE before sending, so two triggers at once can't send twice.
"""
from __future__ import annotations

import datetime as dt
import hmac
import logging
import secrets
import threading

import requests
from flask import Blueprint, jsonify, request
from flask_login import current_user, login_required
from sqlalchemy import select, update

import config
from auth import require_json
from models import AlertSettings, Favorite, db
from ratings import dish_key

log = logging.getLogger("max-alert")
bp = Blueprint("favorites", __name__, url_prefix="/api/favorites")
bp.before_request(require_json)
cron_bp = Blueprint("cron", __name__, url_prefix="/api/cron")

# Set by app.py: the menu store, names on this week's menu, and "Lunch 11:00 AM – 2:00 PM".
_hooks = {"store": None, "week_dishes": lambda: set(), "meal_label": lambda date, meal: meal}


def init(store, week_dishes, meal_label) -> None:
    _hooks.update(store=store, week_dishes=week_dishes, meal_label=meal_label)


def _settings(user_id: int, create: bool = True) -> AlertSettings | None:
    s = db.session.get(AlertSettings, user_id)
    if s is None and create:
        s = AlertSettings(user_id=user_id, topic=f"maxalert-{secrets.token_hex(10)}", enabled=True)
        db.session.add(s)
        db.session.commit()
    return s


def _alerts_dict(s: AlertSettings) -> dict:
    return {"enabled": s.enabled, "topic": s.topic, "server": config.NTFY_SERVER,
            "send_at": _fmt_time(config.FAVORITE_ALERT_TIME)}


def _fmt_time(hhmm: str) -> str:
    return dt.datetime.strptime(hhmm, "%H:%M").strftime("%-I:%M %p")


def _list(user_id: int) -> list[dict]:
    rows = db.session.execute(select(Favorite).where(Favorite.user_id == user_id).order_by(Favorite.name)).scalars()
    return [{"dish": f.dish, "name": f.name} for f in rows]


# ---- API ---------------------------------------------------------------------------
@bp.get("")
@login_required
def get_favorites():
    return jsonify({"favorites": _list(current_user.id), "alerts": _alerts_dict(_settings(current_user.id))})


@bp.post("")
@login_required
def star():
    body = request.get_json(silent=True) or {}
    name, on = " ".join(str(body.get("dish") or "").split())[:120], body.get("on")
    key = dish_key(name)
    if not key or not isinstance(on, bool):
        return jsonify({"error": "Send a dish name and on: true or false."}), 400
    row = db.session.execute(
        select(Favorite).where(Favorite.user_id == current_user.id, Favorite.dish == key)).scalar_one_or_none()
    if on and not row:
        if key not in {dish_key(n) for n in _hooks["week_dishes"]()}:
            return jsonify({"error": "That dish isn't on this week's menu."}), 404
        count = db.session.query(Favorite).filter_by(user_id=current_user.id).count()
        if count >= config.MAX_FAVORITES:
            return jsonify({"error": f"You can star up to {config.MAX_FAVORITES} dishes."}), 400
        db.session.add(Favorite(user_id=current_user.id, dish=key, name=name))
    elif not on and row:
        db.session.delete(row)
    db.session.commit()
    return jsonify({"favorites": _list(current_user.id), "alerts": _alerts_dict(_settings(current_user.id))})


@bp.put("/alerts")
@login_required
def set_alerts():
    enabled = (request.get_json(silent=True) or {}).get("enabled")
    if not isinstance(enabled, bool):
        return jsonify({"error": "enabled must be true or false"}), 400
    s = _settings(current_user.id)
    s.enabled = enabled
    db.session.commit()
    return jsonify({"alerts": _alerts_dict(s)})


@bp.post("/alerts/test")
@login_required
def test_alert():
    s = _settings(current_user.id)
    ok = push(s.topic, "Max Alert test", "Notifications work. You'll hear from us on mornings "
              "when a dish you starred is on the menu.", _site_url())
    if not ok:
        return jsonify({"error": "Couldn't reach ntfy right now. Try again in a minute."}), 502
    return jsonify({"sent": True})


@cron_bp.post("/daily")
def cron_daily():
    """For the scheduled wake-up. 503 + retry while today's menu is still downloading."""
    if not config.CRON_SECRET:
        return jsonify({"error": "CRON_SECRET isn't set on the server."}), 404
    given = request.headers.get("Authorization", "").removeprefix("Bearer ").strip()
    if not hmac.compare_digest(given.encode(), config.CRON_SECRET.encode()):
        return jsonify({"error": "Wrong or missing cron secret."}), 403
    store = _hooks["store"]
    store.ensure_fresh()  # a sleeping server starts with no menus: download in the background
    today = _today()
    if not store.snapshot()["days"].get(today.isoformat()):
        return jsonify({"retry": True, "reason": "Today's menu isn't downloaded yet."}), 503
    return jsonify(send_due())


# ---- sending -------------------------------------------------------------------------
def _today() -> dt.date:
    return dt.datetime.now(config.CAMPUS_TZ).date()


def _site_url() -> str:
    if config.PUBLIC_URL:
        return config.PUBLIC_URL
    try:
        return request.url_root
    except RuntimeError:  # no request (background thread)
        return ""


def push(topic: str, title: str, message: str, click: str = "") -> bool:
    headers = {"Title": title.encode("utf-8"), "Tags": "fork_and_knife"}
    if click:
        headers["Click"] = click
    try:
        resp = requests.post(f"{config.NTFY_SERVER}/{topic}", data=message.encode("utf-8"),
                             headers=headers, timeout=10)
        resp.raise_for_status()
        return True
    except requests.RequestException as e:
        log.error("Couldn't send favorite alert: %s", e)
        return False


def todays_matches(day_menu: dict, favs: dict[str, str], date: str) -> dict[str, list[str]]:
    """{favorite name: ["Lunch 11:00 AM – 2:00 PM", ...]} for favorites on today's menu."""
    found: dict[str, list[str]] = {}
    for meals in day_menu.values():
        for meal in meals:
            for st in meal["stations"]:
                for item in st["items"]:
                    key = dish_key(item["name"])
                    if key in favs:
                        label = _hooks["meal_label"](date, meal["name"])
                        if label not in found.setdefault(favs[key], []):
                            found[favs[key]].append(label)
    return found


def message_for(found: dict[str, list[str]]) -> tuple[str, str]:
    names = sorted(found)
    if len(names) == 1:
        title = f"{names[0]} is on the menu today"
        return title, f"At The MAX: {', '.join(found[names[0]])}."
    title = f"{len(names)} of your favorites are on today's menu"
    return title, "\n".join(f"• {n}: {', '.join(found[n])}" for n in names)


def send_due(click: str = "") -> dict:
    """Send today's pushes to everyone not yet checked today. Safe to call any number of times."""
    now = dt.datetime.now(config.CAMPUS_TZ)
    if now.time() < dt.time.fromisoformat(config.FAVORITE_ALERT_TIME):
        return {"skipped": "too early"}
    today = now.date().isoformat()
    day_menu = _hooks["store"].snapshot()["days"].get(today)
    if not day_menu:
        return {"skipped": "no menu for today yet"}
    click = click or _site_url()
    sent = checked = 0
    user_ids = db.session.execute(select(AlertSettings.user_id).where(
        AlertSettings.enabled.is_(True),
        (AlertSettings.last_sent_date.is_(None)) | (AlertSettings.last_sent_date != today))).scalars().all()
    for uid in user_ids:
        favs = {f.dish: f.name for f in db.session.execute(
            select(Favorite).where(Favorite.user_id == uid)).scalars()}
        if not favs:
            continue  # nothing starred: leave unclaimed so a star later today still counts
        # Claim this user for today; if another trigger already did, skip.
        claimed = db.session.execute(update(AlertSettings).where(
            AlertSettings.user_id == uid,
            (AlertSettings.last_sent_date.is_(None)) | (AlertSettings.last_sent_date != today),
        ).values(last_sent_date=today)).rowcount
        db.session.commit()
        if not claimed:
            continue
        checked += 1
        found = todays_matches(day_menu, favs, today)
        if found:
            topic = db.session.get(AlertSettings, uid).topic
            if push(topic, *message_for(found), click):
                sent += 1
    log.info("Favorite alerts: checked %d users, sent %d", checked, sent)
    return {"checked": checked, "sent": sent}


# ---- run on visits too ------------------------------------------------------------------
_visit_lock = threading.Lock()
_visit_ran_on: str | None = None


def maybe_run_on_visit(app, click: str) -> None:
    """Called on page loads: once per day after the alert time, run send_due in a thread."""
    global _visit_ran_on
    if not config.FAVORITE_ALERTS_ON_VISIT:
        return
    now = dt.datetime.now(config.CAMPUS_TZ)
    today = now.date().isoformat()
    if _visit_ran_on == today or now.time() < dt.time.fromisoformat(config.FAVORITE_ALERT_TIME):
        return
    if not _hooks["store"].snapshot()["days"].get(today):
        return  # menus still downloading; a later visit will try again
    with _visit_lock:
        if _visit_ran_on == today:
            return
        _visit_ran_on = today

    def run():
        with app.app_context():
            try:
                send_due(click=click)
            except Exception:
                log.exception("favorite alerts failed")
    threading.Thread(target=run, daemon=True).start()
