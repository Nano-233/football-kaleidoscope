"""Open-play crosses (passes from wide, more than 20 m off the middle, into the box) and what they lead to, real (Euro 2020 360 frames)
against simulated matches: reached a team-mate, a shot within 3 s, a big chance (xG >= 0.25 by the sim's formula), and who was in the
box when it was hit. Real big chances come from crosses at 0.35 per team-match, the sim's at 0.06.

    python scripts/cross_outcome.py [matches] [key=value ...]
"""
import sys, json, glob, pathlib, collections
import numpy as np
from multiprocessing import Pool
ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from matchsim.engine import Match, TUNE
from matchsim.teams import make_team, Tactics

KV = {k: float(v) for k, v in (a.split("=") for a in sys.argv[2:])}
CAM = bool(KV.pop("camera", 1))  # count simulated players only inside a real broadcast view, as the 360 frames do (scripts/camera.py)
_cam = None
L, W = 105., 68.
in_box = lambda x, y: x > L - 16.5 and abs(y - W / 2) < 20.16


def tally(c, att, opp, reached, shot_xg, shot_d=None, end=None):
    if end is not None:
        c[("end", 0 if end[0] > L - 5.5 else 1 if end[0] > L - 9 else 2 if end[0] > L - 12 else 3)] += 1
    if shot_d is not None:
        c["shot d"] += shot_d
        c[("shot d bin", 0 if shot_d < 6 else 1 if shot_d < 11 else 2 if shot_d < 16 else 3)] += 1
    c["n"] += 1
    c["att in box"] += sum(in_box(*p) for p in att)
    c["def in box"] += sum(in_box(*p) for p in opp)
    free = [p for p in att if in_box(*p) and min((np.hypot(*(o - p)) for o in opp), default=99) > 2]
    c["free att in box"] += len(free)
    c["reached"] += reached
    c["shot"] += shot_xg is not None
    c["big"] += shot_xg is not None and shot_xg >= .25


def real():
    c, files = collections.Counter(), 0
    for f in glob.glob(str(ROOT.parent / "reference_data" / "euro2020" / "[0-9]*_360.json")):
        files += 1
        ff = {r["event_uuid"]: r["freeze_frame"] for r in json.load(open(f, encoding="utf-8"))}
        ev = json.load(open(f.replace("_360", ""), encoding="utf-8"))
        for i, e in enumerate(ev):
            if e["type"]["name"] != "Pass" or "type" in e["pass"] or e["id"] not in ff:
                continue
            ox, oy = e["location"][0] * L / 120, e["location"][1] * W / 80
            ex, ey = e["pass"]["end_location"][0] * L / 120, e["pass"]["end_location"][1] * W / 80
            if abs(oy - W / 2) <= 20 or ox < L - 40 or not in_box(ex, ey):
                continue
            P = lambda p: np.array([p["location"][0] * L / 120, p["location"][1] * W / 80])
            fr = ff[e["id"]]
            shot_xg, shot_d = None, None
            for b in ev[i + 1:i + 8]:
                if b["type"]["name"] == "Shot" and b["team"]["id"] == e["team"]["id"]:
                    if b["id"] in ff:
                        bx, by = b["location"][0] * L / 120, b["location"][1] * W / 80
                        o2 = [P(p) for p in ff[b["id"]] if not p["teammate"] and not p["keeper"]]
                        shot_xg = Match._xg(None, np.array([bx, by]), o2, header=b["shot"].get("body_part", {}).get("name") == "Head")[0]
                    else:
                        shot_xg = b["shot"].get("statsbomb_xg", 0)
                    shot_d = float(np.hypot(L - b["location"][0] * L / 120, W / 2 - b["location"][1] * W / 80))
                    c["head"] += b["shot"].get("body_part", {}).get("name") == "Head"
                    break
                if b["type"]["name"] in ("Pass", "Clearance") and b["team"]["id"] != e["team"]["id"]:
                    break
            tally(c, [P(p) for p in fr if p["teammate"] and not p["actor"] and not p["keeper"]], [P(p) for p in fr if not p["teammate"] and not p["keeper"]],
                  "outcome" not in e["pass"], shot_xg, shot_d, (ex, ey))
    return c, files


def one(seed):
    TUNE.update(KV)
    rng = np.random.default_rng(seed)
    h = make_team("KES", "4-3-3", Tactics(press=.6, line_height=.6, directness=.45), rng=rng)
    a = make_team("NBR", "4-4-2", Tactics(press=.4, line_height=.4, directness=.55, marking=.5), rng=rng)
    m = Match(h, a, seed=seed).run()
    F, dt = np.asarray(m["frames"]), m["meta"]["dt"]
    gk = {s for s in range(22) if (m["meta"]["home"]["players"] + m["meta"]["away"]["players"])[s]["role"] == "GK"}
    ev, c = m["events"], collections.Counter()
    for i, e in enumerate(ev):
        if e["type"] not in ("pass", "cross") or e.get("source") not in (None, "open_play") or e.get("end_x") is None:
            continue
        flip = e["team"] == 1
        fl = (lambda x, y: np.array([L - x, W - y])) if flip else (lambda x, y: np.array([x, y]))
        o, en = fl(e["x"], e["y"]), fl(e["end_x"], e["end_y"])
        if abs(o[1] - W / 2) <= 20 or o[0] < L - 40 or not in_box(*en):
            continue
        f = F[min(int(round(e["t"] / dt)), len(F) - 1)]
        own, opp = (range(11, 22), range(11)) if flip else (range(11), range(11, 22))
        pos = lambda s: fl(*f[5 + 2 * s:7 + 2 * s])
        shot_xg, shot_d = None, None
        for b in ev[i + 1:i + 25]:
            if b["type"] == "shot" and b["team"] == e["team"] and b["t"] - e["t"] < 4:
                shot_xg = b.get("xg", 0)
                bx, by = fl(b["x"], b["y"]); shot_d = float(np.hypot(L - bx, W / 2 - by))
                c["head"] += b.get("body_part") == "head"
                break
            if b["type"] == "possession_change" or b["t"] - e["t"] > 4:
                break
        mates = [pos(s) for s in own if s not in gk and f[6 + 2 * s] > -5]
        oppl = [pos(s) for s in opp if s not in gk and f[6 + 2 * s] > -5]
        if CAM:
            global _cam
            if _cam is None:
                from scripts.camera import Camera
                _cam = Camera()
            poly = _cam.view(o, rng)
            mates, oppl = [q for q in mates if _cam.sees(poly, q)], [q for q in oppl if _cam.sees(poly, q)]
        tally(c, mates, oppl,
              e.get("outcome") == "complete", shot_xg, shot_d, en)
    return c


if __name__ == "__main__":
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 16
    with Pool(12) as p:
        sim = sum(p.map(one, range(9700, 9700 + n)), collections.Counter())
    rl, files = real()
    S, R = sim["n"], rl["n"]
    print(f"open-play crosses into the box per team-match: sim {S / n / 2:.1f} | real {R / files / 2:.1f}")
    for k, nm in (("reached", "reach a team-mate"), ("shot", "a shot within 3-4 s"), ("big", "a big chance")):
        print(f"  {nm:22} {sim[k] / S:.2f} | {rl[k] / R:.2f}")
    print(f"  shots: mean distance {sim['shot d'] / max(sim['shot'], 1):.1f} m | {rl['shot d'] / max(rl['shot'], 1):.1f} m;  headers {sim['head'] / max(sim['shot'], 1):.2f} | {rl['head'] / max(rl['shot'], 1):.2f};  by distance <6 / 6-11 / 11-16 / 16+: " + " ".join(f"{sim[('shot d bin', b)] / max(sim['shot'], 1):.2f}|{rl[('shot d bin', b)] / max(rl['shot'], 1):.2f}" for b in range(4)))
    print("  where the cross ends, metres from the goal line  <5.5 / 5.5-9 / 9-12 / 12+: " + " ".join(f"{sim[('end', b)] / S:.2f}|{rl[('end', b)] / R:.2f}" for b in range(4)))
    for k, nm in (("att in box", "attackers in the box"), ("free att in box", "  of them free (2 m)"), ("def in box", "defenders in the box")):
        print(f"  {nm:22} {sim[k] / S:.2f} | {rl[k] / R:.2f}   (when it is hit)")
