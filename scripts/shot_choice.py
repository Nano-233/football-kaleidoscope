"""How often a player on the ball near goal shoots, by how good a clean shooting chance his spot is (the engine's own xG formula with no
defenders), real (Euro 2020 events) against simulated matches. A real decision frequency for the shooting decision to match.

    python scripts/shot_choice.py [matches] [key=value ...]
"""
import sys, json, glob, pathlib, collections
import numpy as np
from multiprocessing import Pool
ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from matchsim.engine import Match, TUNE
from matchsim.teams import make_team, Tactics

BANDS = [(0, .02), (.02, .04), (.04, .07), (.07, .12), (.12, .2), (.2, 1.)]
KV = {k: float(v) for k, v in (a.split("=") for a in sys.argv[2:])}


def xg0(x, y):
    return Match._xg(None, np.array([x, y]), [])[0]


def band(v):
    return next(i for i, (a, b) in enumerate(BANDS) if a <= v < b)


def real():
    c = collections.Counter()
    for f in glob.glob(str(ROOT.parent / "reference_data" / "euro2020" / "[0-9]*.json")):
        if "_360" in f:
            continue
        for e in json.load(open(f, encoding="utf-8")):
            t = e["type"]["name"]
            if t not in ("Pass", "Carry", "Dribble", "Shot") or e.get("play_pattern", {}).get("name") not in ("Regular Play", "From Counter"):
                continue
            if t == "Pass" and "type" in e["pass"] or t == "Shot" and e["shot"]["type"]["name"] != "Open Play" or t == "Shot" and e["shot"].get("body_part", {}).get("name") == "Head":
                continue
            x, y = e["location"][0] * 105 / 120, e["location"][1] * 68 / 80
            if x < 70:
                continue
            b = band(xg0(x, y))
            c[(b, "all")] += 1
            c[(b, "shot")] += t == "Shot"
    return c


def one(seed):
    TUNE.update(KV)
    rng = np.random.default_rng(seed)
    h = make_team("KES", "4-3-3", Tactics(press=.6, line_height=.6, directness=.45), rng=rng)
    a = make_team("NBR", "4-4-2", Tactics(press=.4, line_height=.4, directness=.55, marking=.5), rng=rng)
    c = collections.Counter()
    for e in Match(h, a, seed=seed).run()["events"]:
        if e["type"] not in ("pass", "cross", "carry", "take_on", "shot") or e.get("source") not in (None, "open_play") or e.get("body_part") in ("hands", "head"):
            continue
        if e["type"] == "shot" and e.get("kind") != "shot":
            continue
        x, y = (e["x"], e["y"]) if e["team"] == 0 else (105 - e["x"], 68 - e["y"])
        if x < 70:
            continue
        b = band(xg0(x, y))
        c[(b, "all")] += 1
        c[(b, "shot")] += e["type"] == "shot"
    return c


if __name__ == "__main__":
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 16
    with Pool(12) as p:
        sim = sum(p.map(one, range(9700, 9700 + n)), collections.Counter())
    rl = real()
    print("clean-look xG band   actions sim | real    shot share sim | real")
    for i, (a, b) in enumerate(BANDS):
        print(f"  {a:.2f}-{b:.2f}        {sim[(i, 'all')] / n / 2:6.1f} | {rl[(i, "all")] / 31 / 2:6.1f}      {sim[(i, 'shot')] / max(sim[(i, 'all')], 1):.3f} | {rl[(i, 'shot')] / max(rl[(i, 'all')], 1):.3f}")
