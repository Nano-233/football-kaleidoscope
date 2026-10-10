# Football conventions the engine should obey

The engine must behave the way football is actually played. This list is the checklist: every convention a coach would
expect, how it is expressed in the engine (always as geometry and cost, never as a scripted spot), and where it stands.
Status: **done** (implemented and checked), **partial**, **missing**. Update the table when something changes, and
record measurements in `DEVLOG.md`.

Principle for implementation: a convention is a *cost or utility computed from the current positions*, so it holds in
every situation (any formation, any ball position) instead of only in the cases someone scripted. Targets for
checking come from real event/freeze-frame data (`scripts/norms.py`, `scripts/norms360.py`).

## Defending, one player

| # | Convention | Intrinsic expression | Status |
|---|---|---|---|
| D1 | Stay goal-side of the attacker and the ball; do not be beaten in behind | marker target = attacker position + lead, offset towards own goal; recovery sprint when behind the target | done |
| D2 | Jockey: approach to 2-3 m, then slow, side-on, back-pedal; do not dive in | presser target on the ball-goal line a fixed gap in front of the carrier; commit only when the touch is heavy | done |
| D3 | Force the attacker to the side where help is (usually outside, away from goal centre) | presser stands on the inside shoulder, bearing set by lateral position and where cover is | done |
| D4 | Press on triggers (back pass, poor touch, receiver facing own goal, ball in the air); otherwise hold | the nearest man engages to a jockeying gap only on a trigger (shooting range, counter-press window, a long hold, back to goal, pinned on a line, running at the defence, a pressing plan); otherwise he stands off 5-6 m on the line to goal and screens | done |
| D5 | Close the receiver down while the ball travels, so you arrive with it | marker of the intended receiver moves during flight to a point goal-side of the arrival spot | done |
| D6 | Tackle when the ball is away from the attacker's body or his back is turned, not when he faces you at speed | duel probability from ball distance, attacker heading and defender's closing speed; a booked man goes in less | partial (contact geometry, no heavy-touch model) |
| D7 | Cover and balance: second defender behind the presser on the inside, third on the far side | slots derived from presser position, ball-goal line and lateral position | done |
| D8 | Mark tight in the box: goal-side and ball-side so you see ball and man; man-orientated inside, zonal outside | marking gate by distance to goal; ball-side bias; the marking gap itself grows with the man's distance from goal (a stride in and around the box, 5-7 m of zonal cover in the middle third and beyond) | done |
| D9 | Track runners, hand them over when they cross into another defender's zone | marking reassignment on zone crossing (Hungarian refresh) with hysteresis; a marker meets a man sprinting at goal where he will be in about a second, not half a second | partial |

## Defending, the team

| # | Convention | Intrinsic expression | Status |
|---|---|---|---|
| D10 | The back line stays a line: same depth, moves together | back-line depth = common value, individual targets pulled to it | done |
| D11 | Line height follows the ball: step up when the ball is pressured or played back, drop when it is free to be played in behind | line depth from ball depth, pressure on the carrier and tactic; a line never steps up into a man driving at it, it drops in front of him and runs at his speed to hold the gap; the midfield compacts onto the line, not the line onto the midfield | done |
| D12 | Offside trap / step-up together | line steps up as a unit on a trigger; attackers' offside check reads the line | partial |
| D13 | Compact: short vertically, narrow horizontally, shift to the ball side, weak-side tuck | block width and depth pulled towards the block centroid; ball-side shift | partial |
| D14 | Cut passing lanes (cover shadow): protect the centre first and show play wide | free defenders stand on the lanes to the most dangerous central receivers | done (effect on shots small) |
| D15 | Midfield screen protects the space between the lines and the half-spaces | DM/CM positions between ball and back line, spaced laterally | partial |
| D16 | Press as a unit: when one presses, the rest compress and take the nearest receivers | support targets derived from presser position | partial |
| D17 | Transition: counter-press for a few seconds, the rest recover goal-side | after turnover, nearest players press, others recover at speed | partial |
| D18 | Rest defence: while attacking keep a screen and 2-3 defenders behind the ball | attacking template keeps minimum cover behind the ball line | partial |
| D19 | Defend crosses: near post, penalty spot, edge of box for cut-backs, clear wide | box slots from ball and goal geometry; a cross or clearance still low off the foot is blocked by a man on its line; a hurried clearance goes the way the body faces | partial |
| D20 | Goalkeeper: on the ball-goal line, narrow the angle one-on-one, sweep behind the line | GK target from ball and goal geometry; comes out to the carrier's shooting point when no defender is there first (time race); smothers the ball at the carrier's feet (gk skill vs dribbling); sweeper trait | done |
| D21 | Drop and get bodies behind the ball when the box is threatened | box_defence slots | done |

## Attacking, the team

| # | Convention | Intrinsic expression | Status |
|---|---|---|---|
| A1 | Width and depth: stretch the defence, hold the touchline on the far side | wide players keep lane; far side stays wide for the switch | done |
| A2 | Always give the carrier two or three passing options: one short, one square, one ahead, at angles, not in a defender's shadow | support candidates scored on open lane, distance band, angle to existing options | partial |
| A3 | Runs in behind, checking runs to feet, third-man runs | run triggers from line position and carrier state | partial |
| A4 | Overload the ball side, keep the far side for a switch | ball-side density and switch utility | missing |
| A5 | Receive between the lines (half-spaces) | seek-space reward for gaps between defensive lines | missing |
| A6 | Full-back overlaps/underlaps to create 2v1 | trigger when winger is on the ball wide and full-back is behind him | done (overlap run) |
| A7 | Striker pins the centre-backs, peels off the blind side, attacks the near post on crosses | seek-space terms and cross runs | partial |
| A8 | Cross shapes: near post, far post, penalty spot, edge for the cut-back | box runs on a wide ball | done |
| A9 | Stay onside: level with the last defender and time runs | offside hover at the last defender's shoulder | done |
| A10 | Patience: recycle when nothing progresses, switch when the near side is congested | utility of free space on the far side | done (relief + onward potential) |
| A11 | Build-up: goalkeeper as outlet, centre-backs split, full-backs high, pivot drops | goal-kick and build-up shapes | partial |

## Attacking, one player

| # | Convention | Intrinsic expression | Status |
|---|---|---|---|
| A12 | First touch away from pressure; receive on the half-turn | control probability from marker distance and side | done (facing, marker limits turn) |
| A13 | Dribble only with space ahead and a pace/skill edge; otherwise pass | take-on probability and carry steering | done (open space ahead, contact duel) |
| A14 | Shoot when in range with a clear sight; square the ball when the angle is poor and a team-mate is better placed | shot propensity from location; best_mate comparison | done |
| A18 | A run at goal is not a chance already used: he shoots when the chance has become real, not when the clock on the ball has run out | the 'time held' penalty on shooting restarts whenever xG becomes clearly better (x1.3, from about .12) | done |
| A19 | A carrier in open space does not treat the keeper as a defender in the way: he shoots or rounds him | keeper excluded from the carry-blocking and crowding terms; the shot terms price him in | done |
| A15 | Through-ball timing: play it when the line steps up | pass utility uses the line as the passer last saw it | done |
| A16 | One-twos to beat a defender | wall-pass option | missing |
| A17 | Manage the game: keep the ball in the corner when ahead late | late-game utilities | missing |

## Order of work

1. Defensive line as a line, step-up/drop on pressure (D10-D12), cover and balance (D7), forcing direction (D3), closing the
   receiver (D5), press triggers (D4), tackle timing (D6).
2. Midfield screen and lane cutting (D14-D15) once the line exists.
3. Attacking width, overlaps, between-the-lines and switch (A1, A4-A6, A10).
4. Re-balance numbers last.

## Body orientation (added after the user pointed out strikers receive back to goal)

Every player has a heading. It follows the run when he runs, points at the incoming ball when he is standing and receives, and opens
part-way towards goal with a good first touch (less if a marker is on him). A standing carrier turns towards goal over time; a
defender on his back slows the turn. The heading is used by: passing view (what he can see), pass accuracy, shot quality and
willingness, take-on and carry success, first-touch control, defenders' tackles and interceptions, and turning cost on sharp changes
of direction for all players. Status: done. Measured effect: carries into the box fell to about real; shot volume unchanged.
