"""Stats are pure reducers over events: totals must agree with the events and the score."""
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import numpy as np
from matchsim.engine import Match
from matchsim.teams import make_team, Tactics
from analytics.stats import match_stats, StatsAccumulator


def test_stats_consistent():
    rng = np.random.default_rng(11)
    m = Match(make_team("KES", "4-3-3", Tactics(), rng=rng), make_team("NBR", "4-4-2", Tactics(), rng=rng), seed=11).run()
    s = match_stats(m)
    assert [t.get("goals", 0) for t in s["teams"]] == m["meta"]["score"]
    ev = m["events"]
    assert sum(t.get("shots", 0) for t in s["teams"]) == sum(e["type"] == "shot" for e in ev)
    assert sum(p.get("passes", 0) for p in s["players"].values()) == sum(e["type"] in ("pass", "cross") for e in ev)
    # incremental == batch
    acc = StatsAccumulator(m["meta"])
    for e in ev[: len(ev) // 2]:
        acc.update(e)
    half = acc.teams()[0].get("passes", 0)
    for e in ev[len(ev) // 2:]:
        acc.update(e)
    assert acc.teams() == s["teams"] and half <= s["teams"][0]["passes"]
    assert all(0 < p["minutes"] <= 115 for p in s["players"].values() if p["minutes"])
    # pass quality and shot speed: hard passes are a subset, difficulty is an average of 0-1 values, the top shot is the fastest
    for t in s["teams"]:
        assert t["hard_passes_completed"] <= t["hard_passes"] <= t["passes"] and 0 <= t["avg_pass_difficulty"] <= 1
    shots = [e["speed"] * 3.6 for e in ev if e["type"] == "shot" and e["team"] == 0]
    assert not shots or abs(s["teams"][0]["top_shot_speed_kmh"] - max(shots)) < .1


if __name__ == "__main__":
    test_stats_consistent()
    print("ok")
