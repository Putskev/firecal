import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")
INCOMING = os.path.join(DATA, "incoming")      # drop raw FIRMS CSVs here (not committed)
RAW = os.path.join(DATA, "raw")                # canonical, NOT harmonized Parquet (committed)
HARMONIZED_DIR = os.path.join(DATA, "harmonized")  # optional finished harmonized table (drop-in)
PROCESSED = os.path.join(DATA, "processed")    # built by build.py, read by the API (not committed)
HOTSPOTS = os.path.join(PROCESSED, "hotspots.parquet")
HARM_INFO = os.path.join(PROCESSED, "harmonization.json")
META = os.path.join(PROCESSED, "meta.json")
