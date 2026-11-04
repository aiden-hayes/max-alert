"""Test settings: a throwaway SECRET_KEY and database, so tests never touch your .env or data/."""
import os
import sys
import tempfile
from pathlib import Path

_tmp = tempfile.mkdtemp(prefix="maxalert-test-")
os.environ["SECRET_KEY"] = "test-secret-key"
os.environ["DATABASE_URL"] = f"sqlite:///{_tmp}/test.db"
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def _no_live_hours(tmp_path, monkeypatch):
    """Never download GCSU's hours page in tests, and ignore any data/hours_cache.json."""
    import app
    import hours
    monkeypatch.setattr(hours, "CACHE_FILE", tmp_path / "hours_cache.json")
    monkeypatch.setattr(hours.HoursStore, "ensure_fresh", lambda self: None)
    monkeypatch.setattr(app, "hours_store", hours.HoursStore(fetch=lambda: ""))
