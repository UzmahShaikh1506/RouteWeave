"""
Route optimization engine using Nearest-Neighbor construction
followed by 2-opt local search improvement.

Designed for last-mile delivery scenarios with 5–50 stops.
"""

import time
from copy import deepcopy

from haversine import haversine, route_length


# Maximum time budget (in seconds) for the 2-opt improvement phase.
TWO_OPT_TIME_BUDGET = 2.0


def nearest_neighbor(depot: dict, stops: list[dict]) -> list[dict]:
    """
    Construct an initial route using the nearest-neighbor heuristic.

    Starting from the depot, repeatedly visit the closest unvisited stop.
    Produces a route that is typically ~25% worse than optimal but is
    computed in O(n²) time.

    Args:
        depot: The starting location dict with 'lat' and 'lng'.
        stops: List of stop dicts, each with 'lat', 'lng', and other fields.

    Returns:
        Ordered list of stops (depot first) representing the constructed route.
    """
    route = [depot]
    remaining = deepcopy(stops)
    current = depot

    while remaining:
        nearest = min(
            remaining,
            key=lambda s: haversine(
                (current["lat"], current["lng"]),
                (s["lat"], s["lng"]),
            ),
        )
        route.append(nearest)
        remaining.remove(nearest)
        current = nearest

    return route


def _two_opt_swap(route: list[dict], i: int, j: int) -> list[dict]:
    """
    Perform a 2-opt swap: reverse the segment between indices i and j.

    Given route: [... A-1, A, ..., B, B+1, ...]
    Produces:    [... A-1, B, ..., A, B+1, ...]
    (the segment from A to B is reversed)

    Args:
        route: Current route as a list of stop dicts.
        i: Start index of the segment to reverse.
        j: End index of the segment to reverse (inclusive).

    Returns:
        New route with the segment reversed.
    """
    new_route = route[:i] + route[i : j + 1][::-1] + route[j + 1 :]
    return new_route


def two_opt(route: list[dict]) -> list[dict]:
    """
    Improve a route using the 2-opt local search algorithm.

    Repeatedly tries reversing every possible segment of the route.
    If a reversal reduces total distance, it is accepted and the
    search restarts. Terminates when no improvement is found or
    the time budget is exhausted.

    Args:
        route: Initial route as a list of stop dicts.

    Returns:
        Improved route (always at least as good as the input).
    """
    best = route
    best_length = route_length(best)
    improved = True
    start_time = time.monotonic()

    while improved:
        improved = False

        # Check time budget
        if time.monotonic() - start_time > TWO_OPT_TIME_BUDGET:
            break

        for i in range(1, len(best) - 1):
            for j in range(i + 1, len(best)):
                # Check time budget inside inner loop for responsiveness
                if time.monotonic() - start_time > TWO_OPT_TIME_BUDGET:
                    return best

                candidate = _two_opt_swap(best, i, j)
                candidate_length = route_length(candidate)

                if candidate_length < best_length:
                    best = candidate
                    best_length = candidate_length
                    improved = True
                    break  # Restart outer loop on improvement

            if improved:
                break

    return best


def optimize_route(
    depot: dict, stops: list[dict]
) -> dict:
    """
    Full optimization pipeline: nearest-neighbor + 2-opt.

    Args:
        depot: Starting location dict with at least 'lat' and 'lng'.
        stops: List of delivery stop dicts with at least 'lat', 'lng',
               and 'address'.

    Returns:
        Dictionary containing:
            - ordered_stops: list of stops in optimized visit order
              (excluding depot), each with an added 'visit_order' field
            - naive_distance_km: total distance of the unoptimized
              (input-order) route
            - optimized_distance_km: total distance of the optimized route
            - pct_improvement: percentage reduction in distance
    """
    if not stops:
        return {
            "ordered_stops": [],
            "naive_distance_km": 0.0,
            "optimized_distance_km": 0.0,
            "pct_improvement": 0.0,
        }

    # Calculate naive distance (input order, starting from depot)
    naive_route = [depot] + stops
    naive_distance = route_length(naive_route)

    # Stage 1: Nearest-neighbor construction
    nn_route = nearest_neighbor(depot, stops)

    # Stage 2: 2-opt improvement
    optimized_route = two_opt(nn_route)
    optimized_distance = route_length(optimized_route)

    # Calculate improvement
    if naive_distance > 0:
        pct_improvement = round(
            ((naive_distance - optimized_distance) / naive_distance) * 100, 2
        )
    else:
        pct_improvement = 0.0

    # Assign visit_order to each stop (skip depot at index 0)
    ordered_stops = []
    for idx, stop in enumerate(optimized_route[1:], start=1):
        stop_with_order = {**stop, "visit_order": idx}
        ordered_stops.append(stop_with_order)

    return {
        "ordered_stops": ordered_stops,
        "naive_distance_km": round(naive_distance, 2),
        "optimized_distance_km": round(optimized_distance, 2),
        "pct_improvement": pct_improvement,
    }
