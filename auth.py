"""Accounts: sign up, log in, log out (Flask-Login sessions).

JSON API used by static/account.js:
    GET  /api/auth/me        -> {"user": {...} | null}
    POST /api/auth/signup    {"email", "password"}
    POST /api/auth/login     {"email", "password"}
    POST /api/auth/logout
"""
from __future__ import annotations

import re
import time
from collections import defaultdict, deque

from flask import Blueprint, jsonify, request
from flask_login import LoginManager, current_user, login_user, logout_user
from sqlalchemy.exc import IntegrityError

from models import User, db

bp = Blueprint("auth", __name__, url_prefix="/api/auth")
login_manager = LoginManager()

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
MIN_PASSWORD = 8
# Simple brute-force brake: after this many failed logins from one IP in the window, wait.
MAX_FAILS, FAIL_WINDOW_S = 10, 15 * 60
_fails: dict[str, deque] = defaultdict(deque)


@login_manager.user_loader
def load_user(user_id: str):
    return db.session.get(User, int(user_id)) if user_id.isdigit() else None


@login_manager.unauthorized_handler
def unauthorized():
    return jsonify({"error": "Log in to save your calorie log.", "login_required": True}), 401


def require_json():
    """Changes must be JSON requests. Browsers can't send those cross-site without
    permission, so another website can't make you log in/out or change your log.
    (DELETE has no body; other sites can't send DELETE without permission either.)"""
    if request.method in ("POST", "PUT", "PATCH") and not request.is_json:
        return jsonify({"error": "Expected JSON."}), 415
    return None


bp.before_request(require_json)


def _creds():
    body = request.get_json(silent=True) or {}
    email = str(body.get("email") or "").strip().lower()
    password = str(body.get("password") or "")
    return email, password


def _too_many_fails(ip: str) -> bool:
    q, now = _fails[ip], time.monotonic()
    while q and now - q[0] > FAIL_WINDOW_S:
        q.popleft()
    return len(q) >= MAX_FAILS


@bp.get("/me")
def me():
    return jsonify({"user": current_user.to_dict() if current_user.is_authenticated else None})


@bp.post("/signup")
def signup():
    email, password = _creds()
    if not EMAIL_RE.match(email) or len(email) > 254:
        return jsonify({"error": "Enter a valid email address."}), 400
    if len(password) < MIN_PASSWORD:
        return jsonify({"error": f"Password must be at least {MIN_PASSWORD} characters."}), 400
    if len(password) > 128:
        return jsonify({"error": "Password is too long (max 128 characters)."}), 400
    if db.session.scalar(db.select(User).filter_by(email=email)):
        return jsonify({"error": "An account with that email already exists. Try logging in."}), 409
    user = User(email=email)
    user.set_password(password)
    db.session.add(user)
    try:
        db.session.commit()
    except IntegrityError:  # two sign-ups with the same email at once
        db.session.rollback()
        return jsonify({"error": "An account with that email already exists. Try logging in."}), 409
    login_user(user, remember=True)
    return jsonify({"user": user.to_dict()}), 201


@bp.post("/login")
def login():
    ip = request.remote_addr or "?"
    if _too_many_fails(ip):
        return jsonify({"error": "Too many tries. Wait a few minutes and try again."}), 429
    email, password = _creds()
    user = db.session.scalar(db.select(User).filter_by(email=email)) if email else None
    if not user or not user.check_password(password):
        _fails[ip].append(time.monotonic())
        # Same message either way, so nobody can use this to find out who has an account.
        return jsonify({"error": "Email or password is incorrect."}), 401
    _fails.pop(ip, None)
    login_user(user, remember=True)
    return jsonify({"user": user.to_dict()})


@bp.post("/logout")
def logout():
    logout_user()
    return jsonify({"user": None})
