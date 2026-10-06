"""Contact sheet of shot freeze-frames from simulated matches: python scripts/shot_snaps.py seed count [min_d max_d] [key=value ...]"""
import sys, pathlib
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle, Polygon
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import calibrate
from matchsim import engine
L, W = 105., 68.
args = [a for a in sys.argv[1:] if "=" not in a]
engine.TUNE.update({k: float(v) for k, v in (a.split("=") for a in sys.argv[1:] if "=" in a)})
seed, count = int(args[0]), int(args[1])
dmin, dmax = (float(args[2]), float(args[3])) if len(args) > 3 else (0, 99)
m, r = calibrate.run(seed)
shots = []
for e in r["events"]:
    if e["type"] != "shot" or e["kind"] == "penalty":
        continue
    tm = e["team"]
    ff = np.array(e["freeze_frame"])
    sx, sy = e["x"], e["y"]
    if tm == 1:
        ff = np.stack([L - ff[:, 0], W - ff[:, 1]], 1); sx, sy = L - sx, W - sy
    d = np.hypot(L - sx, sy - W / 2)
    if dmin <= d < dmax:
        shots.append((e, tm, ff, sx, sy, d))
shots = shots[:count]
cols = 3
rows_ = (len(shots) + cols - 1) // cols
fig, axs = plt.subplots(rows_, cols, figsize=(5.4 * cols, 4.2 * rows_))
axs = np.atleast_1d(axs).ravel()
for a, (e, tm, ff, sx, sy, d) in zip(axs, shots):
    a.add_patch(Rectangle((L - 16.5, W / 2 - 20.16), 16.5, 40.32, fill=False, ec="gray"))
    a.add_patch(Rectangle((L - 5.5, W / 2 - 9.16), 5.5, 18.32, fill=False, ec="gray"))
    a.plot([L, L], [W / 2 - 3.66, W / 2 + 3.66], c="k", lw=4)
    a.add_patch(Polygon([[sx, sy], [L, W / 2 - 3.66], [L, W / 2 + 3.66]], alpha=.15, color="orange"))
    for i in range(22):
        mine = (i // 11) == tm
        a.scatter(*ff[i], c="tab:red" if mine else "tab:blue", s=90 if i % 11 == 0 else 55, edgecolors="gold" if np.allclose(ff[i], [sx, sy]) else "k", linewidths=2 if np.allclose(ff[i], [sx, sy]) else .4, zorder=3)
    a.set_xlim(L - 42, L + 2); a.set_ylim(W / 2 - 28, W / 2 + 28); a.set_aspect("equal"); a.axis("off")
    a.set_title(f"t={e['t']:.0f}s  d={d:.0f}m  xg={e['xg']:.2f} {e['outcome']}  {e['why']['chosen'] if e.get('why') else ''}", fontsize=9)
plt.tight_layout()
out = pathlib.Path(__file__).resolve().parents[1] / "out" / "snaps" / f"shots_{seed}.png"
plt.savefig(out, dpi=60); print(out, len(shots))
