"""
Coarse regions used for the calibration factors.

PLACEHOLDER, as in docs/HANDOFF_Frontend.md ("grobe Längen-/Breitenbox, später echte
Länder/Provinzen"). The names match the data team's calibration table:
Afrika, Amerika, Asien, Europa/Westasien, Ozeanien.
Replace `region_of` once the team delivers real regions (regions.geojson).
"""
from __future__ import annotations

import numpy as np

REGIONS = ["Afrika", "Amerika", "Asien", "Europa/Westasien", "Ozeanien"]


def region_of(lat, lon) -> np.ndarray:
    """Placeholder regions as boxes, identical to the data team's assignment in 'Tabelle B'
    (checked against every cell of fire_activity_7d_2km). Replace with regions.geojson later."""
    lat = np.asarray(lat, dtype=float)
    lon = np.asarray(lon, dtype=float)
    out = np.full(lat.shape, "Europa/Westasien", dtype=object)    # rest: -30 <= lon < 55, north of 35 deg or west of -20
    out[(lon >= -20) & (lon < 55) & (lat < 35)] = "Afrika"
    out[lon >= 55] = "Asien"
    out[(lon >= 110) & (lat < -10)] = "Ozeanien"
    out[lon < -30] = "Amerika"
    return out


# Satellite timeline (start of operational active fire data; end = still running)
SATELLITES = [
    {"satellite": "Terra", "sensor": "MODIS", "start": "2000-11-01"},
    {"satellite": "Aqua", "sensor": "MODIS", "start": "2002-07-04"},
    {"satellite": "SNPP", "sensor": "VIIRS", "start": "2012-01-20"},
    {"satellite": "N20", "sensor": "VIIRS", "start": "2018-01-01"},
    {"satellite": "N21", "sensor": "VIIRS", "start": "2023-01-01"},
]
VIIRS_START = "2012-01-20"
