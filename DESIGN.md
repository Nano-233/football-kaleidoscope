# Design

## Goal

The hackathon asks for one pipeline: ingest synthetic football events, interpret them, explain why moments matter,
render the result next to the match, and personalize it. The engine here is the *ingest* source: it produces
football-realistic synthetic data in two streams, like a real data provider would.

| Stream | Content |
|---|---|
| `events` | ground-truth events with coordinates and context: pass, cross, clearance, carry, take_on, tackle, interception, pressure, possession_change, shot, goal, save, foul, card, free_kick, penalty, corner, throw_in, goal_kick, kickoff, half_end, full_time |
| `frames` | tracking: `[t, ball_x, ball_y, carrier, last_possessing_team, x0, y0, ..., x21, y21]` every 0.2 s (5 Hz). Player 0-10 home, 11-21 away. |

Pitch is 105 x 68 m. Home attacks +x for the whole match. `meta` carries squads, formations, tactics, score and column names.

Every decision event also carries a `why` payload (pressure, nearest opponent, the options considered with their
utilities, success probabilities and xG, and the one chosen). That is *explanation ground truth*: downstream agents
can cite real causes, and a fact-checker agent can verify claims against it.

## Engine

**On-ball (event-driven).** When the carrier decides, he evaluates every visible option from the actual geometry:
- passes: lane interception risk (can an opponent reach the lane before the ball?), receiver pressure, execution
  accuracy (passing, pressure, length), and aerial duels for lofted balls; value gained uses a distance-to-goal surface;
- shot: xG from distance, angle and blockers in the shot cone, adjusted for finishing/composure/goalkeeper;
- carry: including an explicit take-on duel when a defender stands in the way;
- clearance under pressure in the own third.
Options are chosen by softmax over utility (temperature depends on composure). All coefficients live in `engine.TUNE`.

**Off-ball (kinematic, 5 Hz).** `movement.py` gives each player a target: formation anchor, block shifts with the
ball, tactic (line height, width, compactness), phase (role push or drop), offside line, man/zone marking
(Hungarian assignment of defenders to opponents), pressing and jockeying with occasional commitment to the tackle,
shot-blocking cover near the own goal, support triangles, forward runs, loose-ball chasing, and interception
movement. `engine._steer` moves players with pace, acceleration, stamina limits and reaction lag.

**Dynamic resolution.** Passes and loose balls are resolved by whoever physically reaches the ball first rolling for
control, so completion rates emerge from player positions instead of being dice rolls detached from the geometry.

## Realism evidence

`scripts/calibrate.py` simulates N matches and compares aggregates with rough real-football norms (goals, shots,
xG, passes, pass accuracy, fouls, set pieces, distance covered, top speed, team shape, centroid-ball correlation).
`scripts/tactics_effect.py` varies one tactic and checks the behaviour moves the right way (e.g. press 0.1 to 0.9:
pressures +44%, opponent passes -27%, possession +10 points; line height raises the centre-back line by ~6 m).

## Known simplifications

- No side swap at half time, no substitutions, injuries or weather, no real offside calls (attackers are held onside).
- Aerial play and goalkeeping are statistical, not simulated in 3D.
- Corners and set-piece routines are simple.
- Some aggregates sit at the edge of their bands (corners a little low, pass accuracy low end).
