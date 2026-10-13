"""How the ball reaches the good shooting spots (clean-look xG 0.12 or more, the bands where the sim has 2-3x the real number of
touches), real (Euro 2020) against simulated matches. For each pass or shot released there, how the player came to have the ball:

    carried   his own carry started outside the zone (he took it in)
    cross     received from a pass from wide (more than 20 m off the middle)
    cutback   received from a pass from the byline area (within 8 m of the goal line, not from wide)
    forward   received from a pass from in front of the box, outside the zone (through and forward balls, layoffs from deeper)
    inside    received from a pass played inside the zone (a short combination)
    loose     no pass to him (a rebound, a recovery, a clearance that fell to him)

    python scripts/zone_entry.py [matches] [key=value ...]
"""
import sys, json, glob, pathlib, collections
import numpy as np
from multiprocessing import Pool
ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from matchsim.engine import Match, TUNE
from matchsim.teams import make_team, Tactics
from scripts.shot_choice import xg0

KV = {k: float(v) for k, v in (a.split("=") for a in sys.argv[2:])}
L, W, ZONE = 105., 68., .12
KINDS = ("carried", "cross", "cutback", "forward", "inside")
LOOSE = ("block", "save", "clearance", "won it", "lost it", "other", None)  # what came before a loose ball: a block, a keeper's save, a clearance, he won it from the opponent, the opponent lost it
REAL_BEFORE = {"Block": "block", "Goal Keeper": "save", "Clearance": "clearance", "Interception": "won it", "Ball Recovery": "won it", "Duel": "won it",
               "Dispossessed": "lost it", "Miscontrol": "lost it", "Pass": "lost it", "Error": "lost it", "Shot": "block"}
SIM_BEFORE = {"block": "block", "save": "save", "keeper_claim": "save", "clearance": "clearance", "interception": "won it", "ball_recovery": "won it",
              "tackle": "won it", "fifty_fifty": "won it", "duel": "won it", "dispossessed": "lost it", "miscontrol": "lost it", "pass": "lost it", "cross": "lost it",
              "error": "lost it", "shot": "block"}


def in_zone(x, y):
    return x > 70 and xg0(x, y) >= ZONE


def kind(origin, recv, carry_from):
    """origin: where the pass to him was played from (None: no pass); recv: where he received; carry_from: where his carry started."""
    if carry_from is not None and not in_zone(*carry_from) and np.hypot(carry_from[0] - recv[0], carry_from[1] - recv[1]) < 1e-6 or \
            carry_from is not None and not in_zone(*carry_from):
        return "carried"
    if origin is None:
        return "loose"
    ox, oy = origin
    if abs(oy - W / 2) > 20:
        return "cross"
    if ox > L - 8:
        return "cutback"
    return "inside" if in_zone(ox, oy) else "forward"


def real():
    c, n = collections.Counter(), 0
    for f in glob.glob(str(ROOT.parent / "reference_data" / "euro2020" / "[0-9]*.json")):
        if "_360" in f:
            continue
        n += 1
        ev = json.load(open(f, encoding="utf-8"))
        P = lambda loc: (loc[0] * L / 120, loc[1] * W / 80)
        for i, e in enumerate(ev):
            t = e["type"]["name"]
            if t not in ("Pass", "Shot") or t == "Pass" and "type" in e["pass"] or t == "Shot" and (e["shot"]["type"]["name"] != "Open Play" or e["shot"].get("body_part", {}).get("name") == "Head"):
                continue
            if not in_zone(*P(e["location"])):
                continue
            pid, poss = e["player"]["id"], e["possession"]
            carry_from, origin, recv, before = None, None, P(e["location"]), None
            for b in reversed(ev[max(0, i - 12):i]):  # back to the pass that reached him, within the possession
                bt = b["type"]["name"]
                if before is None and (b.get("player", {}).get("id") != pid or REAL_BEFORE.get(bt) == "won it") and bt not in ("Pressure", "Ball Receipt*", "Carry"):
                    before = REAL_BEFORE.get(bt, "other")
                if b["possession"] != poss:
                    break
                if bt == "Carry" and b.get("player", {}).get("id") == pid:
                    carry_from = P(b["location"])
                    recv = carry_from
                elif bt == "Pass" and b["pass"].get("recipient", {}).get("id") == pid and "outcome" not in b["pass"]:
                    origin = P(b["location"])
                    break
                elif bt in ("Pass", "Shot", "Clearance", "Interception", "Ball Recovery", "Block") and b.get("player", {}).get("id") != pid:
                    break
            k = kind(origin, recv, carry_from)
            c[k if k != "loose" else f"loose: {before}"] += 1
    return c, n


def one(seed):
    TUNE.update(KV)
    rng = np.random.default_rng(seed)
    h = make_team("KES", "4-3-3", Tactics(press=.6, line_height=.6, directness=.45), rng=rng)
    a = make_team("NBR", "4-4-2", Tactics(press=.4, line_height=.4, directness=.55, marking=.5), rng=rng)
    ev = Match(h, a, seed=seed).run()["events"]
    c = collections.Counter()
    for i, e in enumerate(ev):
        if e["type"] not in ("pass", "cross", "shot") or e.get("source") not in (None, "open_play") or e.get("body_part") in ("hands", "head"):
            continue
        if e["type"] == "shot" and e.get("kind") != "shot":
            continue
        fl = (lambda x, y: (L - x, W - y)) if e["team"] == 1 else (lambda x, y: (x, y))
        if not in_zone(*fl(e["x"], e["y"])):
            continue
        pid = e["player"]
        carry_from, origin, recv, before = None, None, fl(e["x"], e["y"]), None
        cy = next((b for b in ev[i + 1:i + 4] if b["type"] == "carry" and b.get("player") == pid and abs(b["t"] - e["t"]) < .05), None)  # the sim logs his carry when the touch ends, after the pass or shot
        if cy:
            carry_from = recv = fl(cy["x"], cy["y"])
        for b in reversed([b for b in ev[max(0, i - 60):i] if b["type"] != "pressure"][-12:]):
            bt = b["type"]
            if before is None and b.get("player") and (b.get("player") != pid or SIM_BEFORE.get(bt) == "won it") and bt not in ("pressure", "ball_receipt", "carry"):
                before = SIM_BEFORE.get(bt, "other")
            if bt == "carry" and b.get("player") == pid:
                carry_from = fl(b["x"], b["y"])
                recv = carry_from
            elif bt == "ball_receipt" and b.get("player") == pid and b.get("passer"):  # who it came from (a pass meant for someone else counts too)
                src = next((q for q in reversed(ev[max(0, i - 60):ev.index(b)]) if q["type"] in ("pass", "cross") and q.get("player") == b["passer"]), None)
                if src:
                    origin = fl(src["x"], src["y"])
                    break
            elif bt == "kickoff" or bt == "possession_change" and b.get("team") != e["team"] or bt in ("pass", "cross", "shot", "clearance", "interception", "ball_recovery", "block") and b.get("player") != pid:
                break
        k = kind(origin, recv, carry_from)
        c[k if k != "loose" else f"loose: {before}"] += 1
    return c


if __name__ == "__main__":
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 16
    with Pool(12) as p:
        sim = sum(p.map(one, range(9700, 9700 + n)), collections.Counter())
    rl, nr = real()
    print(f"passes and shots released at clean-look xG >= {ZONE}, per team-match, by how he got the ball:   sim | real")
    for k in KINDS + tuple(f"loose: {b}" for b in LOOSE):
        print(f"  {k:18} {sim[k] / n / 2:5.2f} | {rl[k] / nr / 2:5.2f}")
    print(f"  {'all':8} {sum(sim.values()) / n / 2:5.2f} | {sum(rl.values()) / nr / 2:5.2f}")
