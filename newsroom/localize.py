"""The localizer: translates published lines (feed, overlays, recaps) into the reader's language, keeping names and numbers exactly.
A deterministic check runs after it: the translation must carry the same numbers as the original, or the original is kept."""
import json
import re

from agent_framework import Agent

from newsroom.agents import _run

LOCALIZER = """You are the newsroom's localizer. Translate each line in LINES into {lang} for a football audience, natural and idiomatic.
Keep every player name, team name and number exactly as written (minutes like 63' stay 63'). Reply only with a JSON list of strings, one per
line, in the same order."""


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
    return [t if isinstance(t, str) and _nums(t) == _nums(o) else o for o, t in zip(texts, out)]
