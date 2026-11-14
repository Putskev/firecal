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
CELLS_MONTH = os.path.join(PROCESSED, "cells_month")   # month x 0.1 deg cell, one file per month
CELLS_DAY = os.path.join(PROCESSED, "cells_day")       # day x 0.25 deg cell, one file per month
SERIES_MONTH = os.path.join(PROCESSED, "series_month")  # month x sensor x satellite x flags (no space): fast global series
SERIES_DAY = os.path.join(PROCESSED, "series_day")
DIST_MONTH = os.path.join(PROCESSED, "dist_month")     # month x 1 deg cell x FRP class x local hour (distributions)
DIST_RES = 1.0
FRP_EDGES = [0, 1, 2, 3, 5, 7, 10, 15, 20, 30, 50, 70, 100, 150, 200, 300, 500, 1000, 1e9]
HARM_INFO = os.path.join(PROCESSED, "harmonization.json")
META = os.path.join(PROCESSED, "meta.json")

MONTH_RES = 0.1
DAY_RES = 0.25
