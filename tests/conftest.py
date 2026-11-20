"""Run the tests from the project folder, so "import src..." works with plain `pytest`."""

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


@pytest.fixture(autouse=True)
def _data_files_in_tmp(tmp_path, monkeypatch):
    """stats.json / tags.json of the tests go to a temporary folder, never to the project."""
    from src import config
    monkeypatch.setattr(config, "STATS_FILE", str(tmp_path / "stats.json"))
    monkeypatch.setattr(config, "TAGS_FILE", str(tmp_path / "tags.json"))
