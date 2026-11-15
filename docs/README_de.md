# Phnx (FireCal)

Harmonisierter MODIS- und VIIRS-Brandkalender für die NASA Space Apps Challenge 2026
(„Harmonization of MODIS and VIIRS Hot Spots“). Startseite mit Projekten (eigene Ansichten und dauerhafte Filter,
im Browser gespeichert), interaktives Dashboard mit verknüpften Ansichten, Backend mit FastAPI und DuckDB.

> **Wie funktioniert jede Zahl?** Alle Formeln und Zeiträume: [`ERKLAERUNG.md`](ERKLAERUNG.md).
>
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

Im Repo liegen knapp zwei Wochen echte FIRMS-Daten (01.–13.11.2026, global, 1.559.662 Hotspots: Tabelle A des
Datenteams + FIRMS-Download-ZIPs; der unvollständige 14.11. wird automatisch entfernt), dazu die Kalibriertabelle
und die Zelltabellen (Tabelle B) des Datenteams. Ohne Backend (z. B. GitHub Pages) zeigt die App synthetische Demodaten
von 2001 bis 2026.

## Das Dashboard

Alle Ansichten hängen an **einer Auswahl** aus Gebiet (Karte), Zeitraum (Zeitleiste) und Filtern (Leiste oben).

| Ansicht | zeigt | Interaktion |
|---|---|---|
| Startseite / Projekte | Projekte anlegen (Wizard: Name, Ansichten, Filter, Prüfung mit Hotspot-Zahl), Vorstellung, Harmonisierung, Funktionen | Projekt öffnen, bearbeiten, kopieren, löschen |
| Projektleiste | dauerhafte Filter: Gebiet, Zeitraum, Wochentage, lokale Uhrzeit, Satelliten, Tag/Nacht, Konfidenz | „Filters“, „Views“, „Save current view“ |
| Filterleiste | Kennzahl (Anzahl/FRP, pro Tag), Werte (harmonisiert/roh/MODIS/VIIRS), Satelliten, Tag/Nacht, Monat/Tag, Trefferzahl | wirkt auf alles |
| Zeitleiste | ganzer Zeitraum, umgerechnete Perioden schraffiert, neue Satelliten gepunktet | Zeitraum mit dem Schieber wählen, Balken anklicken |
| Karte (WebGL) | drei Stufen nach Hotspots im Ausschnitt: bis 1.000 Einzelpunkte, bis 1 Mio. Gauß-Kernel, darüber Dichtewürfel; Budget-Balken zeigt den Stand | Rechteck/Polygon zeichnen, Box per Lat/Lon, Maßstab in km |
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
| Karte „Team cells“ | Zellen aus Tabelle B (5°, 0,5°, 2 km), je Fenster | Raster und Fenster wählbar |
| Methodik | aus `content/methodology.md` | – |

**Layout:** Button „▥ Side panel“ (oder Taste `S`) legt alle Statistiken in ein Panel rechts neben Karte und Zeitleiste.
Das Panel lässt sich zu- und aufklappen („Close ▸“ / Lasche „◂ Statistics“ am rechten Rand / Taste `S`), am linken
Rand in der Breite ziehen (Doppelklick = Standard) und hat eine Sprungliste. Die Einstellung bleibt im Browser
gespeichert; per Link erzwingen mit `?layout=side`.

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
data/incoming/  FIRMS-CSV, FIRMS-ZIP (Shapefile/CSV) oder Tabelle A  ──►  python -m backend.ingest
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
| `data/calibration/` | Kalibriertabelle des Teams (Tabelle C), wird für die Faktoren benutzt | ja |
| `data/team/` | Tabelle B des Teams (`fire_activity_7d_<raster>.parquet`), als Kartenebene | ja |
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
| `weekdays=`, `hours=` | an jedem Analyse-Endpunkt: Wochentage 1–7 (UTC-Datum) und lokale Stunden 0–23; dann aus den Einzelpunkten |
| `GET /api/count` | Hotspots im Ausschnitt (für Dichte vs. Punkte und den Budget-Balken) |
| `GET /api/density` | Dichte-Zellen spaltenweise, Zellgröße nach Zoom |
| `GET /api/points.bin` | Einzelpunkte als Binärblock (max. 1 Mio., sonst 413) |
| `GET /api/points` | Einzelpunkte (roh, JSON), die stärksten zuerst |
| `GET /api/distributions` | FRP-Klassen, Ortszeit, Konfidenz, Satellit, Tag/Nacht je Sensor |
| `GET /api/matrix` | aktivste Zellen × Perioden |
| `GET /api/satellites` | Rohdetektionen je Satellit und Periode |
| `GET /api/hotspots.csv` | Download im Format Tabelle A |
| `GET /api/team` | gelieferte Tabellen B: Raster, Zeitfenster |
| `GET /api/team/cells` | Zellen einer Tabelle B für die Karte |

Online-Doku zum Lesen: https://putskev.github.io/firecal/api-docs.html. Nach Änderungen an Endpunkten:
`python -m backend.export_openapi`.

## Leistung

DuckDB liest die Parquet-Dateien direkt; Statistik kommt aus den vorberechneten Zellen. Test mit 10,4 Mio.
Hotspots: API 0,3 GB RAM, Abfragen unter 0,1 s. Der Build-Schritt (pandas) braucht ca. 0,4 GB RAM je
Mio. Hotspots. Für die volle Historie also auf einem Rechner mit genug RAM bauen oder jahresweise liefern.

## Eigener Server (Hetzner, Ubuntu): App + API mit euren Parquet-Dateien

Auf dem Server (per SSH, als Benutzer mit sudo) **ein** Befehl, Pfad = Ordner mit euren Parquet-Dateien:

```bash
curl -fsSL https://raw.githubusercontent.com/Putskev/firecal/main/deploy/setup_server.sh | sudo bash -s -- /pfad/zu/parquet --domain phnx.anohji.dev
```

`--link-only` verbindet den Ordner nur: Die App zeigt, was drin ist (Dateien, Zeilen, Zeitraum aus den
Parquet-Metadaten), lädt die Hotspots aber noch nicht. Ohne Angabe wird automatisch nur verbunden, wenn der RAM
für den Aufbereitungsschritt nicht reicht. Später laden: Skript mit `--load` erneut ausführen.

Ohne Pfad sucht das Skript selbst nach Parquet-Ordnern mit Hotspot-Spalten. Ein schon laufender Webserver bleibt unangetastet
(Caddy bekommt eine zusätzliche Seite per `import`, bei nginx/apache läuft FireCal auf Port 8080).

Das Skript installiert alles (Python, App, systemd-Dienst, Caddy mit HTTPS), prüft den Datenordner (Dateien,
Spalten, Zeilen, RAM) und gibt am Ende die Links aus: `http://<IP>` und `https://<IP-mit-Strichen>.sslip.io`.
Erneut ausführen = aktualisieren. Danach automatisch alle 15 Minuten: neuer Code von GitHub wird geholt,
neue/geänderte Dateien im Datenordner werden eingelesen.

Was im Datenordner (rekursiv, auch `year=/month=`-Ordner) erkannt wird (`backend/sources.py`):

| Datei | erkannt an | Verwendung |
|---|---|---|
| Tabelle A / FIRMS | Spalten `latitude`, `longitude` + `datetime_utc` oder `acq_date` | Hotspots (zusätzlich zu `data/raw`, Duplikate werden entfernt) |
| Tabelle C | Dateiname `calibration*` (.csv/.parquet) | Faktoren |
| Tabelle B | Dateiname `fire_activity_*.parquet` | Karte „Team cells“ |

Ports 80 und 443 müssen in der Hetzner-Cloud-Firewall offen sein. Log: `journalctl -u firecal -f`.

## Hosting

- **GitHub Pages:** https://putskev.github.io/firecal/ (nur Demo). Mit `?api=https://euer-backend` nutzt die
  Seite ein gehostetes Backend.
- **Backend + App:** `Dockerfile` (Render, Railway, Hugging Face Spaces). Port über `PORT`.

## Täglich aktuell halten

`update.py` holt über die FIRMS-API alle Tage seit dem letzten Stand nach `data/raw/nrt_cache.parquet`
(GitHub-Workflow täglich, sobald das Secret `FIRMS_MAP_KEY` gesetzt ist; Gebiet in `config.json`).

## Methodik

Text in `content/methodology.md`, wird unten in der App angezeigt. Stellen mit „To be added.“ ersetzen.
