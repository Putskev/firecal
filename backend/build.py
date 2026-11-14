"""
Build data/processed/, the data the API serves.

    python -m backend.build            # rebuild if inputs changed
    python -m backend.build --force    # always rebuild

Source of harmonized data, in this order:
  1. data/harmonized/*.parquet|*.csv  - a finished harmonized table (canonical columns + weight + frp_h),
                                        used as-is; info from data/harmonized/harmonization.json
  2. data/raw/*.parquet               - canonical raw data, harmonized with backend/harmonization.py

Output (all Parquet, read straight from disk by the API, never fully loaded into memory):
  processed/hotspots/y=YYYY/m=M/*.parquet   every harmonized hotspot, split by year and month
  processed/cells_month.parquet             sums per month x 0.1 deg cell x sensor x filter flags
  processed/cells_day.parquet               sums per day   x 0.25 deg cell x sensor x filter flags
  processed/harmonization.json, meta.json

The aggregates hold n (raw count), frp (raw FRP), w (sum of weight) and fh (sum of frp_h), split by
`low` (conf_class = low) and `veg` (type is 0 or unknown) so the app's filters still work on them.

Note on memory: the harmonization step works on one pandas DataFrame. For the full 20+ year
archive, run the build on a machine with enough RAM (or harmonize per year and use data/harmonized/).
"""
from __future__ import annotations

import glob
import json
import os
import shutil
import sys
from datetime import datetime, timezone

import duckdb
import pandas as pd

from . import harmonization
from .data import HARMONIZED, HARMONIZED_EXTRA, dedupe, drop_partial_last_day, normalize, validate
from .regions import SATELLITES
from .paths import (CELLS_DAY, CELLS_MONTH, DAY_RES, HARM_INFO, HARMONIZED_DIR, HOTSPOTS_DIR, META,
                    MONTH_RES, PROCESSED, RAW)


def _inputs() -> tuple[str, list[str]]:
    h = sorted(glob.glob(os.path.join(HARMONIZED_DIR, "*.parquet")) + glob.glob(os.path.join(HARMONIZED_DIR, "*.csv")))
    if h:
        return "harmonized", h
    return "raw", sorted(glob.glob(os.path.join(RAW, "*.parquet")))


def needs_build() -> bool:
    if not (os.path.exists(META) and os.path.exists(CELLS_DAY) and os.path.isdir(HOTSPOTS_DIR)):
        return True
    _, files = _inputs()
    from . import regions
    deps = (files + [harmonization.__file__, regions.__file__, __file__]
            + glob.glob(os.path.join(HARMONIZED_DIR, "*.json"))
            + glob.glob(os.path.join(os.path.dirname(RAW), "calibration", "*.csv")))
    built = os.path.getmtime(META)
    if any(os.path.getmtime(f) > built for f in deps if os.path.exists(f)):
        return True
    # input files added, removed or still being written (e.g. git pull while the server reloads)
    try:
        with open(META) as f:
            return json.load(f).get("inputs") != _signature(files)
    except (OSError, ValueError):
        return True


def _signature(files: list[str]) -> list[list]:
    return [[os.path.basename(f), os.path.getsize(f)] for f in files if os.path.exists(f)]


def _read(path: str) -> pd.DataFrame:
    return pd.read_parquet(path) if path.endswith(".parquet") else pd.read_csv(path, low_memory=False)


def _harmonized_table(mode: str, files: list[str]) -> tuple[pd.DataFrame, dict]:
    if mode == "harmonized":
        df = pd.concat([_read(f) for f in files], ignore_index=True)
        extra = df[[c for c in HARMONIZED_EXTRA if c in df.columns]].reset_index(drop=True)
        df = pd.concat([normalize(df), extra], axis=1)
        validate(df, harmonized=True)
        info_path = os.path.join(HARMONIZED_DIR, "harmonization.json")
        info = json.load(open(info_path)) if os.path.exists(info_path) else \
            {"name": "external", "version": "?", "status": "final", "description": "Table from data/harmonized/"}
        return df, info
    raw = dedupe(pd.concat([normalize(_read(f)) for f in files], ignore_index=True))
    raw, dropped = drop_partial_last_day(raw)
    validate(raw)
    df, info = harmonization.harmonize(raw)
    validate(df, harmonized=True)
    info["dropped_partial_day"] = dropped
    return df, info


def _cells_sql(period: str, res: float) -> str:
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
        FROM t GROUP BY ALL ORDER BY period"""


def _sat_info(df: pd.DataFrame, s: dict) -> dict:
    part = df.loc[df.satellite == s["satellite"], "acq_date"]
    return dict(s, first_date=part.min() if len(part) else None, last_date=part.max() if len(part) else None,
                rows=int(len(part)))


def build(force: bool = False) -> dict:
    if not force and not needs_build():
        with open(META) as f:
            return json.load(f)
    mode, files = _inputs()
    if not files:
        raise FileNotFoundError("No data: put FIRMS CSVs into data/incoming/ and run "
                                "`python -m backend.ingest`, or put a harmonized table into data/harmonized/")
    os.makedirs(PROCESSED, exist_ok=True)
    df, info = _harmonized_table(mode, files)
    df = df[HARMONIZED].copy()
    df["type"] = df["type"].astype("Int8")
    df["y"] = df["acq_date"].str[:4].astype("int16")
    df["m"] = df["acq_date"].str[5:7].astype("int8")

    # 1) all hotspots, partitioned by year / month
    shutil.rmtree(HOTSPOTS_DIR, ignore_errors=True)
    df.to_parquet(HOTSPOTS_DIR, partition_cols=["y", "m"], index=False, compression="zstd")

    # 2) aggregates
    con = duckdb.connect()
    con.register("t", df)
    con.execute(f"COPY ({_cells_sql('substr(acq_date, 1, 7)', MONTH_RES)}) TO '{CELLS_MONTH}' (FORMAT parquet, COMPRESSION zstd)")
    con.execute(f"COPY ({_cells_sql('acq_date', DAY_RES)}) TO '{CELLS_DAY}' (FORMAT parquet, COMPRESSION zstd)")
    n_month = con.execute(f"SELECT count(*) FROM '{CELLS_MONTH}'").fetchone()[0]
    n_day = con.execute(f"SELECT count(*) FROM '{CELLS_DAY}'").fetchone()[0]
    con.close()

    info = dict(info, source=mode, files=[os.path.basename(f) for f in files])
    with open(HARM_INFO, "w") as f:
        json.dump(info, f, indent=2, default=str)
    meta = {
        "data_version": datetime.now(timezone.utc).strftime("%Y%m%d-%H%M"),
        "inputs": _signature(files),
        "built": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "rows": int(len(df)),
        "first_date": df["acq_date"].min(), "last_date": df["acq_date"].max(),
        "bbox": [float(df.longitude.min()), float(df.latitude.min()), float(df.longitude.max()), float(df.latitude.max())],
        "by_satellite": {f"{s}/{sat}": int(n) for (s, sat), n in df.groupby(["sensor", "satellite"]).size().items()},
        "has_type": bool(df["type"].notna().any()),
        "satellites": [_sat_info(df, s) for s in SATELLITES],
        "storage": {"cells_month_rows": int(n_month), "cells_day_rows": int(n_day),
                    "month_res": MONTH_RES, "day_res": DAY_RES},
        "harmonization": info,
    }
    with open(META, "w") as f:
        json.dump(meta, f, indent=2, default=str)
    print(f"[build] {mode}: {meta['rows']:,} hotspots, {meta['first_date']} to {meta['last_date']}, "
          f"cells: {n_month:,} monthly / {n_day:,} daily, harmonization={info.get('name')} ({info.get('status')})")
    return meta


if __name__ == "__main__":
    build(force="--force" in sys.argv)
