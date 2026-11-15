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


# =====================================================================================
#  ERA CALIBRATION (data team, calibration_out of 14.11.2026, docs/calibration/calibration_report.md)
# =====================================================================================
# Target level: the reference VIIRS constellation (N20+N21). Factors per 0.5-degree cell x calendar month, with the
# fallback cell -> 5-degree block -> region -> global. Applied per hotspot (sensor_timeline.json -> apply):
#   VIIRS hotspot in a month of era e:            weight = factor_n_viirs_to_ref(e)       (1.0 in the reference era)
#   MODIS hotspot on a day without VIIRS data:    weight = factor_n_modis_to_ref          (orbit nominal, 2000-2021)
#                                                 weight = factor_n_modis_drift_to_ref    (orbit drift, 2022+)
#   FRP the same with the factor_frp_* columns. MODIS on days with VIIRS data counts 0 (no double counting).
ERA_DIR = os.path.join(DATA, "calibration_era")
ERA_FACTORS = os.path.join(ERA_DIR, "factors.parquet")
ERA_TIMELINE = os.path.join(ERA_DIR, "sensor_timeline.json")
_TL: dict = {}


def era_files() -> list[str]:
    return [ERA_FACTORS, ERA_TIMELINE] if os.path.exists(ERA_FACTORS) and os.path.exists(ERA_TIMELINE) else []


def era_setup(con) -> dict:
    """Load the era factors into DuckDB tables efm (MODIS -> reference) and efv (VIIRS era -> reference)."""
    import json
    f = ERA_FACTORS.replace("'", "''")
    con.execute(f"""CREATE OR REPLACE TABLE efm AS SELECT level, cell_id, month, m_n, m_n_lo, m_n_hi, m_frp, m_frp_lo, m_frp_hi, md_n, md_frp
                    FROM (SELECT *, row_number() OVER (PARTITION BY level, cell_id, month ORDER BY is_reference_era DESC) AS rn
                          FROM read_parquet('{f}')) WHERE rn = 1""")
    con.execute(f"CREATE OR REPLACE TABLE efv AS SELECT level, cell_id, month, era, v_n, v_frp FROM read_parquet('{f}')")
    tl = json.load(open(ERA_TIMELINE))
    _TL.clear()
    _TL.update(reference=tl.get("reference_era"), anchor=tl.get("anchor_era"), drift_years=set(tl.get("modis_drift_years") or []),
               months={(int(r["y"]), int(r["m"])): r for r in tl.get("months", [])}, raw=tl)
    return _TL


def _era_of(y: int, m: int) -> tuple[str, bool]:
    r = _TL["months"].get((y, m)) or {}
    era = r.get("era") if isinstance(r.get("era"), str) else _TL["reference"]
    drift = r.get("modis_orbit") == "drift" if r.get("modis_orbit") else y in _TL["drift_years"] or y >= 2022
    return era, drift


def era_weights_sql(src: str, viirs_days: str, y: int, m: int) -> str:
    era, drift = _era_of(y, m)
    lit = lambda s: "'" + str(s).replace("'", "''") + "'"
    mcols = ["m_n", "m_n_lo", "m_n_hi", "m_frp", "m_frp_lo", "m_frp_hi", "md_n", "md_frp"]
    co = lambda col, t: f"coalesce({t}c.{col}, {t}b.{col}, {t}r.{col}, {t}g.{col}, 1.0)"
    if drift:   # MODIS with drifting orbit: drift factor, band scaled like the nominal one
        mn, mn_lo, mn_hi = "f_md_n", "f_md_n * f_m_n_lo / f_m_n", "f_md_n * f_m_n_hi / f_m_n"
        mf, mf_lo, mf_hi = "f_md_frp", "f_md_frp * f_m_frp_lo / f_m_frp", "f_md_frp * f_m_frp_hi / f_m_frp"
    else:
        mn, mn_lo, mn_hi = "f_m_n", "f_m_n_lo", "f_m_n_hi"
        mf, mf_lo, mf_hi = "f_m_frp", "f_m_frp_lo", "f_m_frp_hi"
    return f"""
      WITH h AS (SELECT *, {REGION_SQL} AS region,
                 'c' || CAST(floor((latitude + 90) / 0.5) AS INTEGER) || '_' || CAST(floor((longitude + 180) / 0.5) AS INTEGER) AS _cid,
                 'b' || CAST(floor((latitude + 90) / 5) AS INTEGER) || '_' || CAST(floor((longitude + 180) / 5) AS INTEGER) AS _bid,
                 acq_date IN (SELECT acq_date FROM {viirs_days}) AS _uv
                 FROM {src}),
      mc AS (SELECT * FROM efm WHERE level = 'cell' AND month = {m}), mb AS (SELECT * FROM efm WHERE level = 'block' AND month = {m}),
      mr AS (SELECT * FROM efm WHERE level = 'region' AND month = {m}), mg AS (SELECT * FROM efm WHERE level = 'global' AND month = {m}),
      vc AS (SELECT * FROM efv WHERE level = 'cell' AND month = {m} AND era = {lit(era)}), vb AS (SELECT * FROM efv WHERE level = 'block' AND month = {m} AND era = {lit(era)}),
      vr AS (SELECT * FROM efv WHERE level = 'region' AND month = {m} AND era = {lit(era)}), vg AS (SELECT * FROM efv WHERE level = 'global' AND month = {m} AND era = {lit(era)}),
      j AS (SELECT h.*, {", ".join(f"{co(c, 'm')} AS f_{c}" for c in mcols)},
                   {co('v_n', 'v')} AS f_v_n, {co('v_frp', 'v')} AS f_v_frp
            FROM h LEFT JOIN mc ON mc.cell_id = h._cid LEFT JOIN mb ON mb.cell_id = h._bid LEFT JOIN mr ON mr.cell_id = h.region
                   LEFT JOIN mg ON TRUE
                   LEFT JOIN vc ON vc.cell_id = h._cid LEFT JOIN vb ON vb.cell_id = h._bid LEFT JOIN vr ON vr.cell_id = h.region
                   LEFT JOIN vg ON TRUE)
      SELECT * EXCLUDE (_cid, _bid, _uv, {", ".join('f_' + c for c in mcols)}, f_v_n, f_v_frp),
        (CASE WHEN sensor = 'VIIRS' AND _uv THEN f_v_n WHEN sensor <> 'VIIRS' AND NOT _uv THEN {mn} ELSE 0 END)::FLOAT AS weight,
        (CASE WHEN sensor = 'VIIRS' AND _uv THEN f_v_n WHEN sensor <> 'VIIRS' AND NOT _uv THEN {mn_lo} ELSE 0 END)::FLOAT AS weight_lo,
        (CASE WHEN sensor = 'VIIRS' AND _uv THEN f_v_n WHEN sensor <> 'VIIRS' AND NOT _uv THEN {mn_hi} ELSE 0 END)::FLOAT AS weight_hi,
        (CASE WHEN sensor = 'VIIRS' AND _uv THEN frp * f_v_frp WHEN sensor <> 'VIIRS' AND NOT _uv THEN frp * {mf} ELSE 0 END)::FLOAT AS frp_h,
        (CASE WHEN sensor = 'VIIRS' AND _uv THEN frp * f_v_frp WHEN sensor <> 'VIIRS' AND NOT _uv THEN frp * {mf_lo} ELSE 0 END)::FLOAT AS frp_h_lo,
        (CASE WHEN sensor = 'VIIRS' AND _uv THEN frp * f_v_frp WHEN sensor <> 'VIIRS' AND NOT _uv THEN frp * {mf_hi} ELSE 0 END)::FLOAT AS frp_h_hi,
        (CASE WHEN sensor <> 'VIIRS' THEN {mn} ELSE 0 END)::FLOAT AS weight_cal,
        (CASE WHEN sensor <> 'VIIRS' THEN frp * {mf} ELSE 0 END)::FLOAT AS frp_cal
      FROM j"""


def era_info(con, first_viirs: str | None) -> dict:
    """Method description and the factor table per region for the app (median over the 12 calendar months)."""
    reg = con.execute("""SELECT cell_id AS region, median(m_n), median(m_n_lo), median(m_n_hi), median(m_frp), median(m_frp_lo),
                                median(m_frp_hi), median(md_n), median(md_frp)
                         FROM efm WHERE level IN ('region', 'global') GROUP BY 1 ORDER BY (cell_id = 'global'), 1""").fetchall()
    v = {(r, e): (n, f) for r, e, n, f in con.execute("""SELECT cell_id, era, median(v_n), median(v_frp) FROM efv
                                                          WHERE level IN ('region', 'global') GROUP BY 1, 2""").fetchall()}
    anchor = _TL.get("anchor") or "N20"
    table = [{"region": r, "factor_n": round(a, 4), "factor_n_p25": round(b, 4), "factor_n_p75": round(c, 4),
              "factor_frp": round(d, 4), "factor_frp_p25": round(e, 4), "factor_frp_p75": round(f, 4),
              "factor_n_drift": round(g, 4), "factor_frp_drift": round(h, 4),
              "viirs_n_to_ref": round(v.get((r, anchor), (1, 1))[0], 4), "viirs_frp_to_ref": round(v.get((r, anchor), (1, 1))[1], 4),
              "r_log_frp": None, "n_cells": None, "used_global_fallback": False} for r, a, b, c, d, e, f, g, h in reg]
    bt = os.path.join(os.path.dirname(DATA), "docs", "calibration", "calibration_backtest.csv")
    backtest = None
    if os.path.exists(bt):
        b = pd.read_csv(bt)
        if {"metric", "region", "era", "bias_log", "abs_err_median"} <= set(b.columns):
            backtest = b[b.region.isin(["alle", "global"])][["metric", "era", "bias_log", "abs_err_median"]].round(3).to_dict("records")
    eras = _TL["raw"].get("eras", [])
    return {
        "name": "era calibration (VIIRS N20+N21 level)", "version": "1.0", "status": "final",
        "description": ("Data team calibration: VIIRS of every era scaled to today's constellation "
                        f"({_TL['reference']}); MODIS (before VIIRS data) x factor per 0.5-degree cell and calendar month, "
                        f"chained over the anchor era {anchor} 2018-2021 because the MODIS orbits drift from 2022. "
                        "Fallback cell -> 5-degree block -> region -> global. Band: 80 % interval."),
        "params": {"target_level": "VIIRS", "target_constellation": _TL["reference"], "viirs_start": first_viirs or VIIRS_START,
                   "reference_era": _TL["reference"], "anchor_era": anchor, "cell_deg": 0.5, "block_deg": 5,
                   "eras": [{k: e.get(k) for k in ("era", "first", "last", "months_clean", "role")} for e in eras],
                   "modis_drift_years": sorted(_TL["drift_years"]), "band": "80 %",
                   "factor_source": "team calibration (calibration_out, 14.11.2026, docs/calibration/calibration_report.md)",
                   "backtest": backtest},
        "calibration": table,
    }
