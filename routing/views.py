import json

from django.conf import settings
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from routing.services import DEFAULT_MAX_RANGE_MILES, DEFAULT_MPG, build_route_plan

_GRAPHHOPPER_WARNING = (
    "Routing powered by GraphHopper free tier (500 req/day). "
    "Results may degrade or fall back to OSRM if the daily limit is reached "
    "or the API key expires. Fuel prices are a static snapshot — not live data."
)
_OSRM_WARNING = (
    "Routing powered by the public OSRM demo server (no API key). "
    "This server has no uptime SLA and may be slow under heavy load. "
    "Fuel prices are a static snapshot — not live data."
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

    try:
        max_range_miles = float(payload.get("max_range_miles", DEFAULT_MAX_RANGE_MILES))
        mpg = float(payload.get("mpg", DEFAULT_MPG))
    except (TypeError, ValueError):
        return JsonResponse(
            {"error": "'max_range_miles' and 'mpg' must be numeric values."}, status=400
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
