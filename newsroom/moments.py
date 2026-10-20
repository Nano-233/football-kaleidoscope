"""Which moments are worth a newsroom's attention, and the fact pack for each. Deterministic: no model decides what happened.

A fact pack holds every number an agent may quote (score, xG, team stats at that minute, the engine's own `why` for the decisive action).
The fact-checker later rejects any number a line quotes that is not in the pack.
"""
import json

from analytics.stats import StatsAccumulator

BIG_CHANCE_XG = .25
STAT_KEYS = ("shots", "shots_on_target", "xg", "passes_completed", "passes", "big_chances", "ppda", "corners", "fouls", "yellow_cards", "red_cards")
# What each number means, in the pack so the writers cannot misread a metric (e.g. PPDA is a ratio, not a count of actions).
GLOSSARY = {
    "xg": "expected goals: the summed probability that each shot is scored (one shot's xg is that shot's chance)",
    "shots_on_target": "shots that were saved or scored",
    "big_chances": "shots with xg of 0.25 or more",
    "ppda": "passes allowed per defensive action in the opponent's build-up zone; LOWER means a more intense press",
    "passes_completed": "passes that reached a team-mate (passes is all attempts)",
    "score": "home goals - away goals (teams.home first); scoreline spells it out with the names and leader says who is ahead",
}


def _why(e, names):
    """The engine's recorded reasons for a shot, as one sentence."""
    w = e.get("why")
    if not w:
        return ""
    alts = [o for o in w.get("options", []) if o["kind"] != "shot"]
    best = max(alts, key=lambda o: (o.get("p_success") or 0) * 1., default=None)
    s = f"nearest defender {w['nearest_opponent_m']} m away, pressure {w['pressure']}"
    if best and best.get("to"):
        s += f"; the pass to {names.get(best['to'], best['to'])} had {round(best['p_success'] * 100)}% chance"
    return s


def template_line(f):
    """A plain line built only from the pack: what the newsroom publishes when every agent draft was held by the fact-checker."""
    m = f["moment"]
    if m["type"] == "alert":
        return f"{m['clock']} {f['alert']['text']}"
    if m["type"] in ("goal", "own_goal"):
        return f"{m['clock']} Goal for {f['team']}: {f['player']} scores, {f['teams']['home']} {f['score']} {f['teams']['away']}."
    if m["type"] == "full_time":
        return f"Full time: {f['teams']['home']} {f['score']} {f['teams']['away']}."
    if m["type"] == "shape_change":
        sh = f["shift"]
        return f"{m['clock']} {f['team']} change shape {sh['phase']}: {sh['previous']} to {sh['now']}."
    if m["type"] == "spell":
        sp = f["spell"]
        return f"{m['clock']} {f['team']} had the upper hand from {sp['start']} to {sp['end']} (xG {sp['xg']} to {sp['opponent_xg']})."
    if m["type"] == "substitution":
        return f"{m['clock']} Substitution, {f['team']}: {f['player']} on for {f['off']}."
    if m["type"] == "half_time":
        return f"Half-time: {f['teams']['home']} {f['score']} {f['teams']['away']}."
    who = f" {f['player']} ({f['team']})" if f["player"] else f" {f['team']}" if f["team"] else ""
    return f"{m['clock']} {m['type'].replace('_', ' ').capitalize()}:{who}."


def _observed(match, names, team):
    """Moments the newsroom sees from the data rather than from a single event: a change of shape the reader confirmed (with or without
    the ball), the end of a spell of dominance (published when it is over, so it uses nothing from later), substitutions, half-time.
    Never the engine's own record of what the manager decided: a broadcaster sees the shape change, not the instruction."""
    from analytics.tools import MatchView
    mv, out = MatchView(match), []
    for tm in (0, 1):
        for c in mv.formation_timeline(tm)["changes"]:
            out.append((c["confirmed_t"], "shape_change", dict(team=team[tm], shift=dict(phase=c["phase"].replace("_", " "), previous=c["previous"], now=c["formation"], since=c["at"]))))
    for sp in mv.momentum()["dominant_spells"]:
        out.append((sp["end_t"], "spell", dict(team=sp["team"], spell=dict(start=sp["start"], end=sp["end"], xg=sp["xg"], opponent_xg=sp["opponent_xg"]))))
    seen = set()
    for e in match["events"]:
        if e["type"] == "substitution" and (e["t"], e["team"]) not in seen:
            seen.add((e["t"], e["team"]))
            subs = [x for x in match["events"] if x["type"] == "substitution" and x["t"] == e["t"] and x["team"] == e["team"]]
            out.append((e["t"], "substitution", dict(team=team[e["team"]], player=", ".join(x["on_name"] for x in subs), off=", ".join(x["off_name"] for x in subs))))
        if e["type"] == "half_end":
            out.append((e["t"], "half_time", dict(team="", player="")))
    return sorted(out, key=lambda x: x[0])


def moments(match, observed=True):
    meta, acc = match["meta"], StatsAccumulator(match["meta"])
    team = [meta["home"]["name"], meta["away"]["name"]]
    names = {p["id"]: p["name"] for k in ("home", "away") for p in meta[k]["players"] + meta[k]["bench"]}
    pending = _observed(match, names, team) if observed else []
    out, score = [], [0, 0]

    def pack(kind, clock, t, eid, **kw):
        tt = acc.teams()
        stats = {team[i]: {k: round(tt[i][k], 2) if isinstance(tt[i][k], float) else tt[i][k] for k in STAT_KEYS} for i in (0, 1)}
        base = dict(moment=dict(type=kind, clock=clock, t=t, id=eid), teams=dict(home=team[0], away=team[1]), team="", player="",
                    score=f"{score[0]}-{score[1]}", scoreline=f"{team[0]} {score[0]}-{score[1]} {team[1]}",
                    leader=team[0] if score[0] > score[1] else team[1] if score[1] > score[0] else "level", xg=None, assist="", reason="", why="", stats=stats, glossary=GLOSSARY)
        base.update(kw)
        out.append(base)

    for e in match["events"]:
        while pending and pending[0][0] <= e["t"]:  # an observed moment is published with the numbers as they stood then
            t_, kind_, kw_ = pending.pop(0)
            pack(kind_, e["clock"], t_, None, **kw_)
        acc.update(e)
        ty, tm = e["type"], e.get("team")
        if ty in ("goal", "own_goal"):
            score = [e["home"], e["away"]]
        kind = {"goal": "goal", "own_goal": "own_goal", "penalty": "penalty", "sent_off": "red_card", "var_overturned": "var_overturned", "full_time": "full_time"}.get(ty)
        if ty == "shot" and e["outcome"] != "goal" and e["xg"] >= BIG_CHANCE_XG:
            kind = "big_chance"
        if not kind:
            continue
        pack(kind, e["clock"], e["t"], e["id"], team=team[tm] if tm in (0, 1) else "", player=names.get(e.get("player"), ""), xg=e.get("xg"),
             assist=names.get(e.get("assist"), ""), reason=e.get("card") or "", why=_why(e, names))
    return out


if __name__ == "__main__":
    import sys
    for m in moments(json.load(open(sys.argv[1]))):
        print(m["moment"]["clock"], m["moment"]["type"], m["team"], m["player"], m["score"], m["xg"], m["why"])
