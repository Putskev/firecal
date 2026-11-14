"""
Build data/processed/hotspots.parquet, the table the API serves.

    python -m backend.build            # rebuild if inputs changed
    python -m backend.build --force    # always rebuild

Source of harmonized data, in this order:
  1. data/harmonized/*.parquet|*.csv  - a finished harmonized table (canonical columns + weight + frp_h),
                                        used as-is; info from data/harmonized/harmonization.json
  2. data/raw/*.parquet               - canonical raw data, harmonized with backend/harmonization.py
"""
from __future__ import annotations

import glob
import json
import os
import sys
from datetime import datetime, timezone

import pandas as pd

from . import harmonization
from .data import HARMONIZED, dedupe, normalize, validate
from .paths import HARM_INFO, HARMONIZED_DIR, HOTSPOTS, META, PROCESSED, RAW


def _inputs() -> tuple[str, list[str]]:
    h = sorted(glob.glob(os.path.join(HARMONIZED_DIR, "*.parquet")) + glob.glob(os.path.join(HARMONIZED_DIR, "*.csv")))
    if h:
        return "harmonized", h
    return "raw", sorted(glob.glob(os.path.join(RAW, "*.parquet")))


def needs_build() -> bool:
    if not os.path.exists(HOTSPOTS):
        return True
    _, files = _inputs()
    deps = files + [harmonization.__file__] + glob.glob(os.path.join(HARMONIZED_DIR, "*.json"))
    built = os.path.getmtime(HOTSPOTS)
    return any(os.path.getmtime(f) > built for f in deps if os.path.exists(f))


def _read(path: str) -> pd.DataFrame:
    return pd.read_parquet(path) if path.endswith(".parquet") else pd.read_csv(path, low_memory=False)


def build(force: bool = False) -> dict:
    if not force and not needs_build():
        with open(META) as f:
            return json.load(f)
    mode, files = _inputs()
    if not files:
        raise FileNotFoundError("No data: put FIRMS CSVs into data/incoming/ and run "
                                "`python -m backend.ingest`, or put a harmonized table into data/harmonized/")
    os.makedirs(PROCESSED, exist_ok=True)

    if mode == "harmonized":
        df = pd.concat([_read(f) for f in files], ignore_index=True)
        if "conf_class" not in df.columns or "source" not in df.columns:   # tolerate raw-ish columns
            extra = df[[c for c in ("weight", "frp_h") if c in df.columns]]
            df = pd.concat([normalize(df), extra.reset_index(drop=True)], axis=1)
        validate(df, harmonized=True)
        info_path = os.path.join(HARMONIZED_DIR, "harmonization.json")
        info = json.load(open(info_path)) if os.path.exists(info_path) else \
            {"name": "external", "version": "?", "status": "final", "description": "Table from data/harmonized/"}
    else:
        raw = dedupe(pd.concat([_read(f) for f in files], ignore_index=True))
        validate(raw)
        df, info = harmonization.harmonize(raw)
        validate(df, harmonized=True)

    df = df[HARMONIZED].copy()
    df["type"] = df["type"].astype("Int8")
    df.to_parquet(HOTSPOTS, index=False, compression="zstd")
    info = dict(info, source=mode, files=[os.path.basename(f) for f in files])
    with open(HARM_INFO, "w") as f:
        json.dump(info, f, indent=2, default=str)

    meta = {
        "built": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "rows": int(len(df)),
        "first_date": df["acq_date"].min(), "last_date": df["acq_date"].max(),
        "bbox": [float(df.longitude.min()), float(df.latitude.min()), float(df.longitude.max()), float(df.latitude.max())],
        "by_satellite": {f"{s}/{sat}": int(n) for (s, sat), n in df.groupby(["sensor", "satellite"]).size().items()},
        "has_type": bool(df["type"].notna().any()),
        "harmonization": info,
    }
    with open(META, "w") as f:
        json.dump(meta, f, indent=2, default=str)
    print(f"[build] {mode}: {meta['rows']:,} hotspots, {meta['first_date']} to {meta['last_date']}, "
          f"harmonization={info.get('name')} ({info.get('status')})")
    return meta


if __name__ == "__main__":
    build(force="--force" in sys.argv)
