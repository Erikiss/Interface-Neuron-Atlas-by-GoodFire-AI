# Kleiner Pilot für Attention-Eingriffe und interne Messungen

Dieses Protokoll ergänzt die [Vorbereitung](attention_experiments.md).
Es enthält keine Atlas-IDs, Beschriftungen oder Aktivierungsbeispiele.
Auswahl, Katalog und Ergebnisdateien bleiben private Versuchsartefakte.
Ein vorbereiteter Adapter oder bestandener CPU-Test ist noch kein ausgeführter Modellversuch.

## Zwei getrennte Versuche

- **Hauptversuch:** das ursprüngliche Qwen3-1.7B mit unveränderten 286 H_tq-MLP-Koordinaten,
  Modellrevision, Tokenizer, Chatmodus und REF-W-Referenzstatistiken.
  H_tq wurde explorativ ausgewählt und wird für diesen Pilot nicht neu selektiert.
- **Atlas-Vergleich:** eigener Qwen3.6-35B-A3B-Versuch mit sieben Zielpolen und sieben
  Kontrollpolen auf 14 verschiedenen Neuronen. Beide Vorzeichenpole annotieren denselben
  signierten Skalar: 14 rohe Zeitreihen können bis zu 28 beschriftete Auslesungen ergeben,
  aber keine 28 unabhängigen Messreihen oder unabhängig ausgewählten Einheiten.
  Die derzeitige Auswahl betrifft geroutete Experten; Shared-Experten bleiben eine getrennte Messgruppe.
- Es gibt keine Koordinatenübertragung zwischen beiden Modellen und kein Training ihrer Gewichte.
  Atlas-Beschreibungen begründen Hypothesen über Fehler, Korrektur und Kontextverarbeitung;
  sie identifizieren keine bestätigten Selbstmonitoring- oder Selbstbewusstseinsneuronen.

## Phasen und Voraussetzungen

| Phase | Erforderlicher Nachweis | Zulässiger nächster Schritt |
|---|---|---|
| 0: Vorbereitung | Private Auswahl mit Release, Dateihashes, Auswahlregel und Datenaufteilung | Adapterprüfung mit synthetischen Tensoren |
| 1a: CPU-Integration | Formen, Positionsmasken, Nullfälle, Hook-Aufräumen und unveränderte Sham-Ausgabe | Technischer Test einer ausdrücklich festgelegten Modelllaufzeit |
| 1b: Laufzeitzuordnung | Tatsächlicher Checkpoint, Module, Expert-/Neuronindizes, Vorzeichen und Positionszählung geprüft | Kleine Kalibrierung auf echten Gewichten |
| 1c: Kalibrierung | Numerischer Sham-Vergleich und vorab definierte Schadenstoleranz bestanden | Ort und Dosis einfrieren, danach gehaltene Testfragen |
| 2: Erweiterung | Separat geprüfte Generierungs-, KV- und DeltaNet-Zustandsadapter | Freie Antworten und Persistenztests |

Die Gleichheit des Atlas-Checkpoints mit einer gepinnten Modellrevision ist noch nicht belegt.
Solange diese Zuordnung offen ist, darf ein technischer Test nur als Kandidatenmodell-Test gelten;
eine Replikation der Atlas-Funktionen bleibt gesperrt. Formen und gültige Indizes reichen dafür nicht.
Die Layerzählung des Exports wird zunächst als nullbasiert angenommen und muss an der Laufzeit geprüft werden.
Der erste Adapterumfang ist **ein einzelner Forward ohne fortgeführten Cache**.
CPU-Tests ersetzen weder einen Sham-Vergleich auf echten Gewichten noch eine Speicher-/Kernelprüfung.
Jeder verwendete Eingriffsarm benötigt diese Prüfung; die folgenden Bedingungen sind der Versuchsplan.

## Auswahl und Kontrollgruppe einfrieren

Zielauswahl, Suchregeln und Begründungen entstehen vor Einsicht in Testreaktionen.
Kein Auffüllen mit beliebigen Polen, wenn geeignete Kandidaten oder Kontrollen fehlen.
Kontrollen teilen Layer, Pol, Kategorie und Expertentyp; derselbe geroutete Expert wird bevorzugt.
Danach zählen ähnliche Modenzahl, gleicher Verfügbarkeitsstatus der Simulierbarkeit und ein fester Tie-Break.
Unbekannte Werte sind fehlend (JSON `null`), nicht numerisch `0`. Kein Kontrollneuron wird mehrfach oder als Gegenpol eines Ziels verwendet.
Ein anderer Expert erhält ausdrücklich die Kennzeichnung **nicht routinggleich**.
Diese Auswahl ist strukturell und nach Annotation vergleichbar, nicht bereits aktivierungs- oder schadensangepasst.
Aktivierungsfrequenz, Beitragsgröße und Routing sind erst auf einer unabhängigen Referenz messbar.
Ausgewählte Layer und Experten werden einzeln berichtet; ihre Stichprobe repräsentiert nicht das ganze Modell.

## Eingriff und fünf Bedingungen

Für den Atlas-Vergleich sind Full-Attention-Block **19** und Kontrollblock **7** Kandidaten.
Die Begründung ist Architektur und verfügbare Beobachtungsabdeckung, keine diagnostizierte Engstelle.
Eine Änderung des Kandidatenortes ist nur während der getrennten Kalibrierung zulässig und wird protokolliert.
Diese Blockwahl wird nicht automatisch auf den H286-Hauptversuch übertragen.

Der Query-Faktor wirkt **nach der Query-Norm**, nur auf den Query-Zweig und die festgelegten Positionen.
Der zusätzliche Query-Gate-Zweig, Keys, Values, kausale Maske und Ausgabe-Sampling bleiben unverändert.
Ein positiver skalarer Faktor vor der Query-Norm würde weitgehend wieder entfernt.
Es gilt `beta = 1 / attention_temperature`: Temperaturen `1, 1.03, 1.08` entsprechen
Query-Faktoren `1, 0.9708738, 0.9259259` und machen die Verteilung weicher.
`beta > 1` schärft sie dagegen; diese Richtungsänderung wäre ein eigener Versuch.

1. Instrumentierter Sham mit `beta = 1`.
2. Kleinerer, auf Kalibrierungsdaten gewählter Query-Eingriff am Kandidatenblock.
3. Größerer, weiterhin milder Query-Eingriff am selben Ort.
4. Zufallsstörung am selben Query-Ausgang von `q_norm` und denselben Positionen, deren Norm
   je Request, Token und Head der Verschiebung aus Bedingung 3 entspricht; eigener eingefrorener RNG-Seed.
5. Derselbe größere Query-Eingriff am Kontrollblock.

Zusätzlich vergleicht ein kurzer technischer Lauf den Sham mit einer Ausführung ohne Mess-Hooks.
Alle Vergleichsarme nutzen dasselbe Attention-Backend. Ein Wechsel von fusionierter zu expliziter
Attention kann selbst Zahlen, Speicher und Laufzeit verändern und muss separat ausgewiesen werden.
Normgleiches Rauschen kontrolliert nur die Verschiebungsgröße, nicht automatisch Schaden oder Semantik.

## Kleines Budget und Trennung der Daten

Startvorschlag: 16 Kalibrierungsfragen und 64 bisher unbenutzte Testfragen mit bekannten Zielantworten.
Falls eine externe Probe trainiert wird, kommen 32 getrennte Trainingsfragen hinzu;
PCA, Skalierung und Klassifikator sehen ausschließlich dieses Trainingsmaterial.
Kein Training von Modellgewichten; die optionale Probe ist ein eigener Ausleser.
Keine Atlas-Beispiele oder engen Umschreibungen als Tests verwenden; verwandte Fragen/Entitäten gemeinsam aufteilen.
Jede Frage durchläuft alle Bedingungen in randomisierter Reihenfolge; Zufallsquellen und Seeds werden getrennt erfasst.
Ort, Positionsfenster, Dosen, Schadenstoleranz und primärer Kontrast werden vor dem Test eingefroren.
Die Kalibrierung darf eine Dosis wegen allgemeinem Schaden verkleinern, nicht wegen günstiger Testantworten.
Primär: gepaarte Änderung der nächsten Zieltoken-Logwahrscheinlichkeit beziehungsweise der
Antwort-Logwahrscheinlichkeit bei identischen vorgegebenen Fortsetzungen; Auswertung über alle Testfragen.
Unsicherheit wird über Fragen resampelt, nicht über korrelierte Tokens oder einzelne Pole.
Freie Antwortgenauigkeit, Rettungen und Verschlechterungen kommen erst mit geprüftem Generierungsadapter hinzu.
Keine Best-of-Layer-/Best-of-Dosis-Auswahl pro Testfrage und keine ausschließliche Auswertung geretteter Fehler.

## Positionen, Zustände und Messwerte

Jeder Datensatz nennt Request-ID, Bedingung, Block, absolute ungepaddete Tokenposition,
Forward-Aufruf, Prefill-/Decode-Phase und später gegebenenfalls Cache-Länge.
Lokaler Tensorindex, Generierungsschritt und Layerindex sind keine austauschbaren Zeitangaben.
Bei Padding oder Cache muss die Zuordnung explizit sein; ungeprüfte Positionen werden abgelehnt.
Im ersten Pilot wird das Positionsfenster innerhalb eines einzelnen ungecacheten Forwards festgelegt.
Bei identischen Tokens müssen vorgelagerte Messungen vor dem Eingriffsblock unverändert bleiben;
lokale und spätere Reaktionen werden getrennt von diesen Kontrollmessungen ausgewertet.

Aufzeichnen: signiertes MLP-`z`, Koordinate und Messort vor der Down-Projektion, Residualverschiebung,
sowie Expert-ID, Routingrang, Routinggewicht und Auswahlstatus pro Request und Token.
Nicht geroutet bedeutet `null` plus Auswahlstatus; ein tatsächlich berechneter Nullwert bleibt `0`.
Ein Router-gewichteter Beitrag von null bei Nichtauswahl darf separat berechnet werden,
ist aber keine beobachtete interne Nullaktivierung. Shared-Aktivierung und Shared-Gate bleiben getrennt.
Geroutete Rohaktivierung, Down-Projektionsbeitrag und Beitrag nach Routinggewicht sind verschiedene Größen.
Für H286 zusätzlich CETT, aktuellen FFN-Nenner und zCETT mit unveränderten REF-W-Statistiken speichern.
Eine veränderte CETT kann allein durch einen veränderten Nenner entstehen.
Routing-Untergruppen werden anhand der Baseline-Auswahl definiert, nicht erst nach dem Eingriff.

## Spätere Kausal- und Gedächtnistests

Nach Implementierung der Zustandsadapter: Puls abschalten, identische Tokens weiterführen und
erhaltenen Cache gegen Neuaufbau aus identischem Text sowie eine frische Sitzung vergleichen.
Beim Atlas-Modell gehören KV- **und** DeltaNet-/weitere tatsächlich verwendete Laufzeitzustände zum Reset.
Das ist ein künftiger Versuch, keine bereits unterstützte Funktion des Einzelforward-Adapters.
Persistenz im Cache wäre eine Zustandsfortwirkung; bei eingefrorenen Gewichten entsteht keine neue Gewichtememorisierung.
Externe Decodierbarkeit zeigt lesbare Information, noch keine Nutzung zur Selbstüberwachung.
Dafür wären gezielte Vermittlungstests mit passenden Schadenskontrollen und nützlichem Prüf-/Korrekturverhalten nötig.
Reine Beobachtung ohne Zustands-, Eingabe-, RNG- oder zugängliche Zeitänderung liefert dem Modell kein Beobachtungssignal.

## Ressourcen und Telemetrie

Die Modellmetadaten nennen rund **67 GiB BF16-Gewichte**, vor Aktivierungen, Zuständen und Laufzeitreserve.
Die geringe Zahl pro Token aktiver MoE-Parameter verkleinert nicht den Speicher aller residenten Gewichte.
40 GB reichen dafür nicht; 80 GB sind keine Passgarantie. Quantisierung/Offload sind eigene Bedingungen.
Vor größeren Läufen reale Peak-VRAM, Laufzeit, Backend, Präzision und Batch-/Tokenzahlen messen.
Query-Skalierung bei gleichbleibenden dichten Operationen spart keine zugesicherten FLOPs.
GPU-Energie über vorhandene Energiezähler oder integrierte Leistung mit Messintervall und Geräteumfang berichten.
Gesamtenergie und optional um Leerlauf bereinigte Energie getrennt ausweisen; GPU-Telemetrie ist nicht automatisch Systemenergie.
Aufwärmen, Synchronisation, andere GPU-Prozesse und Instrumentierungsaufwand kontrollieren.
Layerzahl, Wandzeit, FLOPs und Joule bleiben getrennte Messgrößen; eine Jacobian-Engstelle ist keine Laufzeitdiagnose.
