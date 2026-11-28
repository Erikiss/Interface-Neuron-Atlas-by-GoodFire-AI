"""Prüft den echten Checkpoint Qwen/Qwen3.6-35B-A3B, ohne Gewichte zu laden: nur die Kopfzeilen der
.safetensors-Dateien werden per HTTP-Bereichsanfrage gelesen. Verglichen werden Namen, Formen und Dtypes
mit dem Modell, das das schichtweise Laden aufbaut (kl_lib2.skeleton). Außerdem: GPU-Bedarf und Download-Größe."""
import json
import os
import shutil
import sys
import tempfile
import urllib.request

import torch
from transformers import AutoConfig

import kl_lib2 as K

HERE = os.path.dirname(os.path.abspath(__file__))
MODEL_ID = "Qwen/Qwen3.6-35B-A3B"
BASE = f"https://huggingface.co/{MODEL_ID}/resolve/main/"
SPLIT = 21
GIB = 2**30


def get(url, a, b):
    req = urllib.request.Request(url, headers={"Range": f"bytes={a}-{b}"})
    with urllib.request.urlopen(req, timeout=300) as r:
        return r.read()


def header(url):
    n = int.from_bytes(get(url, 0, 7), "little")
    h = json.loads(get(url, 8, 8 + n - 1))
    end = max((v["data_offsets"][1] for k, v in h.items() if k != "__metadata__"), default=0)
    return h, 8 + n + end            # Kopfzeilen und Dateigröße (aus den Versatzangaben)


with tempfile.TemporaryDirectory() as d:
    shutil.copy(os.path.join(HERE, "cfg_Qwen3.6-35B-A3B.json"), os.path.join(d, "config.json"))
    cfg = AutoConfig.from_pretrained(d)
tc = cfg.text_config
print("Konfiguration:", tc.num_hidden_layers, "Schichten, H", tc.hidden_size, "Experten", tc.num_experts,
      "I", tc.moe_intermediate_size, "| tie:", cfg.tie_word_embeddings if hasattr(cfg, "tie_word_embeddings") else "?")

skel = K.skeleton(cfg)
names = K.needed_names(skel)
idx = json.load(open(os.path.join(HERE, "idx_bf16.json"), encoding="utf-8"))["weight_map"]
missing_in_index = [n for n in names if n not in idx]
print(f"Gewichte für Textteil und Ausgabe: {len(names)} | fehlen im Index: {len(missing_in_index)} {missing_in_index[:3]}")

files = sorted({idx[n] for n in names})
hdr, size = {}, {}
for f in files:
    hdr[f], size[f] = header(BASE + f)
print(f"Dateien mit benötigten Gewichten: {len(files)} | Download (ohne Bildmodul/MTP, soweit in diesen Dateien): "
      f"{sum(size.values()) / 1e9:.1f} GB")

mism, dtypes = [], {}
numel_by_name = {}
for n, p in skel.named_parameters():
    if n not in names:
        continue
    numel_by_name[n] = p.numel()
    info = hdr[idx[n]].get(n)
    if info is None:
        mism.append((n, "fehlt im Header der Datei"))
        continue
    dtypes[info["dtype"]] = dtypes.get(info["dtype"], 0) + 1
    if list(info["shape"]) != list(p.shape):
        mism.append((n, f"Form {info['shape']} statt {list(p.shape)}"))

extra = []
for f in files:
    for k in hdr[f]:
        if k == "__metadata__" or not k.startswith(("model.language_model.", "lm_head.")):
            continue
        if k not in numel_by_name:
            extra.append(k)
print(f"Dtypes der benötigten Gewichte im Checkpoint: {dtypes}")
print(f"Abweichungen Namen/Formen: {len(mism)} {mism[:5]}")
print(f"Zusätzliche Textgewichte im Checkpoint, die das Modell nicht hat: {len(extra)} {extra[:5]}")

per_layer = {}
for i in range(tc.num_hidden_layers):
    pre = f"model.language_model.layers.{i}."
    per_layer[i] = 2 * sum(v for k, v in numel_by_name.items() if k.startswith(pre))     # BF16 = 2 Byte
emb = 2 * numel_by_name["model.language_model.embed_tokens.weight"]
head = 2 * numel_by_name["lm_head.weight"]
norm = 2 * numel_by_name["model.language_model.norm.weight"]
upper = sum(per_layer[i] for i in range(SPLIT, tc.num_hidden_layers))
resident = upper + emb + head + norm
lazy_max = max(per_layer[i] for i in range(SPLIT))
print(f"Bytes je Schicht: {min(per_layer.values()) / GIB:.2f} bis {max(per_layer.values()) / GIB:.2f} GiB "
      f"(Summe über alle {tc.num_hidden_layers}: {sum(per_layer.values()) / GIB:.1f} GiB)")
print(f"Dauerhaft auf der GPU (Schichten {SPLIT}–{tc.num_hidden_layers - 1}, Einbettung, Ausgabe, Endnorm): "
      f"{resident / GIB:.1f} GiB")
print(f"Vorberechnung: dazu eine untere Schicht gleichzeitig (max {lazy_max / GIB:.2f} GiB)"
      f" -> Spitze {(resident + lazy_max) / GIB:.1f} GiB ohne Aktivierungen")
print(f"Platz für Aktivierungen auf einer 40-GB-GPU (rund 39,4 GiB nutzbar): {39.4 - (resident + lazy_max) / GIB:.1f} GiB")
ok = not missing_in_index and not mism
print("ERGEBNIS:", "Namen, Formen und Dtypes stimmen" if ok else "ABWEICHUNGEN - siehe oben")
sys.exit(0 if ok else 1)
