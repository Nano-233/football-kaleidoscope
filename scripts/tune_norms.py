"""Calibrate the simulator against real event data (StatsBomb aggregates in reference/norms_real.json).

    python scripts/tune_norms.py [matches_per_eval] [passes]          # coordinate search on the TUNE table
    python scripts/tune_norms.py eval [matches] key=value ...         # score one parameter set

The loss is a weighted sum of squared, scaled gaps over about 45 behavioural metrics (volumes, passing mix, progression,
shots, defending events, restarts). It runs after the logic is right: it balances numbers, it never fixes a missing behaviour.
Common random numbers (the same seeds for every candidate) keep the comparison fair. Run it on a snapshot copy of the code,
not on the files being edited: workers re-import the engine for every evaluation.
"""
import sys, os, json, pathlib, time, copy
import numpy as np
ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "scripts"))
from multiprocessing import Pool
import norms

WORKERS = max(1, (os.cpu_count() or 8) - 4)
REAL = {k: v for k, v in json.loads((ROOT / "reference" / "norms_real.json").read_text()).items() if v is not None}
# real restart counts per match (both teams), from the same StatsBomb sample
REAL_MATCH = {"throw_in": 45.4, "corner": 11.6, "goal_kick": 17.1, "free_kick": 22.9, "dist_km": 10.8, "top_speed": 9.4}

# metric -> (weight, scale: gap in these units counts as 1; None = 25 % of the real value)
W = {
    "passes": (1, None), "pass_acc": (1, .03), "carries": (1.5, None), "tackles": (1, None), "interception": (1, None), "clearance": (1, None),
    "block": (1, None), "foul": (1, None), "dispossessed": (1, None), "miscontrol": (1, None), "offside": (1, None),
    "fwd_own": (.5, .06), "side_own": (.5, .06), "back_own": (.5, .06), "fwd_mid": (2, .06), "side_mid": (1, .06), "back_mid": (2, .06),
    "fwd_fin": (1.5, .06), "side_fin": (1, .06), "back_fin": (1.5, .06), "long_share": (.5, .03), "short_share": (.5, .04), "pass_hold_mean": (1, .25),
    "into_final3": (3, None), "into_box": (3, None), "carry_into_final3": (1, None), "carry_into_box": (1, None), "carry_prog10": (1, None), "takeons": (1, None), "takeon_succ": (.5, .08),
    "shots": (3, None), "goals": (3, None), "on_target": (1, None), "blocked": (1, None), "xg_per_shot": (2, .02), "big_chance": (1, None),
    "shot_d_lt12": (1, .06), "shot_d_12_20": (1, .06), "shot_d_ge20": (1, .06), "shot_head": (1, .05), "shot_first": (1, .06), "shot_blockers": (1, .4),
    "acc_free": (2, .03), "acc_press": (2, .04), "press_pass_share": (1, .04), "into_f3_att": (1.5, None), "into_f3_rate": (1.5, .06), "within_f3_rate": (1.5, .05), "box_entry_rate": (1, .06), "inbox_pass_att": (1.5, None), "inbox_pass_rate": (1, .06), "cross_rate": (1, .06), "shot_free_look": (1, .15), "shot_nearest_def": (1, .6), "shot_def1_depth": (1, 1.5), "shot_goalside_n": (1, .6), "shot_def_in_box": (1, .6),
    "high_share": (2, .03), "head_pass": (1, None), "gk_long_share": (1, .08), "gk_high_share": (1, .08), "clear_head": (1, None), "aerial_lost": (1.5, None), "acc_own": (.5, .05), "acc_mid": (.5, .04), "acc_fin": (.5, .05),
}
M = {"throw_in": (1.5, None), "corner": (1, None), "goal_kick": (1, None), "free_kick": (1, None), "dist_km": (3, .8), "top_speed": (.5, .6)}

# parameter -> (low, high)
PARAMS = {
    "react": (1.0, 2.6), "patience": (0., .6), "carry_base": (0., .4), "drive": (0., 1.), "shoot_ref": (.05, .5), "shoot_slope": (1., 4.), "shoot_press": (0., 1.6),
    "shoot_hold": (0., 1.6), "ft_shot": (.1, 1.), "pot": (0., 2.2), "relief": (0., 2.), "risk_pow": (1., 3.), "prog_base": (.5, 2.), "cong_k": (0., 1.),
    "ctrl_marked": (.15, .8), "first_touch": (0., 6.), "tackle_rate": (.4, 3.6), "press_len": (2., 5.), "exec_k": (1., 3.5), "exec_floor": (0., .4), "dec_noise": (0., .5), "contact_r": (1., 2.5), "surv_k": (0., 2.), "gk_shoot": (0., 3.), "hurry_k": (0., 1.), "box_start": (30., 50.), "wn_end": (35., 65.), "lag_k": (.6, 1.8), "drop_k": (.5, 1.8), "wide_error": (1., 4.), "pass_sigma": (.6, 1.5), "intercept": (.3, .7),
    "usage": (0., 1.2), "keep": (.2, .8), "long_bonus": (0., 1.2), "cross_v": (.8, 2.2), "wide_v": (0., 1.3), "turn_cost": (0., 1.), "face_k": (0., 3.), "block_base": (.05, .3), "block_nb": (0., .25), "cruise_k": (.35, 1.), "run_frac": (.5, .95), "burst_hold": (.1, .9), "air_base": (0., 1.2), "air_k": (0., 1.5), "gk_air": (0., 4.), "head_on": (.05, .5), "head_clear": (.2, .8),
}


def _init(over):
    from matchsim import engine
    engine.TUNE.update(over)


def _one(seed):
    import calibrate
    m, r = calibrate.run(seed)
    per = [norms.team_metrics(R) for R in norms.from_sim(r).values()]
    cnt = {}
    fr = np.array(r["frames"]); pos = fr[:, 5:49].reshape(len(fr), 22, 2); dt = r["meta"]["dt"]
    sp = np.linalg.norm(np.diff(pos, axis=0), axis=2) / dt
    sp[sp > 15] = 0
    out = [i for i in range(22) if m.role[i] > 0]
    cnt["dist_km"] = float(sp[:, out].sum(0).mean() * dt / 1000)
    cnt["top_speed"] = float(sp[:, out].max(0).mean())
    for e in r["events"]:
        if e["type"] in REAL_MATCH and e["type"] not in ("dist_km", "top_speed"):
            cnt[e["type"]] = cnt.get(e["type"], 0) + 1
    return per, cnt


def evaluate(over, seeds, pool=None):
    own = pool is None
    if own:
        pool = Pool(WORKERS, initializer=_init, initargs=(over,))
    res = pool.map(_one, seeds)
    if own:
        pool.close(); pool.join()
    per = [m for r, _ in res for m in r]
    agg = norms.aggregate(per)
    cnt = {k: float(np.mean([c.get(k, 0) for _, c in res])) for k in REAL_MATCH}
    loss, parts = 0., {}
    for k, (w, sc) in W.items():
        if k not in agg or k not in REAL or np.isnan(agg[k]):
            continue
        scale = sc if sc is not None else max(abs(REAL[k]) * .25, 1e-3)
        e = float(np.clip((agg[k] - REAL[k]) / scale, -4, 4))
        parts[k] = e
        loss += w * e * e
    for k, (w, sc) in M.items():
        e = float(np.clip((cnt[k] - REAL_MATCH[k]) / (sc if sc is not None else max(REAL_MATCH[k] * .25, 1e-3)), -4, 4))
        parts[k] = e
        loss += w * e * e
    return loss, parts, agg, cnt


def main():
    args = [a for a in sys.argv[1:] if "=" not in a]
    kv = {k: float(v) for k, v in (a.split("=") for a in sys.argv[1:] if "=" in a)}
    if args and args[0] == "eval":
        n = int(args[1]) if len(args) > 1 else 16
        loss, parts, agg, cnt = evaluate(kv, list(range(5000, 5000 + n)))
        print(f"loss {loss:.1f}")
        for k, e in sorted(parts.items(), key=lambda kv_: -abs(kv_[1]))[:14]:
            print(f"  {k:16s} gap {e:+.2f}")
        return
    n = int(args[0]) if args else 16
    rounds = int(args[1]) if len(args) > 1 else 2
    seeds = list(range(5000, 5000 + n))
    from matchsim import engine
    cur = {k: float(engine.TUNE[k]) for k in PARAMS if k in engine.TUNE}
    cur.update(kv)
    out = ROOT / "out"
    out.mkdir(exist_ok=True)
    best, parts, agg, cnt = evaluate(cur, seeds)
    print(f"start loss {best:.1f}", flush=True)
    for rd in range(rounds):
        step = .3 if rd == 0 else .15
        for name in PARAMS:
            if name not in cur:
                continue
            lo, hi = PARAMS[name]
            span = hi - lo
            cand_best, cand_val = best, cur[name]
            for d in (-1, 1):
                v = float(np.clip(cur[name] + d * step * span, lo, hi))
                if abs(v - cur[name]) < 1e-9:
                    continue
                trial = dict(cur); trial[name] = v
                t0 = time.time()
                loss, _, _, _ = evaluate(trial, seeds)
                if loss < cand_best - 1e-9:
                    cand_best, cand_val = loss, v
            if cand_val != cur[name]:
                cur[name], best = cand_val, cand_best
            print(f"round {rd} {name:12s} -> {cur[name]:.3f}  loss {best:.1f}", flush=True)
            (out / "tune_norms_best.json").write_text(json.dumps({"loss": best, "params": cur}, indent=1))
    loss, parts, agg, cnt = evaluate(cur, seeds)
    print("final", round(loss, 1), json.dumps({k: round(v, 3) for k, v in cur.items()}))


if __name__ == "__main__":
    main()
