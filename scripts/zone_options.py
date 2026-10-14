"""Passers in front of the box (x 70-90 m, within 20 m of the middle): how many team-mates stand in the good shooting spots
(clean-look xG >= 0.12) and how free they are, real (Euro 2020 360 freeze frames) against simulated matches. The pass target is
chosen by a policy fitted to real passers (matchsim/policy.py), so if the sim plays into the zone more, the options must differ.

    python scripts/zone_options.py [matches] [key=value ...]
"""
import sys, json, glob, pathlib, collections
import numpy as np
from multiprocessing import Pool
ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from matchsim.engine import Match, TUNE
from matchsim.teams import make_team, Tactics
from scripts.zone_entry import in_zone

KV = {k: float(v) for k, v in (a.split("=") for a in sys.argv[2:])}
L, W = 105., 68.
HIST = (.75, 1., 1.5, 2., 3., 5., 1e9)


def front(x, y):
    return 70 < x < 90 and abs(y - W / 2) < 20 and not in_zone(x, y)


def tally(c, mates, opp):
    zm = [m for m in mates if in_zone(*m)]
    c["passes"] += 1
    c[("mates in zone", min(len(zm), 3))] += 1
    for m in zm:
        d = min((np.hypot(*(o - m)) for o in opp), default=99)
        c[("free", 0 if d < 1.5 else 1 if d < 3 else 2)] += 1
        c[("hist", next(i for i, e in enumerate(HIST) if d < e))] += 1
        if opp:
            o = min(opp, key=lambda o: np.hypot(*(o - m)))
            c[("side", "goal-side" if o[0] > m[0] + .3 else "level" if o[0] > m[0] - .3 else "behind him")] += 1
    c["opp in zone"] += sum(in_zone(*o) for o in opp)
    xs = sorted((o[0] for o in opp), reverse=True)
    if len(xs) >= 1:
        c["line"] += L - xs[0]  # the last outfield defender's distance from his goal line: the offside line (the keeper aside)
        c[("line bin", min(int((L - xs[0]) / 4), 6))] += 1


def real():
    c = collections.Counter()
    for f in glob.glob(str(ROOT.parent / "reference_data" / "euro2020" / "[0-9]*_360.json")):
        ff = {r["event_uuid"]: r["freeze_frame"] for r in json.load(open(f, encoding="utf-8"))}
        for e in json.load(open(f.replace("_360", ""), encoding="utf-8")):
            if e["type"]["name"] != "Pass" or "type" in e["pass"] or e["id"] not in ff:
                continue
            x, y = e["location"][0] * L / 120, e["location"][1] * W / 80
            if not front(x, y):
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
    F, dt = np.asarray(m["frames"]), m["meta"]["dt"]
    gk = {s for s in range(22) if (m["meta"]["home"]["players"] + m["meta"]["away"]["players"])[s]["role"] == "GK"}
    ids = [p["id"] for p in m["meta"]["home"]["players"] + m["meta"]["away"]["players"]]
    c = collections.Counter()
    for e in m["events"]:
        if e["type"] != "pass" or e.get("source") not in (None, "open_play"):
            continue
        flip = e["team"] == 1
        fl = (lambda x, y: np.array([L - x, W - y])) if flip else (lambda x, y: np.array([x, y]))
        if not front(*fl(e["x"], e["y"])):
            continue
        f = F[min(int(round(e["t"] / dt)), len(F) - 1)]
        own, opp = (range(11, 22), range(11)) if flip else (range(11), range(11, 22))
        pos = lambda s: fl(*f[5 + 2 * s:7 + 2 * s])
        tally(c, [pos(s) for s in own if s not in gk and f[6 + 2 * s] > -5 and ids[s] != e["player"]], [pos(s) for s in opp if s not in gk and f[6 + 2 * s] > -5])
    return c


if __name__ == "__main__":
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 16
    with Pool(12) as p:
        sim = sum(p.map(one, range(9700, 9700 + n)), collections.Counter())
    rl = real()
    print(f"passes from in front of the box: {sim['passes']} sim, {rl['passes']} real (360 sample)")
    for k in range(4):
        print(f"  team-mates in the zone {k}{'+' if k == 3 else ' '}   {sim[('mates in zone', k)] / sim['passes']:.2f} | {rl[('mates in zone', k)] / rl['passes']:.2f}")
    zs, zr = sum(sim[('mates in zone', k)] * k for k in range(4)), sum(rl[('mates in zone', k)] * k for k in range(4))
    for k, nm in enumerate(("<1.5 m", "1.5-3 m", "3+ m")):
        print(f"  a team-mate there with his nearest marker {nm:8} {sim[('free', k)] / max(zs, 1):.2f} | {rl[('free', k)] / max(zr, 1):.2f}")
    print("  nearest defender distance, share:  " + "  ".join(f"<{e if e < 1e8 else 'inf'}: {sim[('hist', i)] / max(zs, 1):.2f}|{rl[('hist', i)] / max(zr, 1):.2f}" for i, e in enumerate(HIST)))
    print("  where he stands:  " + "  ".join(f"{k}: {sim[('side', k)] / max(zs, 1):.2f}|{rl[('side', k)] / max(zr, 1):.2f}" for k in ("goal-side", "level", "behind him")))
    print(f"  last outfield defender from his goal line: mean {sim['line'] / sim['passes']:.1f} m | {rl['line'] / rl['passes']:.1f} m;  by 4 m: " + " ".join(f"{sim[('line bin', i)] / sim['passes']:.2f}|{rl[('line bin', i)] / rl['passes']:.2f}" for i in range(7)))
    print(f"  defenders in the zone (outfield)  {sim['opp in zone'] / sim['passes']:.2f} | {rl['opp in zone'] / rl['passes']:.2f}")
