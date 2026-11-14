"""Web interface (Docker version): the same downloads as the desktop window, from a browser.

Start it with ``python -m src.web`` (the Docker image does it). Folders come from two
environment variables:

    EHD_DATA_DIR    E-Hentai/, nhentai/, temp/ and cookies.json   (default: ./data)
    EHD_CONFIG_DIR  settings.json, URLs.txt, failed_downloads.txt, session.log
                    (default: same as EHD_DATA_DIR)
"""
