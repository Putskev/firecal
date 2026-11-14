Optional drop-in for a finished harmonized table.

If this folder contains `*.parquet` or `*.csv` files, the API uses them **as-is** instead of running
`backend/harmonization.py` on `data/raw/`. Required columns (see `backend/data.py`):

    latitude, longitude, acq_date, acq_time, satellite, sensor, confidence, conf_class,
    frp, daynight, type, version, source, weight, frp_h

Optional `harmonization.json` next to it describes the method shown in the app:

    {"name": "my-method", "version": "1.0", "status": "final",
     "description": "One sentence", "params": {"anything": 1}}
