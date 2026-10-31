"""Check how much nutrition info the real menu data has.

    python nutrition_report.py          # coverage from the saved cache (run the app once first)
    python nutrition_report.py --raw    # also download today's raw JSON and list every field
                                        # Sodexo sends per item (to spot new nutrition fields)
"""
import argparse
import datetime as dt
import json
from collections import Counter, defaultdict

import config
from menu_store import CACHE_FILE

FIELDS = ["calories", "protein_g", "carbs_g", "fat_g"]


def coverage() -> None:
    try:
        cache = json.loads(CACHE_FILE.read_text())
    except FileNotFoundError:
        print("No cache yet. Start the app (python app.py) and load the page once, then rerun.")
        return
    print(f"Source: {cache.get('source')}   last updated: {cache.get('last_success')}\n")
    names = {l["slug"]: l["name"] for l in cache.get("locations", [])}
    stats = defaultdict(Counter)
    missing = defaultdict(set)
    for date, locs in sorted(cache.get("days", {}).items()):
        for slug, meals in locs.items():
            for meal in meals:
                key = (names.get(slug, slug), meal["name"])
                for st in meal["stations"]:
                    for item in st["items"]:
                        have = [f for f in FIELDS if item.get(f) is not None]
                        stats[key]["items"] += 1
                        if len(have) == len(FIELDS):
                            stats[key]["full"] += 1
                        elif have:
                            stats[key]["partial"] += 1
                        else:
                            stats[key]["none"] += 1
                            missing[key].add(f"{item['name']} ({st['name']})")
                        if not item.get("portion"):
                            stats[key]["no_portion"] += 1
    if not stats:
        print("The cache has no menu items.")
        return
    print(f"{'Location / meal':32} {'items':>6} {'full':>6} {'partial':>8} {'none':>6} {'no serving':>11}")
    for (loc, meal), c in sorted(stats.items()):
        print(f"{loc + ' / ' + meal:32} {c['items']:>6} {c['full']:>6} {c['partial']:>8} {c['none']:>6} {c['no_portion']:>11}")
    for key, items in sorted(missing.items()):
        print(f"\nNo nutrition at all: {key[0]} / {key[1]}")
        for name in sorted(items):
            print(f"  - {name}")


def raw_fields() -> None:
    import sodexo
    locations = config.load_locations()
    if not locations or not config.SODEXO_API_KEY:
        print("\n--raw needs locations.json and SODEXO_API_KEY (run discover.py first).")
        return
    today = dt.datetime.now(config.CAMPUS_TZ).date()
    for loc in locations:
        raw = sodexo.fetch_raw(loc, today)
        keys = Counter()
        example = None
        for meal in raw:
            for group in meal.get("groups") or []:
                for item in group.get("items") or []:
                    keys.update(k for k, v in item.items() if v not in (None, "", [], {}))
                    if example is None and item.get("calories"):
                        example = item
        print(f"\n{loc['name']} raw item fields today (field: how many items have a value):")
        for k, n in keys.most_common():
            print(f"  {k}: {n}")
        if example:
            print("\nExample item:\n" + json.dumps(example, indent=2)[:3000])


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--raw", action="store_true")
    args = ap.parse_args()
    coverage()
    if args.raw:
        raw_fields()
