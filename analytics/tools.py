"""The analysis toolkit the newsroom agents call. Every number an agent may quote is computed here, from the event stream and the
tracking frames; no model computes a figure. Each tool returns a small JSON-ready dict.

    mv = MatchView(match)                 # match: the engine's output (meta, events, frames)
    mv.shape(team=0, t0=0, t1=2700)       # average positions and the formation they read as
    mv.formation_timeline(team=1)         # the formation read over the match, and when it changed
    mv.pressing(team=0)                   # PPDA and pressing height by 15 minutes, where the ball was won
    mv.momentum()                         # xG and field tilt by 5 minutes, spells of dominance
    mv.rhythm()                           # control vs chaos by 5 minutes: settled and broken possession, turnovers, tempo, stoppages
    mv.fatigue(team=0)                    # who is tiring: high-intensity running by 15 minutes against his own first half-hour and his team-mates
    mv.passing_network(team=0)            # pass links, connectors, where attacks start, preferred side
    mv.sequence(event_id)                 # what led to a moment, with the engine's reasons
    mv.breakdown(event_id)                # the move behind a shot as stops to pause on: the regain, both shapes, the pass options, the shot
    mv.player_profile(player_id)          # stats, rating terms, heat map, physical output, against role averages
    mv.duties(team=1)                     # out of possession, per player and 15 minutes: how often he closes the ball down, whom he tracks, the space he holds
    mv.find_moments(player=..., types=[...], outcome=..., third=..., minutes=(a, b))

Frames: team 0 attacks towards x = L, team 1 towards x = 0. Tools report positions in the team's own frame (own goal at x = 0,
attacking left to right), so the two teams read the same way.
"""
import bisect
import json
import pathlib

import numpy as np
from scipy.optimize import linear_sum_assignment

from analytics.stats import StatsAccumulator, L, W
from matchsim.teams import FORMATIONS

def _rows(n, parts):
    """Every way to split n outfield players into `parts` lines of 1-5, back to front."""
    if parts == 1:
        return [(n,)] if 1 <= n <= 5 else []
    return [(a,) + r for a in range(1, 6) for r in _rows(n - a, parts - 1)]


def _rows_layout(rows):
    """A plain layout for a split into lines: lines evenly deep, each line spread across the width (normalised like MatchView._norm)."""
    pts = []
    for i, c in enumerate(rows):
        ys = [.5] if c == 1 else [.3, .7] if c == 2 else [.15, .5, .85] if c == 3 else list(np.linspace(0, 1, c))
        pts += [(i / (len(rows) - 1), y) for y in ys]
    return np.array(pts)


def _hull(P):
    """Convex hull of 2-D points (monotone chain), as indices in order."""
    idx = sorted(range(len(P)), key=lambda i: (P[i][0], P[i][1]))
    cross = lambda o, a, b: (P[a][0] - P[o][0]) * (P[b][1] - P[o][1]) - (P[a][1] - P[o][1]) * (P[b][0] - P[o][0])
    lo, hi = [], []
    for seq, out in ((idx, lo), (idx[::-1], hi)):
        for i in seq:
            while len(out) >= 2 and cross(out[-2], out[-1], i) <= 0:
                out.pop()
            out.append(i)
    return lo[:-1] + hi[:-1]


SPRINT = 7.0      # m/s (25 km/h), the usual sprint threshold
nrm = lambda a, axis=-1: np.linalg.norm(a, axis=axis)
# the defensive line (mean of the four deepest outfield players, metres from his own goal) in words, against the simulated league: without the
# ball the ten demo sides read 25-40 m (median 31.5), the deep blocks (line_height 0.25-0.4) at 25-28 m and the high lines (0.65-0.72) at 40 m.
# Agents given the bare number called a 25 m line "high"; with the ball the whole team is further up, so the word is only given without it.
LINE_WORD = lambda m, phase: (None if phase != "out" else f"{'deep' if m < 29 else 'high' if m > 36 else 'medium'} (sides in these matches defend 25-40 m from their goal)")
DUTY_PRESS_R, DUTY_APPROACH, DUTY_MARK_R = 8., 2., 8.  # m, m/s, m: closing down (within 8 m and running at the carrier at 2 m/s or more), tracking a man (within 8 m)
ROLE_NORMS = pathlib.Path(__file__).with_name("role_norms.json")  # per-role per-90 averages over many simulated matches (scripts/role_norms.py)
GROUPS = {
    "passing": ("pass", "cross", "ball_receipt"),
    "shooting": ("shot", "goal"),
    "carrying": ("carry", "take_on", "dispossessed", "miscontrol"),
    "defending": ("tackle", "interception", "block", "clearance", "pressure", "duel", "fifty_fifty", "ball_recovery", "dribbled_past"),
    "keeping": ("save", "keeper_claim", "keeper_punch", "keeper_sweep"),
    "discipline": ("foul", "foul_won", "card", "offside", "error"),
}


class MatchView:
    def __init__(self, match):
        self.meta, self.events = match["meta"], match["events"]
        self.F = np.asarray(match["frames"], float)
        self.jobs = np.asarray(match["jobs"], int) if match.get("jobs") else None  # the engine's own defensive jobs: only for scoring the duty reader, never read by it
        self.stamina = np.asarray(match["stamina"], float) if match.get("stamina") else None  # the engine's fatigue: only for scoring the fatigue reader
        self._duty_cache = {}
        self.read_attack = True  # formation reader: the shape with the ball is read only from possession in the opposition half
        self.dt = self.meta["dt"]
        self.players = {}
        for t, side in enumerate(("home", "away")):
            for p in self.meta[side]["players"] + self.meta[side]["bench"]:
                self.players[p["id"]] = dict(p, team=t)
        self.names = {k: v["name"] for k, v in self.players.items()}
        self.team_names = [self.meta["home"]["name"], self.meta["away"]["name"]]
        self.by_id = {e["id"]: e for e in self.events}
        # who stands in each tracking slot over time
        self.slot0 = [p["id"] for p in self.meta["home"]["players"]] + [p["id"] for p in self.meta["away"]["players"]]
        self.slot_changes = sorted((e["t"], e["slot"], e["on"]) for e in self.events if e["type"] in ("substitution", "outfield_keeper") and "slot" in e)
        self.role_now = {pid: p["role"] for pid, p in self.players.items()}
        self._ct = [e["t"] for e in self.events if e.get("clock")]
        self._cc = [e["clock"] for e in self.events if e.get("clock")]

    def _windows(self, window):
        """(t0, t1) windows of `window` seconds within each half: the second half starts its own at kick-off, so no window mixes the end of
        the first half (its stoppage time) with the start of the second; a tail under a third of a window joins the one before it."""
        h2 = next((e["t"] for e in self.events if e["type"] == "half_start" and e.get("period") == 2), None)
        out = []
        for a, b in ((0., h2), (h2, self.meta["duration"])) if h2 else ((0., self.meta["duration"]),):
            w0, part = a, []
            while w0 < b - 1e-6:
                part.append((w0, min(w0 + window, b)))
                w0 += window
            if len(part) > 1 and part[-1][1] - part[-1][0] < window / 3:
                part[-2] = (part[-2][0], part[-1][1]); part.pop()
            out += part
        return out

    def _clock(self, t):
        """The match clock (as on the scoreboard, 45+2', and the second half from 46') at time t in seconds."""
        i = bisect.bisect_right(self._ct, t) - 1
        return self._cc[max(i, 0)] if self._cc else f"{int(t // 60) + 1}'"

    # ---------------------------------------------------------------- helpers
    def _frame_range(self, t0, t1):
        i0, i1 = int(max(0, t0) / self.dt), int(min(t1, self.meta["duration"]) / self.dt)
        return self.F[i0:max(i1, i0 + 1)]

    def _occupant(self, slot, t):
        pid = self.slot0[slot]
        for tt, s, on in self.slot_changes:
            if tt > t:
                break
            if s == slot:
                pid = on
        return pid

    def _team_xy(self, team, xy):
        """World -> the team's own frame (own goal at x = 0)."""
        xy = np.asarray(xy, float)
        return xy if team == 0 else np.stack([L - xy[..., 0], W - xy[..., 1]], -1)

    def _ev_xy(self, e):
        return self._team_xy(e["team"], [e["x"], e["y"]]) if "x" in e and e.get("team") in (0, 1) else None

    # ---------------------------------------------------------------- shape
    def shape(self, team, t0=0., t1=None, phase="out", attacking=False):
        """Average positions of the team's outfield players between t0 and t1 (seconds), in or out of possession (phase: in/out/all),
        and the formation the shape reads as: the template whose normalised layout fits best (Hungarian assignment), with the runner-up.
        attacking: with the ball, only frames with the ball in the opposition half (the formation reader's attacking shape)."""
        t1 = self.meta["duration"] if t1 is None else t1
        fr = self._frame_range(t0, t1)
        poss = fr[:, 4]
        sel = {"in": poss == team, "out": poss == 1 - team, "all": np.ones(len(fr), bool)}[phase]
        if attacking and phase == "in":
            sel &= (fr[:, 1] if team == 0 else L - fr[:, 1]) > L / 2
        if sel.sum() < 25:
            return dict(team=self.team_names[team], error="not enough frames in that phase and window")
        fr = fr[sel]
        tm = (t0 + t1) / 2
        rows = []
        for k in range(11 * team, 11 * team + 11):
            xy = fr[:, [5 + 2 * k, 6 + 2 * k]]
            on = xy[:, 1] > -5  # sent-off players are parked off the pitch
            if on.mean() < .5:
                continue
            pid = self._occupant(k, tm)
            rows.append((pid, self._team_xy(team, xy[on].mean(0))))
        gk = [r for r in rows if self.role_now.get(r[0]) == "GK"]
        out = [r for r in rows if r not in gk]
        P = np.array([r[1] for r in out])
        reads = self._read_formation(P)
        line = sorted(P[:, 0])
        return dict(
            team=self.team_names[team], window=[self._clock(t0), self._clock(t1)], phase=phase,
            formation=reads[0][0], runner_up=reads[1][0] if len(reads) > 1 else None, fit=round(reads[0][1], 3),
            defensive_line_m=round(float(np.mean(line[:4])), 1), block_length_m=round(float(np.ptp(P[:, 0])), 1), block_width_m=round(float(np.ptp(P[:, 1])), 1),
            defensive_line_is=LINE_WORD(float(np.mean(line[:4])), phase),
            positions=[dict(player=self.names[pid], id=pid, x=round(float(xy[0]), 1), y=round(float(xy[1]), 1)) for pid, xy in rows],
        )

    def shape_profile(self, team, t0, t1, phase, back="4"):
        """What the shape looked like on the pitch between t0 and t1 in one phase (in/out), in numbers a viewer could check: the
        height of the back line, the block's length and width, and, for a back line of `back` (four or more), how far the two widest men in
        it stood above the others (wing-backs or full-backs pushed up, or not); `back` is a formation name whose first number is the back
        line. Frames as the formation reader uses them, so the numbers
        describe the shape it named. None if the phase has too few frames."""
        s = self.shape(team, t0, t1, phase, attacking=self.read_attack)
        if "error" in s:
            return None
        P = sorted(([p["x"], p["y"]] for p in s["positions"] if self.role_now.get(p["id"]) != "GK"), key=lambda q: q[0])
        k = int(back.split("-")[0])
        back = sorted(P[:k], key=lambda q: q[1])
        out = dict(back_line_m=s["defensive_line_m"], block_length_m=s["block_length_m"], block_width_m=s["block_width_m"])
        if k >= 4:
            out["wide_backs_above_others_m"] = round(float(np.mean([back[0][0], back[-1][0]]) - np.mean([q[0] for q in back[1:-1]])), 1)
        return out

    def change_observed(self, team, c):
        """What the players did around a change from formation_timeline: shape_profile over the five minutes before it against the time
        since (to when it was confirmed), in its phase. Behind a back three the widest two of the five deepest are the wing-backs."""
        ph, k = ("in" if c["phase"] == "in_possession" else "out"), max(int(c["previous"][0]), int(c["formation"][0]))
        k = 5 if k == 3 else k
        before, since = self.shape_profile(team, c["t"] - 300, c["t"], ph, str(k)), self.shape_profile(team, c["t"], c["confirmed_t"], ph, str(k))
        if not (before and since):
            return None
        d = lambda key: round(since[key] - before[key], 1)
        # in plain words only, every number already worked out, so no writer has to subtract or guess which way a number runs
        words = []
        b = since["back_line_m"]
        words.append(f"back line about the same height, {b} m from their own goal" if abs(d("back_line_m")) < 2 else
                     f"back line {abs(d('back_line_m'))} m {'higher up the pitch' if d('back_line_m') > 0 else 'deeper'}, now {b} m from their own goal")
        for key, up, down, what in (("block_length_m", "longer", "shorter", "length"), ("block_width_m", "wider", "narrower", "width")):
            words.append(f"block about the same {what} ({since[key]} m)" if abs(d(key)) < 2 else f"block {abs(d(key))} m {up if d(key) > 0 else down}, now {since[key]} m")
        if "wide_backs_above_others_m" in since:
            v, was = since["wide_backs_above_others_m"], before.get("wide_backs_above_others_m")
            now_ = f"{v} m above the rest of the back line" if v >= 2 else f"{-v} m behind the rest of the back line" if v <= -2 else "level with the rest of the back line"
            moved = round(v - was, 1) if was is not None else 0
            words.append(f"the two widest of the back {k} now {now_}" + (f", {abs(moved)} m {'further forward' if moved > 0 else 'further back'} than before" if abs(moved) >= 2 else ""))
        return dict(phase=c["phase"].replace("_", " "), in_words=words, compared="the five minutes before the change against the time since")

    @staticmethod
    def _norm(P):
        lo, span = P.min(0), np.maximum(np.ptp(P, 0), 1.)
        return (P - lo) / span

    def _read_formation(self, P):
        """Template fits for the average positions P (outfield), best first. 3-5-2 and 5-3-2 are one layout to the fit (0.003 apart), so
        which name it is comes from the players: the two widest of the five deepest 4 m or more above the other three are wing-backs
        (3-5-2), level with them a back five (5-3-2)."""
        cand = self._fits(P)
        names = [c[0] for c in cand[:2]]
        if sorted(names) == ["3-5-2", "5-3-2"] and len(P) == 10:
            back = sorted(sorted(P.tolist(), key=lambda q: q[0])[:5], key=lambda q: q[1])
            want = "3-5-2" if np.mean([back[0][0], back[-1][0]]) - np.mean([q[0] for q in back[1:-1]]) >= 4 else "5-3-2"
            if cand[0][0] != want:
                cand[0], cand[1] = (cand[1][0], cand[0][1]), (cand[0][0], cand[1][1])  # swap the names, keep the costs in order
        return cand

    def _fits(self, P):
        n = len(P)
        cand = []
        for name, slots in FORMATIONS.items():
            A = np.array([[s[2] * L, s[3] * W] for s in slots if s[1] != "GK"])
            if len(A) != n:
                continue
            a, b = self._norm(P), self._norm(A)
            cost = ((a[:, None] - b[None]) ** 2 * np.array([1.6, 1.]) ).sum(2)  # depth (lines) matters more than spread
            r, c = linear_sum_assignment(cost)
            cand.append((name, float(cost[r, c].mean())))
        return sorted(cand, key=lambda x: x[1]) or [("unknown", 0.)]

    def lines_now(self, team, t, in_possession, back=60.):
        """The team's lines at time t, read from its average positions over the last `back` seconds of the current phase. No list of
        formations: every split of the outfield players into 3 or 4 lines is fitted (Hungarian, as _read_formation) and the best is the
        reading, so a side can be 2-3-5 with the ball and 4-4-1-1 without it if that is where its players stood. A fourth line must earn
        its place (a small penalty), so a flat 4-5-1 is not read as 4-1-4-1 by noise. Returns the label and the tracking slots in each line."""
        fr = self._frame_range(t - back, t + .1)
        fr = fr[fr[:, 4] == (team if in_possession else 1 - team)]
        if len(fr) < 25:
            fr = self._frame_range(t - 3, t + .1)
        P, slots = [], []
        for k in range(11 * team, 11 * team + 11):
            xy = fr[:, [5 + 2 * k, 6 + 2 * k]]
            on = xy[:, 1] > -5
            if on.mean() < .5 or self.role_now[self._occupant(k, t)] == "GK":
                continue
            P.append(self._team_xy(team, xy[on].mean(0)))
            slots.append(k)
        P = np.array(P)
        best = None
        for parts in (3, 4):
            for rows in _rows(len(P), parts):
                if rows[0] < 2:
                    continue
                cost = ((self._norm(P)[:, None] - _rows_layout(rows)[None]) ** 2 * np.array([1.6, 1.])).sum(2)
                r, c = linear_sum_assignment(cost)
                score = cost[r, c].mean() + .015 * (parts - 3)
                if best is None or score < best[0]:
                    best = (score, rows, dict(zip(r, c)))
        _, rows, slot_of = best
        starts = np.cumsum((0,) + rows)
        lines = [[slots[i] for i in sorted((i for i in range(len(P)) if a <= slot_of[i] < b), key=lambda i: P[i][1])] for a, b in zip(starts, starts[1:])]
        return dict(label="-".join(map(str, rows)), lines=lines)

    def _window_costs(self, team, t0, t1, phase):
        """Template fit costs for the average shape in one window and phase ({} when there are too few frames)."""
        fr = self._frame_range(t0, t1)
        sel = fr[:, 4] == (team if phase == "in" else 1 - team)
        if sel.sum() < 25:
            return {}
        fr = fr[sel]
        if phase == "in" and self.read_attack:  # the shape with the ball is the attacking shape: read where the side has it in the opposition half
            att = (fr[:, 1] if team == 0 else L - fr[:, 1]) > L / 2
            if att.sum() < 25:
                return {}
            fr = fr[att]
        P = []
        for k in range(11 * team, 11 * team + 11):
            xy = fr[:, [5 + 2 * k, 6 + 2 * k]]
            on = xy[:, 1] > -5
            if on.mean() < .5 or self.role_now[self._occupant(k, (t0 + t1) / 2)] == "GK":
                continue
            P.append(self._team_xy(team, xy[on].mean(0)))
        return dict(self._read_formation(np.array(P)))

    def formation_timeline(self, team, window=420., step=210., margin=2., scan=300., every=60., confirm=120., hold=1200.):
        """The shape in possession and the shape out of possession, each read from the data and tracked on its own: the two can differ,
        and which shape a team defends in is whatever its players do, not assumed from the system. With the ball the shape is read from
        possession in the opposition half (the attacking organisation; build-up and goal kicks drop the wingers and blur it). The starting shape is read from the
        first `window` seconds; then a `scan`-second window slides forward `every` seconds, and a change is reported where the window
        starting there fits another layout better than the current one by `margin`, and so does the window starting `confirm` seconds
        later (cut short at the final whistle, so a change in the last minutes can still be seen). Two names for one layout
        (4-5-1 / 4-1-4-1, see same_shape) are not a change. `confirmed_t` is when the second window ends: when a viewer could know."""
        end = self.meta["duration"]
        best = lambda c: min(c, key=c.get) if c else None
        reads = {}

        def costs(t0, ph):
            if (t0, ph) not in reads:
                reads[t0, ph] = self._window_costs(team, t0, min(t0 + scan, end), ph) if end - t0 >= scan / 2 else {}
            return reads[t0, ph]
        start, changes = {}, []
        for ph, phase in (("in", "in_possession"), ("out", "out_of_possession")):
            cur = best(self._window_costs(team, 0., window, ph))
            start[phase] = cur
            t = window - scan / 2
            while cur and t + confirm < end - scan / 2:
                ca, cb = costs(t, ph), costs(t + confirm, ph)
                na = best(ca)
                if na and na == best(cb) and not self.same_shape(na, cur) and ca.get(cur, 9) > margin * ca[na] and cb.get(cur, 9) > margin * cb[na]:
                    changes.append(dict(phase=phase, at=self._clock(t), t=t, formation=na, previous=cur, confirmed_t=min(t + confirm + scan, end)))
                    cur = na
                    t += confirm
                t += every
        changes = [c for c in changes if c["phase"] == "in_possession"] + [c for c in changes if c["phase"] != "in_possession"]  # grouped by phase, each in time order
        changes = self._drop_reverts(changes, hold)
        changes.sort(key=lambda c: c["t"])
        wins = np.arange(0., end - 120, 2 * step)
        return dict(team=self.team_names[team], starting=start, changes=changes,
                    by_window=[dict(at=self._clock(t), t=t, in_possession=best(self._window_costs(team, t, t + window, "in")),
                                    out_of_possession=best(self._window_costs(team, t, t + window, "out"))) for t in wins])

    def _drop_reverts(self, changes, hold):
        """A shape that goes back to the one before within `hold` seconds was drift (wingers dropping for a spell), not a new system:
        drop both readings. Changes come grouped by phase, in time order."""
        out = []
        for c in changes:
            p = out[-1] if out and out[-1]["phase"] == c["phase"] else None
            if p and c["t"] - p["t"] < hold and self.same_shape(c["formation"], p["previous"]):
                out.pop()
            else:
                out.append(c)
        return out

    def same_shape(self, a, b):
        """Two formation names for the same layout: their templates are within 0.01 of each other in the reader's own normalised fit
        (4-5-1 / 4-1-4-1 and 3-5-2 / 5-3-2 are 0.003 apart; every other pair is at least 0.029)."""
        if a == b:
            return True
        if a not in FORMATIONS or b not in FORMATIONS:
            return False
        A = np.array([[sl[2] * L, sl[3] * W] for sl in FORMATIONS[a] if sl[1] != "GK"])
        return dict(self._fits(A)).get(b, 9.) < .01

    def score_reading(self, team, read_changes, tolerance=600.):
        """How a reading of the formation changes compares with what the engine did: a true change is found when the reading reports
        the same formation within `tolerance` seconds after it; a reported change that matches none is a false alarm."""
        truth = self.truth(team)["formation_changes"]
        used, hits = set(), []
        for tc in truth:
            m = next((i for i, rc in enumerate(read_changes) if i not in used and self.same_shape(rc["formation"], tc["formation"]) and -210 <= rc["t"] - tc["t"] <= tolerance), None)  # a window centred up to half a window before the change already contains it
            if m is not None:
                used.add(m)
            hits.append(dict(at=tc["at"], formation=tc["formation"], found=m is not None, lag_s=round(read_changes[m]["t"] - tc["t"]) if m is not None else None))
        return dict(true_changes=len(truth), found=sum(h["found"] for h in hits), false_alarms=len(read_changes) - len(used), detail=hits)

    def window_agreement(self, team, timeline):
        """Share of the timeline's windows whose reading is the system the engine had the team set up in at the middle of that window."""
        tr = self.truth(team)
        sched = [(0., tr["starting"])] + [(c["t"], c["formation"]) for c in tr["formation_changes"]]
        ok = n = 0
        for w in timeline["by_window"]:
            tm = w["t"] + 210.
            truth_now = [f for t, f in sched if t <= tm][-1]
            if w["in_possession"]:
                n += 1
                ok += self.same_shape(w["in_possession"], truth_now)
        return round(ok / max(n, 1), 3)

    # ---------------------------------------------------------------- pressing
    def pressing(self, team, window=900.):
        """By window: PPDA (opponent passes in their own 60 % per defensive action there; lower = harder press), the average height of the
        team's defensive actions (metres from its own goal), balls won by third, and counter-press wins (won back within 5 s of losing it)."""
        out, lost_at = [], None
        acts = ("tackle", "interception", "foul", "ball_recovery", "block")
        ppda_acts = ("tackle", "interception", "foul")  # the standard PPDA actions, as analytics.stats counts them
        rows = {}
        for e in self.events:
            w = int(e["t"] // window)
            r = rows.setdefault(w, dict(opp_passes=0, def_actions=0, heights=[], won=[0, 0, 0], counter=0))
            if e["type"] == "possession_change":
                if e.get("team") == 1 - team:
                    lost_at = e["t"]
                elif e.get("team") == team:
                    xy = self._ev_xy(e)
                    if xy is not None:
                        r["won"][min(int(xy[0] / 35), 2)] += 1
                    if lost_at is not None and e["t"] - lost_at < 5:
                        r["counter"] += 1
            xy = self._ev_xy(e)
            if xy is None:
                continue
            if e["type"] == "pass" and e["team"] == 1 - team and xy[0] < .6 * L:
                r["opp_passes"] += 1
            if e["type"] in acts and e["team"] == team:
                r["heights"].append(float(xy[0]))
            if e["type"] in ppda_acts and e["team"] == team and xy[0] > .4 * L:
                r["def_actions"] += 1
        for w in sorted(rows):
            r = rows[w]
            out.append(dict(window=[self._clock(w * window), self._clock((w + 1) * window)], ppda=round(r["opp_passes"] / r["def_actions"], 1) if r["def_actions"] else None,
                            action_height_m=round(float(np.mean(r["heights"])), 1) if r["heights"] else None,
                            balls_won_own_mid_final_third=r["won"], counterpress_wins=r["counter"]))
        passes, actions = sum(r["opp_passes"] for r in rows.values()), sum(r["def_actions"] for r in rows.values())
        return dict(team=self.team_names[team], match_ppda=round(passes / actions, 1) if actions else None, by_window=out,
                    note="PPDA: lower means a more intense press; match_ppda is the whole match (not an average of the windows)")

    # ---------------------------------------------------------------- momentum
    def momentum(self, window=300.):
        """By window for both teams: xG, shots, field tilt (share of the two teams' touches in the final third that are this team's),
        and the spells where one side dominated (field tilt 0.65 or more for two windows in a row)."""
        n = int(self.meta["duration"] // window) + 1
        xg, shots, ft = np.zeros((2, n)), np.zeros((2, n)), np.zeros((2, n))
        for e in self.events:
            w, tm = int(e["t"] // window), e.get("team")
            if tm not in (0, 1):
                continue
            if e["type"] == "shot":
                xg[tm, w] += e["xg"]; shots[tm, w] += 1
            xy = self._ev_xy(e)
            if xy is not None and e["type"] in ("pass", "carry", "ball_receipt", "shot", "cross", "take_on") and xy[0] > 2 * L / 3:
                ft[tm, w] += 1
        tilt = ft[0] / np.maximum(ft.sum(0), 1)
        spells, k = [], 0
        while k < n - 1:
            for side, cond in ((0, tilt >= .65), (1, tilt <= .35)):
                if cond[k] and cond[k + 1]:
                    j = k
                    while j + 1 < n and cond[j + 1]:
                        j += 1
                    spells.append(dict(team=self.team_names[side], start=self._clock(k * window), end=self._clock((j + 1) * window), start_t=k * window, end_t=(j + 1) * window,
                                       xg=round(float(xg[side, k:j + 1].sum()), 2), opponent_xg=round(float(xg[1 - side, k:j + 1].sum()), 2)))
                    k = j
                    break
            k += 1
        return dict(teams=self.team_names, window_s=window,
                    series=[dict(at=self._clock(w * window), xg=[round(float(xg[0, w]), 2), round(float(xg[1, w]), 2)], shots=[int(shots[0, w]), int(shots[1, w])],
                                 field_tilt_home=round(float(tilt[w]), 2)) for w in range(n)],
                    cumulative_xg=[round(float(xg[0].sum()), 2), round(float(xg[1].sum()), 2)], dominant_spells=spells)

    # ---------------------------------------------------------------- win probability
    def win_probability(self, step=60.):
        """Home win / draw / away win chances minute by minute. Each side's scoring rate for the rest of the match blends the league rate
        (1.24 goals a game) with its own xG so far, the match counting for more as it goes on; goals still to come are Poisson. The turning
        points are the biggest swings. A simple, explainable model, not a betting price."""
        from math import exp, factorial
        dur, goals, shots = self.meta["duration"], [], []
        for e in self.events:
            if e["type"] in ("goal", "own_goal"):
                goals.append((e["t"], e["home"], e["away"]))
            elif e["type"] == "shot" and e.get("team") in (0, 1):
                shots.append((e["t"], e["team"], e["xg"]))
        pois = lambda lam, k: exp(-lam) * lam ** k / factorial(k)
        out = []
        grid = sorted(set(np.arange(0., dur + 1e-6, step).tolist()) | {round(g[0], 1) for g in goals})  # every minute, and the moment each goal goes in
        for t in grid:
            sc = next(((h, a) for tt, h, a in reversed(goals) if tt <= t + .05), (0, 0))
            played = max(t / 60., 1.)
            left = max(dur - t, 0.) / 60.
            xg = [sum(x for tt, tm, x in shots if tt <= t and tm == s_) for s_ in (0, 1)]
            rate = [(1.24 * 30 + xg[s_] * 90) / (30 + played) / 90 for s_ in (0, 1)]  # per minute
            ph = [pois(rate[0] * left, k) for k in range(10)]
            pa = [pois(rate[1] * left, k) for k in range(10)]
            hw = dr = aw = 0.
            for i, x in enumerate(ph):
                for j, y in enumerate(pa):
                    d = sc[0] + i - sc[1] - j
                    hw, dr, aw = hw + x * y * (d > 0), dr + x * y * (d == 0), aw + x * y * (d < 0)
            tot = hw + dr + aw
            out.append(dict(at=self._clock(t), t=round(t, 1), home=round(hw / tot, 3), draw=round(dr / tot, 3), away=round(aw / tot, 3)))
        swings = sorted(((abs(b["home"] - a["home"]) + abs(b["away"] - a["away"]), b) for a, b in zip(out, out[1:])), key=lambda x: -x[0])[:4]
        swings = [x for x in swings if x[0] / 2 >= .1]  # a turning point moves the chances by 10 points or more
        return dict(teams=self.team_names, series=out, turning_points=[dict(at=b["at"], t=b["t"], swing=round(sw / 2, 2)) for sw, b in sorted(swings, key=lambda x: x[1]["t"])],
                    note="simple model: league scoring rate blended with each side's xG so far; Poisson for the goals still to come")

    # ---------------------------------------------------------------- passing network
    def passing_network(self, team, t0=0., t1=None, min_links=3):
        """Completed pass links between players, each player's average pass position, the main connectors, which third attacks start in
        (where the team won the ball before a possession that reached the final third) and the side entries into the final third favour."""
        t1 = self.meta["duration"] if t1 is None else t1
        links, pos, deg = {}, {}, {}
        side = [0, 0, 0]
        for e in self.events:
            if not (t0 <= e["t"] <= t1) or e.get("team") != team or e["type"] not in ("pass", "cross"):
                continue
            xy = self._ev_xy(e)
            pos.setdefault(e["player"], []).append(xy)
            rcv = e.get("receiver")
            if e.get("outcome") == "complete" and rcv:
                k = (e["player"], rcv)
                links[k] = links.get(k, 0) + 1
                deg[e["player"]] = deg.get(e["player"], 0) + 1
                deg[rcv] = deg.get(rcv, 0) + 1
                end = self._team_xy(team, [e["end_x"], e["end_y"]]) if e.get("end_x") is not None else None
                if end is not None and xy[0] < 2 * L / 3 <= end[0]:
                    side[0 if end[1] < W / 3 else 1 if end[1] < 2 * W / 3 else 2] += 1
        top = sorted(deg.items(), key=lambda kv: -kv[1])[:3]
        return dict(team=self.team_names[team], window=[self._clock(t0), self._clock(t1)],
                    links=[dict(**{"from": self.names[a], "to": self.names[b]}, passes=n) for (a, b), n in sorted(links.items(), key=lambda kv: -kv[1]) if n >= min_links],
                    avg_positions={self.names[p]: [round(float(np.mean([v[0] for v in xs])), 1), round(float(np.mean([v[1] for v in xs])), 1)] for p, xs in pos.items()},
                    connectors=[dict(player=self.names[p], completed_links=n) for p, n in top],
                    final_third_entries_left_centre_right=side)

    # ---------------------------------------------------------------- sequence
    def sequence(self, event_id, max_s=30.):
        """The possession that led to an event: everything the team did from when it last won the ball (at most max_s earlier), with the
        engine's recorded reasons where it made a choice."""
        e0 = self.by_id[event_id]
        tm, idx = e0.get("team"), next(i for i, e in enumerate(self.events) if e["id"] == event_id)
        start = idx
        while start > 0:
            e = self.events[start - 1]
            if e0["t"] - e["t"] > max_s or (e["type"] == "possession_change" and e.get("team") == tm):
                break
            start -= 1
        steps = []
        for e in self.events[start:idx + 1]:
            if e["type"] in ("pressure", "possession_change", "ball_receipt"):
                continue
            s = dict(clock=e["clock"], t=e["t"], type=e["type"], team=self.team_names[e["team"]] if e.get("team") in (0, 1) else None,
                     player=self.names.get(e.get("player")), outcome=e.get("outcome"))
            if e.get("xg") is not None:
                s["xg"] = e["xg"]
            w = e.get("why")
            if w:
                s["why"] = dict(chosen=w.get("chosen"), pressure=w.get("pressure"), nearest_opponent_m=w.get("nearest_opponent_m"),
                                alternatives=[dict(kind=o["kind"], to=self.names.get(o.get("to")), p_success=o.get("p_success"), xg=o.get("xg")) for o in w.get("options", [])[:3]])
            steps.append(s)
        return dict(moment=dict(clock=e0["clock"], type=e0["type"], player=self.names.get(e0.get("player"))), steps=steps)

    def _slot(self, pid, t):
        return next((k for k in range(22) if self._occupant(k, t) == pid), None)

    def _at(self, t):
        """Ball and the 22 slots (world frame) at time t."""
        f = self.F[min(int(round(t / self.dt)), len(self.F) - 1)]
        return np.array(f[1:3]), np.array(f[5:49]).reshape(22, 2)

    @staticmethod
    def _lane_open(a, b, opp, room=2.):
        """No opponent within `room` metres of the straight path from a to b."""
        d = b - a
        u = np.clip(((opp - a) @ d) / max(d @ d, 1e-6), 0, 1)
        return bool((np.linalg.norm(opp - (a + u[:, None] * d), axis=1) > room).all())

    def breakdown(self, event_id):
        """The move behind a shot as a few stops to pause the replay on and draw over, each with a plain caption built from the data:
        where the ball was won; both teams' lines (the attackers' shape with the ball, the defenders' without it), the defenders' block,
        their last line and the space between their back two lines; the last pass with every open lane from the passer, the triangles
        those lanes make, the runs about to be made and the numbers around the ball; the shot with the defenders between it and the goal.
        Players are tracking slots, so the drawing follows the frame shown."""
        e0 = self.by_id[event_id]
        tm, idx = e0["team"], next(i for i, e in enumerate(self.events) if e["id"] == event_id)
        start = idx
        while start > 0:
            e = self.events[start - 1]
            if e0["t"] - e["t"] > 25 or (e["type"] == "possession_change" and e.get("team") == tm) or e["type"] in ("kickoff", "half_start"):
                break
            start -= 1
        seq = self.events[start:idx]
        name = lambda pid: self.names.get(pid, "")
        mine, theirs = range(11 * tm, 11 * tm + 11), range(11 * (1 - tm), 11 * (1 - tm) + 11)
        fwd = 1 if tm == 0 else -1
        goal = np.array([L if tm == 0 else 0., W / 2])
        on = lambda xy, ks: [k for k in ks if xy[k][1] > -5]
        stops = []
        # where the ball was won
        rg = next((e for e in self.events[max(start - 1, 0):start + 4] if e.get("team") == tm and e["type"] in ("interception", "tackle", "ball_recovery", "block") and e.get("player")), None)
        if rg and e0["t"] - rg["t"] <= 20:
            d = float(np.hypot(*(goal - [rg["x"], rg["y"]])))
            k = self._slot(rg["player"], rg["t"])
            stops.append(dict(t=rg["t"], kind="regain", caption=f"{name(rg['player'])} wins the ball back {d:.0f} m from goal.", ring=[k], names=[k]))
        # both shapes early in the move, the defenders' block, their last line, the space between their back two lines
        passes = [e for e in seq if e["type"] in ("pass", "cross") and e.get("player")]
        ts = next((e["t"] for e in passes if e0["t"] - e["t"] <= 14), e0["t"] - 5)
        dead_ball = e0.get("kind") in ("free_kick_shot", "penalty")  # a shot from a set piece: no open-play shapes or passing lanes to draw
        if not dead_ball and (not stops or ts - stops[0]["t"] > 2):
            a, d = self.lines_now(tm, ts, True, back=4.), self.lines_now(1 - tm, ts, False, back=4.)  # the shape drawn is the shape read
            _, xy = self._at(ts)
            dk = [k for k in on(xy, theirs) if self.role_now[self._occupant(k, ts)] != "GK"]
            P = xy[dk]
            hull = [dk[i] for i in _hull(P)]
            last = float(P[:, 0].max() if tm == 0 else P[:, 0].min())  # the defenders' deepest outfield player
            back, mid = (np.mean([xy[k][0] for k in row]) for row in d["lines"][:2])
            gap = abs(mid - back)
            depth, width = float(np.ptp(P[:, 0])), float(np.ptp(P[:, 1]))
            whose = self.team_names[1 - tm] + ("'" if self.team_names[1 - tm].endswith("s") else "'s")
            stops.append(dict(t=ts, kind="shape",
                              # no formation names: mid-move the players are not in a system (a 4-second read flickers between 3-5-2, 2-4-4, 3-3-3),
                              # so the caption gives what can be measured and the drawing shows where the lines are
                              caption=f"{self.team_names[tm]} attack; {whose} block is {width:.0f} m wide and {depth:.0f} m deep"
                                      + (f", with {gap:.0f} m between its back line and the line in front." if gap >= 5 else "."),
                              lines=[dict(team=tm, label=f"{self.team_names[tm]} with the ball", rows=a["lines"]),
                                     dict(team=1 - tm, label=f"{self.team_names[1 - tm]} without the ball", rows=d["lines"])],
                              hull=[1 - tm, hull], last_line=[1 - tm, round(last, 1)],
                              between=None if gap < 5 else [round(float(back), 1), round(float(mid), 1), round(float(P[:, 1].min()), 1), round(float(P[:, 1].max()), 1)]))
        # the last pass: every open lane from the passer, the triangles they make, the runs being made, the numbers around the ball
        lp = None if dead_ball else next((e for e in reversed(passes) if e0["t"] - e["t"] <= 8 and e["t"] >= ts), None)
        if lp:
            ball, xy = self._at(lp["t"])
            _, xy2 = self._at(lp["t"] + 2.5)
            p, rcv = self._slot(lp["player"], lp["t"]), self._slot(lp.get("receiver"), lp["t"])
            opp = xy[on(xy, theirs)]
            opts = [k for k in on(xy, mine) if k != p and 5 < np.hypot(*(xy[k] - xy[p])) < 40 and self._lane_open(xy[p], xy[k], opp)]
            n = len(opts)
            if rcv is not None and rcv not in opts:
                opts.append(rcv)
            tris = sorted(([a, b] for i, a in enumerate(opts) for b in opts[i + 1:] if np.hypot(*(xy[a] - xy[b])) < 30 and self._lane_open(xy[a], xy[b], opp)),
                          key=lambda ab: (rcv not in ab, np.hypot(*(xy[ab[0]] - xy[ab[1]]))))[:2]
            runs = sorted(([k, *map(lambda v: round(float(v), 1), (*xy[k], *xy2[k]))] for k in on(xy, mine) if k != p and (xy2[k][0] - xy[k][0]) * fwd >= 10),
                          key=lambda r: -abs(r[3] - r[1]))[:3]  # 10 m forward in 2.5 s: a run, not a jog
            near = lambda ks: sum(np.hypot(*(xy[k] - ball)) <= 15 for k in on(xy, ks))
            us, them = near(mine), near(theirs)
            open_ = rcv is None or self._lane_open(xy[p], xy[rcv], opp)
            what = "crosses for" if lp["type"] == "cross" else "goes over the top to" if lp.get("lofted") else "picks out" if open_ else "forces it through to"
            stops.append(dict(t=lp["t"], kind="options",
                              caption=f"{name(lp['player'])} has {n} open passing lane{'s' * (n != 1)} and {what} {name(lp.get('receiver')) or 'a team-mate'}. "
                                      f"{us} v {them} within 15 m of the ball" + (f"; {len(runs)} forward run{'s' * (len(runs) != 1)} under way." if runs else "."),
                              ring=[p], names=sorted({p, *opts, *(r[0] for r in runs)}), opts=[[p, k, int(k == rcv)] for k in opts], tris=[[p, a, b] for a, b in tris],
                              runs=runs, zone=[round(float(ball[0]), 1), round(float(ball[1]), 1), 15, int(us), int(them)]))
        # the shot
        _, xy = self._at(e0["t"])
        s = self._slot(e0["player"], e0["t"])
        if s is not None:
            A, B, C = xy[s], np.array([goal[0], W / 2 - 3.66]), np.array([goal[0], W / 2 + 3.66])

            def inside(q):  # inside the triangle shooter - post - post
                v0, v1, v2 = C - A, B - A, q - A
                d00, d01, d02, d11, d12 = v0 @ v0, v0 @ v1, v0 @ v2, v1 @ v1, v1 @ v2
                den = d00 * d11 - d01 * d01
                u, v = (d11 * d02 - d01 * d12) / den, (d00 * d12 - d01 * d02) / den
                return u >= 0 and v >= 0 and u + v <= 1
            gk = [k for k in theirs if self.role_now[self._occupant(k, e0["t"])] == "GK"]
            block = [k for k in on(xy, theirs) if k not in gk and inside(xy[k])]
            d = float(np.hypot(*(goal - xy[s])))
            stops.append(dict(t=e0["t"], kind="shot",
                              caption=f"{name(e0['player'])} " + {"free_kick_shot": "takes the free kick", "penalty": "takes the penalty", "header": "heads"}.get(e0.get("kind"), "shoots")
                                      + f" from {d:.0f} m with {len(block) or 'no'} defender{'s' * (len(block) != 1)} between him and the goal (xG {e0.get('xg', 0):.2f}).",
                              ring=[s] + block, names=[s] + block, cone=[s, round(float(goal[0]), 1)], keeper=gk[:1]))
        return dict(id=event_id, t=e0["t"], clock=e0["clock"], team=tm, outcome=e0.get("outcome"),
                    title=f"{e0['clock']} {'goal' if e0.get('outcome') == 'goal' else 'chance'} · {name(e0['player'])}",
                    start=round(max(0., stops[0]["t"] - 3), 1), end=round(e0["t"] + 3, 1), stops=stops)

    # ---------------------------------------------------------------- player
    def player_profile(self, pid):
        """Event stats and rating terms, a 6 x 4 heat map (share of time in each zone, own goal at the left), distance, sprints and top
        speed from tracking, and per-90 numbers against the average for his role over many simulated matches."""
        p = self.players[pid]
        acc = StatsAccumulator(self.meta)
        for e in self.events:
            acc.update(e)
        st = acc.players().get(pid, {})
        mins = st.get("minutes", 0.)
        team = p["team"]
        res = int(np.sign(acc.t[team]["goals"] - acc.t[1 - team]["goals"]))
        terms = {k: round(v, 2) for k, v in StatsAccumulator.rating_terms(st, p["role"], mins, res).items()} if mins >= 1 else {}
        # tracking, slot by slot while he occupied it
        xy_all = []
        for k in range(22):
            on = [self._occupant(k, i * self.dt) == pid for i in range(0, len(self.F), 25)]
            if not any(on):
                continue
            idx = np.repeat(np.array(on), 25)[:len(self.F)]
            xy = self.F[idx][:, [5 + 2 * k, 6 + 2 * k]]
            xy_all.append(xy[xy[:, 1] > -5])
        phys = {}
        heat = None
        if xy_all:
            xy = np.concatenate(xy_all)
            sp = np.linalg.norm(np.diff(xy, axis=0), axis=1) / self.dt
            sp = sp[sp < 12]  # jumps at substitutions and restarts
            fast = np.concatenate([[False], sp > SPRINT, [False]]).astype(int)
            starts, ends = np.nonzero(np.diff(fast) == 1)[0], np.nonzero(np.diff(fast) == -1)[0]
            runs = (ends - starts) * self.dt
            phys = dict(distance_km=round(float(sp.sum() * self.dt / 1000), 2), top_speed_kmh=round(float(np.percentile(sp, 99.9) * 3.6), 1) if len(sp) else None,
                        sprints=int((runs >= 1.).sum()), sprint_distance_m=round(float(sp[sp > SPRINT].sum() * self.dt)))  # a sprint: at least a second above 25 km/h
            txy = self._team_xy(team, xy)
            h, _, _ = np.histogram2d(txy[:, 0], txy[:, 1], bins=[6, 4], range=[[0, L], [0, W]])
            heat = (h / max(h.sum(), 1)).round(3).tolist()
        keys = ("passes", "passes_completed", "progressive_passes", "key_passes", "shots", "xg", "take_ons", "take_ons_won", "tackles", "tackles_won",
                "interceptions", "aerial_won", "aerial_lost", "dribbled_past", "dispossessed", "pressures", "recoveries", "fouls", "saves")
        per90 = {k: round(st.get(k, 0) * 90 / mins, 2) for k in keys} if mins >= 20 else {}
        norms = json.loads(ROLE_NORMS.read_text()).get(p["role"], {}) if ROLE_NORMS.exists() else {}
        vs = {k: dict(him=v, role_avg=norms[k]) for k, v in per90.items() if k in norms}
        return dict(player=p["name"], id=pid, number=p["number"], team=self.team_names[team], role=p["role"], traits=p.get("traits", []), star=p.get("star", False),
                    preferred_foot=p.get("foot"), weak_foot_1_to_20=p.get("weak_foot"),
                    minutes=mins, rating=st.get("rating"), rating_terms=terms, rating_note="6.0 plus the terms; above 8 and below 4 the scale eases off, so the terms can add to more than the rating shows",
                    stats={k: st.get(k) for k in keys + ("goals", "assists", "goals_conceded", "pass_accuracy", "avg_pass_length_m", "avg_pass_difficulty", "hard_passes",
                                                         "hard_passes_completed", "hard_pass_accuracy", "avg_shot_speed_kmh", "top_shot_speed_kmh")},
                    stats_note="pass difficulty is 1 minus the chance the pass would arrive, judged when he chose it (lane, pressure, distance); a hard pass is difficulty 0.5 or more",
                    physical=phys, heat_map_6x4=heat, per90_vs_role=vs)

    # ---------------------------------------------------------------- moments
    def fatigue(self, team, window=900., hi=5.5):
        """Who is tiring, from tracking: each player's high-intensity running (metres a minute above 19.8 km/h) by 15 minutes against his
        own first 30 minutes, and that drop against his team-mates' (a side protecting a lead runs less as a whole: that is the game, not
        tiredness). tiring = over the latest two windows he is down 25 % or more on his own start and 15 points more than the team; needs 45
        minutes on the pitch. Everything is read from positions only."""
        sl = slice(11 * team, 11 * team + 11)
        xy = self.F[:, 5:49].reshape(len(self.F), 22, 2)[:, sl]
        sp = nrm(np.diff(xy, axis=0), axis=2) / self.dt
        sp[(sp > 12) | (xy[1:, :, 1] < -5)] = 0.
        ts = self.F[1:, 0]
        rows = self._windows(window)
        per = {}  # player -> [(window, metres a minute, minutes)]
        for k in range(11):
            for a, b in rows:
                sel = (ts >= a) & (ts < b)
                occ = np.array([self._occupant(11 * team + k, t) for t in ts[sel][::25]])
                for pid in set(occ):
                    if self.players.get(pid, {}).get("role") == "GK":
                        continue
                    mask = np.nonzero(sel)[0][np.repeat(occ == pid, 25)[:sel.sum()]]
                    mins = len(mask) * self.dt / 60
                    if mins >= 5:
                        per.setdefault(pid, []).append((f"{self._clock(a)}-{self._clock(b - 1)}", round(float((sp[mask, k] * (sp[mask, k] > hi)).sum() * self.dt / mins), 1), round(mins, 1)))
        out = {}
        for pid, ws in per.items():
            first = [v for _, v, m in ws[:2]]
            base = float(np.mean(first)) if first else 0.
            on = sum(m for *_, m in ws)
            out[pid] = dict(name=self.names[pid], id=pid, role=self.players[pid]["role"], minutes=round(on, 1), hi_m_per_min_by_window={w: v for w, v, _ in ws},
                            start_hi_m_per_min=round(base, 1), latest_drop_pct=round(100 * (1 - np.mean([v for _, v, _m in ws[-2:]]) / base)) if base > 1 and len(ws) > 3 else None)  # now = the latest two windows: one is too noisy
        drops = [q["latest_drop_pct"] for q in out.values() if q["latest_drop_pct"] is not None]
        team_drop = float(np.median(drops)) if drops else None
        for q in out.values():
            d = q["latest_drop_pct"]
            q["vs_team_points"] = round(d - team_drop) if d is not None and team_drop is not None else None
            q["tiring"] = bool(d is not None and q["minutes"] >= 45 and d >= 25 and q["vs_team_points"] >= 15)
        return dict(team=self.team_names[team], team_drop_pct=round(team_drop) if team_drop is not None else None,
                    window_ends={f"{self._clock(a)}-{self._clock(b - 1)}": round(b, 1) for a, b in rows},
                    tiring=[q["name"] for q in sorted(out.values(), key=lambda q: -(q["latest_drop_pct"] or 0)) if q["tiring"]],
                    players={q["name"]: {k: v for k, v in q.items() if k != "name"} for q in out.values()},
                    note="high-intensity running (above 19.8 km/h) in metres a minute; drops against his own first 30 minutes and against the team's median drop")

    def score_fatigue(self, team):
        """The fatigue reader against the engine's own stamina (its log): how well each player's latest drop ranks the truly tired."""
        st = self.stamina
        if st is None:
            return None
        f = self.fatigue(team)
        pairs = []
        for nm, q in f["players"].items():
            if q["latest_drop_pct"] is None:
                continue
            k = next((k for k in range(11) if self._occupant(11 * team + k, self.meta["duration"] - 1) == q["id"]), None) if "id" in q else None
            if k is not None:
                pairs.append((q["latest_drop_pct"], 1. - st[-1][11 * team + k]))
        if len(pairs) < 4:
            return None
        a = np.array(pairs)
        return dict(players=len(a), drop_vs_true_fatigue_r=round(float(np.corrcoef(a[:, 0], a[:, 1])[0, 1]), 2))

    def milestones(self):
        """In-match milestones from the events, at the moment they are reached: a brace and a hat-trick, a goal off the bench, a goal and an
        assist, 50 / 75 / 100 passes completed, 5 tackles won, 5 saves, and a clean sheet kept to the final whistle.
        [{t, clock, player, kind: "milestone", text}], by time."""
        out, c, sub_on = [], {}, {e["on"] for e in self.events if e["type"] == "substitution"}
        add = lambda e, pid, text: out.append(dict(t=e["t"], clock=e.get("clock"), player=pid, kind="milestone", text=text))
        for e in self.events:
            ty, pid = e["type"], e.get("player")
            n = c.setdefault(pid, dict(goals=0, assists=0, passes=0, tackles=0, saves=0)) if pid else None
            if ty == "goal" and pid:
                n["goals"] += 1
                if n["goals"] in (2, 3):
                    add(e, pid, "brace" if n["goals"] == 2 else "hat-trick")
                if n["goals"] == 1 and pid in sub_on:
                    add(e, pid, "scores off the bench")
                if n["goals"] == 1 and n["assists"]:
                    add(e, pid, "a goal and an assist")
            elif ty in ("pass", "cross") and pid and e.get("outcome") == "complete":
                n["passes"] += 1
                if n["passes"] in (50, 75, 100):
                    add(e, pid, f"{n['passes']} passes completed")
                if e.get("assist"):
                    n["assists"] += 1
                    if n["assists"] == 1 and n["goals"]:
                        add(e, pid, "a goal and an assist")
            elif ty == "tackle" and pid and e.get("outcome") == "won":
                n["tackles"] += 1
                if n["tackles"] == 5:
                    add(e, pid, "5 tackles won")
            elif ty == "save" and pid:
                n["saves"] += 1
                if n["saves"] == 5:
                    add(e, pid, "5 saves")
        end = next((e for e in reversed(self.events) if e["type"] in ("full_time", "match_end", "half_end")), self.events[-1] if self.events else None)
        if end:
            scored = [sum(1 for e in self.events if e["type"] in ("goal", "own_goal") and e.get("team") == t) for t in (0, 1)]
            for t in (0, 1):
                gk = self._occupant(11 * t, end["t"])
                if scored[1 - t] == 0 and self.players.get(gk, {}).get("role") == "GK":
                    add(end, gk, "keeps a clean sheet")
        return sorted(out, key=lambda r: r["t"])

    def distance_by_minute(self):
        """Distance covered (km) by every player by the end of each 60 s of match time, from tracking: {player id: [km, ...]}."""
        bounds = [0.] + [c[0] for c in self.slot_changes] + [self.meta["duration"] + 1]
        step = int(round(60 / self.dt))
        nmin = int(np.ceil(len(self.F) / step))
        out = {}
        for k in range(22):
            for a, b in zip(bounds, bounds[1:]):
                i0, i1 = int(a / self.dt), min(int(b / self.dt), len(self.F))
                if i1 - i0 < 2:
                    continue
                xy = self.F[i0:i1, [5 + 2 * k, 6 + 2 * k]]
                sp = nrm(np.diff(xy, axis=0), axis=1)
                sp[(sp > 12 * self.dt) | (xy[1:, 1] < -5)] = 0.  # jumps at restarts and substitutions, a man sent off
                per = np.zeros(nmin)
                np.add.at(per, (np.arange(i0 + 1, i1) // step).clip(0, nmin - 1), sp)
                pid = self._occupant(k, (a + b) / 2)
                out[pid] = out.get(pid, np.zeros(nmin)) + per
        return {pid: np.round(np.cumsum(v) / 1000, 2).tolist() for pid, v in out.items()}

    def player_tags(self, kmh=30., every_km=5.):
        """Moments to tag a player on screen: a sprint that peaks at or above `kmh` and is his fastest of the match so far (speed averaged
        over a second, so one frame of tracking noise is not a sprint; a fixed bar alone tags a quick side hundreds of times), and each
        `every_km` of distance covered. [{t, player, kind: speed|distance, value}], by time."""
        idx = {}  # player -> frame indices and slot, segment by segment as he occupied each slot
        bounds = [0.] + [c[0] for c in self.slot_changes] + [self.meta["duration"] + 1]
        for k in range(22):
            for a, b in zip(bounds, bounds[1:]):
                i0, i1 = int(a / self.dt), min(int(b / self.dt), len(self.F))
                if i1 - i0 > 5:
                    idx.setdefault(self._occupant(k, (a + b) / 2), []).append((i0, i1, k))
        out, need = [], kmh / 3.6
        for pid, segs in idx.items():
            dist, best = 0., need
            for i0, i1, k in sorted(segs):
                xy = self.F[i0:i1, [5 + 2 * k, 6 + 2 * k]]
                sp = np.linalg.norm(np.diff(xy, axis=0), axis=1) / self.dt
                sp[(sp > 12) | (xy[1:, 1] < -5)] = 0.  # jumps at restarts and substitutions, and a man sent off
                cum = np.cumsum(sp * self.dt) + dist
                for m in range(int(dist // (every_km * 1000)) + 1, int(cum[-1] // (every_km * 1000)) + 1 if len(cum) else 0):
                    j = int(np.searchsorted(cum, m * every_km * 1000))
                    out.append(dict(t=round(float((i0 + j + 1) * self.dt), 1), player=pid, kind="distance", value=m * every_km))
                dist = float(cum[-1]) if len(cum) else dist
                sm = np.convolve(sp, np.ones(5) / 5, "same")
                fast = np.concatenate([[0], (sm > SPRINT).astype(int), [0]])
                for a, b in zip(np.nonzero(np.diff(fast) == 1)[0], np.nonzero(np.diff(fast) == -1)[0]):
                    j = a + int(np.argmax(sm[a:b]))
                    if sm[j] >= best:
                        best = sm[j] + .5 / 3.6  # the next tag needs half a km/h more
                        out.append(dict(t=round(float((i0 + j + 1) * self.dt), 1), player=pid, kind="speed", value=round(float(sm[j] * 3.6), 1)))
        return sorted(out, key=lambda r: r["t"])

    # ---------------------------------------------------------------- control vs chaos
    def rhythm(self, window=300.):
        """Control vs chaos and the rhythm of the game, by window. A possession is a run of in-play time with one side on the ball
        (tracking); it is settled when the side strings 4 completed passes or keeps it 15 s, broken when it ends inside 8 s with at most
        one pass (second balls, loose touches, a turnover straight back). Per window: the share of in-play time in each, who had the settled
        ball, turnovers and passes per minute of play, stoppages, and a state: chaos (under 45 % settled, or a fifth broken with 5
        turnovers a minute), stop-start (the ball in play under 40 % of the window), control by one side (60 % settled and 65 % of it
        theirs), else even. Spells: consecutive windows in one state."""
        live = np.ones(len(self.F), bool)
        for a, b, _ in self.meta.get("dead_log", []):
            live[int(a / self.dt):int(b / self.dt) + 1] = False
        poss, n = self.F[:, 4].astype(int), len(self.F)
        done = [e for e in self.events if e["type"] in ("pass", "cross") and e.get("outcome") == "complete"]
        pt, pm = np.array([e["t"] for e in done]), np.array([e["team"] for e in done])
        segs, i = [], 0
        while i < n:
            if not live[i] or poss[i] < 0:
                i += 1
                continue
            j = i
            while j < n and live[j] and poss[j] == poss[i]:
                j += 1
            t0, t1, tm = i * self.dt, j * self.dt, int(poss[i])
            k = int(((pt >= t0) & (pt < t1) & (pm == tm)).sum()) if len(pt) else 0
            kind = "settled" if k >= 4 or t1 - t0 >= 15 else "broken" if k <= 1 and t1 - t0 < 8 else "building"
            segs.append((t0, t1, tm, kind, j < n and live[j]))  # the last flag: lost in play (a turnover), not to a stoppage
            i = j
        stops = [a for a, b, k in self.meta.get("dead_log", []) if k != "kickoff" or a > 0]
        rows = []
        for w0, w1 in self._windows(window):
            part = [(max(a, w0), min(b, w1), tm, k, lost) for a, b, tm, k, lost in segs if b > w0 and a < w1]
            play = sum(b - a for a, b, *_ in part)
            share = {k: sum(b - a for a, b, _, kk, _l in part if kk == k) / play if play else 0. for k in ("settled", "broken")}
            mine = [sum(b - a for a, b, tm, kk, _l in part if kk == "settled" and tm == t) for t in (0, 1)]
            turn = sum(1 for a, b, tm, k, lost in part if lost and w0 <= b < w1) / (play / 60) if play else 0.
            tempo = int(((pt >= w0) & (pt < w1)).sum()) / (play / 60) if play else 0.
            top = int(np.argmax(mine)) if sum(mine) else None
            state = ("stop-start" if play < .4 * (w1 - w0) else
                     "chaos" if share["settled"] < .45 or (share["broken"] >= .2 and turn >= 5) else
                     f"control: {self.team_names[top]}" if share["settled"] >= .6 and top is not None and mine[top] >= .65 * sum(mine) else "even")
            rows.append(dict(window=f"{self._clock(w0)}-{self._clock(w1 - 1)}", t0=w0, t1=w1, ball_in_play_pct=round(100 * play / max(w1 - w0, 1)),
                             settled_pct=round(100 * share["settled"]), broken_pct=round(100 * share["broken"]),
                             settled_by_team_pct=[round(100 * x / sum(mine)) if sum(mine) else None for x in mine],
                             turnovers_per_min=round(turn, 1), passes_per_min=round(tempo, 1), stoppages=sum(1 for a in stops if w0 <= a < w1), state=state))
        spells, cur = [], None
        for r in rows:
            if cur and cur["state"] == r["state"]:
                cur.update(to=r["window"].split("-")[1], end_t=r["t1"], windows=cur["windows"] + 1)
            else:
                cur = dict(state=r["state"], start=r["window"].split("-")[0], to=r["window"].split("-")[1], start_t=r["t0"], end_t=r["t1"], windows=1)
                spells.append(cur)
        play = sum(b - a for a, b, *_ in segs) or 1
        whole = dict(settled_pct=round(100 * sum(b - a for a, b, _, k, _l in segs if k == "settled") / play),
                     broken_pct=round(100 * sum(b - a for a, b, _, k, _l in segs if k == "broken") / play),
                     settled_by_team_pct=[round(100 * sum(b - a for a, b, tm, k, _l in segs if k == "settled" and tm == t) /
                                                max(sum(b - a for a, b, _, k, _l in segs if k == "settled"), 1)) for t in (0, 1)])
        return dict(teams=self.team_names, window_s=window, match=whole, by_window=rows, spells=[s_ for s_ in spells if s_["windows"] >= 2 and s_["state"] != "even"],
                    note="settled = a possession of 4+ completed passes or 15+ s; broken = over inside 8 s with at most one pass; shares are of the "
                         "time the ball was in play; states: chaos, stop-start, control: <team>, even")

    # ---------------------------------------------------------------- duties out of possession
    def _duty_frames(self, team, every=5):
        """Each outfield man's duty, read from tracking alone (as an analyst would read real tracking data), at 1 Hz while the opponents
        have the ball on the pitch: 2 closing the ball down (within DUTY_PRESS_R of the carrier and running at him, or on top of him),
        1 tracking a man (paired one-to-one with the nearest attacker within DUTY_MARK_R, goal-side counted closer), 0 holding his
        space in the block. Only men ahead of the ball are tracked: one behind it offers only a ball back. Returns the frame indices,
        the duties (n, 11) and the slot (0-10 of the opponents) of the man tracked."""
        if (team, every) in self._duty_cache:
            return self._duty_cache[(team, every)]
        XY = self.F[:, 5:49].reshape(len(self.F), 22, 2)
        poss, car = self.F[:, 4].astype(int), self.F[:, 3].astype(int)
        me, op0 = slice(11 * team, 11 * team + 11), 11 * (1 - team)
        live = np.ones(len(self.F), bool)
        for a, b, _ in self.meta.get("dead_log", []):
            live[int(a / self.dt):int(b / self.dt) + 1] = False
        idx = np.nonzero((poss == 1 - team) & live)[0][::every]  # their ball, in play (passes in the air included)
        nxt, cur = np.empty(len(car), int), -1  # with the ball in the air, the man who controls it next (a reading after the fact, a few seconds ahead)
        for i in range(len(car) - 1, -1, -1):
            cur = car[i] if car[i] >= 0 else cur
            nxt[i] = cur
        goal = np.array([0., W / 2]) if team == 0 else np.array([L, W / 2])
        duty, tgt = np.zeros((len(idx), 11), int), -np.ones((len(idx), 11), int)
        for n, i in enumerate(idx):
            P, O = XY[i, me], XY[i, op0:op0 + 11]
            Vp = (XY[min(i + 1, len(XY) - 1), me] - XY[max(i - 1, 0), me]) / (2 * self.dt)
            who = car[i] if car[i] >= 0 else nxt[i]
            on_ball = op0 <= who < op0 + 11  # the ball is theirs, at a man's feet or on its way to him
            c = XY[i, who] if on_ball else self.F[i, 1:3]
            d = nrm(P - c, axis=1)
            app = ((c - P) * Vp).sum(1) / np.maximum(d, .1)  # how fast he runs at the carrier
            dd = np.where(P[:, 1] < -5, 99., d); dd[0] = 99.
            first2 = np.zeros(11, bool); first2[np.argsort(dd)[:2]] = True  # only the nearest two can be engaging the ball; a third man running is covering
            press = (d <= DUTY_PRESS_R) & ((app >= DUTY_APPROACH) | (d <= 2.)) & on_ball & first2  # closing down the man on the ball, or the man it is going to
            press[0] = False
            duty[n, press] = 2
            D = nrm(P[:, None] - O[None], axis=2)
            D = np.where(nrm(P - goal, axis=1)[:, None] < nrm(O - goal, axis=1)[None] + 1., D, D + 3.)  # a man on the wrong side of his attacker is not marking him
            D[:, nrm(O - goal, axis=1) > nrm(c - goal) + 2.] = 99.  # behind the ball
            if on_ball:
                D[:, who - op0] = 99.
            D[:, 0] = D[0, :] = 99.
            D[press, :] = 99.
            D[P[:, 1] < -5, :] = D[:, O[:, 1] < -5] = 99.  # sent off
            for a, b in zip(*linear_sum_assignment(D)):
                if D[a, b] <= DUTY_MARK_R:
                    duty[n, a], tgt[n, a] = 1, b
        self._duty_cache[(team, every)] = idx, duty, tgt
        return idx, duty, tgt

    @staticmethod
    def _area(x, y):
        """A place in the team's own frame, in words: depth and channel (left is the left as the team attacks)."""
        depth = "deep" if x < 30 else "in the middle third" if x < 65 else "high"
        lane = ("on the left", "in the left half-space", "centrally", "in the right half-space", "on the right")[int(np.clip(y / (W / 5), 0, 4))]
        return f"{depth}, {lane}"

    def duties(self, team, window=900.):
        """Out of possession, per player and per window: the share of the time he spends closing the ball down and tracking a man, the
        man he tracks most, and where he stands when he holds his space; then what changed from one window to the next. Read from
        tracking only. Shares are of the time the opponents had the ball on the pitch."""
        idx, duty, tgt = self._duty_frames(team)
        ts = idx * self.dt
        XY = self._team_xy(team, self.F[idx, 5 + 22 * team:5 + 22 * team + 22].reshape(len(idx), 11, 2))
        rows = []
        for w0, w1 in self._windows(window):
            sel = (ts >= w0) & (ts < w1)
            row = dict(window=f"{self._clock(w0)}-{self._clock(w1 - 1)}", t0=w0, t1=w1, players={})
            for k in range(1, 11):
                occ = np.array([self._occupant(11 * team + k, t) for t in ts[sel]])
                for pid in set(occ):
                    on = np.nonzero(sel)[0][occ == pid]
                    if len(on) < 60:  # under a minute of defending: too little to read
                        continue
                    dk, tk = duty[on, k], tgt[on, k]
                    marked = tk[dk == 1]
                    top = np.bincount(marked, minlength=11).argmax() if len(marked) else -1
                    zx = XY[on, k][dk == 0]
                    zx = zx[zx[:, 1] > -5] if len(zx) else zx
                    rows_p = dict(press=round(float((dk == 2).mean()), 3), mark=round(float((dk == 1).mean()), 3),
                                  tracks=self.names.get(self._occupant(11 * (1 - team) + int(top), float(ts[on].mean()))) if top >= 0 and (marked == top).mean() >= .35 else None,
                                  tracks_id=self._occupant(11 * (1 - team) + int(top), float(ts[on].mean())) if top >= 0 and (marked == top).mean() >= .35 else None,
                                  tracks_share=round(float((marked == top).mean()), 2) if top >= 0 else None)
                    px_ = XY[on, k, 0][dk == 2]
                    if len(px_) >= 10:  # where he engages: metres from his own goal, and the share of it in their half
                        rows_p.update(press_height_m=round(float(px_.mean()), 1), press_in_their_half=round(float((px_ > L / 2).mean()), 2))
                    if len(zx):
                        cx, cy = zx.mean(0)
                        rows_p.update(holds=self._area(cx, cy), holds_xy=[round(float(cx), 1), round(float(cy), 1)])
                    rows_p["minutes_defending"] = round(len(on) * 5 * self.dt / 60, 1)  # samples are 1 s apart
                    row["players"][pid] = rows_p
            rows.append(row)
        changes = []  # a change counts when the next two windows both show it (one window of four minutes' defending is too noisy to call)
        for a, b, c in zip(rows, rows[1:], rows[2:]):
            for pid, q in b["players"].items():
                p_, r_ = a["players"].get(pid), c["players"].get(pid)
                if not (p_ and r_) or min(p_["minutes_defending"], q["minutes_defending"], r_["minutes_defending"]) < 3:
                    continue
                nm, at = self.names[pid], b["window"].split("-")[0]
                n0 = len(changes)
                both = lambda f: f(q) and f(r_)
                lo, hi = sorted((p_["press"], min(q["press"], r_["press"]) if q["press"] > p_["press"] else max(q["press"], r_["press"])))
                if hi - lo >= .04 and hi >= 2 * max(lo, .01) and (q["press"] > p_["press"]) == (r_["press"] > p_["press"]):
                    changes.append(dict(at=at, player=nm, change=f"closes the ball down {'more' if q['press'] > p_['press'] else 'less'}", before=p_["press"], after=round((q["press"] + r_["press"]) / 2, 3)))
                if all("press_height_m" in w for w in (p_, q, r_)):
                    d1, d2 = q["press_height_m"] - p_["press_height_m"], r_["press_height_m"] - p_["press_height_m"]
                    if min(abs(d1), abs(d2)) >= 10 and d1 * d2 > 0:
                        changes.append(dict(at=at, player=nm, change=f"closes the ball down {'higher' if d1 > 0 else 'deeper'}", before_height_m=p_["press_height_m"],
                                            after_height_m=round((q["press_height_m"] + r_["press_height_m"]) / 2, 1)))
                if p_["tracks"] and both(lambda w: w["tracks"] and w["tracks"] != p_["tracks"] and w["tracks_share"] >= .5) and q["tracks"] == r_["tracks"]:
                    changes.append(dict(at=at, player=nm, change=f"tracks {q['tracks']} instead of {p_['tracks']}"))
                if "holds" in p_ and both(lambda w: w.get("holds") and w["holds"] != p_["holds"]) and q["holds"] == r_["holds"] and np.hypot(*np.subtract(q["holds_xy"], p_["holds_xy"])) >= 10:
                    changes.append(dict(at=at, player=nm, change=f"holds a different space: now {q['holds']} (was {p_['holds']})"))
                for ch in changes[n0:]:
                    ch.update(id=pid, t=b["t0"], confirmed_t=c["t1"])  # seen once the second window is over
        players = {}
        for pid in {pid for r in rows for pid in r["players"]}:
            ws = [(r["window"], r["players"][pid]) for r in rows if pid in r["players"]]
            mins = sum(w["minutes_defending"] for _, w in ws)
            most = lambda key: max({w[key] for _, w in ws if w.get(key)}, key=lambda v: sum(w["minutes_defending"] for _, w in ws if w.get(key) == v), default=None)
            players[self.names[pid]] = dict(id=pid, role=self.players[pid]["role"], minutes_defending=round(mins, 1), tracks_most=most("tracks"), holds_most=most("holds"),
                                            press=round(sum(w["press"] * w["minutes_defending"] for _, w in ws) / mins, 3),
                                            mark=round(sum(w["mark"] * w["minutes_defending"] for _, w in ws) / mins, 3),
                                            press_height_m=round(float(np.average([w["press_height_m"] for _, w in ws if "press_height_m" in w],
                                                                                   weights=[w["press"] * w["minutes_defending"] for _, w in ws if "press_height_m" in w])), 1)
                                            if any("press_height_m" in w and w["press"] > 0 for _, w in ws) else None,
                                            by_window={wn: {k: v for k, v in w.items() if k not in ("holds_xy", "tracks_id")} for wn, w in ws})
        team_rows = []
        for r in rows:
            sel = (ts >= r["t0"]) & (ts < r["t1"])
            px_ = XY[sel][:, 1:, 0][duty[sel][:, 1:] == 2]
            team_rows.append(dict(window=r["window"], press_share=round(float((duty[sel][:, 1:] == 2).mean()), 3) if sel.any() else None,
                                  mark_share=round(float((duty[sel][:, 1:] == 1).mean()), 3) if sel.any() else None,
                                  press_height_m=round(float(px_.mean()), 1) if len(px_) >= 10 else None))
        return dict(team=self.team_names[team], windows=[r["window"] for r in rows], team_by_window=team_rows, players=players, changes=changes, rows=rows,
                    note="read from tracking alone: press = share of the opponents' possession spent closing the ball down (press_height_m: where, "
                         "metres from his own goal; a high press shows as height more than as time); mark = share spent "
                         "tracking one man (paired goal-side within 8 m); the rest he holds his space in the block. At a single moment a zonal "
                         "defender near an attacker looks like a marker, so read the 15-minute shares, not single frames.")

    def score_duties(self, team, window=900.):
        """The duty reader against what the engine actually told each defender (its job log): frame agreement on zone / mark / press,
        how well the press shares match per player and window, and how often the man read as tracked is the one he was given."""
        if self.jobs is None:
            return None
        idx, duty, tgt = self._duty_frames(team)
        J = self.jobs[idx, 11 * team:11 * team + 11]
        true = np.where((J >= 0) & (J <= 10), 1, np.where((J == 200) | (J == 201), 2, 0))  # cover, lane, post and space are holding a place in the block
        out = true[:, 1:], duty[:, 1:]
        agree = float((out[0] == out[1]).mean())
        ts = idx * self.dt
        rp, tp, hits, n_t = [], [], 0, 0
        for w0, w1 in self._windows(window):
            sel = (ts >= w0) & (ts < w1)
            if sel.sum() >= 60:
                for k in range(1, 11):
                    rp.append(float((duty[sel, k] == 2).mean())); tp.append(float((true[sel, k] == 2).mean()))
                    tj = J[sel, k][(J[sel, k] >= 0) & (J[sel, k] <= 10)]
                    rm = tgt[sel, k][duty[sel, k] == 1]
                    if len(tj) >= .25 * sel.sum() and len(rm):
                        n_t += 1; hits += int(np.bincount(tj, minlength=11).argmax() == np.bincount(rm, minlength=11).argmax())
        XY = self._team_xy(team, self.F[idx, 5 + 22 * team:5 + 22 * team + 22].reshape(len(idx), 11, 2))[:, 1:, 0]
        h_read, h_true = XY[out[1] == 2], XY[out[0] == 2]
        return dict(frame_agreement=round(agree, 3),
                    press_height_read_m=round(float(h_read.mean()), 1) if len(h_read) else None, press_height_true_m=round(float(h_true.mean()), 1) if len(h_true) else None, by_duty={nm: dict(true_share=round(float((out[0] == c).mean()), 3), recall=round(float((out[1][out[0] == c] == c).mean()), 3),
                                                                     precision=round(float((out[0][out[1] == c] == c).mean()), 3) if (out[1] == c).any() else None)
                                                            for c, nm in ((0, "zone"), (1, "mark"), (2, "press"))},
                    press_share_corr=round(float(np.corrcoef(rp, tp)[0, 1]), 3) if len(rp) > 2 else None,
                    press_share_mae=round(float(np.abs(np.subtract(rp, tp)).mean()), 3) if rp else None,
                    tracked_man_right=round(hits / n_t, 3) if n_t else None, tracked_cases=n_t)

    def find_moments(self, player=None, team=None, types=None, group=None, outcome=None, third=None, minutes=None, limit=40):
        """Events matching a filter: player id, team index, event types or an action group (passing, shooting, carrying, defending,
        keeping, discipline), outcome, third of the pitch in the acting team's frame (own/middle/final), time range [a, b) in seconds."""
        want = set(types or ()) | set(GROUPS.get(group, ()))
        out = []
        for e in self.events:
            if player and e.get("player") != player:
                continue
            if team is not None and e.get("team") != team:
                continue
            if want and e["type"] not in want:
                continue
            if outcome and e.get("outcome") != outcome:
                continue
            if minutes and not (minutes[0] <= e["t"] < minutes[1]):
                continue
            if third:
                xy = self._ev_xy(e)
                if xy is None or ["own", "middle", "final"][min(int(xy[0] / 35), 2)] != third:
                    continue
            out.append(dict(id=e["id"], clock=e["clock"], t=e["t"], type=e["type"], team=self.team_names[e["team"]] if e.get("team") in (0, 1) else None,
                            player=self.names.get(e.get("player")), outcome=e.get("outcome"),
                            xg=e.get("xg"), clip=[round(max(0., e["t"] - 6), 1), round(e["t"] + 4, 1)]))
        return dict(count=len(out), moments=out[:limit])

    # ---------------------------------------------------------------- ground truth (for scoring the agents, never shown to them)
    def truth(self, team):
        """What the engine actually did: starting formation, formation changes, tactic changes, with reasons."""
        ev = [e for e in self.events if e.get("team") == team]
        start = next((e["formation"] for e in ev if e["type"] == "lineup"), self.meta[("home", "away")[team]]["formation"])
        ch = [dict(at=e["clock"], t=e["t"], formation=e["formation"], previous=e["previous"], reason=e["reason"]) for e in ev if e["type"] == "formation_change"]
        # a change nobody could see is not scored: one to the same layout (3-5-2 to 5-3-2), with under 2.5 minutes left to show it, or after which
        # the side hardly had the ball in the opposition half (under a minute), where its shape with the ball is read
        att = (self.F[:, 4] == team) & ((self.F[:, 1] if team == 0 else L - self.F[:, 1]) > L / 2)
        att_after = lambda t: float(att[int(t / self.dt):].sum() * self.dt) if self.read_attack else 1e9
        seen = lambda c: not self.same_shape(c["formation"], c["previous"]) and self.meta["duration"] - c["t"] >= 150 and att_after(c["t"]) >= 60
        return dict(team=self.team_names[team], starting=start,
                    formation_changes=[c for c in ch if seen(c)], unseeable_changes=[c for c in ch if not seen(c)],
                    tactic_changes=[dict(at=e["clock"], t=e["t"], reason=e["reason"], changes=e["changes"]) for e in ev if e["type"] == "tactic_change"])
