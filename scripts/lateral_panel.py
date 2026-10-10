"""One panel for how the sim uses the width of the pitch, against real data, from ONE batch of simulated matches (so a change is judged on all of it at once).

    python scripts/lateral_panel.py [N=24] [key=value ...]     # N matches, TUNE overrides; prints sim | real for each line
    python scripts/lateral_panel.py real                        # rebuild the real-data cache (out/lateral_real.json)

Real references: StatsBomb Open Data, PL 2015/16 events (reference_data/pl1516) and Euro 2020 360 freeze frames (reference_data/euro2020), outside the repo.
Lanes are fifths of the width (13.6 m): wide = the two outer fifths, half-space = the next two, centre = the middle one. Run with the system python (the 360 part
needs matplotlib).
"""
import sys, os, json, glob, pathlib, collections
import numpy as np
ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "scripts"))
from multiprocessing import Pool
L, W = 105., 68.
SB = np.array([105 / 120, 68 / 80])
PL = sorted(glob.glob(str(ROOT.parent / "reference_data" / "pl1516" / "[0-9]*.json")))
EU = [f for f in sorted(glob.glob(str(ROOT.parent / "reference_data" / "euro2020" / "[0-9]*.json"))) if "_360" not in f]
CACHE = ROOT / "out" / "lateral_real.json"
SETP = ("Throw-in", "Corner", "Goal Kick", "Free Kick", "Kick Off")
GROUPS = ("FB", "W", "CM", "ST", "DM", "CB")
lane3 = lambda y: [0, 1, 2, 1, 0][min(int(y / 13.6), 4)]
tl = lambda y: min(y, W - y)


def grp_sb(n):
    n = n or ""
    if "Goalkeeper" in n: return "GK"
    if "Center Back" in n: return "CB"
    if "Back" in n: return "FB"
    if "Defensive Midfield" in n: return "DM"
    if "Attacking Midfield" in n: return "AM"
    if "Wing" in n or "Left Midfield" in n or "Right Midfield" in n: return "W"
    if "Midfield" in n: return "CM"
    if "Forward" in n or "Striker" in n: return "ST"
    return "other"


class Acc:
    """Sums for every line of the panel (so real and sim are computed the same way)."""
    def __init__(s):
        s.n = 0
        s.pass_lane = np.zeros((2, 3))          # pass starts by lane: all, final third (x>70)
        s.trans = np.zeros((3, 3))              # completed passes: start lane x end lane
        s.on = {g: [0, 0, 0] for g in GROUPS}   # on-ball events in the opposition half: n, within 14 m of a touchline, central (>24 m)
        s.de = {g: [0, 0, 0] for g in GROUPS}   # defensive events in own half
        s.rec = collections.Counter()           # receptions at x 70-88 by group
        s.strip = np.zeros((3, 2))              # passes from x 55-88 by the receiver's lane: n, completed
        s.cell = np.zeros(6)                    # wide ball (>20.4 m off centre, x>=35): n_pass, mates 0-14 m, opps 0-14 m, mates 14-21 m, far-wide mates, n(far visible)

    def add_pass(s, x0, y0, x1, y1, done):
        s.pass_lane[0, lane3(y0)] += 1
        if x0 > 70: s.pass_lane[1, lane3(y0)] += 1
        if done: s.trans[lane3(y0), lane3(y1)] += 1
        if 55 <= x0 < 88:
            s.strip[lane3(y1), 0] += 1; s.strip[lane3(y1), 1] += int(done)

    def add_on(s, g, x, y):
        if g in s.on and x >= 52.5:
            r = s.on[g]; r[0] += 1; r[1] += int(tl(y) < 14); r[2] += int(abs(y - 34) < 10)

    def add_de(s, g, x, y):
        if g in s.de and x <= 52.5:
            r = s.de[g]; r[0] += 1; r[1] += int(tl(y) < 14); r[2] += int(abs(y - 34) < 10)


def real_acc():
    a = Acc()
    for f in PL:
        ev = json.load(open(f, encoding="utf-8"))
        if not isinstance(ev, list) or not ev or "type" not in ev[0]: continue
        a.n += 2
        for e in ev:
            t = e["type"]["name"]; loc = e.get("location")
            if not loc: continue
            x, y = loc[0] * SB[0], loc[1] * SB[1]; g = grp_sb(e.get("position", {}).get("name"))
            if t == "Pass":
                if e["pass"].get("type", {}).get("name") in SETP: continue
                x1, y1 = e["pass"]["end_location"][0] * SB[0], e["pass"]["end_location"][1] * SB[1]
                a.add_pass(x, y, x1, y1, "outcome" not in e["pass"]); a.add_on(g, x, y)
            elif t == "Carry": a.add_on(g, x, y)
            elif t == "Ball Receipt*":
                if not e.get("ball_receipt", {}).get("outcome"):
                    a.add_on(g, x, y)
                    if 70 <= x < 88: a.rec[g] += 1
            elif t in ("Pressure", "Duel", "Interception", "Ball Recovery", "Clearance", "Block"): a.add_de(g, x, y)
    return a


def real_360():
    from matplotlib.path import Path
    c = np.zeros(6)
    for f in EU:
        try:
            ev = json.load(open(f, encoding="utf-8")); fr = {x["event_uuid"]: x for x in json.load(open(f.replace(".json", "_360.json"), encoding="utf-8"))}
        except Exception:
            continue
        for e in ev:
            if e["type"]["name"] != "Pass" or e["id"] not in fr or e["pass"].get("type", {}).get("name") in SETP: continue
            C = np.array(e["location"][:2]) * SB
            if C[0] < 35 or abs(C[1] - 34) <= 20.4: continue
            ff = fr[e["id"]]["freeze_frame"]
            opps = np.array([np.array(q["location"]) * SB for q in ff if not q["teammate"] and not q.get("keeper")]).reshape(-1, 2)
            mates = np.array([np.array(q["location"]) * SB for q in ff if q["teammate"] and not q["actor"] and not q.get("keeper")]).reshape(-1, 2)
            poly = Path(np.array(fr[e["id"]]["visible_area"]).reshape(-1, 2) * SB)
            s = 1. if C[1] >= 34 else -1.
            x0, x1 = C[0] - 15, C[0] + 15
            def vis(d0, d1):  # the band d0..d1 m from the ball's touchline, +-15 m in depth, fully in the camera
                ya, yb = sorted(((W - d1, W - d0) if s > 0 else (d0, d1)))
                return x0 >= 0 and x1 <= L and all(poly.contains_point(q) for q in [(x0 + .3, ya + .3), (x1 - .3, ya + .3), (x0 + .3, yb - .3), (x1 - .3, yb - .3)])
            dt = lambda P: (W - P[:, 1]) if s > 0 else P[:, 1]
            inx = lambda P: np.abs(P[:, 0] - C[0]) < 15
            if vis(0, 14) and len(mates) and len(opps):
                c[0] += 1
                c[1] += ((dt(mates) < 14) & inx(mates)).sum(); c[2] += ((dt(opps) < 14) & inx(opps)).sum()
            if vis(14, 21): c[3] += ((dt(mates) >= 14) & (dt(mates) < 21) & inx(mates)).sum(); c[5] += 1
    return c.tolist()


def sim_acc(seed):
    import calibrate
    m, r = calibrate.run(seed); ev = r["events"]; fr = np.array(r["frames"]); ft = fr[:, 0]
    a = Acc(); a.n = 2
    rl = {}
    for e in ev:
        if e["type"] == "lineup":
            for pl in e["players"]: rl[pl["id"]] = pl["role"]
        elif e["type"] == "substitution": rl[e["on"]] = e["on_role"]
    G = lambda pid: {"WG": "W"}.get(rl.get(pid, "?"), rl.get(pid, "?"))
    for e in ev:
        t = e["type"]
        if "x" not in e or t in ("lineup",): continue
        tm = e.get("team")
        if tm not in (0, 1): continue
        x, y = (e["x"], e["y"]) if tm == 0 else (L - e["x"], W - e["y"])
        if t in ("pass", "cross") and e.get("source") not in ("throw_in", "corner", "goal_kick", "free_kick", "short_corner"):
            x1, y1 = (e["end_x"], e["end_y"]) if tm == 0 else (L - e["end_x"], W - e["end_y"])
            a.add_pass(x, y, x1, y1, e.get("outcome") == "complete"); a.add_on(G(e["player"]), x, y)
            if e.get("source") in (None, "open_play") and x >= 35 and abs(y - 34) > 20.4:  # the 360 line, at the moment of the pass
                k = min(int(np.searchsorted(ft, e["t"])), len(fr) - 1); pos = fr[k, 5:49].reshape(22, 2)
                P = pos if tm == 0 else np.array([L, W]) - pos
                ci = int(fr[max(k - 1, 0), 3]); s = 1. if y >= 34 else -1.
                mates = np.array([P[11 * tm + i] for i in range(1, 11) if 11 * tm + i != ci]); opps = P[11 * (1 - tm) + 1:11 * (1 - tm) + 11]
                dt = lambda Q: (W - Q[:, 1]) if s > 0 else Q[:, 1]
                inx = lambda Q: np.abs(Q[:, 0] - x) < 15
                a.cell[0] += 1; a.cell[5] += 1
                a.cell[1] += ((dt(mates) < 14) & inx(mates)).sum(); a.cell[2] += ((dt(opps) < 14) & inx(opps)).sum(); a.cell[3] += ((dt(mates) >= 14) & (dt(mates) < 21) & inx(mates)).sum()
        elif t == "carry": a.add_on(G(e["player"]), x, y)
        elif t == "ball_receipt":
            a.add_on(G(e["player"]), x, y)
            if 70 <= x < 88: a.rec[G(e["player"])] += 1
        elif t in ("pressure", "duel", "interception", "ball_recovery", "clearance", "block", "tackle"): a.add_de(G(e["player"]), x, y)
    return a


def merge(accs):
    o = Acc()
    for a in accs:
        o.n += a.n; o.pass_lane += a.pass_lane; o.trans += a.trans; o.strip += a.strip; o.cell += a.cell; o.rec.update(a.rec)
        for g in GROUPS:
            o.on[g] = [p + q for p, q in zip(o.on[g], a.on[g])]; o.de[g] = [p + q for p, q in zip(o.de[g], a.de[g])]
    return o


def to_json(a):
    return dict(n=a.n, pass_lane=a.pass_lane.tolist(), trans=a.trans.tolist(), on=a.on, de=a.de, rec=dict(a.rec), strip=a.strip.tolist(), cell=a.cell.tolist())


def from_json(d):
    a = Acc(); a.n = d["n"]; a.pass_lane = np.array(d["pass_lane"]); a.trans = np.array(d["trans"]); a.on = d["on"]; a.de = d["de"]; a.rec = collections.Counter(d["rec"]); a.strip = np.array(d["strip"]); a.cell = np.array(d["cell"])
    return a


def real_cache():
    if CACHE.exists(): return from_json(json.load(open(CACHE)))
    a = real_acc(); a.cell = np.array(real_360())
    CACHE.parent.mkdir(exist_ok=True); json.dump(to_json(a), open(CACHE, "w"))
    return a


def _ini(o):
    from matchsim import engine
    engine.TUNE.update(o)


def report(S, R, over):
    sh = lambda p: p / p.sum()
    print("lateral panel: sim | real   (%s)" % (over or "no overrides"))
    for k, nm in ((0, "pass starts, all pitch"), (1, "pass starts, final third")):
        s_, r_ = sh(S.pass_lane[k]), sh(R.pass_lane[k])
        print("  %-26s wide/half/centre  %.2f/%.2f/%.2f | %.2f/%.2f/%.2f" % (nm, *s_, *r_))
    ts, tr = S.trans / S.trans.sum(1, keepdims=True), R.trans / R.trans.sum(1, keepdims=True)
    print("  completed passes from a wide lane end wide %.2f | %.2f   end centre %.2f | %.2f      from centre: per team-match %.0f | %.0f" % (ts[0, 0], tr[0, 0], ts[0, 2], tr[0, 2], S.trans[2].sum() / S.n, R.trans[2].sum() / R.n))
    print("  within 14 m of a touchline, on-ball events in the opposition half (sim | real):  " + "  ".join("%s %.2f|%.2f" % (g, S.on[g][1] / max(S.on[g][0], 1), R.on[g][1] / max(R.on[g][0], 1)) for g in GROUPS))
    print("  within 14 m of a touchline, defensive events in own half (sim | real):          " + "  ".join("%s %.2f|%.2f" % (g, S.de[g][1] / max(S.de[g][0], 1), R.de[g][1] / max(R.de[g][0], 1)) for g in GROUPS))
    print("  receptions at x 70-88 per team-match (sim | real):  " + "  ".join("%s %.1f|%.1f" % (g, S.rec[g] / S.n, R.rec[g] / R.n) for g in ("FB", "W", "CM", "ST", "DM")))
    print("  passes from x 55-88 by the receiver's lane: share wide/half/centre %.2f/%.2f/%.2f | %.2f/%.2f/%.2f   completed %.2f/%.2f/%.2f | %.2f/%.2f/%.2f" % (
        *sh(S.strip[:, 0]), *sh(R.strip[:, 0]), *(S.strip[:, 1] / S.strip[:, 0]), *(R.strip[:, 1] / R.strip[:, 0])))
    print("  wide ball: team-mates / opponents within 15 m in depth in the 14 m by the touchline: %.2f/%.2f | %.2f/%.2f   team-mates 14-21 m: %.2f | %.2f" % (
        S.cell[1] / S.cell[0], S.cell[2] / S.cell[0], R.cell[1] / R.cell[0], R.cell[2] / R.cell[0], S.cell[3] / S.cell[5], R.cell[3] / R.cell[5]))


def main():
    args = sys.argv[1:]
    if args and args[0] == "real":
        CACHE.unlink(missing_ok=True); real_cache(); print("real cache written", CACHE); return
    over = {a.split("=")[0]: float(a.split("=")[1]) for a in args if "=" in a}
    n = int(next((a for a in args if "=" not in a), 24))
    R = real_cache()
    with Pool(max(1, (os.cpu_count() or 8) - 4), initializer=_ini, initargs=(over,)) as p:
        S = merge(p.map(sim_acc, range(5000, 5000 + n)))
    report(S, R, over)


if __name__ == "__main__":
    main()
