"""
Haversine distance calculation between geographic coordinates.
Used by the route optimizer to compute straight-line distances
between delivery stops.
"""

import math
from typing import Tuple

# Earth's mean radius in kilometers
EARTH_RADIUS_KM = 6371.0


def haversine(
    coord1: Tuple[float, float],
    coord2: Tuple[float, float],
) -> float:
    """
    Calculate the great-circle distance between two points
    on Earth using the Haversine formula.

    Args:
        coord1: (latitude, longitude) of the first point in decimal degrees.
        coord2: (latitude, longitude) of the second point in decimal degrees.

    Returns:
        Distance in kilometers.
    """
    lat1, lng1 = math.radians(coord1[0]), math.radians(coord1[1])
    lat2, lng2 = math.radians(coord2[0]), math.radians(coord2[1])

    dlat = lat2 - lat1
    dlng = lng2 - lng1

    a = (
        math.sin(dlat / 2) ** 2
        + math.cos(lat1) * math.cos(lat2) * math.sin(dlng / 2) ** 2
    )
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))

    return EARTH_RADIUS_KM * c


def route_length(route: list[dict]) -> float:
    """
    Calculate the total distance of a route by summing pairwise
    haversine distances between consecutive stops.

    Args:
        route: List of stop dicts, each with 'lat' and 'lng' keys.

    Returns:
        Total route distance in kilometers.
    """
    total = 0.0
    for i in range(len(route) - 1):
        total += haversine(
            (route[i]["lat"], route[i]["lng"]),
            (route[i + 1]["lat"], route[i + 1]["lng"]),
        )
    return total
