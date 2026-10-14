"""The broadcast camera for simulated frames: StatsBomb 360 freeze frames only hold the players inside the camera's view (the visible
area), so counting simulated players over the whole pitch against them over-counts the sim (at 12 m from the passer the view covers
81 % of the circle, at 20 m 57 %, at 30 m 35 %). This lends the sim a real camera: for a moment with the ball at a spot, the visible
area of a real 360 frame whose ball was near the same spot (within 4 m, nearest otherwise), shifted by the difference, and a simulated
player counts only inside it. Both sides are then counted through the same kind of window.

    from scripts.camera import Camera
    cam = Camera()                      # loads the real visible areas once (attacking-left-to-right frame, metres)
    poly = cam.view(ball_xy, rng)       # a visible area for a moment with the ball at ball_xy
    cam.sees(poly, point)               # True if the point is inside it
"""
import glob, json, pathlib
import numpy as np

ROOT = pathlib.Path(__file__).resolve().parents[1]
L, W = 105., 68.


class Camera:
    def __init__(self):
        self.ball, self.polys = [], []
        for f in glob.glob(str(ROOT.parent / "reference_data" / "euro2020" / "[0-9]*_360.json")):
            ev = {e["id"]: e for e in json.load(open(f.replace("_360", ""), encoding="utf-8"))}
            for fr in json.load(open(f, encoding="utf-8")):
                e = ev.get(fr["event_uuid"])
                if not e or "location" not in e:
                    continue
                va = fr["visible_area"]
                self.ball.append(np.array(e["location"]) * [L / 120, W / 80])
                self.polys.append(np.array([(va[i] * L / 120, va[i + 1] * W / 80) for i in range(0, len(va) - 1, 2)]))
        self.ball = np.array(self.ball)

    def view(self, ball, rng):
        d = np.hypot(*(self.ball - ball).T)
        near = np.nonzero(d < 4.)[0]
        k = int(rng.choice(near)) if len(near) else int(d.argmin())
        return self.polys[k] + (ball - self.ball[k])

    @staticmethod
    def sees(poly, pt):
        x, y = pt
        c = False
        for (x1, y1), (x2, y2) in zip(poly, np.roll(poly, -1, axis=0)):
            if (y1 > y) != (y2 > y) and x < (x2 - x1) * (y - y1) / (y2 - y1 + 1e-12) + x1:
                c = not c
        return c


if __name__ == "__main__":  # self-check: a real frame's own players are inside its own view
    cam = Camera()
    f = sorted(glob.glob(str(ROOT.parent / "reference_data" / "euro2020" / "[0-9]*_360.json")))[0]
    fr = json.load(open(f, encoding="utf-8"))[100]
    va = fr["visible_area"]
    poly = np.array([(va[i] * L / 120, va[i + 1] * W / 80) for i in range(0, len(va) - 1, 2)])
    ins = [cam.sees(poly, np.array(p["location"]) * [L / 120, W / 80]) for p in fr["freeze_frame"]]
    assert np.mean(ins) > .9, np.mean(ins)
    print(f"{len(cam.polys)} real views loaded; a frame's players inside its own view: {np.mean(ins):.2f}")
