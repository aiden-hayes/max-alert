"""Settings for Max Alert. Most things can be overridden with environment variables (see .env.example)."""
import datetime as dt
import hashlib
import json
import os
import secrets
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv, set_key

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(exist_ok=True)


# Settings and secrets live in .env (see .env.example). Real environment
# variables win over .env, which is what hosting services use.
ENV_FILE = BASE_DIR / ".env"
load_dotenv(ENV_FILE)


def _secret_key() -> str:
    """SECRET_KEY signs login cookies. If it's missing, make a strong one and save it to .env
    so logins survive restarts. Never commit .env."""
    key = os.environ.get("SECRET_KEY", "").strip()
    if key:
        return key
    key = secrets.token_hex(32)
    ENV_FILE.touch(exist_ok=True)
    set_key(str(ENV_FILE), "SECRET_KEY", key, quote_mode="never")
    os.environ["SECRET_KEY"] = key
    print("Max Alert: generated a SECRET_KEY and saved it to .env")
    return key


SECRET_KEY = _secret_key()
# SQLite file in data/ by default. Any SQLAlchemy URL works (e.g. Postgres when hosting).
DATABASE_URL = os.environ.get("DATABASE_URL", f"sqlite:///{DATA_DIR / 'maxalert.db'}")
# Set to 1 when the site is served over HTTPS so login cookies are never sent unencrypted.
SECURE_COOKIES = os.environ.get("SECURE_COOKIES", "").lower() in {"1", "true", "yes"}

CAMPUS_TZ = ZoneInfo("America/New_York")  # Milledgeville, GA

# --- Sodexo menu API -------------------------------------------------------
# How this was found is explained in README.md ("How the data source works").
SODEXO_API_URL = os.environ.get(
    "SODEXO_API_URL", "https://api-prd.sodexomyway.net/v0.2/data/menu/{location_id}/{menu_id}"
)
SODEXO_API_KEY = os.environ.get("SODEXO_API_KEY", "").strip()
DAYS_AHEAD = 7                      # today + next 6 days
REFRESH_HOURS = float(os.environ.get("REFRESH_HOURS", "3"))
REQUEST_TIMEOUT = 20
PARALLEL_DOWNLOADS = 4              # be gentle with Sodexo's servers
ATTEMPTS_PER_DOWNLOAD = 2
# When every download fails, look the key/menu IDs up again at most this often.
REDISCOVER_EVERY_HOURS = 6

# --- Alerts when menus stop updating ----------------------------------------
# Optional. A webhook that gets a short message when menus have failed to download
# for ALERT_AFTER_HOURS, a daily reminder while it lasts, and a note when it's fixed.
# Works with ntfy.sh (https://ntfy.sh/<your-topic>), Discord and Slack webhooks.
ALERT_WEBHOOK_URL = os.environ.get("ALERT_WEBHOOK_URL", "").strip()
ALERT_AFTER_HOURS = float(os.environ.get("ALERT_AFTER_HOURS", "12"))
ALERT_REPEAT_HOURS = 24

# Force demo data even if a key is set (handy for working on the UI offline).
DEMO_MODE = os.environ.get("DEMO_MODE", "").lower() in {"1", "true", "yes"}

# --- Anthropic (the "Anything good?" button) --------------------------------
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "").strip()
ANTHROPIC_MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-haiku-4-5")

# --- Dining locations ------------------------------------------------------
# Each location needs Sodexo's location ID and menu ID. `python discover.py`
# tries to find them automatically and writes locations.json.
LOCATIONS_FILE = BASE_DIR / "locations.json"


def load_locations() -> list[dict]:
    if not LOCATIONS_FILE.exists():
        return []
    locations = json.loads(LOCATIONS_FILE.read_text())
    fixes = sodexo_override().get("locations", {})
    locations = [{**loc, **fixes.get(loc.get("slug"), {})} for loc in locations]
    return [loc for loc in locations if loc.get("location_id") and loc.get("menu_id")]


# --- Self-repair --------------------------------------------------------------
# If Sodexo rotates its key or starts a new menu, the app looks the new values up
# (discover.rediscover) and saves them here. They're only used while .env and
# locations.json still hold the values they replaced: once you update those by
# hand (or redeploy with new ones), this file is ignored.
OVERRIDE_FILE = DATA_DIR / "sodexo_override.json"


def _fingerprint() -> str:
    loc_text = LOCATIONS_FILE.read_text() if LOCATIONS_FILE.exists() else ""
    return hashlib.sha256(json.dumps([SODEXO_API_KEY, SODEXO_API_URL, loc_text]).encode()).hexdigest()


def sodexo_override() -> dict:
    try:
        data = json.loads(OVERRIDE_FILE.read_text())
    except (OSError, ValueError):
        return {}
    return data if data.get("fingerprint") == _fingerprint() else {}


def save_sodexo_override(fix: dict) -> None:
    data = {**fix, "fingerprint": _fingerprint(), "saved_at": dt.datetime.now(CAMPUS_TZ).isoformat()}
    OVERRIDE_FILE.write_text(json.dumps(data, indent=2))


def api_key() -> str:
    return sodexo_override().get("api_key") or SODEXO_API_KEY


def api_url() -> str:
    return sodexo_override().get("api_url") or SODEXO_API_URL


# --- Station names and order -------------------------------------------------
# Sodexo's station names -> what the site shows (matched ignoring capitals).
STATION_NAMES = {
    "Bliss": "Dessert",
    "Grown": "Vegetarian",
    "Showcase": "Main Grill Area",
    "Stacked": "Sandwich Area",
    "Mix": "Salad",
}
# Use the names shown on the site (after renaming). Stations in neither list
# keep Sodexo's order, in between.
STATIONS_FIRST = ["Savory", "Main Grill Area", "Simply 3", "Slices"]
STATIONS_LAST = ["Vegetarian", "Dessert"]


# --- Meal hours --------------------------------------------------------------
# The menu API gives meal names but not times. These are The MAX's posted hours
# (Fall 2025, frontpage.gcsu.edu). Update them if the schedule changes.
# Keys are weekday numbers: 0=Mon ... 6=Sun. Times are 24h "HH:MM".
_MON_THU = {
    "Breakfast": ("07:30", "10:45"),
    "Lunch": ("11:00", "14:00"),
    "Dinner": ("16:30", "20:00"),
    "Late Night": ("20:00", "22:00"),
}
MEAL_HOURS: dict[int, dict[str, tuple[str, str]]] = {
    0: _MON_THU, 1: _MON_THU, 2: _MON_THU, 3: _MON_THU,
    4: {"Breakfast": ("07:30", "10:45"), "Lunch": ("11:00", "14:00"), "Dinner": ("16:30", "19:00")},
    5: {"Brunch": ("11:00", "14:00"), "Lunch": ("11:00", "14:00"), "Dinner": ("17:00", "19:00")},
    6: {"Brunch": ("11:00", "14:00"), "Lunch": ("11:00", "14:00"), "Dinner": ("17:00", "20:00")},
}

# Order meals appear in when hours are unknown.
MEAL_ORDER = ["Breakfast", "Brunch", "Lunch", "Dinner", "Late Night"]
