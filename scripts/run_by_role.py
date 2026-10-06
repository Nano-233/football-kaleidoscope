"""Distance covered, sprints and top speed per role (averaged over N matches).

    python scripts/run_by_role.py [N]
"""
import sys, pathlib
from multiprocessing import Pool
import numpy as np
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from matchsim.engine import Match
from matchsim.teams import make_team, Tactics, ROLES

# rough real-world match norms (km, outfield unless GK): used as tuning targets
NORMS = {"GK": (4, 6), "CB": (9, 10.5), "FB": (10.5, 12), "DM": (10.5, 12), "CM": (11, 12.5), "WG": (10, 11.5), "AM": (10, 11.5), "ST": (9, 10.5)}


def one(seed):
    rng = np.random.default_rng(seed)
    home_f, away_f = [("4-3-3", "4-4-2"), ("4-2-3-1", "3-5-2")][seed % 2]
    m = Match(make_team("KES", home_f, Tactics(), rng=rng), make_team("NBR", away_f, Tactics(), rng=rng), seed=seed)
    r = m.run()
    pos = np.array(r["frames"])[:, 5:49].reshape(-1, 22, 2)
    dt = r["meta"]["dt"]
    sp = np.linalg.norm(np.diff(pos, axis=0), axis=2) / dt
    sp[sp > 15] = 0
    km = sp.sum(0) * dt / 1000
    sprints = (sp > 7.0).sum(0) * dt  # seconds above 7 m/s (25 km/h)
    return [(ROLES[m.role[i]], km[i], sprints[i], sp[:, i].max()) for i in range(22)]


if __name__ == "__main__":
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 6
    with Pool(6) as p:
        rows = [x for r in p.map(one, range(300, 300 + n)) for x in r]
    print(f"{'role':4s} {'km':>6s}  target     {'sprint s':>8s} {'top m/s':>8s}")
    for role in ROLES:
        sel = [r for r in rows if r[0] == role]
        km = np.mean([r[1] for r in sel])
        lo, hi = NORMS[role]
        print(f"{role:4s} {km:6.2f}  {lo}-{hi:<5} {'ok ' if lo <= km <= hi else 'OFF'} {np.mean([r[2] for r in sel]):8.0f} {np.mean([r[3] for r in sel]):8.2f}")
