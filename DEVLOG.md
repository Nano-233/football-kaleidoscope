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
