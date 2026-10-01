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
) -> tuple[list[FuelStop], float, float]:
    """
    Minimize fuel spend using retail prices, while respecting the 500-mile tank.
    """
    max_range = max_range if max_range is not None else settings.VEHICLE_MAX_RANGE_MILES
    mpg = mpg if mpg is not None else settings.VEHICLE_MPG
    reserve = reserve_miles if reserve_miles is not None else settings.FUEL_RESERVE_MILES
    usable = max(max_range - reserve, max_range * 0.5)

    gallons_consumed = total_distance_miles / mpg
    if total_distance_miles <= 0:
        return [], 0.0, 0.0

    stations = _dedupe_nearby(route_stations)
    if not stations:
        raise ValueError(
            "No fuel stations found near this route. "
            "Run: python manage.py load_stations --replace"
        )

    if total_distance_miles <= usable:
        best = min(stations, key=_price)
        cost = gallons_consumed * _price(best)
        return (
            [
                FuelStop(
                    station=best.station,
                    distance_along_miles=best.distance_along_miles,
                    gallons=round(gallons_consumed, 2),
                    cost_usd=round(cost, 2),
                    remaining_range_after_miles=max_range,
                    reason="Cheapest station on this route (trip fits in one tank).",
                )
            ],
            round(cost, 2),
            round(gallons_consumed, 2),
        )

    remaining_range = float(max_range)
    position = 0.0
    stops: list[FuelStop] = []
    total_cost = 0.0

    for _ in range(200):
        if position + remaining_range >= total_distance_miles - 1e-6:
            break

        reach = position + remaining_range
        reachable = _reachable(stations, position, reach)
        if not reachable:
            raise ValueError(
                "Cannot reach a fuel station before the tank runs out "
                f"(around mile {position:.0f} of {total_distance_miles:.0f})."
            )

        viable = [
            s
            for s in reachable
            if _can_continue(s, stations, total_distance_miles, max_range)
        ]
        pool = viable or reachable
        chosen = min(pool, key=lambda s: (_price(s), -s.distance_along_miles))

        miles_to_stop = chosen.distance_along_miles - position
        remaining_range -= miles_to_stop
        position = chosen.distance_along_miles
        price = _price(chosen)

        ahead_reach = position + max_range
        ahead = [
            s
            for s in stations
            if position < s.distance_along_miles <= ahead_reach
            and _can_continue(s, stations, total_distance_miles, max_range)
        ]
        cheaper_ahead = [s for s in ahead if _price(s) < price - 1e-9]

        if cheaper_ahead:
            target = min(cheaper_ahead, key=lambda s: s.distance_along_miles)
            miles_needed = target.distance_along_miles - position
            gallons_needed = max(0.0, (miles_needed - remaining_range) / mpg)
            gallons_needed = max(gallons_needed, 1.0 / mpg)
            reason = (
                f"Cheapest reachable now (${price:.3f}/gal); "
                f"buying only enough to reach cheaper fuel ahead "
                f"(${_price(target):.3f}/gal)."
            )
            remaining_range = remaining_range + gallons_needed * mpg
        else:
            gallons_needed = max(0.0, (max_range - remaining_range) / mpg)
            reason = (
                f"Lowest price in range (${price:.3f}/gal); "
                "filling up because later stations are not cheaper."
            )
            remaining_range = max_range

        gallons_needed = min(gallons_needed, max_range / mpg)
        cost = gallons_needed * price
        total_cost += cost
        stops.append(
            FuelStop(
                station=chosen.station,
                distance_along_miles=position,
                gallons=round(gallons_needed, 2),
                cost_usd=round(cost, 2),
                remaining_range_after_miles=round(remaining_range, 1),
                reason=reason,
            )
        )
    else:
        raise ValueError("Fuel planning failed; check station coverage along the route.")

    purchased = sum(s.gallons for s in stops)
    if purchased + 1e-6 < gallons_consumed and stops:
        missing = gallons_consumed - purchased
        best_stop = min(stops, key=lambda s: s.station.retail_price)
        best_stop.gallons = round(best_stop.gallons + missing, 2)
        extra = missing * best_stop.station.retail_price
        best_stop.cost_usd = round(best_stop.cost_usd + extra, 2)
        total_cost += extra

    return stops, round(total_cost, 2), round(gallons_consumed, 2)


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
