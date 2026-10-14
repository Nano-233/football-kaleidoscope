"""Passes from the passer's own third (open play, outfield) by length: how many per team-match and how many arrive, real (Euro 2020,
all events) against simulated matches. The sim makes 177 there against 101, at 84 % against 73 %.

    python scripts/own_third_passes.py [matches] [key=value ...]
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
BANDS = [(0, 10), (10, 20), (20, 32), (32, 200)]
band = lambda d: next(i for i, (a, b) in enumerate(BANDS) if a <= d < b)


def real():
    c, n = collections.Counter(), 0
    for f in glob.glob(str(ROOT.parent / "reference_data" / "euro2020" / "[0-9]*.json")):
        if "_360" in f:
            continue
        n += 1
        for e in json.load(open(f, encoding="utf-8")):
            if e["type"]["name"] != "Pass" or "type" in e["pass"] or "Goalkeeper" in e.get("position", {}).get("name", ""):
                continue
            if e["location"][0] * L / 120 > L / 3:
                continue
            b = band(e["pass"]["length"] * L / 120)
            c[(b, "n")] += 1
            c[(b, "ok")] += "outcome" not in e["pass"]
    return c, n


def one(seed):
    TUNE.update(KV)
    rng = np.random.default_rng(seed)
    h = make_team("KES", "4-3-3", Tactics(press=.6, line_height=.6, directness=.45), rng=rng)
    a = make_team("NBR", "4-4-2", Tactics(press=.4, line_height=.4, directness=.55, marking=.5), rng=rng)
    m = Match(h, a, seed=seed).run()
    gk_ids = {p["id"] for s in ("home", "away") for p in m["meta"][s]["players"] + m["meta"][s]["bench"] if p["role"] == "GK"}
    c = collections.Counter()
    for e in m["events"]:
        if e["type"] != "pass" or e.get("source") not in (None, "open_play") or e.get("player") in gk_ids:
            continue
        x = e["x"] if e["team"] == 0 else L - e["x"]
        if x > L / 3:
            continue
        b = band(e["length"])
        c[(b, "n")] += 1
        c[(b, "ok")] += e.get("outcome") == "complete"
    return c


if __name__ == "__main__":
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 16
    with Pool(12) as p:
        sim = sum(p.map(one, range(9700, 9700 + n)), collections.Counter())
    rl, nr = real()
    print("own-third passes by length, per team-match (arrived):   sim | real")
    for i, (a, b) in enumerate(BANDS):
        print(f"  {a:3}-{b if b < 200 else '':3} m   {sim[(i, 'n')] / n / 2:5.1f} ({sim[(i, 'ok')] / max(sim[(i, 'n')], 1):.2f}) | {rl[(i, 'n')] / nr / 2:5.1f} ({rl[(i, 'ok')] / max(rl[(i, 'n')], 1):.2f})")
