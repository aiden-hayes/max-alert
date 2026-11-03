"""Find the Sodexo location ID, menu ID and API key for GCSU dining locations.

Usage:
    python discover.py                 # tries the browser method, then the plain-HTTP method
    python discover.py --no-browser    # plain HTTP only
    python discover.py URL [URL ...]   # check specific location pages

What it does:
  1. Browser method (most reliable): opens each location page in a headless
     Chromium (Playwright), watches the network, and records every request to
     api-prd.sodexomyway.net: the URL has /menu/<locationId>/<menuId>, and
     the request header "Api-Key" holds the key.
  2. Plain-HTTP method: downloads the page and its JS files. The site's main
     JS bundle has a per-environment settings table (Local, Development, ...,
     Production); the Production entry holds the API URL and key the live site
     uses. Location and menu IDs come from the page data.
Results are written to locations.json and SODEXO_API_KEY is added to .env.

The running app also calls rediscover() (plain HTTP only, no browser) when
every menu download fails, so it can repair itself if Sodexo rotates the key
or switches to a new menu ID. See menu_store.py.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import sys
from pathlib import Path
from urllib.parse import urljoin

import requests

import config

BASE = "https://georgiacollege.sodexomyway.com"
DEFAULT_PAGES = [f"{BASE}/en-us/locations/the-max"]
API_RE = re.compile(r"api-prd\.sodexomyway\.net/v[\d.]+/data/menu/(\d{5,10})/(\d{3,10})")
# One entry of the bundle's environment table, e.g.
#   Production:{...,API_URL:"https://api-prd.sodexomyway.net/v0.2/",API_KEY:"6871...",...}
ENV_BLOCK_RE = re.compile(
    r"""(\w+)\s*:\s*\{[^{}]*?API_URL\s*:\s*["'](https://api-[a-z]+\.sodexomyway\.net/v[\d.]+/)["']"""
    r"""[^{}]*?API_KEY\s*:\s*["']([A-Za-z0-9_\-]{20,})["']""")
# Fallback guesses. These also match unrelated keys (e.g. Firebase's apiKey), so
# a key found this way is only used after verify() confirms it.
KEY_RES = [
    re.compile(r"""["']?api-?key["']?\s*[:=]\s*["']([A-Za-z0-9_\-]{20,})["']""", re.I),
    re.compile(r"""["']Api-Key["']\s*,\s*["']([A-Za-z0-9_\-]{20,})["']""", re.I),
]
MENU_PATH = "data/menu/{location_id}/{menu_id}"
HERE = Path(__file__).resolve().parent
UA = {"User-Agent": "Mozilla/5.0 (MaxAlert discovery script)"}


def slug_from(url: str) -> str:
    return url.rstrip("/").rsplit("/", 1)[-1]


def name_from(slug: str) -> str:
    return "The MAX" if slug == "the-max" else slug.replace("-", " ").title()


def find_location_pages() -> list[str]:
    """Look for other /locations/<slug> links on the site's location pages."""
    pages = set(DEFAULT_PAGES)
    for url in (f"{BASE}/en-us/locations/hours", f"{BASE}/en-us/categories/dining-hall", DEFAULT_PAGES[0]):
        try:
            html = requests.get(url, headers=UA, timeout=20).text
        except requests.RequestException:
            continue
        for path in re.findall(r'href="(/en-us/locations/[a-z0-9\-]+)"', html):
            if slug_from(path) not in {"hours", "map", "locations"}:
                pages.add(urljoin(BASE, path))
    return sorted(pages)


def via_browser(pages: list[str]) -> tuple[dict, str | None]:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("  (Playwright not installed; skipping browser method. "
              "`pip install playwright && playwright install chromium` to enable it.)")
        return {}, None
    found, key = {}, None
    with sync_playwright() as p:
        browser = p.chromium.launch()
        for page_url in pages:
            page = browser.new_page()
            hits = []

            def on_request(req):
                m = API_RE.search(req.url)
                if m:
                    hits.append((m.group(1), m.group(2), req.headers.get("api-key")))
            page.on("request", on_request)
            try:
                page.goto(page_url, wait_until="networkidle", timeout=45000)
                page.wait_for_timeout(3000)
            except Exception as e:
                print(f"  {page_url}: {e}")
            if hits:
                loc_id, menu_id, k = hits[0]
                found[slug_from(page_url)] = (loc_id, menu_id)
                key = key or k
                print(f"  {slug_from(page_url)}: location {loc_id}, menu {menu_id}")
            else:
                print(f"  {slug_from(page_url)}: no menu API call seen (may not have a daily menu)")
            page.close()
        browser.close()
    return found, key


def via_http(pages: list[str], verbose: bool = True) -> tuple[dict, list[str], str | None]:
    """Returns ({slug: (location_id, menu_id)}, candidate API keys best-first, API URL template)."""
    say = print if verbose else (lambda *a: None)
    found, prod_keys, other_keys, api_url = {}, [], [], None
    for page_url in pages:
        try:
            html = requests.get(page_url, headers=UA, timeout=20).text
        except requests.RequestException as e:
            say(f"  {page_url}: {e}")
            continue
        texts = [html]
        for src in re.findall(r'<script[^>]+src="([^"]+)"', html)[:40]:
            try:
                texts.append(requests.get(urljoin(page_url, src), headers=UA, timeout=20).text)
            except requests.RequestException:
                pass
        blob = "\n".join(texts)
        m = API_RE.search(blob)
        if m:
            found[slug_from(page_url)] = (m.group(1), m.group(2))
            say(f"  {slug_from(page_url)}: location {m.group(1)}, menu {m.group(2)}")
        else:
            loc = re.search(r'locationId["\']?\s*[:=]\s*["\']?(\d{6,10})', blob, re.I)
            menu = re.search(r'menuId["\']?\s*[:=]\s*["\']?(\d{3,10})', blob, re.I)
            if loc and menu:
                found[slug_from(page_url)] = (loc.group(1), menu.group(1))
                say(f"  {slug_from(page_url)}: location {loc.group(1)}, menu {menu.group(1)} (from page data)")
        for env, url, key in ENV_BLOCK_RE.findall(blob):
            if "api-prd." in url or env.lower().startswith("prod"):
                api_url = api_url or url + MENU_PATH
                prod_keys.append(key)
        for rx in KEY_RES:
            other_keys += rx.findall(blob)
    keys = list(dict.fromkeys(prod_keys + other_keys))  # dedupe, keep order
    return found, keys, api_url


def verify(loc_id: str, menu_id: str, key: str, api_url: str | None = None) -> bool:
    """True if this key + IDs return a menu. (Sodexo answers 401 for a bad key *or* a bad menu ID.)"""
    url = (api_url or config.SODEXO_API_URL).format(location_id=loc_id, menu_id=menu_id)
    try:
        r = requests.get(url, params={"date": dt.date.today().isoformat()},
                         headers={"Api-Key": key}, timeout=20)
        return r.status_code == 200 and isinstance(r.json(), list)
    except (requests.RequestException, ValueError):
        return False


def rediscover(locations: list[dict], current_key: str, current_url: str) -> dict | None:
    """Look up fresh values for the configured locations (plain HTTP, no browser).

    Returns {"api_key", "api_url", "locations": {slug: {"location_id", "menu_id"}}}
    only when the result is verified against the real API AND differs from what we
    have now; otherwise None. Never adds or removes locations.
    """
    pages = [f"{BASE}/en-us/locations/{loc['slug']}" for loc in locations]
    found, keys, api_url = via_http(pages, verbose=False)
    urls = list(dict.fromkeys(u for u in (api_url, current_url) if u))
    keys = list(dict.fromkeys(keys + [current_key]))
    for url in urls:
        for key in keys:
            fixed = {}
            for loc in locations:
                options = [found.get(loc["slug"]), (loc["location_id"], loc["menu_id"])]
                for ids in dict.fromkeys(o for o in options if o):
                    if verify(ids[0], ids[1], key, url):
                        fixed[loc["slug"]] = {"location_id": ids[0], "menu_id": ids[1]}
                        break
            if not fixed:
                continue
            unchanged = key == current_key and url == current_url and all(
                fixed.get(l["slug"], {}).get("menu_id") in (None, l["menu_id"]) and
                fixed.get(l["slug"], {}).get("location_id") in (None, l["location_id"]) for l in locations)
            return None if unchanged else {"api_key": key, "api_url": url, "locations": fixed}
    return None


def save(found: dict, key: str | None) -> None:
    locations = [{"slug": s, "name": name_from(s), "location_id": l, "menu_id": m}
                 for s, (l, m) in sorted(found.items())]
    (HERE / "locations.json").write_text(json.dumps(locations, indent=2) + "\n")
    print(f"\nWrote {len(locations)} location(s) to locations.json")
    if key:
        env = HERE / ".env"
        lines = [l for l in (env.read_text().splitlines() if env.exists() else [])
                 if not l.startswith("SODEXO_API_KEY=")]
        lines.append(f"SODEXO_API_KEY={key}")
        env.write_text("\n".join(lines) + "\n")
        print("Saved SODEXO_API_KEY to .env")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("pages", nargs="*")
    ap.add_argument("--no-browser", action="store_true")
    args = ap.parse_args()

    pages = args.pages or find_location_pages()
    print("Location pages to check:\n  " + "\n  ".join(pages))

    found, key, api_url = {}, None, None
    if not args.no_browser:
        print("\nBrowser method:")
        found, key = via_browser(pages)
    if not found or not key:
        print("\nPlain-HTTP method:")
        f2, candidates, api_url = via_http(pages)
        for s, v in f2.items():
            found.setdefault(s, v)
        if not key and found:
            loc_id, menu_id = next(iter(found.values()))
            key = next((k for k in candidates if verify(loc_id, menu_id, k, api_url)), None)

    if not found or not key:
        print("\nCouldn't find everything automatically. Do it by hand (2 minutes):\n"
              "  1. Open the location page in Chrome, press F12, go to the Network tab.\n"
              "  2. Reload, type 'sodexomyway.net' in the filter box.\n"
              "  3. Click the request like  .../v0.2/data/menu/<locationId>/<menuId>?date=...\n"
              "  4. Copy the two numbers from the URL into locations.json, and the\n"
              "     'Api-Key' request header into .env as SODEXO_API_KEY=...")
        if found:
            save(found, key)
        sys.exit(1)

    for slug, (l, m) in found.items():
        print(f"Checking {slug} ... {'OK' if verify(l, m, key, api_url) else 'did NOT return a menu'}")
    if api_url and api_url != config.SODEXO_API_URL:
        print(f"\nNote: the site now uses {api_url}\nSet SODEXO_API_URL to that in .env (and on your host).")
    save(found, key)


if __name__ == "__main__":
    main()
