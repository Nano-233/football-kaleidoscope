"""Turn a simulated match into a provider-style tracking feed: 25 Hz positions, speed and heading per player, ball height.

    python scripts/export_tracking.py out/match_1.json [hz]      -> out/match_1_tracking.npz

The engine records at 5 Hz; positions are interpolated linearly (ball height parabolically between samples is
already in the frames). Arrays: t (T,), players (T, 22, 2) metres, speed (T, 22) m/s, heading (T, 22) degrees
(direction of travel, or towards the ball when almost stationary), ball (T, 3) = x, y, z, carrier (T,), possession (T,).
"""
import sys, json, pathlib
import numpy as np

src = pathlib.Path(sys.argv[1])
hz = float(sys.argv[2]) if len(sys.argv) > 2 else 25.
d = json.loads(src.read_text())
fr = np.array(d["frames"])
t0 = fr[:, 0]
t = np.arange(t0[0], t0[-1], 1 / hz)
lerp = lambda col: np.interp(t, t0, col)
players = np.stack([np.stack([lerp(fr[:, 5 + 2 * k]), lerp(fr[:, 6 + 2 * k])], -1) for k in range(22)], 1)
ball = np.stack([lerp(fr[:, 1]), lerp(fr[:, 2]), lerp(fr[:, 49]) if fr.shape[1] > 49 else np.zeros_like(t)], -1)
vel = np.gradient(players, 1 / hz, axis=0)
speed = np.hypot(vel[..., 0], vel[..., 1])
to_ball = ball[:, None, :2] - players
heading = np.degrees(np.where(speed > .8, np.arctan2(vel[..., 1], vel[..., 0]), np.arctan2(to_ball[..., 1], to_ball[..., 0])))
idx = np.minimum(np.searchsorted(t0, t, side="right") - 1, len(t0) - 1)
out = src.with_name(src.stem + "_tracking.npz")
np.savez_compressed(out, t=t.astype(np.float32), players=players.astype(np.float32), speed=speed.astype(np.float32),
                    heading=heading.astype(np.float32), ball=ball.astype(np.float32), carrier=fr[idx, 3].astype(np.int8), possession=fr[idx, 4].astype(np.int8))
print(f"{out}  {len(t)} frames at {hz:g} Hz, {out.stat().st_size / 1e6:.1f} MB")
