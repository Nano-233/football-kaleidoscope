"""After a ball in the air is contested in open play (an aerial duel), who has it next: the side that won the header, the side that lost
it, or out of play; real (Euro 2020: aerial duels, the next on-ball event within 6 s) against simulated matches. A real header is
rarely a clean possession: the ball goes loose and the second ball is fought for.

    python scripts/second_ball.py [matches] [key=value ...]
"""
import sys, json, glob, pathlib, collections
import numpy as np
from multiprocessing import Pool
ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from matchsim.engine import Match, TUNE
from matchsim.teams import make_team, Tactics

KV = {k: float(v) for k, v in (a.split("=") for a in sys.argv[2:])}
REAL_ON = ("Pass", "Carry", "Shot", "Ball Recovery", "Interception", "Clearance", "Dribble", "Miscontrol", "Dispossessed", "Goal Keeper")
SIM_ON = ("pass", "cross", "carry", "shot", "ball_recovery", "interception", "clearance", "take_on", "miscontrol", "dispossessed", "keeper_claim", "fifty_fifty", "ball_receipt")


def ts(e):
    h, m, s = e["timestamp"].split(":")
    return int(h) * 3600 + int(m) * 60 + float(s)


def real():
    c = collections.Counter()
    for f in glob.glob(str(ROOT.parent / "reference_data" / "euro2020" / "[0-9]*.json")):
        if "_360" in f:
            continue
        ev = json.load(open(f, encoding="utf-8"))
        for i, e in enumerate(ev):
            if e["type"]["name"] != "Duel" or e["duel"].get("type", {}).get("name") != "Aerial Lost":
                continue
            loser = e["team"]["id"]
            header = next((b for b in ev[max(0, i - 3):i + 4] if b is not e and b.get("team", {}).get("id") not in (None, loser) and b["type"]["name"] in ("Pass", "Clearance", "Shot", "Miscontrol", "Ball Recovery", "Interception")), None)  # the winner's own header
            for b in ev[i + 1:i + 12]:
                if b is header or b["type"]["name"] in ("Duel", "Pressure"):
                    continue
                if b["period"] != e["period"] or ts(b) - ts(e) > 6:
                    c["unclear"] += 1; break
                if b["type"]["name"] == "Pass" and b["pass"].get("type", {}).get("name") in ("Throw-in", "Goal Kick", "Corner", "Free Kick"):
                    c["out of play / stoppage"] += 1; break
                if b["type"]["name"] in REAL_ON + ("Ball Receipt*",) and b.get("team"):
                    if b["type"]["name"] == "Goal Keeper" and b["goalkeeper"].get("type", {}).get("name") in ("Shot Faced", "Shot Saved"):
                        continue
                    c["the header's winners" if b["team"]["id"] != loser else "the header's losers"] += 1; break
    return c


def one(seed):
    TUNE.update(KV)
    rng = np.random.default_rng(seed)
    h = make_team("KES", "4-3-3", Tactics(press=.6, line_height=.6, directness=.45), rng=rng)
    a = make_team("NBR", "4-4-2", Tactics(press=.4, line_height=.4, directness=.55, marking=.5), rng=rng)
    ev = Match(h, a, seed=seed).run()["events"]
    c = collections.Counter()
    for i, e in enumerate(ev):
        if e["type"] != "duel" or e.get("duel") != "aerial" or e.get("outcome") != "lost":
            continue
        loser = e["team"]
        for b in ev[i + 1:i + 30]:
            if b["type"] in ("duel", "pressure", "clearance") and b["t"] - e["t"] < .3:  # the header itself (or the other half of the duel)
                continue
            if b["t"] - e["t"] > 6:
                c["unclear"] += 1; break
            if b["type"] in ("throw_in", "goal_kick", "corner", "free_kick"):
                c["out of play / stoppage"] += 1; break
            if b["type"] in SIM_ON and b["type"] not in ("pass", "cross") and b.get("team") in (0, 1) and b.get("player"):
                c["the header's winners" if b["team"] != loser else "the header's losers"] += 1; break
    return c


if __name__ == "__main__":
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 16
    with Pool(12) as p:
        sim = sum(p.map(one, range(9700, 9700 + n)), collections.Counter())
    rl = real()
    S, R = sum(sim.values()), sum(rl.values())
    print(f"after an aerial duel in open play ({S / n:.1f} sim, {R / 31:.1f} real per match), who has the ball next:   sim | real")
    for k in ("the header's winners", "the header's losers", "out of play / stoppage", "unclear"):
        print(f"  {k:24} {sim[k] / S:.2f} | {rl[k] / R:.2f}")
