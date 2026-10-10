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
    assert fr.shape[1] == 5 + 44 + 1 + 22 and np.all(np.diff(fr[:, 0]) > 0)
    pos = fr[:, 5:49].reshape(len(fr), 22, 2)
    on = pos[..., 1] > -5  # sent-off players are parked off the pitch
    assert pos[on][:, 0].min() >= -1.5 and pos[on][:, 0].max() <= L + 1.5 and pos[on][:, 1].min() >= -1.5 and pos[on][:, 1].max() <= W + 1.5
    subs = [e for e in a["events"] if e["type"] == "substitution"]
    assert len(subs) <= 10 and all(sum(e["team"] == t for e in subs) <= 5 for t in (0, 1))
    assert all(e["type"] != "shot" or e.get("why") is None or e["why"]["options"] for e in a["events"])
    ev = a["events"]
    for g in (e for e in ev if e["type"] == "goal"):  # every goal is a shot by the same man that was logged as scoring (a deflected or loose-ball goal included)
        last_shot = next((e for e in reversed(ev[:g["id"]]) if e["type"] == "shot"), None)
        assert last_shot is not None and last_shot["outcome"] == "goal" and last_shot["player"] == g["player"] and last_shot["xg"] == g["xg"]
    seen = set()
    for c in (e for e in ev if e["type"] == "card"):  # a second yellow follows a first, and nobody is booked after being sent off
        assert (c["card"] == "second_yellow") == (c["player"] in seen) or c["card"] == "red"
        seen.add(c["player"])
    for so in (e for e in ev if e["type"] == "sent_off"):
        assert not any(e.get("player") == so["player"] and e["type"] in ("pass", "shot", "tackle", "carry") for e in ev[so["id"] + 1:])


if __name__ == "__main__":
    test_deterministic_and_consistent()
    print("ok")
