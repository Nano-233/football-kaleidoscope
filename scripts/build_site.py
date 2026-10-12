"""Turn simulated matches into the data the match centre (web/) plays back. Run once per match; the site then needs no backend and no credentials.

    python scripts/build_site.py out/match_1.json [out/match_12.json ...]

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
from analytics.tools import MatchView  # noqa: E402
from newsroom.moments import moments  # noqa: E402

PERSONAS = {"fan": "fan", "analyst": "analyst"}  # site persona -> commentator voice (the scout view works from the player data, not commentary)
TEAM_KEYS = ("goals", "xg", "shots", "shots_on_target", "big_chances", "passes", "pass_accuracy", "ppda", "corners", "fouls", "yellow_cards", "red_cards",
             "tackles_won", "interceptions", "take_on_rate", "progressive_passes", "final_third_passes", "possession_gains")
CLIP = {"goal": (15, 6), "own_goal": (12, 5), "penalty": (8, 8), "var_check": (20, 4), "goal_disallowed": (20, 4)}  # seconds before / after; others (8, 4)
OVERLAY_S = 10  # how long a published line stays on screen


def pack_frames(frames):
    fr = np.array(frames)
    q = np.concatenate([fr[:, 1:3] * 20, fr[:, 3:5], fr[:, 5:49] * 20], axis=1).round().astype(np.int16)
    hd = (fr[:, 50:72] * 40).round().clip(-127, 127).astype(np.int8) if fr.shape[1] >= 72 else np.zeros((len(fr), 22), np.int8)
    return q, hd


def stats_timeline(match):
    """Team stats at the end of every match minute (index = clock minute), and final player stats."""
    acc, out, nxt = StatsAccumulator(match["meta"]), [], 60.
    for e in match["events"]:
        while e["t"] >= nxt:
            out.append([{k: t[k] for k in TEAM_KEYS} for t in acc.teams()])
            nxt += 60.
        acc.update(e)
    out.append([{k: t[k] for k in TEAM_KEYS} for t in acc.teams()])
    return out, acc.players()


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
    "half_time": "Write the half-time report: the story of the half, why the score is what it is, and what to watch in the second half.",
    "full_time": "Write the full-time report: the story of the match, the turning points and why the result happened.",
}
RECAP_PROFILES = ({"persona": "fan", "depth": "quick"}, {"persona": "analyst", "depth": "deep"})


async def recaps(match):
    client, out = make_client(), []
    ht = next((e["t"] for e in match["events"] if e["type"] == "half_end"), match["meta"]["half_time"])
    for kind, q in RECAPS.items():
        t = ht if kind == "half_time" else match["meta"]["duration"]
        for prof in RECAP_PROFILES:
            r = await Desk(client, match, prof, until=t).ask(q)
            out.append(dict(kind=kind, t=t, profile=prof, **{k: r[k] for k in ("answer", "held", "approved", "rewrites", "findings", "steps", "watch")}))
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


def build(src):
    match = json.loads(src.read_text(encoding="utf-8"))
    meta = {k: v for k, v in match["meta"].items() if k != "frame_cols"}
    dest = ROOT / "web" / "data" / src.stem
    dest.mkdir(parents=True, exist_ok=True)
    srv = ROOT / "server" / "data"  # the full match for the agents' tools (decision reasons, every frame), compressed; served to nobody
    srv.mkdir(parents=True, exist_ok=True)
    full = {**match, "events": [{k: v for k, v in e.items() if k != "freeze_frame"} for e in match["events"]],
            "frames": np.round(np.asarray(match["frames"]), 2).tolist()}
    with gzip.open(srv / f"{src.stem}.json.gz", "wt", encoding="utf-8") as fh:
        json.dump(full, fh, separators=(",", ":"))
    q, hd = pack_frames(match["frames"])
    (dest / "frames.bin").write_bytes(q.tobytes())
    (dest / "heads.bin").write_bytes(hd.tobytes())
    packs = moments(match)
    timeline, players = stats_timeline(match)
    events = [{k: v for k, v in e.items() if k not in ("freeze_frame", "why", "players", "bench")} for e in match["events"]]
    doc = dict(meta=meta, n_frames=len(q), events=events, team_stats=timeline, players=players,
               newsroom=asyncio.run(newsroom(packs)), recaps=asyncio.run(recaps(match)), reading=reading(match), clips=clips(match["events"], {k: v["name"] for k, v in players.items()}, meta["duration"]))
    (dest / "match.json").write_text(json.dumps(doc, separators=(",", ":")), encoding="utf-8")
    print(dest, f"{sum(f.stat().st_size for f in dest.iterdir()) / 1e6:.1f} MB,", len(packs), "moments,", len(doc["clips"]), "clips")
    return dict(id=src.stem, home=meta["home"]["name"], away=meta["away"]["name"], home_code=meta["home"]["code"], away_code=meta["away"]["code"], score=meta["score"])


if __name__ == "__main__":
    built = [build(pathlib.Path(a)) for a in sys.argv[1:]]
    idx = ROOT / "web" / "data" / "index.json"
    old = {m["id"]: m for m in json.loads(idx.read_text())} if idx.exists() else {}
    old.update({m["id"]: m for m in built})
    idx.write_text(json.dumps(sorted(old.values(), key=lambda m: m["id"]), indent=1), encoding="utf-8")
