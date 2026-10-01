"""In-memory station snapshot — avoids repeated SQLite scans on every request."""

from __future__ import annotations

from dataclasses import dataclass

from django.core.cache import cache

from fuelroute.models import FuelStation

_MEM_KEY = "stations:geocoded:v1"


@dataclass(frozen=True, slots=True)
class StationPoint:
    id: int
    opis_id: int
    name: str
    address: str
    city: str
    state: str
    retail_price: float
    latitude: float
    longitude: float


def invalidate_station_cache() -> None:
    cache.delete(_MEM_KEY)


def get_geocoded_stations() -> list[StationPoint]:
    cached = cache.get(_MEM_KEY)
    if cached is not None:
        return cached

    rows = list(
        FuelStation.objects.filter(latitude__isnull=False, longitude__isnull=False)
        .only(
            "id",
            "opis_id",
            "name",
            "address",
            "city",
            "state",
            "retail_price",
            "latitude",
            "longitude",
        )
        .iterator(chunk_size=2000)
    )
    points = [
        StationPoint(
            id=r.id,
            opis_id=r.opis_id,
            name=r.name,
            address=r.address,
            city=r.city,
            state=r.state,
            retail_price=float(r.retail_price),
            latitude=r.latitude,
            longitude=r.longitude,
        )
        for r in rows
    ]
    # Keep in file cache so next process also starts warm
    cache.set(_MEM_KEY, points, timeout=60 * 60 * 24)
    return points
