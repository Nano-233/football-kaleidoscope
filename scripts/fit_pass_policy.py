"""Fit the pass-choice policy (matchsim/policy.py) to StatsBomb 360 freeze frames.

    python scripts/fit_pass_policy.py            # fit on all matches, report held-out fit, write reference/pass_policy.json

For every open-play pass with a freeze frame, the options are the visible team-mates in three modes (ground, air, through ball). The chosen option is the
team-mate nearest the pass end point, in the mode the pass used. A conditional logit is fitted by maximum likelihood (L-BFGS, small ridge).
Matches are split by id parity: even for fitting, odd held out, to check the policy generalises. Only the coefficients are written.
Data: StatsBomb Open Data (Euro 2020 with 360 frames), calibration use only.
"""
import sys, json, glob, pathlib
import numpy as np
from scipy.optimize import minimize
ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from matchsim import policy

SB = np.array([105 / 120, 68 / 80])
DATA = ROOT.parent / "reference_data" / "euro2020"
SET_PIECES = ("Corner", "Free Kick", "Throw-in", "Goal Kick", "Kick Off")


def load(files):
    """-> list of (features (A, NF), chosen index, meta) per pass."""
    rows = []
    for f in files:
        ev = json.load(open(f, encoding="utf-8"))
        fr = {x["event_uuid"]: x for x in json.load(open(f.replace(".json", "_360.json"), encoding="utf-8"))}
        for e in ev:
            if e["type"]["name"] != "Pass" or e["id"] not in fr:
                continue
            p = e["pass"]
            if p.get("type", {}).get("name", "") in SET_PIECES or "end_location" not in p:
                continue
            ff = fr[e["id"]]["freeze_frame"]
            mates = [(np.array(q["location"]) * SB, bool(q.get("keeper"))) for q in ff if q["teammate"] and not q["actor"]]
            opps = [np.array(q["location"]) * SB for q in ff if not q["teammate"]]
            if len(mates) < 2 or len(opps) < 3:
                continue
            C = np.array(e["location"][:2]) * SB
            M = np.array([m for m, _ in mates]); gk = np.array([k for _, k in mates])
            end = np.array(p["end_location"]) * SB
            h = 1 if p.get("height", {}).get("name") == "High Pass" else 0
            j = int(np.argmin(np.hypot(*(M - end).T)))
            if np.hypot(*(M[j] - end)) > (14. if h else 8.):  # the ball did not go to anyone in view (an air ball lands further from the man it was meant for)
                continue
            mode = 2 if p.get("through_ball") else h
            O = np.array(opps)
            nd_p = float(np.hypot(*(O - C).T).min())
            X = policy.features(C, M, O, nd_p, gk)  # (2, k, NF)
            rows.append((X.reshape(-1, policy.NF), mode * len(M) + j, dict(x=C[0], nd=nd_p, ok="outcome" not in p, f=f, k=len(M), j=j, h=h, dx=M[j][0] - C[0])))
    return rows


def pack(rows):
    A = max(len(r[0]) for r in rows)
    X = np.zeros((len(rows), A, policy.NF)); mask = np.zeros((len(rows), A), bool); y = np.zeros(len(rows), int)
    for i, (x, c, _) in enumerate(rows):
        X[i, :len(x)] = x; mask[i, :len(x)] = True; y[i] = c
    return X, mask, y


def nll(beta, X, mask, y, ridge):
    s = np.where(mask, X @ beta, -1e9)
    s -= s.max(1, keepdims=True)
    lse = np.log(np.exp(s).sum(1))
    ll = s[np.arange(len(y)), y] - lse
    P = np.exp(s - lse[:, None])
    g = -(X[np.arange(len(y)), y] - (P[..., None] * X).sum(1)).sum(0) + ridge * beta
    return -ll.sum() + .5 * ridge * (beta ** 2).sum(), g


PL = ROOT.parent / "reference_data" / "pl1516"


def pl_shares():
    """Share of Premier League open-play passes by mode (ground, air, through) and length bucket, the population the simulation must reproduce.
    The 360 sample only holds passes whose receiver is in the camera frame, which drops most long balls and so teaches the choice model that they are rare."""
    cnt = np.zeros((3, len(policy.EDGES) + 1))
    for f in sorted(glob.glob(str(PL / "[0-9]*.json"))):
        ev = json.load(open(f, encoding="utf-8"))
        if not isinstance(ev, list) or not ev or "type" not in ev[0]:
            continue
        for e in ev:
            if e["type"]["name"] != "Pass":
                continue
            p = e["pass"]
            if p.get("type", {}).get("name", "") in SET_PIECES:
                continue
            mode = 2 if p.get("through_ball") else 1 if p.get("height", {}).get("name") == "High Pass" else 0
            cnt[mode, np.searchsorted(policy.EDGES, p["length"] * 105 / 120)] += 1
    return cnt / cnt.sum()


def main():
    files = [f for f in sorted(glob.glob(str(DATA / "[0-9]*.json"))) if "_360" not in f]
    fit_f = [f for f in files if int(pathlib.Path(f).stem) % 2 == 0]; test_f = [f for f in files if int(pathlib.Path(f).stem) % 2 == 1]
    tr, te = load(fit_f), load(test_f)
    print(f"{len(tr)} passes to fit, {len(te)} held out")
    X, m, y = pack(tr); Xt, mt, yt = pack(te)
    res = minimize(nll, np.zeros(policy.NF), args=(X, m, y, 1.), jac=True, method="L-BFGS-B")
    b = res.x
    null = -np.log(m.sum(1)).sum() / len(y); null_t = -np.log(mt.sum(1)).sum() / len(yt)
    print("log-lik per pass: fit %.3f (uniform %.3f)  held-out %.3f (uniform %.3f)" % (-nll(b, X, m, y, 0)[0] / len(y), null, -nll(b, Xt, mt, yt, 0)[0] / len(yt), null_t))
    for n, v in zip(policy.NAMES, b):
        print(f"  {n:12s} {v:+.3f}")
    # held-out check: does sampling from the policy reproduce what real passers do?
    s = np.where(mt, Xt @ b, -1e9); P = np.exp(s - s.max(1, keepdims=True)); P /= P.sum(1, keepdims=True)
    print("held-out: top-1 %.3f" % np.mean(P.argmax(1) == yt))
    dx = np.array([r[2]["dx"] for r in te]); k = np.array([r[2]["k"] for r in te])
    jx = np.arange(Xt.shape[1])[None] % np.maximum(k[:, None], 1)
    # forward share predicted vs actual (receiver more than 5 m ahead), by pressure
    ahead = np.zeros_like(P, bool)
    for i, r in enumerate(te):
        kk = r[2]["k"]; xs = r[0][:kk, 0] * 10  # fwd feature of the ground rows = max(dx,0)/10
        ahead[i, :kk] = xs > 5; ahead[i, kk:2 * kk] = xs > 5; ahead[i, 2 * kk:3 * kk] = xs > 5
    nd = np.array([r[2]["nd"] for r in te]); xo = np.array([r[2]["x"] for r in te])
    for name, sel in (("all", np.ones(len(te), bool)), ("own third", xo < 35), ("unpressed own third", (xo < 35) & (nd > 5)), ("pressed", nd < 2.5)):
        pred = (P * ahead).sum(1)[sel].mean(); act = (dx[sel] > 5).mean()
        print(f"  forward share ({name}): policy {pred:.3f}  real {act:.3f}  n={sel.sum()}")
    air_pred = (P[:, :]).copy()
    airmask = np.zeros_like(P, bool)
    for i, r in enumerate(te):
        kk = r[2]["k"]; airmask[i, kk:2 * kk] = True
    print("  share in the air: policy %.3f  real %.3f" % ((P * airmask).sum(1).mean(), np.mean([r[2]["h"] for r in te])))
    # prior-shift correction: the fitted choice reproduces the camera-visible sample; scale each (mode, length) class to its Premier League share
    D = np.minimum(X[..., 3] * 10, 1e3)  # pass length of every option
    K = np.maximum(m.sum(1) // 3, 1)
    mode_of = (np.arange(X.shape[1])[None] // K[:, None])
    bucket = np.searchsorted(policy.EDGES, D)
    s_ = np.where(m, X @ b, -1e9); P_ = np.exp(s_ - s_.max(1, keepdims=True)); P_ /= P_.sum(1, keepdims=True)
    model = np.zeros((3, len(policy.EDGES) + 1))
    for md in range(3):
        for bk in range(len(policy.EDGES) + 1):
            model[md, bk] = (P_ * ((mode_of == md) & (bucket == bk) & m)).sum() / len(y)
    target = pl_shares()
    off = np.clip(np.log(np.maximum(target, 1e-4) / np.maximum(model, 1e-4)), -3., 4.)
    print("share by mode (rows ground, air, through) and length bucket", list(policy.EDGES))
    print("  sample (model)"); print(model.round(3))
    print("  Premier League"); print(target.round(3))
    print("  offsets"); print(off.round(2))
    (ROOT / "reference" / "pass_policy.json").write_text(json.dumps({"names": policy.NAMES, "beta": [round(float(v), 4) for v in b], "offsets": off.round(3).tolist(), "edges": list(policy.EDGES),
        "source": "StatsBomb Open Data, Euro 2020 360 freeze frames (even-id matches), conditional logit", "n_passes": len(tr)}, indent=1))


if __name__ == "__main__":
    main()
