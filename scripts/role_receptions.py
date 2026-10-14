"""Where each role receives the ball when its side is attacking the final third (the reception beyond 52.5 m), real (Euro 2020
Ball Receipt events, by StatsBomb position) against simulated matches (ball_receipt events by role): median distance from goal and from
the middle, and share within 14 m of a touchline. For the attacking templates (matchsim/movement.py) the sim's roles stand in.

    python scripts/role_receptions.py [matches] [key=value ...]
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
REAL_ROLE = {"Center Back": "CB", "Right Center Back": "CB", "Left Center Back": "CB", "Right Back": "FB", "Left Back": "FB", "Right Wing Back": "FB",
             "Left Wing Back": "FB", "Center Defensive Midfield": "DM", "Right Defensive Midfield": "DM", "Left Defensive Midfield": "DM",
             "Right Center Midfield": "CM", "Left Center Midfield": "CM", "Center Midfield": "CM", "Right Midfield": "WG", "Left Midfield": "WG",
             "Center Attacking Midfield": "AM", "Right Attacking Midfield": "AM", "Left Attacking Midfield": "AM", "Right Wing": "WG", "Left Wing": "WG",
             "Center Forward": "ST", "Right Center Forward": "ST", "Left Center Forward": "ST", "Secondary Striker": "ST"}
ROLES = ("FB", "DM", "CM", "AM", "WG", "ST")


def real():
    r = collections.defaultdict(list)
    for f in glob.glob(str(ROOT.parent / "reference_data" / "euro2020" / "[0-9]*.json")):
        if "_360" in f:
            continue
        for e in json.load(open(f, encoding="utf-8")):
            if e["type"]["name"] != "Ball Receipt*" or "location" not in e or "ball_receipt" in e:
                continue
            ro = REAL_ROLE.get(e.get("position", {}).get("name"))
            x, y = e["location"][0] * L / 120, e["location"][1] * W / 80
            if ro and x > L / 2:
                r[ro].append((L - x, abs(y - W / 2)))
    return r


def one(seed):
    TUNE.update(KV)
    rng = np.random.default_rng(seed)
    h = make_team("KES", "4-3-3", Tactics(press=.6, line_height=.6, directness=.45), rng=rng)
    a = make_team("NBR", "4-4-2", Tactics(press=.4, line_height=.4, directness=.55, marking=.5), rng=rng)
    m = Match(h, a, seed=seed).run()
    role = {p["id"]: p["role"] for s in ("home", "away") for p in m["meta"][s]["players"] + m["meta"][s]["bench"]}
    r = collections.defaultdict(list)
    for e in m["events"]:
        if e["type"] != "ball_receipt" or "x" not in e:
            continue
        x, y = (e["x"], e["y"]) if e["team"] == 0 else (L - e["x"], W - e["y"])
        if x > L / 2:
            r[role.get(e["player"])].append((L - x, abs(y - W / 2)))
    return r


if __name__ == "__main__":
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 16
    with Pool(12) as p:
        parts = p.map(one, range(9700, 9700 + n))
    sim = collections.defaultdict(list)
    for r in parts:
        for k, v in r.items():
            sim[k] += v
    rl = real()
    print("receptions in the opposition half by role: per team-match, median metres from goal, median from the middle, share within 14 m of a touchline   (sim | real)")
    for ro in ROLES:
        s_, r_ = np.array(sim[ro]).reshape(-1, 2), np.array(rl[ro]).reshape(-1, 2)
        if not len(s_) or not len(r_):
            continue
        print(f"  {ro}  {len(s_) / n / 2:5.1f} | {len(r_) / 30 / 2:5.1f}   from goal {np.median(s_[:, 0]):4.1f} | {np.median(r_[:, 0]):4.1f}   from middle {np.median(s_[:, 1]):4.1f} | {np.median(r_[:, 1]):4.1f}   wide {np.mean(s_[:, 1] > W / 2 - 14):.2f} | {np.mean(r_[:, 1] > W / 2 - 14):.2f}")
