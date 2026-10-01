# How FuelRoute works

## 1. What is the CSV / Excel address for?

The fuel file (`fuel-prices-for-be-assessment.csv`) is a list of truck stops with:

| Column | Purpose |
|--------|---------|
| Truckstop Name | Station name shown in results |
| Address | Highway exit text like `I-65, EXIT 50 & US-50 & US-31` — **human label only** |
| City, State | Used to place the station on the map |
| Retail Price | **$/gallon used for cost optimization** |

There is **no latitude/longitude** in that file. Exit addresses are not street geocodes, so we map each station to its **City + State** center (offline), once at import time.

## 2. How do we find gas stations on a route?

1. Geocode your start and finish
2. Call **OSRM once** for the driving path (polyline)
3. Keep stations whose city-center is within ~12 miles of that path
4. Sort them by distance along the route

No map API is called per station.

## 3. How do we calculate optimum (cheapest) fuel?

Vehicle: **500 mile** tank, **10 mpg**.

1. Start with a full tank
2. While you cannot reach the destination yet:
   - Among stations still reachable before empty, pick the **lowest $/gal**
   - If cheaper fuel exists farther ahead within one tank: buy **only enough** to get there
   - Otherwise: **fill up** at this cheap station
3. Total cost = gallons bought × price at each stop  
   Gallons needed overall ≈ `distance / 10`

## 4. How many API calls?

| Situation | External HTTP calls |
|-----------|---------------------|
| `City, ST` (usual) | **1** — OSRM route only |
| Street / landmark name | up to **3** — Nominatim×2 + OSRM |
| Same trip again | **0** — cached |

## 5. How detailed can start / finish be?

| Input | Works? | Notes |
|-------|--------|-------|
| `Chicago, IL` | Yes (fast) | Local city database, 0 geocode API |
| `Dallas, Texas` | Yes | State name OK |
| `Millennium Park, Chicago, IL` | Yes | Uses Nominatim (1 extra call, cached) |
| `1600 Pennsylvania Avenue NW, Washington, DC` | Yes | Street-level via Nominatim |
| Just `Chicago` | Maybe | Prefer `City, ST` |

More detail = more precise route start, but city-level is enough for this assessment and is faster.
