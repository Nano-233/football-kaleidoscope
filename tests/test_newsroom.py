"""Newsroom plumbing runs offline (scripted client) and the number check rejects an invented statistic.   python tests/test_newsroom.py"""
import asyncio
import gzip
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from newsroom.agents import cover, unsupported_numbers, wrong_leader, wrong_minutes  # noqa: E402
from newsroom.clients import ScriptedClient  # noqa: E402
from newsroom.moments import moments  # noqa: E402

_SRV = pathlib.Path(__file__).resolve().parents[1] / "server" / "data" / "match_7.json.gz"
match = json.load(gzip.open(_SRV, "rt", encoding="utf-8")) if not (_OUT := pathlib.Path(__file__).resolve().parents[1] / "out" / "match_7.json").exists() else json.load(open(_OUT, encoding="utf-8"))  # the committed demo match when out/ is not there (CI)
packs = [p for p in moments(match) if p["moment"]["type"] in ("goal", "own_goal")]  # match_7 (engine-v5.1) has only an own goal
assert packs
item = asyncio.run(cover(ScriptedClient(), packs[0]))
assert item["approved"] and packs[0]["player"] in item["line"], item
assert unsupported_numbers("He has 999 shots.", packs[0]) == ["999"]
assert unsupported_numbers(f"{packs[0]['score']} after {packs[0]['moment']['clock']}", packs[0]) == []
print("newsroom ok")
assert unsupported_numbers("xG 0.07", {**packs[0], "xg": 0.072}) == []
assert wrong_minutes("in the 21st minute", {**packs[0], "moment": {**packs[0]["moment"], "clock": "22'"}}) == ["21"]
assert wrong_minutes("90+6' full time", {**packs[0], "moment": {**packs[0]["moment"], "clock": "90+6'"}}) == []
pk = {**packs[0], "teams": {"home": "Kestrel United", "away": "Northbridge Rovers"}, "leader": "Kestrel United"}
assert wrong_leader("Northbridge Rovers hold a 2-0 lead", pk) and not wrong_leader("Kestrel United lead 2-0", pk)
assert wrong_leader("Kestrel United trail", pk)
print("checks ok")
assert wrong_minutes("the spell from 15' to 30'", {**packs[0], "moment": {**packs[0]["moment"], "clock": "31'"}, "spell": {"start": "15'", "end": "30'"}}) == []
print("minute check ok")
from newsroom.agents import wrong_ppda, wrong_team  # noqa: E402
from newsroom.desk import _first_json_list  # noqa: E402
assert _first_json_list('Here [see]: ```json\n[{"change": {"press": 0.7}, "why": "x [y]"}]\n``` done') == [{"change": {"press": 0.7}, "why": "x [y]"}]
assert _first_json_list("no proposal") == []
assert wrong_ppda("sharper chance creation, highlighted by a lower ppda of 23.0", [9.1, 23.0])  # the slip seen live
assert not wrong_ppda("a lower PPDA of 9.1 against 23.0", [9.1, 23.0]) and not wrong_ppda("PPDA 23.0 is high: they sat off", [9.1, 23.0])
assert not wrong_ppda("a lower PPDA of 7.5 in the last 15 minutes", [9.1, 23.0])  # a window figure: not checked
assert not wrong_ppda("pressing was very low intensity, with a PPDA of 23.0", [9.1, 23.0]) and not wrong_ppda("PPDA 9.1, a high press", [9.1, 23.0])  # the word describes the press
assert wrong_ppda("PPDA 23.0 was low", [9.1, 23.0])
sq = {"Oakhaven FC": ["Ridor Novakley"], "Westmoor Wanderers": ["Arstor Vossez"]}
assert wrong_team("the red card to Oakhaven's Arstor Vossez at 26'", sq) == ["Arstor Vossez plays for Westmoor Wanderers, not Oakhaven FC"]
assert wrong_team("Westmoor's Arstor Vossez was sent off; Oakhaven FC's striker Ridor Novakley scored", sq) == []
print("team check ok")
from newsroom.desk import _until  # noqa: E402
early = _until(match, 60.)  # an early question sees nothing from later in the match
assert early["meta"]["score"] == [0, 0] and "ball_in_play" not in early["meta"] and "added_time" not in early["meta"]
assert all(a <= 60 and b <= 60 for a, b, _ in early["meta"]["dead_log"]) and all(e["t"] <= 60 for e in early["events"])
assert not match.get("stamina") or len(early["stamina"]) == 61
print("no spoilers ok")
from newsroom.agents import fan_jargon  # noqa: E402
assert len(fan_jargon("a low block 27.6 metres from goal, PPDA 12.2, xG 0.4")) == 3 and fan_jargon("a compact low block, 2-1 up at 74'") == []
assert fan_jargon("sitting 30 m deep") and not fan_jargon("the 4-1-4-1 shape held for 15 minutes")
print("fan words ok")
