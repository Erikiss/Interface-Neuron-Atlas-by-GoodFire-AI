# ---------- Neue Fassung in der laufenden Sitzung: das geladene Modell bleibt, nichts wird neu heruntergeladen ----------
# Vorher die laufende Zelle anhalten (Laufzeit > Ausführung unterbrechen). Diese Zelle rechnet dann ab Gate 0 weiter.
import json, urllib.request
NEW_URL = ("https://raw.githubusercontent.com/Erikiss/Interface-Neuron-Atlas-by-GoodFire-AI/"
           "claude/neuronatlas-interface-api-h1uw92/notebooks/kl_unembedding_test.ipynb")
with urllib.request.urlopen(NEW_URL, timeout=60) as r:
    NEW_CELLS = ["".join(c["source"]) for c in json.loads(r.read())["cells"] if c["cell_type"] == "code"]

def new_cell(marker):
    return next(s for s in NEW_CELLS if marker in s)

for blk in text_model(model).layers[STREAM.split:]:      # Hooks der alten Fassung entfernen (sie führt kein Verzeichnis)
    blk.mlp._forward_hooks.clear()
    blk.mlp._forward_pre_hooks.clear()
    blk.mlp.gate._forward_hooks.clear()
exec(new_cell("# ---------- Bibliothek"), globals())
exec(new_cell("# ---------- Modell laden"), globals())    # übernimmt das geladene Modell
for marker in ("# ---------- Zielwörter", "# ---------- Gate 0", "# ---------- Teil 1", "# ---------- Satzpaare",
               "# ---------- Aktivitätsgleiche", "# ---------- Läufe", "# ---------- Statistik",
               "# ---------- Teil 3", "# ---------- Ergebnis"):
    print(f"\n===== {marker[13:]} =====", flush=True)
    exec(new_cell(marker), globals())
