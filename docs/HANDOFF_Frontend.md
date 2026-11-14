# Handoff an das Frontend: Harmonisierter Feuer-Aktivitätskalender (MODIS + VIIRS)

Stand: 14.11.2026. Von: Daten/Backend. An: Frontend-Agent.
Dieses Dokument ist in sich vollständig. Du hast keinen Zugriff auf den Rechner des Backends, nur auf die Dateien, die du von uns bekommst, und auf dieses Dokument.

**Status in einem Satz:** Das Datenschema steht und wurde an **Musterdaten** (eine Novemberwoche 2026) erprobt. Die echten Daten (ca. 2000 bis heute) liefern wir später im selben Schema. Alles, was in diesem Dokument "(geplant)" heißt, existiert noch nicht.

---

## 1. Hintergrund (die Aufgabe)

Hackathon-Aufgabe (NASA, Daten aus FIRMS): Satelliten erkennen aktive Feuer als **Hotspots** (Punkte mit Zeit, Ort und Feuerleistung). Zwei Sensorfamilien liefern sie:

- **MODIS** (Satelliten Terra und Aqua): Daten seit ca. 2000, Pixel ca. 1 km. Läuft aus.
- **VIIRS** (Satelliten SNPP seit 2012, NOAA-20 / "N20" seit 2018, NOAA-21 / "N21" seit 2023): Pixel 375 m, empfindlicher. Ersetzt MODIS.

Die Zahlen beider Sensoren sind nicht direkt vergleichbar (andere Auflösung, Empfindlichkeit, Überflugzeiten). Unser Beitrag ist eine **harmonisierte Zeitreihe**, damit Einsatzkräfte sehen: Ist die Feueraktivität in meinem Gebiet im aktuellen Monat ungewöhnlich hoch gegenüber dem historischen Median?

**Zielgruppe:** Frühwarnung, Katastrophenschutz, Feuerwehr, Landmanager, Wissenschaft.

**Deine Aufgabe:** Die Web-App. Der Nutzer wählt ein Gebiet und einen Zeitraum und bekommt Karte, Brandkalender, Vergleich mit der Historie und einen CSV-Download.

### Begriffe
| Begriff | Bedeutung |
|---|---|
| Hotspot | Ein vom Satelliten erkanntes brennendes Pixel |
| FRP | Fire Radiative Power in Megawatt (MW), Maß für die Feuerleistung. Je größer, desto stärker |
| Konfidenz | Verlässlichkeit einer Erkennung. Bei uns vereinheitlicht auf `low` / `nominal` / `high` |
| NRT | Near Real Time, vorläufiges Echtzeitprodukt (noch nicht endgültig geprüft) |
| Überlappungszeitraum | Zeit, in der MODIS und VIIRS gleichzeitig Daten liefern (ab 2012). Daraus schätzen wir die Umrechnung |
| Harmonisierung | Umrechnung der MODIS-Werte auf das VIIRS-Niveau (oder umgekehrt), damit eine durchgehende Zeitreihe entsteht |

---

## 2. Kernprinzip (bitte beim Entwurf der UI beachten)

| Ebene | Was | Harmonisiert? |
|---|---|---|
| **Hotspot-Karte** | Die echten Einzelpunkte (VIIRS 375 m, MODIS 1 km) | **Nein.** Orte werden nie umgerechnet, nur nach Sensor beschriftet |
| **Kalender, Statistik, Anomalie** | Zählung und FRP im vom Nutzer gewählten **Gebiet** pro Monat/Jahr | **Ja**, über einen Faktor pro Region und Monat |

Warum: Die Umrechnung MODIS → VIIRS funktioniert nur für ganze Gebiete zuverlässig. In unseren Mustern stimmen die Sensoren bei 2-km-Zellen kaum überein (Korrelation 0,29), bei 25 km mittel (0,61) und bei 2° gut (0,83). Die Statistik gilt deshalb für ein **Gebiet**, nicht für einzelne kleine Zellen. Zeige Zellwerte also nie als exakte Zahlen.

---

## 3. Was du bekommst (Lieferumfang)

### 3a. Heute verfügbar (Musterdaten, zur Entwicklung)

Wir senden dir diese Dateien. Sie enthalten **nur die Zeit vom 01. bis 13.11.2026** und sind zum Testen des Schemas gedacht.

| Datei | Inhalt | Größe (Zeilen) |
|---|---|---|
| `harmonisierte_hotspots.csv.gz` | Einzelpunkte (Tabelle A), nur 07.–14.11.2026 | 869.190 |
| `fire_activity_7d_2deg.parquet` und `.csv` | Aggregierte Tabelle (Tabelle B), Wochenfenster, 2°-Zellen | 5.131 |
| `fire_activity_7d_0.5deg.parquet` / `.csv` | Dasselbe mit 0,5°-Zellen | 31.376 |
| `fire_activity_7d_2km.parquet` / `.csv` | Dasselbe mit 2-km-Zellen | 441.262 |
| `calibration_7d_2deg.csv` | Faktoren MODIS → VIIRS (nur Test) | 5 |

Empfehlung für die Entwicklung: `fire_activity_7d_2deg.parquet` (klein) für Karte und Diagramme, `harmonisierte_hotspots.csv.gz` für die Punktekarte.

**Wichtig:** Die Zahlen (Faktoren, Summen, Korrelationen) in diesen Mustern sind **nicht** repräsentativ. Zeig sie nie als echte Aussage an. Eine Woche im November sagt nichts über andere Monate. Die Werte sind nur für Layout und Logik da.

### 3b. Geplant (kommt mit den echten 20-Jahres-Daten)

| Artefakt | Inhalt | Zweck |
|---|---|---|
| `hotspots/` (Parquet, nach `year` und `month` aufgeteilt) | Tabelle A mit allen Punkten, nach `latitude`/`longitude` sortiert | Karte, CSV-Download, exakte Gebietsabfrage |
| `monthly_cube.parquet` | Pro Monat, Zelle (ca. 0,05–0,1°) und Sensor: `n`, `frp_sum`, `n_day`, `n_night`, `n_high_conf` | Schnelle Statistik und Kalender für große Gebiete |
| `calibration.parquet` | Faktor pro Region (oder Zelle), Monat und VIIRS-Satelliten-Kombination, mit Unsicherheit | Harmonisierung |
| `sensor_timeline.json` | Welcher Satellit wann aktiv war | Brüche in der Zeitreihe erkennen |
| `regions.geojson` | Länder und Provinzen, vereinfacht | Regionsauswahl |

Die genauen Spalten der geplanten Dateien stehen in Abschnitt 4. Sie können sich im Detail ändern. Änderungen am Schema melden wir und tragen sie hier nach.

**Dateiformat-Konventionen (gelten für alles):**
- Parquet (zstd) für große Tabellen, CSV (UTF-8, Komma, Dezimalpunkt) für kleine und für den Download.
- Zeit immer **UTC**, Format `YYYY-MM-DD` bzw. `YYYY-MM-DD HH:MM:SS+00:00`.
- Koordinaten WGS 84 (EPSG:4326), Grad. Längengrad -180 bis 180, Breitengrad -90 bis 90.
- Fehlende Werte sind leer (CSV) bzw. `null`/`NaN` (Parquet).

---

## 4. Schema (Spaltennamen, Typ, Bedeutung)

### Tabelle A: `hotspots` (Einzelpunkte)

| Spalte | Typ | Bedeutung | Werte / Wertebereich (Muster) |
|---|---|---|---|
| `sensor` | Text | Sensorfamilie | `MODIS`, `VIIRS` |
| `satellite` | Text | Satellit | `Terra`, `Aqua`, `N20`, `N21` (später auch `SNPP`) |
| `datetime_utc` | Zeitstempel (UTC) | Aufnahmezeit | z. B. `2026-11-07 00:28:00+00:00` |
| `latitude`, `longitude` | Float | Pixelmittelpunkt | -53 bis 72 / -180 bis 180 |
| `frp` | Float | Feuerleistung in MW | 0 bis ca. 5.083, meist 1–50 (Median MODIS ca. 16, VIIRS ca. 6) |
| `brightness_k` | Float | Helligkeitstemperatur Kanal 21 (MODIS) / I-4 (VIIRS), in Kelvin | 207 bis 500 |
| `bright_t31_k` | Float | Helligkeitstemperatur Kanal 31 / I-5, in Kelvin | 236 bis 400 |
| `pixel_km2` | Float | Pixelfläche (`scan_km` × `track_km`) | MODIS 1–9,6, VIIRS 0,14–0,62 |
| `scan_km`, `track_km` | Float | Pixelbreite/-höhe in km | |
| `conf_class` | Text | Konfidenz, vereinheitlicht | `low`, `nominal`, `high` |
| `daynight` | Text | Tag/Nacht | `D`, `N` |
| `local_hour` | Float | Lokale Sonnenzeit in Stunden (UTC-Stunde + Länge/15) | 0 bis 24 |
| `year`, `month` (geplant) | Int | Zum schnellen Filtern | |

**Nicht vergleichbar zwischen den Sensoren:** `brightness_k` (VIIRS sättigt bei 367 K, MODIS erst bei ca. 500 K). `conf_class` ist nur als Klasse vergleichbar. `frp` ist die vergleichbarste Größe.

**Beispielzeilen (CSV, echte Muster):**
```csv
sensor,satellite,datetime_utc,latitude,longitude,brightness_k,bright_t31_k,frp,pixel_km2,scan_km,track_km,conf_class,daynight,local_hour
MODIS,Terra,2026-11-07 00:28:00+00:00,33.2613,131.64961,307.73,289.01,15.28,1.959,1.58,1.24,low,D,9.24
MODIS,Aqua,2026-11-07 00:32:00+00:00,31.1354,49.33065,303.8,277.57,13.67,1.776,1.48,1.2,nominal,N,3.82
```
(Hinweis: Diese Datei enthält noch Punkte mit `conf_class = low`. In der Statistik filtern wir sie heraus, siehe Abschnitt 6.)

### Tabelle B: `fire_activity` (aggregiert pro Zeitfenster und Zelle)

Wird mit einem frei einstellbaren Zeitfenster (heute 7 Tage) und einer Rastergröße erzeugt. Die Spalten sind in allen Varianten gleich.

| Spalte | Typ | Bedeutung |
|---|---|---|
| `period_start`, `period_end` | Datum | Zeitfenster (Beginn/Ende, beide inklusive) |
| `n_days` | Int | Tage im Fenster (kürzer als 7 bei angeschnittenen Fenstern) |
| `cell_id` | Text | Zelle, z. B. `y35_x152` (Zeilen-/Spaltenindex im Raster) |
| `lat`, `lon` | Float | Zellmittelpunkt |
| `region` | Text | Grobe Region: `Afrika`, `Amerika`, `Asien`, `Europa/Westasien`, `Ozeanien`. **Platzhalter** (Längen-/Breitenbox), später echte Länder/Provinzen |
| `n_modis`, `frp_modis` | Float | Anzahl und FRP-Summe MODIS, **roh**. Leer = in dieser Zelle keine MODIS-Detektion |
| `n_viirs`, `frp_viirs` | Float | Anzahl und FRP-Summe VIIRS, **roh**. Leer = keine VIIRS-Detektion |
| `n_modis_calibrated`, `frp_modis_calibrated` | Float | MODIS × Faktor, also auf VIIRS-Niveau |
| `n_harmonized`, `frp_harmonized` | Float | **Die harmonisierte Größe**: nativer VIIRS-Wert ab dem VIIRS-Startdatum, davor `*_modis_calibrated` |
| `frp_harmonized_per_day` | Float | `frp_harmonized / n_days`, vergleichbar zwischen Fenstern |
| `factor_n`, `factor_frp` | Float | Verwendete Faktoren (VIIRS/MODIS) |
| `source` | Text | `VIIRS` oder `MODIS_calibrated` |
| `is_calibrated` | Bool | `true`, wenn der Wert aus MODIS umgerechnet wurde |

Zellen ohne irgendeine Detektion sind **nicht** in der Tabelle. Fehlend heißt also 0 Hotspots, nicht "keine Daten".

**Beispielzeile (CSV, 2°-Raster, echte Muster):**
```csv
period_start,period_end,n_days,cell_id,lat,lon,region,n_modis,frp_modis,n_viirs,frp_viirs,n_modis_calibrated,frp_modis_calibrated,n_harmonized,frp_harmonized,frp_harmonized_per_day,factor_n,factor_frp,source,is_calibrated
2026-11-01,2026-11-07,7,y49_x102,9.0,25.0,Afrika,1338.0,42229.31,12936.0,152622.75,14899.73,152026.52,12936.0,152622.75,21803.25,11.14,3.60,VIIRS,False
```
In den Mustern ist `source` überall `VIIRS`, weil alle Daten nach dem VIIRS-Startdatum liegen. Die Spalten `*_calibrated` zeigen trotzdem, wie die umgerechnete MODIS-Reihe aussieht.

### Tabelle C: `calibration`

| Spalte | Typ | Bedeutung |
|---|---|---|
| `region` | Text | Region |
| `factor_frp` | Float | Verhältnis VIIRS/MODIS der FRP-Summe in Zellen, in denen beide Sensoren etwas sehen |
| `factor_n` | Float | Dasselbe für die Anzahl der Hotspots |
| `factor_frp_p25`, `factor_frp_p75` | Float | Unsicherheitsband (25. bis 75. Perzentil der Zellverhältnisse) |
| `r_log_frp` | Float | Korrelation der Zellwerte (Güte, 0–1) |
| `n_cells` | Int | Anzahl Zellen, auf denen der Faktor beruht |
| `used_global_fallback` | Bool | `true`, wenn die Region zu wenig Zellen hatte und der globale Faktor genutzt wurde |

In der echten Version kommt `month` (1–12) und die VIIRS-Satelliten-Kombination dazu.

**Beispiel (Muster, nicht belastbar):**
```csv
region,factor_frp,factor_n,factor_frp_p25,factor_frp_p75,r_log_frp,n_cells,used_global_fallback
Afrika,3.6,11.136,3.067,10.082,0.881,786,False
Amerika,1.802,6.336,1.527,6.16,0.811,1002,False
Ozeanien,1.875,8.843,1.459,4.529,0.855,257,False
```

### Geplant: `monthly_cube`
`year`, `month`, `cell_id`, `lat`, `lon`, `sensor`, `n`, `frp_sum`, `n_day`, `n_night`, `n_high_conf`. Eine Zeile pro Monat × Zelle × Sensor. Bei Gebietsabfragen summierst du die Zellen, deren Mittelpunkt im Gebiet liegt.

---

## 5. Wie die Harmonisierung funktioniert (damit du die Zahlen richtig anzeigst)

1. Für jede Region (später Region × Monat) wurde aus Zellen, in denen **beide** Sensoren in derselben Woche etwas gesehen haben, der Faktor `factor = Summe(VIIRS) / Summe(MODIS)` bestimmt, getrennt für die FRP-Summe und die Anzahl.
2. **Vor dem VIIRS-Start** (Standard: 20.01.2012) gilt: `harmonized = MODIS × factor`. **Danach** gilt der native VIIRS-Wert. Die Spalte `source` und `is_calibrated` zeigen, welcher Fall vorliegt.
3. Ergebnis ist eine durchgehende Reihe "auf VIIRS-Niveau".

Was das für die UI bedeutet:
- Zeig für jeden Monat, ob der Wert **nativ** (VIIRS) oder **umgerechnet** (MODIS) ist, z. B. durch ein kleines Symbol oder eine andere Schraffur im Kalender.
- Zeig die Unsicherheit der umgerechneten Werte (`factor_frp_p25` bis `p75`, später Konfidenzintervall).
- Die **Anzahl** der Hotspots ist stark sensorabhängig (Faktor in den Mustern ca. 6–11), die **FRP-Summe** deutlich stabiler (ca. 1,8–3,6). Biete deshalb beides an, mit FRP als verlässlicherer Größe.

---

## 6. Rechenregeln für Statistiken im Frontend

1. **Konfidenz filtern:** Für Statistiken `conf_class = low` weglassen (nur `nominal` und `high`). Auf der Karte kann der Nutzer sie optional einblenden.
2. **Anzahl im Gebiet pro Monat** = Summe der Punkte (oder `n_*` der Zellen) im Gebiet, MODIS-Monate × `factor_n`. **FRP im Gebiet** = Summe `frp`, MODIS-Monate × `factor_frp`.
3. **Zeitfenster unterschiedlicher Länge nie direkt vergleichen.** Entweder `frp_harmonized_per_day` nutzen oder durch die Tage teilen (`n_days`). Der letzte Tag eines NRT-Downloads ist oft unvollständig und wurde vom Backend bereits entfernt.
4. **Median für den Vergleich:** Pro Kalendermonat der Median über alle Jahre des Datensatzes, **ohne das aktuelle Jahr**. Anomalie = Wert minus Median (absolut oder in %).
5. **Fehlende Zellen = 0**, nicht "keine Daten". Ausfälle der Satelliten liefert das Backend später als eigenes Flag.
6. **Wenig Daten im Gebiet:** Bei sehr kleinen Gebieten oder wenigen Hotspots pro Monat (Richtwert < 30) ist die Umrechnung unsicher. Zeig eine Warnung an.
7. **Zellen sind keine exakten Gebiete.** Die Gebietsabfrage nimmt Zellen, deren Mittelpunkt im Polygon liegt. Das ist bei kleinen Gebieten ungenau. Dafür gibt es die Einzelpunkte (Tabelle A).

---

## 7. UI-Plan

Grundlage ist die Aufgabenstellung des Hackathons.

1. **Gebiet wählen:** Polygon oder Rechteck auf der Karte zeichnen, Lat/Lon eingeben, oder (optional) Land/Provinz aus `regions.geojson`.
2. **Zeitraum wählen.**
3. **Hotspot-Karte:** Einzelpunkte, einfärbbar nach Sensor oder `conf_class`, Größe nach `frp`. Zeichne nie mehr als einige zehntausend Punkte gleichzeitig (Clustering oder WebGL/Canvas). Für sehr große Gebiete die Zellen aus Tabelle B oder den Würfel zeigen.
4. **Brandkalender:** Heatmap Monat × Jahr (ab ca. 2000) mit der Anzahl der Hotspots im Gebiet. **Zugängliche Farbskala** (sequenziell, auch bei Farbfehlsichtigkeit lesbar, z. B. viridis oder eine ColorBrewer-Skala). Dazu eine Beschriftung der Skala.
5. **Vergleich zur Historie:** Aktueller Monat gegen den Median desselben Monats, mit Anomalie in Prozent.
6. **Jahresvergleich und Trend:** Linien- oder Balkendiagramm pro Jahr, und das Jahresmuster über 12 Monate.
7. **Kennzahlen:** Anzahl Hotspots, FRP-Summe, Anteil Nacht, Anteil `high`-Konfidenz.
8. **Download:** CSV der Hotspots im gewählten Gebiet und Zeitraum, mit den Spalten aus Tabelle A. Zusätzlich die Statistik als CSV.
9. **Transparenz:** Jeder umgerechnete Wert ist gekennzeichnet. Dazu Hinweis auf die Unsicherheit und den Datenstand (Datenzeitraum, Version).

---

## 8. Vorschlag für die Schnittstelle (noch nicht umgesetzt, bitte abstimmen)

Entweder ein kleiner Server (z. B. FastAPI + DuckDB über die Parquet-Dateien) oder, für die Demo, vorberechnete JSON-Dateien pro Region. Die Feldnamen sind in beiden Fällen gleich.

| Aufruf | Antwort |
|---|---|
| `GET /hotspots?bbox=w,s,e,n&from=YYYY-MM-DD&to=YYYY-MM-DD` (Polygon per POST) | Punkte im Schema von Tabelle A, als GeoJSON oder (`format=csv`) CSV |
| `GET /calendar?polygon=...&from=...&to=...` | Pro Monat: `year`, `month`, `n_raw`, `n_harmonized`, `frp_harmonized`, `is_calibrated`, `factor`, `n_obs` |
| `GET /anomaly?polygon=...&year=...&month=...` | `value`, `median_prior_years`, `delta_abs`, `delta_pct`, `n_years` |
| `GET /meta` | `data_from`, `data_to`, `version`, `sensor_timeline`, `calibration_method` |

Beispielantwort `/calendar` (erfunden, nur zur Form):
```json
[{"year":2019,"month":8,"n_raw":412,"n_harmonized":3708,"frp_harmonized":9120.5,"is_calibrated":true,"factor":9.0,"n_obs":412}]
```

---

## 9. Stolpersteine

- **Unterschiedliche Empfindlichkeit:** VIIRS findet im Schnitt ca. 5–10-mal mehr Hotspots als MODIS (Muster, stark von Region und Tag/Nacht abhängig). Rohzahlen beider Sensoren **nie** direkt vergleichen.
- **Satellitenzahl ändert sich:** SNPP ab 2012, N20 ab 2018, N21 ab 2023. Mehr Satelliten bedeuten mehr Überflüge und damit mehr Hotspots, ohne dass mehr brennt. Das Backend normalisiert das über `sensor_timeline.json` und die Faktoren. Markiere optional Brüche in der Zeitreihe, wenn ein Satellit dazukommt.
- **Terra-Überflugzeit:** Soweit uns bekannt, driftet sie seit ca. 2020. Das kann MODIS-Zahlen leicht verzerren. Wir prüfen das an den echten Daten.
- **NRT gegen Standard:** Die Muster sind vorläufige Echtzeitdaten (`6.1NRT`, `2.0NRT`). Für die Historie nehmen wir endgültige Standardprodukte.
- **Regionen:** `region` ist heute nur eine grobe Box. Verlass dich für die Auswahl auf Geometrie (Polygon).
- **Datenmenge:** Hochgerechnet grob 0,5–1 GB pro VIIRS-Jahr als Parquet (Schätzung aus den Mustern, nicht gemessen). Lade nie alle Punkte, nur das Ergebnis einer Abfrage.
- **Heute noch ohne Land-Maske:** Die Muster enthalten auch Hotspots im Meer oder an Küsten (z. B. Gasfackeln, Schiffe). Das sind echte Detektionen, keine Fehler.

---

## 10. Was du jetzt tun kannst

1. Baue das UI-Gerüst mit den Beispieldateien aus Abschnitt 3a, oder erzeuge Mock-Daten aus dem Schema in Abschnitt 4 (für den Kalender brauchst du erfundene Monatswerte über 20 Jahre).
2. Lege die Datenzugriffsschicht so an, dass sich später nur die Quelle ändert (Mock → Parquet/API), nicht die Spaltennamen.
3. Sobald echte Daten kommen, prüfe sie mit dieser **Checkliste** (Python/pandas):
   ```python
   import pandas as pd
   a = pd.read_parquet("hotspots.parquet")                    # oder einen Ordner
   assert {"sensor","satellite","datetime_utc","latitude","longitude","frp","conf_class","daynight"} <= set(a.columns)
   assert a.sensor.isin(["MODIS","VIIRS"]).all()
   assert a.conf_class.isin(["low","nominal","high"]).all()
   assert a.daynight.isin(["D","N"]).all()
   assert a.latitude.between(-90, 90).all() and a.longitude.between(-180, 180).all()
   assert (a.frp >= 0).all()
   print(a.groupby(["sensor", a.datetime_utc.dt.year]).size().unstack(0))   # Punkte pro Jahr und Sensor
   ```
   Prüfe außerdem: Hat jedes Jahr Daten? Ist ein Jahr auffällig leer (Ausfall)? Wechselt die Satellitenzahl wie in `sensor_timeline.json`?
4. Melde Schema-Fragen an das Backend, bevor du Spaltennamen im Code festschreibst.

## 11. Offene Entscheidungen (mit dem Backend klären)

- Zellgröße des Würfels (0,05° oder 0,1°) und ob Tabelle B als Wochen- oder Monatsfenster geliefert wird.
- API (FastAPI) oder statische JSON-Dateien für die Demo.
- Ob die VIIRS-Rohpunkte nur für die letzten 1–2 Jahre bereitgestellt werden (Speicher) und ältere Jahre nur als Würfel.
- Echte Regionen (Länder, Provinzen, ggf. Biome) statt der groben `region`-Boxen.
- Welche Karten-Bibliothek und welche Farbskala ihr nehmt (Backend ist unabhängig davon).
