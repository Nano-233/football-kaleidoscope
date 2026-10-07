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
from .movement import _tf, _unit, nrm, clip, XT

DT = 0.2
# Single tuning table (calibrated by scripts/calibrate.py against real-football norms).
TUNE = dict(blk_a=.47, blk_sig=1.0, blk_lam=11.3, blk_b0=.012, shot_lane_k=1.0, xt_k=0., xt_opp=.6, risk_pow_xt=1.0, fk_cross=.5, head_shot_set=.4, free_dwell=.5, ball_v0=8.5, ball_v1=.25, read_press=.8, tackle_sel=8., exposed_k=1.2, surv_k=1.0, gk_shoot=1.4, contact_r=1.6, press_len=3.2, close_t=.5, hurry_k=.3, exec_k=2.0, exec_floor=.15, dec_noise=.15, windup_k=.35, hold_sigma=.7, fade_up=2.2, fade_dn=1.4, lazy_k=.6, xg_run=8., own_goal_cone=.0, head_run=4.2, head_rate=2.5, mom_k=.75, carry_v0=.9, carry_v1=.9, cruise_k=0.65, burst_hold=0.4, run_frac=0.7, press_dist=1.75, air_base=0.5, head_on=0.28, head_clear=0.5, air_k=0.5, gk_air=0.6, block_base=0.12, block_nb=0.12, ft_shot=0.315, turn_cost=0.55, turn_rate=3.2, face_k=1.05, pot=2.2, relief=1.0, ft_slow=.0, drive=0.1,  cong_k=0.5, risk_pow=2.2, first_touch=3.0, wide_v=0.35, ctrl_marked=0.5, shoot_hold=0.98, shoot_slope=2.05, shoot_ref=0.168, shoot_press=0.56, adv_lin=.6, pack=.04, shoot_min=.12, react=1.84, patience=0.02, pass_sigma=1.0, intercept=0.429, shot_mid=1.82, usage=0.76, fk_bonus=0.05, long_bonus=0.163, prog_base=0.5, prog_dir=0.63, keep=0.455, loss=1.0, cross_v=0.8, gk_long=.5, box_foul=.04, home_edge=1.7, shot_cost=1.3, shot_k=3.15, shot_k_dir=5.0,
            carry_base=0.22, carry_prog=.2, carry_streak=.3, dwell_base=0.9, dwell_shape=2.4, dwell_scale=1.61,
            tackle_rate=1.2, wide_error=3.2, off_miss=.2, off_mult=.25)
TUNE.update(assign_on=1., press_v=7., cover_v=3.5, mark_k=100., lane_job_k=35., post_v=5., space_v=3., job_hyst=1.5, press_hyst=2.0, templ_w=.9, busy_gate=.6, flat_gate=.6, run_gate=.95, mark_sprint_d=3.0, box_start=38., wn_end=42., lag_k=1., drop_k=1., mark_gap=1.1, box_gate=.85, dang_far=38., dang_gate=.85, react_def=.35, close_gap=1.5, jock_k=1., press_k=1., open_w=1., lane_w=1., slot_gate=.5, lean_r=24., lean_min=.35, shoot_close=28., shot_gap=.7, jobs_on=1., jobs_d=38., jobs_att=40., jobs_w=.9, job_mark=3., job_lane=12., job_post=1.4, job_wv=4., way_pen=0., way_r=2.2, run_k=1., ball_side=.9, hover=.3)  # block shape (read by movement.py through TP)
movement.TP = TUNE
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


PRESS_FLAG = {"pass", "cross", "carry", "shot", "take_on", "clearance", "dispossessed", "miscontrol"}


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
        s.poss_fade = np.array([.5, .5])
        s.chance_xg, s.chance_t = 0., 0.
        s.urg_until, s.cap_s = np.zeros(22), None
        s.sprint_hint = np.zeros(22, bool)
        s.head = np.array([[1., 0.]] * 11 + [[-1., 0.]] * 11)  # which way each player's body faces
        s.carry_scale = 1.0
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
        s.space_burst = np.zeros(22, bool)
        s.job_prev = [-np.ones(11, int), -np.ones(11, int)]  # what each defender was doing last tick (a mark, the lane, the post): assignments are kept unless something clearly better turns up
        s.press_prev = [[], []]
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
        if player is not None and type in PRESS_FLAG:
            if type in ("take_on", "clearance", "dispossessed", "tackle"):
                ev["under_pressure"] = True  # a duel is pressure by definition
            else:
                ev["under_pressure"] = bool(float(nrm(s.pos[s._opp(player // 11)] - s.pos[player], axis=1).min()) < TUNE["press_dist"])
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

    def _exec_pressure(s, i):
        """How hard is it for player i to execute right now, 0 (all the time and space he wants) to 1 (a defender on top of him).

        The nearest defender, where he will be in a moment (distance minus what he closes), through a Gaussian: 6 m away and not
        closing is about zero, 2 m is about .6, contact is 1. A fresh ball with a defender around makes it harder still (no time to
        set the feet), a long hold does not. Used for pass and shot execution, the decision noise and the chance to get a shot away."""
        t = i // 11
        opp = s._opp(t)
        rel = s.pos[opp] - s.pos[i]
        d = nrm(rel, axis=1)
        clos = -((s.vel[opp] - s.vel[i]) * rel).sum(1) / np.maximum(d, 1e-6)
        d_eff = float(np.min(np.maximum(d - TUNE["close_t"] * np.maximum(clos, 0.), .3)))
        pe = float(np.exp(-(d_eff / TUNE["press_len"]) ** 2))
        hurry = float(clip(1. - (s.t - s.ctrl_t) / 1.0, 0, 1)) if s.carrier == i else .0
        return float(clip(pe * (1. + TUNE["hurry_k"] * hurry), 0, 1))

    def _exec_scale(s, pe):
        """Multiplier on the execution error of a pass: small with time and space, large under pressure."""
        return TUNE["exec_k"] * (TUNE["exec_floor"] + (1. - TUNE["exec_floor"]) * pe)

    def _lane_block(s, C, defenders):
        """Chance that the shot is blocked: every defender between the shooter and the goal can get a body in the way, more the nearer he stands to the
        line of the shot and the nearer he is to the shooter. The constants are the fit of this exact form to real shots (1481 non-header shots with
        freeze frames: blocked or not against where the defenders stood): at most about .47 for a man right on the line, a lateral width of about
        1 m, falling over about 11 m along the line. C and defenders are in the attacking-left-to-right frame, the keeper is not in `defenders`."""
        g = np.array([L, W / 2]) - C
        dg = float(np.hypot(*g))
        if dg < 1e-6 or not len(defenders):
            return TUNE["blk_b0"]
        u = g / dg
        rel = np.asarray(defenders, float) - C
        al = rel @ u
        perp = np.abs(rel[:, 0] * u[1] - rel[:, 1] * u[0])
        m = (al > .3) & (al < dg)
        bj = TUNE["blk_a"] * np.exp(-(perp[m] / TUNE["blk_sig"]) ** 2) * np.exp(-al[m] / TUNE["blk_lam"])
        return float(1. - (1. - TUNE["blk_b0"]) * np.prod(1. - bj))

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

    def _fade_possession(s):
        for t in (0, 1):
            goal = 1. if s.poss == t else 0. if s.poss == 1 - t else .5
            tau = TUNE["fade_up"] if goal > s.poss_fade[t] else TUNE["fade_dn"]
            s.poss_fade[t] += (goal - s.poss_fade[t]) * (1 - np.exp(-s.dt / tau))

    def _step(s):
        s._fade_possession()
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
        u0 = urg.copy()
        urg = u0 | (s.t < s.urg_until)  # a burst is held for a moment, not switched on and off from tick to tick
        s.urg_until = np.where(u0, s.t + TUNE["burst_hold"], s.urg_until)
        a = np.where(urg, 1 - np.exp(-dt / .12), 1 - np.exp(-dt / tau))[:, None]
        s.tsm = s.tsm + (tg - s.tsm) * a
        d = s.tsm - s.pos
        dist = nrm(d, axis=1)
        vmax = s.vmax * (1 - .15 * (1 - s.stam))
        energy = s.mod["cruise"] * movement.CRUISE[s.role] * TUNE["cruise_k"]  # role/trait work rate: how much ground a player covers
        near_ball = nrm(tg - s.ball, axis=1) < 6.
        if s.state == "flight":
            near_ball |= nrm(tg - s.flight["end"], axis=1) < 6.
        sprint = urg & s.sprint_hint  # all-out only when going for the ball (or running onto it); marking and recovering is a hard run
        d_ball = nrm(s.pos - s.ball, axis=1)
        mind = movement.MIND[s.role]  # how attack-minded the role is
        fade_own = s.poss_fade[np.arange(22) // 11]
        lazy_role = mind + fade_own * (1. - 2. * mind)  # forwards idle when their side has no ball, defenders when their side has it all
        lazy = np.clip((d_ball - 20.) / 45., 0, 1) * (.5 + .5 * lazy_role) * (s.state != "dead")  # at a dead ball everyone gets to his place at a normal jog
        jog = np.minimum(vmax, (4.3 + .07 * s.attr["work_rate"]) * energy) * (1. - TUNE["lazy_k"] * lazy)
        if s.state == "dead":
            jog = np.minimum(vmax, np.maximum(jog, 3.6))  # between phases everyone jogs to his place; the open-play work-rate cap would turn a centre-back's run to a corner into a walk
        cap = np.where(sprint, vmax, np.where(urg, vmax * TUNE["run_frac"], jog))
        if s.state == "held":
            cap[s.carrier] = min(cap[s.carrier], s.carry_speed * s.carry_scale, .85 * vmax[s.carrier])
        if s.cap_s is None:
            s.cap_s = cap.copy()
        s.cap_s += (cap - s.cap_s) * (1 - np.exp(-dt / np.where(cap > s.cap_s, .5, 1.0)))  # speed builds and fades over about a second, it does not jump
        cap = s.cap_s
        vdes = d / np.maximum(dist, 1e-6)[:, None] * np.minimum(cap, np.maximum(dist - .4, 0) * 1.1 * np.where(urg, 1., energy))[:, None]
        dv = vdes - s.vel
        n = nrm(dv, axis=1)
        turn_ = (1 - (s.head * _unit(vdes)).sum(1)) / 2 * (nrm(s.vel, axis=1) > 1.5)  # 0 = carrying on, 1 = reversing
        s.vel += dv * np.minimum(1, s.acc * (1 - TUNE["turn_cost"] * turn_) * dt / np.maximum(n, 1e-6))[:, None]
        s.pos = s.pos + s.vel * dt
        spd_ = nrm(s.vel, axis=1)
        carrier_ = np.zeros(22, bool)
        if s.state == "held":
            carrier_[s.carrier] = True
        run_ = (spd_ > TUNE["head_run"]) | (carrier_ & (spd_ > 1.5))
        h_run = _unit(s.head * .4 + _unit(s.vel) * .6)  # flat out (or carrying the ball): he faces where he runs
        h_play = _unit(s.head + (s._play_heading() - s.head) * float(clip(TUNE["head_rate"] * dt, 0, 1)))  # otherwise his body turns to the play
        s.head = np.where(run_[:, None], h_run, np.where(carrier_[:, None], s.head, h_play))
        r = s.pos[:, None] - s.pos[None]
        dd = np.maximum(nrm(r, axis=2), 1e-3) + np.eye(22) * 99
        s.pos += (r / dd[..., None] * ((1.6 - dd).clip(0))[..., None]).sum(1) * .35
        raw = s.pos.copy()
        s.pos = clip(s.pos, [0, 0], [L, W])  # nobody plays outside the lines: a wide player who overshoots his spot stops on the line
        s.vel = np.where(s.pos != raw, 0., s.vel)
        if s.state == "dead" and s.dead.get("taker") is not None:  # the taker of a throw-in or corner stands just outside
            tk = int(s.dead["taker"])
            s.pos[tk] = clip(raw[tk], [-1, -1], [L + 1, W + 1])
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
                                        s.pos.ravel(), [s._ball_z()], np.round(np.arctan2(s.head[:, 1], s.head[:, 0]), 2)]))

    # ------------------------------------------------------------------ possession
    def _dwell(s, i):
        """Seconds until this player decides: a reaction time that shrinks under pressure; a first-time ball when
        the chance is obvious (a shooting position) or the defender is on him and he is a good passer."""
        t = i // 11
        f = _tf if t else (lambda x: x)
        pr = clip(1 - nrm(s.pos[s._opp(t)] - s.pos[i], axis=1).min() / 5, 0, 1)
        xa = f(s.pos[i])[0]
        ft = float(clip((xa - .62 * L) / (.2 * L), 0, 1))  # the final third is played more carefully: more look-ups, fewer first-time balls
        react = TUNE["react"] * (1.4 - .015 * (s.attr["composure"][i] + s.attr["vision"][i])) * (1 - .2 * pr) * (1 + TUNE["free_dwell"] * (1 - pr)) * (1 + TUNE["ft_slow"] * ft)  # with nobody near he takes his time (looks up, shows for it, passes it back and forth); pressure does not make a real player hold it any shorter
        xg_here, _ = s._xg(f(s.pos[i]), [])
        if xg_here > .08 and s.role[i] > 0 and s._face_goal(i) > .55 and s.rng.random() < TUNE["ft_shot"]:  # in front of goal, facing it and the ball sits right: hit it first time (otherwise he takes a touch first)
            return float(s.rng.uniform(.05, .2))
        if s.rng.random() < clip(.2 + .15 * pr + .02 * (s.attr["passing"][i] - 10), 0, .8) * s.first_time_ok * (1 - TUNE["ft_slow"] * .5 * ft):
            return float(s.rng.uniform(.05, .22))  # one touch
        return float(clip(react * np.exp(s.rng.normal(0., TUNE["hold_sigma"]) - .5 * TUNE["hold_sigma"] ** 2), .12, 9.))  # a skewed spread with the same mean: many quick releases, now and then a long dwell on the ball (real: 31% at once, 12% over 3 s)

    def _play_heading(s):
        """Where each player's body wants to point when he is not running flat out.

        Attackers face the ball (a little towards goal); defenders face the man they mark or the ball and rarely turn their
        chest to their own goal; keepers face the ball."""
        B = s.ball
        out = np.zeros((22, 2))
        for t in (0, 1):
            sl = s._own(t)
            P = s.pos[sl]
            goal_opp = np.array([L if t == 0 else 0., W / 2])
            goal_own = np.array([0. if t == 0 else L, W / 2])
            dB = _unit(B - P)
            if s.poss == t:
                h = _unit(.65 * dB + .35 * _unit(goal_opp - P))
            else:
                mk = s.mark[t]
                O = s.pos[s._opp(t)]
                pt = np.where((mk >= 0)[:, None], O[np.maximum(mk, 0)], B)  # the man he is marking, else the ball
                h = _unit(.55 * _unit(pt - P) + .45 * dB)

            own = _unit(goal_own - P)
            over = np.maximum((h * own).sum(1) - TUNE["own_goal_cone"], 0.)
            h = _unit(h - own * over[:, None])  # nobody turns his chest to his own goal: he stands side-on at most, back to his own goal
            h[0] = dB[0]
            out[sl] = h
        return out

    def _face_goal(s, i):
        """0 = back to goal, 1 = facing it."""
        t = i // 11
        g = _unit(np.array([L if t == 0 else 0., W / 2]) - s.pos[i])
        return float((1 + s.head[i] @ g) / 2)

    def _gain_control(s, i, at):
        t = i // 11
        bv = s.ball_v.copy()  # the ball's direction as it arrives
        if nrm(bv) > .5:  # a standing receiver faces the ball (back to goal when it comes from behind him); a runner faces along his run;
            sp_ = float(nrm(s.vel[i]))
            w_run = float(clip(sp_ / 4., 0, 1))
            h = _unit(w_run * _unit(s.vel[i]) + (1 - w_run) * (-_unit(bv)) + s.rng.normal(0, .05, 2))
            tech = (s.attr["dribbling"][i] + s.attr["composure"][i]) / 2
            dmk = float(nrm(s.pos[s._opp(t)] - s.pos[i], axis=1).min())
            opn = float(clip(.1 + .5 * sig(.25 * (tech - 11)), .1, .6)) * float(clip((dmk - .8) / 3., 0, 1))  # a good first touch takes a half-turn towards goal, unless a marker is on him
            g = _unit(np.array([L if t == 0 else 0., W / 2]) - s.pos[i])
            s.head[i] = _unit(h * (1 - opn) + g * opn + s.rng.normal(0, .05, 2))
        prev = s.last_touch
        if s.last_ctrl_team is not None and s.last_ctrl_team != t and prev >= 0 and prev // 11 != t:
            lt = prev // 11
            pos_t = _tf(np.asarray(at, float)) if lt else np.asarray(at, float)
            s.turnover = dict(t=s.t, team=t, loser=prev, pos=np.asarray(at, float).copy(), own_half=bool(pos_t[0] < .5 * L), used=False)
        if s.state == "loose" and s.role[i] == 0:  # the keeper gets to a loose ball: he collects it
            own_box = (at[0] < 16.5 if t == 0 else at[0] > L - 16.5) and abs(at[1] - W / 2) < 20.16
            s.emit("keeper_claim" if own_box else "keeper_sweep", player=i, pos=at)
        if s.state == "loose" and s.last_ctrl_team != t:
            s.emit("ball_recovery", player=i, pos=at)
        if s.pending_pass is not None:
            pp, s.pending_pass = s.pending_pass, None
            same = pp["team"] == t
            pp["outcome"] = "complete" if same else "intercepted"
            if same:
                s.last_pass_to[t] = (pp["player"], i, s.t, pp)
                s.emit("ball_receipt", player=i, pos=at, outcome="complete", passer=pp["player"])
            elif s.state == "flight" and s.role[i] == 0:  # the keeper collects it: inside the box a claim, outside a sweep
                own_box = (at[0] < 16.5 if t == 0 else at[0] > L - 16.5) and abs(at[1] - W / 2) < 20.16
                s.emit("keeper_claim" if own_box else "keeper_sweep", player=i, pos=at)
            elif s.state == "flight":
                if nrm(np.asarray(at) - s.flight["start"]) < 2.5:  # right next to the passer: a block, not a read of the pass
                    s.emit("block", player=i, pos=at, shooter=pp["player"], kind="pass")
                elif s.flight.get("kind") != "lofted":  # winning a high ball is an aerial duel (logged there), not an interception
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
        s.chance_xg, s.chance_t = .09, s.t  # only a real chance (about .12 and better) restarts the clock below
        s.next_dec = s.t + s._dwell(i)
        s.protect_until = s.t + (.15 if float(nrm(s.pos[s._opp(t)] - s.pos[i], axis=1).min()) < 1.6 else .6)  # a defender on the receiver contests him at once; with space he gets a moment
        s.carry_tgt = s.pos[i] + np.array([1., 0.] if t == 0 else [-1., 0.]) * 5
        s.carry_dir, s.carry_speed = np.array([1., 0.] if t == 0 else [-1., 0.]), TUNE["carry_v0"] + TUNE["carry_v1"] * float(clip(nrm(s.pos[s._opp(t)] - s.pos[i], axis=1).min() / 8, 0, 1))  # a run with the ball: quicker with space, slower with a defender on him
        v0 = float(nrm(s.vel[i]))
        if v0 > 2.5:  # arrived on the move: he carries on in the same direction at nearly the same pace
            s.carry_dir = _unit(s.vel[i])
            s.carry_speed = float(min(max(s.carry_speed * .6, TUNE["mom_k"] * v0), .85 * s.vmax[i]))
        elif s._face_goal(i) < .5:  # back to goal and not moving: he shields and turns, he does not run
            s.carry_dir, s.carry_speed = s.head[i].copy(), .8
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
        opp = np.arange(11 * (1 - t), 11 * (1 - t) + 11)
        d = nrm(s.pos[opp] - s.pos[c], axis=1)
        face = s.head[c]
        if nrm(v) < 1.5:  # standing or shuffling: turn towards goal, but a defender on his back holds him
            g_ = _unit(np.array([L if t == 0 else 0., W / 2]) - s.pos[c])
            behind = ((s.pos[opp] - s.pos[c]) @ face / np.maximum(d, 1e-6) < -.2) & (d < 2.6) & (s.role[opp] > 0)
            pb = float((1 - d[behind] / 2.6).max()) if behind.any() else 0.
            s.head[c] = face = _unit(face + (g_ - face) * float(clip(TUNE["turn_rate"] * (1 - .75 * pb) * s.dt, 0, 1)) + s.rng.normal(0, .02, 2))
        fg = s._face_goal(c)
        if fg > .5 and nrm(v) < 1.5 and s.next_dec > s.t:
            s.carry_dir = _unit(np.array([L if t == 0 else 0., W / 2]) - s.pos[c]) if s.carry_speed < 1.2 else s.carry_dir
        s.ball, s.ball_v = s.pos[c] + face * (.4 + .1 * min(float(nrm(v)), 7.)), v.copy()  # close control when walking, the ball a stride ahead at a sprint: the faster he runs the more exposed it is
        for k in np.nonzero((d < 1.7) & (s.role[opp] > 0))[0]:  # a defender within a couple of metres is pressing the carrier
            if s.t - s.last_press[opp[k]] > 4.:
                s.emit("pressure", player=opp[k], pos=s.pos[opp[k]], target=s.ids[c], counterpress=bool(s.t < s.trans_until[1 - t]))
                s.last_press[opp[k]] = s.t
        # Carrying the ball is hard with a defender in the way: it slows him, and a defender who is in front of or alongside him gets a
        # real chance at the ball. A defender who is behind him (chasing) or has already been beaten hardly does.
        rel = s.pos[opp] - s.pos[c]
        cosang = (rel @ face) / np.maximum(d, 1e-6)  # +1: straight ahead of the carrier
        front = clip(.4 + .6 * clip(cosang + .35, 0, 1), .25, 1.)
        ahead = (cosang > .55) & (d < 9.) & (s.block_until[opp] <= s.t) & (s.role[opp] > 0)
        df = float(d[ahead].min()) if ahead.any() else 9.
        s.carry_scale = float(clip(.5 + .5 * df / 7., .5, 1.))  # the dribbler cannot run at full pace into a defender who is set in front of him
        if s.t >= s.protect_until:
            xt = s.pos[c][0] if t == 0 else L - s.pos[c][0]
            danger = clip((xt - .6 * L) / (.35 * L), 0, 1)
            contact = np.exp(-(nrm(s.pos[opp] - s.ball, axis=1) / TUNE["contact_r"]) ** 2)  # reach for the BALL (a stride and a lunge), not for the man: a defender 1.5-2 m from the ball is in a real contest
            dface = clip(.5 + .5 * ((s.head[opp] * -rel).sum(1) / np.maximum(d, 1e-6) + 1) / 2, .5, 1.)  # defenders who face the carrier can challenge, those looking elsewhere less so
            a = s.attr
            edge_v = clip(.12 * (nrm(s.vel[c]) - nrm(s.vel[opp], axis=1)), -.25, .25)  # a clear pace advantage lets the carrier slip past
            p_w = sig(.22 * ((a["tackling"][opp] + .3 * a["strength"][opp]) - (a["dribbling"][c] + .3 * a["strength"][c])) - .1 - edge_v * 3 + TUNE["exposed_k"] * (contact - .4))  # a ball close to his foot is a ball he wins
            sel = sig(TUNE["tackle_sel"] * (p_w - .4))  # a defender goes in when he likes his chances; otherwise he contains and waits
            lam = TUNE["tackle_rate"] * dface * s.mod["tackle"][opp] * (1 + 1.5 * danger) * (1 + TUNE["first_touch"] * np.exp(-(s.t - s.ctrl_t) / .5)) * front * contact * sel * (s.block_until[opp] <= s.t)
            lam[s.role[opp] == 0] = (TUNE["tackle_rate"] * dface * s.mod["tackle"][opp] * (1 + 1.5 * danger) * front * contact * (s.block_until[opp] <= s.t))[s.role[opp] == 0]  # the keeper commits to a ball at the feet whatever the odds
            att = np.nonzero(s.rng.random(11) < lam * s.dt)[0]  # the first touch is the moment the ball is most exposed
            if len(att):
                ia = att[np.argmin(d[att])]
                o = opp[ia]
                driving = bool(nrm(v) > 1.5 and s.t - s.ctrl_t > .5)  # a dribble is a choice to run at a man; being closed down on receiving the ball is not
                edge = float(edge_v[ia])
                kp = bool(s.role[o] == 0)  # a keeper does not tackle: he comes out, spreads himself and smothers the ball at the carrier's feet
                p_win = float(p_w[ia]) if not kp else sig(.22 * ((a["gk"][o] + .3 * a["strength"][o]) - (a["dribbling"][c] + .3 * a["strength"][c])) - .1 + .5 - edge * 3)
                s.protect_until = s.t + .7
                if kp and s.rng.random() < p_win:
                    if s.rng.random() < .12 * s.mod["foul"][o]:
                        s._foul(o, c)
                    else:
                        at_ = s.pos[c].copy()
                        in_own_box = (at_[0] < 16.5 if (1 - t) == 0 else at_[0] > L - 16.5) and abs(at_[1] - W / 2) < 20.16
                        s.emit("keeper_claim" if in_own_box else "keeper_sweep", player=o, pos=at_, technique="smother")
                        s.emit("dispossessed", player=c, pos=at_)
                        s.block_until[c] = s.t + .8
                        s._end_spell()
                        s._gain_control(o, at_)
                    return
                if s.rng.random() < p_win:
                    if s.rng.random() < (.4 + .2 * (1 - a["tackling"][o] / 20)) * s.mod["foul"][o] * (.75 + .5 * s.ref):
                        s._foul(o, c)
                    else:
                        s.emit("tackle", player=o, pos=s.pos[c], target=s.ids[c], outcome="won")
                        if front[ia] > .7 and driving:  # he was running at a man and lost the duel: a failed dribble (a dispossession is losing it without trying to go past anyone)
                            s.emit("take_on", player=c, pos=s.pos[c], target=s.ids[o], outcome="lost", p_success=1 - float(p_win))
                        else:
                            s.emit("dispossessed", player=c, pos=s.pos[c])
                        if s.rng.random() < .004:
                            s._injury(c, "tackle")
                        s.block_until[c] = s.t + .8
                        s.last_touch = o
                        s._loose_ball(_unit(s.vel[o] + s.rng.normal(0, 1, 2)) * 2.5)
                    return
                if front[ia] > .7 and driving:  # he was running at a man and got past him: a take-on
                    s.emit("take_on", player=c, pos=s.pos[c], target=s.ids[o], outcome="won", p_success=1 - float(p_win))
                    s.emit("dribbled_past", player=o, pos=s.pos[o], by=s.ids[c])
                    s.block_until[o] = s.t + 1.2
                else:
                    s.emit("tackle", player=o, pos=s.pos[c], target=s.ids[c], outcome="lost")
        if s.t >= s.protect_until and s.next_dec > s.t + .3 and s._exec_pressure(c) > TUNE["read_press"]:  # he sees the man coming and does not wait for the dwell to run out
            s.next_dec = s.t + .3
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
        s.emit("foul_won", player=c, pos=s.pos[c], by=s.ids[o])
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
        pe = s._exec_pressure(c)  # time and space for execution (passes, shots, decisions)
        Vf = lambda p: np.exp(-np.hypot(L - p[..., 0], W / 2 - p[..., 1]) / 22)
        Vw = lambda p: TUNE["wide_v"] * np.exp(-np.minimum(np.hypot(L - 8 - p[..., 0], W / 2 + 24 - p[..., 1]), np.hypot(L - 8 - p[..., 0], W / 2 - 24 - p[..., 1])) / 14)  # a wide delivery position is worth something too
        Tf = lambda p: TUNE["adv_lin"] * p[..., 0] / L + np.maximum(Vf(p), Vw(p))  # territory: a little linear progress everywhere plus the better of 'close to goal' and 'in a crossing position'
        VC = Vf(C)
        TC = Tf(C)
        prog_w = (TUNE["prog_base"] + TUNE["prog_dir"] * tc.directness) * s.mod["prog"][c]
        # losing the ball hurts most near your own goal and least in the opponent's final third
        if TUNE["xt_k"] > 0:  # the value of a position is the expected threat of holding the ball there; what a lost ball costs is the possession's value plus the threat the opponent gets where it is lost
            Tf = lambda p: TUNE["xt_k"] * XT(p)
            XC = float(XT(C))
            loss_at = lambda p: TUNE["loss"] * s.mod["loss"][c] * TUNE["xt_k"] * (XC + TUNE["xt_opp"] * XT(np.array([L, W]) - np.asarray(p, float)))
            TC = Tf(C)
        else:
            loss_at = lambda p: TUNE["loss"] * s.mod["loss"][c] * (.45 + 1.1 * (1 - np.asarray(p, float)[..., 0] / L))  # cost of losing it at pitch position x
        loss_eff = loss_at(C)
        if rk == "penalty":
            return s._shoot(c, C, xg=.76, kind="penalty", nb=0)
        if rk == "corner":
            return s._corner(c, f)
        U, K, X = [], [], []
        best_mate = 0.  # value of the best chance he could set up for a teammate (his xG x pass success)
        # -- passes
        mates = np.array([i for i in range(11) if i != ci])
        face = fv(s.head[c])  # he sees what is in front of the way he faces
        fg = s._face_goal(c)
        dv = P[mates] - C
        dist = nrm(dv, axis=1)
        cosang = dv @ face / np.maximum(dist, 1e-6)
        vis = (dist > 3.5) & (dist < 18 + 2.2 * a["vision"][c]) & (cosang > np.cos(np.radians(min(180, 100 + 4 * a["vision"][c]))))
        if rk in ("throw_in", "drop_ball", "indirect_free_kick"):
            vis &= dist < 28
        if rk == "goal_kick":
            vis = (dist > 8) & (dist < 68) & ~s.off[11 * t + mates]  # a goal kick can reach the halfway line and beyond, whatever his usual range of vision
        if not (C[0] < .45 * L or pr > .4):
            vis &= s.role[11 * t + mates] != 0
        vis &= ~s.off[11 * t + mates]
        if vis.any():
            m_ = mates[vis]
            dist0 = nrm(P[m_] - C, axis=1)
            air = np.nonzero((dist0 > 12.) & (dist0 < (68. if rk == "goal_kick" else 55.)))[0]  # any target further than a dozen metres can also be played in the air (chip, diagonal, long ball)
            thr_i = np.nonzero((dist0 > 10.) & (dist0 < 42.) & (P[m_][:, 0] - C[0] > 6.) & (s.role[11 * t + m_] > 0) & (rk is None))[0]  # forward targets in open play can be played into the space ahead of them
            lofted = np.concatenate([np.zeros(len(m_), bool), np.ones(len(air), bool), np.zeros(len(thr_i), bool)])
            through = np.concatenate([np.zeros(len(m_) + len(air), bool), np.ones(len(thr_i), bool)])
            m_ = np.concatenate([m_, m_[air], m_[thr_i]])
            Pm, Vm = P[m_], Vp[m_]
            dist = nrm(Pm - C, axis=1)
            spd = clip(TUNE["ball_v0"] + TUNE["ball_v1"] * dist, TUNE["ball_v0"], 22)
            runner = s.t < s.run_until[11 * t + m_]  # a runner is played into space well ahead of him: through ball
            tgt = clip(Pm + Vm * (np.minimum(dist / spd, 1.5) * np.where(runner, 1.3, .8))[:, None], [1, 1], [L - 1, W - 1])
            gdir = _unit(_unit(np.array([L, W / 2]) - Pm) + .6 * _unit(Vm + 1e-6))
            tgt = np.where(through[:, None], clip(Pm + gdir * (5. + 5. * runner)[:, None], [1, 1], [L - 1, W - 1]), tgt)  # into space: he must run onto it
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
            dd_r = nrm(O[None] - tgt[:, None], axis=2)
            dn_r = dd_r.min(1)
            cong_r = np.exp(-dd_r / 3.).sum(1)  # how crowded the receiver's space is: bodies within a few metres
            tech = (a["dribbling"][11 * t + m_] + a["composure"][11 * t + m_]) / 2
            p_ctrl = clip((.99 - TUNE["ctrl_marked"] * np.exp(-(dn_r - .5) / 1.6) + .004 * (tech - 10)) * np.exp(-TUNE["cong_k"] * np.maximum(cong_r - 1., 0)), .2, .98)  # a defender on the receiver contests the first touch
            p_air = clip((.55 + .02 * (a["aerial"][11 * t + m_] - a["aerial"][s._opp(t)].mean())) * np.exp(-.3 * np.maximum(cong_r - .6, 0)), .15, .85)  # an aerial contest: his height against the defenders around him
            p_ctrl = np.where(lofted, p_air, p_ctrl)
            sg = (.5 + .05 * d2) * (TUNE["pass_sigma"] * (1 - .0353 * a["passing"][c])) * s._exec_scale(pe) * np.where(lofted, 1.5, np.where(through, 1.4, 1.))
            p_acc = 1 - np.exp(-(2.5 ** 2) / (2 * sg ** 2))
            # a ball into space is a race: the receiver against the defenders and the ball's own travel time
            t_r = nrm(tgt - Pm, axis=1) / (s.vmax[11 * t + m_] * .85) + .2
            t_def = (nrm(O[None] - tgt[:, None], axis=2) / (s.vmax[s._opp(t)] * .85)[None]).min(1) + .25
            p_time = sig(2.5 * (t_def - np.maximum(t_r, d2 / spd)))
            p_s = (1 - p_int) * p_acc * p_ctrl * np.where(through, p_time, 1.)
            line = s.line_hist[t][0]  # second-last defender as the passer last saw him; defenders may have stepped up since
            Pp = Pm - Vm * .5  # runners are seen where they were half a second ago, so mistimed runs end up offside
            off_m = (Pp[:, 0] > np.maximum(line, C[0]) + .3) & (Pp[:, 0] > L / 2)
            off_m &= s.rng.random(len(off_m)) > TUNE["off_miss"] - .012 * a["vision"][c]  # now and then the passer misjudges the line
            p_s = p_s * np.where(off_m, TUNE["off_mult"], 1.)
            byp = ((O[None, :, 0] > C[0] + 1) & (O[None, :, 0] < tgt[:, 0, None] - 1) & (dperp < 18) & (s.role[s._opp(t)] > 0)[None]).sum(1)  # opponents this pass takes out of the game
            space_r = clip(dn_r / 6., 0, 1)  # time and room the receiver will have
            zf = float(clip(1. - abs(C[0] / L - .47) / .3, .25, 1.))  # building up and recycling happen in midfield; near either box the game is played more directly
            # onward potential: from the receiver's spot, the best open lane to a team-mate who would be further up the pitch (a recycled ball is worth
            # more when the man we give it to can then play forward; a ball into a dead end is worth less)
            others = P[np.array([i for i in range(11) if i != ci])]
            seg = others[None] - tgt[:, None]  # (n, k, 2) from receiver to every other team-mate
            sl2 = np.maximum((seg ** 2).sum(-1), 1e-6)
            sq = clip(((O[None, None] - tgt[:, None, None]) * seg[:, :, None]).sum(-1) / sl2[..., None], 0, 1)  # (n, k, 11) projection of each opponent on each lane
            lane_open = clip(nrm(O[None, None] - (tgt[:, None, None] + sq[..., None] * seg[:, :, None]), axis=-1).min(-1) / 3., 0, 1)
            gain_k = np.maximum(Tf(others)[None] - Tf(tgt)[:, None], 0.) * (np.sqrt(sl2) > 5.) * (np.sqrt(sl2) < 35.)
            pot = (lane_open * gain_k).max(1)
            relief = pr * space_r  # pressed: the freest man is the way out
            u = p_s ** (TUNE["risk_pow_xt"] if TUNE["xt_k"] > 0 else TUNE["risk_pow"]) * (prog_w * (Tf(tgt) - TC) + TUNE["pack"] * np.minimum(byp, 4) + TUNE["keep"] + zf * (TUNE["pot"] * space_r * pot + TUNE["relief"] * relief)) - (1 - p_s) * loss_at(tgt) - .001 * d2 - TUNE["usage"] * s.recent[11 * t + m_]
            inbox_m = np.nonzero((tgt[:, 0] > L - 24) & (np.abs(tgt[:, 1] - W / 2) < 22))[0]
            for j_ in inbox_m[np.argsort(-Vf(tgt[inbox_m]))][:4]:  # could a teammate get a better chance than mine?
                best_mate = max(best_mate, float(p_s[j_]) * s._xg(tgt[j_], O[s.role[s._opp(t)] > 0])[0])
            u = u + TUNE["long_bonus"] * (.3 + tc.directness) * ((d2 > 28) & (Vf(tgt) - VC > .06))  # direct teams go long
            u = u + (TUNE["air_base"] + TUNE["air_k"] * (tc.directness - .4)) * lofted  # direct sides play it in the air more, patient ones keep it on the floor
            if rk == "goal_kick":  # a goal kick is mostly a long ball to a contest, unless the opponent leaves the short option free
                u = u + TUNE["gk_long"] * tc.directness * (d2 > 30) + TUNE["gk_air"] * lofted * clip((d2 - 25.) / 20., 0, 1)
            for k in range(len(m_)):
                U.append(u[k]); K.append("pass"); X.append((11 * t + m_[k], tgt[k], bool(lofted[k]), spd[k], p_s[k]))
        # -- shot
        dg = np.hypot(L - C[0], W / 2 - C[1])
        if dg < 35 + s.mod["range"][c] and rk in (None, "free_kick"):
            xg, nb = s._xg(C, O[s.role[s._opp(t)] > 0])
            xg = xg * (.4 + .6 * fg) if rk is None else xg  # a shot with his back to goal is a turn-and-hit: poor
            if rk == "free_kick":  # a set ball over the wall: no open-play blockers, specialists bend it round
                xg, _ = s._xg(C, [])
                xg = float(clip(xg * 2.2 * (1 + .25 * s.mod["fk"][c] + .02 * (a["finishing"][c] - 10)), 0, .5))
                nb = 2
            take = False
            if rk is None and s.role[c] > 0:
                # willingness to shoot follows where he is (a clean-look xG from distance and angle), not whether a defender happens to be in the way:
                # real shooters take it through traffic (about two defenders in the shot triangle) and the defence blocks a third of them
                xg0, _ = s._xg(C, [])
                gk_d = float(nrm(O[0] - C))
                gk_close = bool(O[0][0] > C[0] and gk_d < 13.)  # the keeper is coming out at him: this is the moment, whatever the clock says
                gk_prox = float(clip((13. - gk_d) / 7., 0, 1)) * gk_close
                if xg0 > 1.3 * s.chance_xg:  # a clearly better chance than any he has had with this ball: running at goal does not use up the chance he had 30 m back
                    s.chance_xg, s.chance_t = xg0, s.t
                p_lane = s._lane_block(C, O[s.role[s._opp(t)] > 0])  # a man in the way of the shot is a reason not to shoot, however good the position
                z = TUNE["shoot_slope"] * np.log(xg0 * (1. - p_lane) ** TUNE["shot_lane_k"] / TUNE["shoot_ref"]) + 1.5 * np.log(SHOT_PROP[s.role[c]]) + 1.2 * (tc.shoot - .5) + np.log(s.mod["shot"][c]) + TUNE["shoot_press"] * pr - TUNE["shoot_hold"] * (min(s.t - s.chance_t, .6) if gk_close else s.t - s.chance_t) + TUNE["gk_shoot"] * gk_prox + TUNE["face_k"] * 2. * (fg - .5)  # the chance is taken early: a player who keeps carrying has passed it up
                take = bool(s.rng.random() < sig(z)) and best_mate < 1.5 * xg
            elif rk == "free_kick":
                take = s.role[c] > 0 and xg >= .08
            U.append((100. if take else 0.) + (TUNE["shot_k"] + TUNE["shot_k_dir"] * tc.shoot) * s.mod["shot"][c] * ROLE_SHOT[s.role[c]] * (TUNE["shot_mid"] if s.role[c] in (3, 4, 6) else 1.) * xg * (.8 + .02 * a["finishing"][c]) - TUNE["shot_cost"] + (TUNE["fk_bonus"] if rk == "free_kick" and dg < 28 else 0)); K.append("shot"); X.append((xg, nb))
        # -- cross from a wide position into the box
        if (rk is None and abs(C[1] - W / 2) > 13 and C[0] > .58 * L) or (rk == "free_kick" and C[0] > .62 * L and dg >= 20):  # a free kick too far or too tight for a shot is put into the box
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
                U.append(p_c * (TUNE["cross_v"] + TUNE["keep"]) - (1 - p_c) * loss_eff * .4 + s.mod["carry"][c] * .5 + (TUNE["fk_cross"] if rk == "free_kick" else 0.))
                K.append("cross"); X.append((11 * t + tgt_i, aim, p_c))
        # -- carry / clearance
        if rk is None:
            toward = _unit(np.array([L, W / 2 + (C[1] - W / 2) * LANE[s.role[c]]]) - C)  # wide players keep their lane, central players head for goal
            near = dn < 8
            away = (_unit(C - O[near]) * ((8 - dn[near]) / 8)[:, None]).sum(0) if near.any() else 0
            dirv0 = _unit(toward + .8 * away)
            rel = O - C
            rel[0] = 99.  # the keeper is not an obstacle to a run at goal: it ends in a shot or a dribble round him, and the shot terms price him in
            best_c = None
            for ang in (0., -35., 35., -70., 70.):  # he looks at several lines to run: straight on, either side of a man, wide; the best by progress and by the risk of the duel geometry
                ca, sa = np.cos(np.radians(ang)), np.sin(np.radians(ang))
                dirv = _unit(np.array([dirv0[0] * ca - dirv0[1] * sa, dirv0[0] * sa + dirv0[1] * ca]))
                fwd_d = rel @ dirv  # a carrier drives when there is room: nobody in the cone ahead of him
                lat_d = np.abs(rel[:, 0] * dirv[1] - rel[:, 1] * dirv[0])
                free_ahead = float(np.min(np.where((fwd_d > 1) & (lat_d < np.maximum(2.5, .35 * fwd_d)), fwd_d, 40.)))
                open_ = float(clip((free_ahead - 7.) / 14., 0, 1))
                cpos = clip(C + float(clip(.55 * free_ahead, 7., 15.)) * dirv, [1, 1], [L - 1, W - 1])
                ahead = fwd_d
                lat = lat_d
                blk = np.nonzero((ahead > .3) & (ahead < 5) & (lat < 2.2))[0]
                if len(blk):  # a defender stands in the way: carrying on means a take-on
                    j = blk[np.argmin(ahead[blk])]
                    o = 11 * (1 - t) + j
                    p_beat = float(sig(.22 * ((a["dribbling"][c] + .3 * a["pace"][c]) - (a["tackling"][o] + .3 * a["pace"][o])) - .3)) + s.mod["takeon"][c]
                    p_beat = min(p_beat, .97) * (.5 + .5 * fg)
                else:
                    j, o, p_beat = -1, -1, 1.
                seg = cpos - C  # the risk he runs is the same geometry the duel uses: defenders who can reach the ball path in time, weighted by contact
                sl = max(float(seg @ seg), 1e-6)
                sp_ = clip((rel @ seg) / sl, 0, 1)
                d_seg = nrm(O - (C + sp_[:, None] * seg), axis=1)
                rho = np.maximum(d_seg - .75 * .8 * s.vmax[s._opp(t)] * sp_ * np.sqrt(sl) / 4., 0.)
                lam_c = TUNE["tackle_rate"] * (1. + 1.5 * float(clip((C[0] - .6 * L) / (.35 * L), 0, 1))) * np.exp(-(rho / TUNE["contact_r"]) ** 2) * .6
                if o >= 0:
                    lam_c[o - 11 * (1 - t)] = 0.  # the man he is taking on is priced by p_beat; p_surv is for everyone else on the way (the next man)
                p_surv = float(np.prod(1. - (1. - np.exp(-lam_c * 1.0)) * .5))
                p_keep = p_beat * (.4 + .6 * fg) * p_surv ** TUNE["surv_k"] * float(np.exp(-TUNE["cong_k"] * max(float(np.exp(-nrm(O[1:] - cpos, axis=1) / 3.).sum()) - 1., 0)))  # a dribbler avoids walking into a crowd
                u_c = (p_keep * (TUNE["carry_base"] + TUNE["carry_prog"] * prog_w * (Tf(cpos) - TC)) - (1 - p_keep) * loss_eff - TUNE["carry_streak"] * s.carry_streak * (1. - .8 * open_) + s.mod["carry"][c] + CARRY_ROLE[s.role[c]]
                       + TUNE["patience"] * np.exp(-(s.t - s.ctrl_t) / 2.0) * (1 - pr) * (dnear > 4) + TUNE["drive"] * open_ * (1 - pr)
                       + TUNE["xg_run"] * p_keep * max(s._xg(cpos, [])[0] - s._xg(C, [])[0], 0.))  # running at goal is worth the chance it creates (xg_run term above)
                if best_c is None or u_c > best_c[0]:
                    best_c = (u_c, cpos, o, p_beat)
            U.append(best_c[0]); K.append("carry"); X.append(best_c[1:])
            if C[0] < .2 * L and dnear < 3.5:
                cand_c = [np.array([C[0] + 38 * np.cos(a_), clip(C[1] + 38 * np.sin(a_), 1., W - 1.)]) for a_ in np.radians([-65, -40, -15, 15, 40, 65])]
                sc_c = [-1.5 * float((nrm(O - q_, axis=1) < 9).sum()) + .03 * (q_[0] - C[0]) + (.8 if (q_[1] < 6 or q_[1] > W - 6) and C[0] < .12 * L else 0.) for q_ in cand_c]
                U.append(.2 + .35 * pr); K.append("clear"); X.append(cand_c[int(np.argmax(sc_c))])
        if not U:
            U, K, X = [0.], ["carry"], [(clip(C + 7 * np.array([1., 0.]), [1, 1], [L - 1, W - 1]), -1, 1.)]
        tau = .02 + .04 * (1 - a["composure"][c] / 20)
        U = np.array(U)
        U = U + (U < 50) * s.rng.normal(0., TUNE["dec_noise"] * (.25 + 1.5 * pe) * (1.3 - .03 * a["composure"][c]), len(U))  # misjudging the options: rare with time, common when hurried
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
            s._pass(c, j, f(tgt_), lofted_, spd_, ps, why=why, hands_ok=rk != "goal_kick", source="throw_in" if rk == "throw_in" else "goal_kick" if rk == "goal_kick" else None)
        elif kind == "shot":
            s._shoot(c, C, xg=x[0], nb=x[1], kind="free_kick_shot" if rk == "free_kick" else "shot", why=why)
        elif kind == "clear":
            s._pass(c, None, f(x), True, 20., .5, kind="clearance")
        elif kind == "cross":
            j, aim, pc = x
            s._pass(c, j, f(aim), True, 20., pc, kind="cross", source="free_kick" if rk == "free_kick" else "open_play", why=why, sigma_scale=.8)
        else:
            cpos, o, p_beat = x
            if o >= 0:
                won = s.rng.random() < p_beat
                s.emit("take_on", player=c, pos=s.pos[c], target=s.ids[o], outcome="won" if won else "lost", p_success=p_beat, why=why)
                if won:
                    s.emit("dribbled_past", player=o, pos=s.pos[o], by=s.ids[c])
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
            s.carry_speed = float(min(.85 * s.vmax[c], 3.2 + 2.2 * (1 - pr) + 1.6 * float(clip((float(nrm(cpos - C)) - 7.) / 8., 0, 1))))  # more room, longer and faster strides  # running at a defender or into space: faster when unpressed
            s.carry_streak += 1
            s.next_dec = s.t + s.rng.uniform(.35, .6) * (1. - .3 * pe)  # keep carrying and look again shortly; sooner when someone is coming at him

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
    def _pass(s, c, rcv, aim, lofted, spd, est, kind="pass", sigma_scale=1.0, why=None, source=None, hands_ok=True, body="foot"):
        t = c // 11
        start = s.ball.copy()
        d = nrm(aim - start)
        hands = bool(s.role[c] == 0 and kind == "pass" and hands_ok and d < 34 and s.rng.random() < .5)  # keeper throws it out
        if hands or source == "throw_in":
            spd, lofted = min(spd, 15. if hands else 13.), False
        pe = s._exec_pressure(c)
        al_p = float((1 + s.head[c] @ _unit(aim - start)) / 2)  # 1 = passing the way he faces
        sg = (.5 + .05 * d) * (TUNE["pass_sigma"] * (1 - .0353 * s.attr["passing"][c])) * s._exec_scale(pe) * (1.8 if lofted else 1) * sigma_scale * (1 + .5 * (1 - al_p) * (.4 + .6 * pe))  # with time he turns his body and sets the pass
        sg *= s.rain
        wide = min(aim[1], W - aim[1]) < 9
        end = aim + s.rng.normal(0, sg * (1 + TUNE["wide_error"] * wide * (.3 + .7 * pe)), 2)
        dur = max(nrm(end - start) / spd, .3)
        beta = 0. if kind == "shot" else float(clip(.1 + .006 * nrm(end - start), .1, .5)) if not lofted else .12  # share of the average speed lost between launch and arrival
        gx = L if t == 0 else 0
        fwd = abs(gx - start[0]) - abs(gx - aim[0])  # metres gained towards the opposition goal
        f_ = _tf if t else (lambda x: x)
        line = np.sort(f_(s.pos[s._opp(t)])[:, 0])[-2]
        offside = bool(kind == "pass" and source not in ("short_corner", "throw_in", "goal_kick") and rcv is not None and f_(s.pos[rcv])[0] > max(line, f_(start)[0]) + .3 and f_(s.pos[rcv])[0] > L / 2)
        lat = abs(aim[1] - start[1])
        technique = ("long_ball" if lofted and d > 30 else "switch" if lat > 28 and fwd < 12 else
                     "through_ball" if not lofted and fwd > 10 and f_(aim)[0] > line + 1 else "forward" if fwd > 8 else "backward" if fwd < -5 else "sideways")
        if body == "head" and not hands:
            technique = "header_pass"
        if hands:
            technique = "keeper_throw"
        elif source == "throw_in":
            technique = "throw_in"
        ev = s.emit(kind, player=c, pos=start, end=aim, length=d, speed=spd, lofted=lofted, height="high" if lofted else "ground",
                    body_part="hands" if (hands or source == "throw_in") else body, technique=technique, hold=round(float(s.t - s.ctrl_t), 2), receiver=None if rcv is None else s.ids[rcv], p_success=est, exec_pressure=round(pe, 2),
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
        s.flight = dict(src=source, start=start, end=end, aim=aim, t0=s.t, dur=dur, speed=spd, beta=beta, kind="lofted" if lofted else "ground",
                        team=t, passer=c, rcv=-1 if rcv is None else rcv)
        s.pending_pass = ev
        s.ball_v = (end - start) / dur * (1 + beta)
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
            pe = s._exec_pressure(c) if kind != "penalty" else 0.
            Oo = f(s.pos[s._opp(t)])[s.role[s._opp(t)] > 0]
            if kind == "penalty":
                p_block = 0.
            elif kind == "header":
                p_block = min(.6, TUNE["block_base"] + TUNE["block_nb"] * nb + .08 * pe)
            else:
                p_block = s._lane_block(C, Oo)
            p_off = clip(clip(.55 - 1.0 * xg - .01 * (fin - 10), .2, .6) * (.75 + .5 * pe), .08, .7)  # hurried shots go wide, a free one is placed
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
        dd = nrm(s.pos[opp] - at, axis=1)
        dn = dd.min()
        tech = (a["dribbling"][i] + a["composure"][i]) / 2
        al = float((1 + s.head[i] @ -_unit(s.ball_v)) / 2) if nrm(s.ball_v) > .5 else 1.  # 1 = facing the ball, 0 = ball arriving behind him
        mk = float(np.exp(-max(dn - .5, 0.) / 3.))  # a marker near him: how hard a firm ball is to take
        return float(clip((.995 - .012 * max(0, speed - 12) * (.3 + .7 * mk) + .012 * (tech - 10) - TUNE["ctrl_marked"] * np.exp(-(dn - .5) / 1.6)) * np.exp(-TUNE["cong_k"] * max(np.exp(-dd / 3.).sum() - 1., 0)) * (.95 + .05 * al), .2, .995))  # a defender on the receiver contests the first touch

    def _flight(s):
        f = s.flight
        a0 = s.ball.copy()
        u = min(1., (s.t + s.dt - f["t0"]) / f["dur"])
        be = f.get("beta", 0.)
        b = f["start"] + (f["end"] - f["start"]) * (u + be * u * (1 - u))  # decelerating: fast off the foot, slower on arrival
        s.ball, s.ball_v = b, (f["end"] - f["start"]) / f["dur"] * (1 + be * (1 - 2 * u))
        if f["kind"] == "ground":
            ab = b - a0
            sp = clip(((s.pos - a0) @ ab) / max(ab @ ab, 1e-9), 0, 1)
            q = a0 + sp[:, None] * ab
            d = nrm(s.pos - q, axis=1)
            ok = (d < 1.0) & (s.block_until <= s.t)
            g_ = 11 * (1 - f["team"])  # the defending keeper reaches further and is better at it than outfield players
            ok[g_] = bool(d[g_] < 3.2 + .8 * s.mod["sweep"][g_] and s.block_until[g_] <= s.t)
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
                    seen = float((1 + s.head[i] @ _unit(f["start"] - s.pos[i])) / 2)  # he is facing the way the ball comes from
                    p = float(clip(TUNE["intercept"] + .02 * (ar["positioning"][i] - 10) + .01 * (ar["tackling"][i] - 10) - .02 * (f["speed"] - 12), .15, .9)) * float(clip((s.t - f["t0"]) / .3, .35, 1)) * (.75 + .25 * seen)
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

    def _head_target(s, i):
        """Where a header goes: a team-mate with room, further up the pitch if possible."""
        t = i // 11
        own = np.array([j for j in range(11 * t, 11 * t + 11) if j != i and s.role[j] > 0 and not s.off[j]])
        if not len(own):
            return None
        fwd = np.array([1. if t == 0 else -1., 0.])
        rel = s.pos[own] - s.pos[i]
        d = nrm(rel, axis=1)
        opp_d = np.array([nrm(s.pos[s._opp(t)] - s.pos[j], axis=1).min() for j in own])
        sc = .05 * (rel @ fwd) + .25 * np.minimum(opp_d, 6.) - .08 * np.abs(d - 12.) - 4. * (d < 3.)
        pw = np.exp((sc - sc.max()) / 1.2)
        return int(own[s.rng.choice(len(own), p=pw / pw.sum())])

    def _header_on(s, i, pt):
        """The player who won the ball in the air heads it to a team-mate (a flick-on, a lay-off, a headed pass)."""
        j = s._head_target(i)
        if j is None:
            return False
        aim = clip(s.pos[j] + s.vel[j] * .6, [1, 1], [L - 1, W - 1])
        s._gain_control(i, pt)
        s.ball = pt.copy()
        d = float(nrm(aim - pt))
        est = float(clip(.8 - .01 * d, .35, .9))
        s._pass(i, j, aim, False, float(clip(9 + .35 * d, 9, 18)), est, kind="pass", body="head", sigma_scale=1.3)
        return True

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
        rivals = w * (np.arange(22) // 11 != t) * (d < 3.5)  # only a body within reach of the landing point makes it a duel
        rv = int(rivals.argmax()) if rivals.max() > 0 else -1
        if rv < 0 and t == f["team"] and s.role[i] > 0:  # nobody near: he just brings it down (chest, thigh, foot) or it runs on a little
            s.ball = pt.copy()
            if s.rng.random() < .9:
                return s._gain_control(i, pt)
            s.last_touch = i
            if s.pending_pass is not None:
                s.pending_pass["outcome"], s.pending_pass = "incomplete", None
            return s._loose_ball(s.rng.normal(0, 1.8, 2))
        if s.role[i] == 0 and t != f["team"]:
            if s.rng.random() < .3:  # punches it clear
                s.emit("keeper_punch", player=i, pos=pt)
                if s.pending_pass is not None:
                    s.pending_pass["outcome"], s.pending_pass = "intercepted", None
                s.last_touch = i
                return s._loose_ball(_unit(np.array([L / 2, W / 2]) - pt + s.rng.normal(0, .8, 2)) * 8)
            s.emit("keeper_claim", player=i, pos=pt)
        elif rv >= 0:
            s.emit("duel", player=i, pos=pt, duel="aerial", outcome="won", target=s.ids[rv])
            s.emit("duel", player=rv, pos=pt, duel="aerial", outcome="lost", target=s.ids[i])
        s.ball = pt.copy()
        pt_t = _tf(pt) if t else pt
        u = s.rng.random()
        if t == f["team"]:  # the side the ball was played to wins it
            if pt_t[0] > .7 * L and abs(pt_t[1] - W / 2) < 18 and s.rng.random() < (TUNE["head_shot_set"] if f.get("src") in ("corner", "free_kick") else .13):
                s._gain_control(i, pt)
                C = pt_t
                O = _tf(s.pos[s._opp(t)]) if t else s.pos[s._opp(t)]
                xg, nb = s._xg(C, O[s.role[s._opp(t)] > 0], header=True)
                s.ball = pt.copy()
                return s._shoot(i, C, xg * .9, nb, kind="header")
            if s.role[i] > 0 and u < TUNE["head_on"] and s._header_on(i, pt):  # a flick-on or a headed lay-off
                return
        else:  # the other side wins it
            if s.role[i] > 0 and u < TUNE["head_clear"]:  # headed clearance
                s.emit("clearance", player=i, pos=pt, body_part="head", aerial_won=True)
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
            if s.role[i] > 0 and u < TUNE["head_clear"] + TUNE["head_on"] and s._header_on(i, pt):
                return
        if s.rng.random() < .62:  # he controls it
            s._gain_control(i, pt)
        else:  # it drops between them: a second-ball scramble
            s.last_touch = i
            if s.pending_pass is not None:
                s.pending_pass["outcome"], s.pending_pass = "incomplete", None
            s._loose_ball(s.rng.normal(0, 2.6, 2))

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
                      frame_cols=["t", "ball_x", "ball_y", "carrier", "poss"] + [f"{c}{i}" for i in range(22) for c in "xy"] + ["ball_z"] + [f"head{i}" for i in range(22)]),
            events=s.events, frames=fr.tolist())
