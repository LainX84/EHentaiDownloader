"""Version comparison of the update check (no network)."""

import pytest

from src import update_check


@pytest.mark.parametrize("latest, current, newer", [
    ("v2.1.0", "2.0.0", True),
    ("2.0.1", "2.0.0", True),
    ("v10.0.0", "9.9.9", True),
    ("v2.0.0", "2.0.0", False),
    ("2.0", "2.0.0", False),
    ("v1.9.9", "2.0.0", False),
    ("garbage", "2.0.0", False),
])
def test_is_newer(latest, current, newer):
    assert update_check.is_newer(latest, current) is newer


def test_api_url_from_project_url():
    assert update_check.releases_api_url("https://github.com/LainX84/EHentaiDownloader") == (
        "https://api.github.com/repos/LainX84/EHentaiDownloader/releases/latest"
    )


class _Response:
    def __init__(self, status, data):
        self.status_code, self._data = status, data

    def raise_for_status(self):
        if self.status_code >= 400:
            raise update_check.requests.HTTPError(str(self.status_code))

    def json(self):
        return self._data


def test_latest_release(monkeypatch):
    monkeypatch.setattr(update_check.requests, "get", lambda *a, **k: _Response(
        200, {"tag_name": "v99.0.0", "html_url": "https://github.com/x/y/releases/tag/v99.0.0"}))
    result = update_check.latest_release()
    assert result == {"version": "99.0.0", "url": "https://github.com/x/y/releases/tag/v99.0.0", "newer": True}


def test_no_release_published_yet(monkeypatch):
    monkeypatch.setattr(update_check.requests, "get", lambda *a, **k: _Response(404, {}))
    assert update_check.latest_release()["newer"] is False
