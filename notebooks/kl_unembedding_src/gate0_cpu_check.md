# Gate 0 nachgerechnet auf der CPU (28.11.2026)

Gate 0 ist im ersten Lauf auf einer A100 nicht bestanden. Die Werte jenes Laufs gingen mit der Colab-Laufzeit verloren. Deshalb wurde Gate 0 hier ohne GPU nachgerechnet: `cpu_gate0.py` lädt die Schichten 0 bis 24 von `Qwen/Qwen3.6-35B-A3B` Schicht für Schicht vom Hub (43 GB, nichts gespeichert) und rechnet in float32. Gemessen wird wie im Notebook: Aktivierung von Neuron 345 in Experte 124, Schicht 24, geteilt durch θ = 0,6744, und ob der Router den Experten wählt. Das Skript liefert am winzigen Modell dieselben Werte wie die Notebook-Funktion `activation_at` (Abweichung unter 1e-6).

## Fensterarten im Atlas

Der Atlas zeigt je Feuerung bis zu 100 Token vor dem Feuer-Token. 827 der 950 Fenster sind aus einem längeren Text geschnitten (`u` = 84). Der Atlas hat den Text davor mitgerechnet: 18 Paare von Fenstern mit gleichem Text tragen an 51 von 74 gemeinsamen Stellen verschiedene Werte, auch Paare mit wortgleichem Fenster. Die übrigen 123 Fenster beginnen am Textanfang und haben den vollen Kontext; 120 davon lassen sich exakt zurück in Token übersetzen.

## Ergebnis

| Gruppe | Fenster | Pearson r | Steigung durch 0 | Routing gleich | Werte in Toleranz (0,005 + 5 %) |
|---|---|---|---|---|---|
| ab Textanfang, voller Kontext | 120 | 0,9975 | 0,997 | 99,1 % | 60,1 % |
| dieselben mit `<\|endoftext\|>` davor | 120 | 0,933 | 0,842 | 93,6 % | 11,1 % |
| abgeschnitten, Text davor fehlt (Stichprobe) | 123 | 0,935 | 0,897 | 94,6 % | 11,7 % |

Am Feuer-Token selbst (Fenster ab Textanfang): Modellwert gegen den gespeicherten Wert `a` mit r = 0,998; der Router wählt dort den Experten in 97,5 % der Fenster.

Andere denkbare Definitionen des gespeicherten Werts treffen bei vollem Kontext höchstens 13 % (Aktivierung mal Routing-Gewicht 6,6 %, ohne Teilen durch θ 12,7 %, umgekehrtes Vorzeichen 4,5 %, Position um eins verschoben 3,8 bis 4,6 %).

Die Abweichungen bei vollem Kontext haben keine Richtung (mittlere Abweichung −0,0014 θ) und verteilen sich über alle Fenster. Sie wachsen leicht mit der Position im Fenster (Median 0,007 θ in den ersten 30 Positionen, 0,013 θ danach). Relativ sind sie bei kleinen Werten am größten (Median 10 % bei |v| zwischen 0,05 und 0,2, 2,6 % bei |v| über 0,5). Routing-Abweichungen kommen nur bei knappen Entscheidungen vor (Routing-Gewicht im Median 0,06 gegen 0,11).

## Deutung

1. Hook und Definition stimmen mit dem Atlas überein: Steigung 1,00, r = 0,9975, Routing zu 99 % gleich, keine Verschiebung, kein Vorzeichen- oder θ-Fehler.
2. Der Atlas hat kein Sondertoken vorangestellt.
3. Gate 0 scheitert an zwei Dingen, die nicht am Hook liegen. 87 % der Fenster sind abgeschnitten und lassen sich ohne den Text davor nicht nachrechnen. Und die Toleranz, gedacht für die Rundung auf zwei Nachkommastellen, ist enger als der Unterschied zwischen zwei Rechenwegen: Diese Nachrechnung läuft in float32 auf der CPU, der Atlas vermutlich in bfloat16 auf der GPU. Das ist nicht geprüft.

## Grenzen

- float32 auf der CPU statt bfloat16 auf der GPU; ob eine GPU-Rechnung in bfloat16 näher am Atlas liegt, ist offen.
- Die abgeschnittenen Fenster sind eine Stichprobe (123 von 789 exakt übersetzbaren).
- Die vorab festgelegte Regel von Gate 0 bleibt, wie sie ist: nicht bestanden.
