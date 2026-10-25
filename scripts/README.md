# scripts

Run from the repository root with the project's Python (`.venv/Scripts/python.exe scripts/<name>.py ...`). Each script's docstring
says what it does and how to call it.

## The pipeline

| Script | What it does |
|---|---|
| `run_match.py` | Simulate one match: events and 5 Hz tracking to `out/match_<seed>.json` |
| `matchday.py` | A round of fixtures between the league's clubs (the demo matches) |
| `build_site.py` | Turn a match into what the match centre plays back (`web/data`, `server/data`), with the newsroom's feed and recaps; `--keep-agents` recomputes only the deterministic parts |
| `refresh_moments.py` | Re-cover one kind of moment, or the recaps, in the stored site data without a full rebuild |
| `run_newsroom.py` | Cover a match with the newsroom on its own |
| `eval_newsroom.py` | Grade the newsroom's answers against the engine's own record (`web/data/eval.json`) |
| `formation_eval.py`, `formation_misses.py` | How well the formation reader recovers the engine's system changes, and why it misses |
| `event_schema.py` | Regenerate `EVENTS.md` |
| `match_stats.py`, `export_tracking.py`, `bundle_viewer.py` | Print a match's stats, export a 25 Hz tracking feed, bundle a match with the viewer into one HTML file |
| `trim_glb.py` | Trim the 3D player model to the animations the match centre uses |

## Calibration and realism

`calibrate.py`, `realism.py`, `tune_norms.py` (the loss against real event data), `tune.py` and `tune_rs.py` (earlier tuners),
`norms.py`, `norms360.py`, `foot_norms.py`, `fit_xg.py`, `fit_pass_policy.py`, `xt.py` and `role_norms.py` (real-data reference
tables), `tactics_effect.py`, `trait_effect.py`, `run_by_role.py`, `fatigue_profile.py`, `ratings_probe.py`, `brain_metrics.py`.

## Probes

Every other script is a probe: one football question asked of real data (StatsBomb open data, Euro 2020 360 freeze frames) and of
simulated matches, side by side: how possessions end, where take-ons and cut-backs happen, how a back line holds, what follows a
blocked shot, and so on. `scenarios.py`, `snapshot.py`, `sequence.py`, `shot_snaps.py` and `shot_chains.py` render situations for a
human to judge. Each probe's findings, and what was changed or not because of them, are in [DEVLOG.md](../DEVLOG.md).
