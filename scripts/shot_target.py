"""Where unblocked shots end, by distance to goal: off target, saved, scored; real (Euro 2020, open play, feet) against simulated
matches. The sim decides a shot's outcome with a hand-made off-target share; this checks it against real shooting.

    python scripts/shot_target.py [matches] [key=value ...]
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
BANDS = [(0, 12), (12, 18), (18, 25), (25, 99)]
band = lambda d: next(i for i, (a, b) in enumerate(BANDS) if a <= d < b)


def real():
    c, n = collections.Counter(), 0
    for f in glob.glob(str(ROOT.parent / "reference_data" / "euro2020" / "[0-9]*.json")):
        if "_360" in f:
            continue
        n += 1
        for e in json.load(open(f, encoding="utf-8")):
            if e["type"]["name"] != "Shot" or e["shot"]["type"]["name"] != "Open Play" or e["shot"].get("body_part", {}).get("name") == "Head":
                continue
            o = e["shot"]["outcome"]["name"]
            if o == "Blocked":
                continue
            k = "goal" if o == "Goal" else "saved" if o.startswith("Saved") else "off"
            x, y = e["location"][0] * L / 120, e["location"][1] * W / 80
            b = band(np.hypot(L - x, W / 2 - y))
            c[(b, k)] += 1
            c[(b, "xg")] += e["shot"].get("statsbomb_xg", 0)
    return c, n


def one(seed):
    TUNE.update(KV)
    rng = np.random.default_rng(seed)
    h = make_team("KES", "4-3-3", Tactics(press=.6, line_height=.6, directness=.45), rng=rng)
    a = make_team("NBR", "4-4-2", Tactics(press=.4, line_height=.4, directness=.55, marking=.5), rng=rng)
    c = collections.Counter()
    for e in Match(h, a, seed=seed).run()["events"]:
        if e["type"] != "shot" or e.get("kind") != "shot" or e.get("source") not in (None, "open_play") or e.get("body_part") == "head" or e["outcome"] == "blocked":
            continue
        k = "goal" if e["outcome"] == "goal" else "saved" if e["outcome"] == "saved" else "off"
        x, y = (L - e["x"], W - e["y"]) if e["team"] == 1 else (e["x"], e["y"])
        c[(band(np.hypot(L - x, W / 2 - y)), k)] += 1
        c[(band(np.hypot(L - x, W / 2 - y)), "xg")] += e.get("xg", 0)
    return c


if __name__ == "__main__":
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 16
    with Pool(12) as p:
        sim = sum(p.map(one, range(9700, 9700 + n)), collections.Counter())
    rl, nr = real()
    print("unblocked open-play shots with the feet, by distance:   per team-match sim | real    off target    saved    scored   (sim | real)")
    for i, (a, b) in enumerate(BANDS):
        S = sum(sim[(i, k)] for k in ("off", "saved", "goal")); R = sum(rl[(i, k)] for k in ("off", "saved", "goal"))
        print(f"  {a:2}-{b:2} m   {S / n / 2:5.2f} | {R / nr / 2:5.2f}      " + "   ".join(f"{sim[(i, k)] / max(S, 1):.2f} | {rl[(i, k)] / max(R, 1):.2f}" for k in ("off", "saved", "goal")) + f"    mean xG {sim[(i, 'xg')] / max(S, 1):.2f} | {rl[(i, 'xg')] / max(R, 1):.2f}")
