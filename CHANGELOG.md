# Changelog

All notable changes to this fork of EHentai Downloader. Versions follow the release tags
(`v<version>`); the ready-to-use apps of each version are on the
[releases page](https://github.com/LainX84/EHentaiDownloader/releases).

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

[2.0.1]: https://github.com/LainX84/EHentaiDownloader/compare/v2.0.0...v2.0.1
[2.0.0]: https://github.com/LainX84/EHentaiDownloader/releases/tag/v2.0.0
