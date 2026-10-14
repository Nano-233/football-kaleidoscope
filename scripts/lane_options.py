"""For passes from 55-88 m (the build-up into the final third): the passer's options by lane, and the lane he chose, real (Euro 2020
360 frames) against simulated matches. An option is a team-mate 5-40 m away; open = no opponent within 3 m of him and none within
2 m of the straight path. Lanes are fifths of the width (wide = outer two, half-space = next two, centre = middle). Tests whether
the sim's defenders leave the middle open (so passers go there) or whether its passers choose the middle when it is shut.

    python scripts/lane_options.py [matches] [key=value ...]
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
LANES = ("wide", "half", "centre")


def lane(y):
    k = min(int(y / (W / 5)), 4)
    return "wide" if k in (0, 4) else "half" if k in (1, 3) else "centre"


def lane_open(a, b, opp, room=2.):
    d = b - a
    for q in opp:
        u = float(np.clip(((q - a) @ d) / max(d @ d, 1e-6), 0, 1))
        if np.hypot(*(q - (a + u * d))) < room:
            return False
    return True


def tally(c, C, mates, opp, end, rcv=None, tech=None):
    if rcv is not None:
        c[("receiver lane", lane(rcv[1]))] += 1
        c[("tech", tech, lane(end[1]))] += 1
    c["n"] += 1
    for m in mates:
        d = np.hypot(*(m - C))
        if not 5 < d < 40:
            continue
        ln = lane(m[1])
        c[("option", ln)] += 1
        c[("dist", ln)] += d
        c[("near", ln)] += d < 12
        if min((np.hypot(*(o - m)) for o in opp), default=99) > 3 and lane_open(C, m, opp):
            c[("open", ln)] += 1
    c[("chose", lane(end[1]))] += 1


def real():
    c = collections.Counter()
    for f in glob.glob(str(ROOT.parent / "reference_data" / "euro2020" / "[0-9]*_360.json")):
        ff = {r["event_uuid"]: r["freeze_frame"] for r in json.load(open(f, encoding="utf-8"))}
        for e in json.load(open(f.replace("_360", ""), encoding="utf-8")):
            if e["type"]["name"] != "Pass" or "type" in e["pass"] or e["id"] not in ff:
                continue
            C = np.array(e["location"]) * [L / 120, W / 80]
            if not 55 <= C[0] <= 88:
                continue
            P = lambda p: np.array(p["location"]) * [L / 120, W / 80]
            fr = ff[e["id"]]
            tally(c, C, [P(p) for p in fr if p["teammate"] and not p["actor"] and not p["keeper"]], [P(p) for p in fr if not p["teammate"] and not p["keeper"]],
                  np.array(e["pass"]["end_location"]) * [L / 120, W / 80])
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
    c = collections.Counter()
    for e in m["events"]:
        if e["type"] not in ("pass", "cross") or e.get("source") not in (None, "open_play") or e.get("end_x") is None or e["t"] > 2600:
            continue
        flip = e["team"] == 1
        fl = (lambda x, y: np.array([L - x, W - y])) if flip else (lambda x, y: np.array([x, y]))
        C = fl(e["x"], e["y"])
        if not 55 <= C[0] <= 88:
            continue
        f = F[min(int(round(e["t"] / dt)), len(F) - 1)]
        own, opp = (range(11, 22), range(11)) if flip else (range(11), range(11, 22))
        pos = lambda s: fl(*f[5 + 2 * s:7 + 2 * s])
        mates = [pos(s) for s in own if s not in gk and f[6 + 2 * s] > -5 and np.hypot(*(pos(s) - C)) > .5]
        ids = [p["id"] for p in roster]
        rp = pos(ids.index(e["receiver"])) if e.get("receiver") in ids else None
        tally(c, C, mates, [pos(s) for s in opp if s not in gk and f[6 + 2 * s] > -5], fl(e["end_x"], e["end_y"]), rp, e.get("technique") if e["type"] == "pass" else "cross")
    return c


if __name__ == "__main__":
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 16
    with Pool(12) as p:
        sim = sum(p.map(one, range(9700, 9700 + n)), collections.Counter())
    rl = real()
    S, R = sim["n"], rl["n"]
    print(f"passes from 55-88 m ({S} sim, {R} real):   sim | real")
    print("  team-mates per pass in each lane (5-40 m away):  " + "  ".join(f"{k}: {sim[('option', k)] / S:.2f}|{rl[('option', k)] / R:.2f}" for k in LANES))
    print("  mean distance from the passer:                   " + "  ".join(f"{k}: {sim[('dist', k)] / max(sim[('option', k)], 1):.1f}|{rl[('dist', k)] / max(rl[('option', k)], 1):.1f}" for k in LANES))
    print("  options within 12 m per pass:                    " + "  ".join(f"{k}: {sim[('near', k)] / S:.2f}|{rl[('near', k)] / R:.2f}" for k in LANES))
    print("  of them open:                                    " + "  ".join(f"{k}: {sim[('open', k)] / max(sim[('option', k)], 1):.2f}|{rl[('open', k)] / max(rl[('option', k)], 1):.2f}" for k in LANES))
    print("  open team-mates per pass:                        " + "  ".join(f"{k}: {sim[('open', k)] / S:.2f}|{rl[('open', k)] / R:.2f}" for k in LANES))
    T = sum(v for k, v in sim.items() if isinstance(k, tuple) and k[0] == "receiver lane")
    print("  sim: lane the intended receiver stood in:        " + "  ".join(f"{k}: {sim[('receiver lane', k)] / max(T, 1):.2f}" for k in LANES))
    for k, v in sorted(((k, v) for k, v in sim.items() if isinstance(k, tuple) and k[0] == "tech" and k[2] == "centre"), key=lambda kv: -kv[1])[:5]:
        print(f"  sim passes ending central by technique: {k[1]} {v / S:.3f}")
    print("  lane the pass went to:                           " + "  ".join(f"{k}: {sim[('chose', k)] / S:.2f}|{rl[('chose', k)] / R:.2f}" for k in LANES))
