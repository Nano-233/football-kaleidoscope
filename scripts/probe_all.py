"""Structural probes against real data, from ONE batch of simulated matches (every probe reads the same matches).

    python scripts/probe_all.py [N=24] [key=value ...]      # N matches, TUNE overrides; prints sim | real for each probe
    python scripts/probe_all.py real                          # rebuild the real-data cache (out/probe_real.json, aggregates only)

Real references: StatsBomb Open Data, Premier League 2015/16 events (reference_data/pl1516) and Euro 2020 360 freeze frames
(reference_data/euro2020), both outside the repository. Per team-match unless stated. Loss and goals are too noisy at this sample size to judge a
mechanism; these event-level rates are not (hundreds of events each).
"""
import sys, os, json, glob, pathlib, collections
import numpy as np
ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "scripts"))
from multiprocessing import Pool
import norms
L, W = 105., 68.
SB = np.array([105 / 120, 68 / 80])
PL = sorted(glob.glob(str(ROOT.parent / "reference_data" / "pl1516" / "[0-9]*.json")))
EU = [f for f in sorted(glob.glob(str(ROOT.parent / "reference_data" / "euro2020" / "[0-9]*.json"))) if "_360" not in f]
CACHE = ROOT / "out" / "probe_real.json"
XE, YE = [0, 35, 52.5, 70, 88, 105.1], [0, 13.6, 24.8, 43.2, 54.4, 68.1]
FOUL_E = [0, 17.5, 35, 52.5, 70, 87.5, 105.1]
DB = [(0, 18), (18, 23), (23, 28), (28, 35), (35, 50)]
BANDS = [(0, .03), (.03, .06), (.06, .1), (.1, .2), (.2, .35), (.35, 2.)]
SETP = ("Corner", "Free Kick", "Throw-in", "Goal Kick", "Kick Off")


# ------------------------------------------------------------------ event-level probes (records from norms.from_statsbomb / from_sim)
def ev_probe(recs, fouls):
    """recs: {team: [records]}; fouls: list of (own-goal distance, ) for fouls committed. Returns dict of small arrays (sums over teams)."""
    o = collections.defaultdict(lambda: np.zeros(1))
    chan, w2b = np.zeros((5, 3)), collections.Counter()
    shots, goals, xg = np.zeros(6), np.zeros(6), np.zeros(6)
    pen = np.zeros(2)
    for tm, R in recs.items():
        for r in R:
            k = r["k"]
            if k in ("pass", "carry") and not (k == "pass" and r.get("setp")):
                if k == "carry" and np.hypot(r["ex"] - r["x"], r["ey"] - r["y"]) < 4.:
                    pass
                else:
                    xi = min(max(np.searchsorted(XE, r["x"], side="right") - 1, 0), 4); yi = min(max(np.searchsorted(YE, r["y"], side="right") - 1, 0), 4)
                    chan[xi, [0, 1, 2, 1, 0][yi]] += 1
            if k == "pass" and not r.get("setp") and r["x"] > 70 and abs(r["y"] - 34) > 16:
                into = r["ex"] > L - 20 and abs(r["ey"] - 34) < 22
                tag = ("cross" if r["cross"] else "pass") + ("_air" if r["high"] else "_gnd") + ("_box" if into else "_else")
                w2b[tag] += 1; w2b[tag + "_ok"] += r["ok"]
            if k == "shot":
                b = next(i for i, (lo, hi) in enumerate(BANDS) if lo <= r["xg"] < hi)
                shots[b] += 1; xg[b] += r["xg"]; goals[b] += r["outcome"] == "Goal"
                if r["kind"] == "Penalty": pen += [1, r["outcome"] == "Goal"]
    fz = np.zeros(6)
    for d in fouls: fz[min(int(np.searchsorted(FOUL_E, d, side="right") - 1), 5)] += 1
    return dict(chan=chan.tolist(), w2b=dict(w2b), shots=shots.tolist(), goals=goals.tolist(), xg=xg.tolist(), pen=pen.tolist(), fouls=fz.tolist(), n=len(recs))


def real_pl():
    acc = None
    for f in PL:
        ev = json.load(open(f, encoding="utf-8"))
        if not isinstance(ev, list) or not ev or "type" not in ev[0]: continue
        fouls = [e["location"][0] * 105 / 120 for e in ev if e["type"]["name"] == "Foul Committed" and "location" in e]
        acc = merge(acc, ev_probe(norms.from_statsbomb(ev), fouls))
    return acc


def merge(a, b):
    if a is None: return b
    out = {}
    for k in b:
        if k == "w2b":
            c = collections.Counter(a[k]); c.update(b[k]); out[k] = dict(c)
        elif k == "n":
            out[k] = a[k] + b[k]
        else:
            out[k] = (np.array(a[k]) + np.array(b[k])).tolist()
    return out


# ------------------------------------------------------------------ 360 probes (positions of everyone at a pass)
def pass_360(C, end, air, ok, opps, mates, ball_lat_ok, acc):
    """C, end: passer and pass end (attacking frame, m); opps (n,2); mates (k,2) without the passer."""
    if C[0] < 70: return
    acc["n3"] += 1
    if len(opps) >= 6 and any(q[0] > L - 9 and abs(q[1] - 34) < 10 for q in opps):  # keeper in view: the second-deepest opponent is the last defender
        line = np.sort(opps[:, 0])[-2]
        acc["nl"] += 1
        if end[0] - C[0] > 10 and end[0] > line + 1 and C[0] < line:
            if air: acc["beh_air"] += 1
            else: acc["beh_gnd"] += 1; acc["beh_gnd_ok"] += ok
    # a team-mate within 8 m of the touchline on the ball side, by how wide the ball is
    g = 0 if abs(C[1] - 34) < 10 else 1 if abs(C[1] - 34) < 18 else 2
    side_hi = C[1] >= 34
    strip = (mates[:, 1] > W - 8) if side_hi else (mates[:, 1] < 8)
    inx = np.abs(mates[:, 0] - C[0]) < 15
    if ball_lat_ok:
        acc[f"tl{g}_n"] += 1; acc[f"tl{g}_hit"] += bool((strip & inx).any())


def real_360():
    from matplotlib.path import Path
    acc = collections.Counter()
    for f in EU:
        ev = json.load(open(f, encoding="utf-8")); fr = {x["event_uuid"]: x for x in json.load(open(f.replace(".json", "_360.json"), encoding="utf-8"))}
        for e in ev:
            if e["type"]["name"] != "Pass" or e["id"] not in fr or e["pass"].get("type", {}).get("name") in SETP: continue
            p = e["pass"]; ff = fr[e["id"]]["freeze_frame"]
            C = np.array(e["location"][:2]) * SB; end = np.array(p["end_location"]) * SB
            opps = np.array([np.array(q["location"]) * SB for q in ff if not q["teammate"]]).reshape(-1, 2)
            mates = np.array([np.array(q["location"]) * SB for q in ff if q["teammate"] and not q["actor"] and not q.get("keeper")]).reshape(-1, 2)
            if len(mates) == 0: continue
            poly = Path(np.array(fr[e["id"]]["visible_area"]).reshape(-1, 2) * SB)
            strip_y = (W - 8, W) if C[1] >= 34 else (0, 8)
            x0, x1 = max(C[0] - 15, 0), min(C[0] + 15, L)
            seen = all(poly.contains_point(q) for q in [(x0, strip_y[0]), (x1, strip_y[0]), (x0, strip_y[1]), (x1, strip_y[1])])  # the whole strip is in the camera
            pass_360(C, end, p.get("height", {}).get("name") == "High Pass", "outcome" not in p, opps, mates, seen, acc)
    return dict(acc)


# ------------------------------------------------------------------ simulated side
def sim_one(seed):
    import calibrate
    m, r = calibrate.run(seed)
    ev = r["events"]
    fouls = [(e["x"] if e["team"] == 0 else L - e["x"]) for e in ev if e["type"] == "foul" and e.get("team") in (0, 1)]
    out = ev_probe(norms.from_sim(r), fouls)
    fr = np.array(r["frames"]); ft = fr[:, 0]; acc = collections.Counter()
    for e in ev:
        if e["type"] not in ("pass", "cross") or e.get("source") not in (None, "open_play"): continue
        tm = e["team"]; k = min(int(np.searchsorted(ft, e["t"])), len(fr) - 1); pos = fr[k, 5:49].reshape(22, 2); P = pos if tm == 0 else np.array([L, W]) - pos
        fl = (lambda x, y: (x, y)) if tm == 0 else (lambda x, y: (L - x, W - y))
        C = np.array(fl(e["x"], e["y"])); end = np.array(fl(e["end_x"], e["end_y"]))
        ci = int(fr[max(k - 1, 0), 3])
        mates = np.array([P[11 * tm + i] for i in range(1, 11) if 11 * tm + i != ci]).reshape(-1, 2)
        pass_360(C, end, bool(e.get("lofted")), e.get("outcome") == "complete", P[11 * (1 - tm):11 * (1 - tm) + 11], mates, True, acc)
    out["p360"] = dict(acc)
    fk = np.zeros((len(DB), 2))
    for e in ev:
        if e["type"] == "free_kick":
            b = next((i for i, (lo, hi) in enumerate(DB) if lo <= e["distance_to_goal"] < hi), None)
            if b is not None: fk[b, 0] += 1
        if e["type"] == "shot" and e.get("kind") == "free_kick_shot":
            d = np.hypot((L - e["x"]) if e["team"] == 0 else e["x"], 34 - e["y"]); b = next((i for i, (lo, hi) in enumerate(DB) if lo <= d < hi), None)
            if b is not None: fk[b, 1] += 1
    out["fk"] = fk.tolist()
    return out


def real_fk():
    fk = np.zeros((len(DB), 2)); n = 0
    for f in PL:
        ev = json.load(open(f, encoding="utf-8"))
        if not isinstance(ev, list) or not ev or "type" not in ev[0]: continue
        n += 2
        for e in ev:
            ty = e["type"]["name"]
            if not ((ty == "Pass" and e["pass"].get("type", {}).get("name") == "Free Kick") or (ty == "Shot" and e["shot"]["type"]["name"] == "Free Kick")): continue
            loc = np.array(e["location"][:2]) * SB; d = np.hypot(L - loc[0], 34 - loc[1]); b = next((i for i, (lo, hi) in enumerate(DB) if lo <= d < hi), None)
            if b is not None: fk[b, 0] += 1; fk[b, 1] += ty == "Shot"
    return (fk / (n / 1.)).tolist() if False else (fk / n).tolist()


def real_cache():
    if CACHE.exists(): return json.load(open(CACHE))
    pl, e3, fk = real_pl(), real_360(), real_fk()
    CACHE.parent.mkdir(exist_ok=True); json.dump(dict(pl=pl, e360=e3, fk=fk), open(CACHE, "w"))
    return json.load(open(CACHE))


def _ini(o):
    from matchsim import engine
    engine.TUNE.update(o)


def main():
    args = sys.argv[1:]
    if args and args[0] == "real":
        CACHE.unlink(missing_ok=True); real_cache(); print("real cache written", CACHE); return
    over = {a.split("=")[0]: float(a.split("=")[1]) for a in args if "=" in a}
    n = int(next((a for a in args if "=" not in a), 24))
    real = real_cache()
    with Pool(max(1, (os.cpu_count() or 8) - 4), initializer=_ini, initargs=(over,)) as p:
        res = p.map(sim_one, range(5000, 5000 + n))
    S = None
    for r in res: S = merge(S, {k: v for k, v in r.items() if k not in ("p360", "fk")})
    sim_360 = collections.Counter(); [sim_360.update(r["p360"]) for r in res]
    sim_fk = np.mean([r["fk"] for r in res], axis=0) / 2
    R = real["pl"]; nS, nR = S["n"], R["n"]
    f = lambda a, n_: np.array(a) / n_
    print("probes: %d simulated matches %s  (real: %d team-matches PL, Euro 360)" % (n, over or "", nR))
    cs, cr = f(S["chan"], nS), f(R["chan"], nR)
    sh = lambda c: (c[:, 0].sum() / c.sum(), c[:, 1].sum() / c.sum(), c[:, 2].sum() / c.sum())
    print("on-ball actions: wide/half/centre share    sim %.2f/%.2f/%.2f   real %.2f/%.2f/%.2f   | final third (x>70): sim %.2f/%.2f/%.2f real %.2f/%.2f/%.2f   total/team sim %.0f real %.0f"
          % (*sh(cs), *sh(cr), *sh(cs[3:]), *sh(cr[3:]), cs.sum(), cr.sum()))
    print("wide final third passes (x>70, |y-34|>16)            sim   real")
    for t in ("cross_air_box", "cross_gnd_box", "pass_air_box", "pass_gnd_box", "pass_air_else", "pass_gnd_else"):
        print("  %-16s per team-match               %5.2f  %5.2f   complete %.2f / %.2f" % (t, S["w2b"].get(t, 0) / nS, R["w2b"].get(t, 0) / nR, S["w2b"].get(t + "_ok", 0) / max(S["w2b"].get(t, 0), 1), R["w2b"].get(t + "_ok", 0) / max(R["w2b"].get(t, 0), 1)))
    b, e3 = sim_360, real["e360"]
    print("final-third passes ending beyond the last defender: ground  sim %.3f real %.3f (complete %.2f / %.2f)   lofted sim %.3f real %.3f"
          % (b["beh_gnd"] / b["nl"], e3["beh_gnd"] / e3["nl"], b["beh_gnd_ok"] / max(b["beh_gnd"], 1), e3["beh_gnd_ok"] / max(e3["beh_gnd"], 1), b["beh_air"] / b["nl"], e3["beh_air"] / e3["nl"]))
    print("team-mate within 8 m of the ball-side touchline (final third), ball central/half-space/wide:  sim " + " ".join("%.2f" % (b[f"tl{g}_hit"] / max(b[f"tl{g}_n"], 1)) for g in range(3)) + "   real " + " ".join("%.2f" % (e3[f"tl{g}_hit"] / max(e3[f"tl{g}_n"], 1)) for g in range(3)))
    print("shots by xG band (per team-match)  sim: " + " ".join("%.2f" % v for v in f(S["shots"], nS)) + "   real: " + " ".join("%.2f" % v for v in f(R["shots"], nR)))
    print("goals by xG band                   sim: " + " ".join("%.2f" % v for v in f(S["goals"], nS)) + "   real: " + " ".join("%.2f" % v for v in f(R["goals"], nR)))
    print("totals: shots %.1f / %.1f   goals %.2f / %.2f   xG %.2f / %.2f   penalties %.2f / %.2f (goals %.2f / %.2f)" % (sum(S["shots"]) / nS, sum(R["shots"]) / nR, sum(S["goals"]) / nS, sum(R["goals"]) / nR, sum(S["xg"]) / nS, sum(R["xg"]) / nR, S["pen"][0] / nS, R["pen"][0] / nR, S["pen"][1] / nS, R["pen"][1] / nR))
    print("fouls by distance from own goal (0-17.5, 17.5-35, 35-52.5, 52.5-70, 70-87.5, 87.5-105): sim " + " ".join("%.2f" % v for v in f(S["fouls"], nS)) + "  real " + " ".join("%.2f" % v for v in f(R["fouls"], nR)))
    print("free kicks (count, shots) within 18/23/28/35/50 m of goal: sim " + " ".join("%.2f,%.2f" % tuple(r_) for r_ in sim_fk) + "   real " + " ".join("%.2f,%.2f" % tuple(r_) for r_ in real["fk"]))


if __name__ == "__main__":
    main()
