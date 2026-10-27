# Football Kaleidoscope: product plan

An AI newsroom that **investigates** a match rather than just narrating it. The engine produces the facts, the analytics compute
every number, and the agents decide what matters, investigate it with tools and explain it to each audience, with every line
fact-checked. All data is synthetic; no real clubs, players or footage.

Status markers: **[built]**, **[partly]**, nothing = to do. Priority of features: **Core** (needed for the demo and judging),
**Strong**, **Stretch**.

## Use cases

### Casual viewer: wants the story, not the numbers
- **C1** Follow a match live and see at a glance what is happening and who is on top.
- **C2** Be told when something important happens, and in one sentence why it matters. **[built]**
- **C3** Missed the match: a 60-second recap of the story, the turning points and the result. **[built]**
- **C4** Watch the key moments as short clips with a caption. **[partly]**
- **C5** Read everything in their own language. **[built]**
- **C6** Ask a simple question in plain words ("Why was that a penalty?", "Who's been best?"). **[built]**

### Club fan: one team's side of the story, or one player's
- **F1** Choose a club and get commentary and the recap told from its side. **[partly: answers]**
- **F2** See how their players are rated, and why.
- **F3** Understand why the team is winning or losing ("Why can't we get the ball forward?").
- **F4** Have the manager's changes explained (subs, formation, pressing) and whether they worked.
- **F5** Share a clip or recap card of a goal or moment.
- **F6** Follow one player instead of a team: his match told as a story (key moments, rating, how he played), in plain language. **[built]**
- **F7** Get that player's highlights as a clip reel and a short "his match" recap.
- **F8** Get a notification-style line whenever the followed player does something that matters. **[built]**

### Analyst: coach, pundit, data-curious fan
- **A1** See the shape each team actually played, in and out of possession, and when it changed. **[built: tools + answers]**
- **A2** See pressing intensity and where the ball was won back, by period. **[built]**
- **A3** See the momentum: xG and territory over time, with spells of dominance marked. **[built]**
- **A4** See passing networks: who connects, where attacks start, which side they favour. **[built]**
- **A5** Have a goal or chance broken down: the sequence and why each decision was made.
- **A6** Compare halves, or before and after a tactical change, with the evidence.
- **A7** Ask a tactical question and get an answer backed by numbers and pitch views. **[built: numbers; pitch views to do]**
- **A8** Get a half-time and a full-time tactical report. **[built]**
- **A9** Trust the output: every number traceable and checked, and the AI's tactical reading scored against what the engine actually did. **[partly]**

### Scout: assesses players
- **S1** Pick any player and see every action, filterable, each one jumping to the replay. **[built]**
- **S2** See a player's touch map, heat map and physical output (distance, sprints, top speed). **[partly]**
- **S3** Get a written player report: strengths, weaknesses and role fit, backed by the events. **[built: via Ask]**
- **S4** Get a clip reel built automatically for a trait ("all his progressive passes", "duels lost").
- **S5** Compare a player with his role's average or with another player.
- **S6** Ask a question about a player ("How did #9 cope with the press?"). **[built]**

### Broadcast producer or content creator
- **P1** Timed, machine-readable overlays (lower thirds, stat cards) to put over a feed. **[built: broadcast pack]**
- **P2** Suggested clips for key moments, an adjustable range, and export. **[built: JSON]**
- **P3** Talking points for the half-time studio segment. **[built: broadcast pack]**
- **P4** The same match in several languages and tones (pundit, fan, kids). **[built: languages, fan/analyst tones]**

## Features

### 1. Analysis toolkit (fixed calculations, no AI) — Core **[built]**
The functions the agents call. Every number comes from here; the AI never computes a figure.

| Tool | Gives | Use cases |
|---|---|---|
| `shape` | Average positions by team, phase and period; the formation they read as; when it changed | A1, F4, A6 |
| `pressing` | Pressing height, PPDA by 15 minutes, where turnovers were won, counter-press wins | A2, F3 |
| `momentum` | Cumulative xG, territory and field tilt over time; spells of dominance | A3, C1, F3 |
| `passing_network` | Pass links, key connectors, where attacks start, preferred side | A4 |
| `sequence` | The chain of events before a moment, with the engine's reasons for each decision | A5, C2 |
| `player_profile` | Event stats, heat map, physical output from tracking, comparison with role averages | S2, S3, S5, F2, F6 |
| `find_moments` | Every moment matching a filter (player, action, outcome, zone, period) | S4, F7, C4, P2 |

### 2. Specialist agents on Foundry and Agent Framework — Core
- **Live desk** **[built]**: picks the moments that matter (goals, big chances, momentum swings, tactical changes, standout spells, the followed player's moments). C2, F1, F8
- **Tactics analyst** **[built]**: reads shape, press and momentum; explains changes and whether they worked. A1-A6, F3, F4
- **Scout** **[built]**: player reports and trait clip reels; for fans, the same at a casual depth ("his match"). S3-S5, F6, F7
- **Recap editor** **[built: fan quick, analyst deep]**: half-time and full-time reports at each depth (60-second casual, full analyst, one player). C3, A8, P3, F7
- **Writer** **[built]**: turns any finding into the chosen persona, depth and club or player point of view. F1, F6, P4
- **Localizer** **[built]**: translates while keeping names and numbers intact. C5, P4
- **Fact-checker** **[built]**: checks every published line against the toolkit's numbers, rewrites or holds lines, falls back to a plain line. A9

### 3. Ask the newsroom (orchestrator and chat) — Core **[built]**
A question goes to an orchestrator that hands it to the right specialists. They call tools, the writer phrases the answer for the
user's profile, and the fact-checker checks it before it is shown. Answers can carry a pitch view or chart and "watch" links. The
agent steps are visible in the UI. C6, F3, A7, S6

### 4. Tactics scored against the truth — Core (the hook) **[built]**
The engine logs every formation change, tactical shift and the manager's reason. The tactics agent's reading is scored against
that log (changes detected, timing, explanation), shown as an "AI accuracy" panel and reported over many matches. A9

### 5. Personal profile — Core
Persona (fan, analyst, scout; casual = fan at quick depth), favourite club **or** followed player, language, units (metres, yards
or feet; km/h, mph or m/s; km or miles) **[built]**, depth (quick, standard, deep) and one focus metric. Every agent output reads it. C5, F1, F6, all others

### 6. Match centre views
- Live view with momentum strip, win probability and lower thirds **[built]** — Core. C1, C2
- Player-follow mode for fans: the pitch highlights him, the feed is his story, his rating and highlights — Core. F6-F8
- Analyst board: shape, press, network and momentum charts synced to the replay — Core **[built]**. A1-A4
- Scout view **[built]** plus heat map, physical output and the written report — Core. S1-S3
- Recap page, shareable, with clips and a summary card — Strong. C3, F5
- Ask panel — Core (feature 3)
- Breakdowns **[built]**: each goal and big chance replayed with pauses, drawn on the pitch (both teams' lines read from that moment, the defenders' block, last line and space between the lines, open passing lanes and triangles, forward runs, numbers around the ball, the shooter's view of goal), a caption built from the data at each stop — Strong. C1, A1, P1
- Hover and click on everything drawn over the pitch (pass map links, average positions, regains, a player's actions): details on hover, click jumps to the event — Strong. A1-A4, S1
- Clips **[built]**, trait and player reels — Strong; video export — Stretch. S4, F7, P2

### 7. Broadcast output — Strong **[built]**
Timed JSON overlays for each persona and language, a talking-points sheet for each half, the clip JSON export **[built]**. P1, P3, P4

### 8. Platform — Core
Backend API on Azure Container Apps for questions and on-demand analysis; the static site on Azure with cached newsroom output so
judges can use it free; Foundry deployment **[built]**, rate-limit handling **[built]**, agent tracing to Application Insights, shown in the Foundry portal **[built]**; live questions rate-limited
for cost.

## Demo story (2 minutes)
1. Casual or fan: live match with lower thirds; switch club, follow one player, change language.
2. Analyst: ask "why did they concede?"; the orchestrator shows the agents working; the answer arrives with a pitch view; then the
   AI accuracy score against the engine's ground truth.
3. Scout: pick a player, read the report, play the trait clip reel.
4. Producer: export overlays and clips.

## Build order
1. Analysis toolkit (feature 1) with tests, including formation reading checked against the engine's log.
2. Tactics analyst, scout and recap agents on the toolkit (feature 2); ground-truth scoring (feature 4).
3. Personal profile (feature 5) and player-follow mode (F6-F8).
4. Ask the newsroom with the backend API (features 3 and 8).
5. Analyst board and recap page (feature 6), broadcast output (feature 7).
6. Deploy, demo video, submission.
