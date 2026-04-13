"""
Management command: load_fuel_stations

Reads the fuel prices CSV and upserts every row into the FuelStation table.
Safe to run multiple times — existing stations are updated, not duplicated.

Usage:
    python manage.py load_fuel_stations
    python manage.py load_fuel_stations --csv /path/to/custom.csv
"""

import csv
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from routing.models import FuelStation
from routing.services import _normalize_key, _pick_column


class Command(BaseCommand):
    help = "Load or refresh fuel station prices from a CSV file into PostgreSQL."

    def add_arguments(self, parser):
        parser.add_argument(
            "--csv",
            dest="csv_path",
            default=None,
            help=(
                "Absolute path to the fuel prices CSV. "
                "Defaults to settings.FUEL_CSV_PATH."
            ),
        )

    def handle(self, *args, **options):
        csv_path = Path(options["csv_path"] or settings.FUEL_CSV_PATH)
        if not csv_path.exists():
            raise CommandError(f"CSV file not found: {csv_path}")

        created_count = 0
        updated_count = 0
        skipped_count = 0

        with csv_path.open(newline="", encoding="utf-8") as handle:
            reader = csv.DictReader(handle)
            if not reader.fieldnames:
                raise CommandError("CSV has no header row.")

            lat_key = _pick_column(reader.fieldnames, ("latitude", "lat"))
            lon_key = _pick_column(
                reader.fieldnames, ("longitude", "lon", "lng", "long")
            )
            price_key = _pick_column(
                reader.fieldnames, ("price_per_gallon", "price", "fuel_price", "cost")
            )
            name_key = _pick_column(
                reader.fieldnames, ("station_name", "name", "station")
            )
            id_key = _pick_column(reader.fieldnames, ("id", "station_id"))

            if not lat_key or not lon_key or not price_key:
                raise CommandError(
                    "CSV must have latitude, longitude, and price columns."
                )

            for index, row in enumerate(reader):
                try:
                    sid = (
                        row.get(id_key, "").strip()
                        if id_key
                        else f"station-{index + 1}"
                    )
                    name = (
                        row.get(name_key, "").strip()
                        if name_key and row.get(name_key)
                        else f"Station {index + 1}"
                    )
                    lat = float(row[lat_key])
                    lon = float(row[lon_key])
                    price = float(row[price_key])
                except (TypeError, ValueError, KeyError):
                    skipped_count += 1
                    continue

                _, was_created = FuelStation.objects.update_or_create(
                    station_id=sid,
                    defaults={
                        "name": name,
                        "latitude": lat,
                        "longitude": lon,
                        "price_per_gallon": price,
                    },
                )
                if was_created:
                    created_count += 1
                else:
                    updated_count += 1

        total = created_count + updated_count
        self.stdout.write(
            self.style.SUCCESS(
                f"Loaded {total} stations from {csv_path.name} "
                f"({created_count} new, {updated_count} updated, {skipped_count} skipped)."
            )
        )
