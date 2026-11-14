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
import math
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
from .paths import CELLS_DAY, CELLS_MONTH, DAY_RES, HOTSPOTS_DIR, MONTH_RES, ROOT

API_VERSION = "2.0"
app = FastAPI(title="FireCal API", version=API_VERSION,
              description="Harmonized MODIS + VIIRS active fire hotspots (NASA FIRMS), VIIRS level")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["GET"], allow_headers=["*"])
app.add_middleware(GZipMiddleware, minimum_size=2000)

_db: duckdb.DuckDBPyConnection | None = None
_meta: dict = {}
_lock = threading.Lock()
ALL_SATS = ["Terra", "Aqua", "SNPP", "N20", "N21"]


def load() -> None:
    """(Re)build if needed and register the Parquet files as views."""
    global _db, _meta
    with _lock:
        meta = build()
        con = duckdb.connect(":memory:")
        con.execute(f"""CREATE VIEW points AS SELECT * FROM read_parquet('{HOTSPOTS_DIR}/**/*.parquet',
                        hive_partitioning = true)""")
        con.execute(f"CREATE VIEW cells_month AS SELECT * FROM read_parquet('{CELLS_MONTH}')")
        con.execute(f"CREATE VIEW cells_day AS SELECT * FROM read_parquet('{CELLS_DAY}')")
        old, _db, _meta = _db, con, meta
        if old is not None:
            old.close()


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
    if source == "cells_month":
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
)


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
    return {"api": "firecal", "version": API_VERSION, **_meta}


@app.get("/api/timeseries")
def timeseries(granularity: Literal["month", "day"] = "month",
               bbox: str | None = Q["bbox"], polygon: str | None = Q["polygon"],
               exclude_low: bool = Q["exclude_low"], vegetation_only: bool = Q["vegetation_only"],
               satellites: str | None = Q["satellites"], daynight: str = Q["daynight"]):
    """Per period over the WHOLE data span (start/end are applied in the app, medians need history).

    raw (respects satellites/daynight): modis_n, viirs_n, modis_frp, viirs_frp, raw_n, raw_frp, n_night, n_high
    harmonized (VIIRS level, all satellites): harm_n, harm_n_lo, harm_n_hi, harm_frp, harm_frp_lo, harm_frp_hi,
    modis_h_n / viirs_h_n (share of harm_n from calibrated MODIS / native VIIRS), modis_cal_n, modis_cal_frp
    (MODIS x factor for every period, also after the VIIRS start)."""
    c = cur()
    source = "cells_month" if granularity == "month" else "cells_day"
    rel = selection(c, source, bbox, polygon, None, None, exclude_low, vegetation_only)
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
            satellites: str | None = Q["satellites"], daynight: str = Q["daynight"]):
    """Grid cells [[lat, lon, value], ...] for the map. Pass the visible map area as bbox."""
    if res >= MONTH_RES - 1e-9 and _whole_months(start, end):
        source = "cells_month"
    elif res >= DAY_RES - 1e-9:
        source = "cells_day"
    else:
        source = "points"
    s = SOURCES[source]
    c = cur()
    rel = selection(c, source, bbox, polygon, start, end, exclude_low, vegetation_only)
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
           satellites: str | None = Q["satellites"], daynight: str = Q["daynight"],
           limit: int = Query(20000, ge=1, le=100000)):
    """Single RAW hotspots for the map (never harmonized). If more than `limit` match, the strongest
    `limit` by FRP are returned and truncated = true.
    columns: lat, lon, sensor, satellite, frp, conf_class, acq_date, acq_time, daynight, pixel_km2, local_hour"""
    c = cur()
    rel = selection(c, "points", bbox, polygon, start, end, exclude_low, vegetation_only,
                    extra=raw_cond("points", satellites, daynight))
    total = c.execute(f"SELECT count(*) FROM {rel}").fetchone()[0]
    df = c.execute(f"""SELECT latitude, longitude, sensor, satellite, frp, conf_class, acq_date, acq_time,
                              daynight, pixel_km2, local_hour FROM {rel} ORDER BY frp DESC LIMIT {limit}""").df()
    return {"count": int(total), "truncated": bool(total > limit),
            "columns": ["lat", "lon", "sensor", "satellite", "frp", "conf_class", "acq_date", "acq_time",
                        "daynight", "pixel_km2", "local_hour"], "rows": _rows(df)}


FRP_EDGES = [0, 1, 2, 3, 5, 7, 10, 15, 20, 30, 50, 70, 100, 150, 200, 300, 500, 1000, 1e9]


@app.get("/api/distributions")
def distributions(bbox: str | None = Q["bbox"], polygon: str | None = Q["polygon"],
                  start: str | None = Q["start"], end: str | None = Q["end"],
                  exclude_low: bool = Q["exclude_low"], vegetation_only: bool = Q["vegetation_only"],
                  satellites: str | None = Q["satellites"], daynight: str = Q["daynight"]):
    """Distributions of the RAW hotspots in area and period, split by sensor:
    frp (MW, bins frp_edges), local_hour (24 bins), conf_class, satellite, daynight.
    Low-confidence detections are included in conf_class even if exclude_low is set."""
    c = cur()
    rc = raw_cond("points", satellites, daynight)
    rel = selection(c, "points", bbox, polygon, start, end, False, vegetation_only, extra=rc)
    lowf = "conf_class <> 'low'" if exclude_low else "TRUE"
    case = " ".join(f"WHEN frp < {FRP_EDGES[i + 1]} THEN {i}" for i in range(len(FRP_EDGES) - 1))

    def grid(sql, n):
        out = {"MODIS": [0] * n, "VIIRS": [0] * n}
        for sensor, b, cnt in c.execute(sql).fetchall():
            if b is not None and 0 <= int(b) < n:
                out[sensor][int(b)] = int(cnt)
        return out

    frp = grid(f"SELECT sensor, CASE {case} END, count(*) FROM {rel} WHERE {lowf} GROUP BY 1, 2", len(FRP_EDGES) - 1)
    hour = grid(f"SELECT sensor, floor(local_hour)::INT % 24, count(*) FROM {rel} WHERE {lowf} AND local_hour IS NOT NULL GROUP BY 1, 2", 24)
    conf = {f"{a}|{b}": int(n) for a, b, n in c.execute(f"SELECT sensor, conf_class, count(*) FROM {rel} GROUP BY 1, 2").fetchall()}
    sat = {a: int(n) for a, n in c.execute(f"SELECT satellite, count(*) FROM {rel} WHERE {lowf} GROUP BY 1").fetchall()}
    dn = {f"{a}|{b}": int(n) for a, b, n in c.execute(f"SELECT sensor, daynight, count(*) FROM {rel} WHERE {lowf} GROUP BY 1, 2").fetchall()}
    stats = c.execute(f"""SELECT sensor, count(*), median(frp), quantile_cont(frp, 0.9), max(frp), avg(pixel_km2)
                          FROM {rel} WHERE {lowf} GROUP BY 1""").fetchall()
    return {"frp_edges": FRP_EDGES[:-1] + [None], "frp": frp, "local_hour": hour, "conf_class": conf,
            "satellite": sat, "daynight": dn,
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
    source = "cells_month" if granularity == "month" else "cells_day"
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


@app.post("/api/reload")
def reload(token: str = ""):
    """Rebuild (if inputs changed) and reload. Needs the environment variable FIRECAL_RELOAD_TOKEN."""
    expected = os.environ.get("FIRECAL_RELOAD_TOKEN")
    if not expected or token != expected:
        raise HTTPException(403, "reload disabled or wrong token")
    load()
    return {"ok": True, "rows": _meta.get("rows")}


# ---------------------------------------------------------------- frontend
@app.get("/", include_in_schema=False)
def index():
    return FileResponse(os.path.join(ROOT, "index.html"))


app.mount("/content", StaticFiles(directory=os.path.join(ROOT, "content")), name="content")
