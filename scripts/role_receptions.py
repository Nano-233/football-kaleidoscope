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
WIDTH = KV.pop("tac_width", .5)  # both sides' width instruction (0-1), to tell tactics from logic
L, W = 105., 68.
REAL_ROLE = {"Center Back": "CB", "Right Center Back": "CB", "Left Center Back": "CB", "Right Back": "FB", "Left Back": "FB", "Right Wing Back": "FB",
             "Left Wing Back": "FB", "Center Defensive Midfield": "DM", "Right Defensive Midfield": "DM", "Left Defensive Midfield": "DM",
             "Right Center Midfield": "CM", "Left Center Midfield": "CM", "Center Midfield": "CM", "Right Midfield": "WG", "Left Midfield": "WG",
             "Center Attacking Midfield": "AM", "Right Attacking Midfield": "AM", "Left Attacking Midfield": "AM", "Right Wing": "WG", "Left Wing": "WG",
             "Center Forward": "ST", "Right Center Forward": "ST", "Left Center Forward": "ST", "Secondary Striker": "ST"}
ROLES = ("FB", "DM", "CM", "AM", "WG", "ST")


def real_passes():
    r = collections.defaultdict(list)
    for f in glob.glob(str(ROOT.parent / "reference_data" / "euro2020" / "[0-9]*.json")):
        if "_360" in f:
            continue
        ev = json.load(open(f, encoding="utf-8"))
        pos = {}
        for e in ev:
            if "player" in e and "position" in e:
                pos[e["player"]["id"]] = REAL_ROLE.get(e["position"]["name"])
        for e in ev:
            if e["type"]["name"] != "Pass" or "type" in e["pass"] or "outcome" in e["pass"] or "recipient" not in e["pass"]:
                continue
            ex = e["pass"]["end_location"][0] * L / 120
            if ex <= L / 2:
                continue
            ro = pos.get(e["pass"]["recipient"]["id"])
            r[ro].append((abs(e["location"][1] * W / 80 - W / 2), e["pass"]["length"] * L / 120, pos.get(e["player"]["id"])))
    return r


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
    h = make_team("KES", "4-3-3", Tactics(press=.6, line_height=.6, directness=.45, width=WIDTH), rng=rng)
    a = make_team("NBR", "4-4-2", Tactics(press=.4, line_height=.4, directness=.55, marking=.5, width=WIDTH), rng=rng)
    m = Match(h, a, seed=seed).run()
    role = {p["id"]: p["role"] for s in ("home", "away") for p in m["meta"][s]["players"] + m["meta"][s]["bench"]}
    r = collections.defaultdict(list)
    for e in m["events"]:
        if e["type"] == "pass" and e.get("outcome") == "complete" and e.get("receiver") and e.get("end_x") is not None:
            ex = e["end_x"] if e["team"] == 0 else L - e["end_x"]
            if ex > L / 2:
                r[("passed to", role.get(e["receiver"]))].append((abs(e["y"] - W / 2), e["length"], role.get(e["player"])))
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
    rp = real_passes()
    print("completed passes received in the opposition half: passer's median distance off the middle, median length, top passer roles   (sim | real)")
    for ro in ROLES:
        a_, b_ = sim.get(("passed to", ro), []), rp.get(ro, [])
        if not a_ or not b_:
            continue
        ca, cb = collections.Counter(x[2] for x in a_), collections.Counter(x[2] for x in b_)
        print(f"  to {ro}: off middle {np.median([x[0] for x in a_]):4.1f} | {np.median([x[0] for x in b_]):4.1f}   length {np.median([x[1] for x in a_]):4.1f} | {np.median([x[1] for x in b_]):4.1f}   from " + ", ".join(f"{k} {v / len(a_):.2f}" for k, v in ca.most_common(3)) + " | " + ", ".join(f"{k} {v / len(b_):.2f}" for k, v in cb.most_common(3)))
    print("receptions in the opposition half by role: per team-match, median metres from goal, median from the middle, share within 14 m of a touchline   (sim | real)")
    for ro in ROLES:
        s_, r_ = np.array(sim[ro]).reshape(-1, 2), np.array(rl[ro]).reshape(-1, 2)
        if not len(s_) or not len(r_):
            continue
        print(f"  {ro}  {len(s_) / n / 2:5.1f} | {len(r_) / 30 / 2:5.1f}   from goal {np.median(s_[:, 0]):4.1f} | {np.median(r_[:, 0]):4.1f}   from middle {np.median(s_[:, 1]):4.1f} | {np.median(r_[:, 1]):4.1f}   wide {np.mean(s_[:, 1] > W / 2 - 14):.2f} | {np.mean(r_[:, 1] > W / 2 - 14):.2f}")
