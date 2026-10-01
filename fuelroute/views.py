from __future__ import annotations

import hashlib
import json
import logging

from django.conf import settings
from django.core.cache import cache
from django.http import JsonResponse
from django.shortcuts import render
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.csrf import csrf_exempt

from fuelroute.services.external import ExternalServiceError, fetch_route, geocode_place
from fuelroute.services.planner import plan_fuel_stops, serialize_stop, stations_along_route

logger = logging.getLogger(__name__)

RESPONSE_CACHE_TTL = 60 * 60 * 6  # 6 hours


def health(_request):
    return JsonResponse({"status": "ok"})


def map_page(request):
    """Simple Leaflet map UI that calls /api/route/."""
    return render(request, "map.html")


def _route_cache_key(start: str, finish: str) -> str:
    # v3: faster station index + UI refresh
    raw = f"v3|{start.strip().lower()}|{finish.strip().lower()}"
    return "route_response:" + hashlib.sha256(raw.encode()).hexdigest()[:40]


def _hours_minutes(seconds: float) -> str:
    total_min = int(round(seconds / 60.0))
    hours, minutes = divmod(total_min, 60)
    if hours and minutes:
        return f"{hours} hr {minutes} min"
    if hours:
        return f"{hours} hr"
    return f"{minutes} min"


def _build_readable_stops(stops) -> list[dict]:
    readable = []
    for i, stop in enumerate(stops, start=1):
        base = serialize_stop(stop)
        readable.append(
            {
                "stop_number": i,
                "station_name": base["name"],
                "location": f"{base['city']}, {base['state']}",
                "highway_address": base["address"],
                "price_per_gallon_usd": round(base["retail_price_usd"], 3),
                "miles_from_start": base["distance_along_route_miles"],
                "gallons_to_buy": base["gallons"],
                "cost_at_this_stop_usd": base["cost_usd"],
                "why_this_stop": base.get("reason") or "",
                "coordinates": {
                    "latitude": base["latitude"],
                    "longitude": base["longitude"],
                },
                "opis_id": base["opis_id"],
            }
        )
    return readable


@method_decorator(csrf_exempt, name="dispatch")
class RouteFuelView(View):
    """
    GET/POST /api/route/?start=Chicago,IL&finish=Dallas,TX

    External map/route budget:
      - Ideal: 1 OSRM call (start/finish resolved locally or from cache)
      - Acceptable: up to 3 total (2 Nominatim + 1 OSRM) on a cold miss
      - Repeat queries: 0 external calls (full response cache)
    """

    def get(self, request):
        return self._handle(request.GET)

    def post(self, request):
        try:
            body = json.loads(request.body.decode("utf-8") or "{}")
        except json.JSONDecodeError:
            body = {}
        if not body:
            body = request.POST
        return self._handle(body)

    def _handle(self, params):
        start = (params.get("start") or "").strip()
        finish = (params.get("finish") or "").strip()
        if not start or not finish:
            return JsonResponse(
                {
                    "error": "Provide both 'start' and 'finish' locations in the USA.",
                    "example": "/api/route/?start=Chicago, IL&finish=Dallas, TX",
                },
                status=400,
            )

        cache_key = _route_cache_key(start, finish)
        cached = cache.get(cache_key)
        if cached is not None:
            payload = dict(cached)
            payload["cache"] = {
                "response_cache_hit": True,
                "external_api_calls": 0,
                "note": "Full result served from cache — no map/routing HTTP calls.",
            }
            return JsonResponse(payload)

        api_calls = 0
        try:
            start_ll, start_meta = geocode_place(start)
            finish_ll, finish_meta = geocode_place(finish)
            api_calls += start_meta["external_http_calls"]
            api_calls += finish_meta["external_http_calls"]

            route, route_meta = fetch_route(start_ll, finish_ll)
            api_calls += route_meta["external_http_calls"]

            _coords, along = stations_along_route(route["polyline"])
            stops, total_cost, gallons = plan_fuel_stops(
                along, route["distance_miles"]
            )
        except ExternalServiceError as exc:
            return JsonResponse({"error": str(exc)}, status=502)
        except ValueError as exc:
            return JsonResponse({"error": str(exc)}, status=422)
        except Exception:
            logger.exception("Unexpected routing failure")
            return JsonResponse(
                {"error": "Unexpected server error while planning the route."},
                status=500,
            )

        readable_stops = _build_readable_stops(stops)
        distance = round(route["distance_miles"], 1)
        duration_min = round(route["duration_seconds"] / 60.0, 1)
        map_url = (
            "https://www.openstreetmap.org/directions?"
            f"engine=fossgis_osrm_car&route={start_ll[0]}%2C{start_ll[1]}"
            f"%3B{finish_ll[0]}%2C{finish_ll[1]}"
        )

        summary = (
            f"Drive {distance} miles from {start} to {finish} "
            f"({_hours_minutes(route['duration_seconds'])}). "
            f"At {settings.VEHICLE_MPG} mpg you need about {gallons} gallons. "
            f"Optimal fuel plan uses {len(readable_stops)} stop(s) "
            f"for a total fuel cost of ${total_cost:.2f}."
        )

        stop_features = [
            {
                "type": "Feature",
                "geometry": {
                    "type": "Point",
                    "coordinates": [
                        s["coordinates"]["longitude"],
                        s["coordinates"]["latitude"],
                    ],
                },
                "properties": s,
            }
            for s in readable_stops
        ]

        payload = {
            "summary": summary,
            "start": {
                "query": start,
                "latitude": start_ll[0],
                "longitude": start_ll[1],
                "resolved_via": start_meta["source"],
            },
            "finish": {
                "query": finish,
                "latitude": finish_ll[0],
                "longitude": finish_ll[1],
                "resolved_via": finish_meta["source"],
            },
            "vehicle": {
                "max_range_miles": settings.VEHICLE_MAX_RANGE_MILES,
                "miles_per_gallon": settings.VEHICLE_MPG,
                "tank_gallons": settings.VEHICLE_MAX_RANGE_MILES / settings.VEHICLE_MPG,
            },
            "route": {
                "distance_miles": distance,
                "duration_minutes": duration_min,
                "duration_text": _hours_minutes(route["duration_seconds"]),
                "map_url": map_url,
                "polyline": route["polyline"],
                "how_to_use_polyline": (
                    "Decode this Google/OSRM-encoded polyline (precision 5) "
                    "into lat/lng points to draw the driving path on a map."
                ),
            },
            "fuel_plan": {
                "gallons_needed": gallons,
                "total_fuel_cost_usd": total_cost,
                "total_fuel_cost_text": f"${total_cost:.2f}",
                "number_of_fuel_stops": len(readable_stops),
                "strategy": (
                    "Cost-effective: always prefer the lowest $/gal among stations "
                    "you can reach before the tank runs out. Fill up at cheap stations "
                    "when later fuel is more expensive; if cheaper fuel is ahead, "
                    "buy only enough to get there."
                ),
                "fuel_stops": readable_stops,
            },
            "map_geojson": {
                "type": "FeatureCollection",
                "features": [
                    {
                        "type": "Feature",
                        "geometry": {
                            "type": "Point",
                            "coordinates": [start_ll[1], start_ll[0]],
                        },
                        "properties": {"role": "start", "label": start},
                    },
                    {
                        "type": "Feature",
                        "geometry": {
                            "type": "Point",
                            "coordinates": [finish_ll[1], finish_ll[0]],
                        },
                        "properties": {"role": "finish", "label": finish},
                    },
                    *stop_features,
                ],
            },
            "cache": {
                "response_cache_hit": False,
                "external_api_calls": api_calls,
                "geocode_start": start_meta,
                "geocode_finish": finish_meta,
                "routing": route_meta,
                "note": (
                    "Ideal: 1 OSRM routing call. Acceptable: up to 3 total. "
                    "Stations never call the map API — they are pre-geocoded locally."
                ),
            },
            "meta": {
                "stations_considered_on_route": len(along),
                "routing_provider": "OSRM (router.project-osrm.org)",
                "station_coordinates": (
                    "Fuel CSV has exit-style addresses (no lat/lng). "
                    "Stations use City+State centroids from data/us_cities.csv."
                ),
            },
        }

        # Store without the "this request" cache flags rewritten on hit
        cache.set(cache_key, payload, timeout=RESPONSE_CACHE_TTL)
        return JsonResponse(payload)
