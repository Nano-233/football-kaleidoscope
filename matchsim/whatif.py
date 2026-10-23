"""What if: replay a match exactly to a minute, change one team's instructions there, and play the rest of the match many times with and
without the change. The engine is deterministic for a seed, so the replay is the match the viewer watched; after the minute every pair of
runs (as it was / with the change) shares its random numbers, so the difference between them is the change, not luck.

    recipe = match["meta"]["recipe"]            # how the match was made (written by run_match.py and matchday.py)
    out = what_if(recipe, t=3600, team=1, change={"press": .8, "line_height": .7}, runs=12)
    out = what_if(recipe, t=3600, team=0, change={"formation": "3-4-3"})

change: any of press, line_height, compactness, width, directness, risk, shoot (0..1; the manager keeps adjusting around the new
instruction for the score and the clock, as he did around the old one), and/or formation (switched at the next stoppage).
"""
import dataclasses
import functools
import json
import os
import pickle
import zlib
from concurrent.futures import ProcessPoolExecutor

import numpy as np

from .engine import Match
from .teams import FORMATIONS, Tactics, make_team

TACTIC_KEYS = ("press", "line_height", "compactness", "width", "directness", "risk", "shoot")


def build(recipe):
    rng = np.random.default_rng(recipe["seed"])
    h, a = recipe["home"], recipe["away"]
    return Match(make_team(h["code"], h["formation"], Tactics(**h["tactics"]), rng=rng),
                 make_team(a["code"], a["formation"], Tactics(**a["tactics"]), rng=rng), seed=recipe["seed"])


SNAP_EVERY = 60.  # seconds of match time between stored states
_stored = {}  # recipe -> {t: compressed engine state}, from load_snapshots


def write_snapshots(recipe, path):
    """The engine state at every minute of match time, to a file. Replaying the recipe is exact only on the platform that made the match:
    Windows and Linux round some float maths differently, and a match is chaotic, so a server starts its what-ifs from these instead."""
    m, out, nxt = build(recipe), {}, SNAP_EVERY
    while not m.done:
        m._step()
        if m.t >= nxt:  # the state snapshot(recipe, nxt) returns
            frames, m.frames = m.frames, []
            blob = zlib.compress(pickle.dumps(m), 6)
            m.frames = frames
            while m.t >= nxt:
                out[nxt] = blob
                nxt += SNAP_EVERY
    path.write_bytes(pickle.dumps(out))


def load_snapshots(recipe, path):
    if path.exists():
        _stored[json.dumps(recipe, sort_keys=True)] = pickle.loads(path.read_bytes())


def snapshot(recipe, t):
    """The match state at time t (seconds), without the tracking recorded so far (the continuations do not need it). With stored states
    (load_snapshots), the last one at or before t: the exact match on any platform, from up to a minute earlier than asked."""
    st = _stored.get(json.dumps(recipe, sort_keys=True), {})
    k = max((s for s in st if s <= t), default=None)
    if k is not None:
        return pickle.loads(zlib.decompress(st[k]))
    m = build(recipe)
    while not m.done and m.t < t:
        m._step()
    m.frames = []
    return m


def _apply(m, team, change):
    tac = {k: float(np.clip(v, 0, 1)) for k, v in change.items() if k in TACTIC_KEYS}
    if tac:
        m.base_tact[team] = dataclasses.replace(m.base_tact[team], **tac)
        m.tact[team] = dataclasses.replace(m.tact[team], **tac)
    if change.get("formation") in FORMATIONS:
        m.want_form[team] = (change["formation"], "what if")


def _run(args):
    snap, k, team, change = args
    m = pickle.loads(snap)
    m.rng = np.random.default_rng(100_000 + k)  # run k with and without the change share these random numbers
    t0, n0 = m.t, len(m.events)
    if change:
        _apply(m, team, change)
    while not m.done:
        m._step()
    after = m.events[n0:]
    xg = [round(sum(e["xg"] for e in after if e["type"] == "shot" and e.get("team") == s), 3) for s in (0, 1)]
    shots = [sum(1 for e in after if e["type"] == "shot" and e.get("team") == s) for s in (0, 1)]
    return dict(score=list(m.score), xg_after=xg, shots_after=shots)


def what_if(recipe, t, team, change, runs=12, workers=None):
    snap_m = snapshot(recipe, t)
    at = dict(score=list(snap_m.score), clock=snap_m.events[-1]["clock"] if snap_m.events else "1'", events=len(snap_m.events), t=round(snap_m.t, 1))
    snap = pickle.dumps(snap_m)
    jobs = [(snap, k, team, None) for k in range(runs)] + [(snap, k, team, change) for k in range(runs)]
    workers = workers or int(os.environ.get("SIM_WORKERS", 0)) or min(len(jobs), max(1, (os.cpu_count() or 4) - 2))  # a server sets SIM_WORKERS to all its cores
    with ProcessPoolExecutor(max_workers=workers) as ex:
        res = list(ex.map(_run, jobs))
    base, alt = res[:runs], res[runs:]

    def summary(rs):
        w = [np.sign(r["score"][team] - r["score"][1 - team]) for r in rs]
        return dict(win=round(float(np.mean([x > 0 for x in w])), 2), draw=round(float(np.mean([x == 0 for x in w])), 2), loss=round(float(np.mean([x < 0 for x in w])), 2),
                    goals_for=round(float(np.mean([r["score"][team] for r in rs])), 2), goals_against=round(float(np.mean([r["score"][1 - team] for r in rs])), 2),
                    xg_for_after=round(float(np.mean([r["xg_after"][team] for r in rs])), 2), xg_against_after=round(float(np.mean([r["xg_after"][1 - team] for r in rs])), 2),
                    shots_for_after=round(float(np.mean([r["shots_after"][team] for r in rs])), 1), shots_against_after=round(float(np.mean([r["shots_after"][1 - team] for r in rs])), 1))
    changed_results = sum(1 for b, a in zip(base, alt) if b["score"] != a["score"])
    return dict(at=at, team=team, change=change, runs=runs, as_it_was=summary(base), with_change=summary(alt), runs_with_a_different_result=changed_results)


@functools.lru_cache(maxsize=64)
def _cached(recipe_json, t, team, change_json, runs):
    return what_if(json.loads(recipe_json), t, team, json.loads(change_json), runs)


def cached_what_if(recipe, t, team, change, runs=12):
    """what_if, remembered for the life of the process (the server's endpoint and the agents' tool share it)."""
    return _cached(json.dumps(recipe, sort_keys=True), round(float(t), 1), int(team), json.dumps(change, sort_keys=True), runs)


def t_at_minute(events, minute, half_time):
    """Match time (seconds) when the clock starts `minute`, by the engine's own rule: in the first half minute m runs from (m - 1) * 60 s
    (its stoppage time continues the count), in the second half from half_time (meta["half_time"], the first half's scheduled end)
    plus (m - 46) * 60 s. Events are too sparse to mark the boundary themselves. Clipped to the events there are."""
    t = (minute - 1) * 60. if minute <= 45 else half_time + (minute - 46) * 60.
    return min(max(t, 0.), events[-1]["t"])
