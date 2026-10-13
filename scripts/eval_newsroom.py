"""Score the newsroom against what the engine actually did. The data is synthetic, so the true answer to every question is known: the
engine's own record of formations and changes, the goals, the ratings and the pressing numbers.

    python scripts/eval_newsroom.py out/match_1.json out/match_7.json ...     # writes web/data/eval.json and prints a summary

Questions (asked at full time, analyst profile, standard depth), and how each is marked:
  formation  "What system did <team> start in, did they change it, and why?"  the true starting system named (same layout counts), and
             each true change: its formation named with a minute within 10 of the true one. Also scored, over the changes found: how far
             the minute given is from the true one, and whether the reason given is the manager's (a grader agent compares it with the
             engine's logged reason: protecting the lead, chasing the game, playing with ten, against ten)
  goals      "Who scored, and when?"                                       every scorer named with the right minute
  best       "Who was the best player by rating?"                          the top-rated player named
  pressing   "Which team pressed harder?"                                  a grader agent compares the answer with the computed PPDA
Also recorded: fact-checker approval, rewrites, tool calls and seconds per answer.
"""
import asyncio
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
        return r, dict(kind=kind, question=q, approved=r["approved"], rewrites=r["rewrites"], tool_calls=len(r["steps"]), seconds=round(time.time() - t0, 1))

    for tm in (0, 1):
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
    return dict(match=pathlib.Path(path).stem, rows=rows)


async def main(paths):
    client = make_client()
    gate = asyncio.Semaphore(3)  # three matches at a time keeps inside the deployment's request rate

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
    (ROOT / "web" / "data" / "eval.json").write_text(json.dumps(dict(summary=summary, matches=res), indent=1), encoding="utf-8")
    print(json.dumps(summary, indent=1))
    for m in res:
        for r in m["rows"]:
            print(m["match"], r["kind"], "OK " if r["correct"] else "MISS", r["detail"])


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1:]))
