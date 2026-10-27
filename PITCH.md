# Pitch and demo video

Drafts for the submission form and the video. Edit freely; every number here is from the live app or `web/data/eval.json`.

## Elevator pitch (submission form)

**Football Kaleidoscope: Newsroom that explains.** A newsroom of AI agents that watches a football match and explains it as it happens: what just
happened, why it matters, and what it means for you, whether you are a casual fan, an analyst or a scout, in nine languages.

**What we built.** A synthetic football engine that plays full matches and records every event and every player's position
five times a second; a deterministic analytics layer that turns them into the numbers (pass difficulty, ball and shot speed,
pressing, shapes, defensive duties, fatigue, control vs chaos); and a multi-agent newsroom on **Microsoft Foundry** and the
**Microsoft Agent Framework** that turns those numbers into fact-checked live commentary, answers to any question, half-time and
full-time reports, and a bench assistant that tests two changes with paired simulations before recommending one.

**The problem.** A scoreline says nothing about why. Broadcasters and apps fill the gap with generic commentary that often says
things the match never showed. We wanted explanations a viewer can trust: every number comes from the data, a fact-checker agent
plus deterministic checks reject any line with an unsupported number, cause or motive, and a shape change is described by what the
players did (the back line dropped 6 m, the wing-backs pushed 8 m higher) rather than by what a formation's name suggests. We grade
the newsroom against the engine's own record of what happened: 60 answers, 0.85 correct, 92 % approved by the fact-checker.

**Microsoft technology.** Microsoft Foundry (gpt-4.1 writer and fact-checkers, gpt-4.1-mini analysts, tracing in Application Insights), Microsoft Agent Framework
(orchestrator, specialists as tools, writer, fact-checker with rewrites), Azure AI Speech (spoken commentary), Azure Container Apps
(a front end plus simulation workers that scale out per what-if and back to zero; managed identity, no keys), an MCP server with 17
tools for GitHub Copilot agent mode, GitHub Actions (tests, image build).

**Try it:** https://football-kaleidoscope.ashywave-faaedb6c.eastus2.azurecontainerapps.io (free, no login; all data synthetic).

## Video (under 2 minutes): structure

Built automatically from the live site (video/record.py drives a browser, video/build.py edits); the voice-over is read over the
finished cut. Target 1:50, leaving room under the 2:00 limit. One match throughout (Harlow Athletic v Stonebay Albion, 3-3), so the
story carries from shot to shot.

**What matters, in order.** The judges score five things equally: technical build, agent design, real-world use, UX and category fit.
The video spends its time where those are decided and only names the rest:

1. *Agents you can trust* (agent design, category fit): fact-checked commentary, the Ask trail, the bench assistant testing its own advice.
2. *Explainable match intelligence* (category fit, UX): the tactics board, a scout's player profile, the same moment for three viewers.
3. *Proof and build* (technical, real-world): paired simulations on Container Apps, the newsroom graded against the engine's truth, the architecture.

Cut or shortened from the first draft: the manual what-if slider (the bench assistant does the same thing with agents, so it is shown
instead), the shape-change card (folded into the Board), the long goal breakdown (one cut in the montage), the long welcome hold.

| # | Time | Screen | Voice-over | Why it is in |
|---|---|---|---|---|
| 1 | 0:00-0:05 | Welcome card, then **Explore on my own** | "A scoreline never tells you why. Football Kaleidoscope is a newsroom of AI agents that explains a match as it happens." | The problem and the product in one line |
| 2 | 0:05-0:18 | **Next moment** to the 13' goal: the caption on the pitch, the Fan feed with its **Fact-checked** badge, Control vs chaos and the win probability above it | "Every key moment goes to two analysts and a commentator, and a fact-checker rejects any line the data doesn't support." | The core use; the brief's control vs chaos on screen |
| 3 | 0:18-0:25 | Same minute: **Fan, Analyst, Scout** | "The same moment, for a fan, an analyst and a scout." | Personas (UX) |
| 4 | 0:25-0:32 | **Supporting** Stonebay (the list opens on screen), Settings: **Español** | "Your club, your language: nine of them. The facts never change." | Personalisation, quickly |
| 5 | 0:32-0:47 | **Ask**: "How did Harlow Athletic throw away a 3-1 lead?", the answer, then **How the newsroom worked it out** | "Ask anything. An orchestrator on Microsoft Foundry sends specialist agents to the tools, a writer answers for you, and a fact-checker signs it off. Every step is in the trail." | Agent design, the headline |
| 6 | 0:47-0:56 | Analyst **Board**: the shape with and without the ball, then the **pass network** | "Tactics are read from the tracking: the shape in and out of possession, who passes to whom." | Explainable tactics |
| 7 | 0:56-1:05 | Scout: hover a player on the pitch (the ring), click: rating breakdown, every touch on the pitch | "Click any player for a scout's view: what made his rating, and every touch." | The scout use case, hover to click |
| 8 | 1:05-1:21 | **What if > Advise the manager** at 68': the two proposals, the paired results, the fact-checked recommendation | "At 68 minutes, the bench assistant proposes two changes and tests each one: the rest of the match replayed twelve times with and without it, on Azure Container Apps." | The strongest agent feature, and the scale-out |
| 9 | 1:21-1:33 | Montage, about 2 s each with a one-word caption: **Breakdown** of a goal, **3D**, **Voice** (the app speaks), **Live run** (cards drafting), **Broadcast pack**, **GitHub Copilot** | "There's more: goal breakdowns, a 3D view, spoken commentary, live runs, a broadcast pack, and seventeen match tools for GitHub Copilot." | Breadth, named not explained |
| 10 | 1:33-1:40 | Stats: **Answers vs ground truth** | "Because the data is synthetic, we grade the newsroom against the truth: eighty-five percent correct." | Evidence |
| 11 | 1:40-1:47 | Architecture diagram | "Microsoft Foundry, the Agent Framework and Azure Container Apps, end to end." | Technical build, Microsoft stack |
| 12 | 1:47-1:50 | End card with the link | "Football Kaleidoscope: newsroom that explains. Try it live." | The call to action |

Left to the live link and the README: the Clips tab, the focus metric, the Studio style and themes, the other four matches, the
manual what-if sliders, heat maps beyond the Live tab.

Production: the cursor moves on an eased path (about 0.7 s a move) and rests before each click; a dropdown opens in the page as a
list so its choice is visible; cuts inside a shot remove only waiting; crossfades of 0.35 s between shots, no zooms, no music, captions
only in the montage. The app's spoken commentary in the montage is left quiet under the voice-over.
