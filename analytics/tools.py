"""The analysis toolkit the newsroom agents call. Every number an agent may quote is computed here, from the event stream and the
tracking frames; no model computes a figure. Each tool returns a small JSON-ready dict.

    mv = MatchView(match)                 # match: the engine's output (meta, events, frames)
    mv.shape(team=0, t0=0, t1=2700)       # average positions and the formation they read as
    mv.formation_timeline(team=1)         # the formation read over the match, and when it changed
    mv.pressing(team=0)                   # PPDA and pressing height by 15 minutes, where the ball was won
    mv.momentum()                         # xG and field tilt by 5 minutes, spells of dominance
    mv.passing_network(team=0)            # pass links, connectors, where attacks start, preferred side
    mv.sequence(event_id)                 # what led to a moment, with the engine's reasons
    mv.player_profile(player_id)          # stats, rating terms, heat map, physical output, against role averages
    mv.find_moments(player=..., types=[...], outcome=..., third=..., minutes=(a, b))

Frames: team 0 attacks towards x = L, team 1 towards x = 0. Tools report positions in the team's own frame (own goal at x = 0,
attacking left to right), so the two teams read the same way.
"""
import json
import pathlib

import numpy as np
from scipy.optimize import linear_sum_assignment

from analytics.stats import StatsAccumulator, L, W
from matchsim.teams import FORMATIONS

SPRINT = 7.0      # m/s (25 km/h), the usual sprint threshold
ROLE_NORMS = pathlib.Path(__file__).with_name("role_norms.json")  # per-role per-90 averages over many simulated matches (scripts/role_norms.py)
# how each formation usually looks without the ball
DEFENDS_AS = {"4-3-3": ("4-3-3", "4-1-4-1", "4-5-1"), "4-2-3-1": ("4-2-3-1", "4-5-1", "4-4-2"), "3-4-3": ("5-4-1", "3-4-3"), "3-5-2": ("5-3-2", "3-5-2"),
              "4-4-2": ("4-4-2",), "4-5-1": ("4-5-1", "4-1-4-1"), "5-3-2": ("5-3-2",), "5-4-1": ("5-4-1",)}  # the systems a team lines up in (4-1-4-1 is how a 4-3-3 defends)
GROUPS = {
    "passing": ("pass", "cross", "ball_receipt"),
    "shooting": ("shot", "goal"),
    "carrying": ("carry", "take_on", "dispossessed", "miscontrol"),
    "defending": ("tackle", "interception", "block", "clearance", "pressure", "duel", "fifty_fifty", "ball_recovery", "dribbled_past"),
    "keeping": ("save", "keeper_claim", "keeper_punch", "keeper_sweep"),
    "discipline": ("foul", "foul_won", "card", "offside", "error"),
}


def _clock(s):
    return f"{int(s // 60) + 1}'"


class MatchView:
    def __init__(self, match):
        self.meta, self.events = match["meta"], match["events"]
        self.F = np.asarray(match["frames"], float)
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
    def shape(self, team, t0=0., t1=None, phase="out"):
        """Average positions of the team's outfield players between t0 and t1 (seconds), in or out of possession (phase: in/out/all),
        and the formation the shape reads as: the template whose normalised layout fits best (Hungarian assignment), with the runner-up."""
        t1 = self.meta["duration"] if t1 is None else t1
        fr = self._frame_range(t0, t1)
        poss = fr[:, 4]
        sel = {"in": poss == team, "out": poss == 1 - team, "all": np.ones(len(fr), bool)}[phase]
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
            team=self.team_names[team], window=[_clock(t0), _clock(t1)], phase=phase,
            formation=reads[0][0], runner_up=reads[1][0] if len(reads) > 1 else None, fit=round(reads[0][1], 3),
            defensive_line_m=round(float(np.mean(line[:4])), 1), block_length_m=round(float(np.ptp(P[:, 0])), 1), block_width_m=round(float(np.ptp(P[:, 1])), 1),
            positions=[dict(player=self.names[pid], id=pid, x=round(float(xy[0]), 1), y=round(float(xy[1]), 1)) for pid, xy in rows],
        )

    @staticmethod
    def _norm(P):
        lo, span = P.min(0), np.maximum(np.ptp(P, 0), 1.)
        return (P - lo) / span

    def _read_formation(self, P):
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

    def _window_costs(self, team, t0, t1, phase):
        """Template fit costs for the average shape in one window and phase ({} when there are too few frames)."""
        fr = self._frame_range(t0, t1)
        sel = fr[:, 4] == (team if phase == "in" else 1 - team)
        if sel.sum() < 25:
            return {}
        fr = fr[sel]
        P = []
        for k in range(11 * team, 11 * team + 11):
            xy = fr[:, [5 + 2 * k, 6 + 2 * k]]
            on = xy[:, 1] > -5
            if on.mean() < .5 or self.role_now[self._occupant(k, (t0 + t1) / 2)] == "GK":
                continue
            P.append(self._team_xy(team, xy[on].mean(0)))
        return dict(self._read_formation(np.array(P)))

    def formation_timeline(self, team, window=420., step=210., margin=1.5):
        """The formation window by window (overlapping windows), read from both phases: each formation is scored on how well the shape in
        possession fits it plus how well the shape out of possession fits the way that formation defends (DEFENDS_AS: a back three drops
        its wing-backs into a five, a 4-3-3's wingers drop into a 4-1-4-1 or 4-5-1). A change is reported when another formation fits better
        than the current one by `margin` in two windows running."""
        reads, t = [], 0.
        while t < self.meta["duration"] - 120:
            ci, co = self._window_costs(team, t, t + window, "in"), self._window_costs(team, t, t + window, "out")
            comb = {f: ci[f] + min((co[d] for d in DEFENDS_AS.get(f, (f,)) if d in co), default=9.) for f in ci if f in DEFENDS_AS} if ci and co else {}
            reads.append((t, comb, ci, co))
            t += step
        best = lambda c: min(c, key=c.get) if c else None
        cur = next((best(c) for _, c, _, _ in reads if c), None)
        start = cur
        changes = []
        for (ta, ca, _, _), (tb, cb, _, _) in zip(reads, reads[1:]):
            na, nb = best(ca), best(cb)
            if na and na == nb and na != cur and ca.get(cur, 9) > margin * ca[na] and cb.get(cur, 9) > margin * cb[nb]:
                changes.append(dict(at=_clock(ta + window / 2), t=ta + window / 2, formation=na, previous=cur))
                cur = na
        return dict(team=self.team_names[team], starting=start, changes=changes,
                    by_window=[dict(at=_clock(t), formation=best(c), in_possession=best(ci), out_of_possession=best(co)) for t, c, ci, co in reads[::2]])

    def score_reading(self, team, read_changes, tolerance=600.):
        """How a reading of the formation changes compares with what the engine did: a true change is found when the reading reports
        the same formation within `tolerance` seconds after it; a reported change that matches none is a false alarm."""
        truth = self.truth(team)["formation_changes"]
        used, hits = set(), []
        for tc in truth:
            m = next((i for i, rc in enumerate(read_changes) if i not in used and rc["formation"] == tc["formation"] and -60 <= rc["t"] - tc["t"] <= tolerance), None)
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
            tm = (int(w["at"].rstrip("'")) - 1) * 60 + 210.
            truth_now = [f for t, f in sched if t <= tm][-1]
            if w["formation"]:
                n += 1
                ok += w["formation"] == truth_now
        return round(ok / max(n, 1), 3)

    # ---------------------------------------------------------------- pressing
    def pressing(self, team, window=900.):
        """By window: PPDA (opponent passes in their own 60 % per defensive action there; lower = harder press), the average height of the
        team's defensive actions (metres from its own goal), balls won by third, and counter-press wins (won back within 5 s of losing it)."""
        out, lost_at = [], None
        acts = ("tackle", "interception", "foul", "ball_recovery", "block")
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
                if xy[0] > .4 * L:
                    r["def_actions"] += 1
        for w in sorted(rows):
            r = rows[w]
            out.append(dict(window=[_clock(w * window), _clock((w + 1) * window)], ppda=round(r["opp_passes"] / r["def_actions"], 1) if r["def_actions"] else None,
                            action_height_m=round(float(np.mean(r["heights"])), 1) if r["heights"] else None,
                            balls_won_own_mid_final_third=r["won"], counterpress_wins=r["counter"]))
        return dict(team=self.team_names[team], by_window=out, note="PPDA: lower means a more intense press")

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
                    spells.append(dict(team=self.team_names[side], start=_clock(k * window), end=_clock((j + 1) * window),
                                       xg=round(float(xg[side, k:j + 1].sum()), 2), opponent_xg=round(float(xg[1 - side, k:j + 1].sum()), 2)))
                    k = j
                    break
            k += 1
        return dict(teams=self.team_names, window_s=window,
                    series=[dict(at=_clock(w * window), xg=[round(float(xg[0, w]), 2), round(float(xg[1, w]), 2)], shots=[int(shots[0, w]), int(shots[1, w])],
                                 field_tilt_home=round(float(tilt[w]), 2)) for w in range(n)],
                    cumulative_xg=[round(float(xg[0].sum()), 2), round(float(xg[1].sum()), 2)], dominant_spells=spells)

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
        return dict(team=self.team_names[team], window=[_clock(t0), _clock(t1)],
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
                    minutes=mins, rating=st.get("rating"), rating_terms=terms, stats={k: st.get(k) for k in keys + ("goals", "assists", "goals_conceded", "pass_accuracy")},
                    physical=phys, heat_map_6x4=heat, per90_vs_role=vs)

    # ---------------------------------------------------------------- moments
    def find_moments(self, player=None, team=None, types=None, group=None, outcome=None, third=None, minutes=None, limit=40):
        """Events matching a filter: player id, team index, event types or an action group (passing, shooting, carrying, defending,
        keeping, discipline), outcome, third of the pitch in the acting team's frame (own/middle/final), minute range [a, b]."""
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
            if minutes and not (minutes[0] <= e["t"] / 60 <= minutes[1]):
                continue
            if third:
                xy = self._ev_xy(e)
                if xy is None or ["own", "middle", "final"][min(int(xy[0] / 35), 2)] != third:
                    continue
            out.append(dict(id=e["id"], clock=e["clock"], t=e["t"], type=e["type"], player=self.names.get(e.get("player")), outcome=e.get("outcome"),
                            xg=e.get("xg"), clip=[round(max(0., e["t"] - 6), 1), round(e["t"] + 4, 1)]))
        return dict(count=len(out), moments=out[:limit])

    # ---------------------------------------------------------------- ground truth (for scoring the agents, never shown to them)
    def truth(self, team):
        """What the engine actually did: starting formation, formation changes, tactic changes, with reasons."""
        ev = [e for e in self.events if e.get("team") == team]
        start = next((e["formation"] for e in ev if e["type"] == "lineup"), self.meta[("home", "away")[team]]["formation"])
        return dict(team=self.team_names[team], starting=start,
                    formation_changes=[dict(at=e["clock"], t=e["t"], formation=e["formation"], previous=e["previous"], reason=e["reason"]) for e in ev if e["type"] == "formation_change"],
                    tactic_changes=[dict(at=e["clock"], t=e["t"], reason=e["reason"], changes=e["changes"]) for e in ev if e["type"] == "tactic_change"])
