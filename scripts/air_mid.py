"""Open-play passes in the air of 25-40 m (not into the box), per team-match, by where they are played from, pressure and direction,
real (StatsBomb Premier League 2015/16 events: High Pass, under_pressure) against simulated matches (lofted passes; pressure = an
opponent within 3 m of the passer when he plays it). Real teams play 12 of these a match, the sim 30.

    python scripts/air_mid.py [matches] [key=value ...]
"""
import collections, glob, json, pathlib, sys
from multiprocessing import Pool
import numpy as np
ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from matchsim import engine  # noqa: E402
from matchsim.teams import Tactics, make_team  # noqa: E402

KV = {k: float(v) for k, v in (a.split("=") for a in sys.argv[2:])}
ANY = bool(KV.pop("any_mode", 0))  # count ground passes too
L, W = 105., 68.


def key(o, en, pressed):
    third = "own" if o[0] < L / 3 else "mid" if o[0] < 2 * L / 3 else "fin"
    dx, dy = en[0] - o[0], abs(en[1] - o[1])
    dirn = "fwd" if dx > dy else "back" if -dx > dy else "side"
    return third, dirn, "pressed" if pressed else "free"


def into_box(en):
    return en[0] > L - 16.5 and abs(en[1] - W / 2) < 20.16


def real():
    c, n = collections.Counter(), 0
    for f in glob.glob(str(ROOT.parent / "reference_data" / "pl1516" / "*.json")):
        d = json.load(open(f, encoding="utf-8"))
        if not isinstance(d, list) or not d or "type" not in d[0]:
            continue
        n += 2
        for e in d:
            p = e.get("pass")
            if e.get("type", {}).get("name") != "Pass" or "type" in p or (p.get("height", {}).get("name") != "High Pass" and not ANY):
                continue
            if p.get("body_part", {}).get("name") not in ("Left Foot", "Right Foot"):
                continue
            o, en = np.array(e["location"]) * [L / 120, W / 80], np.array(p["end_location"]) * [L / 120, W / 80]
            ln = float(np.hypot(*(en - o)))
            if 25 <= ln < 40 and not into_box(en):
                c[key(o, en, bool(e.get("under_pressure")))] += 1
    return c, n


def one(seed):
    engine.TUNE.update(KV)
    rng = np.random.default_rng(seed)
    r = engine.Match(make_team("KES", "4-3-3", Tactics(), rng=rng), make_team("NBR", "4-4-2", Tactics(), rng=rng), seed=seed).run()
    F, dt = np.asarray(r["frames"]), r["meta"]["dt"]
    c = collections.Counter()
    for e in r["events"]:
        if e["type"] != "pass" or (not e.get("lofted") and not ANY) or e.get("source") not in (None, "open_play") or e.get("end_x") is None or e.get("body_part") != "foot":
            continue
        t = e["team"]
        fl = (lambda x, y: np.array([L - x, W - y])) if t == 1 else (lambda x, y: np.array([x, y]))
        o, en = fl(e["x"], e["y"]), fl(e["end_x"], e["end_y"])
        ln = float(np.hypot(*(en - o)))
        if not (25 <= ln < 40) or into_box(en):
            continue
        xy = F[min(int(round(e["t"] / dt)), len(F) - 1)][5:49].reshape(22, 2)
        nd = min(float(np.hypot(*(xy[k] - [e["x"], e["y"]]))) for k in range(11 * (1 - t), 11 * (1 - t) + 11) if xy[k][1] > -5)
        c[key(o, en, nd < 3.)] += 1
    return c


if __name__ == "__main__":
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 12
    rc, rn = real()
    with Pool(min(n, 12)) as p:
        sc = sum(p.map(one, range(900, 900 + n)), collections.Counter())
    sn = 2 * n
    print(f"25-40 m foot passes in the air, not into the box, per team-match: sim {sum(sc.values()) / sn:.1f} | real {sum(rc.values()) / rn:.1f}")
    for k in sorted(set(rc) | set(sc), key=lambda k: -(sc[k] / sn - rc[k] / rn)):
        print(f"  {' '.join(k):22s} {sc[k] / sn:5.2f} | {rc[k] / rn:5.2f}")
