"""Fictional clubs, squads, formations and tactics.

Everything here is invented: no real clubs, players, badges or datasets
(hackathon rule 4.7: synthetic, football-realistic data only).

Coordinates: metres, pitch L x W. Anchors are stored in the *team frame*:
own goal at x=0, attacking towards x=L, for both teams.
"""
from dataclasses import dataclass, asdict
import numpy as np

L, W = 105.0, 68.0
ATTRS = ["pace", "accel", "stamina", "passing", "vision", "dribbling", "finishing",
         "tackling", "positioning", "strength", "composure", "work_rate", "aerial", "gk"]
ROLES = ["GK", "CB", "FB", "DM", "CM", "WG", "AM", "ST"]
ROLE_IDX = {r: i for i, r in enumerate(ROLES)}

# (label, role, anchor_x, anchor_y) as fractions of the pitch in the team frame
_BACK4 = [("LB", "FB", .22, .10), ("LCB", "CB", .20, .37), ("RCB", "CB", .20, .63), ("RB", "FB", .22, .90)]
FORMATIONS = {
    "4-4-2": [("GK", "GK", .03, .5)] + _BACK4 + [
        ("LM", "WG", .45, .10), ("LCM", "CM", .42, .38), ("RCM", "CM", .42, .62), ("RM", "WG", .45, .90),
        ("LST", "ST", .68, .40), ("RST", "ST", .68, .60)],
    "4-3-3": [("GK", "GK", .03, .5)] + _BACK4 + [
        ("DM", "DM", .37, .50), ("LCM", "CM", .46, .30), ("RCM", "CM", .46, .70),
        ("LW", "WG", .68, .12), ("ST", "ST", .72, .50), ("RW", "WG", .68, .88)],
    "4-2-3-1": [("GK", "GK", .03, .5)] + _BACK4 + [
        ("LDM", "DM", .36, .38), ("RDM", "DM", .36, .62),
        ("LAM", "AM", .58, .15), ("CAM", "AM", .56, .50), ("RAM", "AM", .58, .85), ("ST", "ST", .72, .50)],
    "3-5-2": [("GK", "GK", .03, .5),
              ("LCB", "CB", .20, .25), ("CB", "CB", .18, .50), ("RCB", "CB", .20, .75),
              ("LWB", "FB", .45, .06), ("LCM", "CM", .42, .33), ("CM", "DM", .38, .50),
              ("RCM", "CM", .42, .67), ("RWB", "FB", .45, .94),
              ("LST", "ST", .68, .40), ("RST", "ST", .68, .60)],
}

# attribute offsets on top of the club quality, per role (scale 1-20)
_PROFILE = {
    "GK": dict(gk=5, pace=-4, passing=-2, finishing=-9, tackling=-5, dribbling=-6, accel=-2),
    "CB": dict(tackling=3, strength=3, aerial=3, pace=-1, passing=-1, dribbling=-3, finishing=-5, vision=-1),
    "FB": dict(pace=1, stamina=2, tackling=1, finishing=-3, work_rate=2),
    "DM": dict(tackling=3, passing=1, vision=1, stamina=1, finishing=-3),
    "CM": dict(passing=2, vision=2, stamina=2, finishing=-1),
    "WG": dict(pace=3, dribbling=3, accel=3, tackling=-3, aerial=-3),
    "AM": dict(passing=3, vision=3, dribbling=2, finishing=1, tackling=-4, aerial=-3),
    "ST": dict(finishing=4, pace=1, strength=1, aerial=2, tackling=-6, passing=-1, vision=-1),
}

_ON = ["Ar", "Be", "Ca", "Da", "El", "Fa", "Gi", "Ha", "Io", "Ju", "Ka", "Lu", "Ma", "No", "Or", "Pa",
       "Ri", "Sa", "To", "Va", "Wi", "Ze", "Bro", "Dre", "Kel", "Mor", "Tan", "Vel"]
_MID = ["", "", "r", "l", "n", "st", "m", "d", "v"]
_END = ["an", "ek", "io", "ar", "us", "en", "in", "or", "as", "ik", "ov", "on", "el", "ay"]
_SUR = ["Hart", "Voss", "Lund", "Ward", "Brandt", "Okoro", "Silva", "Marsh", "Keller", "Duran",
        "Ferro", "Novak", "Reyes", "Tamm", "Wolfe", "Ibarra", "Kovac", "Mercer", "Sato", "Quill"]
_SUR_END = ["", "son", "ton", "ley", "er", "ini", "ez", "ova"]

LEAGUE = {  # code: (name, quality 1-20)
    "NBR": ("Northbridge Rovers", 13.5), "HAR": ("Harlow Athletic", 12.0),
    "KES": ("Kestrel United", 14.5), "ASH": ("Ashmere Town", 10.5),
    "RED": ("Redmere City", 15.0), "OAK": ("Oakhaven FC", 11.5),
    "STB": ("Stonebay Albion", 12.5), "WMW": ("Westmoor Wanderers", 11.0),
}


@dataclass
class Tactics:
    line_height: float = .5   # defensive line: 0 deep .. 1 high
    compactness: float = .5   # 0 stretched .. 1 tight
    width: float = .5         # 0 narrow .. 1 wide in possession
    press: float = .5         # 0 passive .. 1 aggressive pressing
    directness: float = .5    # 0 patient build-up .. 1 direct / progressive
    marking: float = .4       # 0 zonal .. 1 man-oriented
    risk: float = .5          # willingness to push players forward / try difficult passes
    shoot: float = .5         # shot-happiness


def _name(rng):
    first = rng.choice(_ON) + rng.choice(_MID) + rng.choice(_END)
    last = rng.choice(_SUR) + rng.choice(_SUR_END)
    return f"{first} {last}"


def make_team(code, formation="4-3-3", tactics=None, quality=None, rng=None):
    rng = rng or np.random.default_rng()
    name, q = LEAGUE[code]
    q = quality if quality is not None else q
    players = []
    for i, (label, role, ax, ay) in enumerate(FORMATIONS[formation]):
        prof = _PROFILE[role]
        attrs = {a: float(np.clip(q + prof.get(a, 0) + rng.normal(0, 1.4), 1, 20)) for a in ATTRS}
        if role != "GK":
            attrs["gk"] = 1.0
        players.append(dict(id=f"{code}{i + 1:02d}", name=_name(rng), number=i + 1, label=label, role=role,
                            anchor=(ax, ay), attrs=attrs))
    return dict(code=code, name=name, formation=formation, quality=q,
                tactics=tactics or Tactics(), players=players)


def team_meta(t):
    return dict(code=t["code"], name=t["name"], formation=t["formation"], quality=t["quality"],
                tactics=asdict(t["tactics"]),
                players=[{k: p[k] for k in ("id", "name", "number", "label", "role")} for p in t["players"]])
