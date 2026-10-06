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

GK, CB, FB, DM, CM, WG, AM, ST = range(8)
PUSH = np.array([0, 3, 9, 2, 4, 8, 7, 6.])        # metres forward when we have the ball
DROP = np.array([0, -2, -3, -1, -3, -6, -7, -10.])  # metres back when we do not
FOLLOW = np.array([.1, .5, .55, .55, .55, .5, .5, .45])  # how strongly the role follows the ball's x
CRUISE = np.array([1.7, 1.5, 1.55, 1.35, 1.75, 1.1, 1.1, .8])  # jogging speed by role (box-to-box midfielders cover more ground)
SHOULDER = np.array([0, 0, 0, 0, 0, -9., -13., -6.])  # where each attacking role stands relative to the last defender (m)
MIN_X = np.array([0, 12, 11, 17, 21, 24, 26, 30.])  # deepest a role will drop (defenders hold the box edge, they do not hug the goal line)
GOAL = np.array([0., W / 2])
TUNE_COMMIT = .08


_OFF = np.array([L, W])


def nrm(a, axis=-1, keepdims=False):
    """Euclidean norm over the last axis (a faster stand-in for np.linalg.norm on small arrays)."""
    r = np.sqrt(np.einsum("...i,...i->...", a, a))
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
    r, c = linear_sum_assignment(cdist(zone[D], O[Oo]))
    mk = -np.ones(11, int)
    mk[D[r]] = Oo[c]
    m.mark[t] = mk


def team_targets(m, t):
    """Returns (targets (11,2) in world coords, urgent (11,) bool) for team t."""
    sl, osl = slice(11 * t, 11 * t + 11), slice(11 * (1 - t), 11 * (1 - t) + 11)
    f = _tf if t else (lambda a: a)
    fv = (lambda v: -v) if t else (lambda v: v)
    P, O, B, Bv = f(m.pos[sl]), f(m.pos[osl]), f(m.ball), fv(m.ball_v)
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
        if has:
            order = [i for r in (ST, CB, AM, CM, WG, DM, FB) for i in idx if role[i] == r and 11 * t + i != m.dead["taker"]]
            spots = [(L - 7, 31), (L - 9, 37), (L - 12, 34), (L - 14, 28), (L - 14, 40), (L - 19, 34), (L - 22, 24), (L - 22, 44)]
            for i, s in zip(order, spots):
                tg[i] = s
            tg[0] = (35, W / 2)
            sup = m.dead.get("sup", -1) - 11 * t
            if m.dead.get("short") and 0 <= sup < 11:
                cor = f(m.dead["pos"])
                tg[sup] = cor + _unit(np.array([L / 2, W / 2]) - cor) * 9
        else:
            order = [i for i in idx if role[i] > GK]
            spots = [(5, 30), (5, 38), (8, 27), (8, 41), (11, 34), (6, 34), (13, 30), (13, 38)]
            for i, s in zip(order, spots):
                tg[i] = s
            tg[0] = (1.5, W / 2)
        return f(tg), urgent

    # ---- team block: follows the ball, shaped by tactic and phase ---------------------
    kx = FOLLOW[role] * m.mod["follow"][sl] * (1 + .3 * (tc.compactness - .5))
    tx = A[:, 0] + kx * (B[0] - L / 2) + (tc.line_height - .5) * 14 * out_f
    by = B[1] - W / 2
    if has:
        ph = clip(.45 + B[0] / L, .5, 1.1)
        tx = tx + PUSH[role] * (.6 + .8 * tc.risk) * ph
        ty = W / 2 + (A[:, 1] - W / 2) * (1 + (tc.width - .5) * .7) + by * .2 * out_f
    else:
        tx = tx + DROP[role] * clip(1.25 - tc.press, .3, 1.2)
        ty = W / 2 + (A[:, 1] - W / 2) * (1 - .3 * tc.compactness) + by * (.28 + .22 * tc.compactness) * out_f
    tx = np.maximum(tx, MIN_X[role])
    cx = tx[out_f].mean()  # compact the block around its own centre
    tx = np.where(out_f, cx + (tx - cx) * (.9 - .2 * tc.compactness), tx)
    if has and B[0] > .62 * L:  # midfielders arrive late at the edge of the box; wingers tuck in when the ball is on the far side
        late = (role == CM) | (role == AM)
        tx = np.where(late, np.maximum(tx, np.minimum(B[0] - 3, L - 20)), tx)
    if has:  # forwards hover around the last defender's shoulder: well timed they stay onside, mistimed they are flagged
        line = max(np.sort(O[:, 0])[-2], L / 2)
        att = (role == ST) | (role == WG) | (role == AM)
        if B[0] > .35 * L:
            tx = np.where(att, np.maximum(tx, line + SHOULDER[role] + 2. * np.sin(.5 * m.t + m.phase[sl])), tx)
    if has and B[0] > .55 * L:
        far = (role == WG) & (np.abs(B[1] - A[:, 1]) > 25)
        ty = np.where(far, W / 2 + np.sign(A[:, 1] - W / 2) * 9, ty)
    zone = np.stack([clip(tx, 2, L - 2), clip(ty, 2, W - 2)], 1)
    carrier = m.carrier if m.state == "held" else -1
    if has and B[0] > .6 * L:
        # attacking shape near the box: every role has a job (near post, far post, edge of the box, overlap, cover),
        # mirrored to the side the ball is on. Blended in as the ball gets closer to goal.
        sd = 1 if B[1] > W / 2 else -1
        w = float(clip((B[0] - .6 * L) / (.15 * L), 0, 1)) * .9
        line_t = max(np.sort(O[:, 0])[-2], L / 2) + .5
        templ = {ST: [(L - 9, W / 2 - sd * 2.5), (L - 11, W / 2 + sd * 6)], WG: [(L - 13, W / 2 + sd * 22), (L - 9, W / 2 - sd * 11)],
                 AM: [(L - 19, W / 2 + sd * 2), (L - 21, W / 2 - sd * 7)], CM: [(L - 21, W / 2 + sd * 9), (L - 23, W / 2 - sd * 8), (L - 31, W / 2)],
                 FB: [(L - 18, W / 2 + sd * 29), (L - 33, W / 2 - sd * 22)], DM: [(L - 32, W / 2)], CB: [(L - 44, W / 2 + 8), (L - 44, W / 2 - 8), (L - 46, W / 2)]}
        for r, spots in templ.items():
            free = [i for i in idx if role[i] == r and 11 * t + i != carrier]
            for spot in spots:
                if not free:
                    break
                i = min(free, key=lambda j: float(nrm(zone[j] - np.array(spot))))
                free.remove(i)
                tgt_ = np.array([min(spot[0], line_t), spot[1]])
                zone[i] = zone[i] * (1 - w) + tgt_ * w

    mine = 11 * t <= carrier < 11 * t + 11

    if not has:
        # man/zone marking: blend zone slot with a goal-side spot next to the assigned opponent
        mk = m.mark[t]
        ok = mk >= 0
        o = O[np.where(ok, mk, 0)]
        man = o + _unit(GOAL - o) * (1.2 + 1.8 * (1 - tc.marking))
        gate = clip(1.4 - nrm(zone - o, axis=1) / 22, 0, 1) * tc.marking * ok
        gate = np.where(ok & (o[:, 0] < 20) & (np.abs(o[:, 1] - W / 2) < 22) & (role > GK), np.maximum(gate, .85), gate)  # attackers in the box are marked, not watched
        zone = zone * (1 - gate[:, None]) + man * gate[:, None]
        # pressing the opposing ball carrier
        if m.poss == 1 - t and carrier >= 0:
            C, Cv = O[carrier - 11 * (1 - t)], fv(m.vel[carrier])
            depth = C[0] / L
            R = (3 + 9 * tc.press) * (1.5 if m.t < m.trans_until[t] else 1.0)
            if depth > .5:
                R *= max(.15, 1 - (depth - .5) * 2 * (1 - tc.press) * .9)
            d = nrm(P - C, axis=1)
            d[role == GK] = 1e9
            n = 1 + (tc.press > .8)
            near = np.argsort(d)
            danger = clip((.45 * L - C[0]) / (.3 * L), 0, 1)
            if danger > 0:  # shot-blocking cover: the next three defenders drop onto the ball-goal line
                gv = _unit(GOAL - C)
                for k, i in enumerate(near[n:n + 2]):
                    if d[i] < 22:
                        zone[i] = C + gv * (3.2 + 2.2 * k) + np.array([-gv[1], gv[0]]) * (1.8 * k * (1 if k % 2 else -1))
                        urgent[i] = True
            for i in np.argsort(d)[:n]:
                if d[i] < R:
                    g = 11 * t + i  # jockey goal-side; occasionally commit to the tackle
                    danger = clip((.4 * L - C[0]) / (.3 * L), 0, 1)  # defenders commit more as the ball nears their goal
                    if m.t >= m.commit_until[g] and m.rng.random() < (TUNE_COMMIT + .12 * tc.press) * m.mod["commit"][g] * (1 + 2 * danger) * m.dt:
                        m.commit_until[g] = m.t + m.rng.uniform(1., 2.)
                    zone[i] = C + Cv * .2 if m.t < m.commit_until[g] else C + _unit(GOAL - C) * (1.8 + 1.6 * (1 - tc.press)) + Cv * .2
                    urgent[i] = True
        elif m.state == "dead" and kind == "free_kick" and np.hypot(B[0], B[1] - W / 2) < 32:  # wall on the line to goal, 9.15 m back
            dist = float(np.hypot(B[0], B[1] - W / 2))
            n = 5 if dist < 19 else 4 if dist < 25 else 3
            u = _unit(GOAL - B)
            wc = B + u * 9.15
            perp = np.array([-u[1], u[0]])
            order = np.argsort(nrm(P - wc, axis=1) + (role == GK) * 1e9)[:n]
            for k, i in enumerate(order):
                zone[i], urgent[i] = wc + perp * (k - (n - 1) / 2) * .85, True
        elif m.state == "loose":
            d = nrm(P - B, axis=1)
            d[role == GK] = 1e9
            for i in np.argsort(d)[:2]:
                zone[i], urgent[i] = B + Bv * .6, True
        elif m.state == "flight" and m.flight["kind"] == "lofted":  # win the second ball / header
            d = nrm(P - f(m.flight["end"]), axis=1)
            if f(m.flight["end"])[0] > 18:  # the keeper only comes for balls into his own area
                d[role == GK] = 1e9
            for i in np.argsort(d)[:2]:
                zone[i], urgent[i] = f(m.flight["end"]), True
        elif m.state == "flight" and m.flight["kind"] == "ground" and m.t - m.flight["t0"] > .35:  # defenders react ~.35 s after release; nearest-in-time one moves to the lane
            fl = m.flight
            a, b = B, f(fl["end"])
            ab = b - a
            s = clip(((P - a) @ ab) / max(ab @ ab, 1e-6), 0, 1)
            q = a + s[:, None] * ab
            slack = nrm(P - q, axis=1) / (m.vmax[sl] * .9) - s * nrm(ab) / fl["speed"]
            if f(fl["end"])[0] > 30:  # the keeper only sweeps up balls played in behind towards his goal
                slack[role == GK] = 1e9
            for i in np.argsort(slack)[:2]:
                if slack[i] < 1.2:
                    zone[i], urgent[i] = q[i], True
    else:
        # runs in behind
        if mine and m.state == "held":
            C = P[carrier - 11 * t]
            elig = ((role == ST) | (role == WG) | (role == AM)) & (m.t >= m.run_cool[sl]) & (C[0] > .3 * L)
            start = elig & (m.rng.random(11) < (.07 + .15 * tc.directness) * m.mod["runs"][sl] * m.dt)
            for i in np.nonzero(start)[0]:
                line = max(np.sort(O[:, 0])[-2], L / 2)
                m.run_tgt[11 * t + i] = (min(line + m.rng.uniform(-1.4, 1.0), C[0] + 40), clip(A[i, 1] + m.rng.uniform(-9, 9), 4, W - 4))
                m.run_until[11 * t + i] = m.t + m.rng.uniform(2.5, 4.0)
                m.run_cool[11 * t + i] = m.t + m.rng.uniform(6, 12)
            if abs(C[1] - W / 2) > 15 and C[0] > .62 * L:  # ball wide in the final third: get bodies into the box
                sd = 1 if C[1] > W / 2 else -1
                line_ = max(np.sort(O[:, 0])[-2], L / 2)
                order = ([i for i in idx if role[i] == ST] + [i for i in idx if role[i] == AM]
                         + [i for i in idx if role[i] == WG and (P[i][1] - W / 2) * sd < 0] + [i for i in idx if role[i] == CM])
                spots = [(L - 8, W / 2 - sd * 2.5), (L - 9.5, W / 2 + sd * 5.5), (L - 18, W / 2 - sd * 1), (L - 20, W / 2 + sd * 3), (L - 19, W / 2 - sd * 8)]
                for i, sp_ in zip([i for i in order if 11 * t + i != carrier][:5], spots):
                    zone[i], urgent[i] = (min(sp_[0], line_ + .5), sp_[1]), True
            # support triangles around the carrier
            pr = clip(1 - np.min(nrm(O - C, axis=1)) / 5, 0, 1)
            cand = np.nonzero(out_f & (idx != carrier - 11 * t))[0]
            for i in cand[np.argsort(nrm(P[cand] - C, axis=1))][:2]:
                side = 1 if P[i, 1] >= C[1] else -1
                th = .9 if pr < .5 else 2.3
                zone[i] = .55 * (C + 15 * np.array([np.cos(th), side * np.sin(th)])) + .45 * zone[i]
        running = has & (m.t < m.run_until[sl])
        for i in np.nonzero(running)[0]:
            zone[i], urgent[i] = m.run_tgt[11 * t + i], True
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

    # ---- goalkeeper ---------------------------------------------------------------------
    sw = 1 + 1.2 * m.mod["sweep"][11 * t]  # sweeper keepers play higher
    if not urgent[0]:
        if has:
            gx, gy = clip(2.5 + .12 * B[0] * sw, 2.5, 14 * sw), W / 2 + clip(by * .22, -8, 8)
        else:  # on the line between the ball and the goal centre, nearer the line the closer the ball
            v = B - GOAL
            db = max(float(nrm(v)), 1e-6)
            depth = clip(1.2 + .08 * db + 2 * (sw - 1), 1.5, 8 + 6 * (sw - 1))
            gx, gy = v[0] / db * depth, W / 2 + clip(v[1] / db * depth, -7, 7)
        zone[0] = (clip(gx, 1., 22.), gy)
    return f(np.stack([clip(zone[:, 0], 1, L - 1), clip(zone[:, 1], 1, W - 1)], 1)), urgent
