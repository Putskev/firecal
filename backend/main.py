"""
FireCal API (FastAPI + DuckDB).

    uvicorn backend.main:app --reload          # http://localhost:8000  (app)  /docs (API docs)

All analysis endpoints share the same filters:
    bbox=west,south,east,north           rectangle (degrees)
    polygon=lat,lon;lat,lon;...          polygon (at least 3 vertices), combined with bbox if both given
    start=YYYY-MM-DD, end=YYYY-MM-DD     inclusive date range (timeseries ignores it on purpose,
                                         because medians need the full history)
    exclude_low=true|false               drop low-confidence detections (default true)
    vegetation_only=true|false           keep type = 0 only (rows without type are kept)
"""
from __future__ import annotations

import json
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
from .paths import HOTSPOTS, ROOT

API_VERSION = "1.0"
app = FastAPI(title="FireCal API", version=API_VERSION,
              description="Harmonized MODIS + VIIRS active fire hotspots (NASA FIRMS)")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["GET"], allow_headers=["*"])
app.add_middleware(GZipMiddleware, minimum_size=2000)

_db: duckdb.DuckDBPyConnection | None = None
_meta: dict = {}
_lock = threading.Lock()


def load() -> None:
    """(Re)build if needed and load the processed table into an in-memory DuckDB."""
    global _db, _meta
    with _lock:
        meta = build()
        con = duckdb.connect(":memory:")
        con.execute("CREATE TABLE h AS SELECT *, substr(acq_date, 1, 7) AS month FROM read_parquet(?)", [HOTSPOTS])
        con.execute("CREATE INDEX IF NOT EXISTS h_date ON h(acq_date)")
        _db, _meta = con, meta


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


def selection(c, bbox, polygon, start, end, exclude_low, vegetation_only, use_dates=True) -> str:
    """Returns the name of a relation (view) on cursor `c` holding the filtered hotspots."""
    where, params = [], []
    box = _parse_bbox(bbox)
    poly = _parse_polygon(polygon)
    if poly is not None:
        pb = [poly[:, 1].min(), poly[:, 0].min(), poly[:, 1].max(), poly[:, 0].max()]
        box = pb if box is None else [max(box[0], pb[0]), max(box[1], pb[1]), min(box[2], pb[2]), min(box[3], pb[3])]
    if box:
        where.append("longitude BETWEEN ? AND ? AND latitude BETWEEN ? AND ?")
        params += [box[0], box[2], box[1], box[3]]
    if use_dates and start:
        where.append("acq_date >= ?"); params.append(start)
    if use_dates and end:
        where.append("acq_date <= ?"); params.append(end)
    if exclude_low:
        where.append("conf_class <> 'low'")
    if vegetation_only:
        where.append("(type IS NULL OR type = 0)")
    sql = "SELECT * FROM h" + (" WHERE " + " AND ".join(where) if where else "")
    name = "sel_" + uuid.uuid4().hex[:8]
    if poly is None:
        c.execute(f"CREATE TEMP VIEW {name} AS {sql}".replace("?", "{}").format(*[_lit(p) for p in params]))
    else:
        df = c.execute(sql, params).df()
        df = df[_in_polygon(df.latitude.to_numpy(), df.longitude.to_numpy(), poly)]
        c.register(name, df)
    return name


def _lit(v) -> str:
    if isinstance(v, (int, float, np.floating)):
        return repr(float(v))
    return "'" + str(v).replace("'", "''") + "'"


Common = dict(
    bbox=Query(None, description="west,south,east,north"),
    polygon=Query(None, description="lat,lon;lat,lon;..."),
    start=Query(None, pattern=r"^\d{4}-\d{2}-\d{2}$"),
    end=Query(None, pattern=r"^\d{4}-\d{2}-\d{2}$"),
    exclude_low=Query(True), vegetation_only=Query(False),
)


# ---------------------------------------------------------------- endpoints
@app.get("/api/health")
def health():
    return {"ok": _db is not None}


@app.get("/api/meta")
def meta():
    """Dataset span, counts per satellite, bounding box and the active harmonization method."""
    cur()
    return {"api": "firecal", "version": API_VERSION, **_meta}


@app.get("/api/timeseries")
def timeseries(granularity: Literal["month", "day"] = "month",
               bbox: str | None = Common["bbox"], polygon: str | None = Common["polygon"],
               exclude_low: bool = Common["exclude_low"], vegetation_only: bool = Common["vegetation_only"]):
    """Raw and harmonized totals per period over the WHOLE data span (for calendar, anomalies, comparison).
    Fields: modis_n, viirs_n, modis_frp, viirs_frp (raw) and modis_h_n, viirs_h_n, modis_h_frp,
    viirs_h_frp, harm_n, harm_frp (harmonized)."""
    c = cur()
    rel = selection(c, bbox, polygon, None, None, exclude_low, vegetation_only, use_dates=False)
    period = "month" if granularity == "month" else "acq_date"
    df = c.execute(f"""
        SELECT {period} AS period,
          count(*) FILTER (WHERE sensor='MODIS')                  AS modis_n,
          count(*) FILTER (WHERE sensor='VIIRS')                  AS viirs_n,
          coalesce(sum(frp)    FILTER (WHERE sensor='MODIS'), 0)  AS modis_frp,
          coalesce(sum(frp)    FILTER (WHERE sensor='VIIRS'), 0)  AS viirs_frp,
          coalesce(sum(weight) FILTER (WHERE sensor='MODIS'), 0)  AS modis_h_n,
          coalesce(sum(weight) FILTER (WHERE sensor='VIIRS'), 0)  AS viirs_h_n,
          coalesce(sum(frp_h)  FILTER (WHERE sensor='MODIS'), 0)  AS modis_h_frp,
          coalesce(sum(frp_h)  FILTER (WHERE sensor='VIIRS'), 0)  AS viirs_h_frp,
          coalesce(sum(weight), 0) AS harm_n,
          coalesce(sum(frp_h), 0)  AS harm_frp
        FROM {rel} GROUP BY 1 ORDER BY 1""").df()
    rows = {r.pop("period"): {k: round(float(v), 3) for k, v in r.items()} for r in df.to_dict("records")}
    return {"granularity": granularity, "first_date": _meta.get("first_date"), "last_date": _meta.get("last_date"),
            "rows": rows, "harmonization": _meta.get("harmonization")}


VALUE = {
    ("harm", "count"): "weight", ("harm", "frp"): "frp_h",
    ("modis", "count"): "CAST(sensor='MODIS' AS DOUBLE)", ("modis", "frp"): "frp * CAST(sensor='MODIS' AS DOUBLE)",
    ("viirs", "count"): "CAST(sensor='VIIRS' AS DOUBLE)", ("viirs", "frp"): "frp * CAST(sensor='VIIRS' AS DOUBLE)",
}


@app.get("/api/heatmap")
def heatmap(view: Literal["harm", "modis", "viirs"] = "harm", metric: Literal["count", "frp"] = "count",
            res: float = Query(0.25, ge=0.01, le=5, description="grid cell size in degrees"),
            bbox: str | None = Common["bbox"], polygon: str | None = Common["polygon"],
            start: str | None = Common["start"], end: str | None = Common["end"],
            exclude_low: bool = Common["exclude_low"], vegetation_only: bool = Common["vegetation_only"]):
    """Hotspots aggregated to grid cells: cells = [[lat, lon, value], ...] (cell centres)."""
    c = cur()
    rel = selection(c, bbox, polygon, start, end, exclude_low, vegetation_only)
    val = VALUE[(view, metric)]
    rows = c.execute(f"""
        SELECT floor(latitude / {res}) * {res} + {res / 2} AS lat,
               floor(longitude / {res}) * {res} + {res / 2} AS lon,
               sum({val}) AS v
        FROM {rel} GROUP BY 1, 2 HAVING sum({val}) > 0""").fetchall()
    return {"res": res, "cells": [[round(a, 4), round(b, 4), round(v, 4)] for a, b, v in rows]}


@app.get("/api/hotspots.csv")
def hotspots_csv(view: Literal["harm", "modis", "viirs"] = "harm",
                 bbox: str | None = Common["bbox"], polygon: str | None = Common["polygon"],
                 start: str | None = Common["start"], end: str | None = Common["end"],
                 exclude_low: bool = Common["exclude_low"], vegetation_only: bool = Common["vegetation_only"]):
    """Download the filtered hotspots incl. harmonized weight and frp_h."""
    c = cur()
    rel = selection(c, bbox, polygon, start, end, exclude_low, vegetation_only)
    sensor = {"modis": " WHERE sensor='MODIS'", "viirs": " WHERE sensor='VIIRS'"}.get(view, "")
    fd, path = tempfile.mkstemp(suffix=".csv"); os.close(fd)
    c.execute(f"""COPY (SELECT latitude, longitude, acq_date, lpad(CAST(acq_time AS VARCHAR), 4, '0') AS acq_time, satellite, sensor, confidence, conf_class,
                       frp, daynight, type, source, weight, frp_h FROM {rel}{sensor} ORDER BY acq_date, acq_time)
                  TO '{path}' (HEADER, DELIMITER ',')""")
    name = f"firecal_hotspots_{start or _meta.get('first_date')}_{end or _meta.get('last_date')}.csv"
    return FileResponse(path, media_type="text/csv", filename=name, background=BackgroundTask(os.remove, path))


@app.post("/api/reload")
def reload(token: str = ""):
    """Rebuild (if inputs changed) and reload the data without restarting the server.
    Protected by the environment variable FIRECAL_RELOAD_TOKEN (disabled if unset)."""
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
