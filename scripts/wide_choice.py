"""What a player on the ball out wide in the final third does (more than 20 m off the middle, within 35 m of the goal line), per
team-match, real (Euro 2020) against simulated matches: a ball into the box, a pass inside or back, a pass down the line, a carry
or take-on, lost. The sim puts about 17.5 balls a team-match into the box from wide against 9.6.

    python scripts/wide_choice.py [matches] [key=value ...]
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
wide = lambda x, y: abs(y - W / 2) > 20 and x > L - 35
in_box = lambda x, y: x > L - 16.5 and abs(y - W / 2) < 20.16
KINDS = ("into the box", "pass inside / back", "pass down the line", "shot", "lost")


def pass_kind(o, e):
    if in_box(*e):
        return "into the box"
    return "pass down the line" if abs(e[1] - W / 2) > 20 and e[0] > o[0] else "pass inside / back"


def real():
    c, n = collections.Counter(), 0
    for f in glob.glob(str(ROOT.parent / "reference_data" / "euro2020" / "[0-9]*.json")):
        if "_360" in f:
            continue
        n += 1
        for e in json.load(open(f, encoding="utf-8")):
            t = e["type"]["name"]
            if t not in ("Pass", "Shot", "Dispossessed", "Miscontrol") or "location" not in e or t == "Pass" and "type" in e["pass"]:
                continue
            o = np.array(e["location"]) * [L / 120, W / 80]
            if not wide(*o):
                continue
            k = pass_kind(o, np.array(e["pass"]["end_location"]) * [L / 120, W / 80]) if t == "Pass" else "shot" if t == "Shot" else "lost"
            c[k] += 1
    return c, n


def one(seed):
    TUNE.update(KV)
    rng = np.random.default_rng(seed)
    h = make_team("KES", "4-3-3", Tactics(press=.6, line_height=.6, directness=.45), rng=rng)
    a = make_team("NBR", "4-4-2", Tactics(press=.4, line_height=.4, directness=.55, marking=.5), rng=rng)
    c = collections.Counter()
    for e in Match(h, a, seed=seed).run()["events"]:
        if e["type"] not in ("pass", "cross", "shot", "dispossessed", "miscontrol") or e.get("source") not in (None, "open_play") or "x" not in e:
            continue
        fl = (lambda x, y: np.array([L - x, W - y])) if e["team"] == 1 else (lambda x, y: np.array([x, y]))
        o = fl(e["x"], e["y"])
        if not wide(*o):
            continue
        k = pass_kind(o, fl(e["end_x"], e["end_y"])) if e["type"] in ("pass", "cross") else "shot" if e["type"] == "shot" else "lost"
        c[k] += 1
    return c


if __name__ == "__main__":
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 16
    with Pool(12) as p:
        sim = sum(p.map(one, range(9700, 9700 + n)), collections.Counter())
    rl, nr = real()
    S, R = sum(sim.values()), sum(rl.values())
    print(f"on the ball wide in the final third: {S / n / 2:.1f} | {R / nr / 2:.1f} actions per team-match (sim | real); share by choice:")
    for k in KINDS:
        print(f"  {k:20} {sim[k] / S:.2f} | {rl[k] / R:.2f}   ({sim[k] / n / 2:.1f} | {rl[k] / nr / 2:.1f})")
