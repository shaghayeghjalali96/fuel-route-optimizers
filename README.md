# Fuel Route API — Django assessment

API that plans a US driving route and picks **cost-effective fuel stops** from the attached OPIS price list.

## Important: no lat/lng in the fuel CSV

Rows look like:

```text
TA SEYMOUR TRAVEL CENTER,"I-65, EXIT 50 & US-50 & US-31",Seymour,IN,...,3.959
```

That is a **highway exit address**, not a street geocode. This project therefore:

1. Imports each station’s **City + State**
2. Resolves approximate coordinates from `data/us_cities.csv` (offline, once)
3. Matches stations to the route corridor in-process (no geocoder flood)

## External API budget (assignment requirement)

| Situation | External HTTP calls |
|-----------|---------------------|
| `City, ST` start/finish (local lookup) | **1** — OSRM route only (ideal) |
| Unusual place names (Nominatim) | up to **3** — 2 geocode + 1 route (acceptable) |
| Same start/finish again | **0** — file cache |

Fuel stations **never** call the map API.

Caches (file-based under `.cache/`):

- Full API response (6h)
- OSRM routes (24h)
- Nominatim geocodes (30d)

## Stack

- Django **6.1.1**
- **OSRM** — routing (`https://router.project-osrm.org`)
- **Nominatim** — fallback geocode only when local cities miss
- SQLite + Leaflet UI at `/`

## Setup

```bash
python -m pip install -r requirements.txt
python manage.py migrate
python manage.py load_stations --replace
python manage.py runserver
```

- Map UI: http://127.0.0.1:8000/
- API: http://127.0.0.1:8000/api/route/?start=Chicago,%20IL&finish=Dallas,%20TX

## API response (readable)

Top-level fields:

| Field | Meaning |
|-------|---------|
| `summary` | One-sentence plain-English result |
| `fuel_plan.total_fuel_cost_text` | e.g. `"$278.51"` |
| `fuel_plan.fuel_stops` | Ordered stops with price, gallons, cost, highway address |
| `route.map_url` | Open the drive in OpenStreetMap |
| `route.polyline` | Encoded path for drawing on a map |
| `cache.external_api_calls` | How many map/geocode HTTP calls this request used |

## Assumptions

| Item | Value |
|------|--------|
| Max range | 500 miles |
| Economy | 10 mpg |
| Start tank | Full |
| Corridor | ~12 miles from route |

Longer explanation of station matching, cost math, and start-point detail: [docs/HOW_IT_WORKS.md](docs/HOW_IT_WORKS.md)

## Project layout

```text
config/           Django settings + URLs
fuelroute/        models, API, planner, load_stations command
data/us_cities.csv
fuel-prices-for-be-assessment.csv
templates/map.html
.cache/           file-based response/route/geocode cache
```
