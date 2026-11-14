"""
External data folders, e.g. the Parquet files on the team's server.

    FIRECAL_SOURCE=/data/firecal                 # one folder
    FIRECAL_SOURCE=/data/hotspots:/data/tables   # several, separated by ':' (';' on Windows)

Every folder is searched recursively. Files are sorted by what they contain:
  * hotspots   Parquet/CSV with latitude + longitude + (datetime_utc or acq_date): 'Tabelle A' or raw FIRMS
  * calibration  calibration*.csv / calibration*.parquet ('Tabelle C')
  * team tables  fire_activity_*.parquet ('Tabelle B', shown as map layer and cross-check)
Other files (monthly_cube, regions, ...) are ignored for now.

    FIRECAL_SOURCE_LOAD=0    link only: the folder is shown (files, rows, date range from the Parquet
                             footers) but its hotspots are NOT loaded into the app. Default 1 = load.
"""
from __future__ import annotations

import gzip
import os

import pyarrow.parquet as pq

SKIP_PREFIXES = ("fire_activity", "calibration", "monthly_cube", "regions", "sensor_timeline", ".")


def load_hotspots() -> bool:
    return os.environ.get("FIRECAL_SOURCE_LOAD", "1").strip().lower() not in ("0", "false", "no", "off")


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


def _date_range(path: str) -> tuple[str | None, str | None]:
    """Min/max date from the Parquet row-group statistics (reads only the footer)."""
    try:
        md = pq.ParquetFile(path).metadata
        names = [md.schema.column(i).name.lower() for i in range(md.num_columns)]
        col = next((c for c in ("acq_date", "datetime_utc") if c in names), None)
        if col is None:
            return None, None
        idx = names.index(col)
        lo = hi = None
        for g in range(md.num_row_groups):
            st = md.row_group(g).column(idx).statistics
            if st is None or not st.has_min_max:
                return None, None
            a, b = str(st.min)[:10], str(st.max)[:10]
            lo = a if lo is None or a < lo else lo
            hi = b if hi is None or b > hi else hi
        return lo, hi
    except Exception:
        return None, None


def summary() -> dict | None:
    """What the linked server folder holds, without loading it."""
    dirs = source_dirs()
    if not dirs:
        return None
    files = []
    for f in hotspot_files():
        rows = None
        if f.endswith(".parquet"):
            try:
                rows = pq.ParquetFile(f).metadata.num_rows
            except Exception:
                pass
        lo, hi = _date_range(f) if f.endswith(".parquet") else (None, None)
        files.append({"file": os.path.relpath(f, dirs[0]) if f.startswith(dirs[0]) else f,
                      "rows": rows, "bytes": os.path.getsize(f), "first_date": lo, "last_date": hi})
    known = [x for x in files if x["first_date"]]
    return {"folders": dirs, "loaded": load_hotspots(), "files": len(files),
            "rows": sum(x["rows"] or 0 for x in files), "bytes": sum(x["bytes"] for x in files),
            "first_date": min((x["first_date"] for x in known), default=None),
            "last_date": max((x["last_date"] for x in known), default=None),
            "calibration_files": len(calibration_files()), "team_files": len(team_files()), "per_file": files}
