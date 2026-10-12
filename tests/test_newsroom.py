"""Newsroom plumbing runs offline (scripted client) and the number check rejects an invented statistic.   python tests/test_newsroom.py"""
import asyncio
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from newsroom.agents import cover, unsupported_numbers, wrong_minutes  # noqa: E402
from newsroom.clients import ScriptedClient  # noqa: E402
from newsroom.moments import moments  # noqa: E402

match = json.load(open(pathlib.Path(__file__).resolve().parents[1] / "out" / "match_7.json", encoding="utf-8"))
packs = [p for p in moments(match) if p["moment"]["type"] == "goal"]
assert packs
item = asyncio.run(cover(ScriptedClient(), packs[0]))
assert item["approved"] and packs[0]["player"] in item["line"], item
assert unsupported_numbers("He has 999 shots.", packs[0]) == ["999"]
assert unsupported_numbers(f"{packs[0]['score']} after {packs[0]['moment']['clock']}", packs[0]) == []
print("newsroom ok")
assert unsupported_numbers("xG 0.07", {**packs[0], "xg": 0.072}) == []
assert wrong_minutes("in the 21st minute", {**packs[0], "moment": {**packs[0]["moment"], "clock": "22'"}}) == ["21"]
assert wrong_minutes("90+6' full time", {**packs[0], "moment": {**packs[0]["moment"], "clock": "90+6'"}}) == []
print("checks ok")
