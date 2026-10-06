"""How does the ball get into dangerous areas, and how many defenders are behind it when it does? (logic probe)

    python scripts/entry_profile.py [N] [key=value ...]
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


def inbox(x, y):
    return L - x < 16.5 and abs(y - W / 2) < 20.16


def one(seed):
    m, r = calibrate.run(seed)
    ev = r["events"]
    fr = np.array(r["frames"])
    ft = fr[:, 0]
    out = collections.Counter()
    for e in ev:
        if e["type"] in ("pass", "cross") and e.get("source") not in ("corner", "throw_in"):
            tm = e["team"]
            ax, ex = (e["x"], e["end_x"]) if tm == 0 else (L - e["x"], L - e["end_x"])
            if inbox(ex, e["end_y"]) and not inbox(ax, e["y"]):
                out["pass_into_box"] += 1
                out["pass_into_box_complete"] += e.get("outcome") == "complete"
                k = min(np.searchsorted(ft, e["t"]), len(fr) - 1)
                pos = fr[k, 5:49].reshape(22, 2)
                d = pos[11 * (1 - tm):11 * (1 - tm) + 11]
                ball_x = e["x"] if tm == 0 else L - e["x"]
                dx = d[:, 0] if tm == 0 else L - d[:, 0]
                out["defenders_behind_ball"] += int((dx > ball_x).sum())   # goal-side of the passer (incl. keeper)
                out["defenders_in_box_at_pass"] += sum(inbox(x if tm == 0 else L - x, y) for x, y in d)
                out["n_samples"] += 1
        if e["type"] == "carry":
            tm = e["team"]
            sx, ex = (e["x"], e["end_x"]) if tm == 0 else (L - e["x"], L - e["end_x"])
            if inbox(ex, e["end_y"]) and not inbox(sx, e["y"]):
                out["carry_into_box"] += 1
        if e["type"] == "shot" and e["kind"] != "penalty":
            out["shots"] += 1
    return out


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if "=" not in a]
    over = {k: float(v) for k, v in (a.split("=") for a in sys.argv[1:] if "=" in a)}
    n = int(args[0]) if args else 24
    with Pool(WORKERS, initializer=_init, initargs=(over,)) as pool:
        res = pool.map(one, range(900, 900 + n))
    tot = collections.Counter()
    for o in res:
        tot.update(o)
    ns = max(tot["n_samples"], 1)
    print("per match:", {k: round(v / n, 1) for k, v in tot.items() if k not in ("n_samples", "defenders_behind_ball", "defenders_in_box_at_pass")})
    print(f"at a pass into the box: defenders goal-side of the passer {tot['defenders_behind_ball'] / ns:.1f} (real about 6-8), defenders inside the box {tot['defenders_in_box_at_pass'] / ns:.1f}")
