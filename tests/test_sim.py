"""Smoke + invariants for the engine. Run: python -m pytest tests  (or python tests/test_sim.py)"""
import sys, pathlib
import numpy as np
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from matchsim.engine import Match
from matchsim.teams import make_team, Tactics, L, W


def play(seed):
    rng = np.random.default_rng(seed)
    return Match(make_team("KES", "4-3-3", Tactics(), rng=rng), make_team("NBR", "4-4-2", Tactics(), rng=rng), seed=seed).run()


def test_deterministic_and_consistent():
    a, b = play(7), play(7)
    assert a["events"] == b["events"] and a["frames"] == b["frames"], "same seed must give the same match"
    goals = [e for e in a["events"] if e["type"] in ("goal", "own_goal")]  # team = team credited with the goal
    assert [sum(e["team"] == t for e in goals) for t in (0, 1)] == a["meta"]["score"]
    fr = np.array(a["frames"])
    assert fr.shape[1] == 5 + 44 and np.all(np.diff(fr[:, 0]) > 0)
    pos = fr[:, 5:].reshape(len(fr), 22, 2)
    on = pos[..., 1] > -5  # sent-off players are parked off the pitch
    assert pos[on][:, 0].min() >= -1.5 and pos[on][:, 0].max() <= L + 1.5 and pos[on][:, 1].min() >= -1.5 and pos[on][:, 1].max() <= W + 1.5
    subs = [e for e in a["events"] if e["type"] == "substitution"]
    assert len(subs) <= 10 and all(sum(e["team"] == t for e in subs) <= 5 for t in (0, 1))
    assert all(e["type"] != "shot" or e.get("why") is None or e["why"]["options"] for e in a["events"])


if __name__ == "__main__":
    test_deterministic_and_consistent()
    print("ok")
