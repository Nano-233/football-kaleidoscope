"""Cut-backs (a pass from within 8 m of the byline, at most 25 m off the middle, to 6-18 m out and central) and what follows,
real (Euro 2020 360 frames) against simulated matches: reached, shot within 3 s, its xG (the sim's formula), first time, and how free
the receiving area was when the ball was played back.

    python scripts/cutback_outcome.py [matches] [key=value ...]
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
is_cb = lambda o, e: o[0] > L - 8 and abs(o[1] - W / 2) < 25 and L - 18 < e[0] < L - 6 and abs(e[1] - W / 2) < 12


def tally(c, end, opp, reached, shot):
    c["n"] += 1
    c["reached"] += reached
    nd = min((np.hypot(*(o - end)) for o in opp), default=99)
    c[("free at the spot", 0 if nd < 2 else 1 if nd < 4 else 2)] += 1
    if shot is not None:
        xg, first, dt = shot
        c["shot"] += 1
        c["xg"] += xg
        c["big"] += xg >= .25
        c["first"] += first


def real():
    c = collections.Counter()
    for f in glob.glob(str(ROOT.parent / "reference_data" / "euro2020" / "[0-9]*_360.json")):
        ff = {r["event_uuid"]: r["freeze_frame"] for r in json.load(open(f, encoding="utf-8"))}
        ev = json.load(open(f.replace("_360", ""), encoding="utf-8"))
        for i, e in enumerate(ev):
            if e["type"]["name"] != "Pass" or "type" in e["pass"] or e["id"] not in ff:
                continue
            o = np.array(e["location"]) * [L / 120, W / 80]; en = np.array(e["pass"]["end_location"]) * [L / 120, W / 80]
            if not is_cb(o, en):
                continue
            P = lambda p: np.array(p["location"]) * [L / 120, W / 80]
            shot = None
            for b in ev[i + 1:i + 6]:
                if b["type"]["name"] == "Shot" and b["team"]["id"] == e["team"]["id"]:
                    bx = np.array(b["location"]) * [L / 120, W / 80]
                    o2 = [P(p) for p in ff[b["id"]] if not p["teammate"] and not p["keeper"]] if b["id"] in ff else []
                    xg = Match._xg(None, bx, o2, header=b["shot"].get("body_part", {}).get("name") == "Head")[0] if b["id"] in ff else b["shot"].get("statsbomb_xg", 0)
                    shot = (xg, bool(b["shot"].get("first_time")), 0)
                    break
                if b["team"]["id"] != e["team"]["id"] and b["type"]["name"] in ("Pass", "Clearance", "Ball Recovery", "Interception", "Block"):
                    break
            tally(c, en, [P(p) for p in ff[e["id"]] if not p["teammate"] and not p["keeper"]], "outcome" not in e["pass"], shot)
            c["lofted"] += e["pass"].get("height", {}).get("name") == "High Pass"
    return c


def one(seed):
    TUNE.update(KV)
    rng = np.random.default_rng(seed)
    h = make_team("KES", "4-3-3", Tactics(press=.6, line_height=.6, directness=.45), rng=rng)
    a = make_team("NBR", "4-4-2", Tactics(press=.4, line_height=.4, directness=.55, marking=.5), rng=rng)
    m = Match(h, a, seed=seed).run()
    F, dt = np.asarray(m["frames"]), m["meta"]["dt"]
    gk = {s for s in range(22) if (m["meta"]["home"]["players"] + m["meta"]["away"]["players"])[s]["role"] == "GK"}
    ev, c = m["events"], collections.Counter()
    for i, e in enumerate(ev):
        if e["type"] not in ("pass", "cross") or e.get("source") not in (None, "open_play") or e.get("end_x") is None:
            continue
        flip = e["team"] == 1
        fl = (lambda x, y: np.array([L - x, W - y])) if flip else (lambda x, y: np.array([x, y]))
        o, en = fl(e["x"], e["y"]), fl(e["end_x"], e["end_y"])
        if not is_cb(o, en):
            continue
        f = F[min(int(round(e["t"] / dt)), len(F) - 1)]
        opp = [fl(*f[5 + 2 * s:7 + 2 * s]) for s in (range(11) if flip else range(11, 22)) if s not in gk and f[6 + 2 * s] > -5]
        shot = None
        for b in ev[i + 1:i + 25]:
            if b["type"] == "shot" and b["team"] == e["team"] and b["t"] - e["t"] < 3.5:
                shot = (b.get("xg", 0), bool(b.get("first_time")), b["t"] - e["t"]); break
            if b["type"] == "possession_change" or b["t"] - e["t"] > 3.5:
                break
        tally(c, en, opp, e.get("outcome") == "complete", shot)
        c["lofted"] += bool(e.get("lofted"))
        c[("kind", e["type"])] += 1
        if e.get("outcome") != "complete":
            cut = next((b for b in ev[i + 1:i + 6] if b["type"] in ("interception", "block", "keeper_claim", "clearance", "ball_recovery") and b.get("team") != e["team"] and "x" in b), None)
            if cut:
                q = fl(cut["x"], cut["y"])
                frac = float(np.clip(((q - o) @ (en - o)) / max((en - o) @ (en - o), 1e-6), 0, 1))
                c[("cut at", min(int(frac * 4), 3))] += 1
                c[("cut by", cut["type"])] += 1
            c["lost"] += 1
    return c


if __name__ == "__main__":
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 16
    with Pool(12) as p:
        sim = sum(p.map(one, range(9700, 9700 + n)), collections.Counter())
    rl = real()
    S, R = sim["n"], rl["n"]
    print(f"cut-backs: {S} sim ({S / n / 2:.2f} per team-match), {R} real (360 sample):   sim | real")
    print(f"  reached a team-mate   {sim['reached'] / S:.2f} | {rl['reached'] / R:.2f}")
    print(f"  a shot follows        {sim['shot'] / S:.2f} | {rl['shot'] / R:.2f}    first time {sim['first'] / max(sim['shot'], 1):.2f} | {rl['first'] / max(rl['shot'], 1):.2f}")
    print(f"  its mean xG           {sim['xg'] / max(sim['shot'], 1):.3f} | {rl['xg'] / max(rl['shot'], 1):.3f}    big chance per cut-back {sim['big'] / S:.2f} | {rl['big'] / R:.2f}")
    print(f"  in the air (high pass) {sim['lofted'] / S:.2f} | {rl['lofted'] / R:.2f}   sim kinds: " + ", ".join(f"{k[1]} {v}" for k, v in sim.items() if isinstance(k, tuple) and k[0] == "kind"))
    print("  cut out where along the pass (quarters): " + " ".join(f"{sim[('cut at', k)] / max(sim['lost'], 1):.2f}" for k in range(4)) + "   by: " + ", ".join(f"{k[1]} {v / max(sim['lost'], 1):.2f}" for k, v in sim.items() if isinstance(k, tuple) and k[0] == "cut by"))
    print("  nearest defender to where it was played <2 / 2-4 / 4+ m: " + " ".join(f"{sim[('free at the spot', b)] / S:.2f}|{rl[('free at the spot', b)] / R:.2f}" for b in range(3)))
