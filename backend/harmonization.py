"""
=====================================================================================
  HARMONIZATION  (method as in docs/HANDOFF_Frontend.md, section 5)
=====================================================================================

Target level: VIIRS. Harmonized value of an area and period =
    native VIIRS                       on days where VIIRS data exist (from 2012-01-20)
    MODIS x factor(region[, month])    before that (or on days without VIIRS data)
with factor = SUM(VIIRS) / SUM(MODIS) over 2-degree cells and 7-day windows in which BOTH
sensors detected fires (low confidence excluded), separately for count and FRP.

Where the factors come from (first match wins):
  1. data/calibration/*.csv  - "Tabelle C" from the data team:
        region, factor_frp, factor_n, factor_frp_p25, factor_frp_p75, r_log_frp, n_cells,
        used_global_fallback   [+ optional: month (1-12), factor_n_p25, factor_n_p75]
  2. computed here from the data (same method, used while no table is delivered)

The result is written per hotspot (weight = how much the hotspot counts on the harmonized
VIIRS level), so every view of the app (calendar, anomalies, any drawn area) is a simple SUM.
Uncertainty: weight_lo / weight_hi use the 25th / 75th percentile of the cell ratios.

Everything runs in SQL inside DuckDB (backend/build.py calls the functions below month by month),
so the archive can be far larger than the RAM. To plug in a different method, change weights_sql()
(per-hotspot weights) and/or calibration_table(); the output columns are listed in data.py.
Full explanation with all formulas: docs/ERKLAERUNG.md
"""
from __future__ import annotations

import glob
import os

import numpy as np
import pandas as pd

from .paths import DATA
from .regions import REGION_SQL, REGIONS, VIIRS_START

CALIBRATION_DIR = os.path.join(DATA, "calibration")
CELL_DEG = 2.0
WINDOW_DAYS = 7
MIN_CELLS = 20

INFO = {
    "name": "region-factor (VIIRS level)",
    "version": "0.2",
    "status": "placeholder",
    "description": "MODIS x factor per region (VIIRS/MODIS over 2-degree cells and 7-day windows where "
                   "both sensors detect fire); native VIIRS from 2012-01-20. Factors from the data, "
                   "not yet from the team's calibration table.",
}


def calibration_sums_sql(src: str, first_viirs: str) -> str:
    """Sums per region x 2-degree cell x 7-day window x sensor (low confidence excluded, VIIRS period only).
    Run per month; the sums of all months are added up in calibration_table()."""
    return f"""
      SELECT {REGION_SQL} AS region,
             CAST(floor(latitude / {CELL_DEG}) AS INTEGER) || '_' || CAST(floor(longitude / {CELL_DEG}) AS INTEGER) AS cell,
             date_diff('day', DATE '{first_viirs}', CAST(acq_date AS DATE)) // {WINDOW_DAYS} AS win,
             sensor, count(*) AS n, sum(frp) AS frp
      FROM {src}
      WHERE conf_class <> 'low' AND acq_date >= '{VIIRS_START}'
      GROUP BY ALL"""


def _calibrate(parts: list[pd.DataFrame]) -> pd.DataFrame:
    """Calibration table (Tabelle C) from the collected cell sums: factor = SUM(VIIRS) / SUM(MODIS) over the
    cells x windows where BOTH sensors detected fire; band = 25th / 75th percentile of the cell ratios."""
    if not parts:
        return pd.DataFrame()
    g = pd.concat(parts, ignore_index=True).groupby(["region", "cell", "win", "sensor"])[["n", "frp"]].sum()
    agg = g.unstack("sensor")
    if ("n", "MODIS") not in agg.columns or ("n", "VIIRS") not in agg.columns:
        return pd.DataFrame()
    agg = agg.fillna(0)
    both = agg[(agg[("n", "MODIS")] > 0) & (agg[("n", "VIIRS")] > 0)].reset_index()
    rows = []

    def row(name, part, fallback):
        mn, vn = part[("n", "MODIS")], part[("n", "VIIRS")]
        mf, vf = part[("frp", "MODIS")], part[("frp", "VIIRS")]
        rf = (vf / mf.replace(0, np.nan)).dropna()
        rn = (vn / mn).dropna()
        ok = (mf > 0) & (vf > 0)
        r = float(np.corrcoef(np.log(mf[ok]), np.log(vf[ok]))[0, 1]) if ok.sum() > 2 else np.nan
        return {"region": name, "factor_frp": vf.sum() / mf.sum() if mf.sum() > 0 else np.nan,
                "factor_n": vn.sum() / mn.sum() if mn.sum() > 0 else np.nan,
                "factor_frp_p25": rf.quantile(.25), "factor_frp_p75": rf.quantile(.75),
                "factor_n_p25": rn.quantile(.25), "factor_n_p75": rn.quantile(.75),
                "r_log_frp": r, "n_cells": int(len(part)), "used_global_fallback": fallback}

    if both.empty:
        return pd.DataFrame()
    glob_row = row("global", both, False)
    for reg in REGIONS:
        part = both[both["region"] == reg]
        rows.append(row(reg, part, False) if len(part) >= MIN_CELLS else
                    {**glob_row, "region": reg, "n_cells": int(len(part)), "used_global_fallback": True})
    rows.append(glob_row)
    return pd.DataFrame(rows)


def calibration_files() -> list[str]:
    """Team calibration tables: data/calibration/*.csv|*.parquet and calibration* files in FIRECAL_SOURCE."""
    from .sources import calibration_files as external
    return sorted(glob.glob(os.path.join(CALIBRATION_DIR, "*.csv")) + glob.glob(os.path.join(CALIBRATION_DIR, "*.parquet"))) + external()


def load_calibration() -> pd.DataFrame | None:
    files = calibration_files()
    if not files:
        return None
    cal = pd.concat([pd.read_parquet(f) if f.endswith(".parquet") else pd.read_csv(f) for f in files], ignore_index=True)
    cal = cal.drop_duplicates([c for c in ["region", "month"] if c in cal.columns], keep="last")
    need = {"region", "factor_n", "factor_frp"}
    if not need <= set(cal.columns):
        raise ValueError(f"calibration table needs columns {need}")
    for c, src in [("factor_frp_p25", "factor_frp"), ("factor_frp_p75", "factor_frp")]:
        if c not in cal.columns:
            cal[c] = cal[src]
    # count uncertainty: same relative band as FRP if not delivered
    if "factor_n_p25" not in cal.columns:
        cal["factor_n_p25"] = cal["factor_n"] * cal["factor_frp_p25"] / cal["factor_frp"]
    if "factor_n_p75" not in cal.columns:
        cal["factor_n_p75"] = cal["factor_n"] * cal["factor_frp_p75"] / cal["factor_frp"]
    return cal


FACTORS = ["factor_n", "factor_frp", "factor_n_p25", "factor_n_p75", "factor_frp_p25", "factor_frp_p75"]


def calibration_table(team: pd.DataFrame | None, parts: list[pd.DataFrame]) -> tuple[pd.DataFrame, str]:
    if team is not None:
        return team, "team calibration table (" + ", ".join(os.path.basename(f) for f in calibration_files()) + ")"
    cal = _calibrate(parts)
    if not cal.empty:
        return cal, "computed from the loaded data"
    return (pd.DataFrame([{"region": r, **{c: 1.0 for c in FACTORS}, "r_log_frp": np.nan, "n_cells": 0,
                           "used_global_fallback": True} for r in REGIONS]),
            "no overlap of both sensors: factor 1 (not harmonized)")


def info(cal: pd.DataFrame, source: str) -> dict:
    table = cal.replace({np.nan: None}).round(4).to_dict("records")
    return dict(INFO, params={"target_level": "VIIRS", "viirs_start": VIIRS_START, "cell_deg": CELL_DEG,
                              "window_days": WINDOW_DAYS, "factor_source": source}, calibration=table)


def weights_sql(src: str, cal: str, viirs_days: str, by_month: bool) -> str:
    """Harmonization per hotspot in SQL (see the module docstring):
         VIIRS on VIIRS days (from VIIRS_START):    weight = 1,          frp_h = frp
         MODIS on days without VIIRS:               weight = factor_n,   frp_h = frp * factor_frp
         everything else:                           weight = 0,          frp_h = 0
       _lo / _hi use the 25th / 75th percentile factors; weight_cal / frp_cal = MODIS x factor on every day.
       `cal` is a table with region[, month] + FACTORS; regions without a row use the 'global' row."""
    key = "c.region = h.region" + (" AND c.month = CAST(substr(h.acq_date, 6, 2) AS INTEGER)" if by_month else "")
    fb = lambda f: f"coalesce(c.{f}, (SELECT {f} FROM {cal} WHERE region = 'global' LIMIT 1), (SELECT median({f}) FROM {cal}))"
    return f"""
      WITH h AS (SELECT *, {REGION_SQL} AS region FROM {src}),
      j AS (SELECT h.*, {", ".join(f"{fb(f)} AS {f}" for f in FACTORS)},
                   h.acq_date >= '{VIIRS_START}' AND h.acq_date IN (SELECT acq_date FROM {viirs_days}) AS use_viirs
            FROM h LEFT JOIN {cal} c ON {key})
      SELECT * EXCLUDE ({", ".join(FACTORS)}, use_viirs),
        (CASE WHEN sensor = 'VIIRS' AND use_viirs THEN 1 WHEN sensor <> 'VIIRS' AND NOT use_viirs THEN factor_n ELSE 0 END)::FLOAT AS weight,
        (CASE WHEN sensor = 'VIIRS' AND use_viirs THEN 1 WHEN sensor <> 'VIIRS' AND NOT use_viirs THEN factor_n_p25 ELSE 0 END)::FLOAT AS weight_lo,
        (CASE WHEN sensor = 'VIIRS' AND use_viirs THEN 1 WHEN sensor <> 'VIIRS' AND NOT use_viirs THEN factor_n_p75 ELSE 0 END)::FLOAT AS weight_hi,
        (CASE WHEN sensor = 'VIIRS' AND use_viirs THEN frp WHEN sensor <> 'VIIRS' AND NOT use_viirs THEN frp * factor_frp ELSE 0 END)::FLOAT AS frp_h,
        (CASE WHEN sensor = 'VIIRS' AND use_viirs THEN frp WHEN sensor <> 'VIIRS' AND NOT use_viirs THEN frp * factor_frp_p25 ELSE 0 END)::FLOAT AS frp_h_lo,
        (CASE WHEN sensor = 'VIIRS' AND use_viirs THEN frp WHEN sensor <> 'VIIRS' AND NOT use_viirs THEN frp * factor_frp_p75 ELSE 0 END)::FLOAT AS frp_h_hi,
        (CASE WHEN sensor <> 'VIIRS' THEN factor_n ELSE 0 END)::FLOAT AS weight_cal,
        (CASE WHEN sensor <> 'VIIRS' THEN frp * factor_frp ELSE 0 END)::FLOAT AS frp_cal
      FROM j"""
