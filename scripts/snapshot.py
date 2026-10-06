"""Render pitch snapshots (PNG) of sampled moments so a human can judge the shape and movement.

    python scripts/snapshot.py seed kind count [key=value ...]
kinds: through (carrier with no defender goal-side, final third), box (carrier inside the box), random
Writes out/snaps/<kind>_<seed>_<k>.png (a 4x2 contact sheet of the sampled frames).
"""
import sys, pathlib
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle, Circle
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import calibrate
from matchsim import engine

L, W = 105., 68.
args = [a for a in sys.argv[1:] if "=" not in a]
engine.TUNE.update({k: float(v) for k, v in (a.split("=") for a in sys.argv[1:] if "=" in a)})
seed, kind, count = int(args[0]), args[1], int(args[2])
m, r = calibrate.run(seed)
fr = np.array(r["frames"])
rng = np.random.default_rng(1)


def pitch(ax):
    ax.add_patch(Rectangle((0, 0), L, W, fill=False, ec="k"))
    for x0, s in ((0, 1), (L, -1)):
        ax.add_patch(Rectangle((x0 if s > 0 else x0 - 16.5, W / 2 - 20.16), 16.5, 40.32, fill=False, ec="gray"))
        ax.add_patch(Rectangle((x0 if s > 0 else x0 - 5.5, W / 2 - 9.16), 5.5, 18.32, fill=False, ec="gray"))
    ax.plot([L / 2, L / 2], [0, W], c="gray", lw=.8)
    ax.set_xlim(-2, L + 2); ax.set_ylim(-2, W + 2); ax.set_aspect("equal"); ax.axis("off")


cand = []
for k in range(60, len(fr) - 5, 3):
    c = int(fr[k, 3])
    if c < 0:
        continue
    t = c // 11
    pos = fr[k, 5:49].reshape(22, 2)
    if pos[:, 1].min() < -5:
        continue
    C = pos[c]
    ax_ = C[0] if t == 0 else L - C[0]
    D = pos[11 * (1 - t):11 * (1 - t) + 11]
    Dx = D[:, 0] if t == 0 else L - D[:, 0]
    out = np.arange(11) > 0
    ahead_close = int(((Dx[out] > ax_ - 1) & (np.abs(D[out][:, 1] - C[1]) < 10)).sum())
    inbox = (L - ax_ < 16.5) and abs(C[1] - W / 2) < 20.16
    if kind == "through" and ax_ > .7 * L and ahead_close == 0:
        cand.append(k)
    elif kind == "box" and inbox:
        cand.append(k)
    elif kind == "random":
        cand.append(k)
sel = sorted(rng.choice(cand, min(count, len(cand)), replace=False))
cols = 2
rows_ = (len(sel) + 1) // 2
fig, axs = plt.subplots(rows_, cols, figsize=(15, 5.2 * rows_ * .75))
axs = np.atleast_1d(axs).ravel()
for a, k in zip(axs, sel):
    pitch(a)
    pos = fr[k, 5:49].reshape(22, 2)
    c = int(fr[k, 3])
    prev = fr[max(k - 3, 0), 5:49].reshape(22, 2)
    for i in range(22):
        col = "tab:red" if i < 11 else "tab:blue"
        a.scatter(*pos[i], c=col, s=70 if i % 11 else 130, edgecolors="k" if i != c else "gold", linewidths=2.2 if i == c else .5, zorder=3)
        a.plot([prev[i, 0], pos[i, 0]], [prev[i, 1], pos[i, 1]], c=col, lw=1, alpha=.5)
        a.text(pos[i, 0] + .8, pos[i, 1] + .8, str(i % 11), fontsize=7)
    a.scatter(fr[k, 1], fr[k, 2], c="k", s=25, zorder=4)
    a.set_title(f"t={fr[k, 0]:.0f}s  carrier {c} ({'red→right' if c < 11 else 'blue→left'})", fontsize=9)
plt.tight_layout()
out = pathlib.Path(__file__).resolve().parents[1] / "out" / "snaps" / f"{kind}_{seed}.png"
out.parent.mkdir(parents=True, exist_ok=True)
plt.savefig(out, dpi=70)
print(out, len(cand), "candidate frames")
