"""The simulator's foot use, against the real figures in scripts/foot_norms.py: each player's weak-foot share of open-play foot passes
(median 0.12, quartiles 0.08-0.17, 5 % two-footed), weak-foot share of open-play shots (0.24), completion strong vs weak (0.818 / 0.814).

    python scripts/foot_sim.py [matches] [key=value ...]
"""
import collections
import pathlib
import sys
from multiprocessing import Pool

import numpy as np

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from matchsim import engine  # noqa: E402
from matchsim.teams import Tactics, make_team  # noqa: E402

KV = {k: float(v) for k, v in (a.split("=") for a in sys.argv[2:])}


def one(seed):
    engine.TUNE.update(KV)
    rng = np.random.default_rng(seed)
    r = engine.Match(make_team("KES", "4-3-3", Tactics(), rng=rng), make_team("NBR", "4-4-2", Tactics(), rng=rng), seed=seed).run()
    pref = {p["id"]: p["foot"] for k in ("home", "away") for p in r["meta"][k]["players"] + r["meta"][k]["bench"]}
    ps, ss = [], []
    for e in r["events"]:
        if e["type"] in ("pass", "cross") and e.get("foot") and e.get("source") is None:
            ps.append((e["player"], e["foot"] != pref[e["player"]], e["outcome"] == "complete"))
        elif e["type"] == "shot" and e.get("foot") and e.get("kind") == "shot":
            ss.append((e["player"], e["foot"] != pref[e["player"]], e["outcome"] == "goal", e["xg"]))
    return ps, ss


if __name__ == "__main__":
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 24
    with Pool(min(n, 12)) as p:
        res = p.map(one, range(800, 800 + n))
    P = [x for r in res for x in r[0]]; S = [x for r in res for x in r[1]]
    by = collections.defaultdict(list)
    for pid, weak, _ in P:
        by[pid].append(weak)
    share = np.array([np.mean(v) for v in by.values() if len(v) >= 150])
    print(f"players with 150+ foot passes: {len(share)}; weak-foot share median {np.median(share):.2f} (real 0.12), quartiles {np.percentile(share, 25):.2f}-{np.percentile(share, 75):.2f} (0.08-0.17), two-footed {np.mean(share >= .35):.0%} (5%)")
    print(f"completion strong {np.mean([c for _, w, c in P if not w]):.3f}, weak {np.mean([c for _, w, c in P if w]):.3f} (real 0.818 / 0.814)")
    print(f"open-play shots off the weak foot {np.mean([w for _, w, _, _ in S]):.2f} (real 0.24); goals per xG strong {sum(g for _, w, g, _ in S if not w) / max(sum(x for _, w, _, x in S if not w), 1e-9):.2f}, weak {sum(g for _, w, g, _ in S if w) / max(sum(x for _, w, _, x in S if w), 1e-9):.2f} (real 0.92 / 1.08)")
