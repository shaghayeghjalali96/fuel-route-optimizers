# FuelRoute

Plan a US driving route and get the **cheapest fuel stops** along the way, using the
OPIS retail price list. Comes with a Django JSON API and a Leaflet map UI.

- Map UI: `http://127.0.0.1:8000/`
- API: `http://127.0.0.1:8000/api/route/?start=Chicago,%20IL&finish=Dallas,%20TX`

---

## TL;DR (the simple version)

1. You type a **start**, a **finish**, and **how much fuel you have now**.
2. We geocode both places, ask OSRM for **one** driving route, and draw it on the map.
3. We find fuel stations near that route and pick the **lowest‑priced stops** you can
   reach before the tank runs dry.
4. You see the stops, how many gallons to buy at each, and the total cost. A button
   lets you also show the **stations we didn’t pick**.

Vehicle assumptions: **500‑mile** tank, **10 mpg** (so a full tank = **50 gallons**).

---

## How it works (the detailed version)

### 1. The fuel data has no coordinates

Rows in `fuel-prices-for-be-assessment.csv` look like:

```text
TA SEYMOUR TRAVEL CENTER,"I-65, EXIT 50 & US-50 & US-31",Seymour,IN,...,3.959
```

That address is a **highway exit label**, not a street geocode. So at import time
(`python manage.py load_stations`) we take each station’s **City + State** and look up an
approximate coordinate from `data/us_cities.csv` — completely offline, done once. The
geocoded stations are then held in an in‑memory snapshot so each request doesn’t re‑scan
the database.

### 2. Finding stations near the route

- We decode the OSRM route polyline and simplify it to ~120 points for speed.
- Stations are first filtered with a configurable **spatial corridor** around the route
  (`ROUTE_CORRIDOR_MILES`, default ~12 mi). This is a fast candidate‑selection
  approximation — it avoids a routing call per station and is not meant to be an exact
  road‑detour distance.
- Survivors are projected onto the route to get their “miles from start,” then sorted.

### 3. Choosing the cheapest stops (the planner)

Starting from the fuel you already have:

1. **If the fuel on board can already reach the destination, we stop planning — no stop
   is added.** (This is why a short trip, or starting with a full tank, can need zero
   stops.)
2. Otherwise, among the stations you can still reach before running low, pick the
   **cheapest** one.
3. Decide how much to buy there:
   - **If the destination is within one tank from this stop → it’s the last stop:** buy
     just enough to finish. We do **not** add another stop just to save a few cents on
     the final miles.
   - Else if there’s cheaper fuel reachable ahead → buy just enough to get there.
   - Else → fill up.
4. Repeat. Only fuel you actually **buy** is counted toward the cost.

Example — Chicago, IL → Dallas, TX (913 mi, full tank):

```
2 stops, total ~$119.84
  1  QUIKTRIP #605       mile 288.9   $2.899/gal   (fill up here)
  2  RAPID ROBERTS #122  mile 555.2   $2.899/gal   (last stop; buy enough to reach the destination)
```

### 4. The map

The UI uses **Esri World Street Map** tiles — free, no API key, and not subject to the
OpenStreetMap tile usage‑policy block. The route is drawn from the encoded polyline;
chosen stops are numbered pins, and the “show other stations” button plots the
not‑chosen candidates as gray dots.

---

## API endpoints

| Method | Path | What it does |
|--------|------|--------------|
| GET/POST | `/api/route/` | Plan a route + cheapest fuel stops |
| GET | `/api/stations/` | All geocoded stations as GeoJSON |
| GET | `/health/` | Health check |
| GET | `/` | Leaflet map UI |

### `/api/route/` parameters

| Param | Required | Meaning |
|-------|----------|---------|
| `start` | yes | Start location, e.g. `Chicago, IL` or a full address |
| `finish` | yes | Destination, same formats |
| `start_gallons` | no | Fuel you have now (0–50). Omit = assume a full tank |

### Key response fields

| Field | Meaning |
|-------|---------|
| `summary` | One‑line plain result, e.g. `913.4 mi, 17 hr 8 min. 2 fuel stop(s), total $119.84.` |
| `route.distance_miles` / `route.duration_text` | Trip distance and drive time |
| `route.polyline` | Encoded path to draw on a map |
| `fuel_plan.start_fuel_gallons` | What you started with (`null` = full tank) |
| `fuel_plan.gallons_needed` | Gallons the whole trip burns |
| `fuel_plan.total_fuel_cost_text` | e.g. `"$119.84"` |
| `fuel_plan.fuel_stops[]` | Ordered stops: price, gallons to buy, cost, address, why |
| `other_stations[]` | Stations near the route that were **not** chosen |
| `cache.external_api_calls` | How many external HTTP calls this request made |

---

## How many external API calls?

Fuel stations **never** hit a map/geocode API — they’re pre‑geocoded locally.

| Situation | External HTTP calls |
|-----------|---------------------|
| `City, ST` start/finish (resolved from local city DB) | **1** — OSRM route only (the ideal case) |
| Unusual place / landmark names (needs Nominatim) | up to **3** — 2 geocode + 1 route |
| Same start/finish + fuel level again | **0** — served from cache |
| `/api/stations/` | **0** — local data only |

Caches are file‑based under `.cache/`:

- Full API response — 6 hours (keyed by start, finish, **and** starting fuel)
- OSRM routes — 24 hours
- Nominatim geocodes — 30 days
- Geocoded station snapshot — 24 hours

---

## Setup

```bash
python -m pip install -r requirements.txt
python manage.py migrate
python manage.py load_stations --replace
python manage.py runserver
```

Then open `http://127.0.0.1:8000/`.

### Postman

Import `postman_collection.json`. It has three requests (Route + fuel plan, All stations,
Health) and a `base_url` variable set to `http://127.0.0.1:8000`.

---

## Stack

- Django **6.1.1**, SQLite
- **OSRM** (`router.project-osrm.org`) — routing
- **Nominatim** — fallback geocode only when the local city lookup misses
- **Leaflet** + **Esri** tiles — map UI

## Configuration (`config/settings.py`)

| Setting | Default | Meaning |
|---------|---------|---------|
| `VEHICLE_MAX_RANGE_MILES` | 500 | Tank range |
| `VEHICLE_MPG` | 10 | Fuel economy (tank = 50 gal) |
| `ROUTE_CORRIDOR_MILES` | 12 | How far a station may sit from the route |
| `FUEL_RESERVE_MILES` | 40 | Safety margin kept before refueling |

## Project layout

```text
config/            Django settings + URLs
fuelroute/
  views.py         API endpoints (route, stations, health)
  services/
    planner.py     cheapest-stop logic
    external.py    OSRM + Nominatim clients (cached)
    station_index.py   in-memory geocoded station snapshot
    local_geocode.py   offline City/State lookup
  management/commands/load_stations.py
data/us_cities.csv
fuel-prices-for-be-assessment.csv
templates/map.html
postman_collection.json
.cache/            file-based response/route/geocode cache
```

More background on station matching and cost math: [docs/HOW_IT_WORKS.md](docs/HOW_IT_WORKS.md)
