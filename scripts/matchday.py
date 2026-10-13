"""A round of fixtures between the league's clubs, each club with its own way of playing, for the match centre's demo.

    python scripts/matchday.py            # writes out/<HOME>-<AWAY>.json for each fixture

For each fixture a few seeds are simulated and the one with the most goals is kept (a lively demo match; every seed is an ordinary
simulation of the same two teams). Then: python scripts/build_site.py out/RED-ASH.json ...
"""
import json
import pathlib
import sys

import numpy as np

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from matchsim.engine import Match  # noqa: E402
from matchsim.teams import Tactics, make_team  # noqa: E402

STYLE = {  # formation, tactics: how each club plays
    "RED": ("4-3-3", dict(press=.8, line_height=.72, directness=.3, risk=.55)),            # high press, keeps the ball
    "ASH": ("5-3-2", dict(press=.25, line_height=.25, directness=.72, compactness=.7)),   # deep block, breaks fast
    "HAR": ("3-5-2", dict(press=.45, line_height=.45, directness=.65)),                   # direct, two up front
    "STB": ("4-4-2", dict(press=.5, line_height=.45, directness=.78)),                     # long balls into the channels
    "WMW": ("3-4-3", dict(press=.72, line_height=.65, directness=.5, risk=.65)),          # aggressive, wing-backs high
    "OAK": ("4-2-3-1", dict(press=.5, line_height=.5, directness=.45)),                   # balanced
}
FIXTURES = [("RED", "ASH"), ("HAR", "STB"), ("WMW", "OAK")]
SEEDS = 4


def play(home, away, seed):
    rng = np.random.default_rng(seed)
    (fh, th), (fa, ta) = STYLE[home], STYLE[away]
    res = Match(make_team(home, fh, Tactics(**th), rng=rng), make_team(away, fa, Tactics(**ta), rng=rng), seed=seed).run()
    res["meta"]["recipe"] = dict(seed=seed, home=dict(code=home, formation=fh, tactics=th), away=dict(code=away, formation=fa, tactics=ta))  # how to rebuild it (matchsim/whatif.py)
    return res


if __name__ == "__main__":
    for i, (h, a) in enumerate(FIXTURES):
        best = max((play(h, a, 700 + 10 * i + k) for k in range(SEEDS)), key=lambda m: sum(m["meta"]["score"]))
        out = ROOT / "out" / f"{h}-{a}.json"
        out.write_text(json.dumps(best, separators=(",", ":")))
        print(out.name, best["meta"]["score"], flush=True)
