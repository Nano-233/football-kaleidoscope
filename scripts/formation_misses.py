"""Why the formation reader misses changes and raises false alarms, over fresh matches: for every true change, the shapes read
in the 5-minute windows before and after it (with the ball); for every false alarm, the nearest logged tactic change, red card
or substitution. Tallies the pairs (true change, what was read) and the false alarms by layout.

    python scripts/formation_misses.py [matches]
"""
import collections
import pathlib
import sys
from multiprocessing import Pool

import numpy as np

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from analytics.tools import MatchView  # noqa: E402
from matchsim.engine import Match  # noqa: E402
from matchsim.teams import Tactics, make_team  # noqa: E402


def one(seed):
    rng = np.random.default_rng(seed)
    h = make_team("KES", "4-3-3", Tactics(press=.6, line_height=.6, directness=.45), rng=rng)
    a = make_team("NBR", "4-4-2", Tactics(press=.4, line_height=.4, directness=.55, marking=.5), rng=rng)
    m = Match(h, a, seed=seed).run()
    mv = MatchView(m)
    out = []
    for tm in (0, 1):
        tl = mv.formation_timeline(tm)
        read = [c for c in tl["changes"] if c["phase"] == "in_possession"]
        sc = mv.score_reading(tm, read)
        tr = mv.truth(tm)
        wins = [(w["t"], w["in_possession"]) for w in tl["by_window"]]
        for d, tc in zip(sc["detail"], tr["formation_changes"]):
            before = [f for t, f in wins if tc["t"] - 420 <= t < tc["t"] - 60]
            after = [f for t, f in wins if tc["t"] + 60 <= t < tc["t"] + 600]
            out.append(("change", tm, tc["previous"], tc["formation"], d["found"], collections.Counter(before).most_common(1), collections.Counter(after).most_common(2), tc["reason"]))
        used = {i for i, rc in enumerate(read) for tc in tr["formation_changes"] if mv.same_shape(rc["formation"], tc["formation"]) and -210 <= rc["t"] - tc["t"] <= 600}
        evs = [e for e in m["events"] if e.get("team") == tm and e["type"] in ("tactic_change", "substitution", "sent_off")] + \
              [e for e in m["events"] if e["type"] == "sent_off"]
        for i, rc in enumerate(read):
            if i in used:
                continue
            near = min(evs, key=lambda e: abs(e["t"] - rc["t"]), default=None)
            out.append(("false", tm, rc["previous"], rc["formation"], near["type"] if near and abs(near["t"] - rc["t"]) < 300 else "nothing within 5 min",
                        mv.truth(tm)["starting"], round(rc["t"] / 60)))
    return out


if __name__ == "__main__":
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 24
    with Pool(12) as p:
        rows = sum(p.map(one, range(9300, 9300 + n)), [])
    ch = [r for r in rows if r[0] == "change"]
    print(f"true changes {len(ch)}, found {sum(r[4] for r in ch)}")
    print("missed (previous -> true | read before | read after | reason):")
    for k, v in collections.Counter((r[2], r[3], str(r[5]), str(r[6]), r[7]) for r in ch if not r[4]).most_common(25):
        print(f"  {v} x  {k[0]} -> {k[1]}  | before {k[2]} | after {k[3]} | {k[4]}")
    fa = [r for r in rows if r[0] == "false"]
    print(f"false alarms {len(fa)} (read previous -> read | nearest event | the team's set system):")
    for k, v in collections.Counter((r[2], r[3], r[4], r[5]) for r in fa).most_common(20):
        print(f"  {v} x  {k[0]} -> {k[1]}  | {k[2]} | set {k[3]}")
