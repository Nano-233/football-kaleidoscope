"""For passes from in front of the box aimed at the good shooting spots (clean-look xG >= 0.12): the defenders around them at the moment
of the pass, real (Euro 2020 360 freeze frames) against simulated matches, and how often the pass arrives with that many in the way.
Tells positioning (fewer men there) from interception (as many men, cutting out fewer passes).

    python scripts/zone_pass_defence.py [matches] [key=value ...]
"""
import sys, json, glob, pathlib, collections
import numpy as np
from multiprocessing import Pool
ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from matchsim.engine import Match, TUNE
from matchsim.teams import make_team, Tactics
from scripts.zone_entry import in_zone
from scripts.zone_passes import origin

KV = {k: float(v) for k, v in (a.split("=") for a in sys.argv[2:])}
L, W = 105., 68.


def around(a, b, opp):
    """(outfield opponents within 2 m of the pass's path, within 5 m of where it is aimed)."""
    d = b - a
    lane = tgt = 0
    for q in opp:
        u = float(np.clip(((q - a) @ d) / max(d @ d, 1e-6), 0, 1))
        lane += np.hypot(*(q - (a + u * d))) < 2.
        tgt += np.hypot(*(q - b)) < 5.
    return min(lane, 3), min(tgt, 3)


def real():
    c, n = collections.Counter(), 0
    for f in glob.glob(str(ROOT.parent / "reference_data" / "euro2020" / "[0-9]*_360.json")):
        n += 1
        ff = {r["event_uuid"]: r["freeze_frame"] for r in json.load(open(f, encoding="utf-8"))}
        for e in json.load(open(f.replace("_360", ""), encoding="utf-8")):
            if e["type"]["name"] != "Pass" or "type" in e["pass"] or e["id"] not in ff or e["pass"].get("body_part", {}).get("name") == "Head":
                continue
            a = np.array([e["location"][0] * L / 120, e["location"][1] * W / 80]); b = np.array([e["pass"]["end_location"][0] * L / 120, e["pass"]["end_location"][1] * W / 80])
            if not in_zone(*b) or origin(*a) != "in front":
                continue
            opp = [np.array([p["location"][0] * L / 120, p["location"][1] * W / 80]) for p in ff[e["id"]] if not p["teammate"] and not p["keeper"]]
            k = around(a, b, opp)
            c[k + ("tried",)] += 1
            c[k + ("arrived",)] += "outcome" not in e["pass"]
    return c, n


def one(seed):
    TUNE.update(KV)
    rng = np.random.default_rng(seed)
    h = make_team("KES", "4-3-3", Tactics(press=.6, line_height=.6, directness=.45), rng=rng)
    a_ = make_team("NBR", "4-4-2", Tactics(press=.4, line_height=.4, directness=.55, marking=.5), rng=rng)
    m = Match(h, a_, seed=seed).run()
    F, dt = np.asarray(m["frames"]), m["meta"]["dt"]
    gk = {s for s in range(22) if (m["meta"]["home"]["players"] + m["meta"]["away"]["players"])[s]["role"] == "GK"}
    c = collections.Counter()
    for e in m["events"]:
        if e["type"] not in ("pass", "cross") or e.get("source") not in (None, "open_play") or e.get("body_part") in ("head", "hands") or e.get("end_x") is None:
            continue
        flip = e["team"] == 1
        fl = (lambda x, y: np.array([L - x, W - y])) if flip else (lambda x, y: np.array([x, y]))
        a, b = fl(e["x"], e["y"]), fl(e["end_x"], e["end_y"])
        if not in_zone(*b) or origin(*a) != "in front":
            continue
        f = F[min(int(round(e["t"] / dt)), len(F) - 1)]
        opp = [fl(*f[5 + 2 * s:7 + 2 * s]) for s in (range(11) if flip else range(11, 22)) if f[6 + 2 * s] > -5 and s not in gk]
        k = around(a, b, opp)
        c[k + ("tried",)] += 1
        c[k + ("arrived",)] += e.get("outcome") == "complete"
    return c


if __name__ == "__main__":
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 16
    with Pool(12) as p:
        sim = sum(p.map(one, range(9700, 9700 + n)), collections.Counter())
    rl, nr = real()
    S = sum(v for k, v in sim.items() if k[2] == "tried"); R = sum(v for k, v in rl.items() if k[2] == "tried")
    print("passes from in front of the box into the zone       share of passes sim | real     arrived sim | real   (n sim, real)")
    for lane in range(4):
        for tgt in range(4):
            s_, r_ = sim[(lane, tgt, "tried")], rl[(lane, tgt, "tried")]
            if s_ + r_ < 6:
                continue
            print(f"  {lane}{'+' if lane == 3 else ' '} in the path, {tgt}{'+' if tgt == 3 else ' '} near the target     {s_ / S:.2f} | {r_ / R:.2f}          "
                  f"{sim[(lane, tgt, 'arrived')] / max(s_, 1):.2f} | {rl[(lane, tgt, 'arrived')] / max(r_, 1):.2f}   ({s_}, {r_})")
