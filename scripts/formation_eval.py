"""How well the formation reader (analytics.tools.MatchView.formation_timeline) recovers what the engine actually did, over fresh simulated matches:
starting formations right, formation changes found within 10 minutes, false alarms, delay.

    python scripts/formation_eval.py [matches] [key=value ...]    # keyword arguments go to formation_timeline (window, step, margin)
"""
import sys, pathlib
import numpy as np
from multiprocessing import Pool
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from matchsim.engine import Match
from matchsim.teams import make_team, Tactics
from analytics.tools import MatchView

KW = {k: float(v) for k, v in (a.split("=") for a in sys.argv[2:])}
SEED0 = int(KW.pop("seed0", 9300))  # seed0=9500: matches the thresholds were not chosen on
BOTH = bool(KW.pop("both", 0))
ATT = KW.pop("attack", None)  # attack=0: read the shape with the ball from all possession (the reader before 2026-10-18)
PHASE = {0: "in_possession", 1: "out_of_possession"}.get(int(KW.pop("out", 0)))  # out=1: score the changes read without the ball


def one(seed):
    rng = np.random.default_rng(seed)
    h = make_team("KES", "4-3-3", Tactics(press=.6, line_height=.6, directness=.45), rng=rng)
    a = make_team("NBR", "4-4-2", Tactics(press=.4, line_height=.4, directness=.55, marking=.5), rng=rng)
    mv = MatchView(Match(h, a, seed=seed).run())
    if ATT is not None:
        mv.read_attack = bool(ATT)
    out = []
    for tm in (0, 1):
        tl = mv.formation_timeline(tm, **KW)
        sc = mv.score_reading(tm, [c for c in tl["changes"] if BOTH or c["phase"] == PHASE])
        out.append((mv.same_shape(tl["starting"]["in_possession"], mv.truth(tm)["starting"]), sc["true_changes"], sc["found"], sc["false_alarms"], [d["lag_s"] for d in sc["detail"] if d["found"]], mv.window_agreement(tm, tl)))
    return out


if __name__ == "__main__":
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 16
    with Pool(12) as p:
        rows = sum(p.map(one, range(SEED0, SEED0 + n)), [])
    lags = sum((r[4] for r in rows), [])
    print(f"teams {len(rows)}: starting right {sum(r[0] for r in rows)}/{len(rows)}  changes found {sum(r[2] for r in rows)}/{sum(r[1] for r in rows)}  "
          f"false alarms {sum(r[3] for r in rows)}  median delay {np.median(lags) / 60 if lags else float('nan'):.1f} min  windows agreeing with the set system {np.mean([r[5] for r in rows]):.2f}")
