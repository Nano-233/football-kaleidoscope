"""The analysis toolkit agrees with the stats layer and reads the starting shapes.   python tests/test_tools.py"""
import gzip
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from analytics.stats import match_stats  # noqa: E402
from analytics.tools import MatchView  # noqa: E402

_SRV = pathlib.Path(__file__).resolve().parents[1] / "server" / "data" / "match_7.json.gz"
match = json.load(gzip.open(_SRV, "rt", encoding="utf-8")) if not (_OUT := pathlib.Path(__file__).resolve().parents[1] / "out" / "match_7.json").exists() else json.load(open(_OUT, encoding="utf-8"))  # the committed demo match when out/ is not there (CI)
mv = MatchView(match)
teams = match_stats(match)["teams"]
assert mv.momentum()["cumulative_xg"] == [round(teams[0]["xg"], 2), round(teams[1]["xg"], 2)]
assert [mv.pressing(t)["match_ppda"] for t in (0, 1)] == [round(t["ppda"], 1) for t in teams]
for tm in (0, 1):
    assert mv.formation_timeline(tm)["starting"]["in_possession"] == mv.truth(tm)["starting"]
    assert "out_of_possession" in mv.formation_timeline(tm)["by_window"][0]
pid = match["meta"]["home"]["players"][9]["id"]
prof = mv.player_profile(pid)
assert prof["physical"]["distance_km"] > 5 and prof["heat_map_6x4"] and prof["per90_vs_role"]
shots = mv.find_moments(player=pid, types=["shot"])["count"]
assert shots == sum(1 for e in match["events"] if e["type"] == "shot" and e.get("player") == pid)
goal = next(e for e in match["events"] if e["type"] == "goal")
assert mv.sequence(goal["id"])["steps"][-1]["type"] == "goal"
# duties: shares are shares, and the reader beats chance against the engine's job log (a third of the frames would be a coin among three)
for tm in (0, 1):
    du = mv.duties(tm)
    assert du["players"] and all(0 <= q["press"] <= 1 and 0 <= q["mark"] <= 1 and q["press"] + q["mark"] <= 1 for q in du["players"].values())
    if match.get("jobs"):
        assert mv.score_duties(tm)["frame_agreement"] > .5
# rhythm: shares add up inside each window; milestones: a hat-trick needs a brace first, every goal count matches the events
rh = mv.rhythm()
assert rh["by_window"] and all(r["settled_pct"] + r["broken_pct"] <= 100 for r in rh["by_window"])
ms = mv.milestones()
for r in ms:
    if r["text"] in ("brace", "hat-trick"):
        assert sum(1 for e in match["events"] if e["type"] == "goal" and e.get("player") == r["player"] and e["t"] <= r["t"]) == (2 if r["text"] == "brace" else 3)
print("tools ok")
