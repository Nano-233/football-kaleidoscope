"""Turn simulated matches into the data the match centre (web/) plays back. Run once per match; the site then needs no backend and no credentials.

    python scripts/build_site.py out/match_1.json [out/match_12.json ...]
    python scripts/build_site.py --keep-agents server/data/match_1.json.gz   # recompute the deterministic parts only, keep the agents' feed and recaps (no model calls)

Writes web/data/<match>/match.json (meta, events, team stats minute by minute, player stats, newsroom feed per persona, suggested clips)
and frames.bin / heads.bin (tracking as int16 positions at 0.05 m and int8 facing at 1/40 rad, the bundle_viewer.py layout), plus
web/data/index.json listing every built match. The newsroom runs on Microsoft Foundry when FOUNDRY_PROJECT_ENDPOINT is set.
"""
import asyncio
import gzip
import json
import pathlib
import sys

import numpy as np

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from analytics.stats import StatsAccumulator  # noqa: E402
from newsroom.agents import cover  # noqa: E402
from newsroom.clients import make_client  # noqa: E402
from newsroom.desk import Desk  # noqa: E402
from matchsim.whatif import write_snapshots  # noqa: E402
from analytics.tools import MatchView  # noqa: E402
from newsroom.moments import moments  # noqa: E402
from newsroom.alerts import alerts  # noqa: E402

PERSONAS = {"fan": "fan", "analyst": "analyst"}  # site persona -> commentator voice (the scout view works from the player data, not commentary)
TEAM_KEYS = ("goals", "xg", "shots", "shots_on_target", "big_chances", "passes", "pass_accuracy", "ppda", "corners", "fouls", "yellow_cards", "red_cards",
             "tackles_won", "interceptions", "take_on_rate", "progressive_passes", "final_third_passes", "possession_gains",
             "avg_pass_length_m", "avg_pass_difficulty", "hard_pass_accuracy", "top_shot_speed_kmh")
CLIP = {"goal": (15, 6), "own_goal": (12, 5), "penalty": (8, 8), "var_check": (20, 4), "goal_disallowed": (20, 4)}  # seconds before / after; others (8, 4)
OVERLAY_S = 10  # how long a published line stays on screen


def pack_frames(frames):
    fr = np.array(frames)
    q = np.concatenate([fr[:, 1:3] * 20, fr[:, 3:5], fr[:, 5:49] * 20], axis=1).round().astype(np.int16)
    hd = (fr[:, 50:72] * 40).round().clip(-127, 127).astype(np.int8) if fr.shape[1] >= 72 else np.zeros((len(fr), 22), np.int8)
    return q, hd


FOCUS = {  # the metric a viewer can choose to follow: player counters summed (running comes from tracking)
    "xg": ("xg",), "pressing": ("pressures", "tackles_won", "interceptions"), "passing": ("hard_passes_completed",),
    "duels": ("tackles_won", "take_ons_won", "aerial_won", "fifty_fifty_won"),
}


def stats_timeline(match):
    """Team stats at the end of every match minute (index = clock minute), final player stats, and for each focus metric the top
    three players at the end of every minute ([[player id, value], ...])."""
    acc, out, nxt = StatsAccumulator(match["meta"]), [], 60.
    lead = {k: [] for k in FOCUS}

    def snap():
        out.append([{k: t[k] for k in TEAM_KEYS} for t in acc.teams()])
        ps = acc.players()
        for k, keys in FOCUS.items():
            vals = sorted(((round(sum(p.get(c) or 0 for c in keys), 2), pid) for pid, p in ps.items()), reverse=True)[:3]
            lead[k].append([[pid, v] for v, pid in vals if v > 0])
    for e in match["events"]:
        while e["t"] >= nxt:
            snap()
            nxt += 60.
        acc.update(e)
    snap()
    dist = MatchView(match).distance_by_minute()
    lead["running"] = [[[pid, km[min(i, len(km) - 1)]] for km, pid in sorted(((km, pid) for pid, km in dist.items()), key=lambda r: -r[0][min(i, len(r[0]) - 1)])[:3]]
                       for i in range(len(out))]
    return out, acc.players(), lead


async def newsroom(packs):
    client, gate, feed = make_client(), asyncio.Semaphore(3), {}  # a few moments at a time keeps inside the deployment's request rate

    async def one(p, voice):
        async with gate:
            return await cover(client, p, voice)
    for site, voice in PERSONAS.items():
        feed[site] = list(await asyncio.gather(*(one(p, voice) for p in packs)))
        for it in feed[site]:
            it["overlay"] = dict(t_in=it["moment"]["t"], t_out=it["moment"]["t"] + OVERLAY_S, kind=it["moment"]["type"])
    return feed


def clip_kind(e):
    """What makes an event worth a suggested clip, or None."""
    ty = e["type"]
    if ty in ("goal", "own_goal", "penalty", "var_check", "goal_disallowed"):
        return ty
    if ty == "shot" and e.get("outcome") != "goal" and (e.get("xg", 0) >= .12 or e.get("outcome") == "saved"):
        return "big_chance" if e["xg"] >= .25 else "shot_saved" if e.get("outcome") == "saved" else "chance"
    if ty == "card":
        return "red_card" if e.get("card") != "yellow" else "yellow_card"
    return None


def clips(events, names, duration):
    out = []
    for e in events:
        kind = clip_kind(e)
        if not kind:
            continue
        pre, post = CLIP.get(kind, (8, 4))
        who = f" · {names[e['player']]}" if e.get("player") in names else ""
        out.append(dict(id=f"e{e['id']}", kind=kind, title=f"{e['clock']} {kind.replace('_', ' ')}{who}", t=e["t"],
                        start=round(max(0., e["t"] - pre), 1), end=round(min(duration, e["t"] + post), 1)))
    return out


RECAPS = {  # (when, question); each is written for every profile in RECAP_PROFILES, from what had happened by then
    "half_time": "Write the half-time report in short paragraphs separated by a blank line: the story of the half (every goal, red card and change of system in it), "
                 "why the score is what it is, and what to watch in the second half. The match is not over: never call a goal the winner or a result decided, "
                 "and give a cause only where the findings show it.",
    "full_time": "Write the full-time report in short paragraphs separated by a blank line: the story of the match (every goal, red card and change of system in it), "
                 "the turning points and why the result happened.",
}
RECAP_PROFILES = ({"persona": "fan", "depth": "quick"}, {"persona": "analyst", "depth": "deep"})


async def recaps(match):
    client, out = make_client(), []
    ht = next((e["t"] for e in match["events"] if e["type"] == "half_end"), match["meta"]["half_time"])
    for kind, q in RECAPS.items():
        t = ht if kind == "half_time" else match["meta"]["duration"]
        for prof in RECAP_PROFILES:
            r = await Desk(client, match, prof, until=t).ask(q)
            out.append(dict(kind=kind, t=t, profile=prof, issues=[a["issues"] for a in r["attempts"]], **{k: r[k] for k in ("answer", "held", "approved", "rewrites", "findings", "steps", "watch")}))
    return out


def reading(match):
    """What the formation reader saw against what the engine did, per team (the "AI accuracy" panel)."""
    mv, out = MatchView(match), []
    for tm in (0, 1):
        tl, tr = mv.formation_timeline(tm), mv.truth(tm)
        sc = mv.score_reading(tm, [c for c in tl["changes"] if c["phase"] == "in_possession"])
        out.append(dict(team=mv.team_names[tm], truth=dict(starting=tr["starting"], changes=[{k: c[k] for k in ("at", "formation", "previous", "reason")} for c in tr["formation_changes"]]),
                        read=dict(starting=tl["starting"], changes=[{k: c[k] for k in ("phase", "at", "formation", "previous")} for c in tl["changes"]]),
                        score=dict(starting_right=mv.same_shape(tl["starting"]["in_possession"], tr["starting"]), found=sc["found"], true_changes=sc["true_changes"],
                                   false_alarms=sc["false_alarms"], windows_agreeing=mv.window_agreement(tm, tl))))
    return out


SHAPE_W, NET_W = 300., 900.
WIN_BALL = ("interception", "tackle", "ball_recovery", "block", "fifty_fifty", "duel", "save", "keeper_claim", "keeper_sweep")  # analyst board windows: shape every 5 minutes, passing network and pressing every 15


def board(match):
    """Everything the analyst board draws, from the same toolkit the agents use, window by window so the replay only ever shows
    windows that have finished. Positions are in each team's own frame (own goal at x = 0)."""
    mv, dur = MatchView(match), match["meta"]["duration"]
    out = dict(shape_window=SHAPE_W, net_window=NET_W, shape=[[], []], network=[[], []], pressing=[], momentum=mv.momentum(), regains=[], winprob=mv.win_probability(),
               rhythm=mv.rhythm())
    for tm in (0, 1):
        t = 0.
        while t < dur:
            row = dict(t0=t, t1=min(t + SHAPE_W, dur))
            for ph, key in (("in", "in_possession"), ("out", "out_of_possession")):
                sh = mv.shape(tm, t, t + SHAPE_W, ph)
                row[key] = None if "error" in sh else dict(formation=sh["formation"], line=sh["defensive_line_m"], length=sh["block_length_m"], width=sh["block_width_m"],
                                                          positions=[[q["id"], q["x"], q["y"]] for q in sh["positions"]])
            out["shape"][tm].append(row)
            t += SHAPE_W
        t = 0.
        while t < dur:
            net = mv.passing_network(tm, t, t + NET_W, min_links=2)
            ids = {v["name"]: k for k, v in mv.players.items()}
            out["network"][tm].append(dict(t0=t, t1=min(t + NET_W, dur), links=[[ids[l["from"]], ids[l["to"]], l["passes"]] for l in net["links"]],
                                           nodes={ids[n]: xy for n, xy in net["avg_positions"].items()}, entries=net["final_third_entries_left_centre_right"]))
            t += NET_W
        out["pressing"].append(mv.pressing(tm, NET_W)["by_window"])
    evs = match["events"]
    for i, e in enumerate(evs):  # the ball won in play (not a restart): the possession change follows the winning action
        if e["type"] == "possession_change" and e.get("team") in (0, 1) and "x" in e:
            won = next((w for w in reversed(evs[max(0, i - 4):i]) if w.get("team") == e["team"] and w["type"] in WIN_BALL and e["t"] - w["t"] <= 2), None)
            if won:
                out["regains"].append([round(e["t"], 1), e["team"], e["x"], e["y"], won.get("player"), won["type"]])
    return out


def duties(match):
    """Out-of-possession duties per team, window by window, for the board; and the reader scored against the engine's job log."""
    mv = MatchView(match)
    out = []
    for tm in (0, 1):
        d = mv.duties(tm)
        out.append(dict(window=900., rows=[dict(t0=r["t0"], t1=r["t1"], players=r["players"]) for r in d["rows"]], team_by_window=d["team_by_window"],
                        changes=d["changes"], score=mv.score_duties(tm)))
    return out


def breakdowns(match):
    """The pause-and-draw breakdown of every goal and big chance (penalties aside)."""
    mv = MatchView(match)
    shots = [e for e in match["events"] if e["type"] == "shot" and e.get("kind") != "penalty" and (e.get("outcome") == "goal" or e.get("xg", 0) >= .25)]
    shots = [e for e, n in zip(shots, shots[1:] + [None]) if not (n and n["team"] == e["team"] and n["t"] - e["t"] <= 3)]  # a rebound: one breakdown, ending on the last shot
    ev = match["events"]
    for i, g in enumerate(ev):  # a goal with no shot before it (a lofted ball that drifts in): break down the pass that scored
        prev = next((x for x in reversed(ev[max(0, i - 4):i]) if x["type"] in ("shot", "pass", "cross")), None)
        if g["type"] == "goal" and prev and prev["type"] != "shot":
            shots.append(prev)
    return [mv.breakdown(e["id"]) for e in sorted(shots, key=lambda e: e["t"])]


def profiles(match):
    """The scout's numbers for everyone who played: heat map, physical output, rating breakdown, per 90 against his role."""
    mv = MatchView(match)
    out = {}
    for pid in mv.players:
        pr = mv.player_profile(pid)
        if pr["minutes"] >= 1:
            out[pid] = {k: pr[k] for k in ("physical", "heat_map_6x4", "per90_vs_role", "rating_terms")}
    return out


def build(src, keep_agents=False):
    name = src.name.split(".")[0]
    with (gzip.open(src, "rt", encoding="utf-8") if src.suffix == ".gz" else open(src, encoding="utf-8")) as fh:
        match = json.load(fh)
    meta = {k: v for k, v in match["meta"].items() if k != "frame_cols"}
    dest = ROOT / "web" / "data" / name
    dest.mkdir(parents=True, exist_ok=True)
    srv = ROOT / "server" / "data"  # the full match for the agents' tools (decision reasons, every frame), compressed; served to nobody
    srv.mkdir(parents=True, exist_ok=True)
    full = {**match, "events": [{k: v for k, v in e.items() if k != "freeze_frame"} for e in match["events"]],
            "frames": np.round(np.asarray(match["frames"]), 2).tolist()}
    with gzip.open(srv / f"{name}.json.gz", "wt", encoding="utf-8") as fh:
        json.dump(full, fh, separators=(",", ":"))
    if match["meta"].get("recipe"):
        write_snapshots(match["meta"]["recipe"], srv / f"{name}.snaps")  # the server's what-ifs start from these (exact on any platform)
    q, hd = pack_frames(match["frames"])
    (dest / "frames.bin").write_bytes(q.tobytes())
    (dest / "heads.bin").write_bytes(hd.tobytes())
    (dest / "ball_z.bin").write_bytes((np.asarray(match["frames"])[:, 49] * 100).round().clip(0, 32000).astype(np.int16).tobytes())  # ball height, cm (the 3D view)
    packs = sorted(moments(match) + alerts(match), key=lambda p: p["moment"]["t"])  # moments, and the shifts the readers flag unasked
    timeline, players, leaders = stats_timeline(match)
    events = [{k: v for k, v in e.items() if k not in ("freeze_frame", "why", "players", "bench")} for e in match["events"]]
    old = json.loads((dest / "match.json").read_text(encoding="utf-8")) if keep_agents else {}
    doc = dict(meta=meta, n_frames=len(q), events=events, team_stats=timeline, players=players, leaders=leaders,
               newsroom=old.get("newsroom") or asyncio.run(newsroom(packs)), recaps=old.get("recaps") or asyncio.run(recaps(match)), reading=reading(match), board=board(match),
               breakdowns=breakdowns(match), profiles=profiles(match), tags=MatchView(match).player_tags(), milestones=MatchView(match).milestones(), duties=duties(match), fatigue=[MatchView(match).fatigue(t) for t in (0, 1)], clips=clips(match["events"], {k: v["name"] for k, v in players.items()}, meta["duration"]))
    (dest / "match.json").write_text(json.dumps(doc, separators=(",", ":")), encoding="utf-8")
    print(dest, f"{sum(f.stat().st_size for f in dest.iterdir()) / 1e6:.1f} MB,", len(packs), "moments,", len(doc["clips"]), "clips")
    return dict(id=name, home=meta["home"]["name"], away=meta["away"]["name"], home_code=meta["home"]["code"], away_code=meta["away"]["code"], score=meta["score"])


if __name__ == "__main__":
    keep = "--keep-agents" in sys.argv
    built = [build(pathlib.Path(a), keep) for a in sys.argv[1:] if not a.startswith("--")]
    idx = ROOT / "web" / "data" / "index.json"
    old = {m["id"]: m for m in json.loads(idx.read_text())} if idx.exists() else {}
    old.update({m["id"]: m for m in built})
    idx.write_text(json.dumps(sorted(old.values(), key=lambda m: m["id"]), indent=1), encoding="utf-8")
