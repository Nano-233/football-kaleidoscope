"""The attacking team plan, ported from the open-source Open Football engine (teamplay/plans/block.rs, attack.rs, wide.rs; ops/attacking/box_movement.rs, support.rs).

Positions in the attacking frame (the opposition goal at x = L), metres. What the plan decides, in the order Open Football decides it:
  1. the block: the formation's pattern projected into a rigid rectangle whose front rank leads the ball by 16 m, 27-41 m long, 37-56 m wide, sliding 30% of
     the ball's lateral offset. Everybody is somewhere in that rectangle unless a job moves him; nobody is 45 m behind the ball except the men kept home.
  2. width: one holder per touchline (movement.wide_holders), then who stays home (rest defence), then the overlap run (movement.py).
  3. four places in the box (near post, penalty spot, back post, edge of the area), each to one man, scored on how near he is, how well he suits the slot and how
     well he times a run. He does not stand on the spot: he holds a waiting point, in the defender's back, and goes when the ball is struck.
  4. the short outlet: the nearest free man offers himself 9.5-20 m from the carrier, outside the presser's shadow, never closer.
"""
import numpy as np
from .teams import L, W

LEN_COMPACT, LEN_STRETCH = 27.5, 41.        # block length (m) at full compactness and at full stretch
WID_NARROW, WID_WIDE = 45., 58.            # block width (m)
FRONT_LEAD, FRONT_CEIL = 16., .93 * L       # the front rank leads the ball by this much, and never goes past here
REAR_FLOOR, REAR_CEIL = .055 * L, .62 * L   # the rear of an attacking block
SLIDE = .30                                 # share of the ball's lateral offset the attacking block follows
MARGIN = 1.75

# the four box places: (finishing point, waiting point), each (depth from the goal line, lateral offset toward the ball side), metres
SLOTS = (((7.25, 4.25), (11., 1.)),         # near post: starts central, attacks across the front defender
         ((11.5, -.75), (18.25, -.75)),     # penalty spot: arrives late from the top of the area
         ((9.25, -7.), (11., -13.25)),      # back post: waits wide on the blind side, comes across
         ((16.75, 3.5), (22.5, 6.5)))       # edge of the area: the arriving cut-back receiver
AERIAL_BIAS = (.55, .45, .70, 0.)
ATTACK_RADIUS = 52.5                        # a man further from the ball than this is not part of the attack
APPROACH, STRIKE, ARRIVAL = .48, .65, .80   # ball progress where he leaves the block, reaches the waiting point, and is on the way to the patch
HOLD, ARRIVAL_STAGE = .5, .9
FLIGHT_RANGE = 35.                          # a ball struck towards goal from this close sends every occupant in at once
NEAR, FAR, SETTLED, CONTEST = 9.5, 20., .45, 5.5   # the short outlet's distance band, where it settles in the band, and how close a presser is pressure
GK, CB, FB, DM, CM, WG, AM, ST = range(8)


def block_anchors(A, out_f, ball, tc, line=0., slide=SLIDE, wid_scale=1.):
    """Anchor of every player in the attacking block (11, 2). A: the formation's own positions, out_f: outfield mask, ball: where the shape is built round.
    slide: share of the ball's lateral offset each player follows (a number or one per player): the men who are not on the wing follow the ball across the pitch."""
    xs, ys = A[out_f, 0], A[out_f, 1]
    depth = (A[:, 0] - xs.min()) / max(float(np.ptp(xs)), .01)
    lat = (A[:, 1] - ys.min()) / max(float(np.ptp(ys)), 1.)
    length = LEN_STRETCH - (LEN_STRETCH - LEN_COMPACT) * tc.compactness
    front = min(max(ball[0] + FRONT_LEAD, line - 1.), FRONT_CEIL)  # the front rank leads the ball, and in any case plays on the shoulder of the last defender
    rear = float(np.clip(front - length, REAR_FLOOR, REAR_CEIL))
    length = min(length, max(L - rear - MARGIN, LEN_COMPACT * .5))
    width = (WID_NARROW + (WID_WIDE - WID_NARROW) * tc.width) * wid_scale
    y = W / 2 + (lat - .5) * width + (ball[1] - W / 2) * slide
    return np.stack([rear + depth * length, np.clip(y, MARGIN + 1.5, W - MARGIN - 1.5)], 1)


def slot_point(slot, side, wait=False):
    d, lat = SLOTS[slot][1 if wait else 0]
    return np.array([L - d, W / 2 + side * lat])


def stage(progress, in_flight):
    """0 at the block, .5 at the waiting point, 1 on the patch. Read from the ball alone, so all four occupants go together."""
    if in_flight:
        return 1.
    a = np.clip((progress - APPROACH) / (STRIKE - APPROACH), 0, 1) * HOLD
    b = np.clip((progress - STRIKE) / (ARRIVAL - STRIKE), 0, 1) * (ARRIVAL_STAGE - HOLD)
    return float(a + b)


def staged_point(slot, side, st, hold):
    wait = slot_point(slot, side, True)
    if st >= HOLD:
        return wait + (slot_point(slot, side) - wait) * ((st - HOLD) * 2.)
    return hold + (wait - hold) * (st * 2.)


def in_flight_to_goal(m, t):
    """Is a ball struck by this side already on its way into the final 35 m?"""
    if m.state != "flight" or m.flight["team"] != t:
        return False
    end = m.flight["end"] if t == 0 else np.array([L, W]) - m.flight["end"]
    return bool(np.hypot(L - end[0], W / 2 - end[1]) < FLIGHT_RANGE and end[0] > (m.ball[0] if t == 0 else L - m.ball[0]))


# who a box place is for, by role: the near post is the striker's, the spot the striker's or the number ten's, the back post the far-side wide man's
# (the blind-side run that the slot's waiting point, 13 m wide of the ball side, describes), the edge the arriving midfielder's. Scored on fit alone
# the strikers took every place (they are nearest and strongest in the air) and the wingers received the ball in the box three times a match
# against the strikers' eighteen; real wide forwards take about a quarter of a side's shots
APPETITE = {ST: (1., 1., .55, .3), WG: (.5, .6, 1., .4), AM: (.5, .9, .5, 1.), CM: (.3, .6, .35, 1.), DM: (.15, .4, .2, .8), FB: (.12, .12, .5, .4), CB: (.12, .12, .12, .12)}


def _fit(m, t, i, slot, target, P, role, far_side=False):
    a = lambda k: m.attr[k][11 * t + i] / 20.
    reach = float(np.clip(1. - np.hypot(*(P[i] - target)) / 25., 0, 1))
    aerial = .8 * a("aerial") + .2 * a("strength")
    ground = .55 * a("finishing") + .25 * a("composure") + .2 * a("dribbling")
    bias = AERIAL_BIAS[slot]
    quality = aerial * bias + ground * (1. - bias)
    r = role[i]
    appetite = APPETITE.get(int(r), (.12,) * 4)[slot]
    if slot == 2 and r == WG and not far_side:
        appetite = .5  # the ball-side winger is the crosser or the width, not the back-post runner
    return (reach * .42 + quality * .28 + a("positioning") * .30) * appetite


def box_slots(m, t, role, out_f, P, ball, side, held, carrier, prev):
    """One man per box place (-1 where nobody suits it). Slots are filled near -> back -> spot -> edge from whoever is left, with a bonus for the incumbent."""
    ids = [-1] * 4
    for s in (0, 2, 1, 3):
        target = slot_point(s, side)
        best, score = -1, .25
        for i in range(11):
            if not out_f[i] or held[i] or i == carrier or i in ids or np.hypot(*(P[i] - ball)) >= ATTACK_RADIUS:
                continue
            far = (m.anchor[11 * t + i][1] - W / 2) * side < 0  # his formation place is on the far side of the ball
            sc = _fit(m, t, i, s, target, P, role, far) + (.15 if prev[s] == i else 0.)
            if sc > score:
                best, score = i, sc
        ids[s] = best
    return ids


def support_target(P_i, carrier_pos, presser_pos, goal):
    """Where a free man stands to be an option: an angle at a distance, the distance never below 9.5 m, outside the presser's shadow."""
    own = P_i - carrier_pos
    n = np.hypot(*own)
    if n < .01:
        return None
    own = own / n
    shadow = carrier_pos - presser_pos
    sn = np.hypot(*shadow)
    shadow = shadow / sn if sn > .01 else own
    fwd = goal - carrier_pos
    fwd = fwd / max(np.hypot(*fwd), .01)
    d = (own * .62 + shadow * .26 + fwd * .12)
    d = d / max(np.hypot(*d), .01)
    pressure = float(np.clip(1. - sn / CONTEST, 0, 1))
    want = NEAR + (FAR - NEAR) * SETTLED
    want = want + (NEAR - want) * pressure
    dist = float(np.clip(np.clip(n, NEAR, FAR) * .45 + want * .55, NEAR, FAR))
    return carrier_pos + d * dist



STANDOFF_PASSIVE, STANDOFF_PRESS = 25., 16.   # how far goal-side of the ball the deepest line stands, at a passive and at a hunting press
SLIDE_DEF = .55                               # a defending block slides over to the ball's side much further than an attacking one
DEF_WID = (37.5, 56.)


def defending_anchors(A, out_f, ball, tc):
    """The defending block (Open Football's ShapeBuilder, out of possession): the back line a standoff goal-side of the ball, capped by the tactic's line height,
    the block 27-41 m long and 37-56 m wide, sliding 55% of the ball's lateral offset. Own goal at x = 0."""
    xs, ys = A[out_f, 0], A[out_f, 1]
    depth = (A[:, 0] - xs.min()) / max(float(np.ptp(xs)), .01)
    lat = (A[:, 1] - ys.min()) / max(float(np.ptp(ys)), 1.)
    standoff = STANDOFF_PASSIVE - (STANDOFF_PASSIVE - STANDOFF_PRESS) * tc.press
    cap = L * (.30 + .35 * tc.line_height)
    rear = float(np.clip(min(ball[0] - standoff, cap), REAR_FLOOR, L))
    length = LEN_STRETCH - (LEN_STRETCH - LEN_COMPACT) * tc.compactness
    width = DEF_WID[0] + (DEF_WID[1] - DEF_WID[0]) * tc.width
    half = min(width / 2, W / 2 - MARGIN)
    cy = float(np.clip(W / 2 + (ball[1] - W / 2) * SLIDE_DEF, MARGIN + half, W - MARGIN - half))
    return np.stack([rear + depth * length, cy + (lat - .5) * width], 1)
