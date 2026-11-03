"""GCJ-02 <-> WGS-84 for map data from mainland China.

Map services licensed in mainland China (AMap / Gaode among them) publish
coordinates in GCJ-02, a datum shifted by a few hundred metres from the WGS-84
that GPS, Google Maps and the rest of Pathfinder use. The shift applies only
inside mainland China; Hong Kong, Macau, Taiwan and every other country are
published in WGS-84 already, so their points are returned untouched.

The forward formula is the standard published one (Krasovsky 1940 ellipsoid).
The reverse has no closed form, so it is solved by fixed-point iteration, which
converges to well under a centimetre in a few steps.
"""

from __future__ import annotations

import math

_A = 6378245.0  # Krasovsky 1940 semi-major axis
_EE = 0.00669342162296594323  # its first eccentricity squared

#: Rough outline of mainland China as (north, west, south, east) boxes ...
_MAINLAND = (
    (49.220400, 79.446200, 42.889900, 96.330000),
    (54.141500, 109.687200, 39.374200, 135.000200),
    (42.889900, 73.124600, 29.529700, 124.143255),
    (29.529700, 82.968400, 26.718600, 97.035200),
    (29.529700, 97.025300, 20.414096, 124.367395),
    (20.414096, 107.975793, 17.871542, 111.744104),
)
#: ... minus the places inside those boxes that are not mainland China.
_NOT_MAINLAND = (
    (25.398623, 119.921265, 21.785006, 122.497559),  # Taiwan
    (22.284000, 101.865200, 20.098800, 106.665000),  # northern Laos / Vietnam
    (21.542200, 106.452500, 20.487800, 108.051000),  # northern Vietnam coast
    (55.817500, 109.032300, 50.325700, 119.127000),  # Mongolia / Russia
    (55.817500, 127.456800, 49.557400, 137.022700),  # Russian Far East
    (44.892200, 131.266200, 42.569200, 137.022700),  # Russian Far East coast
    (22.430000, 113.830000, 22.150000, 113.950000),  # Hong Kong: Lantau, Tuen Mun
    (22.510000, 113.950000, 22.150000, 114.440000),  # Hong Kong: Kowloon, island, NT
    (22.217000, 113.528000, 22.105000, 113.600000),  # Macau
)


def in_mainland_china(lat: float, lng: float) -> bool:
    """True for a point inside mainland China (boxes: within a few kilometres of a border it may err)."""

    def inside(box: tuple[float, float, float, float]) -> bool:
        north, west, south, east = box
        return south <= lat <= north and west <= lng <= east

    return any(inside(box) for box in _MAINLAND) and not any(inside(box) for box in _NOT_MAINLAND)


def _shift_lat(x: float, y: float) -> float:
    ret = -100.0 + 2.0 * x + 3.0 * y + 0.2 * y * y + 0.1 * x * y + 0.2 * math.sqrt(abs(x))
    ret += (20.0 * math.sin(6.0 * x * math.pi) + 20.0 * math.sin(2.0 * x * math.pi)) * 2.0 / 3.0
    ret += (20.0 * math.sin(y * math.pi) + 40.0 * math.sin(y / 3.0 * math.pi)) * 2.0 / 3.0
    ret += (160.0 * math.sin(y / 12.0 * math.pi) + 320.0 * math.sin(y * math.pi / 30.0)) * 2.0 / 3.0
    return ret


def _shift_lng(x: float, y: float) -> float:
    ret = 300.0 + x + 2.0 * y + 0.1 * x * x + 0.1 * x * y + 0.1 * math.sqrt(abs(x))
    ret += (20.0 * math.sin(6.0 * x * math.pi) + 20.0 * math.sin(2.0 * x * math.pi)) * 2.0 / 3.0
    ret += (20.0 * math.sin(x * math.pi) + 40.0 * math.sin(x / 3.0 * math.pi)) * 2.0 / 3.0
    ret += (150.0 * math.sin(x / 12.0 * math.pi) + 300.0 * math.sin(x / 30.0 * math.pi)) * 2.0 / 3.0
    return ret


def _offset(lat: float, lng: float) -> tuple[float, float]:
    dlat = _shift_lat(lng - 105.0, lat - 35.0)
    dlng = _shift_lng(lng - 105.0, lat - 35.0)
    radlat = lat / 180.0 * math.pi
    magic = 1 - _EE * math.sin(radlat) ** 2
    sqrtmagic = math.sqrt(magic)
    dlat = (dlat * 180.0) / ((_A * (1 - _EE)) / (magic * sqrtmagic) * math.pi)
    dlng = (dlng * 180.0) / (_A / sqrtmagic * math.cos(radlat) * math.pi)
    return dlat, dlng


def wgs84_to_gcj02(lat: float, lng: float) -> tuple[float, float]:
    """The GCJ-02 point a mainland map service publishes for a WGS-84 point."""
    if not in_mainland_china(lat, lng):
        return lat, lng
    dlat, dlng = _offset(lat, lng)
    return lat + dlat, lng + dlng


def gcj02_to_wgs84(lat: float, lng: float) -> tuple[float, float]:
    """The WGS-84 point for a GCJ-02 point; points outside mainland China are unchanged."""
    if not in_mainland_china(lat, lng):
        return lat, lng
    wlat, wlng = lat, lng
    for _ in range(30):
        dlat, dlng = _offset(wlat, wlng)
        err_lat = (wlat + dlat) - lat
        err_lng = (wlng + dlng) - lng
        wlat -= err_lat
        wlng -= err_lng
        if abs(err_lat) < 1e-10 and abs(err_lng) < 1e-10:
            break
    return wlat, wlng
