"""
Management command: sync_fuel_prices

Fetches the latest weekly retail gasoline prices from the EIA (US Energy
Information Administration) API v2 and updates every FuelStation row with the
average price for its state.

Data source
-----------
EIA Open Data API v2 — Petroleum & Other Liquids → Weekly Retail Gasoline Prices
  https://www.eia.gov/opendata/
  Endpoint: https://api.eia.gov/v2/petroleum/pri/gnd/data/
  Product : EPM0  (Regular conventional gasoline, all formulations)
  Frequency: weekly
  Sign-up (free): https://www.eia.gov/opendata/register.php

Fallback
--------
If EIA_API_KEY is not configured the command warns and exits without touching
the database, so the app continues to run with placeholder prices.

Usage:
    python manage.py sync_fuel_prices
    python manage.py sync_fuel_prices --product EPD2D   # diesel
"""

import logging

import requests
from django.conf import settings
from django.core.management.base import BaseCommand

from routing.models import FuelStation

logger = logging.getLogger(__name__)

EIA_ENDPOINT = "https://api.eia.gov/v2/petroleum/pri/gnd/data/"

# Map 2-letter state abbreviation → EIA duoarea code (S + abbreviation)
# EIA uses "S" prefix for state-level series.
STATE_TO_DUOAREA: dict[str, str] = {
    "AK": "SAK", "AL": "SAL", "AR": "SAR", "AZ": "SAZ", "CA": "SCA",
    "CO": "SCO", "CT": "SCT", "DC": "SDC", "DE": "SDE", "FL": "SFL",
    "GA": "SGA", "HI": "SHI", "IA": "SIA", "ID": "SID", "IL": "SIL",
    "IN": "SIN", "KS": "SKS", "KY": "SKY", "LA": "SLA", "MA": "SMA",
    "MD": "SMD", "ME": "SME", "MI": "SMI", "MN": "SMN", "MO": "SMO",
    "MS": "SMS", "MT": "SMT", "NC": "SNC", "ND": "SND", "NE": "SNE",
    "NH": "SNH", "NJ": "SNJ", "NM": "SNM", "NV": "SNV", "NY": "SNY",
    "OH": "SOH", "OK": "SOK", "OR": "SOR", "PA": "SPA", "RI": "SRI",
    "SC": "SSC", "SD": "SSD", "TN": "STN", "TX": "STX", "UT": "SUT",
    "VA": "SVA", "VT": "SVT", "WA": "SWA", "WI": "SWI", "WV": "SWV",
    "WY": "SWY",
}

# EIA product codes (the --product argument)
PRODUCT_REGULAR = "EPM0"   # Regular gasoline (all formulations)
PRODUCT_DIESEL = "EPD2D"   # Diesel, all types


class Command(BaseCommand):
    help = "Sync fuel prices from the EIA API into the FuelStation table."

    def add_arguments(self, parser):
        parser.add_argument(
            "--product",
            default=PRODUCT_REGULAR,
            help=(
                f"EIA product code. Default: {PRODUCT_REGULAR} (regular gasoline). "
                f"Use {PRODUCT_DIESEL} for diesel."
            ),
        )

    def handle(self, *args, **options):
        api_key = getattr(settings, "EIA_API_KEY", "")
        if not api_key:
            self.stdout.write(
                self.style.WARNING(
                    "EIA_API_KEY is not set — skipping price sync. "
                    "Stations will keep their current placeholder prices. "
                    "Set EIA_API_KEY in .env or environment to enable live prices."
                )
            )
            return

        product = options["product"]

        # Collect the states that actually have stations in the DB
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

        duoareas = [
            STATE_TO_DUOAREA[s] for s in states_in_db if s in STATE_TO_DUOAREA
        ]
        unknown = states_in_db - set(STATE_TO_DUOAREA)
        if unknown:
            self.stdout.write(
                self.style.WARNING(
                    f"Unknown state codes (no EIA mapping): {', '.join(sorted(unknown))}"
                )
            )

        if not duoareas:
            self.stdout.write(self.style.ERROR("No mappable states found — aborting."))
            return

        self.stdout.write(
            f"Fetching EIA prices for {len(duoareas)} state(s) "
            f"(product={product})..."
        )

        state_prices = self._fetch_state_prices(api_key, product, duoareas)

        if not state_prices:
            self.stdout.write(
                self.style.ERROR(
                    "EIA returned no price data. Check your API key and try again."
                )
            )
            return

        # Reverse the duoarea→state mapping for the data we got back
        duoarea_to_state = {v: k for k, v in STATE_TO_DUOAREA.items()}

        updated_states = 0
        updated_stations = 0

        for duoarea, price in state_prices.items():
            state = duoarea_to_state.get(duoarea)
            if not state:
                continue
            count = FuelStation.objects.filter(state=state).update(
                price_per_gallon=round(price, 3)
            )
            if count:
                updated_states += 1
                updated_stations += count
                self.stdout.write(f"  {state}: ${price:.3f}/gal → {count} station(s)")

        states_without_price = states_in_db - {
            duoarea_to_state[d] for d in state_prices if d in duoarea_to_state
        }
        if states_without_price:
            self.stdout.write(
                self.style.WARNING(
                    "No EIA price returned for: "
                    + ", ".join(sorted(states_without_price))
                    + " — those stations keep their existing price."
                )
            )

        self.stdout.write(
            self.style.SUCCESS(
                f"Synced live EIA prices for {updated_stations} station(s) "
                f"across {updated_states} state(s)."
            )
        )

    def _fetch_state_prices(
        self, api_key: str, product: str, duoareas: list[str]
    ) -> dict[str, float]:
        """
        Call the EIA API v2 and return {duoarea: latest_price_per_gallon}.

        EIA returns data sorted by period descending; we take the first
        (most recent) entry for each state.
        """
        # Build the facets list for all duoareas in one request
        params: list[tuple[str, str]] = [
            ("api_key", api_key),
            ("frequency", "weekly"),
            ("data[0]", "value"),
            ("facets[product][]", product),
            ("sort[0][column]", "period"),
            ("sort[0][direction]", "desc"),
            # One record per state is enough; we ask for len*2 to be safe
            ("length", str(max(len(duoareas) * 2, 50))),
        ]
        for da in duoareas:
            params.append(("facets[duoarea][]", da))

        try:
            response = requests.get(
                EIA_ENDPOINT,
                params=params,
                headers={"User-Agent": "fuel-route-planner/1.0"},
                timeout=15,
            )
            response.raise_for_status()
        except requests.RequestException as exc:
            logger.error("EIA API request failed: %s", exc)
            self.stdout.write(self.style.ERROR(f"EIA API error: {exc}"))
            return {}

        try:
            payload = response.json()
        except ValueError:
            self.stdout.write(self.style.ERROR("EIA response was not valid JSON."))
            return {}

        rows = payload.get("response", {}).get("data", [])
        if not rows:
            self.stdout.write(
                self.style.WARNING(
                    f"EIA returned 0 rows. Response: {payload.get('response', {})}"
                )
            )
            return {}

        # Keep only the latest entry per duoarea (rows are already desc by period)
        latest: dict[str, float] = {}
        for row in rows:
            da = row.get("duoarea", "")
            val = row.get("value")
            if da and val is not None and da not in latest:
                try:
                    latest[da] = float(val)
                except (TypeError, ValueError):
                    pass

        return latest
