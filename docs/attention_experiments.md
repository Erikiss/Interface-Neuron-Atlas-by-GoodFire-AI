# Attention-Eingriffe und Atlas-Vergleich vorbereiten

Diese Erweiterung bereitet einen eigenen Versuch zur Reaktion auf gezielte
Attention-Änderungen vor. Sie lädt keine Modellgewichte und führt keine Inferenz
aus. Der Hauptversuch bleibt auf Qwen3-1.7B mit den bisherigen 286 H_tq-Neuronen.
Qwen3.6-35B-A3B bildet einen eigenständigen Vergleich mit Atlas-Koordinaten.

[CPU-Vorbereitung in Colab öffnen](https://colab.research.google.com/github/Erikiss/Interface-Neuron-Atlas-by-GoodFire-AI/blob/codex/attention-atlas-preparation/notebooks/attention_prepare.ipynb).
Das Notebook braucht weder GPU noch Google Drive. Es erzeugt ein kleines ZIP mit
Modellmetadaten, Speicherabschätzung und Versuchsmanifest. Es startet keinen Versuch.

## Was aus Bernies Repository übernommen wird

Methodische Grundlage ist
[`BurnyCoder/jacobian-mem2gen`, Commit 85e2896](https://github.com/BurnyCoder/jacobian-mem2gen/tree/85e2896304aa1c9d0297def43042a1c1df793d19).
Das dortige Self-Patching versetzt Residualzustände an Entity-Token zwischen
Decoderblöcken. Es identifiziert noch keine allgemeine zeitliche Attention-Engstelle.
Der Jacobian-Fork untersucht Qwen2.5-0.5B mit einem Trainingsseed; seine besten
Layerpaar-Ergebnisse enthalten eine Auswahl anhand der bekannten Antworten.
Gleich starke Zufallsstörungen retten in diesem Oracle mehr Fragen als die
gezielte Kopie. Ein gehaltenes einzelnes Layerpaar hat deutlich kleinere Effekte.

Deshalb übernehmen wir die kausalen Eingriffe und Kontrollvergleiche, jedoch
keine nachträgliche Auswahl des besten Eingriffspunkts pro Testfrage. Ein Block
und eine Dosis werden auf separaten Kalibrierungsfragen festgelegt. Ein
negativer Test einer stark regularisierten Jacobian-Schätzung widerlegt zudem
nicht sämtliche Hypothesen über gezielte Attention-Eingriffe.

## Zwei Modelle mit unterschiedlichen Vergleichszielen

| | Hauptversuch | Atlas-Vergleich |
|---|---|---|
| Modell | Qwen/Qwen3-1.7B | Qwen/Qwen3.6-35B-A3B |
| Revision | `70d244cc86ccca08cf5af4e1e306ecf908b1ad5e` | `345d467b117d1fd5eda9b879a5988de9ae5f47a7` |
| Messgruppe | Feste 286 H_tq-Koordinaten | Eigene, vorab festgelegte Atlas-Pole |
| Koordinate | Layer / MLP-Neuron | Layer / gerouteter oder gemeinsamer Expert / Neuron / Pol |
| Gewichte | Unverändert, kein LoRA | Unverändert im ersten Vergleich |
| Aussage | Reaktion derselben bisher untersuchten Neuronen | Übertragbarkeit eines Effekts auf eine andere Architektur |

Der Atlas verwendet das Modell Qwen3.6-35B-A3B; die genaue Gleichheit seines
Checkpoints mit der hier gepinnten Hugging-Face-Revision ist noch nicht belegt.
Das Manifest kennzeichnet diese Lücke. Ein übereinstimmender Modellname oder eine
gültige Neuronen-ID beweist diese Identität nicht. Auch die Mitgliedschaft der
aus einer Datei übernommenen Pole im angegebenen Atlas-Release bleibt gesondert
zu prüfen. Es gibt keine 1:1-Zuordnung der 286 Neuronen zwischen beiden Modellen.

H_tq war post hoc ausgewählt. Auf dem ursprünglichen Modell bleiben Koordinaten,
Tokenizer, Chatmodus und Referenznormalisierung fest. Unter einem Eingriff wird
die Aktivierung verglichen, ohne H erneut auszuwählen. Gleiche Koordinaten
garantieren bei neuen Aufgaben keine unveränderte Funktion. Zu protokollieren
sind signierte MLP-Aktivierung z, CETT, der aktuelle FFN-Nenner und zCETT mit den
ursprünglichen REF-W-Mittelwerten und Standardabweichungen. Andernfalls kann ein
geänderter Nenner wie eine verstärkte Aktivierung aussehen.

## Atlas-Architektur und Speicher

Die offiziellen [Modellmetadaten](https://huggingface.co/Qwen/Qwen3.6-35B-A3B/tree/345d467b117d1fd5eda9b879a5988de9ae5f47a7)
enthalten 40 Textblöcke: 30 Gated-DeltaNet-Blöcke und volle Attention in den
nullbasierten Blöcken **3, 7, 11, 15, 19, 23, 27, 31, 35, 39**. Es gibt 256
geroutete Experten, davon acht pro Token aktiv, sowie einen gemeinsamen Expert;
die innere Breite beträgt jeweils 512. Die Zahl aktiver Parameter ist kein Maß
für den Speicher aller residenten Gewichte.

Der Gewichtsindex nennt **71.903.645.408 Byte**, etwa **66,97 GiB**, über 26 Shards.
Eine vollständige FP32-Konversion der als BF16 deklarierten Gewichte würde
rechnerisch etwa 133,93 GiB benötigen. Diese Zahlen schließen Aktivierungen,
Attention- und DeltaNet-Zustände, temporäre Tensoren und Laufzeitreserve aus.
Eine A100 mit 40 GB reicht für vollständige BF16-Residenz nicht. Auch eine 80-GB-Karte
ist damit keine gesicherte Lösung für instrumentierte Läufe; größere Karten
bieten Reserve. Quantisierung und CPU-Offload wären eigene numerische Bedingungen.
Die Vorbereitung liefert keine Laufzeit- oder Passgarantie.

## Eingriff und Messung

Der erste Kandidat ist eine milde Änderung der **Attention-Score-Temperatur**
innerhalb eines vorhandenen Full-Attention-Blocks, zunächst etwa 1,03 und 1,08
gegenüber 1,0. Das ist nicht die Sampling-Temperatur der Ausgabe. Es ersetzt den
Attention-Mechanismus nicht und spart bei unveränderten dichten Operationen
keine Rechenarbeit. Sliding Window verändert zusätzlich die verfügbare Information
und gehört in einen späteren, getrennten Versuch mit passendem Kernel.

Bei Qwen3 erfolgt eine Query-Normalisierung. Ein skalarer Faktor vor dieser Norm
wird weitgehend wieder entfernt; ein Temperaturtest muss danach ansetzen. Beim
Atlas-Modell enthält die Full-Attention-Query-Projektion außerdem einen Gate-Zweig.
Pauschales Skalieren ihrer gesamten Ausgabe würde dieses Gate mit verändern.
DeltaNet hat eine andere Zustandsdynamik und benötigt einen eigenen Eingriff.

Der geroutete MLP-Pfad verwendet gebündelte Expert-Parameter statt eines
`down_proj`-Moduls pro Expert. Eine normale PyTorch-Hook-Lösung pro geroutetem
Expert reicht deshalb nicht. Ein Adapter muss gegen die unveränderte native
Berechnung geprüft werden. Gemeinsame Experten und ihr Gate werden getrennt
erfasst. Zusätzlich zu Aktivierungen gehören Expert-ID, Routingrang und Gewicht
ins Protokoll. Nicht zu einem Expert geroutet ist **null**, nicht Aktivierung null.

Kontrollen: instrumentierter Nulleingriff, gleich große Zufallsstörung am selben
Ort, derselbe Eingriff an einem Kontrollblock sowie ein numerischer Vergleich
ohne Mess-Hooks. Dieselben Testfragen durchlaufen alle Bedingungen. Ausgewertet
werden Antwort-Logwahrscheinlichkeit, Genauigkeit, Rettungen und Verschlechterungen.
Eine Wirkung bei identischen vorgegebenen Fortsetzungstokens wird getrennt von
frei generiertem, möglicherweise abweichendem Text gemessen.

Layer, Tokenposition, Generierungsschritt, Trainingsschritt und verstrichene Zeit
sind unterschiedliche Achsen. Die Ausbreitung einer Störung oder ihre externe
Decodierbarkeit beweist noch keine Repräsentation der eigenen Verarbeitung.
Für eine Nutzung durch das Modell wären zusätzliche kausale Vermittlungstests
erforderlich. Persistenz bei erhaltenem Cache wird gegen einen Neuaufbau aus
identischem Text geprüft, beim Atlas-Modell einschließlich DeltaNet-Zuständen.
Bei eingefrorenen Gewichten kann so keine neue Gewichtememorisierung entstehen.

## Vorhandene Interface-Exporte verwenden

Der Planer verwendet den bestehenden ID-Parser des Interface. Er akzeptiert
JSON-Suchergebnisse, JSON-Listen und NDJSON-Exporte. Ein Export muss die vollständige
beabsichtigte Kandidatenauswahl enthalten; paginierte Suchantworten werden abgelehnt.
Positive und negative Pole bleiben getrennt, die Anzahl verschiedener Neuronen
wird zusätzlich ausgewiesen. Beschreibungen sind Auswahlhypothesen, keine
bereits bestätigten Funktionszuweisungen im neuen Versuch.

```bash
# Auf bereits vorhandenen lokalen Daten arbeiten:
neuron-atlas --offline export --help
# Ausgewählte Pole mit dem bestehenden Exportbefehl als JSON/NDJSON speichern.

python -m neuron_atlas.preflight --config config.json \
  --index model.safetensors.index.json \
  --revision 345d467b117d1fd5eda9b879a5988de9ae5f47a7 \
  --out out/attention/preflight.json

python -m neuron_atlas.experiments --preflight out/attention/preflight.json \
  --poles selected_poles.ndjson --atlas-release RELEASE_ID \
  --out out/attention/plan.json
```

Ohne `--poles` entsteht ein leeres Versuchsgerüst. Es erfindet keine Atlas-Auswahl.
Beide neuen Module arbeiten auf lokalen Dateien; das Colab-Notebook lädt
ausschließlich die beiden kleinen, SHA-geprüften Modellmetadaten von Hugging Face.
Weder der Planer noch das Notebook starten Atlas-Abrufe, Modellinferenz oder Training.
`ready_for_inference` bleibt ausdrücklich falsch, bis Laufzeitadapter,
Checkpoint-Zuordnung, Datenaufteilung und Kalibrierung gesondert abgeschlossen sind.
