# Handoff: Matchday Newsroom (state as of 2026-10-15)

Read this first in a new session, then DEVLOG.md (newest entries just above "Is the tuner machine learning?") for detail.

## What it is

Entry for the Microsoft "Inside the Game" hackathon (build closes **Oct 27 2026, 11:59pm PT**; registration done). A synthetic
football match engine (`matchsim/`) feeds deterministic analytics (`analytics/`) and a multi-agent newsroom on **Azure AI Foundry
+ Microsoft Agent Framework** (`newsroom/`), shown in a static match centre (`web/`) served by an aiohttp server (`server/`).
Personas: Fan, Analyst, Scout. Features: live feed with fact-checked commentary, Ask (Q&A with the agent desk), Board (shapes,
networks, press, momentum), What-if (paired simulations from any minute), Recap (half/full-time reports), Breakdowns (pause and
draw on key moments), Clips + JSON/broadcast-pack export, units (metric/imperial) passed to the agents, languages, voice.
Team of two: the user (technical) and a partner (football knowledge, no coding). Repo: github.com/Nano-233/matchday-newsroom (main).

## Status

| Area | State |
|---|---|
| Engine | Tag `engine-v5.1` (2026-10-18): glycogen fatigue, hard-run recovery, contested loose-ball race, cut-back man at the byline (DEVLOG "Fatigue, recovery runs and loose balls", "Big chances: the cut-back man"); real-data loss 128.9 -> 121.6 on 96 unseen matches. Open gaps: big chances 0.54 vs 0.86 (cut-backs still about 0.01 vs 0.25), shots 15.7 vs 12.9, sprinting 90 s vs about 30 s a player, support too far from the passer. |
| Demo data | 5 matches rebuilt on engine-v4 and committed (`web/data`, `server/data`): match_1, match_7, RED-ASH, HAR-STB, WMW-OAK. What-if replays reproduce each exactly. |
| Newsroom accuracy | `web/data/eval.json` on the engine-v5.1 matches: overall 0.88, 96 % fact-checker approved, 10.3 s an answer; goals, best player and pressing right; formation changes found 1/5 (the known weak spot: the reader). An earlier v5 set scored 0.96 only because it had almost no formation changes. |
| Walkthrough | Fan / Analyst / Scout walked; fixes in DEVLOG "Persona walkthrough fixes" and "A red card given to the wrong team". Scout ratings are full-match by design (labelled). |
| Match metrics | Pass length / difficulty / hard passes, shot and ball speed, on-pitch player tags (sprints, distance, man on the ball) shown (DEVLOG "Pass quality, ball speed and player tags"). `build_site.py --keep-agents` rebuilds the deterministic parts without model calls. |
| Live run | `GET /api/live` + "Live run" button: events streamed on the server clock, newsroom runs per moment on Foundry (about 7 s a goal), one run at a time. DEVLOG "Live run". |
| Defensive duties | `MatchView.duties` reads closing down, man tracked, space held from tracking only; scored against the engine job log (`jobs`, logging only, matches identical): frames 0.63-0.69, tracked man 64-81 %. Board "Duties", Scout block, agent tool `defensive_duties`. DEVLOG "Defensive duties". |
| Rhythm, metric, milestones | `MatchView.rhythm` (control vs chaos by 5 min), "Your metric" (per-minute leaders), `MatchView.milestones`. DEVLOG "Control vs chaos, a metric to follow, milestones". |
| Bench assistant + fatigue | `Desk.advise` (POST `/api/assistant`, What-if tab "Advise the manager"): reads the game, proposes two changes, tests each with 12 paired replays, the pick is made in code (expected points +0.15 and 2+ changed results), writer + checker explain. About 105 s. `MatchView.fatigue` (tracking only, r 0.50 with true stamina), agent tool `fatigue`, Scout "Running hard". |
| Deploy | **Ready, not done.** `infra/deploy.ps1` (Azure Container Apps, managed identity, scale to zero). Image verified locally by a copy of exactly what the Dockerfile ships (server loads, replay matches). Needs the user's explicit go-ahead: it creates paid resources. |
| README / pitch / video | Last. Video < 2 min, public repo, free working test link required by the rules. |

## Azure

- Subscription a3c0eba6..., resource group `rg-matchday-newsroom`, Foundry account `matchday-newsroom-a3c0eb` (eastus2), project
  `newsroom`, deployment `gpt-4.1-mini` (GlobalStandard, capacity 50 = 50 requests and 50K tokens a minute). App Insights
  `matchday-newsroom-a3c0eb-insights` (tracing on via `newsroom/clients.py`).
- Env: `FOUNDRY_PROJECT_ENDPOINT`, `FOUNDRY_MODEL` (set in the user's env; also in `../.claude/launch.json`).
- `az` lives at `C:\Program Files\Microsoft SDKs\Azure\CLI2\wbin` (use PowerShell; not on the Bash PATH).
- The app's identity needs the **Foundry User** role (deploy.ps1 assigns it).
- Rate limit is the main cause of slow answers and errors when a site build and an eval run at the same time. Raising the
  deployment capacity was proposed; it is the user's decision (not done).

## Run things

```
.venv/Scripts/python.exe -m server.app                      # match centre + API on :8080 (launch.json "newsroom-server" uses :8090)
.venv/Scripts/python.exe scripts/run_match.py 7             # simulate a match -> out/match_7.json
.venv/Scripts/python.exe scripts/matchday.py                # the three club demo matches -> out/RED-ASH.json etc.
.venv/Scripts/python.exe scripts/build_site.py out/X.json   # web/data/X + server/data/X.json.gz (calls the model: ~8 min a match)
.venv/Scripts/python.exe scripts/build_site.py --keep-agents server/data/X.json.gz   # deterministic parts only, keeps feed and recaps
.venv/Scripts/python.exe scripts/eval_newsroom.py out/match_1.json out/match_7.json out/RED-ASH.json out/HAR-STB.json out/WMW-OAK.json
.venv/Scripts/python.exe scripts/eval_newsroom.py --only=formation ...   # one question kind, prints answers, leaves eval.json alone
.venv/Scripts/python.exe scripts/formation_eval.py 24       # formation reader against engine truth on fresh matches (no model)
.venv/Scripts/python.exe tests/test_tools.py ; tests/test_newsroom.py ; tests/test_stats.py ; tests/test_sim.py   # plain scripts, no pytest
.venv/Scripts/python.exe scripts/fatigue_profile.py 24 ; scripts/sprint_causes.py 8   # running by 15 minutes vs research; where sprinting comes from
.venv/Scripts/python.exe scripts/tune_norms.py eval 96 seed0=6000 [key=value]          # real-data loss on unseen matches (engine changes: compare before/after)
```
Never run a site build and an eval at the same time (429s). Builds and evals retry 429/5xx/timeouts in `newsroom/agents._run`.
Server answer cache: `server/cache` (git-ignored), keyed by match data, question, profile and minute.

## Architecture rules (keep)

- Engine = facts only; analytics = every displayed number (deterministic); agents = insight, wording, personalisation, fact-check.
- Agents only quote tool numbers. Deterministic checks back the LLM fact-checker: `unsupported_numbers`, `wrong_leader`,
  `wrong_team` (newsroom/agents.py). Add a deterministic check whenever the LLM checker lets a factual slip through.
- Minutes: always convert with `matchsim.whatif.t_at_minute(events, minute, meta["half_time"])` (clock rule; second half restarts
  after first-half stoppage). Never `minute * 60`.
- Formation reader: `MatchView.formation_timeline` (5-min sliding window, confirm 2 min later, reverts within 20 min dropped,
  `confirmed_t`). Scoring ignores changes nobody could see (`truth()["unseeable_changes"]`).
- Desk flow (`newsroom/desk.py`): orchestrator -> tactics_analyst / scout (as tools) -> writer -> fact-checker (up to 2 rewrites).

## User's standing rules

- Terse replies (caveman + ponytail modes): short, decisions stated, minimal code, no unrequested features or extras.
- Engine work must be logic-driven (football cause), never stat-fitting; a logic fix that is right against real data stays even if
  the loss gets worse; recalibrate numbers afterwards. Check DEVLOG before trying anything, so no attempt is repeated.
- Keep DEVLOG.md updated (what was seen, measured, fixed, tried and not kept).
- Commit messages end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`. Push to main.
- Never use real clubs, players or badges. Data must be synthetic (rule 4.7).
- Ask before anything that costs money or is outward-facing (deploy, quota change).

## Open items, in order

1. Make the repo public (user's call; it is private now, the rules need it public). Check the history for anything sensitive first.
2. Deploy (on go-ahead): `./infra/deploy.ps1` in PowerShell from the repo root; then open the URL, check Ask, What-if, Recap, Live run.
   Optional: an Azure budget alert on the resource group (the server's daily allowance resets when the app restarts).
3. Eval: add duty and pass-quality questions to `scripts/eval_newsroom.py`, scored against engine truth (jobs log).
4. README (status table is stale: quick start, architecture, Microsoft tech used, judge test instructions), pitch, < 2 min video.
5. Known, can live with: formation 0.6 (reader ceiling: the engine's 4-3-3 plays as 4-1-4-1); Live run queued/drafting states not
   seen on screen; phone layout not rechecked since the new features.

## Key files

`matchsim/engine.py` (engine), `matchsim/whatif.py` (paired what-if, t_at_minute), `analytics/tools.py` (MatchView: shapes,
formation reader, pressing, momentum, win probability, breakdowns, find_moments, truth), `analytics/stats.py` (stats, ratings),
`newsroom/desk.py` (Ask desk + prompts), `newsroom/agents.py` (live commentary, checks, `_run`), `newsroom/moments.py`,
`newsroom/clients.py` (Foundry client, tracing), `server/app.py`, `web/app.js` / `index.html` / `app.css`,
`scripts/build_site.py`, `scripts/eval_newsroom.py`, `infra/setup.ps1` / `deploy.ps1`, `DEVLOG.md`, `DESIGN.md`, `EVENTS.md`.
