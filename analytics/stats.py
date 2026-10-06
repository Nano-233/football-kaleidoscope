"""Deterministic match statistics from the event stream. No AI here: this is the 'data' layer the agents reason over.

Every number is a reducer over events, so a finished match and a live feed use the same code:

    acc = StatsAccumulator(meta)
    for event in stream:
        acc.update(event)
    acc.players(), acc.teams()

The engine never computes aggregates; anything derivable from events lives here.
"""
from collections import Counter, defaultdict

L, W = 105.0, 68.0
SUCCESS = ("goal", "saved")
COUNT_KEYS = ["goals", "own_goals", "xg", "shots", "shots_on_target", "shots_blocked", "shots_in_box", "big_chances", "headed_shots", "passes", "passes_completed",
              "passes_received", "progressive_passes", "key_passes", "assists", "through_balls", "long_passes", "final_third_passes", "crosses", "crosses_completed",
              "throws", "carries", "carry_metres", "progressive_carries", "take_ons", "take_ons_won", "take_ons_stopped", "dribbled_past", "tackles", "tackles_won",
              "dispossessed", "miscontrols", "interceptions", "clearances", "blocks", "recoveries", "aerial_won", "aerial_lost", "fifty_fifty_won", "fifty_fifty_lost",
              "pressures", "fouls", "fouls_won", "yellow_cards", "second_yellow_cards", "red_cards", "offsides", "saves", "keeper_claims", "keeper_punchs", "keeper_sweeps",
              "errors", "errors_leading_to_shot", "errors_leading_to_goal", "corners", "free_kicks", "penalties", "possession_gains", "ppda_actions", "ppda_opp_passes"]


class StatsAccumulator:
    def __init__(self, meta):
        self.meta = meta
        self.info = {}  # player id -> dict(name, team, role, number)
        for t, key in enumerate(("home", "away")):
            for p in meta[key]["players"] + meta[key]["bench"]:
                self.info[p["id"]] = dict(name=p["name"], team=t, role=p["role"], number=p["number"], star=p.get("star", False), traits=p.get("traits", []))
        self.p = defaultdict(Counter)       # player id -> counters
        self.t = [Counter(), Counter()]     # team counters
        self.on = {}                         # player id -> minute went on
        self.off = {}                        # player id -> minute went off
        self.xt = lambda e: (e["x"] if e["team"] == 0 else L - e["x"])  # x in the team's own attacking frame
        self.last_t = 0.
        self._started = False

    # ------------------------------------------------------------------ stream
    def update(self, e):
        ty, pid, tm = e["type"], e.get("player"), e.get("team")
        self.last_t = max(self.last_t, e["t"])
        P, T = self.p, self.t
        inc = lambda who, key, n=1: P[who].update({key: n}) if who else None
        tinc = lambda team, key, n=1: T[team].update({key: n}) if team in (0, 1) else None
        if ty == "lineup":
            for p in e["players"]:
                self.on[p["id"]] = 0.
        elif ty == "substitution":
            self.off[e["off"]], self.on[e["on"]] = e["t"] / 60, e["t"] / 60
        elif ty == "sent_off":
            self.off[pid] = e["t"] / 60
        elif ty in ("pass", "cross", "clearance"):
            if ty == "clearance":
                inc(pid, "clearances"); tinc(tm, "clearances")
            else:
                inc(pid, "passes"); tinc(tm, "passes")
                if e.get("outcome") == "complete":
                    inc(pid, "passes_completed"); tinc(tm, "passes_completed")
                    inc(e.get("receiver"), "passes_received")
                if ty == "cross":
                    inc(pid, "crosses"); tinc(tm, "crosses")
                    if e.get("outcome") == "complete":
                        inc(pid, "crosses_completed")
                if e.get("progressive"):
                    inc(pid, "progressive_passes"); tinc(tm, "progressive_passes")
                if e.get("key_pass"):
                    inc(pid, "key_passes"); tinc(tm, "key_passes")
                if e.get("assist"):
                    inc(pid, "assists")
                if e.get("technique") == "through_ball":
                    inc(pid, "through_balls")
                if e.get("length", 0) > 32:
                    inc(pid, "long_passes"); tinc(tm, "long_passes")
                if self.xt(e) > L * .667 and e.get("outcome") == "complete":
                    tinc(tm, "final_third_passes")
                if e.get("body_part") == "hands":
                    inc(pid, "throws")
        elif ty == "carry":
            inc(pid, "carries"); inc(pid, "carry_metres", e.get("distance", 0))
            tinc(tm, "carries"); tinc(tm, "carry_metres", e.get("distance", 0))
            if e.get("end_x") is not None and (e["end_x"] - e["x"]) * (1 if tm == 0 else -1) > 10:
                inc(pid, "progressive_carries")
        elif ty == "take_on":
            won = e["outcome"] == "won"
            inc(pid, "take_ons"); tinc(tm, "take_ons")
            if won:
                inc(pid, "take_ons_won"); tinc(tm, "take_ons_won"); inc(e["target"], "dribbled_past"); tinc(1 - tm, "dribbled_past")
            else:
                inc(e["target"], "take_ons_stopped")
        elif ty == "tackle":
            inc(pid, "tackles"); tinc(tm, "tackles")
            if e["outcome"] == "won":
                inc(pid, "tackles_won"); tinc(tm, "tackles_won")
        elif ty == "dispossessed":
            inc(pid, "dispossessed")
        elif ty == "miscontrol":
            inc(pid, "miscontrols")
        elif ty == "interception":
            inc(pid, "interceptions"); tinc(tm, "interceptions")
        elif ty == "block":
            inc(pid, "blocks"); tinc(tm, "blocks")
        elif ty == "ball_recovery":
            inc(pid, "recoveries"); tinc(tm, "recoveries")
        elif ty == "duel":
            inc(pid, "aerial_won"); tinc(tm, "aerial_won"); inc(e.get("target"), "aerial_lost"); tinc(1 - tm, "aerial_lost")
        elif ty == "fifty_fifty":
            inc(pid, "fifty_fifty_won"); inc(e.get("target"), "fifty_fifty_lost")
        elif ty == "pressure":
            inc(pid, "pressures"); tinc(tm, "pressures")
        elif ty == "foul":
            inc(pid, "fouls"); tinc(tm, "fouls"); inc(e.get("target"), "fouls_won")
        elif ty == "card":
            inc(pid, e["card"] + "_cards"); tinc(tm, e["card"] + "_cards")
        elif ty == "offside":
            inc(pid, "offsides"); tinc(tm, "offsides")
        elif ty == "shot":
            inc(pid, "shots"); tinc(tm, "shots"); inc(pid, "xg", e["xg"]); tinc(tm, "xg", e["xg"])
            if e["outcome"] in SUCCESS:
                inc(pid, "shots_on_target"); tinc(tm, "shots_on_target")
            if e["outcome"] == "blocked":
                tinc(tm, "shots_blocked")
            if e.get("big_chance"):
                inc(pid, "big_chances"); tinc(tm, "big_chances")
            if e.get("body_part") == "head":
                inc(pid, "headed_shots")
            if self.xt(e) > L - 16.5 and abs(e["y"] - W / 2) < 20.2:
                tinc(tm, "shots_in_box")
        elif ty == "goal":
            inc(pid, "goals"); tinc(tm, "goals")
        elif ty == "own_goal":
            inc(pid, "own_goals"); tinc(tm, "goals")
        elif ty == "save":
            inc(pid, "saves"); tinc(tm, "saves")
        elif ty in ("keeper_claim", "keeper_punch", "keeper_sweep"):
            inc(pid, ty + "s")
        elif ty == "error":
            inc(pid, "errors"); inc(pid, "errors_leading_to_" + e["leads_to"])
        elif ty == "corner":
            tinc(tm, "corners")
        elif ty == "free_kick":
            tinc(tm, "free_kicks")
        elif ty == "penalty":
            tinc(tm, "penalties")
        elif ty == "possession_change":
            tinc(tm, "possession_gains")
        # defensive actions in the opponent's 60 % (for PPDA) and opponent passes there
        if ty in ("tackle", "interception", "foul") and tm in (0, 1) and "x" in e and self.xt(e) > .4 * L:
            tinc(tm, "ppda_actions")
        if ty == "pass" and tm in (0, 1) and self.xt(e) < .6 * L:
            tinc(1 - tm, "ppda_opp_passes")

    # ------------------------------------------------------------------ results
    def minutes(self, pid):
        end = self.last_t / 60
        return max(0., self.off.get(pid, end) - self.on[pid]) if pid in self.on else 0.

    def players(self):
        out = {}
        for pid, c in self.p.items():
            if pid not in self.info:
                continue
            d = dict(self.info[pid], minutes=round(self.minutes(pid), 1), **{k: round(c[k], 2) if isinstance(c[k], float) else c[k] for k in COUNT_KEYS})
            r = lambda a, b: round(a / b, 3) if b else None
            d["pass_accuracy"] = r(c["passes_completed"], c["passes"])
            d["take_on_rate"] = r(c["take_ons_won"], c["take_ons"])
            d["tackle_win_rate"] = r(c["tackles_won"], c["tackles"])
            d["aerial_win_rate"] = r(c["aerial_won"], c["aerial_won"] + c["aerial_lost"])
            d["rating"] = self.rating(c, self.info[pid]["role"], self.minutes(pid))
            out[pid] = d
        return out

    def teams(self):
        res = []
        for t in (0, 1):
            c = self.t[t]
            r = lambda a, b: round(a / b, 3) if b else None
            d = {k: round(c[k], 2) if isinstance(c[k], float) else c[k] for k in COUNT_KEYS}
            d.update(pass_accuracy=r(c["passes_completed"], c["passes"]), take_on_rate=r(c["take_ons_won"], c["take_ons"]),
                     tackle_win_rate=r(c["tackles_won"], c["tackles"]), aerial_win_rate=r(c["aerial_won"], c["aerial_won"] + c["aerial_lost"]),
                     ppda=r(c["ppda_opp_passes"], c["ppda_actions"]), shot_conversion=r(c["goals"], c["shots"]),
                     xg_per_shot=r(c["xg"], c["shots"]))
            res.append(d)
        return res

    @staticmethod
    def rating(c, role, minutes):
        """Transparent heuristic rating on a 1-10 scale (not a learned model): the weights are listed here so an agent can explain them."""
        if minutes < 1:
            return None
        r = 6.0
        r += 1.1 * c["goals"] + .8 * c["assists"] + .15 * c["key_passes"] + 1.2 * c["xg"] * (1 if role in ("ST", "WG", "AM") else .6)
        r += .02 * (c["passes_completed"] - (c["passes"] - c["passes_completed"]) * 1.5) / max(minutes / 90, .2) * .1
        r += .12 * c["tackles_won"] + .12 * c["interceptions"] + .06 * c["clearances"] + .08 * c["blocks"] + .05 * c["aerial_won"] + .1 * c["take_ons_won"]
        r += .15 * c["saves"] + .1 * c["keeper_claims"]
        r -= .5 * c["errors_leading_to_goal"] + .2 * c["errors_leading_to_shot"] + .1 * c["dispossessed"] + .06 * c["fouls"] + .4 * c["yellow_cards"] + 1.5 * (c["red_cards"] + c["second_yellow_cards"]) + 1.2 * c["own_goals"]
        return round(max(1., min(10., r)), 1)


def match_stats(match):
    """Convenience for a finished match dict (events + meta)."""
    acc = StatsAccumulator(match["meta"])
    for e in match["events"]:
        acc.update(e)
    return dict(teams=acc.teams(), players=acc.players())
