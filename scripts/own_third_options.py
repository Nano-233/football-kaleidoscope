"""Passes from the passer's own third (open play, outfield): the options he had and the one he chose, real (Euro 2020 360 frames)
against simulated matches. Real passers there play 66 % forward, the sim's 40 %; the target is a choice model fitted to real
passers (matchsim/policy.py), so the question is whether the forward options are there.

    python scripts/own_third_options.py [matches] [key=value ...]
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


def lane_open(a, b, opp, room=2.):
    d = b - a
    for q in opp:
        u = float(np.clip(((q - a) @ d) / max(d @ d, 1e-6), 0, 1))
        if np.hypot(*(q - (a + u * d))) < room:
            return False
    return True


def tally(c, C, mates, opp, end):
    c["n"] += 1
    ahead = [m for m in mates if m[0] > C[0] + 5 and np.hypot(*(m - C)) < 35]
    open_ = [m for m in ahead if min((np.hypot(*(o - m)) for o in opp), default=99) > 3 and lane_open(C, m, opp)]
    c[("ahead", min(len(ahead), 4))] += 1
    c[("open ahead", min(len(open_), 3))] += 1
    dx = end[0] - C[0]
    c[("chose", "forward" if dx > 5 else "back" if dx < -5 else "sideways")] += 1
    nd = min((np.hypot(*(o - C)) for o in opp), default=99)
    c[("pressed", nd < 3)] += 1
    nm = sorted(np.hypot(*(m - C)) for m in mates)
    for k, d in enumerate(nm[:2]):
        c[("mate", k, min(int(d / 5), 5))] += 1


def real():
    c = collections.Counter()
    for f in glob.glob(str(ROOT.parent / "reference_data" / "euro2020" / "[0-9]*_360.json")):
        ff = {r["event_uuid"]: r["freeze_frame"] for r in json.load(open(f, encoding="utf-8"))}
        for e in json.load(open(f.replace("_360", ""), encoding="utf-8")):
            if e["type"]["name"] != "Pass" or "type" in e["pass"] or e["id"] not in ff or "Goalkeeper" in e.get("position", {}).get("name", ""):
                continue
            C = np.array([e["location"][0] * L / 120, e["location"][1] * W / 80])
            if C[0] > L / 3:
                continue
            P = lambda p: np.array([p["location"][0] * L / 120, p["location"][1] * W / 80])
            fr = ff[e["id"]]
            tally(c, C, [P(p) for p in fr if p["teammate"] and not p["actor"] and not p["keeper"]], [P(p) for p in fr if not p["teammate"] and not p["keeper"]],
                  np.array([e["pass"]["end_location"][0] * L / 120, e["pass"]["end_location"][1] * W / 80]))
    return c


def one(seed):
    TUNE.update(KV)
    rng = np.random.default_rng(seed)
    h = make_team("KES", "4-3-3", Tactics(press=.6, line_height=.6, directness=.45), rng=rng)
    a = make_team("NBR", "4-4-2", Tactics(press=.4, line_height=.4, directness=.55, marking=.5), rng=rng)
    m = Match(h, a, seed=seed).run()
    F, dt = np.asarray(m["frames"]), m["meta"]["dt"]
    roster = m["meta"]["home"]["players"] + m["meta"]["away"]["players"]
    gk = {s for s in range(22) if roster[s]["role"] == "GK"}
    gk_ids = {p["id"] for s in ("home", "away") for p in m["meta"][s]["players"] + m["meta"][s]["bench"] if p["role"] == "GK"}
    ids = [p["id"] for p in roster]
    c = collections.Counter()
    for e in m["events"]:
        if e["type"] != "pass" or e.get("source") not in (None, "open_play") or e.get("player") in gk_ids or e.get("end_x") is None:
            continue
        flip = e["team"] == 1
        fl = (lambda x, y: np.array([L - x, W - y])) if flip else (lambda x, y: np.array([x, y]))
        C = fl(e["x"], e["y"])
        if C[0] > L / 3:
            continue
        f = F[min(int(round(e["t"] / dt)), len(F) - 1)]
        own, opp = (range(11, 22), range(11)) if flip else (range(11), range(11, 22))
        pos = lambda s: fl(*f[5 + 2 * s:7 + 2 * s])
        tally(c, C, [pos(s) for s in own if s not in gk and f[6 + 2 * s] > -5 and ids[s] != e["player"]], [pos(s) for s in opp if s not in gk and f[6 + 2 * s] > -5], fl(e["end_x"], e["end_y"]))
    return c


if __name__ == "__main__":
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 16
    with Pool(12) as p:
        sim = sum(p.map(one, range(9700, 9700 + n)), collections.Counter())
    rl = real()
    S, R = sim["n"], rl["n"]
    print(f"passes from the own third ({S / n / 2:.0f} per team-match sim; {R} real in the 360 sample):   sim | real")
    print("  chose:  " + "  ".join(f"{k}: {sim[('chose', k)] / S:.2f}|{rl[('chose', k)] / R:.2f}" for k in ("forward", "sideways", "back")))
    print("  team-mates 5-35 m ahead:  " + "  ".join(f"{k}{'+' if k == 4 else ''}: {sim[('ahead', k)] / S:.2f}|{rl[('ahead', k)] / R:.2f}" for k in range(5)))
    print("  of them open (3 m free, lane clear):  " + "  ".join(f"{k}{'+' if k == 3 else ''}: {sim[('open ahead', k)] / S:.2f}|{rl[('open ahead', k)] / R:.2f}" for k in range(4)))
    for k in range(2):
        print(f"  {'nearest' if k == 0 else 'second'} team-mate, by 5 m:  " + "  ".join(f"{b * 5}-{b * 5 + 5 if b < 5 else ''}: {sim[('mate', k, b)] / S:.2f}|{rl[('mate', k, b)] / R:.2f}" for b in range(6)))
    print(f"  passer with a man within 3 m: {sim[('pressed', True)] / S:.2f} | {rl[('pressed', True)] / R:.2f}")
