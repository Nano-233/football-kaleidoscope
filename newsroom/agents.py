"""The newsroom: four agents on Microsoft Agent Framework, one workflow per moment.

    stats analyst ─┐
                   ├─> commentator ──> fact-checker ──> published line
    tactics analyst┘

The analysts work from the fact pack only. The commentator writes in a persona. The fact-checker has the last word, and a deterministic
check (`unsupported_numbers`) runs after it whatever the model says: a number in the line that is not in the pack is rejected.
"""
import json
import re

from agent_framework import Agent
from agent_framework_orchestrations import ConcurrentBuilder, SequentialBuilder

PERSONAS = {
    "veteran": "a calm former centre-back: plain words, one tactical insight, never shouts",
    "analyst": "a numbers-first analyst: leads with the one statistic that explains the moment",
    "fan": "an excitable home-end fan: short, loud, emotional, still accurate",
}

RULES = "Use only facts and numbers from FACTS. Never invent a player, minute, score or statistic."


def build(client, persona="veteran"):
    mk = lambda role, text: Agent(client, f"[[ROLE:{role}]] {text} {RULES}", name=role)
    stats = mk("stats", "You are the stats analyst. From FACTS pick the two or three numbers that best explain this moment. Reply as a JSON list of {metric, value}.")
    tactics = mk("tactics", "You are the tactics analyst. Explain in one sentence, from FACTS.why, how the moment came about.")
    commentator = mk("commentator", f"You are the commentator, {PERSONAS[persona]}. Write one or two sentences of live commentary using the analysts' points.")
    checker = mk("fact_checker", "You are the fact-checker. Compare the commentary with FACTS. Reply as JSON {approved: bool, issues: [str]}.")
    desk = ConcurrentBuilder(participants=[stats, tactics]).build()
    return stats, tactics, commentator, checker, desk


def unsupported_numbers(line, pack):
    """Numbers quoted in a line that appear nowhere in the fact pack (minutes written as 10' count as supported by the clock)."""
    blob = json.dumps(pack)
    return [n for n in re.findall(r"\d+(?:\.\d+)?", line) if n not in blob and not any(n == d for d in re.findall(r"\d+", pack["moment"]["clock"]))]


async def cover(client, pack, persona="veteran"):
    """One moment through the newsroom. Returns the published item with every agent's contribution kept for the audit trail."""
    stats, tactics, commentator, checker, _ = build(client, persona)
    prompt = "FACTS: " + json.dumps(pack) + "\n"
    s = await stats.run(prompt)
    t = await tactics.run(prompt)
    c = await commentator.run(prompt + f"STATS: {s.text}\nTACTICS: {t.text}\n")
    v = await checker.run(prompt + f"COMMENTARY: {c.text}\n")
    try:
        verdict = json.loads(v.text)
    except ValueError:
        verdict = {"approved": False, "issues": ["checker reply was not JSON"]}
    bad = unsupported_numbers(c.text, pack)
    if bad:
        verdict = {"approved": False, "issues": verdict.get("issues", []) + [f"numbers not in the facts: {bad}"]}
    return dict(moment=pack["moment"], persona=persona, line=c.text, approved=verdict["approved"], issues=verdict["issues"],
                trail=dict(stats=s.text, tactics=t.text, checker=v.text))
