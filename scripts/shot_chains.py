"""Print the event chain behind a sample of shots so a human can read whether the attack makes football sense.

    python scripts/shot_chains.py [seed] [count] [key=value ...]
"""
import sys, pathlib
import numpy as np
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import calibrate
from matchsim import engine

L, W = 105., 68.
args = [a for a in sys.argv[1:] if "=" not in a]
engine.TUNE.update({k: float(v) for k, v in (a.split("=") for a in sys.argv[1:] if "=" in a)})
seed = int(args[0]) if args else 900
count = int(args[1]) if len(args) > 1 else 12
m, r = calibrate.run(seed)
ev = r["events"]
short = {"pass": "P", "cross": "X", "carry": "C", "shot": "SHOT", "take_on": "TO", "tackle": "T", "interception": "INT", "ball_recovery": "REC", "possession_change": "CHG", "dispossessed": "DISP", "foul": "FOUL", "block": "BLK", "keeper_claim": "GKC", "offside": "OFF"}
role = {m.ids[i]: int(m.role[i]) for i in range(22)}
RN = "GK CB FB DM CM WG AM ST".split()
shown = 0
for k, e in enumerate(ev):
    if e["type"] != "shot" or e.get("kind") == "penalty":
        continue
    tm = e["team"]
    j = k
    while j > 0 and k - j < 14 and not (ev[j]["type"] in ("possession_change", "ball_recovery", "kickoff", "goal_kick", "throw_in", "corner", "free_kick") and ev[j].get("team", tm) == tm and j < k):
        j -= 1
    toks = []
    for q in ev[j:k + 1]:
        ty = q["type"]
        if ty not in short and ty not in ("goal_kick", "throw_in", "corner", "free_kick", "kickoff"):
            continue
        ax = q["x"] if tm == 0 else L - q["x"] if "x" in q else None
        pl = q.get("player", "")
        tag = f"{short.get(ty, ty)}[{RN[role[pl]] if pl in role else ''}{'/' + str(int(ax)) + ',' + str(int(q['y'])) if ax is not None else ''}]"
        if ty in ("pass", "cross"):
            tag += f"->{q.get('receiver', '')[-2:]}:{q.get('outcome', '')[:3]}:{q.get('technique', '')[:4]}"
        if ty == "take_on":
            tag += ":" + q["outcome"]
        if ty == "shot":
            tag += f" xg={q['xg']:.2f} {q['outcome']} press={q['under_pressure']}"
        toks.append(tag + ("" if ev[j].get("team", tm) == tm or ty != "x" else ""))
    print(f"t={e['t']:.0f}s team{tm}  " + " > ".join(toks))
    shown += 1
    if shown >= count:
        break
