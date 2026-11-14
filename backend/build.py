"""
Build data/processed/, the data the API serves.

    python -m backend.build            # rebuild if inputs changed
    python -m backend.build --force    # always rebuild

Everything runs in DuckDB, month by month, with a memory limit, so the input can be much larger than the
RAM (tested design target: 300+ million hotspots on a server with 8 GB). Inputs, in this order:
  1. data/harmonized/*.parquet|*.csv  finished harmonized table (canonical columns + weight columns), used as-is
  2. data/raw/*.parquet + the linked server folders (FIRECAL_SOURCE, if FIRECAL_SOURCE_LOAD is on):
     canonical Parquet, raw FIRMS (CSV/Parquet) or the team's 'Tabelle A', in any mix

Steps
  1 stage      every input file -> canonical columns (SQL), written to processed/_stage/y=/m=/  (one pass)
  2 dedupe     per month: duplicates removed (same pixel, time, satellite; SP archive wins over NRT),
               the incomplete newest NRT day dropped -> processed/_dedup/y=/m=/; calibration sums collected
  3 calibrate  factor table: the team's table (data/calibration/ or the server folder) or computed from step 2
  4 harmonize  per month: weights (harmonization.weights_sql) -> processed/hotspots/y=/m=/data.parquet
               + sums per cell: processed/cells_month/YYYY-MM.parquet (0.1 deg), cells_day/YYYY-MM.parquet (0.25 deg)
The temporary folders are deleted month by month, so the extra disk space stays around one copy of the data.

Aggregates hold n (raw count), frp (raw FRP), n_high, w / w_lo / w_hi (harmonized count with band),
fh / fh_lo / fh_hi (harmonized FRP), wc / fc (MODIS x factor), split by sensor, satellite, `low`
(conf_class = low), `veg` (type 0 or unknown) and `night`, so the app's filters still work on them.
"""
from __future__ import annotations

import glob
import gzip
import json
import os
import shutil
import sys
import time
from datetime import datetime, timezone

import duckdb
import pyarrow.parquet as pq

from . import harmonization, sources
from .data import SAT_NAMES
from .paths import (CELLS_DAY, CELLS_MONTH, DAY_RES, DIST_MONTH, DIST_RES, FRP_EDGES, HARM_INFO, HARMONIZED_DIR,
                    HOTSPOTS_DIR, META, MONTH_RES, PROCESSED, RAW, ROOT, SERIES_DAY, SERIES_MONTH)
from .regions import SATELLITES, VIIRS_START

WORK = PROCESSED + ".new"   # a build is written here and swapped in at the end: the app keeps serving meanwhile
CANON = ["latitude", "longitude", "acq_date", "acq_time", "satellite", "sensor", "confidence", "conf_class",
         "frp", "brightness_k", "pixel_km2", "local_hour", "daynight", "type", "version", "source"]
KEY = ["latitude", "longitude", "acq_date", "acq_time", "satellite"]
EXTRA = ["region", "weight", "frp_h", "weight_lo", "weight_hi", "frp_h_lo", "frp_h_hi", "weight_cal", "frp_cal"]


# ------------------------------------------------------------------ inputs and rebuild check
def _inputs(linked_ok: bool = True) -> tuple[str, list[str]]:
    h = sorted(glob.glob(os.path.join(HARMONIZED_DIR, "*.parquet")) + glob.glob(os.path.join(HARMONIZED_DIR, "*.csv")))
    if h:
        return "harmonized", h
    linked = sources.hotspot_files() if (linked_ok and sources.load_hotspots()) else []
    return "raw", sorted(glob.glob(os.path.join(RAW, "*.parquet"))) + linked


def ready() -> bool:
    """A complete build of the current layout exists (the app can serve it)."""
    return os.path.exists(META) and all(os.path.isdir(d) for d in (HOTSPOTS_DIR, CELLS_MONTH, CELLS_DAY, DIST_MONTH,
                                                                    SERIES_MONTH, SERIES_DAY))


def needs_build() -> bool:
    if not ready():
        return True
    _, files = _inputs()
    from . import regions
    deps = (files + [harmonization.__file__, regions.__file__, __file__]
            + glob.glob(os.path.join(HARMONIZED_DIR, "*.json")) + harmonization.calibration_files())
    built = os.path.getmtime(META)
    if any(os.path.getmtime(f) > built for f in deps if os.path.exists(f)):
        return True
    try:   # input files added, removed or still being written (e.g. git pull while the server reloads)
        with open(META) as f:
            return json.load(f).get("inputs") != _signature(files)
    except (OSError, ValueError):
        return True


def _signature(files: list[str]) -> list[list]:
    files = files + harmonization.calibration_files()
    return [[os.path.relpath(f, ROOT) if f.startswith(ROOT) else f, os.path.getsize(f)] for f in files if os.path.exists(f)]


# ------------------------------------------------------------------ SQL normalization of one input file
def _lit(s: str) -> str:
    return "'" + str(s).replace("'", "''") + "'"


def _columns(path: str) -> dict[str, str]:
    """lower-case name -> real name."""
    if path.endswith(".parquet"):
        names = pq.read_schema(path).names
    else:
        opener = gzip.open if path.endswith(".gz") else open
        with opener(path, "rt", encoding="utf-8", errors="ignore") as f:
            names = [c.strip().strip('"') for c in f.readline().split(",")]
    return {n.lower(): n for n in names}


def _reader(path: str) -> str:
    if path.endswith(".parquet"):
        return f"read_parquet({_lit(path)})"
    return f"read_csv({_lit(path)}, header = true, all_varchar = true)"


def normalize_sql(path: str, extra: bool = False) -> str:
    """SELECT that turns one file (canonical, raw FIRMS or Tabelle A) into the canonical columns."""
    c = _columns(path)
    has = lambda *n: all(x in c for x in n)
    q = lambda n: '"' + c[n].replace('"', '""') + '"'
    v = lambda n: f"CAST({q(n)} AS VARCHAR)"
    num = lambda n, t="DOUBLE": f"TRY_CAST({q(n)} AS {t})"

    if has("datetime_utc"):   # 'YYYY-MM-DD HH:MM:SS+00:00' (UTC)
        dt = v("datetime_utc")
        acq_date = f"substr({dt}, 1, 10)"
        acq_time = f"TRY_CAST(substr({dt}, 12, 2) AS INTEGER) * 100 + TRY_CAST(substr({dt}, 15, 2) AS INTEGER)"
    else:
        acq_date = f"strftime(TRY_CAST({q('acq_date')} AS DATE), '%Y-%m-%d')"
        acq_time = f"coalesce({num('acq_time', 'INTEGER')}, 0)" if has("acq_time") else "0"
    sat_raw = f"trim({v('satellite')})" if has("satellite") else "''"
    sat_case = "CASE upper(s) " + " ".join(f"WHEN {_lit(k)} THEN {_lit(x)}" for k, x in SAT_NAMES.items()) + " ELSE s END"
    if has("sensor"):
        sensor = f"CASE WHEN upper({v('sensor')}) LIKE '%VIIRS%' THEN 'VIIRS' ELSE 'MODIS' END"
    elif has("instrument"):
        sensor = f"CASE WHEN upper({v('instrument')}) LIKE '%VIIRS%' THEN 'VIIRS' ELSE 'MODIS' END"
    else:
        sensor = "'VIIRS'" if has("bright_ti4") else "'MODIS'"
    bright = next((num(n) for n in ("brightness_k", "bright_ti4", "brightness") if has(n)), "NULL::DOUBLE")
    if has("pixel_km2"):
        pixel = num("pixel_km2")
    else:
        scan = num("scan_km") if has("scan_km") else num("scan") if has("scan") else "NULL::DOUBLE"
        track = num("track_km") if has("track_km") else num("track") if has("track") else "NULL::DOUBLE"
        pixel = f"{scan} * {track}"
    inner = f"""
      SELECT round({num('latitude')}, 5) AS latitude, round({num('longitude')}, 5) AS longitude,
             {acq_date} AS acq_date, ({acq_time})::INTEGER AS acq_time,
             {sat_raw} AS s, {sensor} AS sensor,
             {"coalesce(" + v('confidence') + ", '')" if has('confidence') else "''"} AS confidence,
             {"lower(" + v('conf_class') + ")" if has('conf_class') else "NULL::VARCHAR"} AS conf_in,
             coalesce({num('frp') if has('frp') else 'NULL::DOUBLE'}, 0) AS frp,
             {bright} AS brightness_k, {pixel} AS pixel_km2,
             {num('local_hour') if has('local_hour') else 'NULL::DOUBLE'} AS local_in,
             {"upper(substr(" + v('daynight') + ", 1, 1))" if has('daynight') else "''"} AS daynight,
             {num('type', 'TINYINT') if has('type') else 'NULL::TINYINT'} AS type,
             {"coalesce(" + v('version') + ", '')" if has('version') else "''"} AS version,
             {v('source') if has('source') else 'NULL::VARCHAR'} AS source_in
             {''.join(f", {num(x) if x != 'region' else v(x)} AS {x}" for x in EXTRA if extra and has(x))}
      FROM {_reader(path)}"""
    conf_num = "TRY_CAST(confidence AS DOUBLE)"
    conf_derived = (f"CASE WHEN sensor = 'MODIS' THEN CASE WHEN {conf_num} < 30 THEN 'low' WHEN {conf_num} < 80 THEN 'nominal' "
                    f"WHEN {conf_num} >= 80 THEN 'high' END ELSE CASE lower(substr(trim(confidence), 1, 1)) "
                    f"WHEN 'l' THEN 'low' WHEN 'n' THEN 'nominal' WHEN 'h' THEN 'high' END END")
    extra_cols = "".join(f", {x}" for x in EXTRA if extra and has(x))
    return f"""
      SELECT latitude, longitude, acq_date, acq_time, {sat_case} AS satellite, sensor, confidence,
             coalesce(CASE WHEN conf_in IN ('low', 'nominal', 'high') THEN conf_in END, {conf_derived}, 'nominal') AS conf_class,
             frp::FLOAT AS frp, brightness_k::FLOAT AS brightness_k, pixel_km2::FLOAT AS pixel_km2,
             round(coalesce(local_in, (((acq_time // 100) + (acq_time % 100) / 60.0 + longitude / 15) % 24 + 24) % 24), 2)::FLOAT AS local_hour,
             daynight, type, version,
             CASE WHEN source_in IN ('NRT', 'SP') THEN source_in
                  WHEN version = '' OR upper(version) LIKE '%NRT%' THEN 'NRT' ELSE 'SP' END AS source
             {extra_cols}
      FROM ({inner}) WHERE latitude IS NOT NULL AND longitude IS NOT NULL AND acq_date IS NOT NULL"""


# ------------------------------------------------------------------ cells
def _cells_sql(src: str, period: str, res: float) -> str:
    return f"""
        SELECT {period} AS period,
               floor(latitude / {res}) * {res} + {res / 2}  AS lat,
               floor(longitude / {res}) * {res} + {res / 2} AS lon,
               sensor, satellite, conf_class = 'low' AS low, (type IS NULL OR type = 0) AS veg,
               daynight = 'N' AS night,
               count(*)::INTEGER AS n, sum(frp)::DOUBLE AS frp,
               count(*) FILTER (WHERE conf_class = 'high')::INTEGER AS n_high,
               sum(weight)::DOUBLE AS w, sum(weight_lo)::DOUBLE AS w_lo, sum(weight_hi)::DOUBLE AS w_hi,
               sum(frp_h)::DOUBLE AS fh, sum(frp_h_lo)::DOUBLE AS fh_lo, sum(frp_h_hi)::DOUBLE AS fh_hi,
               sum(weight_cal)::DOUBLE AS wc, sum(frp_cal)::DOUBLE AS fc
        FROM {src} GROUP BY ALL ORDER BY lat, lon"""   # sorted: row-group statistics let area queries skip data


def _series_sql(cells: str) -> str:
    """The cells summed over space: global series per period, sensor, satellite and filter flags."""
    return f"""SELECT period, sensor, satellite, low, veg, night, sum(n)::BIGINT AS n, sum(frp) AS frp, sum(n_high)::BIGINT AS n_high,
                      sum(w) AS w, sum(w_lo) AS w_lo, sum(w_hi) AS w_hi, sum(fh) AS fh, sum(fh_lo) AS fh_lo, sum(fh_hi) AS fh_hi,
                      sum(wc) AS wc, sum(fc) AS fc
               FROM read_parquet('{cells}') GROUP BY ALL ORDER BY period"""


def _dist_sql(src: str) -> str:
    """Histogram cells for the distribution charts over long periods: month x 1 deg x FRP class x local hour."""
    fbin = "CASE " + " ".join(f"WHEN frp < {FRP_EDGES[i + 1]} THEN {i}" for i in range(len(FRP_EDGES) - 1)) + " END"
    return f"""
        SELECT substr(acq_date, 1, 7) AS period,
               floor(latitude / {DIST_RES}) * {DIST_RES} + {DIST_RES / 2} AS lat,
               floor(longitude / {DIST_RES}) * {DIST_RES} + {DIST_RES / 2} AS lon,
               sensor, satellite, conf_class, (type IS NULL OR type = 0) AS veg, daynight,
               ({fbin})::TINYINT AS fbin, (floor(local_hour)::INTEGER % 24)::TINYINT AS hour,
               count(*)::INTEGER AS n, sum(frp)::DOUBLE AS frp, max(frp)::FLOAT AS frp_max, sum(pixel_km2)::DOUBLE AS px
        FROM {src} GROUP BY ALL ORDER BY lat, lon"""


def _connect(tmp: str) -> duckdb.DuckDBPyConnection:
    con = duckdb.connect()
    mem = os.environ.get("FIRECAL_DUCKDB_MEMORY")
    if not mem:
        try:
            total = os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") / 2**30
            mem = f"{max(1.0, min(total * 0.5, 8.0)):.1f}GB"
        except (ValueError, OSError, AttributeError):
            mem = "2GB"
    os.makedirs(tmp, exist_ok=True)
    con.execute(f"SET memory_limit = {_lit(mem)}")
    con.execute(f"SET temp_directory = {_lit(tmp)}")
    con.execute("SET preserve_insertion_order = false")
    # few threads and few open partition files keep the memory flat (each thread / open file holds buffers)
    con.execute(f"SET threads = {int(os.environ.get('FIRECAL_DUCKDB_THREADS', '2'))}")
    con.execute("SET partitioned_write_max_open_files = 24")
    con.execute("SET partitioned_write_flush_threshold = 131072")
    _log(f"DuckDB memory limit {mem}, threads {os.environ.get('FIRECAL_DUCKDB_THREADS', '2')}")
    return con


def _months(folder: str) -> list[tuple[int, int]]:
    out = []
    for d in glob.glob(os.path.join(folder, "y=*", "m=*")):
        y = int(os.path.basename(os.path.dirname(d))[2:]); m = int(os.path.basename(d)[2:])
        out.append((y, m))
    return sorted(out)


def _part(folder: str, y: int, m: int) -> str:
    return os.path.join(folder, f"y={y}", f"m={m}")


def _stage_glob(stage: str, y: int, m: int) -> str:
    return os.path.join(stage, "f*", f"y={y}", f"m={m}", "*.parquet")


PROGRESS: dict = {}   # read by the API (/api/meta -> rebuilding.progress) while a build runs


def _log(msg: str) -> None:
    PROGRESS.update(message=msg, time=datetime.now(timezone.utc).strftime("%H:%M:%S"))
    print(f"[build] {msg}", flush=True)


# ------------------------------------------------------------------ build
def build(force: bool = False, linked: bool = True) -> dict:
    """linked=False: only the repo data (a quick first build while the server folder is built later)."""
    if not force and linked and not needs_build():
        with open(META) as f:
            return json.load(f)
    mode, files = _inputs(linked)
    if not files:
        raise FileNotFoundError("No data: put FIRMS files into data/incoming/ and run `python -m backend.ingest`, "
                                "link a folder with FIRECAL_SOURCE, or put a harmonized table into data/harmonized/")
    t0 = time.time()
    shutil.rmtree(WORK, ignore_errors=True)
    w = lambda p: os.path.join(WORK, os.path.relpath(p, PROCESSED))
    STAGE, DEDUP, TMP = (os.path.join(WORK, x) for x in ("_stage", "_dedup", "_tmp"))
    HD, CM, CD, DM, SM, SD = (w(p) for p in (HOTSPOTS_DIR, CELLS_MONTH, CELLS_DAY, DIST_MONTH, SERIES_MONTH, SERIES_DAY))
    os.makedirs(WORK, exist_ok=True)
    con = _connect(TMP)
    extra = mode == "harmonized"

    # 1) stage: all inputs -> canonical columns, partitioned by year / month (one pass over the inputs)
    os.makedirs(STAGE, exist_ok=True)
    for i, f in enumerate(files):   # one input file at a time -> _stage/f<i>/y=/m=/
        _log(f"stage: file {i + 1}/{len(files)} {os.path.basename(f)} ({time.time() - t0:.0f} s)")
        con.execute(f"""COPY (SELECT *, CAST(substr(acq_date, 1, 4) AS INTEGER) AS y, CAST(substr(acq_date, 6, 2) AS INTEGER) AS m
                              FROM ({normalize_sql(f, extra)}))
                        TO {_lit(os.path.join(STAGE, f"f{i:04d}"))} (FORMAT parquet, PARTITION_BY (y, m), COMPRESSION zstd)""")
    months = sorted({ym for d in glob.glob(os.path.join(STAGE, "f*")) for ym in _months(d)})
    if not months:
        raise ValueError("the input files contain no valid hotspots")

    # newest day: drop it if it only has NRT rows (incomplete download)
    ly, lm = months[-1]
    lastsrc = f"read_parquet({_lit(_stage_glob(STAGE, ly, lm))})"
    last, only_nrt = con.execute(f"""SELECT max(acq_date), bool_and(source = 'NRT') FILTER (WHERE acq_date =
        (SELECT max(acq_date) FROM {lastsrc})) FROM {lastsrc}""").fetchone()
    dropped = last if (only_nrt and not extra) else None

    # 2) dedupe per month (+ collect calibration sums)
    cal = None if extra else harmonization.load_calibration()
    calib_parts = []
    first_viirs = con.execute(f"""SELECT min(acq_date) FROM read_parquet({_lit(STAGE + '/*/*/*/*.parquet')}, hive_partitioning = true)
                                  WHERE sensor = 'VIIRS' AND acq_date >= {_lit(VIIRS_START)}""").fetchone()[0]
    for i, (y, m) in enumerate(months):
        src = f"read_parquet({_lit(_stage_glob(STAGE, y, m))}, union_by_name = true)"
        out = _part(DEDUP, y, m)
        os.makedirs(out, exist_ok=True)
        keep = f"WHERE acq_date <> {_lit(dropped)}" if dropped else ""
        others = [c for c in CANON if c not in KEY] + (EXTRA if extra else [])
        # duplicates: same pixel, time, satellite; keep SP (archive) over NRT. Hash aggregation can spill to disk.
        con.execute(f"""COPY (SELECT {", ".join(KEY)}, UNNEST(r) FROM (
                                SELECT {", ".join(KEY)},
                                       arg_min(struct_pack({", ".join(others)}),
                                               CASE source WHEN 'SP' THEN 0 WHEN 'NRT' THEN 1 ELSE 2 END) AS r
                                FROM {src} {keep} GROUP BY {", ".join(KEY)}))
                        TO {_lit(out + '/data.parquet')} (FORMAT parquet, COMPRESSION zstd)""")
        for d in glob.glob(os.path.join(STAGE, "f*")):
            shutil.rmtree(_part(d, y, m), ignore_errors=True)
        if cal is None and first_viirs:
            calib_parts.append(con.execute(harmonization.calibration_sums_sql(
                f"read_parquet({_lit(out + '/data.parquet')})", first_viirs)).df())
        _log(f"dedupe: {i + 1}/{len(months)} months ({time.time() - t0:.0f} s)")
    shutil.rmtree(STAGE, ignore_errors=True)

    # 3) calibration table
    if extra:
        info_path = os.path.join(HARMONIZED_DIR, "harmonization.json")
        info = json.load(open(info_path)) if os.path.exists(info_path) else \
            {"name": "external", "version": "?", "status": "final", "description": "Table from data/harmonized/"}
        cal_df = None
    else:
        cal_df, source = harmonization.calibration_table(cal, calib_parts)
        info = harmonization.info(cal_df, source)
    vd = con.execute(f"""SELECT DISTINCT acq_date FROM read_parquet({_lit(DEDUP + '/**/*.parquet')}, hive_partitioning = true)
                         WHERE sensor = 'VIIRS'""").df()
    con.register("viirs_days", vd)
    if cal_df is not None:
        con.register("cal", cal_df)

    # 4) harmonize per month, write hotspots and cells
    for d in (HD, CM, CD, DM, SM, SD):
        shutil.rmtree(d, ignore_errors=True)
        os.makedirs(d, exist_ok=True)
    stats = {"rows": 0, "modis": 0, "modis_calibrated": 0}
    agg = []
    for i, (y, m) in enumerate(months):
        src = f"read_parquet({_lit(_part(DEDUP, y, m) + '/data.parquet')})"
        sel = src if extra else harmonization.weights_sql(src, "cal", "viirs_days", "month" in cal_df.columns)
        out = _part(HD, y, m)
        os.makedirs(out, exist_ok=True)
        path = out + "/data.parquet"
        cols = ", ".join(CANON + EXTRA)
        con.execute(f"COPY (SELECT {cols} FROM ({sel}) ORDER BY acq_date, acq_time) TO {_lit(path)} (FORMAT parquet, COMPRESSION zstd)")
        shutil.rmtree(_part(DEDUP, y, m), ignore_errors=True)
        h = f"read_parquet({_lit(path)})"
        con.execute(f"COPY ({_cells_sql(h, 'substr(acq_date, 1, 7)', MONTH_RES)}) TO "
                    f"{_lit(os.path.join(CM, f'{y:04d}-{m:02d}.parquet'))} (FORMAT parquet, COMPRESSION zstd)")
        con.execute(f"COPY ({_cells_sql(h, 'acq_date', DAY_RES)}) TO "
                    f"{_lit(os.path.join(CD, f'{y:04d}-{m:02d}.parquet'))} (FORMAT parquet, COMPRESSION zstd)")
        for cells, series in ((CM, SM), (CD, SD)):
            f = os.path.join(cells, f"{y:04d}-{m:02d}.parquet")
            con.execute(f"COPY ({_series_sql(f)}) TO {_lit(os.path.join(series, f'{y:04d}-{m:02d}.parquet'))} (FORMAT parquet)")
        con.execute(f"COPY ({_dist_sql(h)}) TO {_lit(os.path.join(DM, f'{y:04d}-{m:02d}.parquet'))} "
                    "(FORMAT parquet, COMPRESSION zstd)")
        agg.append(con.execute(f"""SELECT sensor, satellite, count(*) AS n, min(acq_date) AS first, max(acq_date) AS last,
              min(longitude) AS w, min(latitude) AS s, max(longitude) AS e, max(latitude) AS n_,
              bool_or(type IS NOT NULL) AS has_type, count(*) FILTER (WHERE sensor = 'MODIS' AND weight > 0) AS mcal
              FROM {h} GROUP BY ALL""").df())
        _log(f"harmonize: {i + 1}/{len(months)} months ({time.time() - t0:.0f} s)")
    shutil.rmtree(DEDUP, ignore_errors=True)
    import pandas as pd
    a = pd.concat(agg, ignore_index=True)
    n_month = con.execute(f"SELECT count(*) FROM read_parquet({_lit(CM + '/*.parquet')})").fetchone()[0]
    n_day = con.execute(f"SELECT count(*) FROM read_parquet({_lit(CD + '/*.parquet')})").fetchone()[0]
    con.close()
    shutil.rmtree(TMP, ignore_errors=True)

    sat = a.groupby(["sensor", "satellite"]).agg(n=("n", "sum"), first=("first", "min"), last=("last", "max"))
    modis_rows = int(a.loc[a.sensor == "MODIS", "n"].sum())
    if not extra:
        info["params"]["calibrated_share"] = round(int(a["mcal"].sum()) / modis_rows, 4) if modis_rows else 0.0
    info = dict(info, source=mode, files=[os.path.basename(f) for f in files], dropped_partial_day=dropped)
    with open(w(HARM_INFO), "w") as f:
        json.dump(info, f, indent=2, default=str)
    by_sat = {s["satellite"]: s for s in SATELLITES}
    meta = {
        "data_version": datetime.now(timezone.utc).strftime("%Y%m%d-%H%M"),
        "inputs": _signature(files),
        "built": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "build_seconds": round(time.time() - t0, 1),
        "rows": int(a["n"].sum()),
        "first_date": str(a["first"].min()), "last_date": str(a["last"].max()),
        "bbox": [float(a.w.min()), float(a.s.min()), float(a.e.max()), float(a.n_.max())],
        "by_satellite": {f"{s}/{x}": int(r.n) for (s, x), r in sat.iterrows()},
        "has_type": bool(a["has_type"].any()),
        "satellites": [dict(s, first_date=(str(sat.xs(s["satellite"], level=1)["first"].min()) if s["satellite"] in sat.index.get_level_values(1) else None),
                            last_date=(str(sat.xs(s["satellite"], level=1)["last"].max()) if s["satellite"] in sat.index.get_level_values(1) else None),
                            rows=int(sat.xs(s["satellite"], level=1)["n"].sum()) if s["satellite"] in sat.index.get_level_values(1) else 0)
                       for s in SATELLITES] + [{"satellite": x, "sensor": s, "start": None,
                                               "first_date": str(r["first"]), "last_date": str(r["last"]), "rows": int(r.n)}
                                              for (s, x), r in sat.iterrows() if x not in by_sat],
        "storage": {"cells_month_rows": int(n_month), "cells_day_rows": int(n_day), "month_res": MONTH_RES,
                    "day_res": DAY_RES, "months": len(months)},
        "harmonization": info,
    }
    with open(w(META), "w") as f:
        json.dump(meta, f, indent=2, default=str)
    # swap the finished build in (the API's views read these paths on every query)
    old = PROCESSED + ".old"
    shutil.rmtree(old, ignore_errors=True)
    if os.path.isdir(PROCESSED):
        os.replace(PROCESSED, old)
    os.replace(WORK, PROCESSED)
    shutil.rmtree(old, ignore_errors=True)
    _log(f"{mode}: {meta['rows']:,} hotspots, {meta['first_date']} to {meta['last_date']}, {len(months)} months, "
         f"cells: {n_month:,} monthly / {n_day:,} daily, {meta['build_seconds']} s, "
         f"harmonization={info.get('name')} ({info.get('status')})")
    return meta


if __name__ == "__main__":
    build(force="--force" in sys.argv)
