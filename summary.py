"""The "Anything good?" button: ask Claude for a short, casual take on today's menu."""
from __future__ import annotations

import hashlib
import json

import requests

import config

_cache: dict[str, str] = {}  # menu hash -> summary, so repeat taps are free

PROMPT = """You're a college student texting a friend about what the dining hall at \
Georgia College (The MAX) has today. Based on the menu below, write 2-3 short, casual \
sentences on the highlights: what's worth grabbing and at which meal. Mention a good \
vegetarian/vegan pick if there is one. No lists, no emojis, no headings. Only mention \
dishes that are actually on the menu.

Today's menu ({date}):
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


def summarize(date: str, menu_text: str) -> str:
    if not config.ANTHROPIC_API_KEY:
        raise SummaryError("The AI summary isn't set up yet (ANTHROPIC_API_KEY is missing).")
    key = hashlib.sha256(json.dumps([date, menu_text, config.ANTHROPIC_MODEL]).encode()).hexdigest()
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
                "max_tokens": 250,
                "messages": [{"role": "user", "content": PROMPT.format(date=date, menu=menu_text)}],
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
