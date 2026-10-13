"""Clearances by the defending side in its own box and the share that go behind for a corner, headed and kicked, real (Euro 2020)
against simulated matches. Corners from clearances are 0.8 a match in the sim against 2.5 real (corner_source.py).

    python scripts/clear_out.py [matches] [key=value ...]
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
in_box = lambda x, y: x < 16.5 and abs(y - W / 2) < 20.16  # own box, defender's frame


def real():
    c, n = collections.Counter(), 0
    for f in glob.glob(str(ROOT.parent / "reference_data" / "euro2020" / "[0-9]*.json")):
        if "_360" in f:
            continue
        n += 1
        ev = json.load(open(f, encoding="utf-8"))
        for i, e in enumerate(ev):
            if e["type"]["name"] != "Clearance":
                continue
            x, y = e["location"][0] * L / 120, e["location"][1] * W / 80
            if not in_box(x, y):
                continue
            hd = "head" if e["clearance"].get("body_part", {}).get("name") == "Head" else "foot"
            c[(hd, "n")] += 1
            nx = next((b for b in ev[i + 1:i + 8] if b["type"]["name"] == "Pass" and "type" in b["pass"]), None)
            if nx and nx["pass"]["type"]["name"] == "Corner" and nx["index"] - e["index"] < 8 and nx["team"]["id"] != e["team"]["id"]:
                c[(hd, "corner")] += 1
    return c, n


def one(seed):
    TUNE.update(KV)
    rng = np.random.default_rng(seed)
    h = make_team("KES", "4-3-3", Tactics(press=.6, line_height=.6, directness=.45), rng=rng)
    a = make_team("NBR", "4-4-2", Tactics(press=.4, line_height=.4, directness=.55, marking=.5), rng=rng)
    ev, c = Match(h, a, seed=seed).run()["events"], collections.Counter()
    for i, e in enumerate(ev):
        if e["type"] != "clearance":
            continue
        x, y = (e["x"], e["y"]) if e["team"] == 0 else (L - e["x"], W - e["y"])
        if not in_box(x, y):
            continue
        hd = "head" if e.get("body_part") == "head" else "foot"
        c[(hd, "n")] += 1
        nx = next((b for b in ev[i + 1:i + 12] if b["type"] in ("corner", "throw_in", "goal_kick", "pass", "carry", "shot", "clearance")), None)
        if nx and nx["type"] == "corner" and nx["team"] != e["team"]:
            c[(hd, "corner")] += 1
    return c


if __name__ == "__main__":
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 16
    with Pool(12) as p:
        sim = sum(p.map(one, range(9700, 9700 + n)), collections.Counter())
    rl, nr = real()
    print("clearances in his own box, per match: count sim | real, share going behind for a corner sim | real")
    for hd in ("head", "foot"):
        print(f"  {hd}  {sim[(hd, 'n')] / n:5.2f} | {rl[(hd, 'n')] / nr:5.2f}    {sim[(hd, 'corner')] / max(sim[(hd, 'n')], 1):.2f} | {rl[(hd, 'corner')] / max(rl[(hd, 'n')], 1):.2f}")
