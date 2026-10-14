"""How the side with the ball loses it in open play in the opponent's half, per team-match, real (Euro 2020: possessions that end
there and are followed by an open-play regain) against simulated matches. The sim's attacks break down about 3x as often.

    python scripts/attack_loss.py [matches] [key=value ...]
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


def real():
    c, n = collections.Counter(), 0
    for f in glob.glob(str(ROOT.parent / "reference_data" / "euro2020" / "[0-9]*.json")):
        if "_360" in f:
            continue
        n += 1
        ev = json.load(open(f, encoding="utf-8"))
        for a, b in zip(ev, ev[1:]):
            if b["possession"] == a["possession"] or b.get("play_pattern", {}).get("name") not in ("Regular Play", "From Counter"):
                continue
            last = next((q for q in reversed(ev[max(0, ev.index(a) - 6):ev.index(a) + 1]) if q.get("team", {}).get("id") == a.get("possession_team", {}).get("id") and "location" in q and q["type"]["name"] not in ("Pressure", "Ball Receipt*")), None)
            if last is None or last["location"][0] * L / 120 < L / 2:
                continue
            t = last["type"]["name"]
            if t == "Pass":
                t = "pass (cut out / misplaced)"
            elif t == "Ball Receipt*":
                t = "pass (cut out / misplaced)"
            c[t] += 1
    return c, n


def one(seed):
    TUNE.update(KV)
    rng = np.random.default_rng(seed)
    h = make_team("KES", "4-3-3", Tactics(press=.6, line_height=.6, directness=.45), rng=rng)
    a = make_team("NBR", "4-4-2", Tactics(press=.4, line_height=.4, directness=.55, marking=.5), rng=rng)
    ev = Match(h, a, seed=seed).run()["events"]
    c = collections.Counter()
    for i, e in enumerate(ev):
        if e["type"] != "possession_change" or "x" not in e:
            continue
        if any(b["type"] in ("throw_in", "goal_kick", "corner", "free_kick", "kickoff") for b in ev[max(0, i - 6):i]):
            continue
        last = next((b for b in reversed(ev[max(0, i - 10):i]) if b.get("team") == 1 - e["team"] and b.get("player") and b["type"] not in ("pressure", "ball_receipt", "carry")), None)
        if last is None or "x" not in last:
            continue
        x = last["x"] if last["team"] == 0 else L - last["x"]
        if x < L / 2:
            continue
        t = last["type"]
        if t in ("pass", "cross"):
            t = f"{t} ({last.get('outcome')})"
        c[t] += 1
    return c


if __name__ == "__main__":
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 16
    with Pool(12) as p:
        sim = sum(p.map(one, range(9700, 9700 + n)), collections.Counter())
    rl, nr = real()
    print("open-play losses in the opponent's half, per team-match, by the loser's last action:   sim | real")
    for k, v in sorted(sim.items(), key=lambda kv: -kv[1])[:12]:
        print(f"  sim  {k:32} {v / n / 2:5.1f}")
    for k, v in sorted(rl.items(), key=lambda kv: -kv[1])[:10]:
        print(f"  real {k:32} {v / nr / 2:5.1f}")
    print(f"  all  {sum(sim.values()) / n / 2:5.1f} | {sum(rl.values()) / nr / 2:5.1f}")
