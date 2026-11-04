"""Thumbs up/down on dishes.

    GET  /api/ratings                   -> {"ratings": {"<dish name>": {"up", "down", "mine"}}}
                                           for every dish on this week's menu
    POST /api/ratings {"dish", "vote"}  vote = 1 (up), -1 (down) or 0 (take back)

Anyone can vote. Logged in, the vote belongs to the account; logged out, to a random
id in a long-lived cookie (one vote per browser). Only dishes on this week's menu can
be voted on, so nobody can fill the table with made-up names.
"""
from __future__ import annotations

import datetime as dt
import secrets

from flask import Blueprint, jsonify, request
from flask_login import current_user
from sqlalchemy import case, func, select

import config
from auth import require_json
from models import DishVote, db

bp = Blueprint("ratings", __name__, url_prefix="/api/ratings")
bp.before_request(require_json)
VOTER_COOKIE = "mx_voter"
_week_dishes = lambda: set()  # noqa: E731  (set by app.py: names on this week's menu)


def init(week_dishes) -> None:
    global _week_dishes
    _week_dishes = week_dishes


def dish_key(name: str) -> str:
    return " ".join(str(name or "").split()).casefold()[:120]


def _voter(create: bool = False) -> tuple[str | None, str | None]:
    """(voter id, new cookie value to set or None)."""
    if current_user.is_authenticated:
        return f"u{current_user.id}", None
    token = request.cookies.get(VOTER_COOKIE, "")
    if 16 <= len(token) <= 39 and token.isalnum():
        return f"a{token}", None
    if not create:
        return None, None
    token = secrets.token_hex(16)
    return f"a{token}", token


def _counts(keys: list[str], voter: str | None) -> dict[str, dict]:
    out = {k: {"up": 0, "down": 0, "mine": 0} for k in keys}
    if not keys:
        return out
    rows = db.session.execute(
        select(DishVote.dish,
               func.sum(case((DishVote.value > 0, 1), else_=0)),
               func.sum(case((DishVote.value < 0, 1), else_=0)))
        .where(DishVote.dish.in_(keys)).group_by(DishVote.dish))
    for dish, up, down in rows:
        out[dish].update(up=int(up or 0), down=int(down or 0))
    if voter:
        for dish, value in db.session.execute(
                select(DishVote.dish, DishVote.value).where(DishVote.voter == voter, DishVote.dish.in_(keys))):
            out[dish]["mine"] = value
    return out


@bp.get("")
def get_ratings():
    names = sorted(_week_dishes())
    keys = {n: dish_key(n) for n in names}
    counts = _counts(sorted(set(keys.values())), _voter()[0])
    return jsonify({"ratings": {n: counts[k] for n, k in keys.items() if counts[k]["up"] or counts[k]["down"] or counts[k]["mine"]}})


@bp.post("")
def vote():
    body = request.get_json(silent=True) or {}
    name, value = body.get("dish"), body.get("vote")
    if value not in (1, -1, 0) or isinstance(value, bool):
        return jsonify({"error": "vote must be 1, -1 or 0"}), 400
    key = dish_key(name)
    if not key or key not in {dish_key(n) for n in _week_dishes()}:
        return jsonify({"error": "That dish isn't on this week's menu."}), 404
    voter, new_cookie = _voter(create=True)
    row = db.session.execute(select(DishVote).where(DishVote.dish == key, DishVote.voter == voter)).scalar_one_or_none()
    if value == 0:
        if row:
            db.session.delete(row)
    elif row:
        row.value, row.updated_at = value, dt.datetime.now(dt.timezone.utc)
    else:
        db.session.add(DishVote(dish=key, voter=voter, value=value))
    db.session.commit()
    resp = jsonify(_counts([key], voter)[key])
    if new_cookie:
        resp.set_cookie(VOTER_COOKIE, new_cookie, max_age=400 * 24 * 3600, httponly=True,
                        samesite="Lax", secure=config.SECURE_COOKIES)
    return resp
