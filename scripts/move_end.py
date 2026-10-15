"""How a passing move ends (moves with at least one pass, so a touch the other side loses at once is not a move on either side), per
team-match, real (Euro 2020: the possession team's last event in each StatsBomb possession) against simulated matches (the team's last
event before the other side's next pass or a restart). The sim has 145 moves a team-match against 80, of 4.1 passes against 6.4.

    python scripts/move_end.py [matches] [key=value ...]
"""
import sys, json, glob, pathlib, collections
import numpy as np
from multiprocessing import Pool
ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from matchsim.engine import Match, TUNE
from matchsim.teams import make_team, Tactics

KV = {k: float(v) for k, v in (a.split("=") for a in sys.argv[2:])}
KINDS = ("shot", "out of play", "pass cut out / misplaced", "aerial duel lost", "tackled / dispossessed", "miscontrol", "foul / offside", "other")


def real():
    c, n = collections.Counter(), 0
    for f in glob.glob(str(ROOT.parent / "reference_data" / "euro2020" / "[0-9]*.json")):
        if "_360" in f:
            continue
        n += 1
        ev = json.load(open(f, encoding="utf-8"))
        per = collections.defaultdict(list)
        for e in ev:
            per[(e["period"], e["possession"])].append(e)
        keys = list(per)
        for k_i, key in enumerate(keys):
            ps = per[key]
            tm = ps[0].get("possession_team", {}).get("id")
            mine = [e for e in ps if e.get("team", {}).get("id") == tm and e["type"]["name"] not in ("Pressure", "Ball Receipt*", "Carry")]
            if not any(e["type"]["name"] == "Pass" for e in mine):
                continue
            last = mine[-1]
            t = last["type"]["name"]
            nxt = per[keys[k_i + 1]][0] if k_i + 1 < len(keys) else None
            restart = nxt is not None and nxt.get("play_pattern", {}).get("name") in ("From Throw In", "From Goal Kick", "From Corner", "From Free Kick", "From Kick Off")
            if t == "Shot":
                k = "shot"
            elif t == "Foul Won" or t == "Offside" or (nxt is not None and nxt.get("play_pattern", {}).get("name") == "From Free Kick"):
                k = "foul / offside"
            elif restart:
                k = "out of play"
            elif t == "Pass":
                k = "aerial duel lost" if last["pass"].get("outcome", {}).get("name") == "Incomplete" and last["pass"].get("height", {}).get("name") == "High Pass" else "pass cut out / misplaced"
            elif t in ("Dispossessed", "Dribble", "Duel"):
                k = "tackled / dispossessed"
            elif t in ("Miscontrol", "Error"):
                k = "miscontrol"
            else:
                k = "other"
            c[k] += 1
    return c, n


def one(seed):
    TUNE.update(KV)
    rng = np.random.default_rng(seed)
    h = make_team("KES", "4-3-3", Tactics(press=.6, line_height=.6, directness=.45), rng=rng)
    a = make_team("NBR", "4-4-2", Tactics(press=.4, line_height=.4, directness=.55, marking=.5), rng=rng)
    ev = Match(h, a, seed=seed).run()["events"]
    c, cur, npass = collections.Counter(), None, 0
    def close(team, nxt):
        if team is None or npass == 0:
            return
        mine = [e for e in reversed(ev[max(0, i - 25):i]) if e.get("team") == team and e.get("player") and e["type"] not in ("pressure", "ball_receipt", "carry")]
        last = mine[0] if mine else None
        if nxt in ("throw_in", "goal_kick", "corner", "kickoff"):
            k = "shot" if last and last["type"] == "shot" else "out of play"
        elif nxt == "free_kick" or (last and last["type"] in ("offside", "foul_won")):
            k = "foul / offside"
        elif last is None:
            k = "other"
        elif last["type"] == "shot":
            k = "shot"
        elif last["type"] in ("pass", "cross"):
            k = "aerial duel lost" if last.get("lofted") and last.get("outcome") != "complete" else "pass cut out / misplaced"
        elif last["type"] in ("dispossessed", "take_on", "duel"):
            k = "aerial duel lost" if last["type"] == "duel" else "tackled / dispossessed"
        elif last["type"] in ("miscontrol", "error"):
            k = "miscontrol"
        else:
            k = "other"
        c[k] += 1
    for i, e in enumerate(ev):
        ty = e["type"]
        if ty in ("throw_in", "goal_kick", "corner", "free_kick", "kickoff"):
            close(cur, ty); cur, npass = None, 0
            continue
        if ty in ("pass", "cross") and e.get("team") in (0, 1):
            if cur is not None and e["team"] != cur:
                close(cur, None); npass = 0
            cur = e["team"]; npass += 1
    return c


if __name__ == "__main__":
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 16
    with Pool(12) as p:
        sim = sum(p.map(one, range(9700, 9700 + n)), collections.Counter())
    rl, nr = real()
    print(f"passing moves per team-match by how they end:   sim | real   (all {sum(sim.values()) / n / 2:.1f} | {sum(rl.values()) / nr / 2:.1f})")
    for k in KINDS:
        print(f"  {k:26} {sim[k] / n / 2:5.1f} | {rl[k] / nr / 2:5.1f}")
