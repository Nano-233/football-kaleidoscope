"""The localizer: translates published lines (feed, overlays, recaps) into the reader's language, keeping names and numbers exactly.
A deterministic check runs after it: the translation must carry the same numbers as the original, or the original is kept."""
import json
import re

from agent_framework import Agent

from newsroom.agents import _run

LOCALIZER = """You are the newsroom's localizer. Translate each line in LINES into {lang} for a football audience, natural and idiomatic.
Keep every player name, team name and number exactly as written (minutes like 63' stay 63'; names stay in their Latin spelling). For
Chinese write Simplified Chinese as a football broadcaster would. Translate every line completely, add nothing and leave nothing out.
Reply only with a JSON list of strings, one per line, in the same order."""


def _nums(s):
    return sorted(re.findall(r"\d+(?:[.,]\d+)?", s.replace(",", ".")))


async def translate(client, lang, texts):
    """Translations for texts (the original where a translation lost or changed a number)."""
    agent = Agent(client, LOCALIZER.format(lang=lang), name="localizer")
    reply = (await _run(agent, "LINES: " + json.dumps(texts, ensure_ascii=False))).text
    m = re.search(r"\[.*\]", reply or "", flags=re.S)
    try:
        out = json.loads(m.group(0)) if m else []
    except ValueError:
        out = []
    if not isinstance(out, list) or len(out) != len(texts):
        return list(texts)
    return [t if isinstance(t, str) and _keeps(o, t) else o for o, t in zip(texts, out)]


WORDS = {"one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten", "eleven", "twelve", "once", "twice", "half"}


def _keeps(original, translated):
    """Every number of the original is in the translation; extra digits are allowed only where the original spelled a number out
    (Chinese writes five shots as 5次射门), so a translation can neither lose a number nor invent one."""
    a, b = _nums(original), _nums(translated)
    rest = list(b)
    for n in a:
        if n not in rest:
            return False
        rest.remove(n)
    spelled = sum(len(re.findall(rf"\b{w}\b", original, flags=re.I)) for w in WORDS)
    return len(rest) <= spelled
