"""Do traits change behaviour? Per-90 stats for players with a trait vs same-role players without it.

    python scripts/trait_effect.py [N]
"""
import sys, pathlib, collections
from multiprocessing import Pool
import numpy as np
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from matchsim.engine import Match
from matchsim.teams import make_team, Tactics, LEAGUE, TRAITS

# trait -> (metric, description)
CHECK = {"long_shots": "shots", "poacher": "shots", "dribbler": "take_ons", "playmaker": "progressive", "safe_passer": "progressive",
         "hard_tackler": "fouls", "box_to_box": "km", "runner": "sprint_s", "target_man": "km"}


def one(seed):
    rng = np.random.default_rng(seed)
    a, b = rng.choice(list(LEAGUE), 2, replace=False)
    m = Match(make_team(a, "4-3-3", Tactics(), rng=rng), make_team(b, "4-4-2", Tactics(), rng=rng), seed=seed)
    r = m.run()
    meta = {p["id"]: p for tm in ("home", "away") for p in r["meta"][tm]["players"]}
    ev = r["events"]
    pos = np.array(r["frames"])[:, 5:].reshape(-1, 22, 2)
    sp = np.linalg.norm(np.diff(pos, axis=0), axis=2) / r["meta"]["dt"]
    sp[sp > 15] = 0
    out = []
    for i, pid in enumerate([p["id"] for tm in ("home", "away") for p in r["meta"][tm]["players"]]):
        p = meta[pid]
        mine = [e for e in ev if e.get("player") == pid]
        out.append(dict(role=p["role"], traits=p["traits"], shots=sum(e["type"] == "shot" for e in mine),
                        take_ons=sum(e["type"] == "take_on" for e in mine), fouls=sum(e["type"] == "foul" for e in mine),
                        progressive=sum(e["type"] == "pass" and e.get("progressive") for e in mine),
                        km=sp[:, i].sum() * r["meta"]["dt"] / 1000, sprint_s=(sp[:, i] > 7).sum() * r["meta"]["dt"]))
    return out


if __name__ == "__main__":
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 30
    with Pool(6) as p:
        rows = [x for r in p.map(one, range(500, 500 + n)) for x in r if x["role"] != "GK"]
    for tr, metric in CHECK.items():
        has = [x for x in rows if tr in x["traits"]]
        roles = {x["role"] for x in has}
        base = [x for x in rows if tr not in x["traits"] and x["role"] in roles]
        print(f"{tr:13s} {metric:11s} with trait {np.mean([x[metric] for x in has]):6.2f} (n={len(has):3d})   same roles without {np.mean([x[metric] for x in base]):6.2f}")
