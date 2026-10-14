"""Where the defending side's last outfield defender stands, by how far the ball is from his goal, real (Euro 2020 360 frames, on-ball
events) against simulated matches (every frame with the attacking side on the ball, central). Real back lines stand at about half the
ball's distance plus 3 m.

    python scripts/line_depth.py [matches] [key=value ...]
"""
import sys, json, glob, pathlib, collections
import numpy as np
from multiprocessing import Pool
ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from matchsim.engine import Match, TUNE
from matchsim.teams import make_team, Tactics

KV = {k: float(v) for k, v in (a.split("=") for a in sys.argv[2:])}
CAM = bool(KV.pop("camera", 1))  # the sim seen through a real broadcast view, with the same "6 defenders in view" rule as the real side
_cam = None
L, W = 105., 68.
BINS = range(10, 45, 5)


def real():
    r = collections.defaultdict(list)
    for f in glob.glob(str(ROOT.parent / "reference_data" / "euro2020" / "[0-9]*_360.json")):
        for fr in json.load(open(f, encoding="utf-8")):
            ff = fr["freeze_frame"]
            act = next((p for p in ff if p.get("actor")), None)
            if not act or abs(act["location"][1] * W / 80 - W / 2) > 20:
                continue
            d = L - act["location"][0] * L / 120
            opp = [L - p["location"][0] * L / 120 for p in ff if not p["teammate"] and not p["keeper"]]
            if 10 <= d < 45 and len(opp) >= 6:
                r[int(d // 5) * 5].append(min(opp))
    return r


def one(seed):
    TUNE.update(KV)
    rng = np.random.default_rng(seed)
    h = make_team("KES", "4-3-3", Tactics(press=.6, line_height=.6, directness=.45), rng=rng)
    a = make_team("NBR", "4-4-2", Tactics(press=.4, line_height=.4, directness=.55, marking=.5), rng=rng)
    m = Match(h, a, seed=seed).run()
    F = np.asarray(m["frames"])
    gk = [s for s in range(22) if (m["meta"]["home"]["players"] + m["meta"]["away"]["players"])[s]["role"] == "GK"]
    r = collections.defaultdict(list)
    for f in F[::5]:
        poss, car = int(f[4]), int(f[3])
        if poss not in (0, 1) or car < 0:
            continue
        bx, by = f[1], f[2]
        if abs(by - W / 2) > 20:
            continue
        dfd = 1 - poss  # the defending side; its goal is at x = 0 for team 0, x = L for team 1
        d = bx if dfd == 0 else L - bx
        xs = [f[5 + 2 * s] for s in range(11 * dfd, 11 * dfd + 11) if s not in gk and f[6 + 2 * s] > -5]
        if CAM and 10 <= d < 45:
            global _cam
            if _cam is None:
                from scripts.camera import Camera
                _cam = Camera()
            att = 1 - dfd
            to_att = (lambda q: np.array(q)) if att == 0 else (lambda q: np.array([L - q[0], W - q[1]]))  # the attacking side's frame, as the real views are stored
            poly = _cam.view(to_att(f[1:3]), rng)
            xs = [f[5 + 2 * s] for s in range(11 * dfd, 11 * dfd + 11) if s not in gk and f[6 + 2 * s] > -5 and _cam.sees(poly, to_att(f[5 + 2 * s:7 + 2 * s]))]
            if len(xs) < 6:
                continue
        if 10 <= d < 45 and xs:
            r[int(d // 5) * 5].append(min(xs) if dfd == 0 else L - max(xs))
    return r


if __name__ == "__main__":
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 16
    with Pool(12) as p:
        parts = p.map(one, range(9700, 9700 + n))
    sim = collections.defaultdict(list)
    for r in parts:
        for k, v in r.items():
            sim[k] += v
    rl = real()
    print("ball from goal   last defender median (quartiles)   sim | real")
    for k in BINS:
        s_, r_ = np.array(sim[k]), np.array(rl[k])
        print(f"  {k}-{k + 5} m   {np.median(s_):5.1f} ({np.percentile(s_, 25):.1f}-{np.percentile(s_, 75):.1f}) | {np.median(r_):5.1f} ({np.percentile(r_, 25):.1f}-{np.percentile(r_, 75):.1f})")
