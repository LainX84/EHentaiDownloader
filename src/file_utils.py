"""Module that provides utility functions for file input and output operations.

It includes methods to read the contents of a file and to write content to a file, with
optional support for clearing the file.
"""

import datetime
import logging
import os
import re
import sys
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


def sanitize_directory_name(directory_name: str, max_length: int = 150) -> str:
    """Sanitize a given directory name and ensure it does not exceed filesystem limits.

    Replace invalid characters with underscores and limit total length on Windows.
    """
    invalid_chars_dict = {
        "nt": r'[\\/:*?"<>|\x00-\x1f]',  # Windows
        "posix": r"[/:\x00]",            # macOS and Linux
    }
    invalid_chars = invalid_chars_dict.get(os.name, r'[\\/:*?"<>|\x00-\x1f]')
    sanitized = re.sub(invalid_chars, "_", directory_name).strip(". ")

    # Collapse multiple consecutive underscores or spaces
    sanitized = re.sub(r"_+", "_", sanitized).strip("._ ")

    # Truncate length to avoid OS path limit errors (MAX_PATH / WinError 123)
    if len(sanitized) > max_length:
        suffix_match = re.search(r"(_g_\d+_[a-f0-9]+)$", sanitized)
        if suffix_match:
            suffix = suffix_match.group(1)
            prefix_len = max(10, max_length - len(suffix) - 1)
            sanitized = f"{sanitized[:prefix_len].rstrip(' ._')}_{suffix}"
        else:
            sanitized = sanitized[:max_length].rstrip(" ._")

    return sanitized


def create_download_directory(directory_name: str) -> str:
    """Create a directory for downloads if it doesn't exist."""
    clean_name = sanitize_directory_name(directory_name)
    download_path = Path(DOWNLOAD_FOLDER) / clean_name

    try:
        download_path.mkdir(parents=True, exist_ok=True)
    except OSError as os_err:
        message = f"Error creating directory: {os_err}"
        logging.exception(message)
        raise

    return str(download_path)
