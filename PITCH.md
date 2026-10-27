# Pitch and demo video

Drafts for the submission form and the video. Edit freely; every number here is from the live app or `web/data/eval.json`.

## Elevator pitch (submission form)

**Matchday Newsroom** is a newsroom of AI agents that watches a football match and explains it as it happens: what just
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

**Try it:** https://matchday-newsroom.ashywave-faaedb6c.eastus2.azurecontainerapps.io (free, no login; all data synthetic).

## Video (under 2 minutes): shot list

Record at 1920x1080 on the live link, browser full screen with the page zoom at 125% (Ctrl +) so the text reads on video, no music
with rights issues (silence or a voice-over). Speed 5x unless noted. About 118 s total. Plain cuts and a voice-over; no transitions or effects (see the note below the table). Before recording: open the link once (the
app wakes up), then open a private window so the welcome card shows; open the What-if tab once so the workers are warm.

| # | Time | Screen | Voice-over (suggested) |
|---|---|---|---|
| 1 | 0:00-0:08 | The link opening on the welcome card (Harlow Athletic v Stonebay Albion), then **Explore on my own** | "A scoreline doesn't say why. Matchday Newsroom is a team of AI agents that explains a match as it happens." |
| 2 | 0:08-0:22 | Press **Next moment** twice: the 13' goal, the lower-third overlay and the Live feed | "Every key moment is covered by four agents: two analysts, a commentator, and a fact-checker that rejects anything the data doesn't show." |
| 3 | 0:22-0:32 | Switch persona **Fan -> Analyst -> Scout** at the same minute | "The same moment, written for a fan, an analyst and a scout." |
| 4 | 0:32-0:42 | **Supporting** dropdown above the panel: Stonebay Albion; Settings: language **Español**; feed and overlay re-render | "In nine languages, from your club's side." |
| 5 | 0:42-0:58 | Analyst, **Feed** at about 76': the 72' shape-change line, then its **Show shape** button (the new shape drawn on the pitch) | "When a team changes shape, the newsroom says what the players actually did, measured from tracking: the back line, the block, the wing-backs." |
| 6 | 0:58-1:12 | **Ask** at full time (3-3): "How did Harlow Athletic throw away a 3-1 lead?" (cached is fine); open "How the newsroom worked it out" | "Ask anything. An orchestrator sends specialists to the tools, a writer answers for you, a fact-checker signs it off, and the whole trail is visible." |
| 7 | 1:12-1:30 | **What if** (open the tab first so the workers warm up): pressing up from 60', **Play it out**; pairs fill in | "What if they'd pressed higher? Twenty-four simulations run in parallel on Azure Container Apps, each pair with the same random numbers, so only the change differs." |
| 8 | 1:30-1:38 | **Clips > Breakdown** of a goal: the move paused and drawn | "Every goal can be broken down: where the ball was won, the open lanes, the shot." |
| 9 | 1:38-1:43 | Montage, about 1 s per cut with a one-word caption: **3D** view, **Voice** (spoken commentary), **Live run** (cards arriving), the **Recap** half-time report, GitHub Copilot agent mode calling one of the MCP tools in VS Code (guide Clip 12; Harbour records the Copilot cut) | "Plus a 3D view, spoken commentary, live runs, match reports, and seventeen tools for GitHub Copilot." |
| 10 | 1:43-1:52 | **Stats**: "Answers vs ground truth" table | "And because the data is synthetic, we can grade the newsroom against what really happened." |
| 11 | 1:52-1:58 | Architecture diagram from the README, or the logo with the link | "Microsoft Foundry, Agent Framework, Azure Container Apps. Matchday Newsroom." |

The montage takes the slot of the old heat-map shot; the team heat map (guide Clip 9) is spare footage for it. Record with the
computer in dark mode so the site uses its dark theme (it follows the system setting). Under the montage, keep the app's own
spoken commentary low so the voice-over stays on top.

Tips: do one dry run first (it warms the What-if workers and caches the Ask answer); keep the cursor still while text renders;
cut dead time in the editor rather than re-recording.

Editing: hard cuts between shots, the voice-over carrying the story, short on-screen captions only where the voice-over names
something (a number, "24 simulations"). Skip transitions, zooms and music beds: judges score clarity and what the product does,
and effects cost time that the 2-minute limit needs for features.
