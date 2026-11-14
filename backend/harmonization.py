"""
=====================================================================================
  HARMONIZATION SLOT  -  replace the function `harmonize()` with the real method.
=====================================================================================

Contract
--------
Input  : pandas DataFrame in the canonical schema (see data.py), all sensors and satellites,
         deduplicated. One row per hotspot.
Output : (DataFrame, info)
         DataFrame = the same rows (you may drop rows, e.g. false alarms) plus two columns:
             weight  float >= 0  how much this hotspot counts in the harmonized hotspot count
                                 (MODIS-equivalent). Harmonized count of an area/period =
                                 SUM(weight) over its hotspots.
             frp_h   float >= 0  harmonized FRP contribution in MW.
                                 Harmonized FRP of an area/period = SUM(frp_h).
         info      = dict shown in the web app (name, version, status, description, params).

Why per-hotspot weights? Every view in the app (calendar, anomaly, map, CSV export, any area
the user draws) is just a SUM over hotspots, so one number per hotspot is enough to make all
of them harmonized at once.

Examples of what a real method can do with this contract:
  * scale VIIRS by a factor that depends on region / land cover / month  -> weight = k(cell, month)
  * blend both sensors during the overlap period                          -> MODIS 0.5, VIIRS 0.5*k
  * remove persistent industrial sources                                  -> drop those rows
If your method produces gridded totals instead of per-hotspot values, you can still fit it in:
give each hotspot weight = harmonized_cell_total / raw_cell_count.

Alternative without touching this file: put a finished table (canonical columns + weight +
frp_h) as CSV or Parquet into data/harmonized/. It is then used as-is (see build.py), and an
optional data/harmonized/harmonization.json provides the `info` dict.

-------------------------------------------------------------------------------------
PLACEHOLDER METHOD (used until the real one is in):
  k = sum(MODIS) / sum(VIIRS) over months in which both sensors detected fires
      (low-confidence detections excluded). From the first VIIRS month on, the harmonized
      series is VIIRS * k ("MODIS-equivalent"); before that it is MODIS.
-------------------------------------------------------------------------------------
"""
from __future__ import annotations

import pandas as pd

INFO = {
    "name": "placeholder-ratio",
    "version": "0.1",
    "status": "placeholder",          # set to "final" for the real method
    "description": "Global ratio of monthly MODIS / VIIRS sums over overlap months. "
                   "Placeholder until the team's harmonization is added.",
}


def harmonize(df: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    d = df.copy()
    month = d["acq_date"].str[:7]
    is_v = d["sensor"].eq("VIIRS")
    good = d["conf_class"].ne("low")

    g = (d[good].assign(month=month[good])
         .groupby(["month", "sensor"]).agg(n=("frp", "size"), frp=("frp", "sum"))
         .unstack("sensor", fill_value=0))
    n_m = g[("n", "MODIS")] if ("n", "MODIS") in g else pd.Series(0, index=g.index)
    n_v = g[("n", "VIIRS")] if ("n", "VIIRS") in g else pd.Series(0, index=g.index)
    f_m = g[("frp", "MODIS")] if ("frp", "MODIS") in g else pd.Series(0.0, index=g.index)
    f_v = g[("frp", "VIIRS")] if ("frp", "VIIRS") in g else pd.Series(0.0, index=g.index)

    both = (n_m > 0) & (n_v > 0)
    k_n = float(n_m[both].sum() / n_v[both].sum()) if both.any() else 1.0
    k_f = float(f_m[both].sum() / f_v[both].sum()) if both.any() and f_v[both].sum() > 0 else 1.0

    # month uses VIIRS if VIIRS exists by then and saw something (or MODIS saw nothing)
    viirs_start = month[is_v].min() if is_v.any() else None
    all_n_v = d[is_v].groupby(month[is_v]).size()
    all_n_m = d[~is_v].groupby(month[~is_v]).size()
    months = pd.Index(month.unique())
    use_v = pd.Series(False, index=months)
    if viirs_start is not None:
        nv = all_n_v.reindex(months, fill_value=0)
        nm = all_n_m.reindex(months, fill_value=0)
        use_v = (months >= viirs_start) & ((nv > 0) | (nm == 0))
        use_v = pd.Series(use_v, index=months)
    use = month.map(use_v).astype(bool)

    d["weight"] = 0.0
    d["frp_h"] = 0.0
    d.loc[is_v & use, "weight"] = k_n
    d.loc[is_v & use, "frp_h"] = d.loc[is_v & use, "frp"] * k_f
    d.loc[~is_v & ~use, "weight"] = 1.0
    d.loc[~is_v & ~use, "frp_h"] = d.loc[~is_v & ~use, "frp"]
    d["weight"] = d["weight"].astype("float32")
    d["frp_h"] = d["frp_h"].astype("float32")

    info = dict(INFO, params={
        "k_count": round(k_n, 5), "k_frp": round(k_f, 5),
        "overlap_months": int(both.sum()), "viirs_start": viirs_start,
    })
    return d, info
