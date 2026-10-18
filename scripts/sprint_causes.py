"""Where the sim's sprinting comes from: each tick a player is above 7 m/s (25.2 km/h), was he on an all-out cue (urgent and the
sprint hint: going for the ball, pressing, a run in behind), an urgent run (effort follows need) or neither?

    python scripts/sprint_causes.py [N] [key=value ...]   # e.g. hint_press=0 to see how much of it one cue makes
"""
import pathlib
import sys
from multiprocessing import Pool

import numpy as np

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from matchsim import engine  # noqa: E402
from matchsim.teams import Tactics, make_team  # noqa: E402


KV = {k: float(v) for k, v in (a.split("=") for a in sys.argv[2:])}


def one(seed):
    engine.TUNE.update(KV)
    rng = np.random.default_rng(seed)
    m = engine.Match(make_team("KES", "4-3-3", Tactics(), rng=rng), make_team("NBR", "4-4-2", Tactics(), rng=rng), seed=seed)
    steer, tally = m._steer, np.zeros(3)

    def probe(tg, urg):
        cue = urg & m.sprint_hint
        burst = urg | (m.t < m.urg_until)
        steer(tg, urg)
        fast = np.linalg.norm(m.vel, axis=1) > 7.
        fast[[0, 11]] = False
        tally[0] += (fast & cue).sum(); tally[1] += (fast & ~cue & burst).sum(); tally[2] += (fast & ~burst).sum()
    m._steer = probe
    r = m.run()
    return np.r_[tally * m.dt / 20, len(r["events"])]  # seconds a player; events (a stalled match has few)


if __name__ == "__main__":
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 8
    with Pool(min(n, 8)) as p:
        t = np.mean(p.map(one, range(700, 700 + n)), axis=0)
    print(f"seconds above 7 m/s a player: all-out cue {t[0]:.0f}, urgent run {t[1]:.0f}, neither {t[2]:.0f} (total {t[:3].sum():.0f}); events a match {t[3]:.0f}")
