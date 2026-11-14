# FireCal: Antwort des Frontends an Daten/Backend

> **Für wen:** den Agenten bzw. die Person, die Harmonisierung und Daten liefert.
> **Bezug:** euer `docs/HANDOFF_Frontend.md` (Stand 14.11.2026). Dieses Dokument sagt, was davon
> umgesetzt ist, **in welcher Form ihr Daten liefern könnt** und welche Fragen offen sind.
>
> **Kurzfassung:** Liefert **Tabelle A** (Einzelpunkte) und **Tabelle C** (Kalibrierfaktoren). Alles andere,
> also Würfel, Fenster-Aggregate, Harmonisierung pro Gebiet, API und Website, erzeugt das Repo selbst.

Repo: https://github.com/Putskev/firecal · Live-Demo: https://putskev.github.io/firecal/ ·
API-Doku: https://putskev.github.io/firecal/api-docs.html

---

## 1. Was aus eurem Handoff umgesetzt ist

| Euer Punkt | Umsetzung |
|---|---|
| Harmonisierung auf **VIIRS-Niveau**: nativ VIIRS ab 20.01.2012, davor MODIS × Faktor | ja, `backend/harmonization.py` (gleiche Methode im Browser für die Demo) |
| Faktor je Region aus 2°-Zellen × 7-Tage-Fenster, nur Zellen mit beiden Sensoren, `conf_class = low` raus | ja, solange ihr keine Tabelle C liefert. **Nur Überlappungszeitraum** (ab VIIRS-Start) |
| Unsicherheit p25–p75 | ja: Band im Harmonisierungs-Check, Spanne in den Kennzahlen, Spalten im Statistik-CSV |
| Karte zeigt **Rohpunkte**, nie umgerechnet; Farbe nach Sensor/Konfidenz, Größe nach FRP | ja, ab Zoom 7 oder Modus „Points“; darunter Dichte |
| Statistik nur für **Gebiete** (Polygon/Rechteck/Lat-Lon) | ja |
| Low-Confidence für Statistik raus, optional einblendbar | ja (Filter „incl. low confidence“) |
| Median **ohne das aktuelle Jahr**, Anomalie absolut und in % | ja (gleicher Kalendermonat, alle anderen Jahre) |
| Zeitfenster unterschiedlicher Länge vergleichbar | Schalter „per day“ teilt durch die Tage je Periode |
| Unvollständiger letzter NRT-Tag | wird im Build automatisch entfernt (`drop_partial_last_day`) |
| Warnung bei < 30 Hotspots | ja |
| Umgerechnete Werte kennzeichnen | Schraffur in Kalender, Zeitleiste und Jahresvergleich; Anteil „from MODIS“ in den Kennzahlen |
| Kennzahlen: Anzahl, FRP, Nachtanteil, High-Confidence-Anteil | ja, plus Rohzahlen je Sensor |
| Jahresvergleich, Trend, Jahresmuster über 12 Monate | ja („Years compared“, „Seasonal pattern“) |
| Download Tabelle-A-Spalten + Statistik-CSV | ja |
| Transparenz: Datenstand, Version, Methode | Fußzeile + Kalibriertabelle in der App |
| Satelliten-Zeitleiste / Brüche markieren | Swimlanes je Satellit, gepunktete Linien 2012 / 2018 / 2023 |
| FRP als robustere Größe anbieten | Umschalter „Hotspots / FRP (MW)“ überall |

Zusätzlich (nach dem Vorbild VIS Stuttgart, VAST 2012): globale Zeitleiste mit Brushing, Filterleiste mit
Trefferzahl, Verteilungen (FRP, Ortszeit, Konfidenz, Satellit) mit Klick-Filtern, Raum-Zeit-Matrix,
automatisch formulierte Erkenntnisse.

---

## 2. Was ihr liefert und wohin

### 2a. Tabelle A (Einzelpunkte), Pflicht

Euer Schema aus Abschnitt 4 wird **unverändert** eingelesen (CSV, `.csv.gz` oder Parquet):

```bash
cp harmonisierte_hotspots.csv.gz data/incoming/      # oder mehrere Dateien, z. B. eine pro Jahr
python -m backend.ingest                              # -> data/raw/*.parquet (kanonisches Schema)
```

Genutzte Spalten: `sensor, satellite, datetime_utc, latitude, longitude, brightness_k, frp, pixel_km2,
conf_class, daynight, local_hour` (+ optional `type`, `version`). `scan_km/track_km/bright_t31_k` werden
toleriert, aber nicht gespeichert. Rohe FIRMS-CSVs gehen genauso.

Bitte beachten:
- `satellite`: `Terra`, `Aqua`, `SNPP`, `N20`, `N21` (FIRMS-Kürzel `N`, `1` werden umgesetzt).
- Für die volle Historie lieber **eine Datei pro Jahr** (GitHub-Limit 100 MB je Datei; Parquet bevorzugt).

### 2b. Tabelle C (Kalibrierung), empfohlen

```
data/calibration/calibration.csv
region,factor_frp,factor_n,factor_frp_p25,factor_frp_p75,r_log_frp,n_cells,used_global_fallback[,month][,factor_n_p25,factor_n_p75]
```

- Liegt die Datei da, werden **eure** Faktoren benutzt, sonst rechnet das Repo sie nach eurer Methode selbst.
- `month` (1–12) optional: dann Faktor je Region × Monat.
- Fehlt `factor_n_p25/p75`, wird das relative FRP-Band übernommen.
- Eine Zeile `region = global` dient als Rückfall für Regionen ohne Faktor.
- Regionsnamen müssen zu den Regionen passen (siehe 2c).

### 2c. Regionen

Aktuell **Platzhalter-Boxen** in `backend/regions.py` (und gleich in `index.html`, `regionOf`) mit euren
Namen `Afrika, Amerika, Asien, Europa/Westasien, Ozeanien`. Eure genauen Boxen kennen wir nicht.
Sobald `regions.geojson` kommt, wird `region_of()` durch eine Punkt-in-Polygon-Zuordnung ersetzt.

### 2d. Alternativen (falls eure Methode anders aussieht)

- **Eigene Funktion:** `harmonize(df) -> (df, info)` in `backend/harmonization.py` ersetzen. Ausgabe pro
  Hotspot: `region, weight, frp_h, weight_lo, weight_hi, frp_h_lo, frp_h_hi, weight_cal, frp_cal`
  (Bedeutung in `backend/data.py`). Harmonisierter Wert eines Gebiets = Summe von `weight` bzw. `frp_h`.
- **Fertige Tabelle:** Tabelle A + diese Spalten nach `data/harmonized/` (wird 1:1 genutzt).

**Nicht nötig** zu liefern: `fire_activity_*` (Tabelle B), `monthly_cube`. Die Aggregate (Monat × 0,1°,
Tag × 0,25°, mit Sensor, Satellit, Tag/Nacht, Konfidenz) baut `backend/build.py` selbst aus Tabelle A.

---

## 3. Antworten auf eure offenen Entscheidungen (Abschnitt 11)

| Frage | Entscheidung |
|---|---|
| Zellgröße des Würfels | 0,1° (Monat), 0,25° (Tag), im Repo gebaut |
| Wochen- oder Monatsfenster | Monat und Tag in der App; Wochenfenster nur intern für die Kalibrierung |
| FastAPI oder JSON | **FastAPI + DuckDB** (`backend/main.py`); GitHub Pages zeigt Demodaten |
| VIIRS-Rohpunkte nur 1–2 Jahre? | Nicht nötig: Punkte liegen nach Jahr/Monat partitioniert, die API liest nur den gewählten Zeitraum. Getestet mit 10 Mio. Punkten: 0,3 GB RAM. Grenze ist der Build-Schritt (pandas, ca. 0,4 GB RAM je Mio. Punkte) |
| Regionen | Platzhalter-Boxen, bitte `regions.geojson` liefern |
| Karte und Farben | Leaflet + Esri-Graukarte; Viridis (sequenziell), Okabe-Ito für Sensoren (MODIS orange, VIIRS blau) |

---

## 4. Beobachtungen aus euren Musterdaten (bitte prüfen)

1. **Faktoren reproduziert:** Mit eurer Methode auf der Woche 07.–13.11. kommen wir auf Afrika
   FRP 3,42 / Anzahl 10,97 (ihr: 3,6 / 11,14). Unterschied vermutlich durch Regionsgrenzen und den
   entfernten 14.11.
2. **Kalibrierung nur im Überlappungszeitraum:** Rechnet man über alle Wochen statt nur ab VIIRS-Start,
   fließen reine MODIS-Jahre ein und der Faktor wird zu klein. Bei der Methode „nur Zellen mit beiden
   Sensoren“ ist das automatisch erfüllt, bei gröberen Fenstern nicht.
3. **Kleine Zahlen verzerren den Faktor:** Werden nur Zellen-Wochen mit MODIS > 0 *und* VIIRS > 0 genutzt,
   wird MODIS bei sehr wenigen Hotspots je Zelle überschätzt und der Faktor zu klein. In unseren
   synthetischen Testdaten (wenige Punkte je Zelle) lag er bei 5 statt 9. Bei echten globalen Daten ist das
   gering, für kleine Regionen oder ruhige Monate aber relevant. Idee: größere Fenster oder Summen über
   alle Zellen-Wochen im Überlappungszeitraum.
4. **Satellitenzahl:** Mit dem Platzhalter steigt die harmonisierte Reihe 2018 (NOAA-20) und 2023 (NOAA-21),
   weil „nativ VIIRS“ alle aktiven VIIRS-Satelliten summiert. Eure geplante Korrektur je
   Satelliten-Kombination ist deshalb wichtig. Die App zeigt den Sprung im Satelliten-Swimlane und
   erwähnt ihn in den Erkenntnissen, wenn er in den Daten auftritt.

---

## 5. Was die Website von der API erwartet (nicht umbenennen)

Endpunkte (Doku: `/docs` lokal oder `api-docs.html`): `/api/meta`, `/api/timeseries`, `/api/heatmap`,
`/api/points`, `/api/distributions`, `/api/matrix`, `/api/satellites`, `/api/hotspots.csv`.
Gemeinsame Filter: `bbox, polygon, start, end, exclude_low, vegetation_only, satellites, daynight`.
**Satelliten- und Tag/Nacht-Filter wirken nur auf Rohwerte und Karte**; harmonisierte Werte nutzen immer
alle Satelliten, weil die Faktoren so definiert sind.

`/api/meta` muss `harmonization.params.target_level = "VIIRS"` enthalten (sonst Demo-Modus) und liefert
`harmonization.calibration` (Tabelle C) sowie `satellites` (Zeitleiste).

---

## 6. Abnahme

```bash
pip install -r requirements.txt
python -m backend.ingest                # eure Dateien aus data/incoming/
python -m backend.build --force         # baut data/processed/
python -m uvicorn backend.main:app      # http://localhost:8000
```

- [ ] `/api/meta`: richtiger Zeitraum, alle Satelliten mit Zeilen, `factor_source` = „team calibration table“
- [ ] Harmonisierungs-Check: „MODIS × factor“ liegt im Überlappungszeitraum nahe an VIIRS
- [ ] Kalender ohne Sprung 2012 (und, nach eurer Korrektur, ohne Sprung 2018 / 2023)
- [ ] `harmonization.py`: `status` auf `"final"`, Methodentext in `content/methodology.md`

## 7. Offene Fragen an euch

1. Genaue Grenzen eurer fünf Regionen (oder gleich `regions.geojson`)?
2. Wie sieht die Korrektur je Satelliten-Kombination aus: ein Faktor pro Kombination (z. B. `SNPP`,
   `SNPP+N20`, `SNPP+N20+N21`) als zusätzliche Spalte in Tabelle C?
3. Kommt `month` in Tabelle C, und wenn ja, mit Rückfall auf den Jahresfaktor bei wenigen Zellen?
4. Liefert ihr Standardprodukte (SP) für die Historie und NRT nur für die letzten Wochen?
