"""The localizer: translates published lines (feed, overlays, recaps) into the reader's language, keeping names and numbers exactly.
A deterministic check runs after it: the translation must carry the same numbers as the original, or the original is kept."""
import json
import re

from agent_framework import Agent

from newsroom.agents import _run

LOCALIZER = """You are the newsroom's localizer. Translate each line in LINES into {lang} for a football audience, natural and idiomatic.
Keep every player name, team name and number exactly as written (minutes like 63' stay 63'; names stay in their Latin spelling). For
Chinese write Simplified Chinese as a football broadcaster would. Translate every line completely, add nothing and leave nothing out.
Short lines are the labels of a football match-centre app (tabs, buttons, headings): translate them as that app would, short
(Clips are video clips; Recap is the match report; Feed is the live news feed; Board is the tactics board; What if is a replay with a change).
LINES is a JSON object of numbered lines; reply only with a JSON object with the same numbers as keys and each translation as the value."""


def _nums(s):
    return sorted(re.findall(r"\d+(?:[.,]\d+)?", s.replace(",", ".")))


async def translate(client, lang, texts):
    """Translations for texts, None where a line came back missing or lost or changed a number: the caller shows the original
    for now and does not keep it, so the line is tried again. Lines are numbered, so one line the model drops or merges costs
    only that line, not the batch."""
    agent = Agent(client, LOCALIZER.format(lang=lang), name="localizer")
    reply = (await _run(agent, "LINES: " + json.dumps({str(i): t for i, t in enumerate(texts)}, ensure_ascii=False))).text
    m = re.search(r"\{.*\}", reply or "", flags=re.S)
    try:
        out = json.loads(m.group(0)) if m else {}
    except ValueError:
        out = {}
    if not isinstance(out, dict):
        out = {}
    return [t if isinstance(t := out.get(str(i)), str) and t.strip() and _keeps(o, t) else None for i, o in enumerate(texts)]


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
