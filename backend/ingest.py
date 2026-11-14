"""
Convert FIRMS files into canonical Parquet files in data/raw/. Accepted:
  * raw FIRMS CSV (.csv / .csv.gz), Parquet
  * FIRMS download ZIPs (DL_FIRE_*.zip) with a shapefile (.dbf attributes are read) or a CSV inside
  * the team's 'Tabelle A' (harmonisierte_hotspots.csv.gz)

    python -m backend.ingest                       # all CSVs in data/incoming/
    python -m backend.ingest path/to/a.csv b.csv   # specific files

Works for NRT downloads, archive downloads (MODIS, VIIRS S-NPP / NOAA-20 / NOAA-21) and API CSVs.
The Parquet files are ~5x smaller than the CSVs and can be committed to the repo.
"""
from __future__ import annotations

import glob
import io
import os
import sys
import tempfile
import zipfile

import pandas as pd

from .data import dedupe, normalize
from .paths import INCOMING, RAW


def _read_zip(path: str) -> pd.DataFrame:
    """FIRMS download ZIP: shapefile (read the .dbf attribute table) or CSV inside."""
    with zipfile.ZipFile(path) as z:
        names = z.namelist()
        csvs = [n for n in names if n.lower().endswith(".csv")]
        if csvs:
            return pd.concat([pd.read_csv(io.BytesIO(z.read(n)), low_memory=False) for n in csvs], ignore_index=True)
        dbfs = [n for n in names if n.lower().endswith(".dbf")]
        if not dbfs:
            raise ValueError(f"{path}: no .csv or .dbf inside")
        from dbfread import DBF  # small pure-Python reader, only needed for shapefile ZIPs
        frames = []
        with tempfile.TemporaryDirectory() as tmp:
            for n in dbfs:
                z.extract(n, tmp)
                frames.append(pd.DataFrame(iter(DBF(os.path.join(tmp, n), load=False, char_decode_errors="ignore"))))
        return pd.concat(frames, ignore_index=True)


def _read(path: str) -> pd.DataFrame:
    if path.endswith(".parquet"):
        return pd.read_parquet(path)
    if path.endswith(".zip"):
        return _read_zip(path)
    return pd.read_csv(path, low_memory=False)


def ingest_file(path: str) -> str:
    df = normalize(_read(path))
    df = dedupe(df)
    name = os.path.basename(path)
    for ext in (".csv.gz", ".csv", ".parquet", ".zip"):
        if name.endswith(ext):
            name = name[: -len(ext)]
    out = os.path.join(RAW, f"{name}.parquet")
    df.to_parquet(out, index=False, compression="zstd")
    by = df.groupby(["sensor", "satellite"]).size().to_dict()
    print(f"[ingest] {os.path.basename(path)} -> {os.path.relpath(out)}: {len(df):,} rows, "
          f"{df.acq_date.min()} to {df.acq_date.max()}, {by}")
    return out


def main(argv: list[str]) -> None:
    os.makedirs(RAW, exist_ok=True)
    files = argv or sorted(sum((glob.glob(os.path.join(INCOMING, p)) for p in ("*.csv", "*.csv.gz", "*.parquet", "*.zip")), []))
    if not files:
        print("[ingest] no CSV files given and none in data/incoming/")
        return
    for f in files:
        ingest_file(f)


if __name__ == "__main__":
    main(sys.argv[1:])
