"""Check GitHub for a newer release of the program.

One request to the GitHub API (https://api.github.com/repos/<owner>/<repo>/releases/latest);
nothing is downloaded or installed: the user is only told that a new version exists and can
open the release page. Turned on/off from Help → "Check for updates at start-up".
"""

from __future__ import annotations

import re

import requests

from src.config import APP_VERSION, CONNECTION_TIMEOUT, PROJECT_URL, prepare_user_agent

_REPO_RE = re.compile(r"github\.com/([^/]+)/([^/]+?)/?$")


def releases_api_url(project_url: str = PROJECT_URL) -> str:
    """Return the GitHub API address of the latest release of the project."""
    match = _REPO_RE.search(project_url)
    if not match:
        raise ValueError(f"Not a GitHub project address: {project_url}")
    return f"https://api.github.com/repos/{match.group(1)}/{match.group(2)}/releases/latest"


def parse_version(text: str) -> tuple[int, ...]:
    """Turn 'v2.1.0' / '2.1' / '2.1.0-beta' into (2, 1, 0) for comparison ((0,) if unreadable)."""
    match = re.match(r"\s*v?(\d+(?:\.\d+)*)", text or "")
    if not match:
        return (0,)
    parts = [int(p) for p in match.group(1).split(".")]
    while len(parts) > 1 and parts[-1] == 0:   # 2.1.0 == 2.1
        parts.pop()
    return tuple(parts)


def is_newer(latest: str, current: str = APP_VERSION) -> bool:
    """True when the latest version is greater than the current one."""
    return parse_version(latest) > parse_version(current)


def latest_release(timeout: float = min(CONNECTION_TIMEOUT, 10)) -> dict:
    """Ask GitHub for the latest release.

    Returns {"version": "2.1.0", "url": "<release page>", "newer": bool}.
    Raises requests.RequestException / ValueError when GitHub can't be reached or answers oddly.
    """
    response = requests.get(
        releases_api_url(),
        headers={"Accept": "application/vnd.github+json", "User-Agent": prepare_user_agent()},
        timeout=timeout,
    )
    if response.status_code == 404:  # no release published yet
        return {"version": APP_VERSION, "url": f"{PROJECT_URL}/releases", "newer": False}
    response.raise_for_status()
    data = response.json()
    tag = str(data.get("tag_name") or "")
    if not tag:
        raise ValueError("GitHub answered without a release version")
    version = tag.lstrip("vV")
    return {
        "version": version,
        "url": str(data.get("html_url") or f"{PROJECT_URL}/releases/latest"),
        "newer": is_newer(version),
    }
