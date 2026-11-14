"""HTTP API and static page of the web interface (FastAPI).

The page (static/index.html) asks /api/state about once a second and calls the other
endpoints for the buttons. There is no login: the container is meant for the home network
only (publish the port on the LAN address of the host, not on the internet).
"""

from __future__ import annotations

import platform
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from src import config, paths, throttle
from src.texts import APP_NAME, DISCLAIMER_TEXT, DISCLAIMER_TITLE
from src.web.engine import FORMAT_OPTIONS, Engine, EngineError

STATIC_DIR = Path(__file__).resolve().parent / "static"

WEB_THIRD_PARTY_TEXT = """Third-party software used by this program (each under its own license):
  • Python – PSF License
  • FastAPI, Starlette, Pydantic, Uvicorn – MIT / BSD
  • requests – Apache 2.0
  • Beautiful Soup – MIT
  • Pillow – MIT-CMU (HPND)
  • Rich – MIT
"""


class UrlBody(BaseModel):
    """A gallery link."""
    url: str = ""


class TextBody(BaseModel):
    """Any text (pasted links, list text, cookies.json)."""
    text: str = ""


class StartBody(BaseModel):
    """Options of Start Batch / Verify Links chosen in the confirmations."""
    remove_duplicates: bool = False


class CookiesPasteBody(BaseModel):
    """Cookies pasted from the browser, and the optional nhentai API key."""
    text: str = ""
    nhentai_api_key: str | None = None


class DisclaimerBody(BaseModel):
    """Notice accepted; snooze hides it for DISCLAIMER_SNOOZE_DAYS days."""
    snooze: bool = False


def create_app(data_dir: Path, config_dir: Path) -> FastAPI:
    """Build the FastAPI application around one Engine."""
    engine = Engine(data_dir, config_dir)
    app = FastAPI(title=APP_NAME, docs_url=None, redoc_url=None, openapi_url=None)

    def run(func, *args):
        """Call an engine method; EngineError becomes HTTP 409 with its message."""
        try:
            return func(*args)
        except EngineError as err:
            raise HTTPException(status_code=409, detail=str(err)) from err

    @app.get("/api/info")
    def info() -> dict:
        """Fixed data of the page: name, version, links, options, disclaimer."""
        return {
            "name": APP_NAME,
            "version": config.APP_VERSION,
            "project_url": config.PROJECT_URL,
            "issues_url": config.ISSUES_URL,
            "upstream_url": config.UPSTREAM_URL,
            "formats": list(FORMAT_OPTIONS),
            "speeds": [{"level": level, "name": name, "workers": workers}
                       for level, (name, _, workers) in sorted(throttle.SPEED_LEVELS.items())],
            "disclaimer": {"title": DISCLAIMER_TITLE, "text": DISCLAIMER_TEXT,
                           "needed": engine.disclaimer_needed(), "snooze_days": config.DISCLAIMER_SNOOZE_DAYS},
            "about": {
                "data_dir": str(data_dir), "config_dir": str(config_dir),
                "python": platform.python_version(),
                "system": f"{platform.system()} {platform.release()}",
            },
        }

    @app.get("/api/state")
    def state(since: int = 0, epoch: int = -1, lines_rev: int = -1, retry_rev: int = -1) -> dict:
        """Snapshot of the state (lists and log only when they changed)."""
        return engine.snapshot(since, epoch, lines_rev, retry_rev)

    @app.post("/api/disclaimer")
    def disclaimer(body: DisclaimerBody) -> dict:
        engine.accept_disclaimer(body.snooze)
        return {"ok": True}

    @app.post("/api/settings")
    def settings(body: dict) -> dict:
        return run(engine.change_settings, body)

    # downloads
    @app.post("/api/single")
    def single(body: UrlBody) -> dict:
        run(engine.start_single, body.url)
        return {"ok": True}

    @app.post("/api/batch/check")
    def batch_check() -> dict:
        return run(engine.find_problems)

    @app.post("/api/batch/start")
    def batch_start(body: StartBody) -> dict:
        run(engine.start_batch, body.remove_duplicates)
        return {"ok": True}

    @app.post("/api/batch/verify")
    def batch_verify(body: StartBody) -> dict:
        run(engine.start_verify, body.remove_duplicates)
        return {"ok": True}

    @app.post("/api/retry/start")
    def retry_start() -> dict:
        run(engine.start_retry)
        return {"ok": True}

    @app.post("/api/pause")
    def pause() -> dict:
        return {"paused": run(engine.toggle_pause)}

    @app.post("/api/resume-ban")
    def resume_ban() -> dict:
        engine.resume_after_ban()
        return {"ok": True}

    # batch list
    @app.post("/api/batch/add")
    def batch_add(body: TextBody) -> dict:
        return run(engine.add_links, body.text, "Paste")

    @app.post("/api/batch/text")
    def batch_text(body: TextBody) -> dict:
        run(engine.set_list_text, body.text)
        return {"ok": True}

    @app.delete("/api/batch/line/{line_id}")
    def batch_delete_line(line_id: int) -> dict:
        run(engine.delete_line, line_id)
        return {"ok": True}

    @app.post("/api/batch/reload")
    def batch_reload() -> dict:
        run(engine.reload_urls_txt)
        return {"ok": True}

    @app.post("/api/batch/clear")
    def batch_clear() -> dict:
        run(engine.clear_list)
        return {"ok": True}

    @app.post("/api/batch/dedupe")
    def batch_dedupe() -> dict:
        return {"removed": run(engine.remove_duplicates)}

    @app.post("/api/batch/sort")
    def batch_sort(body: dict) -> dict:
        if body.get("accept"):
            return {"sorted": run(engine.sort_by_size)}
        engine.dismiss_sort()
        return {"sorted": False}

    # retry list
    @app.post("/api/retry/reload")
    def retry_reload() -> dict:
        engine.reload_retry_list()
        return {"ok": True}

    @app.post("/api/retry/clear")
    def retry_clear() -> dict:
        run(engine.clear_retry_queue)
        return {"ok": True}

    # log
    @app.post("/api/log/clear")
    def log_clear() -> dict:
        engine.clear_log()
        return {"ok": True}

    @app.get("/api/log/file", response_class=PlainTextResponse)
    def log_file() -> str:
        return engine.session_log_tail()

    # account
    @app.get("/api/cookies")
    def cookies() -> dict:
        return engine.cookies_info()

    @app.post("/api/cookies/paste")
    def cookies_paste(body: CookiesPasteBody) -> dict:
        return run(engine.save_cookies_paste, body.text, body.nhentai_api_key)

    @app.post("/api/cookies/raw")
    def cookies_raw(body: TextBody) -> dict:
        run(engine.save_cookies_raw, body.text)
        return {"ok": True}

    # files
    @app.get("/api/files")
    def files() -> dict:
        return engine.list_files()

    @app.get("/api/files/download")
    def file_download(folder: str, name: str) -> FileResponse:
        path = run(engine.file_path, folder, name)
        return FileResponse(path, filename=path.name)

    # help
    @app.post("/api/updates/check")
    def updates_check() -> dict:
        try:
            return engine.check_updates()
        except Exception as err:  # pylint: disable=broad-except
            raise HTTPException(status_code=502, detail=f"Could not reach GitHub: {err}") from err

    @app.get("/api/license", response_class=PlainTextResponse)
    def license_text() -> str:
        try:
            text = paths.resource("LICENSE").read_text(encoding="utf-8")
        except OSError:
            text = f"GPL-3.0 – see {config.PROJECT_URL}/blob/main/LICENSE"
        return f"{WEB_THIRD_PARTY_TEXT}\n{text}"

    @app.exception_handler(HTTPException)
    async def http_error(_request, exc: HTTPException) -> JSONResponse:
        return JSONResponse({"error": exc.detail}, status_code=exc.status_code)

    @app.get("/")
    def index() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html", headers={"Cache-Control": "no-cache"})

    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
    app.mount("/assets", StaticFiles(directory=paths.resource("assets")), name="assets")
    return app
