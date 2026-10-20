"""Score the newsroom against what the engine actually did. The data is synthetic, so the true answer to every question is known: the
engine's own record of formations and changes, the goals, the ratings and the pressing numbers.

    python scripts/eval_newsroom.py out/match_1.json out/match_7.json ...     # writes web/data/eval.json and prints a summary
    python scripts/eval_newsroom.py --only=formation out/...                    # one kind of question; prints only, eval.json is left alone

Questions (asked at full time, analyst profile, standard depth), and how each is marked:
  formation  "What system did <team> start in, did they change it, and why?"  the true starting system named (same layout counts), and
             each true change: its formation named with a minute within 10 of the true one. Also scored, over the changes found: how far
             the minute given is from the true one, and whether the reason given is the manager's (a grader agent compares it with the
             engine's logged reason: protecting the lead, chasing the game, playing with ten, against ten)
  goals      "Who scored, and when?"                                       every scorer named with the right minute
  best       "Who was the best player by rating?"                          the top-rated player named
  pressing   "Which team pressed harder?"                                  a grader agent compares the answer with the computed PPDA
  duties     "Without the ball, which opponent did <player> track most?"   the engine's job log: the man it gave him most (the player
             asked about is the one with the clearest marking job in the match)
  fatigue    "Which <team> player was tiring most by the end?"             the engine's stamina: the named player is among the team's
             three most tired outfield players still on at the end
  passing    "Who completed the most hard passes?"                         the events (difficulty is the engine's own estimate)
  milestones "Did anyone reach a milestone?"                               the events: every brace, hat-trick and goal off the bench named
             (asked only when the match has one)
  control    "Which team controlled the game more?"                        a consistency check against the rhythm analytics (settled
             possession share): there is no engine truth for control
  bench      the bench assistant at 60' for the side not winning: the sentences of its write-up that recommend name what the code
             picked from the replays (deterministic); scored on consistency, not on whether the change is right
Also recorded: fact-checker approval, rewrites, tool calls and seconds per answer.
"""
import asyncio
import collections
import json
import pathlib
import re
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from agent_framework import Agent  # noqa: E402

from analytics.stats import match_stats  # noqa: E402
from analytics.tools import MatchView  # noqa: E402
from newsroom.agents import _run, _verdict  # noqa: E402
from newsroom.clients import make_client  # noqa: E402
from newsroom.desk import Desk  # noqa: E402
from matchsim.whatif import t_at_minute  # noqa: E402
import numpy as np  # noqa: E402

PROFILE = {"persona": "analyst", "depth": "standard"}
REASONS = """You grade a football newsroom's explanation of a team's changes of system. TRUTH lists each change with the manager's real reason.
For each change in TRUTH, in order, decide whether the ANSWER gives that change a reason that means the same thing (wording does not matter:
"holding on to a lead" = protecting the lead, "needed a goal" = chasing the game, "after the red card" = man down). A change the answer does
not mention, or mentions without a reason, is false. Reply only as JSON {"right": [bool, ...]}, one per change in TRUTH."""
GRADER = """You grade a football newsroom's answer. TRUTH holds the correct facts. Reply only as JSON {"correct": bool, "why": str}: correct is true
when the ANSWER names the right team for the QUESTION according to TRUTH (wording does not matter)."""


def minutes(text):
    return [int(a) + int(b or 0) for a, b in re.findall(r"(\d{1,3})(?:\+(\d))?(?:'|st|nd|rd|th|\s*min)", text)]


def clock_min(clock):
    a, _, b = clock.rstrip("'").partition("+")
    return int(a) + int(b or 0)


async def one(client, path):
    match = json.loads(pathlib.Path(path).read_text(encoding="utf-8"))
    mv, st = MatchView(match), match_stats(match)
    rows = []

    async def ask(kind, q):
        t0 = time.time()
        r = await Desk(client, match, PROFILE).ask(q)
        return r, dict(kind=kind, question=q, answer=r["answer"] or r["held"] or "", approved=r["approved"], rewrites=r["rewrites"], tool_calls=len(r["steps"]), seconds=round(time.time() - t0, 1))

    for tm in (0, 1) if ONLY in (None, "formation") else ():
        tr = mv.truth(tm)
        r, row = await ask("formation", f"What system did {mv.team_names[tm]} start the match in, and did they change their system during it? When, to what, and why?")
        text = r["answer"] or r["held"] or ""
        named = re.findall(r"\b\d-\d(?:-\d){1,3}\b", text)
        ok_start = any(mv.same_shape(f, tr["starting"]) for f in named)
        found = [any(mv.same_shape(f, c["formation"]) for f in named) and any(abs(m - clock_min(c["at"])) <= 10 for m in minutes(text)) for c in tr["formation_changes"]]
        ms = minutes(text)
        lag = [min(abs(m - clock_min(c["at"])) for m in ms) for c, f in zip(tr["formation_changes"], found) if f]
        right = []
        if found and any(found):
            truth = "\n".join(f"{c['at']} to {c['formation']}: {c['reason']}" for c in tr["formation_changes"])
            v = await _run(Agent(client, REASONS, name="reason_grader"), f"TEAM: {mv.team_names[tm]}\nTRUTH:\n{truth}\nANSWER: {text}")
            try:
                right = [bool(x) for x in json.loads(re.search(r"\{.*\}", v.text, re.S).group())["right"]][:len(found)]
            except (AttributeError, ValueError, KeyError):
                right = []
            right = [r and f for r, f in zip(right, found)]
        row.update(correct=bool(ok_start and all(found)), detail=dict(starting_named=ok_start, changes_found=f"{sum(found)}/{len(found)}"),
                   changes=len(found), found=sum(found), reasons_right=sum(right), minute_errors=lag)
        rows.append(row)
    if ONLY not in (None, "goals", "best", "pressing"):
        rows += await new_kinds(client, match, mv, st, ask)
        return dict(match=pathlib.Path(path).stem, rows=rows)
    goals = [e for e in match["events"] if e["type"] in ("goal", "own_goal")]
    r, row = await ask("goals", "Who scored the goals in this match, and at which minutes?")
    text = r["answer"] or r["held"] or ""
    hits = [mv.names.get(e.get("player"), "?") in text and clock_min(e["clock"]) in minutes(text) for e in goals]
    row.update(correct=all(hits), detail=f"{sum(hits)}/{len(hits)} goals with scorer and minute")
    rows.append(row)
    best = max((p for p in st["players"].values() if p["rating"] is not None), key=lambda p: p["rating"])
    r, row = await ask("best", "Who was the best player in this match by rating?")
    text = r["answer"] or r["held"] or ""
    row.update(correct=best["name"] in text, detail=f"truth {best['name']} ({best['rating']})")
    rows.append(row)
    ppda = [t["ppda"] for t in st["teams"]]
    harder = mv.team_names[0] if ppda[0] < ppda[1] else mv.team_names[1]
    r, row = await ask("pressing", "Which team pressed harder over the whole match?")
    text = r["answer"] or r["held"] or ""
    g = _verdict((await _run(Agent(client, GRADER, name="grader"), f"QUESTION: Which team pressed harder?\nTRUTH: {harder} pressed harder (PPDA {ppda[0]} vs {ppda[1]}, lower = harder, home first).\nANSWER: {text}")).text.replace('"correct"', '"approved"'))
    row.update(correct=bool(g["approved"]), detail=f"truth {harder}")
    rows.append(row)
    rows += await new_kinds(client, match, mv, st, ask)
    return dict(match=pathlib.Path(path).stem, rows=rows)


WORDS = dict(press=("press",), line_height=("line",), compactness=("compact",), width=("width",), directness=("direct",), risk=("risk",), shoot=("shoot",))
KEEP = ("keep", "as they are", "no change", "unchanged", "maintain", "stay with", "stick with")


def recommends(text, rec):
    """Do the sentences that recommend name the code's pick? A formation by name, an instruction by its word; "keep" when nothing was
    picked. Deterministic: an LLM grader took the description of a change that was tested for a second recommendation."""
    sents = [x.lower() for x in re.split(r"(?<=[.!?])\s+", text) if "recommend" in x.lower()]
    if not sents:
        return False
    if rec is None:
        return any(k in x for x in sents for k in KEEP)
    want = ([rec["formation"]] if "formation" in rec else []) + [WORDS[k][0] for k in rec if k in WORDS]
    return all(any(w in x for x in sents) for w in want)


def true_marker(mv, tm):
    """The outfield player of team tm with the clearest marking job in the engine's own log, and the opponent it gave him most."""
    F, J = mv.F, mv.jobs
    idx = np.nonzero(F[:, 4] == 1 - tm)[0][::5]
    best = None
    for k in range(1, 11):
        occ = {mv._occupant(11 * tm + k, i * mv.dt) for i in idx}
        if len(occ) != 1:  # substituted: his job is not one man's
            continue
        codes = J[idx, 11 * tm + k]
        mk = idx[(codes >= 0) & (codes <= 10)]
        if len(mk) < 120:
            continue
        tgt = collections.Counter(mv._occupant(11 * (1 - tm) + int(J[i, 11 * tm + k]), i * mv.dt) for i in mk)
        who, n = tgt.most_common(1)[0]
        score = len(mk) / len(idx) * n / len(mk)
        if n / len(mk) >= .5 and (best is None or score > best[0]):
            best = (score, occ.pop(), who, round(n / len(mk), 2))
    return best


async def new_kinds(client, match, mv, st, ask):
    rows = []
    if ONLY in (None, "duties") and mv.jobs is not None:
        for tm in (0, 1):
            b = true_marker(mv, tm)
            if not b:
                continue
            _, pid, tgt, share = b
            r, row = await ask("duties", f"Without the ball, which opponent did {mv.names[pid]} ({mv.team_names[tm]}) track most during the match?")
            row.update(correct=mv.names[tgt] in row["answer"], detail=f"truth {mv.names[tgt]} ({share:.0%} of his marking time, engine job log)")
            rows.append(row)
    if ONLY in (None, "fatigue") and mv.stamina is not None:
        for tm in (0, 1):
            end = mv.meta["duration"] - 1
            on = [(1 - mv.stamina[-1][11 * tm + k], mv._occupant(11 * tm + k, end)) for k in range(1, 11) if mv.F[-1, 6 + 2 * (11 * tm + k)] > -5]
            top = [pid for _, pid in sorted(on, reverse=True)[:3]]
            r, row = await ask("fatigue", f"Which {mv.team_names[tm]} player was tiring most by the end of the match?")
            row.update(correct=any(mv.names[p] in row["answer"] for p in top), detail=f"truth (engine stamina) top 3: {', '.join(mv.names[p] for p in top)}")
            rows.append(row)
    if ONLY in (None, "passing"):
        best = max(st["players"].values(), key=lambda p: p.get("hard_passes_completed") or 0)
        r, row = await ask("passing", "Who completed the most hard passes in this match (passes the passer was unlikely to complete)?")
        row.update(correct=best["name"] in row["answer"], detail=f"truth {best['name']} ({best['hard_passes_completed']})")
        rows.append(row)
    if ONLY in (None, "milestones"):
        ms = [r_ for r_ in mv.milestones() if r_["text"] in ("brace", "hat-trick", "scores off the bench")]
        if ms:
            r, row = await ask("milestones", "Did any player reach a milestone in this match, such as a brace, a hat-trick or a goal off the bench?")
            hits = [mv.names[r_["player"]] in row["answer"] for r_ in ms]
            row.update(correct=all(hits), detail=f"{sum(hits)}/{len(hits)}: " + ", ".join(f"{mv.names[r_['player']]} {r_['text']}" for r_ in ms))
            rows.append(row)
    if ONLY in (None, "control"):
        sh = mv.rhythm()["match"]["settled_by_team_pct"]
        if abs(sh[0] - sh[1]) >= 10:  # a clear case only
            more = mv.team_names[0 if sh[0] > sh[1] else 1]
            r, row = await ask("control", "Which team controlled the game more?")
            g = _verdict((await _run(Agent(client, GRADER, name="grader"), f"QUESTION: Which team controlled the game more?\nTRUTH: {more} had more of the settled possession ({sh[0]}% v {sh[1]}%, home first).\nANSWER: {row['answer']}")).text.replace('"correct"', '"approved"'))
            row.update(correct=bool(g["approved"]), detail=f"analytics: {more} ({sh[0]}% v {sh[1]}% of settled possession)")
            rows.append(row)
    if ONLY in (None, "bench") and match["meta"].get("recipe"):
        t = t_at_minute(match["events"], 60, match["meta"]["half_time"])
        sc = [0, 0]
        for e in match["events"]:
            if e["t"] <= t and e["type"] in ("goal", "own_goal"):
                sc = [e["home"], e["away"]]
        tm = 0 if sc[0] <= sc[1] else 1  # the side that is not winning, the home side when level
        t0 = time.time()
        r = await Desk(client, match, PROFILE, until=t).advise(tm)
        pick = "keep things as they are" if r["recommendation"] is None else json.dumps(r["recommendation"])
        text = r["answer"] or r["held"] or ""
        g = dict(approved=recommends(text, r["recommendation"]))
        rows.append(dict(kind="bench", question=f"bench assistant for {r['team']} at {r['minute']}", answer=text, approved=r["approved"], rewrites=r["rewrites"],
                         tool_calls=len(r["steps"]), seconds=round(time.time() - t0, 1), correct=bool(g["approved"]) and bool(r["tests"]),
                         detail=f"picked {pick} from {len(r['tests'])} tested changes"))
    return rows


async def main(paths):
    client = make_client()
    gate = asyncio.Semaphore(2)  # two matches at a time keeps inside the deployment's request rate (three hit 429s)

    async def run(p):
        async with gate:
            return await one(client, p)
    res = list(await asyncio.gather(*(run(p) for p in paths)))
    rows = [r for m in res for r in m["rows"]]
    kinds = sorted({r["kind"] for r in rows})
    summary = dict(matches=len(res), answers=len(rows),
                   accuracy={k: round(sum(r["correct"] for r in rows if r["kind"] == k) / max(1, sum(r["kind"] == k for r in rows)), 2) for k in kinds},
                   overall=round(sum(r["correct"] for r in rows) / len(rows), 2), approved=round(sum(r["approved"] for r in rows) / len(rows), 2),
                   changes=dict(true=sum(r.get("changes", 0) for r in rows), found=sum(r.get("found", 0) for r in rows),
                                reasons_right=sum(r.get("reasons_right", 0) for r in rows),
                                mean_minute_error=round(sum(sum(r.get("minute_errors", [])) for r in rows) / max(1, sum(len(r.get("minute_errors", [])) for r in rows)), 1)),
                   avg_tool_calls=round(sum(r["tool_calls"] for r in rows) / len(rows), 1), avg_seconds=round(sum(r["seconds"] for r in rows) / len(rows), 1))
    if ONLY is None:
        (ROOT / "web" / "data" / "eval.json").write_text(json.dumps(dict(summary=summary, matches=res), indent=1), encoding="utf-8")
    print(json.dumps(summary, indent=1))
    for m in res:
        for r in m["rows"]:
            print(m["match"], r["kind"], "OK " if r["correct"] else "MISS", r["detail"])
            if ONLY and not r["correct"]:
                print("   ", r["answer"].replace(chr(10), " "))


ONLY = next((a.split("=", 1)[1] for a in sys.argv[1:] if a.startswith("--only=")), None)

if __name__ == "__main__":
    asyncio.run(main([a for a in sys.argv[1:] if not a.startswith("--")]))
