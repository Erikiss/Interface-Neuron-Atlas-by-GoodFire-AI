# Neuron Atlas Interface

Such-Oberfläche, REST-API, MCP-Server (für Chatbots), Download-Werkzeug und Geometrie-Toolkit
für den [Qwen3.6 Neuron Atlas](https://www.goodfire.com/research/public_neuron_atlas) von Goodfire.

Der Atlas beschreibt, worauf einzelne Neuronen des Sprachmodells Qwen3.6-35B-A3B reagieren.
Die Original-Seite ist schön anzusehen, aber schwer zu drucken, schwer zu kopieren, nur per
Stichwort durchsuchbar und ohne Schnittstelle. Dieses Projekt setzt genau dort an:

| | Original-Seite | Dieses Interface |
|---|---|---|
| Umfang | Stichprobe von 3.000 Polen | alle 6.492 veröffentlichten Pole und 142 weitere Circuit-Neuronen (Stichprobe ist markiert) |
| Suche | Teilstring in Kurzbeschreibung | Volltext mit Ranking, Phrasen, Ausschluss, Präfix, ODER, Regex, Ähnlichkeitssuche; Treffer pro Modus |
| Filter | Kategorie, Layer | zusätzlich Pol, Expert, Anzahl Modi, Simulierbarkeit, Schadensanteil, vorhandene Daten … |
| Tiefe | Beschreibungen | auf Wunsch auch Modus-/Subcircuit-Texte, Erklärungen, Reports und Beispielpassagen |
| Ausgabe | nur im Browser | JSON, Markdown, Klartext, druckfertiges HTML, CSV, NDJSON |
| Maschinen | – | REST-API (OpenAPI), MCP-Server, `llms.txt`, Batch-Abfragen, Streaming-Export |
| Offline | – | lokaler Spiegel einzelner Regionen oder des ganzen Atlas |
| Analyse | – | Tabellen (CSV/Parquet), semantische Karten, Neuron-Graph |

> **Inoffiziell.** Die Daten, Texte und Visualisierungen des Atlas gehören Goodfire AI.
> Dieses Projekt liest nur die veröffentlichten Dateien und enthält selbst keine Atlas-Daten.
> Bitte den Abschnitt [Rechtliches und Rücksicht](#rechtliches-und-rücksicht) lesen, bevor du
> größere Mengen herunterlädst oder den Server öffentlich erreichbar machst.

## Schnellstart

```bash
git clone https://github.com/Erikiss/Interface-Neuron-Atlas-by-GoodFire-AI.git
cd Interface-Neuron-Atlas-by-GoodFire-AI
python -m venv .venv && . .venv/bin/activate
pip install -e ".[all]"          # API, MCP, UMAP-Karten, Parquet

neuron-atlas serve               # http://127.0.0.1:8000  (Oberfläche, API unter /docs, MCP unter /mcp)
```

Beim ersten Start werden sieben Index-Dateien geladen (zusammen ca. 3 MB) – damit ist der
gesamte Atlas sofort durchsuchbar. Details zu einem Pol werden erst geholt, wenn man ihn öffnet,
und danach lokal vorgehalten.

Mit Docker:

```bash
docker compose up -d             # http://localhost:8000, Daten im Volume "atlas-data"
```

## Begriffe in Kürze

- **Neuron**: `L30_E47_N449` = Layer 30 / Expert 47 / Neuron 449 (angezeigt als `L30/E47/N449`).
- **Pol**: Jedes Neuron wird für positive und negative Ausgabe getrennt gelesen:
  `L30_E47_N449__pos` und `L30_E47_N449__neg`.
- **Modus**: eine Bedeutung, auf die ein Pol feuert. Ein Pol hat einen oder mehrere Modi; jeder
  Modus besteht aus **Subcircuits** (Gruppen von Feuerungen mit denselben Ursachen im Modell).
- **Kategorie**: `mono` (ein Modus), `fn` (ein dominanter Modus), `poly` (mehrere Modi),
  `dark` (nicht interpretierbar), `none` (noch nicht gelesen).
- In Beispieltexten ist das Token, auf dem das Neuron feuert, mit `«…»` markiert.

Alle Begriffe mit den Definitionen der Original-Seite: `GET /api/v1/glossary`.

## Suchen

Die Suchzeile der Oberfläche, `GET /api/v1/search?q=…`, `neuron-atlas search "…"` und das
MCP-Werkzeug `search_neurons` verstehen dieselbe Syntax:

| Eingabe | Bedeutung |
|---|---|
| `braille` | Wortsuche, nach Relevanz sortiert; ganze Wörter zuerst, dann Wortanfänge (ab 3 Zeichen) |
| `"severe pain"` | exakte Phrase |
| `hash -base64` | muss „hash“ enthalten, darf „base64“ nicht enthalten |
| `tokeniz* OR detokeniz*`, `chess NOT opening` | Präfix, ODER, UND NICHT – Operatoren nur in Großbuchstaben (`or`, `not` sind normale Wörter) |
| `C++`, `->`, `:=` | Zeichenfolgen mit Satzzeichen werden als Teilstring gesucht (`-` schließt nur vor einem Wort aus) |
| `L30/E47/N449`, `+ L30/E47/N449`, `L30_E47_N449__neg` | Neuron bzw. Pol direkt öffnen (`9/11/2001` bleibt Text, solange es kein solches Neuron gibt) |
| `layer:30 expert:47 pole:neg` | Filter direkt in der Suchzeile – ohne Leerzeichen nach dem Doppelpunkt; in Anführungszeichen ist es Text |
| `layer:24..36` `cat:mono,fn` `modes:>=3` `sim:>0.8` `damage:<0.5` | Bereich, Kategorie, Anzahl Modi, Simulierbarkeit, Schadensanteil |
| `has:overview` `lacks:mirrored` `sample:public` | vorhandene/fehlende Daten; nur die 3.000 Pole der Original-Seite |
| `in:examples`, `in:labels,deep,examples` | Suchbereich ersetzen: Voreinstellung sind Beschriftungen und lange Texte gespiegelter Pole (`labels,deep`); `examples` = Beispielpassagen gespiegelter Pole |

Weitere Schalter: `match=semantic` (nach Bedeutung statt nach Wörtern), `match=substring`
(Verhalten der Original-Seite), `match=regex` (mit Zeitlimit; abschaltbar über
`ATLAS_ALLOW_REGEX=0`), `group_by=mode` (ein Treffer pro passendem Modus) oder `group_by=neuron`,
`sort=relevance|simulatability|layer|modes|damage|share|random|id`. Ein Tippfehler in einem Filter
(`layer:abc`) oder ein unbekannter Parameter (`?qq=`) wird mit einer verständlichen Fehlermeldung
abgelehnt statt still ignoriert.

Die Beschreibungen im Atlas sind englisch – die Wortsuche braucht also englische Begriffe.

**Suche nach Bedeutung.** `match=semantic`, „Ähnliche Pole“ und die Karten arbeiten mit
Bedeutungsvektoren. Eingebaut ist eine schnelle, rein lokale Wortstatistik (TF-IDF + LSA): gut
für verwandte Formulierungen, schwach bei echten Umschreibungen. Deutlich besser wird es mit
einem kleinen Embedding-Modell (optional, läuft ebenfalls lokal):

```bash
pip install -e ".[embeddings]"
ATLAS_EMBEDDER=fastembed neuron-atlas serve                # englisches Modell (67 MB)
ATLAS_EMBEDDER=fastembed:multilingual neuron-atlas serve   # mehrsprachig (220 MB): Anfragen auch auf Deutsch
```

Mit dem mehrsprachigen Modell findet `Schmerzen und Leiden` die Modi zu „suffering/pain“,
`Blindenschrift` die Braille-Modi. Die Vektoren aller 23.586 Modus-Beschriftungen werden beim
ersten Start einmalig berechnet (einige Minuten) und danach wiederverwendet.

## API

Interaktive Dokumentation: `/docs` · maschinenlesbar: `/openapi.json` · Kurzanleitung für
Sprachmodelle: `/llms.txt`.

```bash
# Suche
curl 'http://127.0.0.1:8000/api/v1/search?q=braille&limit=5'

# Ein Pol mit Modi und je zwei Beispielen – als JSON, Markdown oder druckfertige Seite
curl 'http://127.0.0.1:8000/api/v1/poles/L30_E47_N449__pos?include=modes,examples&max_examples=2'
curl 'http://127.0.0.1:8000/api/v1/poles/L30_E47_N449__pos?include=all&format=md'
open 'http://127.0.0.1:8000/p/L30_E47_N449__pos'

# Ähnliche Bedeutungen
curl 'http://127.0.0.1:8000/api/v1/similar?pole=L30_E47_N449__pos&top=10'
curl 'http://127.0.0.1:8000/api/v1/similar?text=random+hex+hashes'

# Viele Abfragen auf einmal
curl -X POST http://127.0.0.1:8000/api/v1/search/batch -H 'content-type: application/json' \
     -d '{"queries": [{"q": "braille"}, {"q": "fishing", "categories": ["mono"]}]}'

# Alle Treffer als Strom (ohne Seitenlimit)
curl 'http://127.0.0.1:8000/api/v1/export?q=layer:30+cat:mono&format=csv' -o layer30-mono.csv
```

| Endpunkt | Zweck |
|---|---|
| `GET/POST /api/v1/search`, `POST /api/v1/search/batch` | Suche, auch gebündelt |
| `GET /api/v1/similar`, `GET /api/v1/random` | ähnliche Pole/Modi, Zufallsauswahl |
| `GET /api/v1/poles/{id}` (`include`, `max_examples`, `format`) | ein Pol: Modi, Subcircuits, Beispiele, Overview, Landscape, Circuit, Islands |
| `POST /api/v1/poles/batch`, `GET /api/v1/neurons/{id}` | viele Pole; ein Neuron mit beiden Polen |
| `GET /api/v1/poles/{id}/raw/{subcircuit\|overview\|landscape\|islands}` | unveränderte Originaldatei |
| `GET/POST /api/v1/export` | alle Treffer als NDJSON, CSV oder Markdown |
| `GET /api/v1/map`, `GET /api/v1/graph/{id}` | semantische Karte, Circuit-Nachbarschaft |
| `POST /api/v1/mirror/plan`, `POST /api/v1/mirror`, `GET /api/v1/mirror/jobs` | Region planen, herunterladen, Fortschritt |
| `GET /api/v1/info`, `GET /api/v1/glossary` | Stand, Zahlen, Begriffe |

## Chatbots anbinden

**MCP (Claude Desktop, Claude Code und andere MCP-Clients).** Lokal über stdio:

```json
{
  "mcpServers": {
    "neuron-atlas": { "command": "neuron-atlas", "args": ["mcp"] }
  }
}
```

```bash
claude mcp add neuron-atlas -- neuron-atlas mcp                        # Claude Code, lokal
claude mcp add --transport http neuron-atlas http://127.0.0.1:8000/mcp   # gegen den laufenden Server
```

Werkzeuge: `search_neurons`, `batch_search`, `get_pole`, `get_poles`, `get_neuron`,
`find_similar`, `plan_region`, `atlas_info`, `glossary`. Die Antworten sind für Sprachmodelle
aufbereitet:

- Jede Antwort bleibt unter `max_chars` Zeichen (Voreinstellung 14.000, höchstens 120.000).
  Lange Pol-Dokumente verlieren zuerst Beispielpassagen, dann deren Vorlauf-Kontext; erst danach
  wird gekürzt – und am Ende steht immer, was weggelassen wurde. Gekürzte Trefferlisten setzen
  `next_offset` so, dass die nächste Seite nahtlos anschließt.
- Fehler (unbekannte Id, falscher Parameter, Filter-Tippfehler) kommen als lesbare Meldung zurück,
  mit der das Modell den Aufruf korrigieren kann.
- Mit API-Schlüssel (`ATLAS_API_KEYS`) braucht auch `/mcp` den Header `X-API-Key` oder
  `Authorization: Bearer …`.

**Ohne MCP:** Jeder Chatbot, der URLs abrufen kann, findet in `/llms.txt` eine Anleitung und in
`/openapi.json` die vollständige Beschreibung (z. B. als „Action“ einbindbar).

## Drucken und Kopieren

- In der Oberfläche auf jeder Pol-Seite: **Als Markdown kopieren**, **.md / .json herunterladen**, **Drucken**.
- `http://127.0.0.1:8000/p/L30_E47_N449__pos` ist eine eigenständige, druckoptimierte Seite
  (Beispiele mit Aktivierungs-Schattierung wie im Original).
- Auf der Kommandozeile:

```bash
neuron-atlas show "+ L30/E47/N449" --include all -e 3 > pol.md
neuron-atlas show L30_E47_N449__pos -f html -o pol.html      # im Browser öffnen und drucken
neuron-atlas show L30_E47_N449__pos -f txt                   # reiner Text
```

## Den Atlas herunterladen

Ohne Download ist nur die Index-Ebene da (Beschriftungen, Anteile, Kennzahlen). Die
ausführlichen Daten liegen pro Pol in bis zu vier Dateien, pro Neuron in drei weiteren.
Ein Spiegel macht sie offline verfügbar und im Volltext durchsuchbar.

```bash
# Erst ansehen, was geladen würde
neuron-atlas mirror --similar-to "braille dot art" --top 100 --dry-run

# Eine semantische Region: per Bedeutung, per Suche, per Ähnlichkeit zu einem Pol
neuron-atlas mirror --similar-to "random hex hashes and base64" --top 300
neuron-atlas mirror --query "legal OR contract layer:24..36"
neuron-atlas mirror --like L30_E47_N449__pos --top 200 --neighbours 1

# Die 3.000 Pole der Original-Seite bzw. alles
neuron-atlas mirror --public-sample
neuron-atlas mirror --all
```

Größenordnung des kompletten Atlas: rund 32.000 Dateien, etwa 5 GB Übertragung (gzip, so wird
auch gespeichert), etwa 22 GB als JSON. Mit der Voreinstellung von 4 Anfragen pro Sekunde
dauert das gut zwei Stunden. `--all` umfasst auch die rund 140 Neuronen, die nur der
Circuit-Index kennt (ohne eigene Pole), und die wenigen Pole, die nur in den Neben-Indizes
stehen.

- **Abbrechen:** Strg-C hält sofort an; der nächste Aufruf setzt dort fort (vorhandene Dateien
  werden übersprungen).
- **Notbremse:** Scheitern viele Dateien hintereinander (Server blockiert, Ausfall, Login-Portal
  im Netz) oder ist die Platte voll, hört der Lauf von selbst auf und sagt warum. Bittet der
  Server um eine Pause (HTTP 429/503 mit `Retry-After`), warten alle Anfragen gemeinsam.
- **Selbstheilung:** Beschädigte oder abgeschnittene Dateien werden erkannt und neu geholt; ein
  verlorenes oder kaputtes Manifest wird aus den vorhandenen Dateien wieder aufgebaut.

Über die Oberfläche („Region herunterladen“) und die API geht dasselbe als Hintergrund-Job,
dort begrenzt durch `ATLAS_ALLOW_MIRROR` und `ATLAS_MAX_MIRROR_POLES` (Pole pro Job); höchstens
zehn Jobs warten gleichzeitig. Ein Job, bei dem keine einzige Datei ankam, endet als „failed“.

## Geometrie-Analyse

Was in den Daten steckt – und was nicht – steht ausführlich in
[`docs/geometry.md`](docs/geometry.md). Kurzfassung: Die „Landscape“ der Original-Seite ist
*kein* Streudiagramm mit echten Koordinaten; die Punkte werden im Browser zufällig verteilt.
Wirklich vorhanden sind pro Pol: Modus×Modus-Ähnlichkeitsmatrizen (zwei Arten), ein
5×K-Tensor „Seltenheitsstufe × Modus“ für Feuerungen und Schaden, rund 950 einzelne Feuerungen
mit Stärke, gemessenem Schaden, Gate-Zustand und 29-Token-Aktivierungsprofil – und in den
Subcircuit- und Circuit-Dateien gewichtete Kanten zu anderen Neuronen (Gate/Up, Treiber/Gegner,
Leser).

```bash
# Tabellen aus allem, was gespiegelt ist (CSV oder Parquet)
neuron-atlas geometry tables out/tables -f parquet

# Interaktive semantische Karte (eigenständige HTML-Datei)
neuron-atlas geometry map out/karte.html --unit mode --method umap --clusters 40
neuron-atlas geometry map out/hash.html -q "hash OR base64 OR hex" --method umap --clusters 8

# Neuron-zu-Neuron-Graph der gespiegelten Region
neuron-atlas geometry graph out/graph.graphml
```

```python
import pandas as pd
firings = pd.read_parquet("out/tables/firings.parquet")        # eine Zeile pro Feuerung
sim     = pd.read_parquet("out/tables/mode_similarity.parquet")  # Modus × Modus je Pol
links   = pd.read_parquet("out/tables/subcircuit_links.parquet") # Neuron → Pol, Pol → Neuron
```

## Konfiguration

Alle Einstellungen als Umgebungsvariablen (oder Optionen von `neuron-atlas`):

| Variable | Voreinstellung | Bedeutung |
|---|---|---|
| `ATLAS_DATA_DIR` | `~/.local/share/neuron-atlas` | Spiegel, Suchindex, Zwischenspeicher |
| `ATLAS_OFFLINE` | `0` | `1`: niemals ins Netz, nur der lokale Spiegel |
| `ATLAS_RELEASE_URL` | automatisch | bestimmte Atlas-Version festnageln |
| `ATLAS_REQUESTS_PER_SECOND` / `ATLAS_MAX_CONCURRENCY` | `4` / `4` | Rücksicht gegenüber Goodfires Servern |
| `ATLAS_EMBEDDER` | `lsa` | `fastembed`, `fastembed:multilingual` oder `fastembed:<Modellname>` (offline nur ein schon geladenes Modell) |
| `ATLAS_ALLOW_REMOTE_FETCH` | `1` | dürfen Anfragen fehlende Pol-Dateien beim Öffnen nachladen? (Index-Dateien und ausdrückliche Downloads sind davon nicht betroffen) |
| `ATLAS_ALLOW_MIRROR` / `ATLAS_MAX_MIRROR_POLES` | `1` / `500` | Download-Jobs über API und MCP; Pole (und Circuit-Neuronen) pro Job |
| `ATLAS_ALLOW_REGEX` | `1` | Regex-Suche erlauben (läuft mit Zeitlimit) |
| `ATLAS_API_KEYS` | leer | Komma-Liste; dann ist `X-API-Key` oder `Authorization: Bearer …` nötig (für `/api/`, `/mcp`, `/p/`) |
| `ATLAS_CLIENT_RATE_PER_MINUTE` | `0` | Anfragen pro Minute je Client bzw. Schlüssel (0 = unbegrenzt; abgewiesene Schlüssel zählen mit) |
| `ATLAS_CORS_ORIGINS` | leer | erlaubte Browser-Ursprünge für fremde Web-Apps (gelten auch für `/mcp`) |
| `ATLAS_ALLOWED_HOSTS` | leer | weitere Hostnamen, unter denen der ganze Server antwortet; ohne Eintrag nur `localhost`, `127.0.0.1`, `::1` (Schutz vor DNS-Rebinding); `*` = alle |
| `ATLAS_MAX_PAGE_SIZE` / `ATLAS_MAX_BATCH_SIZE` | `500` / `200` | Grenzen für Seiten und Batches |

Ja/Nein-Werte verstehen `1`/`0`, `true`/`false`, `yes`/`no`, `on`/`off`; alles andere ist ein
Fehler (damit ein Tippfehler bei `ATLAS_OFFLINE` nicht still „online“ bedeutet).

**Server im Netz.** `neuron-atlas serve` läuft als ein Prozess (Download-Jobs, Drosselung und
Zähler leben darin; mehrere Worker gibt es bewusst nicht). Soll er von anderen Rechnern erreichbar
sein: `neuron-atlas serve --host 0.0.0.0`, dazu `ATLAS_ALLOWED_HOSTS` mit dem Namen, unter dem
er angesprochen wird, und `ATLAS_API_KEYS`. Anfragen über 2 MB werden abgewiesen (413), zu viele
Anfragen mit 429 und `Retry-After` gebremst.

## Rechtliches und Rücksicht

- Dieses Projekt steht in keiner Verbindung zu Goodfire. Der Code steht unter der MIT-Lizenz;
  die Atlas-Daten gehören Goodfire AI, Inc.
- Die [Nutzungsbedingungen der Goodfire-Website](https://www.goodfire.com/legal/websitetos)
  erlauben die Nutzung für persönliche, nicht-kommerzielle Zwecke und untersagen ohne vorherige
  schriftliche Zustimmung unter anderem den automatisierten Zugriff sowie das Vervielfältigen,
  Speichern und Weiterverbreiten der Inhalte. Ob und wie weit das deine Nutzung betrifft, musst
  du selbst beurteilen – das hier ist keine Rechtsberatung.
- Praktische Konsequenzen, an denen sich die Voreinstellungen orientieren:
  - Standardmäßig wird nur geladen, was du tatsächlich öffnest; alles wird lokal
    zwischengespeichert, Anfragen sind gedrosselt, und jede Datei wird höchstens einmal geholt.
  - Für einen **Komplett-Spiegel** oder einen **öffentlich erreichbaren Server** (das wäre eine
    Weiterverbreitung) vorher bei Goodfire nachfragen.
  - Atlas-Daten gehören nicht in dieses Repository (`.gitignore` schließt den Datenordner aus).
- Die Beispielpassagen sind rohe Web-Texte; es kommen beliebige Sprachen, Code, Spam und auch
  anstößige Inhalte vor.

## Aufbau

```
src/neuron_atlas/
  upstream.py   höflicher HTTP-Client + lokaler Spiegel (gzip, SQLite-Manifest)
  catalog.py    alle Neuronen/Pole aus den Index-Dateien, inkl. Nachbau der 3.000er-Stichprobe
  search.py     Volltextsuche (SQLite FTS5), Filter, Sortierung, Gruppierung
  semantic.py   Bedeutungsvektoren (TF-IDF + LSA), Grundlage für Ähnlichkeit und Karten
  details.py    normalisierte Pol-/Neuron-Dokumente aus den Rohdateien
  views.py      Overview, Landscape, Circuit als aufgeräumte Strukturen
  render.py     Markdown, Klartext, druckfertiges HTML, CSV
  mirror.py     Download-Pläne und Hintergrund-Jobs
  service.py    die eine Fassade, auf der API, MCP und CLI aufsetzen
  api.py        FastAPI-Anwendung · mcp_server.py MCP-Werkzeuge · cli.py Kommandozeile
  geometry/     tables.py · maps.py · graph.py
  web/          statische Oberfläche (kein Build-Schritt)
docs/           Datenquellen des Atlas (rekonstruiert), Geometrie-Leitfaden
tests/          Tests mit synthetischen Daten (kein Netz nötig)
```

Entwicklung:

```bash
pip install -e ".[dev,all]"
pytest            # offline, synthetische Fixtures
ruff check src tests
pytest -m live    # optional: wenige echte Anfragen an den Atlas
```
