"""
Integration test: verify the full optimizer pipeline works correctly
by simulating the optimizer service endpoint locally (without Docker).

Tests:
  1. Direct optimizer function call with sample NYC data
  2. Optimizer FastAPI endpoint via TestClient
  3. Verify distance improvement is meaningful
  4. Test edge cases (1 stop, 2 stops, many stops)
  5. Benchmark performance
"""

import sys
import os
import time
import json

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'optimizer'))

from fastapi.testclient import TestClient
from server import app
from optimize import optimize_route
from haversine import haversine


def test_direct_optimizer():
    """Test the optimizer directly with NYC landmarks."""
    print("\n" + "=" * 60)
    print("TEST 1: Direct optimizer call with NYC landmarks")
    print("=" * 60)

    depot = {
        "id": "depot",
        "address": "Times Square, NYC",
        "lat": 40.7580,
        "lng": -73.9855,
    }

    stops = [
        {"id": "1", "address": "Central Park", "lat": 40.7829, "lng": -73.9654},
        {"id": "2", "address": "Empire State Building", "lat": 40.7484, "lng": -73.9857},
        {"id": "3", "address": "Wall Street", "lat": 40.7074, "lng": -74.0113},
        {"id": "4", "address": "Brooklyn Bridge", "lat": 40.7061, "lng": -73.9969},
        {"id": "5", "address": "Grand Central Terminal", "lat": 40.7527, "lng": -73.9772},
        {"id": "6", "address": "Statue of Liberty", "lat": 40.6892, "lng": -74.0445},
        {"id": "7", "address": "One World Trade Center", "lat": 40.7127, "lng": -74.0134},
        {"id": "8", "address": "Rockefeller Center", "lat": 40.7587, "lng": -73.9787},
        {"id": "9", "address": "Madison Square Garden", "lat": 40.7505, "lng": -73.9934},
        {"id": "10", "address": "Battery Park", "lat": 40.7033, "lng": -74.0170},
    ]

    start = time.monotonic()
    result = optimize_route(depot, stops)
    elapsed = time.monotonic() - start

    print(f"  Naive distance:     {result['naive_distance_km']:.2f} km")
    print(f"  Optimized distance: {result['optimized_distance_km']:.2f} km")
    print(f"  Improvement:        {result['pct_improvement']:.1f}%")
    print(f"  Computation time:   {elapsed*1000:.0f} ms")
    print(f"  Stops optimized:    {len(result['ordered_stops'])}")
    print(f"  Visit order:        {[s['visit_order'] for s in result['ordered_stops']]}")

    assert result['optimized_distance_km'] <= result['naive_distance_km']
    assert result['pct_improvement'] >= 0
    assert len(result['ordered_stops']) == 10
    assert elapsed < 2.0, f"Took too long: {elapsed:.2f}s"

    print("  ✅ PASSED")


def test_optimizer_endpoint():
    """Test the optimizer FastAPI endpoint via TestClient."""
    print("\n" + "=" * 60)
    print("TEST 2: Optimizer HTTP endpoint")
    print("=" * 60)

    client = TestClient(app)

    # Health check
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "healthy"
    print("  Health check: ✅")

    # Optimize endpoint
    payload = {
        "depot": {"id": "depot", "address": "Times Square", "lat": 40.7580, "lng": -73.9855},
        "stops": [
            {"id": "1", "address": "Central Park", "lat": 40.7829, "lng": -73.9654},
            {"id": "2", "address": "Wall Street", "lat": 40.7074, "lng": -74.0113},
            {"id": "3", "address": "Brooklyn Bridge", "lat": 40.7061, "lng": -73.9969},
            {"id": "4", "address": "Empire State", "lat": 40.7484, "lng": -73.9857},
            {"id": "5", "address": "Battery Park", "lat": 40.7033, "lng": -74.0170},
        ],
    }

    resp = client.post("/optimize", json=payload)
    assert resp.status_code == 200
    data = resp.json()

    print(f"  Response status:    200")
    print(f"  Naive distance:     {data['naive_distance_km']:.2f} km")
    print(f"  Optimized distance: {data['optimized_distance_km']:.2f} km")
    print(f"  Improvement:        {data['pct_improvement']:.1f}%")
    print(f"  Stops returned:     {len(data['ordered_stops'])}")

    assert data['optimized_distance_km'] <= data['naive_distance_km']
    assert len(data['ordered_stops']) == 5
    print("  ✅ PASSED")


def test_edge_cases():
    """Test optimizer with edge cases."""
    print("\n" + "=" * 60)
    print("TEST 3: Edge cases")
    print("=" * 60)

    client = TestClient(app)

    # Empty stops
    resp = client.post("/optimize", json={
        "depot": {"lat": 40.75, "lng": -73.98},
        "stops": [],
    })
    assert resp.status_code == 200
    assert resp.json()["ordered_stops"] == []
    print("  Empty stops: ✅")

    # Single stop
    resp = client.post("/optimize", json={
        "depot": {"lat": 40.75, "lng": -73.98},
        "stops": [{"id": "1", "address": "A", "lat": 40.76, "lng": -73.97}],
    })
    assert resp.status_code == 200
    assert len(resp.json()["ordered_stops"]) == 1
    print("  Single stop: ✅")

    # Two stops
    resp = client.post("/optimize", json={
        "depot": {"lat": 40.75, "lng": -73.98},
        "stops": [
            {"id": "1", "address": "A", "lat": 40.76, "lng": -73.97},
            {"id": "2", "address": "B", "lat": 40.74, "lng": -73.99},
        ],
    })
    assert resp.status_code == 200
    assert len(resp.json()["ordered_stops"]) == 2
    print("  Two stops: ✅")

    # Identical coordinates
    resp = client.post("/optimize", json={
        "depot": {"lat": 40.75, "lng": -73.98},
        "stops": [
            {"id": "1", "address": "A", "lat": 40.75, "lng": -73.98},
            {"id": "2", "address": "B", "lat": 40.75, "lng": -73.98},
        ],
    })
    assert resp.status_code == 200
    print("  Identical coordinates: ✅")

    # Invalid coordinates should fail validation
    resp = client.post("/optimize", json={
        "depot": {"lat": 999, "lng": -73.98},
        "stops": [],
    })
    assert resp.status_code == 422
    print("  Invalid coordinates rejected: ✅")


def test_performance():
    """Benchmark with 20 stops."""
    print("\n" + "=" * 60)
    print("TEST 4: Performance benchmark (20 stops)")
    print("=" * 60)

    import random
    random.seed(42)

    depot = {"id": "depot", "address": "Depot", "lat": 40.75, "lng": -73.98}
    stops = [
        {
            "id": str(i),
            "address": f"Stop {i}",
            "lat": 40.7 + random.uniform(-0.1, 0.1),
            "lng": -74.0 + random.uniform(-0.1, 0.1),
        }
        for i in range(20)
    ]

    start = time.monotonic()
    result = optimize_route(depot, stops)
    elapsed = time.monotonic() - start

    print(f"  20 stops computed in {elapsed*1000:.0f} ms")
    print(f"  Naive:     {result['naive_distance_km']:.2f} km")
    print(f"  Optimized: {result['optimized_distance_km']:.2f} km")
    print(f"  Saved:     {result['pct_improvement']:.1f}%")

    assert elapsed < 2.0, f"Exceeded 2s budget: {elapsed:.2f}s"
    assert result['pct_improvement'] >= 0
    print("  ✅ PASSED (within 2s budget)")

    # 50 stops
    stops_50 = [
        {
            "id": str(i),
            "address": f"Stop {i}",
            "lat": 40.7 + random.uniform(-0.15, 0.15),
            "lng": -74.0 + random.uniform(-0.15, 0.15),
        }
        for i in range(50)
    ]

    start = time.monotonic()
    result = optimize_route(depot, stops_50)
    elapsed = time.monotonic() - start

    print(f"\n  50 stops computed in {elapsed*1000:.0f} ms")
    print(f"  Naive:     {result['naive_distance_km']:.2f} km")
    print(f"  Optimized: {result['optimized_distance_km']:.2f} km")
    print(f"  Saved:     {result['pct_improvement']:.1f}%")
    print("  ✅ PASSED")


if __name__ == "__main__":
    test_direct_optimizer()
    test_optimizer_endpoint()
    test_edge_cases()
    test_performance()

    print("\n" + "=" * 60)
    print("🎉 ALL INTEGRATION TESTS PASSED")
    print("=" * 60)
