"""Does behaviour actually respond to tactics? Same two squads, one team's tactic varied.

    python scripts/tactics_effect.py
"""
import sys, pathlib
from multiprocessing import Pool
import numpy as np
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from matchsim.engine import Match
from matchsim.teams import make_team, Tactics


def run(args):
    seed, knob, val = args
    rng = np.random.default_rng(seed)
    home = make_team("KES", "4-3-3", Tactics(**{knob: val}), rng=rng)
    away = make_team("NBR", "4-3-3", Tactics(), rng=rng)
    m = Match(home, away, seed=seed)
    r = m.run()
    fr = np.array(r["frames"])
    pos = fr[:, 5:49].reshape(len(fr), 22, 2)
    cb = [i for i in range(11) if m.role[i] == 1]
    out = [i for i in range(11) if m.role[i] > 0]
    opp_has = fr[:, 4] == 1
    ev = r["events"]
    return dict(
        line_x=pos[opp_has][:, cb, 0].mean(),                      # centre-back line when opponent has the ball
        depth=np.ptp(pos[:, out, 0], axis=1).mean(),
        width=np.ptp(pos[:, out, 1], axis=1).mean(),
        pressures=sum(1 for e in ev if e["type"] == "pressure" and e["team"] == 0),
        opp_passes=sum(1 for e in ev if e["type"] == "pass" and e["team"] == 1),
        poss=np.mean(fr[fr[:, 4] >= 0][:, 4] == 0),
        recoveries=sum(1 for e in ev if e["type"] == "possession_change" and e["team"] == 0),
    )


if __name__ == "__main__":
    seeds = range(200, 206)
    with Pool(6) as p:
        for knob, lo, hi in [("line_height", .15, .85), ("press", .1, .9), ("width", .1, .9), ("marking", 0., 1.)]:
            a = p.map(run, [(s, knob, lo) for s in seeds])
            b = p.map(run, [(s, knob, hi) for s in seeds])
            print(f"\n{knob}: {lo} -> {hi}")
            for k in a[0]:
                print(f"  {k:12s} {np.mean([x[k] for x in a]):8.1f} -> {np.mean([x[k] for x in b]):8.1f}")
