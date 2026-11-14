import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")
INCOMING = os.path.join(DATA, "incoming")          # drop raw FIRMS CSVs here (not committed)
RAW = os.path.join(DATA, "raw")                    # canonical, NOT harmonized Parquet (committed)
HARMONIZED_DIR = os.path.join(DATA, "harmonized")  # optional finished harmonized table (drop-in)
TEAM = os.path.join(DATA, "team")                  # data team deliveries used as-is: Tabelle B (fire_activity_*.parquet)
PROCESSED = os.path.join(DATA, "processed")        # built by build.py, read by the API (not committed)

# harmonized hotspots, one folder per year and month: hotspots/y=2026/m=11/*.parquet
HOTSPOTS_DIR = os.path.join(PROCESSED, "hotspots")
# pre-aggregated sums for fast calendar / anomaly / heatmap queries
CELLS_MONTH = os.path.join(PROCESSED, "cells_month.parquet")   # month x 0.1 deg cell
CELLS_DAY = os.path.join(PROCESSED, "cells_day.parquet")       # day x 0.25 deg cell
HARM_INFO = os.path.join(PROCESSED, "harmonization.json")
META = os.path.join(PROCESSED, "meta.json")

MONTH_RES = 0.1
DAY_RES = 0.25
