"""Where corners come from: the last action before the ball went out over the defending side's goal line, real (Euro 2020) against
simulated matches. Corners are about half the real count; this shows which way of putting the ball behind is missing.

    python scripts/corner_source.py [matches] [key=value ...]
"""
import sys, json, glob, pathlib, collections
import numpy as np
from multiprocessing import Pool
ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from matchsim.engine import Match, TUNE
from matchsim.teams import make_team, Tactics

KV = {k: float(v) for k, v in (a.split("=") for a in sys.argv[2:])}
REAL = {"Shot": "shot (blocked or saved)", "Block": "shot (blocked or saved)", "Goal Keeper": "shot (blocked or saved)", "Clearance": "clearance",
        "Pass": "cross or pass", "Interception": "cross or pass", "Duel": "duel / tackle", "Dribble": "duel / tackle", "Dispossessed": "duel / tackle",
        "Ball Recovery": "other", "Miscontrol": "duel / tackle"}
SIM = {"shot": "shot (blocked or saved)", "block": "shot (blocked or saved)", "save": "shot (blocked or saved)", "keeper_punch": "shot (blocked or saved)",
       "clearance": "clearance", "pass": "cross or pass", "cross": "cross or pass", "interception": "cross or pass", "tackle": "duel / tackle",
       "take_on": "duel / tackle", "duel": "duel / tackle", "fifty_fifty": "duel / tackle", "dispossessed": "duel / tackle", "miscontrol": "duel / tackle"}


def real():
    c, n = collections.Counter(), 0
    for f in glob.glob(str(ROOT.parent / "reference_data" / "euro2020" / "[0-9]*.json")):
        if "_360" in f:
            continue
        n += 1
        ev = json.load(open(f, encoding="utf-8"))
        for i, e in enumerate(ev):
            if e["type"]["name"] == "Pass" and e["pass"].get("type", {}).get("name") == "Corner":
                prev = next((b for b in reversed(ev[max(0, i - 8):i]) if b["type"]["name"] not in ("Pressure", "Ball Receipt*", "Carry", "Half Start", "Substitution", "Referee Ball-Drop")), None)
                c[REAL.get(prev["type"]["name"], prev["type"]["name"]) if prev else "?"] += 1
    return c, n


def one(seed):
    TUNE.update(KV)
    rng = np.random.default_rng(seed)
    h = make_team("KES", "4-3-3", Tactics(press=.6, line_height=.6, directness=.45), rng=rng)
    a = make_team("NBR", "4-4-2", Tactics(press=.4, line_height=.4, directness=.55, marking=.5), rng=rng)
    ev, c = Match(h, a, seed=seed).run()["events"], collections.Counter()
    for i, e in enumerate(ev):
        if e["type"] == "corner":
            prev = next((b for b in reversed(ev[max(0, i - 8):i]) if b["type"] not in ("pressure", "ball_receipt", "carry", "ball_out", "possession_change")), None)
            c[SIM.get(prev["type"], prev["type"]) if prev else "?"] += 1
    return c


if __name__ == "__main__":
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 16
    with Pool(12) as p:
        sim = sum(p.map(one, range(9700, 9700 + n)), collections.Counter())
    rl, nr = real()
    print("corners per match by the last action before the ball went behind:   sim | real")
    for k in sorted(set(sim) | set(rl), key=lambda k: -rl[k] / nr):
        print(f"  {k:26} {sim[k] / n:5.2f} | {rl[k] / nr:5.2f}")
    print(f"  {'all':26} {sum(sim.values()) / n:5.2f} | {sum(rl.values()) / nr:5.2f}")
