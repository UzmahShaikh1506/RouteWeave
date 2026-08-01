"""
Unit tests for the RouteWeave optimizer engine.

Tests cover:
  - Haversine distance accuracy
  - Route length calculation
  - Nearest-neighbor construction
  - 2-opt improvement
  - Full optimize_route pipeline
  - Edge cases (0, 1, 2 stops; identical coordinates)
"""

import math
import time

import pytest

from haversine import haversine, route_length
from optimize import nearest_neighbor, two_opt, optimize_route


class TestHaversine:
    """Tests for the haversine distance function."""

    def test_same_point_returns_zero(self):
        """Distance from a point to itself should be zero."""
        assert haversine((40.7128, -74.0060), (40.7128, -74.0060)) == 0.0

    def test_known_distance_nyc_to_la(self):
        """NYC to LA is approximately 3944 km (great-circle)."""
        nyc = (40.7128, -74.0060)
        la = (34.0522, -118.2437)
        dist = haversine(nyc, la)
        # Allow 1% tolerance
        assert abs(dist - 3944) < 50, f"Expected ~3944 km, got {dist}"

    def test_known_distance_london_to_paris(self):
        """London to Paris is approximately 344 km."""
        london = (51.5074, -0.1278)
        paris = (48.8566, 2.3522)
        dist = haversine(london, paris)
        assert abs(dist - 344) < 10, f"Expected ~344 km, got {dist}"

    def test_symmetry(self):
        """Distance should be the same in both directions."""
        a = (40.7128, -74.0060)
        b = (34.0522, -118.2437)
        assert abs(haversine(a, b) - haversine(b, a)) < 1e-10

    def test_short_distance(self):
        """Two nearby points in Manhattan (~1.1 km apart)."""
        times_sq = (40.7580, -73.9855)
        empire_st = (40.7484, -73.9857)
        dist = haversine(times_sq, empire_st)
        assert 1.0 < dist < 1.5, f"Expected ~1.1 km, got {dist}"

    def test_antipodal_points(self):
        """Points on opposite sides of Earth (~20000 km)."""
        north_pole = (90.0, 0.0)
        south_pole = (-90.0, 0.0)
        dist = haversine(north_pole, south_pole)
        assert abs(dist - 20015.1) < 10, f"Expected ~20015 km, got {dist}"


class TestRouteLength:
    """Tests for route_length calculation."""

    def test_empty_route(self):
        """Empty route has zero length."""
        assert route_length([]) == 0.0

    def test_single_stop(self):
        """Route with one stop has zero length."""
        assert route_length([{"lat": 40.0, "lng": -74.0}]) == 0.0

    def test_two_stops(self):
        """Route with two stops equals haversine between them."""
        a = {"lat": 40.7580, "lng": -73.9855}
        b = {"lat": 40.7484, "lng": -73.9857}
        expected = haversine((a["lat"], a["lng"]), (b["lat"], b["lng"]))
        assert abs(route_length([a, b]) - expected) < 1e-10

    def test_triangle_inequality(self):
        """Sum of pairwise > direct for a triangle."""
        a = {"lat": 40.7580, "lng": -73.9855}
        b = {"lat": 40.7484, "lng": -73.9857}
        c = {"lat": 40.7829, "lng": -73.9654}
        # Route a→b→c should be >= a→c direct
        abc = route_length([a, b, c])
        ac = route_length([a, c])
        assert abc >= ac


class TestNearestNeighbor:
    """Tests for the nearest-neighbor heuristic."""

    def test_visits_all_stops(self, sample_depot, sample_stops):
        """All stops should appear in the route."""
        route = nearest_neighbor(sample_depot, sample_stops)
        # Route should include depot + all stops
        assert len(route) == len(sample_stops) + 1

    def test_starts_at_depot(self, sample_depot, sample_stops):
        """Route should start at the depot."""
        route = nearest_neighbor(sample_depot, sample_stops)
        assert route[0]["lat"] == sample_depot["lat"]
        assert route[0]["lng"] == sample_depot["lng"]

    def test_no_duplicates(self, sample_depot, sample_stops):
        """No stop should appear twice (except depot if it's also a stop)."""
        route = nearest_neighbor(sample_depot, sample_stops)
        stop_ids = [s.get("id", s.get("address")) for s in route[1:]]
        assert len(stop_ids) == len(set(stop_ids))

    def test_single_stop(self, sample_depot):
        """Works with just one stop."""
        stop = {"id": "1", "address": "Test", "lat": 40.75, "lng": -73.98}
        route = nearest_neighbor(sample_depot, [stop])
        assert len(route) == 2
        assert route[0]["id"] == "depot"
        assert route[1]["id"] == "1"

    def test_empty_stops(self, sample_depot):
        """Works with no stops (just returns depot)."""
        route = nearest_neighbor(sample_depot, [])
        assert len(route) == 1
        assert route[0]["id"] == "depot"


class TestTwoOpt:
    """Tests for the 2-opt improvement algorithm."""

    def test_does_not_worsen_route(self, sample_depot, sample_stops):
        """2-opt should never make the route longer."""
        nn_route = nearest_neighbor(sample_depot, sample_stops)
        nn_length = route_length(nn_route)

        improved_route = two_opt(nn_route)
        improved_length = route_length(improved_route)

        assert improved_length <= nn_length + 1e-10

    def test_preserves_all_stops(self, sample_depot, sample_stops):
        """All stops should still be in the route after 2-opt."""
        nn_route = nearest_neighbor(sample_depot, sample_stops)
        improved_route = two_opt(nn_route)
        assert len(improved_route) == len(nn_route)

    def test_short_route_unchanged(self, sample_depot):
        """A route with ≤2 stops shouldn't change."""
        route = [sample_depot, {"id": "1", "address": "A", "lat": 40.75, "lng": -73.98}]
        improved = two_opt(route)
        assert len(improved) == 2

    def test_completes_within_time_budget(self, sample_depot, large_stops):
        """2-opt should respect the time budget."""
        nn_route = nearest_neighbor(sample_depot, large_stops)
        start = time.monotonic()
        two_opt(nn_route)
        elapsed = time.monotonic() - start
        # Should complete within 3 seconds (budget is 2s + overhead)
        assert elapsed < 3.0, f"2-opt took {elapsed:.1f}s, exceeding budget"


class TestOptimizeRoute:
    """Tests for the full optimization pipeline."""

    def test_full_pipeline(self, sample_depot, sample_stops):
        """Full pipeline should return valid results."""
        result = optimize_route(sample_depot, sample_stops)

        assert "ordered_stops" in result
        assert "naive_distance_km" in result
        assert "optimized_distance_km" in result
        assert "pct_improvement" in result

        assert len(result["ordered_stops"]) == len(sample_stops)
        assert result["optimized_distance_km"] <= result["naive_distance_km"]
        assert result["pct_improvement"] >= 0

    def test_visit_order_assigned(self, sample_depot, sample_stops):
        """Each stop should have a visit_order from 1 to N."""
        result = optimize_route(sample_depot, sample_stops)
        orders = [s["visit_order"] for s in result["ordered_stops"]]
        assert sorted(orders) == list(range(1, len(sample_stops) + 1))

    def test_empty_stops(self, sample_depot):
        """Should handle empty stop list gracefully."""
        result = optimize_route(sample_depot, [])
        assert result["ordered_stops"] == []
        assert result["naive_distance_km"] == 0.0
        assert result["optimized_distance_km"] == 0.0

    def test_single_stop(self, sample_depot):
        """Should handle a single stop."""
        stop = {"id": "1", "address": "A", "lat": 40.75, "lng": -73.98}
        result = optimize_route(sample_depot, [stop])
        assert len(result["ordered_stops"]) == 1
        assert result["ordered_stops"][0]["visit_order"] == 1

    def test_identical_coordinates(self, sample_depot):
        """Should handle stops with identical coordinates."""
        stops = [
            {"id": str(i), "address": f"Stop {i}", "lat": 40.75, "lng": -73.98}
            for i in range(3)
        ]
        result = optimize_route(sample_depot, stops)
        assert len(result["ordered_stops"]) == 3

    def test_improvement_percentage(self, sample_depot, sample_stops):
        """Improvement should be a valid percentage."""
        result = optimize_route(sample_depot, sample_stops)
        assert 0 <= result["pct_improvement"] <= 100

    def test_distances_are_positive(self, sample_depot, sample_stops):
        """Distances should be non-negative."""
        result = optimize_route(sample_depot, sample_stops)
        assert result["naive_distance_km"] >= 0
        assert result["optimized_distance_km"] >= 0

    def test_performance_20_stops(self, sample_depot, large_stops):
        """20 stops should optimize in under 3 seconds."""
        start = time.monotonic()
        result = optimize_route(sample_depot, large_stops)
        elapsed = time.monotonic() - start
        assert elapsed < 3.0, f"Optimization took {elapsed:.1f}s"
        assert len(result["ordered_stops"]) == 20
