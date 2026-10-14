"""Own-third passes (open play, outfield) by the time since the side won the ball, per team-match, real (Euro 2020) against simulated
matches: are the sim's extra passes at the back the first seconds after a regain (the shape not yet spread) or settled build-up?

    python scripts/own_third_timing.py [matches] [key=value ...]
"""
import sys, json, glob, pathlib, collections
import numpy as np
from multiprocessing import Pool
ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from matchsim.engine import Match, TUNE
from matchsim.teams import make_team, Tactics

KV = {k: float(v) for k, v in (a.split("=") for a in sys.argv[2:])}
L = 105.
BINS = [(0, 3), (3, 8), (8, 20), (20, 1e9)]
band = lambda s: next(i for i, (a, b) in enumerate(BINS) if a <= s < b)


def secs(ts):
    h, m, s = ts.split(":")
    return int(h) * 3600 + int(m) * 60 + float(s)


def real():
    c, n = collections.Counter(), 0
    for f in glob.glob(str(ROOT.parent / "reference_data" / "euro2020" / "[0-9]*.json")):
        if "_360" in f:
            continue
        n += 1
        start = {}
        for e in json.load(open(f, encoding="utf-8")):
            key = (e["period"], e["possession"])
            start.setdefault(key, secs(e["timestamp"]))
            if e["type"]["name"] != "Pass" or "type" in e["pass"] or "Goalkeeper" in e.get("position", {}).get("name", "") or e["location"][0] * L / 120 > L / 3:
                continue
            c[band(secs(e["timestamp"]) - start[key])] += 1
    return c, n


def one(seed):
    TUNE.update(KV)
    rng = np.random.default_rng(seed)
    h = make_team("KES", "4-3-3", Tactics(press=.6, line_height=.6, directness=.45), rng=rng)
    a = make_team("NBR", "4-4-2", Tactics(press=.4, line_height=.4, directness=.55, marking=.5), rng=rng)
    m = Match(h, a, seed=seed).run()
    gk_ids = {p["id"] for s in ("home", "away") for p in m["meta"][s]["players"] + m["meta"][s]["bench"] if p["role"] == "GK"}
    c, won, restart = collections.Counter(), {0: 0., 1: 0.}, False
    for e in m["events"]:
        if e["type"] == "possession_change" and e.get("team") in (0, 1):
            won[e["team"]] = e["t"]
        if e["type"] in ("throw_in", "goal_kick", "free_kick", "corner", "kickoff"):
            restart = True
        if e["type"] == "possession_change":
            restart = restart and e["t"] - won.get(e.get("team"), 0) < .1
        if e["type"] != "pass" or e.get("source") not in (None, "open_play") or e.get("player") in gk_ids:
            continue
        if (e["x"] if e["team"] == 0 else L - e["x"]) > L / 3:
            continue
        c[band(e["t"] - won[e["team"]])] += 1
    return c


if __name__ == "__main__":
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 16
    with Pool(12) as p:
        sim = sum(p.map(one, range(9700, 9700 + n)), collections.Counter())
    rl, nr = real()
    print("own-third passes (open play) by seconds since the side won the ball, per team-match:   sim | real (all possessions)")
    for i, (a, b) in enumerate(BINS):
        print(f"  {a}-{b if b < 1e8 else ''} s   {sim[i] / n / 2:5.1f} | {rl[i] / nr / 2:5.1f}")
