"""Where take-ons happen, real (StatsBomb open data, Premier League 2015/16) vs the simulator, per team-match by zone; and, in the sim,
how often a carrier with a man in his path (a carry option that is a take-on) chooses it.

    python scripts/takeon_where.py [matches] [key=value ...]
"""
import collections
import glob
import json
import pathlib
import sys
from multiprocessing import Pool

import numpy as np

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from matchsim import engine  # noqa: E402
from matchsim.teams import Tactics, make_team  # noqa: E402

KV = {k: float(v) for k, v in (a.split("=") for a in sys.argv[2:])}
L, W = 105., 68.


def zone(x, y):  # attacking left to right, metres
    return ("own" if x < L / 3 else "mid" if x < 2 * L / 3 else "fin") + ("-wide" if abs(y - W / 2) > 20.16 else "-cent")


def real():
    z, n = collections.Counter(), 0
    for f in glob.glob(str(pathlib.Path(__file__).resolve().parents[2] / "reference_data" / "pl1516" / "*.json")):
        data = json.load(open(f, encoding="utf-8"))
        if not isinstance(data, list) or not data or "type" not in data[0]:
            continue
        n += 1
        for e in data:
            ty = e.get("type", {}).get("name")
            if ty in ("Dribble", "Pass", "Shot") and "location" in e and not (ty == "Pass" and "type" in e["pass"]):
                x, y = e["location"]
                z[(ty == "Dribble", zone(x * L / 120, y * W / 80))] += 1
    return z, 2 * n


def one(seed):
    engine.TUNE.update(KV)
    rng = np.random.default_rng(seed)
    m = engine.Match(make_team("KES", "4-3-3", Tactics(), rng=rng), make_team("NBR", "4-4-2", Tactics(), rng=rng), seed=seed)
    m.audit = []
    r = m.run()
    z = collections.Counter()
    for e in r["events"]:
        if e["type"] == "take_on":
            x, y = e["x"], e["y"]
            if e["team"] == 1:
                x, y = L - x, W - y
            z[(True, zone(x, y))] += 1
    ch = collections.Counter()
    for a in m.audit:
        if a["rk"] is not None:
            continue
        blk = [o for o in a["opts"] if o["kind"] == "carry" and o["blocked"]]
        ch["decisions"] += 1
        x, y = a["pos"]
        z[(False, zone(x, y))] += a["opts"][a["chosen"]]["kind"] in ("pass", "cross", "shot")
        best = a["opts"][a["chosen"]]
        if best["kind"] == "carry":
            ch["carry"] += 1
            ch["takeon_chosen"] += best["blocked"]
        if blk:
            ch["best_carry_is_takeon"] += 1
    # a man within 3 m ahead of the carrier at a decision (every decision has one best carry, so count the blocked lines differently)
    return z, ch


if __name__ == "__main__":
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 12
    rz, rn = real()
    with Pool(min(n, 12)) as p:
        res = p.map(one, range(900, 900 + n))
    sz = sum((r[0] for r in res), collections.Counter()); ch = sum((r[1] for r in res), collections.Counter())
    print(f"take-ons per team-match by zone (real {rn} team-matches, sim {2 * n})")
    for k in sorted({k for _, k in rz}):
        print(f"  {k:10s} real {rz[True, k] / rn:5.2f}  sim {sz[True, k] / (2 * n):5.2f}   per pass/shot: real {rz[True, k] / rz[False, k]:.3f}  sim {sz[True, k] / max(sz[False, k], 1):.3f}   passes+shots real {rz[False, k] / rn:5.1f} sim {sz[False, k] / (2 * n):5.1f}")
    print(f"  total      real {sum(v for (d, _), v in rz.items() if d) / rn:5.2f}  sim {sum(v for (d, _), v in sz.items() if d) / (2 * n):5.2f}")
    print({k: round(v / (2 * n), 1) for k, v in ch.items()}, "per team-match")
