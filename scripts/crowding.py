"""How close the carrier's nearest team-mate is, by how long his side has had the ball (sim frames), against the real on-ball freeze
frames: is the crowding around the man on the ball a moment after a regain or the way the sim's shape always stands?

    python scripts/crowding.py [matches] [key=value ...]
"""
import sys, json, glob, pathlib, collections
import numpy as np
from multiprocessing import Pool
ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from matchsim.engine import Match, TUNE
from matchsim.teams import make_team, Tactics

KV = {k: float(v) for k, v in (a.split("=") for a in sys.argv[2:])}
L, W = 105., 68.
PH = ((0, 3), (3, 8), (8, 20), (20, 1e9))


def real():
    c = collections.Counter()
    for f in glob.glob(str(ROOT.parent / "reference_data" / "euro2020" / "[0-9]*_360.json")):
        for fr in json.load(open(f, encoding="utf-8")):
            ff = fr["freeze_frame"]
            act = next((p for p in ff if p.get("actor")), None)
            if not act or act.get("keeper"):
                continue
            C = np.array(act["location"]) * [L / 120, W / 80]
            ds = [np.hypot(*(np.array(p["location"]) * [L / 120, W / 80] - C)) for p in ff if p["teammate"] and not p["actor"] and not p["keeper"]]
            if ds:
                c["n"] += 1
                c["within 5"] += min(ds) < 5
    return c


def one(seed):
    TUNE.update(KV)
    rng = np.random.default_rng(seed)
    h = make_team("KES", "4-3-3", Tactics(press=.6, line_height=.6, directness=.45), rng=rng)
    a = make_team("NBR", "4-4-2", Tactics(press=.4, line_height=.4, directness=.55, marking=.5), rng=rng)
    m = Match(h, a, seed=seed).run()
    F = np.asarray(m["frames"])
    gk = {s for s in range(22) if (m["meta"]["home"]["players"] + m["meta"]["away"]["players"])[s]["role"] == "GK"}
    c, last, tw = collections.Counter(), -1, 0.
    for k, f in enumerate(F):
        if int(f[4]) != last:
            last, tw = int(f[4]), f[0]
        car = int(f[3])
        if k % 3 or car < 0 or car in gk:
            continue
        t = car // 11
        ds = [np.hypot(*(f[5 + 2 * s:7 + 2 * s] - f[5 + 2 * car:7 + 2 * car])) for s in range(11 * t, 11 * t + 11) if s != car and s not in gk and f[6 + 2 * s] > -5]
        if not ds:
            continue
        ph = next(i for i, (a_, b_) in enumerate(PH) if a_ <= f[0] - tw < b_)
        c[(ph, "n")] += 1
        c[(ph, "within 5")] += min(ds) < 5
    return c


if __name__ == "__main__":
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 16
    with Pool(12) as p:
        sim = sum(p.map(one, range(9700, 9700 + n)), collections.Counter())
    rl = real()
    print(f"carrier's nearest team-mate within 5 m: real {rl['within 5'] / rl['n']:.2f} (all on-ball frames)")
    for i, (a, b) in enumerate(PH):
        print(f"  sim, {a}-{b if b < 1e8 else ''} s after his side won it: {sim[(i, 'within 5')] / max(sim[(i, 'n')], 1):.2f}  (share of frames {sim[(i, 'n')] / sum(sim[(j, 'n')] for j in range(4)):.2f})")
