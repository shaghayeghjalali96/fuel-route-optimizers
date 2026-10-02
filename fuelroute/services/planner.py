"""Cost-effective fuel stop planner (price-first, range-aware)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from django.conf import settings

from fuelroute.geo import (
    bounding_box,
    cumulative_distances,
    decode_polyline,
    project_point_onto_route,
    simplify_polyline,
)
from fuelroute.services.station_index import StationPoint, get_geocoded_stations


@dataclass
class RouteStation:
    station: StationPoint
    distance_along_miles: float
    distance_to_route_miles: float


@dataclass
class FuelStop:
    station: StationPoint
    distance_along_miles: float
    gallons: float
    cost_usd: float
    remaining_range_after_miles: float
    reason: str = ""


def stations_along_route(polyline: str) -> tuple[list[tuple[float, float]], list[RouteStation]]:
    full_coords = decode_polyline(polyline)
    if len(full_coords) < 2:
        return full_coords, []

    # Fewer points = much faster corridor matching (still accurate enough)
    coords = simplify_polyline(full_coords, max_points=120)
    cum = cumulative_distances(coords)
    min_lat, max_lat, min_lon, max_lon = bounding_box(coords, padding_degrees=0.35)
    corridor = settings.ROUTE_CORRIDOR_MILES
    # Coarse reject: farther than corridor from every sampled vertex
    coarse_limit = corridor + 25

    # In-memory index (no per-request SQLite scan)
    # Precompute route sample for coarse distance checks
    sample = coords[:: max(1, len(coords) // 40)]
    if sample[-1] != coords[-1]:
        sample = list(sample) + [coords[-1]]

    along: list[RouteStation] = []
    seg_hint = 1
    for station in get_geocoded_stations():
        if not (min_lat <= station.latitude <= max_lat and min_lon <= station.longitude <= max_lon):
            continue

        # Fast planar approx to sampled route vertices (miles-ish)
        nearest = min(
            ((station.latitude - c[0]) * 69.0) ** 2
            + ((station.longitude - c[1]) * 54.0) ** 2
            for c in sample
        ) ** 0.5
        if nearest > coarse_limit:
            continue

        dist_along, dist_perp, seg_hint = project_point_onto_route(
            station.latitude, station.longitude, coords, cum, start_idx=seg_hint
        )
        if dist_perp <= corridor and 0 <= dist_along <= cum[-1]:
            along.append(
                RouteStation(
                    station=station,
                    distance_along_miles=dist_along,
                    distance_to_route_miles=dist_perp,
                )
            )

    along.sort(key=lambda s: (s.distance_along_miles, s.station.retail_price))
    return full_coords, along


def _dedupe_nearby(route_stations: Sequence[RouteStation]) -> list[RouteStation]:
    deduped: list[RouteStation] = []
    for rs in route_stations:
        if deduped and abs(rs.distance_along_miles - deduped[-1].distance_along_miles) < 1.0:
            if rs.station.retail_price < deduped[-1].station.retail_price:
                deduped[-1] = rs
            continue
        deduped.append(rs)
    return deduped


def _price(rs: RouteStation) -> float:
    return rs.station.retail_price


def _reachable(
    stations: Sequence[RouteStation], position: float, reach: float
) -> list[RouteStation]:
    return [s for s in stations if position < s.distance_along_miles <= reach + 1e-6]


def _can_continue(
    station: RouteStation,
    stations: Sequence[RouteStation],
    total_distance: float,
    max_range: float,
) -> bool:
    after = station.distance_along_miles + max_range
    if after >= total_distance - 1e-6:
        return True
    return any(
        station.distance_along_miles < o.distance_along_miles <= after for o in stations
    )


def plan_fuel_stops(
    route_stations: Sequence[RouteStation],
    total_distance_miles: float,
    *,
    max_range: float | None = None,
    mpg: float | None = None,
    reserve_miles: float | None = None,
    start_range_miles: float | None = None,
) -> tuple[list[FuelStop], float, float]:
    """
    Pick the cheapest fuel stops needed to finish the trip.

    - Starts with the fuel the driver already has (``start_range_miles``).
    - Once the fuel on board can reach the destination, planning stops: no
      stop is ever added after the driver can already make it.
    - Only fuel that is actually bought is counted toward the cost.
    """
    max_range = max_range if max_range is not None else settings.VEHICLE_MAX_RANGE_MILES
    mpg = mpg if mpg is not None else settings.VEHICLE_MPG
    reserve = reserve_miles if reserve_miles is not None else settings.FUEL_RESERVE_MILES
    full = float(max_range)
    # Distance we are willing to cover on one leg before refueling (safety margin).
    usable = max(full - reserve, full * 0.5)

    gallons_for_trip = round(total_distance_miles / mpg, 2)
    if total_distance_miles <= 0:
        return [], 0.0, 0.0

    range_left = full if start_range_miles is None else max(0.0, min(float(start_range_miles), full))

    stations = _dedupe_nearby(route_stations)
    if not stations:
        raise ValueError(
            "No fuel stations found near this route. "
            "Run: python manage.py load_stations --replace"
        )

    position = 0.0
    stops: list[FuelStop] = []
    total_cost = 0.0

    for _ in range(500):
        # Enough fuel on board to reach the destination → done, no extra stop.
        if range_left >= (total_distance_miles - position) - 1e-6:
            break

        reach = position + min(range_left, usable)
        reachable = _reachable(stations, position, reach)
        if not reachable:
            raise ValueError(
                "Cannot reach a fuel station before fuel runs out "
                f"(around mile {position:.0f} of {total_distance_miles:.0f}). "
                "Start with more fuel in the tank."
            )

        viable = [s for s in reachable if _can_continue(s, stations, total_distance_miles, usable)]
        pool = viable or reachable
        chosen = min(pool, key=lambda s: (_price(s), -s.distance_along_miles))

        range_left -= chosen.distance_along_miles - position
        position = chosen.distance_along_miles
        price = _price(chosen)

        remaining_trip = total_distance_miles - position

        if remaining_trip <= full + 1e-6:
            # Destination is reachable on this one fill → last stop. Buy just
            # enough to get there instead of adding another stop for pennies.
            need_range = remaining_trip
            reason = "Last stop; buy enough to reach the destination."
        else:
            ahead = [
                s
                for s in stations
                if position < s.distance_along_miles <= position + usable
                and _can_continue(s, stations, total_distance_miles, usable)
            ]
            cheaper_ahead = [s for s in ahead if _price(s) < price - 1e-9]
            if cheaper_ahead:
                target = min(cheaper_ahead, key=lambda s: s.distance_along_miles)
                need_range = target.distance_along_miles - position
                reason = "Cheapest nearby; buy enough to reach cheaper fuel ahead."
            else:
                need_range = full
                reason = "Cheapest nearby; fill up here."

        buy_range = max(0.0, need_range - range_left)
        if buy_range <= 1e-6:
            buy_range = min(full, remaining_trip) - range_left
        if buy_range <= 1e-6:
            break

        gallons = max(round(buy_range / mpg, 2), 0.01)
        range_left += buy_range
        cost = round(gallons * price, 2)
        total_cost += cost
        stops.append(
            FuelStop(
                station=chosen.station,
                distance_along_miles=position,
                gallons=gallons,
                cost_usd=cost,
                remaining_range_after_miles=round(range_left, 1),
                reason=reason,
            )
        )
    else:
        raise ValueError("Fuel planning failed; check station coverage along the route.")

    return stops, round(total_cost, 2), gallons_for_trip


def serialize_stop(stop: FuelStop) -> dict:
    s = stop.station
    return {
        "opis_id": s.opis_id,
        "name": s.name,
        "address": s.address,
        "city": s.city,
        "state": s.state,
        "retail_price_usd": s.retail_price,
        "latitude": s.latitude,
        "longitude": s.longitude,
        "distance_along_route_miles": round(stop.distance_along_miles, 1),
        "gallons": stop.gallons,
        "cost_usd": stop.cost_usd,
        "reason": stop.reason,
    }
