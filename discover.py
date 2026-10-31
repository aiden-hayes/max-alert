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
  2. Plain-HTTP method: downloads the page and its JS files and searches them
     for the API URL, IDs and key with regexes.
Results are written to locations.json and SODEXO_API_KEY is added to .env.
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

BASE = "https://georgiacollege.sodexomyway.com"
DEFAULT_PAGES = [f"{BASE}/en-us/locations/the-max"]
API_RE = re.compile(r"api-prd\.sodexomyway\.net/v[\d.]+/data/menu/(\d{5,10})/(\d{3,10})")
KEY_RES = [
    re.compile(r"""["']?api-?key["']?\s*[:=]\s*["']([A-Za-z0-9_\-]{20,})["']""", re.I),
    re.compile(r"""["']Api-Key["']\s*,\s*["']([A-Za-z0-9_\-]{20,})["']""", re.I),
]
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


def via_http(pages: list[str]) -> tuple[dict, str | None]:
    found, key = {}, None
    for page_url in pages:
        try:
            html = requests.get(page_url, headers=UA, timeout=20).text
        except requests.RequestException as e:
            print(f"  {page_url}: {e}")
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
            print(f"  {slug_from(page_url)}: location {m.group(1)}, menu {m.group(2)}")
        else:
            loc = re.search(r'locationId["\']?\s*[:=]\s*["\']?(\d{6,10})', blob, re.I)
            menu = re.search(r'menuId["\']?\s*[:=]\s*["\']?(\d{3,10})', blob, re.I)
            if loc and menu:
                found[slug_from(page_url)] = (loc.group(1), menu.group(1))
                print(f"  {slug_from(page_url)}: location {loc.group(1)}, menu {menu.group(1)} (from page data)")
        for rx in KEY_RES:
            k = rx.search(blob)
            if k:
                key = key or k.group(1)
    return found, key


def verify(loc_id: str, menu_id: str, key: str) -> bool:
    url = f"https://api-prd.sodexomyway.net/v0.2/data/menu/{loc_id}/{menu_id}"
    try:
        r = requests.get(url, params={"date": dt.date.today().isoformat()},
                         headers={"Api-Key": key}, timeout=20)
        return r.status_code == 200 and isinstance(r.json(), list)
    except (requests.RequestException, ValueError):
        return False


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

    found, key = {}, None
    if not args.no_browser:
        print("\nBrowser method:")
        found, key = via_browser(pages)
    if not found or not key:
        print("\nPlain-HTTP method:")
        f2, k2 = via_http(pages)
        for s, v in f2.items():
            found.setdefault(s, v)
        key = key or k2

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
        print(f"Checking {slug} ... {'OK' if verify(l, m, key) else 'did NOT return a menu'}")
    save(found, key)


if __name__ == "__main__":
    main()
