"""IP-based geolocation with in-memory cache and config override."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from .config_schema import LocationConfig

if TYPE_CHECKING:
    pass

log = logging.getLogger(__name__)

_IP_API_URL = (
    "http://ip-api.com/json/?fields=status,country,regionName,city,lat,lon,timezone"
)
_CACHE_TTL = 3600

_cache: LocationConfig | None = None
_cache_ts: float = 0.0


def _has_manual_override(cfg: LocationConfig) -> bool:
    return bool(cfg.city or cfg.region or cfg.country or cfg.timezone)


def _fetch_ip_geolocation() -> LocationConfig:
    import httpx

    try:
        resp = httpx.get(_IP_API_URL, timeout=5.0)
        resp.raise_for_status()
        data = resp.json()
    except Exception:
        log.debug("ip geolocation fetch failed", exc_info=True)
        return LocationConfig()
    if data.get("status") != "success":
        log.debug("ip-api returned non-success: %s", data)
        return LocationConfig()
    return LocationConfig(
        city=data.get("city", ""),
        region=data.get("regionName", ""),
        country=data.get("country", ""),
        timezone=data.get("timezone", ""),
        lat=float(data.get("lat", 0)),
        lon=float(data.get("lon", 0)),
    )


def get_location() -> LocationConfig:
    """Return cached location, auto-detecting via IP if needed.

    Checks config for manual override first. Falls back to IP geolocation
    on first call, then caches for the process lifetime.
    """
    global _cache, _cache_ts

    try:
        from .config_file import load as load_config

        cfg = load_config().location
    except Exception:
        cfg = LocationConfig()

    if cfg.disabled:
        return LocationConfig()

    if _has_manual_override(cfg):
        return cfg

    import time

    now = time.monotonic()
    if _cache is not None and (now - _cache_ts) < _CACHE_TTL:
        return _cache

    loc = _fetch_ip_geolocation()
    if loc.timezone or loc.city:
        _cache = loc
        _cache_ts = now
        return loc

    if _cache is not None:
        return _cache

    return LocationConfig()
