"""Render a frame sequence (contact sheet) around a moment: python scripts/sequence.py seed t_start step n [key=value ...]"""
import sys, pathlib
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import calibrate
from matchsim import engine
L, W = 105., 68.
args = [a for a in sys.argv[1:] if "=" not in a]
engine.TUNE.update({k: float(v) for k, v in (a.split("=") for a in sys.argv[1:] if "=" in a)})
seed, t0, step, n = int(args[0]), float(args[1]), float(args[2]), int(args[3])
m, r = calibrate.run(seed)
fr = np.array(r["frames"]); ft = fr[:, 0]
evs = r["events"]
fig, axs = plt.subplots((n + 1) // 2, 2, figsize=(15, 4.6 * ((n + 1) // 2)))
axs = axs.ravel()
for a, j in zip(axs, range(n)):
    t = t0 + j * step
    k = min(np.searchsorted(ft, t), len(fr) - 1)
    pos = fr[k, 5:49].reshape(22, 2); c = int(fr[k, 3]); prev = fr[max(k - 4, 0), 5:49].reshape(22, 2)
    a.add_patch(Rectangle((0, 0), L, W, fill=False, ec="k"))
    for x0, s in ((0, 1), (L, -1)):
        a.add_patch(Rectangle((x0 if s > 0 else x0 - 16.5, W / 2 - 20.16), 16.5, 40.32, fill=False, ec="gray"))
    cx = pos[c][0] if c >= 0 else fr[k, 1]
    lo, hi = max(0, cx - 38), min(L, cx + 38)
    for i in range(22):
        col = "tab:red" if i < 11 else "tab:blue"
        a.scatter(*pos[i], c=col, s=80 if i % 11 else 140, edgecolors="gold" if i == c else "k", linewidths=2.5 if i == c else .5, zorder=3)
        a.plot([prev[i, 0], pos[i, 0]], [prev[i, 1], pos[i, 1]], c=col, lw=1.2, alpha=.6)
        a.text(pos[i, 0] + .7, pos[i, 1] + .7, str(i % 11), fontsize=7)
    a.scatter(fr[k, 1], fr[k, 2], c="k", s=30, zorder=4)
    near = [f"{e['type']}" for e in evs if abs(e["t"] - fr[k, 0]) < step / 2 and e["type"] not in ("pressure",)]
    a.set_title(f"t={fr[k, 0]:.1f}  carrier {c}   {' '.join(near)}", fontsize=9)
    a.set_xlim(lo, hi); a.set_ylim(-2, W + 2); a.set_aspect("equal"); a.axis("off")
plt.tight_layout()
out = pathlib.Path(__file__).resolve().parents[1] / "out" / "snaps" / f"seq_{seed}_{int(t0)}.png"
plt.savefig(out, dpi=65); print(out)
