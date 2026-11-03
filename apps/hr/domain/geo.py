from __future__ import annotations

import math

EARTH_RADIUS_M = 6_371_000.0


def haversine_m(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """Khoảng cách great-circle giữa 2 điểm (mét)."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlam = math.radians(lng2 - lng1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlam / 2) ** 2
    return 2 * EARTH_RADIUS_M * math.asin(math.sqrt(a))


def nearest_location(lat: float, lng: float, locations):
    """``locations``: iterable dict có latitude/longitude. Trả (location, distance_m) gần nhất hoặc (None, None)."""
    best = None
    best_d = None
    for loc in locations:
        d = haversine_m(lat, lng, loc["latitude"], loc["longitude"])
        if best_d is None or d < best_d:
            best_d, best = d, loc
    if best is None:
        return None, None
    return best, best_d
