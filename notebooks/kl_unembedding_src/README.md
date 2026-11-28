# Quellen zu `notebooks/kl_unembedding_test.ipynb`

Das Notebook wird aus diesen Dateien gebaut. Änderungen gehören hierher; danach das Notebook neu bauen:

```bash
python build_nb2.py ../kl_unembedding_test.ipynb
```

| Datei | Zweck |
|---|---|
| `kl_lib2.py` | Bibliothek, wird wörtlich als Zelle ins Notebook übernommen: Hooks, Ablation, Statistik, schichtweises Laden |
| `build_nb2.py` | Zellen des Notebooks und Bauskript |
| `tiny_stream_test.py` | Schichtweises Laden gegen das volle Modell an winzigen Modellen (Gewichte, Logits, Ablationen, Abbruch, Wiederaufnahme) |
| `tiny_model.py` | Winziges Modell mit dem echten Vokabular für den Notebook-Test |
| `harness2.py` | Führt alle Notebook-Zellen im Testmodus aus; prüft erneutes Ausführen und Abbruch mit Fortsetzen |
| `check_real_checkpoint.py` | Prüft Namen, Formen und Dtypes des echten Checkpoints, nur über die Dateikopfzeilen |
| `dryrun_real_branch.py` | Trockenlauf der Modellzelle bis zum Download, mit nachgebildeter GPU |
| `update_cell.py` | Zelle für eine laufende Sitzung mit der vorigen Fassung: holt die neue Fassung von GitHub, übernimmt das geladene Modell und rechnet ab Gate 0 weiter |
| `test_update_cell.py` | Stellt eine solche Sitzung nach (vorige Fassung unter `snap/`, mitten in Gate 0 unterbrochen) und prüft die Zelle gegen einen frischen Lauf |
| `gate0_diagnose_cell.py` | Diagnose nach einem nicht bestandenen Gate 0 aus den schon gerechneten Werten der Sitzung, getrennt nach Fenstern mit und ohne Textanfang |
| `test_gate0_diagnose.py` | Prüft die Diagnose-Zelle in nachgestellten Sitzungen der vorigen (`snap/`) und der neuen Fassung (`snap2/`) |
| `test_gate0b_decision.py` | Prüft in der echten Gate-0-Zelle mit den echten Atlas-Fenstern, wann Gate 0 und Gate 0b den Lauf anhalten |
| `neg_check_s16.py` | Gegenprobe: Der Abbruch-Test erkennt eine festgehaltene Schicht im Stand vor der Korrektur |
| `cpu_gate0.py` | Rechnet Gate 0 ohne GPU nach: Schichten 0 bis 24 der echten Gewichte, Schicht für Schicht vom Hub geladen, getrennt nach Fensterart |
| `gate0_cpu_check.md` | Ergebnis dieser Nachrechnung vom 28.11.2026 und seine Deutung |

## Tests ausführen

Alle Tests laufen auf der CPU (transformers 5.24.1, torch, accelerate, safetensors). Arbeitsverzeichnis ist dieser Ordner.

```bash
python tiny_stream_test.py                                   # rund 30 Sekunden
python harness2.py ../kl_unembedding_test.ipynb              # rund 7 Minuten
python dryrun_real_branch.py ../kl_unembedding_test.ipynb
python check_real_checkpoint.py                              # liest Kopfzeilen vom Hub
python test_update_cell.py <Ergebnisdatei eines harness-Laufs> ../kl_unembedding_test.ipynb
```

Diese lokalen Eingaben liegen nicht im Repo:

- `tokdir/`: Tokenizer-Dateien von `Qwen/Qwen3.6-35B-A3B` (`tokenizer.json`, `tokenizer_config.json`).
- `cfg_Qwen3.6-35B-A3B.json` und `idx_bf16.json`: `config.json` und `model.safetensors.index.json` desselben Modells.
- Ein lokaler Atlas-Spiegel für die Kalibrierung im Testlauf, über `ATLAS_DATA_DIR`. Atlas-Daten gehören in kein Repository.

`tiny_stream_test.py` vergleicht außerdem mit der Bibliotheksfassung des ersten echten Laufs (`snap/kl_lib2.py`), falls sie vorliegt; sonst wird dieser Vergleich übersprungen.
