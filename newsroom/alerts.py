"""Proactive alerts: tactical shifts the newsroom flags without being asked. Each trigger is deterministic and found from the match as
it stood at the check (the readers are re-run on the match cut there, every `every` seconds), so an alert never knows the future.

    duty      a side's players close the ball down more / less / higher / deeper or track someone else (MatchView.duties): one alert per
              side and check, up to three changes; "holds a different space" is the noisiest reading and stays on the board only
    tiring    the side's most-dropped player is read as tiring, checked when a 15-minute window completes, from the hour (MatchView.fatigue)
    spell     a spell of chaos, or of control by one side, starts: two 5-minute windows in a row (MatchView.rhythm)
    flank     4 of a side's last 5 entries into the final third came down one flank, within 10 minutes

Each alert is a fact pack like a moment's (score and stats as they stood, from newsroom.moments) with `alert` holding the trigger's own
numbers and a plain sentence (`alert.text`, also the line published if every draft is held), so it goes through the same desk:
stats and tactics analysts, the commentator, the fact-checker.
"""
from analytics.stats import L, W
from analytics.tools import MatchView
from newsroom.moments import moments

CHANNEL = ("the left", "the middle", "the right")


def _cut(match, t):
    m = dict(match)
    m["events"] = [e for e in match["events"] if e["t"] <= t]
    m["frames"] = match["frames"][:int(t / match["meta"]["dt"]) + 1]
    for k in ("jobs", "stamina"):
        if match.get(k):
            m[k] = match[k][:len(m["frames"]) if k == "jobs" else len(m["frames"]) // 5 + 1]
    m["meta"] = dict(match["meta"], duration=float(t))
    return m


def _entries(events, team):
    """Completed passes and carries into the final third by the team: (t, channel 0 left / 1 middle / 2 right, as the team attacks)."""
    out = []
    for e in events:
        if e.get("team") != team or e["type"] not in ("pass", "cross", "carry") or e.get("end_x") is None:
            continue
        if e["type"] != "carry" and e.get("outcome") != "complete":
            continue
        x0, x1, y1 = (e["x"], e["end_x"], e["end_y"]) if team == 0 else (L - e["x"], L - e["end_x"], W - e["end_y"])
        if x0 < 2 * L / 3 <= x1:
            out.append((e["t"], min(int(y1 / (W / 3)), 2)))
    return out


def triggers(match, every=120.):
    """[(t, kind, team, player id or None, facts, key)] in time order, each found from the data up to t."""
    names = {p["id"]: p["name"] for k in ("home", "away") for p in match["meta"][k]["players"] + match["meta"][k]["bench"]}
    teams = [match["meta"]["home"]["name"], match["meta"]["away"]["name"]]
    seen, out, t = set(), [], 15 * 60.
    last = {}  # (kind, team) -> when it last raised an alert: 10 minutes between alerts of a kind for a side
    ok = lambda kind, tm, t: t - last.get((kind, tm), -1e9) >= 600
    while t < match["meta"]["duration"]:
        mv = MatchView(_cut(match, t))
        for tm in (0, 1):
            new = []
            for c in mv.duties(tm)["changes"]:
                key = ("duty", c["player"], c["change"].split(" instead")[0], c["at"])
                if key not in seen and c["confirmed_t"] <= t and not c["change"].startswith("holds"):
                    seen.add(key)
                    new.append(c)
            if new and ok("duty", tm, t):
                last[("duty", tm)] = t
                new.sort(key=lambda c: (not c["change"].startswith("tracks"), c["player"]))
                share = lambda k, v: k in ("before", "after") and isinstance(v, float)  # closing-down shares (heights are already metres)
                pct = lambda c: {(f"share_of_their_possession_{k}_pct" if share(k, v) else k): (round(v * 100, 1) if share(k, v) else v)
                                 for k, v in c.items() if k not in ("id", "t", "confirmed_t")}  # shares as labelled percentages: 0.053 is not "a rate per second"
                facts = dict(team=teams[tm], since=new[0]["at"], changes=[pct(c) for c in new[:3]])
                out.append((t, "duty", tm, new[0].get("id"), facts, f"{teams[tm]} without the ball since {new[0]['at']}: " + "; ".join(f"{c['player']} {c['change']}" for c in new[:3]) + "."))
            done = [b for a, b in mv._windows(900.) if b <= t and b - a >= 600]
            if t >= 60 * 60 and done and t - done[-1] < every and ok("tiring", tm, t):  # a 15-minute window has just completed
                f = mv.fatigue(tm)
                for nm in f["tiring"][:1]:  # the most dropped of those read as tiring
                    if ("tiring", nm) not in seen:
                        seen.add(("tiring", nm))
                        last[("tiring", tm)] = t
                        q = f["players"][nm]
                        facts = dict(player=nm, drop_pct=q["latest_drop_pct"], team_drop_pct=f["team_drop_pct"], start_hi_m_per_min=q["start_hi_m_per_min"])
                        out.append((t, "tiring", tm, q["id"], facts, f"{nm} ({teams[tm]}) is tiring: his high-intensity running is down {q['latest_drop_pct']}% on his first half-hour, his team's {f['team_drop_pct']}%."))
            ent = [e for e in _entries(mv.events, tm) if t - e[0] <= 600][-5:]
            if len(ent) == 5:
                for ch in (0, 2):
                    n = sum(1 for _, c_ in ent if c_ == ch)
                    if n >= 4 and ok("flank", tm, t):
                        last[("flank", tm)] = t
                        out.append((t, "flank", tm, None, dict(team=teams[tm], flank=CHANNEL[ch], entries=n, of=5),
                                    f"{teams[tm]} keep going down {CHANNEL[ch]}: {n} of their last 5 entries into the final third."))
        rows = [r for r in mv.rhythm()["by_window"] if r["t1"] - r["t0"] >= 240]
        if len(rows) >= 3 and rows[-1]["state"] == rows[-2]["state"] != rows[-3]["state"] and rows[-1]["state"] not in ("even", "stop-start"):
            st = rows[-1]["state"]
            if ("spell", st, rows[-2]["window"]) not in seen:
                seen.add(("spell", st, rows[-2]["window"]))
                who = st.split(": ")[1] if st.startswith("control") else ""
                facts = dict(state=st, since=rows[-2]["window"].split("-")[0], settled_pct=rows[-1]["settled_pct"], broken_pct=rows[-1]["broken_pct"],
                             turnovers_per_min=rows[-1]["turnovers_per_min"])
                text = (f"{who} have taken control since {facts['since']}: {facts['settled_pct']}% of the play is settled possession." if who else
                        f"The game has turned chaotic since {facts['since']}: {facts['broken_pct']}% broken play, {facts['turnovers_per_min']} turnovers a minute.")
                out.append((t, "spell", teams.index(who) if who in teams else None, None, facts, text))
        t += every
    return out


def alerts(match, every=120.):
    """Alert fact packs, in time order, built like moment packs (score and stats as they stood)."""
    base = moments(match, observed=False)
    names = {p["id"]: p["name"] for k in ("home", "away") for p in match["meta"][k]["players"] + match["meta"][k]["bench"]}
    teams = [match["meta"]["home"]["name"], match["meta"]["away"]["name"]]
    clocks = [(e["t"], e["clock"]) for e in match["events"] if e.get("clock")]
    out = []
    for t, kind, tm, pid, facts, text in triggers(match, every):
        prev = [p for p in base if p["moment"]["t"] <= t]
        b = dict(prev[-1]) if prev else dict(base[0]) if base else {}
        clock = next((c for tt, c in reversed(clocks) if tt <= t), "1'")
        b.update(moment=dict(type="alert", clock=clock, t=t, id=None, kind=kind), team=teams[tm] if tm in (0, 1) else "", player=names.get(pid, ""),
                 xg=None, assist="", reason="", why="", alert=dict(kind=kind, text=text, **facts))
        out.append(b)
    return out


if __name__ == "__main__":
    import json
    import sys
    import time
    a = time.time()
    for p in alerts(json.load(open(sys.argv[1]))):
        print(p["moment"]["clock"], p["alert"]["kind"], "|", p["alert"]["text"])
    print(f"{time.time() - a:.0f} s")
