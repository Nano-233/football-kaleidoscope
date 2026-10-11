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

## Research round: what the open-source engines do about wide play, runs in behind and box arrival (2026-10-08)

The user asked to copy what open source does instead of fixing symptoms. Read in full: Open Football (`teamplay/plans/wide.rs`, `attack.rs`,
`block.rs`, `duties.rs`; `passing/cross.rs`, `flank.rs`, `through_ball.rs`; `ops/attacking/box_movement.rs`, `support.rs`), Helios-base
(`actgen_cross`, `cross_generator`: a cross is just another chain action to a team-mate within 16 m of goal, scored by the same end-state evaluator as a
pass; no separate cross rules), CYRUS2D (same generators). Web: no other open-source engine models cut-backs, byline play or box runs as mechanics;
papers give the shape of the models (Spearman's pitch control / OBSO, coordinated multi-agent imitation of roles, ball-conditioned positioning) but no code to take.
What Open Football has that this engine lacked, with its own evidence in the comments:
- `WidePlan`: named touchline holders and an overlap runner (ported, see the width entry above).
- `FlankPlay` ladder for a wide carrier: release the man who has run outside you; deliver only with two bodies in the area (one at the byline) and no
  defender on the striking line; otherwise drive the byline (carry aim at the corner of the area). Crossing volume was throttled by the box-occupancy rule.
- `CrossModel`: five deliveries (floated back post, whipped near post, driven low, cut-back, early ball) aimed at a patch of the area, not at a player; a
  cut-back is chosen at the byline when the runner trails the ball. Our engine only has one lofted ball at a player; we have no low crosses or cut-backs.
- `BoxMovement`: box occupants do not camp on the finishing spot; they hold a waiting point, in the defender's back, and all go at once when the ball is
  struck (staging by ball progress 0.48 to 0.65, then the flight of the ball within 35 m of goal). Camping measured 39% of the match in the area against 5-10% real.
- `ThroughBall`: a meeting point solved from the runner's own line and pace (lead 5-22 m), onside when struck, wins the race, corridor open, value above a
  bar drawn once per possession.
- `rest_fit`: who stays home = centre-backs, the screen, the full-back away from the ball; never a man holding a touchline.
Measured against real data (all coverage-corrected where the 360 camera is involved; scratchpad probes, kept in this log by name):
- Layout around a wide ball in the final third (inverse-visibility-weighted team-mate density, `layout_map`): real teams have 2.7 team-mates on the
  ball-side flank (x 60-105, beyond the passer) and 1.2 in the central x 90-105 patch; the sim 0.9 and 0.3. The sim keeps them central at x 70-85.
- Real passes that end beyond the last defender: ground 6.9% of final-third passes (78% complete), lofted another 4.9%; sim 0.8% (44%) and 0.5%. Half of the real
  ground ones go into the wide channel behind the full-back, 44% into the area. Only 5% of them carry the provider's `through_ball` flag, so the flag is not a usable
  target for the geometric ball in behind (real flagged 2.1 per team-match, the sim's label is purely geometric and counts 5.1).
- Real final-third wide play is much more contested: wingers attempt 5.6 take-ons there (sim 2.0), 4.0 dispossessions (sim 0.5), the defence makes 6.5 tackles,
  4.3 blocks, 2.6 interceptions in its own wide third (sim 3.5, 1.1, 1.2).
- Defensive depth: at shots the sim's deepest defender is 2.2 m and its fifth 2.8 m higher than real; goal-side defenders 3.3 vs 4.6; against a wide ball at x 76-88
  the last defender is 3-4 m higher than for a central ball (real: equal). The block formula puts the centre-backs 10 m behind the ball; marking and pressing
  pull the final targets about 4-5 m forward of that, and their positions lag the targets by 2-3 m.
Changes kept:
- Runs in behind go where the ball will be put: the run target is the last defender plus the distance he covers while the ball travels (0.8 vmax x 1.5 s,
  clipped to Open Football's 5-22 m) instead of one metre either side of the line (`run_lead`; 0 gives the old behaviour). Loss 215 -> 203. Ground balls beyond the line
  0.7% -> 2.0% of final-third passes, into the box 14.2 -> 19.5 (real 24.9), box entries by pass 7.8 -> 11.2 (16.6), big chances 0.19 -> 0.38 (0.86), goals 1.3 -> 1.7.
- That made offsides jump to 5.9 per team (real 2.0). Cause: a man the passer sees offside was only discounted to 25% (`off_mult`), so attackers standing 3-4 m
  beyond the line were still chosen. A passer does not pass to a man he sees offside; offsides come from perception and timing only, so `off_mult` is 0.02 and
  the perception delay is the 0.25 s of a visual reaction instead of 0.5 s. Offsides 2.7, loss 202.
- Who stays home uses `rest_fit` (neutral on the loss; the ball-side full-back is free to go, a touchline holder never stays home).
Tried and reverted (do not repeat):
- Box positions moved inside the area (near post, penalty spot, back post, edge) from Open Football's BoxSlot: loss 208 -> 220 and no more bodies in the area. The
  offside cap on forwards (the last defender) is what holds them outside it, so the slots alone do nothing.
- Excluding the carrier from the touchline holders so the next man takes the line (Open Football does): loss 212 -> 221.
- Raising the team's lateral follow of the ball in possession from 0.2 to 0.6: centroid slope then matches the 360 data (real 0.14 per metre of ball offset, sim 0.055)
  but the ball-side flank density did not move (the final-third template overrides it) and the loss rose 208 -> 216.
- A perception delay of 0.1-0.5 s on its own did not change offsides while off_mult stayed at 0.25.
Next (ordered by measured gap): box arrival staging and a waiting point; byline ladder and cut-back / driven-low deliveries; a meeting-point through ball with the race
(the sim's best ball in behind has a success estimate of 0.10 against real completion of 78%); the defensive line against wide balls; winger take-ons in the wide third.

## Where the extra goals came from (2026-10-09)

Question: big chances (0.4 vs 0.86) and box entries (19.5 vs 24.9) are low, yet goals are high (1.65 vs 1.24 per team). By shot xG band and type (24 matches, per team-match):
- Mid-xG shots (0.06-0.20): 9.2 vs 4.6 real, 1.0 goals vs 0.46. The sim gets the ball to the central lane 12-22 m out twice as often (the central channel holds 2-3x the real share of on-ball actions), and those shots convert at their xG. Big chances (xG over 0.35): 0.27 shots vs 0.68.
- Free kicks: 1.96 shots vs 0.51, 0.25 goals vs 0.03 (8x). Free kicks within 28 m: 2.65 per team vs 0.6. Two causes: the sim fouled 3x as often 17-35 m from its own goal (6.1 vs 2.0 per team-match; real fouls there are fewer than in midfield, the sim's were more: a tackle multiplier of 1 + 1.5 x danger made defenders go in harder the closer to goal) and every free kick in range with xG over 0.08 was shot with an xG inflated 2.2x (shot xG 0.12 vs real 0.07 at 18-23 m).
- Penalties: 0.02 shots vs 0.18 real. A foul in the box was given 4% of the time (`box_foul`), so almost no penalties.
Fixed: a defender needs better odds to go to ground the closer to goal (threshold from the cost of losing the duel at the carrier's spot, the value of that spot to the attack), the danger multiplier is 0.7 instead of 1.5, the chance a challenge becomes a foul falls with the cost of the set piece at that spot (a free kick in range is a shot, a foul in the box a penalty, xG 0.76), free-kick xG is the clean-look xG without the 2.2x, and the choice to shoot a free kick follows the real shot share by distance (88% at 18-23 m, 69% at 23-28, 9% at 28-35; `fk_shot_k`, `fk_shot_d`), every foul in the box is a penalty (`box_foul` 1). Effect: free-kick shots 1.96 -> 1.5, free-kick goals 0.25 -> about 0.08, penalties 0.02 -> 0.23 per team-match (real 0.18), fouls 17-35 m 6.1 -> 3.9 (real 2.0, still high).
Measuring: with 24 matches the loss moves by +-10 and goals per team by +-0.2 between identical-quality configurations, so loss and goals cannot judge a change. Use event-level rates with many events (fouls by zone, free kicks by distance, shots by band) to judge a mechanism. Speed: `nrm` uses hypot and `XT` indexes the grid directly (a match takes about 31 s, was 34 s; most of the rest is `team_targets`).

### Follow-up the same day: shot volume, the central lane and what did not move it

`scripts/probe_all.py` runs one batch of simulated matches and prints every structural probe against the real data (about a minute for 24 matches, one for each earlier scratch script); use it instead of the tuner loss, which moves by +-10 on identical quality. After the fixes above (48 matches): shots 17.1 vs 13.1, goals 1.48 vs 1.24, xG 1.43 vs 1.29, penalties 0.21 vs 0.18, free-kick shots 0.9 vs 0.5. The excess now is shot volume (+31%), in the mid xG bands (0.03-0.20: 13.1 vs 8.3), with big chances (over 0.35) at half the real rate.
Structural causes measured and not yet fixed: final-third on-ball actions 54/32/15% wide/half-space/centre in real play and 36/26/38% in the sim; opponents around a ball in the final third are 82% inside the four central strips in real play, 68% in the sim (full-backs 10 m further out); 1.4 fewer defenders goal-side of the ball at x 70-90 (6.4 vs 4.8); the sim's defenders trail their own targets by 3-8 m (midfielders and wingers most).
Tried, no effect on those: narrowing the block with the ball's nearness to goal (`ty` squeeze), anticipating the carrier's path in the block, a smaller recovery trigger distance, removing the outlet allowance for the striker, turning the running budget (D') off. The full-backs' width comes from marking the touchline holders, so the next step is to make marking zonal on the far side (as Open Football's duty assignment limits marks to threats near the ball or near goal), not to change the block width. Half-space positions for the second line (`templ_hs`): final-third centre share 41 to 37%.

### The Open Football team plan, ported (2026-10-09)

Because small constants (templates, thresholds) moved nothing, the structure was copied instead: `matchsim/teamplay.py` ports Open Football's `ShapeBuilder` (the attacking block: a rigid rectangle 27-41 m long and 45-58 m wide, front rank 16 m ahead of the ball, sliding 30% of the ball's lateral offset, every player at his formation fraction of it), the four exclusive box places (near post, spot, back post, edge) with their waiting and finishing points and the ball-driven staging (0.48 to 0.65 to 0.80 of the pitch, everybody goes when a ball is struck within 35 m), and the support offer (the nearest free man at 9.5-20 m, outside the presser's shadow). `of_plan` (default on) = the block, `of_slots` (default off) = the box places and the support offer. Added with it: while the ball is in its final third a defence keeps everyone not engaging the ball inside the penalty-area width (`area_w`; real teams have 0.15 defenders per pass beyond 20 m of the centre line, the sim had 0.5).
Effect (32-48 matches, same seeds): loss 215 -> 200 (block + area width), shots 18.6 -> 13.5 (real 13.1), xG 1.56 -> 1.14 (1.29), goals 1.59 -> 0.96 (1.24, +-0.2 at this size), shots in the 0.03-0.20 bands 14.6 -> 10.3 (8.3 real), fouls 17-35 m 3.5 -> 2.65, offsides 2.1. The area width alone gave loss 195. With the box places on, shots went back to 19 and xG 1.58 (the occupants make shots, not chances), so they stay off.
What is still wrong (this is now the only structural gap): big chances (xG over 0.2: 0.8 vs 1.4 real), ground balls from wide into the box (3.8 vs 11.6), balls behind the last defender (1.8% of final-third passes vs 6.9%), the final third played down the middle (37% vs 15%) with half-spaces and wings short of receivers (real has 0.99 / 0.75 team-mates level with or ahead of the ball in the half-space / wide lane, the sim 0.4-0.6), passes and carries 20-35% too many. The learned pass policy is not the cause: run on the real positions it predicts the real receiver lanes (wide/half/centre 0.40/0.27/0.33 against 0.42/0.30/0.29); it is the positions and the openness of the lanes.
No effect on those (tested, do not repeat): `catch_t` (how long a player takes to reach his place) 3.4 to 1.2 s; the space-seeking threat weight; support distances (`RING_MIN`, `SUPPORT_MIN`, preferred distance); the template positions of the full-back, the second line and the ball-side overload; the attack width; a smaller rest defence.

Same day, after the port: the box places that make chances (back post and the edge of the area, `of_mask` 12) are filled, the near post and the spot are not (they made shots, not chances): shots 15.0, xG 1.26 against 1.29, goals 1.4, loss 193. The front rank now also plays on the shoulder of the last defender (`front_line`, Open Football's own reason for the 16 m lead): own-third passes 227 -> 176 per team (real 101), own-third forward share 0.37 -> 0.46 (0.66), passes into the final third 34 -> 46 (54), at the price of more shots (17.6, xG 1.42) and more fouls 17-35 m from goal (4.3 vs 2.0): once a side gets the ball up the pitch the sim's defence does not stop it as real defences do.
Own-third play in numbers (`chain_len.py`): real 62 sequences per team-match, 1.6 passes each (60% a single pass, 2% five or more, 55% leave the third); sim 84 sequences, 2.1 passes (47% single, 7% five or more, 56% leave). Own-third pass accuracy is 0.82 against 0.73 real: the sim's build-up is too safe (the `keep` and `usage` terms of the pass utility are hand-set, Open Football and Helios have neither).
Next: (1) the defence's resistance to progression (Open Football's DefensivePlan press and cover, its standoff line), (2) build-up decision from Open Football's PassEvaluator instead of the `keep`/`usage` utility, (3) ground deliveries from wide (cut-back, driven low) and the through-ball meeting point.

### Evaluating each port against ours (2026-10-09, evening)

Rule from the user: do not port a module wholesale, measure it against what we have and keep the better. Results:
- Open Football's defending block (rear a standoff goal-side of the ball, `of_def`) against our follow-the-ball block: goal-side defenders 4.59 vs 4.58 (real 6.4), passes into the final third 46 -> 37 (real 54), loss 202 -> 221. Ours kept, the port stays off.
- Open Football's near-post and penalty-spot places: more shots, not more chances (xG 1.58 vs 1.26): off; back post and edge: kept (`of_mask` 12).
- Removing our hand-set `keep` and `usage` terms (Open Football and Helios have neither) to cure own-third recycling: own-third passes 176 -> 158-174, forward share unchanged: they are not the cause, left alone.
- Defenders more often in front of the ball than behind it when the ball is near their goal (goal-side 4.6 of 10 against 6.4 of 9 visible, the same with only the nine nearest men counted): not the block formula, not the marking, not the target speed, not the striker's outlet allowance. It was effort: a man who is upfield of his place trots at 1.7-2.4 m/s (`CRUISE` x `cruise_k`) because effort follows need and the gap looks small. Fix: with the ball in his half a man more than 3 m upfield of where he should be runs flat out (`rec_sprint`, Open Football's recovery run). Goal-side 4.6 -> 5.6 (real 6.4), 4.1 -> 5.1 (5.6), loss 202 -> 184. Running distance rose to 12.0 km, so the jog speed factor was lowered from 0.65 to 0.42 to put it back at 11.0 km (real 10.8): jogging is walking pace in real play anyway.
State after the day (48 matches): shots 16.6 vs 13.1, xG 1.39 vs 1.29, goals 1.18 vs 1.24, penalties 0.19 vs 0.18, loss 184 (229 at the start of the day). Still open: own-third passing (199 passes per team vs 101, forward share 0.41 vs 0.66), big chances (0.4 vs 0.86), the central lane (44% of final-third actions vs 15%), fouls 17-35 m from goal (3.95 vs 1.96), balls behind the last defender (1.6% vs 6.9%).

Same evening, own-third and middle-third passing traced (`fwd_opts`, `poss_start`, `own3_roles`, `mid_roles` probes):
- Real passers in the middle third see 3.2 team-mates more than 5 m ahead of the ball (2.5 free, 0.9 free with an open lane) and 0.6 free behind; the sim's see 1.9 ahead (1.5 free, 0.5 open) and 1.1 free behind. The surplus of backward and sideways passes (back share 0.34 vs 0.22) follows from that: the choice model, fitted on real positions, gives the real forward share when it is given real positions. In the own third every role passes about 1.5 times as often (CB 49 vs 35, full-back 29 vs 19, centre-mid 16 vs 10).
- It is not effort: attackers sit 6-9 m behind their targets in the middle third, but making effort follow the moving target (`lead_k`), letting everyone run at the need (`need_cap`) or sending men who are 8 m short sprinting (`join_sprint`) left the forward options at 1.9-2.1 and cost 1-2 km of running. All three rejected and removed. The earlier effort finding stands for defenders only (a beaten man now sprints back: that moved goal-side from 4.6 to 5.6).
- Open Football's defending block, tried in the same way, put no more men goal-side and reduced passes into the final third (46 -> 37): not adopted.
- Possession starts per team-match (own / middle / final third) are 65 / 58 / 24 in the sim against 42 / 34 / 18 in the real events; the definitions of a possession differ (the sim counts every change of the ball between teams), so this is only a pointer that the sim turns the ball over about 1.4-1.6x as often, which is where the extra own-third sequences (84 vs 62) come from.
Open, in order of what a viewer or an agent would notice: turnovers 1.4-1.6x too frequent, own-third and middle-third passes 1.5x, big chances at half the real rate, fouls 17-35 m from goal 2x.

### Dice where there should be physics (2026-10-09, late)

The user watched a corner given at one end seconds after play in the other half. Cause: when a defender won a header, the engine rolled 22% for "headed behind for a corner" and put the corner at the byline the attackers attack, wherever the header was won (16.3 corners per match, 3.3 of them from play more than 40 m from that end). Rule from the user: an outcome is what physically happens, not a probability attached to an event. Audit of the 39 random draws in `engine.py`; fixed:
- Headed clearance: away from his own goal and out to the flank, direction error growing with pressure, plus a share of the ball's own momentum (`head_keep`); a corner is whatever `_out` then says (last touch, which byline).
- Hurried foot clearances: kick angle scatter grows with pressure (`clear_dir_sd`).
- Blocked shot: the last touch is the blocker's; the ball comes off the body sideways or back at a fifth to a half of the shot's pace, the blocker cannot play it again for 0.5 s; the 40% corner dice and the 2% own-goal dice are gone. A loose ball over the goal line between the posts is a goal (own goal if the defender touched it last).
- Keeper parry: pushed away from his goal (within 70 degrees of straight out), it can no longer go into his own net; a keeper dives for loose balls within 2.6 m.
- A pass to a man by the goal line is weighted short of it (own goals from back-passes: 0.66 per match -> 0.1-0.3, real 0.11).
- Cards: yellow and red follow the challenge (from behind, speed, a lunge he was unlikely to win, a fast man stopped, the last man denying a run at goal = red), not 13% / 0.5% per foul. Yellows 3.2-4.0 per match (real 3.1), 0.14 per foul (0.13).
- Advantage: the fouled man still has the ball, is moving on, the contact was light and (inside 35 m of goal) he has a clear run: 0.13-0.15 of fouls (real 0.14).
- VAR on a goal: only when the pass that made it was played to a man within the 0.3 m line tolerance, overturned if he was beyond the line (`offside_margin` on every pass into the opposition half). No more random "handball" or "foul in build-up" reasons.
Also changed afterwards: whether a won challenge is a foul now depends on the angle (from behind) and on whether the ball was within his reach (fouls 24.0 per match, real 24.0, the thresholds for cards and advantage re-fitted: yellows 0.13 per foul, advantage 0.12-0.14); a header on goal is taken by the same reckoning as a shot with the feet (header share of shots 0.17, real 0.17; the old fixed 13% / 40% is gone); whether a dropping ball is controlled uses the first-touch odds (pressure, congestion) instead of 62%.
Left as dice for now (contests between players with skill-based odds, or choices): tackle and take-on outcomes, a keeper's foul on a smother (0.12), keeper punch (0.3), VAR checks of penalties and red cards, who takes a short corner (0.16), substitution and time-wasting timing.
The loss rose from 184 to about 215 because corners are now missing (gap -2.4 in the loss; they were mostly a bug); that is the honest number.
Restarts per match now: throw-ins 44.6 (real 45.4), goal kicks 15.8 (17.1), free kicks 24.2 (22.9), corners 3.7 (real 11.6): real corners come 29% from saves tipped behind (3.2), 24% from blocks (2.7), 12% from duels (1.4), 27% from clearances (3.0), 4% from interceptions; the sim has saves (2.0) but only about one corner per match from all the others, which needs the box defending (crosses blocked, headed or hacked behind under pressure) to be modelled, not a probability.

### Where real corners and throw-ins come from, and why the sim does not get them (contact physics, then the flank finding)

Asked: how will the sim get real corners? (Real: 11.1 per match; sim 3.7-5.) Not "crosses cleared behind", as assumed. Measured from the 70 PL matches (`corner_chain.py`, `box_def.py`, scratchpad probes):
- Real corners by what happened just before: pass blocked or cleared near the byline 27% (a low ball into the channel or box, 2.7 blocks per match, the blocker 6 m from his own byline), blocked shots 15% (1.7), saved shots tipped over 14% (1.5), clearances (head/foot, 2.9) 26%, tackles on a winger at the byline 12% (1.4), interceptions 4%. Only about 10% follow a cross (9.3% of open-play crosses end in a corner).
- The sim had a third of the real defender touches in its own box (16 vs 45 per match: clearances 11.8 vs 31.5, blocks 1.6 vs 11.1) and every ball a defender touched in flight was controlled, never deflected.

Physics added (all in `engine.py`, no dice on the outcome):
- Blocked shots: the blocker is the man in the lane (the lane model's own weights), the block is where he stands on the line of the shot; the ball comes off his body by where it strikes the surface (`_body_hit`: impact parameter on a body of radius 0.5 m, 35% of the tangential pace lost, a head-on ball comes back at 30%, the leg-stretched ball hits a limb at no fixed point). Block distance from the shooter (median 2.2 m vs real 2.3), from goal (10 m vs 12.4) and the exit angle off the shot line (0.17/0.29/0.23/0.31 in 0-45/45-90/90-135/>135 degrees vs real 0.18/0.22/0.25/0.34) now match without being fitted to them. Blocked-to-corner went 0.07 -> 0.15 (real 0.25). A deflected shot that goes in is the shooter's goal, not an own goal.
- A pass touched in flight is taken down only if the defender had time to read it (a third of a second after the kick) and it is not hit too hard to kill; otherwise it comes off his body as a block, still moving. Interceptions 26 per match (real 22), deflected blocks 45 (real 30 blocks). The dice that deflected 20% of failed touches is gone.
- A loose ball is controlled less the faster it is moving (nobody played it to him), and a player who reaches it without controlling it gets a touch (the keeper pushes it away from goal).
- A won tackle sends the ball off the tackler's foot, from his body through the ball, not along his running line.
Loss 193.5 -> 177 (32 matches; noise +-10), shots 17.2 -> 15.8 (real 12.5).

What is still missing, and it is not corners: the sim plays down the middle. Per team-match against real:
- On-ball actions wide/half-space/centre 0.29/0.28/0.43 vs 0.40/0.35/0.25. Passes starting in the wide lane end wide 32% vs 57% of the time; central-start passes 166 vs 64.
- In the final third (x 70-88) centre-mids and strikers receive 19 and 23 balls vs 7 and 12.5; wingers and full-backs 14 and 7 vs 24 and 14.
- Real central roles are in the wide lane constantly: on-ball events within 14 m of a touchline in the opposition half, CM 36% (sim 7%), ST 34% (6%), DM 31% (3%), CB 30% (11%); the same for defensive events in own half (CM 31% vs 9%, ST 34% vs 7%, DM 30% vs 4%). Real teams are positionally fluid; the sim's roles stay in their lanes.
- Around a wide ball real teams have 4.45 team-mates and 6.6 opponents within 15 m depth (sim 2.5 and 4.5), and 1.66 team-mates in the 14 m next to the touchline (sim 0.64); the far wide lane holds 0.5 (sim 0.93).
- Real defenders in wide areas are more often the ones that put the ball out: of 46 throw-ins per match 14.6 are passes out (9.5 of them lofted), 6.3 tackles, 6.1 blocks, 4.2 miscontrols, 9.3 clearances, 2.4 interceptions. The sim got its throw-ins from ground passes out of play (30.7 per match vs real 5.0), inflated by `wide_error` (a 4x larger pass error within 9 m of the touchline); that made wide passes fail 38% of the time (real 10%) and kept passers away from the flanks. Taking the inflation out (ground passes 0.4) lowers throw-ins to 31 and raises shots and goals (loss 179 -> 209), so it stays until the flank play is right.

Tried and rejected on the lane/density probes (`near_ball.py`, `lane_occ.py`, `cell_occ.py`, `role_lat.py`, `central_who.py`): far-side holder tucks in when the ball is wide (far wide lane 0.93 -> 0.74, no change in flows); defending block width/slide (no effect, that block is off); attacking slide 0.3 -> 0.6 and width x0.85 (small); full-backs following the ball more (none); closer support distances (none, the ring is not the constraint); lateral squeeze of every player toward the ball side (matches near-ball density exactly at 0.3 but loss 177 -> 207 and fewer wide actions); a second wide player job and a six-place flank template (ahead on the line, behind on the line, two half-space places, target man, far outlet; Hungarian assignment with role costs) both worked as designed but players only close 2 m of the 10 m to their place in 2.5 s and keep a place for 1.4 s, so the lane occupancy at the moment of the pass did not move. A reactive shape cannot produce real fluid positioning; it needs the shape in place before the ball arrives.

Next: positional fluidity for the central roles (done in the next entry as ball-following lanes).

### Width, for the other eight men (2026-10-09)

The user asked why width keeps flipping. It did not flip: the Oct 8 width plan put two men per side (the winger and the full-back, `wide_holders`) on the touchlines and nothing else; the other eight kept their formation lanes (centre-mid, striker, defensive mid and centre-back are within 14 m of a touchline for 6-7% of their touches, real 31-37%). Judging that by one metric at a time is why it looked like a yo-yo, so `scripts/lateral_panel.py` now prints every width measure from one batch of matches next to the real figure (pass lanes, wide-to-wide flow, each role's wide share in attack and defence, receptions by role at x 70-88, receiver room and completion by lane, players in the wide band around a wide ball). Run it before and after any change to shape.

Checked first that the pass policy is not the cause: applied to real freeze-frame positions it sends 41% of passes (x>=55) to the wide lanes, real 40.5%. So it is where the men stand.

What did it (each tested alone and together on the panel):
- Attack: the central roles (DM, CM, AM, ST) follow the ball across the pitch (`slide_c` 1.0 of its lateral offset in `teamplay.block_anchors`, was 0.3 and clamped to +-7 m by the block margin); winger/full-back keep the touchline job. Wide share of pass starts 0.27 -> 0.32 (final third 0.33 -> 0.36), wide-to-wide completed passes 0.31 -> 0.41, centre-start passes 166 -> 133 per team-match (real 64), centre-mid wide share 0.07 -> 0.15, striker 0.06 -> 0.20.
- Defence: the same roles shade across to the ball's side (`lat_def_c` 1.0, was 0.28-0.5): defenders in the wide band around a wide ball 1.5 -> 2.2 (real 2.6), their wide share in own half 0.05-0.10 -> 0.21-0.26 (real 0.30). Alone it also raises shots and goals (the middle is left open); together with the attack change shots stay at 14.2 (real 13.1).
- The ball-side full-back comes up to level with the ball (`fb_up` 6 m behind it, was 13 m behind), the far-side winger and full-back tuck in when the ball is wide (`far_tuck`).
- Team-mates in the wide band around a wide ball 0.66 -> 1.31 (real 1.72). Loss 184 -> 195 (noise +-10), goals 1.03, shots 14.2.

Still open (panel, sim | real): wide share of pass starts 0.32 | 0.40 (final third 0.36 | 0.52); centre-mids and strikers receive 22 and 26 balls at x 70-88 (real 7 and 12.5) while wingers and full-backs get 13.5 and 8 (real 24 and 14); wingers are still too wide (0.72 | 0.58). Corners 4.2 | 11.6 and throw-ins 48.8 | 45.4 (still from `wide_error`; ground passes out 31 | 5). Lowering `wide_error` for ground passes to a realistic level (0.15-0.5) raises wide receptions (full-back 8 -> 11, winger 13.5 -> 16) and wide completion (0.66 -> 0.77, real 0.84) but cuts throw-ins to about 27 and raises shots and pass volume (loss 195 -> 217-246), so it stays 3.2 until the contested-ball sources of throw-ins (tackles 6.3, blocks 6.1, miscontrols 4.2 per match in real data; here about 2 in total) exist on the flanks.
Rejected again on the panel: folding positions that would overflow the touchline back inside (worse than queueing on the line), squeezing every player toward the ball (loss), the flank template.

### The touchline as a physical thing (2026-10-10)

Saw: real ground contests pile up on the line. Per match, tackles within 5 m of a touchline 9.7 (the 20-35 m band, twice as wide, 11.9), 44% of them followed by a throw-in; miscontrols 5.5 there, half followed by a throw-in. The sim had 2.9 tackles in the 0-5 m band (28-30 in the middle), 16% followed by a throw-in, and 1.5 miscontrols.
Fixed (all `engine.py`, `TUNE`: `pin_d`, `pin_p`, `pin_r`, `stop_a`, `touch_sd`):
- A carrier near the line is pinned: a defender on the pitch side of him wins more (`pin_p`) and goes in more often (`pin_r`), because the line takes away the way out. Tackles in the 0-5 m band 2.9 -> 5.0, throw-ins from them 0.46 -> 1.5.
- A receiver can only control a ball running at a line if he has room to kill it: it needs about v^2/(2 x 15) m (a cushioned first touch stops a ball within a metre or two); with less room than that the control odds fall away. At 6 m/s^2 the rule made miscontrols at the line four times real, 15 m/s^2 gives 5.1 (real 5.5) in the 0-5 m band.
- A heavy touch leaves the foot in the direction the ball was going turned by N(0, 0.9 rad), 2-6 m/s, not along the pass.
- Bug found on the way: a keeper's touch on a loose ball pushed it toward his own goal (the sign of "away" was reversed for the touch rule I added on Oct 9). Own goals 0.34 -> 0.16 per match (real 0.11).
Effect (48 matches): loss 198 (noise +-10), shots 14.2, goals 1.32, throw-ins 48.9, corners 4.3, free kicks 20.8.
Tried and not kept: lowering `tackle_rate` to 0.6-0.9 with a larger pin effect (central tackles 29 -> 17, but the band at the line saturates at about 5.5: not enough carriers reach the line, and loss 212); lowering the ground-pass error near the touchline (`wide_error` 1.6-0.5) with these rules on: wide completion 0.63 -> 0.69-0.75 (real 0.84), full-back and winger receptions up 20-30%, but throw-ins 38 -> 27 and shots/passes up (loss 218-246); `dwell_scale` above 2.0 is clipped, no effect.
Still open: central tackles 29 vs real 12 and central miscontrols 21 vs 11; carriers in the wide/half lanes; centre-start passes 132 vs 64 per team-match and centre-mid/striker receptions at x 70-88 (19 and 25 vs 7 and 12.5); corners 4.3 vs 11.6; throw-ins still depend on `wide_error` for ground passes out (31 vs real 5).

## Audit against real data: dice, inconsistencies and officiating (2026-10-10)

The user asked for a check of the engine against real matches: too many hard-coded rules and inconsistencies, subpar statistics. Method as
always: 12 matches on fixed seeds (`calibrate.run` fixtures), every number against the real per-team norms (`reference/norms_real.json`),
the calibrate and realism bands and an audit of the event stream (card reasons, goal kinds, shot outcomes, substitution reasons, what
precedes every own goal and corner). Same seeds after every change, so the columns below compare like with like (12 matches: goals move
by +-0.3, cards by +-0.3; the event-level rates are what to read).

What was wrong, and the cause in the code:
- **Cards.** Yellows 1.5 per match (real 3.5-4.5), two thirds of them for dissent: a dice of 0.004 every 30 s per side (0.7 dissent
  bookings per side per match) and a 0.012 dice for time wasting, both booking a random player; keepers could not be carded at all
  (`if s.role[o] != 0`), nor sent off for a second yellow. The foul-severity threshold `card_thr` 0.85 gave 0.03 yellows per foul
  (real 0.13-0.15).
- **Own goals** 0.42 per match (real 0.11), all of them passes into the own net: a pass to a defender standing on his own goal line (the
  post-covering job), a keeper throwing from his own goalmouth "under pressure" with the ball in his hands (the engine let him be
  pressed and tackled like anyone else and gave him a quarter of a second), and a back-pass the keeper could not reach (a receiver
  stretches 1.6 m, a keeper no further). The deliberate clearance over the byline (`safe_out_x`) was aimed at `y +- 6` from the
  carrier, which from the middle of the six-yard box is the goal mouth; a headed clearance's direction scattered with
  sd up to 1.45 rad (83 degrees) plus the incoming ball's momentum, so some went back over the defender's own line.
- **Goals without a shot.** 0.25 goals per match came through `_out` (a deflected shot, a pass or a loose ball over the line) with a
  hard-coded xG of 0.05, no assist and no shot event, so the shot stayed "blocked" in the feed and goals exceeded shots on target.
- **Aerial duels** 262 events per match (real 50-110): any opponent within 3.5 m of the landing point was logged as a beaten rival.
- **Free-kick shots** 3.2 per match (real about 1.0): fouls 20-40 m from the fouler's goal at twice the real rate.
- **Substitutions** always 5 per side, all in the second half (real about 4.3, a sixth at or before half time); a trailing side
  threw on a forward for a defender at the first window after the break whatever the minute.
- **Pressures** were logged every 4 s per defender while within 1.7 m (300 per side; real 150).
- **DOGSO reds** were given for fouls on a man running along the touchline by the corner flag ("last man" was a lateral strip only).

Fixed (`engine.py`; new TUNE keys `dissent_p`, `waste_s`, `waste_book`, `second_thr`, `booked_care`, `gk_hold0/1`, `head_dir_sd`,
`duel_r`, `duel_w`; `card_thr` 0.85 -> 0.62, `foul_risk` 6 -> 10, `tackle_danger` 0.7 -> 0.4, `safe_out_x` 0.12 -> 0.16, `tackle_rate` 1.2 -> 0.95):
- Dissent follows a decision: after a foul against a side (twice the chance when it brought a card) the nearest unbooked man may be
  booked, more under a strict referee. Time wasting is behaviour: a side protecting a lead from the 65th minute adds seconds to its own
  goal kicks, throw-ins, free kicks and corners (growing to about 10 s late on; half of it is added back by the referee); a restart held up
  beyond `waste_book` seconds books the taker, once per side. Keepers are carded like anyone else; a keeper sent off is replaced by the
  bench keeper with the least useful forward sacrificed (`substitution` reason "keeper sent off"), or a defender takes the gloves. A booked
  man is given more rope for a second yellow (`second_thr`) and tackles more carefully (`booked_care`). DOGSO needs the victim to be running
  at goal, not along a line. Every foul event now carries its `severity` and `dogso` flag.
- Every goal is a shot: a deflected shot that goes in keeps its shot event (outcome "goal", `deflected`), its xG and its assist; a pass,
  cross or loose-ball touch that runs in is logged as a first-time shot from where it was struck with the xG of that spot, then the goal.
- A keeper with the ball in his hands (anything but a deliberate pass from his own side) cannot be pressed or tackled and holds it 2.5-6 s.
  A keeper reaches 3 m for a back-pass. Nobody passes to a team-mate standing in his own goalmouth. The byline clearance is aimed at the near
  byline well clear of the posts; clearance and header direction errors are bounded (+-1.0 / +-1.1 rad) and a headed clearance never goes
  back towards the defender's own goal.
- An aerial duel is logged only when the beaten man was within `duel_r` of the ball and had at least `duel_w` of the winner's chance; the
  control odds are unchanged. A pressure is logged once per defender per spell on the ball and not within 3 s of his last.
- Substitutions: four windows (half time with probability 0.35, 1-2 changes around the hour, 2 around 70-75, a late one with
  probability 0.6); the trailing side turns to its forwards from the 58th minute with a chance that grows with the deficit and the clock.
- `scripts/event_schema.py --from <dir>` regenerates EVENTS.md from saved matches instead of re-simulating.

Effect (12 matches, same seeds; per match unless stated; real in brackets):
| metric | before | after | real |
|---|---|---|---|
| yellow cards | 1.50 | 3.67 | 3.5-4.5 |
| of which dissent / time wasting | 0.67 / 0.08 | 0.33 / 0.08 | about 0.25 / 0.15 |
| second yellows / direct reds | 0.00 / 0.08 | 0.00 / 0.17 | about 0.1 / 0.1 |
| own goals | 0.42 | 0.17 | 0.11 |
| goals logged without a shot | 0.25 | 0 | 0 |
| aerial duel events | 262 | 115 | 50-110 |
| free-kick shots | 3.17 | 2.33 | about 1.0 |
| fouls | 27.8 | 25.8 | 24 |
| substitutions | 10.0 | 8.7 | about 8.6 |
| tackles (per team) | 37.5 | 28.6 | 20.5 |
| pressures (per team) | 280 | 282 | 150 |
| shots / on target / goals | 27.8 / 9.8 / 2.42 | 28.6 / 10.6 / 1.75 (goals 1.75-2.75 over the four rounds on the same seeds: finishing noise) | 25.8 / 7.7 / 2.5 |

Home advantage, re-measured on 16 equal-quality fixtures (the open gap from the last entry): `home_edge` 1.7 attribute points on six attributes for
every home player gave home win / draw / away win 0.62 / 0.19 / 0.19 and goals 1.81 vs 0.75 (real about 0.44 / 0.25 / 0.31, 1.5 vs 1.2).
At 0.7 points: 0.44 / 0.31 / 0.25 and 1.25 vs 0.75 (16 matches: +-0.25 goals a side), so 0.7 it is; the goal ratio is still on the high
side and is worth a 48-match check (`scripts/realism.py --home`).

Measured and not fixed here (the structural list from the earlier entries, unchanged by this work): passes 1.3x real (own third 1.9x,
forward share 0.44 vs 0.66), carries 1.45x, take-ons 1.7x, blocks 1.5x, big chances at a third of real, shots under pressure twice real,
corners about a third of real (3.6-4.3 vs 11.6), aerial duel events still above the band, ball-side congestion (shot_free_look 0.75 vs 0.46,
defenders goal-side of the shooter 3.2 vs 4.6). The volume excess (tackles, pressures, take-ons, blocks) has one cause, too much contested
play, and is not a counting problem. `tackle_rate` 1.2 -> 0.95 took tackles from 1.95x to 1.4x real and take-ons from 1.7x to 1.3x without moving
shots or goals (the earlier entry that rejected it judged by the composite loss); the pressure count did not move with the stricter logging rule,
because each spell on the ball still has a defender within 1.7 m. xG per match sits at 2.07 (real 2.3) after the tackle change, worth watching.

## Root causes behind the structural gaps (2026-10-10, second pass)

Rule for this pass (from the user): fix the logic that causes a number, never the number. Each item below names the mechanism found, how it was
established, and what moved. All on the same 12 fixed seeds as the audit entry above (goals move by +-0.35 at this size, event rates far less).

**xG scale and big chances.** `scripts/fit_xg.py` had already shown the xG formula in use was too flat against real shots (its own docstring:
0.06-0.20 band predicted 0.078 / 0.136, actual 0.099 / 0.180) and had a maximum-likelihood refit that was never switched on. Shots inside 12 m
were rated 0.115 on average, so "big chances" (xG over 0.25) were a quarter of real whatever the play. The refit is now the xG model for the
outcome of every shot and for every event (`xg_*`). The shooting gates (`shoot_ref`, `shoot_slope`, `clear_xg`, `head_ref`, `ft_xg`,
`chance_xg0`) are a behavioural rule fitted to real shot shares by distance under the earlier formula, so the shooter's own reckoning of a
position keeps that scale (`_xg(..., decide=True)`, coefficients `xgd_*`); converting the gates to the new scale instead (tried: 36 shots, 4
goals a match, too many from 12-20 m) mixes two fits. Effect: xG per shot 0.07 -> 0.10 (real 0.09), big chances 0.17 -> 0.88 per team
(real 0.86), header share of shots back at 0.12-0.15 (real 0.17), goals 2.5 (real 2.5-2.7), xG per match 3.2 against 2.3 because shots are
still 31 against 26 (below).

**Crosses were never blocked.** `_flight` resolved interceptions for ground passes only; a lofted ball could not be touched until it came
down, so the closing full-back never blocked a cross and a pressed clearance was never charged down (real corners come 27% from low balls
blocked or cleared near the byline). A lofted ball can now be blocked while it is still below `block_z` (1.7 m: the first few metres of its
flight) by a man on its line, with the same reading odds as a ground pass scaled down as the ball rises, and comes off his body
(`_body_hit`). Deflected crosses 0 -> 2.3 per match, blocked-shot and save corners up with the shot count; corners 4.2 -> 6.3-7.4
(real 11.6). Pass blocks are now above real (39 vs 20 per team) because the sim still plays 1.3x the passes with defenders a stride away.

**Clearances went where the kicker wanted, whatever he faced.** A pressed defender with his back to the play now hooks the ball the way he
faces (behind for a corner, square, out for a throw), blended by pressure and body angle, bounded, never into his own goal mouth (a
direction inside the posts is swung to the near side), and it travels less far. Corners from clearances 0.25 -> 0.67 per match (real 3.0):
the rest of the real figure needs defenders facing their own goal when they clear, which the heading model only allows at a sprint.
A first version applied the kick's scatter after the goal-mouth exclusion and put 0.4 own goals a match back in (four of five from
clearances); the exclusion now acts on the final direction.

**Pressing proximity (measured, cause found, partly fixed).** Frames at pass release: the nearest opponent is a median 2.9 m from the passer
and within 4.6 m on 70% of open-play ground passes; the receiver already has a man at 2.5-5 m when the ball arrives and he closes during
the hold. Two causes in the code: the nearest defender was sent to a 2-4 m jockeying point on every carrier (now he engages only on a
trigger: shooting range, the counter-press window, a long hold, back to goal, pinned on a line, running at the defence, a pressing plan;
otherwise he stands off 5-6 m and screens: `standoff`, `press_after`), and `defend_assign` gave every attacker within 25 m of the ball a
marker at 1.1-2.5 m wherever he stood (now the marking gap grows with the man's distance from goal, `mark_loose`: a stride in and around
the box, 5-7 m of zonal cover in the middle third and beyond). The carrier also releases before contact when a man is arriving within
`ttc_release` seconds instead of waiting out his dwell. The standoff and the early release moved the proximity little (2.5 -> 2.9 m
median): the marker, not the presser, is the man at the passer's shoulder; the marking change is the one that matters (numbers below).
Note that StatsBomb's `under_pressure` (an active press within about 5 yards) is not the engine's distance flag, so the x1.7 gap in
`press_pass_share` is partly definitional; the frames measure is the one to track.

**Own-third circulation decomposed with the decision audit** (`engine.audit`, 2 matches, 479 own-third decisions a match): passes go
forward 45%, sideways 35%, back 20% (real 66 / 20 / 14). In 21% of all own-third decisions the passer played sideways or back although a
safe forward option (p >= 0.7, 5 m or more of gain) existed, and in 64% of those the forward pass had the higher utility: the learned
receiver policy passed it up because the forward men were not free (1.3 safe forward options per decision against about 2.5 free men
ahead of the ball in real build-up). Own-third possessions start 64 times a match from loose-ball recoveries (14 goal kicks, 14 throw-ins,
10 interceptions): the turnover volume feeds the circulation. The tight marking above is the positional cause; the policy is not.

Effect of the loose marking (12 matches): own-third passes 199 -> 189 per team, forward share unchanged at 0.44, tackles per team 27 -> 23, nearest opponent at pass release 2.9 -> 3.0 m
median. So the marking gap is not what puts the man at the passer's shoulder either; the block's own density around the ball (ten men in a
30 x 40 m rectangle that slides to the ball) is the remaining candidate, and the own-third forward share has to be read together with it.
The rule stays because it is right football (zonal cover in the middle third) and it took tackles down without touching shots.

Final state of this pass (12 matches, three runs on the same seeds): goals 2.5 / 4.0 / 3.4 (the spread is finishing at this sample size), xG 2.8-3.2,
shots 31-32, shots on target 13, big chances 0.5-0.9 per team, corners 5.5-6.3, tackles 23-24 per team, own goals 0.25.

Still open after this pass, with the mechanism named where it is known:
- shots 31 against 26 per match and shots on target 12.7 against 7.7: defenders are close to the shooter but not in the lane (0.34 blockers
  per shot against 0.87, 37% of defenders goal-side against 58%), and through balls run at twice the real rate (4.4 against 2.1 per team), so
  many shots are taken running at goal with the defence chasing back; the keeper then saves 9 a match (real 5). The box defence that puts
  bodies on the line of the shot (DEVLOG 2026-10-09, "defenders trail their targets by 3-8 m") is the next mechanism to work on.
- passes 1.3x and carries 1.45x real, own-third passes 1.9x: see the marking result above; the remaining surplus is the turnover count.
- corners 6-7 against 11.6: clearances and tackles at the byline (real 4.4 corners a match between them) need defenders who face their own goal.
- pressures 2x by the engine's count: a defender within 1.7 m once per spell; with the marking change this is the number to re-read.

### Third pass: runs in behind, who is at the passer's shoulder, and what the passer believes (2026-10-10, later)

**Shots behind the line.** Frames at every shot: about six defenders are moving back towards their goal at the moment of the shot, and
only 3.4-3.7 of ten are goal-side of the shooter (real 5.8); by how the ball arrived, a shot after a forward pass has 20% of the defence
goal-side, after a carry 13%, after a through ball 3%. Through balls were played 7.9 times a match (the engine's geometric label, wider
than StatsBomb's tag) and completed 54% (real 30-40%) while the passer's own estimate for them was 0.27: the decision rated them risky and
the flight physics made them easy. Two causes in the code: the marker's target led a sprinting runner by only 0.5 s, so he trailed every
run, and the intended receiver was always given the first touch over a defender who was earlier on the ball's path. Now a marker meets a
man running at goal where he will be in 0.5 + 0.7 s (`run_lead_t`), and the receiver keeps his priority only over a man standing next to
him, not over one clearly earlier on the path. Through-ball completion 0.54 -> 0.46, shots 31 -> 29, shots on target 13.2 -> 11.6,
interceptions up to 15 per team (real 10.9), receipts within 8 m of goal 0.79 -> 1.00 (real 1.37).

**Who is at the passer's shoulder.** Frames at pass release, by third: the defending block is not dense (nearest-neighbour spacing
10-11 m in the sim's own and middle thirds, under two opponents within 10 m); the nearest man is one player closing at about 2 m/s, in the
passer's own third the opposing striker or winger. Four engagement rules were tried on him, each alone on the same seeds, and none moved
the median distance at release (2.9-3.0 m): the presser's standoff, the marking gap by distance to goal, the receiver-closing job held to
the same trigger rule (`close_x`), and the counter-press window opened only after settled possession (`settled_s`: a ball that flips in a
scramble is the same scramble). All four stay, as right football, but the man is not sent by any of them: he arrives because the pass is
released when he arrives (the early-release rule) and because the carrier holds the ball 1.3 s while he closes from 5-6 m at 2-4 m/s. A
real tracking benchmark for release distance is needed before this is pushed further; StatsBomb's `under_pressure` is not it.

**What the passer believes.** Estimate against outcome over 12,000 ground passes: balls rated 0.16 were completed 55% of the time, 0.41
rated 65%, 0.61 rated 72%, and only above 0.7 was the estimate honest (lofted: rated 0.19, completed 48%). The cause: the estimate counted
every defender who could reach the lane before the ball at 90%, while the engine's own flight rule sends only the two best-placed men and
gives them the interception odds. The pessimism made carrying (1.5x real) and the sideways ball look better than playing forward out of
the back (the own-third audit: 21% of decisions passed up a forward option that rated higher, with the forward men's p at 0.57). The
estimate now follows the flight rule (two best-placed defenders at `intercept` + 0.1), and the lofted estimate uses the engine's own aerial
contest (`air_exact`, built earlier and left off). A learned calibration of the old estimate (`cal_on`, fitted to an earlier engine) was
tried first: shots and goals right but passes +5% and interceptions 1.6x; aligning the model with the physics is the fix, not a correction
on top of a wrong model.

Effect (12 matches, same seeds): the estimate is honest (mean 0.80 against 0.81 completed; the 0.3-0.5 bucket 0.42 against 0.57, was 0.41
against 0.65), shots 32 -> 27.6 (real 25.8), shots on target 14 -> 10.5 (7.7), xG 3.1 -> 2.32 (2.3), goals 2.8, big chances 0.33-0.5 per team
(0.86), through balls 4.4 per team (the engine's label), interceptions 15.7 per team (10.9). The price: passes 1283 -> 1364 a match (real 968) and
own-third passes 2.1x real, because an honest estimate makes passing beat carrying more often while the receiver choice still goes sideways:
the pass count is now the one structural number left, and its decomposition is the turnover count (296 possession changes a match against about
200 real: pass blocks 73 a match against about 32, miscontrols 1.4x, interceptions 1.4x), not the passes per possession (4.6, real 4-5).

**Nobody plays a ground ball through a man on its line.** Frames at release: 13% of the sim's open-play ground passes had an opponent within
1 m of the lane between the two (24% within 2 m); a third of those were blocked or intercepted, and they were a large part of the 73 pass
blocks a match (real about 32). A ground option whose line has a man on it (within `lane_block_r`, between 15% and 85% of the way) is no
option now: the chip over him and the other men remain. Interceptions 15.7 -> 11.4 per team (real 10.9), pass blocks 46 -> 34 (20),
recoveries 71 -> 63 (54), miscontrols 1.4x -> 1.26x, take-ons 38 -> 33 (real 18-45 a match), carries 1.48x -> 1.40x, passes 1364 -> 1311 a
match, shots and xG unchanged (26.7, 2.6).

**Corners from clearances, tried twice.** Body orientation at a walking back-pedal limit (`head_run` 4.2 -> 3.2 m/s, so a recovering
defender turns and faces his own goal sooner) moved corners from clearances not at all (1.0 -> 1.1 a match) and was reverted. Letting a
headed clearance keep its backward momentum, with the goal mouth excluded the same way as a kick, gives 0.25 corners a match from
headers at no own goals. Corners stand at 5-6 (real 11.6): the missing 5 a match are low balls blocked or hacked behind at the byline and
tackles there, which need box-defending contact the engine does not yet have.

Open after this pass: passes 1.35x real (own third 2x, forward share 0.43 against 0.66; the receiver policy's choice on the sim's positions),
carries 1.4x, corners 5-6 (11.6), receipts within 8 m of goal about 0.6 of real, shots under pressure twice real, blocked shots 0.6 of real.

### Pace of play: why there are too many passes (2026-10-10, night)

The user's steer: a pass count is a result, look for the pace. Decomposed from saved matches (post the lane rule): possessions carry the same
number of passes as real ones (5.0 against about 4.8) but a pass comes every 2.6 s of possession instead of about 3.5 s, i.e. 21 open-play
passes per minute in play against about 17. The hold-time distribution says where: median 1.0 s (real 1.02) and one-touch share 0.33
(real 0.31) are right, the long tail is missing (8% of holds over 3 s against 12%, unpressed holds 1.38 s), and carries are chopped: 432 a
match of 7.2 m (real 308 of 10.5 m) with the same number of 10 m+ carries as real, so 120 extra short ones.

Three mechanisms in the code, tested one on top of the other on the same seeds:
- **Nobody holds the ball.** The author had built the behaviour (a man with nothing forward on sits on the ball and releases when the press
  arrives, `wait_on`) and left it off. On: hold mean 1.26 -> 1.39 s, holds over 3 s 8% -> 10%, open-play passes 1200 -> 1116 a match, own-third
  forward share 0.44 -> 0.47, passes per minute in play 21.2 -> 19.8.
- **The press trigger "he has held it 1.2 s" fired anywhere on the pitch.** A mid-block striker does not engage a centre-back in his own third;
  sides have a line of engagement (about 40 m from their own goal for a low block, 75 m for a high press, by the press tactic): beyond it the
  forwards screen from further off and the centre-back is left to carry and look up, which is where real long holds come from
  (`engage_lo`, `engage_k`, `beyond_k`). On top of the hold: 1.42 s, 10.8% over 3 s, unpressed 1.60 s.
- **The carry-streak penalty** (a hand-set "do not carry for long" term) was removed on top: no effect on carry length (7.7 m) or count, so it
  was put back as it was; carries end because the carrier looks again every half second and a pass then rates higher.
Shots and goals on these seeds: 28.5 / 2.8 before, 32.9 / 3.6, 30.8 / 3.7 and 27.8 / 2.8 across the three runs (finishing noise at this
size; the mean is about 2 shots a match up, which is a held ball drawing the press and finding the space behind it, as intended).

- **A carry keeps the ball as a pass does.** The carry option's keep-the-ball constant (`carry_base` 0.22) was half a pass's (`keep` 0.455), a
  hand-set preference for passing: at every half-second look the carrier gave the ball up. With one constant for both: hold mean 1.54 s
  (real 1.52), holds over 3 s 12.7% (12%), open-play passes 1116 -> 1052 a match, own-third passes 185 -> 170 per team, progressive carries
  46 per team (real 39), carries into the box 5.3 (5.6), take-ons 43 a match (18-45), big chances 0.83 per team (0.86), goals 2.7.
- **Blocks at the kicker's feet.** 41 of 64 pass blocks a match were a presser a stride away blocking the kick as it left (mean 1.1 m from
  the kick). A rule that takes a pass kicked straight into a man a stride away out of the options (`feet_block_r`) is in the code and off: on
  the same seeds it moved those passes into carries (carries 458 -> 488 a match, passes up 40, blocks 35 -> 32 per team, tackles up) and
  gained nothing on possessions. The behaviour that is missing is the shield and lay-off under a man at the feet.
- **A bug on the way, and what it taught.** While putting the carry constant in, a comment appended to the carry-utility line commented
  out the carry's loss term and streak penalty for four runs: carries cost nothing, take-ons doubled (75-84 a match) and the feet rule was
  blamed for it. The restored line reproduces the earlier run bit for bit. Lesson for this file: a conclusion drawn from one run that
  contradicts the previous one is a diff to read, not a result.
State after the pace work (12 matches, same seeds): open-play passes 1052 a match (real about 870 open play), total passes 1150 (968),
passes per minute in play 18.7 (about 17), hold 1.54 s (1.52) with 12.7% over 3 s (12%), carries 458 of 7.7 m (308 of 10.5), possessions 254
(about 200), own-third passes 170 per team (101), shots 31, xG 3.1, goals 2.7, big chances 0.83 per team (0.86), interceptions 10.3 per team
(10.9), take-ons 43 a match (18-45), tackles 25 per team (20.5), pass blocks 35 per team (20), own goals 0.17 (0.11).

### The back line against a man running at it (2026-10-10, late night)

The largest gap left was the shot context: 0.39 blockers per shot against 0.87, a third of the defence goal-side of the shooter against
58%, shots on target 6.2 per team against 3.8. A controlled scenario (`scratchpad` scen_run_at_line: a carrier driving at a set back four
from 32 m, central) showed the mechanism in one picture: as the carrier ran from 32 m to 20 m the four deepest defenders moved from 20.5 m
to 18.5 m from their goal, so he arrived level with the line and shot with a free look. Instrumenting the four against their targets:
- their targets were right (about 10 m behind the ball, `LAG`), but two things pulled the line's target forward: the compactness pull
  towards the block centroid, which sits in front of the line because the midfield stands ahead of it (the line stood 5 m further out than
  its own depth), and the step-up rule "pressure on the ball: step up", which fired on a carrier running at the line. Fixed: out of
  possession the back line holds its own depth and the midfield compacts down onto it (`line_holds`), and a line never steps up into a man
  driving at it, it drops in front of him (`drop_drive`). Alone these moved the line 1 m.
- the real limit was effort: a non-urgent defender moves towards his target at the engine's jog, which with the running budget as tuned is
  1.2 m/s, a walk; "effort follows need" used the distance to the target only, so a man 4 m from a target that was itself moving away at
  5 m/s set off at walking pace. A target that moves away has to be matched, not only reached: the component of the target's own speed
  along the way he must go is added to the effort (`tv_along`).
That rule needed scoping, each step measured on the same 12 seeds: applied to everyone it added 3 km a match (attackers chased their
sliding shape, the line dropped too deep, shooters got more room: goals 4.0); to the whole defending side, 2 km (every pass slides the
block's targets and the defenders ran after the lateral shifts); to the retreating component only (the target moving towards the own goal,
`track_v`), 1.1 km with goal-side 0.33 -> 0.37 of the defence, blocked shots 2.6 -> 3.0 per team, corners 5.4 -> 6.8; and bounded by what a
controlled retreat can do, about 4.5 m/s (`retreat_v`: a target that jumps faster, a pass in flight, is not chased at a sprint): no further change (13.0 km); scoped
to the last line only (centre-backs and full-backs, the men in front of it recover by the recovery-run rule), 12.9 km with blockers per shot
0.39 -> 0.50 (real 0.87), blocked shots 2.6 -> 3.3 per team (3.9), shots on target 6.2 -> 5.3 per team (3.8), free looks 0.76 -> 0.70 (0.46),
goal-side of the shooter 0.33 -> 0.36 (0.58), receipts within 8 m of goal 0.62 -> 1.00 (1.37), shots 30, xG 2.7, goals 3.0. That is the state
kept. The 0.9 km of extra running (12.9 against 12.0 before, real 10.8) is the line dropping in front of attacks; the rest of the engine's
running budget was tuned with a line that did not, and is the next thing to re-read (`cruise_k`, the jog).
In the scenario the first centre-back now drops from 19.7 m to 13.9 m while the carrier runs from 29 m to 17 m, holding a 3-4 m gap, which
is the real picture.

**The effort horizon is set by the play.** With the line fixed, the shots from 10-22 m were measured second by second: 3 s before the shot 0.22
defenders stood in the shot cone, 1 s before 0.32, at the shot 0.67 (real 0.87): the men assigned to the arriving shooter were sliding in as
he struck it, a second late, and three quarters of those shots had nobody in front within 6 m. The cause was the same effort rule: a
defender sent to a mark, a lane or a cover 8 m away was given a fixed 3.4 s horizon (`catch_t`) and moved at under 2 m/s. For the side
without the ball the horizon is now the ball's own time to reach the target point (its travel at about 12 m/s plus the touch that releases
it, `ball_v_eff`, `horizon_min`): he has to be there before the ball can be. The side with the ball keeps the fixed horizon (its targets are
shape, not threats). Effect on the same seeds: defenders in the cone at a 10-22 m shot 0.67 -> 0.83 (real 0.87), free looks on those shots
0.44 -> 0.41 (0.46), distance covered unchanged (12.8 km), shots 30, xG 2.8; blocked shots and big chances moved within their noise. Still
open after that: the nearest man at the shot was beside the shooter rather than in front of him.

**The presser on the line of the shot.** A static scenario (attackers waiting at 18 and 22 m with the ball wide) showed the striker gets a
centre-back goal-side (67%) but the man at the edge of the box gets his midfield marker at his side (88%): the marking spot leans towards the
ball, as it should, and once the man had the ball the presser was sent to the inside shoulder by the midfield rule "show him outside"
(`FORCE_WIDE`), 30 degrees off the line to goal, where a man a stride away blocks nothing. Inside shooting range the presser now faces the
man on the line of the shot (`in_range` switches the shading off, in both the press target and the assignment's press point). On the same
seeds: blocked shots 2.3 -> 3.4 per team (real 3.9), free looks on 10-22 m shots 0.41-0.48 (0.46), nearest defender at the shot 2.8 m
(2.5), shots 33, xG 3.1, goals 3.2. Open: goal-side of the shooter 0.37 against 0.58 (the balls played beyond the line), big chances 0.5 (0.9).

### Running: who sprints, and why the jog was a walk (2026-10-11)

Distance covered had sat at 12.8-12.9 km a man against 10.8 since the audit, and the shot context would not move: a third of the defence
goal-side of a shooter, the blockers sliding into the cone in the last second. The two turned out to be one problem. Distance by speed band
from the frames (per outfield player, metres; real Premier League tracking in brackets): walking under 2 m/s 2,970 (3,900), jogging 2-4 4,900
(4,200), running 4-5.5 2,400 (1,700), high-speed 5.5-7 1,460 (700), sprinting over 7 m/s 1,115 (250-350), 58 sprints a man (20-30). The sim
sprinted four times too much and walked a kilometre too little: the running budget (`cruise_k`) had been turned down to hold the total, which
made the non-urgent jog 1.2 m/s, a walk, so a defender adjusting his position without an urgent flag arrived late, and the men with a flag made
it up at a flat sprint. Tagging every sprint flag with the line that set it, in one match:
- **The flat-out recovery override** (`rec_sprint`: a man 3 m upfield of his place with the ball in his half runs at top speed) was half the sprint
  distance (540 m of 1,115) and a third of the high-speed running. It was added when the jog was the only other speed; since then a defender's
  effort follows the ball's time to reach his spot (the horizon rule), which is the same need without the override. Off.
- **Dead balls**: 107 m of sprinting and 120 of high-speed running a man came from players flagged urgent between phases, running to set-piece spots
  against a fixed 3.4 s horizon, and a jog floor of 3.6 m/s had every other man trotting through the third of the match the ball is dead. The
  clock between phases is the restart: everyone is given the time until the ball is back in play (`dead["ready"]`) and moves at the pace that
  asks, a walk for a goal kick 20 s off, a run for a quick corner.
- **Job churn**: each defender changed job ten times a minute (a mark picked up or dropped 2.2 times each way, 8-19 m to the new spot) because
  the job set was rebuilt from scratch every 0.4 s on sharp thresholds (a man within 25 m of the carrier, within 36 m of goal). A responsibility
  once taken is kept until the man is clearly out of the picture (`job_keep`, the thresholds widened by 30% for the incumbent, who is never
  "out of reach" of his own man). Churn 10 -> 8.5 a minute; the sprint distance did not move, so this was not the cause, but it stays as right.
Effect of the three (12 matches): distance 12.85 -> 12.11 km, walking 2,970 -> 3,820 m, sprinting 1,115 -> 895 m, shots 32.6, goals 3.6, blocked
shots 6.9 -> 8.2 a match, defenders in the cone of a 10-22 m shot 0.72 -> 0.79.

**The push off the mark.** What remained (900 m of sprinting, 48 sprints a man) was physics: a constant acceleration of 3.8-4.6 m/s^2 up to top
speed had a man at 7 m/s five metres into a ten-metre dash, so every dash counted as a sprint, and every defender's last-second slide into the
shot cone succeeded. Sprinting follows a motor model on tracking data (Morin and Samozino's force-velocity profiles): the push left falls as
speed rises, a = a0 (1 - v / vmax), with a time constant vmax / a0 of about 1.2-1.4 s in footballers, which puts 5.5 m/s about 1.5 s into a
sprint and 7 m/s at 2.5 s. With the old a0 kept (12 matches): the bands landed on the real ones (walk 3,920, jog 4,320, run 2,050, high-speed
1,080, sprint 275 m) but the top speed reached in a match fell to 8.3 m/s (real 8.5-10.5) and shots went to 37 with 14 on target: defenders
who had closed shooters with a dash could no longer, and the shot gates were tuned to defenders who could. With a0 set from the time constant
(`acc_tau0`, `acc_tau_k`: 6-7.5 m/s^2 off the mark, quicker men higher), top speed 8.8, 27 sprints a man (20-30), sprint distance 540 m,
high-speed 1,300 m, distance 12.05 km, shots 33, goals 2.5, xG 2.8. The defenders in the cone at the shot fell from 0.79 to 0.62, and the share
with a man in front within 6 m from 0.25 to 0.16: the honest physics exposed what the sprint had hidden, that the defence was not in place
before the shot and had been getting there by a dash that is not possible.

**Where the back line stands.** Freeze frames at 10-22 m shots, by role: the centre-backs were goal-side of the shooter 31% of the time, standing
16 m from their goal with the shooter at 15, and 21 m three seconds earlier; the full-backs 43%; the midfield 17-24%. Three causes in the
depth target, traced one at a time in a full match against the line's own formula:
- the lag behind the ball was a fixed 10 m at every distance, which stood the line 4 m too high with the ball 30 m out (20 m against the real
  edge of the area) and sent it to the six-yard line when the ball reached the edge of the box, the shooter level with it on the way. A defender
  covers the goal from a fixed fraction of the way along the ball-goal line (the proportional cover of the RoboCup defences), so the gap now
  shrinks with the ball's distance (`lag_ref`): 13.6 m behind a ball 30 m out, 6.8 m behind one 15 m out.
- the target then sat 2.5-4 m above that formula whatever it said, because a defender assigned to a man stood 1.1-2.5 m goal-side of him in depth
  as well as across (the marking blend and the assignment's mark jobs, both at 85-90% weight), and the striker stood on the last defender's
  shoulder: the pair drifted out together. Outside the penalty area a back line is zonal in depth and man-oriented across it (`line_zonal`): a
  mark there fixes where the defender stands across the line, his depth stays the line's, unless the man is running at goal, already goal-side
  of the line, or inside the area.
- the step-up rule fired on pressure alone, and with a presser on the carrier most of the time it added 1.5-3 m; a line steps up on a man who
  is pressed and cannot play forward (his back to goal), a pressed man facing goal can still clip it in behind (`step_face`).
After the three the centre-backs' target follows the formula (16.2 m against 15.4 with the ball 25-30 m out, from 19.0) and their position
trails it by 2-3 m (the retreat's own lag: the 2 m slack, the turn, the speed build). On 12 matches: the line's depth by ball position now matches the real picture (15.2 m with the ball 25-30 m out, from 17.7; 18.6 with it 30-35 m out,
from 21.4), the centre-backs goal-side of a 10-22 m shooter 0.30 -> 0.41 and the full-backs 0.42 -> 0.51, blocked shots 4.9 -> 6.1 a match, big
chances 0.54 -> 0.67 of real (from 0.63). Shots rose to 36 with 15 on target (xG 3.3, goals 2.8): the honest acceleration took away the
defenders' last-second dash and the deeper line opened the space in front of it, where the midfield arrives 4-7 m short of its own targets
(traced at the shot: DM 6.9 m short, CM 4.8, moving at 4-4.6 m/s, urgent) because the ball crosses the final 15 m faster than a man can turn
and run it. Open: the midfield's protection of the space in front of the line in the seconds after a pass beats it, and the shot volume that
follows from it; distance 12.3 km (sprinting 570 m, high-speed 1,400 m against 250-350 and 700).

**The midfield in front of the line.** Traced the same way (the central midfielders' raw target against their position, by ball depth, in a
full match): the midfield stood a steady 4-5 m short of its own block at every ball depth, level with a ball 25-30 m out, and 5-7 m short
of its spot in front of the line at the shot. Two causes, each measured alone:
- its marks: a midfielder assigned to a man standing around the ball was placed 1.1-2.5 m goal-side of him in depth, so the men standing level
  with the midfield pulled it out of its block by 4-8 m. The zonal-depth rule of the back line is the block's: outside the penalty area a
  mark fixes where a defender stands across his line, his depth stays the line's, unless the man is running at goal, already goal-side of
  that line (between the lines, or in behind) or inside the area. Midfield position 28.4 -> 27.2 m with the ball 25-30 m out.
- its pace: a man flagged urgent ran at need, everyone else at the jog, which is a walk (1.2 m/s); the flag switched on 4-5 m from the spot
  (a job 4 m away, a recovery 5 m away), so a defender hovered at the threshold, walking below it and running above it, 4-5 m short for
  good; matching a retreating target (the last line's rule) made no difference to it. A defender has no clock of his own, the ball sets it:
  the whole side without the ball is paced by need, flagged or not (`def_paced`), far from the ball a long horizon and a walk, near it a run;
  a forward screening ahead of the ball has no spot the ball is racing him to and keeps the jog. The line now trails its target by 1.5-2 m
  (from 2-3) and the midfield by 3-4 m (from 4-5), and the midfield is 2.5 m deeper.
On 12 matches: defenders in the cone of a 10-22 m shot 0.59 -> 0.79 (real 0.87), a man in front of the shooter within 6 m 0.24 -> 0.35, free
looks 0.51 -> 0.41 (0.46), goal-side of the shooter at 12-20 m 0.31 -> 0.41 (centre-backs 0.41 -> 0.48, full-backs 0.51 -> 0.66, central
midfielders 0.15 -> 0.32), shots 36 -> 32, on target 15 -> 12.8, pressures 494 -> 450 (in band), xG 2.9, goals 3.5 (finishing noise at this
size), big chances 0.62 of real; distance 12.3 -> 13.1 km. Pacing only the men whose spot lies between the ball and their goal (a forward
screening ahead of it keeps the jog, `def_paced` 2) was tried as the stricter reading: no distance saved on 12 matches (13.2 km), and with
the forwards no longer hustling, attacks reached the box against a set defence every time (cone 0.95, big chances a quarter of real, xG
per shot 0.07): the whole side is paced.
A longer clock for the defender (the ball's travel plus the carrier's release and the receiver's first touch, `horizon_min` 1.3 s) was tried
on top: the cone went to 0.92 (over the real 0.87), big chances halved and the distance did not fall, so the touch stays at 0.8 s. The running
left over is the defending side's constant motion (3.1 m/s on average out of possession against about 2.5 for the side with the ball): the
shape's reference point follows the ball at up to 0.8 of a man's top speed (`block_v`), so every pass moves every defender's spot 4-8 m. Held
to 0.3 of top speed the distance is 12.0 km but the block never reaches the box behind a quick ball (the midfield's target 19 m with the ball
at 10); 0.5 gives 12.7 km. That is a trade on one number, not a cause, and is left open: what real blocks do per pass needs tracking data.

## Is the tuner machine learning?

No, and it should not be described as such. The behaviour weights in `TUNE` are hand-designed utility terms in a softmax
choice model; `tune.py` is a derivative-free coordinate search (simulation-based calibration) that minimises distance to
real-world bands. Nothing is trained on data (there is none). Honest framing: automated calibration against measurable
norms. Possible genuinely-ML upgrades: Bayesian optimisation with a Gaussian-process surrogate over TUNE, or a learned
emulator of the simulator.

## Open gaps (as of this entry)

Measured on 2026-10-10 against the real per-team norms (see the audit entry above): passes 1.3x real (own-third circulation, forward share
0.44 vs 0.66), carries 1.5x, take-ons 1.35x, tackles 1.4x, blocks 1.5x, pressures 1.9x and passes under pressure 0.30 vs 0.17 (too much
contested play, one cause); big chances at a quarter of real and shots under pressure at twice real; corners about a third of real
(the box defending that produces real corners is not modelled); aerial duel events still above the band; home advantage and competitive
balance need re-measuring after every change (`scripts/realism.py --home / --balance`); goalkeeper and free-kick detail.

## Entry template

```
### <short title>
Saw: <metric, value, target>
Guessed: <hypothesis>
Measured: <script/breakdown and what it showed>
Fixed: <the change; commit>
Effect: <before -> after>
```
