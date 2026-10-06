"""Where do the shots come from and how did the ball get there? (a logic probe, not a calibration)

    python scripts/shot_profile.py [N] [key=value ...]
"""
import os, sys, pathlib, collections
from multiprocessing import Pool
import numpy as np
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import calibrate
from matchsim import engine
WORKERS = max(1, (os.cpu_count() or 8) - 4)
L, W = 105., 68.


def _init(over):
    engine.TUNE.update(over)


def one(seed):
    m, r = calibrate.run(seed)
    ev = r["events"]
    shots = [e for e in ev if e["type"] == "shot" and e.get("kind") != "penalty"]
    out = collections.Counter()
    xs = []
    for e in shots:
        x, y = e["x"], e["y"]
        tm = e["team"]
        ax = x if tm == 0 else L - x
        ay = y
        d = np.hypot(L - ax, W / 2 - ay)
        out["n"] += 1
        out["xg"] += e["xg"]
        out["box"] += (L - ax < 16.5 and abs(ay - W / 2) < 20)
        out["six"] += (L - ax < 6 and abs(ay - W / 2) < 9)
        out["d<12"] += d < 12
        out["d12-20"] += 12 <= d < 20
        out["d>=20"] += d >= 20
        out["pressed"] += bool(e.get("under_pressure"))
        out["first_time"] += bool(e.get("first_time"))
        out["assisted"] += e.get("assist") is not None
        out["hold<1"] += e.get("hold", 9) < 1
        out["big"] += e.get("big_chance", False)
        out["head"] += e.get("body_part") == "head"
    # ball progression into the box: entries by team
    return out, r["meta"]["score"]


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if "=" not in a]
    over = {k: float(v) for k, v in (a.split("=") for a in sys.argv[1:] if "=" in a)}
    n = int(args[0]) if args else 24
    with Pool(WORKERS, initializer=_init, initargs=(over,)) as pool:
        res = pool.map(one, range(900, 900 + n))
    tot = collections.Counter()
    for o, sc in res:
        tot.update(o)
    print(f"per match: " + "  ".join(f"{k} {v / n:.1f}" for k, v in tot.items()))
