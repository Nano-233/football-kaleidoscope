"""Running by 15 minutes and where it fades, against real match-play fatigue (outfield players who play the whole match).

    python scripts/fatigue_profile.py [N]

Real references: Mohr, Krustrup & Bangsbo 2003 (top-class players): high-intensity running in the last 15 minutes 35-45 % below
the first 15; after the most intense 5 minutes, the next 5 minutes about 12 % below the match average (temporary fatigue).
Bradley et al. 2009 (Premier League): high-intensity running (> 19.8 km/h) about 2.5 km a match, sprinting (> 25.2 km/h)
about 0.25 km; Rampinini 2011 / Russell 2016: late-match declines in technical performance (passing accuracy a few points).
Also: goals by 15 minutes; the last 15 (with stoppage time) has the most, about 20-22 % of all goals.
"""
import os
import pathlib
import sys
from multiprocessing import Pool

import numpy as np

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from matchsim.engine import Match  # noqa: E402
from matchsim.teams import Tactics, make_team  # noqa: E402

WORKERS = max(1, (os.cpu_count() or 8) - 4)
HI, SPRINT = 5.5, 7.0


def one(seed):
    rng = np.random.default_rng(seed)
    hf, af = [("4-3-3", "4-4-2"), ("4-2-3-1", "3-5-2")][seed % 2]
    r = Match(make_team("KES", hf, Tactics(), rng=rng), make_team("NBR", af, Tactics(), rng=rng), seed=seed).run()
    F, dt, ev = np.array(r["frames"]), r["meta"]["dt"], r["events"]
    xy = F[:, 5:49].reshape(len(F), 22, 2)
    sp = np.linalg.norm(np.diff(xy, axis=0), axis=2) / dt
    sp[(sp > 12) | (xy[1:, :, 1] < -5)] = 0
    t = F[1:, 0]
    h1 = r["meta"]["half_time"]
    # match minute of each frame (second half restarts at 45:00 of match time)
    mm = np.where(t < h1, np.minimum(t / 60, 44.99), 45 + (t - h1) / 60)  # first-half stoppage stays in 30-45
    subbed = {e["slot"] for e in ev if e["type"] in ("substitution", "outfield_keeper", "sent_off") if "slot" in e} | {11 * (e["team"]) for e in ev if False}
    off = {i for i in range(22) if (xy[:, i, 1] < -5).any()}
    keep = [i for i in range(22) if i not in (0, 11) and i not in subbed and i not in off]
    win = np.minimum((mm // 15).astype(int), 5)
    mins = np.array([(win == w).sum() * dt / 60 for w in range(6)])  # windows with stoppage time are longer: rates per minute
    hi = np.array([[(sp[win == w][:, i] * (sp[win == w][:, i] > HI)).sum() * dt for w in range(6)] for i in keep])  # metres above 19.8 km/h
    spr = np.array([[(sp[win == w][:, i] * (sp[win == w][:, i] > SPRINT)).sum() * dt for w in range(6)] for i in keep])
    tot = np.array([sp[:, i].sum() * dt for i in keep])
    # temporary fatigue: after each player's most intense 5 minutes (by HI metres), the next 5 against his match average
    b5 = np.minimum((mm // 5).astype(int), 18)
    tf = []
    for i in keep:
        per5 = np.array([(sp[b5 == k][:, i] * (sp[b5 == k][:, i] > HI)).sum() * dt for k in range(18)])
        k = int(np.argmax(per5[:-1]))
        tf.append(per5[k + 1] / max(per5.mean(), 1))
    goals = [min(int((e["t"] / 60 if e["t"] < h1 else 45 + (e["t"] - h1) / 60) // 15), 5) for e in ev if e["type"] in ("goal", "own_goal")]
    pas = [[0, 0] for _ in range(6)]
    for e in ev:
        if e["type"] == "pass":
            w = min(int((e["t"] / 60 if e["t"] < h1 else 45 + (e["t"] - h1) / 60) // 15), 5)
            pas[w][0] += 1; pas[w][1] += e.get("outcome") == "complete"
    stam = np.array(r.get("stamina", [[1] * 22]))[-1][keep] if r.get("stamina") else np.ones(len(keep))
    return hi, spr, tot, tf, goals, pas, stam, mins


if __name__ == "__main__":
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 12
    with Pool(min(n, WORKERS)) as p:
        res = p.map(one, range(500, 500 + n))
    hi = np.concatenate([r[0] for r in res]); spr = np.concatenate([r[1] for r in res]); tot = np.concatenate([r[2] for r in res])
    print(f"{n} matches, {len(hi)} full-match outfield players")
    print("distance km %.2f   high-intensity km %.2f (real ~2.5)   sprint km %.2f (real ~0.25)" % (tot.mean() / 1000, hi.sum(1).mean() / 1000, spr.sum(1).mean() / 1000))
    rate = np.concatenate([r[0] / r[7] for r in res])  # HI metres a minute, by window
    print("HI metres a minute by 15 min:", rate.mean(0).round(1), " last 15 vs first 15: %+.0f %% (real -35 to -45 %%)" % (100 * (rate[:, 5].mean() / rate[:, 0].mean() - 1)))
    print("2nd half vs 1st half HI a minute: %+.0f %% (real about -10 to -20 %%)" % (100 * (rate[:, 3:].mean() / rate[:, :3].mean() - 1)))
    print("after the peak 5 minutes, next 5 vs match average: %.2f (real ~0.88)" % np.mean([x for r in res for x in r[3]]))
    g = np.bincount([x for r in res for x in r[4]], minlength=6)
    print("goals by 15 min:", g, " last 15 share %.0f %% (real ~20-22 %%)" % (100 * g[5] / max(g.sum(), 1)))
    pa = np.array([r[5] for r in res]).sum(0)
    print("pass accuracy by 15 min:", (pa[:, 1] / pa[:, 0]).round(3))
    print("stamina at the end (engine): mean %.2f min %.2f" % (np.concatenate([r[6] for r in res]).mean(), np.concatenate([r[6] for r in res]).min()))
