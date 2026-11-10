"""nhentai.net support: link validation, gallery metadata and image URLs.

How a gallery is read (one request per gallery, never one per page):

1. The site's public JSON API v2 ``/api/v2/galleries/<id>`` (no account needed): title,
   tags and, for every page, the image path on the image servers.
   The old ``/api/gallery/<id>`` answers 403 "Use new API" and is no longer used.
2. If the API does not answer, the gallery page ``/g/<id>/`` itself: the gallery JSON
   embedded in it, or as a last resort the thumbnail links.

Images come from the image servers listed by ``/api/v2/cdn`` (read once per run; the list
in src/config.py is used if it can't be read): if one server fails the next is tried, and
if the file type given by the site is wrong (404) the other types are tried too.

Being polite with the site, so it does not block or rate-limit the program:

* the API allows 20 requests per minute per IP without an account (45 with the optional
  API key of an account): API calls are spaced by at least NHENTAI_API_INTERVAL seconds
  (NHENTAI_API_INTERVAL_KEY with a key) (shared by every thread), on top of the global
  pacing of the speed slider; the Pause button stops them too;
* HTTP 429 ("Too Many Requests") waits and retries, honouring the Retry-After header;
* a Cloudflare challenge ("Just a moment...") is reported with a clear explanation instead
  of being retried in a loop. It can be solved by copying the browser's ``cf_clearance``
  cookie and User-Agent into cookies.json (see cookies.example.json, "nhentai" section).

nhentai cookies are kept apart from the E-Hentai ones and are only sent to nhentai.
"""

from __future__ import annotations

import json
import logging
import random
import re
import threading
import time
from urllib.parse import urlparse
from pathlib import Path

import requests
from bs4 import BeautifulSoup

from src import throttle
from src.config import (
    CONNECTION_TIMEOUT,
    NHENTAI_API_INTERVAL,
    NHENTAI_API_INTERVAL_KEY,
    NHENTAI_BLOCK_DEFAULT_WAIT,
    NHENTAI_BLOCK_EXTRA_WAIT,
    NHENTAI_IMAGE_MIRRORS,
    PAGE_FETCH_RETRIES,
    prepare_user_agent,
)

NHENTAI_HOST = "nhentai.net"
NHENTAI_BASE = f"https://{NHENTAI_HOST}"

NHENTAI_URL_RE = re.compile(
    r"^https?://(?:www\.)?nhentai\.net/g/(\d+)(?:/(?:\d+/?)?)?(?:[?#].*)?$", re.IGNORECASE,
)
_ANY_NHENTAI_RE = re.compile(r"^https?://(?:www\.)?nhentai\.net(?:/|$)", re.IGNORECASE)

# Page type letters used by the site -> file extension
TYPE_EXT = {"j": "jpg", "p": "png", "g": "gif", "w": "webp", "a": "avif"}
ALL_EXTS = ("jpg", "webp", "png", "gif", "avif")

_EMBEDDED_JSON_RE = re.compile(r"""_gallery\s*=\s*JSON\.parse\(\s*(["'])((?:\\.|(?!\1).)*)\1\s*\)""", re.DOTALL)
_THUMB_RE = re.compile(r"/galleries/(\d+)/(\d+)t\.([a-z0-9]+)(?:\.[a-z0-9]+)?", re.IGNORECASE)


# ---------------------------------------------------------------- links

def is_nhentai_url(url: str) -> bool:
    """True when the link points to nhentai.net (well-formed or not)."""
    return bool(url) and bool(_ANY_NHENTAI_RE.match(url.strip()))


def gallery_id(url: str) -> str | None:
    """Return the gallery number of an nhentai link, e.g. '694850', or None."""
    match = NHENTAI_URL_RE.match((url or "").strip())
    return match.group(1) if match else None


def validate_nhentai_url(url: str) -> str | None:
    """Return an error message if the link is not a valid nhentai gallery link, else None."""
    if gallery_id(url):
        return None
    return (
        f"Invalid nhentai URL: {url}. Expected https://nhentai.net/g/<number>/ "
        "(for example https://nhentai.net/g/123456/)."
    )


def canonical_url(url: str) -> str:
    """Return https://nhentai.net/g/<id>/ for any nhentai gallery or reader link."""
    gid = gallery_id(url)
    return f"{NHENTAI_BASE}/g/{gid}/" if gid else url.strip()


# ---------------------------------------------------------------- cookies / session

def _load_nhentai_settings() -> dict[str, str]:
    """Read the optional "nhentai" section of cookies.json.

    {"nhentai": {"api_key": "...", "cf_clearance": "...", "user_agent": "Mozilla/5.0 ..."}}
    "api_key" goes in the Authorization header of API requests, "user_agent" replaces the
    User-Agent; every other key is sent as a cookie to nhentai.net only.
    """
    path = Path("cookies.json")
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as err:
        logging.warning(f"Error reading cookies.json: {err}")
        return {}
    section = data.get("nhentai") if isinstance(data, dict) else None
    if not isinstance(section, dict):
        return {}
    return {str(k): str(v).strip() for k, v in section.items() if str(v).strip()}


def create_nhentai_session() -> requests.Session:
    """Return a session with browser-like headers and the nhentai cookies (nhentai only)."""
    settings = _load_nhentai_settings()
    session = requests.Session()
    session.headers.update({
        # Cloudflare ties cf_clearance to the browser that solved the challenge:
        # when that cookie is used, the same User-Agent must be sent.
        "User-Agent": settings.get("user_agent") or prepare_user_agent(),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.5",
        "Accept-Encoding": "gzip, deflate",
        "Connection": "keep-alive",
    })
    for name, value in settings.items():
        if name not in ("user_agent", "api_key"):
            session.cookies.set(name, value, domain=f".{NHENTAI_HOST}")
    # Optional API key of the nhentai account: higher API limit (45 instead of 20 per minute).
    # Sent only to the API (see fetch), never to the image servers.
    session.nhentai_api_key = settings.get("api_key", "")
    return session


# ---------------------------------------------------------------- page problems

def _is_cloudflare_challenge(response: requests.Response) -> bool:
    """True when Cloudflare answered with its bot check instead of the real page."""
    if response.headers.get("cf-mitigated", "").lower() == "challenge":
        return True
    if response.status_code not in (403, 503):
        return False
    text = response.text[:5000].lower() if response.text else ""
    return any(marker in text for marker in (
        "just a moment", "cf-browser-verification", "challenge-platform", "cf_chl_", "attention required",
    ))


CLOUDFLARE_HELP = (
    "nhentai is protected by Cloudflare and asked for a browser check. Open the gallery once in "
    "your browser, then copy the 'cf_clearance' cookie and your browser's User-Agent into the "
    "\"nhentai\" section of cookies.json (see cookies.example.json) and try again."
)


def _describe_response(response: requests.Response) -> str:
    """Status, server and page title of a refused request, to understand who refused it."""
    server = response.headers.get("Server", "")
    cloudflare = bool(response.headers.get("cf-ray")) or "cloudflare" in server.lower()
    match = re.search(r"<title[^>]*>(.*?)</title>", response.text or "", re.IGNORECASE | re.DOTALL)
    title = " ".join(match.group(1).split())[:80] if match else ""
    parts = [f"HTTP {response.status_code}", f"server: {server or '?'}{' / Cloudflare' if cloudflare else ''}"]
    if title:
        parts.append(f"page title: {title}")
    elif response.text:
        parts.append(f"text: {' '.join(response.text.split())[:120]}")
    return "; ".join(parts)


def _retry_after(response: requests.Response) -> float:
    """Seconds to wait after a 429, from the Retry-After header when present."""
    value = response.headers.get("Retry-After", "")
    try:
        return max(1.0, min(float(value), 3600.0))
    except ValueError:
        return float(NHENTAI_BLOCK_DEFAULT_WAIT)


def report_rate_limit(response, where: str) -> None:
    """nhentai answered 429: pause every download for the time asked, plus 10-15 s."""
    delay = _retry_after(response)
    logging.warning(f"nhentai rate limit (HTTP 429) on {where}.")
    throttle.report_ban(delay, source="nhentai", margin_range=NHENTAI_BLOCK_EXTRA_WAIT)


_api_lock = threading.Lock()
_next_api_call = 0.0


def _wait_api_slot(interval: float) -> None:
    """Keep nhentai requests under the site's limit (20 per minute per IP, 45 with a key)."""
    global _next_api_call  # pylint: disable=global-statement
    with _api_lock:
        now = time.monotonic()
        slot = max(now, _next_api_call)
        _next_api_call = slot + interval
    if slot > now:
        time.sleep(slot - now)


def fetch(session: requests.Session, url: str, referer: str | None = None,
          retries: int = PAGE_FETCH_RETRIES) -> requests.Response:
    """GET an nhentai page / API URL with global pacing and polite retries.

    Returns the 200 response; raises RuntimeError with a readable reason otherwise.
    404 is returned as well (the caller decides what it means).
    """
    headers = {"Referer": referer} if referer else {}
    api_key = getattr(session, "nhentai_api_key", "")
    if api_key and "/api/" in url:
        headers["Authorization"] = f"Key {api_key}"
    last_error = ""
    for attempt in range(max(1, retries)):
        throttle.wait_while_banned()
        throttle.wait_turn()
        _wait_api_slot(NHENTAI_API_INTERVAL_KEY if api_key else NHENTAI_API_INTERVAL)
        try:
            response = session.get(url, timeout=CONNECTION_TIMEOUT, headers=headers)
        except requests.RequestException as err:
            last_error = f"network error: {err}"
        else:
            if response.status_code in (200, 404):
                return response
            if _is_cloudflare_challenge(response):
                raise RuntimeError(f"Reason: Cloudflare browser check (HTTP {response.status_code}). {CLOUDFLARE_HELP}")
            if response.status_code == 429:
                # Global pause with countdown (GUI status bar); the loop waits it out at the top
                report_rate_limit(response, url)
                last_error = "rate limit (HTTP 429)"
                continue
            if response.status_code == 403:
                raise RuntimeError(f"Reason: access denied by nhentai for {url} ({_describe_response(response)}).")
            last_error = f"HTTP {response.status_code}"
        if attempt < retries - 1:
            delay = 2 ** (attempt + 1) + random.uniform(0.5, 1.5)  # noqa: S311
            logging.warning(f"Temporary error on {url} ({last_error}). Retry {attempt + 2}/{retries} in {delay:.0f}s...")
            time.sleep(delay)
    raise RuntimeError(f"Error fetching page {url}: {last_error}")


# ---------------------------------------------------------------- gallery data

def _page_ext(page: dict) -> str:
    """Extension of one page entry of the gallery JSON."""
    for key in ("path", "url"):
        value = page.get(key)
        if isinstance(value, str) and "." in value:
            return value.rsplit(".", 1)[-1].lower()
    kind = str(page.get("t") or page.get("type") or page.get("ext") or "j").lower().lstrip(".")
    return TYPE_EXT.get(kind, kind if kind in ALL_EXTS else "jpg")


def _parse_gallery_json(data: dict, gid: str) -> dict:
    """Turn the site's gallery JSON into the info used by the downloader."""
    if not isinstance(data, dict) or data.get("error"):
        raise ValueError(str(data.get("error") if isinstance(data, dict) else "unexpected answer"))
    media_id = str(data.get("media_id") or "").strip()
    pages = data.get("pages")  # API v2: [{"number": 1, "path": "galleries/<media>/1.webp", ...}]
    if not isinstance(pages, list):
        images = data.get("images") or {}  # old format: {"pages": [{"t": "j"}, ...]}
        pages = images.get("pages") if isinstance(images, dict) else None
    if not media_id or not isinstance(pages, list) or not pages:
        raise ValueError("gallery data without media id or pages")
    if all(isinstance(p, dict) and str(p.get("number", "")).isdigit() for p in pages):
        pages = sorted(pages, key=lambda p: int(p["number"]))

    titles = data.get("title") or {}
    if isinstance(titles, str):
        titles = {"english": titles}
    title = (titles.get("english") or titles.get("pretty") or titles.get("japanese") or "").strip()

    tags = data.get("tags") or []
    by_type: dict[str, list[str]] = {}
    for tag in tags if isinstance(tags, list) else []:
        if isinstance(tag, dict) and tag.get("name"):
            by_type.setdefault(str(tag.get("type", "tag")), []).append(str(tag["name"]))

    return {
        "id": str(data.get("id") or gid),
        "media_id": media_id,
        "title": title or f"nhentai {gid}",
        "japanese": (titles.get("japanese") or "").strip(),
        "pages": [_page_ext(p) if isinstance(p, dict) else "jpg" for p in pages],
        # image path on the image servers, e.g. "galleries/4304843/1.webp" ("" = build it)
        "paths": [str(p.get("path") or "").lstrip("/") if isinstance(p, dict) else "" for p in pages],
        "artists": by_type.get("artist", []) or by_type.get("group", []),
        "tags": by_type.get("tag", []),
        "languages": [lang for lang in by_type.get("language", []) if lang != "translated"],
        "parodies": by_type.get("parody", []),
    }


def _parse_gallery_html(html: str, gid: str) -> dict:
    """Read the gallery info from the HTML page (embedded JSON, else thumbnails)."""
    match = _EMBEDDED_JSON_RE.search(html)
    if match:
        try:
            raw = json.loads(f'"{match.group(2)}"')  # decode the JS string literal (" ...)
            return _parse_gallery_json(json.loads(raw), gid)
        except ValueError:
            pass

    soup = BeautifulSoup(html, "html.parser")
    thumbs: dict[int, str] = {}
    media_id = ""
    for img in soup.select(".thumb-container img, #thumbnail-container img, a.gallerythumb img"):
        src = img.get("data-src") or img.get("src") or ""
        found = _THUMB_RE.search(src)
        if found:
            media_id = found.group(1)
            thumbs[int(found.group(2))] = found.group(3).lower()
    if not media_id or not thumbs:
        raise ValueError("no gallery data found in the page")

    title_tag = soup.select_one("#info h1.title, h1.title, #info h1")
    title = title_tag.get_text(" ", strip=True) if title_tag else ""
    if not title:
        page_title = soup.title.get_text(strip=True) if soup.title else ""
        title = re.sub(r"\s*[»|-]\s*nhentai.*$", "", page_title, flags=re.IGNORECASE).strip()
    jp_tag = soup.select_one("#info h2.title, h2.title")
    count = max(thumbs)
    return {
        "id": gid,
        "media_id": media_id,
        "title": title or f"nhentai {gid}",
        "japanese": jp_tag.get_text(" ", strip=True) if jp_tag else "",
        "pages": [thumbs.get(i, "jpg") for i in range(1, count + 1)],
        "paths": [""] * count,
        "artists": [], "tags": [], "languages": [], "parodies": [],
    }


def fetch_gallery_info(url: str, session: requests.Session | None = None) -> dict:
    """Return the gallery info: id, media_id, title, pages (list of extensions), tags...

    Raises ValueError for a malformed link and RuntimeError when the gallery can't be read.
    """
    gid = gallery_id(url)
    if not gid:
        raise ValueError(validate_nhentai_url(url))
    session = session or create_nhentai_session()
    page_url = f"{NHENTAI_BASE}/g/{gid}/"
    problems: list[str] = []

    not_found = f"Reason: gallery {gid} does not exist or was removed from nhentai (HTTP 404)."
    attempts = (
        ("API", f"{NHENTAI_BASE}/api/v2/galleries/{gid}", page_url, lambda r: _parse_gallery_json(r.json(), gid)),
        ("page", page_url, f"{NHENTAI_BASE}/", lambda r: _parse_gallery_html(r.text, gid)),
    )
    for label, target, referer, parse in attempts:
        try:
            response = fetch(session, target, referer=referer)
            if response.status_code == 404:
                raise LookupError(not_found)
            info = parse(response)
        except LookupError:
            raise RuntimeError(not_found) from None
        except (RuntimeError, ValueError) as err:
            problems.append(f"{label}: {err}")
            continue
        info["source"] = "api" if label == "API" else "html"
        return info

    message = f"Error reading nhentai gallery {gid}: " + " | ".join(problems)
    if any("Cloudflare" in problem for problem in problems):
        message += f". {CLOUDFLARE_HELP}"
    logging.error(message)
    raise RuntimeError(message)


_image_servers: list[str] = []
_image_servers_lock = threading.Lock()


def image_servers(session=None) -> list[str]:
    """Host names of the image servers, from /api/v2/cdn (read once), else from config."""
    with _image_servers_lock:
        if _image_servers:
            return list(_image_servers)
        hosts: list[str] = []
        try:
            response = fetch(session or create_nhentai_session(), f"{NHENTAI_BASE}/api/v2/cdn", retries=1)
            if response.status_code == 200:
                for server in response.json().get("image_servers") or []:
                    host = urlparse(str(server)).netloc or str(server).strip("/")
                    if host and host not in hosts:
                        hosts.append(host)
        except Exception as err:  # the fixed list below still works
            logging.info(f"nhentai image server list not available ({err}): using the default list.")
        hosts += [h for h in NHENTAI_IMAGE_MIRRORS if h not in hosts]
        _image_servers.extend(hosts)
        return list(hosts)


def page_path(info: dict, page_number: int) -> str:
    """Image path of one page, e.g. 'galleries/4304843/7.webp'."""
    paths = info.get("paths") or []
    if page_number <= len(paths) and paths[page_number - 1]:
        return paths[page_number - 1]
    exts = info.get("pages") or []
    ext = exts[page_number - 1] if page_number <= len(exts) else "jpg"
    return f"galleries/{info['media_id']}/{page_number}.{ext}"


def path_candidates(path: str) -> list[str]:
    """The path given by the site first, then the same file with the other types (used on 404)."""
    stem, _, ext = path.rpartition(".")
    if not stem:
        return [path]
    return [path] + [f"{stem}.{e}" for e in ALL_EXTS if e != ext.lower()]


def image_url(server: str, path: str) -> str:
    """URL of one image on one image server."""
    return f"https://{server}/{path.lstrip('/')}"


def image_headers() -> dict:
    """Headers for the image CDN (the gallery page as Referer, like a browser)."""
    return {
        "Accept": "image/avif,image/webp,image/png,image/svg+xml,image/*;q=0.8,*/*;q=0.5",
        "Referer": f"{NHENTAI_BASE}/",
        "Sec-Fetch-Dest": "image",
        "Sec-Fetch-Mode": "no-cors",
        "Sec-Fetch-Site": "same-site",
    }
