"""Surrogate-assisted calibration (response-surface method): fewer simulations, uses every one of them.

    python scripts/tune_rs.py [matches_per_candidate] [rounds] [candidates_per_round]

Each round: simulate K random perturbations of the current TUNE (same seeds for all, so differences are signal),
fit a ridge regression of every metric on log-parameters (the local Jacobian), then run gradient descent (L-BFGS) on
that cheap surrogate to find parameters that put all metrics in band, and verify the proposal (and a half step) with
real simulations. Also writes the sensitivity table (which parameter moves which metric) to out/sensitivity.md.
Not machine learning of the football: a regression surrogate over the simulator's parameters.
"""
import os, sys, json, time, pathlib
from multiprocessing import Pool
import numpy as np
from scipy.optimize import minimize
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import tune
from matchsim import engine

WORKERS = max(1, (os.cpu_count() or 8) - 4)
PARAMS = {**tune.PARAMS, "fk_bonus": (0., .5), "box_foul": (.04, .3), "wide_error": (.5, 3.)}
NAMES = list(PARAMS)
LO, HI = np.log([PARAMS[k][0] if PARAMS[k][0] > 0 else 1e-3 for k in NAMES]), np.log([PARAMS[k][1] for k in NAMES])


def run_task(args):
    theta, seed = args
    engine.TUNE.update(theta)
    return tune.one(seed)


def evaluate(thetas, seeds, pool):
    rows = pool.map(run_task, [(th, sd) for th in thetas for sd in seeds], chunksize=1)
    n, out = len(seeds), []
    for k in range(len(thetas)):
        rr = rows[k * n:(k + 1) * n]
        out.append({m: float(np.mean([r[m] for r in rr])) for m in tune.BANDS if m in rr[0]})
    return out


def viol(means, soft=False):
    tot = 0.
    for m, v in means.items():
        lo, hi = tune.BANDS[m]
        w = hi - lo
        out = max(0, lo - v, v - hi) / w
        tot += (out ** 2 if soft else out) + .1 * abs(v - (lo + hi) / 2) / w
    return tot


def to_theta(u, base):
    return {k: float(np.exp(u[i])) for i, k in enumerate(NAMES)} | {k: v for k, v in base.items() if k not in NAMES}


if __name__ == "__main__":
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 24
    rounds = int(sys.argv[2]) if len(sys.argv) > 2 else 3
    K = int(sys.argv[3]) if len(sys.argv) > 3 else 36
    seeds = list(range(5000, 5000 + n))
    rng = np.random.default_rng(0)
    base = dict(engine.TUNE)
    u0 = np.log([max(base[k], 1e-3) for k in NAMES])
    out_dir = pathlib.Path(__file__).resolve().parents[1] / "out"
    U_all, Y_all = [], []
    with Pool(WORKERS, initializer=tune._init, initargs=({},)) as pool:
        cur_means = evaluate([to_theta(u0, base)], seeds, pool)[0]
        cur = viol(cur_means)
        print(f"start score {cur:.3f}", flush=True)
        for r in range(rounds):
            delta = [.3, .2, .12][min(r, 2)]
            t0 = time.time()
            U = np.clip(u0 + rng.uniform(-delta, delta, (K, len(NAMES))), LO, HI)
            res = evaluate([to_theta(u, base) for u in U], seeds, pool)
            metrics = list(res[0])
            U_all += list(U)
            Y_all += [[d[m] for m in metrics] for d in res]
            X, Y = np.array(U_all) - u0, np.array(Y_all)  # every simulated point so far, as offsets from the current centre
            xm, ym = X.mean(0), Y.mean(0)
            B = np.linalg.solve((X - xm).T @ (X - xm) + 1e-2 * np.eye(len(NAMES)), (X - xm).T @ (Y - ym))
            pred = lambda u: ym + ((u - u0) - xm) @ B
            lo_b, hi_b = np.maximum(LO - u0, -1.5 * delta), np.minimum(HI - u0, 1.5 * delta)

            def surrogate_loss(du):
                yh = pred(u0 + du)
                return viol(dict(zip(metrics, yh)), soft=True) + 2. * float(du @ du)

            best = None
            for start in [np.zeros(len(NAMES))] + [rng.uniform(lo_b, hi_b) * .5 for _ in range(6)]:
                sol = minimize(surrogate_loss, start, method="L-BFGS-B", bounds=list(zip(lo_b, hi_b)))
                if best is None or sol.fun < best.fun:
                    best = sol
            cands = [u0 + best.x, u0 + .5 * best.x]
            real = evaluate([to_theta(u, base) for u in cands], seeds, pool)
            scores = [viol(m) for m in real]
            i = int(np.argmin(scores))
            msg = f"round {r}: surrogate-optimal real {scores[0]:.3f}, half-step {scores[1]:.3f}, current {cur:.3f}"
            if scores[i] < cur:
                u0, cur, cur_means = cands[i], scores[i], real[i]
                msg += f" -> accepted, score {cur:.3f}"
            print(msg + f"  ({time.time() - t0:.0f}s, {len(U_all)} points)", flush=True)
            best_theta = to_theta(u0, base)
            (out_dir / "tune_best.json").write_text(json.dumps(dict(score=cur, tune={k: round(best_theta[k], 4) for k in NAMES}), indent=1))
    # sensitivity: effect on each metric (in band widths) of a +10 % change in each parameter
    lines = ["| parameter | biggest effects of +10% (in band widths) |", "|---|---|"]
    for i, k in enumerate(NAMES):
        eff = B[i] * np.log(1.1) / np.array([tune.BANDS[m][1] - tune.BANDS[m][0] for m in metrics])
        top = np.argsort(-np.abs(eff))[:3]
        lines.append(f"| {k} | " + ", ".join(f"{metrics[j]} {eff[j]:+.2f}" for j in top) + " |")
    (out_dir / "sensitivity.md").write_text("\n".join(lines) + "\n")
    print("final", round(cur, 3), json.dumps({k: round(best_theta[k], 4) for k in NAMES}))
    for m, v in cur_means.items():
        lo, hi = tune.BANDS[m]
        print(f"  {m:16s} {v:9.3f}  {lo}-{hi}  {'ok' if lo <= v <= hi else 'OFF'}")
