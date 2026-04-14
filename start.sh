#!/usr/bin/env bash
set -o errexit

python manage.py migrate --no-input
python manage.py load_fuel_stations
python manage.py sync_fuel_prices   # no-op if EIA_API_KEY is not set
python manage.py collectstatic --no-input

exec gunicorn fuel_route_planner.wsgi:application \
  --bind "0.0.0.0:${PORT:-8000}" \
  --workers 1 \
  --timeout 120
