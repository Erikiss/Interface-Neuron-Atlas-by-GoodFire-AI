# ---------- Gate 0: Diagnose aus den schon gerechneten Werten (rechnet nichts neu, dauert Sekunden) ----------
import torch
meta = []                                    # (Fenster, u, Position j) in derselben Reihenfolge wie rows
for wi, dot in enumerate(dots):
    t, u, f = dot["t"], dot["u"], dot["f"]
    ids_list = tok.encode("".join(s for s, _ in t), add_special_tokens=False)
    if len(ids_list) != len(t) or [tok.decode([i]) for i in ids_list] != [s for s, _ in t]:
        continue
    meta += [(wi, u, j) for j in range(u, len(t)) if j != f]
assert len(meta) == len(rows), f"Zuordnung passt nicht: {len(meta)} gegen {len(rows)}"
R = [dict(r, wi=m[0], u=m[1], j=m[2]) for r, m in zip(rows, meta)]
val = [r for r in R if r["v"] is not None]

def share(rs, f=lambda r: r["a"]):
    return sum(abs(f(r) - r["v"]) <= CAL_ABS + CAL_REL * abs(r["v"]) for r in rs) / max(len(rs), 1)

print(f"Fenster {len({r['wi'] for r in R})}, Positionen {len(R)}, davon mit Atlas-Wert {len(val)}")
print(f"Wert innerhalb Toleranz: {share(val):.3f} (Gate 0,90) | Routing gleich: "
      f"{sum(r['routed'] == (r['v'] is not None) for r in R) / len(R):.3f} (Gate 0,98)")
n_b = sum(r["routed"] and r["v"] is not None for r in R)
n_a = sum((not r["routed"]) and r["v"] is not None for r in R)
n_m = sum(r["routed"] and r["v"] is None for r in R)
print(f"Routing: beide wählen den Experten {n_b}, nur der Atlas {n_a}, nur das Modell {n_m}, keiner {len(R) - n_b - n_a - n_m}")
if len(val) > 2:
    A = torch.tensor([r["a"] for r in val], dtype=torch.float64)
    V = torch.tensor([r["v"] for r in val], dtype=torch.float64)
    e = (A - V).abs()
    print(f"Modell a gegen Atlas v: Pearson r {float(torch.corrcoef(torch.stack([A, V]))[0, 1]):.3f}, "
          f"Steigung durch 0 {float((A * V).sum() / (V * V).sum()):.3f}, Median |a-v| {float(e.median()):.3f}, "
          f"90-%-Quantil {float(torch.quantile(e, 0.9)):.3f}")
print(f"Innerhalb Toleranz, wo auch das Modell den Experten wählt: {share([r for r in val if r['routed']]):.3f} "
      f"(n={sum(r['routed'] for r in val)}); wo nur der Atlas ihn wählt: {share([r for r in val if not r['routed']]):.3f}")
for name, f in (("ohne Teilen durch theta", lambda r: r["a"] * THETA), ("zweimal durch theta", lambda r: r["a"] / THETA),
                ("mit umgekehrtem Vorzeichen", lambda r: -r["a"])):
    print(f"  Wenn der Atlas-Wert {name} wäre: {share(val, f):.3f} innerhalb")
print("Gate-Kriterien getrennt nach Fensterart:")
for name, sel in (("Fenster ab Textanfang (u < 84, voller Kontext)", lambda r: r["u"] < 84),
                  ("abgeschnittene Fenster (u = 84, Text davor fehlt)", lambda r: r["u"] >= 84)):
    allp = [r for r in R if sel(r)]
    part = [r for r in allp if r["v"] is not None]
    if allp:
        print(f"  {name}: Wert {share(part):.3f} innerhalb (n={len(part)}), Routing gleich "
              f"{sum(r['routed'] == (r['v'] is not None) for r in allp) / len(allp):.3f} (n={len(allp)})")
for lo, hi in ((0, 84), (84, 92), (92, 100), (101, 113)):
    part = [r for r in val if lo <= r["j"] < hi]
    if part:
        print(f"  Position {lo} bis {hi - 1} im Fenster: {share(part):.3f} innerhalb (n={len(part)})")
per = {}
for r in val:
    per.setdefault(r["wi"], []).append(r)
fr = sorted(share(rs) for rs in per.values())
print(f"Je Fenster: alle Werte innerhalb {sum(x == 1.0 for x in fr)} von {len(fr)} Fenstern, "
      f"keiner innerhalb {sum(x == 0.0 for x in fr)}, Median-Anteil {fr[len(fr) // 2]:.2f}")
print("Größte Abweichungen (Fenster, Position, Token, Modell, Atlas, Modell wählt Experten):")
for r in sorted(val, key=lambda r: -abs(r["a"] - r["v"]))[:8]:
    print(f"  {r['wi']:4d} {r['j']:4d} {dots[r['wi']]['t'][r['j']][0]!r:>14} {r['a']:+.3f} {r['v']:+.2f} {r['routed']}")
