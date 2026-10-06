"""Does the football look like football? Behavioural proxies computed from events and tracking.

    python scripts/brain_metrics.py [N]

- hold time before a pass or shot, and the share of first-time actions (a player in front of goal should not pause)
- defensive clustering: defenders within 6 m of the ball (a presser and a cover, not four)
- attackers in the box with no defender within 3 m
- nearest-teammate distance (a team that keeps its shape is not bunched up)
"""
import os, sys, pathlib
from multiprocessing import Pool
import numpy as np
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import calibrate
from matchsim import engine

WORKERS = max(1, (os.cpu_count() or 8) - 4)
L, W = 105., 68.
BANDS = {"hold_pass": (1.2, 3.0), "first_time_pass": (.2, .45), "first_time_big_chance": (.55, 1.0), "near_ball_defenders": (1.2, 2.6),
         "near_ball_danger": (2., 4.5), "bunched_frames": (0., .12), "unmarked_in_box": (.2, .55), "nearest_mate": (9., 16.), "attackers_near_box": (3.5, 6.5), "still_while_held": (0., .25), "still_restart": (0., .4), "mean_speed_held": (1.6, 2.8)}


def _init(over):
    engine.TUNE.update(over)


def one(seed):
    m, r = calibrate.run(seed)
    ev = r["events"]
    passes = [e for e in ev if e["type"] == "pass" and "hold" in e]
    shots = [e for e in ev if e["type"] == "shot" and e.get("xg", 0) >= .15 and "hold" in e]
    full = np.array(r["frames"])
    sp = np.hypot(*np.diff(full[:, 5:49].reshape(len(full), 22, 2), axis=0).transpose(2, 0, 1)) / r["meta"]["dt"]  # speed of every player per frame
    sp[sp > 15] = 0
    out_idx = [i for i in range(22) if i % 11 != 0]
    held = full[1:, 3] >= 0
    bspeed = np.hypot(*np.diff(full[:, 1:3], axis=0).T) / r["meta"]["dt"]
    tt = full[1:, 0]
    dead = np.zeros(len(tt), bool)
    for a, b, k in r['meta']['dead_log']:
        if k in ('throw_in', 'goal_kick', 'free_kick', 'indirect_free_kick') and 4 < b - a < 45:
            dead |= (tt > a + 2) & (tt < b)
    still_held = float((sp[held][:, out_idx] < 1.).mean())
    still_dead = float((sp[dead][:, out_idx] < 1.).mean()) if dead.any() else np.nan
    mean_held = float(sp[held][:, out_idx].mean())
    fr = np.array(r["frames"])[::5]
    pos = fr[:, 5:49].reshape(len(fr), 22, 2)
    ball, carrier, poss = fr[:, 1:3], fr[:, 3], fr[:, 4]
    near, dng, bunched, box_tot, box_free, mates, nbox = [], [], 0, 0, 0, [], []
    for k in range(len(fr)):
        if carrier[k] < 0 or pos[k, :, 1].min() < -5:
            continue
        t = int(carrier[k] // 11)
        defs = pos[k, 11 * (1 - t):11 * (1 - t) + 11]
        d = np.hypot(*(defs - ball[k]).T)
        n = int((d[1:] < 6).sum()) if t == 0 else int((d[1:] < 6).sum())
        near.append(n)
        bx_t = ball[k][0] if t == 0 else L - ball[k][0]
        if bx_t > L - 30:
            dng.append(n)
        bunched += n >= 4
        att = pos[k, 11 * t:11 * t + 11]
        x_t = att[:, 0] if t == 0 else L - att[:, 0]
        if bx_t > L - 20:
            nbox.append(int(((x_t > L - 22) & (np.abs(att[:, 1] - W / 2) < 26)).sum()))
        inbox = (x_t > L - 16.5) & (np.abs(att[:, 1] - W / 2) < 20.2)
        inbox[0] = False
        for i in np.nonzero(inbox)[0]:
            box_tot += 1
            box_free += bool(np.hypot(*(defs - att[i]).T).min() > 3)
        out = att[1:]
        dd = np.hypot(out[:, None, 0] - out[None, :, 0], out[:, None, 1] - out[None, :, 1]) + np.eye(len(out)) * 99
        mates.append(dd.min(1).mean())
    return dict(hold_pass=np.mean([e["hold"] for e in passes]), first_time_pass=np.mean([e["hold"] < .6 for e in passes]),
                first_time_big_chance=np.mean([e["hold"] < .6 for e in shots]) if shots else np.nan, near_ball_defenders=np.mean(near), near_ball_danger=np.mean(dng) if dng else np.nan,
                bunched_frames=bunched / max(len(near), 1), unmarked_in_box=box_free / max(box_tot, 1), nearest_mate=np.mean(mates), attackers_near_box=np.mean(nbox) if nbox else np.nan, still_while_held=still_held, still_restart=still_dead, mean_speed_held=mean_held)


if __name__ == "__main__":
    n = int(sys.argv[1]) if len(sys.argv) > 1 and sys.argv[1].isdigit() else 24
    over = {a.split('=')[0]: float(a.split('=')[1]) for a in sys.argv[1:] if '=' in a}
    with Pool(WORKERS, initializer=_init, initargs=(over,)) as p:
        rows = p.map(one, range(8000, 8000 + n))
    for k, (lo, hi) in BANDS.items():
        v = float(np.nanmean([r[k] for r in rows]))
        print(f"  {k:22s} {v:7.2f}   target {lo}-{hi}   {'ok' if lo <= v <= hi else 'OFF'}")
