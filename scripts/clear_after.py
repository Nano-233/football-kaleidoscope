"""After a clearance from the defender's own box: who has the ball next and where, real (Euro 2020) against simulated matches.
Clearances that fall to an attacker near goal are one of the ways the sim's attackers win the ball in the good shooting spots.

    python scripts/clear_after.py [matches] [key=value ...]
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
in_box = lambda x, y: x < 16.5 and abs(y - W / 2) < 20.16
KINDS = ("out of play", "clearing side keeps it", "opponent, beyond 35 m from goal", "opponent, 20-35 m", "opponent, within 20 m")


def kind(own, x_def):
    """own: the clearing side has it next; x_def: where (distance from the clearing side's goal)."""
    if own:
        return "clearing side keeps it"
    return "opponent, beyond 35 m from goal" if x_def > 35 else "opponent, 20-35 m" if x_def > 20 else "opponent, within 20 m"


def real():
    c = collections.Counter()
    for f in glob.glob(str(ROOT.parent / "reference_data" / "euro2020" / "[0-9]*.json")):
        if "_360" in f:
            continue
        ev = json.load(open(f, encoding="utf-8"))
        for i, e in enumerate(ev):
            if e["type"]["name"] != "Clearance":
                continue
            x, y = e["location"][0] * L / 120, e["location"][1] * W / 80
            if not in_box(x, y):
                continue
            tm = e["team"]["id"]
            for b in ev[i + 1:i + 10]:
                bt = b["type"]["name"]
                if bt == "Pass" and b["pass"].get("type", {}).get("name") in ("Throw-in", "Corner", "Goal Kick", "Free Kick"):
                    c["out of play"] += 1; break
                if bt in ("Pass", "Ball Recovery", "Carry", "Shot", "Duel", "Interception", "Clearance", "Dribble", "Miscontrol", "Ball Receipt*") and "location" in b:
                    own = b["team"]["id"] == tm
                    bx = b["location"][0] * L / 120
                    c[kind(own, bx if own else L - bx)] += 1; break
    return c


def one(seed):
    TUNE.update(KV)
    rng = np.random.default_rng(seed)
    h = make_team("KES", "4-3-3", Tactics(press=.6, line_height=.6, directness=.45), rng=rng)
    a = make_team("NBR", "4-4-2", Tactics(press=.4, line_height=.4, directness=.55, marking=.5), rng=rng)
    ev, c = Match(h, a, seed=seed).run()["events"], collections.Counter()
    for i, e in enumerate(ev):
        if e["type"] != "clearance":
            continue
        tm = e["team"]
        fx = lambda x: x if tm == 0 else L - x  # distance from the clearing side's goal
        if not in_box(fx(e["x"]), e["y"] if tm == 0 else W - e["y"]):
            continue
        for b in ev[i + 1:i + 14]:
            bt = b["type"]
            if bt in ("throw_in", "corner", "goal_kick", "free_kick"):
                c["out of play"] += 1; break
            if bt in ("pass", "cross", "ball_recovery", "carry", "shot", "duel", "interception", "clearance", "fifty_fifty", "miscontrol", "ball_receipt", "keeper_claim", "take_on") and "x" in b:
                c[kind(b.get("team") == tm, fx(b["x"]))] += 1; break
    return c


if __name__ == "__main__":
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 16
    with Pool(12) as p:
        sim = sum(p.map(one, range(9700, 9700 + n)), collections.Counter())
    rl = real()
    S, R = sum(sim.values()), sum(rl.values())
    print(f"after a clearance from his own box ({S / n:.1f} sim, {R / 31:.1f} real per match):   sim | real")
    for k in KINDS:
        print(f"  {k:34} {sim[k] / S:.2f} | {rl[k] / R:.2f}")
