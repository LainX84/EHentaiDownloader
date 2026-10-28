"""Configuration module for managing constants and settings used across the project.

All tunable settings live here. Every value has a short comment explaining what it does;
change them and restart the program to apply.
"""

# ============================
# Paths and Files
# ============================
DOWNLOAD_FOLDER = "Downloads"  # The folder where downloaded files will be stored.
URLS_FILE = "URLs.txt"         # The file containing the list of URLs to process.
SESSION_LOG = "session.log"    # The file where session logs will be recorded.
FAILED_URLS_FILE = "failed_downloads.txt"  # Albums to retry in next runs.

# ============================
# Download Settings
# ============================
KB = 1024             # Number of bytes in a kilobyte.
CHUNK_SIZE = 16 * KB  # The size of each chunk when downloading files.
MAX_WORKERS = 5       # The number of concurrent image downloads.
DEFAULT_IMAGE_FORMAT = "auto"  # CLI default. Options: "auto", "original", "jpg", "png", "webp"
RENAME_SEQUENTIAL = True  # Rename images to 01..N (padding sized on N) when the album is complete.
IMAGE_SERVER_RETRIES = 3  # How many different image servers to try per page ("reload" link).
IMAGE_FETCH_RETRIES = 3   # Attempts on the same image server before switching to another one.
PAGE_FETCH_RETRIES = 3    # Attempts for gallery / picture HTML pages on transient errors.
ALBUM_PAGE_DELAY = (0.5, 1.5)  # Random pause (seconds) between gallery index pages.

# ============================
# HTTP / Network
# ============================
# HTTP status codes
HTTP_RATE_LIMIT = 429  # "Too Many Requests"

CONNECTION_TIMEOUT = 30        # Timeout duration for network requests (in seconds).
RATE_LIMIT_SLEEPING_TIME = 60  # Time to wait when rate-limited (in seconds).

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
