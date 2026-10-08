"""Who the player on the ball passes to: a choice model learned from real football, not a hand-built utility.

Real passers are not expected-value maximisers; they are fairly consistent. So the choice among team-mates is a conditional logit
(random-utility discrete choice) on football features of each option, fitted by maximum likelihood to StatsBomb 360 freeze frames
(Euro 2020), the same "average policy learned from historical data" idea as the expected possession value framework of Fernandez,
Bornn and Cervone (2021). `scripts/fit_pass_policy.py` fits it and writes the coefficients to reference/pass_policy.json.

An option is a team-mate and a mode (on the ground, in the air, or a ball into his path: a through ball). Everything is in the attacking-left-to-right frame, metres.
The features read the pitch only (where he is, how free he is, whether the lane is open, what the ball is worth where he stands),
so the same function scores real positions when fitting and simulated positions when playing.
"""
import json
import pathlib
import numpy as np
from .movement import XT, nrm

NAMES = ["fwd", "back", "lat", "dist", "dist2", "free", "lane", "d_xt", "ahead", "gk", "p_fwd", "p_back", "p_lat",
         "air", "air_dist", "air_blocked", "air_free", "p_air", "p_free", "p_lane", "thr", "thr_fwd", "thr_free", "thr_ahead", "off"]
NF = len(NAMES)
L_HALF = 52.5
_FILE = pathlib.Path(__file__).resolve().parents[1] / "reference" / "pass_policy.json"


def features(C, M, O, nd_p, gk):
    """Features of every option for a passer at C.

    C (2,) passer, M (k,2) team-mates, O (m,2) opponents (keeper included), nd_p distance from the passer to his nearest opponent,
    gk (k,) True for the team's own keeper. Returns (3, k, NF): row 0 the ball on the ground, row 1 in the air, row 2 a through ball.
    """
    rel = M - C
    dx, dy = rel[:, 0], rel[:, 1]
    d = np.maximum(nrm(rel), 1e-6)
    seg = np.maximum((rel ** 2).sum(1), 1e-6)
    s = np.clip(((O[None] - C) * rel[:, None]).sum(-1) / seg[:, None], 0., 1.)  # (k, m) where on the pass line each opponent is closest
    perp = nrm(O[None] - (C + s[..., None] * rel[:, None]), axis=-1).min(1)  # open lane: how near the closest opponent comes to the line
    free = nrm(O[None] - M[:, None], axis=-1).min(1)  # room around the receiver
    d_xt = XT(M) - float(XT(C))
    ahead = ((O[None, :, 0] > M[:, None, 0]) & (np.abs(O[None, :, 1] - M[:, None, 1]) < 15.)).sum(1)  # opponents still between him and goal
    press = float(np.clip(1. - nd_p / 5., 0., 1.))
    line = np.sort(O[:, 0])[-2] if len(O) > 1 else L_HALF  # the second-last opponent (the keeper counts): beyond it and ahead of the ball is offside
    off = ((M[:, 0] > np.maximum(line, C[0])) & (M[:, 0] > L_HALF)).astype(float)
    fwd, back, lat, dist = np.maximum(dx, 0.) / 10., np.maximum(-dx, 0.) / 10., np.abs(dy) / 10., d / 10.
    f_free, f_lane = np.minimum(free, 10.) / 5., np.minimum(perp, 5.) / 2.5
    out = np.zeros((3, len(M), NF))
    for mode in (0, 1, 2):
        h, th = float(mode == 1), float(mode == 2)
        out[mode] = np.stack([fwd, back, lat, dist, dist ** 2, f_free, f_lane * (1 - h), d_xt * 30., np.minimum(ahead, 6) / 3., gk.astype(float),
                              press * fwd, press * back, press * lat, np.full(len(M), h), h * dist, h * (1. - np.minimum(perp, 5.) / 5.),
                              h * f_free, np.full(len(M), press * h), press * f_free, press * f_lane * (1 - h),
                              np.full(len(M), th), th * fwd, th * f_free, th * np.minimum(ahead, 6) / 3., off], 1)
    return out


_BETA = None


def beta():
    global _BETA
    if _BETA is None:
        _BETA = np.array(json.loads(_FILE.read_text())["beta"]) if _FILE.exists() else np.zeros(NF)
    return _BETA


def scores(C, M, O, nd_p, gk):
    """(3, k) utility of each option under the learned policy."""
    return features(C, M, O, nd_p, gk) @ beta()
