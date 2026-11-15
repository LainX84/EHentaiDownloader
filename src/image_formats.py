"""Image formats offered for the downloads.

Shared by the desktop window, the web page and the command line (no GUI imports here):
labels of the "Image Format" field, their internal names, and the notices shown when one is chosen.
"""

from __future__ import annotations

import time

from src.config import DEFAULT_IMAGE_FORMAT, FORMAT_NOTICE_SNOOZE_DAYS

# (label shown in the "Image Format" field, internal name), in the order of the field.
# "original" saves each image exactly as the server sends it, with no change at all.
FORMATS = (
    ("Native (Original)", "original"),
    ("WEBP (.webp)", "webp"),
    ("JPEG (.jpg)", "jpg"),
    ("JPEG XL (.jxl)", "jxl"),
    ("PNG (.png)", "png"),
    ("AVIF (.avif)", "avif"),
)
LABELS = tuple(label for label, _ in FORMATS)
NAMES = tuple(name for _, name in FORMATS)

# File extension written for each conversion format
EXTENSIONS = {"webp": ".webp", "jpg": ".jpg", "jxl": ".jxl", "png": ".png", "avif": ".avif"}

# Formats saved as still images only: animated images keep just their first frame.
# (JPEG XL can hold animations, but the library used to write it saves still images only.)
STATIC_ONLY = frozenset({"jpg", "jxl", "png"})

# Modern formats that many systems cannot open without extra codecs
NEEDS_CODECS = frozenset({"jxl", "avif"})

# Free Microsoft extensions that let Windows open them: (name, Microsoft Store product ID)
WINDOWS_EXTENSIONS = {
    "avif": ("AV1 Video Extension", "9MVZQVXJBQ9V"),
    "jxl": ("JPEG XL Image Extension", "9MZPRTH5C0TB"),
}

OPEN_FORMATS_NOTE = ("All the image formats offered (WebP, JPEG, JPEG XL, PNG, AVIF) are open standards "
                     "and royalty-free.")

_BY_LABEL = dict(FORMATS)
_BY_NAME = {name: label for label, name in FORMATS}
# Labels and names saved by older versions
_LEGACY_LABELS = {"JPG (.jpg)": "jpg"}  # "AUTO (JPG / Native if animated)" was removed: it falls back to the default
_ALIASES = {"jpeg": "jpg", "native": "original", "": "original"}


def normalize_name(name: str | None) -> str:
    """Internal format name from a name or alias ("JPEG", ".webp", "native"…); unknown ones give the default."""
    value = (name or "").lower().strip().lstrip(".")
    value = _ALIASES.get(value, value)
    return value if value in NAMES else DEFAULT_IMAGE_FORMAT


def name_from_label(label: str | None) -> str:
    """Internal format name of a label of the field (old labels included); unknown ones give the default."""
    if label in _BY_LABEL:
        return _BY_LABEL[label]
    return _LEGACY_LABELS.get(label or "", DEFAULT_IMAGE_FORMAT)


def label_for(name: str) -> str:
    """Label of the field for an internal format name."""
    return _BY_NAME[normalize_name(name)]


def normalize_label(label: str | None) -> str:
    """A saved label turned into a valid label of the field (old or unknown ones are converted)."""
    return label_for(name_from_label(label))


DEFAULT_LABEL = label_for(DEFAULT_IMAGE_FORMAT)


def notice(name: str) -> str:
    """Text to show when the format is chosen ("" if there is nothing to say)."""
    name = normalize_name(name)
    parts = []
    if name in STATIC_ONLY:
        reason = {
            "jpg": "JPEG cannot hold animations. ",
            "png": "PNG is saved as a still image (animated PNG is not supported by most viewers). ",
            "jxl": "JPEG XL can hold animations, but this program can only write still JPEG XL images. ",
        }[name]
        parts.append(
            f"{reason}Animated images (animated GIF / WebP) will lose their animation: "
            "only their first frame will be saved.\n"
            "To keep animations choose WEBP (default), AVIF or Native (Original)."
        )
    if name in NEEDS_CODECS:
        fmt = "JPEG XL" if name == "jxl" else "AVIF"
        windows = ('the free "JPEG XL Image Extension" from the Microsoft Store (Windows 11 24H2 or later)'
                   if name == "jxl" else 'the free "AV1 Video Extension" from the Microsoft Store')
        mac = "macOS 14 Sonoma" if name == "jxl" else "macOS 13 Ventura"
        lib = "libjxl" if name == "jxl" else "libavif"
        parts.append(
            f"{fmt} is a modern format that many systems do not open out of the box. The program converts "
            f"the images by itself, but to view them you need {fmt} support installed:\n"
            f"  • Windows: install {windows}.\n"
            f"  • Linux: install {lib} and the {fmt} plug-in of your image viewer, if it is missing.\n"
            f"  • macOS: supported from {mac}; older versions need a viewer that reads {fmt}.\n"
            "Comic readers and CBZ apps may not show these images either: check yours first."
        )
    return "\n\n".join(parts)


def store_page_url(product_id: str) -> str:
    """Web page of a Microsoft Store product (any browser, any system)."""
    return f"https://apps.microsoft.com/detail/{product_id.lower()}"


def store_app_url(product_id: str) -> str:
    """Link that opens only the small install window of the Microsoft Store app (Windows)."""
    return f"ms-windows-store://pdp/?ProductId={product_id}&mode=mini"


def product_id_from_url(url: str) -> str:
    """Product ID of a Microsoft Store page link ("" if the link is something else)."""
    prefix = "https://apps.microsoft.com/detail/"
    return url[len(prefix):].split("?")[0].split("/")[0].upper() if url.startswith(prefix) else ""


def windows_extension(name: str) -> tuple[str, str, str] | None:
    """(extension name, store page link, store app link) of the Windows extension a format needs."""
    ext = WINDOWS_EXTENSIONS.get(normalize_name(name))
    if not ext:
        return None
    title, product_id = ext
    return title, store_page_url(product_id), store_app_url(product_id)


def log_line(name: str) -> str:
    """Short reminder written in the log when the format is chosen ("" if there is nothing to say)."""
    name = normalize_name(name)
    parts = []
    if name in STATIC_ONLY:
        parts.append("animated images keep only their first frame")
    ext = windows_extension(name)
    if ext:
        parts.append(f"to view the images on Windows install the free {ext[0]}: {ext[1]}")
    return f"{label_for(name)}: " + "; ".join(parts) + "." if parts else ""


# ------------------------------------------------------------------ "Don't show again for 30 days"

def notice_hidden(settings: dict, name: str) -> bool:
    """True while the notice of this format is snoozed (settings["format_notice_hidden_until"])."""
    try:
        return float((settings.get("format_notice_hidden_until") or {}).get(normalize_name(name), 0)) > time.time()
    except (AttributeError, TypeError, ValueError):
        return False


def hide_notice(settings: dict, name: str) -> None:
    """Snooze the notice of this format for FORMAT_NOTICE_SNOOZE_DAYS days (the caller saves the settings)."""
    hidden = settings.get("format_notice_hidden_until")
    if not isinstance(hidden, dict):
        hidden = {}
    now = time.time()
    hidden = {k: v for k, v in hidden.items() if isinstance(v, (int, float)) and v > now}  # drop expired ones
    hidden[normalize_name(name)] = now + FORMAT_NOTICE_SNOOZE_DAYS * 86400
    settings["format_notice_hidden_until"] = hidden
