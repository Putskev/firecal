# FireCal

Harmonisierter MODIS- und VIIRS-Brandkalender für die NASA Space Apps Challenge 2026
(„Harmonization of MODIS and VIIRS Hot Spots“). Die Daten bleiben über einen täglichen
GitHub-Actions-Job automatisch aktuell.

## Aufbau

```
index.html                     Web-App (keine Build-Schritte, Bibliotheken per CDN)
update.py                      baut data/hotspots.csv aus Archiv + FIRMS-API
config.json                    Gebiet (west,süd,ost,nord) und FIRMS-Quellen
requirements.txt               pandas
.github/workflows/update.yml   täglicher Lauf von update.py, committet die neuen Daten
data/archive/                  hier die Archiv-CSVs ablegen
data/hotspots.csv              (wird erzeugt) die Datei, die die App lädt
data/nrt_cache.csv             (wird erzeugt) neue NRT-Daten seit Archivende
data/meta.json                 (wird erzeugt) Stand, Anzahl, Zeitraum
data/harmonization.json        (wird erzeugt) fester Harmonisierungsfaktor k
```

## Einrichtung (einmalig, ca. 15 Minuten)

1. **Repo anlegen** und alle Dateien hochladen.
2. **Gebiet festlegen:** In `config.json` bei `area` die Box als `west,süd,ost,nord` eintragen.
   Sie muss zu euren Archivdaten passen.
3. **Archivdaten:** Die CSVs vom FIRMS Archive Download (MODIS und VIIRS) nach `data/archive/` legen.
4. **MAP_KEY als Secret:** Unter *Settings → Secrets and variables → Actions → New repository secret*
   den Namen `FIRMS_MAP_KEY` mit eurem Key anlegen. Der Key gehört nie ins Repo.
5. **Schreibrechte für den Job:** Unter *Settings → Actions → General → Workflow permissions*
   „Read and write permissions“ wählen.
6. **Ersten Lauf starten:** *Actions → Update FIRMS data → Run workflow*. Danach liegen
   `data/hotspots.csv`, `meta.json` und `harmonization.json` im Repo.
7. **Veröffentlichen:** Unter *Settings → Pages* bei Source „Deploy from a branch“, Branch `main`,
   Ordner `/ (root)` wählen. Die App ist dann unter `https://<user>.github.io/<repo>/` erreichbar.

Ab dann läuft der Job jeden Tag um 06:17 UTC, holt neue Hotspots und committet sie.
GitHub Pages veröffentlicht die neue Version automatisch.

## Lokal testen

```bash
pip install -r requirements.txt
FIRMS_MAP_KEY=dein_key python update.py     # oder: python update.py --no-api
python -m http.server 8000                  # dann http://localhost:8000 öffnen
```

Per Doppelklick auf `index.html` blockiert der Browser das Laden von `data/hotspots.csv`.
Die App zeigt dann Demodaten. Eigene CSVs lassen sich aber immer über „Load FIRMS CSV files“ laden.

## Wie die Daten aktuell bleiben

- `update.py` liest bei jedem Lauf alle Archiv-CSVs und den NRT-Cache.
- Über die FIRMS-Area-API holt es alle Tage seit dem letzten Stand, in Blöcken zu höchstens 10 Tagen
  (`/api/area/csv/{KEY}/{QUELLE}/{GEBIET}/{TAGE}/{DATUM}`), und zwar für MODIS, VIIRS S-NPP,
  NOAA-20 und NOAA-21. Ist der Job mal ausgefallen, holt er bis zu 60 Tage nach.
- Doppelte Hotspots werden über Position, Datum, Uhrzeit und Satellit entfernt.
- **NRT wird durch geprüfte Daten ersetzt:** Legt ihr später einen neueren Archiv-Download nach
  `data/archive/`, verwirft das Skript alle NRT-Zeilen bis zum Archivende. Empfehlung: etwa einmal pro
  Monat das Archiv neu herunterladen.

## Harmonisierung

- `update.py` berechnet beim ersten Lauf einen **festen Faktor k** (Verhältnis der Monatssummen
  MODIS/VIIRS über alle Monate, in denen beide Sensoren gemessen haben) und speichert ihn in
  `data/harmonization.json`. Er bleibt fest, damit sich alte Kalenderwerte nicht täglich ändern.
- Neu berechnen: *Run workflow* mit Häkchen „recalibrate“ oder `python update.py --recalibrate`.
- In der App lässt sich zwischen festem k und einem für das gewählte Gebiet geschätzten k umschalten.
- Die Methode ist ein Platzhalter. Eine bessere Kalibrierung des Datenteams ersetzt die Funktion
  `harmonization()` in `update.py` und `harmonizeFactors()` / `monthValue()` in `index.html`.

## Funktionen der App

- Graue Basiskarte mit Gauß-Kernel-Heatmap (Viridis), Fläche zeichnen oder Box per Lat/Lon
- Zeitraum, Metrik (Anzahl / FRP-Summe), Sensoransicht (harmonisiert / MODIS roh / VIIRS roh)
- Filter: niedrige Konfidenz (MODIS < 30, VIIRS `l`), nur Vegetationsfeuer (`type = 0`)
- Kennzahlen, Brandkalender mit Klick-auf-Monat und Animation, Anomalie gegen Median,
  Harmonisierungs-Check
- Downloads: Hotspot-CSV, Monatszusammenfassung, Diagramme als PNG

## Grenzen

- Die App rechnet im Browser. Für eine Pilotregion (bis einige hunderttausend Hotspots) reicht das.
  Für große Gebiete sollte `update.py` zusätzlich pro Zelle und Monat aggregieren.
- GitHub begrenzt einzelne Dateien auf 100 MB. Wird `hotspots.csv` größer, das Gebiet verkleinern
  oder auf Aggregate umstellen.
- Die Anomalie braucht für einen Kalendermonat mindestens 3 Jahre Daten.
