"""Tempo: the time between one completed pass and the team's next pass in the same move, and how many passes a move has, real (Euro 2020,
event timestamps) against simulated matches. Both sides play about 56 minutes of ball in play, yet the sim makes 24 % more passes and
45 % more carries: is the ball moved quicker, or are there more moves?

    python scripts/tempo.py [matches] [key=value ...]
"""
import sys, json, glob, pathlib, collections
import numpy as np
from multiprocessing import Pool
ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from matchsim.engine import Match, TUNE
from matchsim.teams import make_team, Tactics

KV = {k: float(v) for k, v in (a.split("=") for a in sys.argv[2:])}
BINS = (1.5, 2.5, 4., 6., 10., 1e9)


def ts(e):
    h, m, s = e["timestamp"].split(":")
    return e["period"] * 10000 + int(h) * 3600 + int(m) * 60 + float(s)


def real():
    gaps, moves, n = [], [], 0
    for f in glob.glob(str(ROOT.parent / "reference_data" / "euro2020" / "[0-9]*.json")):
        if "_360" in f:
            continue
        n += 1
        ev = json.load(open(f, encoding="utf-8"))
        per = collections.defaultdict(list)
        for e in ev:
            if e["type"]["name"] == "Pass" and e.get("possession_team", {}).get("id") == e["team"]["id"]:
                per[(e["period"], e["possession"])].append(e)
        for ps in per.values():
            moves.append(len(ps))
            for a, b in zip(ps, ps[1:]):
                if "outcome" not in a["pass"]:
                    gaps.append(ts(b) - ts(a))
    return np.array(gaps), np.array(moves), n


def one(seed):
    TUNE.update(KV)
    rng = np.random.default_rng(seed)
    h = make_team("KES", "4-3-3", Tactics(press=.6, line_height=.6, directness=.45), rng=rng)
    a = make_team("NBR", "4-4-2", Tactics(press=.4, line_height=.4, directness=.55, marking=.5), rng=rng)
    ev = Match(h, a, seed=seed).run()["events"]
    gaps, moves, cur, last = [], [], 0, None
    for e in ev:
        if e["type"] in ("possession_change", "throw_in", "goal_kick", "corner", "free_kick", "kickoff", "ball_out"):
            if cur:
                moves.append(cur)
            cur, last = 0, None
        if e["type"] in ("pass", "cross"):
            cur += 1
            if last is not None and last.get("outcome") == "complete" and last["team"] == e["team"]:
                gaps.append(e["t"] - last["t"])
            last = e
    return gaps, moves


if __name__ == "__main__":
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 16
    with Pool(12) as p:
        parts = p.map(one, range(9700, 9700 + n))
    sg = np.concatenate([np.array(x[0]) for x in parts]); sm = np.concatenate([np.array(x[1]) for x in parts])
    rg, rm, nr = real()
    rg = rg[(rg > 0) & (rg < 60)]
    print("time from a completed pass to the team's next pass (s):   sim | real")
    print(f"  median {np.median(sg):.2f} | {np.median(rg):.2f}   mean {sg.mean():.2f} | {rg.mean():.2f}")
    lo = 0.
    for b in BINS:
        print(f"  {lo:4.1f}-{b if b < 1e8 else '':4} s   {np.mean((sg >= lo) & (sg < b)):.2f} | {np.mean((rg >= lo) & (rg < b)):.2f}")
        lo = b
    print(f"moves (passes per possession, possessions with at least one pass) per team-match {len(sm[sm > 0]) / n / 2:.1f} | {len(rm[rm > 0]) / nr / 2:.1f};  passes per move median {np.median(sm[sm > 0]):.0f} | {np.median(rm[rm > 0]):.0f}, mean {sm[sm > 0].mean():.1f} | {rm[rm > 0].mean():.1f}")
