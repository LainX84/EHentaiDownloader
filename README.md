# E-Hentai Downloader

> A Python tool to download E-Hentai and ExHentai galleries, with a simple desktop GUI and a command-line interface.

![Demo](assets/demo.gif)

> This project is a fork of [Lysagxra/EHentaiDownloader](https://github.com/Lysagxra/EHentaiDownloader), extended with a GUI, image format conversion, sequential renaming, smarter retries and more. It is distributed under the same license (GPL-3.0).

---

## Features

- 🖥️ **Desktop GUI** (Tkinter): single, batch and retry modes, live progress bars and a coloured log. Start it by double-clicking `EHentaiDownloader.pyw`.
- 🖼️ **Image format conversion**
  - **AUTO** (default): static images are converted to `.jpg`, animated images (animated GIF/WebP) keep their original format.
  - **Native**: save exactly what the server sends.
  - **JPG / PNG / WEBP**: force one format (animations are reduced to their first frame).
  - Uses **ImageMagick** when installed, otherwise **Pillow**.
- 🔢 **Sequential renaming**: when an album is fully downloaded, images are renamed `01…N`. The number of digits depends on the image count: up to 99 → 2 digits (`01`), up to 999 → 3 (`001`), up to 9999 → 4 (`0001`)… Toggle it with the **Rename 01…N** button in the GUI header.
- ✅ **Completeness check**: an album counts as complete only if every page is really on disk; otherwise it goes to the retry queue.
- 🛟 **Resilient downloads**
  - Network errors, timeouts and HTTP 429/5xx are retried with increasing waits.
  - If an image server is broken, another one is requested through the gallery's "reload" link.
  - The 509 "image quota exceeded" placeholder is detected: it is never saved as a page, and the album is queued for retry.
- ⚡ **Parallel downloads** with a thread pool and short courtesy pauses to avoid rate limits.
- ♻️ **Resume**: images already on disk (also from earlier runs or renamed files) are skipped.
- 📋 **Batch mode** from `URLs.txt` and 🔄 **retry queue** in `failed_downloads.txt`.
- 🗂️ **Log rotation**: `session.log` rotates automatically (`session.log.1` … `session.log.3`).

---

## Requirements

- **Python 3.10+** (with Tkinter, included in the official Windows/macOS installers)
- Python packages: `beautifulsoup4`, `requests`, `rich`, `Pillow`
- *(Optional)* [ImageMagick](https://imagemagick.org/script/download.php) in the system `PATH` for faster conversions

---

## Installation

```bash
git clone https://github.com/LainX84/EHentaiDownloader.git
cd EHentaiDownloader
pip install -r requirements.txt
```

---

## ExHentai cookies (optional)

E-Hentai galleries work without logging in. ExHentai galleries and some restricted galleries need your account cookies.

1. Log in to e-hentai.org / exhentai.org in your browser.
2. Open the browser developer tools → *Storage* / *Application* → *Cookies*.
3. Copy `cookies.example.json` to `cookies.json` and fill in your values:

```json
{
  "ipb_member_id": "your_member_id",
  "ipb_pass_hash": "your_pass_hash",
  "igneous": "your_igneous_value"
}
```

A Netscape-format `cookies.txt` (exported by browser extensions) also works.

> ⚠️ **Never share or commit `cookies.json` / `cookies.txt`**: they give full access to your account. Both are already listed in `.gitignore`.

---

## Usage

### 1. GUI

Double-click **`EHentaiDownloader.pyw`** (Windows opens it without a console window), or run:

```bash
python EHentaiDownloader.pyw
```

| Area | What it does |
| :--- | :--- |
| **Image Format** | Output format for all downloads (see the table below). |
| **Rename 01…N** | ON/OFF: rename images `01…N` when an album is complete. |
| **Open Downloads** | Opens the `Downloads` folder. |
| **Single Download** tab | Paste one gallery URL and press *Start Download*. |
| **Batch Download** tab | Editor for `URLs.txt`: one URL per line, lines starting with `#` are skipped. |
| **Retry Failed** tab | Albums from `failed_downloads.txt`; press *Retry Failed* to try them again. |
| **Log** | Live events; *Open Log File* opens `session.log`. |

### 2. Command line

```bash
# Single gallery
python downloader.py https://e-hentai.org/g/3392858/1a77348e16/

# Every URL in URLs.txt (one per line), plus albums waiting in the retry queue
python main.py

# Retry only the albums in failed_downloads.txt
python retry.py
```

The CLI uses `DEFAULT_IMAGE_FORMAT` and `RENAME_SEQUENTIAL` from `src/config.py`.

---

## Image formats

| Mode | Result |
| :--- | :--- |
| **AUTO** (default) | Static images → `.jpg`; animated GIF/WebP keep their format and animation. |
| **Native (Original)** | Whatever the server provides (`.webp`, `.jpg`, `.png`, `.gif`…), no conversion. |
| **JPG** | Everything converted to JPEG (animations keep only the first frame). |
| **PNG** | Everything converted to PNG (animations keep only the first frame). |
| **WEBP** | Everything converted to WebP. |

---

## Configuration

All settings are in [`src/config.py`](src/config.py):

| Setting | Default | Meaning |
| :--- | :--- | :--- |
| `DOWNLOAD_FOLDER` | `Downloads` | Where albums are saved (one sub-folder per album). |
| `MAX_WORKERS` | `5` | Images downloaded in parallel. |
| `DEFAULT_IMAGE_FORMAT` | `auto` | Format used by the command-line scripts. |
| `RENAME_SEQUENTIAL` | `True` | Rename images `01…N` when an album is complete (initial state of the GUI button). |
| `IMAGE_SERVER_RETRIES` | `3` | Different image servers tried per page. |
| `IMAGE_FETCH_RETRIES` | `3` | Attempts on the same image server before switching. |
| `PAGE_FETCH_RETRIES` | `3` | Attempts for HTML pages on temporary errors. |
| `ALBUM_PAGE_DELAY` | `(0.5, 1.5)` | Random pause (seconds) between gallery pages. |
| `CONNECTION_TIMEOUT` | `30` | Network timeout in seconds. |
| `RATE_LIMIT_SLEEPING_TIME` | `60` | Wait in seconds after a rate-limit response. |
| `SESSION_LOG_MAX_BYTES` / `SESSION_LOG_BACKUPS` | `5 MB` / `3` | Log rotation size and number of old logs kept. |
| `USER_AGENT` | Firefox | Browser identity sent with every request. |

---

## How downloading works

1. The gallery page is read to get the title, the number of images and the list of gallery pages.
2. For every image page, the direct image link is taken from the image viewer page.
3. Images are downloaded in parallel, converted if needed, and saved as `NN_originalname.ext` while the album is in progress.
4. When nothing failed and every page is on disk, the folder is renamed to the clean gallery title and, if enabled, the images are renamed `01…N`.
5. Albums with missing images are written to `failed_downloads.txt` for a later retry; images already downloaded are not downloaded again.

---

## Troubleshooting

| Message in the log | Meaning / fix |
| :--- | :--- |
| `Image limit … 509` | Your E-Hentai image quota is exhausted. Wait (or reset it from your account), then use *Retry Failed*. |
| `cookies rejected … igneous=mystery` / `Sad Panda` | ExHentai cookies missing, expired or wrong: update `cookies.json`. |
| `IP restricted or temporarily banned` | Too many requests: wait before trying again, and consider lowering `MAX_WORKERS`. |
| `Rename skipped` | Some pages were missing or duplicated on disk, so images were left with their download names. |

---

## Project structure

```
EHentaiDownloader/
├── EHentaiDownloader.pyw    # GUI (double-click to start on Windows)
├── downloader.py            # CLI: single gallery
├── main.py                  # CLI: batch download from URLs.txt
├── retry.py                 # CLI: retry failed_downloads.txt
├── cookies.example.json     # Template for your ExHentai cookies
├── requirements.txt
├── assets/                  # README images
└── src/
    ├── config.py            # All settings
    ├── file_utils.py        # Files, folder names, session log + rotation
    ├── general_utils.py     # HTTP session, cookies, page fetching with retries & diagnostics
    ├── crawler/
    │   ├── crawler.py       # Gallery parsing: title, image count, page list
    │   └── crawler_utils.py # Link helpers ("reload" token, picture pages)
    ├── downloader/
    │   ├── album_downloader.py  # Download, conversion, completeness check, renaming
    │   └── download_utils.py    # Image requests with retries and backoff
    └── managers/            # Rich terminal display used by the CLI
```

Files created at runtime (and ignored by git): `Downloads/`, `URLs.txt`, `failed_downloads.txt`, `session.log*`, `cookies.json`.

---

## Disclaimer

This tool is for personal use. Respect the website's terms of service and do not overload its servers.

## License

Released under the [GNU General Public License v3.0](LICENSE), like the original project by [Lysagxra](https://github.com/Lysagxra/EHentaiDownloader).
