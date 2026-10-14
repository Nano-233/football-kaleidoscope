"""The first action after a side wins the ball in open play in its own half (not from a restart), real (Euro 2020: the first on-ball
action of a possession that began with a regain) against simulated matches. How real players use the first seconds after winning it.

    python scripts/regain_first.py [matches] [key=value ...]
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
KINDS = ("short pass (<15 m)", "pass 15-30 m", "long pass (30 m+)", "carry 5 m+ then act", "clearance", "lost it at once")


def kind(t, length, carry_m):
    if t == "clearance":
        return "clearance"
    if t == "lost":
        return "lost it at once"
    if carry_m >= 5:
        return "carry 5 m+ then act"
    return "short pass (<15 m)" if length < 15 else "pass 15-30 m" if length < 30 else "long pass (30 m+)"


def real():
    c, n = collections.Counter(), 0
    for f in glob.glob(str(ROOT.parent / "reference_data" / "euro2020" / "[0-9]*.json")):
        if "_360" in f:
            continue
        n += 1
        ev = json.load(open(f, encoding="utf-8"))
        seen = set()
        for i, e in enumerate(ev):
            key = (e["period"], e["possession"])
            if key in seen or e.get("play_pattern", {}).get("name") not in ("Regular Play", "From Counter") or e.get("possession_team", {}).get("id") != e.get("team", {}).get("id"):
                continue
            if e["type"]["name"] not in ("Pass", "Carry", "Clearance", "Shot", "Dispossessed", "Miscontrol"):
                continue
            seen.add(key)
            x = e["location"][0] * L / 120
            if x > L / 2:
                continue
            carry_m = 0.
            act = e
            if e["type"]["name"] == "Carry":
                carry_m = np.hypot(*(np.array(e["carry"]["end_location"]) - e["location"])) * L / 120
                act = next((b for b in ev[i + 1:i + 6] if b["type"]["name"] in ("Pass", "Clearance", "Shot", "Dispossessed", "Miscontrol")), None)
                if act is None:
                    continue
            t = act["type"]["name"]
            k = kind("clearance" if t == "Clearance" else "lost" if t in ("Dispossessed", "Miscontrol") else "pass", act.get("pass", {}).get("length", 0) * L / 120, carry_m)
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
        if e["type"] != "possession_change" or "x" not in e:
            continue
        before = next((b for b in reversed(ev[max(0, i - 6):i]) if b["type"] in ("throw_in", "goal_kick", "corner", "free_kick", "kickoff")), None)
        if before is not None and e["t"] - before["t"] < 40:
            continue
        x = e["x"] if e["team"] == 0 else L - e["x"]
        if x > L / 2:
            continue
        carrier = None
        for b in ev[i + 1:i + 30]:
            if b["type"] == "possession_change":
                break
            if b.get("team") != e["team"] or b["type"] not in ("pass", "cross", "clearance", "carry", "dispossessed", "miscontrol", "shot"):
                continue
            if b["type"] == "carry":  # logged after the action that ended it, same time
                continue
            cy = next((q for q in ev[ev.index(b) + 1:ev.index(b) + 4] if q["type"] == "carry" and q.get("player") == b.get("player")), None)
            carry_m = float(cy.get("distance", 0)) if cy else 0.
            t = b["type"]
            c[kind("clearance" if t == "clearance" else "lost" if t in ("dispossessed", "miscontrol") else "pass", float(b.get("length", 0)), carry_m)] += 1
            break
    return c


if __name__ == "__main__":
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 16
    with Pool(12) as p:
        sim = sum(p.map(one, range(9700, 9700 + n)), collections.Counter())
    rl, nr = real()
    S, R = sum(sim.values()), sum(rl.values())
    print(f"first action after winning the ball in open play in the own half ({S / n / 2:.1f} sim, {R / nr / 2:.1f} real per team-match):   sim | real")
    for k in KINDS:
        print(f"  {k:24} {sim[k] / S:.2f} | {rl[k] / R:.2f}")
