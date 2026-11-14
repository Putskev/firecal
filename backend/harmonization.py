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

To plug in a different method, keep the signature of `harmonize(df) -> (df, info)` and the
output columns listed in data.py (HARMONIZED_EXTRA).
"""
from __future__ import annotations

import glob
import os

import numpy as np
import pandas as pd

from .paths import DATA
from .regions import REGIONS, VIIRS_START, region_of

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


def _calibrate(d: pd.DataFrame) -> pd.DataFrame:
    """Compute the calibration table (Tabelle C) from data with both sensors."""
    g = d[(d["conf_class"] != "low") & (d["acq_date"] >= VIIRS_START)].copy()   # overlap period only
    g["cell"] = (np.floor(g.latitude / CELL_DEG).astype(int).astype(str) + "_" +
                 np.floor(g.longitude / CELL_DEG).astype(int).astype(str))
    day = pd.to_datetime(g["acq_date"])
    g["win"] = ((day - day.min()).dt.days // WINDOW_DAYS).astype(int)
    agg = g.groupby(["region", "cell", "win", "sensor"]).agg(n=("frp", "size"), frp=("frp", "sum")).unstack("sensor")
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
                "factor_n": vn.sum() / mn.sum(),
                "factor_frp_p25": rf.quantile(.25), "factor_frp_p75": rf.quantile(.75),
                "factor_n_p25": rn.quantile(.25), "factor_n_p75": rn.quantile(.75),
                "r_log_frp": r, "n_cells": int(len(part)), "used_global_fallback": fallback}

    glob_row = row("global", both, False)
    for reg in REGIONS:
        part = both[both["region"] == reg]
        if len(part) >= MIN_CELLS:
            rows.append(row(reg, part, False))
        else:
            rows.append({**glob_row, "region": reg, "n_cells": int(len(part)), "used_global_fallback": True})
    rows.append(glob_row)
    return pd.DataFrame(rows)


def calibration_files() -> list[str]:
    """Team calibration tables: data/calibration/*.csv|*.parquet and calibration* files in FIRECAL_SOURCE."""
    from .sources import calibration_files as external
    return sorted(glob.glob(os.path.join(CALIBRATION_DIR, "*.csv")) + glob.glob(os.path.join(CALIBRATION_DIR, "*.parquet"))) + external()


def _load_calibration() -> pd.DataFrame | None:
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


def harmonize(df: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    d = df.copy()
    d["region"] = region_of(d["latitude"].to_numpy(), d["longitude"].to_numpy())
    cal = _load_calibration()
    source = "team calibration table (" + ", ".join(os.path.basename(f) for f in calibration_files()) + ")"
    if cal is None:
        cal = _calibrate(d)
        source = "computed from the loaded data"
    if cal.empty:
        cal = pd.DataFrame([{"region": r, "factor_n": 1.0, "factor_frp": 1.0, "factor_frp_p25": 1.0,
                             "factor_frp_p75": 1.0, "factor_n_p25": 1.0, "factor_n_p75": 1.0,
                             "r_log_frp": np.nan, "n_cells": 0, "used_global_fallback": True} for r in REGIONS])
        source = "no overlap of both sensors: factor 1 (not harmonized)"

    # factor lookup per hotspot (by region, optionally by month)
    keys = ["region"] + (["month"] if "month" in cal.columns else [])
    if "month" in cal.columns:
        d["month"] = d["acq_date"].str[5:7].astype(int)
    cols = ["factor_n", "factor_frp", "factor_n_p25", "factor_n_p75", "factor_frp_p25", "factor_frp_p75"]
    d = d.merge(cal[keys + cols].drop_duplicates(keys), on=keys, how="left")
    glob_row = cal[cal["region"] == "global"]
    fallback = glob_row.iloc[0] if len(glob_row) else cal[cols].median()
    for c in cols:
        d[c] = d[c].fillna(fallback[c])

    is_v = d["sensor"].eq("VIIRS")
    viirs_days = set(d.loc[is_v, "acq_date"].unique())
    use_viirs = (d["acq_date"] >= VIIRS_START) & d["acq_date"].isin(viirs_days)

    z = np.zeros(len(d), dtype="float32")
    d["weight"], d["weight_lo"], d["weight_hi"] = z.copy(), z.copy(), z.copy()
    d["frp_h"], d["frp_h_lo"], d["frp_h_hi"] = z.copy(), z.copy(), z.copy()
    v = is_v & use_viirs
    d.loc[v, ["weight", "weight_lo", "weight_hi"]] = 1.0
    for c in ["frp_h", "frp_h_lo", "frp_h_hi"]:
        d.loc[v, c] = d.loc[v, "frp"]
    m = ~is_v & ~use_viirs
    d.loc[m, "weight"] = d.loc[m, "factor_n"]
    d.loc[m, "weight_lo"] = d.loc[m, "factor_n_p25"]
    d.loc[m, "weight_hi"] = d.loc[m, "factor_n_p75"]
    d.loc[m, "frp_h"] = d.loc[m, "frp"] * d.loc[m, "factor_frp"]
    d.loc[m, "frp_h_lo"] = d.loc[m, "frp"] * d.loc[m, "factor_frp_p25"]
    d.loc[m, "frp_h_hi"] = d.loc[m, "frp"] * d.loc[m, "factor_frp_p75"]
    d["weight_cal"] = np.where(~is_v, d["factor_n"], 0.0)
    d["frp_cal"] = np.where(~is_v, d["frp"] * d["factor_frp"], 0.0)
    for c in ["weight", "weight_lo", "weight_hi", "frp_h", "frp_h_lo", "frp_h_hi", "weight_cal", "frp_cal"]:
        d[c] = d[c].astype("float32")
    d = d.drop(columns=cols + (["month"] if "month" in d.columns and "month" not in df.columns else []))

    table = cal.replace({np.nan: None}).round(4).to_dict("records")
    info = dict(INFO, params={
        "target_level": "VIIRS", "viirs_start": VIIRS_START, "cell_deg": CELL_DEG,
        "window_days": WINDOW_DAYS, "factor_source": source,
        "calibrated_share": round(float((d["weight"][~is_v] > 0).mean()) if (~is_v).any() else 0.0, 4),
    }, calibration=table)
    return d, info
