"""How does the defence behave when the ball is near its goal? (logic probe, tracking based)

    python scripts/defence_probe.py [N] [key=value ...]

Samples every frame where a team holds the ball inside the opposition's last 30 m and reports, for the defending side:
players inside the box / within 25 m of goal, depth of the back line, nearest defender to the ball and attackers who have a
defender within 2 m (marked) while inside the box. Reference values are rough public tracking-data norms.
"""
import os, sys, pathlib
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
    fr = np.array(r["frames"])[::5]
    pos = fr[:, 5:49].reshape(len(fr), 22, 2)
    rows = []
    for k in range(len(fr)):
        c = int(fr[k, 3])
        if c < 0 or pos[k, :, 1].min() < -5:
            continue
        t = c // 11
        P = pos[k, 11 * t:11 * t + 11]
        D = pos[k, 11 * (1 - t):11 * (1 - t) + 11]
        if t == 1:
            P, D = L - P, L - D  # attackers move +x; defenders' goal at x = L: express defenders as distance from own goal
        dx = L - D[:, 0]                    # distance of each defender from his own goal line
        B = P[c - 11 * t]
        if B[0] < L - 30:
            continue
        inbox = (dx < 16.5) & (np.abs(D[:, 1] - W / 2) < 20.16)
        out = np.arange(11) != 0
        back = np.sort(dx[out])[:4]
        dball = np.hypot(*(D - B).T)
        att_in_box = [(L - p[0] < 16.5 and abs(p[1] - W / 2) < 20.16) for p in P]
        marked = [np.hypot(*(D - p).T).min() < 2. for p, a in zip(P, att_in_box) if a]
        rows.append([inbox.sum(), (dx < 25).sum(), back.mean(), dball.min(), np.sort(dball)[:3].mean(), (dball < 10).sum(), np.mean(marked) if marked else np.nan, sum(att_in_box)])
    return np.array(rows)


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if "=" not in a]
    over = {k: float(v) for k, v in (a.split("=") for a in sys.argv[1:] if "=" in a)}
    n = int(args[0]) if args else 24
    with Pool(WORKERS, initializer=_init, initargs=(over,)) as pool:
        rows = np.vstack(pool.map(one, range(900, 900 + n)))
    mu = np.nanmean(rows, 0)
    print(f"{len(rows)} samples (ball held within 30 m of goal)")
    print(f"defenders in the box          {mu[0]:.1f}   (real about 5-7 incl. keeper)")
    print(f"defenders within 25 m of goal {mu[1]:.1f}   (real about 7-9)")
    print(f"back-four depth from goal     {mu[2]:.1f} m (real about 18-26)")
    print(f"nearest defender to ball      {mu[3]:.1f} m (real about 2-3.5)")
    print(f"defenders within 10 m of ball {mu[5]:.1f}   (real about 3-4)")
    print(f"attackers in box with a defender within 2 m {mu[6]:.2f} (real about 0.5-0.65)   attackers in box {mu[7]:.1f} (real about 2.5-4)")
