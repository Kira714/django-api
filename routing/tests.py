import json
from unittest.mock import patch

from django.test import Client, TestCase

from routing.services import FuelStationData, _optimize_fuel_plan


class RouteApiTests(TestCase):
    def setUp(self):
        self.client = Client()

    @patch("routing.views.build_route_plan")
    def test_plan_route_success(self, mocked_builder):
        mocked_builder.return_value = {"total_fuel_cost": 123.45, "fuel_stops": []}
        response = self.client.post(
            "/api/route/plan/",
            data=json.dumps({"start": "Denver, CO", "finish": "Phoenix, AZ"}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["total_fuel_cost"], 123.45)

    def test_plan_route_requires_start_and_finish(self):
        response = self.client.post(
            "/api/route/plan/",
            data=json.dumps({"start": "Denver, CO"}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("error", response.json())


class FuelOptimizationTests(TestCase):
    def test_optimizer_returns_non_zero_cost(self):
        stations = [
            FuelStationData("a", "A", 0.0, 0.0, 4.0, mile_marker=100.0),
            FuelStationData("b", "B", 0.0, 0.0, 3.2, mile_marker=280.0),
            FuelStationData("c", "C", 0.0, 0.0, 3.8, mile_marker=430.0),
        ]
        stops, total_cost = _optimize_fuel_plan(
            route_distance_miles=500.0,
            stations=stations,
            max_range_miles=500.0,
            mpg=10.0,
        )
        self.assertGreater(total_cost, 0)
        self.assertGreaterEqual(len(stops), 1)
