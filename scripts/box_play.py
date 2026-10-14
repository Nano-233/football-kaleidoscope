"""What outfield players do with the ball in their own box in open play: pass or clear, how often the passes are lost and made under
pressure, real (Euro 2020) against simulated matches. Sim defenders clear less and keep their clearances more (clear_after.py).

    python scripts/box_play.py [matches] [key=value ...]
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


def one(seed):
    TUNE.update(KV)
    rng = np.random.default_rng(seed)
    h = make_team("KES", "4-3-3", Tactics(press=.6, line_height=.6, directness=.45), rng=rng)
    a = make_team("NBR", "4-4-2", Tactics(press=.4, line_height=.4, directness=.55, marking=.5), rng=rng)
    m = Match(h, a, seed=seed).run()
    role = {p["id"]: p["role"] for s in ("home", "away") for p in m["meta"][s]["players"] + m["meta"][s]["bench"]}
    c = collections.Counter()
    for e in m["events"]:
        if e["type"] not in ("pass", "clearance") or e.get("source") not in (None, "open_play") or role.get(e.get("player")) == "GK":
            continue
        x, y = (e["x"], e["y"]) if e["team"] == 0 else (L - e["x"], W - e["y"])
        if not in_box(x, y):
            continue
        c[e["type"]] += 1
        if e["type"] == "pass":
            c["pass lost"] += e.get("outcome") != "complete"
            c["pass pressed"] += bool(e.get("under_pressure"))
            ex, ey = (e["end_x"], e["end_y"]) if e["team"] == 0 else (L - e["end_x"], W - e["end_y"])
            rr = role.get(e.get("receiver"))
            c[("to", "the keeper" if rr == "GK" else "within the box" if in_box(ex, ey) else "out of the box")] += 1
            c[("by", role.get(e["player"]))] += 1
    return c


def real():
    c, n = collections.Counter(), 0
    for f in glob.glob(str(ROOT.parent / "reference_data" / "euro2020" / "[0-9]*.json")):
        if "_360" in f:
            continue
        n += 1
        for e in json.load(open(f, encoding="utf-8")):
            t = e["type"]["name"]
            if t not in ("Pass", "Clearance") or "Goalkeeper" in e.get("position", {}).get("name", "") or t == "Pass" and "type" in e["pass"]:
                continue
            x, y = e["location"][0] * L / 120, e["location"][1] * W / 80
            if not in_box(x, y):
                continue
            k = "pass" if t == "Pass" else "clearance"
            c[k] += 1
            if k == "pass":
                c["pass lost"] += "outcome" in e["pass"]
                c["pass pressed"] += bool(e.get("under_pressure"))
                ex, ey = e["pass"]["end_location"][0] * L / 120, e["pass"]["end_location"][1] * W / 80
                c[("to", "within the box" if in_box(ex, ey) else "out of the box")] += 1
                c[("by", e.get("position", {}).get("name", "?").split()[-1])] += 1
    return c, n


if __name__ == "__main__":
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 16
    with Pool(12) as p:
        sim = sum(p.map(one, range(9700, 9700 + n)), collections.Counter())
    rl, nr = real()
    print("outfield players in their own box, open play, per match:   sim | real")
    for k in ("pass", "clearance"):
        print(f"  {k:10} {sim[k] / n:5.1f} | {rl[k] / nr:5.1f}")
    for k in ("the keeper", "within the box", "out of the box"):
        print(f"  pass to {k:16} {sim[('to', k)] / n:5.1f} | {rl[('to', k)] / nr:5.1f}")
    print("  by role, sim: " + ", ".join(f"{r} {sim[('by', r)] / n:.1f}" for r in ("CB", "FB", "DM", "CM")) + "  | real: " + ", ".join(f"{k[1]} {v / nr:.1f}" for k, v in sorted(rl.items(), key=lambda kv: -kv[1]) if k[0] == "by")[:120])
    print(f"  passes lost {sim['pass lost'] / max(sim['pass'], 1):.2f} | {rl['pass lost'] / max(rl['pass'], 1):.2f}    made under pressure {sim['pass pressed'] / max(sim['pass'], 1):.2f} | {rl['pass pressed'] / max(rl['pass'], 1):.2f}")
