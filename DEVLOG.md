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

### Newsroom on Foundry: making the fact-checker trustworthy (2026-10-11)

Saw (first live run, gpt-4.1-mini): a 22' goal described as "the 21st minute" and approved; "just 7 PPDA actions" (PPDA is a ratio, the pack carried the raw count `ppda_actions`).
Measured: the pack held `minute` (0-based, 21 for a 22' clock), and the number check looked for each number as a substring of the whole pack JSON, so "21" matched inside timestamps and ids.
Fixed (`newsroom/`):
- The pack carries only the clock, the real PPDA ratio, team names, and a `glossary` that says what each metric means (PPDA: lower = harder press; score: home-away).
- Number check: a quoted number must be a rounding of a pack number (0.072 supports 0.07; a fraction supports its percentage); ids and timestamps excluded. Minute check: "21st minute" / "21'" must be on the clock.
- Rewrite loop: a rejected line goes back to the commentator with the checker's issues, up to 2 rewrites. If every draft is held, a template line built only from pack facts is published (`fallback`) and the drafts stay in the audit trail.
- 429 back-off and at most 3 moments in flight (the deployment allows about 50 requests a minute).
Effect: 18 published lines over 2 matches, all approved, 5 after a rewrite. Remaining weakness: the mini model as checker sometimes rejects correct lines (contradicting itself between `approved` and `issues`); a stronger checker model is the upgrade if it shows in demos.

### Free-kick walls and inflated ratings: diagnosis (2026-10-11)

User saw: no walls at direct free kicks; every player rated high whatever the result.
Measured (`scripts/fk_wall.py`, `scripts/ratings_probe.py` on 16 matches, `scripts/norms.py sim 16` against real per-team figures):
- Walls exist (`movement.py`, dead free kick within 32 m) but hold 3-4 men in a loose clump with attackers mixed in, where the code asks for 5 under 19 m, and one defender usually stands 5-6 m from the ball (inside 9.15 m). The wall has no effect on the outcome: `_shoot` decides "goal" first from a wall-free xG (fitted to real free kicks); the wall only decides how a miss is split between blocked, wide and saved.
- Ratings: mean 7.42 (sd 0.93; real about 6.7), losers 7.23, winners 7.57; keepers who conceded 2 rate 7.68 against 6.86 for a clean sheet. The formula credits won tackles, aerials, interceptions and saves and never charges the matching losses (aerials lost, tackles lost, dribbled past, goals conceded); passing contributes 0.03.
- Interaction volume per team-match, sim vs real: aerial duels lost 63 vs 22 (x2.9), tackles 36.6 vs 20.5, pressures 271 vs 150, blocks 32.6 vs 20.1, take-ons 26.4 vs 17.5, miscontrols 19.9 vs 14.3. A one-sided rating turns each surplus contest into extra credit for somebody.
- Aerials: high-ball volume is right (about 115 per team, real about 120); 53-61 % of them become duels against about 15-18 % in real data. Contest rate is 0.84 when an opponent is within 5 m of the landing spot at release, 0.66 at 5-10 m. As found on Oct 7, `_aerial` logs a duel for any rival within 3.5 m of the landing spot, while in real play most of those balls are brought down or never reach heading height; the passer's lofted odds (`p_air`, `air_exact` off) carry no information, and `air_base` adds a flat bonus for playing it in the air.

### Aerials, free-kick walls and ratings: fixed (2026-10-11)

Fixed (`matchsim/engine.py`, `matchsim/movement.py`, `analytics/stats.py`; 24-match loss 198 -> 200, inside the +-10 noise):
- Aerial contest (`_aerial`, `_head_zone`): a lofted ball can be headed over the last stretch of its path (a quarter of the flight, at most 6 m). The first man it comes to takes it; it is a duel only when men of both sides meet it within 1.5 m of the same spot and within reach (`duel_reach` 1.4 m, keepers x1.6 with their hands). A pressed receiver (opponent within 3.5 m) heads it on or controls under pressure instead of bringing it down. Aerial duels lost per team 63 -> 24.5 (real 22), headed passes 18.4 -> 20.5 (24), headed clearances 19.8 (16), goals 1.23 (1.24).
- The passer's odds for a ball in the air (`_air_share`, now on via `air_exact`) use the same rule, so the flat bonus for lofted balls (`air_base`, which compensated for uninformative odds) is 0.
- Landing-spot-only reach made crosses complete 57 % of the time; meeting the ball along its path brought it back to 45 % (it was 47 % before; real 28 %, still open).
- Wall: the outside man on the line from the ball to the near post, the rest 0.6 m apart towards the middle, men kept in order across the line (re-ranking them every tick by distance made them swap places and form a clump). Players with a set-piece job no longer take part in the dead-ball wandering, and the defending side is held 9.15 m from the ball after it (the wander used to walk men into the circle). Nearest defender at the kick 5-6 m -> about 8.5 m.
- Free-kick shot (`_wall_cover`, `_shoot(wall=...)`): the kick first has to clear the wall (share of the goal mouth the wall hides x the chance he gets it up and down: `fk_over0` .45, +.03 per finishing point, +.02 per metre beyond 20 m), then beat the keeper (`fk_xg` 2.2 on the clean-strike xG); a cleared kick misses as often as a hurried shot. Outcomes per direct free-kick shot: blocked 0.43 -> 0.21, conversion about 6 % (real about 6 %).
- Stats: each aerial duel is logged twice (winner's "won", loser's "lost") and both were counted as a win and a loss for each player, so every aerial win rate was 0.5; counted once now. Goals conceded while on the pitch are counted per player.
- Rating (`rating_terms`): every won contest has its lost one (tackles, take-ons, aerials, dribbled past, failed passes, dispossessed); goals conceded for keepers and defenders, clean sheets, a small result term. 16 matches: mean 7.42 -> 6.79, winners 7.25, draws 6.76, losers 6.33; keepers 8.6 with a clean sheet down to 6.2 conceding 4 (was higher for conceding 2 than 0).
Still open: direct free-kick shots 3.1 per match vs about 1 real (fouls 17-35 m from goal), tackles 38.8 vs 20.5 per team, pressures 1.8x, take-ons 1.5x, passes 604 vs 484, crosses completed 45 % vs 28 %, corners 4.8 vs 11.6.

### Tackles, take-ons and fouls against the real contact rate (2026-10-12)

Saw: tackles 38-41 per team-match vs 20.5, take-ons 29-33 vs 17.5, pressures 1.9x, passes under pressure 0.29 vs 0.17; strikers tackled 14.8 times a match.
Measured (`scripts/contact_probe.py`, new; real side from the Euro 2020 360 freeze frames, 23,855 receipts):
- Space at reception is real (nearest opponent median 7.4 vs 8.1 m in the own third, 6.2 vs 6.5 in midfield); the gap opens during the hold.
- Given how tight the receiver was marked when the ball arrived, the chance he is tackled or dispossessed within 1.5 s: tight (<1.75 m) 0.21 sim vs 0.05 real, 1.75-4 m 0.07 vs 0.024, free 0.013 vs 0.002. A third of the sim's tackles came within 0.3 s of control.
- The cause was `first_touch` (3.0): the tackle hazard was multiplied by up to 4 at the moment of control ("the first touch is the moment the ball is most exposed"), which the real data contradicts: a receiver under pressure protects the ball.
- 18.5 of 29.5 take-ons were not dribbles at all: a carrier running into space was caught by a man who arrived, and the event was labelled a take-on because the carrier was moving and facing the man.
- Committed tackles were mostly at good odds (56 % at p >= 0.6, won 65 %), so the decision to go in was not the problem, the number of contacts was.
Fixed:
- `first_touch` 0. Tight receipts tackled 0.065 (real 0.050).
- A take-on is a run at a man: only the defender who was in the carrier's path when he set off with the ball (`carry_into`) makes it one, for either outcome; a man who arrives and wins it has dispossessed him, one who arrives and misses has lost a tackle.
- A defender goes in only when he is more likely than not to win it (`tackle_floor` 0.5, was 0.4): a missed lunge leaves him beaten.
- Fouls also come from a missed challenge that catches the man (`foul_miss` 0.35, more from behind and for poorer tacklers); before, the sim could only foul when the defender won the challenge. Real fouls: 10.6 of 13.3 per team are on a player on the ball.
Effect (24 matches): tackles 38 -> 23-27 (by third 12.9 / 8.3 / 3.6 vs 10.5 / 7.7 / 2.3), won 0.65-0.70 (0.58), dispossessed 6.9 -> 13-15 (13.8), fouls 12.6 (12.0), free kicks 21.4 (22.9), take-on success 0.61 (0.57); loss 200 -> 188.
Still open: take-ons 10.5 vs 17.5 (the chosen dribble is too rare now that the mislabelled ones are gone); fouls sit in the wrong place: 17-35 m from goal 3.15 per team vs 1.1 real, beyond 50 m 5.4 vs 8.8 (real fouls are mostly in midfield, by pressing players and to stop counters), which is why direct free-kick shots stay at 3.3 per match vs about 0.8.

### Where fouls happen: the cost of a missed challenge (2026-10-12)

Saw: fouls in the defending third 4.6-5.1 per team-match vs 2.2 real (Euro 2020), in the attacking third 2.3 vs 4.3; forwards fouled half as often as real; fouls 17-35 m from goal 3.2 vs 1.1, so direct free-kick shots 3.3 per match.
Measured (fouls by fouler role and zone, by code path; real from Euro 2020 "Foul Committed"): half the defending-third fouls came from won challenges that caught the man, half from missed ones. The near-goal caution in the decision to go in (`tackle_risk` x what the carrier's spot is worth) was taken as max() with the new floor and never exceeded it, so a defender at the edge of his box went in at the same odds as in midfield; and the floor itself was the same for the last man and for a forward pressing with his whole team behind him.
Fixed (`_held`): the odds a defender needs = a floor from the cover behind him (`tackle_floor` 0.5, `cover_k` 0.04 per team-mate goal-side of the ball, from 4: a forward pressing high goes in at about 0.35, the last man at about 0.6) plus, added on top, the near-goal cost of a miss. The win odds include the angle: from behind, the man shields the ball (`behind_k` 1.5).
Effect (24 matches): loss 188 -> 179; tackles 20.5 (real 20.5), fouls 11.4 (12.0), fouls 17-35 m 2.1 (1.1), attacking-third fouls 3.1 (4.3), direct free-kick shots 3.3 -> 1.44 per match (about 0.8-1.0).
Still open: take-ons 11 vs 17.5, midfield fouls 6.2 vs 8.8 (tactical fouls to stop a counter are not modelled), tackles in the attacking third 4.8 vs 2.3, big chances 0.35 vs 0.86, corners 4.3 vs 11.6.

### Penalties, crosses and cut-backs: what holds the chances back (2026-10-12)

Fixed: near his goal a defender's extra caution (`tackle_risk`) now fades when the carrier is in a shooting position (`threat_xg` 0.15 of clean-look xG): a man about to shoot shoots if nobody goes in, so holding off saves nothing. Penalties had fallen to 0.02-0.10 per team (real 0.18) after the tackle threshold work; back to about 0.10-0.12 (24-match counts are only about 5 penalties, so this is noise-limited).
Measured, not fixed (`probe_all.py`, Euro 2020 360 frames):
- When the ball is wide within 17 m of the byline, real teams have 2.02 team-mates in the cut-back zone (6-20 m from goal, central 20 m), 0.86 of them with no opponent within 3 m; the sim 0.81 / 0.47. Filling the penalty-spot place of the attack plan (`of_mask` 14) gives 1.65 / 1.06 (real-like), but ground balls into the box stay at 3.5-4 per team (real 9.0 + 2.6 ground crosses) and shots rise in the 0.03-0.10 band: the wide man still chooses the lofted cross.
- The lofted cross succeeds too often: 0.42-0.51 completed against 0.27 real, 14-17 per team against 7.2. At arrival the intended receiver is 1.4 m from where the ball can be headed and the nearest defender 2.8 m from the landing spot; only the attacker can reach it in 31 % of crosses, only a defender in 13 %; the keeper is 12 m away and almost never comes. Sending the defenders to the ball's path during the flight (instead of the landing spot) did not change who reaches it (tried, reverted): the box positions before the cross are the cause. Pricing the cross with the aerial rule (`_air_share`, consistent with the outcome) made crosses more attractive because that outcome is too generous (tried, reverted until the box defence is right).
Next for chances: box defence before the cross (defenders between the ball and the goal, the keeper claiming in the six-yard area), then the cut-back option becomes the better choice on its own.

### Crosses into half-empty boxes (2026-10-12)

Measured (`scratchpad crossbox.py`, Euro 2020 360 frames at the moment of the cross): real boxes hold 2.75 attackers (1.62 with no defender within 2 m) and 5.21 defenders; the sim's 1.43 attackers (1.03 free) and 4.59 defenders, yet its crosses completed 0.46 against 0.28. Defenders win contested headers in their own box 0.53 real, 0.45 sim (small sample), and sim keepers already claim 5.5 high balls a match (real collected + punched 1.4), so neither duels nor keepers are the cause. The cause is precision: a cross carried `sigma_scale` 0.8, more accurate than any other lofted ball (about 1.3 m for a 25 m cross), and the crosser aims at where his man will be.
Fixed: `cross_sigma` 1.6: a cross struck on the move from wide at a moving target is less precise than a lofted pass to a standing man. Cross completion 0.46 -> 0.40, shots 15.8 -> 14.6, clearances 27.0 -> 29.0 (real 29.0), loss 187.6 -> 186.4 (noise).
Tried, not kept: filling the penalty-spot place too (`of_mask` 14): attackers in the cut-back zone become real-like but shots rise to 16.1 (loss 194.9). Still open: crosses 13-15 per team vs 7.2, box occupancy at the cross. Tried, not kept: pricing the cross with the error it is really struck with and the aerial rule (consistent odds): crossers pick better moments and cross more (19 per team, open-play crosses 31 vs 11.8, completion back to 0.45), Nor is it the cross option's value: in the crossing zones (x > 80 m, within 18 m of a touchline) real wide players cross with 14 % of their actions (Euro 2020: pass 48 %, carry 32 %, dribble 6 %), the sim 26-30 %, and lowering `cross_v` from 0.8 to 0.3 only moves it to 26 %: most sim crosses are lofted passes into the area chosen by the learned pass policy (labelled crosses by the provider definition), towards the free man in a half-empty box. The cause is the box positions when the ball is wide (see above), structural work.

### The near-post runner (2026-10-12)

With crosses as imprecise as any lofted ball struck on the move, filling the near-post place of the attack plan again (`of_mask` 13: near post, back post, edge of the area; it was taken out on Oct 9 when crosses were too precise and the place "made shots, not chances") gives loss 180.5 -> 160.9 on 48 matches, goals 1.03 -> 1.17 (1.24), cross completion 0.39 -> 0.36. Shots rise 14.3 -> 17.3 (real 12.9): the shooting gate is the next thing to re-derive (see "Why the simulation has so few big chances").

### Why the sim shoots too much: it gets close too often (2026-10-12)

`scripts/shot_choice.py`: on-ball actions beyond x = 70 m by the clean-look xG of the spot (the engine's own formula, no defenders), and the share that are shots, sim vs Euro 2020 (31 matches, open play, feet): actions per team-match in the 0.07-0.12 band 11.6 vs about 3.1, 0.12-0.20 5.0 vs 1.0, above 0.20 1.8 vs 0.3; shot share in those bands 0.37 vs 0.37, 0.81 vs 0.51, 0.95 vs 0.68, and 0.08 vs 0.15 in the 0.04-0.07 band. The shooting decision is too eager only at the best spots; the larger cause is that the sim gets the ball into close central spots 3-5 times as often as real teams (the central-lane finding of Oct 9: final-third actions 32 % central vs 15 %), and those touches are crowded, which is also why big chances stay at 0.30 vs 0.86. Next: why defences let the ball into the middle near goal (block compactness and the central lane), then the shooting decision against these real shares.

### The defence protects the middle (2026-10-12)

Saw: final-third actions 36 % central vs 15 % real; the ball reaches close central spots 3-5x as often (`shot_choice.py`).
Measured (Euro 2020 360 frames, defenders level with or behind the ball, ball 70-95 m): the real defensive unit is 27.5 m wide with the ball central and 32.4 m with it wide, 5.2 / 4.1 men in the central lane; the sim's 39.6 / 43.3 m and 3.9 / 2.7. Split into edges: with the ball wide the ball-side edge is right (24.9 m from the centre both), the far-side edge is not (real 7.5 m, sim 15 m); with the ball central real 17.3 / 10.2, sim 20.1 / 15.9. The widest far-side man is the full-back with no man to mark, standing where the area limit allowed (20.2 m) and lagging 5 m behind his target when he had one; the rest are men on urgent jobs, exempt from the limit.
Fixed (`movement.py`): the unit's width near goal comes from the real data: men away from the ball stay within `area_half` 13.75 m of the middle, less as the ball goes wide (`area_slide` 0.25 per metre: the unit slides to the ball, the far side tucks in), the ball side as wide as the ball needs; a man left outside it gets back across at a run (`area_run`). A man wide on the far side is left to the zone (`far_free`, `far_dy` 14 m) and a mark across from the ball is worth less (`mark_lat` 8 m), since the block can shift while a switch is in the air.
Effect (48 matches): loss 160.9 -> 153.4, big chances 0.26 -> 0.41 (0.86), goals 1.34 (1.24), xG per shot 0.08 (0.09), final third central 0.36 -> 0.33 (0.15), far-side edge 15 -> 13.4 m (7.5). Still open: the job-holders exempt from the width limit, shots 17 vs 13, central share.

### The shooting decision against real shares (2026-10-12)

Correction to "Why the sim shoots too much": the real side of `shot_choice.py` kept only Regular Play / From Counter possessions, dropping every
attack that started from a throw-in, goal kick or keeper; counted like the sim's, real teams reach the good spots nearly as often (per team-match,
0.07-0.12 band 10.0 sim vs 7.2 real, 0.12-0.20 4.5 vs 2.2, above 0.20 1.6 vs 1.2). The real gap is the decision: the sim shoots 0.74 / 0.94 of
its actions in the two best bands against 0.39 / 0.62, and too rarely from range (0.09 vs 0.16 in the 0.04-0.07 band).
Tried, not kept (48 matches, loss 153.4 before): a flatter decision (`shoot_slope` 1.5, `shoot_ref` 0.14), no clear-chance rule (`clear_xg` 0), no
pressure or keeper push (`shoot_press` 0, `gk_shoot` 0.5): range shots match real (0.17 vs 0.16) but the best bands only fall to 0.73 / 0.87 and
shots rise to 19.2 (loss 161.2). The near-goal excess is not in these terms; the likely cause is that real shooters at those spots more often have a
defender in the lane, which the clean-look xG ignores and the sim's thinner central defence (above) still allows.

### Breakdowns: pause the move and draw on it (2026-10-13)
Idea: broadcast telestration (names, lines joining each unit, passing triangles, the in- and out-of-possession shapes) but built
from the tracking, not drawn by hand. `MatchView.breakdown(event_id)` turns the move behind a goal or big chance into stops:
the regain, both shapes, the last pass, the shot. Captions are plain sentences from the numbers, so nothing needs fact-checking.
- Lines without a formation list: `lines_now` fits every split of the outfield players into 3 or 4 lines (Hungarian, as the
  formation reader) and keeps the best, with a small penalty for a fourth line. Depth-gap clustering alone was tried first and
  failed: a deep block puts 7 players within 3 m of depth (read as 3-7), and attackers spread in depth read as 2-2-1-2-3.
- The lines are read from the last 4 s, not the last minute: a 60 s average gave a sensible label but the lines drawn through
  where the players stood at that frame zig-zagged. The shape drawn has to be the shape read.
- Open lane: no opponent within 2 m of the straight path. A triangle is two open options that can also pass to each other.
  A run is 10 m forward in the next 2.5 s (5 m caught jogging). The numbers are players within 15 m of the ball.
- Rebounds within 3 s get one breakdown, ending on the last shot.
Readings in transition can be odd (2-4-4 without the ball). That is where the players were; it is not smoothed away.

### Press board counted restarts as regains (2026-10-13)
Saw: hovering the Press board dots showed "ball won" with no player. Measured: every possession change was plotted, including
throw-ins and goal kicks (logged at the first touch, up to 25 s after the ball went out). Fixed: a regain is a possession
change that follows a winning action by that team (interception, tackle, recovery, block, duel, save...) within 2 s; the dot
now carries who won it and how.

### The agents' own tactical reading scored, and where the time goes (2026-10-13)
The formation question now also asks why, and the eval marks the reasons: a grader agent compares the reason given for each
change with the engine's logged one (protecting the lead, chasing the game, man down, man advantage). The tactics analyst was
told to explain a change from the match state at that minute (score, red cards, substitutions), which is what a manager reacts to.
Result over the 5 demo matches (25 questions): 80% right overall; scorers and best player 100%, pressing 80%, formations 60%.
System changes: 8 of 14 found with formation and minute, the minute off by 4.6 on average, the manager's reason right for
4 of the 8. Every answer passed the fact-checker, so the misses are reading errors, not invented numbers: an answer can be
correct to the tools and still wrong about the match. The weak link is the change reading, e.g. a 3-4-3 that defends as
5-4-1 reported as a switch to 5-4-1 in both phases.
Time: tracing showed a question at 38 s on average (about 7 model calls in a row at 5 s each) and one model call that hung
for 103 s, the SDK's default timeout being 10 minutes. Calls are now cut at 45 s and retried; the orchestrator calls both
specialists in one step when it needs both; the eval runs three matches at a time.

### Tracing (2026-10-13)
Application Insights connected to the Foundry project (infra/setup.ps1). `make_client` turns on Agent Framework's
OpenTelemetry once per process, so each question is one `newsroom.ask` trace with the agent runs (orchestrator, tactics
analyst, scout, writer, fact-checker), every tool call and every model call, with durations; prompts and replies are not
recorded. The Foundry portal's Tracing page reads it. Deployed, the container gets the connection string as an env var.

### Units (2026-10-13)
The reader picks pitch distances (m, yd, ft), speed (km/h, mph, m/s) and distance run (km, mi). For the agents, tool output is
converted before any agent sees it (every quantity is named by its unit: *_m, *_kmh, *_km, renamed to the new unit), so the
writer never converts and the fact-checker's number checks still hold. Drawings keep the metre values. In the site, numbers
are formatted in the chosen units and published text (commentary, recaps, breakdown captions) is converted before it is
translated.

### What a shooter faces, and where the excess shots come from (2026-10-13)
Hypothesis from the last entry: real shooters at good spots more often have a defender in the lane. Measured with
`scripts/shot_lane.py` (Euro 2020 360 freeze frames vs sim, by clean-look xG band, defenders in the ball-post triangle and the
nearest opponent): wrong. The sim's situations are as crowded as the real ones. Two things differ:
- Counting: real data logs a carry before almost every shot, so counting carries as decisions halves the real shot share.
  Counting only the action that ends a touch (`release=1`), with a clear lane the sim decides like real players (0.59 vs 0.52 in
  the 0.07-0.12 band).
- With defenders in the lane real players still shoot (0.6-0.85) and the sim backed off (0.03-0.35): the decision multiplied the
  clean-look xG by (1 - p_lane), against the code's own comment that willingness follows the spot, not the bodies in the way.
  Removed (`shot_lane_k`): through-traffic shooting moves to 0.42 / 0.24 for 1 / 2+ in the lane, loss 157.4 -> 148.6 (48 matches,
  noise about 10). Still too eager at clean 0.12-0.20 spots (0.9 vs 0.5): the clear-chance rule, next.
The bigger cause of the shot excess is volume: releases at clean-look xG >= 0.12 are 5.8 vs 2.5 per team-match.
`scripts/zone_entry.py` splits them by how the player got the ball (sim | real): forward pass from in front of the box 1.9 | 0.5,
won it himself in the zone 1.2 | 0.3, a defender's mistake 0.5 | 0.02, carried in 0.8 | 0.3, cross 0.9 | 0.8, cutback 0.1 | 0.45.
(Two measurement bugs found on the way: the sim logs a carry after the pass or shot that ends it, and its many pressure events
pushed the pass to him out of a 12-event window.) `scripts/zone_passes.py`: passes aimed at the zone from in front of the box are
tried 5.7 vs 3.3 per team-match and arrive 0.43 vs 0.29; `scripts/zone_pass_defence.py` shows that with the same defenders in the
path and near the target they arrive about as often as real ones. So the excess is the choice to play into the zone (and too
few cutbacks: 0.8 vs 2.1 tried), not the interception.

### The keeper could be tackled with the ball in his hands (2026-10-13)
Saw: in the "won it in the zone" cases, attackers tackling a keeper who had just saved or claimed the ball (about 0.3 per
team-match, right in front of goal). Law 12: a keeper holding the ball cannot be challenged. Fixed: a keeper who takes the ball
in his box from a ball the opponent touched last has it in his hands (`s.hands`) and is not tackled until it leaves him; a ball
his own side played to him stays at his feet (the back-pass rule). Loss within noise; the case is gone.

### Corners, and two changes that did nothing (2026-10-13)
`scripts/corner_source.py`, corners per match by the last action before the ball went behind (sim | real): after a shot 3.0 | 4.9,
clearance 0.9 | 2.5, duel 0.25 | 1.0. Blocked shots go behind 0.14 of the time vs 0.27; saves are tipped behind as often as real
(0.19 vs 0.22) but the sim has about twice the saves per match, so its on-target share is too high.
Tried, not kept: a loss cost that mirrors the attackers' position value (losing the ball costs what the opponent then holds,
`loss_mirror`): the box losses did not fall. A clearance limited by the way the defender faces (no time to turn: only behind or
out): clearance corners 0.92 -> 0.83, since defenders rarely reach that branch; the clearance corners must come from headed
and first-time clearances of crosses. Both removed.
Next: why passers choose the zone (and not the cutback) so often; the clear-chance rule at clean 0.12-0.20 spots; blocks that
kill the ball instead of deflecting it on; the on-target share.

### Why passers find the zone: markers stand off (2026-10-13)
The pass target is a choice model fitted to real passers (`matchsim/policy.py`), so more passes into the zone means the options
differ. `scripts/zone_options.py` (passers in front of the box, 360 frames vs sim): about as many team-mates in the zone
(0.17 vs 0.12 per pass), but tightly marked (a man within 1.5 m) 8 % of the time against 43 %, though the sim has more defenders
in the zone (0.75 vs 0.36): they stand near the attackers, not on them.
A live probe (wrapping `movement.team_targets`): every attacker in the zone has a defender assigned to mark him, and his target is
tight (within 1.5 m) 74 % of the time, but he is there 7 % of the time; he is 3-6 m short, running at 3-4 m/s. (A first probe
reading the targets wrongly, in the team frame instead of world coordinates, pointed at the target first.) The approach speed is
proportional to the gap (1.1 x (d - 0.4)), so a man moving at 4 m/s is followed 4 m behind.
Tried, not kept (48 matches; loss before 148.6, noise about 10):
- marking gap shrinking to arm's length near goal (`mark_gap(o)`): no change in tight marking, loss 163.3;
- following the target's velocity (feed-forward of the smoothed target motion): loss 409, everyone runs 16 km, because block
  shifts move every target, not only a marker's man;
- arriving at the physical limit (v = sqrt(2 a d)) for urgent moves: loss 192.7, 12.1 km per player.
The next attempt should apply to a marker following his man only (his man's velocity, not the target's), not to all movement.
- that too: a marker within 8 m of his man adds the man's velocity (`mark_ff`): tight marking unchanged (7 %), loss 165.1, goals up.
  Not kept. The gap needs a probe that follows single markers over time, not more velocity terms.

### One decision per touch, and no shot by rule (2026-10-13)
The shooting decision was a fresh random draw at every decision tick while the player held the ball, so a 70 % look became a
90 %+ shot over the touch; and a "clear chance" rule shot every time xG >= 0.10 unless a team-mate had a better one. Real
players at clean 12-16 m spots pass about half the time. Now a player draws his threshold once per touch and shoots when the
chance's appeal crosses it (as he closes in, or the keeper comes); the clear-chance threshold still makes him act quickly, but
does not force the shot. Shots 17.6 -> 16.6 per team-match, goals 1.28, loss 158.1 (noise). The share at clean 0.12-0.20 spots
is still 0.83-0.95 against about 0.5: the decision's weights, for the calibrator once the logic is done.

### Blocks, and where shots end (2026-10-13)
`scripts/block_after.py`, after a blocked shot (sim | real): corner 0.15 | 0.25, out otherwise 0.06 | 0.06, the shooting side keeps it
0.38 | 0.32, the defending side has it 0.41 | 0.37 (the keeper collects 0.05). The 2-D contact model gets everything but the
corners; the likely missing piece is height: a shot is in the air, and a leg sends it up as well as sideways, looping over the bar.
Not done yet: it is new physics (a deflection as a lofted flight) for about 0.6 corners a match.
`scripts/shot_target.py`, unblocked open-play shots with the feet inside 12 m (sim | real): 3.5 | 1.6 per team-match, off target
0.53 | 0.39, scored 0.14 | 0.30. The sim takes twice the close shots and they are worse: mean xG by our own formula 0.13 for the
sim against 0.18 for the real ones (scored on the real freeze frames).
Our xG formula itself under-rates real shots (Euro 2020, 701 open-play shots: 0.090 predicted against 0.101 scored; 9-16 m worst).
The maximum-likelihood refit in `scripts/fit_xg.py` (fitted on Premier League 2015/16, never switched on) predicts the Euro shots
better out of sample (log-likelihood -206.3 vs -209.3, every band within a point or two). Switched on, goals go to 1.5-1.7 per
team-match (real 1.24) for any shooting reference (0.168 / 0.22 / 0.28; loss 167-180): a truer finishing model exposes the volume
of close chances. Kept off until the volume is fixed; then switch it on and re-derive the shooting reference from the real
per-band decision shares (`shot_lane.py release=1`).
The root of the shot excess is therefore the defending near goal (markers who trail their man, above), not the shooting.

### Markers near goal: tighter, and late sooner (2026-10-13)
Following single marker-attacker pairs tick by tick (a probe wrapping `movement.team_targets`): the marker's spot was about 2 m
from his man (the marking gap, 1.1 + 1.4 x (1 - marking), plus the lean to the ball), and he counted as late only when more
than 4 m off it; inside that his approach is 1.1 x gap x work rate, about 0.5 m/s, so he parked 1-2 m short of a spot already
2 m off his man. Now the gap shrinks near goal to arm's length (`mark_gap`: x clip(distance to goal / 25 m, 0.45, 1)), and a
marker is late when he is off his spot by more than the room he can give (4 m far out, 1 m near goal). Tight marking (a man
within 1.5 m of an attacker in the good shooting spots, as a pass is played from in front of the box) 0.08 -> 0.14 (real 0.43),
shots 16.6 -> 16.0, goals 1.38, loss 156.4 (noise).
Tried, not kept: approach gain without the work-rate factor (work rate should cap speed, not precision): tight marking 0.15,
13 km per player, loss 174.7. The rest of the gap is still open: the tracking at the moment of a pass is a moving situation, and
the marker lags his man however the spot is set; next is how real markers hold a moving man (body to body, matching his run).

### The 1.6 m bubble: why nobody could mark tight (2026-10-13)
`zone_options.py` with the full distribution of the nearest defender to an attacker in the good shooting spots: the sim had none
under 1.0 m (real 22 %) and 46 % at 1.5-2.0 m (real 16 %). A hard floor, even by chance, means a rule. Two candidates found:
- Attackers stepped away from any defender within 4.5 m ((4.5 - d) x 0.8 m, every target update, anywhere). Switched off inside
  the box (`show_box`): no change to the floor, so not kept.
- After every step, any two players closer than 1.6 m were pushed apart: a personal-space bubble. The closest pair of opponents
  in a whole match was never under 1 m (1 % quantile 1.22 m). Bodies touch at about 0.7 m. Now `body_sep` 0.7.
With 0.7 m the nearest-defender distribution matches the real one bin by bin (<0.75 m 0.12 | 0.10, 0.75-1 0.09 | 0.12, 1-1.5
0.24 | 0.21, 1.5-2 0.16 | 0.16, 2-3 0.18 | 0.24); tight marking 0.45 against 0.43. This, not the marking spot or the reaction
lags (tried: target every tick, faster speed build-up; no effect), was the floor.
It uncovered a geometry fault in the ball's flight: a defender within 1 m of the ball's path this tick could intercept, but the
path is clipped to start at the ball's last position, so a man beside or behind the ball, which was going away from him, counted
as on the line: a presser at the passer's shoulder got a swipe at every pass (78 % of in-flight deflections were in the first
fifth of the pass). Now only men the ball comes towards or goes by can block it.
Effect (48 matches): shots 16.6 -> 15.6, goals 1.26 (1.24), loss 156 -> 164.6. The rise is constants balanced against the bubble:
blocks 37 (real 20), tackles 24.1 (20.5), more passes counted under pressure (the sim's pressure is "an opponent within 1.75 m",
not StatsBomb's pressing events). The passer seeing a body on the line (the flight's rule in his estimate) changed nothing; not kept.
Next: blocks from pressers in front of the passer, then the calibrator for the constants tied to spacing, then the xG refit.
Then: a defender's reach for a passing ball grows with the time he has to read it, from his body (0.5 m) at the strike to 1 m after
0.3 s (it was 1 m from the first instant, the chance scaled down instead): blocks 37 -> 33 (real 20), loss 161.9. How the ball now
reaches the good shooting spots (per team-match, sim | real): forward pass 1.44 | 0.52 (was 1.83), carried in 1.21 | 0.33 (was 0.79),
won in the zone 1.21 | 0.28, a defender's error 0.42 | 0.02, cutback 0.15 | 0.45; all 5.6 | 2.5.

### Carries into the box (2026-10-13)
`scripts/zone_carries.py`: carries that end in the good shooting spots, 1.0 vs 0.38 per team-match; real carriers there mostly have a
man within 2 m as they set off (0.52 vs 0.21) and go 2-5 m, sim carriers have 2-4 m of room and go 5-10 m. The presser's spot is a
jockeying gap goal-side of the carrier, so as the carrier comes on he backs off with him, into his own box. Now he holds the edge of
the box (`hold_d` 17 m): he does not retreat inside it unless the carrier is already there. Loss 161.9 -> 154.3, shots 16.1 (noise;
the carries into the zone did not fall measurably). Tried with it: the jockeying gap shrinking to arm's length near goal like the
marking gap: carries 0.79, but shots 17.0 and loss 165.2; not kept.

### A loose ball can be in the air (2026-10-13)
Corners 4.7 vs 11.6. `block_after.py`: blocked shots go behind 0.10 vs 0.25; `clear_out.py`: clearances in a defender's own box go
behind 0.02 (headed) / 0.05 (kicked) vs 0.11 / 0.11, and kicked clearances there are 5.2 vs 11.7 a match. In the real data the
clearances that give corners are mostly headed clearances of crosses, central, a third in the six-yard box: the ball skims up off
the head and goes over the line or the bar. The engine's loose ball was flat, so none of that could happen.
Now `_loose_ball(v, vz)`: a ball sent up as well keeps its pace while in the air, is over everyone's head above 2 m, and over the
goal line above the bar (2.44 m) is out, not in. A blocked shot keeps part of its lift (struck rising 10-20 degrees: vz = pace x
U(0, 0.35)); a header is directed only as far as the defender faces out and is not hurried (q = (1 + facing . away) / 2 x
(1 - pressure / 2)); the rest is a glance off his head (the contact model) that goes up and on. Blocks behind 0.10 -> 0.13, headed
clearances behind 0.02 -> 0.04, goals 1.17 (1.24), corners still about 4.8: the rest is in the volume of kicked clearances and in
more 3-D deflections (crosses blocked, tackles). Loss 165 (noise about 10).

### The back line near goal sits too deep (2026-10-13, open)
With marking now real (tight marking 0.39 vs 0.43, the nearest-defender distribution matching bin by bin), passes into the good
spots are still tried 5.3 vs 3.3 per team-match: more attackers stand there (0.14 vs 0.08 per pass) and twice the defenders
(0.68 vs 0.36). The last outfield defender is within 8 m of his goal line 25 % of the time when the ball is 15-35 m out, against
2 % in the real frames. Real back lines follow a simple law: the last defender stands at half the ball's distance from goal plus
3 m (9.0 / 12.2 / 14.5 / 17.0 / 18.6 m with the ball 12 / 17 / 22 / 27 / 32 m out); the sim's line keeps a fixed 10 m behind the
ball down to 6 m from the line.
A probe of the deep defenders (team_targets wrapped): unassigned full-backs and centre-backs, the open-play 'post' job (a man on
the near post once the ball is within 26 m, a set-piece habit), and markers following a forward who stands on the last line (a
ratchet: the forward stays on the shoulder, the marker goes goal-side, both drop). Tried together: the half-distance line
(`half_line`), no post job in open play, a marker held level with the line when the ball is outside the box (`hold_line`), and the
line stepping up at once when left 3 m deep (`squeeze`): deep defenders per moment 0.51 -> 0.28, nearly all of the rest on their
way back up; chance volume 5.6 -> 5.2, goals 1.21, but offsides 3.4 (real 2.0) and loss 171.7. Not kept: a higher line exposes the
forwards' run timing. Next: the forwards' timing against a line that holds, then these four together again; and why urgent
recovery is jog pace (the need term reaches flat out only far from the target) while total running is already 11.7 km (10.8).

### Forwards: no planned mistiming; runs timed to the passer (2026-10-13)
Offsides were produced by design: a forward on the last defender's shoulder had a sine wobble of up to 2 m added ("now and then
mistimed"), and runs in behind started at random moments whatever the man on the ball was doing. Now a forward stands just onside
and is caught only when the line moves faster than he reacts (movement lag, and the passer seeing him a quarter-second late), and a
run starts only when the carrier can play it (facing up the pitch, nobody within 2.5 m). 96 matches: offsides 1.99 (real 1.98),
goals 1.18 (1.24), shots 15.2 (from about 16), loss 166 (noise about 10).
The four back-line changes of the last entry, re-tried with this in place on 96 matches, still left the last defender's mean depth at
12.9-13.5 m (real 16.2) and holding the line raised offsides to 2.7: they do not move the line, so not kept. The deep line has
another cause; the probe showed almost all deep defenders with higher targets, i.e. coming back up slowly after defending the box.

### The back line near goal, found (2026-10-13)
`scripts/line_depth.py` (the last outfield defender's depth by the ball's distance from goal, every central on-ball frame vs real
on-ball freeze frames): beyond 25 m the sim matches (30-35 m: 18.8 vs 18.6); inside it collapses (15-20 m: 8.1 vs 12.2, lower
quartile 3.6 m). A probe of the last defender at those moments: a fifth of the time he had the open-play near-post job (target
2.4 m from goal), and the unassigned full-backs aimed at the shape's fixed 10 m behind the ball; with those fixed, the targets were
right (14.1 m) but he stood at 7.4 m, stepping up at a stroll. Now: no near-post job in open play (the keeper has the goal; a man
on the post is a set-piece habit), the back line at half the ball's distance plus 3 m (the real relation), and a defender left
more than 3 m behind his spot steps up urgently. Line depth 15-20 m: 8.1 -> 10.2 (12.2), 10-15 m 4.9 -> 7.8 (9.0), 20-25 m
10.9 -> 12.4 (14.5); offsides 1.97 (1.98). The chances do not fall (releases in the good spots 5.6 either way), shots 16.5,
goals 1.34, loss 172 (96 matches; 166 before): the line was not what makes the extra chances. The biggest route left is the
attacker winning the ball in the zone himself, 1.5 vs 0.28 per team-match.

### First-time clearances in your own box (2026-10-13)
`scripts/clear_after.py`: after a clearance from his own box the clearing side keeps the ball 0.65 vs 0.28, and it goes out 0.08 vs
0.24; `scripts/box_play.py`: outfield players in their own box make 80 passes a match against 17.5 (37 of them to a team-mate also in
the box, 5.5 real) and 17 clearances against 27; `scripts/box_shape.py`: with the ball there, 4+ team-mates are in the box 61 % of
the time (real 24 %), almost all within 8 s of winning it. Where those possessions start: 50 of the 80 passes follow a defender
picking up a loose ball in his own box and taking it under control. Real defenders clear a loose ball there first time when a man
is on them: there is no half-second to take it down. Now an outfield player who reaches a loose ball within 20 m of his own goal
with an opponent inside 3.5 m (the clearance option's own distance) clears it first time. Box passes 80 -> 57, clearances 17 -> 37.5
(real 27, now over), corners 4.8 -> 7.7 (11.6), loss 157.8.
Tried, not kept: a faster switch to the attacking shape after a regain (`fade_up` 0.8 / 0.5 instead of 2.2 s) and the loss cost as
the opponent's position value (`loss_mirror`): box passes unchanged at 80. The volume of chances near goal (5.6 per team-match vs
2.5) did not move with any of this; its biggest single route stays the attacker winning the ball in the zone.

### The ping-pong: too many turnovers (2026-10-13, open)
Passes 604 vs 484 per team-match; by third (`norms`) the excess is the own third, 177 vs 101, at 84 % vs 73 % accuracy.
`scripts/own_third_passes.py` by length: under 10 m 41 vs 14 per team-match, 10-20 m 61 vs 35; long balls from there arrive 0.69 vs
0.49 (real long balls from the back are contests). `scripts/own_third_options.py`: the forward options are there about as often
as in real play, and the pass policy reproduces the real direction on the real frames (forward 0.51 predicted, 0.52 actual), so the
target choice is not the fault; but the carrier's nearest team-mate is within 5 m 46 % of the time (real 11 %) and a man is on him
48 % (34 %). `scripts/own_third_timing.py`: own-third passes by seconds since the side won the ball, per team-match (sim | real, all
possessions): 0-3 s 63 | 16, 3-8 s 49 | 21, 8-20 s 31 | 24, 20 s+ 9 | 19. Possession changes hands 267 times a match against 170.
A loop: a regain in a compact defending shape, a quick short ball to a team-mate a few metres away, lost again. It feeds the extra
chances near goal (the attacker winning the ball in the zone), the passes around the own box and the short possessions. Next: what
happens in the first seconds after a regain (who is near, what the new carrier chooses, and how real players use those seconds:
carry out, go long, clear) before changing anything. `scripts/poss_end.py` compares how possessions end; the two event models label
endings differently (real logs a failed receipt, the sim a carry after the pass), so read it by group.
Correction (same day): the possession counts are not comparable. StatsBomb starts a possession only when the other side
establishes control; the sim logs a change on any touch it gains, so a ball cut out and lost again at once is two changes.
Interceptions (11.9 vs 10.9) and pass accuracy (0.75 vs 0.75) match. `scripts/regain_first.py`: the first action after a regain in
the own half is real in kind (short pass 0.39 | 0.46, 15-30 m 0.28 | 0.24, long 0.06 | 0.07). `scripts/crowding.py`: the carrier's
nearest team-mate within 5 m, real 0.16 on all on-ball frames; sim 0.38 in the first 3 s after a regain and 0.15-0.19 after, so the
shape is right once a possession settles. The excess short passing at the back is the many brief possessions (24 % of on-ball
frames are within 3 s of a change), i.e. the ball bouncing between the sides; next is why contested balls change hands so often
(loose balls, 50-50s, deflections), measured on a possession definition that matches StatsBomb's.

### Big chances: crosses into space (2026-10-14)
Big chances 0.23 vs 0.86 per team-match while goals matched: goals follow xG, so 30 % more shots at about 22 % less xG each give the
same total, from many scrappy chances instead of fewer clean ones. Scored with the same formula on both sides (our xG on the real
freeze frames), real teams have 0.82 big chances per team-match (0.99 with StatsBomb's own xG), so the gap is the sim, not the
model. `scripts/big_chance_source.py` (the ball that set the shooter up, the last ball in by a team-mate within 4 s; a header off
a cross has no completed pass to him, which first hid those): real 0.35 from crosses and 0.25 from cutbacks, the sim 0.08 and 0.02.
`scripts/cross_outcome.py`: crosses reach a team-mate and lead to a shot about as often as real ones, but 54 % of real crosses end
within 9 m of the goal line against 20 %, and the shots after them come from the penalty spot rather than the six-yard area
(inside 6 m 0.04 vs 0.12, 11-16 m 0.45 vs 0.25). The crosser aimed at where the runner stood and only the intended man moved during
the flight. Now a ball in from wide (the cross and any lofted pass from wide into the box) is aimed where the runner will arrive,
his position carried towards goal by up to 5 m and no closer than 6 m; and while it is in the air the attackers in and around the
box attack the space towards goal (offside no longer applies after the kick). Cross shots by distance now 0.10 / 0.48 / 0.30 / 0.12
(real 0.12 / 0.48 / 0.25 / 0.15). 96 matches: big chances 0.23 -> 0.74 (0.86), xG per shot 0.07 -> 0.09 (0.09), cross rate 0.27
(0.28), goals 1.34 (1.24), shots 16.4, loss 162 -> 145. By source: crosses 0.08 -> 0.23 (0.35), other passes 0.11 (0.10), but
loose balls and rebounds 0.28 (0.08): runners charging the six-yard box also feed scrambles. Cutbacks still 0.03 (0.25).

### Cut-backs along the ground (2026-10-14)
`scripts/byline.py`: the sim gets to the byline about as often as real teams (9.8 vs 10.9 actions per team-match) and plays as many
balls back to 6-18 m (2.6 vs 2.1), but `scripts/cutback_outcome.py` shows they reach a team-mate 0.27 vs 0.44 of the time, cut out
at the receiving end (clearances), because 79 % went in the air (real 30 %): the cross routine always lofted the ball. From the byline
a ball pulled back towards the spot now goes along the ground. Reached 0.32, big chances from cut-backs 0.03 -> 0.06 (0.25); the shot
after one is still weaker (xG 0.099 vs 0.144), likely because the sim's defenders read the ball's destination at once.
Goals are now high: 96 matches 1.60 (1.24), shots 16.9 (12.9), big chances 0.78 (0.86), loss 151. Big chances with no ball in from a
team-mate are 0.23 vs 0.08: loose balls near goal picked up by attackers (rebounds off blocks 0.05, 50-50s 0.03, defenders' lost
balls 0.04). The shot volume is the next target.

### A cross valued like a pass (2026-10-14)
Shots by what set them up, all open-play shots per team-match (`big_chance_source.py`, sim | real): after a cross 5.7 | 3.2, after
another pass 6.7 | 4.5, no pass 3.4 | 2.6, cut-back 0.5 | 0.8. `scripts/wide_choice.py`: on the ball wide in the final third the
sim puts it into the box 31 % of the time (real 17 %) and down the line 10 % (23 %). The cross option was a fixed bonus (`cross_v`)
times its chance of arriving, whatever the spot; every pass is valued by the territory it gains. Now the cross is valued the same
way: its chance of arriving (to the power the passer weighs risk with) times the spot's worth, against the cost of losing it there.
Balls into the box from wide 16.9 -> 13.0 per team-match (11.2), cross rate 0.28 (0.28), goals 1.49, shots 16.1, loss 152 (48).
`cross_v` is gone. Down the line is still 12 % (23 %): a question of who is there to receive it.

### Where the shot excess comes from now, and the central overload (2026-10-14, open)
The shooting decision against real decision shares (`shot_lane.py release=1`, now printing the share per band): the sim's is too
steep (0.07-0.12 spots 0.42 vs 0.61, 0.12-0.20 0.79 vs 0.60, 0.20+ 0.99 vs 0.81; real players' willingness hardly rises with the
chance), but refitting it would not cut shots: the sim reaches every band about twice as often (7.6 / 3.8 / 2.1 vs 4.3 / 1.5 / 1.0
releases per team-match), so the excess is getting there. `scripts/lateral_panel.py`: final-third passes start central 0.25 vs 0.11
and wide 0.39 vs 0.52; `scripts/role_receptions.py`: in the opposition half real central midfielders receive a median 19.9 m off
the middle (0.49 within 14 m of a touchline), strikers 15.2 m, defensive midfielders 15.1 m; the sim's 9.3 / 8.6 / 8.7 (0.15 /
0.14 / 0.10). The sim attacks through a crowded middle; real teams stretch the pitch.
Tried, none moved the medians (so not kept): the midfielders' late run to the edge of the box off; standing where real passers
would pass (`pol_space`, loss 177); wider in-possession slots for central midfielders with a stronger ball-side drift for the
defensive midfielder, attacking midfielder and striker; the near-box template with central midfielders 20 m off the middle.
So their receptions are not where the shape puts them: likely the ball-near support (checking to feet, the nearest supporting
player) and the pass choice. Next: where exactly central midfielders are when they receive, relative to the carrier and to their
target, before changing anything else.
Then (`role_receptions.py` with the passes): in the opposition half real attacks run through the full-backs, centre-backs and
defensive midfielder (receptions per team-match FB 50 vs 23, DM 32 vs 10; CM 35 vs 61, ST 29 vs 57; totals alike, about 190); real
passes to central midfielders come mostly from full-backs (0.28) and centre-backs (0.21), the sim's from wingers, central
midfielders and strikers. Real teams circulate around the outside of the attack (a U through the back line and full-backs) and the
sim's front five or six attack through the middle among themselves. That, not the slots, is the central overload. Next: why the sim's
full-backs and defensive midfielder are so rarely the pass in the opposition half (where they stand, whether they are open, and the
pass choice towards them).

### Is the central overload the defence or the passer? (2026-10-14, open)
Tactics or logic: with both sides told to play at width 0.95 instead of 0.5, central midfielders still receive 9.7 m off the middle
(real 19.9) and full-backs 22 times per team-match in the opposition half (50): the width instruction only scales the formation
slots, which the in-game routines override. Engine logic.
Defence or passer (`scripts/lane_options.py`, passes from 55-88 m, options 5-40 m away, open = 3 m free and a 2 m-clear lane):
central options are open 0.25 of the time against 0.24 real, open team-mates per pass by lane wide / half / centre 0.76 / 0.73 / 0.48
against 0.66 / 0.79 / 0.45; the defence closes the middle as real defences do. Yet the pass goes central 0.31 vs 0.16 (the intended
receiver stands central 0.29, so it is the choice, not the aim). The pass policy on the real frames predicts the centre 0.20 (actual
0.16), so it is not biased much. The difference is distance: options within 12 m per pass, wide / half / centre, 0.28 / 0.32 / 0.27
against 0.54 / 0.62 / 0.26 (mean distance to wide options 24.4 vs 16.5 m). Real attacks give the carrier close support on the flank
and in the half-spaces (the full-back, winger and central midfielder near the ball), so the short safe pass is there; the sim's
nearest good option is central.
Tried, none moved the close wide support (not kept): the support ring (`ring_min` 6, `ring_pref` 12, `support_min` 6); the support
triangle at 10 m (it never runs: the Open Football attacking plan `of_plan` skips it); the plan's short outlet (`of_slots`: more
central, shots 18.5); the block sliding further to the ball side (`slide_c` 0.6 / 0.8, with `slide_w` 0.6): more central. Removing
the plan's own hover wobble (the forwards' cap) raised offsides to 2.51 and goals to 1.67: kept as it is.
Next: the space-finding routine re-places most attackers every second (open / lane / threat / distance terms); the threat term
rewards central spots near goal. The fix is likely there or in an explicit ball-side flank structure (full-back, winger, near
central midfielder within 8-15 m of a wide carrier), measured with `lane_options.py`.

### Audit: the 360 camera window (2026-10-14)
StatsBomb 360 frames hold only the players inside the broadcast view. For real passes from 55-88 m the view covers 93 % of the circle
around the passer at 8 m, 81 % at 12 m, 57 % at 20 m, 35 % at 30 m (fully covered in 67 / 28 / 3 / 0 % of frames). Every comparison
that counted real players more than about 10 m from the ball under-counted the real side. `scripts/camera.py` lends the sim the same
window: for a sim moment, the visible area of a real frame with the ball within 4 m of the same spot, shifted onto it; sim players
count only inside it. Re-checked with it:
- `lane_options.py` (per-pass figures are average counts of team-mates, not shares): within 5-40 m of the passer, wide / half / centre,
  sim 0.99 / 1.16 / 1.01 against real 1.48 / 2.63 / 1.86; within 12 m 0.23 / 0.27 / 0.23 against 0.54 / 0.62 / 0.26; open 0.32 / 0.31 /
  0.22 against 0.66 / 0.79 / 0.45. Correction to the entry above: the sim gives the carrier about half the support of a real team in
  every lane, not only on the flank; an option, once there, is open about as often as a real one (centre 0.22 vs 0.24), so the
  defending is not the fault. The sim's whole team (no window): 3.6 outfield team-mates within 20 m of the passer, 4.5 at 20-40 m,
  1.8 beyond (1.4 of them behind the ball).
- `zone_options.py`: tight marking holds (0.42 vs 0.43); defenders in the good spots 0.40 vs 0.36. Correction: the earlier
  "0.68 vs 0.36, twice the defenders" was the window; nothing was kept on it.
- `line_depth.py` (sim through the window, the same at-least-6-defenders-in-view rule): 15-20 m 9.8 vs 12.2, 20-25 m 12.5 vs 14.5;
  the line is still deeper than real near goal, by about 2 m.
- `cross_outcome.py`: attackers in the box when a cross is hit 1.26 vs 2.30 (it read 1.73 without the window), free ones 0.77 vs
  1.33; defenders 3.91 vs 4.12.
Not affected (they look only within a few metres of a player): crowding within 5 m, the nearest-defender distances, where passes and
shots went, shot and cross outcomes.
Tried for the support (no window change in the result; not kept): a block led 8 m ahead of the ball instead of 16, and 0.7 of its width.

### Calibration after the logic changes (2026-10-14)
With the logic changed under it (the per-tick shot dice, the 1.6 m bubble, the crosses), the TUNE weights were out of balance. One
coordinate-search round of `scripts/tune_norms.py` (71 weights, 32 matches per evaluation, on a snapshot copy): 158.7 -> 111.8 on its
own matches. Checked on 96 matches it never saw (`eval ... seed0=6000`, new option): 154.0 -> 121.6, real; but big chances 0.66 ->
0.47 and tackles 24 -> 27.5 (real 20.5). Ablation (the tuned set with one change undone, 64 unseen matches): the tackle rate
(1.2 -> 2.16) costs the most big chances (0.84 without it, tackles 18.4), then the cover value (3.5 -> 5.6, 0.74) and filling the box
lanes from 32 m (0.72). Kept: the tuned set without those three (unseen 96: loss 132.3, big chances 0.73 (0.86), tackles 18.5
(20.5), blocks 23.7 (20.1), goals 1.40 (1.24), shots 16.1 (12.9), offsides 2.64 (1.98), corners 7.3 (11.6), xG per shot 0.09).
Changed: patience 0.02 -> 0.2, ft_shot 0.315 -> 0.1, pot 2.2 -> 1.54, ctrl_marked 0.5 -> 0.305, press_len 3.2 -> 4.1, dec_noise
0.15 -> 0.3, press_v 7 -> 4.3, intercept 0.429 -> 0.309, follow_poss 0.2 -> 0.5, wide_v 0.35 -> 0, block_base 0.12 -> 0.05.
The logic checks hold: tight marking 0.42 (0.43); big chances by source cross 0.33 (0.35), cut-back 0.09 (0.25), loose balls 0.27
(0.08), all 0.84 (0.80).

### A support group around the carrier (2026-10-14, tried, not kept)
New behaviour (not the 15 m triangle, the Open Football short outlet or the ring distances, all tried before): the three nearest free
men take short options 8-12 m from the ball at different angles (positional play's triangles; with the carrier wide: down the line,
inside in the half-space, back and inside; with him inside: out to the near touchline, into the other half-space, behind), stepped
off any opponent on the spot (Open Football's presser-shadow idea), onside, kept from the space-finding routine. A probe: it ran
(2.3 men placed per update, targets 10.8 m from the ball) but they stood a median 18 m away: support moves were a jog (urgent only
means "as fast as the gap needs"), so they were made urgent with the sprint hint, and the job made sticky (a man who started keeps
it). Through the camera window: options within 12 m wide / half 0.23 / 0.27 -> 0.29 / 0.35 (real 0.54 / 0.62), passes into the
centre 0.31 -> 0.28 (0.16). But on 96 unseen matches loss 132.3 -> 150.7: more short sideways passing (forward passes in the final
third and lofted passes down), more running (12.3 km), passes 614. Real teams have twice the close support AND fewer passes, so
close support alone is not the missing piece.
Also checked: ball in play 55.7 min of 97.8 (real about 55-58), restarts 20-38 s; so the across-the-board excess (passes +24 %, shots
+25 %, carries +45 %) is not more playing time. It is more actions per minute of play.

### Tempo: twice the passing moves, broken by balls in the air (2026-10-14, open)
`scripts/tempo.py`: the time from a completed pass to the team's next pass is about real (median 2.40 vs 2.69 s); the sim has 145
passing moves per team-match against 80, of 4.1 passes against 6.4 (145 x 4.1 ~ 594 passes, 80 x 6.4 ~ 510). `scripts/move_end.py`
(moves with at least one pass on both sides, so a touch lost at once is not a move on either): they end by a pass cut out or misplaced
35.4 vs 12.5 and by a ball in the air lost 33.8 vs 10.0; shots, out of play, tackles and miscontrols are near real. Pass accuracy is
the same (0.75), so it is what follows a failed pass and how many balls go in the air. `scripts/second_ball.py`: after a header the
winners' side has the ball next 0.80 vs 0.73 (losers 0.19 vs 0.20), so header outcomes are real; but there are 59.6 aerial
contests a match against 30.7. `scripts/lofted.py`: open-play passes in the air, not into the box, per team-match: under 25 m 37.2 vs
11.3, 25-40 m 37.9 vs 12.4, over 40 m 14.6 vs 19.3; they end in a contest 0.17-0.30 vs 0.10-0.20. All with the foot, from every role,
backward and sideways too, 62 % unpressed.
The pass choice model chooses the mode too, and on the real frames it predicts 19.6 % of foot passes under 40 m in the air against 7.2 %
actual (Premier League foot passes: 10.9 %). Cause: its prior-shift correction (`fit_pass_policy.py`, `pl_shares`) targets the
Premier League shares of all passes, headers included (15.5 % in the air under 40 m), while it is only used for passes with the feet.
Refit with foot passes as the target: short-range air offsets go from +0.22 / -0.05 to -0.74 / -0.92, held-out checks hold (forward
share 0.390 vs 0.389); prediction on the real frames 15.3 %; in the sim short balls in the air 37 -> 23 (11), 25-40 m unchanged
(39.5 vs 12.4: the sim's options stand further away, see the support entries). On 96 unseen matches loss 132.3 -> 137.6 and big chances
0.73 -> 0.61, moves 139.8: reverted. The refit is right in kind; it needs the weights recalibrated with it (one tuning round, about 3 h).
Then kept after all, on a rule agreed with the user: a logic fix that is demonstrably right against real data stays even if the loss
dips, since the dip means numeric weights were tuned around the fault; the weights are recalibrated afterwards. A change is only rejected
when it makes the behaviour it targets less like real football (as the support group did: more sideways passing, more passes).

### The support group again, on the corrected pass model (2026-10-15, not kept)
Re-tested because the first test ran on the pass model that over-chose balls in the air. 96 unseen matches and the camera-window
measures, without | with: options within 12 m wide 0.20 | 0.30, half 0.28 | 0.34 (real 0.54 / 0.62); OPEN options wide 0.34 | 0.35,
half 0.32 | 0.34 (0.66 / 0.79); passes into the centre 0.29 | 0.27 (0.16); balls in the air 25-40 m 39.5 | 35.2 (12.4); big chances
0.61 | 0.68; but moves 145 | 152 (80), passes per move 4.1 | 4.0 (6.4), 11.8 | 12.3 km, loss 137.6 | 166.8. Wrong in kind: support
exists to keep the ball, and the moves got neither longer nor fewer, because the extra men are not open: fixed spots around the ball
only step off a defender standing on them, so they land inside the defence's cover. Real support stands in the gaps (free and out of
the passing lanes). The next design should pick support positions by space (pitch control: a spot belongs to whoever reaches it
first), not by fixed shapes.

### Calibration with the corrected pass model (2026-10-15)
One round (`tune_norms.py 32 1`, paused after 22 weights and resumed with `only=` the rest from the saved best): 138.1 -> 122.6 on its own
matches. On 96 unseen matches (`seed0=6000`): 132.3 (before the pass-model fix) -> 128.9. Changed: dec_noise 0.3 -> 0.45, contact_r
1.6 -> 2.05, surv_k 1 -> 0.4, space_v 3 -> 4.8, dang_gate 0.85 -> 1, wide_error 3.2 -> 4, block_base 0.05 -> 0.125. Unseen: offsides
2.03 (1.98), passes 567 (484, from 596), corners 7.35 (11.6), tackles 23.4 (20.5), blocks 23.5 (20.1), goals 1.39 (1.24), shots 16.4
(12.9), 11.5 km, big chances 0.59 (0.86, from 0.73). Ablation: without the tackle reach 138.1 with big chances 0.57, without the
decision noise big chances 0.38; the drop comes from the pass-model fix itself. `big_chance_source.py`: crosses 0.33 -> 0.14 (0.35),
because crosses into the box are now 11.1 per team-match (9.6, from about 17): the over-crossing is gone, the quality per cross is not
(a big chance from 0.01 of crosses against 0.03; a shot after 0.23 vs 0.16). Tight marking holds (0.44 vs 0.43). Moves per team-match
138.6 (79.7). Open: the quality of the chance a cross makes, cut-backs (0.03 vs 0.25), and the support in the gaps.

### Formation reader: late changes and drift (2026-10-15)
Saw: the tactics agent finds few system changes (in-possession changes found 25/57 on 24 fresh matches, `formation_eval.py 24`,
false alarms 33). The engine makes its changes from the 65th minute on, and the reader needed two 7-minute windows in a row
(stepped 3.5 minutes), so a change after about the 80th minute could never be confirmed.
Fixed: the starting shape is still read from the first 7 minutes; after that a 5-minute window slides forward a minute at a time
and a change is the first window that fits another layout better by the same margin, confirmed by the window 2 minutes later.
Windows are cut short at the final whistle (at least 2.5 minutes), so a change near the end can still be seen. Each change now
carries `confirmed_t` (when a viewer could know), which the live moments use instead of a fixed delay.
Drift is not a system: shorter windows see wingers dropping for a spell (4-3-3 to 4-1-4-1 and back) as two changes. A shape that
returns to the one before within 20 minutes is dropped with its return, and two names for one layout (same_shape) are never a change.
Effect (24 fresh matches): found 25 -> 33 of 57, false alarms 33 -> 37, median delay 3.0 -> -0.5 min (the window starts before the
change). Tried: 7-minute sliding windows (28 found, 51 false alarms), 6 min with margin 2.5 (21, 34), 8 min (25, 58); a
15-minute revert hold (33, 45).
Open: most remaining false alarms are 4-3-3 <-> 4-1-4-1 spells longer than 20 minutes and 4-3-3 read as 5-4-1. Not yet checked:
whether the first follows tactic changes (line height, press) that the engine does not log as formation changes, in which case the
shape on the pitch did move and the label, not the reading, is wrong.

### Newsroom accuracy: what the eval was really measuring (2026-10-15)
Eval on the five rebuilt matches (`eval_newsroom.py`): overall 0.76, formation 0.6, pressing 0.6, changes found 8/13, 39 s an answer.
- Unseeable changes were scored. Two of the 13 could not be seen in any data: one at 90+5' (no play left to show it) and one from 3-5-2
  to 5-3-2 (the same layout, 0.003 apart in the reader's fit). `MatchView.truth` now keeps only changes to a different layout with at
  least 2.5 minutes left (the reader's shortest window); the others are listed as `unseeable_changes`.
- Pressing misses on clear cases (PPDA 10.9 against 24.9). Two causes: the pressing tool gave PPDA only per 15 minutes, so the agent
  had to combine windows (an average of ratios is not the match ratio), and the tool counted recoveries and blocks as pressing actions
  while the stats layer (and the standard definition) counts tackles, interceptions and fouls. The tool now uses the standard actions
  and returns `match_ppda`, which a test holds equal to the stats layer's. Pressing 0.6 -> 1.0.
- The agent told every reading as a system change, including without-ball flips (4-3-3 / 4-1-4-1) and window readings. The
  formation tool now returns `system_changes` (shape with the ball) apart from `without_ball_changes`, without the window list, and
  each change carries the match state then (score from the team's side, players on the pitch). Formation 0.5 -> 0.6-0.7 (10
  questions; one answer is the run-to-run noise), changes found 5 -> 6-7 of 11.
- The 39 s was mostly rate limiting: the deployment allows 50 requests and 50K tokens a minute, and the site build (which writes the
  coverage with the same model) was running at the same time. Alone the eval averages 16-20 s. The eval runs two matches at a time.
- Under load the deployment also returns 500s, which `_run` did not retry (only 429), so a site build died; and when a specialist's
  call failed, the orchestrator answered without data ("pressing data unavailable", then a guess from the goals) and the checker,
  comparing with the same empty findings, approved it. `_run` now retries 5xx as well, and the orchestrator calls a failing specialist
  again or says the data could not be read. Request timeouts are retried the same way (a build died on one).
Result on the rebuilt demo matches (engine-v4): overall 0.76 -> 0.84, pressing 0.6 -> 1.0, formation 0.6, best player and goals 1.0,
22 s an answer.
Open: the remaining misses are the reader's: the engine's 4-3-3 is played with the wingers level with the midfield (reads 4-1-4-1),
and its 3-4-3 with ten men defends and attacks as 5-4-1.

### Ratings stopped at 10.0 (2026-10-15)
Saw: two goals and an assist rated 10.0, the same as any bigger match. Cause: the rating is 6.0 plus additive terms, clipped at 1
and 10. Fixed: above 8 and below 4 the scale eases off (tanh), so the ends are approached, not hit (published 1-10 scales flatten
the same way). That match is now 9.5; a hat-trick with little else 8.8. The player report notes that the terms can add to more than
the rating shows.

### Persona walkthrough fixes (2026-10-15)
Walked the fan and analyst views on the rebuilt matches.
- Header overflowed at 1024 px (the units menu pushed it to 1231 px): it now wraps. Static code files are served with
  `Cache-Control: no-cache` (revalidated each load) so a deploy is seen at once; the stale stylesheet hid the fix at first.
- Win probability lagged goals: the series was per minute and the panel showed the last full minute, so straight after a goal it
  read 1-0 beside the 0-0 chances (HAR 17 % while leading). The series now also has a row at each goal and the panel picks the
  latest row by time.
- Fan commentary quoted "ppda 9.09": the fan voice now says what a metric means instead of naming it (takes effect at the next build).
- "Why did they concede?" gave general trends after 10-20 tool calls. Causes, in order of finding:
  1. find_moments filtered by the scoring team and did not say which team a moment belonged to, so "Stonebay, goal" found nothing
     for Stonebay's goals conceded and the agent reported the details "not available". Moments now carry their team, and the tool
     says a goal belongs to the scorer's side.
  2. Minute filters compared seconds / 60 with the clock: 33' runs from 32:00, and the second half restarts at 46' after the first
     half's stoppage (4 minutes here), so a 33-33 filter missed the 33' goal and second-half windows were minutes off.
     `t_at_minute` now uses the engine's clock rule (first half (m - 1) * 60 s; second half meta.half_time + (m - 46) * 60 s;
     events are too sparse to mark minutes, the first event of 60' came 56 s into it). All the desk's minute arguments go through it.
  3. The orchestrator sent the scout as well (player reports for every defender) and the scout broke down guessed event ids
     ("The move: 1' match info"). Team "why" questions now go to the tactics analyst alone, which breaks down each goal; replay
     views are only offered for moves ending in a shot or goal.
  Result: 7 steps, 31 s, and the answer names the move (the passes, the free scorer) instead of PPDA trends.
- The server's answer cache was keyed without the match data, so answers survived a rebuild; the key now includes it.

### A red card given to the wrong team (2026-10-15)
Saw: the Westmoor-Oakhaven reports said "Oakhaven's Arstor Vossez was sent off"; he plays for Westmoor, and the fact-checker
approved it. The half-time reports did not mention the red card at all. Causes: match_summary listed cards without the team, so the
writer guessed; the checker compares the answer with the findings, which carried the same guess. Fixed: cards carry their team; the
recap questions ask for every goal, red card and change of system; and a deterministic check (`agents.wrong_team`, like
wrong_leader) fails any line that gives a player to the other side ("<team>'s <player>"), which sends it back for a rewrite. Run on
the stored reports it found two (match_1 full time, the Westmoor half time); both rebuilt clean. Live commentary also said "this
red_card moment" (the event type copied as is): the commentator now names the moment in plain words. Recaps keep each draft's
checker issues, so a held report can be explained (one was held once and passed on the rerun: the LLM checker varies).

### Pass quality, ball speed and player tags (2026-10-16)
Why: the challenge lists pass distance, accuracy and a pass difficulty rating, ball and shot speed, and player name tags with speed
and distance indicators at thresholds. The engine already logged all of it per event (`length`, `difficulty` = 1 - the passer's
completion chance when he chose the pass, launch `speed`); nothing showed it.
- `analytics/stats.py`: average pass length, average difficulty, hard passes (difficulty >= 0.5) attempted and completed, average
  and fastest shot speed, for players and teams. Hard passes complete at 49-50 % in match_7, so the difficulty reads true.
- `MatchView.player_tags`: speed over a 1 s average (one frame of tracking noise is not a sprint). A fixed 30 km/h bar tagged
  400-630 sprints a match (6 a minute; 32 km/h still 34-218, depending on the squads' pace), so a tag needs 30 km/h and the man's
  fastest of the match so far (+0.5 km/h each time): 21-38 a match. Distance: every 5 km (about 33 a match).
- Match centre: the man on the ball is named on the pitch; tags for 3 s; shot speed and hard passes (length, difficulty, ball
  speed) at the ball; new team stat rows; a "Pass quality" block and a "Hard passes" reel per player; tooltips and action rows
  carry the numbers. The scout's report tool returns the new numbers, named by unit so they convert.
- `build_site.py --keep-agents` recomputes the deterministic parts from `server/data/*.json.gz` and keeps the stored feed and
  recaps, so no model calls. All five demo matches rebuilt this way; only team_stats, players and tags changed.
- Noted, not reopened (engine frozen): the sim's players sprint (> 25 km/h for 1 s) about 60 times a match; real players
  manage roughly 20-40.

### Live run: the newsroom covers the match as it streams (2026-10-18)
Why: the site replayed commentary written by `build_site.py` after the match. The pipeline was already as-of (each fact pack holds
the numbers as they stood, stats are incremental), but nothing showed it running live, and the brief asks for ingest as it happens.
- `GET /api/live` (server/app.py): the server plays the event stream on its own clock (from the viewer's minute, at the chosen
  speed, up to 30x) and sends each moment the moment it arrives; the newsroom (`agents.cover`: stats + tactics, commentator,
  fact-checker, rewrites) runs on it then, on Foundry, nothing cached. Server-sent events: start, clock (1 s), moment, drafting,
  published (with latency), end. Two moments drafted at once; one live run for the whole site (a second gets 409), so judges
  cannot pile up 429s. A viewer leaving stops the run (the next clock write fails, the drafts are cancelled). A model failure after
  `_run`'s retries publishes the plain line from the facts (as when every draft is held).
- Match centre: "Live run" button; the pitch follows the server's clock; the feed shows each moment queued, drafting, then
  published with "Live · N s". Click again to stop (the live feed stays), again for the stored feed.
- Measured: match_7 from 26' at 10x, the goal published 7.2 s after it arrived. The first run waits a few seconds while the server
  loads the match. The cached replay stays the default (free, instant).
- Seen in passing, not fixed: the live goal line called a PPDA of 23.0 "lower", and the checker approved it.

### Defensive duties: who closes down, who tracks whom, who holds which space (2026-10-18)
Why: the user wanted tactical insight that would hold for real matches: each player's tendency to close the ball down, whether he
follows a man or holds a space, and when that changes. The engine already decides this (`movement.defend_assign`: one optimal
matching of the defence to jobs: press, cover, mark a given attacker, lane, post, open space, or none = hold his place), but the jobs
were never logged.
- Engine: `_record` logs `job_prev` per frame (`jobs` in the match, codes in `jobs_note`). Logging only: the five demo matches were
  re-simulated from their recipes and are identical (events equal, frames equal), so the engine stays engine-v4 in behaviour.
- Reader (`MatchView.duties`, tracking only, as with real data), at 1 Hz while the opponents have the ball in play: closing down =
  within 8 m of the man on the ball (or the man it is travelling to) and running at him at 2 m/s or more, or on top of him, and one
  of the two nearest; tracking = paired one-to-one (Hungarian) with an attacker within 8 m, goal-side, and ahead of the ball (a man
  behind it offers only a ball back); otherwise he holds his space (his mean position then, in words: depth and channel). By 15
  minutes; a change (more / less / higher / deeper closing down, a different man, a different space) counts when the next two
  windows both show it (one window is about four minutes of defending: one alone is noise; first try had 30-45 "changes" a team).
- Scored against the job log (`score_duties`, five matches): frame agreement 0.63-0.69 (zone recall about 0.85, mark precision
  0.6-0.68); the man read as tracked is the one the engine gave him 64-81 % of the time; press share per player and window r = 0.37-0.59.
  Single frames cannot do better: a zonal defender standing near an attacker looks like a marker at that moment.
- Seen: the press slider barely changes how much of the time a side presses, even in the truth (0.040 at press 0.25, 0.057 at 0.80);
  it changes where. Pressing height read 54.9 m (true 58.8) for Redmere (0.8) against 42.0 (44.0) for Ashmere (0.25), so the reader
  reports height (`press_height_m`) as well as share. The engine's press job also covers a jockeying or designated man 20 m away, which
  is why press recall is only about 0.3: not tuned further (it would be fitting the reader to an internal label, not to football).
- Tried, not kept: closing down only on the ball at someone's feet (fewer frames); nearest-defender-is-the-presser (recall 0.35,
  precision 0.25-0.29).
- Shown: agent tool `defensive_duties` (tactics analyst and scout); Board "Duties" (each man where he holds his space, ring = how
  much he closes down, dashed line to the man he tracks, table, confirmed changes, the score); Scout "Without the ball" by window.
  Live test on Foundry: "who presses and who tracks whom for Oakhaven, and after the red card?" called defensive_duties twice,
  approved first time. The answer quoted raw shares ("press share 0.031") instead of percentages.

### Before going public: a daily allowance, CI, and two wording slips (2026-10-18)
- Daily allowance (server/app.py `spend`): new questions 300, live runs 30, translations 3000, spoken lines 1500 a day (UTC; env
  ASKS_PER_DAY etc.); past it a 429 that says cached answers and the replay still work. In memory on the one replica: a restart or
  scale to zero resets it, so an Azure budget alert is the backstop (not set up: the user's call).
- CI: `.github/workflows/tests.yml` runs the four test scripts; test_tools and test_newsroom fall back to the committed
  `server/data/match_7.json.gz` when `out/` is missing (it is git-ignored). MIT LICENSE added.
- PPDA slip ("a lower ppda of 23.0" for the side with the higher one, approved by the LLM checker): deterministic `agents.wrong_ppda`,
  in live commentary and the desk: low/lower next to the higher of the two match values (or high/higher next to the lower) fails
  the draft; a 15-minute figure that is neither is left alone.
- "press share 0.108": a prompt rule (shares as percentages) was ignored, because the writer copies the findings. The duty tool
  now hands the agents whole percentages and keys that say what they are (`closes_down_pct`, `closes_down_height_m`,
  `holds_his_space`); before, the answer gave the space a man holds as where he presses. Rerun: "11% of opponents' possession ...
  about 47.7 metres from their own goal", approved first time.

### Control vs chaos, a metric to follow, milestones (2026-10-18)
The brief names all three ("control vs chaos ... game rhythm patterns", "a single performance metric a viewer cares about",
"notable milestones").
- `MatchView.rhythm` (5-minute windows): possessions from tracking (a run of in-play time with one side on the ball); settled = 4+
  completed passes or 15+ s, broken = over inside 8 s with at most one pass. Per window: shares of in-play time, who had the settled
  ball, turnovers and passes a minute of play, stoppages, and a state: stop-start (ball in play under 40 % of the window), chaos
  (under 45 % settled, or 20 % broken with 5 turnovers a minute), control by a side (60 % settled, 65 % of it theirs), else even.
  Football check: Harlow-Stonebay (both direct, long balls) has the most chaos (6 windows, 16 % broken, a chaos spell 10'-30');
  Redmere (keeps the ball) holds 60 % of the settled time against Ashmere's deep block. Shown on the fan Live strip and the
  Momentum board (chips per window, spells); agent tool `control_and_rhythm`.
- Metric focus: "Your metric" in the header (chances, pressing and winning it back, running, hard passes, duels won, control vs
  chaos). `build_site.stats_timeline` stores the top three players at the end of every minute per metric (running from tracking:
  `MatchView.distance_by_minute`, equal to the scout's distance at full time); a card leads the Live and Feed tabs; the metric goes
  to the desk in the profile (the writer leads with it). Not tested live with the model.
- `MatchView.milestones`: brace, hat-trick, a goal off the bench, a goal and an assist, 50 / 75 / 100 passes completed, 5 tackles won,
  5 saves, a clean sheet at the final whistle: 5-10 a match. On the pitch as a tag for 6 s, in the feed as notes (no model), in the
  followed player's notes, and in match_summary for the agents.

### Fatigue, recovery runs and loose balls (engine, 2026-10-18; tag engine-v5)
Saw: players barely tired. Stamina at full time 0.92 (0.78-0.97), subs off at 0.82-0.94, so the 15 % top-speed cut was worth 1-2 %.
`scripts/fatigue_profile.py` (16 matches, rates a minute so stoppage time does not inflate a window): high-intensity running
(> 19.8 km/h) in the last 15 minutes -1 % on the first 15 (real -35 to -45 %, Mohr, Krustrup & Bangsbo 2003), second half +3 % on the
first (real about -10 to -20 %), last-15 goals 14 % of all (real 20-22 %). A tracking-only fatigue reader found almost nothing
(r 0.14-0.28 with the true stamina): there was no fatigue to find.
- Fixed (logic): match fatigue is glycogen gone from the fast fibres, spent per metre run above critical speed (`fat_k`) plus a
  small base, instead of a cube of speed; and it lowers both halves of the critical-speed model: the reserve above CS shrinks
  with stamina (`dmax * stam`) and CS dips (`fat_cs` 10 %), as prolonged exercise does. Magnitude set once against the research
  (`fat_k` 1.1e-3, the last value tried after 4e-4 and 5.5e-4): last 15 -32 %, second half -16 %, after the most intense 5 minutes
  the next 5 at 0.98 of the average (real about 0.88), last-15 goals 20 %, stamina at the end 0.46 (min 0.18).
- Sprinting (`scripts/sprint_causes.py`): 110 s a player above 7 m/s (real about 30 s, 0.25 km), 83 s of it on all-out cues.
  Attributing it by switching one cue off at a time first gave "loose balls: 110 -> 21 s": wrong. Those matches stalled (the
  defensive-recovery rule takes "sprinting" to mean "engaged with the ball", so a chaser who stopped sprinting was pulled 3 m goal-side
  of the ball and nobody fetched it; 385 events a match). With a separate `chasing` flag the real attribution: recovery runs
  83 -> 32 s when off; every other cue 1-7 s.
- Fixed (logic): a recovering man a few metres out of position makes a hard run (`hard`, at least 6.5 m/s, 23 km/h), flat out only
  when beaten by 10 m or more (`rec_far`); a loose ball is a race: flat out only when it is contested (he and the nearest opponent
  within 0.5 s of it) and 8 m or more away (a sprint needs room to build); the second man and a man who has lost the race run as the
  gap needs. Sprinting 110 -> 90 s a player; most of the rest is need-based runs over long gaps (the 3.4 s time budget), structural.
- Real-data loss (96 unseen matches, seed0=6000): 128.9 before, 127.1 with fatigue (5.5e-4), 125.7 + hard-run recovery, 122.4 + the
  loose-ball race, 124.2 with fatigue at the research strength. Distance 10.96 km (10.8), passes 550 (484, from 577), shots 15.4
  (12.9), goals 1.11 (1.24), big chances 0.36 (0.86, from 0.50: the open gap widens a little).
- Tried, not kept: support spots by pitch control (Spearman: 0.7 s reaction, 5 m/s, 0.45 s logistic) instead of distance to the
  nearest defender: no change, because openness was never the gap (open share of options matches real: half-space 0.29 vs 0.30;
  the sim has fewer team-mates near the passer: options within 12 m 0.19-0.29 vs 0.54-0.62 in the camera view). Speed-dependent
  acceleration (force-velocity profile, tau 0.95-1.33 s): loss 127 -> 139-147 and no sprint gain once top speed was set right;
  removed.
- Fatigue reader (`MatchView.fatigue`, tracking only): high-intensity metres a minute by 15 minutes against his first 30 and against
  his team-mates' drop. On the five rebuilt demo matches, pooled over 54 full-match players: r = 0.50 with true fatigue; those flagged
  tiring are at 0.71 true fatigue against 0.49.

### Big chances: the cut-back man at the byline (engine, 2026-10-18; tag engine-v5.1)
Measured first (`big_chance_source.py` 48): the whole big-chance gap is cut-backs: 0.02 vs 0.25 a team-match (cross 0.27 | 0.35,
through ball 0.03 | 0.02, other pass 0.09 | 0.10, no pass 0.08 | 0.08); shots after a cut-back 0.2 vs 0.8, after a cross 5.1 vs 3.2.
Checked DEVLOG before building (user): a defender reaction delay is already on the dead-end list (Oct 8, "no effect"): dropped;
re-valuing passes into the box by the receiver's xG would override the pass policy, which reproduces real choices on real frames
("it is where the men stand"): dropped; filling the penalty-spot place all the time (of_mask 14, Oct 9 and 12) made shots, not
chances. Built the narrower thing those entries point at:
- With our man on the ball at the byline (last 12 m, outside the six-yard box) the man for the penalty-spot place holds the
  cut-back point (11.5 m out, 1.5 m far side of the middle) instead of charging; if the cross goes in instead he arrives late at
  the spot (`byline_on`, `cb_man`).
- `cutback_outcome.py` 48, off | on | real: reached a team-mate 0.41 | 0.44 | 0.44, a shot follows 0.23 | 0.31 | 0.31, first time
  0.76 | 0.61 | 0.59, its xG 0.091 | 0.105 | 0.144.
- 96 unseen matches: loss 124.2 -> 122.7 (holding only) -> 121.6 (holding, then the spot on a cross); goals 1.11 -> 1.28 (1.24),
  big chances 0.36 -> 0.54 (0.86), shots 15.4 -> 15.7 (12.9).
- Not solved: big chances from cut-backs are still about 0.01 (cut-backs 1.2 a team-match; the shot after one is weaker than real).
  The 48-match source probe (one pairing, default tactics) put all big chances at 0.32 against 0.50 before: 48 matches swing about
  0.1 at this rate, so the 96 unseen matches decide.

### Formation reader: the shape with the ball is the attacking shape (2026-10-18)
`formation_eval.py 24` on engine-v5.1: starting shapes 40/48, changes found 34/59, false alarms 37. `scripts/formation_misses.py`
and a pairwise probe: the change is in the data (after a true change the new system fits better than the old in 0.81 of 5-minute
windows, before it the old one fits better 0.78), but the best label overall is often a neighbour, and the reader's current label
had often drifted already: 9 false alarms were 4-3-3 <-> 4-1-4-1 flips with nothing happening, so a later real 4-3-3 -> 4-5-1 looked
like no change (4-5-1 and 4-1-4-1 are one layout).
- Fixed (logic): "with the ball" pooled every possession frame (goal kicks, build-up in our own half, counters), where wingers drop
  to receive. The attacking shape is now read from possession in the opposition half (`MatchView.read_attack`). Tuning matches
  (9300+): found 34 -> 31, false alarms 37 -> 16. Unseen matches (9500+, nothing chosen on them): found 27 -> 30 of 50, false alarms
  59 -> 24. The fit margin stays at 2 (1.7: 34 / 29; 1.5: 35 / 43; 1.3: 34 / 67 on the tuning matches).
- Tried, not kept: accumulated evidence (CUSUM on log fit ratios, k 0.2-0.4, h 2-4): found 18-25, false alarms 24-57; positions
  relative to the team centre per frame (Shaw & Glickman): identical (min-max normalisation already removes a translation), and
  also scaled by the team's spread per frame: worse (26 found, 43 false alarms, starts 37/48); scoring the shape without the ball:
  22 found, 58 false alarms (the with-ball reading is the better one to score).
- Still open: 3-4-3 read as 5-4-1 when wing-backs and wingers sit deep; starting shapes 40/48 (the engine's 4-3-3 played as 4-1-4-1).

### The eval covers the new features; three small fixes (2026-10-19)
- `eval_newsroom.py` asks five more kinds, graded against the engine's own records where there is one: duties (which opponent the
  player with the clearest marking job in the job log tracked most), fatigue (the named player is among the team's three most tired
  by the engine's stamina), passing (most hard passes completed, from the events), milestones (every brace, hat-trick and goal off
  the bench named; asked only when the match has one), control (a consistency check against the rhythm analytics, which have no
  engine truth); and the bench assistant at 60' for the side not winning, scored on whether its write-up recommends exactly what the
  code picked from the replays (`BENCH` grader).
- "High line" for a deep block: the shape tool gave a bare `defensive_line_m`; it now adds `defensive_line_is` (deep under 29 m,
  high over 36 m, from the ten demo sides without the ball: 25-40 m, median 31.5; the line reading follows the line_height setting:
  0.25 -> 24.9 m, 0.72 -> 39.8 m).
- Late formation changes: the two 84-85' misses were not too late. After them the sides had over a minute of attacking possession.
  In match_1 the switch to 4-5-1 to protect a lead left the shape with the ball a clear 4-3-3 (fit 0.034, its best of the match):
  the change is in how they defend. Scored over either phase (false alarms still the with-ball reading's, `formation_eval.py
  either=1`): 48 unseen teams found 30 -> 35 of 49, false alarms 24. The formation tool already gives the agents both lists. `truth()`
  also drops a change after which the side had under a minute of possession in the opposition half (1 of 50 on the unseen set).
- Windows (rhythm, duties, fatigue, the duty score) restart at the second-half kick-off (`MatchView._windows`): no window mixes
  first-half stoppage time with the second half; labels end at the clock just before the window closes ("30'-45+4'", "46'-61'").
  The board and the scout views pick windows by their end time, not by index.
- First run of the wider eval (54 answers): overall 0.74; duties, goals, best, pressing, milestones, control all right; passing 2/5,
  fatigue 4/10, bench 4/5. Causes and fixes: no tool ranked players across the match (the agent could not find the leader without 22
  player reports): `stat_leaders` (top three for hard passes, key passes, tackles won, interceptions, take-ons won, shots); the fatigue
  tool led with `tiring` (drop beyond team-mates as well), which the agents named, while the reader's top by drop was in the true top
  three for 7 of 10 teams: it now leads with `most_dropped_first`; the bench miss was the LLM grader taking a tested change for a
  second recommendation: graded deterministically now (`recommends`). Rerun: overall 0.87, passing 5/5, fatigue 6/10, bench 5/5.

### Proactive alerts and an MCP server (2026-10-19)
Why: the judging criteria name "MCP integration" (Agentic Design) and "Azure MCP" among the hero technologies; the brief asks for
real-time intelligence that explains why a moment matters.
- `newsroom/alerts.py`: the newsroom flags shifts nobody asked about. Triggers are deterministic and found from the match cut at each
  check (every 2 minutes of match time, the readers re-run on the data so far): a side's players closing down more / less / higher /
  deeper or tracking someone else (one alert per side and check, up to three changes; "holds a different space" is too noisy and stays
  on the board), the most-dropped player read as tiring when a 15-minute window completes from the hour on, a spell of chaos or of one
  side's control starting (two 5-minute windows), and 4 of a side's last 5 final-third entries down one flank. 10 minutes between
  alerts of a kind for a side. First run raised 23 on one match (8 "tiring" at 51', read from a 5-minute half-window); throttled to
  6-8 a match. Each alert is a fact pack like a moment's (score and stats as they stood) plus `alert` (its numbers and a plain sentence,
  the fallback line), so it goes through the same desk; tried on Foundry: duty, spell, tiring and flank alerts, fan and analyst, all
  approved (one rewrite). Live run streams them with the moments; the site build writes them into the stored feeds; the feed and the
  lower third label them "Spotted · ...".
- `server/mcp_server.py` (MCP Python SDK, FastMCP; stdio, or streamable HTTP with --http on :8787/mcp): 17 tools. The 14 analysis
  tools are the desk's own functions (`Desk.tool_fns`, no second copy), each with `match` and an optional `minute` (as of then) added;
  plus list_matches, what_if (the paired replays) and ask_newsroom (the whole multi-agent desk on Foundry). `.vscode/mcp.json` makes
  it a server for GitHub Copilot agent mode. Checked with the SDK's own client: tools listed with argument descriptions, fatigue at
  70', duties for one player, and ask_newsroom ("who tracked whom for Oakhaven after half-time?" at 75': the tactics analyst called
  defensive_duties twice, approved). `tests/test_mcp.py` in CI (offline).

### A 3D view (2026-10-20)
Looked for something to reuse first: no open three.js viewer for football tracking data (the nearest are a Unity viewer for SkillCorner
data and a three.js viewer for a browser game with no repo); CC0 animated humanoids exist as glTF (Quaternius Universal Animation
Library, KayKit) but need a download from itch.io and have no kick; the three.js sample Soldier / Xbot models come from Mixamo with
no clear licence, so not used. Built from primitives instead (`web/view3d.js`, three.js 0.160.0 from jsDelivr through an import map):
the pitch drawn to a canvas texture with the real markings and mowing stripes, goals with wire nets, stepped stands, players as simple
figures in team colours (keepers in their own colour) turned to the logged facing, legs and arms swinging with their speed, a number
tag over each, the ball at the height the engine logged (`ball_z.bin`, new in the site build) with a blob shadow. Cameras: broadcast,
tactical, behind the goal, follow the player (the scout's player or the followed one), free orbit. The 2D pitch stays for the board,
the breakdowns and the shapes (the 3D view hands back to it for those). About 0.8 ms of CPU a frame.
- Animated players (user's go-ahead to download): Quaternius Universal Animation Library [Standard] (itch.io, free, CC0 1.0; the
  zip's License.txt says so). It is one mannequin (8.5k vertices, 65 bones) and 43 clips, 7.6 MB; `scripts/trim_glb.py` keeps the
  mesh, the skin and five clips (Idle, Walk, Jog, Sprint, Jump) in `web/assets/player.glb`, 1.46 MB, with the licence note beside it.
  Each player is a SkeletonUtils clone with its own AnimationMixer; idle / walk / jog / sprint are blended by his real speed and each
  played at the rate that matches its stride (1.5 / 3.8 / 7.2 m/s), so the feet do not slide; the main material takes the shirt
  colour, the joints the shorts. Sized from the skeleton (feet to head bone), not the mesh box: the box is the unskinned geometry in
  the armature's units and made them crossbar-high. Forward is +z (the toe bone ahead of the foot). No kick clip in the pack: passes
  and shots show in the ball's flight. About 4 ms of CPU a frame with 22 rigs; the primitive figures stay as the fallback.
- A kick posed on the skeleton (the pack has none): for a pass, cross, shot or clearance with the foot, the passer's right thigh and
  calf swing through keys over 0.55 s on top of the running clip (backswing, contact at the event's moment, follow-through). The
  rotation directions were set by measuring the foot against the hip along his facing (the first try kicked backwards): backswing
  foot 0.43 m behind and curled up to hip height, contact 0.41 m ahead and low, follow-through 0.73 m ahead and rising.

### Footedness (engine, 2026-10-20; tag engine-v5.2)
Measured first (`scripts/foot_norms.py`, StatsBomb Premier League 2015/16, 133 players with 150+ open-play foot passes): 26 % left-footed;
the weaker foot plays a median 12 % of passes (quartiles 8-17 %; 5 % of players two-footed at 35 %+) and 24 % of open-play shots;
weak-foot passes complete as often as strong-foot ones (0.814 / 0.818, and in every length band) and weak-foot shots convert about as
well per xG (1.08 / 0.92, 178 shots): players choose when to use it.
- Built (`teams._foot`, `Match._foot`): every player has a preferred foot and a weak-foot rating 1-20 (Football Manager's scale),
  drawn from a generator seeded by the player so no other attribute changes; most N(9, 2.5), 5 % two-footed (16-20). Each foot kick
  (pass, cross, clearance, shot; penalties and free kicks off the strong foot) picks a foot on its own random stream: base 0.025 +
  0.3 x (rating/20)^2; x 1.8 when hurried (first time or a man on him); with time, less for a hard ball (x (1 - difficulty)), which
  he moves onto his strong foot; shots x 1.5 (switching feet costs a touch the defender closes). The weak foot costs pass error
  (x 1 + 0.25 (1 - rating/20)), and for shots finishing (3 x (1 - rating/20)) and pace. Events carry `foot`.
- `scripts/foot_sim.py` 48: weak-foot share of passes median 0.14 (0.12), quartiles 0.10-0.17 (0.08-0.17), two-footed 3 % (5 %), of
  shots 0.23 (0.24); completion strong 0.786, weak 0.764 (with every cost off 0.772: most of the gap is that hurried balls are both
  weak-footed and harder). Weak-foot shot conversion per xG 0.40-0.65 against strong 0.82-0.91 across runs: about 30 goals, noise;
  first costs (error 0.9, finishing 6) were halved towards the real like-for-like result.
- Real-data loss on 96 unseen matches 121.6 -> 122.7, big chances 0.54 -> 0.37; but partial versions (no pass cost / no shot cost)
  gave 125.3 / 124.5 and 0.46 / 0.46: the spread of these figures between near-identical engines is larger than the change itself, so
  the effect is within the run-to-run noise. Kept: it is a real mechanism matched to real usage.
- 3D view: the kick uses the event's foot (left or right leg); the scout's player card shows the preferred foot and weak-foot rating;
  player_report gives them to the agents.

### Take-ons: one judgement to pass, a take-on priced at its own odds (engine, 2026-10-20; tag engine-v5.3)
Saw: take-ons 3.5 per team-match against 17.5 real (10.5 on Oct 12, "the chosen dribble is too rare"), success 0.51 (0.57).
Measured (`scripts/takeon_where.py`, StatsBomb PL 2015/16 against 12 sim matches, plus the decision audit):
- Every real take-on is under pressure; the sim chose a carry line through a man about 3 times a match, though the best carry
  line was a take-on 20 times. Real per zone: final third 4.5 central + 4.5 wide, middle 3.4 + 3.2, own third 1.0 + 0.9.
- The decision priced a take-on at p_beat x (0.4 + 0.6 facing) although p_beat already holds the facing term, so it counted
  facing twice, and it counted a lost duel that ends in a foul for us as a lost ball.
- Bug: every pass option got its own misjudgement draw against one draw for the single best carry line. With about ten passes the
  best noisy pass wins by luck (the expected max of ten draws is +0.7 to +1.0 at the noise of a pressed player), and the pass
  played is then re-drawn by the learned policy anyway, so the lucky draw was not even the ball he plays.
- Not the cause: the full-back's permission to engage (`BACK_PRESS`; a carrier level with or past him now may be engaged,
  `level_press`, no measurable effect, kept as the right rule), the carry's value at the byline (below).
Fixed: a take-on line is priced at p_beat (plus the foul won when it is lost) times surviving the others (`to_price`); whether to
pass is one judgement, one noise draw for all pass options (`one_pass_nz`); a carry's chance term counts the best ball he could
put on a team-mate in the box from where it ends, as well as his own shot (`xg_set`). Then the shot willingness was recalibrated
(it had been fitted with the bug in): one coordinate round on 32 matches over ten shot and carry weights, `shoot_ref` 0.168 -> 0.303,
`shoot_slope` 2.05 -> 2.95, `shoot_hold` 0.98 -> 0.5, `patience` 0.2 -> 0.38, `drive` 0.1 -> 0 (the others unchanged).
Effect, 96 unseen matches: loss 122.7 -> 118.8 (take-on pricing) -> 121.1 (one pass judgement: shots 15.3 -> 18.1, long shots from
central carries) -> 115.0 after the recalibration. Take-ons 3.5 -> 19.4 (17.5), success 0.47 (0.57), passes 487 (484), shots 14.6
(12.9), goals 1.28 (1.24), big chances 0.48 (0.86); tackles 27 -> 34.7 (20.5) and dispossessed 18.8 (13.8) rose with the duels.
Still open: the zones. Too many central take-ons (final third 8.7 vs 4.5, own third 3.0 vs 1.0), too few wide (final third 2.0 vs 4.5):
wide, the carrier has a man within 4 m in 34 % of decisions against 81 % centrally, so a line through a man rarely exists there.

### Cut-backs re-measured (2026-10-20)
`cutback_outcome.py` 48 (now prints where the ball is played to and the shot's distance, blockers and nearest defender), sim | real:
1.39 per team-match | 124 in 51 Euro 2020 matches (about 1.2): volume is not the gap. Reached 0.38 | 0.44, a shot follows 0.26 |
0.31, played to 11.8 | 10.8 m from goal, shot from 10.0 | 11.2 m, defenders in the way at the shot 1.47 | 1.38, nearest 1.9 | 2.4 m,
xG 0.117 | 0.144, big chance per cut-back 0.01 | 0.06 (real: 7 of 124). At the moment of the pass the receiving spot is as free as
real; by the shot the defenders are closer than real. The reaction delay is on the dead-end list (Oct 8), so not retried.

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
