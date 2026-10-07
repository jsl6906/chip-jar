"""Read-only Jellyfin client for movie-night suggestions.

The movie library is fetched once per user and merged (anyone having watched a film counts), then
cached in memory for JELLYFIN_CACHE_TTL seconds. All functions degrade to "nothing" when the
integration is not configured or the server is unreachable.
"""

import logging
import random
import re
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Optional

import httpx

from . import settings

logger = logging.getLogger(__name__)

_ITEM_ID = re.compile(r"^[0-9a-fA-F]{32}$")
KIDS_RATINGS = {"G", "PG", "TV-Y", "TV-Y7", "TV-Y7-FV", "TV-G", "TV-PG"}
ALL_AGES_RATINGS = {"G", "TV-Y", "TV-G"}
FAMILY_GENRES = {"Family", "Animation"}


@dataclass
class Movie:
    id: str
    name: str
    year: Optional[int] = None
    genres: list[str] = field(default_factory=list)
    rating: Optional[str] = None
    community_rating: Optional[float] = None
    runtime_min: Optional[int] = None
    overview: str = ""
    has_image: bool = False
    play_count: int = 0
    last_played: Optional[datetime] = None

    @property
    def played(self) -> bool:
        return self.play_count > 0 or self.last_played is not None

    @property
    def is_stale(self) -> bool:
        """Never watched, or not watched within JELLYFIN_RECENT_DAYS."""
        if not self.played:
            return True
        if self.last_played is None:
            return False
        return self.last_played < datetime.now(timezone.utc) - timedelta(days=settings.JELLYFIN_RECENT_DAYS)

    @property
    def kids_ok(self) -> bool:
        # PG alone lets in old Bond films etc., so PG/unrated also needs a Family/Animation tag.
        rating = (self.rating or "").upper()
        if rating in ALL_AGES_RATINGS:
            return True
        return (not rating or rating in KIDS_RATINGS) and bool(FAMILY_GENRES.intersection(self.genres))

    @property
    def watched_label(self) -> str:
        if not self.played:
            return "Never watched"
        if self.last_played is None:
            return "Watched"
        days = (datetime.now(timezone.utc) - self.last_played).days
        if days < 30:
            return "Watched recently"
        if days < 365:
            return f"Watched {days // 30}mo ago"
        return f"Watched {days // 365}y ago"


def enabled() -> bool:
    return bool(settings.JELLYFIN_URL and settings.JELLYFIN_API_KEY)


def valid_item_id(item_id: str) -> bool:
    return bool(_ITEM_ID.match(item_id or ""))


def _client() -> httpx.Client:
    auth = (
        f'MediaBrowser Client="ChipJar", Device="chip-jar", DeviceId="chip-jar", Version="0.4.0", '
        f'Token="{settings.JELLYFIN_API_KEY}"'
    )
    return httpx.Client(base_url=settings.JELLYFIN_URL, headers={"Authorization": auth}, timeout=httpx.Timeout(15.0))


def _parse_dt(raw: Optional[str]) -> Optional[datetime]:
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    # Jellyfin uses 0001-01-01 as "never".
    return None if dt.year < 1900 else dt.astimezone(timezone.utc)


def _fetch_library() -> dict[str, Movie]:
    movies: dict[str, Movie] = {}
    with _client() as client:
        users = client.get("/Users").raise_for_status().json()
        for user in users:
            resp = client.get(
                "/Items",
                params={
                    "userId": user["Id"],
                    "IncludeItemTypes": "Movie",
                    "Recursive": "true",
                    "Fields": "Genres,Overview,ProductionYear,OfficialRating,CommunityRating,RunTimeTicks",
                    "EnableImageTypes": "Primary",
                    "ImageTypeLimit": "1",
                },
            ).raise_for_status()
            for item in resp.json().get("Items", []):
                ud = item.get("UserData") or {}
                m = movies.get(item["Id"])
                if m is None:
                    ticks = item.get("RunTimeTicks")
                    m = Movie(
                        id=item["Id"],
                        name=item.get("Name", "Untitled"),
                        year=item.get("ProductionYear"),
                        genres=item.get("Genres") or [],
                        rating=item.get("OfficialRating"),
                        community_rating=item.get("CommunityRating"),
                        runtime_min=round(ticks / 600_000_000) if ticks else None,
                        overview=item.get("Overview") or "",
                        has_image=bool((item.get("ImageTags") or {}).get("Primary")),
                    )
                    movies[m.id] = m
                m.play_count += int(ud.get("PlayCount") or 0) or (1 if ud.get("Played") else 0)
                lp = _parse_dt(ud.get("LastPlayedDate"))
                if lp and (m.last_played is None or lp > m.last_played):
                    m.last_played = lp
    logger.info("Jellyfin library loaded: %d movies across %d users", len(movies), len(users))
    return movies


_lock = threading.Lock()
_cache: tuple[float, dict[str, Movie]] = (0.0, {})


def library(force: bool = False) -> dict[str, Movie]:
    """Cached {item_id: Movie}; empty when disabled or unreachable."""
    global _cache
    if not enabled():
        return {}
    with _lock:
        expires, data = _cache
        if not force and data and time.monotonic() < expires:
            return data
        try:
            data = _fetch_library()
            _cache = (time.monotonic() + settings.JELLYFIN_CACHE_TTL, data)
        except (httpx.HTTPError, KeyError, ValueError) as e:
            logger.warning("Jellyfin library fetch failed: %s", e)
            # Keep serving stale data briefly rather than hammering a down server.
            _cache = (time.monotonic() + 60, data)
        return data


def get(item_id: str) -> Optional[Movie]:
    return library().get(item_id)


def genres(limit: int = 8) -> list[str]:
    counts: dict[str, int] = {}
    for m in library().values():
        for g in m.genres:
            counts[g] = counts.get(g, 0) + 1
    return [g for g, _ in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[:limit]]


def search(
    q: str = "",
    genre: str = "",
    kids: bool = False,
    exclude: Optional[set[str]] = None,
    limit: int = 24,
    shuffle: bool = True,
) -> list[Movie]:
    """Suggestions: stale (unwatched / long-ago) films first, never-watched weighted higher.

    With a search term, all matches are returned (watched or not) sorted by name.
    """
    exclude = exclude or set()
    needle = q.strip().lower()
    pool = [
        m
        for m in library().values()
        if (not needle or needle in m.name.lower())
        and (not genre or genre in m.genres)
        and (not kids or m.kids_ok)
        and (needle or (m.is_stale and m.id not in exclude))
    ]
    if needle:
        pool.sort(key=lambda m: (not m.name.lower().startswith(needle), m.name.lower()))
        return pool[:limit]
    if shuffle:
        # Weighted sampling without replacement (Efraimidis–Spirakis): never-watched x3.
        rnd = random.Random()
        pool.sort(key=lambda m: rnd.random() ** (1.0 / (3.0 if not m.played else 1.0)), reverse=True)
    return pool[:limit]


def image(item_id: str) -> Optional[tuple[bytes, str]]:
    """Primary poster bytes + content type, or None."""
    if not enabled() or not valid_item_id(item_id):
        return None
    try:
        with _client() as client:
            resp = client.get(f"/Items/{item_id}/Images/Primary", params={"maxHeight": "450", "quality": "90"})
        if resp.status_code != 200:
            return None
        return resp.content, resp.headers.get("content-type", "image/jpeg")
    except httpx.HTTPError as e:
        logger.warning("Jellyfin image fetch failed for %s: %s", item_id, e)
        return None
