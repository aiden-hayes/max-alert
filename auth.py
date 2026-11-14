"""Accounts: sign up, log in, log out (Flask-Login sessions).

JSON API used by static/account.js:
    GET  /api/auth/me        -> {"user": {...} | null}
    POST /api/auth/signup    {"email", "password"}
    POST /api/auth/login     {"email", "password"}
    POST /api/auth/logout
    POST /api/auth/forgot        {"email"}  -> emails a reset link (same answer either way)
    POST /api/auth/reset/check   {"token"}  -> {"email"} if the link still works
    POST /api/auth/reset         {"token", "password"} -> sets it, logs in

Reset links look like https://site/#reset=TOKEN. The part after # never reaches the
server or its logs. The token is signed with SECRET_KEY, lasts RESET_MAX_AGE_S, and
contains a stamp of the current password, so it stops working once it's been used.
"""
from __future__ import annotations

import hmac
import html
import logging
import re
import threading
import time
from collections import defaultdict, deque

from flask import Blueprint, current_app, jsonify, request
from flask_login import LoginManager, current_user, login_user, logout_user
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from sqlalchemy.exc import IntegrityError

import config
import mailer
from models import User, db

log = logging.getLogger(__name__)

bp = Blueprint("auth", __name__, url_prefix="/api/auth")
login_manager = LoginManager()

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
MIN_PASSWORD = 8
# Simple brute-force brake: after this many failed logins from one IP in the window, wait.
MAX_FAILS, FAIL_WINDOW_S = 10, 15 * 60
_fails: dict[str, deque] = defaultdict(deque)
# Password reset emails: per IP and per email address, within an hour.
RESET_MAX_AGE_S = 60 * 60
RESETS_PER_IP, RESETS_PER_EMAIL, RESET_WINDOW_S = 5, 3, 60 * 60
_reset_ips: dict[str, deque] = defaultdict(deque)
_reset_emails: dict[str, deque] = defaultdict(deque)


@login_manager.user_loader
def load_user(user_id: str):
    # The cookie holds "id:password stamp", so changing the password logs out other devices.
    uid, _, stamp = user_id.partition(":")
    user = db.session.get(User, int(uid)) if uid.isdigit() else None
    return user if user and hmac.compare_digest(stamp, user.password_stamp()) else None


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


def _over_limit(q: deque, limit: int, window_s: float) -> bool:
    now = time.monotonic()
    while q and now - q[0] > window_s:
        q.popleft()
    return len(q) >= limit


def _too_many_fails(ip: str) -> bool:
    return _over_limit(_fails[ip], MAX_FAILS, FAIL_WINDOW_S)


def _password_problem(password: str) -> str | None:
    if len(password) < MIN_PASSWORD:
        return f"Password must be at least {MIN_PASSWORD} characters."
    if len(password) > 128:
        return "Password is too long (max 128 characters)."
    return None


@bp.get("/me")
def me():
    return jsonify({"user": current_user.to_dict() if current_user.is_authenticated else None})


@bp.post("/signup")
def signup():
    email, password = _creds()
    if not EMAIL_RE.match(email) or len(email) > 254:
        return jsonify({"error": "Enter a valid email address."}), 400
    if problem := _password_problem(password):
        return jsonify({"error": problem}), 400
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


# ---------- password reset ----------
RESET_SENT = ("If there's an account with that email, we sent it a link to reset the password. "
              "Check your inbox (and spam). The link works for 1 hour.")
LINK_DEAD = "This reset link has expired or was already used. Ask for a new one."


def _serializer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(current_app.secret_key, salt="password-reset")


def make_reset_token(user: User) -> str:
    return _serializer().dumps({"u": user.id, "p": user.password_stamp()})


def user_from_reset_token(token: str) -> User | None:
    try:
        data = _serializer().loads(str(token or ""), max_age=RESET_MAX_AGE_S)
    except (SignatureExpired, BadSignature):
        return None
    if not isinstance(data, dict) or not isinstance(data.get("u"), int):
        return None
    user = db.session.get(User, data["u"])
    if not user or not hmac.compare_digest(str(data.get("p", "")), user.password_stamp()):
        return None  # password already changed with this link (or another way)
    return user


def reset_email(link: str) -> tuple[str, str, str]:
    subject = "Reset your Max Alert password"
    text = ("Someone (hopefully you) asked to reset the password for your Max Alert account.\n\n"
            f"Choose a new password here. The link works for 1 hour:\n{link}\n\n"
            "If you didn't ask for this, ignore this email. Your password won't change.")
    a = html.escape(link, quote=True)
    body = f"""<div style="font-family:-apple-system,Segoe UI,Roboto,sans-serif;max-width:480px;margin:auto;color:#13213a">
  <h2 style="color:#0b2545">Reset your Max Alert password</h2>
  <p>Someone (hopefully you) asked to reset the password for your Max Alert account.</p>
  <p><a href="{a}" style="display:inline-block;background:#0b2545;color:#fff;padding:12px 18px;border-radius:10px;text-decoration:none;font-weight:700">Choose a new password</a></p>
  <p style="color:#5b6577;font-size:14px">The link works for 1 hour. If you didn't ask for this, ignore this email. Your password won't change.</p>
  <p style="color:#5b6577;font-size:12px;word-break:break-all">{a}</p>
</div>"""
    return subject, text, body


def send_later(fn, *args) -> None:
    """Send in the background so the answer takes the same time whether or not the
    account exists (otherwise the delay would reveal who has one)."""
    threading.Thread(target=fn, args=args, daemon=True).start()


@bp.post("/forgot")
def forgot():
    if mailer.mode() is None:
        return jsonify({"error": "Password reset emails aren't set up on this site yet."}), 503
    body = request.get_json(silent=True) or {}
    email = str(body.get("email") or "").strip().lower()
    if not EMAIL_RE.match(email) or len(email) > 254:
        return jsonify({"error": "Enter a valid email address."}), 400
    ip = request.remote_addr or "?"
    if _over_limit(_reset_ips[ip], RESETS_PER_IP, RESET_WINDOW_S):
        return jsonify({"error": "Too many tries. Wait a while and try again."}), 429
    _reset_ips[ip].append(time.monotonic())
    user = db.session.scalar(db.select(User).filter_by(email=email))
    if user and not _over_limit(_reset_emails[email], RESETS_PER_EMAIL, RESET_WINDOW_S):
        _reset_emails[email].append(time.monotonic())
        base = config.PUBLIC_URL or request.host_url.rstrip("/")
        link = f"{base}/#reset={make_reset_token(user)}"
        send_later(mailer.send, email, *reset_email(link))
    return jsonify({"ok": True, "message": RESET_SENT})


@bp.post("/reset/check")
def reset_check():
    user = user_from_reset_token((request.get_json(silent=True) or {}).get("token"))
    if not user:
        return jsonify({"error": LINK_DEAD}), 400
    return jsonify({"email": user.email})


@bp.post("/reset")
def reset():
    body = request.get_json(silent=True) or {}
    user = user_from_reset_token(body.get("token"))
    if not user:
        return jsonify({"error": LINK_DEAD}), 400
    password = str(body.get("password") or "")
    if problem := _password_problem(password):
        return jsonify({"error": problem}), 400
    user.set_password(password)
    db.session.commit()
    login_user(user, remember=True)
    return jsonify({"user": user.to_dict()})
