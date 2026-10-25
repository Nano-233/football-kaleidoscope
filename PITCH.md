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
the newsroom against the engine's own record of what happened: 60 answers, 0.83 correct.

**Microsoft technology.** Microsoft Foundry (gpt-4.1-mini, tracing in Application Insights), Microsoft Agent Framework
(orchestrator, specialists as tools, writer, fact-checker with rewrites), Azure AI Speech (spoken commentary), Azure Container Apps
(a front end plus simulation workers that scale out per what-if and back to zero; managed identity, no keys), an MCP server with 17
tools for GitHub Copilot agent mode, GitHub Actions (tests, image build).

**Try it:** https://matchday-newsroom.ashywave-faaedb6c.eastus2.azurecontainerapps.io (free, no login; all data synthetic).

## Video (under 2 minutes): shot list

Record at 1920x1080 on the live link, browser full screen, no music with rights issues (silence or a voice-over). Speed 5x unless
noted. About 115 s total.

| # | Time | Screen | Voice-over (suggested) |
|---|---|---|---|
| 1 | 0:00-0:08 | Title card or the match page loading, `HAR-STB`, Fan view | "A scoreline doesn't say why. Matchday Newsroom is a team of AI agents that explains a match as it happens." |
| 2 | 0:08-0:22 | Press **Next moment** twice: the 13' goal, the lower-third overlay and the Live feed | "Every key moment is covered by four agents: two analysts, a commentator, and a fact-checker that rejects anything the data doesn't show." |
| 3 | 0:22-0:32 | Switch persona **Fan -> Analyst -> Scout** at the same minute | "The same moment, written for a fan, an analyst and a scout." |
| 4 | 0:32-0:42 | Settings: language **Español**, favourite club Stonebay; feed and overlay re-render | "In nine languages, from your club's side." |
| 5 | 0:42-0:58 | Analyst, **Feed** at 72'-89': a shape-change line; then **Board > Shape** with the dashed back line | "When a team changes shape, the newsroom says what the players actually did, measured from tracking: the back line, the block, the wing-backs." |
| 6 | 0:58-1:12 | **Ask**: "Why did Harlow Athletic concede?" (cached is fine); open "How the newsroom worked it out" | "Ask anything. An orchestrator sends specialists to the tools, a writer answers for you, a fact-checker signs it off, and the whole trail is visible." |
| 7 | 1:12-1:30 | **What if** (open the tab first so the workers warm up): pressing up from 60', **Play it out**; pairs fill in | "What if they'd pressed higher? Twenty-four simulations run in parallel on Azure Container Apps, each pair with the same random numbers, so only the change differs." |
| 8 | 1:30-1:40 | **Clips > Breakdown** of a goal: the move paused and drawn | "Every goal can be broken down: where the ball was won, the open lanes, the shot." |
| 9 | 1:40-1:50 | **Stats**: "Newsroom answers scored against the truth" table | "And because the data is synthetic, we can grade the newsroom against what really happened." |
| 10 | 1:50-1:57 | Architecture diagram from the README, or the logo with the link | "Microsoft Foundry, Agent Framework, Azure Container Apps. Matchday Newsroom." |

Tips: do one dry run first (it warms the What-if workers and caches the Ask answer); keep the cursor still while text renders;
cut dead time in the editor rather than re-recording.
