"""
Canonical hotspot schema and normalization of all supported inputs.

Supported inputs (auto-detected by their columns):
  * raw FIRMS CSV (NRT, archive or API): latitude, longitude, acq_date, acq_time, satellite,
    instrument, confidence, frp, scan, track, brightness / bright_ti4, bright_t31 / bright_ti5, ...
  * "Tabelle A" from the data team (docs/HANDOFF_Frontend.md): sensor, satellite, datetime_utc,
    latitude, longitude, brightness_k, bright_t31_k, frp, pixel_km2, scan_km, track_km,
    conf_class, daynight, local_hour

Canonical schema (one row per hotspot):

    column        type     example       notes
    ------------  -------  ------------  -----------------------------------------------------------
    latitude      float    -12.34567     pixel centre (WGS 84)
    longitude     float    -55.12345
    acq_date      str      2026-11-07    UTC
    acq_time      int      1340          UTC, HHMM
    satellite     str      Aqua          Terra | Aqua | SNPP | N20 | N21
    sensor        str      MODIS         MODIS | VIIRS
    confidence    str      57 / n        raw FIRMS value ('' if unknown)
    conf_class    str      nominal       low | nominal | high (MODIS: <30, 30-79, >=80)
    frp           float    13.67         fire radiative power in MW (raw, sensor-specific)
    brightness_k  float    307.7         channel 21 (MODIS) / I-4 (VIIRS) brightness temperature, K
    pixel_km2     float    1.96          scan x track, pixel area at the ground
    local_hour    float    9.24          local solar time (UTC hour + longitude / 15), 0-24
    daynight      str      D             D | N
    type          Int8     0             0 vegetation, 1 volcano, 2 static land source, 3 offshore; archive only
    version       str      6.1NRT
    source        str      NRT           NRT | SP

The harmonization step (harmonization.py) adds per hotspot:
    weight, frp_h            contribution to the harmonized count / FRP (VIIRS level)
    weight_lo, weight_hi     same with the 25th / 75th percentile factor (uncertainty band)
    frp_h_lo, frp_h_hi
    weight_cal, frp_cal      MODIS x factor for every MODIS hotspot (also after VIIRS start), 0 for VIIRS
    region                   region used for the factor
"""
from __future__ import annotations

import numpy as np
import pandas as pd

CANONICAL = ["latitude", "longitude", "acq_date", "acq_time", "satellite", "sensor", "confidence",
             "conf_class", "frp", "brightness_k", "pixel_km2", "local_hour", "daynight", "type",
             "version", "source"]
HARMONIZED_EXTRA = ["region", "weight", "frp_h", "weight_lo", "weight_hi", "frp_h_lo", "frp_h_hi",
                    "weight_cal", "frp_cal"]
HARMONIZED = CANONICAL + HARMONIZED_EXTRA
DEDUP_KEY = ["latitude", "longitude", "acq_date", "acq_time", "satellite"]

SAT_NAMES = {"T": "Terra", "TERRA": "Terra", "A": "Aqua", "AQUA": "Aqua", "N": "SNPP", "NPP": "SNPP",
             "SNPP": "SNPP", "S-NPP": "SNPP", "SUOMI NPP": "SNPP", "1": "N20", "N20": "N20",
             "NOAA-20": "N20", "J1": "N20", "N21": "N21", "NOAA-21": "N21", "J2": "N21"}


def _col(d: pd.DataFrame, name: str, default=np.nan) -> pd.Series:
    return d[name] if name in d.columns else pd.Series(default, index=d.index)


def _conf_class(sensor: pd.Series, conf: pd.Series) -> pd.Series:
    num = pd.to_numeric(conf, errors="coerce")
    letter = conf.astype(str).str.strip().str.lower().str[:1]
    modis = pd.Series(pd.NA, index=conf.index, dtype="object")
    modis[num < 30] = "low"
    modis[(num >= 30) & (num < 80)] = "nominal"
    modis[num >= 80] = "high"
    viirs = letter.map({"l": "low", "n": "nominal", "h": "high"})
    return modis.where(sensor == "MODIS", viirs).fillna("nominal")


def normalize(df: pd.DataFrame, sensor_hint: str | None = None) -> pd.DataFrame:
    """Turn a raw FIRMS table or a 'Tabelle A' table into the canonical schema."""
    if df is None or df.empty:
        return pd.DataFrame({c: pd.Series(dtype="object") for c in CANONICAL})
    d = df.copy()
    d.columns = [c.strip().lower() for c in d.columns]

    # sensor
    if "sensor" in d.columns:
        sensor = d["sensor"].astype(str).str.upper().map(lambda s: "VIIRS" if "VIIRS" in s else "MODIS")
    elif "instrument" in d.columns:
        sensor = d["instrument"].astype(str).str.upper().map(lambda s: "VIIRS" if "VIIRS" in s else "MODIS")
    elif sensor_hint:
        sensor = pd.Series(sensor_hint.upper(), index=d.index)
    else:
        sensor = pd.Series("VIIRS" if "bright_ti4" in d.columns else "MODIS", index=d.index)

    # date and time
    if "datetime_utc" in d.columns:
        dt = pd.to_datetime(d["datetime_utc"], utc=True, errors="coerce")
        acq_date = dt.dt.strftime("%Y-%m-%d")
        acq_time = (dt.dt.hour * 100 + dt.dt.minute).fillna(0).astype("int32")
    else:
        acq_date = pd.to_datetime(d["acq_date"], errors="coerce").dt.strftime("%Y-%m-%d")
        acq_time = pd.to_numeric(_col(d, "acq_time", 0), errors="coerce").fillna(0).astype("int32")

    lat = pd.to_numeric(d["latitude"], errors="coerce")
    lon = pd.to_numeric(d["longitude"], errors="coerce")

    brightness = pd.to_numeric(d["brightness_k"] if "brightness_k" in d.columns else
                               d["bright_ti4"] if "bright_ti4" in d.columns else _col(d, "brightness"), errors="coerce")
    if "pixel_km2" in d.columns:
        pixel = pd.to_numeric(d["pixel_km2"], errors="coerce")
    else:
        scan = pd.to_numeric(_col(d, "scan_km", np.nan), errors="coerce").fillna(pd.to_numeric(_col(d, "scan"), errors="coerce"))
        track = pd.to_numeric(_col(d, "track_km", np.nan), errors="coerce").fillna(pd.to_numeric(_col(d, "track"), errors="coerce"))
        pixel = scan * track
    if "local_hour" in d.columns:
        local = pd.to_numeric(d["local_hour"], errors="coerce")
    else:
        local = ((acq_time // 100) + (acq_time % 100) / 60 + lon / 15) % 24

    sat_raw = _col(d, "satellite", "").astype(str).str.strip()
    sat = sat_raw.str.upper().map(SAT_NAMES).fillna(sat_raw)

    out = pd.DataFrame({
        "latitude": lat.round(5), "longitude": lon.round(5),
        "acq_date": acq_date, "acq_time": acq_time,
        "satellite": sat, "sensor": sensor,
        "confidence": _col(d, "confidence", "").astype(str).replace("nan", ""),
        "frp": pd.to_numeric(_col(d, "frp", 0), errors="coerce").fillna(0.0).astype("float32"),
        "brightness_k": brightness.astype("float32"),
        "pixel_km2": pixel.astype("float32"),
        "local_hour": local.astype("float32").round(2),
        "daynight": _col(d, "daynight", "").astype(str).str.upper().str[:1],
        "type": pd.to_numeric(d["type"], errors="coerce").astype("Int8") if "type" in d.columns
                else pd.Series(pd.NA, index=d.index, dtype="Int8"),
        "version": _col(d, "version", "").astype(str).replace("nan", ""),
    })
    if "conf_class" in d.columns:
        out["conf_class"] = d["conf_class"].astype(str).str.lower().where(
            d["conf_class"].astype(str).str.lower().isin(["low", "nominal", "high"]),
            _conf_class(out["sensor"], out["confidence"]))
    else:
        out["conf_class"] = _conf_class(out["sensor"], out["confidence"])
    if "source" in d.columns and d["source"].astype(str).isin(["NRT", "SP"]).all():
        out["source"] = d["source"].astype(str)
    else:
        out["source"] = np.where(out["version"].str.upper().str.contains("NRT") | (out["version"] == ""), "NRT", "SP")
    out = out.dropna(subset=["latitude", "longitude", "acq_date"])
    return out[CANONICAL].reset_index(drop=True)


def dedupe(df: pd.DataFrame) -> pd.DataFrame:
    """Remove duplicate detections. SP (archive) rows win over NRT rows of the same pixel."""
    if df.empty:
        return df
    order = df["source"].map({"SP": 0, "NRT": 1}).fillna(2)
    return (df.assign(_o=order).sort_values("_o").drop_duplicates(DEDUP_KEY, keep="first")
              .drop(columns="_o").sort_values(["acq_date", "acq_time"]).reset_index(drop=True))


def drop_partial_last_day(df: pd.DataFrame) -> tuple[pd.DataFrame, str | None]:
    """The newest day of an NRT download is usually incomplete: drop it if it only has NRT rows."""
    if df.empty:
        return df, None
    last = df["acq_date"].max()
    if (df.loc[df["acq_date"] == last, "source"] == "NRT").all():
        return df[df["acq_date"] < last].reset_index(drop=True), last
    return df, None


def validate(df: pd.DataFrame, harmonized: bool = False) -> None:
    need = HARMONIZED if harmonized else CANONICAL
    missing = [c for c in need if c not in df.columns]
    if missing:
        raise ValueError(f"missing columns: {missing}. Expected schema: {need}")
    if harmonized:
        num = ["weight", "frp_h", "weight_lo", "weight_hi", "frp_h_lo", "frp_h_hi", "weight_cal", "frp_cal"]
        if df[num].isna().any().any():
            raise ValueError("harmonized columns must not contain empty values (use 0 instead)")
        if (df[num] < 0).any().any():
            raise ValueError("harmonized columns must be >= 0")
    bad = set(df["sensor"].unique()) - {"MODIS", "VIIRS"}
    if bad:
        raise ValueError(f"unknown sensor values: {bad}")
