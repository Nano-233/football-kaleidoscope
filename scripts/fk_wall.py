"""Direct free kicks: is there a wall when the kick is taken (defenders near the spot 9.15 m along the line to goal) and is anyone encroaching.

    python scripts/fk_wall.py out/match_1.json
"""
import json, sys, numpy as np
m = json.load(open(sys.argv[1])); F = np.array(m["frames"]); dt = m["meta"]["dt"]; L, W = 105, 68
ev = m["events"]
for i, e in enumerate(ev):
    if e["type"] != "free_kick" or e["distance_to_goal"] > 32: continue
    t, att = e["t"], e["team"]
    nxt = next(x for x in ev[i+1:] if x["type"] in ("pass", "shot", "cross"))
    gx = L if att == 0 else 0.  # attacking goal
    B = np.array([e["x"], e["y"]]); u = (np.array([gx, W/2]) - B); u /= np.linalg.norm(u); wc = B + u * 9.15
    k = round((nxt["t"] - .2) / dt)  # just before the kick is taken
    fr = F[k]; d0 = 11 * (1 - att)
    defs = np.array([[fr[5 + 2*j], fr[6 + 2*j]] for j in range(d0, d0 + 11)])
    near_wall = np.linalg.norm(defs - wc, axis=1)
    near_ball = np.linalg.norm(defs - B, axis=1)
    print(f"{e['clock']:>6} dist {e['distance_to_goal']:5.1f}  next={nxt['type']:5} after {nxt['t']-t:4.1f}s | defenders within 1.5 m of the central wall spot: {(near_wall < 1.5).sum()}, within 3 m: {(near_wall < 3).sum()} | nearest to ball {near_ball.min():.1f} m")
