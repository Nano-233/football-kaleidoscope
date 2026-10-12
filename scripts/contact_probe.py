"""How often a receiver is tackled or dispossessed within 1.5 s, by how tight he was marked when the ball arrived, real (Euro 2020 360 freeze
frames) against simulated matches; plus tackles and take-ons per team-match.

    python scripts/contact_probe.py [matches] [key=value ...]     # TUNE overrides for the simulated side

Real (computed once, 1.5 s window, opponent tackle duel or dispossession): tight (<1.75 m) 0.050, 1.75-4 m 0.024, free 0.002.
"""
import sys, pathlib, numpy as np
from multiprocessing import Pool
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from matchsim.engine import Match, TUNE
from matchsim.teams import make_team, Tactics

REAL = {"tight": .050, "mid": .024, "free": .002}
KV = {k: float(v) for k, v in (a.split("=") for a in sys.argv[2:])}


def one(seed):
    TUNE.update(KV)
    rng = np.random.default_rng(seed)
    h = make_team("KES", "4-3-3", Tactics(press=.6, line_height=.6, directness=.45), rng=rng)
    a = make_team("NBR", "4-4-2", Tactics(press=.4, line_height=.4, directness=.55, marking=.5), rng=rng)
    m = Match(h, a, seed=seed).run()
    F, dt, ev = np.array(m["frames"]), m["meta"]["dt"], m["events"]
    res = {k: [0, 0] for k in REAL}
    for i, e in enumerate(ev):
        if e["type"] != "ball_receipt":
            continue
        fr, o0 = F[round(e["t"] / dt)], 11 * (1 - e["team"])
        opp = np.array([[fr[5 + 2 * j], fr[6 + 2 * j]] for j in range(o0 + 1, o0 + 11)])
        d = np.linalg.norm(opp - [e["x"], e["y"]], axis=1).min()
        k = "tight" if d < 1.75 else "mid" if d < 4 else "free"
        hit = False
        for x in ev[i + 1:i + 15]:
            if x["t"] - e["t"] > 1.5 or (x["type"] == "take_on" and x.get("player") == e["player"]):
                break
            hit |= (x["type"] == "tackle" and x.get("target") == e["player"]) or (x["type"] == "dispossessed" and x.get("player") == e["player"])
        res[k][0] += 1; res[k][1] += hit
    return res, sum(e["type"] == "tackle" for e in ev) / 2, sum(e["type"] == "take_on" for e in ev) / 2, sum(e["type"] == "foul" for e in ev) / 2


if __name__ == "__main__":
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 12
    with Pool(12) as p:
        out = p.map(one, range(8000, 8000 + n))
    tot = {k: [sum(o[0][k][0] for o in out), sum(o[0][k][1] for o in out)] for k in REAL}
    print("tackled within 1.5 s of receipt (sim | real):", "  ".join(f"{k} {v[1] / max(v[0], 1):.3f} | {REAL[k]:.3f}" for k, v in tot.items()))
    print(f"per team-match: tackles {np.mean([o[1] for o in out]):.1f} (real 20.5)  take-ons {np.mean([o[2] for o in out]):.1f} (17.5)  fouls {np.mean([o[3] for o in out]):.1f} (12.0)")
