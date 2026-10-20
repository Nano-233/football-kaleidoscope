"""The shape at the moment of a pass, real (Euro 2020 360 freeze frames, open-play passes, frames with 8+ outfield opponents in view)
against simulated matches (the frame at each open-play pass): the things that look wrong when watching.

  box        defending outfielders inside their own box while the ball is outside it, by the ball's distance from that goal
  straggler  the deepest outfield defender's distance behind the next deepest (ball in the defending half): a lone man playing them on
  offside    attackers beyond the second-last defender (and the ball) in the opponent half when the pass is played
  build-up   passer in his own third: team-mates within 15 m, of them free (no opponent within 3 m), and the nearest one

    python scripts/shape_probe.py [matches] [key=value ...]
"""
import collections, glob, json, pathlib, sys
from multiprocessing import Pool
import numpy as np
ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.camera import Camera  # noqa: E402
from matchsim import engine  # noqa: E402
from matchsim.teams import Tactics, make_team  # noqa: E402

KV = {k: float(v) for k, v in (a.split("=") for a in sys.argv[2:])}
CAM = bool(KV.pop("camera", 1))  # the sim seen through a real broadcast view (scripts/camera.py), with the same 8-in-view rule as the real side
L, W = 105., 68.


def tally(c, ball, mates, opps, opp_gk_x, since=None):
    """Attacking left to right: ball, the passer's outfield team-mates, the opponents' outfielders; opp_gk_x = their keeper's x (None if unseen)."""
    gd = float(np.hypot(L - ball[0], W / 2 - ball[1]))
    in_box = lambda p: p[0] > L - 16.5 and abs(p[1] - W / 2) < 20.16
    if not in_box(ball) and ball[0] > L / 2:
        b = "20-30" if gd < 30 else "30-40" if gd < 40 else "40+"
        c[("box", b, "n")] += 1
        c[("box", b, "def")] += sum(in_box(o) for o in opps)
        c[("box", b, "att")] += sum(in_box(m) for m in mates)
    if ball[0] > L / 2 and len(opps) >= 8:
        xs = np.sort([o[0] for o in opps])[::-1]  # deepest first (towards their goal at x = L)
        g = xs[0] - xs[1]
        c[("strag", "n")] += 1
        c[("strag", "gap")] += g
        c[("strag", ">3")] += g > 3
        c[("strag", ">6")] += g > 6
        line = max(min(xs[0], xs[1]) if opp_gk_x is None else sorted([opp_gk_x] + list(xs))[-2], ball[0], L / 2)
        off = [m for m in mates if m[0] > line + .3]
        c[("off", "n")] += 1
        c[("off", "count")] += len(off)
        c[("off", "any")] += bool(off)
    if ball[0] < L / 3:
        near = [m for m in mates if np.hypot(*(m - ball)) < 15]
        free = [m for m in near if not opps or min(np.hypot(*(o - m)) for o in opps) > 3]
        c[("bu", "n")] += 1
        c[("bu", "near")] += len(near)
        c[("bu", "free")] += len(free)
        c[("bu", "nearest")] += min((float(np.hypot(*(m - ball))) for m in mates), default=40.)
        if since is not None:
            b = "<4" if since < 4 else "4-10" if since < 10 else "10+"
            c[("bus", b, "n")] += 1
            c[("bus", b, "near")] += len(near)
            c[("bus", b, "nearest")] += min((float(np.hypot(*(m - ball))) for m in mates), default=40.)


def real():
    c = collections.Counter()
    for f in glob.glob(str(ROOT.parent / "reference_data" / "euro2020" / "[0-9]*_360.json")):
        ff = {r["event_uuid"]: r["freeze_frame"] for r in json.load(open(f, encoding="utf-8"))}
        p0 = {}
        for e in json.load(open(f.replace("_360", ""), encoding="utf-8")):
            ts = e["minute"] * 60 + e["second"]
            p0.setdefault(e["possession"], ts)
            if e["type"]["name"] != "Pass" or "type" in e["pass"] or e["id"] not in ff:
                continue
            P = lambda p: np.array(p["location"]) * [L / 120, W / 80]
            fr = ff[e["id"]]
            opps = [P(p) for p in fr if not p["teammate"] and not p["keeper"]]
            mates = [P(p) for p in fr if p["teammate"] and not p["keeper"] and not p.get("actor")]
            gk = [P(p)[0] for p in fr if not p["teammate"] and p["keeper"]]
            if len(opps) < 8:
                continue
            tally(c, np.array(e["location"]) * [L / 120, W / 80], mates, opps, gk[0] if gk else None, ts - p0[e["possession"]])
    return c


def one(seed):
    engine.TUNE.update(KV)
    rng = np.random.default_rng(seed)
    r = engine.Match(make_team("KES", "4-3-3", Tactics(), rng=rng), make_team("NBR", "4-4-2", Tactics(), rng=rng), seed=seed).run()
    F, dt = np.asarray(r["frames"]), r["meta"]["dt"]
    pl = r["meta"]["home"]["players"] + r["meta"]["away"]["players"]
    gk = [s for s in range(22) if pl[s]["role"] == "GK"]
    slot = {p["id"]: s for s, p in enumerate(pl)}
    c = collections.Counter()
    cam, crng = (Camera(), np.random.default_rng(seed)) if CAM else (None, None)
    since0 = [0., 0.]
    for e in r["events"]:
        if e["type"] == "possession_change":
            since0[e["team"]] = e["t"]
        if e["type"] != "pass" or e.get("source") not in (None, "open_play") or e["player"] not in slot:
            continue
        t, me = e["team"], slot[e["player"]]
        xy = F[min(int(round(e["t"] / dt)), len(F) - 1)][5:49].reshape(22, 2)
        if (xy[:, 1] < -5).any():
            continue
        fl = (lambda p: np.array([L - p[0], W - p[1]])) if t == 1 else (lambda p: np.asarray(p, float))
        mates = [fl(xy[s]) for s in range(11 * t, 11 * t + 11) if s != me and s not in gk]
        opps = [fl(xy[s]) for s in range(11 * (1 - t), 11 * (1 - t) + 11) if s not in gk]
        ogk = [fl(xy[s])[0] for s in gk if s // 11 != t]
        if cam is not None:
            poly = cam.view(fl(xy[me]), crng)
            mates, opps = [q for q in mates if cam.sees(poly, q)], [q for q in opps if cam.sees(poly, q)]
            ogk = [x for x, s in zip(ogk, [s for s in gk if s // 11 != t]) if cam.sees(poly, fl(xy[s]))]
            if len(opps) < 8:
                continue
        tally(c, fl(xy[me]), mates, opps, ogk[0] if ogk else None, e["t"] - since0[t])
    return c


if __name__ == "__main__":
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 12
    rc = real()
    with Pool(min(n, 12)) as p:
        sc = sum(p.map(one, range(900, 900 + n)), collections.Counter())
    q = lambda c, k, d: c[k] / max(c[d], 1)
    print("defending outfielders in their box, ball outside it (real | sim); attackers in the box alongside")
    for b in ("20-30", "30-40", "40+"):
        print(f"  ball {b} m out   defenders {q(rc, ('box', b, 'def'), ('box', b, 'n')):.2f} | {q(sc, ('box', b, 'def'), ('box', b, 'n')):.2f}   attackers {q(rc, ('box', b, 'att'), ('box', b, 'n')):.2f} | {q(sc, ('box', b, 'att'), ('box', b, 'n')):.2f}")
    print(f"deepest defender behind the next (ball in their half): mean {q(rc, ('strag', 'gap'), ('strag', 'n')):.1f} | {q(sc, ('strag', 'gap'), ('strag', 'n')):.1f} m, "
          f"> 3 m {q(rc, ('strag', '>3'), ('strag', 'n')):.2f} | {q(sc, ('strag', '>3'), ('strag', 'n')):.2f}, > 6 m {q(rc, ('strag', '>6'), ('strag', 'n')):.2f} | {q(sc, ('strag', '>6'), ('strag', 'n')):.2f}")
    print(f"attackers offside at the pass (ball in their half): per pass {q(rc, ('off', 'count'), ('off', 'n')):.2f} | {q(sc, ('off', 'count'), ('off', 'n')):.2f}, "
          f"any {q(rc, ('off', 'any'), ('off', 'n')):.2f} | {q(sc, ('off', 'any'), ('off', 'n')):.2f}")
    print(f"build-up (passer in own third): team-mates within 15 m {q(rc, ('bu', 'near'), ('bu', 'n')):.2f} | {q(sc, ('bu', 'near'), ('bu', 'n')):.2f}, "
          f"of them free {q(rc, ('bu', 'free'), ('bu', 'n')):.2f} | {q(sc, ('bu', 'free'), ('bu', 'n')):.2f}, nearest {q(rc, ('bu', 'nearest'), ('bu', 'n')):.1f} | {q(sc, ('bu', 'nearest'), ('bu', 'n')):.1f} m")
    for b in ("<4", "4-10", "10+"):
        print(f"   {b:5s} s into the possession: share {q(rc, ('bus', b, 'n'), ('bu', 'n')):.2f} | {q(sc, ('bus', b, 'n'), ('bu', 'n')):.2f}, within 15 m {q(rc, ('bus', b, 'near'), ('bus', b, 'n')):.2f} | {q(sc, ('bus', b, 'near'), ('bus', b, 'n')):.2f}, nearest {q(rc, ('bus', b, 'nearest'), ('bus', b, 'n')):.1f} | {q(sc, ('bus', b, 'nearest'), ('bus', b, 'n')):.1f} m")
    print(f"samples real | sim: box {sum(rc[k] for k in rc if k[0] == 'box' and k[2] == 'n')} | {sum(sc[k] for k in sc if k[0] == 'box' and k[2] == 'n')}, strag {rc['strag', 'n']} | {sc['strag', 'n']}, build-up {rc['bu', 'n']} | {sc['bu', 'n']}")
