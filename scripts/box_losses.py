"""Balls lost by the defending side near its own goal (where the opponent wins it at clean-look xG >= 0.12 for the attackers), and how,
real (Euro 2020) against simulated matches. The sim's attackers get 7x the real number of touches there with no pass to them
(zone_entry.py): this is where they come from.

    python scripts/box_losses.py [matches] [key=value ...]
"""
import sys, json, glob, pathlib, collections
import numpy as np
from multiprocessing import Pool
ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from matchsim.engine import Match, TUNE
from matchsim.teams import make_team, Tactics
from scripts.zone_entry import in_zone

KV = {k: float(v) for k, v in (a.split("=") for a in sys.argv[2:])}
L, W = 105., 68.


def real():
    """Possessions won in the zone: a loser's action right before (dispossessed, miscontrol, an intercepted or blocked pass, a
    clearance that went to them) and who made it."""
    c, n = collections.Counter(), 0
    for f in glob.glob(str(ROOT.parent / "reference_data" / "euro2020" / "[0-9]*.json")):
        if "_360" in f:
            continue
        n += 1
        ev = json.load(open(f, encoding="utf-8"))
        for i in range(1, len(ev)):
            a, b = ev[i - 1], ev[i]
            if b["possession"] == a["possession"] or b.get("team", {}).get("id") != b.get("possession_team", {}).get("id") or "location" not in b:
                continue
            x, y = b["location"][0] * L / 120, b["location"][1] * W / 80
            if not in_zone(x, y) or b.get("play_pattern", {}).get("name") not in ("Regular Play", "From Counter", "From Keeper", "From Goal Kick", "From Throw In"):
                continue
            pos = a.get("position", {}).get("name", "")
            who = "GK" if "Goalkeeper" in pos else "CB" if "Center Back" in pos else "FB" if "Back" in pos else "other"
            how = {"Pass": "pass", "Clearance": "clearance", "Dispossessed": "dispossessed", "Miscontrol": "miscontrol", "Duel": "tackled", "Carry": "carry"}.get(a["type"]["name"], a["type"]["name"].lower())
            if a.get("team", {}).get("id") == b.get("team", {}).get("id"):
                how = "other"
            c[(who, how)] += 1
    return c, n


def one(seed):
    TUNE.update(KV)
    rng = np.random.default_rng(seed)
    h = make_team("KES", "4-3-3", Tactics(press=.6, line_height=.6, directness=.45), rng=rng)
    a = make_team("NBR", "4-4-2", Tactics(press=.4, line_height=.4, directness=.55, marking=.5), rng=rng)
    m = Match(h, a, seed=seed).run()
    role = {p["id"]: p["role"] for s in ("home", "away") for p in m["meta"][s]["players"] + m["meta"][s]["bench"]}
    ev, c = m["events"], collections.Counter()
    for i, e in enumerate(ev):
        if e["type"] != "possession_change" or "x" not in e:
            continue
        x, y = (L - e["x"], W - e["y"]) if e["team"] == 1 else (e["x"], e["y"])
        if not in_zone(x, y):
            continue
        lost = next((b for b in reversed(ev[max(0, i - 6):i]) if b.get("team") == 1 - e["team"] and b.get("player") and b["type"] in ("pass", "cross", "clearance", "dispossessed", "miscontrol", "carry", "shot", "error")), None)
        if lost is None:
            c[("other", "other")] += 1
            continue
        r = role.get(lost["player"], "")
        who = "GK" if r == "GK" else "CB" if r == "CB" else "FB" if r in ("FB", "WB") else "other"
        how = {"pass": "pass", "cross": "pass", "clearance": "clearance", "dispossessed": "dispossessed", "miscontrol": "miscontrol", "error": "miscontrol"}.get(lost["type"], lost["type"])
        c[(who, how)] += 1
    return c


if __name__ == "__main__":
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 16
    with Pool(12) as p:
        sim = sum(p.map(one, range(9700, 9700 + n)), collections.Counter())
    rl, nr = real()
    print("possessions won at clean-look xG >= 0.12, per team-match, by the loser's last action:   sim | real")
    for k in sorted(set(sim) | set(rl), key=lambda k: -(sim[k] / n + rl[k] / nr)):
        print(f"  {k[0]:6} {k[1]:13} {sim[k] / n / 2:5.2f} | {rl[k] / nr / 2:5.2f}")
    print(f"  {'all':20} {sum(sim.values()) / n / 2:5.2f} | {sum(rl.values()) / nr / 2:5.2f}")
