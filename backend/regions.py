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
    lat = np.asarray(lat, dtype=float)
    lon = np.asarray(lon, dtype=float)
    out = np.full(lat.shape, "Asien", dtype=object)
    out[lon < -25] = "Amerika"
    europe = (lon >= -25) & (lon < 60) & (lat >= 36)
    out[europe] = "Europa/Westasien"
    africa = (lon >= -25) & (lon < 55) & (lat < 36)
    out[africa] = "Afrika"
    west_asia = (lon >= 35) & (lon < 60) & (lat >= 12) & (lat < 36)
    out[west_asia] = "Europa/Westasien"
    oceania = (lon >= 110) & (lat < -10)
    out[oceania] = "Ozeanien"
    oceania_east = (lon >= 150) & (lat < 0)
    out[oceania_east] = "Ozeanien"
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
