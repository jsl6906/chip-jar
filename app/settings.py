"""Runtime configuration loaded from environment / .env."""

import os
from typing import Optional
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

# HTTP Basic password for the /manage admin page; the page returns 404 while unset.
MANAGE_PASSWORD: str = os.environ.get("MANAGE_PASSWORD", "")

# Optional Jellyfin integration for movie-night suggestions (enabled when the API key is set).
# Default URL is the compose service name on MediaServer; override when running elsewhere.
JELLYFIN_URL: str = os.environ.get("JELLYFIN_URL", "http://jellyfin:8096").rstrip("/")
JELLYFIN_API_KEY: str = os.environ.get("JELLYFIN_API_KEY", "")
JELLYFIN_CACHE_TTL: int = int(os.environ.get("JELLYFIN_CACHE_TTL", "900"))
# A movie counts as "watched recently" if anyone played it within this many days.
JELLYFIN_RECENT_DAYS: int = int(os.environ.get("JELLYFIN_RECENT_DAYS", "365"))


def _float_or_none(name: str) -> Optional[float]:
    raw = os.environ.get(name, "").strip()
    try:
        return float(raw) if raw else None
    except ValueError:
        return None


# Optional Google Places (New) + Routes integration for restaurant spinners; needs the key and home coords.
GOOGLE_MAPS_API_KEY: str = os.environ.get("GOOGLE_MAPS_API_KEY", "")
HOME_LAT: Optional[float] = _float_or_none("HOME_LAT")
HOME_LNG: Optional[float] = _float_or_none("HOME_LNG")
RESTAURANT_MAX_DRIVE_MIN: int = int(os.environ.get("RESTAURANT_MAX_DRIVE_MIN", "15"))
# Nearby Search returns at most 20 places, so keep the circle close to the drive limit (~0.5 km per minute).
PLACES_SEARCH_RADIUS_M: int = int(os.environ.get("PLACES_SEARCH_RADIUS_M", str(RESTAURANT_MAX_DRIVE_MIN * 500)))
PLACES_CACHE_TTL: int = int(os.environ.get("PLACES_CACHE_TTL", "86400"))

# Restaurant ABC suggestions roam further than the dinner spinner (it's a planned outing).
ABC_MAX_DRIVE_MIN: int = int(os.environ.get("ABC_MAX_DRIVE_MIN", "35"))
ABC_SEARCH_RADIUS_M: int = min(50000, int(os.environ.get("ABC_SEARCH_RADIUS_M", "25000")))
ABC_POOL_TTL: int = int(os.environ.get("ABC_POOL_TTL", str(7 * 86400)))
