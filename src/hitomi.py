"""hitomi.la support: link validation, gallery data and image URLs.

How a gallery is read (one request per gallery, never one per page):

1. The gallery data ``https://ltn.<domain>/galleries/<id>.js`` (no account needed), a small
   script ``var galleryinfo = {...}`` with the title, the tags and, for every page, the file
   hash and which formats the site has for it (WebP always; AVIF and JPEG XL often).
2. ``https://ltn.<domain>/gg.js``: the site's own little script that says on which image
   server each file is and the current path prefix. It changes now and then, so it is read
   again after HITOMI_GG_MAX_AGE seconds, and at once when an image answers 403 / 404.

Images are ``https://<w|a|j><1|2>.<domain>/<prefix>/<number>/<hash>.<webp|avif|jxl>``: the
number comes from the last three characters of the hash, the server from gg.js. The same
calculation is in the site's common.js and in gallery-dl.

hitomi no longer gives the original files (JPEG / PNG / GIF): every image is WebP, AVIF or
JPEG XL. The program asks for the format chosen in "Image Format" when the site has it (so
nothing is converted), otherwise for WebP. "Native (Original)" saves the WebP as it is.

<domain> is HITOMI_CDN_DOMAIN in src/config.py. When it does not answer, the program reads
the domain used by a hitomi page and switches to it by itself.

Being cautious with the site (hitomi publishes no limits, but it answers "503 Service
Unavailable" to clients that download too fast, and 429 "Too Many Requests"):

* data requests are spaced by HITOMI_DATA_INTERVAL seconds and by the speed slider
  (hitomi has its own pace and pause, apart from the other sites);
* images in parallel by Speed level as in HITOMI_WORKERS (1, 2, 2, 3, 4), each followed by a
  pause of HITOMI_IMAGE_DELAY seconds;
* 429 or 503 ("soft ban"): every hitomi download pauses for Retry-After when the site gives
  it (else HITOMI_SLOWDOWN_WAIT for a 503, HITOMI_BLOCK_DEFAULT_WAIT for a 429) plus a random
  HITOMI_SOFTBAN_EXTRA_WAIT (45-60 s), with a warning and the countdown in the status bar;
  then the same image is asked again (it is not counted as failed);
* after HITOMI_SLOWDOWN_LIMIT soft bans in one gallery, its images go one at a time.

No cookies or account are needed.
"""

from __future__ import annotations

import json
import logging
import random
import re
import string
import threading
import time
from typing import NamedTuple

import requests

from src import throttle
from src.config import (
    CONNECTION_TIMEOUT,
    HITOMI_BLOCK_DEFAULT_WAIT,
    HITOMI_CDN_DOMAIN,
    HITOMI_DATA_INTERVAL,
    HITOMI_GG_MAX_AGE,
    HITOMI_SLOWDOWN_WAIT,
    HITOMI_SOFTBAN_EXTRA_WAIT,
    PAGE_FETCH_RETRIES,
    prepare_user_agent,
)

HITOMI_HOST = "hitomi.la"
HITOMI_BASE = f"https://{HITOMI_HOST}"

# Gallery pages: /galleries/<id>.html, /reader/<id>.html, and /<type>/<title>-<lang>-<id>.html
# (type: doujinshi, manga, cg (artist CG), gamecg, imageset, anime)
_GALLERY_TYPES = "galleries|reader|doujinshi|manga|cg|gamecg|imageset|anime"
HITOMI_URL_RE = re.compile(
    rf"^https?://(?:www\.)?hitomi\.la/(?:{_GALLERY_TYPES})/(?:[^/?#]*-)?(\d+)\.html(?:[?#].*)?$",
    re.IGNORECASE,
)
_ANY_HITOMI_RE = re.compile(r"^https?://(?:www\.)?hitomi\.la(?:/|$)", re.IGNORECASE)
# The same links inside any text (used by general_utils.extract_gallery_links)
LINK_IN_TEXT = rf"hitomi\.la/(?:{_GALLERY_TYPES})/(?:[^/\s?#\"'<>]*-)?(?P<hi_id>\d+)\.html"

MAX_CREDITS = 3  # authors written in front of the title; more become "Various"

# Formats the image servers have: internal format name -> (file extension, "has..." key)
SERVER_FORMATS = {"webp": "webp", "avif": "avif", "jxl": "jxl"}

_GG_CASE_RE = re.compile(r"case\s+(\d+):(?:\s*o\s*=\s*(\d+))?")
_GG_IF_RE = re.compile(r"if\s+\(g\s*===?\s*(\d+)\)[\s{]*o\s*=\s*(\d+)")
_GG_DEFAULT_RE = re.compile(r"(?:var\s|default:)\s*o\s*=\s*(\d+)")
_GG_PREFIX_RE = re.compile(r"b:\s*[\"']([^\"']+)[\"']")
_DOMAIN_IN_PAGE_RE = re.compile(r"//ltn\.([a-z0-9-]+(?:\.[a-z0-9-]+)+)/", re.IGNORECASE)


# ---------------------------------------------------------------- links

def is_hitomi_url(url: str) -> bool:
    """True when the link points to hitomi.la (well-formed or not)."""
    return bool(url) and bool(_ANY_HITOMI_RE.match(url.strip()))


def gallery_id(url: str) -> str | None:
    """Return the gallery number of a hitomi link, e.g. '3105447', or None."""
    match = HITOMI_URL_RE.match((url or "").strip())
    return match.group(1) if match else None


def validate_hitomi_url(url: str) -> str | None:
    """Return an error message if the link is not a valid hitomi gallery link, else None."""
    if gallery_id(url):
        return None
    return (
        f"Invalid hitomi URL: {url}. Expected a gallery link such as "
        "https://hitomi.la/galleries/<number>.html or https://hitomi.la/doujinshi/<title>-<number>.html "
        "(search, tag and artist pages are not galleries)."
    )


def canonical_url(url: str) -> str:
    """Return https://hitomi.la/galleries/<id>.html for any hitomi gallery or reader link."""
    gid = gallery_id(url)
    return f"{HITOMI_BASE}/galleries/{gid}.html" if gid else url.strip()


def reader_url(gid: str, page: int | None = None) -> str:
    """Reader page of a gallery (the Referer the image servers expect); with page, that page."""
    return f"{HITOMI_BASE}/reader/{gid}.html" + (f"#{page}" if page else "")


# ---------------------------------------------------------------- session / requests

def create_hitomi_session() -> requests.Session:
    """Return a session with browser-like headers (hitomi needs no cookies)."""
    session = requests.Session()
    session.headers.update({
        "User-Agent": prepare_user_agent(),
        "Accept": "*/*",
        "Accept-Language": "en-US,en;q=0.5",
        "Accept-Encoding": "gzip, deflate",
        "Origin": HITOMI_BASE,
        "Referer": f"{HITOMI_BASE}/",
        "Connection": "keep-alive",
    })
    return session


SLOWDOWN_CODES = (429, 503)  # what hitomi answers to a client that asks too much, too fast


def _retry_after(response: requests.Response) -> float | None:
    """Seconds asked by the Retry-After header, or None when the site does not say."""
    try:
        return max(1.0, min(float(response.headers.get("Retry-After", "")), 3600.0))
    except (TypeError, ValueError):
        return None


class SoftBan(NamedTuple):
    """One "slow down" answer of hitomi: whether it started a new pause, and its length."""

    new: bool      # False when hitomi was already paused (several threads report the same one)
    wait: float    # seconds asked by the site, or the program's own wait when it does not say
    extra: float   # random cautious extra (HITOMI_SOFTBAN_EXTRA_WAIT)
    asked: bool    # True when the wait comes from the site's Retry-After

    @property
    def total(self) -> int:
        """Whole pause in seconds."""
        return int(round(self.wait + self.extra))


def report_slowdown(response, where: str) -> SoftBan:
    """hitomi answered 429 / 503 ("soft ban"): pause every hitomi request for a while.

    The time asked by the site (Retry-After), else HITOMI_SLOWDOWN_WAIT (30-60 s) for a 503
    and HITOMI_BLOCK_DEFAULT_WAIT for a 429, plus a random HITOMI_SOFTBAN_EXTRA_WAIT (45-60 s).
    The other sites go on. The status bar shows the countdown (throttle ban listeners).
    """
    already_paused = throttle.ban_remaining(throttle.SITE_HI) > 0
    asked = _retry_after(response)
    if asked is not None:
        wait = asked
    elif response.status_code == 503:
        wait = random.uniform(*HITOMI_SLOWDOWN_WAIT)  # noqa: S311
    else:
        wait = float(HITOMI_BLOCK_DEFAULT_WAIT)
    extra = random.uniform(*HITOMI_SOFTBAN_EXTRA_WAIT)  # noqa: S311
    logging.warning(
        f"hitomi soft ban (HTTP {response.status_code}) on {where}: "
        f"{wait:.0f}s {'asked by the site' if asked is not None else 'wait'} + {extra:.0f}s extra.")
    throttle.report_ban(wait, source=throttle.SITE_HI, margin_range=(extra, extra))
    return SoftBan(new=not already_paused, wait=wait, extra=extra, asked=asked is not None)


_data_lock = threading.Lock()
_next_data_call = 0.0


def _wait_data_slot() -> None:
    """Space the data requests (gallery info, gg.js) of every thread by HITOMI_DATA_INTERVAL."""
    global _next_data_call  # pylint: disable=global-statement
    with _data_lock:
        now = time.monotonic()
        slot = max(now, _next_data_call)
        _next_data_call = slot + HITOMI_DATA_INTERVAL
    if slot > now:
        throttle.sleep(slot - now)


def fetch(session: requests.Session, url: str, referer: str | None = None,
          retries: int = PAGE_FETCH_RETRIES) -> requests.Response:
    """GET a hitomi data URL with hitomi pacing and polite retries.

    Returns the 200 or 404 response (the caller decides what 404 means); raises
    RuntimeError with a readable reason otherwise.
    """
    headers = {"Referer": referer} if referer else {}
    last_error = ""
    for attempt in range(max(1, retries)):
        throttle.wait_while_banned(throttle.SITE_HI)
        throttle.wait_turn(throttle.SITE_HI)
        _wait_data_slot()
        try:
            response = session.get(url, timeout=CONNECTION_TIMEOUT, headers=headers)
        except requests.ConnectionError as err:
            if throttle.wait_if_offline(err):
                continue
            last_error = f"server unreachable: {err}"
            break  # DNS / refused: the caller may try another domain
        except requests.RequestException as err:
            last_error = f"network error: {err}"
        else:
            if response.status_code in (200, 404):
                return response
            if response.status_code in SLOWDOWN_CODES:
                # hitomi pause with countdown; the loop waits it out at the top
                report_slowdown(response, url)
                last_error = f"too many requests (HTTP {response.status_code})"
                continue
            if response.status_code == 403:
                raise RuntimeError(f"Reason: access denied by hitomi for {url} (HTTP 403).")
            last_error = f"HTTP {response.status_code}"
        if attempt < retries - 1:
            delay = 2 ** (attempt + 1) + random.uniform(0.5, 1.5)  # noqa: S311
            logging.warning(f"Temporary error on {url} ({last_error}). Retry {attempt + 2}/{retries} in {delay:.0f}s...")
            throttle.sleep(delay)
    raise RuntimeError(f"Error fetching {url}: {last_error}")


# ---------------------------------------------------------------- domain of the data / image servers

_domain_lock = threading.Lock()
_domain = HITOMI_CDN_DOMAIN


def cdn_domain() -> str:
    """Domain of the data and image servers in use (HITOMI_CDN_DOMAIN unless the site moved)."""
    return _domain


def domain_from_page(html: str) -> str:
    """Domain of the data servers named by a hitomi page ('' when none is found)."""
    match = _DOMAIN_IN_PAGE_RE.search(html or "")
    return match.group(1).lower() if match else ""


def _rediscover_domain(session: requests.Session, gid: str) -> bool:
    """Read the reader page of a gallery and switch to the data domain it uses.

    True when a different domain was found (the caller then tries again).
    """
    global _domain  # pylint: disable=global-statement
    try:
        response = fetch(session, reader_url(gid), referer=f"{HITOMI_BASE}/", retries=1)
    except RuntimeError as err:
        logging.info(f"hitomi page not readable to find the data domain: {err}")
        return False
    found = domain_from_page(response.text) if response.status_code == 200 else ""
    with _domain_lock:
        if not found or found == _domain:
            return False
        logging.warning(f"hitomi moved its data servers from {_domain} to {found}: using the new domain.")
        _domain = found
    with _gg_lock:
        _gg.clear()  # gg.js of the old domain no longer applies
    return True


# ---------------------------------------------------------------- gallery data

def _flag(value) -> bool:
    """The female / male markers of a tag are "1", 1, "" or missing."""
    return str(value or "").strip() not in ("", "0", "false", "False")


def parse_gallery_js(text: str, gid: str) -> dict:
    """Turn the gallery script ("var galleryinfo = {...}") into the info used by the downloader."""
    raw = (text or "").strip()
    if "=" in raw.split("{", 1)[0]:
        raw = raw.split("=", 1)[1]
    raw = raw.strip().rstrip(";").strip()
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError("unexpected gallery data")
    files = data.get("files")
    if not isinstance(files, list) or not files:
        kind = str(data.get("type") or "").lower()
        if kind == "anime":
            raise ValueError("this hitomi gallery is an anime (video), not images: it can't be downloaded")
        raise ValueError("gallery data without images")
    pages = []
    for item in files:
        if not isinstance(item, dict) or not re.fullmatch(r"[0-9a-fA-F]{3,}", str(item.get("hash") or "")):
            raise ValueError("gallery data with an image without hash")
        pages.append({
            "hash": str(item["hash"]).lower(),
            "name": str(item.get("name") or ""),
            "formats": [fmt for fmt in SERVER_FORMATS if fmt == "webp" or _flag(item.get(f"has{fmt}"))],
        })

    all_tags: list[str] = []
    content_tags: list[str] = []
    for tag in data.get("tags") or []:
        name = str(tag.get("tag") or "").strip() if isinstance(tag, dict) else ""
        if not name:
            continue
        namespace = "female" if _flag(tag.get("female")) else "male" if _flag(tag.get("male")) else "tag"
        all_tags.append(f"{namespace}:{name}")
        content_tags.append(name)

    def _names(key: str, field: str) -> list[str]:
        return [str(o.get(field)).strip() for o in data.get(key) or []
                if isinstance(o, dict) and str(o.get(field) or "").strip()]

    artists, groups = _names("artists", "artist"), _names("groups", "group")
    parodies, characters = _names("parodys", "parody"), _names("characters", "character")
    all_tags += [f"artist:{n}" for n in artists] + [f"group:{n}" for n in groups]
    all_tags += [f"parody:{n}" for n in parodies] + [f"character:{n}" for n in characters]
    language = str(data.get("language") or "").strip()
    if language:
        all_tags.append(f"language:{language}")

    raw_title = " ".join(str(data.get("title") or "").split())
    title = with_credits(raw_title, artists, groups)
    return {
        "id": str(data.get("id") or gid),
        # "[Group (Artist)] Title", like the E-Hentai / nhentai titles (hitomi keeps them apart)
        "title": title or f"hitomi {gid}",
        "raw_title": raw_title,
        "japanese": " ".join(str(data.get("japanese_title") or "").split()),
        "type": str(data.get("type") or ""),
        "language": language,
        "pages": pages,
        "artists": [display_name(n) for n in artists or groups],
        "tags": content_tags,
        # every tag as "namespace:name" (female, male, tag, artist, group, parody, character, language)
        "all_tags": all_tags,
        "parodies": parodies,
    }


def display_name(name: str) -> str:
    """hitomi keeps names in lower case ("hiiragi popura"): show them as the site does ("Hiiragi Popura")."""
    name = " ".join(str(name or "").split())
    return string.capwords(name) if name == name.lower() else name


def with_credits(title: str, artists: list[str], groups: list[str]) -> str:
    """Title with the authors in front, as on E-Hentai: "[Group (Artist)] Title" / "[Artist] Title".

    More than MAX_CREDITS names become "Various". A title that already starts with "[" is
    left as it is (the authors are already there).
    """
    def _names(names: list[str]) -> str:
        names = [display_name(n) for n in names if str(n or "").strip()]
        return "Various" if len(names) > MAX_CREDITS else ", ".join(names)

    if not title or title.startswith("["):
        return title
    who_artists, who_groups = _names(artists), _names(groups)
    if who_groups and who_artists:
        credit = f"{who_groups} ({who_artists})"
    else:
        credit = who_groups or who_artists
    return f"[{credit}] {title}" if credit else title


def fetch_gallery_info(url: str, session: requests.Session | None = None) -> dict:
    """Return the gallery info: id, title, pages (hash + formats of each image), tags...

    Raises ValueError for a malformed link and RuntimeError when the gallery can't be read.
    """
    gid = gallery_id(url)
    if not gid:
        raise ValueError(validate_hitomi_url(url))
    session = session or create_hitomi_session()
    not_found = f"Reason: gallery {gid} does not exist or was removed from hitomi (HTTP 404)."
    for attempt in range(2):
        data_url = f"https://ltn.{cdn_domain()}/galleries/{gid}.js"
        try:
            response = fetch(session, data_url, referer=reader_url(gid))
        except RuntimeError as err:
            if attempt == 0 and "unreachable" in str(err) and _rediscover_domain(session, gid):
                continue
            message = f"Error reading hitomi gallery {gid}: {err}"
            logging.error(message)
            raise RuntimeError(message) from None
        if response.status_code == 404:
            raise RuntimeError(not_found)
        try:
            return parse_gallery_js(response.text, gid)
        except ValueError as err:
            message = f"Error reading hitomi gallery {gid}: {err}"
            logging.error(message)
            raise RuntimeError(message) from None
    raise RuntimeError(f"Error reading hitomi gallery {gid}: data servers not reachable.")


# ---------------------------------------------------------------- gg.js: where each image is

def parse_gg(text: str) -> tuple[dict[int, int], str, int]:
    """Read gg.js: (number -> server offset, path prefix, default offset).

    gg.js looks like:  m: function(g) { var o = 0; switch (g) { case 1303: case 3979: ...
    o = 1; break; } return o; }, ... b: '1795449601/'
    """
    offsets: dict[int, int] = {}
    pending: list[int] = []
    for match in _GG_CASE_RE.finditer(text or ""):
        pending.append(int(match.group(1)))
        if match.group(2):
            for key in pending:
                offsets[key] = int(match.group(2))
            pending.clear()
    for match in _GG_IF_RE.finditer(text or ""):
        offsets[int(match.group(1))] = int(match.group(2))
    default = _GG_DEFAULT_RE.search(text or "")
    prefix = _GG_PREFIX_RE.search(text or "")
    if not prefix:
        raise ValueError("gg.js without the image path prefix (b: '...')")
    return offsets, prefix.group(1).strip("/"), int(default.group(1)) if default else 0


_gg_lock = threading.Lock()
_gg: dict = {}  # {"offsets", "prefix", "default", "read_at"}


def gg_data(session: requests.Session | None = None,
            stale: tuple | None = None) -> tuple[dict[int, int], str, int]:
    """Current gg.js data, read again after HITOMI_GG_MAX_AGE seconds.

    stale: the data an image server just refused. It is read again at once if that is still
    the current data (when another thread already read a newer one, that one is returned).
    """
    with _gg_lock:
        current = (_gg["offsets"], _gg["prefix"], _gg["default"]) if _gg else None
        fresh = current is not None and time.monotonic() - _gg["read_at"] < HITOMI_GG_MAX_AGE
        if fresh and (stale is None or stale != current):
            return current
        response = fetch(session or create_hitomi_session(), f"https://ltn.{cdn_domain()}/gg.js")
        if response.status_code != 200:
            raise RuntimeError(f"hitomi gg.js not available (HTTP {response.status_code}).")
        offsets, prefix, default = parse_gg(response.text)
        _gg.update(offsets=offsets, prefix=prefix, default=default, read_at=time.monotonic())
        return offsets, prefix, default


def image_number(image_hash: str) -> int:
    """Number of an image from its hash: last character + the two before it, in hexadecimal."""
    return int(image_hash[-1] + image_hash[-3:-1], 16)


def image_url(image_hash: str, ext: str, gg: tuple[dict[int, int], str, int], domain: str | None = None) -> str:
    """URL of one image, e.g. https://w1.<domain>/1795449601/1234/<hash>.webp."""
    offsets, prefix, default = gg
    number = image_number(image_hash)
    server = f"{ext[0]}{offsets.get(number, default) + 1}"
    return f"https://{server}.{domain or cdn_domain()}/{prefix}/{number}/{image_hash}.{ext}"


def formats_to_try(page: dict, image_format: str) -> list[str]:
    """Server formats to ask for one page, best first.

    The format chosen in "Image Format" when the site has it (no conversion needed), then
    WebP (always there), then the other formats of that image.
    """
    available = page.get("formats") or ["webp"]
    first = image_format if image_format in SERVER_FORMATS and image_format in available else "webp"
    return [first] + [fmt for fmt in available if fmt != first]


def image_headers(gid: str) -> dict:
    """Headers for the image servers (the reader page as Referer, like a browser)."""
    return {
        "Accept": "image/avif,image/webp,image/png,image/svg+xml,image/*;q=0.8,*/*;q=0.5",
        "Referer": reader_url(gid),
        "Sec-Fetch-Dest": "image",
        "Sec-Fetch-Mode": "no-cors",
        "Sec-Fetch-Site": "cross-site",
    }
