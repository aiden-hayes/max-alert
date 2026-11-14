"""The "Anything good?" button: a short, blunt student-style verdict on the meal that
matters right now (the one being served, or the next one if The MAX is between meals)."""
from __future__ import annotations

import hashlib
import json

import requests

import config

_cache: dict[str, str] = {}  # menu hash -> summary, so repeat taps are free

PROMPT = """You're a Georgia College student texting your friends a quick verdict on \
The MAX (the dining hall). Keep it real.

Right now: {situation}
{others}

How to answer:
- Talk about the meal that matters right now ({meal}). Only bring up a later meal \
if it's clearly the better move ("dinner's where it's at").
- Give a straight verdict. If it's good, hype the one or two best dishes by name. If \
it's weak (say it's just fish and pork), say so plainly, like "L nibbles today, fade \
that", and if another campus spot is open right now, point people there.
- House opinions (always follow these): chicken and beef dishes are good, so a meal \
with solid chicken or beef is a W and those are the dishes to hype. Mediterranean \
chicken is the exception: if it's on the menu, call it out as mad dry.
- Sound like a college student texting: casual and a little funny, with slang like W, \
L, mid, lock in, fade, bussin, cooked, the move. Don't cram slang into every sentence. \
Stay friendly: no swearing, no emojis, no hashtags, no lists or headings.
- 2 to 3 short sentences, under 50 words total.
- Only name dishes that are on the menu below. A quick vegetarian shout-out is fine \
if there's a solid one.

Menu ({when}):
{menu}"""


def menu_as_text(meals_by_location: dict[str, list[dict]], names: dict[str, str]) -> str:
    lines = []
    for slug, meals in meals_by_location.items():
        if len(meals_by_location) > 1:
            lines.append(f"## {names.get(slug, slug)}")
        for meal in meals:
            lines.append(f"{meal['name']}:")
            for st in meal["stations"]:
                dishes = ", ".join(
                    i["name"] + (f" ({'/'.join(i['tags'])})" if i["tags"] else "") for i in st["items"]
                )
                lines.append(f"  {st['name']}: {dishes}")
    return "\n".join(lines)


class SummaryError(RuntimeError):
    pass


def summarize(prompt: str, cache_key: list) -> str:
    """Ask Claude. `cache_key` identifies the menu + meal, so repeat taps during the same
    meal are free (the clock time in the prompt is left out of it on purpose)."""
    if not config.ANTHROPIC_API_KEY:
        raise SummaryError("The AI summary isn't set up yet (ANTHROPIC_API_KEY is missing).")
    key = hashlib.sha256(json.dumps([*cache_key, config.ANTHROPIC_MODEL]).encode()).hexdigest()
    if key in _cache:
        return _cache[key]
    try:
        resp = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": config.ANTHROPIC_API_KEY,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": config.ANTHROPIC_MODEL,
                "max_tokens": 200,
                "messages": [{"role": "user", "content": prompt}],
            },
            timeout=30,
        )
    except requests.RequestException as e:
        raise SummaryError("Couldn't reach the AI right now. Try again in a bit.") from e
    if resp.status_code != 200:
        raise SummaryError(f"The AI request failed ({resp.status_code}). Check your API key and model name.")
    text = "".join(b.get("text", "") for b in resp.json().get("content", []) if b.get("type") == "text").strip()
    if not text:
        raise SummaryError("The AI came back empty. Try again.")
    _cache[key] = text
    return text
