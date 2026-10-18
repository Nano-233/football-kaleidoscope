"""The newsroom: four agents on Microsoft Agent Framework, one workflow per moment.

    stats analyst ─┐
                   ├─> commentator ──> fact-checker ──> published line
    tactics analyst┘        ^                │
                            └── rejected ────┘  (up to MAX_REWRITES times, with the checker's issues)

The analysts work from the fact pack only. The commentator writes in a persona. The fact-checker has the last word, and deterministic
checks run after it whatever the model says: a number that is not a rounding of a number in the pack, or a minute that is not the clock,
is rejected. A line still rejected after the rewrites is not published: a template line built only from the pack goes out instead
(`fallback`), and the held drafts stay in the trail.
"""
import asyncio
import json
import re

from agent_framework import Agent

from newsroom.clients import traced
from newsroom.moments import template_line

PERSONAS = {
    "veteran": "a calm former centre-back: plain words, one tactical insight, never shouts",
    "analyst": "a numbers-first analyst: leads with the one statistic that explains the moment, names the metric exactly as FACTS.glossary defines it",
    "fan": "an excitable fan: short, loud, emotional, still accurate; at most one number, and no metric names or abbreviations (xG, PPDA, field tilt): say what they mean in a fan's words",
}
MAX_REWRITES = 2

RULES = ("Use only facts and numbers from FACTS. Never invent a player, minute, score or statistic. The minute is FACTS.moment.clock exactly. "
         "Read each metric as FACTS.glossary defines it.")


def build(client, persona="veteran"):
    mk = lambda role, text: Agent(client, f"[[ROLE:{role}]] {text} {RULES}", name=role)
    stats = mk("stats", "You are the stats analyst. From FACTS pick the two or three numbers that best explain this moment. Reply as a JSON list of {metric, value, meaning}.")
    tactics = mk("tactics", "You are the tactics analyst. Explain in one sentence, from FACTS.why, how the moment came about.")
    commentator = mk("commentator", f"You are the commentator, {PERSONAS[persona]}. Write one or two sentences of live commentary using the analysts' points. "
                                    "First say what this moment is (FACTS.moment.type, in plain words: a red card, not red_card): for a shape_change the shape before and after from FACTS.shift and which phase; "
                                    "for a spell who dominated and when from FACTS.spell; for a substitution who came on and off. Use FACTS.scoreline for the score; "
                                    "FACTS.leader is ahead.")
    checker = mk("fact_checker", "You are the fact-checker. Compare the commentary with FACTS line by line. Reject it if any number is attached to the wrong "
                                 "metric or team, the wrong team is said to lead (FACTS.leader is ahead), a metric is described against its FACTS.glossary meaning, the minute differs from FACTS.moment.clock, or a claim "
                                 "is not supported by FACTS. Wording, style and emphasis are not errors; set approved to true whenever you find no real error. Reply only as JSON {\"approved\": bool, \"issues\": [str]}, "
                                 "one short sentence per real error, and an empty list when approved.")
    return stats, tactics, commentator, checker


def _pack_numbers(x, out=None):
    """Every number in the pack (including those inside strings such as the clock, the score or the `why` sentence), skipping internal ids."""
    out = [] if out is None else out
    if isinstance(x, dict):
        for k, v in x.items():
            if k not in ("t", "id", "glossary"):
                _pack_numbers(v, out)
    elif isinstance(x, (list, tuple)):
        for v in x:
            _pack_numbers(v, out)
    elif isinstance(x, (int, float)) and not isinstance(x, bool):
        out.append(float(x))
    elif isinstance(x, str):
        out += [float(n) for n in re.findall(r"\d+(?:\.\d+)?", x)]
    return out


def unsupported_numbers(line, pack):
    """Numbers quoted in a line that are not a rounding of some number in the pack (0.072 supports 0.07; a fraction supports its percentage)."""
    nums = _pack_numbers(pack)

    def ok(tok):
        v, dec = float(tok), len(tok.split(".")[1]) if "." in tok else 0
        tol = .5 * 10 ** -dec + 1e-9
        return any(abs(v - p) <= tol or (p <= 1 and abs(v - p * 100) <= tol) for p in nums)
    return [n for n in re.findall(r"\d+(?:\.\d+)?", line) if not ok(n)]


def wrong_leader(line, pack):
    """A team said to lead when it does not, or to trail when it is ahead (the score reads home-away, which is easy to turn round)."""
    leader, out = pack.get("leader"), []
    for name in (pack.get("teams") or {}).values():
        n = re.escape(name)
        if re.search(rf"{n}\s+(?:lead|leads|holds?\s+(?:a|the|their)\s+[\w-]*\s*lead|(?:is|are|go|goes)\s+ahead|in front)", line, flags=re.I) and leader != name:
            out.append(f"{name} is not ahead")
        if re.search(rf"{n}\s+(?:trail|trails|(?:is|are)\s+behind)", line, flags=re.I) and leader == name:
            out.append(f"{name} is ahead, not behind")
    return out


def wrong_ppda(line, pair):
    """PPDA called low / lower when it is the higher of the two sides' (the lighter press), or high / higher when it is the lower. pair: both
    sides' PPDA; a quoted value that is neither (a 15-minute figure, say) is left alone."""
    vals = [v for v in pair if v is not None]
    if len(vals) != 2 or abs(vals[0] - vals[1]) < .05:
        return []
    out = []
    for m in re.finditer(r"\b(low|lower|high|higher)\b[^.\d]{0,25}?ppda[^.\d]{0,20}?(\d+(?:\.\d+)?)|ppda[^.\d]{0,20}?(\d+(?:\.\d+)?)[^.]{0,25}?\b(low|lower|high|higher)\b", line, flags=re.I):
        word, num = (m.group(1), m.group(2)) if m.group(1) else (m.group(4), m.group(3))
        v = float(num)
        side = next((i for i, p in enumerate(vals) if abs(v - p) <= .5 * 10 ** -(len(num.split(".")[1]) if "." in num else 0) + 1e-9), None)
        if side is None:
            continue
        is_low = vals[side] < vals[1 - side]
        if word.lower().startswith("low") != is_low:
            out.append(f"PPDA {num} is the {'lower' if is_low else 'higher'} of the two ({vals[0]} v {vals[1]}): lower PPDA means the harder press")
    return out


def wrong_team(line, squads):
    """A player given to the other side ("Oakhaven's Arstor Vossez" for a Westmoor player). squads: {team name: [player names]};
    a team is also matched by its first word (Oakhaven for Oakhaven FC)."""
    out = []
    for team in squads:
        said = "|".join(re.escape(v) for v in {team, team.split()[0]})
        for other, theirs in squads.items():
            for name in theirs if other != team else ():
                if re.search(rf"\b(?:{said})(?:'s|’s)\s+(?:\w+\s+){{0,2}}{re.escape(name)}", line):
                    out.append(f"{name} plays for {other}, not {team}")
    return out


def wrong_minutes(line, pack):
    """Minutes the line states ("21st minute", "21'") that are neither the moment's clock nor a minute the pack names."""
    clock = set(re.findall(r"\d+", pack["moment"]["clock"]))
    clock |= {m for c in re.findall(r"\d+(?:\+\d+)?'", json.dumps(pack)) for m in re.findall(r"\d+", c)}  # minutes the pack itself names (a spell's start and end, a change's minute)
    said = [a or b for a, b in re.findall(r"(\d+)(?:st|nd|rd|th)?\s+minute|(\d+)'", line)]
    return [m for m in said if m not in clock]


async def _run(agent, prompt, tries=6):
    """agent.run with back-off when the deployment is rate limited (429), has a passing server error (5xx) or times out; other errors propagate."""
    for i in range(tries):
        try:
            return await agent.run(prompt)
        except Exception as ex:  # the framework wraps the HTTP error in its own exception type
            if not re.search(r"Error code: (429|5\d\d)|timed out", str(ex)) or i == tries - 1:
                raise
            await asyncio.sleep(3 * 2 ** i)


def _verdict(text):
    m = re.search(r"\{.*\}", text or "", flags=re.S)
    try:
        v = json.loads(m.group(0)) if m else None
    except ValueError:
        v = None
    return v if isinstance(v, dict) and "approved" in v else {"approved": False, "issues": ["checker reply was not JSON"]}


@traced("newsroom.cover_moment")
async def cover(client, pack, persona="veteran"):
    """One moment through the newsroom. Returns the published item with every agent's contribution kept for the audit trail."""
    stats, tactics, commentator, checker = build(client, persona)
    facts = "FACTS: " + json.dumps(pack) + "\n"
    s, t = await asyncio.gather(_run(stats, facts), _run(tactics, facts))
    brief = facts + f"STATS: {s.text}\nTACTICS: {t.text}\n"
    attempts, ask = [], brief
    for _ in range(1 + MAX_REWRITES):
        line = (await _run(commentator, ask)).text.strip()
        v = await _run(checker, facts + f"COMMENTARY: {line}\n")
        verdict = _verdict(v.text)
        checks = []
        if bad := unsupported_numbers(line, pack):
            checks.append(f"numbers not in the facts: {bad}")
        if bad := wrong_leader(line, pack):
            checks.append("; ".join(bad))
        if bad := wrong_ppda(line, [st.get("ppda") for st in pack.get("stats", {}).values()]):
            checks.append("; ".join(bad))
        if bad := wrong_minutes(line, pack):
            checks.append(f"minute {bad} is not the clock {pack['moment']['clock']}")
        approved = bool(verdict["approved"]) and not checks
        issues = list(verdict.get("issues") or []) + checks
        attempts.append(dict(line=line, approved=approved, issues=issues, checker=v.text))
        if approved:
            break
        ask = brief + f"Your previous line was rejected by the fact-checker.\nPREVIOUS: {line}\nISSUES: {issues}\nRewrite it, fixing these issues.\n"
    last = attempts[-1]
    line, fallback = (last["line"], False) if last["approved"] else (template_line(pack), True)  # never publish a held draft; the template states only pack facts
    return dict(moment=pack["moment"], persona=persona, line=line, approved=True, fallback=fallback, issues=last["issues"],
                rewrites=len(attempts) - 1, trail=dict(stats=s.text, tactics=t.text, attempts=attempts))
