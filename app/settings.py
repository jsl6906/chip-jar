"""Runtime configuration loaded from environment / .env."""

import os
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

load_dotenv()

DATABASE_URL: str = os.environ.get("DATABASE_URL", "")
DB_SCHEMA: str = os.environ.get("DB_SCHEMA", "personal_tracking")
DB_POOL_MIN: int = int(os.environ.get("DB_POOL_MIN", "1"))
DB_POOL_MAX: int = int(os.environ.get("DB_POOL_MAX", "4"))

# Timezone used for display and for interpreting user-entered local times.
LOCAL_TZ = ZoneInfo(os.environ.get("APP_TZ", "America/New_York"))
UTC = ZoneInfo("UTC")

# Optional file log path; stdout logging is always on.
LOG_FILE: str = os.environ.get("LOG_FILE", "")

# Optional Jellyfin integration for movie-night suggestions (enabled when the API key is set).
# Default URL is the compose service name on MediaServer; override when running elsewhere.
JELLYFIN_URL: str = os.environ.get("JELLYFIN_URL", "http://jellyfin:8096").rstrip("/")
JELLYFIN_API_KEY: str = os.environ.get("JELLYFIN_API_KEY", "")
JELLYFIN_CACHE_TTL: int = int(os.environ.get("JELLYFIN_CACHE_TTL", "900"))
# A movie counts as "watched recently" if anyone played it within this many days.
JELLYFIN_RECENT_DAYS: int = int(os.environ.get("JELLYFIN_RECENT_DAYS", "365"))
