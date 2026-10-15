"""Passes played in the air in open play (not crosses into the box): how often they end in an aerial contest and how many arrive, by
length, and how free the target was, real (Euro 2020: High Pass; the free-ness from 360 frames) against simulated matches (lofted).
The sim has twice the aerial contests per match on a similar share of passes in the air.

    python scripts/lofted.py [matches] [key=value ...]
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
BANDS = [(0, 25), (25, 40), (40, 200)]
band = lambda d: next(i for i, (a, b) in enumerate(BANDS) if a <= d < b)
into_box = lambda x, y: x > L - 16.5 and abs(y - W / 2) < 20.16


def real():
    c, n = collections.Counter(), 0
    for f in glob.glob(str(ROOT.parent / "reference_data" / "euro2020" / "[0-9]*_360.json")):
        n += 1
        ff = {r["event_uuid"]: r["freeze_frame"] for r in json.load(open(f, encoding="utf-8"))}
        ev = json.load(open(f.replace("_360", ""), encoding="utf-8"))
        for i, e in enumerate(ev):
            if e["type"]["name"] != "Pass" or "type" in e["pass"] or e["pass"].get("height", {}).get("name") != "High Pass":
                continue
            end = np.array(e["pass"]["end_location"]) * [L / 120, W / 80]
            if into_box(*end):
                continue
            b = band(e["pass"]["length"] * L / 120)
            c[(b, "n")] += 1
            c[(b, "ok")] += "outcome" not in e["pass"]
            c[(b, "contest")] += any(x["type"]["name"] == "Duel" and x["duel"].get("type", {}).get("name") == "Aerial Lost" or x.get("pass", {}).get("aerial_won") or x.get("clearance", {}).get("aerial_won") for x in ev[i + 1:i + 5])
            if e["id"] in ff:
                opp = [np.array(p["location"]) * [L / 120, W / 80] for p in ff[e["id"]] if not p["teammate"]]
                if opp:
                    d = min(np.hypot(*(o - end)) for o in opp)
                    c[(b, "free n")] += 1
                    c[(b, "free")] += d > 5
    return c, n


def one(seed):
    TUNE.update(KV)
    rng = np.random.default_rng(seed)
    h = make_team("KES", "4-3-3", Tactics(press=.6, line_height=.6, directness=.45), rng=rng)
    a = make_team("NBR", "4-4-2", Tactics(press=.4, line_height=.4, directness=.55, marking=.5), rng=rng)
    m = Match(h, a, seed=seed).run()
    F, dt = np.asarray(m["frames"]), m["meta"]["dt"]
    ev, c = m["events"], collections.Counter()
    for i, e in enumerate(ev):
        if e["type"] != "pass" or not e.get("lofted") or e.get("source") not in (None, "open_play") or e.get("end_x") is None:
            continue
        fl = (lambda x, y: np.array([L - x, W - y])) if e["team"] == 1 else (lambda x, y: np.array([x, y]))
        end = fl(e["end_x"], e["end_y"])
        if into_box(*end):
            continue
        b = band(e["length"])
        c[(b, "n")] += 1
        c[(b, "ok")] += e.get("outcome") == "complete"
        c[(b, "contest")] += any(x["type"] == "duel" and x.get("duel") == "aerial" for x in ev[i + 1:i + 8] if x["t"] - e["t"] < 4)
        f = F[min(int(round(e["t"] / dt)), len(F) - 1)]
        opp = range(11) if e["team"] == 1 else range(11, 22)
        d = min(np.hypot(*(fl(*f[5 + 2 * s:7 + 2 * s]) - end)) for s in opp if f[6 + 2 * s] > -5)
        c[(b, "free n")] += 1
        c[(b, "free")] += d > 5
    return c


if __name__ == "__main__":
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 16
    with Pool(12) as p:
        sim = sum(p.map(one, range(9700, 9700 + n)), collections.Counter())
    rl, nr = real()
    print("passes in the air in open play (not into the box), by length:   per team-match sim | real;  arrive;  end in an aerial contest;  no opponent within 5 m of where it lands, as it is played")
    for i, (a, b) in enumerate(BANDS):
        S, R = sim[(i, "n")], rl[(i, "n")]
        print(f"  {a}-{b if b < 200 else ''} m   {S / n / 2:5.1f} | {R / nr / 2:5.1f}   {sim[(i, 'ok')] / max(S, 1):.2f} | {rl[(i, 'ok')] / max(R, 1):.2f}   {sim[(i, 'contest')] / max(S, 1):.2f} | {rl[(i, 'contest')] / max(R, 1):.2f}   {sim[(i, 'free')] / max(sim[(i, 'free n')], 1):.2f} | {rl[(i, 'free')] / max(rl[(i, 'free n')], 1):.2f}")
