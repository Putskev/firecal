# Phnx: fire, on one scale

**NASA Space Apps Challenge 2026 · Challenge: Harmonization of MODIS and VIIRS Hot Spots**

Phnx is a web application that turns 26 years of satellite fire detections from two different sensors, MODIS and VIIRS, into one consistent **burning activity calendar**. Users pick any area (draw it, or type a country, region or city and the area follows its real borders) and any period, and see when and where it burned, how a month compares with the same month in all other years, what to expect for the rest of the year, and the single detections on a WebGL map.

* **Live app:** https://reicht.anohji.dev
* **Data:** about 363 million NASA FIRMS active fire detections, November 2000 to today
* **Calibration report:** [`docs/calibration/calibration_report.md`](docs/calibration/calibration_report.md)
* **Methodology (shown in the app):** [`content/methodology.md`](content/methodology.md)

## Why

MODIS (Terra since 2000, Aqua since 2002, 1 km pixels) holds the long record but is reaching the end of its mission. VIIRS (375 m pixels) sees many more small fires, and every new VIIRS satellite adds overpasses: NOAA-20 in 2018, NOAA-21 in 2023. Adding the raw detections up therefore makes fire activity jump in 2018 and double in 2023 without any more fire. Emergency managers, scientists and land managers need one series in which the sensor no longer matters.

## What it does

| | |
|---|---|
| **Projects** | Landing page with your own projects (kept in the browser, no accounts). A 4-step wizard: name, views (calendar, map, compare, statistics …), persistent filters (area, period, weekdays, local time of day, satellites, day / night, confidence, map style), and a check that tells how many hotspots the project holds and how the map will show them. |
| **Burning activity calendar** | Harmonized hotspots or fire radiative power (FRP) per month, quarter, ISO week or day. Months converted from MODIS are hatched. |
| **Forecast** | Seasonal ARIMA forecast of the harmonized monthly series until the end of the year, clearly marked in the calendar (dashed, hatched, "≈"), and the expected value of the coming month in the key figures. |
| **Anomalies and trends** | Every month against the median of the same month in all other years, seasonal pattern, years compared with trend, automatic insights. |
| **Map** | WebGL (MapLibre GL + deck.gl). Up to 1,000 hotspots in view: every single detection with its details; up to 1,000,000: Gaussian kernels of the single detections; above: pre-aggregated density cubes. Points and kernels can be coloured by time to see how fires move. Satellite, street and topographic backgrounds, daily true-colour imagery from NASA GIBS. |
| **Compare** | Two periods side by side: table, curves, FRP classes, two synchronized maps on one colour scale. |
| **Statistics** | Fire intensity (FRP), local time of detection, confidence, satellites, space × time matrix, calibration factors, harmonization check, own charts (choose x-axis, value and chart type). |
| **Accessible colours** | Palette presets including two that are safe for colour-blind people (red-green: Okabe-Ito / viridis; all types: cividis). Dark mode. |
| **Downloads** | Hotspots of the selection as CSV (with the harmonization weight of every detection), statistics as CSV, every chart as PNG. |

## Harmonization

Target level: today's VIIRS constellation (NOAA-20 + NOAA-21). Today's values stay unchanged; everything else is scaled to them.

| Period | Harmonized value |
|---|---|
| VIIRS data, era NOAA-20 only (2018-04 to 2023-02) | VIIRS × f<sub>VIIRS</sub> (≈ 1.96 for counts, ≈ 2.13 for FRP) |
| VIIRS data, NOAA-20 + NOAA-21 (from 2023-03) | VIIRS × 1 |
| Before VIIRS data (2000-11 to 2018-03) | MODIS × f<sub>MODIS</sub>(0.5° cell, calendar month) |

f<sub>MODIS</sub> is chained over 2018–2021, when both MODIS orbits were still stable (Terra and Aqua drift from 2022: overpass time 2026 Terra −1.0 h, Aqua +1.8 h): (NOAA-20 / MODIS in 2018–2021) × (NOAA-20 + NOAA-21) / NOAA-20 in 2023–2026. Factors are pooled over all years of an era per 0.5° cell and calendar month, shrunk towards their parent level (cell → 5° block → region → global) and adjusted so that the totals of each parent are kept. Count and FRP have separate factors; the band is an 80 % interval. Single detections on the map are never converted.

**Validation:** leave-one-year-out backtest on 5° blocks × month: bias ≤ 0.03 (log scale), median error 16 % for counts and 21 % for FRP (MODIS → NOAA-20), 4 % / 8 % for NOAA-20 → NOAA-20 + NOAA-21. End to end, harmonized MODIS meets VIIRS within ±1.6 % in 2018–2021, and the series has no jump at the start of VIIRS (2018) or of NOAA-21 (2023). Forecast: the same model fitted without the last 12 months misses them by a median of 10.8 % (global) and 12.5 % (southern Africa).

## How it works

```
NASA FIRMS (MODIS C6.1, VIIRS 375 m) ── Parquet ──► backend/build.py (DuckDB, month by month)
     de-duplicate · harmonize (era calibration) · pre-aggregate: cells per month (0.1°) and day (0.25°),
     global series, histogram cells
                         │
                         ▼
backend/main.py  FastAPI + DuckDB over Parquet: series, density, counts, binary points, forecast
                         │
                         ▼
index.html  one page: MapLibre GL, deck.gl, Plotly
```

The archive (363 million rows, 5.7 GB Parquet) is processed out of core: the full build takes about 14 minutes on a 4-core server with 8 GB RAM, typical queries answer in 0.1–1 s.

### Run it locally

```bash
pip install -r requirements.txt
python -m uvicorn backend.main:app
# http://localhost:8000
```

The repository contains two weeks of real FIRMS data (November 2026) for a quick start. Point `FIRECAL_SOURCE` at a folder with more FIRMS Parquet files to use a longer archive. Without a backend (e.g. GitHub Pages) the page shows synthetic demo data.

## Data and resources

**NASA data**

* NASA FIRMS (Fire Information for Resource Management System), LANCE: MODIS Collection 6.1 active fires (Terra, Aqua, 1 km) and VIIRS 375 m active fires (NOAA-20, NOAA-21), standard products and near-real-time data. https://firms.modaps.eosdis.nasa.gov
* NASA GIBS / Worldview: daily true-colour imagery (MODIS Terra, Aqua; VIIRS NOAA-20, NOAA-21, S-NPP) and Blue Marble as map backgrounds. https://earthdata.nasa.gov/gibs

**Other data and tools**

* Map backgrounds: Esri World Imagery, World Imagery (Firefly), Street, Topographic, Light / Dark Gray Canvas, Boundaries and Places.
* Place search and borders: OpenStreetMap Nominatim (© OpenStreetMap contributors, ODbL).
* Software: Python, FastAPI, DuckDB, pandas, NumPy, PyArrow, statsmodels; MapLibre GL JS, deck.gl, Plotly.js, Papa Parse, marked; fonts: Inter Tight (Google Fonts).
* References: Giglio, Schroeder & Justice (2016), The collection 6 MODIS active fire detection algorithm and fire products, Remote Sensing of Environment 178; Schroeder et al. (2014), The New VIIRS 375 m active fire detection data product, Remote Sensing of Environment 143.

## Use of AI

AI tools were used as development aids. The idea, the approach to the challenge, the harmonization method and all product decisions come from the team.

* **Code:** large parts of the web application and the API (`index.html`, `backend/`, `deploy/`) were written with the help of Anthropic's Claude (a large language model, used as a coding assistant), following the team's specifications and reviewed and tested by the team. The calibration scripts on the data server were also developed with AI assistance.
* **Text:** documentation and the methodology text were drafted with AI assistance and checked by the team.
* **Images:** no AI-generated images, video or audio are used in the app. The Phnx logo was provided by the team.

## Team

Phnx team, NASA Space Apps Challenge 2026.

## Licence of the data

FIRMS data: NASA, open data. Please cite NASA FIRMS when you use exports from Phnx.
