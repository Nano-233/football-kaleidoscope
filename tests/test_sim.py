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
    goals = [e for e in a["events"] if e["type"] == "goal"]
    assert [sum(e["team"] == t for e in goals) for t in (0, 1)] == a["meta"]["score"]
    fr = np.array(a["frames"])
    assert fr.shape[1] == 5 + 44 and np.all(np.diff(fr[:, 0]) > 0)
    pos = fr[:, 5:].reshape(len(fr), 22, 2)
    assert pos[..., 0].min() >= -1.5 and pos[..., 0].max() <= L + 1.5 and pos[..., 1].min() >= -1.5 and pos[..., 1].max() <= W + 1.5
    assert all(e["type"] != "shot" or e.get("why") is None or e["why"]["options"] for e in a["events"])


if __name__ == "__main__":
    test_deterministic_and_consistent()
    print("ok")
