import json
import time

import requests

BASE = "http://127.0.0.1:8000"


def once(label: str) -> None:
    t0 = time.time()
    r = requests.get(
        f"{BASE}/api/route/",
        params={"start": "Chicago, IL", "finish": "Dallas, TX"},
        timeout=120,
    )
    elapsed = round(time.time() - t0, 2)
    data = r.json()
    print(f"\n=== {label} ({elapsed}s, HTTP {r.status_code}) ===")
    if "error" in data:
        print(json.dumps(data, indent=2))
        return
    print("summary:", data.get("summary"))
    print("cost:", data["fuel_plan"]["total_fuel_cost_text"])
    print("stops:", data["fuel_plan"]["number_of_fuel_stops"])
    print("cache:", data["cache"])
    for s in data["fuel_plan"]["fuel_stops"]:
        print(
            f"  #{s['stop_number']} @ {s['miles_from_start']} mi - "
            f"{s['station_name']} ({s['location']}) "
            f"${s['price_per_gallon_usd']}/gal = ${s['cost_at_this_stop_usd']}"
        )


for i in range(15):
    try:
        print("health", requests.get(f"{BASE}/health/", timeout=5).text)
        break
    except Exception as e:
        print("wait", e)
        time.sleep(1)
else:
    raise SystemExit("server not up — start with: python manage.py runserver")

once("first request")
once("second request (should be cache hit)")
