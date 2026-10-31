"""Calorie log API for the logged-in user (replaces the old /api/log/<log name> routes).

    GET    /api/log?date=YYYY-MM-DD      -> {"goal", "date", "meals": [...]}
    POST   /api/log/meals                {"date", "meal", "items": [...]}
    PUT    /api/log/meals/<id>           {"meal", "items": [...]}
    DELETE /api/log/meals/<id>
    PUT    /api/log/settings             {"goal": 2200 | null}
    GET    /api/log/import?legacy=<old log name>  -> {"offer": {...} | null}
    POST   /api/log/import               {"legacy": "<old log name>"}
    POST   /api/log/import/dismiss

Everything here needs a login; logged-out calls get a 401 with login_required=true.
"""
from __future__ import annotations

from flask import Blueprint, jsonify, request
from flask_login import current_user, login_required

import logbook
from auth import require_json
from menu_store import campus_now
from models import db

bp = Blueprint("tracker", __name__, url_prefix="/api/log")
bp.before_request(require_json)


def _call(fn):
    try:
        return fn()
    except logbook.LogError as e:
        db.session.rollback()
        return jsonify({"error": str(e)}), 400


def _body() -> dict:
    return request.get_json(silent=True) or {}


@bp.get("")
@login_required
def log_day():
    date = request.args.get("date") or campus_now().date().isoformat()
    return _call(lambda: jsonify(logbook.day(current_user, date)))


@bp.post("/meals")
@login_required
def log_add():
    body = _body()
    date = body.get("date") or campus_now().date().isoformat()
    now = campus_now().isoformat(timespec="seconds")
    return _call(lambda: (jsonify(logbook.add_meal(current_user, date, body, now)), 201))


@bp.put("/meals/<meal_id>")
@login_required
def log_update(meal_id):
    return _call(lambda: jsonify(logbook.update_meal(current_user, meal_id, _body())))


@bp.delete("/meals/<meal_id>")
@login_required
def log_delete(meal_id):
    return _call(lambda: (logbook.delete_meal(current_user, meal_id), ("", 204))[1])


@bp.put("/settings")
@login_required
def log_settings():
    return _call(lambda: jsonify({"goal": logbook.set_goal(current_user, _body().get("goal"))}))


# ---- one-time import of the pre-accounts log ------------------------------
@bp.get("/import")
@login_required
def import_offer():
    if current_user.import_offer_done:
        return jsonify({"offer": None})
    return jsonify({"offer": logbook.legacy_summary(request.args.get("legacy", ""))})


@bp.post("/import")
@login_required
def import_run():
    if current_user.import_offer_done:
        return jsonify({"error": "You've already imported or skipped your old log."}), 409
    name = str(_body().get("legacy") or "")
    return _call(lambda: jsonify({"imported": logbook.import_legacy(current_user, name)}))


@bp.post("/import/dismiss")
@login_required
def import_dismiss():
    current_user.import_offer_done = True
    db.session.commit()
    return jsonify({"ok": True})
