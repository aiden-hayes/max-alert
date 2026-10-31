"""Test settings: a throwaway SECRET_KEY and database, so tests never touch your .env or data/."""
import os
import sys
import tempfile
from pathlib import Path

_tmp = tempfile.mkdtemp(prefix="maxalert-test-")
os.environ["SECRET_KEY"] = "test-secret-key"
os.environ["DATABASE_URL"] = f"sqlite:///{_tmp}/test.db"
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
