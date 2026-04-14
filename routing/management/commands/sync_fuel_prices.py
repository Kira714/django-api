"""
Management command: sync_fuel_prices

Loads fuel prices from a provided CSV file and updates FuelStation rows with
state-level average prices.

Expected CSV columns:
  - State
  - Retail Price

Usage:
  python manage.py sync_fuel_prices
  python manage.py sync_fuel_prices --csv /absolute/path/to/file.csv
"""

import csv
from collections import defaultdict
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from routing.models import FuelStation


class Command(BaseCommand):
    help = "Sync fuel prices from CSV into the FuelStation table."

    def add_arguments(self, parser):
        parser.add_argument(
            "--csv",
            dest="csv_path",
            default=None,
            help=(
                "Absolute path to a fuel price CSV file. "
                "Defaults to settings.FUEL_PRICES_CSV_PATH."
            ),
        )

    def handle(self, *args, **options):
        csv_path = Path(options["csv_path"] or settings.FUEL_PRICES_CSV_PATH)
        if not csv_path.exists():
            raise CommandError(f"Fuel prices CSV not found: {csv_path}")

        states_in_db = set(
            FuelStation.objects.exclude(state="")
            .values_list("state", flat=True)
            .distinct()
        )
        if not states_in_db:
            self.stdout.write(
                self.style.WARNING(
                    "No stations with state codes found in DB. "
                    "Run load_fuel_stations first."
                )
            )
            return

        state_prices = self._load_state_average_prices(csv_path)
        if not state_prices:
            raise CommandError(
                f"No valid (State, Retail Price) rows found in CSV: {csv_path}"
            )

        updated_states = 0
        updated_stations = 0
        for state in sorted(states_in_db):
            price = state_prices.get(state)
            if price is None:
                continue
            count = FuelStation.objects.filter(state=state).update(
                price_per_gallon=round(price, 3)
            )
            if count:
                updated_states += 1
                updated_stations += count
                self.stdout.write(f"  {state}: ${price:.3f}/gal -> {count} station(s)")

        states_without_price = sorted(states_in_db - set(state_prices))
        if states_without_price:
            self.stdout.write(
                self.style.WARNING(
                    "No CSV price rows for: "
                    + ", ".join(states_without_price)
                    + " - those stations keep existing prices."
                )
            )

        self.stdout.write(
            self.style.SUCCESS(
                f"Synced CSV prices for {updated_stations} station(s) "
                f"across {updated_states} state(s) from {csv_path.name}."
            )
        )

    def _load_state_average_prices(self, csv_path: Path) -> dict[str, float]:
        sums: dict[str, float] = defaultdict(float)
        counts: dict[str, int] = defaultdict(int)

        with csv_path.open(newline="", encoding="utf-8") as fh:
            reader = csv.DictReader(fh)
            if not reader.fieldnames:
                return {}
            for row in reader:
                state = str(row.get("State", "")).strip().upper()
                raw_price = row.get("Retail Price")
                if len(state) != 2 or raw_price in (None, ""):
                    continue
                try:
                    price = float(str(raw_price).strip())
                except ValueError:
                    continue
                sums[state] += price
                counts[state] += 1

        return {state: sums[state] / counts[state] for state in counts if counts[state] > 0}
