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
import collections
import dataclasses
import numpy as np
from scipy.optimize import linear_sum_assignment
from .teams import L, W, ATTRS, ROLE_IDX, TRAITS, FORMATIONS, team_meta, make_match_info
from . import movement
from .movement import _tf, _unit, nrm, clip

DT = 0.2
# Single tuning table (calibrated by scripts/calibrate.py against real-football norms).
TUNE = dict(risk_pow=1.6, first_touch=3., wide_v=.35, ctrl_marked=.45, shoot_hold=.5, shoot_slope=2.5, shoot_ref=.10, shoot_press=.8, adv_lin=.6, pack=.04, shoot_min=.12, react=1.6, patience=.6, pass_sigma=1.0, intercept=0.429, shot_mid=1.82, usage=0.4, fk_bonus=0.05, long_bonus=0.523, prog_base=1.2, prog_dir=0.63, keep=0.455, loss=1.0, cross_v=1.3, gk_long=.5, box_foul=.04, home_edge=1.7, shot_cost=1.3, shot_k=3.15, shot_k_dir=5.0,
            carry_base=.3, carry_prog=.2, carry_streak=.3, dwell_base=0.9, dwell_shape=2.4, dwell_scale=1.61,
            tackle_rate=0.22, wide_error=3.2, off_miss=.2, off_mult=.25)
sig = lambda x: 1 / (1 + np.exp(-x))
GOAL_HALF = 3.66
_MULT = ("shot", "runs", "follow", "prog", "loss", "cruise", "commit", "foul", "tackle")
_ADD = ("range", "carry", "takeon", "sweep", "fk")
CARRY_ROLE = np.array([0., 0., .1, 0., .05, .25, .15, .05])  # wingers and attacking midfielders take defenders on more (GK CB FB DM CM WG AM ST)
LANE = np.array([.5, .6, .9, .6, .5, .85, .4, .25])  # how much of his lateral position a carrier keeps (GK CB FB DM CM WG AM ST)
SHOT_PROP = np.array([.2, .5, .7, .8, 1., 1., 1.15, 1.3])  # how readily each role shoots (GK CB FB DM CM WG AM ST)
ROLE_SHOT = np.array([0., .6, .7, .9, 1.15, 1.1, 1.25, .72])  # shot appetite by role (GK CB FB DM CM WG AM ST)
# manager's formation switches (11 players) and the ten-man shape after a red card
ATTACK = {"4-4-2": "4-3-3", "4-5-1": "4-3-3", "4-1-4-1": "4-2-3-1", "5-3-2": "3-5-2", "5-4-1": "4-4-2", "3-5-2": "3-4-3", "4-2-3-1": "4-3-3", "4-3-3": "3-4-3"}
DEFEND = {"4-3-3": "4-5-1", "4-4-2": "4-5-1", "4-2-3-1": "4-1-4-1", "3-5-2": "5-3-2", "3-4-3": "5-4-1", "4-1-4-1": "5-4-1", "4-5-1": "5-4-1"}
TEN_MAN = {"4-4-2": "4-4-1", "4-3-3": "4-3-2", "4-2-3-1": "4-4-1", "3-5-2": "5-3-1", "5-3-2": "5-3-1", "4-5-1": "4-4-1", "3-4-3": "5-3-1", "5-4-1": "5-3-1", "4-1-4-1": "4-4-1"}
_ROLE_NEAR = {frozenset(p): c for p, c in {("CB", "FB"): 1.5, ("CB", "DM"): 2., ("FB", "WG"): 1.5, ("DM", "CM"): 1., ("CM", "AM"): 1., ("CM", "WG"): 1.8,
                                          ("AM", "WG"): 1., ("AM", "ST"): 1.5, ("WG", "ST"): 1.8, ("FB", "DM"): 3., ("FB", "CM"): 2.5}.items()}
_PRESET_X = {"GK": .03, "CB": .20, "FB": .22, "DM": .37, "CM": .43, "WG": .66, "AM": .57, "ST": .70}
_ROLE_NAME = {v: k for k, v in ROLE_IDX.items()}
PARK = np.array([[L / 2 + 6 * (i // 11), -40. - 2 * (i % 11)] for i in range(22)])  # distinct spots so parked players never coincide  # sent-off players wait off the pitch


class Match:
    def __init__(s, home, away, seed=0, dt=DT, info=None):
        s.rng = np.random.default_rng(seed)
        s.info = info or make_match_info(s.rng, home)
        s.ref = s.info["referee"]["strict"]                      # strict referees whistle and book more
        s.rain = 1.1 if s.info["weather"]["rain"] else 1.0      # wet ball: less accurate passing
        s.heat = 1 + .03 * max(0., s.info["weather"]["temp_c"] - 18)  # heat: faster fatigue
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
        s.cur = [None] * 22
        for i, p in enumerate(pl):
            s._load(i, p, p["anchor"])
        s.bench = [list(home["bench"]), list(away["bench"])]
        s.yellow, s.off = np.zeros(22, int), np.zeros(22, bool)
        s.subs = [0, 0]
        s.formation = [home["formation"], away["formation"]]
        s.last_form_t, s.forms_used, s.want_form = [-9999., -9999.], [0, 0], [None, None]
        s.windows = [[(s.rng.uniform(9, 16), 2), (s.rng.uniform(24, 30), 2), (s.rng.uniform(34, 40), 1)] for _ in (0, 1)]
        s.mark = [-np.ones(11, int), -np.ones(11, int)]
        s.run_until, s.run_cool = np.zeros(22), np.zeros(22)
        s.run_tgt = np.zeros((22, 2))
        s.commit_until = np.zeros(22)  # presser committed to a tackle until this time
        s.block_until = np.zeros(22)   # cannot claim the ball (just lost / just passed it)
        s.last_press = np.full(22, -9.)
        s.trans_until = [0., 0.]
        s.t, s.tick, s.period = 0., 0, 1
        s.h1, s.end_t = 1e9, 1e9      # set when added time is announced
        s.stop = [0., 0.]             # accumulated stoppage seconds per half (goals, subs, cards, injuries, VAR)
        s.added = [None, None]
        s.injured, s.pending_inj = np.zeros(22, bool), []
        s.ctrl_t = 0.
        s.first_time_ok = 1.0
        s.audit = None  # a list switches on decision auditing (scripts/logic_audit.py)
        s._tg = s._urg = None
        s.turnover, s.sched = None, []
        s.dead_log = []  # [start, end, kind] of every dead-ball spell
        s.idle_off, s.idle_until = np.zeros((22, 2)), np.zeros(22)  # players mill about while the ball is dead
        s.state_ticks = collections.Counter()
        s.phase = s.rng.uniform(0, 6.28, 22)
        s.recent = np.zeros(22)  # recent touches, decays; the ball gets spread around instead of feeding one man
        s.line_hist = [collections.deque(maxlen=4), collections.deque(maxlen=4)]  # the line as the passer last saw it (~.8 s ago)
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
        s.carry_dir, s.carry_speed = np.array([1., 0.]), 3.
        s.space_off, s.space_until = np.zeros((22, 2)), np.zeros(22)  # where each off-ball player has chosen to stand (offset from his zone) and until when
        s._reset_positions()
        s.tgt = s.pos.copy()
        s.tsm = s.pos.copy()
        s._kickoff(0, delay=2.)
        s.emit("match_info", **s.info)
        for t in (0, 1):
            tm = s.teams[t]
            s.emit("lineup", team=t, formation=tm["formation"], tactics=dataclasses.asdict(s.base_tact[t]),
                   players=[dict(id=p["id"], number=p["number"], label=p["label"], role=p["role"], star=p["star"], traits=p["traits"])
                            for p in tm["players"]],
                   bench=[dict(id=p["id"], number=p["number"], role=p["role"]) for p in tm["bench"]])
        s.emit("half_start")
        s.emit("kickoff", team=0)

    # ------------------------------------------------------------------ helpers
    def emit(s, type, player=None, team=None, pos=None, end=None, **kw):
        if player is not None and team is None:
            team = player // 11
        mn = int(s.t // 60) if s.period == 1 else int(45 + (s.t - s.h1) // 60)
        clock = f"45+{mn - 44}'" if s.period == 1 and mn >= 45 else f"90+{mn - 89}'" if s.period == 2 and mn >= 90 else f"{mn + 1}'"
        ev = dict(id=len(s.events), t=round(s.t, 1), period=s.period, minute=mn, clock=clock, type=type)
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
        if i < 11:  # home crowd: a small edge in the mental and technical game
            for k in ("passing", "dribbling", "finishing", "composure", "tackling", "positioning"):
                s.attr[k][i] = min(20., s.attr[k][i] + TUNE["home_edge"])
        s.vmax[i], s.acc[i] = 6.4 + .17 * s.attr["pace"][i], 2.8 + .1 * s.attr["accel"][i]
        s.cur[i] = p

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
        z = -2.08 - .07 * d + 1.89 * ang - (.2 if header else 0)
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
        if len(defenders):  # a defender breathing down his neck: closing-down matters as well as bodies in the way
            odds *= .75 + .25 * float(min(1., np.min(np.hypot(*(np.asarray(defenders) - C).T)) / 3.))
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
                    cand = own[np.isin(s.role[own], [ROLE_IDX["WG"], ROLE_IDX["FB"], ROLE_IDX["AM"], ROLE_IDX["CM"]]) & ~s.off[own]]
                    cand = cand if len(cand) else own[(s.role[own] > 0) & ~s.off[own]]
                if kind in ("corner", "free_kick"):  # best deliverer among the three nearest
                    near3 = cand[np.argsort(nrm(s.pos[cand] - pos, axis=1))[:3]]
                    taker = near3[np.argmax(s.attr["passing"][near3] + .5 * s.attr["vision"][near3] + 3 * s.mod["fk"][near3])]
                else:
                    taker = cand[np.argmin(nrm(s.pos[cand] - pos, axis=1))]
        s.state, s.poss, s.carrier, s.flight = "dead", team, -1, None
        s.ball, s.ball_v = np.array(pos, float), np.zeros(2)
        s.dead = dict(kind=kind, team=team, pos=np.array(pos, float), taker=int(taker), ready=s.t + delay)
        if s.dead_log and s.dead_log[-1][1] is None:
            s.dead_log[-1][1] = s.t
        s.dead_log.append([s.t, None, kind])
        if kind == "corner":  # a short corner now and then: a teammate comes to the corner flag
            own = np.arange(11 * team, 11 * team + 11)
            helpers = [j for j in own if s.role[j] in (ROLE_IDX["FB"], ROLE_IDX["CM"], ROLE_IDX["WG"], ROLE_IDX["AM"]) and not s.off[j] and j != taker]
            s.dead["short"] = bool(helpers and s.rng.random() < .16)
            s.dead["sup"] = int(min(helpers, key=lambda j: nrm(s.pos[j] - pos))) if helpers else -1

    def _out(s, b):
        x, y = b
        tl = s.last_touch // 11 if s.last_touch >= 0 else 0
        if s.pending_pass is not None:
            s.pending_pass["outcome"], s.pending_pass = "out", None
        if y < 0 or y > W:
            kind, team, pos, d = "throw_in", 1 - tl, np.array([clip(x, 1, L - 1), 0. if y < 0 else W]), 14 + s.rng.random() * 10
        else:
            owner = 0 if x < 0 else 1
            if tl == owner:
                kind, team, pos, d = "corner", 1 - owner, np.array([0. if x < 0 else L, 0. if y < W / 2 else W]), 30 + s.rng.random() * 15
            else:
                kind, team, pos, d = "goal_kick", owner, np.array([5.5 if x < 0 else L - 5.5, W / 2]), 22 + s.rng.random() * 12
        s.emit("ball_out", pos=b, last_touch=s.ids[s.last_touch] if s.last_touch >= 0 else None, over="touchline" if (y < 0 or y > W) else "goal_line")
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
        if s.tick % 2 == 0 or s.state in ("flight", "loose") or s._tg is None:  # targets move slowly: recompute every other tick unless the ball is in the air or loose
            tg, urg = np.empty((22, 2)), np.zeros(22, bool)
            for t in (0, 1):
                tg[s._own(t)], urg[s._own(t)] = movement.team_targets(s, t)
            s._tg, s._urg = tg, urg
        else:
            tg, urg = s._tg.copy(), s._urg.copy()
        if s.state == "held":
            tg[s.carrier], urg[s.carrier] = clip(s.pos[s.carrier] + s.carry_dir * 12, [1., 1.], [L - 1, W - 1]), True  # always running somewhere, never parked
        elif s.state == "dead":
            tg[s.dead["taker"]], urg[s.dead["taker"]] = s.dead["pos"], True
        if s.state == "dead" and s.dead["kind"] not in ("kickoff", "penalty") and s.dead["ready"] - s.t > 1.5:
            due = s.idle_until <= s.t  # waiting players wander a few metres every few seconds: stretch, reposition, find a marker
            if due.any():
                sd = np.where(np.arange(22) // 11 == s.dead["team"], 3.0, 1.8)[due]  # the side with the restart looks for room, the other holds its shape
                s.idle_off[due] = s.rng.normal(0, 1, (int(due.sum()), 2)) * sd[:, None]
                s.idle_until[due] = s.t + s.rng.uniform(4, 8, int(due.sum()))
            mill = ~s.off
            mill[[0, 11, s.dead["taker"]]] = False
            tg[mill] += s.idle_off[mill]
        s.tgt = tg
        s._steer(tg, urg)
        for t in (0, 1):
            o = s.pos[s._opp(t)]
            s.line_hist[t].append(float(np.sort(_tf(o) if t else o, axis=0)[-2, 0]))
        if s.state != "dead":
            on = ~s.off & ~s.injured
            rate = 1.2e-6 * (1 + 3 * (1 - s.stam)) * on  # non-contact injury hazard per second, higher when tired
            if s.rng.random() < s.dt * rate.sum():
                s._injury(int(s.rng.choice(22, p=rate / rate.sum())), "non-contact")
        if s.pending_inj and s.state in ("held", "dead"):
            s._stop_for_injury()
        {"held": s._held, "flight": s._flight, "loose": s._loose, "dead": s._dead_step}[s.state]()
        s.state_ticks[s.state] += 1
        s.recent *= np.exp(-s.dt / 25.)
        if s.tick % 150 == 0 and s.t > 120:
            s._manage()
        while s.sched and s.sched[0][0] <= s.t:
            _, kw = s.sched.pop(0)
            s.emit(kw.pop("type"), pos=s.pos[kw["player"]], **kw)
        if s.state == "dead":
            s._do_subs()
            s._do_formation()
        s._record()
        s.t += s.dt
        s.tick += 1
        s._clock()

    def _clock(s):
        if s.period == 1 and s.added[0] is None and s.t >= 2700:
            n = int(clip(np.ceil(.7 * (s.stop[0] + 25) / 60 + s.rng.normal(.4, .6)), 1, 7))
            s.added[0], s.h1 = n, 2700 + n * 60 + float(s.rng.uniform(0, 30))
            s.emit("added_time", minutes=n)
        elif s.period == 2 and s.added[1] is None and s.t >= s.h1 + 2700:
            n = int(clip(np.ceil(.7 * (s.stop[1] + 60) / 60 + s.rng.normal(.6, .8)), 2, 10))
            s.added[1], s.end_t = n, s.h1 + 2700 + n * 60 + float(s.rng.uniform(0, 30))
            s.emit("added_time", minutes=n)
        if s.period == 1 and s.t >= s.h1 and (s.state == "dead" or s.t >= s.h1 + 40):
            s.emit("half_end")
            s.period = 2
            s.emit("half_start")
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
        dist = nrm(d, axis=1)
        vmax = s.vmax * (1 - .15 * (1 - s.stam))
        energy = s.mod["cruise"] * movement.CRUISE[s.role]  # role/trait work rate: how much ground a player covers
        cap = np.where(urg, vmax, np.minimum(vmax, (4.3 + .07 * s.attr["work_rate"]) * energy))
        if s.state == "held":
            cap[s.carrier] = min(cap[s.carrier], s.carry_speed, .85 * vmax[s.carrier])
        vdes = d / np.maximum(dist, 1e-6)[:, None] * np.minimum(cap, np.maximum(dist - .4, 0) * 1.1 * np.where(urg, 1., energy))[:, None]
        dv = vdes - s.vel
        n = nrm(dv, axis=1)
        s.vel += dv * np.minimum(1, s.acc * dt / np.maximum(n, 1e-6))[:, None]
        s.pos = s.pos + s.vel * dt
        r = s.pos[:, None] - s.pos[None]
        dd = np.maximum(nrm(r, axis=2), 1e-3) + np.eye(22) * 99
        s.pos += (r / dd[..., None] * ((1.6 - dd).clip(0))[..., None]).sum(1) * .35
        s.pos = clip(s.pos, [-1, -1], [L + 1, W + 1])
        if s.off.any():
            s.pos[s.off], s.vel[s.off] = PARK[s.off], 0
        sp = nrm(s.vel, axis=1)
        s.stam = clip(s.stam - dt * (1.5e-5 + 6e-4 * (sp / s.vmax) ** 3) * (1.35 - .035 * s.attr["stamina"]) * s.heat, 0, 1)

    def _ball_z(s):
        """Ball height in metres: parabola for lofted passes and shots, zero otherwise."""
        f = s.flight
        if s.state != "flight" or f is None:
            return 0.
        u = float(clip((s.t - f["t0"]) / f["dur"], 0, 1))
        if f["kind"] == "lofted":
            return 4 * float(clip(.1 * nrm(f["end"] - f["start"]), 2, 12)) * u * (1 - u)
        if f["kind"] == "shot":
            return 4 * (3.5 if f.get("res") == "off_target" else 1.6) * u * (1 - u)
        return 0.

    def _record(s):
        s.frames.append(np.concatenate([[s.t, *s.ball, s.carrier, -1 if s.last_ctrl_team is None else s.last_ctrl_team],
                                        s.pos.ravel(), [s._ball_z()]]))

    # ------------------------------------------------------------------ possession
    def _dwell(s, i):
        """Seconds until this player decides: a reaction time that shrinks under pressure; a first-time ball when
        the chance is obvious (a shooting position) or the defender is on him and he is a good passer."""
        t = i // 11
        f = _tf if t else (lambda x: x)
        pr = clip(1 - nrm(s.pos[s._opp(t)] - s.pos[i], axis=1).min() / 5, 0, 1)
        react = TUNE["react"] * (1.4 - .015 * (s.attr["composure"][i] + s.attr["vision"][i])) * (1 - .55 * pr)
        xg_here, _ = s._xg(f(s.pos[i]), [])
        if xg_here > .08 and s.role[i] > 0:  # in front of goal: hit it first time
            return float(s.rng.uniform(.05, .2))
        if s.rng.random() < clip(.2 + .5 * pr + .02 * (s.attr["passing"][i] - 10), 0, .8) * s.first_time_ok:
            return float(s.rng.uniform(.05, .22))  # one touch
        return float(max(.12, react + s.rng.normal(0, .05)))

    def _gain_control(s, i, at):
        t = i // 11
        prev = s.last_touch
        if s.last_ctrl_team is not None and s.last_ctrl_team != t and prev >= 0 and prev // 11 != t:
            lt = prev // 11
            pos_t = _tf(np.asarray(at, float)) if lt else np.asarray(at, float)
            s.turnover = dict(t=s.t, team=t, loser=prev, pos=np.asarray(at, float).copy(), own_half=bool(pos_t[0] < .5 * L), used=False)
        if s.state == "loose" and s.last_ctrl_team != t:
            s.emit("ball_recovery", player=i, pos=at)
        if s.pending_pass is not None:
            pp, s.pending_pass = s.pending_pass, None
            same = pp["team"] == t
            pp["outcome"] = "complete" if same else "intercepted"
            if same:
                s.last_pass_to[t] = (pp["player"], i, s.t, pp)
            elif s.state == "flight" and s.role[i] == 0:  # the keeper collects it: inside the box a claim, outside a sweep
                own_box = (at[0] < 16.5 if t == 0 else at[0] > L - 16.5) and abs(at[1] - W / 2) < 20.16
                s.emit("keeper_claim" if own_box else "keeper_sweep", player=i, pos=at)
            elif s.state == "flight":
                if nrm(np.asarray(at) - s.flight["start"]) < 5:  # right next to the passer: a block, not a read of the pass
                    s.emit("block", player=i, pos=at, shooter=pp["player"], kind="pass")
                else:
                    s.emit("interception", player=i, pos=at)
        if s.last_ctrl_team is not None and s.last_ctrl_team != t:
            s.emit("possession_change", team=t, pos=at)
            s.trans_until[1 - t] = s.t + 4
        s.last_ctrl_team = t
        s.carry_streak = 0
        s.state, s.poss, s.carrier, s.flight = "held", t, i, None
        s.ball, s.ball_v = np.array(at, float), s.vel[i].copy()
        s.last_touch = i
        s.recent[i] += 1.
        s.ctrl_t = s.t
        s.next_dec = s.t + s._dwell(i)
        s.protect_until = s.t + (.15 if float(nrm(s.pos[s._opp(t)] - s.pos[i], axis=1).min()) < 1.6 else .6)  # a defender on the receiver contests him at once; with space he gets a moment
        s.carry_tgt = s.pos[i] + np.array([1., 0.] if t == 0 else [-1., 0.]) * 5
        s.carry_dir, s.carry_speed = np.array([1., 0.] if t == 0 else [-1., 0.]), 1.6 + 1.0 * float(clip(nrm(s.pos[s._opp(t)] - s.pos[i], axis=1).min() / 8, 0, 1))  # a jog with the ball, faster with space
        s.spell = (s.t, s.pos[i].copy(), i)

    def _end_spell(s):
        if s.spell is not None:
            t0, p0, i = s.spell
            d = nrm(s.pos[i] - p0)
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
        face = v / nrm(v) if nrm(v) > .5 else np.array([1., 0.] if t == 0 else [-1., 0.])
        s.ball, s.ball_v = s.pos[c] + face * .5, v.copy()
        opp = np.arange(11 * (1 - t), 11 * (1 - t) + 11)
        d = nrm(s.pos[opp] - s.pos[c], axis=1)
        for k in np.nonzero((d < 3.0) & (s.commit_until[opp] > s.t))[0]:  # a defender commits to closing the carrier down
            if s.t - s.last_press[opp[k]] > 2.5:
                s.emit("pressure", player=opp[k], pos=s.pos[opp[k]], target=s.ids[c])
                s.last_press[opp[k]] = s.t
        if s.t >= s.protect_until:
            xt = s.pos[c][0] if t == 0 else L - s.pos[c][0]
            danger = clip((xt - .65 * L) / (.3 * L), 0, 1)
            att = np.nonzero(s.rng.random(11) < TUNE["tackle_rate"] * s.mod["tackle"][opp] * (1 + 1.5 * danger) * (1 + TUNE["first_touch"] * np.exp(-(s.t - s.ctrl_t) / .5)) * clip(1 - d / 2.2, 0, 1) * s.dt)[0]  # the first touch is the moment the ball is most exposed
            if len(att):
                o = opp[att[np.argmin(d[att])]]
                a = s.attr
                p_win = sig(.22 * ((a["tackling"][o] + .3 * a["strength"][o]) - (a["dribbling"][c] + .3 * a["strength"][c])) - .1)
                s.protect_until = s.t + .7
                if s.rng.random() < p_win:
                    if s.rng.random() < (.4 + .2 * (1 - a["tackling"][o] / 20)) * s.mod["foul"][o] * (.75 + .5 * s.ref):
                        s._foul(o, c)
                    else:
                        s.emit("tackle", player=o, pos=s.pos[c], target=s.ids[c], outcome="won")
                        s.emit("dispossessed", player=c, pos=s.pos[c])
                        if s.rng.random() < .004:
                            s._injury(c, "tackle")
                        s.block_until[c] = s.t + .8
                        s.last_touch = o
                        s._loose_ball(_unit(s.vel[o] + s.rng.normal(0, 1, 2)) * 2.5)
                    return
                s.emit("tackle", player=o, pos=s.pos[c], target=s.ids[c], outcome="lost")
        if s.t >= s.next_dec:
            s._decide()

    def _yellow(s, o, reason):
        s.yellow[o] += 1
        s.stop[s.period - 1] += 10
        if s.yellow[o] >= 2 and s.role[o] != 0:
            s.emit("card", player=o, card="second_yellow", reason=reason)
            s._send_off(o)
        else:
            s.emit("card", player=o, card="yellow", reason=reason)

    def _penalty_spot(s, t):
        spot = np.array([L - 11, W / 2])
        return _tf(spot) if t else spot

    def _var_review(s, team, player, reason):
        dur = float(s.rng.uniform(70, 150))
        s.stop[s.period - 1] += dur
        s.emit("var_check", team=team, player=player, reason=reason, duration=dur)
        return dur

    def _foul(s, o, c):
        t = c // 11
        s.stop[s.period - 1] += 4
        pt0 = _tf(s.pos[c]) if t else s.pos[c]
        in_box = bool(pt0[0] > L - 16.5 and abs(pt0[1] - W / 2) < 20.16)
        if in_box and s.rng.random() > TUNE["box_foul"]:
            # contact in the box that is not given; now and then VAR sends the referee to the screen
            s.emit("tackle", player=o, pos=s.pos[c], target=s.ids[c], outcome="won")
            s.emit("dispossessed", player=c, pos=s.pos[c])
            if s.rng.random() < .03:
                dur = s._var_review(t, o, "missed foul in the box")
                s.emit("foul", player=o, pos=s.pos[c], target=s.ids[c], reason="tackle", var=True)
                s.emit("var_overturned", team=t, reason="penalty awarded after review")
                s._end_spell()
                s.emit("penalty", team=t, pos=s._penalty_spot(t))
                return s._set_dead("penalty", t, s._penalty_spot(t), 45 + dur)
            s.block_until[c] = s.t + .8
            s.last_touch = o
            return s._loose_ball(_unit(s.vel[o] + s.rng.normal(0, 1, 2)) * 2.5)
        reason = str(s.rng.choice(["tackle", "trip", "shirt_pull", "handball"], p=[.55, .2, .2, .05]))
        s.emit("foul", player=o, pos=s.pos[c], target=s.ids[c], reason=reason)
        u = s.rng.random()
        k = .7 + .6 * s.ref
        card = "red" if (u < .005 * k and s.role[o] != 0) else ("yellow" if u < .13 * k else None)
        if card == "red" and s.rng.random() < .3:  # VAR looks at the challenge
            s._var_review(o // 11, o, "possible red card")
            if s.rng.random() < .3:
                s.emit("var_overturned", team=o // 11, reason="red card downgraded to yellow")
                card = "yellow"
            else:
                s.emit("var_confirmed", team=o // 11, player=o)
        if card == "red":
            s.emit("card", player=o, card="red")
            s.stop[s.period - 1] += 40
            s._send_off(o)
        elif card == "yellow":
            s._yellow(o, reason)
        if s.rng.random() < .012:
            s._injury(c, "foul")
        p = s.pos[c].copy()
        if not in_box and card != "red" and s.state == "held" and s.carrier == c and s.rng.random() < .14:
            # the referee plays advantage: the fouled side keeps the ball and runs on
            s.emit("advantage", team=t, pos=p)
            s.block_until[o] = s.t + 2.
            s.protect_until = s.t + 2.
            return
        s._end_spell()
        if in_box:
            if s.rng.random() < .08:  # VAR checks the penalty award
                dur = s._var_review(t, o, "penalty review")
                if s.rng.random() < .25:
                    s.emit("var_overturned", team=t, reason="penalty cancelled")
                    return s._set_dead("drop_ball", 1 - t, clip(p, [1, 1], [L - 1, W - 1]), dur + 8)
                s.emit("var_confirmed", team=t, player=o)
            s.emit("penalty", team=t, pos=s._penalty_spot(t))
            s._set_dead("penalty", t, s._penalty_spot(t), 45)
        else:
            s.emit("free_kick", team=t, pos=p, direct=bool(np.hypot((L - pt0[0]), pt0[1] - W / 2) < 40), distance_to_goal=round(float(np.hypot((L - pt0[0]), pt0[1] - W / 2)), 1))
            s._set_dead("free_kick", t, p, 20 + s.rng.random() * 20)

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
        dn = nrm(O - C, axis=1)
        dnear = dn.min()
        pr = float(clip(1 - dnear / 5, 0, 1))
        Vf = lambda p: np.exp(-np.hypot(L - p[..., 0], W / 2 - p[..., 1]) / 22)
        Vw = lambda p: TUNE["wide_v"] * np.exp(-np.minimum(np.hypot(L - 8 - p[..., 0], W / 2 + 24 - p[..., 1]), np.hypot(L - 8 - p[..., 0], W / 2 - 24 - p[..., 1])) / 14)  # a wide delivery position is worth something too
        Tf = lambda p: TUNE["adv_lin"] * p[..., 0] / L + np.maximum(Vf(p), Vw(p))  # territory: a little linear progress everywhere plus the better of 'close to goal' and 'in a crossing position'
        VC = Vf(C)
        TC = Tf(C)
        prog_w = (TUNE["prog_base"] + TUNE["prog_dir"] * tc.directness) * s.mod["prog"][c]
        # losing the ball hurts most near your own goal and least in the opponent's final third
        loss_at = lambda x: TUNE["loss"] * s.mod["loss"][c] * (.45 + 1.1 * (1 - x / L))  # cost of losing it at pitch position x
        loss_eff = loss_at(C[0])
        if rk == "penalty":
            return s._shoot(c, C, xg=.76, kind="penalty", nb=0)
        if rk == "corner":
            return s._corner(c, f)
        U, K, X = [], [], []
        best_mate = 0.  # value of the best chance he could set up for a teammate (his xG x pass success)
        # -- passes
        mates = np.array([i for i in range(11) if i != ci])
        hv = fv(s.vel[c])
        face = hv / nrm(hv) if nrm(hv) > .5 else np.array([1., 0.])
        dv = P[mates] - C
        dist = nrm(dv, axis=1)
        cosang = dv @ face / np.maximum(dist, 1e-6)
        vis = (dist > 3.5) & (dist < 18 + 2.2 * a["vision"][c]) & (cosang > np.cos(np.radians(min(180, 100 + 4 * a["vision"][c]))))
        if rk in ("throw_in", "drop_ball", "indirect_free_kick"):
            vis &= dist < 28
        if rk == "goal_kick":
            vis &= dist > 8
        if not (C[0] < .45 * L or pr > .4):
            vis &= s.role[11 * t + mates] != 0
        vis &= ~s.off[11 * t + mates]
        if vis.any():
            m_ = mates[vis]
            Pm, Vm = P[m_], Vp[m_]
            dist = nrm(Pm - C, axis=1)
            lofted = (dist > 30) & (s.rng.random(len(m_)) < .35 + .5 * tc.directness)
            spd = clip(11 + .3 * dist, 11, 24)
            runner = s.t < s.run_until[11 * t + m_]  # a runner is played into space well ahead of him: through ball
            tgt = clip(Pm + Vm * (np.minimum(dist / spd, 1.5) * np.where(runner, 1.3, .8))[:, None], [1, 1], [L - 1, W - 1])
            ab = tgt - C
            d2 = nrm(ab, axis=1)
            ap = O[None] - C
            sp = clip((ap * ab[:, None]).sum(2) / np.maximum((ab * ab).sum(1), 1e-6)[:, None], 0, 1)
            q = C + sp[..., None] * ab[:, None]
            dperp = nrm(O[None] - q, axis=2)
            tb = sp * d2[:, None] / spd[:, None]
            to = np.maximum(dperp - 1, 0) / (s.vmax[s._opp(t)] * .85)[None] + .4
            pk = sig(4 * (tb - to) - 1.2) * (dperp < 14) * clip((.95 - sp) / .15, 0, 1)  # markers next to the receiver contest control (p_ctrl), not the lane
            p_int = np.where(lofted, .08, 1 - np.prod(1 - .9 * pk, axis=1))
            dn_r = nrm(O[None] - tgt[:, None], axis=2).min(1)
            tech = (a["dribbling"][11 * t + m_] + a["composure"][11 * t + m_]) / 2
            p_ctrl = clip(.97 - TUNE["ctrl_marked"] * np.exp(-(dn_r - .5) / 1.6) + .004 * (tech - 10), .25, .98)  # a defender on the receiver contests the first touch
            p_air = clip(.55 + .02 * (a["aerial"][11 * t + m_] - a["aerial"][s._opp(t)].mean()), .2, .85)
            p_ctrl = np.where(lofted, p_air, p_ctrl)
            sg = (.5 + .05 * d2) * (TUNE["pass_sigma"] * (1 - .0353 * a["passing"][c])) * (1 + .35 * pr) * np.where(lofted, 1.5, 1)
            p_acc = 1 - np.exp(-(2.5 ** 2) / (2 * sg ** 2))
            p_s = (1 - p_int) * p_acc * p_ctrl
            line = s.line_hist[t][0]  # second-last defender as the passer last saw him; defenders may have stepped up since
            Pp = Pm - Vm * .5  # runners are seen where they were half a second ago, so mistimed runs end up offside
            off_m = (Pp[:, 0] > np.maximum(line, C[0]) + .3) & (Pp[:, 0] > L / 2)
            off_m &= s.rng.random(len(off_m)) > TUNE["off_miss"] - .012 * a["vision"][c]  # now and then the passer misjudges the line
            p_s = p_s * np.where(off_m, TUNE["off_mult"], 1.)
            byp = ((O[None, :, 0] > C[0] + 1) & (O[None, :, 0] < tgt[:, 0, None] - 1) & (dperp < 18) & (s.role[s._opp(t)] > 0)[None]).sum(1)  # opponents this pass takes out of the game
            u = p_s ** TUNE["risk_pow"] * (prog_w * (Tf(tgt) - TC) + TUNE["pack"] * np.minimum(byp, 4) + TUNE["keep"]) - (1 - p_s) * loss_at(tgt[:, 0]) - .001 * d2 - TUNE["usage"] * s.recent[11 * t + m_]
            inbox_m = np.nonzero((tgt[:, 0] > L - 24) & (np.abs(tgt[:, 1] - W / 2) < 22))[0]
            for j_ in inbox_m[np.argsort(-Vf(tgt[inbox_m]))][:4]:  # could a teammate get a better chance than mine?
                best_mate = max(best_mate, float(p_s[j_]) * s._xg(tgt[j_], O[s.role[s._opp(t)] > 0])[0])
            u = u + TUNE["long_bonus"] * (.3 + tc.directness) * ((d2 > 28) & (Vf(tgt) - VC > .06))  # direct teams go long
            if rk == "goal_kick":  # keepers go long when the team plays direct
                u = u + TUNE["gk_long"] * tc.directness * (d2 > 30)
            for k in range(len(m_)):
                U.append(u[k]); K.append("pass"); X.append((11 * t + m_[k], tgt[k], bool(lofted[k]), spd[k], p_s[k]))
        # -- shot
        dg = np.hypot(L - C[0], W / 2 - C[1])
        if dg < 35 + s.mod["range"][c] and rk in (None, "free_kick"):
            xg, nb = s._xg(C, O[s.role[s._opp(t)] > 0])
            if rk == "free_kick":  # a set ball over the wall: no open-play blockers, specialists bend it round
                xg, _ = s._xg(C, [])
                xg = float(clip(xg * 2.2 * (1 + .25 * s.mod["fk"][c] + .02 * (a["finishing"][c] - 10)), 0, .5))
                nb = 2
            take = False
            if rk is None and s.role[c] > 0:
                # willingness to shoot follows where he is (a clean-look xG from distance and angle), not whether a defender happens to be in the way:
                # real shooters take it through traffic (about two defenders in the shot triangle) and the defence blocks a third of them
                xg0, _ = s._xg(C, [])
                z = TUNE["shoot_slope"] * np.log(xg0 / TUNE["shoot_ref"]) + 1.5 * np.log(SHOT_PROP[s.role[c]]) + 1.2 * (tc.shoot - .5) + np.log(s.mod["shot"][c]) + TUNE["shoot_press"] * pr - TUNE["shoot_hold"] * (s.t - s.ctrl_t)  # the chance is taken early: a player who keeps carrying has passed it up
                take = bool(s.rng.random() < sig(z)) and best_mate < 1.5 * xg
            elif rk == "free_kick":
                take = s.role[c] > 0 and xg >= .08
            U.append((100. if take else 0.) + (TUNE["shot_k"] + TUNE["shot_k_dir"] * tc.shoot) * s.mod["shot"][c] * ROLE_SHOT[s.role[c]] * (TUNE["shot_mid"] if s.role[c] in (3, 4, 6) else 1.) * xg * (.8 + .02 * a["finishing"][c]) - TUNE["shot_cost"] + (TUNE["fk_bonus"] if rk == "free_kick" and dg < 28 else 0)); K.append("shot"); X.append((xg, nb))
        # -- cross from a wide position into the box
        if rk is None and abs(C[1] - W / 2) > 13 and C[0] > .58 * L:
            boxm = [i for i in range(11) if i != ci and P[i][0] > L - 22 and abs(P[i][1] - W / 2) < 15 and not s.off[11 * t + i]]
            if boxm:
                gk_a = a["aerial"][s._opp(t)].mean()
                sc = np.array([a["aerial"][11 * t + i] + .4 * a["finishing"][11 * t + i] - 2.0 * (nrm(O - P[i], axis=1) < 2.5).sum() for i in boxm])
                pw = np.exp((sc - sc.max()) / 3.0)
                tgt_i = int(boxm[s.rng.choice(len(boxm), p=pw / pw.sum())])
                aim = clip(P[tgt_i] + Vp[tgt_i] * .6, [1, 1], [L - 1, W - 1])
                d_c = float(nrm(aim - C))
                p_acc = 1 - np.exp(-(3.0 ** 2) / (2 * (2.0 + .07 * d_c) ** 2 * (1.9 - .06 * a["passing"][c]) ** 2))
                p_air = float(clip(.5 + .02 * (a["aerial"][11 * t + tgt_i] - gk_a), .2, .8))
                p_c = p_acc * p_air
                U.append(p_c * (TUNE["cross_v"] + TUNE["keep"]) - (1 - p_c) * loss_eff * .4 + s.mod["carry"][c] * .5)
                K.append("cross"); X.append((11 * t + tgt_i, aim, p_c))
        # -- carry / clearance
        if rk is None:
            toward = _unit(np.array([L, W / 2 + (C[1] - W / 2) * LANE[s.role[c]]]) - C)  # wide players keep their lane, central players head for goal
            near = dn < 8
            away = (_unit(C - O[near]) * ((8 - dn[near]) / 8)[:, None]).sum(0) if near.any() else 0
            dirv = _unit(toward + .8 * away)
            cpos = clip(C + 7 * dirv, [1, 1], [L - 1, W - 1])
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
            U.append(p_keep * (TUNE["carry_base"] + TUNE["carry_prog"] * prog_w * (Tf(cpos) - TC)) - (1 - p_keep) * .5 - TUNE["carry_streak"] * s.carry_streak + s.mod["carry"][c] + CARRY_ROLE[s.role[c]]
                     + TUNE["patience"] * np.exp(-(s.t - s.ctrl_t) / 2.0) * (1 - pr) * (dnear > 4)); K.append("carry"); X.append((cpos, o, p_beat))
            if C[0] < .2 * L and dnear < 3.5:
                U.append(.2 + .35 * pr); K.append("clear"); X.append(np.array([C[0] + 40, clip(C[1] + s.rng.choice([-1, 1]) * 18, 5, W - 5)]))
        if not U:
            U, K, X = [0.], ["carry"], [(clip(C + 7 * np.array([1., 0.]), [1, 1], [L - 1, W - 1]), -1, 1.)]
        tau = .02 + .04 * (1 - a["composure"][c] / 20)
        U = np.array(U)
        pr_ = np.exp((U - U.max()) / tau)
        k = s.rng.choice(len(U), p=pr_ / pr_.sum())
        if s.audit is not None:
            s._audit(t, c, C, O, pr, dnear, rk, K, X, U, k)
        kind, x = K[k], X[k]
        order = np.argsort(-U)[:4]
        why = dict(pressure=round(pr, 2), nearest_opponent_m=round(float(dnear), 1), chosen=kind,
                   options=[dict(kind=K[o], utility=round(float(U[o]), 2),
                                 to=s.ids[X[o][0]] if K[o] in ("pass", "cross") else None,
                                 p_success=round(float(X[o][4]), 2) if K[o] == "pass" else round(float(X[o][2]), 2) if K[o] == "cross" else None,
                                 xg=round(float(X[o][0]), 2) if K[o] == "shot" else None) for o in order])
        s.last_why = why
        if kind != "carry":
            s.carry_streak = 0
        if kind == "pass":
            j, tgt_, lofted_, spd_, ps = x
            s._pass(c, j, f(tgt_), lofted_, spd_, ps, why=why, hands_ok=rk != "goal_kick", source="throw_in" if rk == "throw_in" else None)
        elif kind == "shot":
            s._shoot(c, C, xg=x[0], nb=x[1], kind="free_kick_shot" if rk == "free_kick" else "shot", why=why)
        elif kind == "clear":
            s._pass(c, None, f(x), True, 20., .5, kind="clearance")
        elif kind == "cross":
            j, aim, pc = x
            s._pass(c, j, f(aim), True, 20., pc, kind="cross", source="open_play", why=why, sigma_scale=.8)
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
            s.carry_dir = _unit(f(cpos) - s.pos[c])
            s.carry_speed = float(min(.85 * s.vmax[c], 3.2 + 2.2 * (1 - pr)))  # running at a defender or into space: faster when unpressed
            s.carry_streak += 1
            s.next_dec = s.t + s.rng.uniform(.35, .6)  # keep carrying and look again shortly

    def _audit(s, t, c, C, O, pr, dnear, rk, K, X, U, k):
        """Record every on-ball decision with all the options considered (attacking-left-to-right frame)."""
        opts = []
        for kk, xx, uu in zip(K, X, U):
            o = dict(kind=kk, u=float(uu))
            if kk == "pass":
                tg = np.asarray(xx[1]); o.update(to=int(xx[0]) - 11 * t, tgt=tg.tolist(), p=float(xx[4]), lofted=bool(xx[2]), rcv_d=float(nrm(O - tg, axis=1).min()))
            elif kk == "cross":
                o.update(to=int(xx[0]) - 11 * t, tgt=np.asarray(xx[1]).tolist(), p=float(xx[2]))
            elif kk == "shot":
                o.update(xg=float(xx[0]), nb=int(xx[1]))
            elif kk == "carry":
                o.update(tgt=np.asarray(xx[0]).tolist(), p=float(xx[2]), blocked=bool(xx[1] >= 0))
            else:
                o.update(tgt=np.asarray(xx).tolist())
            opts.append(o)
        out = O[s.role[s._opp(t)] > 0]
        s.audit.append(dict(t=round(s.t, 1), team=t, c=int(c - 11 * t), role=int(s.role[c]), pos=C.tolist(), rk=rk, press=float(pr), dnear=float(dnear),
                            ahead=int((out[:, 0] > C[0]).sum()), ahead_close=int(((out[:, 0] > C[0] - 1) & (np.abs(out[:, 1] - C[1]) < 10)).sum()),
                            hold=round(float(s.t - s.ctrl_t), 2), chosen=int(k), opts=opts))

    # ------------------------------------------------------------------ actions
    def _pass(s, c, rcv, aim, lofted, spd, est, kind="pass", sigma_scale=1.0, why=None, source=None, hands_ok=True):
        t = c // 11
        start = s.ball.copy()
        d = nrm(aim - start)
        hands = bool(s.role[c] == 0 and kind == "pass" and hands_ok and d < 34 and s.rng.random() < .5)  # keeper throws it out
        if hands or source == "throw_in":
            spd, lofted = min(spd, 15. if hands else 13.), False
        pr = clip(1 - nrm(s.pos[s._opp(t)] - s.pos[c], axis=1).min() / 5, 0, 1)
        sg = (.5 + .05 * d) * (TUNE["pass_sigma"] * (1 - .0353 * s.attr["passing"][c])) * (1 + .35 * pr) * (1.8 if lofted else 1) * sigma_scale
        sg *= s.rain
        wide = min(aim[1], W - aim[1]) < 9
        end = aim + s.rng.normal(0, sg * (1 + TUNE["wide_error"] * wide), 2)
        dur = max(nrm(end - start) / spd, .3)
        gx = L if t == 0 else 0
        fwd = abs(gx - start[0]) - abs(gx - aim[0])  # metres gained towards the opposition goal
        f_ = _tf if t else (lambda x: x)
        line = np.sort(f_(s.pos[s._opp(t)])[:, 0])[-2]
        offside = bool(kind == "pass" and source not in ("short_corner", "throw_in") and rcv is not None and f_(s.pos[rcv])[0] > max(line, f_(start)[0]) + .3 and f_(s.pos[rcv])[0] > L / 2)
        lat = abs(aim[1] - start[1])
        technique = ("long_ball" if lofted and d > 30 else "switch" if lat > 28 and fwd < 12 else
                     "through_ball" if not lofted and fwd > 10 and f_(aim)[0] > line + 1 else "forward" if fwd > 8 else "backward" if fwd < -5 else "sideways")
        if hands:
            technique = "keeper_throw"
        elif source == "throw_in":
            technique = "throw_in"
        ev = s.emit(kind, player=c, pos=start, end=aim, length=d, speed=spd, lofted=lofted, height="high" if lofted else "ground",
                    body_part="hands" if (hands or source == "throw_in") else "foot", technique=technique, hold=round(float(s.t - s.ctrl_t), 2), receiver=None if rcv is None else s.ids[rcv], p_success=est,
                    difficulty=1 - est, progressive=bool(fwd >= 10), gain=round(float(fwd), 1), source=source, outcome=None, why=why)
        s._end_spell()
        if offside:
            ev["outcome"] = "offside"
            spot = s.pos[rcv].copy()
            s.emit("offside", player=rcv, pos=spot, passer=s.ids[c], line_x=round(float(f_(np.array([line, 0.]))[0]), 1))
            s.stop[s.period - 1] += 8
            s.last_touch = c
            return s._set_dead("indirect_free_kick", 1 - t, clip(spot, [1, 1], [L - 1, W - 1]), 16 + s.rng.random() * 10)
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
        if s.dead.get("short") and s.dead.get("sup", -1) >= 0:
            sup = s.dead["sup"]
            return s._pass(c, sup, s.pos[sup].copy(), False, 12., .9, kind="pass", source="short_corner")
        zone_name = str(s.rng.choice(["near_post", "far_post", "penalty_spot", "edge_of_box"], p=[.3, .3, .3, .1]))
        zc = {"near_post": (L - 6, W / 2 - 4), "far_post": (L - 6, W / 2 + 4), "penalty_spot": (L - 11, W / 2), "edge_of_box": (L - 18, W / 2)}[zone_name]
        side = 1 if P[ci := c - 11 * t][1] > W / 2 else -1  # near/far are relative to the corner taker's side
        zc = (zc[0], W / 2 + (zc[1] - W / 2) * side)
        j = max(box, key=lambda i: s.attr["aerial"][11 * t + i] - .2 * nrm(P[i] - np.array(zc)))
        aim = np.array(zc) * .5 + P[j] * .5
        ev = s._pass(c, 11 * t + j, f(aim), True, 19., .35, kind="cross", sigma_scale=.8, source="corner")
        if s.pending_pass is not None:
            s.pending_pass["delivery"] = zone_name

    def _shoot(s, c, C, xg, nb, kind="shot", why=None):
        t = c // 11
        a = s.attr
        f = _tf if t else (lambda x: x)
        fin, gk = a["finishing"][c], a["gk"][s._opp(t)][0]
        odds = xg / (1 - xg + 1e-9) * np.exp(.05 * (fin - 12.5) + .02 * (a["composure"][c] - 12.5)) * np.exp(-.05 * (gk - 12.5))  # the xG model is for an average finisher / keeper (about 12.5 in this league)
        p_goal = float(odds / (1 + odds)) if kind != "penalty" else .76 * (.9 + .01 * (fin - 2.5)) * (1.1 - .01 * (gk - 2.5))
        u = s.rng.random()
        if u < p_goal:
            res = "goal"
        else:
            r = s.rng.random()
            p_block = 0 if kind == "penalty" else min(.5, .12 + .12 * nb)
            p_off = clip(.55 - 1.0 * xg - .01 * (fin - 10), .2, .6)
            res = "blocked" if r < p_block else ("off_target" if r < p_block + p_off else "saved")
        start = s.ball.copy()
        wy = s.rng.uniform(-3.2, 3.2)
        endt = {"goal": [L + .5, W / 2 + wy], "saved": [L - .5, W / 2 + wy],
                "off_target": [L + .5, W / 2 + s.rng.choice([-1, 1]) * s.rng.uniform(4, 8)],
                "blocked": list(C + _unit(np.array([L, W / 2]) - C) * s.rng.uniform(1.5, 3))}[res]
        end = f(np.array(endt))
        spd = float(clip(20 + .5 * fin + s.rng.normal(0, 2), 16, 33))
        lp = s.last_pass_to.get(t)
        assist_ev = lp[3] if lp and lp[1] == c and s.t - lp[2] <= 8 else None
        if assist_ev is not None:
            assist_ev["key_pass"] = True
        nd = float(nrm(s.pos[s._opp(t)] - s.pos[c], axis=1).min())
        pen_kw = {}
        if kind == "penalty":
            side = str(s.rng.choice(["left", "centre", "right"], p=[.45, .1, .45]))
            other = [x for x in ("left", "centre", "right") if x != side]
            pen_kw = dict(placement=side, keeper_dive=side if res == "saved" else (str(s.rng.choice(other)) if s.rng.random() < .85 else side))
        ev = s.emit("shot", player=c, pos=start, end=end, xg=xg, speed=spd, outcome=res, kind=kind, why=why, **pen_kw,
                    assist=None if assist_ev is None else assist_ev["player"], body_part="head" if kind == "header" else "foot",
                    first_time=bool(s.t - s.ctrl_t < .6), hold=round(float(s.t - s.ctrl_t), 2), under_pressure=bool(nd < 2.5), big_chance=bool(xg >= .25),
                    freeze_frame=[[round(float(x), 1), round(float(y), 1)] for x, y in s.pos])
        err_ev = None
        te = s.turnover
        if te is not None and te["team"] == t and not te["used"] and te["own_half"] and s.t - te["t"] < 12:
            te["used"] = True  # the side that lost the ball in its own half gifted this shot
            err_ev = s.emit("error", player=te["loser"], team=1 - t, pos=te["pos"], leads_to="shot", delay=round(s.t - te["t"], 1), shooter=s.ids[c])
        var = None
        if res == "goal" and kind != "penalty" and s.rng.random() < .05:
            var = dict(overturn=bool(s.rng.random() < .5), reason=str(s.rng.choice(["offside in build-up", "foul in build-up", "handball"], p=[.55, .35, .1])))
        s._end_spell()
        if s.pending_pass is not None:
            s.pending_pass = None
        s.state, s.carrier = "flight", -1
        s.flight = dict(start=start, end=end, aim=end, t0=s.t, dur=max(nrm(end - start) / spd, .25), speed=spd,
                        kind="shot", team=t, passer=c, rcv=-1, res=res, ev=ev, var=var, assist_ev=assist_ev, err_ev=err_ev)
        s.ball_v = (end - start) / s.flight["dur"]
        s.last_touch = c
        s.block_until[c] = s.t + .6

    # ------------------------------------------------------------------ injuries
    def _injury(s, i, cause):
        if s.off[i] or s.injured[i]:
            return
        u = s.rng.random()
        sev = "knock" if u < .5 else ("substitution" if u < .93 else "serious")
        s.injured[i] = True
        s.emit("injury", player=i, pos=s.pos[i], severity=sev, cause=cause, play_on=sev == "knock")
        if sev == "knock":  # plays on, but slower; sometimes after a spell of treatment
            s.vmax[i] *= .88
            s.acc[i] *= .9
            if s.rng.random() < .4:
                s.pending_inj.append((i, "treatment"))
        else:
            s.pending_inj.append((i, sev))

    def _stop_for_injury(s):
        i, sev = s.pending_inj.pop(0)
        t = i // 11
        treat = float(s.rng.uniform(25, 55) if sev == "treatment" else s.rng.uniform(45, 100) if sev == "substitution" else s.rng.uniform(150, 240))
        s.stop[s.period - 1] += treat
        if s.state == "held":
            team = s.poss
            s._end_spell()
            pos = clip(s.ball.copy(), [1, 1], [L - 1, W - 1])
            s.emit("play_stopped", reason="injury", team=int(team), pos=pos, duration=treat)
            s._set_dead("drop_ball", team, pos, treat + 8)
        else:
            s.dead["ready"] += treat
        if sev == "treatment":  # leaves the pitch for treatment and comes back
            s.emit("player_off", player=i, pos=s.pos[i], reason="treatment", permanent=False)
            s.sched.append((s.t + treat, dict(type="player_on", player=i, reason="treatment return")))
            return
        name = _ROLE_NAME[s.role[i]]
        near = {"AM": ("CM", "WG"), "CM": ("DM", "AM"), "DM": ("CM", "CB"), "WG": ("ST", "AM", "FB"), "ST": ("WG",), "FB": ("CB", "WG"), "CB": ("FB", "DM")}
        pick = [b for b in s.bench[t] if b["role"] == name] or ([b for b in s.bench[t] if b["role"] in near.get(name, ())] if name != "GK" else [])
        if s.subs[t] < 5 and pick:
            return s._swap(t, i, max(pick, key=lambda b: b["overall"]), "injury")
        if name == "GK":
            return s._outfield_keeper(t, i)
        s.off[i] = True  # no replacement: the team plays a man short
        s.emit("injury_off", player=i, pos=s.pos[i], men_left=int(11 - s.off[s._own(t)].sum()))

    def _outfield_keeper(s, t, i):
        """No keeper left on the bench: the tallest defender pulls on the gloves and the team plays a man short."""
        cand = [j for j in range(11 * t, 11 * t + 11) if s.role[j] == ROLE_IDX["CB"] and not s.off[j]] or \
               [j for j in range(11 * t, 11 * t + 11) if s.role[j] > 0 and not s.off[j]]
        j = max(cand, key=lambda k: s.attr["aerial"][k])
        pj = dict(s.cur[j])
        pj["role"], pj["attrs"] = "GK", {**pj["attrs"], "gk": 6.0}
        s._load(i, pj, (.03, .5))
        s.injured[i] = False
        s.off[j] = True
        s.emit("outfield_keeper", player=i, slot=int(i), on=pj["id"], pos=s.pos[i], men_left=int(11 - s.off[s._own(t)].sum()))

    # ------------------------------------------------------------------ manager: tactics, substitutions, red cards
    def _send_off(s, i):
        t = i // 11
        s.off[i] = True
        s.emit("sent_off", player=i, pos=s.pos[i], men_left=int(11 - s.off[s._own(t)].sum()))

    def _manage(s):
        """Every 30 s: shift each team's tactics for the score, time and numbers, like a manager would."""
        mins = (s.t if s.period == 1 else 2700 + (s.t - s.h1)) / 60
        late = float(clip((mins - 55) / 35, 0, 1))
        for t in (0, 1):
            gd = s.score[t] - s.score[1 - t]
            down = int(s.off[s._own(t)].sum() - s.off[s._opp(t)].sum())
            need = float(clip(-gd, 0, 3)) * .12 * (.15 + .85 * late)
            lead = float(clip(gd, 0, 3)) * .10 * late
            b, cur = s.base_tact[t], s.tact[t]
            goal = dict(press=b.press + need - lead - .15 * down, line_height=b.line_height + .8 * need - 1.2 * lead - .15 * down,
                        risk=b.risk + 1.2 * need - lead, directness=b.directness + need + .4 * lead, shoot=b.shoot + .5 * need,
                        compactness=b.compactness - .4 * need + .8 * lead + .15 * down, width=b.width - .1 * down)
            new = {k: float(clip(getattr(cur, k) + .4 * (v - getattr(cur, k)), 0, 1)) for k, v in goal.items()}
            last = s.last_tact_emit[t]
            if sum(abs(new[k] - last[k]) for k in new) > .2 and s.t - s.last_tact_t[t] > 240:
                reason = ("man down" if down > 0 else "man advantage" if down < 0 else "chasing the game" if gd < 0
                          else "protecting the lead" if gd > 0 else "reset")
                s.emit("tactic_change", team=t, reason=reason,
                       changes={k: [round(last[k], 2), round(new[k], 2)] for k in new if abs(new[k] - last[k]) > .02})
                s.last_tact_emit[t].update(new)
                s.last_tact_t[t] = s.t
            s.tact[t] = dataclasses.replace(cur, **new)
            if mins > 80 and gd > 0 and s.rng.random() < .012:  # time wasting by the leading side
                i = int(s.rng.choice([j for j in s._own(t).start + np.arange(11) if not s.off[j] and s.role[j] > 0]))
                s.stop[s.period - 1] += 12
                s._yellow(i, "time_wasting")
            elif s.rng.random() < .004:
                i = int(s.rng.choice([j for j in s._own(t).start + np.arange(11) if not s.off[j] and s.role[j] > 0]))
                s._yellow(i, "dissent")
            if s.want_form[t] is None and s.forms_used[t] < 3 and s.t - s.last_form_t[t] > 900:
                choice = s._choose_formation(t, gd, mins, down)
                if choice:
                    s.want_form[t] = choice

    def _choose_formation(s, t, gd, mins, down):
        cur, n_on = s.formation[t], int(11 - s.off[s._own(t)].sum())
        if n_on == 10:
            new = TEN_MAN.get(cur) if cur not in ("4-4-1", "4-3-2", "5-3-1") else None
            return (new, "man down") if new else None
        if n_on < 10:
            return None
        if down < 0 and mins > 20 and s.rng.random() < .25:
            new = ATTACK.get(cur)
            return (new, "man advantage") if new else None
        if gd <= -1 and mins >= 60 and s.rng.random() < .06:
            new = ATTACK.get(cur)
            return (new, "chasing the game") if new else None
        if gd >= 1 and mins >= 75 and s.rng.random() < .06:
            new = DEFEND.get(cur)
            return (new, "protecting the lead") if new else None
        return None

    def _do_formation(s):
        for t in (0, 1):
            if s.want_form[t] is None:
                continue
            name, reason = s.want_form[t]
            s.want_form[t] = None
            own = [i for i in range(11 * t, 11 * t + 11) if not s.off[i]]
            slots = FORMATIONS.get(name)
            if slots is None or len(slots) != len(own) or name == s.formation[t]:
                continue
            cost = np.array([[100. if (s.role[i] == 0) != (sl[1] == "GK") else
                              (0. if _ROLE_NAME[s.role[i]] == sl[1] else _ROLE_NEAR.get(frozenset((_ROLE_NAME[s.role[i]], sl[1])), 4.)) + 3 * abs(s.anchor[i][1] / W - sl[3])
                              for sl in slots] for i in own])
            rows, cols = linear_sum_assignment(cost)
            moves = []
            for r_, c_ in zip(rows, cols):
                i, (lab, role, ax, ay) = own[r_], slots[c_]
                if _ROLE_NAME[s.role[i]] != role:
                    moves.append(dict(player=s.ids[i], role_from=_ROLE_NAME[s.role[i]], role_to=role))
                s.role[i], s.anchor[i] = ROLE_IDX[role], [ax * L, ay * W]
            s.emit("formation_change", team=t, formation=name, previous=s.formation[t], reason=reason, moves=moves)
            s.formation[t], s.last_form_t[t], s.forms_used[t] = name, s.t, s.forms_used[t] + 1

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
            anchor = (_PRESET_X[on["role"]], ay if on["role"] in ("FB", "WG") else float(clip(ay, .35, .65)))
        off_id = s.ids[i]
        s.bench[t].remove(on)
        s.subs[t] += 1
        s.emit("substitution", team=t, slot=int(i), off=off_id, on=on["id"], off_name=s.names[off_id], on_name=on["name"],
               reason=reason, off_stamina=round(float(s.stam[i]), 2), on_role=on["role"], pos=s.pos[i])
        s._load(i, on, anchor)
        s.stam[i], s.yellow[i], s.commit_until[i], s.run_until[i] = 1., 0, 0., 0.
        s.injured[i] = False
        s.stop[s.period - 1] += 15
        return True

    # ------------------------------------------------------------------ flight
    def _claim_p(s, i, speed, t_team, at, receiver):
        a = s.attr
        opp = s._opp(i // 11)
        dn = nrm(s.pos[opp] - at, axis=1).min()
        tech = (a["dribbling"][i] + a["composure"][i]) / 2
        return float(clip(.985 - .012 * max(0, speed - 12) + .012 * (tech - 10) - TUNE["ctrl_marked"] * np.exp(-(dn - .5) / 1.6), .25, .995))  # a defender on the receiver contests the first touch

    def _flight(s):
        f = s.flight
        a0 = s.ball.copy()
        u = min(1., (s.t + s.dt - f["t0"]) / f["dur"])
        b = f["start"] + (f["end"] - f["start"]) * u
        s.ball, s.ball_v = b, (f["end"] - f["start"]) / f["dur"]
        if f["kind"] == "ground":
            ab = b - a0
            sp = clip(((s.pos - a0) @ ab) / max(ab @ ab, 1e-9), 0, 1)
            q = a0 + sp[:, None] * ab
            d = nrm(s.pos - q, axis=1)
            ok = (d < 1.0) & (s.block_until <= s.t)
            g_ = 11 * (1 - f["team"])  # the defending keeper reaches further and is better at it than outfield players
            ok[g_] = bool(d[g_] < 3.2 and s.block_until[g_] <= s.t)
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
                    p = float(clip(TUNE["intercept"] + .02 * (ar["positioning"][i] - 10) + .01 * (ar["tackling"][i] - 10) - .02 * (f["speed"] - 12), .15, .9)) * float(clip((s.t - f["t0"]) / .3, .35, 1))
                if s.role[i] == 0 and not mine:
                    p = max(p, float(clip(.45 + .03 * (s.attr["gk"][i] - 10), .3, .85)))
                if s.rng.random() < p:
                    s.ball = q[i]
                    return s._gain_control(i, q[i])
                if mine:  # first touch lost
                    s.emit("miscontrol", player=i, pos=q[i])
                    s.block_until[i] = s.t + .5
                    if s.pending_pass is not None:
                        s.pending_pass["outcome"], s.pending_pass = "incomplete", None
                    return s._loose_ball(_unit(ab + s.rng.normal(0, .8, 2)) * s.rng.uniform(2, 5))
                s.block_until[i] = s.t + .4
                if s.rng.random() < .2:
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
        d = nrm(s.pos - pt, axis=1)
        ok = (d < 6.5) & (s.block_until <= s.t)
        for g in (0, 11):
            ok[g] = bool(d[g] < 10 and s.block_until[g] <= s.t)
        if not ok.any():
            return s._loose_ball(s.rng.normal(0, 1.5, 2))
        a = s.attr
        w = (a["aerial"] + .5 * a["strength"] + .5 * a["positioning"]) * np.exp(-d / 3.5) * ok
        for g in (0, 11):  # a keeper coming for the ball outweighs outfield players near him
            if ok[g]:
                w[g] = (.6 * a["gk"][g] + .4 * a["aerial"][g] + 4) * np.exp(-d[g] / 4.5)
        w[f["rcv"]] *= 1.2 if f["rcv"] >= 0 else 1
        i = int(s.rng.choice(22, p=w / w.sum()))
        t = i // 11
        rivals = w * (np.arange(22) // 11 != t)
        if s.role[i] == 0 and t != f["team"]:
            if s.rng.random() < .3:  # punches it clear
                s.emit("keeper_punch", player=i, pos=pt)
                if s.pending_pass is not None:
                    s.pending_pass["outcome"], s.pending_pass = "intercepted", None
                s.last_touch = i
                return s._loose_ball(_unit(np.array([L / 2, W / 2]) - pt + s.rng.normal(0, .8, 2)) * 8)
            s.emit("keeper_claim", player=i, pos=pt)
        elif rivals.max() > 0:
            s.emit("duel", player=i, pos=pt, duel="aerial", outcome="won", target=s.ids[int(rivals.argmax())])
        s.ball = pt.copy()
        pt_t = _tf(pt) if t else pt
        if t == f["team"]:
            if pt_t[0] > .7 * L and abs(pt_t[1] - W / 2) < 18 and s.rng.random() < .13:
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
            if s.rng.random() < .22:  # headed behind for a corner
                cp = np.array([L if f["team"] == 0 else 0., 0. if pt[1] < W / 2 else W])
                s.emit("corner", team=f["team"], pos=cp)
                s._end_spell()
                return s._set_dead("corner", f["team"], cp, 32 + float(s.rng.uniform(0, 12)))
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
            var = f.get("var")
            if var is not None:
                dur = float(s.rng.uniform(70, 140))
                s.stop[s.period - 1] += dur
                s.emit("var_check", team=t, player=c, reason=var["reason"], duration=dur)
                if var["overturn"]:
                    f["ev"]["outcome"] = "disallowed"
                    s.emit("goal_disallowed", player=c, pos=f["start"], xg=f["ev"]["xg"], reason=var["reason"])
                    s._end_spell()
                    return s._set_dead("indirect_free_kick", 1 - t, clip(f["start"], [1, 1], [L - 1, W - 1]), dur + 8)
                s.emit("var_confirmed", team=t, player=c)
            if f.get("assist_ev") is not None:
                f["assist_ev"]["assist"] = True
            if f.get("err_ev") is not None:
                f["err_ev"]["leads_to"] = "goal"
            s.score[t] += 1
            s.stop[s.period - 1] += 35
            s.emit("goal", player=c, pos=f["start"], xg=f["ev"]["xg"], home=s.score[0], away=s.score[1],
                   assist=f["ev"].get("assist"), body_part=f["ev"]["body_part"])
            s._end_spell()
            s._kickoff(1 - t, delay=65. + float(s.rng.uniform(0, 20)))
        elif res == "saved":
            xg_ = f["ev"]["xg"]
            p_hold = float(clip(.5 + .02 * (s.attr["gk"][opp0] - 10) - 1.0 * xg_, .25, .8))
            u = s.rng.random()
            dive = "dive" if (f["speed"] > 24 or xg_ > .2) else "stand"
            if u < p_hold:
                s.emit("save", player=opp0, pos=f["end"], save_type="catch", technique=dive, outcome="held", penalty=f["ev"]["kind"] == "penalty")
                s.last_touch = c
                s._gain_control(opp0, f["end"])
            elif u < p_hold + .45 * (1 - p_hold):
                s.emit("save", player=opp0, pos=f["end"], save_type="parry", technique=dive, outcome="rebound", penalty=f["ev"]["kind"] == "penalty")
                s.last_touch = opp0
                away = np.array([-1. if t == 0 else 1., 0.])
                s._loose_ball(_unit(away + s.rng.normal(0, .9, 2)) * s.rng.uniform(3, 7))
            else:
                s.emit("save", player=opp0, pos=f["end"], save_type="tip_over", technique=dive, outcome="corner", penalty=f["ev"]["kind"] == "penalty")
                cp = np.array([L if t == 0 else 0., 0. if f["end"][1] < W / 2 else W])
                s.last_touch = opp0
                s.emit("corner", team=t, pos=cp)
                s._set_dead("corner", t, cp, 32 + float(s.rng.uniform(0, 12)))
        elif res == "off_target":
            s.last_touch = c
            s.ball = f["end"].copy()
            s._out(f["end"])
        else:  # blocked: a defender gets in the way
            opp_f = np.arange(11 * (1 - t), 11 * (1 - t) + 11)
            opp_f = opp_f[(s.role[opp_f] > 0) & ~s.off[opp_f]]
            blocker = int(opp_f[np.argmin(nrm(s.pos[opp_f] - f["end"], axis=1))])
            s.emit("block", player=blocker, pos=f["end"], shooter=s.ids[c], kind="shot")
            if s.rng.random() < .02:  # deflects past his own keeper
                s.score[t] += 1
                s.stop[s.period - 1] += 35
                s.emit("own_goal", player=blocker, team=t, pos=f["end"], home=s.score[0], away=s.score[1], scorer=s.ids[c])
                s._end_spell()
                return s._kickoff(1 - t, delay=28.)
            s.last_touch = blocker if s.rng.random() < .7 else c
            if s.rng.random() < .4:
                cp = np.array([L if t == 0 else 0., 0. if f["start"][1] < W / 2 else W])
                s.emit("corner", team=t, pos=cp)
                s._end_spell()
                s._set_dead("corner", t, cp, 32 + float(s.rng.uniform(0, 12)))
            else:
                s._loose_ball(s.rng.normal(0, 4, 2))

    # ------------------------------------------------------------------ loose ball
    def _loose(s):
        sp = nrm(s.ball_v)
        a0 = s.ball.copy()
        nsp = max(0., sp - 2.2 * s.dt)
        s.ball_v = s.ball_v * (nsp / sp) if sp > 1e-6 else s.ball_v
        b = a0 + s.ball_v * s.dt
        s.ball = b
        if not (0 <= b[0] <= L and 0 <= b[1] <= W):
            return s._out(b)
        ab = b - a0
        spn = clip(((s.pos - a0) @ ab) / max(ab @ ab, 1e-9), 0, 1) if ab @ ab > 1e-9 else np.zeros(22)
        q = a0 + spn[:, None] * ab
        d = nrm(s.pos - q, axis=1)
        close = [i for i in np.argsort(d / (s.vmax * .5 + 1)) if d[i] <= 1.3 and s.block_until[i] <= s.t]
        for i in close:
            if s.rng.random() < s._claim_p(i, nsp, i // 11, b, False) * .9:
                rival = [j for j in close if j // 11 != i // 11]
                if rival:
                    s.emit("fifty_fifty", player=i, pos=b, target=s.ids[rival[0]], outcome="won")
                return s._gain_control(i, b)
            s.block_until[i] = s.t + .4

    # ------------------------------------------------------------------ dead ball
    def _dead_step(s):
        d = s.dead
        s.ball, s.ball_v = d["pos"].copy(), np.zeros(2)
        k = d["taker"]
        if s.t >= d["ready"] and nrm(s.pos[k] - d["pos"]) < 1.3:
            s.last_ctrl_team = d["team"] if s.last_ctrl_team is None else s.last_ctrl_team
            s._gain_control(k, d["pos"])
            if s.dead_log and s.dead_log[-1][1] is None:
                s.dead_log[-1][1] = s.t
            s.rk = d["kind"] if d["kind"] != "kickoff" else None
            s.next_dec = s.t + .4

    # ------------------------------------------------------------------ output
    def result(s):
        fr = np.round(np.array(s.frames), 1)
        return dict(
            meta=dict(seed=s.seed, dt=s.dt, pitch=[L, W], home=team_meta(s.teams[0]), away=team_meta(s.teams[1]),
                      score=s.score, dead_log=[[round(a, 1), round(b if b is not None else s.t, 1), k] for a, b, k in s.dead_log], ball_in_play=round(1 - s.state_ticks["dead"] / max(sum(s.state_ticks.values()), 1), 3), half_time=s.h1, added_time=s.added, duration=round(s.t, 1), info=s.info,
                      frame_cols=["t", "ball_x", "ball_y", "carrier", "poss"] + [f"{c}{i}" for i in range(22) for c in "xy"] + ["ball_z"]),
            events=s.events, frames=fr.tolist())
