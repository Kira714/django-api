#!/usr/bin/env bash
# Render build script — runs once per deploy before the server starts.
set -o errexit   # exit on any error

pip install --upgrade pip
pip install -r requirements.txt

python manage.py collectstatic --no-input
python manage.py migrate
python manage.py load_fuel_stations
