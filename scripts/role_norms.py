"""Per-role per-90 averages over many simulated matches, for the scout's "against his role" comparison (analytics/role_norms.json).

    python scripts/role_norms.py [matches]
"""
import sys, json, pathlib
import numpy as np
from multiprocessing import Pool
ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from matchsim.engine import Match
from matchsim.teams import make_team, Tactics
from analytics.stats import match_stats

KEYS = ("passes", "passes_completed", "progressive_passes", "key_passes", "shots", "xg", "take_ons", "take_ons_won", "tackles", "tackles_won",
        "interceptions", "aerial_won", "aerial_lost", "dribbled_past", "dispossessed", "pressures", "recoveries", "fouls", "saves")


def one(seed):
    rng = np.random.default_rng(seed)
    h = make_team("KES", "4-3-3", Tactics(press=.6, line_height=.6, directness=.45), rng=rng)
    a = make_team("NBR", "4-4-2", Tactics(press=.4, line_height=.4, directness=.55, marking=.5), rng=rng)
    return [(p["role"], p["minutes"], {k: p.get(k, 0) for k in KEYS}) for p in match_stats(Match(h, a, seed=seed).run())["players"].values() if p["minutes"] >= 30]


if __name__ == "__main__":
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 24
    with Pool(12) as p:
        rows = sum(p.map(one, range(9500, 9500 + n)), [])
    out = {}
    for role in sorted({r[0] for r in rows}):
        sel = [r for r in rows if r[0] == role]
        mins = sum(r[1] for r in sel)
        out[role] = {k: round(sum(r[2][k] for r in sel) * 90 / mins, 2) for k in KEYS}
    (ROOT / "analytics" / "role_norms.json").write_text(json.dumps(out, indent=1))
    print({r: out[r]["passes"] for r in out})
