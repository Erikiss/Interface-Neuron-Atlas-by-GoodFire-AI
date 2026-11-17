# Auswahl einfrieren und Laufzeitadapter prüfen

Die CPU-Vorbereitung prüft Modellmetadaten. Danach folgen zwei getrennte Schritte:
eine reproduzierbare Auswahl aus einem lokalen Atlas-Katalog und technische
Tests des Eingriffs auf kleinen, zufällig initialisierten Modellen. Keiner dieser
Schritte liefert bereits Ergebnisse auf den veröffentlichten Modellgewichten.

## Private Auswahl

`neuron_atlas.selection` liest ausschließlich lokale JSON-Dateien:

```bash
python -m neuron_atlas.selection \
  --catalog out/atlas_selection/catalog_poles.json \
  --targets out/atlas_selection/target_spec.json \
  --release 2026-11-09-082204 \
  --out-dir out/atlas_selection/frozen
```

Der Katalog enthält die vollständige vorgesehene Kontrollpopulation. Die
Zielspezifikation enthält eine Liste `targets` mit jeweils `pid` und eigener
`rationale`. Redundante Koordinaten und Metadaten werden gegen den Katalog geprüft.
Die Auswahl erfolgt vor Modellmessungen. Atlas-Beschreibungen begründen lediglich
explorative Hypothesen; Suchrang und Label sind keine gemessene Kausalwirkung.

Jedes Ziel erhält genau ein anderes Kontrollneuron mit gleichem Layer,
primärem Pol, gleicher Kategorie und gleichem Typ (geroutet/gemeinsam). Innerhalb
dieser Population werden derselbe Expert, passende Verfügbarkeit der Modenzahl,
geringe bekannte Modenzahldifferenz und passende Verfügbarkeit des
Simulatability-Werts bevorzugt. Ein SHA256-Wert entscheidet verbleibende
Gleichstände. Ziele werden in kanonischer PID-Reihenfolge verarbeitet; eine
Machbarkeitsprüfung verhindert, dass frühe Zuordnungen eine vollständige
Kontrollzuordnung ausschließen. Bei fehlender vollständiger Zuordnung bricht der
Schritt ab, statt Anforderungen still zu lockern.

`selection_manifest.json` hält Algorithmus, Seed, Eingabehashes, Paare und
Abweichungen fest. `selected_poles.json` enthält beide Pole eines ausgewählten
Neurons, sofern beide im Katalog vorhanden sind. Primäre Auswahlpole und
zusätzliche Gegenpole bleiben unterscheidbar. Zwei Pole zählen als ein Neuron.
`router_matched` bezeichnet lediglich dieselbe Expert-ID, keine gleichen
Routinggewichte oder identische Aktivität. Kontrollen sind nur nach Metadaten
abgeglichen; Aktivierungsstärke, Gewichtsnormen und Störwirkung sind ungemessen.

Alle Kataloge, Beschreibungen, IDs und Auswahlresultate bleiben im ignorierten
Verzeichnis `out/`. Ein abweichender Lauf überschreibt keine vorhandene Auswahl.
Das Manifest bestätigt keine Gleichheit zwischen Atlas-Checkpoint und
Hugging-Face-Revision. Layerbasis, konkrete Messstelle und Normalisierung müssen
für eine Atlas-Replikation ebenfalls belegt sein.

## Optionaler Laufzeitadapter

Das Interface benötigt weiterhin kein Torch. Nur für die Adaptertests werden
Torch und genau `transformers==5.11.0` benötigt. Installationen für große
GPU-Läufe müssen zusätzlich zur CUDA-Umgebung passen; die CPU-Tests belegen
keine Kompatibilität beliebiger GPU-Kernel.

```bash
python -m pip install -e '.[dev]'
python -m pip install torch 'transformers==5.11.0'
python -m pytest tests/test_selection.py tests/test_runtime_adapter.py
```

Die Laufzeittests initialisieren kleine native Qwen3.5-MoE-Modelle aus einer
Konfiguration. Sie verwenden keine `from_pretrained`-Aufrufe, laden keine
Gewichte herunter und benötigen keine GPU. Ohne optionale Abhängigkeiten wird
die native Laufzeittestgruppe übersprungen; nur der Importtest läuft weiter.
Das ist kein bestandener Adaptertest. Lokal geprüft wurden Python 3.12,
Torch 2.15.0+cpu und Transformers 5.11.0, einschließlich eines kleinen hybriden
DeltaNet-/Full-Attention-Modells. Die Tests decken TextModel und CausalLM ab;
der ConditionalGeneration-/Vision-Wrapper ist noch nicht integriert getestet.

Der Adapter unterstützt den nativen Textpfad mit unveränderten, gebündelten
Experten und deren `eager`-Implementierung. Das Modell muss im `eval`-Modus sein.
Quantisierung, abweichende Expertenlayouts und Eingriffe in DeltaNet sind nicht
abgedeckt. Modellrevision und Gewichtsdateien müssen beim späteren Laden separat
an das Vorbereitungsmanifest gebunden werden.

Beispiel für ein **bereits geladenes und geprüftes** Modell und einen vorbereiteten
Token-Batch; dies ist kein vollständiger Experiment-Runner:

```python
import torch
from neuron_atlas.experiments import selected_coordinates
from neuron_atlas.runtime_adapter import FullAttentionPulse, MoeActivationCapture

coordinates = selected_coordinates(selected_pole_records)
positions = [intervention_token_index]  # absolute, nullbasierte Spalten
with MoeActivationCapture(model, coordinates, positions) as capture:
    with FullAttentionPulse(model, block=19, positions=positions,
                            beta=1 / 1.03, mode="scale") as pulse:
        with torch.inference_mode():
            output = model(**token_batch, use_cache=False)
records = capture.records
```

Ein Context erfasst genau einen vollständigen, ungepaddeten Textdurchlauf.
Die ausgewählten Positionen gelten für jedes Batch-Element. Bereits gefüllte
Caches werden nicht erneut gestört; ein weiterer frischer Durchlauf verlangt
einen neuen Context. Das ist noch kein Runner für zeitlich beliebige Eingriffe
mitten in der Generierung. Persistenztests mit KV- und DeltaNet-Zuständen gehören
in eine spätere Stufe.

`FullAttentionPulse` verändert Queries **nach** `q_norm` und **vor** RoPE.
Der separate Output-Gate-Zweig bleibt unverändert. `beta = 1 / T`: Eine
Attention-Temperatur von 1,03 bedeutet also ungefähr 0,97087 als Query-Faktor.
Der erste Adapter unterstützt Faktoren von 0 bis 1; `beta=1` ist der Nulleingriff.
`mode="random"` erzeugt eine lokale Zufallsstörung mit derselben angestrebten
Änderungsnorm pro Query-Head. Die RNG-Zustände des Modells werden dabei nicht
verändert. Rundung kann die tatsächlich angewandte Norm beeinflussen.

`MoeActivationCapture` verändert die native Vorwärtsrechnung nicht. Für
angeforderte geroutete Experten berechnet es Gate, Up und `SiLU(gate) * up`
zusätzlich aus den beobachteten Eingaben und nativen Parametern. Dieser separate
Messpfad muss mit numerischer Toleranz gegen die native Berechnung geprüft
werden, insbesondere auf GPU: Die kleinere Zahl erneut berechneter Tokens kann
die GEMM-Rundung verändern. Jeder Datensatz nennt dafür `measurement_method`
(`shadow_selected_token_gemm` beziehungsweise `native_down_proj_input`) und
kennzeichnet die noch unbestätigte Atlas-Messstellenzuordnung.
Nicht ausgewählte Experten liefern `null` statt
einer erfundenen Nullaktivierung. Routingrang und Routinggewicht werden separat
gespeichert. Bei gemeinsamen Experten werden Aktivierung und Output-Gate getrennt
erfasst. Die Werte sind signierte Rohaktivierungen, keine CETT-Werte; ein negativer
Atlas-Pol kehrt ihr Vorzeichen nicht um.

Die Mess-Hooks verursachen zusätzliche Berechnungen und Transfers. Laufzeit oder
Energieverbrauch mit Hooks sind deshalb nicht automatisch Werte des ungemessenen
Modells. Das [Pilotprotokoll](attention_pilot_protocol.md) legt die Kontrollarme,
Auswertung und noch offenen Prüfungen vor einem großen Lauf fest.
