"""How big chances (open-play shots with xG >= 0.25 by the sim's own formula, freeze frames for the real side) are created, per
team-match, real (Euro 2020) against simulated matches: the ball that set the shooter up.

    cut-back      a pass from near the byline (within 8 m of it, inside the width of the box) played back towards the penalty spot
    cross         a pass from wide (more than 20 m off the middle)
    through ball  a pass played into space behind the defence (StatsBomb technique; the sim's through ball)
    other pass    any other pass
    no pass       a rebound, a loose ball, a dribble, a ball won

    python scripts/big_chance_source.py [matches] [key=value ...]
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
KINDS = ("cut-back", "cross", "through ball", "other pass", "no pass")


def how(ox, oy, through):
    if ox is None:
        return "no pass"
    if through:
        return "through ball"
    if ox > L - 8 and abs(oy - W / 2) < 20.16:
        return "cut-back"
    if abs(oy - W / 2) > 20:
        return "cross"
    return "other pass"


def real():
    c, files = collections.Counter(), 0
    for f in glob.glob(str(ROOT.parent / "reference_data" / "euro2020" / "[0-9]*_360.json")):
        files += 1
        ff = {r["event_uuid"]: r["freeze_frame"] for r in json.load(open(f, encoding="utf-8"))}
        ev = json.load(open(f.replace("_360", ""), encoding="utf-8"))
        by_id = {e["id"]: e for e in ev}
        for e in ev:
            if e["type"]["name"] != "Shot" or e["shot"]["type"]["name"] != "Open Play" or e["id"] not in ff:
                continue
            x, y = e["location"][0] * L / 120, e["location"][1] * W / 80
            opp = [np.array([p["location"][0] * L / 120, p["location"][1] * W / 80]) for p in ff[e["id"]] if not p["teammate"] and not p["keeper"]]
            xg = Match._xg(None, np.array([x, y]), opp, header=e["shot"].get("body_part", {}).get("name") == "Head")[0]
            c["shots"] += 1
            if xg < .25:
                continue
            kp = by_id.get(e["shot"].get("key_pass_id"))
            if kp is None:
                c[how(None, None, False)] += 1
                continue
            ox, oy = kp["location"][0] * L / 120, kp["location"][1] * W / 80
            c[how(ox, oy, kp["pass"].get("technique", {}).get("name") == "Through Ball" or kp["pass"].get("through_ball"))] += 1
    return c, files


def one(seed):
    TUNE.update(KV)
    rng = np.random.default_rng(seed)
    h = make_team("KES", "4-3-3", Tactics(press=.6, line_height=.6, directness=.45), rng=rng)
    a = make_team("NBR", "4-4-2", Tactics(press=.4, line_height=.4, directness=.55, marking=.5), rng=rng)
    ev = Match(h, a, seed=seed).run()["events"]
    by_id = {e["id"]: e for e in ev}
    c = collections.Counter()
    for i, e in enumerate(ev):
        if e["type"] != "shot" or e.get("kind") not in ("shot", "header") or e.get("source") not in (None, "open_play"):
            continue
        c["shots"] += 1
        if e.get("xg", 0) < .25:
            continue
        # the pass that set him up: the last completed pass to him in this move
        kp = next((b for b in reversed(ev[max(0, i - 40):i]) if b["type"] in ("pass", "cross") and b.get("team") == e["team"] and b.get("player") != e["player"] and e["t"] - b["t"] < 4), None)  # the last ball in by a team-mate (a header off a cross has no completed pass to him)
        if kp is None or any(b["type"] == "possession_change" for b in ev[ev.index(kp):i]):
            c["no pass"] += 1
            continue
        fl = (lambda x, y: (L - x, W - y)) if kp["team"] == 1 else (lambda x, y: (x, y))
        ox, oy = fl(kp["x"], kp["y"])
        c[how(ox, oy, kp.get("technique") == "through_ball")] += 1
    return c


if __name__ == "__main__":
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 16
    with Pool(12) as p:
        sim = sum(p.map(one, range(9700, 9700 + n)), collections.Counter())
    rl, files = real()
    print(f"big chances (open play, xG >= 0.25 by the sim's formula) per team-match:   sim | real   (all open-play shots {sim['shots'] / n / 2:.1f} | {rl['shots'] / files / 2:.1f})")
    for k in KINDS:
        print(f"  {k:13} {sim[k] / n / 2:5.2f} | {rl[k] / files / 2:5.2f}")
    print(f"  {'all':13} {sum(sim[k] for k in KINDS) / n / 2:5.2f} | {sum(rl[k] for k in KINDS) / files / 2:5.2f}")
