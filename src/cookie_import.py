"""Read account cookies pasted by the user and save them to cookies.json.

Accepted input (whatever the browser: Chrome, Chromium, Edge, Firefox, also Flatpak/Snap):

* the cookie table copied from the browser developer tools (one cookie per line,
  columns separated by tabs: name, value, domain, ...);
* a Netscape ``cookies.txt`` file exported by a browser extension;
* a JSON export (list of {"name", "value", "domain"});
* a ``Cookie:`` header / "name=value; name2=value2" line.

Only the cookies the program needs are kept; everything else is ignored.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

COOKIES_FILE = Path("cookies.json")

# E-Hentai / ExHentai login cookies used by the program
EHENTAI_COOKIES = ("ipb_member_id", "ipb_pass_hash", "igneous")

_NAME_RE = re.compile(r"^[A-Za-z0-9_\-.]+$")


def parse_cookies(text: str) -> list[tuple[str, str, str]]:
    """Return (name, value, domain) for every cookie found in the text (domain may be '')."""
    text = (text or "").strip()
    if not text:
        return []
    if text[0] in "[{":
        found = _parse_json(text)
        if found:
            return found
    found: list[tuple[str, str, str]] = []
    for raw_line in text.splitlines():
        line = raw_line.rstrip("\r")
        if not line.strip():
            continue
        if line.startswith("#") and not line.startswith("#HttpOnly_"):
            continue  # comment line of cookies.txt
        columns = line.split("\t")
        if len(columns) >= 7 and columns[4].strip().lstrip("-").isdigit():
            # Netscape cookies.txt: domain, subdomains, path, secure, expiry, name, value
            domain = columns[0].removeprefix("#HttpOnly_").strip()
            found.append((columns[5].strip(), columns[6].strip(), domain))
            continue
        if len(columns) >= 2 and _NAME_RE.match(columns[0].strip()):
            # Developer tools table: name, value, domain, path, expires, ...
            domain = columns[2].strip() if len(columns) >= 3 else ""
            found.append((columns[0].strip(), columns[1].strip(), domain))
            continue
        found.extend(_parse_header(line))
    return [(n, v, d) for n, v, d in found if n and v]


def _parse_json(text: str) -> list[tuple[str, str, str]]:
    """JSON exports: a list of cookie objects, or a {name: value} object."""
    try:
        data = json.loads(text)
    except ValueError:
        return []
    if isinstance(data, dict):
        return [(str(k), str(v), "") for k, v in data.items() if isinstance(v, (str, int))]
    found = []
    for item in data if isinstance(data, list) else []:
        if isinstance(item, dict) and item.get("name"):
            found.append((str(item["name"]), str(item.get("value", "")), str(item.get("domain", ""))))
    return found


def _parse_header(line: str) -> list[tuple[str, str, str]]:
    """'Cookie: a=1; b=2' or 'a=1; b=2'."""
    line = re.sub(r"^\s*cookie\s*:\s*", "", line, flags=re.IGNORECASE)
    found = []
    for part in line.split(";"):
        name, sep, value = part.strip().partition("=")
        if sep and _NAME_RE.match(name.strip()):
            found.append((name.strip(), value.strip(), ""))
    return found


def extract_ehentai(cookies: list[tuple[str, str, str]]) -> dict[str, str]:
    """The E-Hentai / ExHentai login cookies among the parsed ones.

    'igneous' only exists on exhentai.org: a value from that domain wins. Cookies of other
    sites with the same name are ignored.
    """
    result: dict[str, str] = {}
    for name, value, domain in cookies:
        if name not in EHENTAI_COOKIES:
            continue
        domain = domain.lower().lstrip(".")
        if domain and not domain.endswith(("e-hentai.org", "exhentai.org")):
            continue
        if name == "igneous" and value.lower() == "mystery":
            continue  # placeholder given to accounts without ExHentai access
        if name not in result or domain.endswith("exhentai.org"):
            result[name] = value
    return result


def load_cookies_file(path: Path = COOKIES_FILE) -> dict:
    """Current content of cookies.json ({} if missing or unreadable)."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save_cookies_file(ehentai: dict[str, str], nhentai_api_key: str | None,
                      path: Path = COOKIES_FILE) -> None:
    """Write cookies.json, keeping every other value already in the file.

    Empty E-Hentai values remove that cookie; nhentai_api_key None leaves it as it is,
    '' removes it.
    """
    data = load_cookies_file(path)
    for name in EHENTAI_COOKIES:
        value = (ehentai.get(name) or "").strip()
        if value:
            data[name] = value
        else:
            data.pop(name, None)
    if nhentai_api_key is not None:
        section = data.get("nhentai") if isinstance(data.get("nhentai"), dict) else {}
        if nhentai_api_key.strip():
            section["api_key"] = nhentai_api_key.strip()
        else:
            section.pop("api_key", None)
        if section:
            data["nhentai"] = section
        else:
            data.pop("nhentai", None)
    tmp = path.with_name(f"{path.name}.part")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    os.replace(tmp, path)
    try:
        os.chmod(path, 0o600)  # private to the user on Linux / macOS
    except OSError:
        pass
