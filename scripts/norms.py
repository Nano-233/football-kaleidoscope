"""Real-vs-simulated behavioural norms.

    python scripts/norms.py real [events_dir]      # aggregate real StatsBomb open-data events -> reference/norms_real.json
    python scripts/norms.py sim [N] [key=value]    # same metrics from simulated matches, side by side with the real ones

Only AGGREGATE numbers are written to the repository (reference/norms_real.json); the raw open data stays outside it.
Data: StatsBomb Open Data (https://github.com/statsbomb/open-data), used for calibration targets only, never inside the app.
Both sides are reduced to the same neutral records first, so a metric means the same thing on each.
"""
import sys, os, json, glob, pathlib, collections
import numpy as np
ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "scripts"))
L, W = 105., 68.
SB = np.array([105 / 120, 68 / 80])


# ------------------------------------------------------------------ adapters -> neutral records
def from_statsbomb(events):
    """One match -> {team_id: [records]}; every record is in metres, attacking left to right (StatsBomb already stores it so)."""
    recs = collections.defaultdict(list)
    last_receipt = {}
    for e in events:
        ty = e["type"]["name"]
        tm = e.get("team", {}).get("id")
        loc = np.array(e["location"][:2]) * SB if "location" in e else None
        t = e["minute"] * 60 + e["second"]
        if tm is None:
            continue
        pl = e.get("player", {}).get("id")
        press = bool(e.get("under_pressure"))
        if ty == "Ball Receipt*":
            last_receipt[pl] = t
            if loc is not None and "outcome" not in e.get("ball_receipt", {}):
                recs[tm].append(dict(k="receipt", x=loc[0], y=loc[1]))
        if ty == "Pass":
            p = e["pass"]
            end = np.array(p["end_location"]) * SB
            kind = p.get("type", {}).get("name", "")
            if p.get("outcome", {}).get("name") == "Pass Offside":
                recs[tm].append(dict(k="offside", x=0, y=0))
            recs[tm].append(dict(k="pass", t=t, x=loc[0], y=loc[1], ex=end[0], ey=end[1], ok="outcome" not in p, cross=bool(p.get("cross")),
                                 through=bool(p.get("through_ball")), switch=bool(p.get("switch")), high=p.get("height", {}).get("name") == "High Pass",
                                 setp=kind in ("Corner", "Free Kick", "Throw-in", "Goal Kick", "Kick Off"), length=p["length"] * 105 / 120, press=press, kind=kind, head=p.get("body_part", {}).get("name") == "Head",
                                 hold=(t - last_receipt[pl]) if pl in last_receipt and 0 <= t - last_receipt[pl] < 10 else None, play=e["play_pattern"]["name"]))
        elif ty == "Clearance":
            recs[tm].append(dict(k="clearance", head=e.get("clearance", {}).get("body_part", {}).get("name") == "Head", x=loc[0] if loc is not None else 0, y=loc[1] if loc is not None else 0))
        elif ty == "Duel" and e.get("duel", {}).get("type", {}).get("name") == "Aerial Lost":
            recs[tm].append(dict(k="aerial_lost", x=loc[0], y=loc[1]))
        elif ty == "Carry":
            end = np.array(e["carry"]["end_location"]) * SB
            recs[tm].append(dict(k="carry", t=t, x=loc[0], y=loc[1], ex=end[0], ey=end[1], dur=e.get("duration", 0.)))
        elif ty == "Shot":
            s = e["shot"]
            ff = [(np.array(f["location"]) * SB, bool(f["teammate"]), bool(f.get("keeper")) or f.get("position", {}).get("name") == "Goalkeeper") for f in s.get("freeze_frame", [])]  # PL 15/16 frames carry no keeper flag, only the position
            recs[tm].append(dict(k="shot", t=t, x=loc[0], y=loc[1], xg=s.get("statsbomb_xg", 0.), outcome=s["outcome"]["name"], kind=s["type"]["name"],
                                 head=s["body_part"]["name"] == "Head", first=bool(s.get("first_time")), press=press, ff=ff, play=e["play_pattern"]["name"]))
        elif ty == "Dribble":
            recs[tm].append(dict(k="dribble", ok=e["dribble"]["outcome"]["name"] == "Complete", x=loc[0], y=loc[1]))
        elif ty == "Duel" and e["duel"].get("type", {}).get("name") == "Tackle":
            recs[tm].append(dict(k="tackle", x=loc[0], y=loc[1], won=e["duel"].get("outcome", {}).get("name") in ("Won", "Success In Play", "Success Out")))
        elif ty in ("Interception", "Clearance", "Block", "Foul Committed", "Ball Recovery", "Pressure", "Miscontrol", "Dispossessed", "Offside"):
            recs[tm].append(dict(k=ty.split()[0].lower(), x=loc[0] if loc is not None else 0, y=loc[1] if loc is not None else 0))
    return recs


def from_sim(r):
    """Simulated match result -> {team: [records]} in the same shape (coordinates flipped so every team attacks +x)."""
    ev = r["events"]
    recs = collections.defaultdict(list)
    fl = lambda tm, x, y: (x, y) if tm == 0 else (L - x, W - y)
    fr = np.array(r["frames"]); ft = fr[:, 0]
    for e in ev:
        ty, tm = e["type"], e.get("team")
        if ty in ("pass", "cross") and e.get("source") not in ("kickoff",):
            x, y = fl(tm, e["x"], e["y"]); ex, ey = fl(tm, e["end_x"], e["end_y"])
            recs[tm].append(dict(k="pass", t=e["t"], x=x, y=y, ex=ex, ey=ey, ok=e.get("outcome") == "complete", cross=ty == "cross", through=e.get("technique") == "through_ball",
                                 switch=e.get("technique") == "switch", high=bool(e.get("lofted")), setp=e.get("source") in ("throw_in", "corner", "short_corner", "goal_kick"),
                                 length=e["length"], press=bool(e.get("under_pressure")), hold=e.get("hold"), play="Regular Play",
                                 kind={"goal_kick": "Goal Kick", "throw_in": "Throw-in", "corner": "Corner", "short_corner": "Corner"}.get(e.get("source"), ""), head=e.get("body_part") == "head"))
        elif ty == "ball_receipt" and tm is not None:
            x, y = fl(tm, e["x"], e["y"])
            k_ = min(int(np.searchsorted(ft, e["t"])), len(fr) - 1)
            pos_ = fr[k_, 5:49].reshape(22, 2)
            opp_ = pos_[11 * (1 - tm) + 1: 11 * (1 - tm) + 11]
            recs[tm].append(dict(k="receipt", x=x, y=y, nd=float(np.hypot(*(opp_ - np.array([e["x"], e["y"]])).T).min())))
        elif ty == "carry":
            x, y = fl(tm, e["x"], e["y"]); ex, ey = fl(tm, e["end_x"], e["end_y"])
            recs[tm].append(dict(k="carry", t=e["t"], x=x, y=y, ex=ex, ey=ey, dur=e["duration"]))
        elif ty == "shot":
            x, y = fl(tm, e["x"], e["y"])
            ff = []
            for i, (px, py) in enumerate(e.get("freeze_frame", [])):
                px2, py2 = fl(tm, px, py)
                ff.append((np.array([px2, py2]), i // 11 == tm, i % 11 == 0))
            recs[tm].append(dict(k="shot", t=e["t"], x=x, y=y, xg=e["xg"], outcome={"goal": "Goal", "saved": "Saved", "off_target": "Off T", "blocked": "Blocked"}.get(e["outcome"], "Goal"),
                                 kind="Penalty" if e["kind"] == "penalty" else "Free Kick" if e["kind"] == "free_kick_shot" else "Open Play", head=e.get("body_part") == "head",
                                 first=bool(e.get("first_time")), press=bool(e.get("under_pressure")), ff=ff, play="Regular Play"))
        elif ty == "take_on":
            recs[tm].append(dict(k="dribble", ok=e["outcome"] == "won", x=e["x"], y=e["y"]))
        elif ty == "tackle":
            x, y = fl(tm, e["x"], e["y"])
            recs[tm].append(dict(k="tackle", x=x, y=y, won=e.get("outcome") == "won"))
        elif ty == "clearance":
            x, y = fl(tm, e.get("x", 0), e.get("y", 0))
            recs[tm].append(dict(k="clearance", head=e.get("body_part") == "head", x=x, y=y))
        elif ty == "duel" and e.get("duel") == "aerial" and e.get("outcome") == "lost":
            recs[tm].append(dict(k="aerial_lost", x=e.get("x", 0), y=e.get("y", 0)))
        elif ty in ("interception", "foul", "ball_recovery", "dispossessed", "offside", "block", "pressure", "miscontrol"):
            recs[tm].append(dict(k={"ball_recovery": "ball", "foul": "foul"}.get(ty, ty), x=e.get("x", 0), y=e.get("y", 0)))
    return recs


# ------------------------------------------------------------------ metrics (one team-match -> dict)
def inbox(x, y):
    return (L - x < 16.5) & (np.abs(y - W / 2) < 20.16)


def team_metrics(R):
    P = [r for r in R if r["k"] == "pass" and not r["setp"]]  # open-play passes
    Pall = [r for r in R if r["k"] == "pass"]
    C = [r for r in R if r["k"] == "carry" and np.hypot(r["ex"] - r["x"], r["ey"] - r["y"]) >= 4.]
    S = [r for r in R if r["k"] == "shot" and r["kind"] != "Penalty"]
    D = [r for r in R if r["k"] == "dribble"]
    T = [r for r in R if r["k"] == "tackle"]
    m = {}
    cnt = lambda k: sum(1 for r in R if r["k"] == k)
    m["passes"] = len(Pall)
    m["pass_acc"] = np.mean([r["ok"] for r in Pall]) if Pall else np.nan
    gain = np.array([r["ex"] - r["x"] for r in P]) if P else np.zeros(0)
    xs = np.array([r["x"] for r in P]) if P else np.zeros(0)
    for name, lo, hi in (("own", 0, L / 3), ("mid", L / 3, 2 * L / 3), ("fin", 2 * L / 3, L + 1)):
        s = (xs >= lo) & (xs < hi)
        n = max(int(s.sum()), 1)
        ok_ = np.array([r["ok"] for r in P])[s] if P else np.zeros(0)
        m[f"acc_{name}"] = float(ok_.mean()) if len(ok_) else np.nan
        m[f"n_{name}"] = int(s.sum())
        m[f"fwd_{name}"] = float((gain[s] > 5).sum() / n)
        m[f"side_{name}"] = float((np.abs(gain[s]) <= 5).sum() / n)
        m[f"back_{name}"] = float((gain[s] < -5).sum() / n)
    Pg = [r for r in P if not r["high"] and not r["cross"]]  # ground passes: accuracy with and without a defender on the passer
    fr_ = [r["ok"] for r in Pg if not r["press"]]; pr_ = [r["ok"] for r in Pg if r["press"]]
    m["acc_free"] = float(np.mean(fr_)) if fr_ else np.nan
    m["acc_press"] = float(np.mean(pr_)) if pr_ else np.nan
    m["press_pass_share"] = len(pr_) / max(len(Pg), 1)
    zone_rates = {"into_f3": [r for r in P if not r["cross"] and r["x"] < 70 <= r["ex"]],
                  "within_f3": [r for r in P if not r["cross"] and r["x"] >= 70 and r["ex"] > 70 and not inbox(r["ex"], r["ey"])],
                  "inbox_pass": [r for r in P if not r["cross"] and inbox(r["x"], r["y"])],
                  "box_entry": [r for r in P if not r["cross"] and inbox(r["ex"], r["ey"]) and not inbox(r["x"], r["y"])]}
    for zn, zr in zone_rates.items():
        m[f"{zn}_att"] = len(zr)
        m[f"{zn}_rate"] = float(np.mean([r["ok"] for r in zr])) if zr else np.nan
    cr = [r for r in P if r["cross"]]
    m["cross_rate"] = float(np.mean([r["ok"] for r in cr])) if cr else np.nan
    m["long_share"] = float(np.mean([r["length"] >= 32 for r in P])) if P else np.nan
    m["short_share"] = float(np.mean([r["length"] < 10 for r in P])) if P else np.nan
    m["avg_pass_len"] = float(np.mean([r["length"] for r in P])) if P else np.nan
    m["high_share"] = float(np.mean([r["high"] for r in Pall])) if Pall else np.nan
    m["head_pass"] = sum(1 for r in Pall if r.get("head"))
    gk = [r for r in Pall if r.get("kind") == "Goal Kick"]
    m["gk_long_share"] = float(np.mean([r["length"] >= 35 for r in gk])) if gk else np.nan
    m["gk_high_share"] = float(np.mean([r["high"] for r in gk])) if gk else np.nan
    m["clear_head"] = sum(1 for r in R if r["k"] == "clearance" and r.get("head"))
    m["aerial_lost"] = sum(1 for r in R if r["k"] == "aerial_lost")
    m["crosses_open"] = sum(1 for r in P if r["cross"])
    m["through"] = sum(1 for r in P if r["through"])
    m["switch"] = sum(1 for r in P if r["switch"])
    m["into_box"] = sum(1 for r in P if inbox(r["ex"], r["ey"]) and not inbox(r["x"], r["y"]))
    m["into_box_ok"] = sum(1 for r in P if inbox(r["ex"], r["ey"]) and not inbox(r["x"], r["y"]) and r["ok"])
    m["into_final3"] = sum(1 for r in P if r["ex"] > 2 * L / 3 and r["x"] < 2 * L / 3)
    h = [r["hold"] for r in P if r.get("hold") is not None]
    m["pass_hold_med"] = float(np.median(h)) if h else np.nan
    m["pass_hold_mean"] = float(np.mean(h)) if h else np.nan
    m["carries"] = len(C)
    m["carry_len"] = float(np.mean([np.hypot(r["ex"] - r["x"], r["ey"] - r["y"]) for r in C])) if C else np.nan
    m["carry_prog10"] = sum(1 for r in C if r["ex"] - r["x"] >= 9.1)
    m["carry_into_final3"] = sum(1 for r in C if r["ex"] > 2 * L / 3 and r["x"] < 2 * L / 3)
    m["carry_into_box"] = sum(1 for r in C if inbox(r["ex"], r["ey"]) and not inbox(r["x"], r["y"]))
    m["takeons"] = len(D)
    m["takeon_succ"] = float(np.mean([r["ok"] for r in D])) if D else np.nan
    m["tackles"] = len(T)
    m["tackle_won"] = float(np.mean([r["won"] for r in T])) if T else np.nan
    for k in ("interception", "clearance", "block", "foul", "ball", "dispossessed", "offside", "miscontrol", "pressure"):
        m[k] = cnt(k)
    for name, lo, hi in (("own", 0, L / 3), ("mid", L / 3, 2 * L / 3), ("fin", 2 * L / 3, L + 1)):
        m[f"tackle_{name}"] = sum(1 for r in T if lo <= r["x"] < hi)
    # shots
    rcr = [r for r in R if r["k"] == "receipt"]
    rc = np.array([np.hypot(L - r["x"], r["y"] - W / 2) for r in rcr])
    rnd = np.array([r.get("nd", np.nan) for r in rcr])
    for lo, hi in ((8, 16), (16, 25), (25, 35)):
        s_ = (rc >= lo) & (rc < hi) & ~np.isnan(rnd)
        m[f"rcv_nd_{lo}_{hi}"] = float(rnd[s_].mean()) if s_.any() else np.nan
        if lo >= 16:
            m[f"rcv_gt4_{lo}_{hi}"] = float((rnd[s_] > 4).mean()) if s_.any() else np.nan
    for lo, hi in ((0, 8), (8, 12), (12, 16), (16, 20), (20, 25), (25, 32)):
        m[f"rcv_{lo}_{hi}"] = float(((rc >= lo) & (rc < hi)).sum())
    m["shots"] = len(S)
    m["shot_xg"] = float(sum(r["xg"] for r in S))
    m["xg_per_shot"] = m["shot_xg"] / max(len(S), 1)
    d = np.array([np.hypot(L - r["x"], r["y"] - W / 2) for r in S]) if S else np.zeros(0)
    n = max(len(S), 1)
    m["shot_d_lt12"] = float((d < 12).sum() / n)
    m["shot_d_12_20"] = float(((d >= 12) & (d < 20)).sum() / n)
    m["shot_d_ge20"] = float((d >= 20).sum() / n)
    m["shot_box"] = float(np.mean([inbox(r["x"], r["y"]) for r in S])) if S else np.nan
    m["shot_head"] = float(np.mean([r["head"] for r in S])) if S else np.nan
    m["shot_first"] = float(np.mean([r["first"] for r in S])) if S else np.nan
    m["shot_press"] = float(np.mean([r["press"] for r in S])) if S else np.nan
    m["goals"] = sum(1 for r in R if r["k"] == "shot" and r["outcome"] == "Goal")
    m["on_target"] = sum(1 for r in S if r["outcome"] in ("Goal", "Saved"))
    m["blocked"] = sum(1 for r in S if r["outcome"] == "Blocked")
    m["big_chance"] = sum(1 for r in S if r["xg"] >= .25)
    # freeze frame at shot: defenders between shooter and goal, nearest opponent, keeper depth
    nb, nd, near5, gsf, tri2, tin, din, d1, d5 = [], [], [], [], [], [], [], [], []
    for r in S:
        if not r["ff"]:
            continue
        sh = np.array([r["x"], r["y"]])
        a, b = np.array([L, W / 2 - 3.66]), np.array([L, W / 2 + 3.66])
        opp = [(p, k) for p, tm, k in r["ff"] if not tm]
        ob = 0
        for p, kp in opp:
            if kp:
                continue
            cr = lambda p0, q, rr: (q[0] - p0[0]) * (rr[1] - p0[1]) - (q[1] - p0[1]) * (rr[0] - p0[0])
            s1, s2, s3 = cr(sh, a, p), cr(a, b, p), cr(b, sh, p)
            ob += int((s1 >= 0 and s2 >= 0 and s3 >= 0) or (s1 <= 0 and s2 <= 0 and s3 <= 0))
        nb.append(ob)
        dd = [np.hypot(*(p - sh)) for p, kp in opp if not kp]
        nd.append(min(dd) if dd else np.nan)
        near5.append(sum(1 for x in dd if x < 5))
        ox = [p[0] for p, kp in opp if not kp]
        gsf.append(np.mean([x > sh[0] for x in ox]) if ox else np.nan)
        tri2.append(sum(1 for x in ox if x > sh[0]))
        tin.append(sum(1 for p, tm, k in r["ff"] if tm and not k and inbox(p[0], p[1]) and np.hypot(*(p - sh)) > .1))
        din.append(sum(1 for p, kp in opp if not kp and inbox(p[0], p[1])))
        srt = np.sort([L - p[0] for p, kp in opp if not kp])
        d1.append(srt[0] if len(srt) else np.nan)
        d5.append(srt[min(4, len(srt) - 1)] if len(srt) else np.nan)
    m["shot_blockers"] = float(np.mean(nb)) if nb else np.nan
    m["shot_free_look"] = float(np.mean([b == 0 for b in nb])) if nb else np.nan
    m["shot_nearest_def"] = float(np.nanmean(nd)) if nd else np.nan
    m["shot_def_within5"] = float(np.mean(near5)) if near5 else np.nan
    m["shot_team_in_box"] = float(np.mean(tin)) if tin else np.nan
    m["shot_def_in_box"] = float(np.mean(din)) if din else np.nan
    m["shot_def1_depth"] = float(np.nanmean(d1)) if d1 else np.nan
    m["shot_def5_depth"] = float(np.nanmean(d5)) if d5 else np.nan
    m["shot_goalside_frac"] = float(np.nanmean(gsf)) if gsf else np.nan
    m["shot_goalside_n"] = float(np.mean(tri2)) if tri2 else np.nan
    return m


def aggregate(per):
    keys = per[0].keys()
    return {k: float(np.nanmean([p[k] for p in per])) for k in keys}


def real(dirp):
    per = []
    for f in sorted(glob.glob(str(pathlib.Path(dirp) / "[0-9]*.json"))):
        ev = json.load(open(f, encoding="utf-8"))
        recs = from_statsbomb(ev)
        for tm, R in recs.items():
            per.append(team_metrics(R))
    return per


def _sim_one(seed):
    import calibrate
    m, r = calibrate.run(seed)
    return [team_metrics(R) for R in from_sim(r).values()]


def _init(over):
    from matchsim import engine
    engine.TUNE.update(over)


GROUPS = [("volume", ["passes", "pass_acc", "carries", "tackles", "interception", "clearance", "block", "foul", "ball", "dispossessed", "offside", "pressure", "miscontrol"]),
          ("aerial", ["high_share", "head_pass", "gk_long_share", "gk_high_share", "clear_head", "aerial_lost"]),
          ("passing", ["acc_free", "acc_press", "press_pass_share", "into_f3_att", "into_f3_rate", "within_f3_att", "within_f3_rate", "box_entry_att", "box_entry_rate", "inbox_pass_att", "inbox_pass_rate", "cross_rate", "acc_own", "acc_mid", "acc_fin", "n_own", "n_mid", "n_fin", "fwd_own", "side_own", "back_own", "fwd_mid", "side_mid", "back_mid", "fwd_fin", "side_fin", "back_fin", "long_share", "short_share", "avg_pass_len", "crosses_open", "through", "switch", "pass_hold_med", "pass_hold_mean"]),
          ("progression", ["into_final3", "into_box", "into_box_ok", "carry_len", "carry_prog10", "carry_into_final3", "carry_into_box", "takeons", "takeon_succ", "tackle_won", "tackle_own", "tackle_mid", "tackle_fin"]),
          ("receipts near goal", ["rcv_nd_8_16", "rcv_nd_16_25", "rcv_nd_25_35", "rcv_gt4_16_25", "rcv_gt4_25_35", "rcv_0_8", "rcv_8_12", "rcv_12_16", "rcv_16_20", "rcv_20_25", "rcv_25_32"]),
          ("shots", ["shots", "goals", "on_target", "blocked", "shot_xg", "xg_per_shot", "big_chance", "shot_d_lt12", "shot_d_12_20", "shot_d_ge20", "shot_box", "shot_head", "shot_first", "shot_press", "shot_blockers", "shot_free_look", "shot_nearest_def", "shot_def_within5", "shot_goalside_frac", "shot_goalside_n", "shot_team_in_box", "shot_def_in_box", "shot_def1_depth", "shot_def5_depth"])]


def table(real_m, sim_m=None):
    for g, ks in GROUPS:
        print(f"-- {g}")
        for k in ks:
            rv = real_m.get(k, np.nan)
            line = f"  {k:18s} real {rv:8.2f}"
            if sim_m is not None:
                sv = sim_m.get(k, np.nan)
                ratio = sv / rv if rv else np.nan
                flag = "" if np.isnan(ratio) or .75 <= ratio <= 1.3 else "   <<<"
                line += f"   sim {sv:8.2f}   x{ratio:4.2f}{flag}"
            print(line)


if __name__ == "__main__":
    mode = sys.argv[1]
    outp = ROOT / "reference" / "norms_real.json"
    if mode == "real":
        d = sys.argv[2] if len(sys.argv) > 2 else str(ROOT.parent / "reference_data" / "pl1516")
        per = real(d)
        agg = aggregate(per)
        agg["_n_team_matches"] = len(per)
        ex = ROOT / "reference" / "real_extras.json"
        if ex.exists():
            agg.update({k: v for k, v in json.loads(ex.read_text()).items() if not k.startswith("_")})
        outp.parent.mkdir(exist_ok=True)
        outp.write_text(json.dumps({k: (None if isinstance(v, float) and np.isnan(v) else v) for k, v in agg.items()}, indent=1))
        table(agg)
    else:
        from multiprocessing import Pool
        args = [a for a in sys.argv[2:] if "=" not in a]
        over = {k: float(v) for k, v in (a.split("=") for a in sys.argv[2:] if "=" in a)}
        n = int(args[0]) if args else 24
        W_ = max(1, (os.cpu_count() or 8) - 4)
        with Pool(W_, initializer=_init, initargs=(over,)) as pool:
            res = pool.map(_sim_one, range(700, 700 + n))
        per = [m for r in res for m in r]
        real_m = json.loads(outp.read_text())
        real_m = {k: (np.nan if v is None else v) for k, v in real_m.items()}
        table(real_m, aggregate(per))
