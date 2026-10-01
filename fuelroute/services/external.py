"""External geocoding + routing clients. Keep call count low per request."""

from __future__ import annotations

import hashlib
import logging
from typing import Any

import requests
from django.conf import settings
from django.core.cache import cache

from fuelroute.services.local_geocode import local_geocode

logger = logging.getLogger(__name__)

USER_AGENT = "FuelRouteAssessment/1.0 (django coding exercise)"

# How long to keep external results (seconds)
GEOCODE_CACHE_TTL = 60 * 60 * 24 * 30  # 30 days
ROUTE_CACHE_TTL = 60 * 60 * 24  # 24 hours


class ExternalServiceError(Exception):
    pass


def _cache_key(prefix: str, value: str) -> str:
    digest = hashlib.sha256(value.strip().lower().encode("utf-8")).hexdigest()[:32]
    return f"{prefix}:{digest}"


def geocode_place(query: str) -> tuple[tuple[float, float], dict[str, Any]]:
    """
    Resolve a place to (lat, lon).

    Prefer local US-cities lookup (0 network calls). Fall back to cached
    Nominatim, then a live Nominatim request.

    Returns ((lat, lon), meta) where meta describes the source / cache hit.
    """
    query = query.strip()
    local = local_geocode(query)
    if local:
        return local, {
            "source": "local_us_cities",
            "cache_hit": True,
            "external_http_calls": 0,
        }

    cache_key = _cache_key("geocode", query)
    cached = cache.get(cache_key)
    if cached:
        return cached, {
            "source": "nominatim_cache",
            "cache_hit": True,
            "external_http_calls": 0,
        }

    try:
        response = requests.get(
            settings.NOMINATIM_URL,
            params={
                "q": query,
                "format": "json",
                "limit": 1,
                "countrycodes": "us",
            },
            headers={"User-Agent": USER_AGENT},
            timeout=20,
        )
        response.raise_for_status()
        data = response.json()
    except requests.RequestException as exc:
        raise ExternalServiceError(f"Geocoding failed: {exc}") from exc

    if not data:
        raise ExternalServiceError(f"Could not geocode location: {query!r}")

    lat = float(data[0]["lat"])
    lon = float(data[0]["lon"])
    result = (lat, lon)
    cache.set(cache_key, result, timeout=GEOCODE_CACHE_TTL)
    return result, {
        "source": "nominatim",
        "cache_hit": False,
        "external_http_calls": 1,
    }


def fetch_route(
    start: tuple[float, float], finish: tuple[float, float]
) -> tuple[dict[str, Any], dict[str, Any]]:
    """
    One OSRM driving route call (cached by rounded coordinates).

    Returns (route_dict, meta).
    """
    start_lat, start_lon = start
    finish_lat, finish_lon = finish
    # Round coords so tiny float noise still shares a cache entry
    cache_key = _cache_key(
        "osrm",
        f"{start_lat:.5f},{start_lon:.5f}|{finish_lat:.5f},{finish_lon:.5f}",
    )
    cached = cache.get(cache_key)
    if cached:
        return cached, {
            "source": "osrm_cache",
            "cache_hit": True,
            "external_http_calls": 0,
        }

    url = (
        f"{settings.OSRM_URL}/"
        f"{start_lon},{start_lat};{finish_lon},{finish_lat}"
    )
    try:
        response = requests.get(
            url,
            params={"overview": "full", "geometries": "polyline", "steps": "false"},
            headers={"User-Agent": USER_AGENT},
            timeout=30,
        )
        response.raise_for_status()
        payload = response.json()
    except requests.RequestException as exc:
        raise ExternalServiceError(f"Routing failed: {exc}") from exc

    if payload.get("code") != "Ok" or not payload.get("routes"):
        raise ExternalServiceError(
            f"No driving route found ({payload.get('code', 'unknown')})."
        )

    route = payload["routes"][0]
    result = {
        "distance_miles": float(route["distance"]) / 1609.344,
        "duration_seconds": float(route["duration"]),
        "polyline": route["geometry"],
    }
    cache.set(cache_key, result, timeout=ROUTE_CACHE_TTL)
    return result, {
        "source": "osrm",
        "cache_hit": False,
        "external_http_calls": 1,
    }
