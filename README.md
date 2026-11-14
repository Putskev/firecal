# FireCal

Harmonisierter MODIS- und VIIRS-Brandkalender für die NASA Space Apps Challenge 2026
(„Harmonization of MODIS and VIIRS Hot Spots“).

- **Backend:** Python FastAPI + DuckDB (`backend/`), API-Doku automatisch unter `/docs`
- **Frontend:** `index.html` (Leaflet, Plotly), wird vom Backend unter `/` ausgeliefert
- **Daten:** NASA LANCE FIRMS, MODIS C6.1 und VIIRS 375 m (S-NPP, NOAA-20, NOAA-21)

> **Harmonisierung / Datenbank übernehmen?** Alles Nötige steht in [`docs/AGENT_HANDOFF.md`](docs/AGENT_HANDOFF.md):
> Eingabeschema, was geliefert werden muss, API-Vertrag zur Website, Validierung und Abnahme-Checkliste.

## Schnellstart (lokal)

```bash
pip install -r requirements.txt
uvicorn backend.main:app --reload
# App:      http://localhost:8000
# API-Doku: http://localhost:8000/docs
```

Im Repo liegt bereits eine Woche echter Daten (07.–14.11.2026, global, 869.189 Hotspots) in
`data/raw/`. Beim ersten Start baut das Backend daraus automatisch die harmonisierte Tabelle.

## Architektur und Datenfluss

```
FIRMS CSV (NRT/Archiv)          FIRMS API (täglich)
        │                              │
        ▼ python -m backend.ingest     ▼ python update.py
data/raw/*.parquet  ◄──────────────────┘     kanonisches Schema, NICHT harmonisiert
        │
        ▼ backend/build.py  ── ruft backend/harmonization.py auf   ◄── HIER kommt die Harmonisierung rein
        │                   ── oder nimmt data/harmonized/* direkt  ◄── ODER fertige Tabelle hier ablegen
        ▼
data/processed/
   hotspots/y=2026/m=11/*.parquet   alle harmonisierten Hotspots, nach Jahr/Monat aufgeteilt
   cells_month.parquet              vorberechnete Summen: Monat × 0,1°-Zelle
   cells_day.parquet                vorberechnete Summen: Tag × 0,25°-Zelle
        │
        ▼ backend/main.py (FastAPI + DuckDB liest die Parquet-Dateien direkt von der Platte)
/api/meta · /api/timeseries · /api/heatmap · /api/hotspots.csv
        │
        ▼
index.html (Karte, Kalender, Anomalien, Harmonisierungs-Check, Methodik)
```

| Ordner | Inhalt | im Git |
|---|---|---|
| `data/incoming/` | rohe FIRMS-CSVs zum Einlesen | nein |
| `data/raw/` | kanonische Parquet-Dateien, nicht harmonisiert | ja |
| `data/harmonized/` | optional: fertige harmonisierte Tabelle (Drop-in) | ja |
| `data/processed/` | vom Backend gebaut | nein |
| `content/methodology.md` | Methodik-Text, wird in der App angezeigt | ja |

## Speicher und Geschwindigkeit

DuckDB liest die Parquet-Dateien direkt von der Platte, es wird nichts komplett in den Arbeitsspeicher geladen.

- **Kalender, Anomalien, Harmonisierungs-Check** lesen nur die vorberechneten Zellen-Summen.
- **Heatmap** nutzt die Zellen-Summen und lädt nur den sichtbaren Kartenausschnitt. Nur stark
  hineingezoomt (Raster feiner als 0,25°) werden einzelne Hotspots gelesen.
- **CSV-Export** liest einzelne Hotspots, aber nur die Jahr/Monat-Ordner im gewählten Zeitraum.
- Ein Gebiet zählt bei den Zellen-Summen als „drin“, wenn der Zellmittelpunkt drin liegt (0,1° bzw. 0,25°).

Test mit 10,4 Mio. Hotspots (12-fache Woche): API braucht 0,3 GB RAM, Abfragen 50–110 ms,
CSV-Export einer Woche Australien 0,6 s. **Der Build-Schritt** (Harmonisierung mit pandas) braucht dagegen
etwa 0,4 GB RAM pro Million Hotspots. Für die volle Archiv-Historie den Build auf einem Rechner mit genug
RAM laufen lassen oder jahresweise harmonisieren und das Ergebnis nach `data/harmonized/` legen.

## Neue Daten einlesen

```bash
cp ~/Downloads/fire_*.csv data/incoming/
python -m backend.ingest          # -> data/raw/<name>.parquet
# Server neu starten: baut automatisch neu, sobald sich data/raw/ geändert hat
```

Das funktioniert für NRT- und Archivdateien von MODIS und VIIRS. Doppelte Hotspots (gleiche Position,
Zeit und Satellit) werden entfernt. Dabei gewinnen Archivdaten (SP) gegenüber NRT.

## Kanonisches Schema (eine Zeile pro Hotspot)

| Spalte | Typ | Beispiel | Hinweis |
|---|---|---|---|
| `latitude`, `longitude` | float | -12.3456 | Pixelmitte |
| `acq_date` | str | 2026-11-07 | UTC |
| `acq_time` | int | 1340 | UTC, HHMM |
| `satellite` | str | Aqua | Terra, Aqua, N (S-NPP), N20, N21 |
| `sensor` | str | MODIS | MODIS oder VIIRS |
| `confidence` | str | 57 / n | Rohwert von FIRMS |
| `conf_class` | str | nominal | low / nominal / high (MODIS: <30, 30–79, ≥80) |
| `frp` | float | 13.67 | MW, sensorspezifisch, **nicht** harmonisiert |
| `daynight` | str | D | D oder N |
| `type` | int/leer | 0 | nur in Archivdaten: 0 Vegetation, 1 Vulkan, 2 statische Quelle, 3 offshore |
| `version`, `source` | str | 6.1NRT, NRT | NRT oder SP |

Die Harmonisierung ergänzt zwei Spalten:

| Spalte | Bedeutung |
|---|---|
| `weight` | Beitrag dieses Hotspots zur **harmonisierten Anzahl** (MODIS-Äquivalent). Anzahl eines Gebiets und Zeitraums = Summe von `weight`. |
| `frp_h` | Beitrag zur **harmonisierten FRP** in MW. FRP eines Gebiets und Zeitraums = Summe von `frp_h`. |

**Warum pro Hotspot?** Kalender, Anomalien, Karte, CSV-Export und jedes vom Nutzer gezeichnete Gebiet sind
nur Summen über Hotspots. Mit einer Zahl pro Hotspot sind deshalb alle Ansichten auf einmal harmonisiert.

## Die echte Harmonisierung einbauen (zwei Wege)

**Weg A: Funktion ersetzen.** In `backend/harmonization.py` die Funktion `harmonize(df)` neu schreiben:

- Eingabe: DataFrame im kanonischen Schema (alle Sensoren und Satelliten, ohne Duplikate)
- Ausgabe: `(df_mit_weight_und_frp_h, info)`. `info` ist ein dict mit `name`, `version`,
  `status` („final“), `description` und `params`, die App zeigt es an.
- Zeilen dürfen entfernt werden, z. B. Falschalarme durch Industrie.
- Liefert die Methode Rasterwerte statt Werte pro Hotspot, gilt
  `weight = harmonisierter_Zellwert / Anzahl_Rohhotspots_in_der_Zelle`.

**Weg B: Fertige Tabelle ablegen.** Eine CSV- oder Parquet-Datei mit allen Spalten oben plus `weight` und
`frp_h` nach `data/harmonized/` legen, optional mit `harmonization.json` (siehe README dort). Sie wird dann
unverändert verwendet, und `harmonization.py` wird übersprungen.

Danach den Server neu starten. Das Backend erkennt die Änderung und baut neu. Im Bereich „Sensor view“
der App erscheinen dann Name, Version und Status „final“ statt „placeholder“.

Bis dahin läuft ein **Platzhalter**: k = Summe(MODIS) / Summe(VIIRS) über alle Monate, in denen beide
Sensoren gemessen haben, angewendet ab dem ersten VIIRS-Monat.

## API

Alle Analyse-Endpunkte haben dieselben Filter: `bbox=west,south,east,north`,
`polygon=lat,lon;lat,lon;…`, `start`/`end` (YYYY-MM-DD), `exclude_low` (Standard true),
`vegetation_only` (Standard false).

| Endpunkt | Liefert |
|---|---|
| `GET /api/meta` | Zeitraum, Anzahl pro Satellit, Bounding Box, aktive Harmonisierung |
| `GET /api/timeseries?granularity=month\|day` | pro Periode: Roh- und harmonisierte Summen (Anzahl und FRP) |
| `GET /api/heatmap?view=harm\|modis\|viirs&metric=count\|frp&res=0.25` | Rasterzellen `[lat, lon, wert]` |
| `GET /api/hotspots.csv` | gefilterte Hotspots inkl. `weight` und `frp_h` |
| `POST /api/reload?token=…` | Daten neu laden ohne Neustart (nur mit Umgebungsvariable `FIRECAL_RELOAD_TOKEN`) |

## Methodik

Der Text steht in `content/methodology.md` und erscheint unten in der App. Die Überschriften und eine
Validierungstabelle sind vorbereitet. Ihr ersetzt einfach die Stellen mit „To be added.“.

## Hosting

- **Backend und App zusammen:** Mit dem `Dockerfile` läuft das auf Render, Railway, Fly.io oder
  Hugging Face Spaces. Port über die Umgebungsvariable `PORT`.
- **GitHub Pages** (https://putskev.github.io/firecal/) kann kein Python ausführen und zeigt
  deshalb Demodaten. Mit `?api=https://euer-backend.example.com` am Ende der URL nutzt die Pages-Seite ein
  gehostetes Backend (CORS ist freigeschaltet).

## Täglich aktuell halten

`update.py` holt über die FIRMS-API alle Tage seit dem neuesten Hotspot in `data/raw/` und hängt sie an
`data/raw/nrt_cache.parquet` an. Der GitHub-Workflow `.github/workflows/update.yml` macht das täglich,
sobald das Secret `FIRMS_MAP_KEY` gesetzt ist. Das Gebiet steht in `config.json`, aktuell weltweit.

**Achtung:** Weltweit kommen etwa 2 MB Parquet pro Tag dazu. Für einen Dauerbetrieb über Monate sollte der
Updater auf dem Server laufen statt Daten ins Git zu committen, oder das Gebiet wird verkleinert.

## Grenzen

- Die Harmonisierung ist ein Platzhalter (siehe oben).
- Der letzte Tag eines NRT-Downloads ist meist unvollständig. Er erscheint deshalb im Kalender niedriger.
- Saisonale Anomalien brauchen mehrere Jahre Monatsdaten. Mit einer Woche Daten zeigt die App die
  Tagesansicht und vergleicht mit dem Median der anderen Tage.
