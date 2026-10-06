"""Simulate one match and write out/match_<seed>.json (events + tracking frames).

    python scripts/run_match.py [seed] [HOME] [AWAY]
"""
import json, sys, time, pathlib
import numpy as np
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from matchsim.engine import Match
from matchsim.teams import make_team, Tactics

seed = int(sys.argv[1]) if len(sys.argv) > 1 else 1
hc, ac = (sys.argv[2], sys.argv[3]) if len(sys.argv) > 3 else ("KES", "NBR")
rng = np.random.default_rng(seed)
home = make_team(hc, "4-3-3", Tactics(press=.6, line_height=.6, directness=.45), rng=rng)
away = make_team(ac, "4-4-2", Tactics(press=.4, line_height=.4, directness=.55, marking=.5), rng=rng)
t0 = time.time()
res = Match(home, away, seed=seed).run()
print(f"simulated in {time.time() - t0:.1f}s  score {res['meta']['score']}  events {len(res['events'])}  frames {len(res['frames'])}")
out = pathlib.Path(__file__).resolve().parents[1] / "out" / f"match_{seed}.json"
out.write_text(json.dumps(res, separators=(",", ":")))
print("wrote", out, f"{out.stat().st_size / 1e6:.1f} MB")
