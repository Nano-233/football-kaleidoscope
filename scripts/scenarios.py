"""Scenario tests: put the engine in one exact football situation and watch a few seconds of it.

    python scripts/scenarios.py [trials] [name ...]

A full match takes about 12 s and says little about one behaviour. A scenario takes well under a second, so a fix can be tested
with 30-50 random repeats in a few seconds. This is the same idea as the Google Research Football "academy" scenarios (run to
score, 3 vs 1 with keeper, corner, counter-attack). Each scenario prints what happened and a plain expectation from real football.

Scenarios:
  through     striker alone with the ball in open space, no outfield defender in front: should run at goal / shoot, not pass
  pressed     carrier at speed with a defender set 3 m in front: should slow, and the contest should be real
  corner      attacking corner: players in the box, nobody behind the keeper or on the goal line
  hold_up     striker receives with a marker on his back: lay-off or shield, not a turn-and-shoot
  defending   the other side attacks down the wing: defenders face the play, keep a line, nobody turns their chest to their own goal
  chase       a ball played into space behind the line: runner and defenders race for it, keeper comes for it when he can
"""
import sys, pathlib
import numpy as np
ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from matchsim.engine import Match, _tf
from matchsim.teams import make_team, Tactics, LEAGUE

L, W = 105., 68.
ST, WG, CM, CB, GKR = 7, 5, 4, 1, 0


def build(seed):
    rng = np.random.default_rng(seed)
    codes = list(LEAGUE)
    h, a = rng.choice(codes, 2, replace=False)
    home = make_team(h, "4-3-3", Tactics(*np.full(8, .5)), rng=rng)
    away = make_team(a, "4-4-2", Tactics(*np.full(8, .5)), rng=rng)
    m = Match(home, away, seed=seed)
    m._reset_positions()
    m.events.clear()
    return m


def of(m, t, role):
    return [i for i in range(11 * t, 11 * t + 11) if m.role[i] == role]


def put(m, i, x, y, vx=0., vy=0.):
    m.pos[i] = (x, y)
    m.vel[i] = (vx, vy)
    m.head[i] = (np.sign(vx) or (1. if i < 11 else -1.), 0.)
    m.tsm[i] = m.pos[i]


def give(m, c):
    m.state, m.carrier = "held", -1
    m._gain_control(c, m.pos[c].copy())
    m.carry_speed = max(m.carry_speed, 3.)


def run(m, seconds):
    for _ in range(int(seconds / m.dt)):
        m._step()
        if m.done:
            break


def first(m, types, who=None):
    for e in m.events:
        if e["type"] in types and (who is None or e.get("player") == who):
            return e
    return None


# ---------------------------------------------------------------------------------------------
def through(seed):
    m = build(seed)
    st = of(m, 0, ST)[0]
    gk = 11
    for i in range(11, 22):  # the defending side is back in its own half, behind the striker
        put(m, i, 50 + 2.0 * (i - 11), 10 + 5.0 * (i - 11) % 50)
    put(m, gk, L - 1.5, W / 2)
    put(m, st, 70, 34, 5.5, 0)
    give(m, st)
    run(m, 7)
    shot = first(m, ["shot"], m.ids[st])
    ps = [e for e in m.events if e["type"] == "pass" and e.get("player") == m.ids[st]]
    return dict(shot=shot is not None, t_shot=shot["t"] if shot else np.nan, shot_d=float(np.hypot(L - shot["x"], shot["y"] - W / 2)) if shot else np.nan,
                passed=len(ps) > 0, xg=shot["xg"] if shot else np.nan)


def pressed(seed):
    m = build(seed)
    st = of(m, 0, ST)[0]
    cb = of(m, 1, CB)
    for i in range(11, 22):
        put(m, i, 85 + 1.0 * (i - 11), 4 + 6.0 * (i - 11))
    put(m, 11, L - 1.5, W / 2)
    put(m, cb[0], 64, 34, -1.5, 0)  # one defender, set 3 m in front of the carrier on the way to goal
    put(m, st, 61, 34, 5.5, 0)
    give(m, st)
    sp = []
    for k in range(10):
        m._step()
        sp.append(float(np.hypot(*m.vel[st])) if m.carrier == st else np.nan)
    run(m, 3)
    lost = first(m, ["dispossessed", "tackle"]) is not None or m.carrier // 11 == 1
    to = first(m, ["take_on"])
    p = first(m, ["pass"], m.ids[st])
    return dict(speed_1s=float(np.nanmean(sp[3:6])) if not np.all(np.isnan(sp[3:6])) else np.nan, lost=bool(lost), take_on=to is not None, passed=p is not None)


def corner(seed):
    m = build(seed)
    cp = np.array([L, 0.])
    taker = of(m, 0, WG)[0]
    m._set_dead("corner", 0, cp, 36., taker=taker)
    for _ in range(int(60 / m.dt)):
        m._step()
        if m.state != "dead":
            break
    att = [i for i in range(1, 11) if i != taker]
    gx = m.pos[11][0]
    xs = np.array([m.pos[i][0] for i in att])
    return dict(in_box=float(np.sum((xs > L - 16.5) & (np.abs([m.pos[i][1] for i in att] - np.full(len(att), W / 2)) < 20.16))),
                behind_gk=float(np.sum(xs > gx + .2)), on_line=float(np.sum(xs > L - 2.5)), deepest=float(xs.max() - L))


def hold_up(seed):
    m = build(seed)
    st = of(m, 0, ST)[0]
    cm = of(m, 0, CM)[0]
    cb = of(m, 1, CB)[0]
    for i in range(11, 22):
        put(m, i, 95 + 0.2 * (i - 11), 4 + 6.0 * (i - 11))
    put(m, 11, L - 1.5, W / 2)
    put(m, st, 86, 34, 0, 0)
    put(m, cb, 87.6, 34, 0, 0)  # goal-side marker, on him
    put(m, cm, 70, 30, 0, 0)
    give(m, cm)
    m.next_dec = m.t + 99
    m._pass(cm, st, np.array([86., 34.]), False, 16., .8)
    run(m, 4)
    shot = first(m, ["shot"], m.ids[st])
    pas = first(m, ["pass"], m.ids[st])
    fg = np.nan
    return dict(shot=shot is not None, laid_off=pas is not None, lost=(m.carrier // 11 == 1) if m.carrier >= 0 else False)


def defending(seed):
    m = build(seed)
    wg = of(m, 0, WG)[0]
    base = {GKR: 103., 1: 90., 2: 90., 3: 82., 4: 78., 5: 70., 6: 70., 7: 62.}
    for i in range(11, 22):  # the defending side starts in a normal shape, back line deep
        m.pos[i][0] = base[int(m.role[i])]
        m.tsm[i] = m.pos[i]
    for i in range(11):
        if i != wg and m.role[i] != GKR:
            m.pos[i][0] = np.clip(m.pos[i][0] + 25, 30, 85)
    put(m, wg, 80, 12, 4.0, 1.0)
    give(m, wg)
    run(m, 5)
    own = np.array([L, W / 2])
    facing, line = [], []
    for i in range(11, 22):
        if m.role[i] == GKR:
            continue
        g = own - m.pos[i]
        g /= np.linalg.norm(g) + 1e-9
        if np.hypot(*m.vel[i]) < 4.:  # a defender sprinting back to his goal naturally faces it; the rest should not
            facing.append(float(m.head[i] @ g > .3))
    back = [m.pos[i][0] for i in range(11, 22) if m.role[i] in (1, 2)]
    return dict(facing_own_goal=float(np.mean(facing)), line_spread=float(np.std(back)))


def chase(seed):
    m = build(seed)
    st = of(m, 0, ST)[0]
    cm = of(m, 0, CM)[0]
    for i in range(11, 22):
        put(m, i, 85 + 1.0 * (i - 11) % 8, 8 + 5.5 * (i - 11))
    put(m, 11, L - 4, W / 2)
    put(m, st, 82, 22, 5.0, 2.0)  # a runner off the line of the ball, going for the space behind the defence
    put(m, cm, 55, 34, 0, 0)
    give(m, cm)
    m.next_dec = m.t + 99
    m._pass(cm, st, np.array([97., 34.]), False, 20., .6)  # a ball played in behind
    run(m, 6)
    ev = [e["type"] for e in m.events]
    return dict(keeper_came=any(e in ev for e in ("keeper_claim", "keeper_sweep")), shot="shot" in ev, rcv_won=m.carrier // 11 == 0 if m.carrier >= 0 else False)


def marked_run(seed):
    """A striker makes a diagonal run in the final third with a centre-back assigned to him: does the marker stay with him?"""
    m = build(seed)
    st = of(m, 0, ST)[0]
    cm = of(m, 0, CM)[0]
    cbs = of(m, 1, CB)
    for i in range(11, 22):
        put(m, i, 90 + 0.4 * (i - 11), 4 + 6.0 * (i - 11))
    put(m, 11, L - 1.5, W / 2)
    put(m, cbs[0], 88, 30)
    put(m, cbs[1], 88, 40)
    put(m, st, 80, 24, 0, 0)
    put(m, cm, 66, 34)
    give(m, cm)
    m.next_dec = m.t + 99  # the carrier holds; the striker runs
    m.run_tgt[st] = (95., 44.)
    m.run_until[st] = m.t + 3.
    gap = []
    for k in range(15):
        m._step()
        d = np.hypot(*(m.pos[11:] - m.pos[st]).T)
        d[0] = 99
        gap.append(float(d.min()))
    off = float(m.pos[st][0] - np.sort(m.pos[11:, 0])[-2])
    return dict(gap_end=gap[-1], gap_mean=float(np.mean(gap)), loose=float(np.mean(np.array(gap) > 4.)), beyond_line=float(off > 0))


SCEN = dict(marked_run=marked_run, through=through, pressed=pressed, corner=corner, hold_up=hold_up, defending=defending, chase=chase)
EXPECT = dict(
    through="shot in most trials and few passes (real: a striker through on goal shoots or rounds the keeper, he does not play a pass)",
    pressed="speed 1 s in: well below his 5.5 m/s (about 2-3.3), a real contest (ball lost or a take-on logged in a good share of trials)",
    corner="in the box 4-7, behind the keeper 0, on the goal line 0",
    hold_up="mostly a lay-off or a shield; a shot is rare (<15%) and the ball is often lost to the marker",
    defending="facing own goal under 10%; back line spread (std of x) under 4 m",
    marked_run="the nearest defender stays within about 2-3 m of a runner through the run (real defenders track runners; the share beyond 4 m should be small)",
    chase="runner and defenders race; the keeper collects or sweeps in a good share when the ball is in his area",
)


def summarise(name, res):
    keys = res[0].keys()
    out = {}
    for k in keys:
        v = np.array([r[k] for r in res], float)
        out[k] = float(np.nanmean(v)) if not np.all(np.isnan(v)) else float("nan")
    print(f"{name:10s} " + "  ".join(f"{k}={v:.2f}" for k, v in out.items()))
    print(f"           expect: {EXPECT[name]}")


if __name__ == "__main__":
    args = sys.argv[1:]
    n = int(args[0]) if args and args[0].isdigit() else 30
    names = [a for a in args if a in SCEN] or list(SCEN)
    for nm in names:
        res = [SCEN[nm](100 + k) for k in range(n)]
        summarise(nm, res)
