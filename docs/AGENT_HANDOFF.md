# FireCal: Übergabe für Datenbank und Harmonisierung

> **Für wen:** den Agenten bzw. die Person, die die **Harmonisierung von MODIS und VIIRS** und die
> **Datenhaltung** übernimmt. Dieses Dokument beschreibt, was bereits existiert, was geliefert werden muss
> und welche Schnittstellen sich **nicht ändern dürfen**, weil die Website darauf aufbaut.
>
> **Kurzfassung:** Lieferung ist **eine Zahl `weight` und eine Zahl `frp_h` pro Hotspot**, entweder als
> Python-Funktion (`backend/harmonization.py`) oder als fertige Tabelle (`data/harmonized/`). Alles andere,
> also Speicherung, Aggregation, API und Website, ist fertig und passt sich automatisch an.

Projekt: NASA Space Apps Challenge 2026, Challenge „Harmonization of MODIS and VIIRS Hot Spots“.
Repo: https://github.com/Putskev/firecal · Stand dieses Dokuments: 14.11.2026.

---

## 1. Ziel der Challenge (worauf die Lieferung einzahlt)

Ein **konsistenter Brandkalender** von Anfang der 2000er bis heute: MODIS (lange Historie, 1 km) und VIIRS
(375 m, empfindlicher, ab 2012) so zusammenführen, dass **der Sensor keine Rolle mehr spielt**. Nutzer wählen
Gebiet und Zeitraum und sehen Hotspots pro Monat/Tag, Anomalien gegen den Median und Trends.

Daraus folgt die Kernanforderung an die Harmonisierung:
**Die harmonisierte Reihe darf beim Wechsel MODIS → VIIRS keinen Sprung haben und muss in jedem beliebigen
Gebiet und Zeitraum durch einfaches Summieren entstehen.**

---

## 2. Architektur und Zuständigkeiten

```
FIRMS CSV ──► backend/ingest.py ──► data/raw/*.parquet          (vorhanden: kanonisch, NICHT harmonisiert)
                                          │
                                          ▼
                        ┌─────────── DEIN TEIL ───────────┐
                        │ A) backend/harmonization.py     │
                        │    harmonize(df) -> (df, info)  │
                        │ ODER                            │
                        │ B) data/harmonized/*.parquet    │
                        │    + harmonization.json         │
                        └─────────────────────────────────┘
                                          │
                                          ▼  backend/build.py (vorhanden, automatisch)
data/processed/  hotspots/y=YYYY/m=M/*.parquet · cells_month.parquet · cells_day.parquet · meta.json
                                          │
                                          ▼  backend/main.py  FastAPI + DuckDB (vorhanden)
/api/meta · /api/timeseries · /api/heatmap · /api/hotspots.csv
                                          │
                                          ▼
index.html  Karte · Kalender · Anomalien · Harmonisierungs-Check · Methodik   (vorhanden)
```

| Bereich | Status | Wer |
|---|---|---|
| Einlesen der FIRMS-Dateien, kanonisches Schema | fertig | – |
| **Harmonisierung (`weight`, `frp_h`)** | **Platzhalter, muss ersetzt werden** | **du** |
| **Validierungskennzahlen** | **fehlt** | **du** |
| **Methodik-Text** `content/methodology.md` | **leer, Überschriften vorbereitet** | **du / Team** |
| Speicherung, Aggregate, API, Website | fertig | – |
| Archivdaten (mehrere Jahre) | beantragt, noch nicht da | Team |

---

## 3. Eingabe: kanonisches Rohschema (`data/raw/*.parquet`)

Eine Zeile pro Hotspot, MODIS und VIIRS in **derselben** Tabelle. Lesen mit
`pd.concat(pd.read_parquet(f) for f in glob('data/raw/*.parquet'))` oder
`duckdb.sql("SELECT * FROM 'data/raw/*.parquet'")`. Duplikate entfernt bereits `backend.data.dedupe`.

| Spalte | Typ | Beispiel | Bedeutung |
|---|---|---|---|
| `latitude`, `longitude` | float64 | -12.34567 | Pixelmitte, WGS84 |
| `acq_date` | str | `2026-11-07` | UTC, ISO |
| `acq_time` | int32 | `1340` | UTC, HHMM |
| `satellite` | str | `Terra`, `Aqua`, `N`, `N20`, `N21` | `N` = Suomi NPP |
| `sensor` | str | `MODIS` / `VIIRS` | |
| `confidence` | str | `57` / `n` | Rohwert (MODIS 0–100, VIIRS l/n/h) |
| `conf_class` | str | `low` / `nominal` / `high` | MODIS: <30 / 30–79 / ≥80 |
| `frp` | float32 | 13.67 | Fire Radiative Power in MW, **sensorspezifisch** |
| `daynight` | str | `D` / `N` | |
| `type` | Int8, nullable | 0 | 0 Vegetation, 1 Vulkan, 2 statische Landquelle, 3 offshore. **Nur in Archivdaten**, bei NRT leer |
| `version` | str | `6.1NRT`, `2.0NRT` | |
| `source` | str | `NRT` / `SP` | SP = Standard Processing (Archiv) |

Eindeutiger Schlüssel eines Hotspots: `latitude, longitude, acq_date, acq_time, satellite`.

**Aktuell vorhandene Daten** (echte FIRMS-NRT-Daten, global, 07.–14.11.2026):

| Datei | Sensor / Satellit | Zeilen |
|---|---|---|
| `fire_nrt_M-C61_847347.parquet` | MODIS Aqua 48.658, Terra 27.265 | 75.923 |
| `fire_nrt_J1V-C2_847348.parquet` | VIIRS NOAA-20 | 399.584 |
| `fire_nrt_J2V-C2_847349.parquet` | VIIRS NOAA-21 | 393.682 |

Wichtige Eigenheiten:
- **Zwei VIIRS-Satelliten gleichzeitig** (NOAA-20 und NOAA-21), dazu ab Archivdaten auch S-NPP. Roh kommen
  auf einen MODIS-Hotspot etwa 10 VIIRS-Hotspots. Mehrfacherfassung desselben Feuers durch mehrere
  Satelliten ist normal und muss die Harmonisierung berücksichtigen.
- **FRP ist nicht direkt vergleichbar:** Median MODIS 15,7 MW, VIIRS 5–6 MW (kleinere Pixel, kleinere Feuer).
- **Letzter Tag unvollständig:** Der 14.11. enthält nur einen Teil des Tages, nicht für Kalibrierung nutzen.
- **Keine `type`-Spalte** in NRT: Falschalarme (Industrie, Gasfackeln) lassen sich erst mit Archivdaten
  über `type` filtern, oder über eigene Logik (z. B. statische Quellen an jedem Tag am selben Ort).
- **Mit einer Woche lässt sich keine Methode ernsthaft kalibrieren.** Die Woche dient zum Entwickeln und
  Testen der Pipeline. Kalibrieren erst mit den Archivdaten (Überlappungszeitraum MODIS/VIIRS ab 2012).

---

## 4. Lieferung: harmonisierte Werte pro Hotspot

### 4.1 Zwei zusätzliche Spalten

| Spalte | Typ | Pflicht | Bedeutung |
|---|---|---|---|
| `weight` | float ≥ 0, kein NaN | ja | Beitrag dieses Hotspots zur **harmonisierten Hotspot-Anzahl** in **MODIS-Äquivalenten** |
| `frp_h` | float ≥ 0, kein NaN | ja | Beitrag zur **harmonisierten FRP** in MW (MODIS-Äquivalent) |

Die Website rechnet **ausschließlich** so:

```
harmonisierte Anzahl(Gebiet, Zeitraum) = SUM(weight) über alle Hotspots darin   (beide Sensoren!)
harmonisierte FRP   (Gebiet, Zeitraum) = SUM(frp_h)  über alle Hotspots darin
```

### 4.2 Regeln, die eingehalten werden müssen

1. **Additivität:** Die Werte müssen über beliebige Gebiete und Zeiträume summierbar sein. Keine
   Normalisierung, die nur für ein bestimmtes Gebiet oder eine Regionsgröße gilt.
2. **Keine Doppelzählung zwischen Sensoren:** Im Überlappungszeitraum entscheidet die Methode, wie MODIS und
   VIIRS kombiniert werden, z. B. MODIS `weight = 0` und VIIRS `weight = k`, oder beide je 0,5 gewichtet.
   Die Summe über **beide** Sensoren muss das harmonisierte Ergebnis sein.
3. **Einheit = MODIS-Äquivalent:** Die lange MODIS-Historie ist die Referenz. VIIRS wird auf MODIS-Niveau
   gebracht, nicht umgekehrt (MODIS kann kleine Feuer, die es nie gesehen hat, nicht nachträglich erfinden).
4. **Zeilen dürfen entfernt werden** (z. B. Falschalarme), aber keine erfunden. Alle anderen Spalten bleiben
   unverändert.
5. **Raster-Methoden sind erlaubt:** Liefert die Methode Werte pro Zelle und Zeitraum, dann gilt
   `weight = harmonisierter_Zellwert / Anzahl_Rohhotspots_dieser_Zelle_und_Periode` (gleichmäßig verteilt).
   Dasselbe für `frp_h` mit der Roh-FRP als Verteilungsschlüssel, falls sinnvoll.
6. **Filter der App:** Die App schließt standardmäßig `conf_class = 'low'` aus und kann auf `type = 0`
   filtern. Die Gewichte müssen trotzdem für **alle** Zeilen gesetzt sein, auch für low-confidence.
   Empfehlung: Kalibrierung ohne low-confidence, Anwendung auf alle Zeilen.

### 4.3 Weg A: Funktion ersetzen (bevorzugt)

Datei `backend/harmonization.py`, Funktion `harmonize(df)`. Signatur und Rückgabe **nicht ändern**:

```python
INFO = {
    "name": "quantile-mapping-grid",   # kurzer Name, wird in der App angezeigt
    "version": "1.0",
    "status": "final",                 # "placeholder" oder "final" (App zeigt Badge)
    "description": "Ein Satz, was die Methode macht.",
}

def harmonize(df: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    """df: kanonisches Schema (Abschnitt 3), alle Sensoren/Satelliten, ohne Duplikate.
    Rückgabe: (df + Spalten weight, frp_h; info-dict)."""
    d = df.copy()
    ...                                   # Methode
    d["weight"] = ...
    d["frp_h"] = ...
    info = dict(INFO, params={            # alles JSON-serialisierbar, wird als key = value angezeigt
        "viirs_start": "2012-01",         # WICHTIG: erster VIIRS-Monat 'YYYY-MM', die App zeichnet dort
                                          # eine senkrechte Linie im Harmonisierungs-Check
        "r_before": 0.81, "r_after": 0.97,  # empfohlen: Validierungskennzahlen (siehe Abschnitt 7)
        "bias_before": 2.3, "bias_after": 0.04,
    })
    return d, info
```

Der aktuelle Platzhalter in derselben Datei zeigt ein vollständiges, lauffähiges Beispiel.

### 4.4 Weg B: fertige Tabelle ablegen

Für Methoden, die außerhalb dieses Repos laufen (Notebook, R, großer Rechner):

- Dateien: `data/harmonized/*.parquet` (bevorzugt, zstd) oder `*.csv`
- Spalten: **alle Spalten aus Abschnitt 3 plus `weight` und `frp_h`**
- Optional `data/harmonized/harmonization.json` mit demselben Aufbau wie `info` oben
- Sobald dort Dateien liegen, wird `harmonization.py` übersprungen und die Tabelle unverändert verwendet
- Mehrere Dateien sind erlaubt, z. B. eine pro Jahr: `harmonized_2003.parquet`, …
- **GitHub-Limit: 100 MB pro Datei.** Bei Parquet mit zstd sind das grob 5–6 Mio. Zeilen pro Datei

---

## 5. Was daraus automatisch entsteht (nicht selbst schreiben)

`python -m backend.build` (läuft auch automatisch beim API-Start, wenn sich Eingaben geändert haben) erzeugt
`data/processed/`. Dieser Ordner wird **nicht** committet und **nicht** von Hand befüllt.

| Datei | Inhalt | Wofür |
|---|---|---|
| `hotspots/y=YYYY/m=M/*.parquet` | alle Hotspots inkl. `weight`, `frp_h`, partitioniert | CSV-Export, Heatmap bei starkem Zoom |
| `cells_month.parquet` | `period` (YYYY-MM), `lat`, `lon` (0,1°-Zellmitte), `sensor`, `low`, `veg`, `n`, `frp`, `w`, `fh` | Monatskalender, Anomalien, Heatmap |
| `cells_day.parquet` | wie oben, `period` = YYYY-MM-DD, 0,25°-Zellen | Tageskalender, Heatmap |
| `meta.json`, `harmonization.json` | Zeitraum, Zählungen, `info` | `/api/meta` |

`n` = Rohanzahl, `frp` = Roh-FRP, `w` = Σ`weight`, `fh` = Σ`frp_h`, `low` = conf_class low, `veg` = type 0 oder unbekannt.

---

## 6. Schnittstelle zur Website (darf sich nicht ändern)

Die Website (`index.html`) ruft nur diese Endpunkte. **Felder dürfen ergänzt, aber nicht umbenannt oder
entfernt werden.** Gemeinsame Filter: `bbox=w,s,e,n`, `polygon=lat,lon;lat,lon;…`, `start`, `end`
(YYYY-MM-DD), `exclude_low` (true), `vegetation_only` (false).

**`GET /api/meta`**: muss `api: "firecal"` enthalten, sonst fällt die Website auf Demodaten zurück.
```json
{"api":"firecal","version":"1.1","rows":869189,"first_date":"2026-11-07","last_date":"2026-11-14",
 "bbox":[-179.99,-53.2,179.99,72.48],"by_satellite":{"MODIS/Aqua":48658,"VIIRS/N20":399584},
 "has_type":false,
 "harmonization":{"name":"…","version":"…","status":"placeholder|final","description":"…",
                  "params":{"viirs_start":"2012-01","…":"…"}}}
```

**`GET /api/timeseries?granularity=month|day`**: über den **gesamten** Zeitraum, ignoriert start/end:
```json
{"granularity":"day","rows":{"2026-11-07":{
  "modis_n":11378,"viirs_n":110984,"modis_frp":608374.2,"viirs_frp":1279504.0,
  "modis_h_n":0.0,"viirs_h_n":11577.4,"modis_h_frp":0.0,"viirs_h_frp":504379.8,
  "harm_n":11577.4,"harm_frp":504379.8}}}
```
`*_n` / `*_frp` = roh, `*_h_*` = harmonisierter Beitrag je Sensor, `harm_n = modis_h_n + viirs_h_n`.

**`GET /api/heatmap?view=harm|modis|viirs&metric=count|frp&res=0.25`**:
`{"res":0.25,"source":"cells_day","cells":[[lat,lon,wert],…]}`

**`GET /api/hotspots.csv`**: Spalten
`latitude,longitude,acq_date,acq_time,satellite,sensor,confidence,conf_class,frp,daynight,type,source,weight,frp_h`

### Was die Website mit den Werten macht

| Website-Element | nutzt |
|---|---|
| Ansicht „Harmonized“ | `harm_n` bzw. `harm_frp` |
| Ansicht „MODIS raw“ / „VIIRS raw“ | `modis_n`/`viirs_n` bzw. `*_frp` |
| Harmonisierungs-Check (Diagramm) | MODIS roh, VIIRS roh, `viirs_h_*`, `harm_*`, Linie bei `params.viirs_start` |
| Info-Kasten „Sensor view“ | `name`, `version`, `status` (Badge), `description`, alle `params` als `key = value` |
| Kalender, Kennzahlen, Anomalie | Werte der gewählten Ansicht; Monatsansicht: Median desselben Kalendermonats über alle Jahre |
| Heatmap | Σ`weight` bzw. Σ`frp_h` pro Rasterzelle |

**Konsequenz für die Methode:** Im Diagramm „Harmonisierungs-Check“ soll die schwarze Linie (harmonisiert)
im Überlappungszeitraum nahe an MODIS roh liegen und am VIIRS-Start **keinen Sprung** zeigen. Das ist der
sichtbare Beweis für die Jury.

---

## 7. Erwartete Validierung

Bitte im Überlappungszeitraum berechnen und in `info["params"]` **und** in `content/methodology.md` eintragen:

| Kennzahl | Vergleich | vorher (VIIRS roh vs. MODIS) | nachher (VIIRS harmonisiert vs. MODIS) |
|---|---|---|---|
| Pearson r | monatliche Summen pro Zelle/Region | | |
| Bias (Mittelwert der Differenz) | dito | | |
| MAE | dito | | |

Empfehlungen:
- Auf Zeiträumen validieren, die **nicht** zur Kalibrierung benutzt wurden (z. B. kalibrieren 2012–2018,
  testen 2019–heute).
- Getrennt für mindestens 2–3 Regionen oder Biome (z. B. Savanne Afrika, Amazonas/Cerrado, Boreal),
  weil der Zusammenhang je nach Feuerart unterschiedlich ist.
- Für Anzahl **und** FRP.

---

## 8. Methodik-Text

`content/methodology.md` (Markdown) wird unten in der Website gerendert. Überschriften sind vorbereitet:
Data · Harmonization of MODIS and VIIRS · Validation (Tabelle) · Burning activity calendar and anomalies ·
Limitations · References. Nur die Stellen „To be added.“ ersetzen. Sprache: Englisch (Jury international).

---

## 9. Grenzen der Infrastruktur

| Punkt | Wert |
|---|---|
| API-Arbeitsspeicher | ~0,3 GB, unabhängig von der Datenmenge (liest Parquet von der Platte) |
| Build-Schritt (Weg A) | ~0,4 GB RAM pro Mio. Hotspots, weil `harmonize()` ein pandas-DataFrame bekommt |
| Erwartetes Volumen voller Archive | mehrere 100 Mio. Hotspots weltweit (MODIS ab 2000, VIIRS S-NPP ab 2012, NOAA-20 ab 2018, NOAA-21 ab 2023) |
| Empfehlung bei voller Historie | Pilotregion(en) statt global, **oder** Weg B mit jahresweise harmonisierten Dateien |
| Räumliche Genauigkeit der Aggregate | 0,1° (Monat) bzw. 0,25° (Tag); Gebietsfilter auf Zellmittelpunkt |

---

## 10. Abnahme-Checkliste

```bash
pip install -r requirements.txt
python -m backend.build --force          # muss ohne Fehler durchlaufen
python - <<'EOF'
import pandas as pd, glob, json
from backend.data import validate
df = pd.read_parquet("data/processed/hotspots")             # alle Partitionen
validate(df, harmonized=True)                                # Spalten, keine NaN, weight >= 0
info = json.load(open("data/processed/harmonization.json"))
assert info["status"] == "final" and "viirs_start" in info["params"]
m = df.assign(month=df.acq_date.str[:7]).groupby(["month", "sensor"]).agg(n=("weight", "size"), w=("weight", "sum")).unstack()
print(m.tail(24))     # harmonisierte Summe (w, beide Sensoren) soll im Overlap nahe an MODIS-n liegen
EOF
uvicorn backend.main:app --reload       # dann http://localhost:8000 und /docs prüfen
```

- [ ] `build` läuft ohne Fehler, `validate(..., harmonized=True)` besteht
- [ ] `/api/meta` zeigt den neuen Methodennamen und `status: "final"`
- [ ] `params.viirs_start` gesetzt (YYYY-MM)
- [ ] Im Harmonisierungs-Check kein Sprung am VIIRS-Start, harmonisiert ≈ MODIS im Überlappungszeitraum
- [ ] Validierungskennzahlen in `params` und in `content/methodology.md`
- [ ] Keine Spalten oder API-Felder umbenannt; `data/processed/` nicht committet; kein MAP_KEY im Code

---

## 11. Bitte nicht

- Spaltennamen des kanonischen Schemas oder API-Antworten umbenennen (die Website bricht sonst).
- Direkt in `data/processed/` schreiben (wird bei jedem Build überschrieben).
- `weight` pro Gebiet normalisieren (verletzt die Additivität, Abschnitt 4.2).
- Rohdaten in `data/raw/` verändern; neue Rohdaten immer über `python -m backend.ingest` einlesen.
- Secrets (FIRMS MAP_KEY) in Dateien schreiben; dafür gibt es das GitHub-Secret `FIRMS_MAP_KEY`.

## 12. Offene Fragen an die Harmonisierung

1. Wird pro Hotspot, pro Rasterzelle oder pro Region kalibriert? (Bestimmt Weg A oder B und Regel 4.2.5.)
2. Wie werden die bis zu drei VIIRS-Satelliten gleichzeitig behandelt: alle zusammen oder je Satellit ein Faktor?
3. Wird im Überlappungszeitraum MODIS, VIIRS oder eine Mischung verwendet?
4. Welche Pilotregionen und welcher Zeitraum werden für die Abgabe gezeigt?
5. Wird FRP mit derselben Methode harmonisiert wie die Anzahl oder separat?
