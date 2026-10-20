"""Who closes a wide carrier, real (Euro 2020 360 freeze frames at on-ball events) against the simulator (frames and the defenders' job
log): how near the nearest opponent is when a player has the ball wide and central, by third; and in the sim, which defender has the
press job, his role and distance, and the nearest man's role.

    python scripts/wide_press.py [matches] [key=value ...]
"""
import collections, glob, json, pathlib, sys
from multiprocessing import Pool
import numpy as np
ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from matchsim import engine  # noqa: E402
from matchsim.teams import Tactics, make_team  # noqa: E402

KV = {k: float(v) for k, v in (a.split("=") for a in sys.argv[2:])}
L, W = 105., 68.
ROLES = ["GK", "CB", "FB", "DM", "CM", "WG", "AM", "ST"]


def zone(x, y):
    return ("own" if x < L / 3 else "mid" if x < 2 * L / 3 else "fin") + ("-wide" if abs(y - W / 2) > 20.16 else "-cent")


def bucket(d):
    return "<2" if d < 2 else "2-4" if d < 4 else "4-7" if d < 7 else "7+"


def real():
    c = collections.Counter()
    for f in glob.glob(str(ROOT.parent / "reference_data" / "euro2020" / "[0-9]*_360.json")):
        ff = {r["event_uuid"]: r["freeze_frame"] for r in json.load(open(f, encoding="utf-8"))}
        for e in json.load(open(f.replace("_360", ""), encoding="utf-8")):
            rec = e["type"]["name"] == "Ball Receipt*"
            if e["type"]["name"] not in ("Pass", "Carry", "Dribble", "Shot", "Ball Receipt*") or e["id"] not in ff or "location" not in e:
                continue
            if e["type"]["name"] == "Pass" and "type" in e["pass"]:
                continue
            x, y = np.array(e["location"]) * [L / 120, W / 80]
            opp = [np.array(p["location"]) * [L / 120, W / 80] for p in ff[e["id"]] if not p["teammate"] and not p.get("keeper")]
            if not opp:
                continue
            d = min(float(np.hypot(*(o - [x, y]))) for o in opp)
            z = zone(x, y)
            if rec:
                c[(z, "rn")] += 1; c[(z, "r" + bucket(d))] += 1
                continue
            c[(z, "n")] += 1; c[(z, bucket(d))] += 1
    return c


def one(seed):
    engine.TUNE.update(KV)
    rng = np.random.default_rng(seed)
    h, a = make_team("KES", "4-3-3", Tactics(), rng=rng), make_team("NBR", "4-4-2", Tactics(), rng=rng)
    r = engine.Match(h, a, seed=seed).run()
    F, J = np.asarray(r["frames"]), np.asarray(r["jobs"])
    role = [p["role"] for k in ("home", "away") for p in r["meta"][k]["players"]]
    c = collections.Counter()
    prev = -1
    for i in range(len(F)):
        f, cr = F[i], int(F[i][3])
        new = cr >= 0 and cr != prev and prev >= 0 and prev // 11 == cr // 11
        prev = cr if cr >= 0 else prev
        if cr >= 0 and new:
            t = cr // 11; xy = f[5:49].reshape(22, 2)
            x, y = xy[cr] if t == 0 else (L - xy[cr][0], W - xy[cr][1])
            dm = min(float(np.hypot(*(xy[k] - xy[cr]))) for k in range(11 * (1 - t), 11 * (1 - t) + 11) if role[k] != "GK")
            c[(zone(x, y), "rn")] += 1; c[(zone(x, y), "r" + bucket(dm))] += 1
            jb = J[min(i, len(J) - 1)]
            mk = [k for k in range(11 * (1 - t), 11 * (1 - t) + 11) if jb[k] == cr - 11 * t]
            c[(zone(x, y), "rmarked" if mk else "runmarked")] += 1
            if mk:
                c[(zone(x, y), "rmarker " + role[mk[0]] + " " + bucket(float(np.hypot(*(xy[mk[0]] - xy[cr])))))] += 1
            c[(zone(x, y), "rrole " + role[cr])] += 1
        if i % 2:
            continue
        if cr < 0:
            continue
        t = cr // 11
        xy = f[5:49].reshape(22, 2)
        if (xy[:, 1] < -5).any():
            continue
        x, y = xy[cr] if t == 0 else (L - xy[cr][0], W - xy[cr][1])
        z = zone(x, y)
        opp = range(11 * (1 - t), 11 * (1 - t) + 11)
        d = {k: float(np.hypot(*(xy[k] - xy[cr]))) for k in opp if role[k] != "GK"}
        k0 = min(d, key=d.get)
        c[(z, "n")] += 1; c[(z, bucket(d[k0]))] += 1
        c[(z, "nearest " + role[k0])] += 1
        pr = [k for k in opp if J[min(i, len(J) - 1)][k] in (200, 201)]
        if pr:
            k = min(pr, key=lambda q: d.get(q, 99))
            c[(z, "presser " + role[k])] += 1; c[(z, "presser d " + bucket(d.get(k, 99)))] += 1
        else:
            c[(z, "no presser")] += 1
    return c


if __name__ == "__main__":
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 12
    rc = real()
    with Pool(min(n, 12)) as p:
        sc = sum(p.map(one, range(900, 900 + n)), collections.Counter())
    for z in ("fin-wide", "fin-cent", "mid-wide", "mid-cent"):
        print(f"{z}: nearest opponent at the ball, real | sim  " + "  ".join(f"{b} {rc[z, b] / rc[z, 'n']:.2f}|{sc[z, b] / sc[z, 'n']:.2f}" for b in ("<2", "2-4", "4-7", "7+")))
        print("   sim at reception " + ", ".join(f"{k[1:]} {v / sc[z, 'rn']:.2f}" for (zz, k), v in sorted(sc.items()) if zz == z and k.startswith(("rmark", "runm", "rrole"))))
        print(f"   at reception, real | sim  " + "  ".join(f"{b} {rc[z, 'r' + b] / max(rc[z, 'rn'], 1):.2f}|{sc[z, 'r' + b] / max(sc[z, 'rn'], 1):.2f}" for b in ("<2", "2-4", "4-7", "7+")))
        print("   sim " + ", ".join(f"{k} {v / sc[z, 'n']:.2f}" for (zz, k), v in sorted(sc.items()) if zz == z and k not in ("n", "rn", "<2", "2-4", "4-7", "7+") and not k.startswith("r")))
