"""
Management command: load_fuel_stations

Reads stations.json (real coordinates + state codes, no prices) and upserts
every entry into the FuelStation table with a placeholder price of $3.50/gal.
Run sync_fuel_prices afterwards to replace placeholders with live EIA prices.

Safe to run multiple times — existing stations are updated, not duplicated.

Usage:
    python manage.py load_fuel_stations
    python manage.py load_fuel_stations --json /path/to/custom.json
"""

import json
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from routing.models import FuelStation

DEFAULT_PLACEHOLDER_PRICE = 3.50


class Command(BaseCommand):
    help = "Load or refresh fuel stations from stations.json into the database."

    def add_arguments(self, parser):
        parser.add_argument(
            "--json",
            dest="json_path",
            default=None,
            help=(
                "Absolute path to the stations JSON file. "
                "Defaults to settings.FUEL_STATIONS_JSON_PATH."
            ),
        )

    def handle(self, *args, **options):
        json_path = Path(options["json_path"] or settings.FUEL_STATIONS_JSON_PATH)
        if not json_path.exists():
            raise CommandError(f"Stations JSON file not found: {json_path}")

        try:
            with json_path.open(encoding="utf-8") as fh:
                stations_data = json.load(fh)
        except json.JSONDecodeError as exc:
            raise CommandError(f"Invalid JSON in {json_path}: {exc}") from exc

        if not isinstance(stations_data, list):
            raise CommandError("stations.json must be a JSON array.")

        created_count = 0
        updated_count = 0
        skipped_count = 0

        for index, entry in enumerate(stations_data):
            try:
                sid = str(entry["station_id"]).strip()
                name = str(entry.get("name", f"Station {index + 1}")).strip()
                lat = float(entry["latitude"])
                lon = float(entry["longitude"])
                state = str(entry.get("state", "")).strip().upper()
            except (KeyError, TypeError, ValueError):
                skipped_count += 1
                continue

            station, was_created = FuelStation.objects.get_or_create(
                station_id=sid,
                defaults={
                    "name": name,
                    "latitude": lat,
                    "longitude": lon,
                    "state": state,
                    "price_per_gallon": DEFAULT_PLACEHOLDER_PRICE,
                },
            )
            if was_created:
                created_count += 1
            else:
                # Existing row — refresh location/state but preserve the live price.
                FuelStation.objects.filter(station_id=sid).update(
                    name=name, latitude=lat, longitude=lon, state=state
                )
                updated_count += 1

        total = created_count + updated_count
        self.stdout.write(
            self.style.SUCCESS(
                f"Loaded {total} stations from {json_path.name} "
                f"({created_count} new at ${DEFAULT_PLACEHOLDER_PRICE:.2f}/gal placeholder, "
                f"{updated_count} updated, {skipped_count} skipped). "
                "Run sync_fuel_prices to fetch live EIA prices."
            )
        )
