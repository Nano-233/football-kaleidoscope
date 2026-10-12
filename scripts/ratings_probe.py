"""Player ratings over 16 simulated matches: distribution, won/drew/lost, each term of analytics.stats rating by role, keepers by goals conceded.

    python scripts/ratings_probe.py
"""
import sys, pathlib, numpy as np
from multiprocessing import Pool
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from matchsim.engine import Match
from matchsim.teams import make_team, Tactics
from analytics.stats import match_stats, StatsAccumulator

def one(seed):
    rng = np.random.default_rng(seed)
    home = make_team("KES", "4-3-3", Tactics(press=.6, line_height=.6, directness=.45), rng=rng)
    away = make_team("NBR", "4-4-2", Tactics(press=.4, line_height=.4, directness=.55, marking=.5), rng=rng)
    m = Match(home, away, seed=seed).run()
    st = match_stats(m); sc = m["meta"]["score"]
    rows = []
    for pid, p in st["players"].items():
        if p["rating"] is None or p["minutes"] < 30: continue
        res = np.sign(sc[p["team"]] - sc[1 - p["team"]])
        terms = StatsAccumulator.rating_terms(p, p["role"], p["minutes"], int(res))
        rows.append(dict(rating=p["rating"], role=p["role"], res=res, against=sc[1 - p["team"]], **terms,
                         lost=p["dispossessed"] + p["miscontrols"], dribbled=p["dribbled_past"], passes_failed=p["passes"] - p["passes_completed"], tackles_lost=p["tackles"] - p["tackles_won"], aerial_lost=p["aerial_lost"]))
    return rows

if __name__ == "__main__":
    with Pool(8) as pool:
        rows = sum(pool.map(one, range(101, 117)), [])
    R = np.array([r["rating"] for r in rows])
    print(f"players {len(R)}  mean {R.mean():.2f}  sd {R.std():.2f}  min {R.min()}  max {R.max()}  <6: {(R < 6).mean():.0%}  >=7.5: {(R >= 7.5).mean():.0%}")
    for k, name in ((1, 'won'), (0, 'drew'), (-1, 'lost')):
        sel = [r["rating"] for r in rows if r["res"] == k]; print(f"  {name}: mean {np.mean(sel):.2f} (n={len(sel)})")
    for role in ("GK", "CB", "FB", "DM", "CM", "AM", "WG", "ST"):
        sel = [r for r in rows if r["role"] == role]
        if sel: print(f"  {role}: rating {np.mean([r['rating'] for r in sel]):.2f} | " + " ".join(f"{k} {np.mean([r[k] for r in sel]):+.2f}" for k in ("attack", "passing", "carrying", "defending", "keeping", "conceded", "result", "discipline")) + " | per match: " + " ".join(f"{k} {np.mean([r[k] for r in sel]):.1f}" for k in ("lost", "dribbled", "passes_failed", "tackles_lost", "aerial_lost")))
    gk = [r for r in rows if r["role"] == "GK"]
    for c in sorted(set(r["against"] for r in gk)):
        sel = [r["rating"] for r in gk if r["against"] == c]; print(f"  GK conceded {c}: {np.mean(sel):.2f} (n={len(sel)})")
