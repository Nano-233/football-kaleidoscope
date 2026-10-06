"""Match engine: event-driven on-ball decisions + 5 Hz kinematic movement.

Two output streams (SoccER-style): ground-truth `events` and `frames` (tracking).
- On-ball: the carrier periodically evaluates pass / carry / shot / clearance options
  from the *actual geometry* (lane interception, receiver pressure, xG), picks one by
  softmax over utilities, and executes it with attribute-driven noise.
- Off-ball: movement.py gives every player a target (shape, marking, pressing, runs,
  support); `_steer` moves them with pace / acceleration / stamina limits.
- Passes/loose balls are resolved dynamically: whoever physically reaches the ball
  first rolls for control, so outcomes follow from where players really are.
Home attacks +x for the whole match (no side swap; ponytail: invisible to analytics).
"""
import dataclasses
import numpy as np
from .teams import L, W, ATTRS, ROLE_IDX, TRAITS, team_meta
from . import movement
from .movement import _tf, _unit

DT = 0.2
# Single tuning table (calibrated by scripts/calibrate.py against real-football norms).
TUNE = dict(prog_base=.25, prog_dir=.5, keep=.35, loss=1.0, shot_cost=.45, shot_k=2.5, shot_k_dir=5.0,
            carry_base=.1, carry_prog=.2, carry_streak=.3, dwell_base=.8, dwell_shape=2.4, dwell_scale=1.4,
            tackle_rate=.12, wide_error=1.0)
sig = lambda x: 1 / (1 + np.exp(-x))
GOAL_HALF = 3.66
_MULT = ("shot", "runs", "follow", "prog", "loss", "cruise", "commit", "foul", "tackle")
_ADD = ("range", "carry", "takeon", "sweep")
_PRESET_X = {"GK": .03, "CB": .20, "FB": .22, "DM": .37, "CM": .43, "WG": .66, "AM": .57, "ST": .70}
_ROLE_NAME = {v: k for k, v in ROLE_IDX.items()}
PARK = np.array([[L / 2, -40. - 2 * (i % 11)] for i in range(22)])  # sent-off players wait off the pitch


class Match:
    def __init__(s, home, away, seed=0, dt=DT):
        s.rng = np.random.default_rng(seed)
        s.seed, s.dt = seed, dt
        s.teams = [home, away]
        s.base_tact = [home["tactics"], away["tactics"]]
        s.tact = [dataclasses.replace(t) for t in s.base_tact]
        s.last_tact_emit = [dataclasses.asdict(t) for t in s.tact]
        s.last_tact_t = [-999., -999.]
        pl = home["players"] + away["players"]
        s.ids, s.shirt = [""] * 22, [0] * 22
        s.names = {p["id"]: p["name"] for tm in (home, away) for p in tm["players"] + tm["bench"]}
        s.role, s.anchor = np.zeros(22, int), np.zeros((22, 2))
        s.attr = {a: np.zeros(22) for a in ATTRS}
        s.mod = {k: np.ones(22) for k in _MULT}
        s.mod.update({k: np.zeros(22) for k in _ADD})
        s.vmax, s.acc = np.zeros(22), np.zeros(22)
        s.vel = np.zeros((22, 2))
        s.stam = np.ones(22)
        for i, p in enumerate(pl):
            s._load(i, p, p["anchor"])
        s.bench = [list(home["bench"]), list(away["bench"])]
        s.yellow, s.off = np.zeros(22, int), np.zeros(22, bool)
        s.subs = [0, 0]
        s.windows = [[(s.rng.uniform(9, 16), 2), (s.rng.uniform(24, 30), 2), (s.rng.uniform(34, 40), 1)] for _ in (0, 1)]
        s.mark = [-np.ones(11, int), -np.ones(11, int)]
        s.run_until, s.run_cool = np.zeros(22), np.zeros(22)
        s.run_tgt = np.zeros((22, 2))
        s.commit_until = np.zeros(22)  # presser committed to a tackle until this time
        s.block_until = np.zeros(22)   # cannot claim the ball (just lost / just passed it)
        s.last_press = np.full(22, -9.)
        s.trans_until = [0., 0.]
        s.t, s.tick, s.period = 0., 0, 1
        s.h1 = 2700 + int(s.rng.integers(60, 150))
        s.end_t = s.h1 + 2700 + int(s.rng.integers(120, 300))
        s.done = False
        s.score = [0, 0]
        s.events, s.frames = [], []
        s.ball, s.ball_v = np.array([L / 2, W / 2]), np.zeros(2)
        s.state, s.poss, s.carrier, s.flight, s.rk = "dead", -1, -1, None, None
        s.last_touch, s.last_ctrl_team = -1, None
        s.pending_pass, s.last_pass_to = None, {}
        s.spell = None
        s.last_why = None
        s.carry_streak = 0
        s.next_dec, s.protect_until = 0., 0.
        s.carry_tgt = s.ball.copy()
        s._reset_positions()
        s.tgt = s.pos.copy()
        s.tsm = s.pos.copy()
        s._kickoff(0, delay=2.)
        s.emit("kickoff", team=0)

    # ------------------------------------------------------------------ helpers
    def emit(s, type, player=None, team=None, pos=None, end=None, **kw):
        if player is not None and team is None:
            team = player // 11
        ev = dict(id=len(s.events), t=round(s.t, 1), period=s.period,
                  minute=int(s.t // 60) if s.period == 1 else int(45 + (s.t - s.h1) // 60), type=type)
        if team is not None:
            ev["team"] = int(team)
        if player is not None:
            ev["player"] = s.ids[player]
        if pos is not None:
            ev["x"], ev["y"] = round(float(pos[0]), 1), round(float(pos[1]), 1)
        if end is not None:
            ev["end_x"], ev["end_y"] = round(float(end[0]), 1), round(float(end[1]), 1)
        ev.update({k: (round(float(v), 3) if isinstance(v, (float, np.floating)) else v) for k, v in kw.items()})
        s.events.append(ev)
        return ev

    def _load(s, i, p, anchor):
        """Put player dict p into field slot i (start of match or substitution)."""
        s.ids[i], s.shirt[i], s.role[i] = p["id"], p["number"], ROLE_IDX[p["role"]]
        if anchor is not None:
            s.anchor[i] = [anchor[0] * L, anchor[1] * W]
        for a in ATTRS:
            s.attr[a][i] = p["attrs"][a]
        for k in _MULT:
            s.mod[k][i] = 1.
        for k in _ADD:
            s.mod[k][i] = 0.
        for tr in p["traits"]:
            for k, v in TRAITS[tr][1].items():
                if k in ("vision", "aerial", "finishing"):
                    s.attr[k][i] = min(20., s.attr[k][i] + v)
                elif k in _MULT:
                    s.mod[k][i] *= v
                else:
                    s.mod[k][i] += v
        s.vmax[i], s.acc[i] = 6.4 + .17 * s.attr["pace"][i], 2.8 + .1 * s.attr["accel"][i]

    def _reset_positions(s):
        a = s.anchor.copy()
        a[:, 0] = np.minimum(a[:, 0], L / 2 - 2)
        s.pos = np.vstack([a[:11], _tf(a[11:])])
        s.vel[:] = 0

    def _opp(s, t):
        return slice(11 * (1 - t), 11 * (1 - t) + 11)

    def _own(s, t):
        return slice(11 * t, 11 * t + 11)

    def _xg(s, C, defenders, header=False):
        dx, dy = L - C[0], C[1] - W / 2
        d = np.hypot(dx, dy)
        ang = abs(np.arctan2(W / 2 + GOAL_HALF - C[1], dx) - np.arctan2(W / 2 - GOAL_HALF - C[1], dx))
        z = -.6 - .14 * d + 2.2 * ang - (.6 if header else 0)
        a, b = np.array([L, W / 2 - GOAL_HALF]), np.array([L, W / 2 + GOAL_HALF])
        cr = lambda p, q, r: (q[0] - p[0]) * (r[1] - p[1]) - (q[1] - p[1]) * (r[0] - p[0])
        nb = 0
        for o in defenders:
            if np.hypot(*(o - C)) < 2.0:
                nb += 1
                continue
            s1, s2, s3 = cr(C, a, o), cr(a, b, o), cr(b, C, o)
            if (s1 >= 0 and s2 >= 0 and s3 >= 0) or (s1 <= 0 and s2 <= 0 and s3 <= 0):
                nb += 1
        odds = np.exp(z) * .72 ** nb
        return float(odds / (1 + odds)), nb

    # ------------------------------------------------------------------ set pieces / restarts
    def _kickoff(s, team, delay):
        own = s._own(team)
        sts = [11 * team + i for i in range(11) if s.role[own][i] == ROLE_IDX["ST"]] or [11 * team + 10]
        s._set_dead("kickoff", team, np.array([L / 2, W / 2]), delay, taker=sts[0])

    def _set_dead(s, kind, team, pos, delay, taker=None):
        if taker is None:
            own = np.arange(11 * team, 11 * team + 11)
            if kind == "goal_kick":
                taker = 11 * team
            elif kind == "penalty":
                taker = own[np.argmax(s.attr["finishing"][own])]
            else:
                cand = own[(s.role[own] > 0) & ~s.off[own]]
                if kind == "corner":
                    cand = own[np.isin(s.role[own], [ROLE_IDX["WG"], ROLE_IDX["FB"], ROLE_IDX["AM"]]) & ~s.off[own]]
                    cand = cand if len(cand) else own[(s.role[own] > 0) & ~s.off[own]]
                taker = cand[np.argmin(np.linalg.norm(s.pos[cand] - pos, axis=1))]
        s.state, s.poss, s.carrier, s.flight = "dead", team, -1, None
        s.ball, s.ball_v = np.array(pos, float), np.zeros(2)
        s.dead = dict(kind=kind, team=team, pos=np.array(pos, float), taker=int(taker), ready=s.t + delay)

    def _out(s, b):
        x, y = b
        tl = s.last_touch // 11 if s.last_touch >= 0 else 0
        if s.pending_pass is not None:
            s.pending_pass["outcome"], s.pending_pass = "out", None
        if y < 0 or y > W:
            kind, team, pos, d = "throw_in", 1 - tl, np.array([np.clip(x, 1, L - 1), 0. if y < 0 else W]), 5 + s.rng.random() * 3
        else:
            owner = 0 if x < 0 else 1
            if tl == owner:
                kind, team, pos, d = "corner", 1 - owner, np.array([0. if x < 0 else L, 0. if y < W / 2 else W]), 10 + s.rng.random() * 4
            else:
                kind, team, pos, d = "goal_kick", owner, np.array([5.5 if x < 0 else L - 5.5, W / 2]), 8 + s.rng.random() * 4
        s.emit(kind, team=team, pos=pos)
        s._end_spell()
        s._set_dead(kind, team, pos, d)

    # ------------------------------------------------------------------ main loop
    def run(s):
        while not s.done:
            s._step()
        return s.result()

    def _step(s):
        if s.tick % 5 == 0:
            for t in (0, 1):
                movement.refresh_marking(s, t)
        tg, urg = np.empty((22, 2)), np.zeros(22, bool)
        for t in (0, 1):
            tg[s._own(t)], urg[s._own(t)] = movement.team_targets(s, t)
        if s.state == "held":
            tg[s.carrier], urg[s.carrier] = s.carry_tgt, True
        elif s.state == "dead":
            tg[s.dead["taker"]], urg[s.dead["taker"]] = s.dead["pos"], True
        s.tgt = tg
        s._steer(tg, urg)
        {"held": s._held, "flight": s._flight, "loose": s._loose, "dead": s._dead_step}[s.state]()
        if s.tick % 150 == 0 and s.t > 120:
            s._manage()
        if s.state == "dead":
            s._do_subs()
        s._record()
        s.t += s.dt
        s.tick += 1
        s._clock()

    def _clock(s):
        if s.period == 1 and s.t >= s.h1 and (s.state == "dead" or s.t >= s.h1 + 40):
            s.emit("half_end")
            s.period = 2
            s.stam = np.minimum(1, s.stam + .08)
            s._end_spell()
            s._reset_positions()
            s.tsm = s.pos.copy()
            s._kickoff(1, delay=3.)
            s.emit("kickoff", team=1)
        elif s.period == 2 and s.t >= s.end_t and (s.state == "dead" or s.t >= s.end_t + 40):
            s.emit("full_time", home=s.score[0], away=s.score[1])
            s.done = True

    def _steer(s, tg, urg):
        dt = s.dt
        tau = .45 - .014 * s.attr["positioning"]
        a = np.where(urg, 1., 1 - np.exp(-dt / tau))[:, None]
        s.tsm = s.tsm + (tg - s.tsm) * a
        d = s.tsm - s.pos
        dist = np.linalg.norm(d, axis=1)
        vmax = s.vmax * (1 - .15 * (1 - s.stam))
        energy = s.mod["cruise"] * movement.CRUISE[s.role]  # role/trait work rate: how much ground a player covers
        cap = np.where(urg, vmax, np.minimum(vmax, (3.7 + .07 * s.attr["work_rate"]) * energy))
        if s.state == "held":
            cap[s.carrier] = min(cap[s.carrier], .85 * vmax[s.carrier])
        vdes = d / np.maximum(dist, 1e-6)[:, None] * np.minimum(cap, np.maximum(dist - .4, 0) * 1.1 * np.where(urg, 1., energy))[:, None]
        dv = vdes - s.vel
        n = np.linalg.norm(dv, axis=1)
        s.vel += dv * np.minimum(1, s.acc * dt / np.maximum(n, 1e-6))[:, None]
        s.pos = s.pos + s.vel * dt
        r = s.pos[:, None] - s.pos[None]
        dd = np.linalg.norm(r, axis=2) + np.eye(22) * 99
        s.pos += (r / dd[..., None] * ((1.6 - dd).clip(0))[..., None]).sum(1) * .35
        s.pos = np.clip(s.pos, [-1, -1], [L + 1, W + 1])
        if s.off.any():
            s.pos[s.off], s.vel[s.off] = PARK[s.off], 0
        sp = np.linalg.norm(s.vel, axis=1)
        s.stam = np.clip(s.stam - dt * (1.5e-5 + 6e-4 * (sp / s.vmax) ** 3) * (1.35 - .035 * s.attr["stamina"]), 0, 1)

    def _record(s):
        s.frames.append(np.concatenate([[s.t, *s.ball, s.carrier, -1 if s.last_ctrl_team is None else s.last_ctrl_team],
                                        s.pos.ravel()]))

    # ------------------------------------------------------------------ possession
    def _dwell(s, i):
        t = i // 11
        pr = np.clip(1 - np.linalg.norm(s.pos[s._opp(t)] - s.pos[i], axis=1).min() / 5, 0, 1)
        return (TUNE["dwell_base"] + s.rng.gamma(TUNE["dwell_shape"], TUNE["dwell_scale"]) * (1 - .6 * pr)) * (1.25 - .025 * s.attr["composure"][i])

    def _gain_control(s, i, at):
        t = i // 11
        if s.pending_pass is not None:
            pp, s.pending_pass = s.pending_pass, None
            same = pp["team"] == t
            pp["outcome"] = "complete" if same else "intercepted"
            if same:
                s.last_pass_to[t] = (pp["player"], i, s.t)
            elif s.state == "flight":
                s.emit("interception", player=i, pos=at)
        if s.last_ctrl_team is not None and s.last_ctrl_team != t:
            s.emit("possession_change", team=t, pos=at)
            s.trans_until[1 - t] = s.t + 4
        s.last_ctrl_team = t
        s.carry_streak = 0
        s.state, s.poss, s.carrier, s.flight = "held", t, i, None
        s.ball, s.ball_v = np.array(at, float), s.vel[i].copy()
        s.last_touch = i
        s.next_dec = s.t + s._dwell(i)
        s.protect_until = s.t + .6
        s.carry_tgt = s.pos[i] + np.array([1., 0.] if t == 0 else [-1., 0.]) * 5
        s.spell = (s.t, s.pos[i].copy(), i)

    def _end_spell(s):
        if s.spell is not None:
            t0, p0, i = s.spell
            d = np.linalg.norm(s.pos[i] - p0)
            if d > 4:
                s.emit("carry", player=i, pos=p0, end=s.pos[i], distance=d, duration=s.t - t0)
            s.spell = None

    def _loose_ball(s, v):
        s._end_spell()
        s.state, s.poss, s.carrier, s.flight = "loose", -1, -1, None
        s.ball_v = np.array(v, float)

    # ------------------------------------------------------------------ held
    def _held(s):
        c = s.carrier
        t = c // 11
        v = s.vel[c]
        face = v / np.linalg.norm(v) if np.linalg.norm(v) > .5 else np.array([1., 0.] if t == 0 else [-1., 0.])
        s.ball, s.ball_v = s.pos[c] + face * .5, v.copy()
        opp = np.arange(11 * (1 - t), 11 * (1 - t) + 11)
        d = np.linalg.norm(s.pos[opp] - s.pos[c], axis=1)
        for k in np.nonzero(d < 3.0)[0]:
            if s.t - s.last_press[opp[k]] > 1.0:
                s.emit("pressure", player=opp[k], pos=s.pos[opp[k]], target=s.ids[c])
            s.last_press[opp[k]] = s.t
        if s.t >= s.protect_until:
            xt = s.pos[c][0] if t == 0 else L - s.pos[c][0]
            danger = np.clip((xt - .65 * L) / (.3 * L), 0, 1)
            att = np.nonzero(s.rng.random(11) < TUNE["tackle_rate"] * s.mod["tackle"][opp] * (1 + 1.5 * danger) * np.clip(1 - d / 2.2, 0, 1) * s.dt)[0]
            if len(att):
                o = opp[att[np.argmin(d[att])]]
                a = s.attr
                p_win = sig(.22 * ((a["tackling"][o] + .3 * a["strength"][o]) - (a["dribbling"][c] + .3 * a["strength"][c])) - .1)
                s.protect_until = s.t + .7
                if s.rng.random() < p_win:
                    if s.rng.random() < (.4 + .2 * (1 - a["tackling"][o] / 20)) * s.mod["foul"][o]:
                        s._foul(o, c)
                    else:
                        s.emit("tackle", player=o, pos=s.pos[c], target=s.ids[c], outcome="won")
                        s.block_until[c] = s.t + .8
                        s.last_touch = o
                        s._loose_ball(_unit(s.vel[o] + s.rng.normal(0, 1, 2)) * 2.5)
                    return
                s.emit("tackle", player=o, pos=s.pos[c], target=s.ids[c], outcome="lost")
        if s.t >= s.next_dec:
            s._decide()

    def _foul(s, o, c):
        t = c // 11
        s.emit("foul", player=o, pos=s.pos[c], target=s.ids[c])
        u = s.rng.random()
        if u < .005 and s.role[o] != 0:
            s.emit("card", player=o, card="red")
            s._send_off(o)
        elif u < .13:
            s.yellow[o] += 1
            if s.yellow[o] >= 2 and s.role[o] != 0:
                s.emit("card", player=o, card="second_yellow")
                s._send_off(o)
            else:
                s.emit("card", player=o, card="yellow")
        p = s.pos[c].copy()
        pt = _tf(p) if t else p
        s._end_spell()
        if pt[0] > L - 16.5 and abs(pt[1] - W / 2) < 20.16:
            spot = np.array([L - 11, W / 2])
            s.emit("penalty", team=t, pos=_tf(spot) if t else spot)
            s._set_dead("penalty", t, _tf(spot) if t else spot, 18)
        else:
            s.emit("free_kick", team=t, pos=p)
            s._set_dead("free_kick", t, p, 6 + s.rng.random() * 4)

    # ------------------------------------------------------------------ decision
    def _decide(s):
        c = s.carrier
        t = c // 11
        ci = c - 11 * t
        rk, s.rk = s.rk, None
        tc, a = s.tact[t], s.attr
        f = _tf if t else (lambda x: x)
        fv = (lambda v: -v) if t else (lambda v: v)
        P, O = f(s.pos[s._own(t)]), f(s.pos[s._opp(t)])
        Vp = fv(s.vel[s._own(t)])
        C = P[ci]
        dn = np.linalg.norm(O - C, axis=1)
        dnear = dn.min()
        pr = float(np.clip(1 - dnear / 5, 0, 1))
        Vf = lambda p: np.exp(-np.hypot(L - p[..., 0], W / 2 - p[..., 1]) / 22)
        VC = Vf(C)
        prog_w = (TUNE["prog_base"] + TUNE["prog_dir"] * tc.directness) * s.mod["prog"][c]
        if rk == "penalty":
            return s._shoot(c, C, xg=.76, kind="penalty", nb=0)
        if rk == "corner":
            return s._corner(c, f)
        U, K, X = [], [], []
        # -- passes
        mates = np.array([i for i in range(11) if i != ci])
        hv = fv(s.vel[c])
        face = hv / np.linalg.norm(hv) if np.linalg.norm(hv) > .5 else np.array([1., 0.])
        dv = P[mates] - C
        dist = np.linalg.norm(dv, axis=1)
        cosang = dv @ face / np.maximum(dist, 1e-6)
        vis = (dist > 3.5) & (dist < 18 + 2.2 * a["vision"][c]) & (cosang > np.cos(np.radians(min(180, 100 + 4 * a["vision"][c]))))
        if rk == "throw_in":
            vis &= dist < 28
        if rk == "goal_kick":
            vis &= dist > 8
        if not (C[0] < .45 * L or pr > .4):
            vis &= s.role[11 * t + mates] != 0
        vis &= ~s.off[11 * t + mates]
        if vis.any():
            m_ = mates[vis]
            Pm, Vm = P[m_], Vp[m_]
            dist = np.linalg.norm(Pm - C, axis=1)
            lofted = (dist > 30) & (s.rng.random(len(m_)) < .35 + .5 * tc.directness)
            spd = np.clip(11 + .3 * dist, 11, 24)
            tgt = np.clip(Pm + Vm * np.minimum(dist / spd, 1.5)[:, None] * .8, [1, 1], [L - 1, W - 1])
            ab = tgt - C
            d2 = np.linalg.norm(ab, axis=1)
            ap = O[None] - C
            sp = np.clip((ap * ab[:, None]).sum(2) / np.maximum((ab * ab).sum(1), 1e-6)[:, None], 0, 1)
            q = C + sp[..., None] * ab[:, None]
            dperp = np.linalg.norm(O[None] - q, axis=2)
            tb = sp * d2[:, None] / spd[:, None]
            to = np.maximum(dperp - 1, 0) / (s.vmax[s._opp(t)] * .85)[None] + .4
            pk = sig(4 * (tb - to) - 1.2) * (dperp < 14) * np.clip((.95 - sp) / .15, 0, 1)  # markers next to the receiver contest control (p_ctrl), not the lane
            p_int = np.where(lofted, .08, 1 - np.prod(1 - .9 * pk, axis=1))
            dn_r = np.linalg.norm(O[None] - tgt[:, None], axis=2).min(1)
            tech = (a["dribbling"][11 * t + m_] + a["composure"][11 * t + m_]) / 2
            p_ctrl = np.clip(.84 + .13 * sig(.7 * (dn_r - 2.2)) + .004 * (tech - 10), .35, .98)
            p_air = np.clip(.45 + .02 * (a["aerial"][11 * t + m_] - a["aerial"][s._opp(t)].mean()), .2, .8)
            p_ctrl = np.where(lofted, p_air, p_ctrl)
            sg = (.5 + .05 * d2) * (1.7 - .06 * a["passing"][c]) * (1 + .35 * pr) * np.where(lofted, 1.8, 1)
            p_acc = 1 - np.exp(-(2.5 ** 2) / (2 * sg ** 2))
            p_s = (1 - p_int) * p_acc * p_ctrl
            u = p_s * (prog_w * np.maximum(Vf(tgt) - VC, -.05) + TUNE["keep"]) - (1 - p_s) * TUNE["loss"] * s.mod["loss"][c] - .001 * d2
            for k in range(len(m_)):
                U.append(u[k]); K.append("pass"); X.append((11 * t + m_[k], tgt[k], bool(lofted[k]), spd[k], p_s[k]))
        # -- shot
        dg = np.hypot(L - C[0], W / 2 - C[1])
        if dg < 35 + s.mod["range"][c] and rk in (None, "free_kick"):
            xg, nb = s._xg(C, O[s.role[s._opp(t)] > 0])
            U.append((TUNE["shot_k"] + TUNE["shot_k_dir"] * tc.shoot) * s.mod["shot"][c] * xg * (.6 + .04 * a["finishing"][c]) - TUNE["shot_cost"]); K.append("shot"); X.append((xg, nb))
        # -- carry / clearance
        if rk is None:
            toward = _unit(np.array([L, W / 2]) - C)
            near = dn < 8
            away = (_unit(C - O[near]) * ((8 - dn[near]) / 8)[:, None]).sum(0) if near.any() else 0
            dirv = _unit(toward + .8 * away)
            cpos = np.clip(C + 7 * dirv, [1, 1], [L - 1, W - 1])
            rel = O - C
            ahead = rel @ dirv
            lat = np.abs(rel[:, 0] * dirv[1] - rel[:, 1] * dirv[0])
            blk = np.nonzero((ahead > .3) & (ahead < 5) & (lat < 2.2))[0]
            if len(blk):  # a defender stands in the way: carrying on means a take-on
                j = blk[np.argmin(ahead[blk])]
                o = 11 * (1 - t) + j
                p_beat = float(sig(.22 * ((a["dribbling"][c] + .3 * a["pace"][c]) - (a["tackling"][o] + .3 * a["pace"][o])) - .3)) + s.mod["takeon"][c]
                p_beat = min(p_beat, .97)
            else:
                j, o, p_beat = -1, -1, 1.
            p_keep = p_beat * (1 - .15 * np.exp(-dnear / 2.3))
            U.append(p_keep * (TUNE["carry_base"] + TUNE["carry_prog"] * prog_w * (Vf(cpos) - VC)) - (1 - p_keep) * .5 - TUNE["carry_streak"] * s.carry_streak + s.mod["carry"][c]); K.append("carry"); X.append((cpos, o, p_beat))
            if C[0] < .2 * L and dnear < 3.5:
                U.append(.2 + .35 * pr); K.append("clear"); X.append(np.array([C[0] + 40, np.clip(C[1] + s.rng.choice([-1, 1]) * 18, 5, W - 5)]))
        if not U:
            U, K, X = [0.], ["carry"], [(np.clip(C + 7 * np.array([1., 0.]), [1, 1], [L - 1, W - 1]), -1, 1.)]
        tau = .08 + .12 * (1 - a["composure"][c] / 20)
        U = np.array(U)
        pr_ = np.exp((U - U.max()) / tau)
        k = s.rng.choice(len(U), p=pr_ / pr_.sum())
        kind, x = K[k], X[k]
        order = np.argsort(-U)[:4]
        why = dict(pressure=round(pr, 2), nearest_opponent_m=round(float(dnear), 1), chosen=kind,
                   options=[dict(kind=K[o], utility=round(float(U[o]), 2),
                                 to=s.ids[X[o][0]] if K[o] == "pass" else None,
                                 p_success=round(float(X[o][4]), 2) if K[o] == "pass" else None,
                                 xg=round(float(X[o][0]), 2) if K[o] == "shot" else None) for o in order])
        s.last_why = why
        if kind != "carry":
            s.carry_streak = 0
        if kind == "pass":
            j, tgt_, lofted_, spd_, ps = x
            s._pass(c, j, f(tgt_), lofted_, spd_, ps, why=why)
        elif kind == "shot":
            s._shoot(c, C, xg=x[0], nb=x[1], kind="free_kick_shot" if rk == "free_kick" else "shot", why=why)
        elif kind == "clear":
            s._pass(c, None, f(x), True, 20., .5, kind="clearance")
        else:
            cpos, o, p_beat = x
            if o >= 0:
                won = s.rng.random() < p_beat
                s.emit("take_on", player=c, pos=s.pos[c], target=s.ids[o], outcome="won" if won else "lost", p_success=p_beat, why=why)
                if not won:
                    if s.rng.random() < .25:
                        return s._foul(o, c)
                    s.emit("tackle", player=o, pos=s.pos[c], target=s.ids[c], outcome="won")
                    s.block_until[c] = s.t + .8
                    s.last_touch = o
                    return s._loose_ball(_unit(s.vel[o] + s.rng.normal(0, 1, 2)) * 2.5)
                s.block_until[o] = s.t + 1.2
            s.carry_tgt = f(cpos)
            s.carry_streak += 1
            s.next_dec = s.t + s.rng.uniform(.8, 1.6)

    # ------------------------------------------------------------------ actions
    def _pass(s, c, rcv, aim, lofted, spd, est, kind="pass", sigma_scale=1.0, why=None):
        t = c // 11
        start = s.ball.copy()
        d = np.linalg.norm(aim - start)
        pr = np.clip(1 - np.linalg.norm(s.pos[s._opp(t)] - s.pos[c], axis=1).min() / 5, 0, 1)
        sg = (.5 + .05 * d) * (1.7 - .06 * s.attr["passing"][c]) * (1 + .35 * pr) * (1.8 if lofted else 1) * sigma_scale
        wide = min(aim[1], W - aim[1]) < 7
        end = aim + s.rng.normal(0, sg * (1 + TUNE["wide_error"] * wide), 2)
        dur = max(np.linalg.norm(end - start) / spd, .3)
        ev = s.emit(kind, player=c, pos=start, end=aim, length=d, speed=spd, lofted=lofted,
                    receiver=None if rcv is None else s.ids[rcv], p_success=est, difficulty=1 - est,
                    progressive=bool(abs((L if t == 0 else 0) - start[0]) - abs((L if t == 0 else 0) - aim[0]) >= 10), outcome=None, why=why)
        s._end_spell()
        s.state, s.carrier = "flight", -1
        s.flight = dict(start=start, end=end, aim=aim, t0=s.t, dur=dur, speed=spd, kind="lofted" if lofted else "ground",
                        team=t, passer=c, rcv=-1 if rcv is None else rcv)
        s.pending_pass = ev
        s.ball_v = (end - start) / dur
        s.last_touch = c
        s.block_until[c] = s.t + .6

    def _corner(s, c, f):
        t = c // 11
        att = [i for i in range(11) if s.role[11 * t + i] in (ROLE_IDX["ST"], ROLE_IDX["CB"], ROLE_IDX["AM"], ROLE_IDX["CM"])]
        P = f(s.pos[s._own(t)])
        box = [i for i in att if P[i][0] > L - 22 and abs(P[i][1] - W / 2) < 16 and 11 * t + i != c]
        if not box:
            box = [i for i in range(1, 11) if 11 * t + i != c]
        j = max(box, key=lambda i: s.attr["aerial"][11 * t + i] - .15 * np.linalg.norm(P[i] - np.array([L - 9, W / 2])))
        s._pass(c, 11 * t + j, f(P[j]), True, 19., .35, kind="cross", sigma_scale=.8)

    def _shoot(s, c, C, xg, nb, kind="shot", why=None):
        t = c // 11
        a = s.attr
        f = _tf if t else (lambda x: x)
        fin, gk = a["finishing"][c], a["gk"][s._opp(t)][0]
        odds = xg / (1 - xg + 1e-9) * np.exp(.05 * (fin - 10) + .02 * (a["composure"][c] - 10)) * np.exp(-.05 * (gk - 10))
        p_goal = float(odds / (1 + odds)) if kind != "penalty" else .76 * (.9 + .01 * fin) * (1.1 - .01 * gk)
        u = s.rng.random()
        if u < p_goal:
            res = "goal"
        else:
            r = s.rng.random()
            p_block = 0 if kind == "penalty" else min(.5, .12 + .12 * nb)
            p_off = np.clip(.55 - 1.0 * xg - .01 * (fin - 10), .2, .6)
            res = "blocked" if r < p_block else ("off_target" if r < p_block + p_off else "saved")
        start = s.ball.copy()
        wy = s.rng.uniform(-3.2, 3.2)
        endt = {"goal": [L + .5, W / 2 + wy], "saved": [L - .5, W / 2 + wy],
                "off_target": [L + .5, W / 2 + s.rng.choice([-1, 1]) * s.rng.uniform(4, 8)],
                "blocked": list(C + _unit(np.array([L, W / 2]) - C) * s.rng.uniform(1.5, 3))}[res]
        end = f(np.array(endt))
        spd = float(np.clip(20 + .5 * fin + s.rng.normal(0, 2), 16, 33))
        ev = s.emit("shot", player=c, pos=start, end=end, xg=xg, speed=spd, outcome=res, kind=kind, why=why,
                    assist=None if t not in s.last_pass_to or s.t - s.last_pass_to[t][2] > 8 or s.last_pass_to[t][1] != c else s.last_pass_to[t][0])
        s._end_spell()
        if s.pending_pass is not None:
            s.pending_pass = None
        s.state, s.carrier = "flight", -1
        s.flight = dict(start=start, end=end, aim=end, t0=s.t, dur=max(np.linalg.norm(end - start) / spd, .25), speed=spd,
                        kind="shot", team=t, passer=c, rcv=-1, res=res, ev=ev)
        s.ball_v = (end - start) / s.flight["dur"]
        s.last_touch = c
        s.block_until[c] = s.t + .6

    # ------------------------------------------------------------------ manager: tactics, substitutions, red cards
    def _send_off(s, i):
        t = i // 11
        s.off[i] = True
        s.emit("sent_off", player=i, pos=s.pos[i], men_left=int(11 - s.off[s._own(t)].sum()))

    def _manage(s):
        """Every 30 s: shift each team's tactics for the score, time and numbers, like a manager would."""
        mins = (s.t if s.period == 1 else 2700 + (s.t - s.h1)) / 60
        late = float(np.clip((mins - 55) / 35, 0, 1))
        for t in (0, 1):
            gd = s.score[t] - s.score[1 - t]
            down = int(s.off[s._own(t)].sum() - s.off[s._opp(t)].sum())
            need = float(np.clip(-gd, 0, 3)) * .12 * (.15 + .85 * late)
            lead = float(np.clip(gd, 0, 3)) * .10 * late
            b, cur = s.base_tact[t], s.tact[t]
            goal = dict(press=b.press + need - lead - .15 * down, line_height=b.line_height + .8 * need - 1.2 * lead - .15 * down,
                        risk=b.risk + 1.2 * need - lead, directness=b.directness + need + .4 * lead, shoot=b.shoot + .5 * need,
                        compactness=b.compactness - .4 * need + .8 * lead + .15 * down, width=b.width - .1 * down)
            new = {k: float(np.clip(getattr(cur, k) + .4 * (v - getattr(cur, k)), 0, 1)) for k, v in goal.items()}
            last = s.last_tact_emit[t]
            if sum(abs(new[k] - last[k]) for k in new) > .2 and s.t - s.last_tact_t[t] > 240:
                reason = ("man down" if down > 0 else "man advantage" if down < 0 else "chasing the game" if gd < 0
                          else "protecting the lead" if gd > 0 else "reset")
                s.emit("tactic_change", team=t, reason=reason,
                       changes={k: [round(last[k], 2), round(new[k], 2)] for k in new if abs(new[k] - last[k]) > .02})
                s.last_tact_emit[t].update(new)
                s.last_tact_t[t] = s.t
            s.tact[t] = dataclasses.replace(cur, **new)

    def _do_subs(s):
        if s.period != 2:
            return
        mins = (s.t - s.h1) / 60 + 45
        for t in (0, 1):
            while s.windows[t] and mins >= 45 + s.windows[t][0][0] and s.subs[t] < 5:
                _, n = s.windows[t].pop(0)
                for _ in range(min(n, 5 - s.subs[t])):
                    if not s._substitute(t):
                        break

    def _substitute(s, t):
        own = np.arange(11 * t, 11 * t + 11)
        gd = s.score[t] - s.score[1 - t]
        late = s.t > s.h1 + 1800
        outf = [i for i in own if s.role[i] > 0 and not s.off[i]]
        bench = s.bench[t]
        if not outf or not bench:
            return False
        tired = lambda i: (1 - s.stam[i]) + .08 * s.yellow[i] + s.rng.normal(0, .03)
        att_r, def_r = {"ST", "WG", "AM"}, {"DM", "CM", "CB", "FB"}
        best = lambda roles: max((b for b in bench if b["role"] in roles), key=lambda b: b["overall"])
        if gd < 0 and any(b["role"] in ("ST", "WG") for b in bench):
            reason, on = "chasing the game", best(("ST", "WG"))
            pool = [i for i in outf if _ROLE_NAME[s.role[i]] in def_r] or outf
            return s._swap(t, max(pool, key=tired), on, reason)
        if gd > 0 and late and any(b["role"] in ("DM", "CB", "FB") for b in bench):
            reason, on = "protecting the lead", best(("DM", "CB", "FB"))
            pool = [i for i in outf if _ROLE_NAME[s.role[i]] in att_r] or outf
            return s._swap(t, max(pool, key=tired), on, reason)
        off_i = max(outf, key=tired)
        name = _ROLE_NAME[s.role[off_i]]
        near = {"AM": ("CM", "WG"), "CM": ("DM", "AM"), "DM": ("CM", "CB"), "WG": ("ST", "AM", "FB"),
                "ST": ("WG",), "FB": ("CB", "WG"), "CB": ("FB", "DM")}
        pick = [b for b in bench if b["role"] == name] or [b for b in bench if b["role"] in near.get(name, ())] or [b for b in bench if b["role"] != "GK"]
        if not pick:
            return False
        return s._swap(t, off_i, max(pick, key=lambda b: b["overall"]), "fresh legs")

    def _swap(s, t, i, on, reason):
        anchor = None
        if on["role"] != _ROLE_NAME[s.role[i]]:  # tactical change: the newcomer takes up his natural line, same flank
            ay = s.anchor[i][1] / W
            anchor = (_PRESET_X[on["role"]], ay if on["role"] in ("FB", "WG") else float(np.clip(ay, .35, .65)))
        off_id = s.ids[i]
        s.bench[t].remove(on)
        s.subs[t] += 1
        s.emit("substitution", team=t, slot=int(i), off=off_id, on=on["id"], off_name=s.names[off_id], on_name=on["name"],
               reason=reason, off_stamina=round(float(s.stam[i]), 2), on_role=on["role"], pos=s.pos[i])
        s._load(i, on, anchor)
        s.stam[i], s.yellow[i], s.commit_until[i], s.run_until[i] = 1., 0, 0., 0.
        return True

    # ------------------------------------------------------------------ flight
    def _claim_p(s, i, speed, t_team, at, receiver):
        a = s.attr
        opp = s._opp(i // 11)
        dn = np.linalg.norm(s.pos[opp] - at, axis=1).min()
        tech = (a["dribbling"][i] + a["composure"][i]) / 2
        return float(np.clip(.96 - .012 * max(0, speed - 12) + .012 * (tech - 10) - .15 * np.clip(1 - dn / 4, 0, 1), .3, .99))

    def _flight(s):
        f = s.flight
        a0 = s.ball.copy()
        u = min(1., (s.t + s.dt - f["t0"]) / f["dur"])
        b = f["start"] + (f["end"] - f["start"]) * u
        s.ball, s.ball_v = b, (f["end"] - f["start"]) / f["dur"]
        if f["kind"] == "ground":
            ab = b - a0
            sp = np.clip(((s.pos - a0) @ ab) / max(ab @ ab, 1e-9), 0, 1)
            q = a0 + sp[:, None] * ab
            d = np.linalg.norm(s.pos - q, axis=1)
            ok = (d < 1.0) & (s.block_until <= s.t)
            if f["rcv"] >= 0:  # the intended receiver can stretch further for it
                ok[f["rcv"]] = d[f["rcv"]] < 1.6 and s.block_until[f["rcv"]] <= s.t
            order = list(np.argsort(sp))
            r = f["rcv"]
            if r >= 0 and ok[r] and sp[r] > .6:  # receiver gets first touch over a marker standing next to him
                order.remove(r)
                order.insert(0, r)
            for i in order:
                if not ok[i]:
                    continue
                mine = i // 11 == f["team"]
                if mine and i != f["rcv"]:
                    continue
                if mine:
                    p = s._claim_p(i, f["speed"], f["team"], q[i], True)
                else:
                    ar = s.attr
                    p = float(np.clip(.4 + .02 * (ar["positioning"][i] - 10) + .01 * (ar["tackling"][i] - 10) - .02 * (f["speed"] - 12), .15, .9)) * float(np.clip((s.t - f["t0"]) / .3, .35, 1))
                if s.rng.random() < p:
                    s.ball = q[i]
                    return s._gain_control(i, q[i])
                if mine:  # first touch lost
                    s.block_until[i] = s.t + .5
                    if s.pending_pass is not None:
                        s.pending_pass["outcome"], s.pending_pass = "incomplete", None
                    return s._loose_ball(_unit(ab + s.rng.normal(0, .8, 2)) * s.rng.uniform(2, 5))
                s.block_until[i] = s.t + .4
                if s.rng.random() < .35:
                    if s.pending_pass is not None:
                        s.pending_pass["outcome"], s.pending_pass = "deflected", None
                    return s._loose_ball(_unit(ab + s.rng.normal(0, 1.0, 2)) * f["speed"] * .35)
        if not (0 <= b[0] <= L and 0 <= b[1] <= W) and f["kind"] != "shot":
            return s._out(b)
        if u >= 1:
            s._arrive(f)

    def _arrive(s, f):
        end = f["end"]
        if f["kind"] == "shot":
            return s._shot_end(f)
        if f["kind"] == "lofted":
            return s._aerial(f)
        s._loose_ball(_unit(f["end"] - f["start"]) * f["speed"] * .5)

    def _aerial(s, f):
        pt = f["end"]
        d = np.linalg.norm(s.pos - pt, axis=1)
        ok = (d < 5) & (s.block_until <= s.t)
        if not ok.any():
            return s._loose_ball(s.rng.normal(0, 1.5, 2))
        a = s.attr
        w = (a["aerial"] + .5 * a["strength"] + .5 * a["positioning"]) * np.exp(-d / 3.5) * ok
        w[f["rcv"]] *= 1.2 if f["rcv"] >= 0 else 1
        i = int(s.rng.choice(22, p=w / w.sum()))
        t = i // 11
        s.ball = pt.copy()
        pt_t = _tf(pt) if t else pt
        if t == f["team"]:
            if pt_t[0] > .7 * L and abs(pt_t[1] - W / 2) < 18 and s.rng.random() < .5:
                s._gain_control(i, pt)
                C = pt_t
                O = _tf(s.pos[s._opp(t)]) if t else s.pos[s._opp(t)]
                xg, nb = s._xg(C, O[s.role[s._opp(t)] > 0], header=True)
                s.ball = pt.copy()
                return s._shoot(i, C, xg * .9, nb, kind="header")
        elif s.rng.random() < .45:  # defender heads it clear
            s.emit("clearance", player=i, pos=pt)
            s.pending_pass = None if s.pending_pass is None else s.pending_pass
            if s.pending_pass is not None:
                s.pending_pass["outcome"], s.pending_pass = "intercepted", None
            s.last_touch = i
            tow = np.array([L / 2, W / 2]) - pt
            return s._loose_ball(_unit(tow + s.rng.normal(0, .8, 2)) * 8)
        if s.rng.random() < .75:
            s._gain_control(i, pt)
        else:
            s.last_touch = i
            if s.pending_pass is not None:
                s.pending_pass["outcome"], s.pending_pass = "incomplete", None
            s._loose_ball(s.rng.normal(0, 2, 2))

    def _shot_end(s, f):
        res, c = f["res"], f["passer"]
        t = c // 11
        opp0 = 11 * (1 - t)
        if res == "goal":
            s.score[t] += 1
            s.emit("goal", player=c, pos=f["start"], xg=f["ev"]["xg"], home=s.score[0], away=s.score[1],
                   assist=f["ev"].get("assist"))
            s._end_spell()
            s._kickoff(1 - t, delay=28.)
        elif res == "saved":
            s.emit("save", player=opp0, pos=f["end"])
            s.last_touch = c
            u = s.rng.random()
            if u < .5:
                s._gain_control(opp0, f["end"])
            elif u < 1.0:
                s.emit("corner", team=t, pos=np.array([L if t == 0 else 0., 0. if f["end"][1] < W / 2 else W]))
                s._set_dead("corner", t, np.array([L if t == 0 else 0., 0. if f["end"][1] < W / 2 else W]), 12)
            else:
                s._loose_ball(s.rng.normal(0, 3, 2))
        elif res == "off_target":
            s.last_touch = c
            s.ball = f["end"].copy()
            s._out(f["end"])
        else:  # blocked: sometimes behind for a corner
            s.last_touch = s.rng.choice(np.arange(11 * (1 - t), 11 * (1 - t) + 11)) if s.rng.random() < .7 else c
            if s.rng.random() < .4:
                cp = np.array([L if t == 0 else 0., 0. if f["start"][1] < W / 2 else W])
                s.emit("corner", team=t, pos=cp)
                s._end_spell()
                s._set_dead("corner", t, cp, 12)
            else:
                s._loose_ball(s.rng.normal(0, 4, 2))

    # ------------------------------------------------------------------ loose ball
    def _loose(s):
        sp = np.linalg.norm(s.ball_v)
        a0 = s.ball.copy()
        nsp = max(0., sp - 2.2 * s.dt)
        s.ball_v = s.ball_v * (nsp / sp) if sp > 1e-6 else s.ball_v
        b = a0 + s.ball_v * s.dt
        s.ball = b
        if not (0 <= b[0] <= L and 0 <= b[1] <= W):
            return s._out(b)
        ab = b - a0
        spn = np.clip(((s.pos - a0) @ ab) / max(ab @ ab, 1e-9), 0, 1) if ab @ ab > 1e-9 else np.zeros(22)
        q = a0 + spn[:, None] * ab
        d = np.linalg.norm(s.pos - q, axis=1)
        for i in np.argsort(d / (s.vmax * .5 + 1)):
            if d[i] > 1.3 or s.block_until[i] > s.t:
                continue
            if s.rng.random() < s._claim_p(i, nsp, i // 11, b, False) * .9:
                return s._gain_control(i, b)
            s.block_until[i] = s.t + .4

    # ------------------------------------------------------------------ dead ball
    def _dead_step(s):
        d = s.dead
        s.ball, s.ball_v = d["pos"].copy(), np.zeros(2)
        k = d["taker"]
        if s.t >= d["ready"] and np.linalg.norm(s.pos[k] - d["pos"]) < 1.3:
            s.last_ctrl_team = d["team"] if s.last_ctrl_team is None else s.last_ctrl_team
            s._gain_control(k, d["pos"])
            s.rk = d["kind"] if d["kind"] != "kickoff" else None
            s.next_dec = s.t + .4

    # ------------------------------------------------------------------ output
    def result(s):
        fr = np.round(np.array(s.frames), 1)
        return dict(
            meta=dict(seed=s.seed, dt=s.dt, pitch=[L, W], home=team_meta(s.teams[0]), away=team_meta(s.teams[1]),
                      score=s.score, half_time=s.h1, duration=round(s.t, 1),
                      frame_cols=["t", "ball_x", "ball_y", "carrier", "poss"] + [f"{c}{i}" for i in range(22) for c in "xy"]),
            events=s.events, frames=fr.tolist())
