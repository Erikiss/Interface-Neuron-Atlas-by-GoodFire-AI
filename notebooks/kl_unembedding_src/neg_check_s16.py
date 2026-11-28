"""Gegenprobe zu S16: Mit der Bibliothek VOR der Korrektur (snap2/kl_lib2.py) muss die geladene Schicht nach einem
Abbruch über die aufbewahrte Ausnahme erreichbar bleiben; mit der jetzigen (kl_lib2.py) nicht."""
import gc
import importlib.util
import os
import sys
import tempfile
import weakref

import torch

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from tiny_stream_test import make_full, save_checkpoint


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


LT = ["linear_attention", "full_attention", "linear_attention", "full_attention"]
full = make_full(LT, seed=11)
seqs = [torch.randint(0, 256, (n,)).tolist() for n in (5, 9, 12)]
result = {}
with tempfile.TemporaryDirectory() as d:
    shard_of = save_checkpoint(full, d)
    for tag, path in (("vor der Korrektur", os.path.join(HERE, "snap2", "kl_lib2.py")), ("jetzt", os.path.join(HERE, "kl_lib2.py"))):
        K = load(path, "lib_" + tag.replace(" ", "_"))
        S = K.LayerStreamer(K.skeleton(full.config), shard_of, "cpu", torch.float32, 2, chunk=3)
        S.verbose = False
        orig, refs = S.build_layer, []

        def build_and_arm(i, orig=orig, refs=refs):
            layer = orig(i)
            if i == 1:
                refs.append(weakref.ref(layer))

                def boom(mod, args):
                    raise KeyboardInterrupt()

                layer.mlp.register_forward_pre_hook(boom)
            return layer

        S.build_layer = build_and_arm
        kept = None
        try:
            S.ensure(seqs)
        except KeyboardInterrupt as ex:
            kept = ex
        gc.collect()
        result[tag] = refs[0]() is not None
        print(f"{tag}: Schicht nach Abbruch noch erreichbar = {result[tag]}")
        del kept
ok = result["vor der Korrektur"] and not result["jetzt"]
print("GEGENPROBE", "OK: der Test erkennt das Problem" if ok else "UNERWARTET")
sys.exit(0 if ok else 1)
