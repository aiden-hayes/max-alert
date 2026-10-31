"""Daily food log: input validation + storage in the database (models.py).

Every function takes the logged-in User. Nutrition numbers are PER SERVING;
totals are servings x value. None = unknown (never 0).

Before accounts existed, logs were JSON files in data/logs/<log name>.json,
keyed by a "log name" the browser remembered. `legacy_summary` and
`import_legacy` let a user pull that old log into their account once.
"""
from __future__ import annotations

import json
import re

import config
from models import LoggedItem, LoggedMeal, User, db

LOG_DIR = config.DATA_DIR / "logs"  # pre-accounts JSON logs (import only)
MEALS = ["Breakfast", "Brunch", "Lunch", "Dinner", "Late Night", "Snack"]
NUTRIENTS = ["calories", "protein_g", "carbs_g", "fat_g"]
USER_RE = re.compile(r"^[a-z0-9_-]{1,40}$")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
MAX_ITEMS = 60


class LogError(ValueError):
    """Bad input; message is safe to show to the user."""


def clean_user(user: str) -> str:
    user = (user or "").strip().lower()
    if not USER_RE.match(user):
        raise LogError("Log name can only use letters, numbers, - and _ (max 40).")
    return user


def _num(value, lo: float, hi: float, label: str):
    if value is None or value == "":
        return None
    try:
        n = float(value)
    except (TypeError, ValueError):
        raise LogError(f"{label} must be a number.")
    if not lo <= n <= hi:
        raise LogError(f"{label} must be between {lo:g} and {hi:g}.")
    return int(n) if n == int(n) else round(n, 1)


def _clean_item(raw: dict) -> dict:
    if not isinstance(raw, dict):
        raise LogError("Bad item.")
    name = " ".join(str(raw.get("name") or "").split())[:120]
    if not name:
        raise LogError("Every item needs a name.")
    servings = _num(raw.get("servings", 1), 0.5, 10, "Servings")
    if servings is None or (servings * 2) != int(servings * 2):
        raise LogError("Servings go in steps of 0.5.")
    item = {
        "name": name,
        "servings": servings,
        "source": "quick" if raw.get("source") == "quick" else "menu",
        "station": str(raw.get("station") or "")[:60],
        "location": str(raw.get("location") or "")[:60],
        "portion": str(raw.get("portion") or "")[:40],
    }
    for key in NUTRIENTS:
        limit = 5000 if key == "calories" else 500
        item[key] = _num(raw.get(key), 0, limit, key.replace("_g", "").title())
    return item


def _clean_meal(body: dict) -> tuple[str, list[dict]]:
    meal = body.get("meal")
    if meal not in MEALS:
        raise LogError(f"Meal must be one of: {', '.join(MEALS)}.")
    items = body.get("items")
    if not isinstance(items, list) or not items:
        raise LogError("Add at least one item.")
    if len(items) > MAX_ITEMS:
        raise LogError(f"That's more than {MAX_ITEMS} items.")
    return meal, [_clean_item(i) for i in items]


def _check_date(date: str) -> str:
    if not DATE_RE.match(date or ""):
        raise LogError("Bad date.")
    return date


def _items(meal: LoggedMeal, items: list[dict]) -> None:
    meal.items = [LoggedItem(position=i, **item) for i, item in enumerate(items)]


def _own_meal(user: User, meal_id) -> LoggedMeal:
    meal = db.session.get(LoggedMeal, int(meal_id)) if str(meal_id).isdigit() else None
    if meal is None or meal.user_id != user.id:  # other people's meals look the same as missing
        raise LogError("That logged meal doesn't exist anymore.")
    return meal


def add_meal(user: User, date: str, body: dict, logged_at: str) -> dict:
    meal_name, items = _clean_meal(body)
    meal = LoggedMeal(user=user, date=_check_date(date), meal=meal_name, logged_at=logged_at)
    _items(meal, items)
    db.session.add(meal)
    db.session.commit()
    return meal.to_dict()


def update_meal(user: User, meal_id, body: dict) -> dict:
    meal_name, items = _clean_meal(body)
    meal = _own_meal(user, meal_id)
    meal.meal = meal_name
    _items(meal, items)
    db.session.commit()
    return meal.to_dict()


def delete_meal(user: User, meal_id) -> None:
    db.session.delete(_own_meal(user, meal_id))
    db.session.commit()


def set_goal(user: User, goal) -> int | None:
    goal = _num(goal, 500, 10000, "Daily goal")
    user.calorie_goal = int(goal) if goal is not None else None
    db.session.commit()
    return user.calorie_goal


def day(user: User, date: str) -> dict:
    meals = db.session.scalars(db.select(LoggedMeal).filter_by(user_id=user.id, date=_check_date(date))
                               .order_by(LoggedMeal.id)).all()
    return {"goal": user.calorie_goal, "date": date, "meals": [m.to_dict() for m in meals]}


# ---- one-time import of a pre-accounts log -----------------------------------
def _legacy_path(name: str):
    return LOG_DIR / f"{clean_user(name)}.json"


def _legacy_load(name: str) -> dict | None:
    try:
        data = json.loads(_legacy_path(name).read_text())
    except (FileNotFoundError, LogError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def legacy_summary(name: str) -> dict | None:
    """What an old log holds, or None if there's nothing to import."""
    data = _legacy_load(name)
    if not data:
        return None
    days = data.get("days") or {}
    count = sum(len(v) for v in days.values())
    if not count and not data.get("goal"):
        return None
    return {"meals": count, "days": len(days), "goal": data.get("goal")}


def import_legacy(user: User, name: str) -> int:
    """Copy an old JSON log into `user`'s account. Returns how many meals were imported.

    The file is renamed afterwards so the same log can't be imported twice
    (or claimed by a different account)."""
    data = _legacy_load(name)
    if not data:
        raise LogError("There's no old log to import.")
    imported = 0
    for date, meals in (data.get("days") or {}).items():
        for m in meals:
            try:
                meal_name, items = _clean_meal(m)
                meal = LoggedMeal(user=user, date=_check_date(date), meal=meal_name,
                                  logged_at=str(m.get("logged_at") or f"{date}T12:00:00"))
            except LogError:
                continue  # skip anything malformed rather than failing the whole import
            _items(meal, items)
            db.session.add(meal)
            imported += 1
    if data.get("goal") and not user.calorie_goal:
        try:
            user.calorie_goal = int(_num(data["goal"], 500, 10000, "Daily goal"))
        except (LogError, TypeError):
            pass
    user.import_offer_done = True
    db.session.commit()
    path = _legacy_path(name)
    path.rename(path.with_name(f"{path.stem}.imported-by-{user.id}.json"))
    return imported
