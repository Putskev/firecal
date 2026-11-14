"""
Convert raw FIRMS CSV files into canonical Parquet files in data/raw/.

    python -m backend.ingest                       # all CSVs in data/incoming/
    python -m backend.ingest path/to/a.csv b.csv   # specific files

Works for NRT downloads, archive downloads (MODIS, VIIRS S-NPP / NOAA-20 / NOAA-21) and API CSVs.
The Parquet files are ~5x smaller than the CSVs and can be committed to the repo.
"""
from __future__ import annotations

import glob
import os
import sys

import pandas as pd

from .data import dedupe, normalize
from .paths import INCOMING, RAW


def ingest_file(path: str) -> str:
    df = normalize(pd.read_csv(path, low_memory=False))
    df = dedupe(df)
    name = os.path.splitext(os.path.basename(path))[0]
    out = os.path.join(RAW, f"{name}.parquet")
    df.to_parquet(out, index=False, compression="zstd")
    by = df.groupby(["sensor", "satellite"]).size().to_dict()
    print(f"[ingest] {os.path.basename(path)} -> {os.path.relpath(out)}: {len(df):,} rows, "
          f"{df.acq_date.min()} to {df.acq_date.max()}, {by}")
    return out


def main(argv: list[str]) -> None:
    os.makedirs(RAW, exist_ok=True)
    files = argv or sorted(glob.glob(os.path.join(INCOMING, "*.csv")))
    if not files:
        print("[ingest] no CSV files given and none in data/incoming/")
        return
    for f in files:
        ingest_file(f)


if __name__ == "__main__":
    main(sys.argv[1:])
