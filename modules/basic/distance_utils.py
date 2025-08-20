# modules/basic/distance_utils.py
# Common geographic distance calculations for the project.

import logging

logger = logging.getLogger(__name__)

def haversine(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """
    Calculate the great-circle distance between two points on Earth in meters.
    Inputs are in decimal degrees.
    """
    # Earth radius in meters
    R = 6371000
    from math import radians, sin, cos, sqrt, atan2

    phi1, phi2 = radians(lat1), radians(lat2)
    dphi = radians(lat2 - lat1)
    dlambda = radians(lon2 - lon1)

    a = sin(dphi/2)**2 + cos(phi1) * cos(phi2) * sin(dlambda/2)**2
    c = 2 * atan2(sqrt(a), sqrt(1 - a))
    return R * c

# Threshold distance for proximity checks (3 ft ≈ 0.9144 m)
THRESHOLD_M = 3 / 3.28084


def bearing(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """
    Calculate the initial bearing (forward azimuth) from point A to point B.
    Returns a value in degrees [0, 360).
    """
    from math import radians, sin, cos, atan2, degrees

    phi1, phi2 = radians(lat1), radians(lat2)
    dlambda    = radians(lon2 - lon1)

    x = sin(dlambda) * cos(phi2)
    y = cos(phi1) * sin(phi2) - sin(phi1) * cos(phi2) * cos(dlambda)
    theta = atan2(x, y)
    return (degrees(theta) + 360) % 360