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
MIN_X = np.array([0, 12, 11, 17, 21, 24, 26, 30.])  # deepest a role will drop (defenders hold the box edge, they do not hug the goal line)
GOAL = np.array([0., W / 2])
TUNE_COMMIT = .12


def _tf(a):
    """world <-> team frame for the away side (an involution: flips both axes)."""
    return np.stack([L - a[..., 0], W - a[..., 1]], -1)


def _unit(v):
    return v / np.maximum(np.linalg.norm(v, axis=-1, keepdims=True), 1e-6)


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
        else:
            order = [i for i in idx if role[i] > GK]
            spots = [(5, 30), (5, 38), (8, 27), (8, 41), (11, 34), (6, 34), (13, 30), (13, 38)]
            for i, s in zip(order, spots):
                tg[i] = s
            tg[0] = (1.5, W / 2)
        return f(tg), urgent

    # ---- team block: follows the ball, shaped by tactic and phase ---------------------
    kx = FOLLOW[role] * (1 + .3 * (tc.compactness - .5))
    tx = A[:, 0] + kx * (B[0] - L / 2) + (tc.line_height - .5) * 14 * out_f
    by = B[1] - W / 2
    if has:
        ph = np.clip(.45 + B[0] / L, .5, 1.1)
        tx = tx + PUSH[role] * (.6 + .8 * tc.risk) * ph
        ty = W / 2 + (A[:, 1] - W / 2) * (1 + (tc.width - .5) * .7) + by * .2 * out_f
    else:
        tx = tx + DROP[role] * np.clip(1.25 - tc.press, .3, 1.2)
        ty = W / 2 + (A[:, 1] - W / 2) * (1 - .3 * tc.compactness) + by * (.28 + .22 * tc.compactness) * out_f
    if has:  # attackers stay onside while the ball is behind them
        line = max(np.sort(O[:, 0])[-2], L / 2)
        lim = max(line, B[0]) - .6
        att = (role == ST) | (role == WG) | (role == AM)
        tx = np.where(att, np.minimum(tx, lim), tx)
    tx = np.maximum(tx, MIN_X[role])
    cx = tx[out_f].mean()  # compact the block around its own centre
    tx = np.where(out_f, cx + (tx - cx) * (.9 - .2 * tc.compactness), tx)
    zone = np.stack([np.clip(tx, 2, L - 2), np.clip(ty, 2, W - 2)], 1)

    carrier = m.carrier if m.state == "held" else -1
    mine = 11 * t <= carrier < 11 * t + 11

    if not has:
        # man/zone marking: blend zone slot with a goal-side spot next to the assigned opponent
        mk = m.mark[t]
        ok = mk >= 0
        o = O[np.where(ok, mk, 0)]
        man = o + _unit(GOAL - o) * (1.2 + 1.8 * (1 - tc.marking))
        gate = np.clip(1.4 - np.linalg.norm(zone - o, axis=1) / 22, 0, 1) * tc.marking * ok
        zone = zone * (1 - gate[:, None]) + man * gate[:, None]
        # pressing the opposing ball carrier
        if m.poss == 1 - t and carrier >= 0:
            C, Cv = O[carrier - 11 * (1 - t)], fv(m.vel[carrier])
            depth = C[0] / L
            R = (4 + 14 * tc.press) * (1.5 if m.t < m.trans_until[t] else 1.0)
            if depth > .5:
                R *= max(.15, 1 - (depth - .5) * 2 * (1 - tc.press) * .9)
            d = np.linalg.norm(P - C, axis=1)
            d[role == GK] = 1e9
            n = 1 + (tc.press > .65)
            near = np.argsort(d)
            danger = np.clip((.4 * L - C[0]) / (.3 * L), 0, 1)
            if danger > 0:  # shot-blocking cover: the next two defenders drop onto the ball-goal line
                gv = _unit(GOAL - C)
                for k, i in enumerate(near[n:n + 2]):
                    if d[i] < 22:
                        zone[i] = C + gv * (3.5 + 2.5 * k) + np.array([-gv[1], gv[0]]) * (1.6 * k * (1 if k % 2 else -1))
                        urgent[i] = True
            for i in np.argsort(d)[:n]:
                if d[i] < R:
                    g = 11 * t + i  # jockey goal-side; occasionally commit to the tackle
                    danger = np.clip((.4 * L - C[0]) / (.3 * L), 0, 1)  # defenders commit more as the ball nears their goal
                    if m.t >= m.commit_until[g] and m.rng.random() < (TUNE_COMMIT + .3 * tc.press) * (1 + 2 * danger) * m.dt:
                        m.commit_until[g] = m.t + m.rng.uniform(1., 2.)
                    zone[i] = C + Cv * .2 if m.t < m.commit_until[g] else C + _unit(GOAL - C) * (1.8 + 1.6 * (1 - tc.press)) + Cv * .2
                    urgent[i] = True
        elif m.state == "loose":
            d = np.linalg.norm(P - B, axis=1)
            d[role == GK] = 1e9
            for i in np.argsort(d)[:2]:
                zone[i], urgent[i] = B + Bv * .6, True
        elif m.state == "flight" and m.flight["kind"] == "lofted":  # win the second ball / header
            d = np.linalg.norm(P - f(m.flight["end"]), axis=1)
            d[role == GK] = 1e9
            for i in np.argsort(d)[:2]:
                zone[i], urgent[i] = f(m.flight["end"]), True
        elif m.state == "flight" and m.flight["kind"] == "ground" and m.t - m.flight["t0"] > .35:  # defenders react ~.35 s after release; nearest-in-time one moves to the lane
            fl = m.flight
            a, b = B, f(fl["end"])
            ab = b - a
            s = np.clip(((P - a) @ ab) / max(ab @ ab, 1e-6), 0, 1)
            q = a + s[:, None] * ab
            slack = np.linalg.norm(P - q, axis=1) / (m.vmax[sl] * .9) - s * np.linalg.norm(ab) / fl["speed"]
            slack[role == GK] = 1e9
            for i in np.argsort(slack)[:2]:
                if slack[i] < 1.2:
                    zone[i], urgent[i] = q[i], True
    else:
        # runs in behind
        if mine and m.state == "held":
            C = P[carrier - 11 * t]
            elig = ((role == ST) | (role == WG) | (role == AM)) & (m.t >= m.run_cool[sl]) & (C[0] > .3 * L)
            start = elig & (m.rng.random(11) < (.05 + .12 * tc.directness) * m.dt)
            for i in np.nonzero(start)[0]:
                line = max(np.sort(O[:, 0])[-2], L / 2)
                m.run_tgt[11 * t + i] = (min(line - 1, C[0] + 28), np.clip(A[i, 1] + m.rng.uniform(-9, 9), 4, W - 4))
                m.run_until[11 * t + i] = m.t + m.rng.uniform(2.5, 4.0)
                m.run_cool[11 * t + i] = m.t + m.rng.uniform(6, 12)
            # support triangles around the carrier
            pr = np.clip(1 - np.min(np.linalg.norm(O - C, axis=1)) / 5, 0, 1)
            cand = np.nonzero(out_f & (idx != carrier - 11 * t))[0]
            for i in cand[np.argsort(np.linalg.norm(P[cand] - C, axis=1))][:2]:
                side = 1 if P[i, 1] >= C[1] else -1
                th = .9 if pr < .5 else 2.3
                zone[i] = .55 * (C + 15 * np.array([np.cos(th), side * np.sin(th)])) + .45 * zone[i]
        running = has & (m.t < m.run_until[sl])
        for i in np.nonzero(running)[0]:
            zone[i], urgent[i] = m.run_tgt[11 * t + i], True
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
    zone[0] = (np.clip(2.5 + (.12 if has else .05) * B[0], 2.5, 14), W / 2 + np.clip(by * .22, -8, 8))
    return f(np.stack([np.clip(zone[:, 0], 1, L - 1), np.clip(zone[:, 1], 1, W - 1)], 1)), urgent
