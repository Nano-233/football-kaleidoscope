"""The investigating newsroom: specialists that work the analysis toolkit, an orchestrator that hands them questions, a writer for the
reader's profile, and the fact-checker. Microsoft Agent Framework on Foundry.

    question -> orchestrator -+-> tactics analyst --+  (each calls analytics.tools: shape, formation changes, pressing, momentum,
                              +-> scout ------------+   passing network, moment breakdown, player report, find moments)
                                                    v
                    writer (persona, depth, club or player, language) -> fact-checker -> answer
                       ^                                                     |
                       +------------- rejected: issues, rewrite -------------+

Every tool result is kept in `facts`; the fact-checker only lets through numbers that are roundings of numbers the tools returned, so
every figure in an answer is traceable to a calculation. A question asked at minute 60 is answered from what had happened by minute 60.
"""
import asyncio
import functools
import json
import re
from typing import Annotated

from agent_framework import Agent

from analytics.tools import MatchView
from matchsim.whatif import cached_what_if, t_at_minute
from newsroom.agents import _run, _verdict, unsupported_numbers, wrong_leader, wrong_team
from newsroom.clients import traced

MAX_REWRITES = 2
DEPTH = {"quick": "two or three short sentences, plain words, at most one number",
         "standard": "a short paragraph that quotes the two or three numbers that prove the point (with their minutes)",
         "deep": "a thorough answer with the evidence: numbers, phases, players, minutes"}
PERSONA = {"fan": "a football fan who wants the story and why it matters", "analyst": "an analyst who wants mechanisms and evidence",
           "scout": "a scout who judges players: strengths, weaknesses, role fit"}

TACTICS = """You are the tactics analyst of a football newsroom. Investigate the question with your tools before answering; never guess.
Formations: a team has a shape in possession and a shape out of possession at the same time, and they may differ; read both from the
tools (never assume how a system defends) and say which phase you mean. A system change is what formation_changes lists under system_changes, at
that minute, and nothing else: name each one with its minute; do not add or infer others from team_shape windows, substitutions or
without_ball_changes (those describe how the side defended; mention them only as that). Call formation_changes first, then team_shape for each period between changes. For each change of system, say why it was made: the match state at that minute
(the score from find_moments with goals, red cards, substitutions) is what a manager reacts to. Look for causes: changes of shape, pressing height and PPDA by period, momentum
spells, where attacks start, substitutions. For who presses, who tracks whom and who holds which space (and when that changed), use
defensive_duties: its numbers are shares of the opponents' possession, read from tracking. For why goals were scored or conceded, find every one of
those goals so far with find_moments and call moment_breakdown on each: say how each move was built and where it beat the defence (who lost the ball,
the pass that broke the line, who was free), not general trends. For a "what if" question (a different instruction or formation from a minute on), use what_if
and report both columns, as it was and with the change, as chances out of the runs; say the runs are simulations. Use only numbers your tools returned. Reply with concise findings (bullet points), each with
its numbers and the minutes (e.g. 63') it refers to."""

SCOUT = """You are the scout of a football newsroom. Investigate players with your tools before answering: the player report (stats, rating
terms, physical output, per-90 against his role's average, heat map) and his moments. Judge strengths and weaknesses from that evidence,
compared with his role. Use only numbers your tools returned. Reply with concise findings (bullet points) with numbers and minutes."""

ORCHESTRATOR = """You run the newsroom desk. For the reader's question, call the specialists you need (tactics_analyst for teams, shapes,
pressing, momentum, why things happened and "what if" questions about a different instruction or formation; scout for players), with a precise task for each; call only the specialists the question needs (why a team scored or conceded is the tactics analyst's
alone), and when you need both, call them in the same step so they work at the same time. Use match_summary for
the basic facts. Then reply with the combined findings exactly as the specialists gave them (bullet points, numbers and minutes kept).
Do not add numbers of your own. If a specialist returns an error or no evidence, call it again; if it still has none, say the
data could not be read rather than answering without it."""

CHECKER = """You are the fact-checker. Compare the ANSWER with the FINDINGS the analysts produced from their tools. Reject it if a number is
attached to the wrong player, team, metric or minute, a metric is described wrongly (PPDA: lower means a harder press), or a claim is not
supported by the findings. Wording, tone and language are not errors. Reply only as JSON {"approved": bool, "issues": [str]}, one short
sentence per real error, an empty list when approved."""


UNITS = {"m": ("_m", 1.), "yd": ("_yd", 1.09361), "ft": ("_ft", 3.28084), "kmh": ("_kmh", 1.), "mph": ("_mph", .621371), "ms": ("_m_s", 1 / 3.6),
         "km": ("_km", 1.), "mi": ("_mi", .621371)}
UNIT_WORDS = {"m": "metres", "yd": "yards", "ft": "feet", "kmh": "km/h", "mph": "mph", "ms": "m/s", "km": "km", "mi": "miles"}


def _units(profile):
    """The reader's units, "length,speed,distance" (e.g. "yd,mph,mi"); metric when unset or unknown."""
    u = str((profile or {}).get("units") or "m,kmh,km").split(",")
    ok = len(u) == 3 and u[0] in ("m", "yd", "ft") and u[1] in ("kmh", "mph", "ms") and u[2] in ("km", "mi")
    return u if ok else ["m", "kmh", "km"]


def in_units(obj, units):
    """Tool output with every quantity named by its unit (*_m, *_kmh, *_km) converted to the reader's units and renamed to match."""
    plan = {"_m": units[0], "_kmh": units[1], "_km": units[2]}
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            suf = next((s for s in plan if isinstance(k, str) and k.endswith(s)), None)
            if suf and isinstance(v, (int, float)) and not isinstance(v, bool) and plan[suf] != suf[1:]:
                new, f = UNITS[plan[suf]]
                k, v = k[:-len(suf)] + new, round(v * f, 2 if suf == "_km" else 1)
            out[k] = in_units(v, units)
        return out
    if isinstance(obj, list):
        return [in_units(x, units) for x in obj]
    return obj


def _writer_text(profile):
    p = profile or {}
    who = PERSONA.get(p.get("persona", "fan"), PERSONA["fan"])
    side = (f" The reader supports {p['club']}: tell it from that side, warmly but honestly." if p.get("club") else "") + \
           (f" The reader follows the player {p['player']}: put him at the centre where the facts allow." if p.get("player") else "")
    lang = p.get("language", "English")
    return (f"You are the newsroom's writer. Turn the FINDINGS into the answer to the QUESTION for {who}. Length and style: "
            f"{DEPTH.get(p.get('depth', 'standard'), DEPTH['standard'])}.{side} Write in {lang}; keep player and team names as they are. "
            "Use only facts and numbers from the FINDINGS; never invent one. Mention minutes as they appear (e.g. 63'). "
            f"Units: {', '.join(UNIT_WORDS[u] for u in _units(p))}; the FINDINGS already use them, so never convert a number.")


def _until(match, t):
    """The match as it stood at time t (seconds): later events and frames removed."""
    if t is None:
        return match
    m = dict(match)
    m["events"] = [e for e in match["events"] if e["t"] <= t]
    m["frames"] = match["frames"][:int(t / match["meta"]["dt"]) + 1]
    m["meta"] = dict(match["meta"], duration=float(t))
    if match.get("jobs"):
        m["jobs"] = match["jobs"][:len(m["frames"])]
    return m


class Desk:
    def __init__(self, client, match, profile=None, until=None, on_step=None):
        self.units = _units(profile)
        self.client, self.profile, self.on_step = client, profile or {}, on_step
        self.mv = MatchView(_until(match, until))
        self.full = match
        self.facts, self.raw, self.trail = [], [], []  # facts: what the agents saw (reader's units); raw: the same in metres, for drawing
        mv = self.mv
        teams = {0: 0, 1: 1, "home": 0, "away": 1}
        for t, side in enumerate(("home", "away")):
            teams[mv.meta[side]["name"].lower()] = t
            teams[mv.meta[side]["code"].lower()] = t

        def team_ix(team):
            k = str(team).strip().lower()
            if k not in teams:
                raise ValueError(f"unknown team {team!r}; use home, away, {mv.team_names[0]} or {mv.team_names[1]}")
            return teams[k]

        def player_id(player):
            k = str(player).strip().lower()
            for pid, p in mv.players.items():
                if k in (pid.lower(), p["name"].lower()) or k == str(p["number"]):
                    return pid
            hits = [pid for pid, p in mv.players.items() if k in p["name"].lower()]
            if len(hits) == 1:
                return hits[0]
            raise ValueError(f"unknown or ambiguous player {player!r}")

        def logged(agent, fn):
            @functools.wraps(fn)  # keeps the signature: the framework builds the tool's parameters from it
            def wrap(**kw):
                try:
                    out = fn(**kw)
                except Exception as ex:  # the model gets the error back and can retry with better arguments
                    out = {"error": str(ex)}
                self.raw.append(out)
                out = in_units(out, self.units)
                self.facts.append(out)
                self.trail.append(dict(agent=agent, tool=fn.__name__, args=kw))
                self._say(agent, fn.__name__, kw)
                return json.dumps(out, default=str)
            return wrap

        # minutes as the clock shows them (33' runs from 32:00; the second half restarts at 46' after the first half's stoppage time)
        t_from = lambda m: t_at_minute(mv.events, m, mv.meta["half_time"]) if m and mv.events else 0.
        t_to = lambda m: t_at_minute(mv.events, m + 1, mv.meta["half_time"]) if mv.events else 0.

        def match_summary() -> dict:
            """Score, goals (minute, scorer, team), cards, substitutions and the starting formations so far."""
            ev = mv.events
            goals = [dict(minute=e["clock"], team=mv.team_names[e["team"]], scorer=mv.names.get(e.get("player")), score=f"{e.get('home')}-{e.get('away')}") for e in ev if e["type"] in ("goal", "own_goal")]
            score = [0, 0]
            if goals:
                last = [e for e in ev if e["type"] in ("goal", "own_goal")][-1]
                score = [last["home"], last["away"]]
            return dict(home=mv.team_names[0], away=mv.team_names[1], score=f"{score[0]}-{score[1]}", minute=ev[-1]["clock"] if ev else "1'", goals=goals,
                        cards=[dict(minute=e["clock"], team=mv.team_names[e["team"]], player=mv.names.get(e.get("player")), card=e["card"]) for e in ev if e["type"] == "card"],
                        substitutions=[dict(minute=e["clock"], team=mv.team_names[e["team"]], off=e["off_name"], on=e["on_name"], reason=e.get("reason")) for e in ev if e["type"] == "substitution"],
                        starting_formations={mv.team_names[t]: mv.meta[s]["formation"] for t, s in enumerate(("home", "away"))},
                        win_probability_now={k: wp[k] for k in ("home", "draw", "away")} if (wp := mv.win_probability()["series"][-1]) else None,
                        win_probability_note="simple model: league scoring rate blended with each side's xG so far")

        def list_players(team: Annotated[str, "home, away or the team name"]) -> dict:
            """The team's players: number, name, id, position, minutes and rating so far."""
            t = team_ix(team)
            from analytics.stats import StatsAccumulator
            acc = StatsAccumulator(mv.meta)
            for e in mv.events:
                acc.update(e)
            st = acc.players()
            return dict(team=mv.team_names[t], players=[dict(number=p["number"], name=p["name"], id=pid, role=p["role"], minutes=st.get(pid, {}).get("minutes", 0), rating=st.get(pid, {}).get("rating"))
                                                       for pid, p in mv.players.items() if p["team"] == t and st.get(pid, {}).get("minutes", 0) > 0])

        def team_shape(team: Annotated[str, "home, away or the team name"], from_minute: Annotated[int, "first minute"] = 0, to_minute: Annotated[int, "last minute; omit for now"] = 0) -> dict:
            """Average positions and the formation the team's shape reads as, in possession and out of possession (they differ at the same time), with the defensive line height and the block's length and width."""
            t, t0 = team_ix(team), t_from(from_minute)
            t1 = t_to(to_minute) if to_minute else None
            out = dict(in_possession=mv.shape(t, t0, t1, "in"), out_of_possession=mv.shape(t, t0, t1, "out"))
            inside = [c for c in mv.formation_timeline(t)["changes"] if t0 < c["t"] < (t1 or 1e9)]
            if inside:  # an average across a change of system describes a shape nobody played
                out["warning"] = "the system changed inside this window (" + ", ".join(f"{c['at']} to {c['formation']}" for c in inside) + "); call team_shape for each period separately"
            return out

        def formation_changes(team: Annotated[str, "home, away or the team name"]) -> dict:
            """The team's system over the match: the shape it started in with and without the ball, the changes of system (read from its shape
            with the ball) and the changes in how it defended (its shape without the ball)."""
            tl = mv.formation_timeline(team_ix(team))
            t = team_ix(team)

            def state(at):  # the match state the bench saw at that moment: score from this team's side, players on the pitch
                ev = [e for e in mv.events if e["t"] <= at]
                g = [e for e in ev if e["type"] in ("goal", "own_goal")]
                h, a = (g[-1]["home"], g[-1]["away"]) if g else (0, 0)
                us, them = (h, a) if t == 0 else (a, h)
                reds = [sum(1 for e in ev if e["type"] == "card" and e.get("card") != "yellow" and e.get("team") == s) for s in (t, 1 - t)]
                return dict(score_for_against=f"{us}-{them}", state="leading" if us > them else "trailing" if us < them else "level", players=[11 - reds[0], 11 - reds[1]])
            ch = lambda ph: [dict(minute=c["at"], formation=c["formation"], previous=c["previous"], match_state_then=state(c["t"])) for c in tl["changes"] if c["phase"] == ph]
            return dict(team=tl["team"], starting=tl["starting"], system_changes=ch("in_possession"), without_ball_changes=ch("out_of_possession"),
                        note="a system change is a change in system_changes; without_ball_changes say how the team defended and are not changes of system on their own")

        def pressing(team: Annotated[str, "home, away or the team name"]) -> dict:
            """PPDA for the whole match and by 15 minutes (lower = harder press), height of the defensive actions, where balls were won and counter-press wins."""
            return mv.pressing(team_ix(team))

        def defensive_duties(team: Annotated[str, "home, away or the team name"], player: Annotated[str, "player name, shirt number or id; empty for the whole team"] = "") -> dict:
            """Out of possession, read from tracking: how much of the opponents' possession each player spends closing the ball down and where
            (press_height_m from his own goal), how much tracking one man and whom, the space he holds in the block, by 15 minutes, and the
            changes (closes down more / less / higher / deeper, tracks someone else, holds a different space) with the minute they started."""
            d = mv.duties(team_ix(team))
            if player:
                pid = player_id(player)
                one = next((v for v in d["players"].values() if v["id"] == pid), None)
                return dict(team=d["team"], player=mv.names.get(pid), duties=one or "no defending time to read", changes=[{k: v for k, v in c.items() if k not in ("id", "t", "confirmed_t")} for c in d["changes"] if c["player"] == mv.names.get(pid)], note=d["note"])
            return dict(team=d["team"], team_by_window=d["team_by_window"], changes=[{k: v for k, v in c.items() if k not in ("id", "t", "confirmed_t")} for c in d["changes"]], note=d["note"],
                        players={nm: {k: v for k, v in q.items() if k != "by_window"} for nm, q in d["players"].items()})

        def momentum() -> dict:
            """xG, shots and field tilt for both teams by 5 minutes, cumulative xG, and the spells when one side dominated."""
            return mv.momentum()

        def passing_network(team: Annotated[str, "home, away or the team name"], from_minute: Annotated[int, "first minute"] = 0, to_minute: Annotated[int, "last minute; omit for now"] = 0) -> dict:
            """Pass links between players, average pass positions, the main connectors and which side entries into the final third favour."""
            return mv.passing_network(team_ix(team), t_from(from_minute), t_to(to_minute) if to_minute else None)

        def moment_breakdown(event_id: Annotated[int, "the id of the event (a goal, shot, etc.) from find_moments"]) -> dict:
            """The possession that led to a moment, step by step, with the alternatives each player had and the pressure he was under."""
            return mv.sequence(int(event_id))

        def player_report(player: Annotated[str, "player name, shirt number or id"]) -> dict:
            """A player's stats (including pass length and difficulty, hard passes completed, shot speed), rating and what made it, physical output (distance, sprints, top speed), heat map, per-90 against his role's average."""
            return mv.player_profile(player_id(player))

        def find_moments(player: Annotated[str, "player name or id; empty for any"] = "", team: Annotated[str, "home, away or team name; empty for any"] = "",
                         action_group: Annotated[str, "passing, shooting, carrying, defending, keeping or discipline; empty for any"] = "",
                         event_type: Annotated[str, "a single event type, e.g. goal, shot, tackle, foul, substitution; empty for any"] = "",
                         outcome: Annotated[str, "e.g. complete, won, lost, saved, goal; empty for any"] = "",
                         third: Annotated[str, "own, middle or final (in the acting team's direction); empty for any"] = "",
                         from_minute: Annotated[int, "first minute"] = 0, to_minute: Annotated[int, "last minute; 0 for now"] = 0) -> dict:
            """Events matching a filter, each with its id, minute, team and a clip range; use the id with moment_breakdown. An event belongs to
            the team that made it: a goal to the scoring team, so the goals a team conceded are the other team's goals (or leave team empty)."""
            return mv.find_moments(player=player_id(player) if player else None, team=team_ix(team) if team else None,
                                   types=[event_type] if event_type else None, group=action_group or None, outcome=outcome or None, third=third or None,
                                   minutes=(t_from(from_minute), t_to(to_minute) if to_minute else 1e9) if (from_minute or to_minute) else None)

        async def what_if(team: Annotated[str, "home, away or the team name"], from_minute: Annotated[int, "the minute the change is made"],
                          press: Annotated[float, "pressing intensity 0-1; -1 to leave as it was"] = -1., line_height: Annotated[float, "defensive line 0 deep - 1 high; -1 to leave"] = -1.,
                          directness: Annotated[float, "0 patient - 1 direct; -1 to leave"] = -1., risk: Annotated[float, "0 safe - 1 adventurous; -1 to leave"] = -1.,
                          formation: Annotated[str, "a formation such as 3-4-3; empty to leave"] = "") -> str:
            """Replay the match to that minute, make the change for that team, and play the rest of the match 12 times with and 12 times without it
            (the same random numbers for each pair): win/draw/loss chances, goals and xG after the change, as it was and with the change. Takes about a minute."""
            recipe = self.full["meta"].get("recipe")
            if not recipe:
                return json.dumps({"error": "this match cannot be replayed (no recipe)"})
            change = {k: v for k, v in dict(press=press, line_height=line_height, directness=directness, risk=risk).items() if v >= 0}
            if formation:
                change["formation"] = formation
            kw = dict(team=team, from_minute=from_minute, **change)
            self._say("tactics_analyst", "what_if", kw)
            try:
                t = t_at_minute(self.full["events"], int(from_minute), self.full["meta"]["half_time"])
                out = await asyncio.get_running_loop().run_in_executor(None, cached_what_if, recipe, t, team_ix(team), change)
                out = dict(out, team=mv.team_names[team_ix(team)], from_minute=from_minute)
            except Exception as ex:
                out = {"error": str(ex)}
            self.raw.append(out)
            self.facts.append(out)
            self.trail.append(dict(agent="tactics_analyst", tool="what_if", args=kw))
            return json.dumps(out, default=str)

        tt = [team_shape, formation_changes, pressing, defensive_duties, momentum, passing_network, moment_breakdown, find_moments, match_summary]
        st = [player_report, defensive_duties, find_moments, moment_breakdown, list_players, match_summary]
        self.tactics = Agent(client, TACTICS, name="tactics_analyst", tools=[logged("tactics_analyst", f) for f in tt] + [what_if])
        self.scout = Agent(client, SCOUT, name="scout", tools=[logged("scout", f) for f in st])
        self.orchestrator = Agent(client, ORCHESTRATOR, name="orchestrator", tools=[
            self.tactics.as_tool(name="tactics_analyst", description="Investigates teams, shapes, pressing, who closes down or tracks whom, momentum and why things happened.", arg_name="task"),
            self.scout.as_tool(name="scout", description="Investigates players: reports, strengths, weaknesses, moments.", arg_name="task"),
            logged("orchestrator", match_summary), logged("orchestrator", list_players)])
        self.writer = Agent(client, _writer_text(self.profile), name="writer")
        self.checker = Agent(client, CHECKER, name="fact_checker")

    def _say(self, agent, what, args=None):
        """Tell a listener (the streaming endpoint) what the newsroom is doing right now."""
        if self.on_step:
            self.on_step(dict(agent=agent, tool=what, args=args or {}))

    def _score_pack(self):
        """Who is ahead at the moment of the question, for the deterministic leader check."""
        g = [e for e in self.mv.events if e["type"] in ("goal", "own_goal")]
        h, a = (g[-1]["home"], g[-1]["away"]) if g else (0, 0)
        names = self.mv.team_names
        return dict(teams=dict(home=names[0], away=names[1]), leader=names[0] if h > a else names[1] if a > h else "level")

    def _squads(self):
        names = self.mv.team_names
        return {names[t]: [p["name"] for p in self.mv.players.values() if p["team"] == t] for t in (0, 1)}

    def _views(self, steps, facts):
        """Pictures for the answer, from the evidence the agents actually pulled: shapes, passing networks, the moves they broke down."""
        views, seen = [], set()
        names = self.mv.team_names
        for st, f in zip(steps, facts):
            if not isinstance(f, dict) or "error" in f:
                continue
            if st["tool"] == "team_shape":
                for ph in ("in_possession", "out_of_possession"):
                    sh = f.get(ph) or {}
                    if "positions" in sh and (sh["team"], ph, tuple(sh["window"])) not in seen:
                        seen.add((sh["team"], ph, tuple(sh["window"])))
                        views.append(dict(kind="shape", team=names.index(sh["team"]), phase=ph, title=f"{sh['team']} {sh['formation']} {ph.replace('_', ' ')} {sh['window'][0]}-{sh['window'][1]}",
                                          line=sh["defensive_line_m"], positions=[[q["id"], q["x"], q["y"]] for q in sh["positions"]]))
            elif st["tool"] == "passing_network" and f.get("links"):
                ids = {p["name"]: pid for pid, p in self.mv.players.items()}
                views.append(dict(kind="network", team=names.index(f["team"]), title=f"{f['team']} passing network {f['window'][0]}-{f['window'][1]}",
                                  links=[[ids[l["from"]], ids[l["to"]], l["passes"]] for l in f["links"]], nodes={ids[n]: xy for n, xy in f["avg_positions"].items()}))
            elif st["tool"] == "moment_breakdown" and f.get("steps") and f["moment"]["type"] in ("goal", "own_goal", "shot", "penalty"):
                views.append(dict(kind="move", title=f"The move: {f['moment']['clock']} {f['moment']['type'].replace('_', ' ')}", start=f["steps"][0]["t"], end=f["steps"][-1]["t"]))
        return views[:5]

    @traced("newsroom.ask")
    async def ask(self, question):
        """Answer a reader's question. Returns the answer, whether the fact-checker approved it, the rewrites, the steps each agent took and the
        moments it refers to (for watch links)."""
        n0, f0 = len(self.trail), len(self.facts)
        self._say("orchestrator", "reading the question")
        found = (await _run(self.orchestrator, f"QUESTION: {question}")).text.strip()
        facts = {"facts": self.facts}
        brief = f"QUESTION: {question}\nFINDINGS:\n{found}\n"
        attempts, ask = [], brief
        for _ in range(1 + MAX_REWRITES):
            self._say("writer", "drafting" if not attempts else f"rewriting (draft {len(attempts) + 1})")
            answer = (await _run(self.writer, ask)).text.strip()
            self._say("fact_checker", "checking every number against the tool results")
            v = await _run(self.checker, f"FINDINGS:\n{found}\nANSWER:\n{answer}\n")
            verdict = _verdict(v.text)
            bad = unsupported_numbers(answer, facts)
            lead = wrong_leader(answer, self._score_pack()) + wrong_team(answer, self._squads())
            approved = bool(verdict["approved"]) and not bad and not lead
            issues = list(verdict.get("issues") or []) + ([f"numbers not in the tool results: {bad}"] if bad else []) + lead
            attempts.append(dict(answer=answer, approved=approved, issues=issues))
            if approved:
                break
            self._say("fact_checker", "rejected", {"issues": "; ".join(issues)[:200]})
            ask = brief + f"Your previous answer was rejected by the fact-checker.\nPREVIOUS: {answer}\nISSUES: {issues}\nRewrite it, fixing these issues.\n"
        last = attempts[-1]
        moments = {m["clock"]: m for f in self.facts if isinstance(f, dict) for m in f.get("moments", [])}
        watch = [dict(clock=c, t=m["t"], type=m["type"], player=m["player"]) for c, m in moments.items() if re.search(rf"(?<![\d+]){re.escape(c)}", last["answer"])][:6]
        views = self._views(self.trail[n0:], self.raw[f0:])
        return dict(question=question, views=views, answer=last["answer"] if last["approved"] else None, held=None if last["approved"] else last["answer"],
                    approved=last["approved"], rewrites=len(attempts) - 1, findings=found, steps=self.trail[n0:], attempts=attempts, watch=watch)


if __name__ == "__main__":
    import sys
    from newsroom.clients import make_client
    m = json.load(open(sys.argv[1], encoding="utf-8"))
    q = sys.argv[2]
    prof = json.loads(sys.argv[3]) if len(sys.argv) > 3 else {}
    out = asyncio.run(Desk(make_client(), m, prof).ask(q))
    print(json.dumps({k: out[k] for k in ("answer", "held", "approved", "rewrites", "watch")}, indent=1, ensure_ascii=False))
    print("steps:", [(s["agent"], s["tool"], s["args"]) for s in out["steps"]])
