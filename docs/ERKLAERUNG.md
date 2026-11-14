# FireCal: Wie alles funktioniert (mit allen Formeln)

Stand: 14.11.2026. Diese Datei erklärt jede Zahl, die die App zeigt: woher sie kommt, wie sie berechnet
wird und für welchen Zeitraum sie gilt. Code-Stellen stehen jeweils in Klammern.

---

## 1. Überblick in einem Satz pro Schritt

1. **Rohdaten**: NASA FIRMS liefert „Hotspots“: Pixel, in denen ein Satellit ein Feuer erkannt hat.
2. **Aufbereitung** (`backend/build.py`): Alle Dateien werden in ein einheitliches Schema gebracht, Duplikate
   entfernt, unvollständige Tage weggelassen.
3. **Harmonisierung** (`backend/harmonization.py`): MODIS-Werte werden auf das Niveau von VIIRS umgerechnet, damit
   man 2005 mit 2025 vergleichen kann.
4. **Vorberechnete Summen**: Für schnelle Antworten werden Summen pro Rasterzelle und Monat/Tag gespeichert.
5. **API** (`backend/main.py`): FastAPI + DuckDB beantwortet Abfragen (Gebiet, Zeitraum, Filter).
6. **Dashboard** (`index.html`): Alle Ansichten hängen an einer Auswahl aus Gebiet, Zeitraum und Filtern.

```
FIRMS-Dateien / Tabelle A / Server-Archiv
        │  Schritt 1 stage: Schema vereinheitlichen, nach Jahr/Monat aufteilen
        │  Schritt 2 dedupe: Duplikate raus, unvollständigen Tag raus, Kalibrier-Summen sammeln
        │  Schritt 3 calibrate: Faktoren (Tabelle C des Teams oder selbst berechnet)
        │  Schritt 4 harmonize: Gewichte pro Hotspot + Summen pro Zelle
        ▼
data/processed/  hotspots/  cells_month/  cells_day/  series_month/  series_day/  dist_month/  meta.json
        ▼
API (/api/...)  ──►  Dashboard
```

---

## 2. Begriffe

| Begriff | Bedeutung |
|---|---|
| Hotspot / Detektion | Ein Pixel, in dem der Satellit beim Überflug ein aktives Feuer erkannt hat. Ein großes Feuer kann viele Hotspots haben. |
| **FRP** (Fire Radiative Power) | Abgestrahlte Leistung des Feuers in diesem Pixel, in **Megawatt (MW)**. Maß für die Intensität. Summe über Hotspots = gesamte Feuerleistung. |
| MODIS | Sensor auf den Satelliten **Terra** (seit 2000) und **Aqua** (seit 2002). Pixel ca. **1 km**. |
| VIIRS | Sensor auf **S-NPP** (2012 bis Ende 2026), **NOAA-20** (seit 2018), **NOAA-21** (seit 2023). Pixel ca. **375 m**. Sieht viel mehr kleine Feuer. |
| Konfidenz | Wie sicher die Erkennung ist: `low`, `nominal`, `high`. Für Statistiken wird `low` standardmäßig weggelassen. |
| NRT / SP | Near Real Time (schnell, vorläufig) bzw. Standard Product (Archiv, endgültig). Gibt es beide, gewinnt SP. |
| UTC | Alle Datumsangaben sind in UTC (Weltzeit). |

---

## 3. Aufbereitung (`backend/build.py`)

### 3.1 Einheitliches Schema
Jede Eingabedatei (rohe FIRMS-CSV/Parquet, Tabelle A des Teams, FIRMS-ZIP über `backend.ingest`) wird per SQL in
dieselben Spalten gebracht:

| Spalte | Regel |
|---|---|
| `latitude`, `longitude` | auf 5 Nachkommastellen gerundet |
| `acq_date`, `acq_time` | Datum `YYYY-MM-DD` und Uhrzeit `HHMM` (UTC); bei Tabelle A aus `datetime_utc` |
| `satellite` | vereinheitlicht: `T`→Terra, `A`→Aqua, `N`→SNPP, `1`/`J1`→N20, `J2`→N21 |
| `sensor` | MODIS oder VIIRS (aus `sensor` bzw. `instrument`) |
| `conf_class` | MODIS: Konfidenz < 30 → low, 30–79 → nominal, ≥ 80 → high. VIIRS: `l`/`n`/`h`. Unbekannt → nominal |
| `pixel_km2` | Pixelfläche = `scan × track` (km × km) |
| `local_hour` | Lokale Sonnenzeit = `(UTC-Stunde + Minute/60 + Längengrad/15) mod 24` |
| `source` | NRT, wenn die Version „NRT“ enthält oder leer ist, sonst SP |

### 3.2 Duplikate und unvollständige Tage
* **Duplikat** = gleicher Pixel (`latitude`, `longitude`), gleiches Datum, gleiche Uhrzeit, gleicher Satellit.
  Davon bleibt eine Zeile, bevorzugt SP vor NRT.
* **Unvollständiger Tag**: Der neueste Tag eines NRT-Downloads ist meist nur zum Teil da. Besteht der letzte Tag
  nur aus NRT-Zeilen, wird er weggelassen (aktuell: 14.11.2026).

### 3.3 Warum es mit großen Datenmengen geht
Alles läuft in DuckDB **Datei für Datei und Monat für Monat** mit Speicherlimit (Standard: halber RAM, max. 8 GB,
einstellbar mit `FIRECAL_DUCKDB_MEMORY`) und 2 Threads (`FIRECAL_DUCKDB_THREADS`). Duplikate werden per
Gruppierung entfernt, die auf die Platte auslagern kann. Getestet: 10,9 Mio. Hotspots in einem Monat mit 600 MB,
26,5 Mio. über 192 Monate mit 300 MB Speicher.
Test: 42,7 Mio. Hotspots in 2¼ Minuten mit 1,3 GB RAM. Euer Server-Archiv (317 Mio.) braucht geschätzt 15–20 Minuten.
Neu gebaut wird in `data/processed.new/`, erst am Ende wird umgeschaltet: **Die App läuft während des Baus mit
den alten Daten weiter.**

---

## 4. Harmonisierung (`backend/harmonization.py`)

**Ziel:** Eine Zeitreihe, die über alle Jahre vergleichbar ist, auf **VIIRS-Niveau**. Problem: VIIRS sieht viel mehr
(kleine) Feuer als MODIS. Würde man einfach zählen, sähe es aus, als hätte es ab 2012 plötzlich viel mehr gebrannt.

### 4.1 Regionen (Platzhalter des Datenteams)
In dieser Reihenfolge (`backend/regions.py`, gleich in `index.html`):

| Region | Regel |
|---|---|
| Amerika | Länge < −30° |
| Ozeanien | Länge ≥ 110° und Breite < −10° |
| Asien | Länge ≥ 55° |
| Afrika | −20° ≤ Länge < 55° und Breite < 35° |
| Europa/Westasien | alles andere |

### 4.2 Kalibrierfaktor (Tabelle C)
Liegt eine Tabelle des Teams vor (`data/calibration/` oder `calibration*` im Server-Ordner), werden deren Faktoren
benutzt. Sonst rechnet FireCal sie mit derselben Methode selbst:

1. Nur Hotspots **ohne** `low`-Konfidenz und **ab dem VIIRS-Start (20.01.2012)**.
2. Einteilung in **Zellen von 2° × 2°** und **Fenster von 7 Tagen**.
3. Pro Zelle und Fenster: Anzahl und FRP-Summe für MODIS und für VIIRS.
4. Es zählen nur Zellen-Fenster, in denen **beide** Sensoren mindestens einen Hotspot haben.
5. Pro Region:

```
factor_n   = Σ VIIRS-Anzahl  /  Σ MODIS-Anzahl        (Summe über alle Zellen-Fenster der Region)
factor_frp = Σ VIIRS-FRP     /  Σ MODIS-FRP
factor_p25, factor_p75 = 25. und 75. Perzentil der Verhältnisse VIIRS/MODIS der einzelnen Zellen-Fenster
r_log_frp  = Korrelation (Pearson) von ln(MODIS-FRP) und ln(VIIRS-FRP) über die Zellen-Fenster   (Güte, 0–1)
```

6. Hat eine Region weniger als **20** Zellen-Fenster, gilt der globale Faktor (`used_global_fallback = true`).

Beispiel (Daten 01.–13.11.2026): Afrika factor_n = 11,1 (VIIRS findet 11× so viele Hotspots), factor_frp = 3,6.
**FRP ist zwischen den Sensoren viel vergleichbarer als die Anzahl.**

### 4.3 Gewicht pro Hotspot
Jeder Hotspot bekommt Gewichte. Jede harmonisierte Zahl in der App ist dann einfach eine **Summe**:

| Fall | `weight` (Anzahl) | `frp_h` (FRP) |
|---|---|---|
| VIIRS-Hotspot an einem Tag mit VIIRS-Daten (ab 20.01.2012) | 1 | `frp` |
| MODIS-Hotspot an einem Tag **ohne** VIIRS-Daten | `factor_n` | `frp × factor_frp` |
| alle anderen (MODIS an VIIRS-Tagen, VIIRS vor 2012) | 0 | 0 |

```
Harmonisierte Anzahl eines Gebiets/Zeitraums = Σ weight
Harmonisierte FRP                              = Σ frp_h
Unsicherheitsband                              = dieselbe Summe mit factor_p25 bzw. factor_p75
MODIS × Faktor (Kontrolle, an jedem Tag)       = Σ MODIS-Hotspots × factor_n   bzw.  Σ MODIS-FRP × factor_frp
```

**Bedeutung des Schalters „Values“ in der App:**
* **Harmonized**: Σ weight bzw. Σ frp_h, die vergleichbare Reihe. Vor 2012 umgerechnet (schraffiert), danach echt VIIRS.
* **Raw all**: MODIS + VIIRS einfach addiert. Springt, sobald ein Satellit dazukommt. Nicht über Jahre vergleichbar.
* **MODIS** / **VIIRS**: nur ein Sensor, so wie erkannt.

Bekannte Schwäche (Datenteam arbeitet daran): Ab 2018 und 2023 kommen weitere VIIRS-Satelliten dazu, „echt VIIRS“
zählt dann mehr Überflüge. Die Korrektur je Satelliten-Kombination fehlt noch.

---

## 5. Vorberechnete Summen

Zellmittelpunkt bei Rastergröße `res`: `floor(Breite / res) × res + res / 2` (für Länge genauso).

| Ordner | Inhalt | Zweck |
|---|---|---|
| `hotspots/y=/m=/` | jeder Hotspot mit Gewichten | Karte (Punkte), Export, exakte Statistik |
| `cells_month/` | Monat × 0,1°-Zelle × Sensor × Satellit × Filter | Kalender, Zeitreihe für ein Gebiet |
| `cells_day/` | Tag × 0,25°-Zelle × … | Tagesansicht, Karte |
| `series_month/`, `series_day/` | wie oben, aber ohne Raum (ganze Welt) | sehr schnelle Zeitreihe ohne Gebiet |
| `dist_month/` | Monat × 1°-Zelle × FRP-Klasse × Ortsstunde | Verteilungen über lange Zeiträume |

Gespeicherte Summen je Zeile: `n` (Anzahl roh), `frp` (FRP roh), `n_high`, `w`/`w_lo`/`w_hi` (harmonisierte Anzahl
mit Band), `fh`/`fh_lo`/`fh_hi` (harmonisierte FRP), `wc`/`fc` (MODIS × Faktor). Die Filter (`low`, Vegetation,
Nacht, Satellit) bleiben als Spalten erhalten, damit sie auf den Summen funktionieren.

**Gebiet auf Zellen:** Eine Zelle zählt zum Gebiet, wenn ihr **Mittelpunkt** im Rechteck/Polygon liegt. Bei kleinen
Gebieten ist das ungenau; die Karte und der Export nutzen deshalb die einzelnen Hotspots.

---

## 6. Filter: was sie beeinflussen

| Filter | wirkt auf |
|---|---|
| Metric: Hotspots / FRP | alle Ansichten (Anzahl oder MW) |
| per day | Wert ÷ Tage der Periode (Monate mit 28–31 Tagen und angeschnittene Perioden werden vergleichbar) |
| Values | siehe 4.3 |
| Satellites, Time of day | **nur Rohwerte und Karte**. Harmonisierte Werte nutzen immer alle Satelliten und Tag+Nacht, weil die Faktoren so definiert sind. Es erscheinen nur Satelliten, die in den Daten vorkommen. |
| incl. low confidence | ohne Haken: `low` überall weggelassen |
| vegetation only | nur `type = 0` (Vegetationsfeuer); Zeilen ohne Typ (NRT) bleiben drin |
| Calendar: Monthly / Daily | Auflösung aller Zeitansichten; Daily zeigt höchstens die letzten ~13 Monate |
| Gebiet (Karte) | alle Ansichten; die Kalibriertabelle gilt global (je Region) |

---

## 7. Jede Ansicht: Formel und Zeitraum

Jede Karte im Dashboard hat ein Zeit-Abzeichen 🕒 neben dem Titel. Es zeigt genau den Zeitraum, den die Ansicht nutzt.

| Ansicht | Zeitraum | Berechnung |
|---|---|---|
| **Zeitleiste** | alle geladenen Perioden | Wert je Monat/Tag (siehe Values). Mit den Griffen wählt man die **Auswahl** für alle anderen Ansichten. |
| **Karte** | Auswahl, oder die angeklickte Periode | Herausgezoomt: Dichte = Summe pro Rasterzelle ÷ Anzahl Perioden, Farbe relativ zum 99. Perzentil. Rastergröße nach Zoom: `360 / (256 · 2^Zoom) · 8` Grad, gerundet auf 0,01–4°. Ab Zoom 7: echte Hotspots (die 15.000 stärksten nach FRP), Größe ∝ log(FRP). Nie umgerechnet. |
| **Kennzahlen** | Auswahl | Harmonisiert = Σ über die Perioden; „pro Tag“ = Summe ÷ Tage; „% from MODIS“ = Σ weight(MODIS) ÷ Σ weight; Nachtanteil = Nacht-Hotspots ÷ alle; High confidence = high ÷ alle. Letzte Periode vs. Median: siehe Anomalie. |
| **Brandkalender** | Auswahl | Ein Kästchen je Monat (bzw. Tag). Farbe relativ zum 95. Perzentil. Schraffiert, wenn > 50 % des harmonisierten Werts aus umgerechnetem MODIS kommen. |
| **Anomalie** | Auswahl; Vergleich: Monat → gleicher Kalendermonat in **allen anderen Jahren**; Tag → andere Tage der Auswahl | `Anomalie % = (Wert − Median) / Median × 100`, absolut: `Wert − Median`. Das eigene Jahr ist nie im Median. Ab 3 Vergleichswerten. |
| **Saisonmuster** | alle Jahre außer dem neuesten; Linie = neuestes Jahr | Je Kalendermonat: Median und Band vom 25. bis 75. Perzentil über die Jahre. |
| **Jahresvergleich** | Jahre in der Auswahl | Summe je Jahr. Vollständig = alle 12 Monate in den Daten. Trend: lineare Regression `y = a + b·Jahr` über vollständige Jahre (kleinste Quadrate), angezeigt als `b ÷ Mittelwert` in % pro Jahr. |
| **Harmonisierungs-Check** | Auswahl | Linien: MODIS roh, VIIRS roh, MODIS × Faktor, harmonisiert mit Band. Wo beide Sensoren laufen, sollte „MODIS × Faktor“ nahe an VIIRS liegen. |
| **FRP-Verteilung** | **eigener Zeitraum wählbar** (Auswahl, Kartenperiode, letzte 7/30/365 Tage, alles, frei) | Anteil der Hotspots je FRP-Klasse (0–1, 1–2, … ≥ 1000 MW) je Sensor. Median in der Legende. |
| **Ortszeit, Konfidenz, Satelliten** | Auswahl | Anteile je Stunde (Ortszeit) / Klasse / Satellit. Klick auf „low“ oder einen Satelliten setzt den Filter. |
| Verteilungen, Genauigkeit | – | Zeiträume bis 62 Tage: exakt aus den Einzelpunkten. Länger: aus `dist_month` (ganze Monate, Gebiet nach 1°-Zellmittelpunkt). Median/90. Perzentil dann innerhalb der FRP-Klasse linear interpoliert: `Untergrenze + Klassenbreite · (Ziel − kumuliert) / Anzahl in der Klasse`. Das Abzeichen zeigt „whole months“. |
| **Raum-Zeit-Matrix** | Auswahl | Die 30 aktivsten Zellen (Zeilen) × Perioden (Spalten). Zellgröße automatisch: kleinste aus 0,25/0,5/1/2/5/10°, die ≥ Gebietsgröße ÷ 12 ist. „per row“ färbt jede Zeile relativ zu ihrem Maximum. |
| **Satelliten-Zeitleiste** | alle geladenen Perioden | Rohe Hotspots je Satellit und Periode, Farbe log10. |
| **Kalibrierfaktoren** | 7-Tage-Fenster, VIIRS-Zeitraum ab 20.01.2012 | Tabelle C (siehe 4.2). Die Region des gewählten Gebiets ist hervorgehoben. |

### Automatische Sätze („What the data says“)
* Stärkste Periode = Maximum der Werte in der Auswahl.
* Aktivstes / ruhigstes Jahr und Trend nur bei ≥ 3 vollständigen Jahren.
* Feuersaison = Monate, deren Median ≥ halbes Maximum des Saisonmusters ist.
* „VIIRS sieht X×“ = Σ VIIRS roh ÷ Σ MODIS roh in der Auswahl (FRP genauso).
* Spitzenstunde = Ortsstunde mit den meisten Hotspots.
* Satellitensprung: erwähnt, wenn VIIRS roh in den 12 Monaten nach einem Satellitenstart > 25 % über den 12 Monaten davor liegt.

### Warnungen
* Weniger als 30 harmonisierte Hotspots pro Periode: Umrechnung unsicher.
* Harmonisierung ist „placeholder“, solange das Team sie nicht als final markiert.

---

### Vergleichsmodus (⇄ Compare)
Zwei Zeitspannen mit gleichem Gebiet und gleichen Filtern. **A** = betrachteter Zeitraum, **B** = Referenz
(gleiche Spanne 1 oder 5 Jahre früher, die Spanne direkt davor oder frei gewählt).
* Tabelle: Summen je Spanne, `A − B` und `A vs B = (A − B) / B × 100`. Anteile (Nacht, High confidence, aus MODIS
  umgerechnet) als Differenz in Prozentpunkten.
* Verlauf: beide Reihen ab dem ersten Tag (bzw. Monat) der jeweiligen Spanne übereinandergelegt.
* FRP-Klassen: Anteil der Hotspots je Klasse für A und B.
* Karten: eigene Leiste wie bei der Hauptkarte (Auto/Dichte/Punkte, Farbe nach Sensor/Konfidenz/FRP, Konfidenz-Chips, Skala,
  Hintergrund, Ortsnamen). Dichte **pro Tag** (Summe ÷ Tage der Spanne), gemeinsame Farbskala (95. Perzentil beider Karten);
  ab Zoom 7 einzelne Hotspots (je Karte die 8.000 stärksten). Beide Karten bewegen sich gemeinsam.
* Spannen bis 400 Tage werden tageweise verglichen, längere monatsweise (ganze Monate).

### Konfidenz auf der Karte
Die Chips „low / nominal / high“ in der Kartenleiste gelten **nur für die Karte** (Punkte und Dichte). Weicht die Wahl
vom Standard ab, rechnet die Dichte aus den Einzelpunkten, weil die Zellsummen nur „low / nicht low“ kennen.

### Vollbild
Jede Ansicht hat oben rechts ⛶ (Vollbild, Esc zum Verlassen).

## 8. Karte: Hintergründe und Satellitenbilder

* Hintergründe: Firefly, Satellit (Esri), Blue Marble (NASA GIBS), Straßen, Topographie, Dunkel-/Hellgrau.
* Tägliche Echtfarbbilder (NASA GIBS) von VIIRS NOAA-20/NOAA-21/S-NPP und MODIS Aqua/Terra. **Datum = Tag, der auf der
  Karte gezeigt wird** (angeklickter Tag, sonst letzter Tag der Auswahl).
* Auf dunklen Hintergründen wechselt die Dichte-Farbskala auf Feuerfarben.

---

### Zoom-Grenze
Die Karte zoomt höchstens so weit, dass die Maßstabsleiste mindestens **200 m** zeigt. Web-Mercator:
`Meter pro Pixel = 156 543 · cos(Breite) / 2^Zoom`; die Leiste ist bis zu 100 px breit, also gilt
`max. Zoom = floor(log2(156 543 · cos(Breite) / 2))` (am Äquator 16, in Mitteleuropa 15).

### Herkunft der Kartenbilder
| Ebene | Quelle |
|---|---|
| Firefly, Satellite, Streets, Topographic, Dark/Light gray | Esri ArcGIS Online Basemaps (World Imagery: Maxar, Earthstar Geographics u. a.; Firefly = abgedunkelte Variante davon) |
| Blue Marble | NASA GIBS, Ebene `BlueMarble_ShadedRelief_Bathymetry` (NASA Earth Observatory) |
| Tägliche Echtfarbbilder | NASA GIBS, Ebenen `*_CorrectedReflectance_TrueColor` von VIIRS NOAA-20/NOAA-21/S-NPP und MODIS Terra/Aqua (dieselben Bilder wie NASA Worldview und die FIRMS-Karte) |
| Grenzen und Ortsnamen | Esri `World_Boundaries_and_Places` |

## 9. Server-Archiv

* `FIRECAL_SOURCE` = Ordner mit den Parquet-Dateien (wird rekursiv durchsucht).
* `FIRECAL_SOURCE_LOAD=0` (verbunden): Die App zeigt nur, was drin ist (Dateien, Zeilen, Zeitraum aus den
  Parquet-Metadaten).
* `FIRECAL_SOURCE_LOAD=1` (laden): Die Hotspots kommen in die App. Beim ersten Mal startet die App sofort mit den
  Repo-Daten und baut das Archiv im Hintergrund; danach schaltet sie um. Im Fuß der Seite steht währenddessen
  „updating the data in the background“.
* Neue Dateien im Ordner werden vom 15-Minuten-Update erkannt und im Hintergrund eingebaut.

Umschalten auf dem Server:
```bash
curl -fsSL https://raw.githubusercontent.com/Putskev/firecal/main/deploy/setup_server.sh | bash -s -- /root/spacechallenge/firms_data/parquet --domain reicht.anohji.dev --load
```

---

## 10. Grenzen (ehrlich)

* Regionen sind grobe Boxen; echte Länder/Provinzen fehlen noch.
* Ein Faktor je Region (noch nicht je Monat und je Satelliten-Kombination).
* Faktoren hängen von der Zellgröße ab (2 km: Asien 8,7; 0,5°: 7,0), das gehört in die Unsicherheit.
* Musterdaten (eine Woche November) sind nicht repräsentativ für andere Monate.
* Gebiete auf Zellbasis sind am Rand ungenau (Mittelpunkt-Regel).
