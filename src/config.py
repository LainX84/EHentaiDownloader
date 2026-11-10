"""Configuration module for managing constants and settings used across the project.

All tunable settings live here. Every value has a short comment explaining what it does;
change them and restart the program to apply.
"""

# ============================
# Paths and Files
# ============================
DOWNLOAD_FOLDER = "Downloads"  # The folder where downloaded files will be stored.
TEMP_FOLDER = "temp"            # Albums are downloaded here and moved to DOWNLOAD_FOLDER when complete.
URLS_FILE = "URLs.txt"         # The file containing the list of URLs to process.
SESSION_LOG = "session.log"    # The file where session logs will be recorded.
FAILED_URLS_FILE = "failed_downloads.txt"  # Albums to retry in next runs.
SETTINGS_FILE = "settings.json"  # GUI choices remembered between runs (format, Auto Rename, CBZ).

# ============================
# Download Settings
# ============================
KB = 1024             # Number of bytes in a kilobyte.
CHUNK_SIZE = 16 * KB  # The size of each chunk when downloading files.
MAX_WORKERS = 5       # Upper limit of concurrent image downloads (the speed level picks 1..5).
DEFAULT_SPEED = 3     # Start speed: 1 Very slow, 2 Slow, 3 Normal, 4 Fast, 5 Very fast (GUI slider).
DEFAULT_IMAGE_FORMAT = "auto"  # CLI default. Options: "auto", "original", "jpg", "png", "webp"
RENAME_SEQUENTIAL = True  # Rename images to 01..N (padding sized on N) when the album is complete.
CREATE_CBZ = False        # Save each completed album as "Downloads/<title>.cbz" instead of a folder of images.
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
APP_VERSION = "2.0.0"           # Shown in Help → About; release tags are v<APP_VERSION>.
PROJECT_URL = "https://github.com/LainX84/EHentaiDownloader"
ISSUES_URL = f"{PROJECT_URL}/issues"
UPSTREAM_URL = "https://github.com/Lysagxra/EHentaiDownloader"
DISCLAIMER_SNOOZE_DAYS = 30     # "Don't show again" hides the start-up notice for this many days.
NHENTAI_MAX_MIRRORS = 4         # Mirrors tried per image before giving up on it (until 'Retry Failed').
NHENTAI_IMAGE_DELAY = (0.4, 1.2)  # Random pause (seconds) after each image, per download thread.

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
