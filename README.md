# FireCal

Harmonisierter MODIS- und VIIRS-Brandkalender für die NASA Space Apps Challenge 2026
(„Harmonization of MODIS and VIIRS Hot Spots“). Interaktives Dashboard mit verknüpften Ansichten,
Backend mit FastAPI und DuckDB.

> **Was ist der Stand?** Siehe [`docs/PROJEKTSTAND.md`](docs/PROJEKTSTAND.md).
>
> **Daten/Harmonisierung liefern?** Handoff des Datenteams: [`docs/HANDOFF_Frontend.md`](docs/HANDOFF_Frontend.md),
> unsere Antwort und das Lieferformat: [`docs/AGENT_HANDOFF.md`](docs/AGENT_HANDOFF.md).

## Schnellstart (lokal)

```bash
pip install -r requirements.txt
python -m uvicorn backend.main:app --reload
# App:      http://localhost:8000
# API-Doku: http://localhost:8000/docs
```

Im Repo liegt eine Woche echter FIRMS-Daten (07.–13.11.2026, global, 849.393 Hotspots; der unvollständige
14.11. wird automatisch entfernt). Ohne Backend (z. B. GitHub Pages) zeigt die App synthetische Demodaten
von 2001 bis 2026.

## Das Dashboard

Alle Ansichten hängen an **einer Auswahl** aus Gebiet (Karte), Zeitraum (Zeitleiste) und Filtern (Leiste oben).

| Ansicht | zeigt | Interaktion |
|---|---|---|
| Filterleiste | Kennzahl (Anzahl/FRP, pro Tag), Werte (harmonisiert/roh/MODIS/VIIRS), Satelliten, Tag/Nacht, Konfidenz, Vegetation, Monat/Tag, Trefferzahl | wirkt auf alles |
| Zeitleiste | ganzer Zeitraum, umgerechnete Perioden schraffiert, neue Satelliten gepunktet | Zeitraum mit dem Schieber wählen, Balken anklicken |
| Karte | Dichte (herausgezoomt) oder echte Einzelpunkte (ab Zoom 7), Farbe nach Sensor/Konfidenz/FRP, Größe nach FRP | Rechteck/Polygon zeichnen, Box per Lat/Lon, Maßstab in km |
| Kennzahlen + Erkenntnisse | harmonisierte Anzahl und FRP mit Unsicherheit, Rohzahlen je Sensor, Nacht- und High-Confidence-Anteil, aktueller Monat gegen Median; automatische Sätze zu Spitzen, Trend, Saison, Sensoren | – |
| Brandkalender | Jahr × Monat oder Woche × Tag, umgerechnete Monate schraffiert | Klick zeigt Periode auf der Karte |
| Anomalie | Abweichung vom Median desselben Monats in allen anderen Jahren (% oder absolut) | – |
| Saisonmuster | Median je Kalendermonat mit mittlerem 50-%-Band, dazu das letzte Jahr | – |
| Jahresvergleich | Summe je Jahr, Trend über vollständige Jahre | – |
| Harmonisierungs-Check | MODIS roh, VIIRS roh, MODIS × Faktor, harmonisiert mit Unsicherheitsband | – |
| Verteilungen | FRP-Klassen, Ortszeit (Überflüge), Konfidenz, Satelliten | Klick auf „low“ oder einen Satelliten filtert alles |
| Raum-Zeit-Matrix | aktivste Rasterzellen × Perioden, sortierbar, absolut oder je Zeile | Zeile: Zelle wird Gebiet; Kästchen: Periode auf Karte |
| Satelliten-Zeitleiste | Rohdetektionen je Satellit und Periode | – |
| Kalibriertabelle | Faktor je Region mit 25–75 %, r, Zellen; Region des Gebiets hervorgehoben | – |
| Methodik | aus `content/methodology.md` | – |

Downloads: Hotspots im Format „Tabelle A“ (+ Region, weight, frp_h) und eine Statistik-CSV je Periode.

## Harmonisierung (Stand: Platzhalter nach dem Handoff des Datenteams)

- **Zielniveau VIIRS.** Ab 20.01.2012 gilt der native VIIRS-Wert, davor MODIS × Faktor(Region).
- **Faktor** = Summe VIIRS / Summe MODIS über 2°-Zellen × 7-Tage-Fenster, in denen beide Sensoren Feuer
  sehen (ohne low confidence, nur Überlappungszeitraum), getrennt für Anzahl und FRP, mit 25/75-%-Band.
- Liegt eine Kalibriertabelle des Teams in `data/calibration/`, werden deren Faktoren benutzt.
- Umgesetzt pro Hotspot (`weight`, `frp_h` usw.), damit jede Ansicht und jedes Gebiet eine einfache Summe ist.
- **Karte = Rohpunkte**, nie umgerechnet.
- Offen beim Datenteam: Korrektur je Satelliten-Kombination, echte Regionen, Faktor je Monat.

## Architektur

```
data/incoming/  FIRMS-CSV oder Tabelle A (.csv/.csv.gz/.parquet)  ──►  python -m backend.ingest
data/raw/       kanonisches Schema, nicht harmonisiert (im Git)
data/calibration/  optional: Tabelle C des Teams
        │  backend/build.py  (automatisch beim Serverstart)
        │    entfernt unvollständigen NRT-Tag · harmonisiert (harmonization.py) · prüft · schreibt:
        ▼
data/processed/ hotspots/y=/m=/ (alle Punkte) · cells_month (0,1°) · cells_day (0,25°) · meta.json
        │  backend/main.py  FastAPI + DuckDB, liest Parquet direkt von der Platte
        ▼
/api/meta · timeseries · heatmap · points · distributions · matrix · satellites · hotspots.csv
        │
        ▼
index.html   (ApiProvider; ohne Backend: LocalProvider mit Demo/CSV, gleiche Rechnung im Browser)
```

| Ordner | Inhalt | im Git |
|---|---|---|
| `data/incoming/` | Rohdateien zum Einlesen | nein |
| `data/raw/` | kanonisches Parquet | ja |
| `data/calibration/` | Kalibriertabelle des Teams (optional) | ja |
| `data/harmonized/` | fertige harmonisierte Tabelle (optional, ersetzt harmonization.py) | ja |
| `data/processed/` | gebaut | nein |

Kanonisches Schema und Harmonisierungsspalten: siehe Kopf von `backend/data.py`.

## API

Gemeinsame Filter: `bbox`, `polygon`, `start`, `end`, `exclude_low`, `vegetation_only`, `satellites`,
`daynight`. Satelliten- und Tag/Nacht-Filter wirken nur auf Rohwerte und Karte.

| Endpunkt | Liefert |
|---|---|
| `GET /api/meta` | Zeitraum, Version, Satelliten-Zeitleiste, Methode, Kalibriertabelle |
| `GET /api/timeseries` | je Monat/Tag: Rohwerte je Sensor, Nacht, High-Conf, harmonisiert mit Band, MODIS × Faktor |
| `GET /api/heatmap` | Rasterzellen für die Karte |
| `GET /api/points` | Einzelpunkte (roh) im Kartenausschnitt, die stärksten zuerst |
| `GET /api/distributions` | FRP-Klassen, Ortszeit, Konfidenz, Satellit, Tag/Nacht je Sensor |
| `GET /api/matrix` | aktivste Zellen × Perioden |
| `GET /api/satellites` | Rohdetektionen je Satellit und Periode |
| `GET /api/hotspots.csv` | Download im Format Tabelle A |

Online-Doku zum Lesen: https://putskev.github.io/firecal/api-docs.html. Nach Änderungen an Endpunkten:
`python -m backend.export_openapi`.

## Leistung

DuckDB liest die Parquet-Dateien direkt; Statistik kommt aus den vorberechneten Zellen. Test mit 10,4 Mio.
Hotspots: API 0,3 GB RAM, Abfragen unter 0,1 s. Der Build-Schritt (pandas) braucht ca. 0,4 GB RAM je
Mio. Hotspots. Für die volle Historie also auf einem Rechner mit genug RAM bauen oder jahresweise liefern.

## Hosting

- **GitHub Pages:** https://putskev.github.io/firecal/ (nur Demo). Mit `?api=https://euer-backend` nutzt die
  Seite ein gehostetes Backend.
- **Backend + App:** `Dockerfile` (Render, Railway, Hugging Face Spaces). Port über `PORT`.

## Täglich aktuell halten

`update.py` holt über die FIRMS-API alle Tage seit dem letzten Stand nach `data/raw/nrt_cache.parquet`
(GitHub-Workflow täglich, sobald das Secret `FIRMS_MAP_KEY` gesetzt ist; Gebiet in `config.json`).

## Methodik

Text in `content/methodology.md`, wird unten in der App angezeigt. Stellen mit „To be added.“ ersetzen.
