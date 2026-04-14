import json
from pathlib import Path

from django.conf import settings
from django.http import JsonResponse
from django.shortcuts import render
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from routing.services import DEFAULT_MAX_RANGE_MILES, DEFAULT_MPG, build_route_plan

_GRAPHHOPPER_WARNING = (
    "Routing powered by GraphHopper free tier (500 req/day). "
    "Results may degrade or fall back to OSRM if the daily limit is reached "
    "or the API key expires. Fuel prices come from EIA weekly averages when "
    "EIA_API_KEY is configured; otherwise placeholders are used."
)
_OSRM_WARNING = (
    "Routing powered by the public OSRM demo server (no API key). "
    "This server has no uptime SLA and may be slow under heavy load. "
    "Fuel prices come from EIA weekly averages when EIA_API_KEY is configured; "
    "otherwise placeholders are used."
)


def route_planner_ui(request):
    suggestions: list[str] = []
    json_path = Path(getattr(settings, "FUEL_STATIONS_JSON_PATH", ""))
    if json_path.exists():
        try:
            with json_path.open(encoding="utf-8") as fh:
                rows = json.load(fh)
            if isinstance(rows, list):
                # Use "City ST" extracted from station names like:
                # "Pilot - Kennebunk ME" -> "Kennebunk ME"
                unique = set()
                for row in rows:
                    name = str(row.get("name", "")).strip()
                    if not name:
                        continue
                    value = name.split(" - ", 1)[-1].strip()
                    if value:
                        unique.add(value)
                suggestions = sorted(unique)
        except (OSError, json.JSONDecodeError):
            suggestions = []

    return render(
        request,
        "routing/index.html",
        {"location_suggestions": suggestions},
    )


@csrf_exempt
@require_POST
def plan_route_view(request):
    try:
        payload = json.loads(request.body.decode("utf-8"))
    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON body."}, status=400)

    start = payload.get("start")
    finish = payload.get("finish")
    if not start or not finish:
        return JsonResponse(
            {"error": "Both 'start' and 'finish' fields are required."}, status=400
        )

    vehicle_payload = payload.get("vehicle")
    if vehicle_payload is not None and not isinstance(vehicle_payload, dict):
        return JsonResponse(
            {"error": "'vehicle' must be a JSON object when provided."},
            status=400,
        )

    try:
        # Backward compatible defaults (existing flat fields).
        max_range_miles = float(payload.get("max_range_miles", DEFAULT_MAX_RANGE_MILES))
        mpg = float(payload.get("mpg", DEFAULT_MPG))

        # New optional nested vehicle config.
        if vehicle_payload:
            mpg = float(vehicle_payload.get("mpg", mpg))

            # Prefer explicit range if provided.
            if "max_range_miles" in vehicle_payload:
                max_range_miles = float(vehicle_payload["max_range_miles"])
            elif "tank_gallons" in vehicle_payload:
                tank_gallons = float(vehicle_payload["tank_gallons"])
                max_range_miles = tank_gallons * mpg
    except (TypeError, ValueError):
        return JsonResponse(
            {
                "error": (
                    "'max_range_miles', 'mpg', and vehicle fields "
                    "('mpg', 'max_range_miles', 'tank_gallons') must be numeric."
                )
            },
            status=400,
        )

    try:
        result = build_route_plan(
            start_location=start,
            finish_location=finish,
            max_range_miles=max_range_miles,
            mpg=mpg,
        )
    except ValueError as exc:
        return JsonResponse({"error": str(exc)}, status=400)
    except Exception as exc:  # pragma: no cover - safety net for external API failures
        return JsonResponse({"error": f"Unable to plan route: {exc}"}, status=502)

    engine = result.get("route", {}).get("engine", "osrm")
    result["warnings"] = [
        _GRAPHHOPPER_WARNING if engine == "graphhopper" else _OSRM_WARNING
    ]

    return JsonResponse(result, status=200)
