"""Run with:  python -m pytest -q"""
import datetime as dt
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))  # conftest.py sets a test SECRET_KEY and database first

import config  # noqa: E402
import menu_store  # noqa: E402
import sodexo  # noqa: E402

FIXTURE = json.loads((ROOT / "tests/fixtures/sample_menu.json").read_text())


def test_normalize_real_sodexo_shape():
    meals = sodexo.normalize(FIXTURE)
    assert [m["name"] for m in meals] == ["Breakfast", "Lunch", "Dinner"]
    item = meals[0]["stations"][0]["items"][0]
    assert item["name"] == "Raspberry White Chocolate Scone"
    assert item["calories"] == 264 and item["tags"] == [] and item["allergens"] == []
    # nutrition from the real response: "35g" carbs, "4g" protein, "12" fat, portion "Scone"
    assert (item["carbs_g"], item["protein_g"], item["fat_g"], item["portion"]) == (35, 4, 12, "Scone")


def test_missing_nutrition_is_none_not_zero():
    raw = [{"name": "Lunch", "groups": [{"name": "Deli", "items": [
        {"formalName": "Mystery Wrap", "calories": "", "protein": None, "fat": "N/A"},
        {"formalName": "Oatmeal", "calories": "150", "protein": "5.5g", "carbohydrates": "27g", "fat": "2.5"}]}]}]
    wrap, oats = sodexo.normalize(raw)[0]["stations"][0]["items"]
    assert [wrap[k] for k in ("calories", "protein_g", "carbs_g", "fat_g")] == [None] * 4
    assert (oats["calories"], oats["protein_g"], oats["carbs_g"], oats["fat_g"]) == (150, 5.5, 27, 2.5)


def test_normalize_tags_allergens_and_placeholders():
    raw = [{"name": "LATE NIGHT", "groups": [{"name": "Grill", "items": [
        {"formalName": "Veggie Wrap &amp; Chips", "isVegan": True, "isVegetarian": True,
         "allergens": [{"name": "wheat"}, {"name": "soy", "contains": False}]},
        {"formalName": "Have a Nice Day", "ingredients": "Plate Cost Peripherals"},
    ]}]}]
    meal = sodexo.normalize(raw)[0]
    assert meal["name"] == "Late Night"
    items = meal["stations"][0]["items"]
    assert len(items) == 1
    assert items[0]["name"] == "Veggie Wrap & Chips"
    assert items[0]["tags"] == ["vegan", "vegetarian"]
    assert items[0]["allergens"] == ["Wheat"]


@pytest.fixture
def live_store(tmp_path, monkeypatch):
    monkeypatch.setattr(menu_store, "CACHE_FILE", tmp_path / "cache.json")
    monkeypatch.setattr(config, "SODEXO_API_KEY", "test-key")
    monkeypatch.setattr(config, "DEMO_MODE", False)
    monkeypatch.setattr(config, "load_locations", lambda: [
        {"slug": "the-max", "name": "The MAX", "location_id": "1", "menu_id": "2"}])


def test_failed_refresh_keeps_last_good_menu(live_store):
    calls = {"fail": False}

    def fetch(loc, day):
        if calls["fail"]:
            raise ConnectionError("site down")
        return FIXTURE

    store = menu_store.MenuStore(fetch=fetch)
    store.refresh()
    good = store.snapshot()
    assert good["last_error"] is None and len(good["days"]) == config.DAYS_AHEAD

    calls["fail"] = True
    store.refresh()
    after = store.snapshot()
    assert "site down" in after["last_error"]
    assert after["days"] == good["days"]              # old menu still there
    assert after["last_success"] == good["last_success"]

    # and it survives a restart (disk cache)
    reloaded = menu_store.MenuStore(fetch=fetch).snapshot()
    assert reloaded["days"] == good["days"]


def test_current_meal_picks_next_meal(monkeypatch, live_store):
    import app as app_module
    today = dt.date(2026, 9, 30)  # a Wednesday
    days = {today.isoformat(): {"the-max": [{"name": n, "stations": []} for n in ["Breakfast", "Lunch", "Dinner"]]},
            (today + dt.timedelta(days=1)).isoformat(): {"the-max": [{"name": "Breakfast", "stations": []}]}}

    def at(h, m):
        monkeypatch.setattr(app_module, "campus_now",
                            lambda: dt.datetime(2026, 9, 30, h, m, tzinfo=config.CAMPUS_TZ))
        return app_module.current_or_next_meal(days)

    assert at(8, 0)["meal"] == "Breakfast" and at(8, 0)["status"] == "now"
    assert at(10, 50)["meal"] == "Lunch" and at(10, 50)["status"] == "next"
    assert at(12, 0)["meal"] == "Lunch" and at(12, 0)["status"] == "now"
    late = at(21, 0)
    assert late["meal"] == "Breakfast" and late["date"] == "2026-10-01"


def test_api_endpoints_in_demo_mode(tmp_path, monkeypatch):
    monkeypatch.setattr(menu_store, "CACHE_FILE", tmp_path / "cache.json")
    monkeypatch.setattr(config, "DEMO_MODE", True)
    import app as app_module
    app_module.store = menu_store.MenuStore()
    client = app_module.app.test_client()
    data = client.get("/api/menus").get_json()
    assert data["mode"] == "demo" and len(data["dates"]) == 7
    assert data["days"][data["dates"][0]]["the-max"]
    # no Anthropic key -> friendly error, not a crash
    monkeypatch.setattr(config, "ANTHROPIC_API_KEY", "")
    r = client.post("/api/summary", json={})
    assert r.status_code == 503 and "isn't set up" in r.get_json()["error"]


@pytest.fixture
def client(tmp_path, monkeypatch):
    """Fresh database for each test."""
    import app as app_module
    import logbook
    from models import db
    monkeypatch.setattr(logbook, "LOG_DIR", tmp_path / "logs")
    with app_module.app.app_context():
        db.drop_all()
        db.create_all()
    return app_module.app.test_client()


def signup(client, email="jimmy@gcsu.edu", password="correct horse"):
    return client.post("/api/auth/signup", json={"email": email, "password": password})


ITEM = {"name": "Cheeseburger", "servings": 1.5, "calories": 540, "protein_g": 28,
        "carbs_g": None, "fat_g": 22.5, "station": "Grill", "location": "The MAX"}


def test_signup_login_logout_and_password_is_hashed(client):
    r = signup(client, "Jimmy@GCSU.edu")
    assert r.status_code == 201 and r.get_json()["user"]["email"] == "jimmy@gcsu.edu"
    assert client.get("/api/auth/me").get_json()["user"]["email"] == "jimmy@gcsu.edu"
    client.post("/api/auth/logout", json={})
    assert client.get("/api/auth/me").get_json()["user"] is None

    bad = client.post("/api/auth/login", json={"email": "jimmy@gcsu.edu", "password": "wrong password"})
    assert bad.status_code == 401 and bad.get_json()["error"] == "Email or password is incorrect."
    nobody = client.post("/api/auth/login", json={"email": "nobody@gcsu.edu", "password": "whatever1"})
    assert nobody.get_json()["error"] == bad.get_json()["error"]  # doesn't reveal who has an account
    ok = client.post("/api/auth/login", json={"email": "JIMMY@gcsu.edu", "password": "correct horse"})
    assert ok.status_code == 200

    import app as app_module
    from models import User, db
    with app_module.app.app_context():
        stored = db.session.scalar(db.select(User)).password_hash
    assert "correct horse" not in stored and stored.startswith(("scrypt:", "pbkdf2:"))


@pytest.mark.parametrize("email,password,msg", [
    ("not-an-email", "longenough", "valid email"),
    ("a@b.co", "short", "at least 8"),
])
def test_signup_validation(client, email, password, msg):
    r = client.post("/api/auth/signup", json={"email": email, "password": password})
    assert r.status_code == 400 and msg in r.get_json()["error"]


def test_duplicate_signup_rejected(client):
    signup(client)
    client.post("/api/auth/logout", json={})
    assert signup(client).status_code == 409


def test_log_requires_login(client):
    for method, path in [("get", "/api/log"), ("post", "/api/log/meals"), ("put", "/api/log/settings")]:
        r = getattr(client, method)(path, json={} if method != "get" else None)
        assert r.status_code == 401 and r.get_json()["login_required"] is True


def test_changes_must_be_json(client):
    """A plain form post from another website can't log someone in or change their log."""
    r = client.post("/api/auth/login", data={"email": "x@y.co", "password": "whatever1"})
    assert r.status_code == 415
    signup(client)
    assert client.post("/api/log/meals", data={"meal": "Lunch"}).status_code == 415


def test_daily_log_crud(client):
    signup(client)
    r = client.post("/api/log/meals", json={"date": "2026-09-30", "meal": "Lunch", "items": [ITEM]})
    assert r.status_code == 201
    meal_id = r.get_json()["id"]
    day = client.get("/api/log?date=2026-09-30").get_json()
    assert day["goal"] is None and len(day["meals"]) == 1
    saved = day["meals"][0]["items"][0]
    assert saved["servings"] == 1.5 and saved["carbs_g"] is None and saved["fat_g"] == 22.5

    quick = {"name": "Gatorade", "calories": 140, "source": "quick"}
    r = client.put(f"/api/log/meals/{meal_id}", json={"meal": "Snack", "items": [ITEM, quick]})
    assert r.status_code == 200 and r.get_json()["meal"] == "Snack"
    assert [i["name"] for i in client.get("/api/log?date=2026-09-30").get_json()["meals"][0]["items"]] == \
        ["Cheeseburger", "Gatorade"]

    assert client.put("/api/log/settings", json={"goal": 2200}).get_json()["goal"] == 2200
    assert client.get("/api/log?date=2026-09-30").get_json()["goal"] == 2200
    assert client.put("/api/log/settings", json={"goal": ""}).get_json()["goal"] is None

    assert client.delete(f"/api/log/meals/{meal_id}").status_code == 204
    assert client.get("/api/log?date=2026-09-30").get_json()["meals"] == []


def test_users_cannot_see_or_change_each_others_meals(client):
    signup(client, "a@gcsu.edu")
    meal_id = client.post("/api/log/meals", json={"date": "2026-09-30", "meal": "Lunch", "items": [ITEM]}).get_json()["id"]
    client.post("/api/auth/logout", json={})
    signup(client, "b@gcsu.edu")
    assert client.get("/api/log?date=2026-09-30").get_json()["meals"] == []
    assert client.put(f"/api/log/meals/{meal_id}", json={"meal": "Snack", "items": [ITEM]}).status_code == 400
    assert client.delete(f"/api/log/meals/{meal_id}").status_code == 400


@pytest.mark.parametrize("body,msg", [
    ({"meal": "Second Breakfast", "items": [{"name": "x"}]}, "Meal must be"),
    ({"meal": "Lunch", "items": []}, "at least one"),
    ({"meal": "Lunch", "items": [{"name": "x", "servings": 0.3}]}, "Servings"),
    ({"meal": "Lunch", "items": [{"name": "x", "calories": -5}]}, "Calories"),
])
def test_daily_log_rejects_bad_input(client, body, msg):
    signup(client)
    r = client.post("/api/log/meals", json=body)
    assert r.status_code == 400 and msg in r.get_json()["error"]


def test_import_old_log_once(client, tmp_path):
    import logbook
    logbook.LOG_DIR.mkdir(parents=True)
    old = {"goal": 2100, "days": {"2026-09-29": [
        {"id": "ab12", "meal": "Dinner", "logged_at": "2026-09-29T18:02:00-04:00", "items": [ITEM]}]}}
    (logbook.LOG_DIR / "me-abc123.json").write_text(json.dumps(old))
    signup(client)
    offer = client.get("/api/log/import?legacy=me-abc123").get_json()["offer"]
    assert offer == {"meals": 1, "days": 1, "goal": 2100}
    assert client.post("/api/log/import", json={"legacy": "me-abc123"}).get_json()["imported"] == 1
    day = client.get("/api/log?date=2026-09-29").get_json()
    assert day["goal"] == 2100 and day["meals"][0]["items"][0]["name"] == "Cheeseburger"
    # offered only once, and the old file can't be claimed again
    assert client.get("/api/log/import?legacy=me-abc123").get_json()["offer"] is None
    assert not (logbook.LOG_DIR / "me-abc123.json").exists()
    client.post("/api/auth/logout", json={})
    signup(client, "other@gcsu.edu")
    assert client.get("/api/log/import?legacy=me-abc123").get_json()["offer"] is None


def test_import_dismiss(client):
    signup(client)
    assert client.post("/api/log/import/dismiss", json={}).status_code == 200
    assert client.get("/api/auth/me").get_json()["user"]["import_offer_done"] is True


def test_old_cache_without_nutrition_is_rebuilt(live_store):
    """A cache saved by the previous version (no schema field) gets re-downloaded once."""
    menu_store.CACHE_FILE.write_text(json.dumps({
        "days": {"2026-09-30": {"the-max": [{"name": "Lunch", "stations": []}]}},
        "source": "live", "last_attempt": menu_store.campus_now().isoformat(), "last_error": None}))
    store = menu_store.MenuStore(fetch=lambda loc, day: FIXTURE)
    assert store.is_stale()
    store.ensure_fresh()  # blocking refresh
    snap = store.snapshot()
    assert snap["schema"] == menu_store.SCHEMA_VERSION and not store.is_stale()
    first_day = snap["days"][min(snap["days"])]["the-max"]
    assert first_day[0]["stations"][0]["items"][0]["carbs_g"] == 35
