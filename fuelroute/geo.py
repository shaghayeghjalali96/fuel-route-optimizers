"""Geometry helpers for routing and station matching."""

from __future__ import annotations

import math
from typing import Iterable, Sequence


EARTH_RADIUS_MILES = 3958.7613


def haversine_miles(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in miles between two WGS84 points."""
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = (
        math.sin(dphi / 2) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    )
    return 2 * EARTH_RADIUS_MILES * math.asin(math.sqrt(a))


def decode_polyline(encoded: str, precision: int = 5) -> list[tuple[float, float]]:
    """Decode Google/OSRM encoded polyline into (lat, lon) pairs."""
    coordinates: list[tuple[float, float]] = []
    index, lat, lon = 0, 0, 0
    factor = 10**precision

    while index < len(encoded):
        for result_shift in (True, False):
            result = shift = 0
            while True:
                b = ord(encoded[index]) - 63
                index += 1
                result |= (b & 0x1F) << shift
                shift += 5
                if b < 0x20:
                    break
            delta = ~(result >> 1) if result & 1 else (result >> 1)
            if result_shift:
                lat += delta
            else:
                lon += delta
        coordinates.append((lat / factor, lon / factor))
    return coordinates


def simplify_polyline(
    coords: Sequence[tuple[float, float]], max_points: int = 250
) -> list[tuple[float, float]]:
    """Keep endpoints and evenly sample vertices for faster corridor checks."""
    if len(coords) <= max_points:
        return list(coords)
    step = (len(coords) - 1) / (max_points - 1)
    out = [coords[0]]
    for i in range(1, max_points - 1):
        out.append(coords[int(round(i * step))])
    out.append(coords[-1])
    return out


def cumulative_distances(coords: Sequence[tuple[float, float]]) -> list[float]:
    """Miles along the polyline at each vertex (index 0 => 0)."""
    dists = [0.0]
    for i in range(1, len(coords)):
        dists.append(
            dists[-1]
            + haversine_miles(
                coords[i - 1][0], coords[i - 1][1], coords[i][0], coords[i][1]
            )
        )
    return dists


def project_point_onto_route(
    lat: float,
    lon: float,
    coords: Sequence[tuple[float, float]],
    cum_dist: Sequence[float],
    *,
    start_idx: int = 1,
) -> tuple[float, float, int]:
    """
    Approximate distance-along-route and perpendicular distance to nearest segment.

    Returns (distance_along_route_miles, distance_to_route_miles, best_segment_index).
    """
    best_along = 0.0
    best_perp = float("inf")
    best_i = start_idx

    # Local search window: stations progress along the route, so we can start near
    # the previous match and only scan a band of segments.
    n = len(coords)
    window = max(40, n // 8)
    lo = max(1, start_idx - 5)
    hi = min(n, lo + window)

    def scan(a: int, b: int) -> None:
        nonlocal best_along, best_perp, best_i
        for i in range(a, b):
            lat1, lon1 = coords[i - 1]
            lat2, lon2 = coords[i]
            seg_len = cum_dist[i] - cum_dist[i - 1]
            if seg_len <= 1e-9:
                continue

            mid_lat = (lat1 + lat2) / 2.0
            cos_lat = max(math.cos(math.radians(mid_lat)), 1e-6)
            x = (lon - lon1) * cos_lat
            y = lat - lat1
            dx = (lon2 - lon1) * cos_lat
            dy = lat2 - lat1
            seg2 = dx * dx + dy * dy
            t = 0.0 if seg2 == 0 else max(0.0, min(1.0, (x * dx + y * dy) / seg2))
            proj_lat = lat1 + t * (lat2 - lat1)
            proj_lon = lon1 + t * (lon2 - lon1)
            # Cheap planar approx first, then haversine only for near hits
            dlat = (lat - proj_lat) * 69.0
            dlon = (lon - proj_lon) * 69.0 * cos_lat
            perp_approx = math.hypot(dlat, dlon)
            if perp_approx > best_perp + 2:
                continue
            perp = haversine_miles(lat, lon, proj_lat, proj_lon)
            along = cum_dist[i - 1] + t * seg_len
            if perp < best_perp:
                best_perp = perp
                best_along = along
                best_i = i

    scan(lo, hi)
    # If nothing close, fall back to full scan once
    if best_perp == float("inf") or best_perp > 50:
        best_perp = float("inf")
        scan(1, n)

    return best_along, best_perp, best_i


def bounding_box(
    coords: Iterable[tuple[float, float]], padding_degrees: float = 0.35
) -> tuple[float, float, float, float]:
    lats = [c[0] for c in coords]
    lons = [c[1] for c in coords]
    return (
        min(lats) - padding_degrees,
        max(lats) + padding_degrees,
        min(lons) - padding_degrees,
        max(lons) + padding_degrees,
    )
