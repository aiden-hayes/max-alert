"""Download menus from Sodexo's menu API and turn them into a simple, consistent shape.

Raw API response (one location, one day) is a list of meals:

    [{"name": "Lunch",
      "groups": [                              # groups = stations
        {"name": "Grill",
         "items": [{"formalName": "Cheeseburger", "description": "...",
                    "isVegan": false, "isVegetarian": false,
                    "isPlantBased": false, "isMindful": false,
                    "calories": "540", "protein": "28g", "allergens": [...], ...}]}]}]

We normalize that into:

    [{"name": "Lunch", "stations": [{"name": "Grill", "items": [
        {"name": "Cheeseburger", "description": "...", "calories": 540,
         "protein_g": 28, "carbs_g": 40, "fat_g": 22.5, "portion": "1 EA",
         "tags": ["vegetarian", ...], "allergens": ["Milk", "Wheat"]}]}]}]

Nutrition fields are None when Sodexo leaves them blank.
"""
from __future__ import annotations

import datetime as dt
import html
import re

import requests

import config

HEADERS = {"User-Agent": "MaxAlert student menu app (personal, non-commercial)"}

# Sodexo lists non-food placeholder rows (e.g. "Have a Nice Day") with this text.
NON_FOOD_MARKERS = {"plate cost peripherals"}

# Boolean flags on each item -> the tag we show. Extra spellings are tolerated
# in case Sodexo renames things.
FLAG_TAGS = {
    "isVegan": "vegan",
    "isVegetarian": "vegetarian",
    "isPlantBased": "plant-based",
    "isMindful": "mindful",
    "isGlutenFree": "gluten-free",
    "glutenFree": "gluten-free",
    "isAvoidingGluten": "gluten-free",
}


class MissingApiKey(RuntimeError):
    pass


def fetch_raw(location: dict, day: dt.date) -> list[dict]:
    """Raw JSON for one location on one day. Raises on network/HTTP/shape errors."""
    if not config.SODEXO_API_KEY:
        raise MissingApiKey("SODEXO_API_KEY is not set")
    url = config.SODEXO_API_URL.format(location_id=location["location_id"], menu_id=location["menu_id"])
    resp = requests.get(
        url,
        params={"date": day.isoformat()},
        headers={**HEADERS, "Api-Key": config.SODEXO_API_KEY},
        timeout=config.REQUEST_TIMEOUT,
    )
    resp.raise_for_status()
    data = resp.json()
    if not isinstance(data, list):
        raise ValueError(f"expected a list of meals, got {type(data).__name__}")
    return data


def clean(text) -> str:
    return " ".join(html.unescape(str(text or "")).split())


def number(value, decimals: int = 0):
    """"35g" -> 35, "4.5g" -> 4.5 (decimals=1), "264" -> 264, "" / None / "N/A" -> None.

    Missing stays None (never 0) so the UI can say "Nutrition info not available".
    """
    m = re.match(r"\s*(\d+(?:\.\d+)?)", str(value if value is not None else ""))
    if not m:
        return None
    n = round(float(m.group(1)), decimals)
    return int(n) if decimals == 0 or n == int(n) else n


def first(item: dict, *keys):
    """Value of the first key present (Sodexo has used slightly different names)."""
    for k in keys:
        if item.get(k) not in (None, ""):
            return item[k]
    return None


def meal_name(raw: str) -> str:
    name = clean(raw)
    # "LATE NIGHT" / "late night" -> "Late Night"
    return name.title() if name.isupper() or name.islower() else name


def _allergens(item: dict) -> list[str]:
    out = []
    for a in item.get("allergens") or []:
        name = a.get("name") if isinstance(a, dict) else a
        # Some feeds include allergens with contains=false; skip those.
        if isinstance(a, dict) and a.get("contains") is False:
            continue
        if name:
            out.append(clean(name).title())
    return sorted(set(out))


def normalize(raw_meals: list[dict]) -> list[dict]:
    meals = []
    for meal in raw_meals:
        stations = []
        for group in meal.get("groups") or []:
            items = []
            for item in group.get("items") or []:
                name = clean(item.get("formalName") or item.get("name"))
                if not name or clean(item.get("ingredients")).lower() in NON_FOOD_MARKERS:
                    continue
                tags = []
                for key, tag in FLAG_TAGS.items():
                    if item.get(key) and tag not in tags:
                        tags.append(tag)
                items.append({
                    "name": name,
                    "description": clean(item.get("description")),
                    # Nutrition is per serving, straight from the menu response
                    # (no separate nutrition endpoint needed; see README).
                    "calories": number(first(item, "calories", "calorie")),
                    "protein_g": number(first(item, "protein", "proteins"), 1),
                    "carbs_g": number(first(item, "carbohydrates", "carbohydrate", "carbs", "totalCarbohydrate"), 1),
                    "fat_g": number(first(item, "fat", "totalFat"), 1),
                    "portion": clean(first(item, "portion", "servingSize", "serving")),
                    "tags": tags,
                    "allergens": _allergens(item),
                })
            if items:
                stations.append({"name": clean(group.get("name")) or "Other", "items": items})
        if stations:
            meals.append({"name": meal_name(meal.get("name")), "stations": stations})
    return meals
