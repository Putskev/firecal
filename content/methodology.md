# Methodology

## Data

Active fire detections from NASA FIRMS (Fire Information for Resource Management System):

* **MODIS** Collection 6.1, Terra (from November 2000) and Aqua (from July 2002), 1 km pixels.
* **VIIRS** 375 m, NOAA-20 (from April 2018 in our archive) and NOAA-21 (from March 2023). S-NPP (VIIRS since January 2012) is not yet in the archive.
* About 363 million detections, 2000-11-01 to today. Standard (science-quality) products up to July 2026, near-real-time data after that.

Preparation: one schema for both sensors, duplicates removed (same satellite, time and position; the standard product wins over near-real-time data), the last, incomplete day of a download dropped. Confidence classes: MODIS 0–29 low, 30–79 nominal, 80–100 high; VIIRS l / n / h.

## Harmonization of MODIS and VIIRS

The two sensors cannot be added up: VIIRS has smaller pixels and sees many small fires that MODIS misses, and every new VIIRS satellite adds overpasses. Raw counts therefore jump in 2018 and 2023 without more fire.

**Target level:** today's VIIRS constellation (NOAA-20 + NOAA-21). Today's values stay unchanged; everything else is scaled to them.

| Period | Harmonized value |
|---|---|
| Days with VIIRS data, era with NOAA-20 only (2018-04 to 2023-02) | VIIRS × f<sub>VIIRS</sub> (≈ 1.96 for counts, ≈ 2.13 for FRP) |
| Days with VIIRS data, NOAA-20 + NOAA-21 (from 2023-03) | VIIRS × 1 |
| Before VIIRS data (2000-11 to 2018-03) | MODIS × f<sub>MODIS</sub>(0.5° cell, calendar month) |

* **f<sub>VIIRS</sub>** is measured directly in 2023–2026: (NOAA-20 + NOAA-21) / NOAA-20.
* **f<sub>MODIS</sub>** is chained: (NOAA-20 / MODIS in 2018–2021) × f<sub>VIIRS</sub>. The chain is needed because both MODIS orbits drift from 2022 (overpass time 2026: Terra −1.0 h, Aqua +1.8 h), so MODIS is no stable yardstick in the reference era. 2018–2021 has the same orbits and product version as the whole MODIS history.
* Factors are ratios of sums, pooled over all years of an era per 0.5° cell and calendar month. Small cells are shrunk towards their parent level (cell → 5° block → region → global, weight w = s / (s + 200), s = smaller of the two sensor counts) and adjusted so that the totals of each parent are kept.
* Count and FRP have separate factors. Low-confidence detections are not used for the calibration; the factors are applied to all detections.
* MODIS detections on days with VIIRS data count 0 (no double counting). MODIS × factor is still shown in the harmonization check.
* Uncertainty band: 80 % interval from the year-to-year spread of the ratio plus counting noise.
* Single detections on the map are never converted.

## Validation

Leave-one-year-out backtest: for every 5° block, calendar month and era one year is left out, the factor is estimated from the other years, and the VIIRS sum is predicted from the MODIS sum (log scale; 0.16 ≈ 16 %).

| Step | Bias (count / FRP) | Median error (count / FRP) |
|---|---|---|
| MODIS → NOAA-20 (anchor 2018–2021) | +0.013 / −0.009 | 0.16 / 0.21 |
| NOAA-20 → NOAA-20 + NOAA-21 | +0.001 / +0.001 | 0.04 / 0.08 |
| MODIS → NOAA-20 + NOAA-21 directly (drifting orbits) | +0.017 / +0.001 | 0.18 / 0.26 |

End to end, with the factors applied like in the app: in 2018–2021 the harmonized series from MODIS meets the one from VIIRS within ±1.6 % (yearly medians), and there is no jump when NOAA-21 starts in March 2023.

Before harmonization, raw VIIRS / MODIS was about 4.4 (one VIIRS satellite) and 8.8 (two satellites) for counts. The single-year error of a 5° block is ±15–20 % for counts and about ±25 % for FRP; larger areas and longer periods are more accurate.

## Burning activity calendar and anomalies

* **Calendar:** harmonized hotspots (or FRP) per month or day in the selected area, colour on a sequential scale (95th percentile = darkest), months converted from MODIS hatched.
* **Anomaly:** each month compared with the median of the same calendar month in all other years (its own year excluded). Daily view: compared with the other days of the selection.
* **Seasonal pattern:** median and middle 50 % of each calendar month over all other years, with the latest year on top.
* **Years compared:** total per year with a linear trend over complete years.
* Area filters work on the cell centre of the pre-aggregated cells (0.1° per month, 0.25° per day); weekday and local-hour filters use the single detections.

## Limitations

* MODIS from 2022 drifts; for those years the app uses VIIRS, MODIS × drift factor is only an average over 2023–2026.
* Regions are coarse boxes (Americas, Africa, Asia, Europe / West Asia, Oceania), used only as a fallback level.
* Hotspots are detections, not fires: one fire can produce many detections, clouds and overpass times hide others, and static sources (e.g. gas flares) are counted as well.
* One factor per cell and calendar month cannot follow changes of fire behaviour inside a month or between day and night.

## References

* NASA FIRMS, Fire Information for Resource Management System: https://firms.modaps.eosdis.nasa.gov
* Giglio, L., Schroeder, W., Justice, C. O. (2016): The collection 6 MODIS active fire detection algorithm and fire products. Remote Sensing of Environment 178, 31–41.
* Schroeder, W., Oliva, P., Giglio, L., Csiszar, I. A. (2014): The New VIIRS 375 m active fire detection data product. Remote Sensing of Environment 143, 85–96.
* Calibration report of the data team: docs/calibration/calibration_report.md in the repository.
