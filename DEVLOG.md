# Engineering log

How the synthetic match engine was built, debugged and tuned, and why. Append to this file as the project goes on:
every non-trivial change gets an entry (what we saw, what we guessed, what the measurement said, what fixed it).

## Method

The engine has to produce data a football fan or analyst would accept as real. "Looks plausible" is not testable, so the
loop is always the same:

1. **Targets.** Write down rough real-football norms as bands (goals 2.4-3.1, pass accuracy 0.78-0.88, ball in play 55-68%,
   ...). They are tuning targets from memory, not redistributed data, and they live in `scripts/calibrate.py` and
   `scripts/realism.py`. When a norm is uncertain, say so and keep the band wide.
2. **Measure, do not eyeball.** Simulate many matches in parallel and compare means with the bands. One match proves
   nothing; 24-48 matches give usable means (goal count noise is about +/-0.3).
3. **Decompose before changing anything.** When a number is off, split it until the cause is visible, using throwaway
   scripts (scratchpad, not committed unless reusable): decisions by pitch third, shot sources by kind and by role, pass
   outcomes by estimated vs actual success, spell lengths, receiver position relative to the offside line.
4. **Fix the root cause, then re-measure.** Changing the symptom's own knob usually just moves the problem (see the
   shot-volume story below). Prefer the change that makes behaviour more football-like over the one that hits the number.
5. **One tuning table.** Behaviour coefficients live in `engine.TUNE`; role and phase tables live in `movement.py`.
   Sweeps can override TUNE from the command line (`calibrate.py key=value`).
6. **Let a search balance the table, not a human.** Parameters interact (more forward passing means more shots means more
   goals), so after structural fixes `scripts/tune.py` does a coordinate search on common random numbers (same seeds for
   every candidate) and minimises the distance outside all bands at once. Structural gaps (missing behaviour) cannot be
   tuned away: if a metric will not move, the model lacks something.
7. **Keep tests and determinism.** `tests/test_sim.py` checks the same seed gives the same match and basic invariants.
8. **Commit every iteration** with the measured effect in the message.

Rules learned the hard way:
- Do not edit sim files while a tuner run is going: its workers re-import them on every evaluation.
- Never run multiprocessing from `python - <<EOF` on Windows (spawn re-imports `__main__`; it forks a runaway storm).
  Put the script in a file.
- Check how many cores the machine has before choosing worker counts (24 here; we ran 6 for a day).
- Parameter bounds must not allow impossible values (a pass-error scale went negative at a low tuned value).
- A metric that only counts a label (pressure events) can hide a real behavioural mismatch: investigate it.

## Design decisions (and why)

- **Engine style**: event-driven on-ball decisions over real geometry plus 5 Hz kinematic movement, not a full
  per-tick agent simulation (too much behaviour to tune) and not pure random events (no space, no xG, no tracking).
  Researched: SoccER (synthetic tracking + events from a game engine), FootballEngine (40 Hz F# 2D), minute-by-minute
  zone simulators, Google Research Football. Borrowed ideas only; no code.
- **Two output streams**: ground-truth events and tracking frames, like a real provider.
- **Explanation ground truth**: each decision records the options considered and why one won (`why`), so the explain
  and fact-check agents can cite real causes.

## Case studies

### Shots: 150 per match (target 25)
Hypothesis 1: shot utility too high. Raising the shot cost barely helped. Decision profile by pitch third showed carriers
*carrying* into the box instead of passing (take-ons were free). Added an explicit take-on duel and cheaper passing risk.
Still too many. Shot-source breakdown showed the quality distribution (distance, xG) was realistic but the *number of
box entries* was 4x real: defenders collapsed onto their goal line (the whole block followed the ball with no depth
floor) and nobody blocked the shot line. Fixes: minimum depth per role, and three "cover" defenders who drop onto the
ball-goal line when the ball is near their goal. Goals/shots then came into band.

### Pass completion that did not match its own estimate
Chosen passes had an estimated success of 0.82 but 0.60-0.70 happened in the 0.7-0.85 bucket. Logging where interceptions
occurred showed most "interceptions" were losses of passes that arrived loose: the receiver went to the *aimed* point
while the ball flew to the point with execution error, so a nearby marker won it. The receiver now tracks the real ball
flight and can stretch further. Completion went 0.59 to 0.86 with no other change. Lesson: compare estimate vs actual
before touching probabilities.

### Pressing and interceptions looked fine but were not
Pressure events were 780 per match (real about 300) and passes per defensive action 7 (real 9-12): defenders hounded
the carrier. Pressing radius, number of pressers and the commit rate were reduced; pass-block events were split from
true interceptions (a defender next to the passer blocks, he does not read the pass).

### Offsides (0.1 per match, real about 4-5)
Receivers were 10-30 m behind the defensive line at pass time. Forwards now hover around the last defender with a
slow oscillation, runs end around the line, and the passer perceives the line with a 0.8 s lag so mistimed runs are
flagged. Measured the ceiling first (ignore the offside check entirely: about 2 per match) to prove the positioning
was the limit, not the passer's awareness.

### Running volume by position
Everyone ran about the same, and strikers ran the most. Role energy table (`movement.CRUISE`) now gives central
midfielders about 12 km, centre-backs 9, strikers 10, keepers 4; `scripts/run_by_role.py` guards it.

### Traits had no effect (playmaker)
`scripts/trait_effect.py` compares players with a trait to same-role players without it. Playmaker showed no change in
progressive passes until the progression multiplier was raised.

### xG scale
Typical shots had xG about 0.2 (real 0.1): the logistic intercept was too generous. Re-scaled (and shot utility with it)
so mean xG per shot is about 0.1 and headers about 0.07.

## Tuning runs (`scripts/tune.py`, score = sum of distances outside bands / band width; 0 is perfect)

| run | matches per eval | start | end | notes |
|---|---|---|---|---|
| 1 | 24 | 5.78 | 2.98 | after passing-style rewrite; goals/shots/crosses balanced |
| 2 | 24 | 8.17 | 3.90 | stricter metrics added (role shot shares, passes per possession) |
| 3 | 24 | 3.74 | 2.67 | after accuracy and shot-share parameters; 14 parameters, 1 round |
| 4 | 40 | 2.75 | 2.29 | 20 workers, stopped after round 0 (diminishing returns). Validated on 96 fresh matches: goals 3.04, shots 24.6, xG 3.08, offsides 6.1; still off: pass accuracy 0.76, throw-ins 27, open-play crosses 11.6, aerial duels 41.5, striker shot share 0.53 |

### Completeness over decimals (decision)
After the aggregates were in band we stopped chasing them and added what a real feed contains, even where numbers are
slightly off: keeper actions (catch / parry / tip over, throws and kicks, claims, punches, sweeps, outfield keeper),
free kicks with takers and walls, corner routines (short, near/far post, penalty spot, edge of box), throw-ins tagged
with hands, formation changes (11- and 10-man shapes), VAR for penalties and red cards, advantage play. Each is an event
type or event field in `EVENTS.md`, so downstream agents see the full vocabulary.
Bugs found on the way: two sent-off players with the same slot index were parked at the same point (zero distance in
the separation step gave NaN); fixed with distinct parking spots and a distance floor. A free-kick xG that counted wall
players as open-play blockers made direct free kicks worthless; set balls now use a separate xG with a fixed wall block.

### Speed of the tuning loop
One evaluation is 24-40 full matches at about 10 s each. We used 6 workers on a 24-core machine for a day. Using 20 workers
cut a tuner step from 80 s to 52 s. Next: faster engine hot paths (60 % of time is the movement targets), half-match tuning,
and a surrogate-regression tuner (fit metric ~ parameters from perturbation runs, then gradient descent on the surrogate).

### Engine speed (9.5 s -> 5.8 s per match)
Profile (cProfile, sorted by own time): 60 % in `movement.team_targets`, 22 % in `_steer`; the cost was call overhead on
tiny arrays, not arithmetic. Three changes, none altering behaviour: `np.clip` / `np.linalg.norm` / `np.stack` replaced by
two-ufunc `clip`, an einsum `nrm` and a plain subtraction for the frame flip (same match, 16 % faster); movement
targets recomputed every other tick unless the ball is in flight or loose (players already lag their targets by about
0.25 s; 30 % faster). Calibration unchanged within noise. Further options if needed: numba on `team_targets`.

### Surrogate tuner: honest result (did not beat the baseline)
`scripts/tune_rs.py` (regression surrogate over 18 parameters, gradient descent on it, verify with real runs) proposed
settings that scored worse than the current ones in both rounds we ran (3.43 and 2.77 vs 2.47), and its half-steps were worse
still (4.50, 5.64). Diagnosis: the score is noisy. A metric with a narrow band (penalties 0.15-0.45) has a sampling error
of about one band width at 24 matches, and parameter changes reshuffle the random stream, so common random numbers do not
hold. The coordinate-search "improvements" of earlier runs were partly seed-fitting. Lessons: score noise must be
estimated (use max(band width, 2 x standard error) as the denominator), evaluations need 100+ matches, and the sensitivity
table is more useful than the optimiser. Kept the baked parameters (score 2.47) and stopped tuning; the realism work is
now finished and frozen as engine-v2.

### Final gap fixes (engine v2.1)
Fresh 96-match battery showed real gaps, so we fixed the big ones by measuring sensitivity first instead of guessing:
- **Home advantage** had vanished (equal teams 33/35/33). Measured goal difference vs `home_edge` (1.2: +0.10, 3.0: +0.81,
  about 0.39 goals per unit) and set 1.7. Equal teams now 44% / 28% / 29% (real about 44 / 25 / 31).
- **Competitive balance** was too steep (3 points stronger: +1.43 goals, 71% wins). Compressed the league's quality spread
  (`teams.SPREAD` 0.8 to 0.5): +1.14 goals, 62% wins (real about +0.9, 55-65%).
- **Wide play**: open-play crosses 11 to 30 (`cross_v`), headed-shot conversion lifted, aerial duels 39 to 51.
- **Take-ons** were 11 per match after the passing rewrite; wingers and attacking midfielders now carry and take on more (role
  bonus), 19-20 per match.
- **Pressure events** 166 to 230 (commit rate), **penalties** 0.58 to 0.39 (fewer given in the box, fewer VAR awards).
- xG model centred on an average finisher/keeper and given a closing-down factor.
Left slightly off on purpose: goals run 3.4-3.6 (an attacking league, real top flights 2.7-3.3) and shots come at about 0.15 xG
each (real 0.10); pass volume about 680 (real 700-900) and pass accuracy 0.77-0.78; striker share of shots 0.53.

### Player brain v3 (why it looked random, and what changed)
Watching a match exposed behaviour the aggregates could not: players paused with the ball in front of goal, four defenders
swarmed one attacker while others were free, and attackers stayed 20-30 m from the ball so the carrier had no support.
Causes found by sampling frames (not by guessing): a random 1-3 s "thinking pause" after every touch; a softmax choice with
noise that made close options look arbitrary; attackers only taking up box positions once the ball was already there; every
defender collapsing on the ball because nobody near goal needed marking.
Changes, each with a measurable proxy in `scripts/brain_metrics.py`:
- decision timing: short reaction time that shrinks under pressure, first-time passes/shots, a fading patience term so an
  unpressed player carries a bit before releasing (time on the ball 2.1 s, first-time big chances 55 %);
- a clear sight of goal is taken (xG above a threshold) unless a teammate is clearly better placed;
- near the box every role has a job (near post, far post, edge of the box, overlap, cover) and the team moves up with the
  ball's path (4 attackers near the box when the ball is there, real about 4-6);
- defenders mark by threat (attackers closest to goal first, firmly inside the box), keep 7 m between non-engaged defenders and
  use one cover player instead of three (an attacker with 3+ defenders within 5 m: 0.6 % of frames).
Aggregates were re-balanced by sweeping tempo, shot cost and tackle rate; playback is smoothed with Catmull-Rom interpolation.
Known gap: players still follow targets rather than a full tactical model; fluidity of the raw 5 Hz data is limited.

## Logic first, numbers second: calibrating against real event data (Oct 2026)

**Why.** Tuning to aggregate bands hid logic errors: a wrong behaviour can still average out right. After the user watched
the viewer (players pass back with a free forward pass on, no shot when through on goal) the rule became: fix the
decision logic and positioning, then re-tune numbers once at the end.

**New measurement tools (all in `scripts/`, none of them tune anything).**
- `logic_audit.py` records every on-ball decision with the options considered (engine hook `Match.audit`) and checks
  common-sense rules: through on goal and no shot, back pass with a free forward option, pass into a marked player while a
  free one existed, dribbling into a defender with a safe pass available, and so on.
- `norms.py real|sim` reduces real StatsBomb open data (Premier League 2015/16, 70 matches, 140 team-matches) and simulated
  matches to the same neutral records and prints about 80 behavioural metrics side by side (`<<<` = outside 0.75-1.3x).
  Only the aggregates are committed (`reference/norms_real.json`); the raw files stay outside the repository. StatsBomb Open
  Data is used for calibration targets only and never inside the app. `norms360.py` does the same for shape using the 360
  freeze frames (Euro 2020 sample) and compares positions around every pass in the final third.
- Probes: `defence_probe.py`, `shot_chains.py` (reads the event chain behind a shot), `snapshot.py`, `sequence.py`,
  `shot_snaps.py` (PNG pitch pictures: look at them, a single picture beat many tables), `entry_profile.py`, `shot_profile.py`.

**What the real data told us (per team per match, real vs sim at the time).**
- Possessions 88 vs 92, possessions reaching the final third 59 vs 61, reaching the box 27 vs 24: the funnel is right.
- Shots 12.9 vs about 32 (2.5x). P(shot | possession reaches the box) 0.35 vs 0.81; P(shot | final third, not box) 0.07 vs 0.23.
- The probability of shooting *given a location* matches almost exactly (0.04 / 0.15 / 0.33 / 0.56 / 0.61 / 0.85 / 0.94 across
  xG-by-location bins). So the shooting decision is fine; the **supply of actions in the central lane near goal is 3-5x real**.
- Real completion of passes into the box: centre 32%, half-spaces 45%, wide 68%. Sim 55% / 67% / 75%. Real teams attempt
  7.8 / 14.4 / 17.9 passes into those three zones; the sim attempts 19.7 / 7.5 / 7.4. Same total, mirrored distribution:
  real teams are forced wide, the sim goes straight through the middle.
- At the moment of a shot real shooters have about 1.8 defenders inside the shot triangle (only 1% have none); in the sim 70-90%
  had a free look. Defenders in the box at a shot: 6.3 real, 5.2 sim. Nearest two defenders are goal-side (within 45 degrees of the
  line to goal) 47% of the time in real data and 21-25% in the sim.
- Hold time before a pass: median 1.0 s real, 2.2-2.6 s sim. Carries of 4 m or more: 154 vs 255 per team; into the box 5.6 vs 9-12.

**What worked (kept).**
- The xG model was too flat at close range and too steep with distance; refitted to real anchor points (`z = -2.08 - .07 d + 1.89 angle`).
- Shot-taking is now location driven and probabilistic (`shoot_slope`, `shoot_ref`, pressure, time on the ball), not "wait for
  xG >= threshold with no blocker", which made the sim shoot only from free looks.
- Territory value for passes and carries now counts forward progress everywhere (`adv_lin`), has a packing term (opponents a
  pass takes out of the game) and no longer clamps backward passes to zero cost. Raising `prog_base` from 0.65 to 1.3 cut
  back passes that ignored a free forward option from 13.5% to 6% (not baked in yet: it pushes own-third passing to 81%
  forward, real 66%; needs the rest of the model first).
- Defence: the block follows the ball a fixed distance behind it near its own goal (`LAG`), defenders stay goal-side of the
  ball (`GS_CAP`), defenders who are not marking fill the lanes between ball and goal and the posts (`box_defence`), players
  well behind the play sprint back (recovery runs) and attackers well behind sprint up, man-markers stand goal-side of where
  the attacker is going (velocity lead) at the attacker's pace. Defenders in the box at a shot went 3.1 to 5.2-5.9.
- A defender next to the receiver now contests the first touch much more strongly (`ctrl_marked`).

**What did NOT work (do not repeat).**
- Raising `tackle_rate` to stop dribblers: 1.5 cut carries into the box from 70 to 35 but produced 260 tackles and 66 fouls per match
  (real about 41 tackles, 24 fouls). Duels at a fixed rate are the wrong lever.
- Sweeping decision knobs (`carry_base`, `patience`, `pass_sigma`, `intercept`, `adv_lin`, `shoot_min`, `carry_prog`) one at a time
  against box entries: carries into the box stayed 60-70. The cause is geometry (where players stand), not utility weights.
- Sweeping `shoot_ref`, `shoot_slope`, `shoot_hold` jointly: shots never went below about 25 per team without wrecking the shot-distance
  mix (too many close shots, too few from 20 m+). Real volume cannot be reached by shot propensity alone; it is a supply problem.
- Lowering `MIN_X` (how deep a role may drop) without more: the defenders' *targets* were already deep (about 10 m with the ball in
  the area); the actual positions lagged by 5-10 m because non-urgent players only jog (`CRUISE`). Deeper floors alone did nothing
  and, with the ball 25 m out, put the back line on the goal line and gave shooters time.
- Making defenders commit to tackles less near goal (`COMMIT_DANGER` 2 to -0.7): no change in any geometry metric.
- Raising `wide_v` (value of a crossing position) alone: only at 1.1 did attempts start to move wide, and the central lane at
  x 70-88 stayed 3x real. Moving the far-side winger's template spot wide did not move his average width either (he is pulled
  inside by `seek_space`, which scores threat by distance to goal centre, and by the ball-side shift).
- Pulling the forwards' reference closer to the last defender (`SHOULDER`): targets were right, positions still lagged; only the
  sprint-up rule helped, and only a little.
- Judging geometry by eye from tables: the shot-snapshot PNGs looked fine (crowded box, defenders around the shooter) while the
  numbers said otherwise. Use both, and count rather than look.

**Open problem and next idea (from the user).** Defending is too reactive. Real defenders cut passing lanes from the middle
(cover shadows) and show the carrier outside, which is why central passes into the box complete only 32% and the ball ends up
wide for crosses. In the sim defenders hold zones/men and press the ball, but nobody stands on the lane between the carrier and
the central targets. Next: lane-cutting and "force wide" positioning for the nearest defenders and the midfield screen, then
re-measure with `norms.py sim`, `norms360.py` and `logic_audit.py`. Bake in `prog_base` and re-balance last.

## Football conventions, build-up patience and body orientation (continued)

**The user's three diagnoses, in order, and what each did.** The user thought of them; each moved something measurable.
1. *"Defending is too reactive: it does not cut lanes or force the ball wide."* Implemented as geometry (see
   `FOOTBALL_PRINCIPLES.md`): flat back line that steps up with pressure on the ball, presser stands on the inside shoulder at a
   jockeying gap, cover behind him, closers on the receiver while the ball is in flight, lane-cutting for free defenders,
   goal-side + ball-side marking, layered cone of bodies between ball and goal, recovery sprints. Shape checks against Euro 2020
   freeze frames: back-line spread 3.3 m (real 2.9), defenders in the box at a shot 5.6-5.9 (6.3), nearest defender to a central
   receiver 2.5 m (2.1). Shot volume did not respond.
2. *"You underestimate build-up: players recycle when no good lane is on."* Right. Real teams spend about twice as long in the
   middle third and play 22% of middle-third passes backwards (sim had 10%). Added two terms to pass utility computed from the
   geometry: **relief** (a pressed carrier values the freest team-mate) and **onward potential** (a two-ply look-ahead: the best
   open lane from the receiver's spot to someone further up the pitch), weighted towards midfield (`zf`). With them the passing mix
   is close to real (middle third forward/side/back .49/.31/.20 vs .50/.28/.22) and shots fell from 35 to about 31.
3. *"Strikers receive back to goal, cannot turn or take men on: you are missing body orientation."* Right: a standing receiver was
   assumed to face goal. Added a heading per player: a runner faces his run, a standing receiver faces the incoming ball, a good
   first touch opens the body part-way (less if a marker is on him), a standing carrier turns towards goal at `turn_rate`
   (75% slower with a defender on his back). Heading now feeds: what he can see to pass, pass accuracy (worse across or behind the
   body), shot quality and willingness, take-on and carry success, first-touch control, a defender's tackle and interception
   chances (worse if not facing the play) and a turning cost on sharp direction changes for everyone. Effect: carries into the box
   dropped to about real (5 vs 5.6); shots did not.

**Hypotheses tested and rejected with data (do not repeat).**
- *Shot quality too high:* xG per shot 0.06-0.09 vs 0.09 real. Not it.
- *Finishing or saves:* goals equal xG on both sides (sim 2.0 vs 2.8, real 1.24 vs 1.15). Not it.
- *Too few defensive actions:* the sim has more interceptions, clearances, blocks and tackles than real.
- *Easy line-breaking into the final third:* entries match (final-third passes 56 vs 53, box passes 24 vs 25); time with the ball in the
  final third matches (280 s vs 310 s).
- *Counter-attacks / transitions:* median time from winning the ball to a shot is 13.6 s sim vs 13.0 s real.
- *One hidden shot route:* shots come through the normal shoot rule (headers and free kicks add about 3 per team).
- *Ball-side marking, capping how much space a forward can find, ball-side drift of the front line, role shoot propensity:*
  the striker kept 53-59% of our shots (real 27%) in every variant. The excess is about 2.5x for every role, so it is not a
  striker-only effect.
- *Raising the duel rate (`tackle_rate`) to stop carriers:* fixes tackle, foul and dispossession counts (done: 2.0 with the contact
  model) but not shots. Contact must be geometric (defender in front or alongside, not chasing, not already beaten).
- *Throw-ins:* `wide_error` is what produces them (43 per match with 3.2, 17 with 1.0, real 45). Keep it until outs have proper
  sources (miscontrols near the line, clearances, carriers running out).

**Open and measured.** Per team per match, sim vs real: shots ~32 vs 12.9, goals ~2 vs 1.24, miscontrols 23-26 vs 14, first-time
shots 47-56% vs 32%, throw-ins depend on `wide_error`. The shot funnel is the one structural gap left: the ball reaches the final
third and the box as often as in real football, but the attackers there shoot about twice as often per entry.
`scripts/tune_norms.py` now scores the simulator against about 45 real-data metrics at once and balances the numbers.

## The aerial game and a full event inventory (user: "balls only go to feet, no clearances, no loose balls, no aerial duels")

**Method.** Instead of looking at the aggregates we already had, `scripts/inventory.py` lists every StatsBomb event type and sub-type
per team per match next to the simulator's closest equivalent. It is the check to run whenever someone says "the game feels like it is
missing something".

**What it showed (real per team per match, sim at the time).**
- High (lofted) passes 115 = 24% of all passes; sim 34 = 7%. Lofted passes only existed as a coin flip for targets beyond 30 m. There were
  no chips, no diagonals, no balls over the top.
- Aerial duels: 22 lost per team per match (about 44 duels a match). Sim: not logged as duels, and an aerial contest had one outcome (control).
- Headed passes (flick-ons, lay-offs) 24 per team; headed clearances 16 of 29 clearances. Sim: none tagged as headed.
- Goal kicks: 79% long, 77% high. Sim: a low-probability bonus; many short.
- Goalkeeper events 15.6 vs 7, pressure events 148 vs 49 (sim only logs a press at the moment of commitment), under-pressure flag on 75% of
  events (sim none), counterpress flag, ball receipts, fouls won, dribbled past, injury stoppages (1.6 vs 0.25).

**Implemented now.** Every target further than 12 m has two candidates, ground and lofted; the lofted one gets the aerial-contest
success estimate (his height against the bodies near the landing point), flies over the lane (low interception) and is wilder.
`air_base`, `air_k` (directness) and `gk_air` set how readily teams play it in the air. A lofted ball arriving with no opponent within
3.5 m of the landing spot is just controlled from the air; with a rival within reach it is an aerial duel, both players are logged
(won/lost), and the winner can control it, head it on to a team-mate (flick-on / lay-off: a `pass` with `body_part=head`), head it
clear (a `clearance` with `body_part=head`) or the ball drops loose into a second-ball scramble. Goal kicks can reach the halfway line
and beyond whatever the keeper's usual range of vision, and are exempt from offside. New metrics in `norms.py`: `high_share`,
`head_pass`, `gk_long_share`, `gk_high_share`, `clear_head`, `aerial_lost`.

**Measured after the change (before re-tuning).** High passes 17-19% (real 25%), headed passes 18 (24), headed clearances 21 of 40
(real 16 of 29), goal kicks 85% long and high with `gk_air` 0.6 (real 79/77), aerial duels about 2x real (our high balls are contested
more often than real ones; many real long switches are uncontested).

**Still not in the engine (from the same inventory).** Pressure events and the under-pressure / counterpress annotation, ball-receipt
events, fouls won and dribbled-past events, goalkeeper involvement beyond saves and claims (sweeper actions, throws and drop kicks as
distinct restarts), injury stoppages at the real rate.

## Ideas from other football AIs, keeper 1v1 and the shot clock

Read-only study of two open-source football AIs (RoboCup 2D HELIOS base, Google Research Football's engine). Nothing was copied; the
ideas that fit were re-derived as geometry/utility rules. Used: possession that fades in and out instead of flipping (so the shape moves
smoothly between attack and defence), laziness far from the ball in roles with little to do, a support ring around the carrier (attract
from afar, repel inside ~11 m), the keeper coming out to the carrier's "shooting point" when no defender can get there first.
Not used: HELIOS's deep action-chain search (our two-ply `pot` term already covers the cheap part of it) and a "knock-on" self-pass
(the existing carry stride already represents running onto a ball pushed ahead, and a self-pass would pollute pass statistics and
trigger false offsides - skipped on purpose).

**A measurement bug first.** `norms360.sim()` counted the passer as a supporting team-mate (the ball sits 0.5 m ahead of him), which
produced a false "hoarding, nearest support 5 m" alarm. Excluding the carrier by identity gave nearest support 9.3 m (real 9.1).
Lesson: when a metric looks wrong, check that the instrument is not the thing that is wrong before changing the engine.

**Keeper rushing out broke the through-on-goal behaviour (0.73 -> 0.23 shots).** Two separate reasons, both real football logic that
was missing: (1) the carry option treated the keeper as a defender standing in the way / a crowd, so the striker passed instead of
running at him (a run at goal ends in a shot or a dribble round the keeper, and the shot terms already price the keeper in);
(2) nothing physical happened when the striker reached the keeper: the 5 Hz step let him run straight through. Now a keeper who
reaches the carrier smothers the ball (gk skill vs dribbling, a small chance of a foul/penalty) and logs `keeper_claim` /
`keeper_sweep` with `technique="smother"`. Result in the scenario: shot 0.70, mean shot distance 11 m, xG 0.17, no passes; the other
30% are saved at his feet, which is the real-football outcome of a 1v1 (the keeper wins about half).

**The shot clock was punishing running at goal.** The willingness to shoot falls with the time he has held the ball (`shoot_hold`),
which is right for a player who sits on a chance in the box, but a striker who carried 40 m had used it all up before reaching the box,
so he shot from 3-5 m. The clock now restarts when the chance becomes clearly better (xG at least 1.3x the best he has had with this
ball, only counted from about .12 xG upward). Through-on-goal shots come from 11 m instead of 5 m.

**Defensive depth - what the real freeze frames say (Euro 2020 360, final third passes).** Defenders goal-side of the ball: 83% real
vs 61% sim in the middle third, 67% real vs 47% sim in the final third. At real shots the deepest outfield defender is 2.2 m off his
goal line (sim 9 m), and the sim's defence only gets deep about 6 s after the ball arrives (14 m -> 4.7 m): the sim's attacks are
quick relative to the defence's drop. A "forwards stay ahead" allowance (`ALLOW`) was tested first and changed nothing: the players
ahead of the ball were centre-backs/full-backs marking edge-of-box runners and midfielders level with the ball, not forwards.

Tried (24 matches per variant, because 8 matches was pure noise: shots per team-match have a standard deviation of about 4.5):
midfield and wingers stand deeper when the ball is in the final third (`LAG`/`COMPRESS`) - almost no effect on shots; the box lanes start
to fill earlier (`BOX_START` 30 -> 38 m) - shots with a free look 0.70 -> 0.58 and the deepest defender 9.9 -> 7.3 m, **but box entries
and shots went up** (16 -> 20, 20 -> 22): a defence that drops early gives the attack the space between the lines. Kept, because the
shape at the shot is closer to real (deepest defender 7 m vs 2 m real, goal-side 4 vs 5.6, bodies in the box 7 vs 6.3), and shots per
entry is a separate problem (below). Real defences do both: compact and high in midfield, then drop fast once the ball is in the
final third; the sim now drops about 4 s after the ball arrives (14 m -> 4.7 m), real is faster.

**Where the extra shots come from.** Not the defence alone: the sim converts box entries to shots about twice as often as real
(1.1 per entry vs 0.5), and 13-16 of its box entries per team come from carries (real 5.6). 28% of sim shots follow a "sideways" pass in
the final third, 26% a forward pass, 14% a cross; only 0.9 through balls per team (real 2.1).

**Hold time was the hidden driver of the carry count.** Real time between receiving and passing is heavily skewed (31% released at
once, median about 1 s, 12% over 3 s). The sim's non-first-time dwell was a near-constant 1.8-2.0 s (normal with sd 0.05), so every
receiver drifted 4-5 m before releasing and almost every reception became a "carry" (285 per team vs 154 real). The dwell is now
lognormal with the same mean (`hold_sigma` .7): median 1.0 s, 90th percentile 3 s, 10% over 3 s - and carries fell to 217-223 without
touching any carry logic. Lesson: a distribution that is the wrong shape can look right on its mean (hold mean 1.4 vs 1.5 s was fine
all along).

**Walking to corners.** In the corner scenario centre-backs took 36 s and had still only reached the halfway line: jogging speed is
(work rate x role cruise factor), about 1.7 m/s for a centre-back, meant for open play. At dead balls there is now a jog floor of
3.6 m/s and no laziness; attackers in the box at the kick 2.6 -> 4.8.

**Scenario metric corrected.** `defending` counted a striker standing up the pitch and looking back at the play as "facing his own
goal"; only players between the ball and their goal count now (0.22 -> 0.05).

Open after this entry (measured over 16 matches, un-retuned): shots 21.9 vs 12.9, goals 1.9 vs 1.24, carries 217 vs 154, carries into
the box 16 vs 5.6, final-third entries 31 vs 53, forward passes in the middle third .37 vs .50, interceptions 18.8 vs 10.9, shots with
a free look .55 vs .01, first-time shots .50 vs .32.

## The defence as a team plan: what a working open-source engine does, and what we copied

**Why we looked.** Weeks of per-stat fixes left shots at 1.5-2x real with the same shape each time: markers a few metres off their man,
defenders level with or upfield of the ball at the moment of a shot, an empty shot lane. The user asked for public code that simulates real
games and to copy its logic instead of tuning. Studied: HELIOS base and Cyrus2D (RoboCup 2D: formation positions as a function of the BALL,
an intercept-time press rule, a "block" that predicts the dribble line and sends the first man who can get there in time, an offside-trap
step-up) and **Open Football** (Rust, `ZOXEXIVO/open-football`, match engine under `src/core/src/match/engine`). Open Football is the useful
one: its authors hit exactly our symptoms and wrote down the measurements (74 % of attackers in their own third with nobody within 3 m,
defenders 14.5 m off a shot's line, blocks 1.2 % against a real 18-22 %, midfielders taking 74 % of shots because nobody marked them).

**What fixed it there, and what is now here** (`defend_assign` in `matchsim/movement.py`):
- One team-level plan, exclusive and sticky: somebody is responsible for every dangerous opponent and nobody is responsible for the same one.
  Order: the press (the first man who can get to a point on the carrier's predicted line), the cover, then marks by threat.
- Only the back line and the midfield are given a man; the forwards are in the unit for the press (and screening lanes) only.
- Threat set = men near our goal (about 36 m out) or within about 25 m of the ball; a marker must be within about 19 m of his man.
- "Mark the man in your layer": a defender already level with or deeper than the threat pays nothing, one dragged upfield pays for every
  metre, so midfielders pick up midfielders and the back four the men in and around the box.
- A mark is worth what a pass to the man would add to the threat (expected-threat gain), so a man behind the ball is not worth leaving for.
- Defensive recovery: a defender who is not engaging the ball and has the ball goal-side of him goes back to a cover point 3 m goal-side of it;
  his job still decides where he stands across the pitch. In Open Football this single rule moved attackers-with-a-defender-goal-side per shot
  from 0.2 to 3.3. Inert for a defender already goal-side.
- Shape around where the ball will be controlled (flight end), not the flying ball; effort follows need (a defender 6 m off his target sprints).

**Measured against real freeze frames (defenders goal-side of the shooter, by shot distance, real vs sim):** 0-8 m 2.6 / 2.4, 8-12 m
3.6 / 3.3, 12-16 m 4.3 / 3.4, 16-20 m 6.0 / 5.3, 20-25 m 7.5 / 6.1, 25-40 m 8.4 / 4.8 (few sim shots there). Before the plan and the
recovery rule the sim was about 2 lower in every band. Marking tightness of every attacker within 20 m of goal matches real (nearest defender
median 2.6 m vs 2.3, share with more than 4 m of space .19 vs .19).

**What was a dead end (kept so nobody repeats it).** Tightening mark_gap / ball_side / press constants (no effect: the structure, not the numbers,
was wrong); reaction delay and target update rate (no effect); first-time-shot incentives; a flat 12 % block probability; a one-parameter-at-a-time
calibration (three runs, no gain: the gaps were structural); a hard per-zone first-touch penalty (rejected: stat-fitting, not football).

### Reference bug: the real keeper was counted as a defender (found 2026-10-08)

Saw: real shots had 1.8 defenders in the shot triangle, only 1.5 % with a free look, the last defender 2.2 m from the goal line, 5.6 goal-side
defenders; the simulation had 0.7, 65 %, 8.8 m and 4.1. That looked like the defence being far too loose, and the defence was rebuilt against it.
Measured: the 2015/16 freeze frames carry no `keeper` flag (that field only exists in the 360 data). The keeper is marked by `position.name ==
"Goalkeeper"`. `norms.from_statsbomb` read the missing flag, so the opposing keeper was always treated as an outfield defender: always on the
line, always inside the triangle, always goal-side. The same bug sat under the lane-block fit.
Fixed: keeper detected by position name. Real values become: blockers 0.87, free look 46 %, last defender 8.7 m, goal-side 4.6, defenders in the
box 5.3 (simulation 0.63 / 66 % / 8.7 m / 4.0 / 5.4). Block model refit on the corrected frames: peak 0.60 per defender (was 0.47), lateral
width 1.0 m, decay 10.6 m (was 11.3), base 0.035 (was 0.012).
Effect: the shot-time defence gap was mostly a measurement error. Lesson: when a reference number looks extreme (1.5 % free look), check the
extraction before building engine logic against it. The remaining real gaps are in build-up and progression (see the table of open gaps).

### Why the simulated side lingers in its own third (analysis, 2026-10-08)

Saw (corrected reference): forward passes in the own third match real (56 vs 58 unpressed per team-match) but sideways and back passes are
2.5-4x real (GK, CB and FB), own-third sequences run 2.1 passes against 1.6, final-third entry attempts 34 vs 54, aerial duels 3.4x, clearances 1.9x.
Measured (event data and 360 frames, probes kept in the session scratchpad, not in the repo):
- Sequence starts per match are equal (66 vs 65): the gap is chain length, so it is the decision, not the number of regains.
- Receivers of passes from the back stand 9-24 m deeper than real (CB 14 vs 23 m, FB 18 vs 33, DM 25 vs 33, attackers 26-33 vs 40-56). When the keeper
  plays, the four deepest team-mates are 3 m ahead of him against 13 m in real. Cause: the whole formation block follows the ball (`FOLLOW` 0.5) in
  possession as well as in defence; the defensive compaction idea was applied to the attacking shape.
- Among safe options (p >= 0.93) a forward pass is only 0.04 better than a lateral one and the decision noise plus invented terms (`usage`, onward
  `pot`, `keep`) are larger than that, so the choice follows the option count (more lateral candidates than forward ones). Forward share stayed at
  0.40-0.47 under every single-term ablation (usage 0, pot 0, relief, risk exponent 1, weaker pressing); only the shape moved it.
- Passer beliefs are miscalibrated: ground passes over-estimated by 0.04-0.07 in the 0.75-0.95 range, lofted passes estimated 0.19-0.56 while the
  realised rate is flat at 0.48 (the estimate carries no information). This is why the code needed `air_base`, `long_bonus` and `gk_long`: they
  compensate for a belief that is wrong.
- Real own-third passes include 14 % balls of 45 m or more (28 % completed); the simulation has about 1 %, because a failed long ball is priced as a
  clean turnover although it is a contested loose ball.
- Aerial duels: 59 % of real high-pass receivers have a defender within 3.5 m (the simulation has the same), but only about 15 % of real high passes
  produce a header contest; the simulation logs a duel for every landing with a marker within 3.5 m (66 %). Real headers are contested about half the time.
Tried and rejected: removing `usage` (more ping-pong, n_own 190), onward potential relative to the passer (no effect), weaker pressing (no
effect on own-third volume), duel reach 1.5 m (74 -> 47 duels but head passes fell), a full expected-possession-value utility in xT units (forward
share and entries come out right but the game becomes frantic: shots 33, tackles 36, because estimated odds are still wrong and the carry and
shot options were not yet on the same footing).
Built behind flags, default off (so behaviour is unchanged): `follow_poss` (share of the ball-follow kept in possession), `pin_from`, `air_exact`
(passer's lofted odds from the engine's own `_aerial` rule), `xt_k` (expected-value utility). The calibration loss of the committed model is 287.7 on
24 matches; the new structure needs a rebalancing pass before it can beat that, so none of it is switched on yet.

### Who the ball goes to is now learned from real passers (2026-10-08)

Source: Fernandez, Bornn and Cervone (2021) model the carrier as an agent with an "average policy learned from historical data"; the Helios
engine keeps risk out of the value (a pass is a candidate only if it is safe, the value is the end position). Our hand-built utility mixed the
two and its extra terms (`keep`, `usage`, onward `pot`) decided the choice among safe options.
Built: `matchsim/policy.py` (features of every option: forward/back/lateral, length, room around the receiver, open lane, xT change, defenders
still ahead of him, keeper, offside position, ground/air/through mode, and how pressure changes each), `scripts/fit_pass_policy.py` (conditional
logit, maximum likelihood, 10,192 Euro 2020 passes with 360 frames, even match ids fitted, odd held out). Coefficients are in
`reference/pass_policy.json`. The utility still decides whether to pass, carry, cross or clear; the policy decides whom and how.
Check (held out, not tuned): the policy reproduces the real forward share (0.390 vs 0.389; unpressed own third 0.582 vs 0.582; pressed 0.296 vs
0.280) and the share of air balls (0.117 vs 0.108). In the simulation the pass accuracy came out at 0.74 against 0.75 real without any change to the
execution model, which is an independent check that the risk the policy takes is the risk real passers take.
Also on by default: `follow_poss` 0.2 (the formation follows the ball a fifth as strongly in possession as in defence: receivers of passes from the
back stood 9-24 m deeper than real).
What it did not fix: the own-third forward share in the simulation stays at 0.41 (real 0.66). The same policy gives 0.48 on real positions, so the
rest is the layout: in the middle third the real carrier has 5.1 team-mates 5-30 m ahead of him (count over frames whose camera covers the whole
band, so no camera bias) and the simulated carrier 2.3. That is the next target. Tried on the way: scoring off-ball spots by the same policy (players
crowd the carrier, because real passers choose near options: choice likelihood is not the right objective for positioning), doubling the
possession push (no effect on those bands).
Remaining gaps with this default (36 matches): own-third passes 145 vs 101, entry attempts 40 vs 54, clearances 48 vs 29, aerial duels 57 vs 22,
offside 5.6 vs 2, carries 217 vs 154, shots 18 vs 13, long balls 5 % vs 13 %.

### Time on the ball and the long ball (2026-10-08)

Saw (user): "a centre-back may hold the ball a while to draw the press". Measured: time from receiving to passing (open play, StatsBomb timestamps
to the millisecond): real CB 1.77 s (18 % over 3 s), DM/CM 1.39 s, simulated 1.38 s (10 %) and about 1.0 s. Overall 1.54 s against 1.27 s; the share of
one-touch passes is the same (31 %), so the missing part is the tail of long holds.
Source: Helios keeps HoldBall as the default of its action chain (a candidate wins only when it is safe and better); Open Football's passing state
"looks for options, moving slowly or standing still". Built: if the chosen action is only a recycling pass and no safe pass that gains ground exists
(safe = the success rate of the passes real players make unpressed, norms `acc_free` 0.88), the carrier keeps the ball and looks again every 0.3-0.6 s,
until the man closing him is within two wind-up times (time to contact from his distance and closing speed) or 8 s have passed. That is how a
centre-back draws the press and plays into the space it leaves. Effect: mean hold 1.27 -> 1.40 s, passes 580 -> 538 per team.
Long balls: the 360 sample only holds passes whose receiver is in the camera frame, which drops most long balls (45 m or more: 0.0 % of the sample,
4.7 % of Premier League passes in the air plus 0.3 % on the ground), so the learned choice believed they were rare. Fixed by a prior-shift
correction: each (mode, length class) gets the offset log(league share / modelled share), computed in `scripts/fit_pass_policy.py` and stored in
`reference/pass_policy.json`. Long-pass share 5 % -> 10 % (real 13 %), air share 0.20 (0.25).
Checked and found not to be the cause: pressing intensity (PPDA median 13.1 against 13.8 real; only the pressure-event labelling is 1.5x), the support
triangles (weight 0.55 -> 0.9: no effect), a block-length cap on the front rank (moves the wrong way: real teams have MORE men ahead of the
carrier), first-time shots (probability scaled by pressure: shot_first 0.55 -> 0.51, so they come from the generic one-touch release).
Distance covered: 14.5 km a player against 10.8 real, the sprint band (> 7 m/s) 2.5 km against about 0.25. Not jitter (a 1 s smoothing changes
nothing). 35 % of outfield time carries the urgent flag; with the shape reference limited to running pace (`block_v` 0.8, Open Football's BLOCK_SPEED)
and urgent men running as hard as the distance demands (`run_frac` -1) the total drops to 13.0 km and the sprint band to 1.7 km. A critical-speed
(D') budget is the principled next step: soccer critical speed is about 3.7-4.0 m/s and D' a few hundred metres (3-minute all-out tests), so a man can
only spend about that much running above it before he has to recover.

### Running: a stamina-linked speed budget and a real time budget (2026-10-08)

Saw: 14.5 km a player (real 10.8 +/- 1.0, Prozone, Premiership) and 2.5 km above 7 m/s (real sprinting about 0.25 km). Not jitter (1 s smoothing changed
nothing). Cause found by logging which line set the "urgent" flag: off-ball bursts 21 %, "joining the attack" 18 %, recovery runs 11 %. The reason it
turned into sprinting: effort followed need = distance / `catch_t`, and `catch_t` was 1.2 s, so any gap over 10 m meant flat out.
Source for the time budget: the real pass interval, about 3.4 s of ball in play per pass (968 passes in about 55 minutes of play): a man has until the
next pass to be where the play needs him. `catch_t` 1.2 -> 3.4 gave 10.5 km, high-speed (5.5-7 m/s) 0.37 km and sprint 0.26 km on its own.
Source for the budget: the critical speed model of running (Jones; Skiba's W'bal): a runner can hold his critical speed (CS) indefinitely and has a
finite distance D' above it that refills below it. Soccer values from 3-minute all-out tests: CS about 3.7-4.0 m/s, D' a few hundred metres (rugby
288 m); recovery constant 316-862 s (Skiba), 450 s used. Built in `_steer`: `dp` drains at (v - CS) above CS, refills towards `dmax` below it, and the
speed cap falls from top speed to CS as the reserve empties. The stamina stat sets both: CS = 3.5 + 0.9 f, D' = 120 + 100 f, f = (stamina - 1) / 19.
Check: stamina 8-12 players have CS 3.97 m/s, D' 172 m and 2.55 km above 5.5 m/s; stamina 16-21 have 4.27 m/s, 205 m and 2.88 km, and fade less in the
second half (high-speed ratio 0.90 -> 0.97).
Also: off-ball bursts only when the new spot is worth it or he is marked (`burst_gain`, off by default: the budget and `catch_t` made it unnecessary).
Explicit sprint cues (press, loose ball, air contest, run in behind) stay all-out (`sprint_need` 0): making them need-based killed penetration.
Result (36 matches / 24 seeds): distance 11.3 km, high-speed 0.68 km, sprint 0.52 km; calibration loss 287.7 -> 211.8 on the tuning seeds and 219.8 on
48 unseen seeds. Passes 502 (real 484), own-third passes 146 (101), carries 137 (154), clearances 20 (29).
Hold times: with the new movement the explicit wait rule is redundant (centre-backs 1.83 s vs 1.77 real, DM/CM 1.38 vs 1.39), so `wait_on` is off;
full-backs, wingers and strikers still hold too long (about 2.0 s vs 1.4-1.5) and pressed holds too short (ratio 0.64 vs 0.86).
Shots: "first time" was flagged for anything under 0.6 s after control; a shot after a touch is not first time. Flag now < 0.3 s: 0.33 against 0.32
real, with no change to behaviour. A "take a touch unless hurried" gate was tried and made no difference, so it was removed.
Remaining gaps: box entries 11-13 vs 25 and big chances 0.17-0.39 vs 0.86 (the final third is now harder to get into than real), tackles 36-38
vs 20 and interceptions 17-19 vs 11, aerial duels 58 vs 22 (the duel is logged for every marked landing), throw-ins 28 vs 45.

### Corners: rest defence first (2026-10-08)

Saw (user, viewer): at an attacking corner eight men are in or at the box, one full-back is left at x = 23 and the keeper stands 34 m out, while the other
side's two forwards wait 29 and 44 m from the attackers' goal, onside because of that one defender: "just people in the box, then a whole empty area".
Cause: the corner layout was a fixed list of 8 box spots filled in role order (striker, both centre-backs, ...) with whoever was left staying on his
formation dot. Nobody decided who stays home, and the defending side's leftovers sat on their own forward dots, deep in the corner side's half.
Source: Open Football's AttackPlan decides rest defence BEFORE the attacking roles ("a slot can never be filled by a man who is supposed to be staying
home"), sized from the nominal defenders and game state, ranked by depth plus a bonus for centre-backs, the screen and the far-side full-back. A corner
is the same rule with a known counter-threat.
Built (movement.team_targets, corner branch): the defending side leaves n_out = 10 - 8 box spots = 2 quick forwards (pace) on the halfway line; the
attacking side keeps n_out + 1 = 3 outfield men home, chosen by least value in the opposition's box (aerial + half finishing, defenders and the screen
first, attackers last), on the halfway line paired goal-side with the outlets, a sweeper keeper 28 m behind them (21 m from goal); the rest fill the box
spots in role order. Sent-off players are excluded from the plan.
Check: scenarios.py corner in the box 4.8 (expected 4-7), behind the keeper 0.0; sample corners show cover at x = 46-51, keeper 21-22, outlets 52-56.

### Clear chances are taken (2026-10-08)

Rule from the user: attackers always go for goal; they pass only when there is no clear chance or a team-mate has a better one.
Measured on real data (Euro 2020 360, receipts within 32 m of goal, xG of the spot with the defenders around him, what the player does next): shoots
3 % below xG 0.03, 18 % at 0.03-0.06, 47 % at 0.06-0.10, 63 % at 0.10-0.15, 74 % above 0.15 (pass 11 %, loses it 14 %). The simulation matched up to 0.10
(18 %, 38 %, 57 %) and fell short on the clear chances: 49 % shot, 31 % pass, 20 % lost, and only 35 receipts of that quality in 24 matches against 109 in 30
real ones.
Built: a chance of xG >= 0.10 (`clear_xg`, the point where real players shoot about two times in three) is shot at once (dwell 0.05-0.3 s instead of a
look-up) unless a team-mate's chance (pass success x his xG) is better than his own; the earlier rule needed the team-mate to be 1.5x better.
Below 0.10 the learned gate is unchanged. Result: clear chances shot 64 % (real 74 %), passes 7 % (11 %), but 29 % are lost before he acts (14 % real);
shots 17.6 (real 12.9), goals 1.49 (1.24). Big chances (xG >= 0.25) stay at 0.31 against 0.86: they are not decided at the shot but earlier, in how often the
ball reaches the six-yard area, which is the box-entry gap.

### Why the simulation has so few big chances (2026-10-08)

Saw: 0.17-0.31 shots per team-match with xG >= 0.25 against 0.86 real, and 1.5 clear chances (xG >= 0.15 on receiving the ball) per match against 3.6.
Measured (scripts in the session scratchpad): (1) real big chances come from every kind of ball into the box: rebounds/carries/solo 0.29, other passes
0.24, crosses 0.15, through balls 0.11, set-piece deliveries 0.07 per team-match; the simulation has 0.08 / 0.03 / 0.014 / 0.014 / 0.00. (2) It is not
tighter marking at the shot: close central shooters have the nearest defender at 2.0 m (real 1.6) and fewer defenders in the triangle (0.29, real 0.56).
(3) The simulation takes MORE close central shots (5.1 per team-match against 3.9) but they are worse and convert worse: goal 0.10 against 0.16, saved 0.30
against 0.19, mean xG 0.11 against 0.17. (4) Cause of the conversion: a shot's goal probability IS the simulator's own xG, and that formula is too flat.
On 1,733 real shots it predicts 0.078 / 0.136 in the 0.06-0.10 / 0.10-0.20 bands where the real rate is 0.099 / 0.180, and 0.54 at the top where the
real rate is 0.38. `scripts/fit_xg.py` refits it (log-lik -439.7 against -451.4; StatsBomb's own model -414.3).
Tried: switching the refit on. Per-shot xG then matches (0.09) and big chances rise to 0.54-0.76, but the shooting gate (balanced against the old scale)
over-shoots from 12-20 m: shots 18-19, goals 1.7-1.85, loss 256-288 against 228. So the gate has to be re-derived from real data (shoot vs pass at the
release, from receipts in the 360 frames) rather than nudged; that and the refit go together and are the next step.

## Nobody holds the touchline: the width plan (2026-10-08)

Saw: the user asked whether the sim still does not cross, carry or play through balls. Counts per team-match, sim vs real: carries 201 vs 154,
take-ons 31 vs 17, through balls 4.1 vs 2.1 (all too many), open-play crosses 7.0 vs 11.8 (too few). Only crossing is short.
Measured (scratchpad probes `cross_zone`, `chan_map`, `width_role`, `touchline_hold`, `wide_to_box`, `box_when_wide`, `line_depth`):
- Where the on-ball actions happen. Real: 40% wide channels, 35% half-spaces, 25% centre (the areas are 40/33/27). Sim: 25/30/45. In the final third the
  sim has 0.5-0.7x the real wide actions and 3x the central ones. In the deep wide zone (x>88, |y-34|>20): real 24 actions per team-match
  (passes 12, crosses 5.4, carries 6.7), sim 11.
- Strikers, midfielders and wingers touch the ball much closer to the middle than real ones (striker mean |y-34| 7 m vs 15, centre-mid 8 vs 15-18).
- Real 360 frames, ball-side flank, final third: a team-mate within 8 m of the touchline on 79% of passes (1.24 players); sim 37% (0.42). Wingers
  were within 8 m of the line 9% of the time. (Far flank 18% real vs 12-16% sim: the far-side winger already tucks in correctly.)
Read: Open Football's `teamplay/plans/wide.rs` documents the very same defect ("nothing in the model ever told anybody to stand on a touchline": 2.2
open-play crosses per team against a real 16-18 and 5 cut-backs in 200 matches; widening the rectangle did nothing). Its fix: width is not an
average of where everybody stands, it is two named players standing where nobody else is. A `WidePlan` names one holder per flank (the widest,
highest formation slot, a winger before the full-back behind him; delivery, 1v1 and legs break ties; incumbent keeps the job), who stands 4.3 m off
the line; the far-side holder is released to attack the back post once the ball is in the final third (box slot outranks width anchor).
Fixed: `movement.wide_holders` + a lateral override on the holders in the possession branch of `team_targets` (hug scales with the width tactic and
the possession fade; the carrier and men on a timed run are left alone). Switch: `wide_on` (default 1).
Effect (32 matches, same seeds): loss 229 -> 210; ball-side touchline presence in the final third 0.37 -> 0.52 (ball central 0.40 vs real 0.27,
half-space 0.51 vs 0.53, ball wide 0.70 vs 0.91); shots 18.1 -> 16.2 (real 12.9), goals 1.66 -> 1.28 (1.24), passes 607 -> 583, take-ons 31 -> 27.
Crosses did NOT rise (7.0 -> 5.8): holding the line is necessary, not sufficient. What else the probes showed (next steps):
- Cross availability: the cross option exists in only 27% of wide final-third decisions (it needs a team-mate already in the area). With the ball wide at
  x 70-88 real teams have 2.3 team-mates in the area, the sim 0.3 (byline zone 2.7 vs 1.5). Forwards stand on the last defender's shoulder and the
  box slots (`templ`) sit mostly outside the area.
- The back line is 4 m too high against wide balls (x 76-88: 85.1 / 88.0 vs real 89.0 / 92.3) while against central balls it matches (87.6 / 92.2 vs
  89.1 / 91.7); forwards cannot be in the box when the line is outside it.
- Low balls: passes from the wide final third into the box area on the ground 3.0 vs 11.6 real (cut-backs, low crosses; the sim has no low crosses at all),
  ground passes along the wide channel 23 vs 44. The sim lofts instead (air passes from there to places other than the box 10.5 vs 2.6).
- The cross option has its own hand utility (p_c about 0.15, loses to the best alternative by 0.8 on average) rather than the learned pass policy.

Labelling (same day). Counting passes from the wide final third (x>70, |y-34|>16) into the area by height: real 9.8 lofted per team-match (74% of
them flagged `cross` by the provider) and 11.6 on the ground (23% flagged); sim 8.3 lofted of which only 3.1 were flagged because the learned pass
policy chose the rest as ordinary passes. They were crosses in everything but the label, so deliveries are now labelled by what they are: a lofted
ball from a flank (past 0.67 L, more than 16 m off the centre line) into the area (last 20 m, within 22 m of the centre line) is a `cross`
(`_pass`; offside is still judged as for a pass). Open-play crosses 5.8 -> 11.0 (real 11.8), loss 210 -> 208. Because crosses are excluded from the
box-entry metrics, the non-cross box entries fell to 7.8 vs 16.6 real, which is the honest size of the penetration gap: ground balls into the box
(cut-backs, low crosses, through balls) are about half of real. Through balls are over-attempted (3.8 vs 2.1) rather than missing.

## Is the tuner machine learning?

No, and it should not be described as such. The behaviour weights in `TUNE` are hand-designed utility terms in a softmax
choice model; `tune.py` is a derivative-free coordinate search (simulation-based calibration) that minimises distance to
real-world bands. Nothing is trained on data (there is none). Honest framing: automated calibration against measurable
norms. Possible genuinely-ML upgrades: Bayesian optimisation with a Gaussian-process surrogate over TUNE, or a learned
emulator of the simulator.

## Open gaps (as of this entry)

Pass accuracy still a little low (0.74 vs 0.78+), pass volume low (about 690 vs 750-950), open-play crosses low (12-22 vs
25+) because crosses created too many header shots (header conversion reduced, retune pending), aerial duels (44 vs 50+),
strikers still take a large share of shots, home advantage and competitive balance need re-measuring after the latest
changes, goalkeeper and free-kick detail, tracking orientation and 25 Hz output.

## Entry template

```
### <short title>
Saw: <metric, value, target>
Guessed: <hypothesis>
Measured: <script/breakdown and what it showed>
Fixed: <the change; commit>
Effect: <before -> after>
```
