Put raw FIRMS CSV downloads (NRT or archive, MODIS or VIIRS) here, then run

    python -m backend.ingest

They are converted to canonical Parquet files in `data/raw/`. CSV files in this folder are not committed.
