"""The side on the ball in its own box: how many outfield team-mates are in the box too, and how far up the nearest options stand,
real (Euro 2020 360 frames, events by outfield players in their own box) against simulated matches (frames with the ball held
there). The sim passes the ball around its own box about 5x as often as real teams (box_play.py).

    python scripts/box_shape.py [matches] [key=value ...]
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


def tally(c, mates, opp):
    c["n"] += 1
    c[("mates in box", min(sum(in_box(*m) for m in mates), 4))] += 1
    c["opp in box"] += sum(in_box(*o) for o in opp)
    c["mates beyond 30 m"] += sum(m[0] > 30 for m in mates)


def real():
    c = collections.Counter()
    for f in glob.glob(str(ROOT.parent / "reference_data" / "euro2020" / "[0-9]*_360.json")):
        ff = {r["event_uuid"]: r["freeze_frame"] for r in json.load(open(f, encoding="utf-8"))}
        for e in json.load(open(f.replace("_360", ""), encoding="utf-8")):
            if e["id"] not in ff or "location" not in e or e["type"]["name"] not in ("Pass", "Carry", "Ball Receipt*") or "Goalkeeper" in e.get("position", {}).get("name", ""):
                continue
            x, y = e["location"][0] * L / 120, e["location"][1] * W / 80
            if not in_box(x, y) or e.get("play_pattern", {}).get("name") not in ("Regular Play", "From Counter", "From Keeper", "From Goal Kick", "From Throw In"):
                continue
            P = lambda p: np.array([p["location"][0] * L / 120, p["location"][1] * W / 80])
            tally(c, [P(p) for p in ff[e["id"]] if p["teammate"] and not p["actor"] and not p["keeper"]], [P(p) for p in ff[e["id"]] if not p["teammate"] and not p["keeper"]])
    return c


def one(seed):
    TUNE.update(KV)
    rng = np.random.default_rng(seed)
    h = make_team("KES", "4-3-3", Tactics(press=.6, line_height=.6, directness=.45), rng=rng)
    a = make_team("NBR", "4-4-2", Tactics(press=.4, line_height=.4, directness=.55, marking=.5), rng=rng)
    m = Match(h, a, seed=seed).run()
    F = np.asarray(m["frames"])
    won = np.zeros(len(F))  # time since the side on the ball last won it
    last, tw = -1, 0.
    for k, f in enumerate(F):
        if int(f[4]) != last:
            last, tw = int(f[4]), f[0]
        won[k] = f[0] - tw
    gk = {s for s in range(22) if (m["meta"]["home"]["players"] + m["meta"]["away"]["players"])[s]["role"] == "GK"}
    c = collections.Counter()
    for k, f in enumerate(F):
        if k % 5:
            continue
        car = int(f[3])
        if car < 0 or car in gk:
            continue
        t = car // 11
        fl = (lambda q: np.array(q)) if t == 0 else (lambda q: np.array([L - q[0], W - q[1]]))
        b = fl(f[1:3])
        if not in_box(*b):
            continue
        pos = lambda s: fl(f[5 + 2 * s:7 + 2 * s])
        own, opp = range(11 * t, 11 * t + 11), range(11 * (1 - t), 11 * (1 - t) + 11)
        mates = [pos(s) for s in own if s != car and s not in gk and f[6 + 2 * s] > -5]
        tally(c, mates, [pos(s) for s in opp if s not in gk and f[6 + 2 * s] > -5])
        ph = "first 3 s" if won[k] < 3 else "3-8 s" if won[k] < 8 else "after 8 s"
        c[("phase", ph)] += 1
        c[("phase 4+", ph)] += sum(in_box(*q) for q in mates) >= 4
    return c


if __name__ == "__main__":
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 16
    with Pool(12) as p:
        sim = sum(p.map(one, range(9700, 9700 + n)), collections.Counter())
    rl = real()
    print(f"ball with an outfield player in his own box ({sim['n']} sim frames, {rl['n']} real events):   sim | real")
    print("  team-mates also in the box: " + "  ".join(f"{k}{'+' if k == 4 else ''}: {sim[('mates in box', k)] / sim['n']:.2f}|{rl[('mates in box', k)] / rl['n']:.2f}" for k in range(5)))
    print("  by time since the side won the ball (share of frames, and share of those with 4+ team-mates in the box): " + "  ".join(f"{ph}: {sim[('phase', ph)] / sim['n']:.2f} / {sim[('phase 4+', ph)] / max(sim[('phase', ph)], 1):.2f}" for ph in ("first 3 s", "3-8 s", "after 8 s")))
    print(f"  opponents in the box {sim['opp in box'] / sim['n']:.2f} | {rl['opp in box'] / rl['n']:.2f}    team-mates beyond 30 m {sim['mates beyond 30 m'] / sim['n']:.2f} | {rl['mates beyond 30 m'] / rl['n']:.2f}")
