"""Google Places (New) + Routes client for restaurant-spinner suggestions.

Searches are cached for PLACES_CACHE_TTL and drive times for a week, so a family's usage stays
well inside Google's monthly free caps. Everything degrades to "no suggestions" on error.
"""

import logging
import random
import re
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional
from urllib.parse import quote

import httpx

from . import settings

logger = logging.getLogger(__name__)

PLACES_URL = "https://places.googleapis.com/v1"
ROUTES_URL = "https://routes.googleapis.com/distanceMatrix/v2:computeRouteMatrix"
_PLACE_ID = re.compile(r"^[A-Za-z0-9_-]{10,300}$")

FIELDS = [
    "id", "displayName", "shortFormattedAddress", "location", "rating", "userRatingCount", "priceLevel",
    "primaryType", "primaryTypeDisplayName", "goodForChildren", "menuForChildren", "takeout", "dineIn",
    "reservable", "regularOpeningHours", "photos", "businessStatus",
]

CASUAL_TYPES = {
    "fast_food_restaurant", "hamburger_restaurant", "pizza_restaurant", "sandwich_shop", "mexican_restaurant",
    "meal_takeaway", "barbecue_restaurant", "breakfast_restaurant", "ice_cream_shop", "bakery", "cafe",
    "ramen_restaurant", "chinese_restaurant", "vietnamese_restaurant",
}

# key -> (label, includedTypes for Nearby Search)
CATEGORIES: dict[str, tuple[str, list[str]]] = {
    "casual": ("Casual", ["fast_food_restaurant", "hamburger_restaurant", "pizza_restaurant", "sandwich_shop",
                          "mexican_restaurant", "barbecue_restaurant", "meal_takeaway", "breakfast_restaurant"]),
    "pizza": ("Pizza", ["pizza_restaurant"]),
    "burgers": ("Burgers", ["hamburger_restaurant"]),
    "mexican": ("Mexican", ["mexican_restaurant"]),
    "asian": ("Asian", ["chinese_restaurant", "japanese_restaurant", "thai_restaurant", "vietnamese_restaurant",
                        "ramen_restaurant", "sushi_restaurant", "korean_restaurant"]),
    "breakfast": ("Breakfast", ["breakfast_restaurant", "brunch_restaurant", "bakery"]),
    "treats": ("Treats", ["ice_cream_shop", "bakery"]),
    "sitdown": ("Sit-down", ["american_restaurant", "italian_restaurant", "seafood_restaurant", "steak_house",
                             "mediterranean_restaurant"]),
}

TYPE_EMOJI = {
    "pizza_restaurant": "🍕", "hamburger_restaurant": "🍔", "mexican_restaurant": "🌮", "ice_cream_shop": "🍦",
    "bakery": "🥐", "cafe": "☕", "sandwich_shop": "🥪", "breakfast_restaurant": "🥞", "brunch_restaurant": "🥞",
    "sushi_restaurant": "🍣", "ramen_restaurant": "🍜", "chinese_restaurant": "🥡", "barbecue_restaurant": "🍖",
    "seafood_restaurant": "🦐", "steak_house": "🥩", "italian_restaurant": "🍝",
}

PRICE = {"PRICE_LEVEL_FREE": 0, "PRICE_LEVEL_INEXPENSIVE": 1, "PRICE_LEVEL_MODERATE": 2,
         "PRICE_LEVEL_EXPENSIVE": 3, "PRICE_LEVEL_VERY_EXPENSIVE": 4}

# Text search can't exclude types server-side, so these are dropped client-side.
ADULT_TYPES = {"bar", "pub", "night_club", "wine_bar", "brewery", "brewpub", "cocktail_bar", "lounge_bar",
               "sports_bar", "beer_hall", "liquor_store", "casino", "hookah_bar", "irish_pub", "winery"}
NOT_RESTAURANT_TYPES = {"convenience_store", "gas_station", "grocery_store", "supermarket"}


@dataclass
class Place:
    id: str
    name: str
    address: str = ""
    lat: Optional[float] = None
    lng: Optional[float] = None
    rating: Optional[float] = None
    rating_count: int = 0
    price: Optional[int] = None
    primary_type: str = ""
    type_label: str = ""
    good_for_children: Optional[bool] = None
    menu_for_children: Optional[bool] = None
    takeout: Optional[bool] = None
    dine_in: Optional[bool] = None
    reservable: Optional[bool] = None
    periods: list[dict] = field(default_factory=list)
    photo_name: Optional[str] = None
    operational: bool = True
    drive_min: Optional[int] = None

    @property
    def casual(self) -> bool:
        # Takeout alone is too loose (most sit-down places offer it); cheap + not reservable is a better signal.
        return self.primary_type in CASUAL_TYPES or (self.price == 1 and not self.reservable)

    @property
    def emoji(self) -> str:
        return TYPE_EMOJI.get(self.primary_type, "🍽️")

    @property
    def price_label(self) -> str:
        return "$" * self.price if self.price else ""

    @property
    def maps_url(self) -> str:
        return f"https://www.google.com/maps/search/?api=1&query={quote(self.name)}&query_place_id={quote(self.id)}"

    @property
    def open_now(self) -> Optional[bool]:
        """From regular hours in local time (Google days: 0=Sunday); None when hours are unknown."""
        if not self.periods:
            return None
        now = datetime.now(settings.LOCAL_TZ)
        week = 7 * 1440
        cur = ((now.weekday() + 1) % 7) * 1440 + now.hour * 60 + now.minute
        for p in self.periods:
            o, c = p.get("open"), p.get("close")
            if not o:
                continue
            if not c:
                return True
            start = o.get("day", 0) * 1440 + o.get("hour", 0) * 60 + o.get("minute", 0)
            end = c.get("day", 0) * 1440 + c.get("hour", 0) * 60 + c.get("minute", 0)
            if end <= start:
                end += week
            if start <= cur < end or start <= cur + week < end:
                return True
        return False

    def score(self) -> float:
        # Bayesian-smoothed rating so a 5.0 with 3 reviews doesn't beat a 4.6 with 2,000.
        r = ((self.rating or 4.0) * self.rating_count + 4.0 * 25) / (self.rating_count + 25)
        r += 0.5 if self.casual else 0
        r += 0.3 if self.good_for_children else 0
        r += 0.2 if self.menu_for_children else 0
        r -= 0.4 if (self.price or 0) >= 3 else 0
        return r


def enabled() -> bool:
    return bool(settings.GOOGLE_MAPS_API_KEY and settings.HOME_LAT is not None and settings.HOME_LNG is not None)


def valid_place_id(place_id: str) -> bool:
    return bool(_PLACE_ID.match(place_id or ""))


def _headers(mask: str) -> dict:
    return {"X-Goog-Api-Key": settings.GOOGLE_MAPS_API_KEY, "X-Goog-FieldMask": mask}


def _home() -> dict:
    return {"latitude": settings.HOME_LAT, "longitude": settings.HOME_LNG}


def _parse(raw: dict) -> Place:
    loc = raw.get("location") or {}
    photos = raw.get("photos") or []
    return Place(
        id=raw["id"],
        name=(raw.get("displayName") or {}).get("text", "Restaurant"),
        address=raw.get("shortFormattedAddress", ""),
        lat=loc.get("latitude"),
        lng=loc.get("longitude"),
        rating=raw.get("rating"),
        rating_count=int(raw.get("userRatingCount") or 0),
        price=PRICE.get(raw.get("priceLevel", "")),
        primary_type=raw.get("primaryType", ""),
        type_label=(raw.get("primaryTypeDisplayName") or {}).get("text", ""),
        good_for_children=raw.get("goodForChildren"),
        menu_for_children=raw.get("menuForChildren"),
        takeout=raw.get("takeout"),
        dine_in=raw.get("dineIn"),
        reservable=raw.get("reservable"),
        periods=(raw.get("regularOpeningHours") or {}).get("periods") or [],
        photo_name=photos[0].get("name") if photos else None,
        operational=raw.get("businessStatus", "OPERATIONAL") == "OPERATIONAL",
    )


def _nearby(client: httpx.Client, types: list[str]) -> list[dict]:
    body = {
        "includedTypes": types,
        "excludedPrimaryTypes": ["bar", "convenience_store", "gas_station"],
        "maxResultCount": 20,
        "rankPreference": "POPULARITY",
        "locationRestriction": {"circle": {"center": _home(), "radius": float(settings.PLACES_SEARCH_RADIUS_M)}},
    }
    resp = client.post(f"{PLACES_URL}/places:searchNearby", json=body,
                       headers=_headers(",".join(f"places.{f}" for f in FIELDS)))
    resp.raise_for_status()
    return resp.json().get("places", [])


def _text(client: httpx.Client, query: str) -> list[dict]:
    body = {
        "textQuery": query,
        "pageSize": 20,
        "locationBias": {"circle": {"center": _home(), "radius": float(settings.PLACES_SEARCH_RADIUS_M)}},
    }
    resp = client.post(f"{PLACES_URL}/places:searchText", json=body,
                       headers=_headers(",".join(f"places.{f}" for f in FIELDS)))
    resp.raise_for_status()
    return resp.json().get("places", [])


def _drive_minutes(client: httpx.Client, places: list[Place]) -> dict[str, int]:
    targets = [p for p in places if p.lat is not None and p.lng is not None][:25]
    if not targets:
        return {}
    body = {
        "origins": [{"waypoint": {"location": {"latLng": _home()}}}],
        "destinations": [{"waypoint": {"location": {"latLng": {"latitude": p.lat, "longitude": p.lng}}}} for p in targets],
        "travelMode": "DRIVE",
        "routingPreference": "TRAFFIC_UNAWARE",
    }
    resp = client.post(ROUTES_URL, json=body, headers=_headers("originIndex,destinationIndex,duration,condition"))
    resp.raise_for_status()
    out: dict[str, int] = {}
    for el in resp.json():
        if el.get("condition") != "ROUTE_EXISTS" or "duration" not in el:
            continue
        # proto3 JSON omits zero-valued indexes
        idx = el.get("destinationIndex", 0)
        out[targets[idx].id] = round(int(el["duration"].rstrip("s")) / 60)
    return out


_lock = threading.Lock()
_places: dict[str, tuple[float, Place]] = {}
_searches: dict[str, tuple[float, list[str]]] = {}
_drive: dict[str, tuple[float, int]] = {}
_photos: "OrderedDict[str, tuple[bytes, str]]" = OrderedDict()
_DRIVE_TTL = 7 * 86400
_PHOTO_CACHE = 200


def _search_ids(key: str, fetch) -> list[str]:
    """Run (or reuse) a cached search, fill in drive times, return place ids. Call with _lock held."""
    now = time.monotonic()
    hit = _searches.get(key)
    if hit and hit[0] > now:
        return hit[1]
    try:
        with httpx.Client(timeout=httpx.Timeout(15.0)) as client:
            found = [_parse(r) for r in fetch(client)]
            need = [p for p in found if not (p.id in _drive and _drive[p.id][0] > now)]
            for pid, mins in _drive_minutes(client, need).items():
                _drive[pid] = (now + _DRIVE_TTL, mins)
    except (httpx.HTTPError, KeyError, ValueError) as e:
        logger.warning("Places search %r failed: %s", key, e)
        _searches[key] = (now + 60, hit[1] if hit else [])
        return _searches[key][1]
    for p in found:
        _places[p.id] = (now + settings.PLACES_CACHE_TTL, p)
    ids = [p.id for p in found]
    _searches[key] = (now + settings.PLACES_CACHE_TTL, ids)
    logger.info("Places search %r: %d results", key, len(ids))
    return ids


def get(place_id: str) -> Optional[Place]:
    with _lock:
        hit = _places.get(place_id)
        if not hit:
            return None
        p = hit[1]
        d = _drive.get(place_id)
        p.drive_min = d[1] if d else None
        return p


def search(
    q: str = "",
    category: str = "casual",
    open_now: bool = False,
    avoid: Optional[set[str]] = None,
    shuffle: bool = False,
    limit: int = 24,
) -> list[Place]:
    """Family-friendly suggestions within RESTAURANT_MAX_DRIVE_MIN of home, best first."""
    if not enabled():
        return []
    q = q.strip()[:80]
    if q:
        key, fetch = f"text:{q.lower()}", lambda c: _text(c, q)
    else:
        category = category if category in CATEGORIES else "casual"
        key, fetch = f"cat:{category}", lambda c: _nearby(c, CATEGORIES[category][1])
    avoid = avoid or set()
    with _lock:
        ids = _search_ids(key, fetch)
        results = []
        for pid in ids:
            p = _places[pid][1]
            d = _drive.get(pid)
            p.drive_min = d[1] if d else None
            if not p.operational or p.good_for_children is False or p.primary_type in ADULT_TYPES | NOT_RESTAURANT_TYPES:
                continue
            if p.drive_min is None or p.drive_min > settings.RESTAURANT_MAX_DRIVE_MIN:
                continue
            if open_now and p.open_now is False:
                continue
            results.append(p)
    rnd = random.Random()
    # Recent winners sink to the bottom; shuffle keeps better-scored places likelier near the top.
    results.sort(key=lambda p: (p.id in avoid, -(p.score() + (rnd.random() * 1.5 if shuffle else 0))))
    return results[:limit]


def photo(place_id: str) -> Optional[tuple[bytes, str]]:
    """First photo for a cached place (small in-memory LRU; photos are billed per fetch)."""
    if not enabled() or not valid_place_id(place_id):
        return None
    with _lock:
        if place_id in _photos:
            _photos.move_to_end(place_id)
            return _photos[place_id]
        hit = _places.get(place_id)
    name = hit[1].photo_name if hit else None
    try:
        with httpx.Client(timeout=httpx.Timeout(15.0), follow_redirects=True) as client:
            if not hit:
                # Not in the search cache (restart/expiry): look up just the photo reference.
                details = client.get(f"{PLACES_URL}/places/{place_id}", headers=_headers("photos"))
                photos = details.json().get("photos") or [] if details.status_code == 200 else []
                name = photos[0].get("name") if photos else None
            if not name:
                return None
            resp = client.get(f"{PLACES_URL}/{name}/media", params={"maxHeightPx": "360", "maxWidthPx": "480"},
                              headers={"X-Goog-Api-Key": settings.GOOGLE_MAPS_API_KEY})
        if resp.status_code != 200:
            return None
    except (httpx.HTTPError, ValueError) as e:
        logger.warning("Places photo fetch failed for %s: %s", place_id, e)
        return None
    data = (resp.content, resp.headers.get("content-type", "image/jpeg"))
    with _lock:
        _photos[place_id] = data
        while len(_photos) > _PHOTO_CACHE:
            _photos.popitem(last=False)
    return data
