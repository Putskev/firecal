"""
External data folders, e.g. the Parquet files on the team's server.

    FIRECAL_SOURCE=/data/firecal                 # one folder
    FIRECAL_SOURCE=/data/hotspots:/data/tables   # several, separated by ':' (';' on Windows)

Every folder is searched recursively. Files are sorted by what they contain:
  * hotspots   Parquet/CSV with latitude + longitude + (datetime_utc or acq_date): 'Tabelle A' or raw FIRMS
  * calibration  calibration*.csv / calibration*.parquet ('Tabelle C')
  * team tables  fire_activity_*.parquet ('Tabelle B', shown as map layer and cross-check)
Other files (monthly_cube, regions, ...) are ignored for now.
"""
from __future__ import annotations

import gzip
import os

import pyarrow.parquet as pq

SKIP_PREFIXES = ("fire_activity", "calibration", "monthly_cube", "regions", "sensor_timeline", ".")


def source_dirs() -> list[str]:
    return [d for d in os.environ.get("FIRECAL_SOURCE", "").split(os.pathsep) if d.strip() and os.path.isdir(d)]


def _walk(exts: tuple[str, ...]) -> list[str]:
    out = []
    for d in source_dirs():
        for root, dirs, files in os.walk(d):
            dirs[:] = [x for x in dirs if not x.startswith(".")]
            out += [os.path.join(root, f) for f in files if f.lower().endswith(exts)]
    return sorted(out)


def _columns(path: str) -> set[str]:
    try:
        if path.endswith(".parquet"):
            return {c.lower() for c in pq.read_schema(path).names}
        opener = gzip.open if path.endswith(".gz") else open
        with opener(path, "rt", encoding="utf-8", errors="ignore") as f:
            return {c.strip().strip('"').lower() for c in f.readline().split(",")}
    except Exception:
        return set()


def hotspot_files() -> list[str]:
    files = []
    for f in _walk((".parquet", ".csv", ".csv.gz")):
        if os.path.basename(f).lower().startswith(SKIP_PREFIXES):
            continue
        cols = _columns(f)
        if {"latitude", "longitude"} <= cols and ({"datetime_utc"} & cols or {"acq_date"} & cols):
            files.append(f)
    return files


def calibration_files() -> list[str]:
    return [f for f in _walk((".csv", ".parquet")) if os.path.basename(f).lower().startswith("calibration")]


def team_files() -> list[str]:
    return [f for f in _walk((".parquet",)) if os.path.basename(f).lower().startswith("fire_activity_")]
