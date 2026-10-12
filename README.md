# Matchday Newsroom

Entry for the Microsoft "Inside the Game" (Premier League) hackathon: AI agents that turn **synthetic**
football events into explainable match intelligence, narratives, and personalized experiences.

> All data is synthetic. Clubs, players and results are fictional. No real footage, data, names or badges are used.

## Status

| Piece | State |
|---|---|
| `matchsim/` synthetic match engine (events + tracking frames) | working, calibrated (see below) |
| `viewer/` 2D playback of a generated match | working |
| `analytics/` deterministic stats from events (take-on rate, dribbled past, PPDA, ratings, ...), batch and live | working |
| Director layer (story arcs) | dropped (not needed; real matches produce stories) |
| `newsroom/` agents on Microsoft Foundry + Agent Framework: stats analyst, tactics analyst, commentator (persona voices), fact-checker with rewrite loop, deterministic number/minute checks, template fallback, 429 back-off | working |
| `web/` match centre: replay with timed lower-third overlays; Fan / Analyst / Scout views; agent audit trail; team stats by minute; scout player view (every action, touch map, jump to replay); clips (suggested + from any action, adjustable range, JSON export) | working |
| Localizer and recap agents, favourite-club personalisation | planned |
| Azure deploy (static site + cached newsroom output) | planned |
| Clip video export (record the pitch canvas in the browser) | later |

## Quick start

```bash
pip install numpy scipy            # + matplotlib pytest for dev
python scripts/run_match.py 1      # writes out/match_1.json (events + 5 Hz tracking frames), ~8 s
python -m http.server 8765         # then open http://localhost:8765/viewer/index.html?src=../out/match_1.json
python scripts/calibrate.py 18     # simulate 18 matches and compare with real-football norms
python scripts/tactics_effect.py   # check that tactics measurably change behaviour
python scripts/match_stats.py out/match_1.json   # team and player statistics computed from events only
python scripts/bundle_viewer.py out/match_1.json   # one self-contained HTML file with the match inside (share it, works offline)
python scripts/run_by_role.py 18   # distance covered per position vs real-world norms
python scripts/realism.py 48       # style, wide play, restarts, pressing, officiating vs real norms (--home / --balance variants)
python scripts/tune.py 24 2        # auto-tune TUNE so every metric sits inside its band (coordinate search)
python scripts/tune_rs.py 24 3 36  # faster: regression surrogate + gradient descent, writes out/sensitivity.md
python scripts/export_tracking.py out/match_1.json 25   # 25 Hz tracking feed (npz)
python tests/test_sim.py           # determinism + invariants
```

Match centre (needs Azure for live agents; without `FOUNDRY_PROJECT_ENDPOINT` the newsroom uses a scripted offline client):

```bash
powershell -File infra/setup.ps1                      # one-time Azure setup: Foundry resource, project, gpt-4.1-mini deployment, role
python scripts/build_site.py out/match_1.json out/match_12.json   # run the newsroom, write web/data/
python -m http.server 8770 --directory web            # open http://localhost:8770/?m=match_12&p=scout  (p = fan | analyst | scout)
python tests/test_newsroom.py                         # newsroom plumbing + fact checks, offline
```

## How it was built

[DEVLOG.md](DEVLOG.md) documents the tuning method, debugging case studies and tuning runs.

## How the engine works

See [DESIGN.md](DESIGN.md). In short: decisions are event-driven and based on real geometry (lane interception,
receiver pressure, xG); movement is a 5 Hz kinematic model with marking, pressing, runs, support and block shape
driven by tactics; the ball is resolved dynamically, so outcomes follow from where players actually are.

## Credits and inspiration

The two-stream idea (ground-truth events plus tracking frames) follows the SoccER synthetic-data paper
(Morra et al., 2020) and the way commercial providers pair event and tracking data. No SoccER code or data is used.
