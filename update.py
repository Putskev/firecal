"""
Daily FIRMS updater.

Fetches all days since the newest hotspot in data/raw/ from the FIRMS area API
(MODIS + VIIRS S-NPP / NOAA-20 / NOAA-21), normalizes them to the canonical schema and appends
them to data/raw/nrt_cache.parquet. The API rebuilds the harmonized table on its next start
(or via POST /api/reload).

    FIRMS_MAP_KEY=... python update.py           # fetch + append
    FIRMS_MAP_KEY=... python update.py --build   # fetch + append + rebuild data/processed/

Settings in config.json: area (west,south,east,north), sources, max_catchup_days.
"""
from __future__ import annotations

import glob
import io
import json
import os
import sys
import time
from datetime import date, datetime, timedelta, timezone
from urllib.request import Request, urlopen

import pandas as pd

from backend.data import dedupe, normalize
from backend.paths import RAW, ROOT

CACHE = os.path.join(RAW, "nrt_cache.parquet")


def log(*a):
    print("[update]", *a, flush=True)


def load_config() -> dict:
    with open(os.path.join(ROOT, "config.json")) as f:
        cfg = json.load(f)
    cfg.setdefault("api_base", "https://firms.modaps.eosdis.nasa.gov")
    cfg.setdefault("max_catchup_days", 30)
    return cfg


def fetch(url: str, tries: int = 3) -> pd.DataFrame | None:
    for i in range(tries):
        try:
            with urlopen(Request(url, headers={"User-Agent": "FireCal/1.0"}), timeout=180) as r:
                text = r.read().decode("utf-8")
            if not text.strip() or text.lstrip().lower().startswith(("invalid", "error", "<")):
                raise RuntimeError(text[:200])
            return pd.read_csv(io.StringIO(text))
        except Exception as ex:  # noqa: BLE001
            log(f"  attempt {i + 1}/{tries} failed: {ex}")
            time.sleep(5 * (i + 1))
    return None


def newest_date() -> str | None:
    dates = []
    for f in glob.glob(os.path.join(RAW, "*.parquet")):
        try:
            dates.append(pd.read_parquet(f, columns=["acq_date"])["acq_date"].max())
        except Exception:  # noqa: BLE001
            pass
    return max(dates) if dates else None


def main() -> None:
    cfg = load_config()
    key = os.environ.get("FIRMS_MAP_KEY", "").strip()
    if not key:
        log("FIRMS_MAP_KEY not set, nothing to fetch")
        return
    os.makedirs(RAW, exist_ok=True)
    today = datetime.now(timezone.utc).date()
    last = newest_date()
    since = date.fromisoformat(last) - timedelta(days=1) if last else today - timedelta(days=9)
    since = max(since, today - timedelta(days=cfg["max_catchup_days"]))

    frames = []
    for src in cfg["sources"]:
        d = since
        while d <= today:
            n = min(10, (today - d).days + 1)
            url = f"{cfg['api_base']}/api/area/csv/{key}/{src}/{cfg['area']}/{n}/{d.isoformat()}"
            log(f"{src} {d} +{n}d")
            df = fetch(url)
            if df is not None and len(df):
                frames.append(normalize(df, sensor_hint="MODIS" if src.upper().startswith("MODIS") else "VIIRS"))
            d += timedelta(days=n)
    new = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    log(f"{len(new):,} rows received")
    if new.empty:
        return
    old = pd.read_parquet(CACHE) if os.path.exists(CACHE) else new.iloc[0:0]
    cache = dedupe(pd.concat([old, new], ignore_index=True))
    cache.to_parquet(CACHE, index=False, compression="zstd")
    log(f"nrt_cache.parquet: {len(cache):,} rows, {cache.acq_date.min()} to {cache.acq_date.max()}")

    if "--build" in sys.argv:
        from backend.build import build
        build(force=True)


if __name__ == "__main__":
    main()
