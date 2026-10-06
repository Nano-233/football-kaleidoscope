# Matchday Newsroom

Entry for the Microsoft "Inside the Game" (Premier League) hackathon: AI agents that turn **synthetic**
football events into explainable match intelligence, narratives, and personalized experiences.

> All data is synthetic. Clubs, players and results are fictional. No real footage, data, names or badges are used.

## Status

| Piece | State |
|---|---|
| `matchsim/` synthetic match engine (events + tracking frames) | working, calibrated (see below) |
| `viewer/` 2D playback of a generated match | working |
| Director layer (story arcs: red card, comeback, ...) | planned |
| Agents (stats, tactics, commentators, localizer, fact-checker, recap) on Microsoft Foundry / Agent Framework | planned |
| Event streaming + overlay output + web UI + Azure deploy | planned |

## Quick start

```bash
pip install numpy scipy            # + matplotlib pytest for dev
python scripts/run_match.py 1      # writes out/match_1.json (events + 5 Hz tracking frames), ~8 s
python -m http.server 8765         # then open http://localhost:8765/viewer/index.html?src=../out/match_1.json
python scripts/calibrate.py 18     # simulate 18 matches and compare with real-football norms
python scripts/tactics_effect.py   # check that tactics measurably change behaviour
python scripts/run_by_role.py 18   # distance covered per position vs real-world norms
python tests/test_sim.py           # determinism + invariants
```

## How the engine works

See [DESIGN.md](DESIGN.md). In short: decisions are event-driven and based on real geometry (lane interception,
receiver pressure, xG); movement is a 5 Hz kinematic model with marking, pressing, runs, support and block shape
driven by tactics; the ball is resolved dynamically, so outcomes follow from where players actually are.

## Credits and inspiration

The two-stream idea (ground-truth events plus tracking frames) follows the SoccER synthetic-data paper
(Morra et al., 2020) and the way commercial providers pair event and tracking data. No SoccER code or data is used.
