"""What a player on the ball near goal faces, and how often he shoots in each situation, real (Euro 2020 360 freeze frames) against
simulated matches. Splits shot_choice.py's bands by the defence: defenders inside the triangle from the ball to the posts (the shooting
lane; keepers aside) and the nearest outfield opponent. If the sim's shot share is right in each situation but its situations are
cleaner, the gap is the defending; if the shares differ in the same situation, it is the decision.

    python scripts/shot_lane.py [matches] [key=value ...]
"""
import sys, json, glob, pathlib, collections
import numpy as np
from multiprocessing import Pool
ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from matchsim.engine import Match, TUNE
from matchsim.teams import make_team, Tactics
from scripts.shot_choice import xg0, band, BANDS

KV = {k: float(v) for k, v in (a.split("=") for a in sys.argv[2:])}
RELEASE = bool(KV.pop("release", 0))  # release=1: only the action that ends a player's touch (pass, cross, shot), so a carry before a shot is not a second decision
L, W = 105., 68.
POSTS = np.array([[L, W / 2 - 3.66], [L, W / 2 + 3.66]])


def in_lane(b, q):
    """q inside the triangle ball b - post - post."""
    a, c = POSTS
    v0, v1, v2 = c - b, a - b, q - b
    d00, d01, d02, d11, d12 = v0 @ v0, v0 @ v1, v0 @ v2, v1 @ v1, v1 @ v2
    den = d00 * d11 - d01 * d01
    if abs(den) < 1e-9:
        return False
    u, v = (d11 * d02 - d01 * d12) / den, (d00 * d12 - d01 * d02) / den
    return u >= 0 and v >= 0 and u + v <= 1


def situation(b, opp):
    """(defenders in the lane: 0, 1, 2+; nearest outfield opponent: <2 m, 2-4 m, 4+ m)."""
    n = sum(in_lane(b, q) for q in opp)
    d = min((np.hypot(*(q - b)) for q in opp), default=99)
    return min(n, 2), 0 if d < 2 else 1 if d < 4 else 2


def real():
    c = collections.Counter()
    for f in glob.glob(str(ROOT.parent / "reference_data" / "euro2020" / "[0-9]*_360.json")):
        ff = {r["event_uuid"]: r["freeze_frame"] for r in json.load(open(f, encoding="utf-8"))}
        for e in json.load(open(f.replace("_360", ""), encoding="utf-8")):
            t = e["type"]["name"]
            if t not in (("Pass", "Shot") if RELEASE else ("Pass", "Carry", "Dribble", "Shot")) or e["id"] not in ff:
                continue
            if t == "Pass" and "type" in e["pass"] or t == "Shot" and e["shot"]["type"]["name"] != "Open Play" or t == "Shot" and e["shot"].get("body_part", {}).get("name") == "Head":
                continue
            x, y = e["location"][0] * L / 120, e["location"][1] * W / 80
            bd = band(xg0(x, y))
            if x < 70 or bd < 3:
                continue
            opp = [np.array([p["location"][0] * L / 120, p["location"][1] * W / 80]) for p in ff[e["id"]] if not p["teammate"] and not p["keeper"]]
            k = (bd, *situation(np.array([x, y]), opp))
            c[k + ("all",)] += 1
            c[k + ("shot",)] += t == "Shot"
    return c


def one(seed):
    TUNE.update(KV)
    rng = np.random.default_rng(seed)
    h = make_team("KES", "4-3-3", Tactics(press=.6, line_height=.6, directness=.45), rng=rng)
    a = make_team("NBR", "4-4-2", Tactics(press=.4, line_height=.4, directness=.55, marking=.5), rng=rng)
    m = Match(h, a, seed=seed).run()
    F, dt = np.asarray(m["frames"]), m["meta"]["dt"]
    gk = {s for s in range(22) if (m["meta"]["home"]["players"] + m["meta"]["away"]["players"])[s]["role"] == "GK"}
    c = collections.Counter()
    for e in m["events"]:
        if e["type"] not in (("pass", "cross", "shot") if RELEASE else ("pass", "cross", "carry", "take_on", "shot")) or e.get("source") not in (None, "open_play") or e.get("body_part") in ("hands", "head"):
            continue
        if e["type"] == "shot" and e.get("kind") != "shot":
            continue
        flip = e["team"] == 1
        x, y = (L - e["x"], W - e["y"]) if flip else (e["x"], e["y"])
        bd = band(xg0(x, y))
        if x < 70 or bd < 3:
            continue
        f = F[min(int(round(e["t"] / dt)), len(F) - 1)]
        opp = []
        for s in (range(11) if flip else range(11, 22)):
            q = f[5 + 2 * s:7 + 2 * s]
            if q[1] > -5 and s not in gk:  # keepers aside; sent-off players are parked off the pitch
                opp.append(np.array([L - q[0], W - q[1]]) if flip else np.array(q))
        k = (bd, *situation(np.array([x, y]), opp))
        c[k + ("all",)] += 1
        c[k + ("shot",)] += e["type"] == "shot"
    return c


if __name__ == "__main__":
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 16
    with Pool(12) as p:
        sim = sum(p.map(one, range(9700, 9700 + n)), collections.Counter())
    rl = real()
    nr = len(glob.glob(str(ROOT.parent / "reference_data" / "euro2020" / "[0-9]*_360.json")))
    for bd in (3, 4, 5):
        a, b = BANDS[bd]
        S = sum(sim[k] for k in sim if k[0] == bd and k[3] == "all"); R = sum(rl[k] for k in rl if k[0] == bd and k[3] == "all")
        print(f"\nclean-look xG {a:.2f}-{b:.2f}: actions per team-match sim {S / n / 2:.1f} | real {R / nr / 2:.1f}")
        print("  in lane  nearest    share of actions sim | real    shot share sim | real   (n sim, real)")
        for lane in (0, 1, 2):
            for pr, pn in enumerate(("<2 m", "2-4 m", "4+ m")):
                s, r = sim[(bd, lane, pr, "all")], rl[(bd, lane, pr, "all")]
                if s + r == 0:
                    continue
                print(f"  {lane}{'+' if lane == 2 else ' '}       {pn:6}     {s / max(S, 1):.2f} | {r / max(R, 1):.2f}            "
                      f"{sim[(bd, lane, pr, 'shot')] / max(s, 1):.2f} | {rl[(bd, lane, pr, 'shot')] / max(r, 1):.2f}      ({s}, {r})")
