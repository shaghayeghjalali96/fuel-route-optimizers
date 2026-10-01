"""Load OPIS fuel CSV and attach City+State coordinates (CSV has no lat/lng)."""

from __future__ import annotations

import csv
from decimal import Decimal, InvalidOperation
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from fuelroute.models import FuelStation
from fuelroute.services.station_index import invalidate_station_cache


def _norm_city(value: str) -> str:
    return " ".join(value.strip().split()).title()


def _load_city_index(path: Path) -> dict[tuple[str, str], tuple[float, float]]:
    """Map (CITY upper, STATE) -> (lat, lon). Keep first occurrence."""
    if not path.exists():
        raise CommandError(
            f"US cities file not found: {path}. Expected data/us_cities.csv"
        )

    index: dict[tuple[str, str], tuple[float, float]] = {}
    with path.open(newline="", encoding="utf-8-sig") as fh:
        reader = csv.DictReader(fh)
        # Normalize headers (source file may have trailing spaces like "LONGITUDE ")
        if reader.fieldnames:
            reader.fieldnames = [((h or "").strip()) for h in reader.fieldnames]

        for raw in reader:
            row = {(k or "").strip(): (v or "").strip() for k, v in raw.items()}
            state = (
                row.get("STATE_CODE")
                or row.get("state_id")
                or row.get("state")
                or ""
            ).upper()
            city = row.get("CITY") or row.get("city") or ""
            lat_s = row.get("LATITUDE") or row.get("lat") or row.get("latitude")
            lon_s = row.get("LONGITUDE") or row.get("lng") or row.get("longitude")
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


def _lookup(
    index: dict[tuple[str, str], tuple[float, float]], city: str, state: str
) -> tuple[float, float] | None:
    state = state.strip().upper()
    city_clean = _norm_city(city)
    key = (city_clean.upper(), state)
    if key in index:
        return index[key]

    # Try without punctuation / common suffixes
    compact = city_clean.upper().replace(".", "").replace("'", "")
    if (compact, state) in index:
        return index[(compact, state)]

    # Prefix / containment fallback within same state (small towns / truncated names)
    for (c, st), coords in index.items():
        if st != state:
            continue
        if compact.startswith(c) or c.startswith(compact):
            return coords
    return None


class Command(BaseCommand):
    help = (
        "Import fuel-prices CSV. Because rows only have exit-style addresses "
        "(no lat/lng), coordinates are resolved from City+State via data/us_cities.csv."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--csv",
            type=str,
            default=str(settings.FUEL_CSV_PATH),
            help="Path to OPIS fuel prices CSV",
        )
        parser.add_argument(
            "--cities",
            type=str,
            default=str(settings.US_CITIES_CSV_PATH),
            help="Path to US cities lat/lng CSV",
        )
        parser.add_argument(
            "--replace",
            action="store_true",
            help="Delete existing FuelStation rows before import",
        )

    def handle(self, *args, **options):
        csv_path = Path(options["csv"])
        cities_path = Path(options["cities"])
        if not csv_path.exists():
            raise CommandError(f"Fuel CSV not found: {csv_path}")

        self.stdout.write(f"Loading city index from {cities_path} ...")
        city_index = _load_city_index(cities_path)
        self.stdout.write(self.style.SUCCESS(f"City index size: {len(city_index)}"))

        # Aggregate by OPIS id — keep cheapest retail price + stable attributes
        best: dict[int, dict] = {}
        with csv_path.open(newline="", encoding="utf-8") as fh:
            reader = csv.DictReader(fh)
            for row in reader:
                try:
                    opis_id = int(row["OPIS Truckstop ID"])
                    price = Decimal(str(row["Retail Price"]).strip())
                except (KeyError, ValueError, InvalidOperation):
                    continue

                city = _norm_city(row.get("City", ""))
                state = (row.get("State") or "").strip().upper()
                name = (row.get("Truckstop Name") or "").strip()
                address = (row.get("Address") or "").strip()
                rack_raw = (row.get("Rack ID") or "").strip()
                rack_id = int(rack_raw) if rack_raw.isdigit() else None

                current = best.get(opis_id)
                if current is None or price < current["retail_price"]:
                    best[opis_id] = {
                        "opis_id": opis_id,
                        "name": name,
                        "address": address,
                        "city": city,
                        "state": state,
                        "rack_id": rack_id,
                        "retail_price": price,
                    }

        self.stdout.write(f"Unique stations in CSV: {len(best)}")

        missing_coords = 0
        objects: list[FuelStation] = []
        for data in best.values():
            coords = _lookup(city_index, data["city"], data["state"])
            lat = lon = None
            source = ""
            if coords:
                lat, lon = coords
                source = "us_cities_city_state"
            else:
                missing_coords += 1

            objects.append(
                FuelStation(
                    opis_id=data["opis_id"],
                    name=data["name"],
                    address=data["address"],
                    city=data["city"],
                    state=data["state"],
                    rack_id=data["rack_id"],
                    retail_price=data["retail_price"],
                    latitude=lat,
                    longitude=lon,
                    geocode_source=source,
                )
            )

        with transaction.atomic():
            if options["replace"]:
                deleted, _ = FuelStation.objects.all().delete()
                self.stdout.write(f"Deleted {deleted} existing rows")
            FuelStation.objects.bulk_create(objects, batch_size=1000)

        invalidate_station_cache()

        geocoded = FuelStation.objects.exclude(latitude__isnull=True).count()
        total = FuelStation.objects.count()
        self.stdout.write(
            self.style.SUCCESS(
                f"Imported {total} stations; {geocoded} geocoded via City+State; "
                f"{missing_coords} without coordinates."
            )
        )
        self.stdout.write(
            "Note: addresses like 'I-65, EXIT 50 & US-50 & US-31' are highway exits — "
            "city centroids are approximate but avoid thousands of geocoder API calls."
        )
