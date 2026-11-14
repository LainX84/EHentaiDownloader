# Changelog

All notable changes to this fork of EHentai Downloader. Versions follow the release tags
(`v<version>`); the ready-to-use apps of each version are on the
[releases page](https://github.com/LainX84/EHentaiDownloader/releases).

## [2.0.2] – 2026-11-14

### Added
- **Docker version with a web interface** (`docker/`, `src/web/`): the same downloads from a
  browser, for a home server. Single / Batch / Retry tabs, Verify Links, duplicates, pause, speed,
  image format, Auto Rename, CBZ / ComicInfo.xml, theme, log, disclaimer and update check work as
  in the desktop window. The downloads run in the container, so they go on with the browser
  closed and every open page shows the same state. `/data` holds `E-Hentai/`, `nhentai/`,
  `temp/` and `cookies.json`; `/config` the lists, settings and log. No login: meant for the
  home network only. Guide: [docker/README.md](docker/README.md).
  - *Account & cookies*: paste the cookies from the browser and `cookies.json` is written by
    itself; once it exists, its content is shown and can be edited directly.
  - Status bar counter of the images downloaded **today** (from 06:00 to 06:00, kept across
    restarts) instead of "this session".
  - At the end of a download the total bar goes back to empty after ~45 s (the result stays in
    the status line), so a page opened later does not show an old 100 %.
  - Not in the web version: drag and drop, clipboard watcher, *When done* and *Open Downloads*
    (the albums are in the `/data` folder of the server).
- **Docker images published automatically** (`.github/workflows/docker.yml`): every `v*` tag builds
  `linux/amd64` and `linux/arm64` images and pushes them to Docker Hub
  (`lainx84/ehentaidownloader`) and GitHub Packages (`ghcr.io/lainx84/ehentaidownloader`); the
  Docker Hub page text comes from `docker/README.md`.

### Changed
- **E-Hentai / ExHentai and nhentai download in parallel**: they are different servers with
  different rules, so *Start Batch* and *Retry Failed* keep one gallery of each site going at
  the same time and the queue empties faster. Galleries of the same site still download one
  after another. The batch list highlights both active lines, the status bar names both
  galleries, and the log lines of each album start with `[E-Hentai]` or `[nhentai]`.
- **One progress bar per site**: the old *Page Download* bar is now two bars,
  *E-Hentai/ExHentai* (yellow) and *nhentai* (red), under *Album Total* / *Batch Total*
  (green). In a batch or retry each bar shows the images of the album in progress on that
  site; in a single download the bar of the album's site shows the page being downloaded. A site
  with nothing to do shows *Idle*.
- Request pace and ban pause are now **per site**: an E-Hentai IP ban pauses only the E-Hentai
  downloads and an nhentai "Too Many Requests" only the nhentai ones; the status bar shows a
  countdown for each paused site and *Resume now* clears both.

## [2.0.1] – 2026-11-13

### Added
- **One folder per site**: completed albums (folders and `.cbz` files) go to `Downloads/E-Hentai/`
  (e-hentai.org and exhentai.org) or `Downloads/nhentai/`. Albums and archives made by older
  versions directly in `Downloads/` are still recognised, so they are not downloaded again.
- **Paste many links at once**: Ctrl+V in the batch list keeps only the gallery links of the
  copied text (a web page, a chat message, a forum post...) and skips galleries already listed.
  The *Paste* button of the Single tab offers to send several links to the batch list.
- **Drag and drop**: links, text or `.txt` files dropped on the window are added to the batch
  list (needs the `tkinterdnd2` package, now in `requirements.txt`; without it everything else
  works).
- **Clipboard watcher** (*Settings → Watch clipboard for links*, OFF by default): every gallery
  link you copy is added to the batch list.
- **Update check**: *Help → Check for updates now*, plus a quiet check at start-up, at most once
  a day (*Help → Check for updates at start-up*, ON by default). It only asks GitHub for the
  latest version: nothing is downloaded or installed.
- **Version shown** next to the program name at the top of the window (not only in *Help → About*).
- **ComicInfo.xml in CBZ** option (*Settings*, OFF by default; `CBZ_COMIC_INFO` for the CLI):
  new CBZ archives hold only the images unless it is turned on.
- **Image estimate** under the batch list (exact once the links are verified) and a status bar
  counter of the images downloaded since the program was opened.
- **Automatic tests** (`tests/`, run with `python -m pytest`) for file names, links, ban pages,
  update check and download folders.

### Changed
- File and folder names follow the same rules on Windows, macOS and Linux: characters not
  allowed on Windows (`\ / : * ? " < > |`) become `_` and Windows device names (`CON`, `NUL`,
  `COM1`...) get a `_`, so the `Downloads` folder can be copied to any drive or system.
- The GUI code moved from the single `EHentaiDownloader.pyw` (about 2,700 lines) to the
  `src/gui/` package; `EHentaiDownloader.pyw` is now only the launcher and works as before.
- GitHub check: Pylint now also checks `EHentaiDownloader.pyw` and runs on Python 3.10–3.13, and
  the tests run on every push. Dependabot proposes dependency and security updates every week.
- The release archives also contain this `CHANGELOG.md`.

### Fixed
- Long Chinese / Japanese / Korean gallery titles could not be saved on Linux and macOS ("File
  name too long"): names are now shortened to the 255-byte limit of those systems, never
  splitting a character.
- A `BAD` line of the link list holding a broken link (e.g. `BAD | - | (invalid link) | v`) was
  read back as the link "BAD".
- Text that is not a link at all is no longer kept in `failed_downloads.txt` for *Retry Failed*.

## [2.0.0] – 2026-11-10

First release of the fork with ready-to-use apps.

### Added
- **nhentai.net support** through the site's public API v2 (no account needed), respecting its
  limit of 20 requests per minute, with a pause and countdown when the site asks to slow down.
- **Menu bar** (File / Settings / Help) that follows the theme, also on Windows; Auto Rename,
  CBZ archive, Speed (1x–5x), When done and Theme are there.
- **Light, dark and automatic theme**, platform fonts, tabs of equal size.
- **Account & cookies** window that writes `cookies.json` from cookies pasted from the browser
  or from a `cookies.txt`.
- **Ready-to-use apps** for Windows, macOS (Apple Silicon and Intel) and Linux, built by GitHub
  Actions for every release tag (no Python needed).
- **Start-up notice** (disclaimer: no warranty, developed with the help of Claude AI) with
  *Don't show this again for 30 days*; *Help* menu with GitHub page, license and about.

[2.0.2]: https://github.com/LainX84/EHentaiDownloader/compare/v2.0.1...v2.0.2
[2.0.1]: https://github.com/LainX84/EHentaiDownloader/compare/v2.0.0...v2.0.1
[2.0.0]: https://github.com/LainX84/EHentaiDownloader/releases/tag/v2.0.0
