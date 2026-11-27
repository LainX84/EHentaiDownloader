"""Gallery tags: reading them from E-Hentai / ExHentai pages, nhentai and hitomi data, favorite tags.

The tags met in verified or downloaded galleries are remembered in tags.json (name and how
many galleries had it): the favorite tags window lists them, with a selector next to each one.

Tags are kept as "namespace:name" strings, lower case, single spaces:
    E-Hentai / ExHentai   "female:big breasts", "other:ai generated", "language:english"
    nhentai               "tag:netorare", "artist:...", "language:english"
    hitomi                "female:big breasts", "male:...", "tag:full color", "artist:...", "language:english"

Favorite tags (Settings → Favorite tags…) are a list of {"tag", "label", "color", "merged"} in
priority order (position 1 = most important). The first favorite (lowest position) found in a
gallery is its "badge": shown in the lists and, with "Sort downloads into folders by favorite
tag", the name of the sub-folder of the album.

Merged tags: a favorite can stand for several tags ("merged": e.g. "gender bender" with
"gender morph" and "gender change", or "netorare" with "netorase" and "netori"): a gallery
with any of them gets that favorite (one label, one colour, one folder).

Nested folders: with "Nest the folders by priority" every favorite of a gallery becomes a folder
level, in priority order: Downloads/<site>/Gender Bender/Netorare/<gallery> (at most
NEST_MAX_DEPTH levels). Without it only the first favorite is used.

A favorite matches a gallery tag by name, whatever the namespace ("netorare" matches
"female:netorare" and "tag:netorare"); "female:big breasts" matches that namespace only.
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
from pathlib import Path

from src import config

BADGE_MAX = 20  # longest favorite label shown in the lists (the column is as wide as the longest one)
NEST_MAX_DEPTH = 5  # most folder levels made by "Nest the folders by priority" (paths stay short)

# Namespaces of tags that describe the content (the others are artist, language, parody...)
CONTENT_NAMESPACES = {"female", "male", "mixed", "other", "misc", "tag", "f", "m", "x", "o", ""}

_SEP_RE = re.compile(r"[\s_]+")

_lock = threading.RLock()
_favorites: list[dict] = []
_folders_enabled = False
_nested_folders = False


def normalize(tag: str) -> str:
    """ "Female:Big_Breasts " -> "female:big breasts" (namespace kept, if any)."""
    text = _SEP_RE.sub(" ", (tag or "").strip().lower())
    if ":" in text:
        namespace, name = text.split(":", 1)
        return f"{namespace.strip()}:{name.strip()}"
    return text


def split(tag: str) -> tuple[str, str]:
    """ "female:big breasts" -> ("female", "big breasts"); "netorare" -> ("", "netorare")."""
    text = normalize(tag)
    if ":" in text:
        namespace, name = text.split(":", 1)
        return namespace, name
    return "", text


def is_content_tag(tag: str) -> bool:
    """True for tags that say what the gallery is about (not artist, language, parody...)."""
    return split(tag)[0] in CONTENT_NAMESPACES


# ---------------------------------------------------------------------------- reading tags

def from_ehentai_soup(soup) -> list[str]:
    """Tags of an E-Hentai / ExHentai gallery page (the #taglist table)."""
    if soup is None:
        return []
    taglist = soup.find("div", {"id": "taglist"})
    if taglist is None:
        return []
    found: list[str] = []
    # <div id="td_female:big_breasts" class="gt"><a id="ta_female:big_breasts">big breasts</a></div>
    for node in taglist.find_all(["div", "a"], id=True):
        node_id = str(node.get("id") or "")
        if node_id.startswith(("td_", "ta_")) and len(node_id) > 3:
            found.append(normalize(node_id[3:]))
    if not found:  # older layout: "namespace:" cell, then the tag links
        for row in taglist.find_all("tr"):
            cells = row.find_all("td")
            if len(cells) < 2:
                continue
            namespace = cells[0].get_text(strip=True).rstrip(":").lower()
            for link in cells[1].find_all("a"):
                name = link.get_text(" ", strip=True)
                if name:
                    found.append(normalize(f"{namespace}:{name}" if namespace else name))
    return _unique(found)


def from_nhentai_info(info: dict) -> list[str]:
    """Tags of an nhentai gallery (the data of src.nhentai.fetch_gallery_info)."""
    tags = info.get("all_tags")
    if isinstance(tags, list) and tags:
        return _unique(normalize(tag) for tag in tags)
    return _unique(normalize(f"tag:{name}") for name in info.get("tags", []) or [])


def from_hitomi_info(info: dict) -> list[str]:
    """Tags of a hitomi gallery (the data of src.hitomi.fetch_gallery_info)."""
    tags = info.get("all_tags")
    if isinstance(tags, list) and tags:
        return _unique(normalize(tag) for tag in tags)
    return _unique(normalize(f"tag:{name}") for name in info.get("tags", []) or [])


def _unique(tags) -> list[str]:
    """Drop duplicates and empty tags, keeping the order."""
    seen: set[str] = set()
    result = []
    for tag in tags:
        if tag and tag not in seen and split(tag)[1]:
            seen.add(tag)
            result.append(tag)
    return result


# ---------------------------------------------------------------------------- favorites

# Colours given in turn to new favorites (each can be changed in Settings → Favorite tags…)
DEFAULT_COLORS = ("#C2185B", "#7B1FA2", "#1565C0", "#00897B", "#EF6C00", "#6D4C41",
                  "#C62828", "#2E7D32", "#455A64", "#AD1457", "#283593", "#00838F")
_COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")


def default_color(index: int) -> str:
    """Colour of the index-th favorite when the user did not choose one."""
    return DEFAULT_COLORS[index % len(DEFAULT_COLORS)]


def text_color(background: str) -> str:
    """Black or white: whichever reads better on the background colour."""
    try:
        red, green, blue = (int(background[i:i + 2], 16) for i in (1, 3, 5))
    except (TypeError, ValueError):
        return "#FFFFFF"
    luminance = 0.299 * red + 0.587 * green + 0.114 * blue
    return "#1A1A1A" if luminance > 160 else "#FFFFFF"


def _favorite(tag: str, label: str, color: str, index: int, merged=()) -> dict | None:
    """One clean favorite entry (None when the tag is empty).

    merged: other tags that count as this favorite (duplicates and empty ones are dropped).
    """
    tag = normalize(tag)
    if not split(tag)[1]:
        return None
    label = " ".join(str(label or "").split()) or split(tag)[1]
    color = color if isinstance(color, str) and _COLOR_RE.match(color) else default_color(index)
    if isinstance(merged, str):
        merged = merged.split(",")
    extra = [t for t in _unique(normalize(str(t)) for t in (merged or [])) if t != tag]
    return {"tag": tag, "label": label, "color": color.upper(), "merged": extra}


def favorite_tags(fav: dict) -> list[str]:
    """Every tag of a favorite: its own one, then the merged ones."""
    return [fav.get("tag", "")] + list(fav.get("merged") or [])


def _add_favorite(favorites: list[dict], tags: list[str], label, color) -> None:
    """Append a favorite made of tags (first = main one); tags already used by a favorite are skipped."""
    used = {t for fav in favorites for t in favorite_tags(fav)}
    free = [t for t in _unique(normalize(str(t)) for t in tags) if t not in used]
    if free:
        fav = _favorite(free[0], label, color, len(favorites), free[1:])
        if fav:
            favorites.append(fav)


def parse_favorites(text: str) -> list[dict]:
    """Read favorites written as text, one per line ("tag" or "tag = label"; '#' starts a comment).
    Tags separated by commas are merged into one favorite (the first one is the main tag).

        netorare, netorase, netori = Netorare
        ai generated = AI
        female:big breasts = Big Boobs
    """
    favorites: list[dict] = []
    for raw in (text or "").splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        tags, _, label = line.partition("=")
        _add_favorite(favorites, tags.split(","), label, "")
    return favorites


def favorites_text(favorites: list[dict]) -> str:
    """Favorites as text (inverse of parse_favorites; colours are not kept)."""
    lines = []
    for fav in favorites or []:
        tag, label = fav.get("tag", ""), fav.get("label", "")
        tags = ", ".join(favorite_tags(fav))
        lines.append(tags if not label or label == split(tag)[1] else f"{tags} = {label}")
    return "\n".join(lines)


def clean_favorites(value) -> list[dict]:
    """Favorites read from settings.json or sent by a window (anything not valid is dropped)."""
    if not isinstance(value, list):
        return []
    favorites: list[dict] = []
    for item in value:
        if isinstance(item, dict):
            merged = item.get("merged") or []
            if isinstance(merged, str):
                merged = merged.split(",")
            if not isinstance(merged, list):
                merged = []
            _add_favorite(favorites, [item.get("tag", "")] + merged, item.get("label", ""), item.get("color", ""))
        else:
            tags, _, label = str(item).partition("=")
            _add_favorite(favorites, tags.split(","), label, "")
    return favorites


def set_favorites(favorites: list[dict]) -> None:
    """Favorite tags in use (in priority order)."""
    global _favorites  # pylint: disable=global-statement
    with _lock:
        _favorites = clean_favorites(favorites)


def get_favorites() -> list[dict]:
    """Favorite tags in use."""
    with _lock:
        return [dict(fav) for fav in _favorites]


def badge_width() -> int:
    """Width of the tag column: the longest favorite label (at least 4 characters)."""
    return max([4] + [len(badge_text(fav["label"])) for fav in get_favorites()])


def badge_color(badge: str) -> str:
    """Colour of the favorite shown as badge ('' when it is not a favorite any more)."""
    for fav in get_favorites():
        if badge_text(fav["label"]) == badge:
            return fav["color"]
    return ""


def set_folders_enabled(enabled: bool) -> None:
    """Settings → "Sort downloads into folders by favorite tag"."""
    global _folders_enabled  # pylint: disable=global-statement
    _folders_enabled = bool(enabled)


def folders_enabled() -> bool:
    """True when finished albums go into a sub-folder named after their favorite tag."""
    return _folders_enabled


def set_nested_folders(enabled: bool) -> None:
    """Favorite tags window → "Nest the folders by priority"."""
    global _nested_folders  # pylint: disable=global-statement
    _nested_folders = bool(enabled)


def nested_folders() -> bool:
    """True when every favorite of a gallery is a folder level (in priority order)."""
    return _nested_folders


def _matches(favorite: str, tag: str) -> bool:
    """True when a gallery tag is the favorite one."""
    fav_ns, fav_name = split(favorite)
    tag_ns, tag_name = split(tag)
    return fav_name == tag_name and (not fav_ns or fav_ns == tag_ns)


def favorites_for(tags: list[str] | None) -> list[dict]:
    """Every favorite found among the tags of a gallery, in priority order (merged tags included)."""
    if not tags:
        return []
    normalized = [normalize(tag) for tag in tags]
    return [fav for fav in get_favorites()
            if any(_matches(own, tag) for own in favorite_tags(fav) for tag in normalized)]


def favorite_for(tags: list[str] | None) -> dict | None:
    """The first favorite (lowest position = highest priority) found in a gallery, or None."""
    found = favorites_for(tags)
    return found[0] if found else None


def favorites_summary(favorites: list[dict]) -> str:
    """Favorites for the log, in priority order: "1 GENDER BENDER (+gender morph, gender change), 2 NETORARE"."""
    parts = []
    for position, fav in enumerate(favorites or [], 1):
        text = f"{position} {badge_text(fav.get('label', ''))}"
        if fav.get("merged"):
            text += f" (+{', '.join(fav['merged'])})"
        parts.append(text)
    return ", ".join(parts) or "none"


def nested_log_line(nested: bool) -> str:
    """Log line of the "Nest the folders by priority" option."""
    if nested:
        return ("Nested folders ON: every favorite of a gallery is a folder level, in priority order "
                f"(for example E-Hentai/Gender Bender/Netorare/; at most {NEST_MAX_DEPTH} levels).")
    return "Nested folders OFF: a gallery goes into the folder of its favorite with the highest priority."


def badge_text(label: str) -> str:
    """Label of the tag column: capitals, at most BADGE_MAX characters, no '|'."""
    text = " ".join((label or "").replace("|", "/").split()).upper()
    return text[:BADGE_MAX].strip()


def badge_for(tags: list[str] | None) -> str:
    """Tag column of a gallery ('' when none of its tags is a favorite)."""
    fav = favorite_for(tags)
    return badge_text(fav["label"]) if fav else ""


def folder_parts_for(tags: list[str] | None) -> list[str]:
    """Favorite-tag sub-folders of a gallery, outermost first ([] = none, or the option is off).

    One folder (the favorite with the highest priority), or one level per favorite of the gallery
    in priority order with "Nest the folders by priority" (at most NEST_MAX_DEPTH).
    """
    if not _folders_enabled:
        return []
    found = favorites_for(tags)
    if not _nested_folders:
        found = found[:1]
    parts: list[str] = []
    for fav in found:
        label = fav["label"].strip()
        if label and label not in parts:
            parts.append(label)
    return parts[:NEST_MAX_DEPTH]


def folder_for(tags: list[str] | None) -> str:
    """Favorite-tag sub-folder of a gallery as text ("Gender Bender/Netorare"; '' = none)."""
    return "/".join(folder_parts_for(tags))


# ---------------------------------------------------------------------------- tags seen

# Common content tags offered in the favorite tags window before any download (marked
# "suggested"); every tag of the galleries verified or downloaded is added to them.
SUGGESTED_TAGS = (
    "netorare", "cheating", "big breasts", "gender change", "gender morph", "gender bender",
    "ai generated", "full color", "sole female", "sole male", "milf", "glasses", "stockings",
    "femdom", "futanari", "yuri", "yaoi", "harem", "monster girl", "elf", "dark skin", "tentacles",
    "mind control", "body swap", "transformation", "crossdressing", "maid", "bunny girl",
    "swimsuit", "uncensored", "x-ray", "ahegao", "paizuri", "big ass", "exhibitionism",
)

_seen_lock = threading.RLock()
_seen: dict[str, int] = {}          # tag name -> galleries in which it was seen
_seen_path = ""                     # file read (empty: not read yet)
_galleries: dict[str, list[str]] = {}  # gallery id -> its tags (the lists use them after a restart)


def _tags_path() -> Path:
    return Path(config.TAGS_FILE)


def _ensure_seen() -> None:
    """Read tags.json the first time it is needed (lock held)."""
    global _seen_path  # pylint: disable=global-statement
    path = _tags_path()
    if _seen_path == str(path):
        return
    _seen_path = str(path)
    _seen.clear()
    _galleries.clear()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return
    except (OSError, ValueError) as err:
        logging.warning(f"Could not read {path.name}: {err}")
        return
    galleries = data.get("galleries") if isinstance(data, dict) else None
    if isinstance(galleries, dict):
        for key, tags in galleries.items():
            if isinstance(tags, list):
                _galleries[str(key)] = [normalize(str(tag)) for tag in tags if str(tag).strip()]
    seen = data.get("seen") if isinstance(data, dict) else None
    if isinstance(seen, dict):
        for name, count in seen.items():
            if isinstance(count, int) and count > 0 and split(name)[1]:
                _seen[split(name)[1]] = count


def _save_seen() -> None:
    """Write tags.json (lock held, atomic replace)."""
    path = _tags_path()
    tmp = path.with_name(f"{path.name}.part")
    ordered = dict(sorted(_seen.items(), key=lambda item: (-item[1], item[0])))
    try:
        tmp.write_text(json.dumps({"seen": ordered, "galleries": _galleries}, ensure_ascii=False,
                                  separators=(",", ":")), encoding="utf-8")
        os.replace(tmp, path)
    except OSError as err:
        logging.warning(f"Could not save {path.name}: {err}")


def record_gallery(gallery_key: str, tags: list[str] | None) -> None:
    """Remember the tags of a gallery; the tag counts grow only the first time a gallery is met."""
    normalized = _unique(normalize(tag) for tag in tags or [])
    if not normalized or not gallery_key:
        return
    with _seen_lock:
        _ensure_seen()
        first_time = gallery_key not in _galleries
        if not first_time:  # met before: only new tags are added
            normalized = _galleries[gallery_key] + [t for t in normalized if t not in _galleries[gallery_key]]
            if normalized == _galleries[gallery_key]:
                return
        _galleries[gallery_key] = normalized
        if first_time:
            for name in {split(tag)[1] for tag in normalized if is_content_tag(tag)}:
                _seen[name] = _seen.get(name, 0) + 1
        _save_seen()


def cached_tags(gallery_key: str) -> list[str]:
    """Tags of a gallery met before (also in earlier runs); [] if unknown."""
    with _seen_lock:
        _ensure_seen()
        return list(_galleries.get(gallery_key, []))


def catalog() -> list[dict]:
    """Every tag of the favorite tags window: the favorites first (in priority order), then the
    tags seen in the galleries (most frequent first), then the suggested ones never seen.

    [{"tag", "label", "color", "favorite", "merged", "seen", "suggested"}]; the merged tags of a
    favorite are not listed again on their own.
    """
    with _seen_lock:
        _ensure_seen()
        seen = dict(_seen)
        galleries = list(_galleries.values())
    favorites = get_favorites()
    rows = []
    listed: set[str] = set()
    for fav in favorites:
        name = split(fav["tag"])[1]
        own = favorite_tags(fav)
        if fav["merged"]:  # galleries with any of the merged tags (each counted once)
            count = sum(1 for tags in galleries
                        if any(_matches(t, tag) for t in own for tag in tags if is_content_tag(tag)))
            count = count or sum(seen.get(split(t)[1], 0) for t in own)
        else:
            count = seen.get(name, 0)
        rows.append({"tag": fav["tag"], "label": fav["label"], "color": fav["color"], "favorite": True,
                     "merged": list(fav["merged"]), "seen": count,
                     "suggested": name in SUGGESTED_TAGS and not count})
        for tag in own:
            listed.add(tag)
            listed.add(split(tag)[1])
    for name, count in sorted(seen.items(), key=lambda item: (-item[1], item[0])):
        if name not in listed:
            rows.append({"tag": name, "label": name, "color": "", "favorite": False, "merged": [],
                         "seen": count, "suggested": False})
            listed.add(name)
    for name in sorted(SUGGESTED_TAGS):
        if name not in listed:
            rows.append({"tag": name, "label": name, "color": "", "favorite": False, "merged": [], "seen": 0,
                         "suggested": True})
    return rows


def seen_of(tag: str) -> int:
    """In how many galleries a tag was met (by name, whatever the namespace)."""
    with _seen_lock:
        _ensure_seen()
        return _seen.get(split(tag)[1], 0)


def seen_count() -> int:
    """How many different tags were met in the galleries."""
    with _seen_lock:
        _ensure_seen()
        return len(_seen)
