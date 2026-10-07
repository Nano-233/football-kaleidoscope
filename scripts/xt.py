"""Expected Threat (xT) surface from real events: how much a possession is worth at each spot, as a probability of scoring soon.

    python scripts/xt.py            # reads StatsBomb open data from ../reference_data/pl1516, writes reference/xt_grid.json (aggregate only)

The surface is the standard xT construction (Singh 2018): on a grid, the value of holding the ball in a cell is
    xT(c) = P(shoot | c) * P(goal | shot from c)  +  P(move | c) * sum over c' of P(c -> c' | move) * xT(c')
where a move is a pass or carry (a failed move ends the possession with nothing, so it only appears in the denominator).
Solved by iteration. It is used by the simulator's decision utilities as the value of where the ball is: a pass is worth the
chance it is completed times the value of where it lands, a lost ball costs what the possession was worth. Nothing in it is a
tuned stat: it is the empirical football value of the pitch, and risk-taking in the final third follows from it
(a 30 % ball into the box is worth it because the box is worth far more than the edge of it).
"""
import sys, glob, json, pathlib
import numpy as np

ROOT = pathlib.Path(__file__).resolve().parents[1]
L, W = 105., 68.
NX, NY = 16, 12
SB = np.array([105 / 120, 68 / 80])


def cell(x, y):
    return int(np.clip(x / L * NX, 0, NX - 1)), int(np.clip(y / W * NY, 0, NY - 1))


def build(files):
    shots = np.zeros((NX, NY)); goals = np.zeros((NX, NY)); moves = np.zeros((NX, NY)); trans = np.zeros((NX, NY, NX, NY))
    for f in files:
        ev = json.load(open(f, encoding="utf-8"))
        if not isinstance(ev, list) or not ev or "type" not in ev[0]:
            continue
        for e in ev:
            ty = e["type"]["name"]
            if ty == "Shot" and e["shot"]["type"]["name"] != "Penalty":
                x, y = np.array(e["location"][:2]) * SB
                i, j = cell(x, y)
                shots[i, j] += 1
                goals[i, j] += e["shot"]["outcome"]["name"] == "Goal"
            elif ty == "Pass":
                p = e["pass"]
                if p.get("type", {}).get("name") in ("Corner", "Free Kick", "Throw-in", "Goal Kick", "Kick Off"):
                    continue
                x, y = np.array(e["location"][:2]) * SB
                i, j = cell(x, y)
                moves[i, j] += 1
                if "outcome" not in p:
                    ex, ey = np.array(p["end_location"]) * SB
                    a, b = cell(ex, ey)
                    trans[i, j, a, b] += 1
            elif ty == "Carry":
                x, y = np.array(e["location"][:2]) * SB
                ex, ey = np.array(e["carry"]["end_location"]) * SB
                if np.hypot(ex - x, ey - y) < 3:
                    continue
                i, j = cell(x, y)
                moves[i, j] += 1
                a, b = cell(ex, ey)
                trans[i, j, a, b] += 1
    return shots, goals, moves, trans


def solve(shots, goals, moves, trans, iters=60):
    tot = shots + moves
    ps = np.where(tot > 0, shots / np.maximum(tot, 1), 0.)
    pm = 1 - ps
    pg = np.where(shots > 0, goals / np.maximum(shots, 1), 0.)
    # smooth the goal probability with a distance prior where there are few shots
    X, Y = np.meshgrid((np.arange(NX) + .5) * L / NX, (np.arange(NY) + .5) * W / NY, indexing="ij")
    d = np.hypot(L - X, Y - W / 2)
    prior = np.clip(.45 * np.exp(-d / 11.), 0.002, .6)
    k = 8.
    pg = (goals + k * prior) / (shots + k)
    T = trans / np.maximum(moves[..., None, None], 1)  # includes failed moves: rows sum to the completion rate
    xt = np.zeros((NX, NY))
    for _ in range(iters):
        xt = ps * pg + pm * (T * xt[None, None]).sum((2, 3))
    return xt, ps, pg


if __name__ == "__main__":
    d = pathlib.Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT.parent / "reference_data" / "pl1516"
    files = sorted(glob.glob(str(d / "*.json")))
    shots, goals, moves, trans = build(files)
    xt, ps, pg = solve(shots, goals, moves, trans)
    out = ROOT / "reference" / "xt_grid.json"
    out.write_text(json.dumps(dict(nx=NX, ny=NY, pitch=[L, W], source="StatsBomb open data, Premier League 2015/16 (aggregates only)", xt=np.round(xt, 5).tolist(),
                                   p_shoot=np.round(ps, 4).tolist(), p_goal=np.round(pg, 4).tolist())))
    print("matches", len(files), "shots", int(shots.sum()), "moves", int(moves.sum()))
    np.set_printoptions(linewidth=200, precision=3, suppress=True)
    print("xT by cell (rows = x from own goal to opponent's goal, columns = y):")
    print(xt[::2, ::2][:, :6] if False else (xt * 100).round(1)[:, 5:7])
    print("centre column, x from own goal line to the opponent's:", (xt[:, NY // 2] * 100).round(2))
