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


# Traits: small, football-readable behaviour modifiers on top of attributes.
# keys - mult: shot, runs, follow, prog, loss, cruise, commit, foul, tackle | add: range (m of shot range), carry,
#        takeon (p_beat) | baked into attributes: vision, aerial, finishing | flag: sweep
TRAITS = {
    "long_shots":     ("Shoots from distance",                  dict(shot=1.5, range=7)),
    "poacher":        ("Lives on the last defender",            dict(shot=1.25, runs=1.6, follow=.7)),
    "clinical":       ("Rarely wastes a chance",                dict(finishing=2.5)),
    "playmaker":      ("Looks for the incisive pass",           dict(prog=3.0, vision=2.5, loss=.8)),
    "safe_passer":    ("Keeps it simple, retains possession",   dict(loss=1.6, prog=.6)),
    "dribbler":       ("Takes defenders on",                    dict(carry=.15, takeon=.07)),
    "runner":         ("Runs in behind, stretches the defence", dict(runs=2.2, cruise=1.05)),
    "box_to_box":     ("Covers every blade of grass",           dict(cruise=1.15, follow=1.35)),
    "hard_tackler":   ("Wins the ball by force",                dict(commit=1.6, tackle=1.3, foul=1.5)),
    "target_man":     ("Holds the ball up, wins headers",       dict(aerial=4, follow=.65, cruise=.9, runs=.6)),
    "sweeper_keeper": ("Plays high, sweeps behind the line",    dict(sweep=1)),
    "low_workrate":   ("Conserves energy",                      dict(cruise=.88, follow=.8)),
}
_ROLE_TRAITS = {
    "ST": ["poacher", "target_man", "runner", "clinical", "long_shots", "dribbler"],
    "WG": ["dribbler", "runner", "long_shots", "playmaker", "clinical", "low_workrate"],
    "AM": ["playmaker", "long_shots", "dribbler", "runner", "clinical"],
    "CM": ["box_to_box", "playmaker", "hard_tackler", "long_shots", "safe_passer", "low_workrate"],
    "DM": ["hard_tackler", "safe_passer", "playmaker", "box_to_box"],
    "FB": ["runner", "box_to_box", "safe_passer", "hard_tackler", "dribbler"],
    "CB": ["hard_tackler", "safe_passer", "target_man", "playmaker"],
    "GK": ["sweeper_keeper"],
}
_ROLE_KEY = {"GK": ["gk", "composure", "passing"], "CB": ["tackling", "aerial", "strength", "positioning"],
             "FB": ["pace", "stamina", "tackling", "passing"], "DM": ["tackling", "passing", "positioning", "stamina"],
             "CM": ["passing", "vision", "stamina", "tackling"], "WG": ["pace", "dribbling", "accel", "passing"],
             "AM": ["passing", "vision", "dribbling", "finishing"], "ST": ["finishing", "pace", "aerial", "composure"]}
_BENCH = ["GK", "CB", "FB", "DM", "CM", "WG", "ST"]  # 7 on the bench; 5 substitutions allowed


def overall(p):
    return round(float(np.mean([p["attrs"][a] for a in _ROLE_KEY[p["role"]]])), 1)


def _player(code, num, label, role, anchor, q, rng, star=False):
    prof = _PROFILE[role]
    attrs = {a: float(np.clip(q + prof.get(a, 0) + rng.normal(0, 1.4), 1, 20)) for a in ATTRS}
    if role != "GK":
        attrs["gk"] = 1.0
    if star:
        for a in _ROLE_KEY[role]:
            attrs[a] = min(20., attrs[a] + 2.5)
    pool = _ROLE_TRAITS[role]
    n = 1 if role == "GK" and rng.random() < .5 else (0 if role == "GK" else int(rng.choice([0, 1, 1, 2]) if not star else rng.choice([1, 2])))
    traits = [str(t) for t in rng.choice(pool, size=min(n, len(pool)), replace=False)] if n else []
    p = dict(id=f"{code}{num:02d}", name=_name(rng), number=num, label=label, role=role, anchor=anchor,
             attrs=attrs, traits=traits, star=star)
    p["overall"] = overall(p)
    return p


def make_team(code, formation="4-3-3", tactics=None, quality=None, rng=None):
    rng = rng or np.random.default_rng()
    name, q = LEAGUE[code]
    q = quality if quality is not None else q
    q = 12.8 + (q - 12.8) * .8  # compress the spread: real leagues are more balanced than raw attribute gaps suggest
    slots = FORMATIONS[formation]
    # stars: the best forward, one creative midfielder/winger and one defender or the keeper
    pick = lambda roles: [i for i, s_ in enumerate(slots) if s_[1] in roles]
    stars = {int(rng.choice(pick(["ST", "AM", "WG"]))), int(rng.choice(pick(["CM", "AM", "DM", "WG"]))), int(rng.choice(pick(["CB", "GK", "FB"])))}
    players = [_player(code, i + 1, lab, role, (ax, ay), q, rng, star=i in stars) for i, (lab, role, ax, ay) in enumerate(slots)]
    bench = [_player(code, 12 + k, r, r, None, q - 1.0, rng) for k, r in enumerate(_BENCH)]
    return dict(code=code, name=name, formation=formation, quality=q, tactics=tactics or Tactics(), players=players, bench=bench)


def team_meta(t):
    keep = ("id", "name", "number", "label", "role", "traits", "star", "overall")
    return dict(code=t["code"], name=t["name"], formation=t["formation"], quality=t["quality"],
                tactics=asdict(t["tactics"]),
                players=[{k: p[k] for k in keep} for p in t["players"]],
                bench=[{k: p[k] for k in keep} for p in t["bench"]])


_CAPACITY = {"NBR": 38000, "HAR": 29000, "KES": 52000, "ASH": 21000, "RED": 60000, "OAK": 26000, "STB": 32000, "WMW": 24000}


def make_match_info(rng, home):
    """Fictional match context: referee, venue, weather, kick-off. Referee strictness and weather feed the engine."""
    code = home["code"]
    cap = _CAPACITY.get(code, 30000)
    return dict(
        referee=dict(name=_name(rng), strict=round(float(rng.uniform(.2, .9)), 2)),
        venue=dict(name=f"{home['name'].split()[0]} Park", capacity=cap, attendance=int(cap * rng.uniform(.82, 1.0))),
        weather=dict(temp_c=round(float(rng.normal(13, 6)), 1), rain=bool(rng.random() < .25), wind_kph=round(float(rng.uniform(0, 35)), 0)),
        kickoff=str(rng.choice(["12:30", "15:00", "17:30", "20:00"])))
