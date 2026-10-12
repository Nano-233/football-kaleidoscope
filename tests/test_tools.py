"""The analysis toolkit agrees with the stats layer and reads the starting shapes.   python tests/test_tools.py"""
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from analytics.stats import match_stats  # noqa: E402
from analytics.tools import MatchView  # noqa: E402

match = json.load(open(ROOT / "out" / "match_7.json", encoding="utf-8"))
mv = MatchView(match)
teams = match_stats(match)["teams"]
assert mv.momentum()["cumulative_xg"] == [round(teams[0]["xg"], 2), round(teams[1]["xg"], 2)]
for tm in (0, 1):
    assert mv.formation_timeline(tm)["starting"] == mv.truth(tm)["starting"]
    assert "out_of_possession" in mv.formation_timeline(tm)["by_window"][0]
pid = match["meta"]["home"]["players"][9]["id"]
prof = mv.player_profile(pid)
assert prof["physical"]["distance_km"] > 5 and prof["heat_map_6x4"] and prof["per90_vs_role"]
shots = mv.find_moments(player=pid, types=["shot"])["count"]
assert shots == sum(1 for e in match["events"] if e["type"] == "shot" and e.get("player") == pid)
goal = next(e for e in match["events"] if e["type"] == "goal")
assert mv.sequence(goal["id"])["steps"][-1]["type"] == "goal"
print("tools ok")
