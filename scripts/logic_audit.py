"""Logic audit: does the player on the ball decide sensibly?

    python scripts/logic_audit.py [N] [key=value ...]

Runs N matches with decision auditing on (engine `s.audit`), then checks every on-ball decision against football common
sense rules, independent of match statistics:

  through_no_shot     no outfield defender goal-side, inside 28 m, and he does not shoot
  back_pass_free_fwd  passes backwards although a free forward pass (or an open carry) was there and nobody was close
  into_marked         plays to a covered team-mate while a free one at least as far forward was available
  shot_poor_pass_on   shoots from distance (xG < .03) while a safe forward pass existed
  chance_declined     a shot with xG >= .2 was available and he did something else
  dribble_into_man    carries into a defender he is unlikely to beat while a safe pass existed
  gk_pass_unpressed   passes back to his keeper outside his own third with nobody near
  clear_unpressed     clears the ball with nobody within 6 m
  hospital_ball       lofted pass to a receiver who is marked tightly, low p, with an easier option available

Each rule prints a rate (violations / opportunities) and a few concrete examples (seed, minute, position, options).
"""
import os, sys, pathlib, collections
from multiprocessing import Pool
import numpy as np
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import calibrate
from matchsim import engine

WORKERS = max(1, (os.cpu_count() or 8) - 4)
L, W = 105., 68.


def _init(over):
    engine.TUNE.update(over)


def gain(o, pos):
    return o["tgt"][0] - pos[0]


def check(rec):
    """Return {rule: (opportunity?, violation?)} for one decision record."""
    pos, opts, ch = rec["pos"], rec["opts"], rec["opts"][rec["chosen"]]
    out = {}
    dg = float(np.hypot(L - pos[0], W / 2 - pos[1]))
    passes = [o for o in opts if o["kind"] == "pass"]
    shots = [o for o in opts if o["kind"] == "shot"]
    carries = [o for o in opts if o["kind"] == "carry"]
    free_fwd = [o for o in passes if gain(o, pos) > 8 and o["p"] >= .7 and o["rcv_d"] >= 4]
    if rec["rk"] is None and rec["role"] > 0 and rec["ahead_close"] == 0 and dg < 30 and shots and shots[0]["xg"] >= .12:
        out["through_no_shot"] = ch["kind"] != "shot"
    if ch["kind"] == "pass" and gain(ch, pos) < -3 and rec["rk"] is None:
        out["back_pass_free_fwd"] = bool((free_fwd and rec["dnear"] > 3) or (rec["dnear"] > 5 and any(not c["blocked"] for c in carries)))
    if ch["kind"] == "pass" and ch["rcv_d"] < 2.2 and ch["p"] < .6 and rec["rk"] is None:
        out["into_marked"] = any(o["rcv_d"] >= 5 and o["p"] >= .8 and gain(o, pos) >= gain(ch, pos) - 4 for o in passes)
    if ch["kind"] == "shot" and rec["rk"] is None and ch["xg"] < .03:
        out["shot_poor_pass_on"] = any(gain(o, pos) > 5 and o["p"] >= .8 for o in passes)
    if shots and rec["rk"] is None and shots[0]["xg"] >= .2:
        out["chance_declined"] = ch["kind"] != "shot"
    if ch["kind"] == "carry" and ch["blocked"] and ch["p"] < .45:
        out["dribble_into_man"] = any(o["p"] >= .85 and o["rcv_d"] >= 3 for o in passes)
    if ch["kind"] == "pass" and ch["to"] == 0 and rec["role"] > 0:
        out["gk_pass_unpressed"] = rec["dnear"] > 7 and pos[0] > L / 3
    if ch["kind"] == "clear":
        out["clear_unpressed"] = rec["dnear"] > 6
    if ch["kind"] == "pass" and ch.get("lofted") and ch["rcv_d"] < 2.5 and ch["p"] < .5:
        out["hospital_ball"] = any((not o.get("lofted")) and o["p"] >= .8 and gain(o, pos) >= 0 for o in passes)
    return out


def one(seed):
    m = None
    rng = np.random.default_rng(seed)
    codes = list(calibrate.LEAGUE)
    h, a = rng.choice(codes, 2, replace=False)
    home = calibrate.make_team(h, rng.choice(["4-3-3", "4-4-2", "4-2-3-1"]), calibrate.Tactics(*rng.uniform(.3, .7, 8)), rng=rng)
    away = calibrate.make_team(a, rng.choice(["4-3-3", "4-4-2", "4-2-3-1", "3-5-2"]), calibrate.Tactics(*rng.uniform(.3, .7, 8)), rng=rng)
    m = engine.Match(home, away, seed=seed)
    m.audit = []
    m.run()
    opp, vio, ex = collections.Counter(), collections.Counter(), collections.defaultdict(list)
    dirs = collections.Counter()
    kinds = collections.Counter()
    for rec in m.audit:
        ch = rec["opts"][rec["chosen"]]
        if ch["kind"] == "pass" and rec["rk"] is None:
            g = gain(ch, rec["pos"])
            third = 0 if rec["pos"][0] < L / 3 else 1 if rec["pos"][0] < 2 * L / 3 else 2
            dirs[(third, "fwd" if g > 5 else "back" if g < -5 else "side")] += 1
        dirs[("n", ch["kind"])] += 1
        for rule, bad in check(rec).items():
            opp[rule] += 1
            if bad:
                vio[rule] += 1
                kinds[(rule, ch['kind'])] += 1
                if rule == 'through_no_shot':
                    kinds[('xg', min(int(rec['opts'][[o['kind'] for o in rec['opts']].index('shot')]['xg'] / .05), 8))] += 1
                    kinds[('dn', min(int(rec['dnear']), 6))] += 1
                if len(ex[rule]) < 3:
                    ex[rule].append((seed, rec))
    dirs.update(kinds)
    return opp, vio, ex, dirs, len(m.audit)


def fmt(rec):
    o = lambda x: f"{x['kind']}{'->' + str(x['to']) if 'to' in x else ''} u={x['u']:.2f}" + (f" p={x['p']:.2f}" if 'p' in x else "") + (f" xg={x['xg']:.2f}" if 'xg' in x else "") + (f" gain={x['tgt'][0] - rec['pos'][0]:+.0f}" if 'tgt' in x else "") + (f" rcv_d={x['rcv_d']:.1f}" if 'rcv_d' in x else "")
    best = sorted(rec["opts"], key=lambda x: -x["u"])[:5]
    return (f"  t={rec['t']:.0f}s team{rec['team']} #{rec['c']} role={rec['role']} pos=({rec['pos'][0]:.0f},{rec['pos'][1]:.0f}) press={rec['press']:.2f} nearest={rec['dnear']:.1f}m ahead={rec['ahead']} hold={rec['hold']}\n"
            + "    chose " + o(rec["opts"][rec["chosen"]]) + "\n    top: " + " | ".join(o(x) for x in best))


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if "=" not in a]
    over = {k: float(v) for k, v in (a.split("=") for a in sys.argv[1:] if "=" in a)}
    n = int(args[0]) if args else 24
    with Pool(WORKERS, initializer=_init, initargs=(over,)) as pool:
        res = pool.map(one, range(500, 500 + n))
    opp, vio, dirs, nd = collections.Counter(), collections.Counter(), collections.Counter(), 0
    ex = collections.defaultdict(list)
    for o, v, e, d, k in res:
        opp.update(o); vio.update(v); dirs.update(d); nd += k
        for r, l in e.items():
            ex[r] += l
    print(f"{n} matches, {nd / n:.0f} on-ball decisions per match\n")
    for r in ('through_no_shot', 'back_pass_free_fwd', 'dribble_into_man'):
        print(r, 'chosen:', {k[1]: v for k, v in dirs.items() if k[0] == r}, ' xg bins(.05):', sorted((k[1], v) for k, v in dirs.items() if k[0] == 'xg') if r == 'through_no_shot' else '', ' nearest def m:', sorted((k[1], v) for k, v in dirs.items() if k[0] == 'dn') if r == 'through_no_shot' else '')
    print("decision mix:", {k[1]: round(v / nd, 3) for k, v in dirs.items() if k[0] == "n"})
    for third, name in enumerate(("own third", "middle", "final third")):
        tot = sum(v for k, v in dirs.items() if k[0] == third) or 1
        print(f"  open-play passes, {name:11s}: " + "  ".join(f"{d} {dirs[(third, d)] / tot:.2f}" for d in ("fwd", "side", "back")))
    print()
    for rule in ("through_no_shot", "back_pass_free_fwd", "into_marked", "shot_poor_pass_on", "chance_declined", "dribble_into_man", "gk_pass_unpressed", "clear_unpressed", "hospital_ball"):
        print(f"{rule:20s} {vio[rule]:5d} / {opp[rule]:5d}  = {vio[rule] / max(opp[rule], 1):.3f}   ({vio[rule] / n:.1f} per match)")
    if "--examples" in sys.argv or True:
        for rule, l in ex.items():
            print(f"\n== {rule}")
            for seed, rec in l[:3]:
                print(f" seed {seed}:\n" + fmt(rec))
