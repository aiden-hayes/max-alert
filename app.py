"""Max Alert: Flask backend. Run with `python app.py` and open http://localhost:5000."""
from __future__ import annotations

import datetime as dt
import logging

from flask import Flask, jsonify, request, send_from_directory

import config
import summary
from auth import bp as auth_bp, login_manager
from menu_store import MenuStore, campus_now, stale_since
from models import db
from tracker import bp as tracker_bp

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
app = Flask(__name__, static_folder="static", static_url_path="/static")
store = MenuStore()

# ---- Accounts + calorie log (auth.py, tracker.py, models.py) ----------------
app.config.update(
    SECRET_KEY=config.SECRET_KEY,
    SQLALCHEMY_DATABASE_URI=config.DATABASE_URL,
    SESSION_COOKIE_SAMESITE="Lax", REMEMBER_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=config.SECURE_COOKIES, REMEMBER_COOKIE_SECURE=config.SECURE_COOKIES,
    REMEMBER_COOKIE_DURATION=dt.timedelta(days=60),
)
db.init_app(app)
login_manager.init_app(app)
app.register_blueprint(auth_bp)
app.register_blueprint(tracker_bp)
with app.app_context():
    db.create_all()  # creates data/maxalert.db and any missing tables; never deletes data


@app.cli.command("init-db")
def init_db():
    """Create the database tables (they're also created automatically on startup)."""
    db.create_all()
    print(f"Database ready: {config.DATABASE_URL}")

# Used when config.MEAL_HOURS has no entry for a meal name on that day.
DEFAULT_HOURS = {
    "Breakfast": ("07:00", "10:45"), "Brunch": ("10:00", "14:00"), "Lunch": ("11:00", "14:00"),
    "Dinner": ("16:30", "20:00"), "Late Night": ("20:00", "23:00"),
}


def meal_hours(day: dt.date, meal: str) -> tuple[dt.time, dt.time] | None:
    hours = config.MEAL_HOURS.get(day.weekday(), {}).get(meal) or DEFAULT_HOURS.get(meal)
    if not hours:
        return None
    return dt.time.fromisoformat(hours[0]), dt.time.fromisoformat(hours[1])


def meals_on(days: dict, date: str) -> list[str]:
    names = {m["name"] for loc in days.get(date, {}).values() for m in loc}

    def key(n):
        h = meal_hours(dt.date.fromisoformat(date), n)
        order = config.MEAL_ORDER.index(n) if n in config.MEAL_ORDER else 99
        return (h[0] if h else dt.time(23, 59), order)
    return sorted(names, key=key)


def fmt(t: dt.time) -> str:
    return dt.datetime.combine(dt.date.today(), t).strftime("%-I:%M %p")


def current_or_next_meal(days: dict) -> dict:
    now = campus_now()
    for offset in range(config.DAYS_AHEAD):
        day = now.date() + dt.timedelta(days=offset)
        for name in meals_on(days, day.isoformat()):
            hours = meal_hours(day, name)
            if offset == 0 and hours and now.time() >= hours[1]:
                continue  # already over
            status = "next"
            if offset == 0 and hours and hours[0] <= now.time() < hours[1]:
                status = "now"
            label = ""
            if hours:
                label = f"Until {fmt(hours[1])}" if status == "now" else \
                    f"{'Today' if offset == 0 else 'Tomorrow' if offset == 1 else day.strftime('%A')} " \
                    f"{fmt(hours[0])} – {fmt(hours[1])}"
            # When this answer changes (meal ends, or the next one starts), so the page can switch then.
            changes_at = None
            if hours:
                t = hours[1] if status == "now" else hours[0]
                changes_at = dt.datetime.combine(day, t, tzinfo=config.CAMPUS_TZ).isoformat()
            return {"date": day.isoformat(), "meal": name, "status": status, "label": label,
                    "changes_at": changes_at}
    return {"date": now.date().isoformat(), "meal": None, "status": "none", "label": "", "changes_at": None}


@app.get("/")
def index():
    return send_from_directory("static", "index.html")


@app.get("/api/menus")
def menus():
    try:
        store.ensure_fresh()
    except Exception:  # never let a refresh problem break the page
        logging.exception("refresh failed")
    data = store.snapshot()
    now = campus_now()
    hours = {}
    for i in range(config.DAYS_AHEAD):
        d = now.date() + dt.timedelta(days=i)
        hours[d.isoformat()] = {
            m: [fmt(h[0]), fmt(h[1])] for m in meals_on(data["days"], d.isoformat())
            if (h := meal_hours(d, m))
        }
    return jsonify({
        "mode": data.get("source") or store.mode(),
        "locations": data.get("locations") or store.locations(),
        "days": data["days"],
        "dates": [(now.date() + dt.timedelta(days=i)).isoformat() for i in range(config.DAYS_AHEAD)],
        "last_success": data.get("last_success"),
        "last_error": data.get("last_error"),
        "stale_since": stale_since(data, now),  # menus stuck for ALERT_AFTER_HOURS+
        "refreshing": store.refreshing,
        "now": now.isoformat(),
        "current": current_or_next_meal(data["days"]),
        "hours": hours,
    })


@app.post("/api/summary")
def ai_summary():
    body = request.get_json(silent=True) or {}
    data = store.snapshot()
    date = body.get("date") or campus_now().date().isoformat()
    loc = body.get("location")
    day = data["days"].get(date, {})
    if loc and loc != "all":
        day = {loc: day.get(loc, [])}
    if not any(day.values()):
        return jsonify({"error": "There's no menu posted for that day yet, so there's nothing to judge."}), 404
    names = {l["slug"]: l["name"] for l in data.get("locations") or []}
    try:
        text = summary.summarize(date, summary.menu_as_text(day, names))
    except summary.SummaryError as e:
        return jsonify({"error": str(e)}), 503
    return jsonify({"summary": text})


@app.get("/api/health")
def health():
    data = store.snapshot()
    return jsonify({"ok": True, "mode": store.mode(), "locations": len(store.locations()),
                    "menus_ok": stale_since(data) is None, "last_success": data.get("last_success"),
                    "failing_since": data.get("failing_since")})


if __name__ == "__main__":
    import os
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), debug=False)
