"""On the ball near the byline (within 8 m of the goal line, at most 25 m off the middle), per team-match, real (Euro 2020) against
simulated matches: how often a side gets there and what the player does (a cut-back to the penalty area, a cross, a shot, lost).
Real big chances from cut-backs are 0.25 per team-match, the sim's 0.03.

    python scripts/byline.py [matches] [key=value ...]
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
near = lambda x, y: x > L - 8 and abs(y - W / 2) < 25


def what(kind, end=None):
    if kind == "pass":
        ex, ey = end
        if ex < L - 6 and ex > L - 18 and abs(ey - W / 2) < 12:
            return "cut-back (to 6-18 m, central)"
        return "other pass / cross"
    return kind


def real():
    c, n = collections.Counter(), 0
    for f in glob.glob(str(ROOT.parent / "reference_data" / "euro2020" / "[0-9]*.json")):
        if "_360" in f:
            continue
        n += 1
        for e in json.load(open(f, encoding="utf-8")):
            t = e["type"]["name"]
            if t not in ("Pass", "Shot", "Dispossessed", "Miscontrol", "Dribble") or "location" not in e:
                continue
            if t == "Pass" and "type" in e["pass"] or t == "Shot" and e["shot"]["type"]["name"] != "Open Play":
                continue
            x, y = e["location"][0] * L / 120, e["location"][1] * W / 80
            if not near(x, y):
                continue
            if t == "Pass":
                k = what("pass", (e["pass"]["end_location"][0] * L / 120, e["pass"]["end_location"][1] * W / 80))
            else:
                k = {"Shot": "shot", "Dispossessed": "lost", "Miscontrol": "lost", "Dribble": "take-on"}[t]
            c[k] += 1
    return c, n


def one(seed):
    TUNE.update(KV)
    rng = np.random.default_rng(seed)
    h = make_team("KES", "4-3-3", Tactics(press=.6, line_height=.6, directness=.45), rng=rng)
    a = make_team("NBR", "4-4-2", Tactics(press=.4, line_height=.4, directness=.55, marking=.5), rng=rng)
    c = collections.Counter()
    for e in Match(h, a, seed=seed).run()["events"]:
        if e["type"] not in ("pass", "cross", "shot", "dispossessed", "miscontrol", "take_on") or e.get("source") not in (None, "open_play") or "x" not in e:
            continue
        fl = (lambda x, y: (L - x, W - y)) if e["team"] == 1 else (lambda x, y: (x, y))
        if not near(*fl(e["x"], e["y"])):
            continue
        if e["type"] in ("pass", "cross"):
            k = what("pass", fl(e["end_x"], e["end_y"]))
        else:
            k = {"shot": "shot", "dispossessed": "lost", "miscontrol": "lost", "take_on": "take-on"}[e["type"]]
        c[k] += 1
    return c


if __name__ == "__main__":
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 16
    with Pool(12) as p:
        sim = sum(p.map(one, range(9700, 9700 + n)), collections.Counter())
    rl, nr = real()
    print("on the ball near the byline, per team-match:   sim | real")
    for k in ("cut-back (to 6-18 m, central)", "other pass / cross", "shot", "take-on", "lost"):
        print(f"  {k:30} {sim[k] / n / 2:5.2f} | {rl[k] / nr / 2:5.2f}")
    print(f"  {'all':30} {sum(sim.values()) / n / 2:5.2f} | {sum(rl.values()) / nr / 2:5.2f}")
