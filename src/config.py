"""Configuration module for managing constants and settings used across the project.

All tunable settings live here. Every value has a short comment explaining what it does;
change them and restart the program to apply.
"""

# ============================
# Paths and Files
# ============================
DOWNLOAD_FOLDER = "Downloads"  # Where finished albums go (desktop: Settings → Download folder… changes it).
TEMP_FOLDER = "temp"            # Albums are downloaded here and moved to DOWNLOAD_FOLDER when complete.
URLS_FILE = "URLs.txt"         # The file containing the list of URLs to process.
SESSION_LOG = "session.log"    # The file where session logs will be recorded.
FAILED_URLS_FILE = "failed_downloads.txt"  # Albums to retry in next runs.
SETTINGS_FILE = "settings.json"  # GUI choices remembered between runs (format, Auto Rename, CBZ).
STATS_FILE = "stats.json"      # Download statistics since the first start (Statistics tab).
TAGS_FILE = "tags.json"        # Tags met in the galleries (listed in Settings → Favorite tags…).
# Completed albums go into one sub-folder of DOWNLOAD_FOLDER per site:
EHENTAI_SUBFOLDER = "E-Hentai"   # galleries from e-hentai.org and exhentai.org
NHENTAI_SUBFOLDER = "nhentai"    # galleries from nhentai.net
HITOMI_SUBFOLDER = "hitomi"      # galleries from hitomi.la

# ============================
# Download Settings
# ============================
KB = 1024             # Number of bytes in a kilobyte.
CHUNK_SIZE = 16 * KB  # The size of each chunk when downloading files.
MAX_WORKERS = 5       # Upper limit of concurrent image downloads (the speed level picks 1..5).
DEFAULT_SPEED = 3     # Start speed: 1 Very slow, 2 Slow, 3 Normal, 4 Fast, 5 Very fast (GUI slider).
# Default format (desktop and web at first start, CLI). Options: "original", "webp", "jpg", "jxl", "png", "avif"
DEFAULT_IMAGE_FORMAT = "webp"
RENAME_SEQUENTIAL = True  # Rename images to 01..N (padding sized on N) when the album is complete.
CREATE_CBZ = False        # Save each completed album as "Downloads/<title>.cbz" instead of a folder of images.
CBZ_COMIC_INFO = False    # Also put a ComicInfo.xml (title, page count, source link) inside each CBZ.
IMAGE_SERVER_RETRIES = 3  # How many different image servers to try per page ("reload" link).
IMAGE_FETCH_RETRIES = 3   # Attempts on the same image server before switching to another one.
PAGE_FETCH_RETRIES = 3    # Attempts for gallery / picture HTML pages on transient errors.
ALBUM_PAGE_DELAY = (0.5, 1.5)  # Random pause (seconds) between gallery index pages.

# ============================
# nhentai.net
# ============================
# Image servers used when the site's own list (/api/v2/cdn) can't be read; tried in order.
NHENTAI_IMAGE_MIRRORS = (
    "i1.nhentai.net", "i2.nhentai.net", "i3.nhentai.net", "i4.nhentai.net", "i.nhentai.net",
)
NHENTAI_BLOCK_EXTRA_WAIT = (10, 15)  # Random extra seconds after the wait asked by nhentai (HTTP 429).
NHENTAI_BLOCK_DEFAULT_WAIT = 60      # Wait used when nhentai does not say how long (no Retry-After).
NHENTAI_API_INTERVAL = 3.2      # Min. seconds between nhentai API requests (site limit: 20/min per IP).
NHENTAI_API_INTERVAL_KEY = 1.4  # Same, with an nhentai API key in cookies.json (limit: 45/min per IP).
APP_VERSION = "2.2.1"           # Shown next to the program name and in Help → About; release tags are v<APP_VERSION>.
PROJECT_URL = "https://github.com/LainX84/EHentaiDownloader"
ISSUES_URL = f"{PROJECT_URL}/issues"
UPSTREAM_URL = "https://github.com/Lysagxra/EHentaiDownloader"
DISCLAIMER_SNOOZE_DAYS = 30     # "Don't show again" hides the start-up notice for this many days.
FORMAT_NOTICE_SNOOZE_DAYS = 30  # "Don't show again" hides the notice of an image format for this many days.
NHENTAI_MAX_MIRRORS = 4         # Mirrors tried per image before giving up on it (until 'Retry Failed').
NHENTAI_IMAGE_DELAY = (0.4, 1.2)  # Random pause (seconds) after each image, per download thread.

# ============================
# hitomi.la
# ============================
# Domain of hitomi's data and image servers (ltn.<domain> for the gallery data and gg.js,
# w1/w2/a1/a2.<domain> for the images). If the site moves to another domain, the program
# finds the new one from a hitomi page by itself; change it here only if that fails.
HITOMI_CDN_DOMAIN = "gold-usergeneratedcontent.net"
HITOMI_GG_MAX_AGE = 1800        # Seconds gg.js (where each image is) is kept before reading it again.
HITOMI_DATA_INTERVAL = 1.0      # Min. seconds between hitomi data requests (gallery info, gg.js).
HITOMI_BLOCK_DEFAULT_WAIT = 60      # Wait used for a 429 when hitomi does not say how long (no Retry-After).
HITOMI_SLOWDOWN_WAIT = (30, 60)     # Wait (random seconds) for a 503 "Service Unavailable" without Retry-After.
HITOMI_SOFTBAN_EXTRA_WAIT = (45, 60)  # Soft ban (503 / 429): random extra seconds added to the wait above
                                      # (or to the time asked by the site), to be cautious.
HITOMI_IMAGE_DELAY = (1.1, 2.5)  # Random pause (seconds) after each image, per download thread.
# Images downloaded in parallel for each Speed level (1 Very slow ... 5 Very fast): hitomi is
# cautious and uses fewer than the other sites at the higher levels.
HITOMI_WORKERS = {1: 1, 2: 2, 3: 2, 4: 3, 5: 4}
HITOMI_SLOWDOWN_LIMIT = 2       # After this many soft bans in one gallery, its images go one at a time.
HITOMI_SLOWDOWN_TRIES = 5       # Soft bans waited for the same image before it goes to Retry Failed.

# ============================
# Batch list (GUI)
# ============================
AUTOSAVE_INTERVAL = 10       # Seconds between automatic saves of the batch list to URLs.txt.
DONE_LINE_REMOVE_DELAY = 45  # Seconds a downloaded (green) line stays in the batch list.

# ============================
# HTTP / Network
# ============================
# HTTP status codes
HTTP_RATE_LIMIT = 429  # "Too Many Requests"

CONNECTION_TIMEOUT = 30        # Timeout duration for network requests (in seconds).
RATE_LIMIT_SLEEPING_TIME = 60  # Time to wait when rate-limited (in seconds).
BAN_EXTRA_WAIT = (30, 45)      # Extra seconds (random) waited after a temporary IP ban expires.
BAN_DEFAULT_WAIT = 300         # Wait used when the ban page does not say how long the ban lasts.
MAX_BAN_WAITS = 10             # Bans in a row tolerated on the same page before giving up on it.

# ============================
# Session log rotation
# ============================
SESSION_LOG_MAX_BYTES = 5 * 1024 * 1024  # Rotate session.log when it grows past this size.
SESSION_LOG_BACKUPS = 3                   # Keep session.log.1 .. session.log.N.

# ============================
# User-Agent
# ============================
# A single, realistic and consistent browser identity (replaces fake_useragent).
# Update it from time to time if you like: any recent Firefox UA works.
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:140.0) Gecko/20100101 Firefox/140.0"


# Helper functions
def prepare_user_agent() -> str:
    """Return the user-agent string used for every request."""
    return USER_AGENT
