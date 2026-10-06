"""Compare simulated matches with rough real-football norms (the gap list), per match, both teams combined.

    python scripts/realism.py [N]            style, wide play, restarts, pressing, officiating (random fixtures)
    python scripts/realism.py --home [N]     home advantage: equal-quality teams
    python scripts/realism.py --balance [N]  competitive balance: home side ~3 quality points stronger

Norms are rough public aggregates from memory, used as tuning targets, not redistributed data.
"""
import sys, pathlib, collections
from multiprocessing import Pool
import numpy as np
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from matchsim.engine import Match
from matchsim.teams import make_team, Tactics, LEAGUE, L, W

BANDS = {
    "pass_fwd": (.30, .45), "pass_back": (.18, .32), "pass_len": (16.5, 21), "long_share": (.06, .13), "through": (.005, .03),
    "open_crosses": (25, 50), "head_share": (.10, .20), "box_share": (.66, .82), "penalties": (.15, .45),
    "ball_in_play": (.55, .68), "pressures": (200, 450), "ppda": (8, 14), "interceptions": (14, 30), "aerial_duels": (50, 110),
    "goals": (2.4, 3.1), "shots": (20, 30), "shots_ST": (.3, .5), "shots_mid": (.18, .35), "shots_CB": (.02, .1), "pass_per_poss": (3.5, 6.5),
}


def one(args):
    seed, mode = args
    rng = np.random.default_rng(seed)
    a, b = rng.choice(list(LEAGUE), 2, replace=False)
    qa = qb = None
    if mode == "home":
        qa = qb = 13.0
    elif mode == "balance":
        qa, qb = 14.5, 11.5
    f1, f2 = rng.choice(["4-3-3", "4-4-2", "4-2-3-1"]), rng.choice(["4-3-3", "4-4-2", "4-2-3-1", "3-5-2"])
    m = Match(make_team(a, f1, Tactics(*rng.uniform(.3, .7, 8)), quality=qa, rng=rng), make_team(b, f2, Tactics(*rng.uniform(.3, .7, 8)), quality=qb, rng=rng), seed=seed)
    return stats(m, m.run())


def stats(m, r):
    ev = r["events"]
    c = collections.Counter(e["type"] for e in ev)
    passes = [e for e in ev if e["type"] == "pass" and e.get("outcome")]
    n = max(len(passes), 1)
    shots = [e for e in ev if e["type"] == "shot"]
    xt = lambda e: e["x"] if e["team"] == 0 else L - e["x"]
    in_box = lambda e: xt(e) > L - 16.5 and abs(e["y"] - W / 2) < 20.2
    ppda = []
    for t in (0, 1):
        opp = sum(1 for e in passes if e["team"] == 1 - t and xt(e) < .6 * L)
        act = sum(1 for e in ev if e["type"] in ("tackle", "interception", "foul") and e.get("team") == t and xt(e) > .4 * L)
        ppda.append(opp / max(act, 1))
    roles = {p["id"]: p["role"] for tm in ("home", "away") for p in r["meta"][tm]["players"] + r["meta"][tm]["bench"]}
    role_share = lambda evs, ro: np.mean([roles[e["player"]] == ro for e in evs]) if evs else 0
    return dict(
        shots_ST=role_share(shots, "ST"), shots_mid=sum(role_share(shots, ro) for ro in ("CM", "DM", "AM")), shots_CB=role_share(shots, "CB"), pass_per_poss=len(passes) / max(c["possession_change"], 1),
        pass_fwd=sum(e["gain"] > 5 for e in passes) / n, pass_back=sum(e["gain"] < -5 for e in passes) / n,
        pass_len=np.mean([e["length"] for e in passes]), long_share=sum(e["length"] > 32 for e in passes) / n,
        through=sum(e["technique"] == "through_ball" for e in passes) / n,
        open_crosses=sum(1 for e in ev if e["type"] == "cross" and e.get("source") == "open_play"),
        head_share=np.mean([e["body_part"] == "head" for e in shots]) if shots else 0, box_share=np.mean([in_box(e) for e in shots]) if shots else 0,
        penalties=c["penalty"], ball_in_play=r["meta"]["ball_in_play"], pressures=c["pressure"], ppda=np.mean(ppda),
        interceptions=c["interception"], aerial_duels=c["duel"], goals=sum(r["meta"]["score"]), shots=len(shots), score=tuple(r["meta"]["score"]))


if __name__ == "__main__":
    args = sys.argv[1:]
    mode = "home" if "--home" in args else "balance" if "--balance" in args else "mix"
    nums = [a for a in args if a.isdigit()]
    n = int(nums[0]) if nums else (100 if mode != "mix" else 48)
    with Pool(6) as p:
        rows = p.map(one, [(1000 + i, mode) for i in range(n)])
    sc = np.array([r["score"] for r in rows])
    if mode == "home":
        print(f"{n} matches, equal teams: home win {np.mean(sc[:, 0] > sc[:, 1]):.2f}  draw {np.mean(sc[:, 0] == sc[:, 1]):.2f}  away win {np.mean(sc[:, 0] < sc[:, 1]):.2f}   (real ~0.44 / 0.25 / 0.31)   home goals {sc[:, 0].mean():.2f} away {sc[:, 1].mean():.2f}")
    elif mode == "balance":
        print(f"{n} matches, home ~3 quality points stronger: mean goal diff {np.mean(sc[:, 0] - sc[:, 1]):+.2f}  stronger side wins {np.mean(sc[:, 0] > sc[:, 1]):.2f}  draws {np.mean(sc[:, 0] == sc[:, 1]):.2f}   (real: roughly +0.9, 0.55-0.65 wins)")
    else:
        print(f"{n} matches")
        for k, (lo, hi) in BANDS.items():
            v = np.mean([r[k] for r in rows])
            print(f"  {k:14s} {v:8.3f}   target {lo}-{hi}   {'ok' if lo <= v <= hi else 'OFF'}")
        print(f"  goals/match variance {sc.sum(1).var():.2f}  0-0 share {np.mean((sc == 0).all(1)):.2f} (real ~0.07)  draw share {np.mean(sc[:, 0] == sc[:, 1]):.2f} (real ~0.25)")
