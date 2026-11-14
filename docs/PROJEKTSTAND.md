# FireCal: Projektstand

Stand: Samstag, 14.11.2026, 15:30 Uhr · NASA Space Apps Challenge 2026 (14.–15.11.)
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

### Frontend: interaktives Dashboard (`index.html`)
Nach dem Vorbild des VIS-Stuttgart-Tools (VAST Challenge 2012): verknüpfte Ansichten, die alle an einer
Auswahl aus Gebiet, Zeitraum und Filtern hängen.
- Filterleiste mit Trefferzahl: Anzahl/FRP, pro Tag, harmonisiert/roh/MODIS/VIIRS, Satelliten, Tag/Nacht,
  Konfidenz, Vegetation, Monat/Tag
- Zeitleiste mit Schieber (Brushing), schraffierte umgerechnete Perioden, Linien bei neuen Satelliten, Play
- Karte: Dichte oder echte Einzelpunkte (Farbe Sensor/Konfidenz/FRP, Größe FRP), Gebiet zeichnen, km-Maßstab
- Kennzahlen mit Unsicherheit, Nacht- und High-Confidence-Anteil, automatisch formulierte Erkenntnisse, Warnungen
- Brandkalender, Anomalie (Median ohne eigenes Jahr), Saisonmuster, Jahresvergleich mit Trend
- Harmonisierungs-Check mit Unsicherheitsband
- Verteilungen: FRP, Ortszeit, Konfidenz, Satellit (Klick filtert global)
- Raum-Zeit-Matrix (Zellen × Perioden), Satelliten-Zeitleiste, Kalibriertabelle
- Downloads: Hotspots (Format Tabelle A) und Statistik-CSV; Methodik-Abschnitt (noch leer)

### Backend (`backend/`)
- FastAPI + DuckDB, liest Parquet direkt (10 Mio. Hotspots: 0,3 GB RAM, Antworten < 0,1 s)
- Liest rohe FIRMS-Dateien **und** Tabelle A des Datenteams (.csv, .csv.gz, .parquet)
- Harmonisierung wie im Handoff: VIIRS-Niveau, MODIS × Faktor je Region mit 25–75-%-Band; nimmt die
  Kalibriertabelle des Teams automatisch, sobald sie in `data/calibration/` liegt
- Entfernt den unvollständigen letzten NRT-Tag automatisch
- 8 Endpunkte, Doku unter `/docs` und online

### Daten
- Echte FIRMS-NRT-Daten, global, 01.–13.11.2026: **1.559.662 Hotspots** (MODIS Terra/Aqua, VIIRS NOAA-20/21),
  aus Tabelle A des Datenteams und den FIRMS-ZIPs
- Kalibriertabelle (Tabelle C) und Zelltabellen (Tabelle B, 5°/0,5°/2 km) des Datenteams eingebunden;
  Abgleich: mit Tabelle B (2 km) **0,000 % Abweichung**, Regionen exakt übernommen

### Infrastruktur
- GitHub-Repo, GitHub Pages (Demo), API-Doku online, täglicher Workflow (wartet auf MAP_KEY), Dockerfile

## Wichtige Entscheidungen

| Entscheidung | Begründung |
|---|---|
| Harmonisierung auf **VIIRS-Niveau** (MODIS × Faktor je Region) | Vorgabe des Datenteams (Handoff); VIIRS ist der laufende Sensor |
| Karte zeigt nur Rohpunkte, Statistik nur für Gebiete | Faktoren gelten nur für Gebiete zuverlässig (Korrelation bei 2-km-Zellen nur 0,29) |
| Faktor intern als Gewicht pro Hotspot | Jede Ansicht und jedes gezeichnete Gebiet ist dann eine einfache Summe |
| DuckDB + Parquet statt PostgreSQL | Die App macht fast nur Summen über Millionen Zeilen; dafür ist eine analytische Datenbank schneller, braucht keinen eigenen Server und ist für eine nur lesende App ausreichend |
| Website spricht nur mit der API, nie direkt mit der Datenbank | Datenbank austauschbar, sicher, klare Schnittstelle |
| Anzahl und FRP gleichwertig umschaltbar | Handoff: FRP ist zwischen den Sensoren robuster (Faktor ~2–4 statt ~7–11) |

## Was noch offen ist

| # | Aufgabe | Wichtigkeit | Zuständig |
|---|---|---|---|
| 1 | **Tabelle A mit der Historie** (ab 2000) liefern → `data/incoming/` | kritisch | Datenteam |
| 2 | **Kalibriertabelle** (Tabelle C) → `data/calibration/`, inkl. Korrektur je Satelliten-Kombination | kritisch | Datenteam |
| 3 | **Validierung** (r, Bias, MAE vor/nach) und **Methodik-Text** in `content/methodology.md` | hoch | Datenteam / Team |
| 4 | Echte Regionen (`regions.geojson`) statt Platzhalter-Boxen | mittel | Datenteam |
| 5 | **Öffentliches Hosting** mit Backend (Dockerfile liegt bereit) | mittel | Team |
| 6 | Pilotregion(en) und Story für die Präsentation | mittel | Team |
| 7 | **Abgabe**: Projektseite, Demo-Video (2–3 Min.), Pitch | kritisch | Team |
| 8 | FIRMS MAP_KEY als GitHub-Secret | niedrig | Putu |

Details und offene Fragen an das Datenteam: `docs/AGENT_HANDOFF.md`, Abschnitt 4 und 7.

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
