"""
Canonical hotspot schema and FIRMS file normalization.

Every hotspot, no matter whether it comes from MODIS or VIIRS, an NRT file, an archive file or the
FIRMS API, is turned into ONE row with these columns (the "canonical schema"):

    column       type     example        notes
    -----------  -------  -------------  ---------------------------------------------------------
    latitude     float    -12.3456       pixel centre, 4-5 decimals
    longitude    float    -55.1234
    acq_date     str      2026-11-07     UTC, ISO date
    acq_time     int      1340           UTC, HHMM
    satellite    str      Aqua           Terra | Aqua | N (S-NPP) | N20 | N21 | ...
    sensor       str      MODIS          MODIS | VIIRS
    confidence   str      57 / n         raw FIRMS value (MODIS 0-100, VIIRS l/n/h)
    conf_class   str      nominal        low | nominal | high (MODIS: <30 low, 30-79 nominal, >=80 high)
    frp          float    13.67          fire radiative power, MW (sensor-specific, NOT harmonized)
    daynight     str      D              D | N
    type         Int8     0              0 vegetation, 1 volcano, 2 static land source, 3 offshore;
                                         only in archive files, otherwise empty
    version      str      6.1NRT
    source       str      NRT            NRT | SP (standard processing / archive)

The harmonization step adds two more columns (see harmonization.py):

    weight       float    contribution of this hotspot to the harmonized hotspot count
    frp_h        float    contribution of this hotspot to the harmonized FRP (MW)
"""
from __future__ import annotations

import pandas as pd

CANONICAL = ["latitude", "longitude", "acq_date", "acq_time", "satellite", "sensor", "confidence",
             "conf_class", "frp", "daynight", "type", "version", "source"]
HARMONIZED = CANONICAL + ["weight", "frp_h"]
DEDUP_KEY = ["latitude", "longitude", "acq_date", "acq_time", "satellite"]


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
    """Turn a raw FIRMS table (CSV from NRT, archive or API) into the canonical schema."""
    if df is None or df.empty:
        return pd.DataFrame({c: pd.Series(dtype="object") for c in CANONICAL})
    d = df.copy()
    d.columns = [c.strip().lower() for c in d.columns]

    if "instrument" in d.columns:
        sensor = d["instrument"].astype(str).str.upper().map(lambda s: "VIIRS" if "VIIRS" in s else "MODIS")
    elif sensor_hint:
        sensor = pd.Series(sensor_hint.upper(), index=d.index)
    else:
        sensor = pd.Series("VIIRS" if "bright_ti4" in d.columns else "MODIS", index=d.index)

    out = pd.DataFrame({
        "latitude": pd.to_numeric(d["latitude"], errors="coerce").round(5),
        "longitude": pd.to_numeric(d["longitude"], errors="coerce").round(5),
        "acq_date": pd.to_datetime(d["acq_date"], errors="coerce").dt.strftime("%Y-%m-%d"),
        "acq_time": pd.to_numeric(d.get("acq_time", 0), errors="coerce").fillna(0).astype("int32"),
        "satellite": d.get("satellite", pd.Series("", index=d.index)).astype(str),
        "sensor": sensor,
        "confidence": d.get("confidence", pd.Series("", index=d.index)).astype(str),
        "frp": pd.to_numeric(d.get("frp", 0), errors="coerce").fillna(0.0).astype("float32"),
        "daynight": d.get("daynight", pd.Series("", index=d.index)).astype(str),
        "type": pd.to_numeric(d["type"], errors="coerce").astype("Int8") if "type" in d.columns
                else pd.Series(pd.NA, index=d.index, dtype="Int8"),
        "version": d.get("version", pd.Series("", index=d.index)).astype(str),
    })
    out["conf_class"] = _conf_class(out["sensor"], out["confidence"])
    out["source"] = out["version"].str.upper().str.contains("NRT").map({True: "NRT", False: "SP"})
    out = out.dropna(subset=["latitude", "longitude", "acq_date"])
    return out[CANONICAL].reset_index(drop=True)


def dedupe(df: pd.DataFrame) -> pd.DataFrame:
    """Remove duplicate detections. SP (archive) rows win over NRT rows of the same pixel."""
    if df.empty:
        return df
    order = df["source"].map({"SP": 0, "NRT": 1}).fillna(2)
    return (df.assign(_o=order).sort_values("_o").drop_duplicates(DEDUP_KEY, keep="first")
              .drop(columns="_o").sort_values(["acq_date", "acq_time"]).reset_index(drop=True))


def validate(df: pd.DataFrame, harmonized: bool = False) -> None:
    need = HARMONIZED if harmonized else CANONICAL
    missing = [c for c in need if c not in df.columns]
    if missing:
        raise ValueError(f"missing columns: {missing}. Expected schema: {need}")
    if harmonized:
        if df["weight"].isna().any() or df["frp_h"].isna().any():
            raise ValueError("weight / frp_h must not contain empty values (use 0 instead)")
        if (df["weight"] < 0).any():
            raise ValueError("weight must be >= 0")
    bad = set(df["sensor"].unique()) - {"MODIS", "VIIRS"}
    if bad:
        raise ValueError(f"unknown sensor values: {bad}")
