# Handoff an das Frontend: Performance-Upgrade (Dichte statt Punkte, Punkt-Budget-Balken)

Stand: 14.11.2026. Von: Daten/Backend. An: Frontend-Agent.
Ergänzt das Dokument `HANDOFF_Frontend.md` (Schema der Tabellen, Rechenregeln, UI-Plan). Spaltennamen werden dort erklärt und hier nur wiederholt, wo nötig. Dieses Dokument ist ohne Zugriff auf unseren Rechner verständlich.

**Status:** Alles in Abschnitt 4 (Endpunkte, Würfel in mehreren Auflösungen) ist **geplant, noch nicht gebaut**. Das Backend baut es parallel. Du kannst mit Mock-Daten und den Beispieldateien entwickeln (Abschnitt 9).

---

## 1. Problem

Die Datenbasis hat ca. 500 Mio. einzelne Hotspot-Punkte (VIIRS ab 2012, MODIS ab 2000). Selbst nach der Auswahl einer Region lädt die Seite zu lange, weil immer noch zu viele Einzelpunkte in den Browser kommen. Das Problem ist die Menge der übertragenen und gezeichneten Punkte, nicht das Backend allein.

## 2. Entscheidung (Produkt)

1. **Standardansicht ist Dichte**, nicht Punkte. Sie funktioniert für alle Jahre und beide Sensoren (MODIS und VIIRS) und ist auch bei einer Weltansicht klein.
2. **Einzelpunkte gibt es nur, wenn die Zahl der Punkte im aktuellen Ausschnitt unter einem festen Limit liegt: zunächst 1.000.000.** Darüber zeigt die App nur Dichte.
3. **Ein Balken zeigt dem Nutzer jederzeit**, wie nah er am Limit ist, und erklärt, warum er (noch) keine Punkte sieht.
4. Die Darstellung läuft über **WebGL** (deck.gl auf MapLibre oder gleichwertig). Keine SVG-Karten (Plotly, SVG-Marker, DOM-Elemente pro Punkt).
5. Das Limit ist eine **einzelne Konstante** (`POINT_LIMIT = 1_000_000`), damit wir es nach Tests am Zielgerät ändern können.

Statistik, Kalender und Anomalie (Abschnitt 6 von `HANDOFF_Frontend.md`) sind **nicht betroffen**. Sie laufen weiter über den Würfel und brauchen keine Einzelpunkte.

---

## 3. Ansichtsmodi (Zustandsmodell)

| Modus | Bedingung | Datenquelle | Darstellung |
|---|---|---|---|
| `density` | `count > POINT_LIMIT` oder Zeitraum liegt außerhalb der Rohpunkt-Daten | `/density` (aggregierte Zellen) | WebGL-Heatmap bzw. Zellraster |
| `points` | `count ≤ POINT_LIMIT` und Rohpunkte für den Zeitraum vorhanden | `/points` (binär) | WebGL-Punkte (Scatterplot) |

`count` = Anzahl Hotspots **im aktuellen Kartenausschnitt, Zeitraum und mit den aktuellen Filtern** (Sensor, Konfidenz). Ändert der Nutzer Ausschnitt, Zoom, Zeitraum oder Filter, wird `count` neu angefragt.

**Ablauf bei jeder Änderung:**
1. Debounce 300 ms, laufende Anfragen abbrechen (`AbortController`).
2. `GET /count` (billig, kommt aus dem Würfel).
3. Balken aktualisieren (Abschnitt 5).
4. Entscheiden: `count ≤ POINT_LIMIT` → Modus `points`, sonst `density`.
5. Passende Daten laden. **Die Dichte bleibt als Untergrund sichtbar, bis die Punkte da sind**, damit die Karte nie leer wirkt.
6. Beim Verlassen des Punktmodus Punktpuffer verwerfen (Speicher freigeben).

**Hysterese gegen Flackern:** Wechsle von `points` zu `density` erst bei `count > POINT_LIMIT` und von `density` zu `points` erst bei `count ≤ 0.95 × POINT_LIMIT`.

---

## 4. Daten-Schnittstelle (Vorschlag, wird vom Backend geliefert)

Alle Abfragen nehmen dieselben Filter: `bbox=west,south,east,north` (oder Polygon per POST), `from=YYYY-MM-DD`, `to=YYYY-MM-DD`, `sensor=MODIS|VIIRS|both`, `conf=nominal,high` (Standard: ohne `low`).

### 4.1 `GET /count`
Antwort (JSON):
```json
{"count": 640000, "by_sensor": {"MODIS": 12000, "VIIRS": 628000}, "points_available": true, "approx": true}
```
- `points_available`: `false`, wenn für den Zeitraum keine Rohpunkte vorliegen (z. B. ältere Jahre). Dann immer `density`.
- `approx`: `true`, weil die Zählung aus den Zellen kommt und am Rand des Ausschnitts um wenige Prozent abweichen kann. Für den Balken reicht das.

### 4.2 `GET /density`
Wählt die Auflösung automatisch aus der Zoomstufe (Parameter `zoom=<Zahl>`). Antwort **spaltenweise** (kompakt, direkt in `Float32Array` zu verwandeln):
```json
{"resolution_deg": 0.1, "lat": [..], "lon": [..], "n": [..], "frp_sum": [..], "period": "2019-01..2019-03"}
```
Vorgesehene Auflösungen (Vorschlag, wird anhand der echten Daten bestätigt):

| Kartenzoom | Zellgröße | ca. Zellen weltweit (grob) |
|---|---|---|
| 0–3 | 2° | wenige Tausend |
| 4–5 | 0,5° | einige 10.000 |
| 6–7 | 0,1° | |
| 8 und höher | 0,05° | |

Die Dichte wird auf **Monate** aggregiert. Ein Zeitraum wird für die Dichte auf ganze Monate gerundet, **zeig das dem Nutzer** ("Dichte-Ansicht: ganze Monate"). Falls Wochen- oder Tageswerte nötig sind, klären wir das mit dem Backend (siehe Abschnitt 10).

### 4.3 `GET /points`
Liefert Einzelpunkte als **binär** (Apache Arrow IPC oder Parquet, Spalten wie in Tabelle A von `HANDOFF_Frontend.md`), nicht als JSON oder GeoJSON.
- Spalten für die Karte: `latitude`, `longitude`, `frp`, `sensor`, `conf_class`, `datetime_utc` (optional `daynight`).
- Wird das Limit überschritten, antwortet das Backend mit **HTTP 413** und `{"count": ..., "limit": 1000000}`. Das Frontend darf sich nicht darauf verlassen, aber so ist der Server geschützt.
- Antworten dürfen in **Blöcken** kommen (Streaming), damit der erste Teil schon gezeichnet wird.

### 4.4 `GET /download` (CSV)
Streamt **alle** Hotspots im gewählten Gebiet und Zeitraum (auch MODIS, auch ältere Jahre). Hat nichts mit dem Punkt-Limit zu tun. Der Download läuft als Datei, nicht über die Karte.

### Warum nicht JSON/GeoJSON für Punkte?
Hochgerechnet (Schätzung): 1 Mio. Punkte mit 5 Zahlen sind binär etwa 20 MB, als GeoJSON deutlich über 100 MB. Eine Parse-Zeit von mehreren Sekunden im Browser macht das Limit sinnlos. Bitte Binärformat oder Typed Arrays nutzen und die Dekodierung in einem Web Worker erledigen.

---

## 5. Der Punkt-Budget-Balken

**Zweck:** Der Nutzer versteht, warum er gerade Dichte sieht oder Punkte, und ab wann sich das ändert.

**Wert:** `füllung = min(count / POINT_LIMIT, 1)`. Beschriftung: `"Punkte im Ausschnitt: 640.000 / 1.000.000"` (Tausenderpunkte, deutsches Format).

**Zustände:**

| Füllung | Bedeutung | Text unter dem Balken | Ansicht |
|---|---|---|---|
| < 70 % | Genug Spielraum | "Einzelpunkte werden angezeigt" | `points` |
| 70 % bis 100 % | Nahe am Limit | "Fast am Limit. Beim Verkleinern des Gebiets oder Zeitraums bleiben die Punkte sichtbar" | `points` |
| voll (> 100 %) | Limit überschritten | "Zu viele Punkte (x). Es wird die Dichte gezeigt. Zoome näher heran oder verkürze den Zeitraum, um Einzelpunkte zu sehen" | `density` |
| Rohpunkte nicht verfügbar | Zeitraum außerhalb der Rohdaten | "Für diesen Zeitraum liegt nur die Dichte vor" | `density` (Balken ausgegraut) |

**Anforderungen:**
- Immer sichtbar in der Kartenansicht (z. B. unten links oder in der Seitenleiste), nicht erst bei Überschreitung.
- Aktualisiert sich mit jeder Änderung von Ausschnitt, Zoom, Zeitraum oder Filter (nach dem Debounce). Beim Laden der neuen Zahl kurz einen Lade-Zustand anzeigen, damit er nicht veraltete Werte zeigt.
- Zeigt einen Marker bei 100 % und ein Tooltip: "Ab dieser Zahl wird nur noch Dichte angezeigt".
- **Zugänglich:** `role="progressbar"` mit `aria-valuenow`, `aria-valuemin=0`, `aria-valuemax` und einem verständlichen Text. Die Bedeutung darf nicht nur an Farbe hängen (zusätzlich Text und Muster). Wähle Farben, die auch bei Farbfehlsichtigkeit lesbar sind.
- Optional: Der Nutzer kann auf den Balken klicken und sieht, welche Filter (Sensor, Konfidenz, Zeitraum) den größten Hebel haben, z. B. "Ohne MODIS: 520.000".

---

## 6. Rendering mit WebGL

**Empfehlung:** MapLibre GL als Basiskarte, darauf deck.gl-Layer. (Gleichwertige Lösungen sind in Ordnung, solange alles auf der GPU läuft.)

| Ansicht | Layer | Hinweise |
|---|---|---|
| Dichte | `HeatmapLayer` (Gewicht = `n` oder `frp_sum`) oder ein Zellraster (z. B. `GridCellLayer`/`ScreenGridLayer`) | Farbskala **zugänglich** (z. B. viridis), Legende mit echten Werten. Das Raster ist besser, wenn Nutzer Werte ablesen sollen, die Heatmap für den Gesamteindruck |
| Punkte | `ScatterplotLayer` mit binären Attributen (`Float32Array`) | Radius nach `frp` (begrenzen), Farbe nach `sensor` oder `conf_class`. Kein Clustering im Browser bei 1 Mio. Punkten |

**Wichtig für die Geschwindigkeit:**
- Daten als **Typed Arrays** übergeben (deck.gl unterstützt Daten im Binärformat), keine Objekt-Arrays mit einem Objekt pro Punkt.
- **Picking/Hover** bei vielen Punkten abschalten oder auf die Punktzahl begrenzen. Picking von 1 Mio. Punkten ist teuer. Alternative: bei Klick eine kleine Abfrage nach dem nächsten Punkt an das Backend.
- Dekodierung der Antworten in einem **Web Worker**.
- Zoomstufe oder Ausschnitt ändert sich oft: Anfragen abbrechen, kurz **cachen** (z. B. pro Zoom/Zeitraum/Filter), vorherige Ansicht stehen lassen, bis die neue da ist.
- Bei Verlassen des Punktmodus Puffer freigeben.

**Wichtig:** 1 Mio. Punkte sind für WebGL machbar, aber nicht auf jedem Gerät flüssig (schwache Laptops, Mobilgeräte). Deshalb ist `POINT_LIMIT` eine Konstante. Bitte teste auf einem durchschnittlichen Gerät und melde die Bildrate und die Ladezeit, damit wir das Limit anpassen.

---

## 7. Rolle von MODIS

- MODIS wird in der Dichte wie VIIRS behandelt (beide stehen im Würfel).
- Standardmäßig dürfen auch MODIS-Punkte als Einzelpunkte erscheinen, wenn das Limit nicht überschritten ist. Das lässt sich über eine Konfiguration (`POINT_SENSORS = ["VIIRS", "MODIS"]`) ändern, falls wir entscheiden, MODIS nur noch als Dichte zu zeigen. Das ist noch nicht entschieden (siehe Abschnitt 10).
- Für die **Statistik** ist die Wahl egal. Sie nutzt den Würfel und die Faktoren (siehe `HANDOFF_Frontend.md`, Abschnitte 5 und 6).
- Bei MODIS-Dichte vor 2012 gibt es ohnehin nur 1-km-Pixel. Die Dichte zeigt dort fast dasselbe wie Einzelpunkte, nur lesbarer.

---

## 8. Messlatte (Richtwerte zur Abnahme)

Das sind Vorschläge, keine gemessenen Werte. Bitte am Zielgerät prüfen und uns die Zahlen melden.

| Situation | Ziel |
|---|---|
| Weltansicht, beliebiger Zeitraum (Dichte) | Sichtbar in unter ca. 2 s |
| Zoom/Pan (Dichte) | Flüssig (ca. 30 FPS), Nachladen im Hintergrund |
| Punktansicht an der Grenze (ca. 1 Mio.) | Laden in ca. 5 s oder weniger, Interaktion weiter flüssig |
| Wechsel zwischen den Modi | Ohne Flackern (Hysterese aus Abschnitt 3) |

Bitte miss und logge mindestens: Dauer von `/count`, Dauer und Größe von `/density` und `/points`, Zeit bis zum ersten Bild, Bildrate beim Schwenken.

---

## 9. Wie du ohne das fertige Backend arbeiten kannst

Wir senden dir diese Beispieldateien (alle nur aus **einer Novemberwoche 2026**, Muster, nicht für die Anzeige echter Aussagen):

| Datei | Zweck |
|---|---|
| `harmonisierte_hotspots.csv.gz` (869.190 Punkte, 07.–14.11.2026) | **Testet den Punktmodus.** Liegt knapp unter dem Limit von 1 Mio. |
| `fire_activity_7d_2deg.parquet`, `fire_activity_7d_0.5deg.parquet`, `fire_activity_7d_2km.parquet` | Testen die Dichte (2°: 5.131 Zeilen, 0,5°: 31.376, 2 km: 441.262). Spalten `lat`, `lon`, `n_viirs`, `n_modis`, `frp_viirs`, `frp_modis`, `frp_harmonized`, `period_start` u. a. |

Tipps zum Testen:
- **Mock-Endpunkte** bauen, die diese Dateien nach `bbox` und Datum filtern und `/count`, `/density`, `/points` nachbilden (Formate wie in Abschnitt 4).
- **Überlauf testen:** Um mehr als 1 Mio. Punkte zu bekommen, verdopple die Punktedatei mit zufälligem Versatz (Jitter von ca. 0,01°) oder senke `POINT_LIMIT` temporär auf z. B. 100.000. So siehst du den vollen Balken und den Moduswechsel.
- Wenn die echten Endpunkte stehen, tauschst du nur die Datenquelle, nicht die Logik. Halte die Feldnamen aus Abschnitt 4 konsistent.

---

## 10. Offene Entscheidungen (mit dem Backend klären)

1. **MODIS als Einzelpunkte erlauben oder nur als Dichte?** Standard im Entwurf: erlaubt, solange unter dem Limit.
2. **Zeitauflösung der Dichte:** Monate (Entwurf). Falls Nutzer Wochen sehen sollen, bräuchte das Backend zusätzlich einen Wochenwürfel (größer). Bitte melden, ob das gebraucht wird.
3. **Wie weit zurück gibt es Rohpunkte?** Wird nach den echten Daten festgelegt (grob: aktuelle ein bis zwei Jahre, sonst nur Dichte). Das Frontend liest das aus `points_available` im `/count`, nicht fest im Code.
4. **Vektor-Kacheln statt eigener API?** Alternative: vorberechnete Kacheln (z. B. PMTiles) für Dichte. Das würde Caching vereinfachen. Sag uns, ob deine Kartenbibliothek das besser unterstützt.
5. **Endgültiges Limit:** 1 Mio. ist der Startwert. Nach deinen Messungen am Zielgerät kann es sinken oder steigen.

---

## 11. Zusammenfassung der Aufgaben für das Frontend

1. Karte auf WebGL (MapLibre + deck.gl) umstellen. Kein SVG/Plotly für Punkte.
2. Zwei Modi (`density`, `points`) mit Zustandsmodell und Hysterese (Abschnitt 3) bauen.
3. `POINT_LIMIT = 1_000_000` als eine Konstante.
4. Punkt-Budget-Balken inkl. Zuständen, Texten und Barrierefreiheit (Abschnitt 5).
5. Binäre Daten, Web Worker, Abbrechen und Cachen von Anfragen.
6. Mit Mock-Endpunkten und den Beispieldateien testen, Messwerte (Abschnitt 8) an das Backend melden.
7. Kalender, Anomalie und Download bleiben wie in `HANDOFF_Frontend.md`.
