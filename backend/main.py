"""
FireCal API (FastAPI + DuckDB).

    uvicorn backend.main:app --reload          # http://localhost:8000  (app)  /docs (API docs)

Data access
-----------
DuckDB queries the Parquet files in data/processed/ directly from disk (nothing is loaded into
memory up front):
  * timeseries, matrix, satellites: pre-aggregated cells (cells_month 0.1 deg, cells_day 0.25 deg)
  * heatmap: cells when the grid is coarse enough, single hotspots when zoomed in
  * points, distributions, CSV export: single hotspots (only year/month folders in the date range)
  * team/*: the data team's aggregated table "Tabelle B" (data/team/fire_activity_7d_<grid>.parquet),
    served as delivered, for the map and for a cross-check against FireCal's own numbers

Filters shared by all analysis endpoints
----------------------------------------
    bbox=west,south,east,north        polygon=lat,lon;lat,lon;...   (cells: centre must be inside)
    start=YYYY-MM-DD  end=YYYY-MM-DD  exclude_low=true  vegetation_only=false
    satellites=Terra,Aqua,SNPP,N20,N21  daynight=all|D|N
satellites and daynight filter the RAW values and the map; the HARMONIZED values always use all
satellites and day + night, because the calibration factors are defined that way.
"""
from __future__ import annotations

import calendar
import glob
import json
import math
import time
import os
import tempfile
import threading
import uuid
from typing import Literal

import duckdb
import numpy as np
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from starlette.background import BackgroundTask

from .build import build
from .paths import (CELLS_DAY, CELLS_MONTH, DAY_RES, DIST_MONTH, DIST_RES, FRP_EDGES, HOTSPOTS_DIR, META, MONTH_RES,
                    ROOT, SERIES_DAY, SERIES_MONTH, TEAM)

API_VERSION = "2.0"
app = FastAPI(title="FireCal API", version=API_VERSION,
              description="Harmonized MODIS + VIIRS active fire hotspots (NASA FIRMS), VIIRS level")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["GET"], allow_headers=["*"])
app.add_middleware(GZipMiddleware, minimum_size=2000)

_db: duckdb.DuckDBPyConnection | None = None
_meta: dict = {}
_team: dict = {}          # grid label -> {"view", "rows", "periods"}
_lock = threading.Lock()
ALL_SATS = ["Terra", "Aqua", "SNPP", "N20", "N21"]


_build_state = {"running": False, "started": None, "error": None}


def _register(meta: dict) -> None:
    """Open a new DuckDB connection with views on data/processed/ and swap it in."""
    global _db, _meta
    con = duckdb.connect(":memory:")
    con.execute(f"""CREATE VIEW points AS SELECT * FROM read_parquet('{HOTSPOTS_DIR}/**/*.parquet',
                    hive_partitioning = true)""")
    con.execute(f"CREATE VIEW cells_month AS SELECT * FROM read_parquet('{CELLS_MONTH}/*.parquet')")
    con.execute(f"CREATE VIEW cells_day AS SELECT * FROM read_parquet('{CELLS_DAY}/*.parquet')")
    con.execute(f"CREATE VIEW dist_month AS SELECT * FROM read_parquet('{DIST_MONTH}/*.parquet')")
    con.execute(f"CREATE VIEW series_month AS SELECT * FROM read_parquet('{SERIES_MONTH}/*.parquet')")
    con.execute(f"CREATE VIEW series_day AS SELECT * FROM read_parquet('{SERIES_DAY}/*.parquet')")
    team = {}
    from .sources import summary, team_files
    found = {os.path.basename(f): f for f in sorted(glob.glob(os.path.join(TEAM, "fire_activity_*.parquet")))}
    found.update({os.path.basename(f): f for f in team_files()})   # server files win over the repo copies
    for f in found.values():
        grid = os.path.basename(f)[len("fire_activity_"):-len(".parquet")].split("_", 1)[-1]  # 7d_0.5deg -> 0.5deg
        view = "team_" + "".join(ch if ch.isalnum() else "_" for ch in grid)
        con.execute(f"CREATE VIEW {view} AS SELECT * FROM read_parquet('{f}')")
        per = con.execute(f"""SELECT CAST(period_start AS VARCHAR), CAST(period_end AS VARCHAR), max(n_days)
                              FROM {view} GROUP BY 1, 2 ORDER BY 1""").fetchall()
        team[grid] = {"view": view, "file": os.path.relpath(f, ROOT) if f.startswith(ROOT) else f,
                      "rows": con.execute(f"SELECT count(*) FROM {view}").fetchone()[0],
                      "periods": [{"start": a, "end": b, "n_days": int(n)} for a, b, n in per]}
    try:
        meta = {**meta, "server_data": summary()}
    except Exception as ex:          # never let the summary break the app
        meta = {**meta, "server_data": {"error": str(ex)}}
    old, _db, _meta = _db, con, meta
    _team.clear(); _team.update(team)
    if old is not None:
        old.close()


def _background_build() -> None:
    try:
        meta = build()
        with _lock:
            _register(meta)
        _build_state["error"] = None
    except Exception as ex:
        _build_state["error"] = str(ex)
        print(f"[api] background build failed: {ex}")
    finally:
        _build_state["running"] = False


def load() -> None:
    """Serve data/processed/. If the inputs changed and an older complete build exists, keep serving it and
    rebuild in the background (a build of the full archive takes minutes); otherwise build first."""
    from .build import needs_build, ready
    from .sources import hotspot_files, load_hotspots
    with _lock:
        if not ready() and load_hotspots() and hotspot_files():
            _register(build(force=True, linked=False))    # quick start with the repo data, full archive below
        if needs_build() and ready():
            if _db is None:
                with open(META) as f:
                    _register(json.load(f))
            if not _build_state["running"]:
                _build_state.update(running=True, started=time.strftime("%Y-%m-%d %H:%M:%S"), error=None)
                threading.Thread(target=_background_build, daemon=True).start()
            return
        _register(build())


@app.on_event("startup")
def _startup() -> None:
    try:
        load()
    except FileNotFoundError as ex:
        print(f"[api] {ex}")


def cur() -> duckdb.DuckDBPyConnection:
    if _db is None:
        raise HTTPException(503, "No data loaded. See README: ingest FIRMS files, then restart.")
    return _db.cursor()


# ---------------------------------------------------------------- filters
def _parse_bbox(bbox: str | None) -> list[float] | None:
    if not bbox:
        return None
    try:
        w, s, e, n = [float(x) for x in bbox.split(",")]
    except ValueError:
        raise HTTPException(422, "bbox must be west,south,east,north")
    if not (-180 <= w < e <= 180 and -90 <= s < n <= 90):
        raise HTTPException(422, "bbox out of range or min >= max")
    return [w, s, e, n]


def _parse_polygon(polygon: str | None) -> np.ndarray | None:
    if not polygon:
        return None
    try:
        pts = np.array([[float(v) for v in p.split(",")] for p in polygon.split(";") if p.strip()])
    except ValueError:
        raise HTTPException(422, "polygon must be lat,lon;lat,lon;...")
    if pts.ndim != 2 or pts.shape[1] != 2 or len(pts) < 3:
        raise HTTPException(422, "polygon needs at least 3 lat,lon vertices")
    return pts


def _in_polygon(lat: np.ndarray, lon: np.ndarray, poly: np.ndarray) -> np.ndarray:
    inside = np.zeros(len(lat), dtype=bool)
    py, px = poly[:, 0], poly[:, 1]
    j = len(poly) - 1
    for i in range(len(poly)):
        cond = (py[i] > lat) != (py[j] > lat)
        with np.errstate(divide="ignore", invalid="ignore"):
            xint = (px[j] - px[i]) * (lat - py[i]) / (py[j] - py[i]) + px[i]
        inside ^= cond & (lon < xint)
        j = i
    return inside


def _lit(v) -> str:
    if isinstance(v, (int, float, np.floating, np.integer)):
        return repr(float(v))
    return "'" + str(v).replace("'", "''") + "'"


SOURCES = {
    "points":      {"lat": "latitude", "lon": "longitude", "low": "conf_class = 'low'",
                    "veg": "(type IS NULL OR type = 0)", "date": "acq_date", "night": "daynight = 'N'"},
    "cells_month": {"lat": "lat", "lon": "lon", "low": "low", "veg": "veg", "date": "period", "night": "night"},
    "cells_day":   {"lat": "lat", "lon": "lon", "low": "low", "veg": "veg", "date": "period", "night": "night"},
    "series_month": {"lat": None, "lon": None, "low": "low", "veg": "veg", "date": "period", "night": "night"},
    "series_day":   {"lat": None, "lon": None, "low": "low", "veg": "veg", "date": "period", "night": "night"},
    "dist_month":  {"lat": "lat", "lon": "lon", "low": "conf_class = 'low'", "veg": "veg", "date": "period",
                    "night": "daynight = 'N'"},
}


def _sats(satellites: str | None) -> list[str]:
    if not satellites:
        return ALL_SATS
    s = [x.strip() for x in satellites.split(",") if x.strip()]
    bad = set(s) - set(ALL_SATS)
    if bad:
        raise HTTPException(422, f"unknown satellites {bad}; allowed {ALL_SATS}")
    return s


def raw_cond(source: str, satellites: str | None, daynight: str) -> str:
    """SQL condition for the RAW filters (satellites, day/night)."""
    s = SOURCES[source]
    parts = []
    sats = _sats(satellites)
    if set(sats) != set(ALL_SATS):
        parts.append("satellite IN (" + ",".join(_lit(x) for x in sats) + ")" if sats else "FALSE")
    if daynight == "D":
        parts.append(f"NOT ({s['night']})")
    elif daynight == "N":
        parts.append(s["night"])
    return " AND ".join(parts) if parts else "TRUE"


def selection(c, source, bbox, polygon, start, end, exclude_low, vegetation_only, extra: str | None = None) -> str:
    """Create a view on cursor `c` with the filtered rows of `source`; returns its name."""
    s = SOURCES[source]
    where = []
    box = _parse_bbox(bbox)
    poly = _parse_polygon(polygon)
    if poly is not None:
        pb = [poly[:, 1].min(), poly[:, 0].min(), poly[:, 1].max(), poly[:, 0].max()]
        box = pb if box is None else [max(box[0], pb[0]), max(box[1], pb[1]), min(box[2], pb[2]), min(box[3], pb[3])]
    if box:
        where.append(f"{s['lon']} BETWEEN {_lit(box[0])} AND {_lit(box[2])} "
                     f"AND {s['lat']} BETWEEN {_lit(box[1])} AND {_lit(box[3])}")
    if source in ("cells_month", "dist_month", "series_month"):
        start, end = (start[:7] if start else None), (end[:7] if end else None)
    if start:
        where.append(f"{s['date']} >= {_lit(start)}")
    if end:
        where.append(f"{s['date']} <= {_lit(end)}")
    if source == "points" and start:            # partition pruning
        where.append(f"(y * 100 + m) >= {int(start[:4]) * 100 + int(start[5:7])}")
    if source == "points" and end:
        where.append(f"(y * 100 + m) <= {int(end[:4]) * 100 + int(end[5:7])}")
    if exclude_low:
        where.append(f"NOT ({s['low']})")
    if vegetation_only:
        where.append(s["veg"])
    if extra:
        where.append(extra)
    sql = f"SELECT * FROM {source}" + (" WHERE " + " AND ".join(where) if where else "")
    name = "sel_" + uuid.uuid4().hex[:8]
    if poly is None:
        c.execute(f"CREATE TEMP VIEW {name} AS {sql}")
    else:
        df = c.execute(sql).df()
        df = df[_in_polygon(df[s["lat"]].to_numpy(), df[s["lon"]].to_numpy(), poly)]
        c.register(name, df)
    return name


Q = dict(
    bbox=Query(None, description="west,south,east,north"),
    polygon=Query(None, description="lat,lon;lat,lon;..."),
    start=Query(None, pattern=r"^\d{4}-\d{2}-\d{2}$"),
    end=Query(None, pattern=r"^\d{4}-\d{2}-\d{2}$"),
    exclude_low=Query(True, description="drop low-confidence detections"),
    vegetation_only=Query(False, description="keep type = 0 (rows without type are kept)"),
    satellites=Query(None, description="comma list of Terra,Aqua,SNPP,N20,N21 (raw values and map only)"),
    daynight=Query("all", pattern="^(all|D|N)$", description="raw values and map only"),
    conf=Query(None, description="map only: comma list of low,nominal,high (overrides exclude_low)"),
)


def conf_cond(conf: str | None) -> str | None:
    if not conf:
        return None
    cls = [c.strip().lower() for c in conf.split(",") if c.strip()]
    bad = set(cls) - {"low", "nominal", "high"}
    if bad:
        raise HTTPException(422, f"unknown confidence classes {bad}")
    return "conf_class IN (" + ",".join(_lit(c) for c in cls) + ")" if cls else "FALSE"


def _rows(df) -> list[list]:
    return [[None if (isinstance(v, float) and math.isnan(v)) else (round(v, 4) if isinstance(v, float) else v)
             for v in r] for r in df.itertuples(index=False, name=None)]


# ---------------------------------------------------------------- endpoints
@app.get("/api/health")
def health():
    return {"ok": _db is not None}


@app.get("/api/meta")
def meta():
    """Data span and version, counts per satellite, satellite timeline, harmonization method and
    calibration table (factor per region with 25-75 % band)."""
    cur()
    from .build import PROGRESS
    return {"api": "firecal", "version": API_VERSION, **_meta,
            "rebuilding": {**_build_state, "progress": dict(PROGRESS) if _build_state["running"] else None}}


@app.get("/api/timeseries")
def timeseries(granularity: Literal["month", "day"] = "month",
               bbox: str | None = Q["bbox"], polygon: str | None = Q["polygon"],
               start: str | None = Q["start"], end: str | None = Q["end"],
               exclude_low: bool = Q["exclude_low"], vegetation_only: bool = Q["vegetation_only"],
               satellites: str | None = Q["satellites"], daynight: str = Q["daynight"]):
    """Per period. Monthly: over the WHOLE data span (medians need history). Daily: start/end (if given) limit
    the window, which keeps daily requests fast on a long archive.

    raw (respects satellites/daynight): modis_n, viirs_n, modis_frp, viirs_frp, raw_n, raw_frp, n_night, n_high
    harmonized (VIIRS level, all satellites): harm_n, harm_n_lo, harm_n_hi, harm_frp, harm_frp_lo, harm_frp_hi,
    modis_h_n / viirs_h_n (share of harm_n from calibrated MODIS / native VIIRS), modis_cal_n, modis_cal_frp
    (MODIS x factor for every period, also after the VIIRS start)."""
    c = cur()
    source = ("cells_" if (bbox or polygon) else "series_") + granularity   # no area: the small global series
    # monthly: always the whole span (the medians need every year); daily: start/end limit the window
    rel = selection(c, source, bbox, polygon, start if granularity == "day" else None,
                    end if granularity == "day" else None, exclude_low, vegetation_only)
    rc = raw_cond(source, satellites, daynight)
    df = c.execute(f"""
        SELECT period,
          coalesce(sum(n)   FILTER (WHERE sensor='MODIS' AND {rc}), 0) AS modis_n,
          coalesce(sum(n)   FILTER (WHERE sensor='VIIRS' AND {rc}), 0) AS viirs_n,
          coalesce(sum(frp) FILTER (WHERE sensor='MODIS' AND {rc}), 0) AS modis_frp,
          coalesce(sum(frp) FILTER (WHERE sensor='VIIRS' AND {rc}), 0) AS viirs_frp,
          coalesce(sum(n)   FILTER (WHERE {rc}), 0) AS raw_n,
          coalesce(sum(frp) FILTER (WHERE {rc}), 0) AS raw_frp,
          coalesce(sum(n)   FILTER (WHERE night AND {rc}), 0) AS n_night,
          coalesce(sum(n_high) FILTER (WHERE {rc}), 0) AS n_high,
          coalesce(sum(w), 0) AS harm_n, coalesce(sum(w_lo), 0) AS harm_n_lo, coalesce(sum(w_hi), 0) AS harm_n_hi,
          coalesce(sum(fh), 0) AS harm_frp, coalesce(sum(fh_lo), 0) AS harm_frp_lo, coalesce(sum(fh_hi), 0) AS harm_frp_hi,
          coalesce(sum(w) FILTER (WHERE sensor='MODIS'), 0) AS modis_h_n,
          coalesce(sum(w) FILTER (WHERE sensor='VIIRS'), 0) AS viirs_h_n,
          coalesce(sum(wc), 0) AS modis_cal_n, coalesce(sum(fc), 0) AS modis_cal_frp
        FROM {rel} GROUP BY 1 ORDER BY 1""").df()
    rows = {r.pop("period"): {k: round(float(v), 3) for k, v in r.items()} for r in df.to_dict("records")}
    return {"granularity": granularity, "first_date": _meta.get("first_date"), "last_date": _meta.get("last_date"),
            "rows": rows}


def _whole_months(start: str | None, end: str | None) -> bool:
    if start and not start.endswith("-01"):
        return False
    if end:
        y, m, d = (int(x) for x in end.split("-"))
        return d == calendar.monthrange(y, m)[1]
    return True


def _value(source: str, view: str, metric: str, rc: str) -> str:
    pts = source == "points"
    if view == "harm":
        return ("weight" if pts else "w") if metric == "count" else ("frp_h" if pts else "fh")
    base = ("1.0" if pts else "n") if metric == "count" else "frp"
    cond = rc if view == "raw" else f"sensor = '{'MODIS' if view == 'modis' else 'VIIRS'}' AND {rc}"
    return f"CASE WHEN {cond} THEN {base} ELSE 0 END"


@app.get("/api/heatmap")
def heatmap(view: Literal["harm", "raw", "modis", "viirs"] = "harm", metric: Literal["count", "frp"] = "count",
            res: float = Query(0.25, ge=0.01, le=5, description="grid cell size in degrees"),
            bbox: str | None = Q["bbox"], polygon: str | None = Q["polygon"],
            start: str | None = Q["start"], end: str | None = Q["end"],
            exclude_low: bool = Q["exclude_low"], vegetation_only: bool = Q["vegetation_only"],
            satellites: str | None = Q["satellites"], daynight: str = Q["daynight"], conf: str | None = Q["conf"]):
    """Grid cells [[lat, lon, value], ...] for the map. Pass the visible map area as bbox.
    conf (confidence classes) switches to the single hotspots, because the cells only know low / not low."""
    if conf:
        source = "points"
    elif res >= MONTH_RES - 1e-9 and _whole_months(start, end):
        source = "cells_month"
    elif res >= DAY_RES - 1e-9:
        source = "cells_day"
    else:
        source = "points"
    s = SOURCES[source]
    c = cur()
    rel = selection(c, source, bbox, polygon, start, end, exclude_low and not conf, vegetation_only, extra=conf_cond(conf))
    val = _value(source, view, metric, raw_cond(source, satellites, daynight))
    rows = c.execute(f"""
        SELECT floor({s['lat']} / {res}) * {res} + {res / 2} AS lat,
               floor({s['lon']} / {res}) * {res} + {res / 2} AS lon, sum({val}) AS v
        FROM {rel} GROUP BY 1, 2 HAVING sum({val}) > 0""").fetchall()
    return {"res": res, "source": source, "cells": [[round(a, 4), round(b, 4), round(v, 4)] for a, b, v in rows]}


@app.get("/api/points")
def points(bbox: str = Query(..., description="west,south,east,north (visible map area)"),
           polygon: str | None = Q["polygon"], start: str | None = Q["start"], end: str | None = Q["end"],
           exclude_low: bool = Q["exclude_low"], vegetation_only: bool = Q["vegetation_only"],
           satellites: str | None = Q["satellites"], daynight: str = Q["daynight"], conf: str | None = Q["conf"],
           limit: int = Query(20000, ge=1, le=100000)):
    """Single RAW hotspots for the map (never harmonized). If more than `limit` match, the strongest
    `limit` by FRP are returned and truncated = true.
    columns: lat, lon, sensor, satellite, frp, conf_class, acq_date, acq_time, daynight, pixel_km2, local_hour"""
    c = cur()
    cc = conf_cond(conf)
    rc = raw_cond("points", satellites, daynight)
    rel = selection(c, "points", bbox, polygon, start, end, exclude_low and not conf, vegetation_only,
                    extra=f"{rc} AND {cc}" if cc else rc)
    total = c.execute(f"SELECT count(*) FROM {rel}").fetchone()[0]
    df = c.execute(f"""SELECT latitude, longitude, sensor, satellite, frp, conf_class, acq_date, acq_time,
                              daynight, pixel_km2, local_hour FROM {rel} ORDER BY frp DESC LIMIT {limit}""").df()
    return {"count": int(total), "truncated": bool(total > limit),
            "columns": ["lat", "lon", "sensor", "satellite", "frp", "conf_class", "acq_date", "acq_time",
                        "daynight", "pixel_km2", "local_hour"], "rows": _rows(df)}




EXACT_DAYS = 62   # periods up to this length use the single hotspots (exact), longer ones the histogram cells


def _hist_quantile(counts: list[int], q: float) -> float | None:
    total = sum(counts)
    if not total:
        return None
    goal, acc = q * total, 0
    for i, c in enumerate(counts):
        if acc + c >= goal and c:
            lo, hi = FRP_EDGES[i], FRP_EDGES[i + 1] if FRP_EDGES[i + 1] < 1e9 else FRP_EDGES[i] * 2
            return round(lo + (hi - lo) * (goal - acc) / c, 2)
        acc += c
    return None


@app.get("/api/distributions")
def distributions(bbox: str | None = Q["bbox"], polygon: str | None = Q["polygon"],
                  start: str | None = Q["start"], end: str | None = Q["end"],
                  exclude_low: bool = Q["exclude_low"], vegetation_only: bool = Q["vegetation_only"],
                  satellites: str | None = Q["satellites"], daynight: str = Q["daynight"]):
    """Distributions of the RAW hotspots in area and period, split by sensor:
    frp (MW, bins frp_edges), local_hour (24 bins), conf_class, satellite, daynight.
    Low-confidence detections are included in conf_class even if exclude_low is set.
    Periods up to 62 days: exact, from the single hotspots. Longer periods: from the histogram cells
    (whole months, area by 1-degree cell centre); basis tells which one was used."""
    c = cur()
    s0, e0 = start or _meta.get("first_date"), end or _meta.get("last_date")
    days = (np.datetime64(e0) - np.datetime64(s0)).astype(int) + 1 if s0 and e0 else 10 ** 6
    exact = days <= EXACT_DAYS
    lowf = "conf_class <> 'low'" if exclude_low else "TRUE"
    nb = len(FRP_EDGES) - 1

    def grid(sql, n):
        out = {"MODIS": [0] * n, "VIIRS": [0] * n}
        for sensor, b, cnt in c.execute(sql).fetchall():
            if b is not None and 0 <= int(b) < n:
                out[sensor][int(b)] = int(cnt)
        return out

    if exact:
        rel = selection(c, "points", bbox, polygon, start, end, False, vegetation_only,
                        extra=raw_cond("points", satellites, daynight))
        case = " ".join(f"WHEN frp < {FRP_EDGES[i + 1]} THEN {i}" for i in range(nb))
        frp = grid(f"SELECT sensor, CASE {case} END, count(*) FROM {rel} WHERE {lowf} GROUP BY 1, 2", nb)
        hour = grid(f"SELECT sensor, floor(local_hour)::INT % 24, count(*) FROM {rel} WHERE {lowf} AND local_hour IS NOT NULL GROUP BY 1, 2", 24)
        cnt, frps, pxs = "count(*)", "frp", "pixel_km2"
        stats = c.execute(f"""SELECT sensor, count(*), median(frp), quantile_cont(frp, 0.9), max(frp), avg(pixel_km2)
                              FROM {rel} WHERE {lowf} GROUP BY 1""").fetchall()
    else:
        rel = selection(c, "dist_month", bbox, polygon, start, end, False, vegetation_only,
                        extra=raw_cond("dist_month", satellites, daynight))
        frp = grid(f"SELECT sensor, fbin, sum(n) FROM {rel} WHERE {lowf} GROUP BY 1, 2", nb)
        hour = grid(f"SELECT sensor, hour, sum(n) FROM {rel} WHERE {lowf} AND hour IS NOT NULL GROUP BY 1, 2", 24)
        cnt = "sum(n)"
        stats = [(s, int(n), _hist_quantile(frp.get(s, [0] * nb), .5), _hist_quantile(frp.get(s, [0] * nb), .9), mx, px / n if n else None)
                 for s, n, mx, px in c.execute(f"SELECT sensor, sum(n), max(frp_max), sum(px) FROM {rel} WHERE {lowf} GROUP BY 1").fetchall()]
    conf = {f"{a}|{b}": int(n) for a, b, n in c.execute(f"SELECT sensor, conf_class, {cnt} FROM {rel} GROUP BY 1, 2").fetchall()}
    sat = {a: int(n) for a, n in c.execute(f"SELECT satellite, {cnt} FROM {rel} WHERE {lowf} GROUP BY 1").fetchall()}
    dn = {f"{a}|{b}": int(n) for a, b, n in c.execute(f"SELECT sensor, daynight, {cnt} FROM {rel} WHERE {lowf} GROUP BY 1, 2").fetchall()}
    basis = ("exact: single hotspots" if exact else
             f"histogram cells: whole months {str(s0)[:7]} to {str(e0)[:7]}, area by {DIST_RES:g} degree cell centre, "
             "median / 90th percentile interpolated within the FRP classes")
    return {"frp_edges": FRP_EDGES[:-1] + [None], "frp": frp, "local_hour": hour, "conf_class": conf,
            "satellite": sat, "daynight": dn, "start": s0, "end": e0, "exact": bool(exact), "basis": basis,
            "stats": {s: {"n": int(n), "frp_median": med, "frp_p90": p90, "frp_max": mx, "pixel_km2_mean": px}
                      for s, n, med, p90, mx, px in stats}}


@app.get("/api/matrix")
def matrix(granularity: Literal["month", "day"] = "month",
           view: Literal["harm", "raw", "modis", "viirs"] = "harm", metric: Literal["count", "frp"] = "count",
           res: float | None = Query(None, ge=0.25, le=20, description="cell size in degrees (auto if empty)"),
           top: int = Query(30, ge=1, le=200),
           bbox: str | None = Q["bbox"], polygon: str | None = Q["polygon"],
           start: str | None = Q["start"], end: str | None = Q["end"],
           exclude_low: bool = Q["exclude_low"], vegetation_only: bool = Q["vegetation_only"],
           satellites: str | None = Q["satellites"], daynight: str = Q["daynight"]):
    """Space x time matrix: the `top` most active cells in the area (rows) x periods (columns)."""
    source = "cells_month" if granularity == "month" else "cells_day"
    c = cur()
    if res is None:
        box = _parse_bbox(bbox) or [-180, -60, 180, 80]
        span = max(box[2] - box[0], box[3] - box[1])
        res = next((r for r in [0.25, 0.5, 1, 2, 5, 10] if r >= span / 12), 10)
    rel = selection(c, source, bbox, polygon, start, end, exclude_low, vegetation_only)
    val = _value(source, view, metric, raw_cond(source, satellites, daynight))
    df = c.execute(f"""
        WITH x AS (SELECT floor(lat / {res}) * {res} + {res / 2} AS clat, floor(lon / {res}) * {res} + {res / 2} AS clon,
                          period, sum({val}) AS v FROM {rel} GROUP BY 1, 2, 3),
             t AS (SELECT clat, clon, sum(v) AS total FROM x GROUP BY 1, 2 HAVING sum(v) > 0 ORDER BY total DESC LIMIT {top})
        SELECT x.clat, x.clon, t.total, x.period, x.v FROM x JOIN t USING (clat, clon) ORDER BY t.total DESC""").df()
    rows = {}
    for r in df.itertuples(index=False):
        key = (round(r.clat, 4), round(r.clon, 4))
        rows.setdefault(key, {"lat": key[0], "lon": key[1], "total": round(float(r.total), 3), "values": {}})
        rows[key]["values"][r.period] = round(float(r.v), 3)
    from .regions import region_of
    out = list(rows.values())
    for o in out:
        o["region"] = str(region_of([o["lat"]], [o["lon"]])[0])
    return {"res": res, "granularity": granularity, "rows": out}


@app.get("/api/satellites")
def satellites_timeline(granularity: Literal["month", "day"] = "month",
                        bbox: str | None = Q["bbox"], polygon: str | None = Q["polygon"],
                        exclude_low: bool = Q["exclude_low"], vegetation_only: bool = Q["vegetation_only"]):
    """RAW hotspot count per period and satellite (swim lanes; shows when satellites start or drop out)."""
    source = ("cells_" if (bbox or polygon) else "series_") + granularity
    c = cur()
    rel = selection(c, source, bbox, polygon, None, None, exclude_low, vegetation_only)
    out: dict = {}
    for period, sat, n in c.execute(f"SELECT period, satellite, sum(n) FROM {rel} GROUP BY 1, 2 ORDER BY 1").fetchall():
        out.setdefault(sat, {})[period] = int(n)
    return {"granularity": granularity, "satellites": out, "timeline": _meta.get("satellites")}


@app.get("/api/hotspots.csv")
def hotspots_csv(view: Literal["harm", "raw", "modis", "viirs"] = "harm",
                 bbox: str | None = Q["bbox"], polygon: str | None = Q["polygon"],
                 start: str | None = Q["start"], end: str | None = Q["end"],
                 exclude_low: bool = Q["exclude_low"], vegetation_only: bool = Q["vegetation_only"],
                 satellites: str | None = Q["satellites"], daynight: str = Q["daynight"]):
    """Filtered hotspots in the column layout of 'Tabelle A' plus region, weight and frp_h."""
    c = cur()
    extra = raw_cond("points", satellites, daynight)
    if view in ("modis", "viirs"):
        extra += f" AND sensor = '{view.upper()}'"
    rel = selection(c, "points", bbox, polygon, start, end, exclude_low, vegetation_only, extra=extra)
    fd, path = tempfile.mkstemp(suffix=".csv"); os.close(fd)
    c.execute(f"""COPY (SELECT sensor, satellite,
                       strftime(strptime(acq_date || ' ' || lpad(CAST(acq_time AS VARCHAR), 4, '0'), '%Y-%m-%d %H%M'),
                                '%Y-%m-%d %H:%M:%S+00:00') AS datetime_utc,
                       latitude, longitude, brightness_k, frp, pixel_km2, conf_class, daynight, local_hour,
                       type, source, region, weight, frp_h
                       FROM {rel} ORDER BY acq_date, acq_time)
                  TO '{path}' (HEADER, DELIMITER ',')""")
    name = f"firecal_hotspots_{start or _meta.get('first_date')}_{end or _meta.get('last_date')}.csv"
    return FileResponse(path, media_type="text/csv", filename=name, background=BackgroundTask(os.remove, path))


# ---------------------------------------------------------------- data team table (Tabelle B)
TEAM_VALUES = {
    ("harm", "count"): "n_harmonized", ("harm", "frp"): "frp_harmonized",
    ("modis", "count"): "n_modis", ("modis", "frp"): "frp_modis",
    ("viirs", "count"): "n_viirs", ("viirs", "frp"): "frp_viirs",
    ("raw", "count"): "coalesce(n_modis, 0) + coalesce(n_viirs, 0)",
    ("raw", "frp"): "coalesce(frp_modis, 0) + coalesce(frp_viirs, 0)",
}
TEAM_SUMS = ["n_modis", "frp_modis", "n_viirs", "frp_viirs", "n_harmonized", "frp_harmonized",
             "n_modis_calibrated", "frp_modis_calibrated"]


def _grid_cell(grid: str) -> dict:
    """Cell size of a team grid label: '0.5deg' -> {"deg": 0.5}, '2km' -> {"km": 2}."""
    try:
        if grid.endswith("deg"):
            return {"deg": float(grid[:-3])}
        if grid.endswith("km"):
            return {"km": float(grid[:-2])}
    except ValueError:
        pass
    return {}


def _team_view(grid: str) -> str:
    cur()
    if grid not in _team:
        raise HTTPException(404, f"no team table for grid '{grid}'; available: {list(_team)}")
    return _team[grid]["view"]


def _team_area(c, view: str, bbox, polygon, periods: list[str] | None):
    """Rows of a team table whose cell centre lies in the area, as a DataFrame."""
    where = []
    box = _parse_bbox(bbox)
    poly = _parse_polygon(polygon)
    if poly is not None:
        pb = [poly[:, 1].min(), poly[:, 0].min(), poly[:, 1].max(), poly[:, 0].max()]
        box = pb if box is None else [max(box[0], pb[0]), max(box[1], pb[1]), min(box[2], pb[2]), min(box[3], pb[3])]
    if box:
        where.append(f"lon BETWEEN {_lit(box[0])} AND {_lit(box[2])} AND lat BETWEEN {_lit(box[1])} AND {_lit(box[3])}")
    if periods:
        where.append("CAST(period_start AS VARCHAR) IN (" + ",".join(_lit(p) for p in periods) + ")")
    df = c.execute(f"SELECT * FROM {view}" + (" WHERE " + " AND ".join(where) if where else "")).df()
    if poly is not None and len(df):
        df = df[_in_polygon(df["lat"].to_numpy(), df["lon"].to_numpy(), poly)]
    return df


@app.get("/api/server-data")
def server_data():
    """The linked data folder on the server (FIRECAL_SOURCE): files, rows and date range from the Parquet
    footers, and whether its hotspots are loaded into the app (loaded = false: link only)."""
    cur()
    return _meta.get("server_data") or {"folders": [], "loaded": False, "files": 0}


@app.get("/api/team")
def team_info():
    """Tables delivered by the data team that are served as-is: grids, periods (time windows), rows."""
    cur()
    return {"grids": [{"grid": g, "cell": _grid_cell(g), "rows": t["rows"], "file": t["file"], "periods": t["periods"]}
                      for g, t in sorted(_team.items(), key=lambda kv: -(_grid_cell(kv[0]).get("deg", 0) or
                                                                         _grid_cell(kv[0]).get("km", 0) / 111.32))]}


@app.get("/api/team/cells")
def team_cells(grid: str = Query("0.5deg", description="grid label, see /api/team"),
               period: str | None = Query(None, description="period_start of one window; empty = all windows summed"),
               view: Literal["harm", "raw", "modis", "viirs"] = "harm", metric: Literal["count", "frp"] = "count",
               bbox: str | None = Q["bbox"], polygon: str | None = Q["polygon"],
               limit: int = Query(30000, ge=1, le=200000)):
    """Cells of the team's 'Tabelle B' for the map: [[lat, lon, value, value_per_day, region, calibrated], ...].
    Several windows are summed per cell; value_per_day divides by the days of the chosen windows.
    If more than `limit` cells match, the largest are returned and truncated = true."""
    tv = _team_view(grid)
    c = cur()
    periods = [period] if period else None
    df = _team_area(c, tv, bbox, polygon, periods)
    days = sum(p["n_days"] for p in _team[grid]["periods"] if not period or p["start"] == period) or 1
    if df.empty:
        return {"grid": grid, "cell": _grid_cell(grid), "days": days, "count": 0, "truncated": False, "rows": []}
    c.register("tb", df)
    agg = c.execute(f"""SELECT lat, lon, sum({TEAM_VALUES[(view, metric)]}) AS v, any_value(region) AS region,
                               bool_or(is_calibrated) AS cal
                        FROM tb GROUP BY lat, lon HAVING sum({TEAM_VALUES[(view, metric)]}) > 0
                        ORDER BY v DESC""").df()
    total = len(agg)
    agg = agg.head(limit)
    rows = [[round(a, 5), round(b, 5), round(float(v), 3), round(float(v) / days, 4), r, bool(k)]
            for a, b, v, r, k in agg.itertuples(index=False, name=None)]
    return {"grid": grid, "cell": _grid_cell(grid), "days": days, "count": total, "truncated": total > limit,
            "rows": rows}


@app.get("/api/team/check")
def team_check(grid: str = Query("0.5deg", description="grid label, see /api/team"),
               bbox: str | None = Q["bbox"], polygon: str | None = Q["polygon"],
               vegetation_only: bool = Q["vegetation_only"]):
    """Cross-check: sums of the team's 'Tabelle B' vs FireCal's own numbers for the same time windows and area.
    Both without low-confidence detections (the team table excludes them). Team cells count if their centre is
    in the area, FireCal counts the exact hotspots, so small differences at the edge of an area are expected.
    Per window and in total, also per region (whole windows)."""
    tv = _team_view(grid)
    c = cur()
    out = []
    tdf = _team_area(c, tv, bbox, polygon, None)
    tdf = tdf.assign(period_start=tdf["period_start"].astype(str).str[:10]) if len(tdf) else tdf
    for p in _team[grid]["periods"]:
        t = tdf[tdf["period_start"] == p["start"]] if len(tdf) else tdf
        team = {k: round(float(t[k].sum()), 2) if len(t) else 0.0 for k in TEAM_SUMS}
        rel = selection(c, "points", bbox, polygon, p["start"], p["end"], True, vegetation_only)
        r = c.execute(f"""SELECT
              count(*) FILTER (WHERE sensor='MODIS'), coalesce(sum(frp) FILTER (WHERE sensor='MODIS'), 0),
              count(*) FILTER (WHERE sensor='VIIRS'), coalesce(sum(frp) FILTER (WHERE sensor='VIIRS'), 0),
              coalesce(sum(weight), 0), coalesce(sum(frp_h), 0), coalesce(sum(weight_cal), 0), coalesce(sum(frp_cal), 0)
            FROM {rel}""").fetchone()
        ours = {k: round(float(v), 2) for k, v in zip(TEAM_SUMS, r)}
        out.append({**p, "team": team, "firecal": ours})
    regions = []
    if len(tdf):
        tr = tdf.groupby("region")[TEAM_SUMS].sum()
        first, last = _team[grid]["periods"][0]["start"], _team[grid]["periods"][-1]["end"]
        rel = selection(c, "points", bbox, polygon, first, last, True, vegetation_only)
        fr = c.execute(f"""SELECT region, count(*) FILTER (WHERE sensor='MODIS') AS n_modis,
              count(*) FILTER (WHERE sensor='VIIRS') AS n_viirs, coalesce(sum(frp_h), 0) AS frp_harmonized,
              coalesce(sum(weight_cal), 0) AS n_modis_calibrated FROM {rel} GROUP BY 1""").df().set_index("region")
        for reg in sorted(set(tr.index) | set(fr.index)):
            regions.append({"region": reg,
                            "team": {k: round(float(tr.loc[reg, k]), 2) if reg in tr.index else 0.0
                                     for k in ["n_modis", "n_viirs", "frp_harmonized", "n_modis_calibrated"]},
                            "firecal": {k: round(float(fr.loc[reg, k]), 2) if reg in fr.index else 0.0
                                        for k in ["n_modis", "n_viirs", "frp_harmonized", "n_modis_calibrated"]}})
    return {"grid": grid, "windows": out, "regions": regions,
            "note": "low-confidence detections excluded on both sides; team cells by centre, FireCal by exact hotspot"}


@app.post("/api/reload")
def reload(token: str = ""):
    """Rebuild (if inputs changed) and reload. Needs the environment variable FIRECAL_RELOAD_TOKEN."""
    expected = os.environ.get("FIRECAL_RELOAD_TOKEN")
    if not expected or token != expected:
        raise HTTPException(403, "reload disabled or wrong token")
    load()
    return {"ok": True, "rows": _meta.get("rows"), "rebuilding": dict(_build_state)}


# ---------------------------------------------------------------- frontend
@app.get("/", include_in_schema=False)
def index():
    return FileResponse(os.path.join(ROOT, "index.html"))


app.mount("/content", StaticFiles(directory=os.path.join(ROOT, "content")), name="content")
