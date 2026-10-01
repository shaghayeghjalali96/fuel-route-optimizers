"""Resolve US place names without hitting external geocoders when possible."""

from __future__ import annotations

import csv
import re
from functools import lru_cache
from pathlib import Path

from django.conf import settings

# "Chicago, IL" / "Dallas TX" / "New York, New York"
_PLACE_RE = re.compile(
    r"^\s*(?P<city>.+?)\s*,?\s+(?P<state>[A-Za-z]{2}|[A-Za-z .]+)\s*$"
)

_STATE_NAMES = {
    "alabama": "AL",
    "alaska": "AK",
    "arizona": "AZ",
    "arkansas": "AR",
    "california": "CA",
    "colorado": "CO",
    "connecticut": "CT",
    "delaware": "DE",
    "district of columbia": "DC",
    "florida": "FL",
    "georgia": "GA",
    "hawaii": "HI",
    "idaho": "ID",
    "illinois": "IL",
    "indiana": "IN",
    "iowa": "IA",
    "kansas": "KS",
    "kentucky": "KY",
    "louisiana": "LA",
    "maine": "ME",
    "maryland": "MD",
    "massachusetts": "MA",
    "michigan": "MI",
    "minnesota": "MN",
    "mississippi": "MS",
    "missouri": "MO",
    "montana": "MT",
    "nebraska": "NE",
    "nevada": "NV",
    "new hampshire": "NH",
    "new jersey": "NJ",
    "new mexico": "NM",
    "new york": "NY",
    "north carolina": "NC",
    "north dakota": "ND",
    "ohio": "OH",
    "oklahoma": "OK",
    "oregon": "OR",
    "pennsylvania": "PA",
    "rhode island": "RI",
    "south carolina": "SC",
    "south dakota": "SD",
    "tennessee": "TN",
    "texas": "TX",
    "utah": "UT",
    "vermont": "VT",
    "virginia": "VA",
    "washington": "WA",
    "west virginia": "WV",
    "wisconsin": "WI",
    "wyoming": "WY",
}


def _norm_state(raw: str) -> str | None:
    s = raw.strip().upper()
    if len(s) == 2 and s.isalpha():
        return s
    return _STATE_NAMES.get(raw.strip().lower())


@lru_cache(maxsize=1)
def _city_index() -> dict[tuple[str, str], tuple[float, float]]:
    path = Path(settings.US_CITIES_CSV_PATH)
    index: dict[tuple[str, str], tuple[float, float]] = {}
    if not path.exists():
        return index
    with path.open(newline="", encoding="utf-8-sig") as fh:
        reader = csv.DictReader(fh)
        if reader.fieldnames:
            reader.fieldnames = [(h or "").strip() for h in reader.fieldnames]
        for raw in reader:
            row = {(k or "").strip(): (v or "").strip() for k, v in raw.items()}
            state = (row.get("STATE_CODE") or "").upper()
            city = row.get("CITY") or ""
            lat_s = row.get("LATITUDE")
            lon_s = row.get("LONGITUDE")
            if not state or not city or not lat_s or not lon_s:
                continue
            key = (city.upper(), state)
            if key in index:
                continue
            try:
                index[key] = (float(lat_s), float(lon_s))
            except ValueError:
                continue
    return index


def parse_city_state(query: str) -> tuple[str, str] | None:
    m = _PLACE_RE.match(query.strip())
    if not m:
        return None
    state = _norm_state(m.group("state"))
    if not state:
        return None
    city = " ".join(m.group("city").strip().split())
    return city, state


def local_geocode(query: str) -> tuple[float, float] | None:
    """
    Geocode 'City, ST' from the bundled US cities file (no network).

    Returns None when the query is not a simple city/state pair or is unknown.
    """
    parsed = parse_city_state(query)
    if not parsed:
        return None
    city, state = parsed
    coords = _city_index().get((city.upper(), state))
    if coords:
        return coords
    # light fallback: prefix match within state
    city_u = city.upper()
    for (c, st), ll in _city_index().items():
        if st == state and (c.startswith(city_u) or city_u.startswith(c)):
            return ll
    return None
