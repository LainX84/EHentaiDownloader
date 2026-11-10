"""Utility modules and functions to support the main application.

It includes tools for media crawling, URL handling, and other related tasks.

Modules:
    - album_downloader: Handles downloading of entire albums (E-Hentai / ExHentai).
    - nhentai_downloader: Same for nhentai.net galleries.
    - download_utils: Provides utility functions for downloading tasks.

This package is modular and reusable, enabling its components to be easily imported and
integrated into various parts of the application.
"""

# downloaders/__init__.py

__all__ = [
    "album_downloader",
    "nhentai_downloader",
    "download_utils",
]
