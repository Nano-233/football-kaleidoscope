"""Simulate N matches and compare aggregate stats with real-football norms.

    python scripts/calibrate.py [N] [--check]

Norms are rough public aggregates for top-flight football (per match, both teams
combined unless noted). They are tuning targets, not redistributed data.
--check exits non-zero when a stat leaves its band (used as the realism test).
"""
import sys, pathlib
from multiprocessing import Pool
import numpy as np
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from matchsim.engine import Match
from matchsim.teams import make_team, Tactics, LEAGUE

BANDS = {  # stat: (low, high)
    "goals": (2.2, 3.4), "shots": (20, 30), "on_target": (7, 12), "xg": (2.2, 3.4),
    "passes": (700, 1000), "pass_acc": (.78, .88), "poss_gap": (0, .2),
    "fouls": (16, 28), "corners": (7, 13), "throw_ins": (30, 55), "goal_kicks": (8, 18),
    "dist_km": (9.5, 12), "top_speed": (8.5, 10.5), "tackles": (40, 100),
    "offsides": (2.5, 8), "centroid_ball_corr": (.5, 1.0), "team_depth": (25, 45), "team_width": (40, 60),
}


def _init(tune):
    from matchsim import engine
    engine.TUNE.update(tune)


def run(seed):
    rng = np.random.default_rng(seed)
    codes = list(LEAGUE)
    h, a = rng.choice(codes, 2, replace=False)
    home = make_team(h, rng.choice(["4-3-3", "4-4-2", "4-2-3-1"]), Tactics(*rng.uniform(.3, .7, 8)), rng=rng)
    away = make_team(a, rng.choice(["4-3-3", "4-4-2", "4-2-3-1", "3-5-2"]), Tactics(*rng.uniform(.3, .7, 8)), rng=rng)
    m = Match(home, away, seed=seed)
    return m, m.run()


def stats(m, r):
    ev = r["events"]
    cnt = lambda ty, **kw: sum(1 for e in ev if e["type"] == ty and all(e.get(k) == v for k, v in kw.items()))
    passes = [e for e in ev if e["type"] in ("pass", "cross")]
    fr = np.array(r["frames"])
    pos = fr[:, 5:49].reshape(len(fr), 22, 2)
    poss = fr[:, 4]
    dt = r["meta"]["dt"]
    sp = np.linalg.norm(np.diff(pos, axis=0), axis=2) / dt
    sp[sp > 15] = 0  # teleports at half time
    outfield = [i for i in range(22) if m.role[i] > 0]
    bx = fr[:, 1]
    cen = [pos[:, [i for i in outfield if i // 11 == t], 0].mean(1) for t in (0, 1)]
    corr = np.mean([np.corrcoef(c, bx)[0, 1] for c in cen])
    depth = np.mean([np.mean(np.ptp(pos[:, [i for i in outfield if i // 11 == t], 0], axis=1)) for t in (0, 1)])
    width = np.mean([np.mean(np.ptp(pos[:, [i for i in outfield if i // 11 == t], 1], axis=1)) for t in (0, 1)])
    shots = [e for e in ev if e["type"] == "shot"]
    return dict(
        goals=sum(r["meta"]["score"]), shots=len(shots), on_target=sum(1 for e in shots if e["outcome"] in ("goal", "saved")),
        xg=sum(e["xg"] for e in shots), passes=len(passes),
        pass_acc=np.mean([e["outcome"] == "complete" for e in passes if e["outcome"]]) if passes else 0,
        poss_gap=abs(np.mean(poss[poss >= 0] == 0) - .5) * 2,
        fouls=cnt("foul"), corners=cnt("corner"), throw_ins=cnt("throw_in"), goal_kicks=cnt("goal_kick"),
        tackles=cnt("tackle"), offsides=cnt("offside"), injuries=cnt("injury"), var=cnt("var_check"), disallowed=cnt("goal_disallowed"), subs=cnt("substitution"), recoveries=cnt("ball_recovery"), added1=r["meta"]["added_time"][0], added2=r["meta"]["added_time"][1], dist_km=sp[:, outfield].sum(0).mean() * dt / 1000, top_speed=np.percentile(sp[:, outfield].max(0), 50),
        centroid_ball_corr=corr, team_depth=depth, team_width=width,
        yellow=cnt("card", card="yellow"), red=cnt("card", card="red"), score=tuple(r["meta"]["score"]),
        stamina_end=float(m.stam[outfield].mean()))


def one(seed):
    return stats(*run(seed))


if __name__ == "__main__":
    n = int(sys.argv[1]) if len(sys.argv) > 1 and sys.argv[1].isdigit() else 12
    from matchsim import engine
    for a in sys.argv[1:]:  # --set key=value overrides for tuning sweeps
        if "=" in a:
            k, v = a.split("=")
            engine.TUNE[k] = float(v)
    with Pool(min(n, 6), initializer=_init, initargs=(dict(engine.TUNE),)) as p:
        rows = p.map(one, range(100, 100 + n))
    bad = 0
    print(f"{n} matches")
    for k, (lo, hi) in BANDS.items():
        v = np.mean([r[k] for r in rows])
        ok = lo <= v <= hi
        bad += not ok
        print(f"  {k:20s} {v:8.2f}   target {lo}-{hi}   {'ok' if ok else 'OFF'}")
    for k in ("yellow", "red", "injuries", "var", "disallowed", "subs", "recoveries", "added1", "added2", "stamina_end"):
        print(f"  {k:20s} {np.mean([r[k] for r in rows]):8.2f}")
    print("  scores:", [r["score"] for r in rows])
    if "--check" in sys.argv and bad:
        sys.exit(1)
