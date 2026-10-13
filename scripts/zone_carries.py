"""Carries that take the ball into the good shooting spots (clean-look xG >= 0.12) from outside them, real (Euro 2020 360 frames) against
simulated matches: how many, how long, and how close the nearest defender was and how many stood between the carrier and goal
when he set off. The sim carries into the zone about 3.5x as often as real players (zone_entry.py).

    python scripts/zone_carries.py [matches] [key=value ...]
"""
import sys, json, glob, pathlib, collections
import numpy as np
from multiprocessing import Pool
ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from matchsim.engine import Match, TUNE
from matchsim.teams import make_team, Tactics
from scripts.zone_entry import in_zone

KV = {k: float(v) for k, v in (a.split("=") for a in sys.argv[2:])}
L, W = 105., 68.


def tally(c, a, b, opp):
    c["n"] += 1
    c[("len", min(int(np.hypot(*(b - a)) / 5), 4))] += 1
    nd = min((np.hypot(*(o - a)) for o in opp), default=99)
    c[("nearest", 0 if nd < 2 else 1 if nd < 4 else 2 if nd < 7 else 3)] += 1
    g = np.array([L, W / 2])
    between = sum(1 for o in opp if np.hypot(*(g - o)) < np.hypot(*(g - a)) and abs(np.cross(_u(g - a), o - a)) < 5)
    c[("between", min(between, 3))] += 1


def _u(v):
    return v / max(np.hypot(*v), 1e-9)


def real():
    c = collections.Counter(); n = 0
    for f in glob.glob(str(ROOT.parent / "reference_data" / "euro2020" / "[0-9]*_360.json")):
        n += 1
        ff = {r["event_uuid"]: r["freeze_frame"] for r in json.load(open(f, encoding="utf-8"))}
        for e in json.load(open(f.replace("_360", ""), encoding="utf-8")):
            if e["type"]["name"] != "Carry" or e["id"] not in ff:
                continue
            a = np.array([e["location"][0] * L / 120, e["location"][1] * W / 80]); b = np.array([e["carry"]["end_location"][0] * L / 120, e["carry"]["end_location"][1] * W / 80])
            if in_zone(*a) or not in_zone(*b) or np.hypot(*(b - a)) < 2:
                continue
            tally(c, a, b, [np.array([p["location"][0] * L / 120, p["location"][1] * W / 80]) for p in ff[e["id"]] if not p["teammate"] and not p["keeper"]])
    return c, n


def one(seed):
    TUNE.update(KV)
    rng = np.random.default_rng(seed)
    h = make_team("KES", "4-3-3", Tactics(press=.6, line_height=.6, directness=.45), rng=rng)
    a_ = make_team("NBR", "4-4-2", Tactics(press=.4, line_height=.4, directness=.55, marking=.5), rng=rng)
    m = Match(h, a_, seed=seed).run()
    F, dt = np.asarray(m["frames"]), m["meta"]["dt"]
    gk = {s for s in range(22) if (m["meta"]["home"]["players"] + m["meta"]["away"]["players"])[s]["role"] == "GK"}
    c = collections.Counter()
    for e in m["events"]:
        if e["type"] != "carry" or e.get("end_x") is None:
            continue
        flip = e["team"] == 1
        fl = (lambda x, y: np.array([L - x, W - y])) if flip else (lambda x, y: np.array([x, y]))
        a, b = fl(e["x"], e["y"]), fl(e["end_x"], e["end_y"])
        if in_zone(*a) or not in_zone(*b) or np.hypot(*(b - a)) < 2:
            continue
        f = F[min(int(round((e["t"] - e.get("duration", 0)) / dt)), len(F) - 1)]  # when he set off
        tally(c, a, b, [fl(*f[5 + 2 * s:7 + 2 * s]) for s in (range(11) if flip else range(11, 22)) if f[6 + 2 * s] > -5 and s not in gk])
    return c


if __name__ == "__main__":
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 16
    with Pool(12) as p:
        sim = sum(p.map(one, range(9700, 9700 + n)), collections.Counter())
    rl, nr = real()
    print(f"carries into the zone per team-match: sim {sim['n'] / n / 2:.2f} | real {rl['n'] / nr / 2:.2f} (360 sample)")
    for k, names in (("len", ("2-5 m", "5-10", "10-15", "15-20", "20+")), ("nearest", ("<2 m", "2-4", "4-7", "7+")), ("between", ("0", "1", "2", "3+"))):
        print(f"  {k:8} " + "  ".join(f"{nm}: {sim[(k, i)] / max(sim['n'], 1):.2f}|{rl[(k, i)] / max(rl['n'], 1):.2f}" for i, nm in enumerate(names)))
