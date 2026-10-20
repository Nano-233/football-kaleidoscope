"""How real players use their feet (StatsBomb open data, Premier League 2015/16 in reference_data/pl1516): each player's preferred foot
(the one he plays most passes with), how often he uses the other, how much worse it is (pass completion; shot xG and goals), and what
makes him use it (the side the ball goes to, pressure, first time).

    python scripts/foot_norms.py
"""
import collections
import glob
import json
import pathlib

import numpy as np

ROOT = pathlib.Path(__file__).resolve().parents[2] / "reference_data" / "pl1516"
FEET = ("Left Foot", "Right Foot")

passes, shots = collections.defaultdict(list), collections.defaultdict(list)
for f in glob.glob(str(ROOT / "*.json")):
    data = json.load(open(f, encoding="utf-8"))
    for e in data if isinstance(data, list) else []:
        if not isinstance(e, dict) or "type" not in e or "player" not in e:
            continue
        ty = e["type"]["name"]
        if ty == "Pass" and e.get("pass", {}).get("body_part", {}).get("name") in FEET and "type" not in e.get("pass", {}):  # open play
            p = e["pass"]
            # side the ball goes to, relative to the passer's direction of play: + to his right (StatsBomb y grows to the attacking team's right)
            dy = p["end_location"][1] - e["location"][1]
            passes[e["player"]["id"]].append((p["body_part"]["name"], "outcome" not in p, dy, bool(e.get("under_pressure")), p.get("length", 0)))
        elif ty == "Shot" and e["shot"].get("body_part", {}).get("name") in FEET and e["shot"]["type"]["name"] == "Open Play":
            s = e["shot"]
            shots[e["player"]["id"]].append((s["body_part"]["name"], s.get("statsbomb_xg", 0), s["outcome"]["name"] == "Goal", bool(s.get("first_time"))))

rows = []
for pid, ps in passes.items():
    if len(ps) < 150:
        continue
    left = sum(1 for p in ps if p[0] == "Left Foot") / len(ps)
    pref = "Left Foot" if left > .5 else "Right Foot"
    weak = [p for p in ps if p[0] != pref]
    strong = [p for p in ps if p[0] == pref]
    rows.append(dict(pid=pid, pref=pref, weak_share=len(weak) / len(ps), acc_weak=np.mean([p[1] for p in weak]) if weak else np.nan,
                     acc_strong=np.mean([p[1] for p in strong]), weak=weak, strong=strong))
ws = np.array([r["weak_share"] for r in rows])
print(f"players with 150+ open-play foot passes: {len(rows)}; left-footed {sum(r['pref'] == 'Left Foot' for r in rows) / len(rows):.0%}")
print("weak-foot share of passes: median %.2f, quartiles %.2f-%.2f, 90th pct %.2f; two-footed (weak share >= 0.35): %.0f%%"
      % (np.median(ws), *np.percentile(ws, [25, 75]), np.percentile(ws, 90), 100 * (ws >= .35).mean()))
aw = np.nanmean([r["acc_weak"] for r in rows]); asg = np.mean([r["acc_strong"] for r in rows])
print(f"completion: strong foot {asg:.3f}, weak foot {aw:.3f}")
# what makes him use the weak foot: the ball going to his weak side, pressure
W = [p + (r["pref"],) for r in rows for p in r["weak"] + r["strong"]]
side = lambda p: (p[2] > 3) if p[5] == "Left Foot" else (p[2] < -3)  # to the weak foot's side (right for a left-footer), beyond 3 m
for name, sel in (("ball to his weak side", lambda p: side(p)), ("ball to his strong side or straight", lambda p: not side(p)),
                  ("under pressure", lambda p: p[3]), ("not under pressure", lambda p: not p[3])):
    g = [p for p in W if sel(p)]
    print(f"  {name:36s} weak-foot share {np.mean([p[0] != p[5] for p in g]):.2f}  (n {len(g)})")
# like for like: same length band and pressure, strong vs weak completion
for lo, hi in ((0, 15), (15, 30), (30, 80)):
    g = [p for p in W if lo <= p[4] < hi]
    a = [p[1] for p in g if p[0] != p[5]]; b = [p[1] for p in g if p[0] == p[5]]
    print(f"  passes {lo}-{hi} m: completion strong {np.mean(b):.3f}, weak {np.mean(a):.3f}")
pref = {r["pid"]: r["pref"] for r in rows}
S = [s + (pref[pid],) for pid, ss in shots.items() if pid in pref for s in ss]
sw = [s for s in S if s[0] != s[4]]; ss_ = [s for s in S if s[0] == s[4]]
print(f"open-play foot shots by these players: {len(S)}; weak foot {len(sw) / len(S):.2f} of them")
print(f"  goals per xG: strong {sum(s[2] for s in ss_) / sum(s[1] for s in ss_):.2f}, weak {sum(s[2] for s in sw) / sum(s[1] for s in sw):.2f}; mean xG strong {np.mean([s[1] for s in ss_]):.3f}, weak {np.mean([s[1] for s in sw]):.3f}")
print(f"  first time: weak-foot share {np.mean([s[0] != s[4] for s in S if s[3]]):.2f}, after a touch {np.mean([s[0] != s[4] for s in S if not s[3]]):.2f}")
