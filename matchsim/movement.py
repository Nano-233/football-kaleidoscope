"""Off-ball positioning: where each player WANTS to be this tick.

A target is built in the team frame (own goal x=0, attacking +x) from, in order:
formation anchor -> team block follows the ball -> tactic (line height, width,
compactness) -> phase (in/out of possession role push/drop) -> offside line ->
man/zone marking -> pressing -> support triangles / forward runs -> loose-ball
chasing / pass interception. engine.Match then steers players toward the targets
with pace/acceleration/stamina limits, so movement always reacts to the ball,
opponents and tactics.
"""
import numpy as np
from scipy.optimize import linear_sum_assignment
from scipy.spatial.distance import cdist
from .teams import L, W
from . import teamplay

GK, CB, FB, DM, CM, WG, AM, ST = range(8)
PUSH = np.array([0, 3, 9, 2, 4, 8, 7, 6.])        # metres forward when we have the ball
DROP = np.array([0, -2, -3, -1, -3, -6, -7, -10.])  # metres back when we do not
FOLLOW = np.array([.1, .5, .55, .55, .55, .5, .5, .45])  # how strongly the role follows the ball's x
CRUISE = np.array([1.7, .56, .56, .6, .7, .5, .6, .48])  # jogging speed by role (box-to-box midfielders cover more ground)
SHOULDER = np.array([0, 0, 0, 0, 0, -3.5, -8., -1.5])  # where each attacking role stands relative to the last defender (m)
WANDER = np.array([.4, 1.3, 1.8, 1.7, 2.2, 2.3, 2.4, 2.4])  # metres of constant drifting around the target: nobody stands still
MIND = np.array([0., .1, .25, .35, .5, .75, .7, .9])  # attack-mindedness by role (GK CB FB DM CM WG AM ST), used for laziness
MIN_X = np.array([0, 6, 6, 9, 10, 11, 12, 16.])
COMPRESS = np.array([0, 1., 1., 1., 1., 1., .9, .4])  # share of the 'get behind the ball' pull each role feels near his own goal (GK CB FB DM CM WG AM ST)
LAG = np.array([0, 10, 10, 7, 7, 6, 6, -4.])  # metres behind the ball each role stands when the ball is near its goal (a compact block that follows the ball)  # deepest a role will drop (defenders hold the box edge, they do not hug the goal line)
TP = {}  # the engine's TUNE table (set by engine.py) so the calibration can reach the block-shape constants below
BOX_START = 38.  # the defence starts filling the box lanes once the ball is this close to goal (they read the attack early; fully set 10 m closer)
WN_END = 42.  # the block follows the ball (each line a fixed distance behind it) once the ball is closer than this to our goal
ALLOW = np.array([0, 0, 0, 0, 4., 8., 8., 40.])  # extra metres past the ball a role may stand while defending: only the striker is a real outlet (real freeze frames: 67-83% of defenders are goal-side of the ball)
GS_CAP = np.array([0, 0, 0, 0, 0, 2, 2, 8.])  # how far past the ball (towards the opponent) each role may stand while defending: most men stay goal-side of it
GOAL = np.array([0., W / 2])


def _load_xt():
    """Expected-threat surface (value of holding the ball at each spot) from real events, see scripts/xt.py."""
    import json, pathlib
    f = pathlib.Path(__file__).resolve().parents[1] / "reference" / "xt_grid.json"
    return np.array(json.loads(f.read_text())["xt"]) if f.exists() else None


_XTG = _load_xt()


def XT(p):
    """Bilinear xT at points p (..., 2) in the attacking-left-to-right frame (the opponent's goal at x = L)."""
    p = np.asarray(p, float)
    nx, ny = _XTG.shape
    cx = np.minimum(np.maximum(p[..., 0] * (nx / L) - .5, 0.), nx - 1.)
    cy = np.minimum(np.maximum(p[..., 1] * (ny / W) - .5, 0.), ny - 1.)
    x0, y0 = np.minimum(cx.astype(int), nx - 2), np.minimum(cy.astype(int), ny - 2)
    fx, fy = cx - x0, cy - y0
    g = _XTG
    return g[x0, y0] * (1 - fx) * (1 - fy) + g[x0 + 1, y0] * fx * (1 - fy) + g[x0, y0 + 1] * (1 - fx) * fy + g[x0 + 1, y0 + 1] * fx * fy
TUNE_COMMIT = .08
RING_MIN = 11.       # supporting players do not want to stand closer than this to the carrier (m)
RING_K = 1.8         # how much they mind
GK_RISK = .2         # the keeper comes out if he can get there within this many seconds of the nearest attacker
DANGER_ZONE = 38.    # an attacker this close to goal who is unmarked gets picked up by a free defender
PICK_UP = .8         # how much of his own place a defender gives up to take the free man
SUPPORT_MIN = 10.     # the nearest supporting player stands at least this far from the carrier
ATT_SPACING = 6.5    # attackers keep at least this far from each other (m)
BACK_PRESS = 6.      # a centre-back or full-back only leaves the line to a carrier this close
MOVE_BURST = 4.      # an attacker who picks a new spot further away than this gets there at pace
REST_N = 3           # players kept back as rest defence while the team attacks (the deepest anchored)
REST_LAG = 16.       # how far behind the ball they stay (m)
REST_MAX = 54.       # and the furthest up the pitch they go (m from their own goal)
ROT_COST = 7.        # extra metres of effort a player is charged for taking a template spot that belongs to another role
FRONT_SHIFT = .0     # extra share of the ball's lateral offset that strikers and number tens follow
OPEN_CAP = 6.        # separation (m) that counts as fully open when choosing where to stand
OVERLAP_RATE = .5     # per second chance that a full-back with a team-mate wide in front of him overlaps
BALL_SIDE = .9       # how strongly a marker leans to the ball side of his man when the ball is far away
MARK_GAP = 1.1       # how far goal-side of his man a marker stands (tighter in the box)


def mark_gap(o, tc):
    """How far goal-side of his man (at o, or rows of o) a marker stands: the room he can give is the time he needs to react, and near
    goal there is none to give (a man with a yard in the box turns and shoots), so it shrinks to arm's length there (real freeze
    frames: 43 % of attackers in the good shooting spots have a man within 1.5 m when the ball is in front of the box)."""
    near = np.clip(nrm(np.asarray(o, float) - GOAL) / TP.get("mark_r", 25.), TP.get("mark_box", .45), 1.)
    return (TP.get("mark_gap", MARK_GAP) + 1.4 * (1 - tc.marking)) * near
LINE_STEP = 5.       # metres the back line moves with pressure on the ball (up when pressed, down when the carrier has time)
LINE_FLAT2 = .35     # the same after marking has been applied
LINE_FLAT = .25      # how much of its own spread each back-line player keeps (0 = perfectly flat line)
COVER_GAP = 6.       # distance of the cover defender behind the presser
LANE_CUT = .6        # how far free defenders move onto a passing lane (0 = hold the zone, 1 = stand on the lane)
CENTRE_BIAS = 1.0    # central receivers count this much more when choosing which lanes to cut
FORCE_WIDE = .6      # how far the presser shades to the inside of the carrier so that the open side is the touchline
NARROW = .3   # how much a defending block squeezes in from its formation width at full compactness
SPACING = 7.   # minimum gap between defenders who are holding zones
COMMIT_DANGER = 2.
GOAL_HALF = 3.66
INSET = 4.3          # a width holder stands this far off the touchline: on the line without being out of play every time he is found (Open Football's WidePlan)
WIDE_SLOT = .5       # a slot is a candidate for a touchline when it lies in the outer quarter of the formation's own width
WINGER_DEPTH = .45   # a slot this far up the formation or better is a winger; below it a full-back, who holds the line only when nobody is in front of him
INCUMBENCY = .15     # the man already holding a touchline keeps it unless another is clearly better
REST_CB, REST_SCREEN, REST_FAR = 17.5, 21.25, 8.75  # metres of 'depth' a centre-back, the screening midfielder and the full-back away from the ball are worth when choosing who stays home (Open Football's rest_fit)


_OFF = np.array([L, W])


def nrm(a, axis=-1, keepdims=False):
    """Euclidean norm over the last axis (a faster stand-in for np.linalg.norm on small arrays)."""
    r = np.hypot(a[..., 0], a[..., 1]) if a.shape[-1] == 2 else np.sqrt(np.einsum("...i,...i->...", a, a))
    return r[..., None] if keepdims else r


def clip(x, lo, hi):
    return np.minimum(np.maximum(x, lo), hi)


def _tf(a):
    """world <-> team frame for the away side (an involution: flips both axes)."""
    return _OFF - a


def _unit(v):
    return v / np.maximum(nrm(v, axis=-1, keepdims=True), 1e-6)


def refresh_marking(m, t):
    """Assign each CB/FB/DM/CM an opposing outfield player (Hungarian on zone distance)."""
    sl, osl = slice(11 * t, 11 * t + 11), slice(11 * (1 - t), 11 * (1 - t) + 11)
    f = _tf if t else (lambda a: a)
    O, zone = f(m.pos[osl]), f(m.tgt[sl])
    D = np.nonzero((m.role[sl] >= CB) & (m.role[sl] <= CM))[0]
    Oo = np.nonzero(m.role[osl] > GK)[0]
    threat = np.exp(-nrm(O[Oo] - GOAL) / 22)  # attackers closer to our goal matter more
    r, c = linear_sum_assignment(cdist(zone[D], O[Oo]) - 14 * threat[None, :])
    mk = -np.ones(11, int)
    mk[D[r]] = Oo[c]
    m.mark[t] = mk


WTHREAT = np.array([0., 0., .35, .2, .5, .95, .9, 1.])  # how much each role cares about being dangerous (GK CB FB DM CM WG AM ST)
OFFS = np.array([[0, 0], [6, 0], [-6, 0], [0, 6], [0, -6], [4.5, 4.5], [4.5, -4.5], [-4.5, 4.5], [-4.5, -4.5], [9, 0], [0, 9], [0, -9]], float)


def seek_space(m, t, P, O, C, zone, role, out_f, skip, line):
    """Off-ball players of the team with the ball look for a better place to stand, every second or sooner when marked.

    Each candidate spot near the player's zone is scored on: space from defenders, an open passing lane from the ball,
    how dangerous the spot is, a useful distance from the ball, spacing from team-mates, staying onside and staying
    close to the shape. The best spot is kept for about a second (less when a defender is on him)."""
    sl = slice(11 * t, 11 * t + 11)
    near = np.sqrt(((P[:, None] - O[None]) ** 2).sum(-1)).min(1)
    due = out_f & ~skip & ((m.space_until[sl] <= m.t) | (near < 2.2))
    idx = np.nonzero(due)[0]
    if len(idx):
        cand = clip(zone[idx][:, None, :] + OFFS[None], [2., 2.], [L - 2., W - 2.])  # (n, K, 2)
        # A forward who cannot find a lane up where the shape put him also looks for one closer to the ball: checking to feet, dropping into the half-space, the
        # useful passing band 10-20 m from the carrier (the open-source forward 'creating space' state scores spots the same way, with the passing lane weighted heaviest)
        fwd_ = np.isin(role[idx], (ST, WG, AM))[:, None, None]
        short = np.stack(np.meshgrid([5., 11., 17.], [-10., 0., 10.], indexing="ij"), -1).reshape(-1, 2)  # (9, 2): ahead of the ball by 5-17 m, either side
        sc_ = np.stack([np.clip(C[0] + short[:, 0], 2., L - 2.)[None].repeat(len(idx), 0), np.clip(zone[idx][:, 1:2] + short[:, 1][None], 2., W - 2.)], -1)  # (n, 9, 2)
        cand = np.concatenate([cand, np.where(fwd_, sc_, cand[:, :1])], axis=1)
        n, K = cand.shape[:2]
        dmin = np.sqrt(((cand[:, :, None, :] - O[None, None]) ** 2).sum(-1)).min(2)  # (n, K)
        open_ = clip(dmin / OPEN_CAP, 0, 1.)  # separation from the nearest defender only counts up to a few metres: a marked forward cannot get 6 m of room
        ab = cand - C
        ab2 = np.maximum((ab ** 2).sum(-1), 1e-6)
        rel = O[None, None] - C  # (1,1,11,2)
        s_ = clip((rel * ab[:, :, None, :]).sum(-1) / ab2[:, :, None], 0, 1)
        perp = np.sqrt(((O[None, None] - (C + s_[..., None] * ab[:, :, None, :])) ** 2).sum(-1))  # (n, K, 11)
        lane = clip(perp.min(2) / 3., 0, 1)
        thr = np.exp(-np.hypot(L - cand[..., 0], W / 2 - cand[..., 1]) / 22.) * 3 * WTHREAT[role[idx]][:, None]
        dc = np.sqrt(ab2)
        pref = np.exp(-((dc - 16.) / 12.) ** 2) * (dc < 40) - RING_K * np.maximum(RING_MIN - dc, 0) / RING_MIN
        dz = np.sqrt(((cand[:, :, None, :] - zone[None, None]) ** 2).sum(-1))  # distance to every team-mate's target
        dz[np.arange(n)[:, None], np.arange(K)[None, :], idx[:, None]] = 99.  # not to himself
        spacing = clip((7. - dz) / 7., 0, 1).sum(2)
        att = np.isin(role[idx], (WG, AM, ST, CM))[:, None]
        offside = att * ((cand[..., 0] > line + .3) & (cand[..., 0] > C[0]))
        dev = np.sqrt(((cand - zone[idx][:, None]) ** 2).sum(-1)) / 8.
        dev[:, len(OFFS):] *= TP.get("check_dev", .3)  # checking to feet is a deliberate move away from the shape, not drifting out of it
        # a player cannot walk through the man who is marking him: a spot that needs him to get past a defender who stands between him and it is worth less
        a_pos = P[idx][:, None, :]  # (n,1,2)
        vv = cand - a_pos  # (n,K,2)
        vl = np.maximum(np.sqrt((vv ** 2).sum(-1)), 1e-6)
        rd = O[None, None] - a_pos[:, :, None, :]  # (n,1,11,2)
        proj = (rd * vv[:, :, None, :]).sum(-1) / vl[:, :, None]
        perp_d = np.sqrt(np.maximum((rd ** 2).sum(-1) - proj ** 2, 0.))
        toward_goal = ((GOAL - a_pos) * vv).sum(-1) > 0  # (n,K)
        in_way = ((proj > .5) & (proj < vl[:, :, None]) & (perp_d < TP.get("way_r", 2.2))).sum(2) * toward_goal
        marked = (near[idx] < 2.2)[:, None]
        if TP.get("pol_space", 0.) > 0:  # where to stand: where real passers choose to pass, free, an open lane, ahead, worth something (the learned choice, matchsim/policy.py)
            from . import policy
            pol = policy.scores(C, cand.reshape(-1, 2), O, float(np.sqrt(((O - C) ** 2).sum(-1)).min()), np.zeros(n * K, bool))[0].reshape(n, K)
            score = TP["pol_space"] * pol - 1.2 * spacing - 2. * offside - .6 * dev - TP.get("way_pen", 0.) * in_way + m.rng.normal(0, .05, (n, K))
        else:
            score = (1. + .5 * marked) * open_ * TP.get("open_w", 1.) + .9 * lane * TP.get("lane_w", 1.) + .7 * thr + .5 * pref - 1.2 * spacing - 2. * offside - .6 * dev - TP.get("way_pen", 0.) * in_way + m.rng.normal(0, .05, (n, K))
        best = score.argmax(1)
        gain = score[np.arange(n), best] - score[:, 0]  # what the new spot is worth over staying where the shape puts him
        m.space_gain[11 * t + idx] = gain + 10. * marked[:, 0]  # a man with a defender on him has to break away whatever it is worth
        m.space_off[11 * t + idx] = cand[np.arange(n), best] - zone[idx]
        m.space_until[11 * t + idx] = m.t + m.rng.uniform(.8, 1.6, n)
    far_ = (nrm(m.space_off[sl], axis=1) > MOVE_BURST) & out_f & ~skip & (m.space_gain[sl] > TP.get("burst_gain", -1e9))  # at pace only when the move is worth it
    m.space_burst[sl] = far_
    return zone + m.space_off[sl] * (out_f & ~skip)[:, None]


def defend_assign(m, t, zone, urgent, role, P, O, Ov, C, Cv, ci, tc, tgt1, cover_pt, R_press, in_range, cover_ok, d_ball, avail=None, with_press=True):
    """The defence as one connected system: every outfield defender is assigned the job that is worth most to the team, and the assignment is solved
    for all of them together, so when one man leaves his job the others re-arrange around it.

    Jobs, each with a value (seconds of running it is worth) and a place to stand:
      press   the man on the ball (one, two for a high press), only for those within reach
      cover   behind the presser on the inside, when the ball is in the defensive two thirds
      mark    each attacker, worth the threat he carries: where he stands (expected threat of that spot) x the chance the ball gets to him (an open
              lane from the carrier, where nobody stands in the way) x more when he is running at goal; he is covered goal-side with a lean to the ball
      lane    one body on the line from the ball to goal when the carrier can shoot
      post    one man on the near post / goal line when the ball can be shot, crossed or cut back
      space   the cut-back area when the ball is wide and deep
    The cost of a job for a defender is the time to get there (minus nothing for a job he already has: a defender keeps his job unless another is clearly
    worth more), a defender may take none and then holds his place in the block. Dangerous men are therefore always picked up; when the nearest man is
    pulled out to the ball, the next one slides across to the most dangerous free attacker, and the chain ends with the least dangerous man left alone."""
    outf = np.nonzero((role > GK) & (np.ones(len(role), bool) if avail is None else avail))[0]
    if not len(outf):
        return zone, urgent, [], []
    dgc = float(nrm(C - GOAL))
    side = 1. if C[1] >= W / 2 else -1.
    jobs, vals, codes, kinds, allowed = [], [], [], [], []
    n_def = len(P)
    can_mark = (role >= CB) & (role <= AM)  # the back line and the midfield are given men; the forwards are in the unit for the press (and the lanes) only

    def add(pos, val, code, kind, ok=None):
        jobs.append(np.asarray(pos, float)); vals.append(val); codes.append(code); kinds.append(kind); allowed.append(np.ones(n_def, bool) if ok is None else ok)

    # press (one or two) and cover. The presser does not run to where the carrier is but to the first point on the carrier's predicted line that he can reach
    # no later than the carrier does (the open-source RoboCup defences predict the dribble line and send the first man who can get there in time); he stands
    # one jockeying gap ahead of the carrier on that line, goal-side.
    gvv = _unit(GOAL - C)
    head = _unit(Cv) if float(nrm(Cv)) > 1.5 else gvv
    v_c = max(float(nrm(Cv)), 2.0)
    ts = np.arange(.3, 3.01, .3)
    path = C[None] + head[None] * (v_c * ts)[:, None]  # (T, 2)
    vmx = m.vmax[11 * t: 11 * t + len(P)] * .85
    reach = cdist(P, path) / vmx[:, None] + .25  # (n, T) time for each defender to get to each path point
    feas = reach <= ts[None]
    k_first = np.where(feas.any(1), feas.argmax(1), len(ts) - 1)
    t_int = np.where(feas.any(1), ts[k_first], 3.0 + reach[np.arange(len(P)), len(ts) - 1] - ts[-1])
    inside_ = np.array([0., np.sign(W / 2 - C[1]) * float(clip(abs(C[1] - W / 2) / 18., 0, 1))])
    press_pts = path[k_first] + _unit(gvv + FORCE_WIDE * inside_) * (np.linalg.norm(tgt1 - C) if True else 2.5) * (.6 if float(nrm(Cv)) > 1.5 else 1.)
    m._press_pts = press_pts
    n_press = 1 + (tc.press > .8)
    back = (role == CB) | (role == FB)
    ok_press = (d_ball < np.where(back & ~in_range, np.minimum(R_press, BACK_PRESS), R_press))
    for k in range(n_press if with_press else 0):
        add(tgt1 if k == 0 else tgt1 + _unit(GOAL - C) * 3. + np.array([0., side * 2.]), TP.get("press_v", 7.) * (1. if k == 0 else .5), 200 + k, "press", ok_press)
    if cover_ok and with_press:
        ok_cov = (d_ball < np.where(back, BACK_PRESS + 6., 24.))
        add(cover_pt, TP.get("cover_v", 3.5), 210, "cover", ok_cov)
    # marks
    xt_c = float(XT(np.array([L - C[0], W - C[1]])))
    for j in range(1, 11):
        if j == ci:
            continue
        o = O[j]
        dg = float(nrm(o - GOAL))
        xt_a = float(XT(np.array([L - o[0], W - o[1]])))  # the spot he stands on, valued from his own team's point of view
        ab = o - C
        dl = float(nrm(ab))
        run = float(clip(float((Ov[j] * _unit(GOAL - o)).sum()) / 6., 0, 1))
        if dl > 1e-3:
            end = C + ab * max(1. - 2.5 / dl, 0.)  # the lane up to the last couple of metres: a man standing goal-side of him does not close it
            segv = end - C
            s_ = clip(((P[outf] - C) @ segv) / max(float(segv @ segv), 1e-6), 0, 1)
            lane_open = float(clip(nrm(P[outf] - (C + s_[:, None] * segv), axis=1).min() / 2.5, 0, 1))
        else:
            lane_open = 1.
        gain = max(xt_a - xt_c, 0.) + TP.get("back_w", .2) * xt_a  # what a pass to him could add to the threat: a man behind the ball only offers a ball back, so he is not worth leaving the goal side of the ball for
        v = TP.get("mark_k", 100.) * gain * (.3 + .7 * lane_open) * (1. + .7 * run) * float(np.exp(-dl / 45.))
        v *= float(np.exp(-max(abs(o[1] - C[1]) - 10., 0.) / TP.get("mark_lat", 1e9)))  # a man far across from the ball: the block can shift while a switch is in the air, so he is not worth pinning a defender wide for
        near_goal = o[0] <= TP.get("threat_depth", .34) * L and abs(o[1] - W / 2) <= TP.get("threat_wide", 22.)  # he is dangerous to our goal (about 36 m out, and not on the far touchline: the zone has him until the ball comes across) ...
        near_ball = dl <= TP.get("threat_ball", 25.)  # ... or the carrier can find him with his next pass
        if not (near_goal or near_ball):
            continue
        v += TP.get("mark_base", 4.)  # somebody is responsible for every dangerous man: the assignment is made whatever the price, if a defender is within reach
        wb = TP.get("ball_side", BALL_SIDE) * float(clip(dl / 20., 0, 1)) * float(clip(dg / TP.get("lean_r", 24.), TP.get("lean_min", .35), 1.))
        spot = o + Ov[j] * .5 + _unit(_unit(GOAL - o) + wb * _unit(C - o)) * mark_gap(o, tc)
        add(spot, v, j, "mark", can_mark & (nrm(P - spot, axis=1) <= TP.get("mark_reach", 19.)))
    # dangerous space nobody covers (pitch-control thinking: a cell is the attackers' if they can get there before any defender; its worth is the expected threat of
    # holding the ball there times the chance the ball gets there). The most valuable uncovered cells become jobs, so the block drops in front of goal and
    # does not stay on far-out men while the middle is open.
    if TP.get("space_cover", 1.):
        nx_, ny_ = _XTG.shape
        gx_ = (np.arange(nx_) + .5) * L / nx_; gy_ = (np.arange(ny_) + .5) * W / ny_
        cx_, cy_ = np.meshgrid(L - gx_, W - gy_, indexing="ij")  # defenders' frame: own goal at x = 0
        cells = np.stack([cx_, cy_], -1).reshape(-1, 2)
        wcell = _XTG.reshape(-1) * np.exp(-nrm(cells - C, axis=1) / 25.)
        att_pts = np.vstack([O[1:], C[None]])
        t_att = (cdist(cells, att_pts) / 7.).min(1)
        t_def = (cdist(cells, P[outf]) / 6.).min(1) + .3
        unc = wcell * clip((t_def - t_att + .6) / 1.5, 0, 1)
        picked = []
        for idx_ in np.argsort(-unc):
            if unc[idx_] * TP.get("space_k", 100.) < .6 or len(picked) >= TP.get("n_space", 3):
                break
            if all(float(nrm(cells[idx_] - cells[k])) > 8. for k in picked):
                picked.append(idx_)
        for idx_ in picked:
            pt = cells[idx_] + _unit(GOAL - cells[idx_]) * 1.5
            add(pt, TP.get("space_k", 100.) * float(unc[idx_]), 300 + int(idx_), "cell", can_mark)
    # lane, post, space
    if dgc < 30.:
        add(C + _unit(GOAL - C) * max(4., .45 * dgc), TP.get("lane_job_k", 35.) * float(np.exp(-dgc / 12.)) * .4, 100, "lane", can_mark)
    if dgc < 30. and abs(C[1] - W / 2) > 14.:
        add(np.array([11., W / 2 + .3 * (C[1] - W / 2)]), TP.get("space_v", 3.), 102, "space", can_mark)
    if not jobs:
        return zone, urgent, [], []
    jobs = np.array(jobs); vals = np.array(vals); codes = np.array(codes)
    vref = 6.
    trav = cdist(P[outf], jobs) / vref
    for k_, kd in enumerate(kinds):
        if kd == "press":
            trav[:, k_] = t_int[outf] * TP.get("press_tcost", 1.)
        elif kd == "mark":
            trav[:, k_] += TP.get("layer_bias", .9) * np.maximum(jobs[k_][0] - P[outf][:, 0], 0.) / vref  # the man in front of the back four goes to whoever is already in front of the back four
    cost = trav - vals[None, :]
    cost = cost - TP.get("job_hyst", 1.5) * (m.job_prev[t][outf][:, None] == codes[None, :])
    al = np.array(allowed)[:, outf].T
    cost = np.where(al, cost, 1e3)
    cost = np.concatenate([cost, np.zeros((len(outf), len(outf)))], axis=1)
    r, c = linear_sum_assignment(cost)
    m.job_prev[t][outf] = -1
    press_ids, cover_ids = [], []
    for i_, k_ in zip(r, c):
        if k_ >= len(jobs) or cost[i_, k_] >= 1e3:
            continue
        i = int(outf[i_])
        m.job_prev[t][i] = codes[k_]
        if kinds[k_] == "press":
            press_ids.append(i)
        elif kinds[k_] == "cover":
            cover_ids.append(i)
            zone[i] = jobs[k_]; urgent[i] = True
        else:
            w = TP.get("jobs_w", .9)
            zone[i] = zone[i] * (1 - w) + jobs[k_] * w
            slack = 4.
            if kinds[k_] == "mark":  # a marker who is off his man by more than the room he can give is late: near goal that is a metre
                slack = float(np.clip(nrm(jobs[k_] - GOAL) / 25., .25, 1.)) * 4.
            if float(nrm(P[i] - jobs[k_])) > slack:
                urgent[i] = True
    return zone, urgent, press_ids, cover_ids


def box_defence(m, B, zone, urgent, gate, role, P):
    """Ball near our goal: defenders fill the lanes between the ball and the goal, in layers, and cover the posts.

    Real shots almost always have defenders in front of the shooter on the line to goal (about two on that line within 6 m and
    another near the six-yard box) and one close to the goal line. Slots come from the geometry (ball, goal mouth), in priority
    order: a body on the line to goal, the near post, a second layer deeper on the line, the far post, a lane to each post, the
    penalty spot (cut-backs) and the edge of the area (second balls). Defenders take them by shortest total distance; those
    committed to a man can be taken too (a marker who is also in the shot lane serves both), at a cost."""
    dgl = float(nrm(B - GOAL))
    bs = TP.get("box_start", BOX_START)
    if dgl >= bs:
        return zone, urgent
    wz = float(clip((bs - dgl) / 10, 0, 1))
    free = np.nonzero((role > GK) & ~urgent & (gate < TP.get("slot_gate", 9.)))[0]  # a defender who has a dangerous man to stay with marks him; the spare ones fill the lanes
    if not len(free):
        return zone, urgent
    pl, pr = np.array([0., W / 2 - GOAL_HALF]), np.array([0., W / 2 + GOAL_HALF])
    side = 1. if B[1] >= W / 2 else -1.
    slots = np.array([B + (GOAL - B) * .42, (1.4, W / 2 + side * 2.6), B + (GOAL - B) * .75, (2.2, W / 2 - side * 2.8), B + (pl - B) * .5, B + (pr - B) * .5,
                      (10., W / 2 + .25 * (B[1] - W / 2)), (17., W / 2 + .4 * (B[1] - W / 2))])[:min(len(free), 4 + int(dgl < 20))]
    cost = cdist(zone[free], slots) + 6. * gate[free][:, None]  # a defender busy marking is a worse choice, not an impossible one
    r, c = linear_sum_assignment(cost)
    for i, j in zip(free[r], slots[c]):
        zone[i] = zone[i] * (1 - .85 * wz) + j * (.85 * wz)
        if np.hypot(*(P[i] - j)) > 6:
            urgent[i] = True
    return zone, urgent


def pick_up_free(zone, urgent, gate, role, P, O, ci, tc):
    """An attacker in a dangerous area with nobody near him is picked up by the nearest defender who is not busy: the 'who has him?' rule.

    Dangerous = inside about 38 m of goal. 'Nobody near' = no defender within 3 m. Free defenders take such attackers by shortest
    total distance and stand goal-side of where each is going."""
    out = np.arange(1, 11)
    att = [j for j in out if j != ci and nrm(O[j] - GOAL) < DANGER_ZONE]
    if not att:
        return zone, urgent
    att = np.array(att)
    dd = cdist(P[role > GK], O[att])
    near_def = dd.min(0)
    open_ = att[near_def > 3.0]
    if not len(open_):
        return zone, urgent
    free = np.nonzero((role > GK) & (gate < .6) & ~urgent)[0]
    if not len(free):
        return zone, urgent
    cost = cdist(P[free], O[open_])
    r, c = linear_sum_assignment(cost)
    for i_, j_ in zip(r, c):
        if cost[i_, j_] > 20.:
            continue
        o = O[open_[j_]]
        spot = o + _unit(GOAL - o) * mark_gap(o, tc)
        zone[free[i_]] = zone[free[i_]] * (1 - PICK_UP) + spot * PICK_UP
        if cost[i_, j_] > 5.:
            urgent[free[i_]] = True
    return zone, urgent


def cut_lanes(zone, urgent, gate, role, P, O, C, ci):
    """Cover shadows: defenders who are not marking a man stand on the passing lane between the ball carrier and the most
    dangerous team-mates ahead of the ball (central ones first), so the safe pass is the one out wide.

    Each lane point sits about half way along the pass line (at least 5 m from the carrier, who has his own presser).
    Free defenders take lanes by shortest total distance and only move part of the way, so the block keeps its shape.
    Near goal box_defence takes over (this fades out inside about 20 m of goal)."""
    w = LANE_CUT * (1. - float(clip((30. - float(nrm(C - GOAL))) / 10., 0, 1)))
    if w < .05:
        return zone, urgent
    free = np.nonzero((role > GK) & (role != CB) & (role != FB) & (gate < .6) & ~urgent)[0]
    if not len(free):
        return zone, urgent
    rec = np.array([j for j in range(1, 11) if j != ci and O[j, 0] < C[0] + 4.])
    if not len(rec):
        return zone, urgent
    dist = nrm(O[rec] - C, axis=1)
    thr = np.exp(-nrm(O[rec] - GOAL, axis=1) / 25.) * (1. + CENTRE_BIAS * (np.abs(O[rec, 1] - W / 2) < 14)) * clip(1.2 - dist / 35., .2, 1.) * (dist > 6.)
    top = np.argsort(-thr)[:min(len(free), 4)]
    top = top[thr[top] > .02]
    if not len(top):
        return zone, urgent
    s_ = np.maximum(.5, 5. / np.maximum(dist[top], 1e-6))
    Q = C + (O[rec[top]] - C) * s_[:, None]
    r, c = linear_sum_assignment(cdist(zone[free], Q))
    for i, j in zip(free[r], Q[c]):
        if np.hypot(*(zone[i] - j)) < 28.:
            zone[i] = zone[i] * (1 - w) + j * w
    return zone, urgent


def wide_holders(m, t, A, role, out_f):
    """One man per touchline holds the width while the team has the ball (Open Football's WidePlan).

    Width is not an average of where everybody stands, it is two named players standing where nobody else is. Without them the wingers drift
    infield to where the threat is, nobody is on the line to be found, and the side crosses far less than real teams (that engine measured 2.2
    open-play crosses per team against a real 16-18 before it added this). The man is the one whose formation slot is widest and highest, a
    winger before the full-back behind him, ties decided by delivery, 1v1 ability and legs; the incumbent keeps the line, so two men do not
    trade the job every tick."""
    sl = slice(11 * t, 11 * t + 11)
    live = out_f & ~m.off[sl]
    ys, xs = A[live, 1], A[live, 0]
    lat = (A[:, 1] - ys.min()) / max(float(np.ptp(ys)), 1.)
    depth = (A[:, 0] - xs.min()) / max(float(np.ptp(xs)), 1.)
    a = {k: m.attr[k][sl] / 20. for k in ("passing", "dribbling", "accel", "stamina", "pace")}
    fit = (clip((depth - WINGER_DEPTH) / (1. - WINGER_DEPTH), 0, 1) * .45 + a["passing"] * .2
           + (.6 * a["dribbling"] + .4 * a["accel"]) * .2 + (.5 * a["stamina"] + .5 * a["pace"]) * .15)
    wide = live & (np.abs(lat - .5) * 2. >= WIDE_SLOT)
    for k, low in enumerate((True, False)):
        side = wide & ((A[:, 1] < W / 2) == low)
        if side.any():
            sc = np.where(side, fit + INCUMBENCY * (np.arange(11) == m.wide_hold[t][k]), -1e9)
            m.wide_hold[t][k] = int(sc.argmax())
        else:
            m.wide_hold[t][k] = -1
    return m.wide_hold[t]


def team_targets(m, t):
    """Returns (targets (11,2) in world coords, urgent (11,) bool) for team t."""
    sl, osl = slice(11 * t, 11 * t + 11), slice(11 * (1 - t), 11 * (1 - t) + 11)
    m.sprint_hint[sl] = False
    f = _tf if t else (lambda a: a)
    fv = (lambda v: -v) if t else (lambda v: v)
    P, O, B, Bv = f(m.pos[sl]), f(m.pos[osl]), f(m.ball), fv(m.ball_v)
    # the team shapes itself around where the ball WILL be controlled, not where it is on its way (Helios and its descendants do the same: the
    # formation is set from the ball's position at the first interception step). Shaping on the flying ball makes the whole block swing 3 m a tick.
    Bs = f(m.flight["end"]) if m.state == "flight" and m.flight["kind"] in ("ground", "lofted") else B
    if TP.get("block_v", 0.) > 0:  # the block cannot move faster than its players can run (Open Football's BLOCK_SPEED): a 40 m pass would otherwise swing every target 40 m at once and leave the team sprinting after a shape it could never reach
        ref, last = m.bs_ref[t], m.bs_t[t]
        if ref is None or m.t - last > 3. or m.state == "dead" and m.dead.get("kind") in ("kickoff",):
            ref = Bs.copy()
        else:
            step = TP["block_v"] * float(m.vmax[sl][1:].mean()) * (m.t - last)
            dv_ = Bs - ref
            dn_ = float(nrm(dv_))
            ref = Bs.copy() if dn_ <= step else ref + dv_ / dn_ * step
        m.bs_ref[t], m.bs_t[t] = ref, m.t
        Bs = ref
    A, role, tc = m.anchor[sl], m.role[sl], m.tact[t]
    has = m.poss == t
    out_f = (role > GK)
    urgent = np.zeros(11, bool)
    kind = m.dead["kind"] if m.state == "dead" else None
    idx = np.arange(11)

    # ---- restarts with special shapes -------------------------------------------------
    if kind == "kickoff":
        tg = A.copy()
        tg[:, 0] = np.minimum(tg[:, 0], L / 2 - (1.5 if has else 10))
        if has:
            cand = [i for i in idx if role[i] in (ST, AM, CM) and 11 * t + i != m.dead["taker"]]
            if cand:
                tg[min(cand, key=lambda i: abs(A[i, 1] - W / 2))] = (L / 2 - 3, W / 2 + 5)
        return f(tg), urgent
    if kind == "corner":
        tg = A.copy()
        att_spots = [(L - 7, 31), (L - 9, 37), (L - 12, 34), (L - 14, 28), (L - 14, 40), (L - 19, 34), (L - 22, 24), (L - 22, 44)]  # near post, far post, penalty spot, edge of the box
        def_spots = [(5, 30), (5, 38), (8, 27), (8, 41), (11, 34), (6, 34), (13, 30), (13, 38)]
        n_out = 10 - len(def_spots)  # the men a defending side leaves out of its box: its counter-attack outlets
        rest_x, out_x = L / 2 - 3., L / 2  # the attacking side's cover stands on the halfway line, the outlets on the shoulder of its last man
        out_y = [W / 2 - 10, W / 2 + 10]
        cand = [i for i in idx if role[i] > GK and 11 * t + i != m.dead["taker"] and not m.off[11 * t + i]]  # a sent-off man is not part of the plan
        if has:
            # who stays home is decided first (Open Football's AttackPlan: rest defence before the attacking roles): one more than the opposition
            # has left up the pitch, the men worth least in their box (aerial and finishing), defenders and the screen first; a keeper behind them
            n_rest = int(clip(n_out + 1, 2, 4))
            val = {i: m.attr["aerial"][11 * t + i] + .5 * m.attr["finishing"][11 * t + i] - 6. * (role[i] in (FB, DM)) + 8. * (role[i] in (ST, WG, AM)) for i in cand}
            rest = sorted(cand, key=lambda i: val[i])[:n_rest]
            ys = [W / 2 + (k - (n_rest - 1) / 2) * 14. for k in range(n_rest)]
            for k, i in enumerate(sorted(rest, key=lambda i: A[i, 1])):
                tg[i] = (rest_x, ys[k])
            order = [i for r in (ST, CB, AM, CM, WG, DM, FB) for i in cand if role[i] == r and i not in rest]
            for i, sp_ in zip(order, att_spots):
                tg[i] = sp_
            tg[0] = (float(clip(rest_x - 28., 12., 24.)), W / 2)  # a sweeper behind his cover: close enough to the line to get back, far enough to reach a ball over it
            sup = m.dead.get("sup", -1) - 11 * t
            if m.dead.get("short") and 0 <= sup < 11:
                cor = f(m.dead["pos"])
                tg[sup] = cor + _unit(np.array([L / 2, W / 2]) - cor) * 9
        else:
            # the quickest forwards stay up as outlets on the shoulder of the last man; everybody else defends the box
            fast = sorted([i for i in cand if role[i] in (ST, WG, AM)] or cand, key=lambda i: -m.attr["pace"][11 * t + i])[:n_out]
            for k, i in enumerate(fast):
                tg[i] = (L - out_x, out_y[k % len(out_y)])
            order = [i for i in cand if i not in fast]
            for i, sp_ in zip(order, def_spots):
                tg[i] = sp_
            tg[0] = (1.5, W / 2)
        jost = 1.1 * np.stack([np.sin(.9 * m.t + m.phase[sl] * 3), np.cos(.7 * m.t + m.phase[sl] * 2)], 1)
        tg[1:] += jost[1:]  # jostling for position in the box
        return f(tg), urgent

    # ---- team block: follows the ball, shaped by tactic and phase ---------------------
    kx = FOLLOW[role] * m.mod["follow"][sl] * (1 + .3 * (tc.compactness - .5)) * (TP.get("follow_poss", 1.) if has else 1.)
    tx = A[:, 0] + kx * (Bs[0] - L / 2) + (tc.line_height - .5) * 14 * out_f
    by = Bs[1] - W / 2
    fd = float(m.poss_fade[t])  # 1 = a side that has had the ball for a while, 0 = one that has just lost it: the shape moves between the two smoothly
    ph = clip(.45 + Bs[0] / L, .5, 1.1)
    tx = tx + fd * PUSH[role] * TP.get("push_k", 1.) * (.6 + .8 * tc.risk) * ph + (1 - fd) * DROP[role] * TP.get("drop_k", 1.) * clip(1.25 - tc.press, .3, 1.2)
    ty = (W / 2 + (A[:, 1] - W / 2) * (fd * (1 + (tc.width - .5) * .7) + (1 - fd) * (1 - NARROW * tc.compactness))
          + by * (fd * (.2 + FRONT_SHIFT * ((role == ST) | (role == AM))) + (1 - fd) * np.where(np.isin(role, (DM, CM, AM, ST)), TP.get("lat_def_c", .28 + .22 * tc.compactness), .28 + .22 * tc.compactness)) * out_f)  # the front line drifts to the ball side to link up
    tx = np.maximum(tx, MIN_X[role])
    if not has:  # defending: near our goal the block follows the ball, each line a fixed distance behind it
        wn = float(clip((TP.get("wn_end", WN_END) - Bs[0]) / 12., 0, 1)) * COMPRESS[role]  # how far this role is pulled onto the ball: the back line fully, forwards hardly (they are the outlet)
        follow_x = np.maximum(Bs[0] - LAG[role] * TP.get("lag_k", 1.) * (1.25 - .5 * tc.press), MIN_X[role])
        # the back line splits the distance between the ball and the goal (real freeze frames: last defender at 9 / 12 / 14.5 / 17 / 18.6 m with the ball 12 / 17 / 22 / 27 / 32 m out)
        follow_x = np.where((role == CB) | (role == FB), np.maximum(.5 * Bs[0] + 3., MIN_X[role]), follow_x)
        tx = np.where(out_f, tx * (1 - wn) + follow_x * wn, tx)
        tx = np.where(out_f, np.minimum(tx, np.maximum(Bs[0] + GS_CAP[role] * (.6 + .8 * tc.press), MIN_X[role]) + ALLOW[role]), tx)
    if not has:
        bl = (role == CB) | (role == FB)
        cur = m.carrier if m.state == "held" and m.poss == 1 - t else -1
        if bl.sum() >= 2:
            step = 0.
            if cur >= 0:  # pressure on the ball: step up together; free to look up: drop together
                dn = float(nrm(P[out_f] - O[cur - 11 * (1 - t)], axis=1).min())
                step = LINE_STEP * (float(clip(1 - dn / 6., 0, 1)) - .4)
            lx = tx[bl].mean()
            tx = np.where(bl, lx + step + LINE_FLAT * (tx - lx), tx)  # one depth for the line, a little individual variation
    blk = out_f & (COMPRESS[role] > .5)  # compaction acts on the block (back line and midfield), not on the outlets up front
    cx = tx[blk].mean() if blk.any() else tx[out_f].mean()  # compact the block around its own centre
    tx = np.where(blk, cx + (tx - cx) * (.9 - .2 * tc.compactness), tx)
    if has and Bs[0] > .62 * L:  # midfielders arrive late at the edge of the box; wingers tuck in when the ball is on the far side
        late = (role == CM) | (role == AM)
        tx = np.where(late, np.maximum(tx, np.minimum(Bs[0] - 3, L - 20)), tx)
    if has:  # forwards hover on the last defender's shoulder, just onside; they are caught when the line moves faster than they react, not by a planned wobble
        line = max(np.sort(O[:, 0])[-2], L / 2)
        att = (role == ST) | (role == WG) | (role == AM)
        if Bs[0] > TP.get("pin_from", .35) * L:
            tx = np.where(att, np.maximum(tx, line + SHOULDER[role] - TP.get("hover", .3) + .3), tx)
        fw = att | (role == CM)  # nobody up front waits beyond the last defender and the ball (a timed run is a separate thing)
        tx = np.where(fw, np.minimum(tx, max(line, Bs[0]) - TP.get("hover", .3)), tx)
    if has and Bs[0] > .55 * L:
        far = (role == WG) & (np.abs(Bs[1] - A[:, 1]) > 25)
        ty = np.where(far, W / 2 + np.sign(A[:, 1] - W / 2) * 21, ty)  # the far-side winger stays wide: he is the switch outlet
    zone = np.stack([clip(tx, 2, L - 2), clip(ty, 2, W - 2)], 1)
    carrier = m.carrier if m.state == "held" else -1
    bx = Bs[0] + (max(float(Bv[0]), 0.) if m.state != "flight" else 0.)  # the team moves up with the play: look a second ahead along the ball's path
    wt = 0.  # how far the attacking shape has taken over from the block (the box slots outrank a width anchor)
    if not has and m.state != "dead" and TP.get("of_def", 0.):  # the defending block of the Open Football plan, in place of the follow-the-ball formula above
        da = teamplay.defending_anchors(A, out_f, Bs, tc)
        zone = np.where(out_f[:, None], zone * fd + da * (1 - fd), zone)
    plan_skip = np.zeros(11, bool)  # the men the team plan placed: they are not left to look for space on their own
    of = bool(has and m.state != "dead" and TP.get("of_plan", 1.))
    GOAL_ATT = np.array([L, W / 2])
    if of:  # the attacking block (Open Football's ShapeBuilder): everybody is in a 27-41 m rectangle whose front rank leads the ball
        line_o = max(np.sort(O[:, 0])[-2], L / 2)
        ba = teamplay.block_anchors(A, out_f, Bs, tc, line_o if TP.get("front_line", 1.) else 0., np.where(np.isin(role, (DM, CM, AM, ST)), TP.get("slide_c", .3), .3), TP.get("wid_att", 1.))
        cap_o = max(line_o, Bs[0]) - TP.get("hover", .3) + np.sin(.5 * m.t + m.phase[sl])  # nobody up front waits beyond the last defender and the ball
        att_o = (role == ST) | (role == WG) | (role == AM)
        ba[:, 0] = np.where(att_o, np.minimum(ba[:, 0], cap_o), ba[:, 0])
        if TP.get("fb_up", 0.) > 0 and Bs[0] > .45 * L:  # the full-back on the ball's flank comes up to support the winger: level with the ball or a little behind, not at the back of the block
            ball_side_fb = (role == FB) & ((A[:, 1] > W / 2) == (Bs[1] > W / 2))
            ba[:, 0] = np.where(ball_side_fb, np.maximum(ba[:, 0], np.minimum(Bs[0] - TP["fb_up"], max(line_o, L / 2) - 2.)), ba[:, 0])
        zone = np.where(out_f[:, None], zone * (1 - fd) + ba * fd, zone)
    if has and bx > .5 * L and not of:
        # attacking shape near the box: every role has a job (near post, far post, edge of the box, overlap, cover),
        # mirrored to the side the ball is on. Blended in as the ball gets closer to goal.
        sd = 1 if Bs[1] > W / 2 else -1
        w = wt = float(clip((bx - .5 * L) / (.2 * L), 0, 1)) * TP.get("templ_w", .9)
        line_t = max(np.sort(O[:, 0])[-2], L / 2) + .5
        templ = {ST: [(L - 9, W / 2 - sd * 2.5), (L - 11, W / 2 + sd * 6)], WG: [(L - 13, W / 2 + sd * 22), (L - 17, W / 2 - sd * 21)],
                 AM: [(L - 19, W / 2 + sd * 2), (L - 21, W / 2 - sd * 7)], CM: [(L - 21, W / 2 + sd * 9), (L - 23, W / 2 - sd * 8), (L - 31, W / 2)],
                 FB: [(L - 18, W / 2 + sd * 29), (L - 33, W / 2 - sd * 22)], DM: [(L - 32, W / 2)], CB: [(L - 44, W / 2 + 8), (L - 44, W / 2 - 8), (L - 46, W / 2)]}
        if TP.get("templ_hs", 1.):  # the second line stands in the half-spaces, not on the edge of the area's centre: real teams play 32% of final-third actions there and 15% down the middle
            templ[AM] = [(L - 19, W / 2 + sd * 12), (L - 21, W / 2 - sd * 11)]
            templ[CM] = [(L - 22, W / 2 + sd * 14), (L - 24, W / 2 - sd * 13), (L - 31, W / 2 + sd * 4)]
        slots = [(r, np.array(sp, float)) for r, spots in templ.items() for sp in spots if r not in (CB,)]  # the centre-backs are rest defence, not part of the attack
        cand_i = [i for i in idx if role[i] > GK and role[i] != CB and 11 * t + i != carrier]
        if slots and cand_i:
            cost = np.array([[float(nrm(zone[i] - sp_)) + (0. if role[i] == r_ else ROT_COST * (1. + abs(int(role[i]) - int(r_)) / 4.)) for (r_, sp_) in slots] for i in cand_i])
            rr, cc = linear_sum_assignment(cost)
            for a_, b_ in zip(rr, cc):
                i, (r_, sp_) = cand_i[a_], slots[b_]
                tgt_ = np.array([min(sp_[0], line_t - .5), sp_[1]])  # on the shoulder of the last defender, just onside
                zone[i] = zone[i] * (1 - w) + tgt_ * w

    mine = 11 * t <= carrier < 11 * t + 11
    hold = (-1, -1)
    if has and kind not in ("corner", "kickoff") and TP.get("wide_on", 1.):
        hold = wide_holders(m, t, A, role, out_f)  # who holds each touchline is decided before who stays home
    if has:  # rest defence: who stays well behind the ball (Open Football's rest_fit: the centre-backs, the screen in front of them, the full-back away from the ball; never a man holding a touchline), never beyond a limit just inside the opposition half
        eff = A[:, 0] - TP.get("rest_fit", 1.) * (REST_CB * (role == CB) + REST_SCREEN * (role == DM) + REST_FAR * ((A[:, 1] < W / 2) != (Bs[1] < W / 2)))
        eff = np.where(out_f, eff, 1e9)
        eff[[h for h in hold if h >= 0]] = 1e9
        deep = np.argsort(eff)[:REST_N]
        cap = float(clip(Bs[0] - REST_LAG, 20., REST_MAX))
        zone[deep, 0] = np.minimum(zone[deep, 0], np.maximum(cap, MIN_X[role[deep]]))
        if of and (TP.get("of_slots", 0.) or TP.get("of_mask", 13.)):
            side_o = 1. if Bs[1] >= W / 2 else -1.
            carrier_l = carrier - 11 * t if 11 * t <= carrier < 11 * t + 11 else -1
            held = np.zeros(11, bool)
            held[deep] = True
            if hold[0 if Bs[1] < W / 2 else 1] >= 0:
                held[hold[0 if Bs[1] < W / 2 else 1]] = True  # only the ball-side holder is reserved: the far-side one may attack the back post
            ids = teamplay.box_slots(m, t, role, out_f, P, Bs, side_o, held, carrier_l, m.box_prev[t])
            if not TP.get("of_slots", 0.):
                ids = [i_ if (int(TP.get("of_mask", 13.)) >> k_) & 1 else -1 for k_, i_ in enumerate(ids)]  # only the places in the mask are filled
            m.box_prev[t] = ids
            st = teamplay.stage(Bs[0] / L, teamplay.in_flight_to_goal(m, t))
            for k_, i_ in enumerate(ids):
                if i_ >= 0:
                    pt_ = teamplay.staged_point(k_, side_o, st, ba[i_])
                    zone[i_] = (min(pt_[0], max(cap_o[i_], ba[i_, 0])), pt_[1])
                    plan_skip[i_] = True
                    held[i_] = True
            if carrier_l >= 0 and m.state == "held" and TP.get("of_slots", 0.):  # the short outlet
                held[carrier_l] = True
                C_ = P[carrier_l]
                free_ = [i_ for i_ in range(11) if out_f[i_] and not held[i_] and float(nrm(P[i_] - Bs)) < 27.5]
                if free_:
                    j_ = min(free_, key=lambda i_: float(nrm(P[i_] - Bs)))
                    pr_ = O[int(nrm(O - C_, axis=1).argmin())]
                    tg_ = teamplay.support_target(P[j_], C_, pr_, GOAL_ATT)
                    if tg_ is not None:
                        zone[j_] = tg_
                        plan_skip[j_] = True

    if not has:
        # man/zone marking: blend zone slot with a goal-side spot next to the assigned opponent
        mk = m.mark[t]
        ok = mk >= 0
        o = O[np.where(ok, mk, 0)]
        ov = fv(m.vel[osl])[np.where(ok, mk, 0)]
        op = o + ov * .5  # stand goal-side of where he is going, not where he is
        wb = TP.get("ball_side", BALL_SIDE) * clip(nrm(B - op) / 20., 0, 1)  # marking is goal-side AND ball-side: the further the ball, the more the marker stands on the pass lane
        wb = wb * clip(nrm(op - GOAL, axis=1) / TP.get("lean_r", 24.), TP.get("lean_min", .35), 1.)  # in the box he is between his man and the goal first: that is the shot lane
        man = op + _unit(_unit(GOAL - op) + wb[:, None] * _unit(B - op)) * mark_gap(op, tc)[:, None]
        gate = clip(1.4 - nrm(zone - o, axis=1) / 22, 0, 1) * tc.marking * ok
        gate = np.where(ok & (o[:, 0] < 20) & (np.abs(o[:, 1] - W / 2) < 22) & (role > GK), np.maximum(gate, TP.get("box_gate", .85)), gate)  # attackers in the box are marked, not watched
        thr = np.exp(-nrm(o - GOAL) / 22)
        gate = np.where(ok, np.maximum(gate, clip((thr - .25) * 1.6, 0, .8)), gate)  # and so is anyone within about 30 m of goal
        dang = clip((TP.get("dang_far", 38.) - nrm(o - GOAL, axis=1)) / 18., 0, 1)  # the closer an attacker is to goal the more he is marked (man first, zone second)
        gate = np.where(ok & (role > GK), np.maximum(gate, TP.get("dang_gate", .85) * dang), gate)
        # the far side: a man wide on the side away from the ball is left to the zone (the defence tucks in and protects the middle); he is
        # picked up again when he runs at goal (below) or the ball comes across
        far = clip((np.abs(o[:, 1] - B[1]) - TP.get("far_dy", 22.)) / 10., 0, 1) * clip((np.abs(o[:, 1] - W / 2) - 12.) / 8., 0, 1)
        gate = gate * (1 - TP.get("far_free", 0.) * far)
        ovg = (ov * _unit(GOAL - o)).sum(1)  # a man running at goal is tracked goal-side first, the zone second
        gate = np.where(ok & (role > GK) & (nrm(o - GOAL, axis=1) < 45.), np.maximum(gate, TP.get("run_gate", .95) * clip((ovg - 2.5) / 2.5, 0, 1)), gate)
        zone = zone * (1 - gate[:, None]) + man * gate[:, None]
        free = out_f & (gate < .5)  # defenders holding a zone keep their distance from each other: no bunching, no gaps
        dz = zone[:, None] - zone[None]
        dd = np.maximum(nrm(dz), 1e-3) + np.eye(11) * 99
        push = dz / dd[..., None] * np.maximum(SPACING - dd, 0)[..., None] * (free[:, None] & free[None])[..., None] * .5
        zone = zone + push.sum(1) * free[:, None]
        # pressing the opposing ball carrier: the nearest defender closes to a jockeying gap, standing slightly on the inside so the
        # open side is the touchline; the next one covers behind him (cover and balance); both go earlier when the touch is fresh
        if m.poss == 1 - t and carrier >= 0:
            C, Cv = O[carrier - 11 * (1 - t)], fv(m.vel[carrier])
            gv = _unit(GOAL - C)
            inside = np.array([0., np.sign(W / 2 - C[1]) * float(clip(abs(C[1] - W / 2) / 18., 0, 1))])
            cs = float(nrm(Cv))
            trig = 1. + .35 * (m.t - m.ctrl_t < 1.2) + .3 * (Cv[0] > .5)  # press triggers: a fresh touch, a carrier turning away from our goal
            depth = C[0] / L
            R = (3 + 9 * tc.press) * (1.5 if m.t < m.trans_until[t] else 1.0) * trig * TP.get("press_k", 1.)
            if depth > .5:
                R *= max(.15, 1 - (depth - .5) * 2 * (1 - tc.press) * .9)
            d = nrm(P - C, axis=1)
            d[role == GK] = 1e9
            n = 1 + (tc.press > .8)
            gap = (2.4 + 1.4 * (1 - tc.press) + .15 * cs) * TP.get("jock_k", 1.)
            gap *= TP.get("shot_gap", .7) if float(nrm(C - GOAL)) < TP.get("shoot_close", 28.) else 1.
            tgt1 = C + _unit(gv + FORCE_WIDE * inside) * gap + Cv * .2
            if TP.get("hold_d", 17.) > 0:  # he does not back off into his own box: at its edge he stands, and the carrier has to beat him or pass
                rg = float(nrm(tgt1 - GOAL))
                rh = min(TP.get("hold_d", 17.), float(nrm(C - GOAL)) - .7)
                if rg < rh:
                    tgt1 = GOAL + _unit(tgt1 - GOAL) * rh
            in_range = float(nrm(C - GOAL)) < TP.get("shoot_close", 28.)  # the carrier can shoot: the nearest man, whatever his role, gets tight and stands in the shot lane
            if in_range:
                R = max(R, 12.)
            cover_pt = tgt1 + gv * COVER_GAP + inside * 2.0
            if TP.get("assign_on", 1.):
                Ov = fv(m.vel[osl])
                zone, urgent, press_ids, cover_ids = defend_assign(m, t, zone, urgent, role, P, O, Ov, C, Cv, carrier - 11 * (1 - t), tc, tgt1, cover_pt, R, in_range, depth < .62, d)
            else:
                busy = (gate > TP.get("busy_gate", .6)) | (((role == CB) | (role == FB)) & (d > BACK_PRESS + 2.))
                keep_ = np.zeros(11); keep_[[i for i in m.press_prev[t] if i < 11]] = TP.get("press_hyst", 2.0)
                near = np.argsort(d + 40. * busy - keep_)
                m.press_prev[t] = [int(i) for i in near[:n + 1]]
                if depth < .62:
                    for i in near[n:n + 1]:
                        if d[i] < 24:
                            zone[i] = cover_pt
                            urgent[i] = True
                press_ids = [int(i) for i in near[:n] if d[i] < (R if in_range else (min(R, BACK_PRESS) if role[i] in (CB, FB) else R))]
            for i in press_ids:
                g = 11 * t + i  # jockey goal-side; occasionally commit to the tackle
                danger = clip((.4 * L - C[0]) / (.3 * L), 0, 1)  # defenders commit more as the ball nears their goal
                if m.t >= m.commit_until[g] and m.rng.random() < (TUNE_COMMIT + .12 * tc.press) * m.mod["commit"][g] * (1 + COMMIT_DANGER * danger) * m.dt:
                    m.commit_until[g] = m.t + m.rng.uniform(1., 2.)
                zone[i] = C + Cv * .2 if m.t < m.commit_until[g] else (m._press_pts[i] if TP.get("assign_on", 1.) else tgt1)
                urgent[i] = True
                m.sprint_hint[g] = bool(TP.get("hint_press", 1.))  # the presser goes for the ball
        elif m.state == "dead" and kind == "free_kick" and np.hypot(B[0], B[1] - W / 2) < 32:  # wall on the line to goal, 9.15 m back
            # the wall covers the near-post side (the keeper takes the far side): its outside man stands on the line from the ball to the near post,
            # the rest shoulder to shoulder towards the middle. Men keep their order across the line, so nobody runs through the wall to his slot.
            dist = float(np.hypot(B[0], B[1] - W / 2))
            n = 5 if dist < 19 else 4 if dist < 25 else 3
            near = np.array([0., W / 2 + (3.66 if B[1] > W / 2 else -3.66)]) if abs(B[1] - W / 2) > 1. else GOAL
            u = _unit(near - B)
            perp = np.array([-u[1], u[0]])
            inward = perp if float(perp @ (GOAL - near)) >= 0 else -perp
            slots = B + u * 9.15 + inward[None] * (.6 * np.arange(n) + .3)[:, None]  # 0.6 m a man; the end man just outside the post line
            pick = np.argsort(nrm(P - slots.mean(0), axis=1) + (role == GK) * 1e9)[:n]
            pick = pick[np.argsort(P[pick] @ inward)]
            for k, i in enumerate(pick):
                zone[i], urgent[i] = slots[k], True
        elif m.state == "loose":
            d = nrm(P - B, axis=1)
            if nrm(B - GOAL) > 24:  # the keeper goes for a loose ball in and around his own box, not out in the field
                d[role == GK] = 1e9
            for i in np.argsort(d)[:2]:
                zone[i], urgent[i] = B + Bv * .6, True
                m.sprint_hint[11 * t + i] = bool(TP.get("hint_loose", 1.))  # a loose ball is chased flat out
        elif m.state == "flight" and m.flight["kind"] == "lofted":  # win the second ball / header
            d = nrm(P - f(m.flight["end"]), axis=1)
            if f(m.flight["end"])[0] > 18:  # the keeper only comes for balls into his own area
                d[role == GK] = 1e9
            for i in np.argsort(d)[:2]:
                zone[i], urgent[i] = f(m.flight["end"]), True
                m.sprint_hint[11 * t + i] = bool(TP.get("hint_air", 1.))  # the contest for a ball in the air
        elif m.state == "flight" and m.flight["kind"] == "ground" and m.t - m.flight["t0"] > TP.get("react_def", .35):  # defenders react ~.35 s after release; nearest-in-time one moves to the lane
            fl = m.flight
            a, b = B, f(fl["end"])
            ab = b - a
            s = clip(((P - a) @ ab) / max(ab @ ab, 1e-6), 0, 1)
            q = a + s[:, None] * ab
            slack = nrm(P - q, axis=1) / (m.vmax[sl] * .9) - s * nrm(ab) / fl["speed"]
            if f(fl["end"])[0] > 30:  # the keeper only sweeps up balls played in behind towards his goal
                slack[role == GK] = 1e9
            taken = set()
            for i in np.argsort(slack)[:2]:
                if slack[i] < 1.2:
                    zone[i], urgent[i] = q[i], True
                    m.sprint_hint[11 * t + i] = bool(TP.get("hint_lane", 1.))  # getting to the lane in time
                    taken.add(int(i))
            e_ = f(fl["end"])
            if e_[0] < 30. and role[0] == GK:  # the keeper comes for a ball into his area if he can get there no later than the nearest attacker
                gk_t = float(nrm(P[0] - e_)) / (m.vmax[sl][0] * .9) + .15
                att_t = float((nrm(O[1:] - e_, axis=1) / (m.vmax[osl][1:] * .9)).min())
                if gk_t < att_t + GK_RISK:
                    zone[0], urgent[0] = e_, True
                    m.sprint_hint[11 * t] = True
            # the nearest other defender closes the receiver so that he arrives with the ball
            de = nrm(P - e_, axis=1)
            de[role == GK] = 1e9
            for i in np.argsort(de)[:2]:
                if int(i) not in taken and de[i] < 12:
                    zone[i], urgent[i] = e_ + _unit(GOAL - e_) * TP.get("close_gap", 1.5), True
                    m.sprint_hint[11 * t + i] = bool(TP.get("hint_close", 1.))  # closing the receiver
                    break
            if TP.get("assign_on", 1.) and m.flight["rcv"] >= 0 and m.flight["rcv"] // 11 == 1 - t:  # everyone else is covering as if the receiver already had it: the same assignment, around where the ball is going
                avail = ~urgent
                zone, urgent, _, _ = defend_assign(m, t, zone, urgent, role, P, O, fv(m.vel[osl]), e_, np.zeros(2), m.flight["rcv"] - 11 * (1 - t), tc, e_, e_, 0., False, False, nrm(P - e_, axis=1), avail, False)
        if m.state != "dead":
            bl = ((role == CB) | (role == FB)) & ~urgent & (gate < TP.get("flat_gate", .6))  # a defender who has a man running at goal stays with him: the line does not pull him back level
            if bl.sum() >= 2 and float(nrm(Bs - GOAL)) > 26.:  # marking must not pull defenders out of the line: re-flatten it (outside the box)
                mx = zone[bl, 0].mean()
                zone[bl, 0] = mx + LINE_FLAT2 * (zone[bl, 0] - mx)
            # a defender left deep behind his line's spot steps up at once ('out!'), not at a stroll
            bl_ = out_f & ~urgent & (gate < TP.get("flat_gate", .6))
            urgent |= bl_ & (zone[:, 0] - P[:, 0] > 3.)
            if m.poss == 1 - t and carrier >= 0:
                if not TP.get("assign_on", 1.):
                    zone, urgent = pick_up_free(zone, urgent, gate, role, P, O, carrier - 11 * (1 - t), tc)
                zone, urgent = cut_lanes(zone, urgent, gate, role, P, O, O[carrier - 11 * (1 - t)], carrier - 11 * (1 - t))
            Bd = f(m.flight["end"]) if m.state == "flight" and m.flight["kind"] in ("ground", "lofted") and m.flight["team"] != t else B  # build the cone around where the ball is going
            if not (TP.get("assign_on", 1.) and m.poss == 1 - t and carrier >= 0):
                zone, urgent = box_defence(m, Bd, zone, urgent, gate, role, P)
            # DEFENSIVE RECOVERY (the open-source Open Football engine, where it moved the number of attackers with a defender goal-side of the ball at a shot
            # from 0.2 to 3.3 and fixed "every shot is a one-on-one with the keeper"): a defender who is not engaging the ball and has the ball goal-side of him
            # goes back to a cover point three metres goal-side of it. His job (a man, a lane) fixes where he stands across and along the ball line; this only
            # stops a job putting him on the wrong side of the ball. It is inert for a defender already goal-side of the cover point.
            if TP.get("recover_on", 1.):
                engaged = m.sprint_hint[sl]  # the presser and the men closing the receiver are going to the ball
                lim = out_f & ((role != ST) | (Bs[0] < TP.get("recover_st", 0.))) & ~engaged
                cover_x = Bs[0] - TP.get("cover_margin", 3.)
                zone[lim, 0] = np.minimum(zone[lim, 0], np.maximum(cover_x, MIN_X[role[lim]]))
            urgent |= (gate > .6) & (nrm(zone - P, axis=1) > 2.5)  # a man-marker in the danger area stays with his man at the man's pace
            behind_m = (gate > .6) & (nrm(zone - P, axis=1) > TP.get("mark_sprint_d", 3.0))  # a marker who is losing his man goes flat out: effort follows need, he does not jog after a sprinting forward
            m.sprint_hint[11 * t + np.nonzero(behind_m)[0]] = bool(TP.get("hint_mark", 1.))
            rec = out_f & (P[:, 0] - zone[:, 0] > 5.) & (Bs[0] < .65 * L)
            urgent |= rec  # recovery run: well upfield of where he should be, ball in his half: sprint back, do not jog
            if TP.get("rec_sprint", 0.):
                m.sprint_hint[sl] |= rec & (P[:, 0] - zone[:, 0] > TP["rec_sprint"])  # with his goal under threat a man who is beaten by the ball runs flat out
    else:
        # runs in behind
        if mine and m.state == "held":
            C = P[carrier - 11 * t]
            elig = ((role == ST) | (role == WG) | (role == AM)) & (m.t >= m.run_cool[sl]) & (C[0] > .3 * L)
            start = elig & (m.rng.random(11) < (.07 + .15 * tc.directness) * m.mod["runs"][sl] * m.dt * TP.get("run_k", 1.))
            gdir = np.array([1., 0.]) if t == 0 else np.array([-1., 0.])
            free_c = float(nrm(m.pos[11 * (1 - t):11 * (1 - t) + 11] - m.pos[carrier], axis=1).min()) > 2.5
            start &= bool(free_c and float(m.head[carrier] @ gdir) > .3)  # a run is made when the man on the ball can play it: facing up the pitch with nobody on him
            for i in np.nonzero(start)[0]:
                line = max(np.sort(O[:, 0])[-2], L / 2)
                lead = TP.get("run_lead", 1.) * float(clip(.8 * m.vmax[11 * t + i] * 1.5, 5., 22.))  # a run in behind goes where the ball will be put: as far as he covers while it travels (Open Football's through-ball lead, 5-22 m)
                m.run_tgt[11 * t + i] = (min(line + (lead if lead > 0 else m.rng.uniform(-1.4, 1.0)), C[0] + 40, L - 4.), clip(A[i, 1] + m.rng.uniform(-9, 9), 4, W - 4))
                m.run_until[11 * t + i] = m.t + m.rng.uniform(2.5, 4.0)
                m.run_cool[11 * t + i] = m.t + m.rng.uniform(6, 12)
            if abs(C[1] - W / 2) > 15 and C[0] > .62 * L and not of:  # ball wide in the final third: get bodies into the box
                sd = 1 if C[1] > W / 2 else -1
                line_ = max(np.sort(O[:, 0])[-2], L / 2)
                order = ([i for i in idx if role[i] == ST] + [i for i in idx if role[i] == AM]
                         + [i for i in idx if role[i] == WG and (P[i][1] - W / 2) * sd < 0] + [i for i in idx if role[i] == CM])
                spots = [(L - 8, W / 2 - sd * 2.5), (L - 9.5, W / 2 + sd * 5.5), (L - 18, W / 2 - sd * 1), (L - 20, W / 2 + sd * 3), (L - 19, W / 2 - sd * 8)]
                for i, sp_ in zip([i for i in order if 11 * t + i != carrier][:5], spots):
                    zone[i], urgent[i] = (min(sp_[0], line_ + .5), sp_[1]), True
            # overlap: a team-mate on the ball out wide with his full-back behind him, the full-back runs past on the outside
            if abs(C[1] - W / 2) > 17 and C[0] > .38 * L and role[carrier - 11 * t] != FB:
                sd = 1 if C[1] > W / 2 else -1
                for i in idx:
                    if role[i] == FB and (A[i, 1] - W / 2) * sd > 0 and P[i, 0] < C[0] - 2 and m.t >= m.run_cool[11 * t + i] and m.t >= m.run_until[11 * t + i]                             and m.rng.random() < OVERLAP_RATE * m.mod["runs"][11 * t + i] * m.dt:
                        m.run_tgt[11 * t + i] = (min(C[0] + 11., L - 9.), float(clip(C[1] + sd * 6., 3., W - 3.)))
                        m.run_until[11 * t + i] = m.t + m.rng.uniform(2.5, 3.5)
                        m.run_cool[11 * t + i] = m.t + m.rng.uniform(9, 16)
            # support triangles around the carrier
            pr = clip(1 - np.min(nrm(O - C, axis=1)) / 5, 0, 1)
            cand = np.nonzero(out_f & (idx != carrier - 11 * t))[0]
            for i in (cand[np.argsort(nrm(P[cand] - C, axis=1))][:2] if not of else []):
                side = 1 if P[i, 1] >= C[1] else -1
                th = .9 if pr < .5 else 2.3
                zone[i] = TP.get("tri_w", .55) * (C + 15 * np.array([np.cos(th), side * np.sin(th)])) + (1 - TP.get("tri_w", .55)) * zone[i]
        holder = carrier - 11 * t if mine else (m.dead["taker"] - 11 * t if m.state == "dead" and kind in ("throw_in", "goal_kick", "drop_ball", "indirect_free_kick", "free_kick") else -1)
        if holder >= 0 and kind not in ("corner", "kickoff"):
            skip = (idx == holder) | urgent | (m.t < m.run_until[sl]) | plan_skip
            zone = seek_space(m, t, P, O, P[holder] if mine else B, zone, role, out_f, skip, max(np.sort(O[:, 0])[-2], L / 2))
        urgent |= out_f & (zone[:, 0] - P[:, 0] > 6.) & (Bs[0] > .3 * L)  # joining the attack: well behind where he should be with the ball in the opposition half, he sprints up instead of jogging
        urgent |= m.space_burst[sl] & has & out_f & (m.t < m.space_until[sl] - .3)  # moving into space at pace, not drifting
        if holder >= 0 and kind not in ("corner", "kickoff") and mine:
            fwp = out_f & (idx != holder) & ~urgent
            near_c = nrm(zone - P[holder], axis=1)
            zone = zone + _unit(zone - P[holder]) * (np.maximum(SUPPORT_MIN - near_c, 0.) * fwp)[:, None] * .6  # nobody crowds the carrier
            dz_ = zone[:, None] - zone[None]
            dd_ = np.maximum(nrm(dz_), 1e-3) + np.eye(11) * 99
            push_ = (dz_ / dd_[..., None] * np.maximum(ATT_SPACING - dd_, 0)[..., None] * (fwp[:, None] & fwp[None])[..., None] * .5).sum(1)
            zone = zone + push_ * fwp[:, None]
            zone[fwp, 0] = np.minimum(zone[fwp, 0], max(float(np.sort(O[:, 0])[-2]), L / 2, float(Bs[0])) + 1.0)  # and the push does not take anyone beyond the offside limit
        if has and kind not in ("corner", "kickoff") and TP.get("wide_on", 1.):
            # the width plan: the man holding a touchline stands on it, moving up and down the line but not infield to where the threat is.
            # The far-side holder is the one who attacks the back post once the ball is in the final third, so the box slot outranks his width anchor.
            hug = float(clip(2. * tc.width, 0, 1)) * fd  # how committed the side is to playing wide
            for k, h in enumerate(hold):
                if h < 0 or h == holder or (m.t < m.run_until[11 * t + h]):
                    continue
                far = (k == 0) != (Bs[1] < W / 2)  # the flank the ball is not on
                zone[h, 1] += ((INSET if k == 0 else W - INSET) - zone[h, 1]) * hug * (((1. - wt) * (1. - TP.get("far_tuck", 0.) * float(clip((abs(Bs[1] - W / 2) - 10.) / 17., 0, 1)))) if far else 1.)
        running = has & (m.t < m.run_until[sl])
        for i in np.nonzero(running)[0]:
            zone[i], urgent[i] = m.run_tgt[11 * t + i], True
            m.sprint_hint[11 * t + i] = True  # a timed run in behind is flat out
        if kind == "free_kick" and B[0] > .6 * L:  # attackers into the box for a delivery
            order = [i for r in (ST, CB, AM, CM, WG) for i in idx if role[i] == r and 11 * t + i != m.dead["taker"]]
            spots = [(L - 9, W / 2 - 2), (L - 11, W / 2 + 4), (L - 13, W / 2), (L - 15, W / 2 - 6), (L - 16, W / 2 + 7)]
            for i, sp_ in zip(order, spots):
                zone[i], urgent[i] = sp_, True
        # open a passing lane: step away from a close marker
        D = cdist(P, O)
        j = D.argmin(1)
        near = D[idx, j]
        push = (near < 4.5) & out_f
        zone[push] += _unit(P[push] - O[j[push]]) * ((4.5 - near[push]) * .8)[:, None]
        if m.state == "flight":  # intended receiver goes to where the ball is going
            r = m.flight["rcv"] - 11 * t
            if 0 <= r < 11:
                zone[r], urgent[r] = f(m.flight["end"]), True  # receiver tracks the real ball flight
                m.sprint_hint[11 * t + r] = True

    if not has and m.state != "dead" and TP.get("area_w", 1.):
        # a defence with the ball in its final third protects the area in front of goal: nobody not engaging the ball stands wider than the penalty area (40 m), the
        # ball-side men go as wide as the ball needs. Real teams have every defender inside it (0.15 per pass beyond 20 m of the centre line against 0.5 here).
        wthr = float(clip((50. - Bs[0]) / 15., 0, 1))
        if wthr > 0:
            dy = zone[:, 1] - W / 2
            half = TP.get("area_half", 20.2)  # how far from the middle the men away from the ball may stand (real defensive units: about 14 m with the ball central)
            far_lim = max(half - TP.get("area_slide", 0.) * abs(by), 4.)  # the whole unit slides to the ball: the far side tucks in as the ball goes wide
            lim = np.where(np.sign(dy) == np.sign(by), max(half, abs(by) * TP.get("area_ball", .8)), far_lim)  # the ball side goes as wide as the ball needs
            keep = out_f & ~m.sprint_hint[sl]
            zone[:, 1] = np.where(keep, W / 2 + dy * (1 - wthr) + np.clip(dy, -lim, lim) * wthr, zone[:, 1])
            urgent |= keep & (np.abs(P[:, 1] - W / 2) > lim + 3.) * (wthr > .5) * bool(TP.get("area_run", 0.))  # a man left outside the unit's width gets back across at a run

    # ---- goalkeeper ---------------------------------------------------------------------
    sw = 1 + 1.2 * m.mod["sweep"][11 * t]  # sweeper keepers play higher
    rush = 0.
    if not has and m.state == "held" and m.poss == 1 - t and carrier >= 0:
        Cc = O[carrier - 11 * (1 - t)]
        dgl = float(nrm(Cc - GOAL))
        if dgl < 34.:
            sp_pt = Cc + _unit(GOAL - Cc) * max(dgl - 18., 0.)  # where he becomes a real shooting threat
            if float(nrm(P[1:] - sp_pt, axis=1).min()) / 7.5 > float(nrm(sp_pt - Cc)) / 7. + 1.:  # no team-mate gets there a second before him
                rush = float(clip((34. - dgl) / 14., .35, 1.))
    if not urgent[0]:
        if has:
            gx, gy = clip(2.5 + .12 * Bs[0] * sw, 2.5, 14 * sw), W / 2 + clip(by * .22, -8, 8)
        else:  # on the line between the ball and the goal centre, nearer the line the closer the ball
            v = Bs - GOAL
            db = max(float(nrm(v)), 1e-6)
            depth = clip(1.2 + .08 * db + 2 * (sw - 1), 1.5, 8 + 6 * (sw - 1))
            if rush > 0:
                depth = max(depth, float(clip(db - 5.5, 0., 16.)) * rush)  # out to meet the carrier, not beyond the edge of the area
            gx, gy = v[0] / db * depth, W / 2 + clip(v[1] / db * depth, -7, 7)
        zone[0] = (clip(gx, 1., 22.), gy)
    return f(np.stack([clip(zone[:, 0], 1, L - 1), clip(zone[:, 1], 1, W - 1)], 1)), urgent
