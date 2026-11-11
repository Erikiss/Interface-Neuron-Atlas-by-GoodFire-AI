# Geometrie im Atlas: was da ist, was nicht, und wie man es analysiert

Dieses Dokument beantwortet die Frage „Kann man im Atlas verborgene Geometrien finden?“ –
ausgehend davon, was in den veröffentlichten Dateien tatsächlich steht. Die exakten
Feldbeschreibungen liegen in [`upstream-schema/`](upstream-schema/).

## 1. Die wichtigste Erkenntnis vorweg

Die **„Landscape“** der Original-Seite sieht aus wie eine Punktwolke, ist aber keine:
Die Datei enthält **keine Koordinaten**. Höhe = Seltenheitsstufe der Feuerung, Breite =
Schaden bzw. Häufigkeit, die Bahnen = Modi; die Position eines Punktes *innerhalb* seiner Bahn
ist im Browser erzeugter Zufall. Ebenso wenig gibt es Gewichtsvektoren, Embeddings oder
gate/up-Werte pro Feuerung in den Pol-Dateien.

Geometrie muss man also aus den folgenden echten Strukturen **bauen**.

## 2. Was wirklich vorhanden ist

| Struktur | Wo | Form | Wozu |
|---|---|---|---|
| Modus × Modus-Ähnlichkeit (zwei Arten) | Landscape `rel_sim` (nach Beschreibung), `sim` (nach Feuerverhalten) | o × o je Pol | Lage der Modi eines Pols zueinander (MDS, Spektral-Einbettung) |
| Reihung der Modi | Landscape `rel_order`, Farben `rel_col` | Permutation | 1-D-Anordnung „Verwandtes nebeneinander“ |
| Stufe × Modus | Landscape `bands[i].counts`, `dmg_lin` | 5 × K je Pol | Wie sich der Modus-Mix mit der Feuerstärke verschiebt |
| Einzelne Feuerungen | Landscape `dots` | ~950 je Pol | Stärke, gemessener/geschätzter Schaden, Modus, Subcircuit, Gate-Zustand, 29-Token-Aktivierungsprofil |
| Schadens-Skalierung | Landscape `kl_fit` | Skalar je Pol | Schaden ∝ Stärke^slope |
| Faltungs-Graph | Landscape `modes[].fold`, `rel` | Kanten Q3/Q4-Modus → Modus | Welche „Leck“-Lesarten Sonderfälle welcher Modi sind |
| Neuron → Pol (Subcircuit-Ebene) | Subcircuit `subcircuits.*.circuit` | 5 Tabellen × 10 Einheiten je Subcircuit | Gate-/Up-Treiber und -Gegner, Leser – je Subcircuit |
| Neuron ↔ Neuron (Circuit-Ebene) | Circuit `meta.json` → `groups` | ~500 Eingänge, ~120 Leser je Neuron, mit Pol und Vorzeichen | gerichteter, gewichteter Graph über alle 40 Layer |
| Beispiel × Gruppe | Circuit `incidence.json` | Matrix je Neuron | welche Eingangsgruppe in welchem Beispiel aktiv war |
| gate/up je Beispiel | Circuit `exemplars.json` (`gate_pre`, `up_pre`) | ~250–300 je Neuron | die einzige Stelle mit echten (gate, up)-Werten: `act = silu(gate) · up` |
| Texte | Modus-Labels (22.620), Beschreibungen, Lesarten, Beispiele | – | semantische Einbettung über Pole hinweg |

Wichtig für Graph-Analysen: Die Nachbarn eines Atlas-Neurons liegen fast nie selbst im Atlas
(sie stammen aus allen 40 Layern). Der Graph ist „Nabe und Speichen“; zwei Atlas-Neuronen
hängen vor allem über **gemeinsame Nachbarn** zusammen.

## 3. Werkzeuge

### Tabellen

```bash
neuron-atlas mirror --similar-to "random hex hashes and base64" --top 300   # Region holen
neuron-atlas geometry tables out/tables -f parquet
```

| Tabelle | Eine Zeile pro | Wichtige Spalten |
|---|---|---|
| `poles` | Pol | Kategorie, Anzahl Modi, θ, Simulierbarkeit, Schadensanteil |
| `modes` | Modus (Subcircuit-Lesung) | `kind`, `share`, `damage_ratio` |
| `landscape_modes` | Modus (Landscape) | `firing_share`, `damage_share`, `damage_per_firing` (gewichtet wie in der Overview) |
| `landscape_levels`, `landscape_level_modes` | Stufe bzw. Stufe × Modus | Stärke, Token-Häufigkeit, Feuerungen, Schaden |
| `mode_similarity` | Modus-Paar je Pol | `kind` = `description` / `firing`, `similarity` |
| `firings` | Feuerung | `strength`, `kl_measured`, `log10_kl`, `gate_closed`, `mode_index`, `act_m16 … act_0 … act_p12` |
| `subcircuits`, `examples` | Subcircuit, Beispielpassage | Lesarten, Stärkeprofil; Text mit `«Token»` |
| `subcircuit_links` | (Subcircuit, Einheit) | `direction`, `lane`, `role`, `weight`, `rest` |
| `circuit_groups`, `circuit_edges` | Gruppe bzw. Kante | `gate`, `up` (vorzeichenbehaftet), `role`, `effect` |

Hinweise zur Statistik:

- `firings` ist eine geschichtete Stichprobe (je Stufe 1.000 gezogen, davon ~190 gelistet;
  getestete Feuerungen sind auf mindestens fünf je Modus aufgefüllt). Für unverzerrte
  Modus-Häufigkeiten `landscape_level_modes` mit `token_frequency` gewichten.
- Leere `act_*`-Zellen bedeuten „Token wurde nicht zu diesem Expert geroutet“ – das ist
  Information, kein fehlender Wert, also nicht mit 0 auffüllen.
- Stärken sind Vielfache der Schwelle θ; negative Pole haben positive Stärken.
- `share` (Stichprobe) und `firing_share` (auf echten Text gewichtet) unterscheiden sich stark.

### Karten

```bash
neuron-atlas geometry map out/karte.html --unit mode --method umap --clusters 40
neuron-atlas geometry map out/region.html -q "legal OR contract" --method umap --clusters 8
```

Grundlage sind die Bedeutungsvektoren der Modus-Labels (TF-IDF + LSA, lokal berechnet).
`pca` ist schnell und deterministisch, `umap` trennt Cluster deutlich besser (optionales Paket).
Die Karte ist eine **Sprach-Geometrie**: Sie zeigt, welche Beschreibungen einander ähneln –
nicht, wie die Neuronen im Modell verschaltet sind. Dafür ist der Graph da.

### Graph

```bash
neuron-atlas geometry graph out/graph.graphml          # für Gephi, Cytoscape, networkx
neuron-atlas geometry graph out/kanten.csv             # Kantenliste + kanten_nodes.csv
```

```python
from neuron_atlas.service import Atlas
from neuron_atlas.geometry.graph import shared_input_similarity

with Atlas() as atlas:
    paare = shared_input_similarity(atlas, top=10)   # Pole, die von denselben Neuronen gespeist werden
```

`shared_input_similarity` ist eine **textfreie Geometrie**: Jeder Pol wird zum Vektor über
(Quell-Neuron, Quell-Pol, gate/up) mit dem vorzeichenbehafteten Beitrag als Wert; ähnlich sind
Pole, die von denselben Neuronen in dieselbe Richtung getrieben werden.

## 4. Ideen für Analysen

1. **Sprach- gegen Schaltungs-Geometrie.** Für eine Region beide Ähnlichkeiten berechnen
   (`/api/v1/similar` bzw. LSA-Vektoren gegen `shared_input_similarity`) und vergleichen:
   Wo liegen Pole sprachlich nah, aber schaltungstechnisch fern – und umgekehrt?
2. **Modus-Geometrie innerhalb eines Pols.** `mode_similarity` hat zwei Matrizen je Pol;
   ihre Differenz zeigt Modi, die ähnlich *klingen*, aber unterschiedlich *feuern*.
3. **Trajektorien im Modus-Raum.** Die Zeilen von `landscape_level_modes` auf 1 normieren:
   fünf Punkte je Pol, von „häufig und schwach“ bis „selten und stark“. Bleibt der Mix stabil?
4. **Kausale Konzentration.** Entropie der `firing_share` gegen Entropie der `damage_share`
   je Pol; `fn`-Pole sollten hier auffallen.
5. **Gate-Regime.** Anteil `gate_closed` nach Stufe und Modus; Schadensanteil der
   Q3/Q4-Feuerungen.
6. **Lokale Aktivierungsform.** Die 29 Spalten `act_*` um die Feuerung herum clustern
   (Einzelspitze, Plateau, Gegenpol-Nachbarn, Routing-Lücken).
7. **Schichtfluss.** Aus `circuit_edges` die Layer-Abstände der Eingänge und Leser auswerten;
   knapp die Hälfte der Eingangsmasse kommt aus dem direkt darunterliegenden Layer.
8. **Echte gate×up-Ebene.** `exemplars.json` (über `/api/v1/neurons/{id}/raw/exemplars`) enthält
   `gate_pre` und `up_pre` – damit lässt sich die Quadranten-Darstellung nachbauen, die der
   Atlas nur als Text erwähnt.

## 5. Grenzen

- Semantische Karten beruhen auf den *Beschreibungen*, die ein LLM geschrieben hat – nicht auf
  Aktivierungen.
- Labels der Nachbar-Neuronen sind abgeschnitten (220 bzw. 500 Zeichen).
- Schaden ist nur für rund 200 Feuerungen je Pol gemessen, der Rest geschätzt (exakte Funktion
  von Stärke und Modus). Kennzahlen kleiner Modi sind entsprechend verrauscht.
- Die sechs Schema-Dokumente wurden an wenigen Beispieldateien rekonstruiert; seltene Varianten
  können abweichen. Die Parser sind deshalb tolerant geschrieben.
