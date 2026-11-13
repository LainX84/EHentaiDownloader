"""Module that provides utility functions for file input and output operations.

It includes methods to read the contents of a file and to write content to a file, with
optional support for clearing the file.
"""

import datetime
import logging
import re
import threading
from pathlib import Path

from .config import DOWNLOAD_FOLDER, SESSION_LOG, SESSION_LOG_BACKUPS, SESSION_LOG_MAX_BYTES

_log_lock = threading.Lock()


def read_file(filename: str) -> list[str]:
    """Read the contents of a file and returns a list of its lines."""
    with Path(filename).open("r", encoding="utf-8") as file:
        return file.read().splitlines()


def write_file(filename: str, content: str = "") -> None:
    """Write content to a specified file.

    If content is not provided, the file is cleared.
    """
    with Path(filename).open("w", encoding="utf-8") as file:
        file.write(content)


def rotate_session_log(force: bool = False) -> None:
    """Rotate session.log -> session.log.1 -> ... when too big (or always if force=True)."""
    log_path = Path(SESSION_LOG)
    with _log_lock:
        try:
            if not log_path.exists() or log_path.stat().st_size == 0:
                return
            if not force and log_path.stat().st_size < SESSION_LOG_MAX_BYTES:
                return
            oldest = Path(f"{SESSION_LOG}.{SESSION_LOG_BACKUPS}")
            if oldest.exists():
                oldest.unlink()
            for n in range(SESSION_LOG_BACKUPS - 1, 0, -1):
                src = Path(f"{SESSION_LOG}.{n}")
                if src.exists():
                    src.rename(f"{SESSION_LOG}.{n + 1}")
            log_path.rename(f"{SESSION_LOG}.1")
        except OSError as err:
            logging.warning(f"Error rotating {SESSION_LOG}: {err}")


def append_session_log(event: str, details: str, dt: datetime.datetime | None = None) -> None:
    """Append a timestamped log entry with date and time to the session log file."""
    if dt is None:
        dt = datetime.datetime.now()
    timestamp_str = dt.strftime("%Y-%m-%d %H:%M:%S")
    clean_details = str(details).rstrip()
    line = f"[{timestamp_str}] [{event}] {clean_details}\n"
    try:
        with _log_lock, Path(SESSION_LOG).open("a", encoding="utf-8") as file:
            file.write(line)
    except Exception as err:
        logging.warning(f"Error writing to {SESSION_LOG}: {err}")


def append_session_banner(title: str, dt: datetime.datetime | None = None) -> None:
    """Append a section/lifecycle banner to the session log file."""
    if dt is None:
        dt = datetime.datetime.now()
    timestamp_str = dt.strftime("%Y-%m-%d %H:%M:%S")
    banner = f"\n[{timestamp_str}] {'=' * 15} {title} {'=' * 15}\n"
    rotate_session_log()
    try:
        with _log_lock, Path(SESSION_LOG).open("a", encoding="utf-8") as file:
            file.write(banner)
    except Exception as err:
        logging.warning(f"Error writing to {SESSION_LOG}: {err}")


def write_on_session_log(content: str) -> None:
    """Append content to the session log file (backward compatibility)."""
    append_session_log("INFO", content)


# Characters not allowed in Windows file names (also used on Linux / macOS, so a Downloads
# folder can be copied to an exFAT / NTFS drive or a Windows PC without errors).
_INVALID_NAME_CHARS = re.compile(r'[\\/:*?"<>|\x00-\x1f\x7f]')
# Names Windows reserves for devices, with or without an extension ("CON", "nul.txt", "COM1").
_RESERVED_NAMES = {"CON", "PRN", "AUX", "NUL"} | {f"{p}{i}" for p in ("COM", "LPT") for i in range(1, 10)}
# File-name limit of ext4, APFS, Btrfs... is 255 *bytes* (a CJK character takes 3 in UTF-8);
# NTFS allows 255 UTF-16 units, which is never fewer, so the byte limit works everywhere.
MAX_NAME_BYTES = 255


def _truncate_utf8(text: str, max_bytes: int) -> str:
    """Cut text to at most max_bytes when encoded as UTF-8, never splitting a character."""
    encoded = text.encode("utf-8")
    if len(encoded) <= max_bytes:
        return text
    return encoded[:max_bytes].decode("utf-8", errors="ignore")


def _fit(text: str, max_length: int, max_bytes: int) -> str:
    """Cut text to max_length characters and max_bytes UTF-8 bytes, without trailing dots/spaces."""
    if len(text) > max_length:
        text = text[:max_length]
    return _truncate_utf8(text, max_bytes).rstrip(" ._")


def sanitize_directory_name(directory_name: str, max_length: int = 150,
                            max_bytes: int = MAX_NAME_BYTES) -> str:
    """Make a gallery title safe as a file / folder name on Windows, macOS and Linux.

    The same rules are used on every system: characters invalid on Windows become "_",
    trailing dots and spaces are removed, Windows device names (CON, NUL, COM1...) get a "_"
    and the name is shortened to max_length characters and max_bytes bytes (UTF-8).
    """
    sanitized = _INVALID_NAME_CHARS.sub("_", directory_name or "")
    # Collapse multiple consecutive underscores, drop leading / trailing dots, spaces, "_"
    sanitized = re.sub(r"_+", "_", sanitized).strip("._ ")

    if len(sanitized) > max_length or len(sanitized.encode("utf-8")) > max_bytes:
        suffix_match = re.search(r"(_g_\d+_[a-f0-9]+)$", sanitized)
        if suffix_match:  # keep the "_g_<id>_<token>" ending that identifies the gallery
            suffix = suffix_match.group(1)
            prefix = _fit(sanitized[: -len(suffix)], max(10, max_length - len(suffix) - 1),
                          max(10, max_bytes - len(suffix.encode("utf-8")) - 1))
            sanitized = f"{prefix}_{suffix}"
        else:
            sanitized = _fit(sanitized, max_length, max_bytes)

    if sanitized.split(".", 1)[0].upper() in _RESERVED_NAMES:
        sanitized = f"{sanitized}_"
    return sanitized


def create_download_directory(directory_name: str, base_folder: str = DOWNLOAD_FOLDER) -> str:
    """Create a directory for downloads (inside base_folder) if it doesn't exist."""
    clean_name = sanitize_directory_name(directory_name)
    download_path = Path(base_folder) / clean_name

    try:
        download_path.mkdir(parents=True, exist_ok=True)
    except OSError as os_err:
        message = f"Error creating directory: {os_err}"
        logging.exception(message)
        raise

    return str(download_path)
