"""
FireCal data updater.

Builds data/hotspots.csv from
  1. archive files in data/archive/*.csv  (FIRMS Archive Download, MODIS + VIIRS, SP or NRT)
  2. a rolling near-real-time cache data/nrt_cache.csv, filled from the FIRMS area API

NRT rows that are older than the newest archive date are dropped, so when you put a newer
archive download into data/archive/, the checked standard data replaces the provisional NRT data.

Also writes
  data/meta.json         last update, counts, date span
  data/harmonization.json fixed VIIRS -> MODIS factors (only on first run or with --recalibrate)

Usage:
  FIRMS_MAP_KEY=... python update.py            # normal daily run
  python update.py --recalibrate                # recompute the fixed harmonization factors
  python update.py --no-api                     # only rebuild from archive + cache
"""
import argparse
import glob
import io
import json
import os
import sys
import time
from datetime import date, datetime, timedelta, timezone
from urllib.request import urlopen, Request

import pandas as pd

ROOT = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(ROOT, "data")
ARCHIVE_GLOB = os.path.join(DATA, "archive", "*.csv")
CACHE = os.path.join(DATA, "nrt_cache.csv")
OUT = os.path.join(DATA, "hotspots.csv")
META = os.path.join(DATA, "meta.json")
HARM = os.path.join(DATA, "harmonization.json")
CONFIG = os.path.join(ROOT, "config.json")

COLS = ["latitude", "longitude", "acq_date", "acq_time", "satellite", "instrument",
        "confidence", "frp", "daynight", "type", "source"]
KEY_COLS = ["latitude", "longitude", "acq_date", "acq_time", "satellite"]


def log(*a):
    print("[update]", *a, flush=True)


def load_config():
    with open(CONFIG) as f:
        cfg = json.load(f)
    cfg.setdefault("api_base", "https://firms.modaps.eosdis.nasa.gov")
    cfg.setdefault("max_catchup_days", 60)
    return cfg


def normalize(df, source):
    """Bring a FIRMS CSV (archive or API) into one schema."""
    if df.empty:
        return pd.DataFrame(columns=COLS)
    df = df.copy()
    if "instrument" not in df.columns:
        df["instrument"] = "VIIRS" if "bright_ti4" in df.columns else "MODIS"
    df["instrument"] = df["instrument"].astype(str).str.upper().map(lambda s: "VIIRS" if "VIIRS" in s else "MODIS")
    if "type" not in df.columns:
        df["type"] = pd.NA
    df["source"] = source
    df["acq_date"] = pd.to_datetime(df["acq_date"]).dt.strftime("%Y-%m-%d")
    df["acq_time"] = pd.to_numeric(df["acq_time"], errors="coerce").fillna(0).astype(int)
    df["latitude"] = pd.to_numeric(df["latitude"], errors="coerce").round(4)
    df["longitude"] = pd.to_numeric(df["longitude"], errors="coerce").round(4)
    df["frp"] = pd.to_numeric(df["frp"], errors="coerce")
    df["satellite"] = df["satellite"].astype(str)
    for c in COLS:
        if c not in df.columns:
            df[c] = pd.NA
    return df[COLS].dropna(subset=["latitude", "longitude", "acq_date"])


def in_area(df, area):
    w, s, e, n = [float(x) for x in area.split(",")]
    return df[(df.longitude >= w) & (df.longitude <= e) & (df.latitude >= s) & (df.latitude <= n)]


def read_archive(area):
    files = sorted(glob.glob(ARCHIVE_GLOB))
    frames = []
    for f in files:
        try:
            frames.append(normalize(pd.read_csv(f, low_memory=False), "archive"))
            log(f"archive: {os.path.basename(f)} ({len(frames[-1])} rows)")
        except Exception as ex:
            log(f"archive: could not read {f}: {ex}")
    if not frames:
        return pd.DataFrame(columns=COLS)
    return in_area(pd.concat(frames, ignore_index=True), area)


def read_cache():
    if os.path.exists(CACHE):
        return normalize(pd.read_csv(CACHE, low_memory=False), "nrt")
    return pd.DataFrame(columns=COLS)


def fetch(url, tries=3):
    for i in range(tries):
        try:
            with urlopen(Request(url, headers={"User-Agent": "FireCal/1.0"}), timeout=120) as r:
                text = r.read().decode("utf-8")
            if not text.strip() or text.lstrip().lower().startswith(("invalid", "error", "<")):
                raise RuntimeError(text[:200])
            return pd.read_csv(io.StringIO(text))
        except Exception as ex:
            log(f"  attempt {i + 1}/{tries} failed: {ex}")
            time.sleep(5 * (i + 1))
    return None


def fetch_api(cfg, key, since):
    """Fetch all days from `since` until today in chunks of at most 10 days per request."""
    today = datetime.now(timezone.utc).date()
    start = max(since, today - timedelta(days=cfg["max_catchup_days"]))
    frames = []
    for src in cfg["sources"]:
        d = start
        while d <= today:
            n = min(10, (today - d).days + 1)
            url = f"{cfg['api_base']}/api/area/csv/{key}/{src}/{cfg['area']}/{n}/{d.isoformat()}"
            log(f"api: {src} {d} +{n}d")
            df = fetch(url)
            if df is not None:
                df = normalize(df, "nrt")
                if src.upper().startswith("MODIS"):
                    df["instrument"] = "MODIS"
                elif src.upper().startswith("VIIRS"):
                    df["instrument"] = "VIIRS"
                frames.append(df)
            d += timedelta(days=n)
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(columns=COLS)


def harmonization(df):
    """Fixed factors k = sum(MODIS) / sum(VIIRS) over months where both sensors detected fires.
    Uses the same default quality filter as the frontend (MODIS conf >= 30, VIIRS not 'l')."""
    d = df.copy()
    d["month"] = d["acq_date"].str[:7]
    conf = d["confidence"].astype(str).str.lower()
    is_modis = d["instrument"] == "MODIS"
    num = pd.to_numeric(d["confidence"], errors="coerce")
    keep = (is_modis & (num >= 30)) | (~is_modis & ~conf.str.startswith("l"))
    d = d[keep]
    g = d.groupby(["month", "instrument"]).agg(n=("frp", "size"), frp=("frp", "sum")).unstack(fill_value=0)
    if ("n", "MODIS") not in g.columns or ("n", "VIIRS") not in g.columns:
        return None
    both = g[(g[("n", "MODIS")] > 0) & (g[("n", "VIIRS")] > 0)]
    if both.empty:
        return None
    return {
        "k_count": round(float(both[("n", "MODIS")].sum() / both[("n", "VIIRS")].sum()), 5),
        "k_frp": round(float(both[("frp", "MODIS")].sum() / both[("frp", "VIIRS")].sum()), 5),
        "overlap_months": int(len(both)),
        "period": [both.index.min(), both.index.max()],
        "method": "ratio of monthly sums over overlap months (placeholder, replace with team calibration)",
        "created": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--recalibrate", action="store_true")
    ap.add_argument("--no-api", action="store_true")
    args = ap.parse_args()
    cfg = load_config()
    os.makedirs(DATA, exist_ok=True)

    archive = read_archive(cfg["area"])
    cache = read_cache()
    archive_end = archive["acq_date"].max() if len(archive) else None

    key = os.environ.get("FIRMS_MAP_KEY", "").strip()
    if args.no_api:
        log("api: skipped (--no-api)")
    elif not key:
        log("api: skipped, FIRMS_MAP_KEY not set")
    else:
        last = cache["acq_date"].max() if len(cache) else archive_end
        since = (date.fromisoformat(last) - timedelta(days=2)) if last else \
            datetime.now(timezone.utc).date() - timedelta(days=9)
        new = fetch_api(cfg, key, since)
        log(f"api: {len(new)} rows received")
        cache = pd.concat([cache, new], ignore_index=True)

    if len(cache):
        cache = in_area(cache, cfg["area"]).drop_duplicates(subset=KEY_COLS, keep="last")
        if archive_end:
            cache = cache[cache["acq_date"] > archive_end]   # archive (SP) replaces older NRT
        cache.sort_values(["acq_date", "acq_time"]).to_csv(CACHE, index=False)

    allp = pd.concat([archive, cache], ignore_index=True)
    allp = allp.drop_duplicates(subset=KEY_COLS, keep="first").sort_values(["acq_date", "acq_time"])
    if allp.empty:
        # Not an error during setup: the app shows demo data until real data exists.
        log("no data yet: put archive CSVs into data/archive/ and/or set the FIRMS_MAP_KEY secret")
        sys.exit(0)
    allp.to_csv(OUT, index=False)

    if args.recalibrate or not os.path.exists(HARM):
        h = harmonization(allp)
        if h:
            with open(HARM, "w") as f:
                json.dump(h, f, indent=2)
            log(f"harmonization: k_count={h['k_count']} k_frp={h['k_frp']} ({h['overlap_months']} months)")
        else:
            log("harmonization: no overlap months yet, file not written")

    meta = {
        "updated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "area": cfg["area"],
        "rows": int(len(allp)),
        "modis": int((allp["instrument"] == "MODIS").sum()),
        "viirs": int((allp["instrument"] == "VIIRS").sum()),
        "first_date": allp["acq_date"].min(),
        "last_date": allp["acq_date"].max(),
        "archive_until": archive_end,
        "nrt_rows": int(len(cache)),
    }
    with open(META, "w") as f:
        json.dump(meta, f, indent=2)
    log(f"done: {meta['rows']} hotspots, {meta['first_date']} to {meta['last_date']}")


if __name__ == "__main__":
    main()
