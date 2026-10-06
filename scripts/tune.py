"""Coordinate-search the TUNE table so simulated matches land inside the real-football bands.

    python scripts/tune.py [matches_per_eval] [rounds]

Runs each candidate on the same seeds, scores how far every banded metric sits outside its band
(normalised by band width) and keeps the best value per parameter. Writes out/tune_best.json.
"""
import sys, json, pathlib, time
from multiprocessing import Pool
import numpy as np
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import calibrate, realism
from matchsim import engine

BANDS = {**calibrate.BANDS, **realism.BANDS}
BANDS.update({"goals": (2.4, 3.1), "shots": (20, 30), "passes": (750, 950)})
# parameter: (low, high)
PARAMS = {"pass_sigma": (1.0, 2.0), "intercept": (.15, .5), "long_bonus": (0., 1.2), "cross_v": (.8, 3.), "dwell_base": (.4, 1.3), "dwell_scale": (.8, 2.),
          "shot_cost": (.2, .9), "shot_k": (2.5, 7.), "shot_mid": (.8, 2.5), "usage": (.1, .8), "tackle_rate": (.06, .22), "prog_base": (.4, 2.0),
          "prog_dir": (.2, 1.6), "keep": (.2, .7)}


def _init(tune):
    engine.TUNE.update(tune)


def one(seed):
    m, r = calibrate.run(seed)
    d = calibrate.stats(m, r)
    d.update(realism.stats(m, r))
    return d


def score(tune, seeds, workers=6):
    with Pool(workers, initializer=_init, initargs=(tune,)) as p:
        rows = p.map(one, seeds)
    means = {k: float(np.mean([r[k] for r in rows])) for k in BANDS if k in rows[0]}
    total = 0.
    for k, v in means.items():
        lo, hi = BANDS[k]
        w = hi - lo
        total += max(0, lo - v, v - hi) / w + .1 * abs(v - (lo + hi) / 2) / w
    return total, means


if __name__ == "__main__":
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 30
    rounds = int(sys.argv[2]) if len(sys.argv) > 2 else 2
    seeds = list(range(3000, 3000 + n))
    best = {k: float(engine.TUNE[k]) for k in PARAMS}
    base, means = score(best, seeds)
    print(f"start score {base:.3f}", flush=True)
    out = pathlib.Path(__file__).resolve().parents[1] / "out" / "tune_best.json"
    for r in range(rounds):
        delta = [.3, .15, .08][min(r, 2)]
        for k, (lo, hi) in PARAMS.items():
            t0 = time.time()
            cand = []
            for f in (1 - delta, 1 + delta):
                v = float(np.clip(best[k] * f, lo, hi))
                if abs(v - best[k]) < 1e-9:
                    continue
                sc, _ = score({**best, k: v}, seeds)
                cand.append((sc, v))
            if cand and min(cand)[0] < base:
                base, best[k] = min(cand)
            print(f"round {r} {k:12s} -> {best[k]:.3f}  score {base:.3f}  ({time.time() - t0:.0f}s)", flush=True)
            out.write_text(json.dumps(dict(score=base, tune=best), indent=1))
    base, means = score(best, seeds)
    print("final", round(base, 3), json.dumps(best))
    for k, v in means.items():
        lo, hi = BANDS[k]
        print(f"  {k:16s} {v:9.3f}  {lo}-{hi}  {'ok' if lo <= v <= hi else 'OFF'}")
