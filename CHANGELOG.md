# Changelog

All notable changes to this fork of EHentai Downloader. Versions follow the release tags
(`v<version>`); the ready-to-use apps of each version are on the
[releases page](https://github.com/LainX84/EHentaiDownloader/releases).

## [Unreleased]

## [2.2.1] – 2026-11-27

### Added
- **Merged favorite tags** (desktop and Docker / web): several similar tags can count as one
  favorite, with one label, one colour and one folder — e.g. *gender bender* + *gender morph* +
  *gender change*, or *netorare* + *netorase* + *netori*. In *Settings → Favorite tags…* select a
  tag, choose the favorite in *Merge into* and press *Merge* (web: *Merge … into …*); *Split* (✂)
  separates them again. Typing `netorare, netorase, netori = NTR` in *Add a tag* does the same.
  The *Galleries* column of a merged favorite counts each gallery once.
- **Nested folders by priority**: with *Sort downloads into folders by favorite tag* on, the new
  option *Nest the folders by priority* makes one folder level per favorite of the gallery, in
  priority order (★ 1 outermost): `E-Hentai/Gender Bender/Netorare/<title>` (at most 5 levels).
  Saved as `tag_folders_nested` in `settings.json`.

### Changed
- The favorite tags window and the log state the rule clearly: when a gallery has more than one
  favorite, the **lowest number** (★ 1) decides the label and the folder. The log lists the
  favorites with their position and merged tags.

## [2.2.0] – 2026-11-25

### Added
- **hitomi.la** as a fourth site (desktop, Docker / web and command line): paste any link of a
  gallery (`/galleries/<number>.html`, `/reader/…`, `/doujinshi/<title>-<number>.html`,
  `/manga/…`, `/cg/…`, `/gamecg/…`, `/imageset/…`). No account or cookies needed. The gallery is
  read with one request (title, tags, images); the image addresses come from the site's `gg.js`,
  read again every 30 minutes and at once when an image server refuses an image. If the site moves
  its data servers to another domain, the new one is found by itself.
- hitomi has **its own download lane** (purple progress bar), pace and HTTP 429 pause, so it
  downloads side by side with E-Hentai / ExHentai and nhentai. Albums go to `Downloads/hitomi/`
  (or `hitomi/<favorite tag>/`), the lists show the HITOMI logo, the statistics have a hitomi row,
  and its tags (`female:…`, `male:…`, `artist:…`, `language:…`…) work with the favorite tags.
- hitomi gives only WebP, AVIF and sometimes JPEG XL images (no original files): the program asks
  for the chosen *Image Format* when the site has it, so nothing is converted; otherwise it takes
  the WebP and converts it. *Native (Original)* saves the WebP as it is.
- hitomi titles get the **authors in front**, as on E-Hentai / nhentai: `[Artist] Title` or
  `[Group (Artist)] Title` (folders, CBZ names, lists and the ComicInfo writer).
- **Cautious with hitomi** (it publishes no limits, but answers *503* to clients that are too
  fast): 1, 2, 2, 3, 4 images in parallel for Speed 1–5 (`HITOMI_WORKERS`) and a 1.1–2.5 s pause
  after each image. On a **soft ban** (503 / 429) every hitomi download pauses for the time asked
  (or 30–60 s) **plus a random 45–60 s**, with a warning in the log, *Soft ban by hitomi* and a
  countdown in the status bar, and a notification when the window is in the tray; then the same
  image is asked again (not counted as failed). After 2 soft bans in a gallery the rest of it goes
  one image at a time.
- **Stop** and **Cancel** buttons (desktop and Docker / web), next to *Pause*, for a long
  session that you want to end:
  - **⏹ Stop** lets the galleries already downloading finish, then the download ends. The links
    not started yet stay in the batch list (and, for *Retry Failed*, in the retry queue), so the
    next *Start Batch* goes on from there. During *Verify Links* it stops after the link being
    checked.
  - **✖ Cancel** asks for a confirmation, then stops everything at once (also during a pause, an
    IP-ban wait or a lost connection): the galleries in progress are interrupted, the `temp/`
    folder is emptied (partial galleries of earlier sessions too), and both the batch list
    (`URLs.txt`) and the retry queue (`failed_downloads.txt`) are emptied. Galleries already
    completed stay in the download folder. The *When done* action is not run after a Cancel.
  - The tray menu has *Stop after the current galleries* and *Cancel all downloads…* too.

- **One window only** (desktop): starting the program while it is already open does not open
  a second copy (which would write the same `URLs.txt`, `settings.json`, statistics…): the
  open window comes to the front, also from the system tray. Windows, Linux and macOS; the
  lock is per data folder and is freed by the system even after a crash.
- **Confirmation before closing** (desktop): window ✕, *File → Exit* and the tray's *Quit* ask
  first when downloads are running (also paused), links are waiting in the batch list or
  failed downloads are waiting to be retried, and list what is pending. The lists are saved
  as before. Closing during a pause no longer happens without asking.

### Changed
- Desktop: **Pause / Stop / Cancel** are now at the right of the progress bars, so they are at
  hand for single downloads and *Retry Failed* too (Pause used to be only in the Batch tab).
- Desktop: **drag and drop of links works anywhere on the window** (header, tabs, progress bars,
  log, status bar…), not only on the batch list; while dragging, the status bar says that
  releasing adds the links to the batch list.
- Docker / web: **drag and drop of links onto the page** too, anywhere (links from another tab,
  selected text, `.txt` / `.url` files): the gallery links go to the batch list, as with Ctrl+V.
  A link dropped on the *Single Download* field still goes in that field.

### Fixed
- Desktop: links dragged onto the window **during a download** were sometimes refused until a
  timeout. The drop is now answered at once (the links are added right after), and the window
  stays responsive during fast downloads: the messages of the download threads are handled a
  bit at a time, and the log box keeps its last 5,000 lines (`session.log` still has them all).

## [2.1.0] – 2026-11-20

### Added
- **Statistics tab** (desktop and Docker / web): totals since the first start (kept in
  `stats.json`) and for this session. For each site (E-Hentai, ExHentai, nhentai) it shows
  galleries, images and size. It also shows the time spent downloading, the average and peak
  speed, the speed right now, and the **most downloaded tags** (content tags of the completed
  galleries: not artist, language or parody). *Reset* starts again from zero. The current speed
  is also shown in the status bar.
- **Gallery tags** are read from the gallery page (E-Hentai / ExHentai) and from the nhentai data,
  when a link is verified and when it is downloaded.
- **Favorite tags** (*Settings → Favorite tags…*): a list of the tags met in your galleries,
  each with a selector (☐ / ★) to make it a favorite. The list fills up by itself with the tags
  of every gallery verified or downloaded (saved in `tags.json`, with how many galleries had
  them); a few common tags are offered from the start ("suggested") and any tag can be added
  by hand (also `artist:name`). Each favorite has a label (`ai generated` → `AI`, up to 20
  characters), **its own colour** and a priority (▲ ▼): when a gallery has more than one
  favorite tag, the one with the highest priority is shown. The tags of every gallery are
  remembered in `tags.json`, so the lists keep the right tag (and follow a new priority) also
  after a restart. The columns of the window can be resized.
- **Folders by favorite tag** (*Settings → Sort downloads into folders by favorite tag*, OFF by
  default): a gallery with a favorite tag goes into `<site>/<label>/<gallery>` (for example
  `E-Hentai/Netorare/…`); the others stay in `E-Hentai/` or `nhentai/`.
- **Download folder** (desktop, *Settings → Download folder…* / *Use the default download
  folder*): the finished albums can go to any folder (another drive, a NAS share…). Albums
  still downloading stay in `temp/`.
- **Queue order** (desktop and web): the lines not started yet can be moved (▲ ▼ next to the
  list, Alt+Up / Alt+Down; on the web page also drag and drop) and removed (✖, Ctrl+Delete) while
  the batch runs. Each site downloads its galleries in the order of the list, and removed lines
  are skipped.

- **Internet connection lost** (desktop and web): when a request fails and no site can be
  reached at all, every download waits (the connection is checked every 10 s, shown in the
  status bar) instead of failing, and goes on when it is back. The gallery that was
  downloading is then gone through once more, skipping the pages already on disk, so no image
  is lost; a gallery whose page could not be read during the outage is tried again.

- **Minimize to the system tray** (desktop, Windows and Linux; *Settings → Minimize to system
  tray*, ON by default): a minimized window goes to a tray icon next to the clock and the
  downloads go on in the background. The icon menu has *Show EHentai Downloader* (also a click
  on the icon), *Pause / Resume downloads*, *Open Downloads* and *Quit*; its tooltip shows what is
  downloading and a notification says when the downloads are finished. Closing the window
  while a download runs asks whether to keep downloading in the tray or stop and close. On macOS
  the window is minimized to the Dock as before. Needs the new optional package `pystray`.

### Changed
- **README**: the animated demo is replaced by up-to-date screenshots (`assets/screenshots/`).
- **List order**: after *Verify Links* and at *Start Batch* the list is sorted by itself (no
  question any more): E-Hentai / ExHentai galleries first, then nhentai, and in each group the
  galleries with fewer images first. Each site then goes down the list line by line, and the
  list scrolls only when no gallery being downloaded is on screen (it no longer jumps up and
  down between the two sites).
- **Clearer lists, in columns**: `✅ | [site logo] | [ NETORARE ] | 123 | Gallery title`
  (`❌` when the gallery can't be read): site, favorite tag (blank when none), number of
  pages (centred) and title, aligned on every line. The link is hidden but still kept in the line, for
  downloads, Retry and the log; when the title is unknown, the link is shown instead.
  `URLs.txt` and `failed_downloads.txt` lines are now `✅ NHENTAI | TAG | IMAGES | TITLE | LINK`;
  older lines (`GOOD | 35 | title | link`) are still read and rewritten in the new form.
- Site logos: `assets/sites/` (original 40×10 badges, sized on the text). Put your own
  `ehentai.png`, `exhentai.png`, `nhentai.png` in a `logos/` folder next to `URLs.txt` (Docker:
  `/config/logos/`) to use other images.

## [2.0.4] – 2026-11-15

### Changed
- **New Image Format list** (desktop, Docker / web and CLI): *Native (Original)*, *WEBP*,
  *JPEG*, *JPEG XL*, *PNG*, *AVIF*. **WEBP is the default** at the first start. *AUTO* has been
  removed (a saved AUTO choice becomes WEBP, a saved *JPG* becomes *JPEG*).
  - *Native (Original)* saves each image exactly as the server sends it, without any change.
  - WEBP and AVIF keep animations (animated GIF / WebP), with each frame's timing.
  - Choosing JPEG, JPEG XL or PNG warns that animated images keep only their first frame; choosing
    AVIF or JPEG XL explains which codecs are needed to view them on Windows, Linux and macOS.
    The notice has *Don't show this again for 30 days* (for each format); a short reminder is
    always written in the log.
  - Windows extension links (AV1 Video Extension, JPEG XL Image Extension) in the notice and in
    the log: on Windows they open only the small install window of the Microsoft Store.
  - Links in the log (desktop and web) are now clickable.
  - README and *About*: all the formats offered are open standards and royalty-free.

### Added
- **JPEG XL** (`.jxl`, through `pillow-jxl-plugin`) and **AVIF** (`.avif`, built into Pillow
  11.3+) output formats. Requirements: `Pillow>=11.3.0`, `pillow-jxl-plugin`.

### Fixed
- Docker / web: removed the *Open Downloads* button and the *File → Open Downloads folder*
  entry (with the file list behind them), which were still in the page although the web
  version is not meant to have them: the albums are in the `/data` folder of the server.

## [2.0.3] – 2026-11-14

### Fixed
- CBZ archives of galleries with very long titles failed with "File name too long" on
  Linux / macOS / Docker (the album was left as a folder): the name now leaves room for the
  temporary `.cbz.part` ending used while the archive is written.
- Clear warning when no ExHentai account cookies are set (desktop and Docker): it is shown in the
  log for every E-Hentai / ExHentai gallery and at web start-up, and the error now says the cookies
  are missing instead of "expired or wrong". ExHentai links, and galleries removed from E-Hentai
  that are still on ExHentai, need them.

### Added
- *Contributors* section in the README.

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

[Unreleased]: https://github.com/LainX84/EHentaiDownloader/compare/v2.2.1...HEAD
[2.2.1]: https://github.com/LainX84/EHentaiDownloader/compare/v2.2.0...v2.2.1
[2.2.0]: https://github.com/LainX84/EHentaiDownloader/compare/v2.1.0...v2.2.0
[2.1.0]: https://github.com/LainX84/EHentaiDownloader/compare/v2.0.4...v2.1.0
[2.0.4]: https://github.com/LainX84/EHentaiDownloader/compare/v2.0.3...v2.0.4
[2.0.3]: https://github.com/LainX84/EHentaiDownloader/compare/v2.0.2...v2.0.3
[2.0.2]: https://github.com/LainX84/EHentaiDownloader/compare/v2.0.1...v2.0.2
[2.0.1]: https://github.com/LainX84/EHentaiDownloader/compare/v2.0.0...v2.0.1
[2.0.0]: https://github.com/LainX84/EHentaiDownloader/releases/tag/v2.0.0
