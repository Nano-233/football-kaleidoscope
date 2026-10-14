"""How possessions end, per match, real (Euro 2020: the last event of each possession) against simulated matches (the event before
each possession change or restart). The sim changes possession about 1.6x as often as real football.

    python scripts/poss_end.py [matches] [key=value ...]
"""
import sys, json, glob, pathlib, collections
import numpy as np
from multiprocessing import Pool
ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from matchsim.engine import Match, TUNE
from matchsim.teams import make_team, Tactics

KV = {k: float(v) for k, v in (a.split("=") for a in sys.argv[2:])}
REAL = {"Pass": "pass misplaced or cut out", "Interception": "pass misplaced or cut out", "Shot": "shot", "Goal Keeper": "shot",
        "Clearance": "clearance", "Dispossessed": "tackled / dispossessed", "Duel": "tackled / dispossessed", "Dribble": "take-on lost",
        "Miscontrol": "miscontrol", "Foul Committed": "foul / offside", "Foul Won": "foul / offside", "Offside": "foul / offside",
        "Ball Recovery": "loose ball", "Block": "block", "Carry": "carry out / other", "Error": "miscontrol"}
SIM = {"pass": "pass misplaced or cut out", "cross": "pass misplaced or cut out", "interception": "pass misplaced or cut out",
       "shot": "shot", "save": "shot", "goal": "shot", "clearance": "clearance", "dispossessed": "tackled / dispossessed",
       "tackle": "tackled / dispossessed", "take_on": "take-on lost", "dribbled_past": "take-on lost", "miscontrol": "miscontrol",
       "error": "miscontrol", "foul": "foul / offside", "offside": "foul / offside", "ball_recovery": "loose ball",
       "fifty_fifty": "loose ball", "duel": "aerial duel", "block": "block", "carry": "carry out / other", "keeper_claim": "shot"}


def real():
    c, n = collections.Counter(), 0
    for f in glob.glob(str(ROOT.parent / "reference_data" / "euro2020" / "[0-9]*.json")):
        if "_360" in f:
            continue
        n += 1
        ev = json.load(open(f, encoding="utf-8"))
        for a, b in zip(ev, ev[1:]):
            if b["possession"] != a["possession"]:
                prev = a
                k = REAL.get(prev["type"]["name"], prev["type"]["name"])
                c[k] += 1
    return c, n


def one(seed):
    TUNE.update(KV)
    rng = np.random.default_rng(seed)
    h = make_team("KES", "4-3-3", Tactics(press=.6, line_height=.6, directness=.45), rng=rng)
    a = make_team("NBR", "4-4-2", Tactics(press=.4, line_height=.4, directness=.55, marking=.5), rng=rng)
    ev = Match(h, a, seed=seed).run()["events"]
    c = collections.Counter()
    for i, e in enumerate(ev):
        if e["type"] != "possession_change":
            continue
        prev = next((b for b in reversed(ev[max(0, i - 8):i]) if b["type"] not in ("pressure", "ball_receipt", "possession_change") and b.get("team") != e["team"] and b.get("player")), None)
        if prev is None:
            prev = next((b for b in reversed(ev[max(0, i - 8):i]) if b["type"] in ("throw_in", "goal_kick", "corner", "free_kick", "kickoff", "ball_out")), None)
            c["restart / out of play" if prev else "?"] += 1
            continue
        c[SIM.get(prev["type"], prev["type"])] += 1
    return c


if __name__ == "__main__":
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 16
    with Pool(12) as p:
        sim = sum(p.map(one, range(9700, 9700 + n)), collections.Counter())
    rl, nr = real()
    print("possessions per match by how they end:   sim | real")
    for k in sorted(set(sim) | set(rl), key=lambda k: -(sim[k] / n + rl[k] / nr))[:16]:
        print(f"  {k:28} {sim[k] / n:6.1f} | {rl[k] / nr:6.1f}")
    print(f"  {'all':28} {sum(sim.values()) / n:6.1f} | {sum(rl.values()) / nr:6.1f}")
