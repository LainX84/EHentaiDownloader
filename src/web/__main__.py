"""Start the web interface: python -m src.web

The download code uses paths relative to the current folder and the constants of
src.config, so the folders are set here, before any other module of the program is
imported (they copy the constants when they are imported).
"""

from __future__ import annotations

import os
from pathlib import Path


def _setup_folders() -> tuple[Path, Path]:
    """Create the data / config folders, move into the data folder and point src.config at them."""
    data_dir = Path(os.environ.get("EHD_DATA_DIR") or "data").resolve()
    config_dir = Path(os.environ.get("EHD_CONFIG_DIR") or data_dir).resolve()
    data_dir.mkdir(parents=True, exist_ok=True)
    config_dir.mkdir(parents=True, exist_ok=True)
    os.chdir(data_dir)  # temp/, E-Hentai/, nhentai/ and cookies.json are relative to it

    from src import config  # pylint: disable=import-outside-toplevel

    config.DOWNLOAD_FOLDER = "."  # E-Hentai/ and nhentai/ directly in the data folder
    config.URLS_FILE = str(config_dir / "URLs.txt")
    config.FAILED_URLS_FILE = str(config_dir / "failed_downloads.txt")
    config.SETTINGS_FILE = str(config_dir / "settings.json")
    config.SESSION_LOG = str(config_dir / "session.log")
    return data_dir, config_dir


def main() -> None:
    """Run the web server (uvicorn) on EHD_HOST:EHD_PORT (default 0.0.0.0:8000)."""
    data_dir, config_dir = _setup_folders()

    import uvicorn  # pylint: disable=import-outside-toplevel

    from src.web.server import create_app  # pylint: disable=import-outside-toplevel

    app = create_app(data_dir, config_dir)
    uvicorn.run(
        app,
        host=os.environ.get("EHD_HOST", "0.0.0.0"),  # noqa: S104 (inside a container)
        port=int(os.environ.get("EHD_PORT", "8000")),
        log_level="warning",
        access_log=False,
    )


if __name__ == "__main__":
    main()
