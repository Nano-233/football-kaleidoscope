"""Debug helper: what do carriers choose per pitch third, and how risky are the passes they pick?"""
import sys, pathlib, collections
import numpy as np
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from matchsim.engine import Match
from matchsim.teams import make_team, Tactics

rng = np.random.default_rng(1)
home = make_team("KES", "4-3-3", Tactics(), rng=rng)
away = make_team("NBR", "4-4-2", Tactics(), rng=rng)
m = Match(home, away, seed=1)
rows = []
od = Match._decide


def dec(self):
    c = self.carrier
    x = self.pos[c][0] if c < 11 else 105 - self.pos[c][0]
    od(self)
    w = self.last_why
    ch = [o for o in w["options"] if o["kind"] == w["chosen"]]
    rows.append((x, w["chosen"], ch[0]["p_success"] if ch and ch[0]["p_success"] is not None else None, w["pressure"]))


Match._decide = dec
while not m.done and m.t < float(sys.argv[1] if len(sys.argv) > 1 else 1800):
    m._step()
xs = np.array([r[0] for r in rows])
ks = np.array([r[1] for r in rows])
for lo, hi in [(0, 35), (35, 70), (70, 85), (85, 105)]:
    sel = (xs >= lo) & (xs < hi)
    ps = [r[2] for r, s in zip(rows, sel) if s and r[1] == "pass"]
    print(f"x {lo:3d}-{hi:3d}  n={sel.sum():4d}  {dict(collections.Counter(ks[sel]))}  mean chosen pass p_success={np.mean(ps) if ps else float('nan'):.2f}")
print("mean pressure at decision", np.mean([r[3] for r in rows]).round(2), " decisions/sec", round(len(rows) / m.t, 2))
