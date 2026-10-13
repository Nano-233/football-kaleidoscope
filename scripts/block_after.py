"""What follows a blocked shot, real (Euro 2020) against simulated matches: a corner, a throw-in or goal kick, the shooting side
keeps it (the rebound falls to them), or the defending side has it. The block's deflection is physics (Match._body_hit); this
says whether its outcomes look like real blocks.

    python scripts/block_after.py [matches] [key=value ...]
"""
import sys, json, glob, pathlib, collections
import numpy as np
from multiprocessing import Pool
ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from matchsim.engine import Match, TUNE
from matchsim.teams import make_team, Tactics

KV = {k: float(v) for k, v in (a.split("=") for a in sys.argv[2:])}
KINDS = ("corner", "out (throw / goal kick)", "shooting side keeps it", "defending side has it", "  of which the keeper collects")


def real():
    c = collections.Counter()
    for f in glob.glob(str(ROOT.parent / "reference_data" / "euro2020" / "[0-9]*.json")):
        if "_360" in f:
            continue
        ev = json.load(open(f, encoding="utf-8"))
        for i, e in enumerate(ev):
            if e["type"]["name"] != "Shot" or e["shot"]["outcome"]["name"] != "Blocked":
                continue
            tm = e["team"]["id"]
            for b in ev[i + 1:i + 12]:
                bt = b["type"]["name"]
                if bt == "Pass" and b["pass"].get("type", {}).get("name") == "Corner":
                    c["corner"] += 1; break
                if bt == "Pass" and b["pass"].get("type", {}).get("name") in ("Throw-in", "Goal Kick"):
                    c["out (throw / goal kick)"] += 1; break
                if bt in ("Pass", "Shot", "Carry", "Clearance", "Ball Recovery", "Dribble", "Interception", "Goal Keeper") and b.get("team", {}).get("id") and bt != "Block":
                    if bt == "Goal Keeper" and b["goalkeeper"].get("type", {}).get("name") in ("Shot Faced", "Shot Saved", "Goal Conceded", "Penalty Conceded"):
                        continue  # the keeper's record of the shot he faced, not a touch
                    c["shooting side keeps it" if b["team"]["id"] == tm else "defending side has it"] += 1
                    c["  of which the keeper collects"] += bt == "Goal Keeper"
                    break
    return c


def one(seed):
    TUNE.update(KV)
    rng = np.random.default_rng(seed)
    h = make_team("KES", "4-3-3", Tactics(press=.6, line_height=.6, directness=.45), rng=rng)
    a = make_team("NBR", "4-4-2", Tactics(press=.4, line_height=.4, directness=.55, marking=.5), rng=rng)
    ev, c = Match(h, a, seed=seed).run()["events"], collections.Counter()
    for i, e in enumerate(ev):
        if e["type"] != "shot" or e["outcome"] != "blocked":
            continue
        for b in ev[i + 1:i + 20]:
            bt = b["type"]
            if bt == "corner":
                c["corner"] += 1; break
            if bt in ("throw_in", "goal_kick"):
                c["out (throw / goal kick)"] += 1; break
            if bt in ("pass", "cross", "shot", "carry", "clearance", "ball_recovery", "interception", "keeper_claim", "take_on"):
                c["shooting side keeps it" if b.get("team") == e["team"] else "defending side has it"] += 1
                c["  of which the keeper collects"] += bt == "keeper_claim"
                break
    return c


if __name__ == "__main__":
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 16
    with Pool(12) as p:
        sim = sum(p.map(one, range(9700, 9700 + n)), collections.Counter())
    rl = real()
    S, R = sum(v for k, v in sim.items() if not k.startswith(" ")), sum(v for k, v in rl.items() if not k.startswith(" "))
    print(f"after a blocked shot ({S} sim, {R} real):   sim | real")
    for k in KINDS:
        print(f"  {k:26} {sim[k] / S:.2f} | {rl[k] / R:.2f}")
