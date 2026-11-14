# FireCal: Projektstand

Stand: Samstag, 14.11.2026, 13:30 Uhr · NASA Space Apps Challenge 2026 (14.–15.11.)
Challenge: **Harmonization of MODIS and VIIRS Hot Spots**

## Links

| Was | Wo |
|---|---|
| Repo (Code, Daten, Doku) | https://github.com/Putskev/firecal |
| Live-Demo (nur Demodaten) | https://putskev.github.io/firecal/ |
| Lokale App mit echten Daten | http://localhost:8000 (nach dem Start, siehe unten) |
| API-Doku (online, zum Lesen) | https://putskev.github.io/firecal/api-docs.html |
| API-Doku (lokal, zum Ausprobieren) | http://localhost:8000/docs |
| Übergabe für die Harmonisierung | [`docs/AGENT_HANDOFF.md`](AGENT_HANDOFF.md) |
| Technische Doku | [`README.md`](../README.md) |

## Die Aufgabe in einem Satz

Eine Web-App, die MODIS (lange Historie seit 2000, 1 km) und VIIRS (genauer, ab 2012, 375 m) zu **einer**
konsistenten Zeitreihe zusammenführt und für ein frei wählbares Gebiet einen Brandkalender, Anomalien gegen
den historischen Median und einen CSV-Download zeigt.

## Was fertig ist

### Frontend (`index.html`)
- Graue Grundkarte mit Heatmap (Gauß-Kerne, barrierefreie Viridis-Farben), Auflösung passt sich dem Zoom an,
  lädt nur den sichtbaren Ausschnitt
- Gebiet wählen: Rechteck oder Polygon zeichnen, oder Bounding Box per Lat/Lon
- Zeitraum, Monats- oder Tagesansicht (Tagesansicht automatisch bei kurzen Zeiträumen)
- Brandkalender (Jahr × Monat bzw. Woche × Wochentag), Klick auf eine Zelle zeigt sie auf der Karte, „Play“
  spielt die Perioden ab
- Kennzahlen: Summe, stärkste Periode, stärkstes Jahr bzw. Tagesdurchschnitt, letzte Periode gegen Median
- Diagramme: Anomalie gegen Median, Harmonisierungs-Check (MODIS roh, VIIRS roh, VIIRS harmonisiert, gesamt)
- Umschalter: harmonisiert / MODIS roh / VIIRS roh, Anzahl / FRP-Summe
- Filter: niedrige Konfidenz ausschließen, nur Vegetationsfeuer (sobald Archivdaten da sind)
- Downloads: Hotspots als CSV, Zusammenfassung als CSV, Diagramme als PNG
- Methodik-Abschnitt, gerendert aus `content/methodology.md` (noch leer)
- Quellenangabe NASA FIRMS

### Backend (`backend/`)
- **FastAPI + DuckDB**, liest Parquet-Dateien direkt von der Platte
- Einlesen beliebiger FIRMS-CSVs (MODIS/VIIRS, NRT/Archiv) in ein einheitliches Format (`backend/ingest.py`)
- Daten nach Jahr/Monat aufgeteilt, dazu vorberechnete Summen pro Monat × 0,1° und Tag × 0,25°
- Endpunkte: `/api/meta`, `/api/timeseries`, `/api/heatmap`, `/api/hotspots.csv`
- **Platz für die Harmonisierung**: Funktion `backend/harmonization.py` ersetzen oder fertige Tabelle nach
  `data/harmonized/` legen. Bis dahin läuft ein Platzhalter (einfacher Faktor MODIS/VIIRS)
- Getestet mit 10,4 Mio. Hotspots: 0,3 GB RAM, Antworten unter 0,1 s

### Daten
- Echte FIRMS-NRT-Daten, global, 07.–14.11.2026: **869.189 Hotspots**
  (MODIS Terra/Aqua 75.923 · VIIRS NOAA-20 399.584 · VIIRS NOAA-21 393.682)
- Liegen als Parquet in `data/raw/` (15 MB statt 73 MB CSV)
- Hinweis: Der 14.11. ist im Download unvollständig und erscheint deshalb niedriger

### Infrastruktur
- GitHub-Repo, GitHub Pages (Demo), täglicher GitHub-Workflow für neue FIRMS-Daten (wartet auf MAP_KEY)
- `Dockerfile` für späteres Hosting (Render, Hugging Face Spaces o. ä.)

## Wichtige Entscheidungen

| Entscheidung | Begründung |
|---|---|
| VIIRS wird auf MODIS-Niveau gebracht, nicht umgekehrt | MODIS kann kleine Feuer, die es nie gesehen hat, nicht nachträglich „erfinden“ |
| Harmonisierung als Gewicht pro Hotspot (`weight`, `frp_h`) | Jede Ansicht ist dann eine einfache Summe: Kalender, Karte, Export und jedes Gebiet sind automatisch harmonisiert |
| DuckDB + Parquet statt PostgreSQL | Die App macht fast nur Summen über Millionen Zeilen; dafür ist eine analytische Datenbank schneller, braucht keinen eigenen Server und ist für eine nur lesende App ausreichend |
| Website spricht nur mit der API, nie direkt mit der Datenbank | Datenbank austauschbar, sicher, klare Schnittstelle |
| FRP nur als Zusatz, Hauptmaß ist die Hotspot-Anzahl | Die Challenge fragt nach Anzahl; FRP ist zwischen den Sensoren schwerer vergleichbar |

## Was noch offen ist

| # | Aufgabe | Wichtigkeit | Zuständig |
|---|---|---|---|
| 1 | **Echte Harmonisierung** liefern (siehe `docs/AGENT_HANDOFF.md`) | kritisch, Kern der Challenge | Harmonisierung |
| 2 | **Validierung**: r, Bias, MAE vor/nach im Überlappungszeitraum | kritisch für die Jury | Harmonisierung |
| 3 | **Archivdaten** (mehrere Jahre) einlesen: `data/incoming/` → `python -m backend.ingest` | hoch, sonst kein Mehrjahreskalender | Team |
| 4 | **Methodik-Text** in `content/methodology.md` (Englisch) | hoch | Team / Harmonisierung |
| 5 | **Öffentliches Hosting** mit Backend (Dockerfile liegt bereit) | mittel, für Jury-Link | Team |
| 6 | **FIRMS MAP_KEY** als GitHub-Secret `FIRMS_MAP_KEY` | niedrig für die Abgabe | Putu |
| 7 | **Abgabe**: Projektseite, Demo-Video (2–3 Min.), Pitch | kritisch | Team |
| 8 | Pilotregion(en) für die Präsentation festlegen | mittel | Team |

Empfohlener Feature-Freeze: **Sonntag 12:00 Uhr**, danach nur noch Abgabe, Video, Pitch.

## Lokal starten (Windows, PowerShell)

Einmalig:
```powershell
git clone https://github.com/Putskev/firecal.git
cd firecal
py -m venv .venv
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass -Force
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```
Jedes Mal:
```powershell
cd $HOME\firecal
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass -Force
.\.venv\Scripts\Activate.ps1
git pull
python -m uvicorn backend.main:app --reload
```
Dann http://localhost:8000 öffnen. Beenden mit Strg+C.

## Zugriff für das Team

| Wer / was | Geht das? | Wie |
|---|---|---|
| Code ansehen, herunterladen | ja, für alle | Repo ist öffentlich |
| Code ändern und hochladen | nur mit Freigabe | Putu fügt die GitHub-Namen unter *Settings → Collaborators* hinzu |
| Demo-Seite ansehen | ja, für alle | https://putskev.github.io/firecal/ (nur Demodaten) |
| App mit echten Daten | auf dem eigenen Rechner | Repo klonen und lokal starten (siehe oben), die Daten sind im Repo |
| Putus laufende App von außen | nur mit Zusatzschritt | gleiches WLAN: Start mit `--host 0.0.0.0`, dann `http://<Putus-IP>:8000`; von überall: Tunnel oder Hosting |
