"""
Fuel-route planning service layer.

Routing priority
----------------
1. GraphHopper API  – free tier (500 req/day); set GRAPHHOPPER_API_KEY in .env.
2. OSRM public API  – automatic fallback when the GH key is absent or the call fails.

Fuel station data
-----------------
Stations are read from the FuelStation table (populated by
`python manage.py load_fuel_stations`). Prices are synced from EIA via
`python manage.py sync_fuel_prices`.

Optimisation algorithm
----------------------
Greedy look-ahead:
  - At each stop, scan ahead up to max_range_miles for the cheapest station.
  - If a cheaper station is reachable, buy only enough fuel to reach it.
  - Otherwise fill the tank as full as needed to cover the next mandatory stop.
"""

import logging
import math
from dataclasses import dataclass, field
from typing import Any

import requests
from django.conf import settings
from django.core.cache import cache

logger = logging.getLogger(__name__)

MILES_PER_METER = 0.000621371
EARTH_RADIUS_MILES = 3958.8
DEFAULT_MAX_RANGE_MILES = 500.0
DEFAULT_MPG = 10.0
NEAR_ROUTE_THRESHOLD_MILES = 30.0
LAT_LON_PADDING_DEGREES = 0.5


# ── Data classes ──────────────────────────────────────────────────────────────

@dataclass
class FuelStationData:
    station_id: str
    name: str
    latitude: float
    longitude: float
    price_per_gallon: float
    mile_marker: float = field(default=0.0)


# ── Station loaders ───────────────────────────────────────────────────────────

def _load_stations_from_db() -> list[FuelStationData]:
    """Query FuelStation rows from the configured Django database."""
    from routing.models import FuelStation  # local import avoids circular at module load

    stations = [
        FuelStationData(
            station_id=str(s.station_id),
            name=s.name,
            latitude=s.latitude,
            longitude=s.longitude,
            price_per_gallon=float(s.price_per_gallon),
        )
        for s in FuelStation.objects.all()
    ]
    if not stations:
        raise ValueError(
            "FuelStation table is empty. Run `python manage.py load_fuel_stations` first."
        )
    return stations


def load_fuel_stations() -> list[FuelStationData]:
    """Return station list from the FuelStation table."""
    return _load_stations_from_db()


# ── Geo utilities ─────────────────────────────────────────────────────────────

def haversine_miles(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    d_lat = math.radians(lat2 - lat1)
    d_lon = math.radians(lon2 - lon1)
    a = (
        math.sin(d_lat / 2) ** 2
        + math.cos(math.radians(lat1))
        * math.cos(math.radians(lat2))
        * math.sin(d_lon / 2) ** 2
    )
    return EARTH_RADIUS_MILES * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


# ── Geocoding (Nominatim / OSM) ───────────────────────────────────────────────

def geocode_us_location(location: str) -> tuple[float, float]:
    cache_key = "geocode__" + location.lower().strip().replace(" ", "_").replace(",", "")
    cached = cache.get(cache_key)
    if cached:
        return cached

    response = requests.get(
        "https://nominatim.openstreetmap.org/search",
        params={
            "q": f"{location}, USA",
            "format": "jsonv2",
            "limit": 1,
            "countrycodes": "us",
        },
        headers={"User-Agent": "fuel-route-planner/1.0"},
        timeout=10,
    )
    response.raise_for_status()
    payload = response.json()
    if not payload:
        raise ValueError(f"Could not geocode location: {location!r}")

    coords: tuple[float, float] = (float(payload[0]["lat"]), float(payload[0]["lon"]))
    cache.set(cache_key, coords, timeout=60 * 60 * 24)
    return coords


# ── Routing engines ───────────────────────────────────────────────────────────

def _route_via_graphhopper(
    start: tuple[float, float], finish: tuple[float, float]
) -> dict[str, Any]:
    """
    Call the GraphHopper Directions API.

    Free tier: 500 req/day — https://www.graphhopper.com/
    Response geometry is returned as a GeoJSON LineString (points_encoded=false).
    """
    api_key = settings.GRAPHHOPPER_API_KEY
    if not api_key:
        raise ValueError("GRAPHHOPPER_API_KEY not configured.")

    response = requests.get(
        "https://graphhopper.com/api/1/route",
        params={
            "point": [f"{start[0]},{start[1]}", f"{finish[0]},{finish[1]}"],
            "vehicle": "car",
            "locale": "en",
            "calc_points": "true",
            "points_encoded": "false",
            "key": api_key,
        },
        headers={"User-Agent": "fuel-route-planner/1.0"},
        timeout=15,
    )
    response.raise_for_status()
    payload = response.json()

    if not payload.get("paths"):
        raise ValueError("GraphHopper returned no paths.")

    path = payload["paths"][0]
    # GeoJSON coordinates are [lon, lat] — consistent with OSRM output
    return {
        "distance_meters": path["distance"],
        "duration_seconds": path["time"] / 1000,
        "geometry": path["points"]["coordinates"],  # [[lon, lat], ...]
        "engine": "graphhopper",
    }


def _route_via_osrm(
    start: tuple[float, float], finish: tuple[float, float]
) -> dict[str, Any]:
    """
    Call the public OSRM demo server (project-osrm.org).

    No API key required. Rate limits apply — self-host for production use.
    """
    response = requests.get(
        (
            "https://router.project-osrm.org/route/v1/driving/"
            f"{start[1]},{start[0]};{finish[1]},{finish[0]}"
        ),
        params={"overview": "full", "geometries": "geojson"},
        headers={"User-Agent": "fuel-route-planner/1.0"},
        timeout=15,
    )
    response.raise_for_status()
    payload = response.json()

    if payload.get("code") != "Ok" or not payload.get("routes"):
        raise ValueError("OSRM returned no routes.")

    route = payload["routes"][0]
    return {
        "distance_meters": route["distance"],
        "duration_seconds": route["duration"],
        "geometry": route["geometry"]["coordinates"],  # [[lon, lat], ...]
        "engine": "osrm",
    }


def get_route(start: tuple[float, float], finish: tuple[float, float]) -> dict[str, Any]:
    """
    Fetch driving route.  Tries GraphHopper first; falls back to OSRM.
    Result is cached for 1 hour.
    """
    cache_key = (
        f"route__{start[0]:.5f}_{start[1]:.5f}__{finish[0]:.5f}_{finish[1]:.5f}"
    )
    cached = cache.get(cache_key)
    if cached:
        return cached

    result: dict[str, Any] | None = None

    if settings.GRAPHHOPPER_API_KEY:
        try:
            result = _route_via_graphhopper(start, finish)
            logger.info("Route computed via GraphHopper.")
        except Exception as exc:
            logger.warning("GraphHopper failed (%s); falling back to OSRM.", exc)

    if result is None:
        result = _route_via_osrm(start, finish)
        logger.info("Route computed via OSRM.")

    cache.set(cache_key, result, timeout=60 * 60)
    return result


# ── Route → station projection ────────────────────────────────────────────────

def _bounding_box(
    geometry: list[list[float]], padding: float = LAT_LON_PADDING_DEGREES
) -> tuple[float, float, float, float]:
    """Return (min_lat, max_lat, min_lon, max_lon) with padding."""
    lons = [p[0] for p in geometry]
    lats = [p[1] for p in geometry]
    return (
        min(lats) - padding,
        max(lats) + padding,
        min(lons) - padding,
        max(lons) + padding,
    )


def _project_stations_onto_route(
    route_geometry: list[list[float]], stations: list[FuelStationData]
) -> list[FuelStationData]:
    """
    For each station, find the closest point on the route polyline and record
    its cumulative mile marker.  Discard stations farther than
    NEAR_ROUTE_THRESHOLD_MILES from the route.
    """
    if len(route_geometry) < 2:
        return []

    # Pre-compute cumulative miles along the route
    cumulative_miles = [0.0]
    for idx in range(1, len(route_geometry)):
        prev_lon, prev_lat = route_geometry[idx - 1]
        lon, lat = route_geometry[idx]
        cumulative_miles.append(
            cumulative_miles[-1] + haversine_miles(prev_lat, prev_lon, lat, lon)
        )

    # Pre-filter stations to a bounding box (fast pass)
    min_lat, max_lat, min_lon, max_lon = _bounding_box(route_geometry)
    bbox_candidates = [
        s
        for s in stations
        if min_lat <= s.latitude <= max_lat and min_lon <= s.longitude <= max_lon
    ]

    nearby: list[FuelStationData] = []
    for station in bbox_candidates:
        best_dist = float("inf")
        best_marker = 0.0
        for idx, point in enumerate(route_geometry):
            point_lon, point_lat = point
            dist = haversine_miles(
                station.latitude, station.longitude, point_lat, point_lon
            )
            if dist < best_dist:
                best_dist = dist
                best_marker = cumulative_miles[idx]

        if best_dist <= NEAR_ROUTE_THRESHOLD_MILES:
            station.mile_marker = best_marker
            nearby.append(station)

    return sorted(nearby, key=lambda s: s.mile_marker)


# ── Fuel optimisation ─────────────────────────────────────────────────────────

def _optimize_fuel_plan(
    route_distance_miles: float,
    stations: list[FuelStationData],
    start_coords: tuple[float, float],
    finish_coords: tuple[float, float],
    max_range_miles: float,
    mpg: float,
) -> tuple[list[dict[str, Any]], float]:
    """
    Greedy look-ahead optimiser.

    Rules
    -----
    1. At each stop scan ahead up to max_range_miles for the cheapest station.
    2. If a cheaper station is reachable, buy only enough fuel to get there.
    3. Otherwise buy enough to cover the maximum reachable stretch.
    4. Raise ValueError if any segment exceeds max_range_miles with the
       available station data.
    """
    if mpg <= 0:
        raise ValueError("mpg must be greater than zero.")
    if max_range_miles <= 0:
        raise ValueError("max_range_miles must be greater than zero.")

    default_price = min((s.price_per_gallon for s in stations), default=3.50)

    start = FuelStationData(
        station_id="start",
        name="Starting Point",
        latitude=start_coords[0],
        longitude=start_coords[1],
        price_per_gallon=default_price,
        mile_marker=0.0,
    )
    destination = FuelStationData(
        station_id="destination",
        name="Destination",
        latitude=finish_coords[0],
        longitude=finish_coords[1],
        price_per_gallon=float("inf"),
        mile_marker=route_distance_miles,
    )

    checkpoints = (
        [start]
        + [s for s in stations if 0 < s.mile_marker < route_distance_miles]
        + [destination]
    )
    checkpoints.sort(key=lambda s: s.mile_marker)

    # Validate that no gap exceeds the vehicle's range
    for i in range(len(checkpoints) - 1):
        gap = checkpoints[i + 1].mile_marker - checkpoints[i].mile_marker
        if gap > max_range_miles:
            raise ValueError(
                f"Route has a gap of {gap:.0f} mi between "
                f"'{checkpoints[i].name}' and '{checkpoints[i+1].name}' "
                f"— exceeds the {max_range_miles:.0f}-mile vehicle range. "
                "Add more stations or increase max_range_miles."
            )

    fuel_in_tank = 0.0
    total_cost = 0.0
    stop_plan: list[dict[str, Any]] = []

    for idx, station in enumerate(checkpoints[:-1]):
        remaining_to_dest = route_distance_miles - station.mile_marker
        if remaining_to_dest <= 0:
            break

        # Find the first cheaper station within range
        cheaper_idx = None
        for j in range(idx + 1, len(checkpoints)):
            ahead = checkpoints[j]
            dist_ahead = ahead.mile_marker - station.mile_marker
            if dist_ahead > max_range_miles:
                break
            if ahead.price_per_gallon < station.price_per_gallon:
                cheaper_idx = j
                break

        if cheaper_idx is not None:
            target_miles = checkpoints[cheaper_idx].mile_marker - station.mile_marker
        else:
            target_miles = min(max_range_miles, remaining_to_dest)

        required_gallons = target_miles / mpg
        buy_gallons = max(0.0, required_gallons - fuel_in_tank)

        if buy_gallons > 0 and math.isfinite(station.price_per_gallon):
            cost = buy_gallons * station.price_per_gallon
            total_cost += cost
            fuel_in_tank += buy_gallons
            stop_plan.append(
                {
                    "station_id": station.station_id,
                    "station_name": station.name,
                    "latitude": station.latitude,
                    "longitude": station.longitude,
                    "mile_marker": round(station.mile_marker, 2),
                    "price_per_gallon": round(station.price_per_gallon, 3),
                    "gallons_purchased": round(buy_gallons, 3),
                    "cost": round(cost, 2),
                }
            )

        dist_to_next = checkpoints[idx + 1].mile_marker - station.mile_marker
        fuel_in_tank -= dist_to_next / mpg
        fuel_in_tank = max(0.0, fuel_in_tank)

    return stop_plan, round(total_cost, 2)


# ── GeoJSON builder ───────────────────────────────────────────────────────────

def _build_geojson(
    route_geometry: list[list[float]],
    start_coords: tuple[float, float],
    finish_coords: tuple[float, float],
    start_name: str,
    finish_name: str,
    fuel_stops: list[dict[str, Any]],
) -> dict[str, Any]:
    """
    Build a GeoJSON FeatureCollection ready for Leaflet / MapLibre rendering.

    Features
    --------
    - One LineString for the full driving route.
    - One Point per fuel stop (with price / cost metadata).
    - Start and finish Points.
    """
    features: list[dict[str, Any]] = []

    # Route line
    features.append(
        {
            "type": "Feature",
            "geometry": {"type": "LineString", "coordinates": route_geometry},
            "properties": {"type": "route"},
        }
    )

    # Start pin
    features.append(
        {
            "type": "Feature",
            "geometry": {
                "type": "Point",
                "coordinates": [start_coords[1], start_coords[0]],
            },
            "properties": {"type": "start", "label": start_name},
        }
    )

    # Finish pin
    features.append(
        {
            "type": "Feature",
            "geometry": {
                "type": "Point",
                "coordinates": [finish_coords[1], finish_coords[0]],
            },
            "properties": {"type": "finish", "label": finish_name},
        }
    )

    # Fuel stop pins
    for stop in fuel_stops:
        features.append(
            {
                "type": "Feature",
                "geometry": {
                    "type": "Point",
                    "coordinates": [stop["longitude"], stop["latitude"]],
                },
                "properties": {
                    "type": "fuel_stop",
                    "station_id": stop["station_id"],
                    "label": stop["station_name"],
                    "mile_marker": stop["mile_marker"],
                    "price_per_gallon": stop["price_per_gallon"],
                    "gallons_purchased": stop["gallons_purchased"],
                    "cost": stop["cost"],
                },
            }
        )

    return {"type": "FeatureCollection", "features": features}


# ── Public entry point ────────────────────────────────────────────────────────

def build_route_plan(
    start_location: str,
    finish_location: str,
    max_range_miles: float = DEFAULT_MAX_RANGE_MILES,
    mpg: float = DEFAULT_MPG,
) -> dict[str, Any]:
    """
    Orchestrate geocoding → routing → station matching → optimisation.

    Returns a JSON-serialisable dict that includes:
    - start / finish metadata
    - vehicle parameters
    - route summary (distance, duration, engine used)
    - ordered fuel_stops with per-stop cost breakdown
    - total_fuel_cost
    - map_data: GeoJSON FeatureCollection for frontend rendering
    """
    start_coords = geocode_us_location(start_location)
    finish_coords = geocode_us_location(finish_location)
    route = get_route(start_coords, finish_coords)

    route_distance_miles = route["distance_meters"] * MILES_PER_METER
    stations = load_fuel_stations()
    nearby = _project_stations_onto_route(route["geometry"], stations)
    stop_plan, total_cost = _optimize_fuel_plan(
        route_distance_miles=route_distance_miles,
        stations=nearby,
        start_coords=start_coords,
        finish_coords=finish_coords,
        max_range_miles=max_range_miles,
        mpg=mpg,
    )

    geojson = _build_geojson(
        route_geometry=route["geometry"],
        start_coords=start_coords,
        finish_coords=finish_coords,
        start_name=start_location,
        finish_name=finish_location,
        fuel_stops=stop_plan,
    )

    return {
        "start": {
            "query": start_location,
            "coordinates": {"lat": start_coords[0], "lon": start_coords[1]},
        },
        "finish": {
            "query": finish_location,
            "coordinates": {"lat": finish_coords[0], "lon": finish_coords[1]},
        },
        "vehicle": {
            "max_range_miles": max_range_miles,
            "mpg": mpg,
            "max_tank_gallons": round(max_range_miles / mpg, 2),
        },
        "route": {
            "distance_miles": round(route_distance_miles, 2),
            "duration_minutes": round(route["duration_seconds"] / 60, 2),
            "engine": route.get("engine", "unknown"),
        },
        "fuel_stops": stop_plan,
        "total_fuel_cost": total_cost,
        "map_data": geojson,
    }
