"""Positional norms from StatsBomb 360 freeze frames (Euro 2020 sample), compared with the simulator's tracking.

    python scripts/norms360.py [N_matches] [sim_matches]

For every open-play pass by the team in possession with the ball in the opposition's final third, take the visible players
and measure shape: where the passer's team-mates stand laterally and in depth, how many defenders are goal-side of the
ball, how close the nearest defender is to the passer. The simulator is sampled at the same moments (every pass event), using
only players within 32 m of the ball as a stand-in for the camera's visible area. Aggregates only; raw data stays out of the repo.
"""
import sys, os, json, glob, pathlib, collections
import numpy as np
ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "scripts"))
L, W = 105., 68.
SB = np.array([105 / 120, 68 / 80])
VIS = 32.


def shape(ball, mates, opps, gk_opp=None):
    """Shape descriptors around the ball for the team in possession (all in metres, attacking +x, ball = passer)."""
    m = {}
    mates = np.array(mates).reshape(-1, 2)
    opps = np.array(opps).reshape(-1, 2)
    if len(mates):
        m["mate_abs_y"] = float(np.mean(np.abs(mates[:, 1] - W / 2)))
        m["mate_wide"] = float(np.mean(np.abs(mates[:, 1] - W / 2) > 20))
        m["mate_ahead"] = float(np.mean(mates[:, 0] > ball[0]))
        m["mate_in_box"] = float(np.sum((L - mates[:, 0] < 16.5) & (np.abs(mates[:, 1] - W / 2) < 20.16)))
    if len(opps):
        d = np.hypot(*(opps - ball).T)
        m["opp_nearest"] = float(d.min())
        m["opp_within5"] = float((d < 5).sum())
        m["opp_within10"] = float((d < 10).sum())
        m["opp_goalside"] = float((opps[:, 0] > ball[0]).sum())
        m["opp_in_box"] = float(np.sum((L - opps[:, 0] < 16.5) & (np.abs(opps[:, 1] - W / 2) < 20.16)))
        m["opp_central_frac"] = float(np.mean(np.abs(opps[:, 1] - W / 2) < 12))
        m["opp_width"] = float(np.ptp(opps[:, 1])) if len(opps) > 2 else np.nan
        m["opp_depth"] = float(np.ptp(opps[:, 0])) if len(opps) > 2 else np.nan
    if len(opps) >= 5:
        xs = np.sort(opps[:, 0])[::-1]
        back = xs[:4]  # the four deepest outfield defenders (largest x = closest to their own goal)
        m["line_gap"] = float(back.mean() - ball[0])  # how far the back line stands in front of (positive) or behind the ball
        m["line_flat"] = float(back.std())
        o = np.argsort(np.hypot(*(opps - ball).T))
        n1, n2 = opps[o[0]], opps[o[1]]
        wide = abs(ball[1] - W / 2) > 14
        if wide:
            m["press_inside"] = float((n1[1] - ball[1]) * np.sign(W / 2 - ball[1]))  # nearest defender stands on the inside (+) or outside (-) of a wide carrier
        m["press_goalside"] = float(n1[0] - ball[0])
        m["cover_dist"] = float(np.hypot(*(n2 - n1)))
        near8 = opps[o[:8]]
        m["block_width"] = float(near8[:, 1].std())
        m["block_depth"] = float(near8[:, 0].std())
        m["central_between"] = float(np.sum((opps[:, 0] > ball[0]) & (np.abs(opps[:, 1] - W / 2) < 12)))
    m["n_vis_mates"] = float(len(mates))
    m["n_vis_opps"] = float(len(opps))
    return m


def real(n_matches, zlo=2 * L / 3, zhi=L + 1):
    rows = []
    d = pathlib.Path(__file__).resolve().parents[2] / "reference_data" / "euro2020"
    files = [f for f in sorted(glob.glob(str(d / "[0-9]*.json"))) if "_360" not in f][:n_matches]
    for f in files:
        ev = json.load(open(f, encoding="utf-8"))
        fr = {x["event_uuid"]: x for x in json.load(open(f.replace(".json", "_360.json"), encoding="utf-8"))}
        for e in ev:
            if e["type"]["name"] != "Pass" or e["id"] not in fr or e["pass"].get("type", {}).get("name") in ("Corner", "Free Kick", "Throw-in", "Goal Kick", "Kick Off"):
                continue
            ball = np.array(e["location"][:2]) * SB
            if not (zlo <= ball[0] < zhi):
                continue
            ff = fr[e["id"]]["freeze_frame"]
            mates = [np.array(p["location"]) * SB for p in ff if p["teammate"] and not p["actor"] and not p.get("keeper")]
            opps = [np.array(p["location"]) * SB for p in ff if not p["teammate"] and not p.get("keeper")]
            rows.append(shape(ball, mates, opps))
    return rows


def sim(n_matches, zlo=2 * L / 3, zhi=L + 1):
    import calibrate
    rows = []
    for seed in range(700, 700 + n_matches):
        m, r = calibrate.run(seed)
        fr = np.array(r["frames"]); ft = fr[:, 0]
        for e in r["events"]:
            if e["type"] not in ("pass",) or e.get("source") in ("throw_in", "corner", "short_corner", "kickoff"):
                continue
            tm = e["team"]
            k = min(np.searchsorted(ft, e["t"]), len(fr) - 1)
            pos = fr[k, 5:49].reshape(22, 2)
            if pos[:, 1].min() < -5:
                continue
            P = pos if tm == 0 else np.array([L, W]) - pos
            ball = np.array([e["x"], e["y"]]) if tm == 0 else np.array([L - e["x"], W - e["y"]])
            if not (zlo <= ball[0] < zhi):
                continue
            mates = [P[11 * tm + i] for i in range(1, 11) if np.hypot(*(P[11 * tm + i] - ball)) > .5 and np.hypot(*(P[11 * tm + i] - ball)) < VIS]
            opps = [P[11 * (1 - tm) + i] for i in range(1, 11) if np.hypot(*(P[11 * (1 - tm) + i] - ball)) < VIS]
            rows.append(shape(ball, mates, opps))
    return rows


def agg(rows):
    keys = sorted({k for r in rows for k in r})
    return {k: float(np.nanmean([r.get(k, np.nan) for r in rows])) for k in keys}


if __name__ == "__main__":
    nr = int(sys.argv[1]) if len(sys.argv) > 1 else 20
    ns = int(sys.argv[2]) if len(sys.argv) > 2 else 8
    for name, lo, hi in (("middle third (ball 35-70 m)", L / 3, 2 * L / 3), ("final third (ball 70+ m)", 2 * L / 3, L + 1)):
        R, S = agg(real(nr, lo, hi)), agg(sim(ns, lo, hi))
        print(name)
        for k in sorted(R):
            print(f"  {k:18s} real {R[k]:7.2f}   sim {S.get(k, np.nan):7.2f}")
