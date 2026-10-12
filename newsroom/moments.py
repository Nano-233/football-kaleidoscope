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
    "score": "home-away (teams.home first); a team may say its own score first, e.g. the away side leading 0-1 is '1-0 up'",
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
    if m["type"] in ("goal", "own_goal"):
        return f"{m['clock']} Goal for {f['team']}: {f['player']} scores, {f['teams']['home']} {f['score']} {f['teams']['away']}."
    if m["type"] == "full_time":
        return f"Full time: {f['teams']['home']} {f['score']} {f['teams']['away']}."
    who = f" {f['player']} ({f['team']})" if f["player"] else f" {f['team']}" if f["team"] else ""
    return f"{m['clock']} {m['type'].replace('_', ' ').capitalize()}:{who}."


def moments(match):
    meta, acc = match["meta"], StatsAccumulator(match["meta"])
    team = [meta["home"]["name"], meta["away"]["name"]]
    names = {p["id"]: p["name"] for k in ("home", "away") for p in meta[k]["players"] + meta[k]["bench"]}
    out, score, last_tactic = [], [0, 0], {}
    for e in match["events"]:
        acc.update(e)
        ty, tm = e["type"], e.get("team")
        if ty in ("goal", "own_goal"):
            score = [e["home"], e["away"]]
        kind = {"goal": "goal", "own_goal": "own_goal", "penalty": "penalty", "sent_off": "red_card", "var_overturned": "var_overturned", "full_time": "full_time"}.get(ty)
        if ty == "shot" and e["outcome"] != "goal" and e["xg"] >= BIG_CHANCE_XG:
            kind = "big_chance"
        if ty == "tactic_change" and e.get("reason") == "man down" and e["t"] - last_tactic.get(tm, -1e9) > 900:  # the manager re-tunes every 30 s: only the first reaction is news
            kind, last_tactic[tm] = "tactic_change", e["t"]
        if not kind:
            continue
        t = acc.teams()
        stats = {team[i]: {k: round(t[i][k], 2) if isinstance(t[i][k], float) else t[i][k] for k in STAT_KEYS} for i in (0, 1)}
        out.append(dict(
            moment=dict(type=kind, clock=e["clock"], t=e["t"], id=e["id"]), teams=dict(home=team[0], away=team[1]),
            team=team[tm] if tm in (0, 1) else "", player=names.get(e.get("player"), ""), score=f"{score[0]}-{score[1]}", xg=e.get("xg"),
            assist=names.get(e.get("assist"), ""), reason=e.get("reason") or e.get("card") or "", why=_why(e, names), stats=stats, glossary=GLOSSARY))
    return out


if __name__ == "__main__":
    import sys
    for m in moments(json.load(open(sys.argv[1]))):
        print(m["moment"]["clock"], m["moment"]["type"], m["team"], m["player"], m["score"], m["xg"], m["why"])
