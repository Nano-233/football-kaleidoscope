"""What happens in a real match that the simulator does not produce (or produces far too rarely)?

    python scripts/inventory.py [real_matches] [sim_matches]

Counts every StatsBomb event type and the sub-types that matter (pass height and body part, clearance body part, duel kinds,
goalkeeper actions, dribbled past, 50/50, shield, errors, ...) per team per match, and the simulator's closest equivalents.
Rows where the simulator is missing or far off are marked. Aggregates only; raw data stays outside the repository.
"""
import sys, glob, json, pathlib, collections
import numpy as np
ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "scripts"))
L, W = 105., 68.
SB = np.array([105 / 120, 68 / 80])


def real_counts(n):
    c = collections.Counter()
    files = sorted(glob.glob(str(ROOT.parent / "reference_data" / "pl1516" / "[0-9]*.json")))[:n]
    for f in files:
        for e in json.load(open(f, encoding="utf-8")):
            ty = e["type"]["name"]
            c["E:" + ty] += 1
            if ty == "Pass":
                p = e["pass"]
                c["pass height: " + p.get("height", {}).get("name", "?")] += 1
                c["pass body: " + p.get("body_part", {}).get("name", "?")] += 1
                if p.get("type"):
                    c["pass type: " + p["type"]["name"]] += 1
                if p.get("technique"):
                    c["pass technique: " + p["technique"]["name"]] += 1
                for k in ("cross", "cut_back", "switch", "through_ball", "shot_assist", "goal_assist", "aerial_won", "deflected", "miscommunication"):
                    if p.get(k):
                        c["pass flag: " + k] += 1
                if p.get("type", {}).get("name") == "Goal Kick":
                    c["goal kick: long (>=35 m)" if p["length"] * 105 / 120 >= 35 else "goal kick: short"] += 1
                    c["goal kick height: " + p.get("height", {}).get("name", "?")] += 1
                if p.get("outcome"):
                    c["pass outcome: " + p["outcome"]["name"]] += 1
            elif ty == "Clearance":
                cl = e.get("clearance", {})
                c["clearance body: " + cl.get("body_part", {}).get("name", "?")] += 1
                if cl.get("aerial_won"):
                    c["clearance aerial won"] += 1
            elif ty == "Duel":
                d = e.get("duel", {})
                c["duel: " + d.get("type", {}).get("name", "?") + " / " + d.get("outcome", {}).get("name", "-")] += 1
            elif ty == "Goal Keeper":
                g = e.get("goalkeeper", {})
                c["gk: " + g.get("type", {}).get("name", "?")] += 1
            elif ty == "Shot":
                c["shot body: " + e["shot"].get("body_part", {}).get("name", "?")] += 1
                c["shot technique: " + e["shot"].get("technique", {}).get("name", "?")] += 1
            elif ty == "Ball Recovery":
                c["recovery: " + ("failed" if e.get("ball_recovery", {}).get("recovery_failure") else "won")] += 1
            elif ty == "Dribble":
                c["dribble: " + e.get("dribble", {}).get("outcome", {}).get("name", "?")] += 1
            elif ty == "Foul Committed":
                c["foul: " + e.get("foul_committed", {}).get("type", {}).get("name", "regular")] += 1
            elif ty == "Interception":
                c["interception: " + e.get("interception", {}).get("outcome", {}).get("name", "?")] += 1
            elif ty == "Block":
                b = e.get("block", {})
                c["block: " + ("deflection" if b.get("deflection") else "save-block" if b.get("save_block") else "standard")] += 1
            if e.get("under_pressure"):
                c["flag: under pressure"] += 1
            if e.get("counterpress"):
                c["flag: counterpress"] += 1
    return {k: v / (len(files) * 2) for k, v in c.items()}, len(files)


def sim_counts(n):
    import calibrate
    c = collections.Counter()
    for seed in range(800, 800 + n):
        m, r = calibrate.run(seed)
        for e in r["events"]:
            ty = e["type"]
            c["E:" + ty] += 1
            if ty in ("pass", "cross"):
                c["pass height: " + ("High" if e.get("lofted") else "Ground")] += 1
                c["pass body: " + ("Hands" if e.get("body_part") == "hands" else "Foot")] += 1
                if e.get("technique"):
                    c["pass technique: " + e["technique"]] += 1
                if e.get("source"):
                    c["pass source: " + str(e["source"])] += 1
                if ty == "cross":
                    c["pass flag: cross"] += 1
            if ty == "duel":
                c["duel: " + str(e.get("duel", "?")) + " / " + str(e.get("outcome", "-"))] += 1
            if ty == "shot":
                c["shot body: " + e.get("body_part", "?")] += 1
    return {k: v / (n * 2) for k, v in c.items()}, n


# real StatsBomb name -> simulator key(s), where the meaning is the same
MAP = {
    "E:Pass": ["E:pass", "E:cross"], "E:Carry": ["E:carry"], "E:Shot": ["E:shot"], "E:Clearance": ["E:clearance"], "E:Interception": ["E:interception"],
    "E:Block": ["E:block"], "E:Foul Committed": ["E:foul"], "E:Dispossessed": ["E:dispossessed"], "E:Miscontrol": ["E:miscontrol"], "E:Ball Recovery": ["E:ball_recovery"],
    "E:Offside": ["E:offside"], "E:Dribble": ["E:take_on"], "E:Pressure": ["E:pressure"], "E:Goal Keeper": ["E:save", "E:keeper_claim", "E:keeper_punch", "E:keeper_sweep"],
    "E:Substitution": ["E:substitution"], "E:Injury Stoppage": ["E:injury"], "E:Duel": ["E:duel", "E:tackle"], "E:50/50": ["E:fifty_fifty"],
    "E:Card": [], "E:Dribbled Past": [], "E:Shield": [], "E:Error": ["E:error"], "E:Foul Won": [], "E:Ball Receipt*": [],
    "pass height: High Pass": ["pass height: High"], "pass height: Ground Pass": ["pass height: Ground"], "pass height: Low Pass": [],
    "pass flag: cross": ["pass flag: cross"], "pass body: Head": [], "pass type: Throw-in": ["pass source: throw_in"], "pass type: Corner": ["E:corner"],
}


def main():
    nr = int(sys.argv[1]) if len(sys.argv) > 1 else 50
    ns = int(sys.argv[2]) if len(sys.argv) > 2 else 8
    R, n_real = real_counts(nr)
    S, _ = sim_counts(ns)
    print(f"per team per match; real = {n_real} StatsBomb matches, sim = {ns} matches\n")
    rows = []
    for k, v in sorted(R.items(), key=lambda kv: -kv[1]):
        if v < .15:
            continue
        keys = MAP.get(k, [k])
        s = sum(S.get(x, 0.) for x in keys) if keys else None
        rows.append((k, v, s))
    for k, v, s in rows:
        if s is None:
            flag = "   <<< NOT SIMULATED"
            ss = "    -"
        else:
            ratio = s / v if v else float("nan")
            flag = "" if .6 <= ratio <= 1.6 else "   <<< " + ("MISSING" if s == 0 else "LOW" if ratio < .6 else "HIGH")
            ss = f"{s:7.2f}"
        print(f"  {k:46s} real {v:8.2f}   sim {ss}{flag}")


if __name__ == "__main__":
    main()
