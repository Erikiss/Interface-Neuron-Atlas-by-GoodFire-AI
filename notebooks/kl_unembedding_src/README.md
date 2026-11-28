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

## Tests ausführen

Alle Tests laufen auf der CPU (transformers 5.24.1, torch, accelerate, safetensors). Arbeitsverzeichnis ist dieser Ordner.

```bash
python tiny_stream_test.py                                   # rund 30 Sekunden
python harness2.py ../kl_unembedding_test.ipynb              # rund 7 Minuten
python dryrun_real_branch.py ../kl_unembedding_test.ipynb
python check_real_checkpoint.py                              # liest Kopfzeilen vom Hub
```

Diese lokalen Eingaben liegen nicht im Repo:

- `tokdir/`: Tokenizer-Dateien von `Qwen/Qwen3.6-35B-A3B` (`tokenizer.json`, `tokenizer_config.json`).
- `cfg_Qwen3.6-35B-A3B.json` und `idx_bf16.json`: `config.json` und `model.safetensors.index.json` desselben Modells.
- Ein lokaler Atlas-Spiegel für die Kalibrierung im Testlauf, über `ATLAS_DATA_DIR`. Atlas-Daten gehören in kein Repository.

`tiny_stream_test.py` vergleicht außerdem mit der Bibliotheksfassung des ersten echten Laufs (`snap/kl_lib2.py`), falls sie vorliegt; sonst wird dieser Vergleich übersprungen.
