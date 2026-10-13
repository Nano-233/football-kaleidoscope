"""Passes aimed into the good shooting spots (clean-look xG >= 0.12), real (Euro 2020) against simulated matches: how many are tried,
from where, and how many arrive. Splits zone_entry.py's "forward" gap into choosing the pass and the pass getting through.

    python scripts/zone_passes.py [matches] [key=value ...]
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


def origin(x, y):
    return "wide" if abs(y - W / 2) > 20 else "byline" if x > L - 8 else "inside" if in_zone(x, y) else "in front"


def real():
    c, n = collections.Counter(), 0
    for f in glob.glob(str(ROOT.parent / "reference_data" / "euro2020" / "[0-9]*.json")):
        if "_360" in f:
            continue
        n += 1
        for e in json.load(open(f, encoding="utf-8")):
            if e["type"]["name"] != "Pass" or "type" in e["pass"] or e["pass"].get("body_part", {}).get("name") == "Head":
                continue
            ex, ey = e["pass"]["end_location"][0] * L / 120, e["pass"]["end_location"][1] * W / 80
            if not in_zone(ex, ey):
                continue
            k = origin(e["location"][0] * L / 120, e["location"][1] * W / 80)
            c[(k, "tried")] += 1
            c[(k, "arrived")] += "outcome" not in e["pass"]
    return c, n


def one(seed):
    TUNE.update(KV)
    rng = np.random.default_rng(seed)
    h = make_team("KES", "4-3-3", Tactics(press=.6, line_height=.6, directness=.45), rng=rng)
    a = make_team("NBR", "4-4-2", Tactics(press=.4, line_height=.4, directness=.55, marking=.5), rng=rng)
    c = collections.Counter()
    for e in Match(h, a, seed=seed).run()["events"]:
        if e["type"] not in ("pass", "cross") or e.get("source") not in (None, "open_play") or e.get("body_part") in ("head", "hands") or e.get("end_x") is None:
            continue
        fl = (lambda x, y: (L - x, W - y)) if e["team"] == 1 else (lambda x, y: (x, y))
        if not in_zone(*fl(e["end_x"], e["end_y"])):
            continue
        k = origin(*fl(e["x"], e["y"]))
        c[(k, "tried")] += 1
        c[(k, "arrived")] += e.get("outcome") == "complete"
    return c


if __name__ == "__main__":
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 16
    with Pool(12) as p:
        sim = sum(p.map(one, range(9700, 9700 + n)), collections.Counter())
    rl, nr = real()
    print("passes aimed at clean-look xG >= 0.12, per team-match       tried sim | real     arrived sim | real")
    for k in ("in front", "wide", "byline", "inside"):
        print(f"  from {k:9}   {sim[(k, 'tried')] / n / 2:5.2f} | {rl[(k, 'tried')] / nr / 2:5.2f}      {sim[(k, 'arrived')] / max(sim[(k, 'tried')], 1):.2f} | {rl[(k, 'arrived')] / max(rl[(k, 'tried')], 1):.2f}")
