"""Newsroom plumbing runs offline (scripted client) and the number check rejects an invented statistic.   python tests/test_newsroom.py"""
import asyncio
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from newsroom.agents import cover, unsupported_numbers  # noqa: E402
from newsroom.clients import ScriptedClient  # noqa: E402
from newsroom.moments import moments  # noqa: E402

match = json.load(open(pathlib.Path(__file__).resolve().parents[1] / "out" / "match_12.json", encoding="utf-8"))
packs = moments(match)
assert packs and packs[0]["moment"]["type"] == "goal"
item = asyncio.run(cover(ScriptedClient(), packs[0]))
assert item["approved"] and packs[0]["player"] in item["line"], item
assert unsupported_numbers("He has 7 shots.", packs[0]) == ["7"]
assert unsupported_numbers("1-0 after 10'", packs[0]) == []
print("newsroom ok")
