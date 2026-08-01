"""
Pytest fixtures for RouteWeave tests.

Provides:
  - sys.path configuration so optimizer modules can be imported
  - Sample stop data for optimizer tests
  - Mock geocoder for API tests
  - FastAPI test client with SQLite backend
"""

import sys
import os

import pytest

# Add service directories to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'optimizer'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'api'))


# ---------------------------------------------------------------------------
# Optimizer Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def sample_depot():
    """A sample depot location (Times Square, NYC)."""
    return {
        "id": "depot",
        "address": "Times Square, New York, NY",
        "lat": 40.7580,
        "lng": -73.9855,
    }


@pytest.fixture
def sample_stops():
    """Sample delivery stops across Manhattan."""
    return [
        {"id": "1", "address": "Central Park, New York, NY", "lat": 40.7829, "lng": -73.9654},
        {"id": "2", "address": "Empire State Building, New York, NY", "lat": 40.7484, "lng": -73.9857},
        {"id": "3", "address": "Wall Street, New York, NY", "lat": 40.7074, "lng": -74.0113},
        {"id": "4", "address": "Brooklyn Bridge, New York, NY", "lat": 40.7061, "lng": -73.9969},
        {"id": "5", "address": "Grand Central Terminal, New York, NY", "lat": 40.7527, "lng": -73.9772},
    ]


@pytest.fixture
def large_stops():
    """A larger set of stops for performance testing."""
    import random
    random.seed(42)
    return [
        {
            "id": str(i),
            "address": f"Location {i}",
            "lat": 40.7 + random.uniform(-0.1, 0.1),
            "lng": -74.0 + random.uniform(-0.1, 0.1),
        }
        for i in range(20)
    ]


# ---------------------------------------------------------------------------
# API Test Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def sample_csv_content():
    """Valid CSV content for testing job creation."""
    return (
        "address\n"
        '"Central Park, New York, NY"\n'
        '"Empire State Building, New York, NY"\n'
        '"Wall Street, New York, NY"\n'
        '"Brooklyn Bridge, New York, NY"\n'
        '"Grand Central Terminal, New York, NY"\n'
    )


@pytest.fixture
def invalid_csv_content():
    """CSV without an 'address' column."""
    return (
        "name,location\n"
        "Stop 1,123 Main St\n"
        "Stop 2,456 Oak Ave\n"
    )
