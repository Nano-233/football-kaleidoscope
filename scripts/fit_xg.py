"""Fit the simulator's xG formula to real open-play shots (StatsBomb Open Data, Premier League 2015/16, shots with freeze frames).

    python scripts/fit_xg.py

Same form as Match._xg: logit = z0 + d*distance + a*angle + h*header, times a blocker factor per defender in the shot triangle, times a closing-down
term. Prints how the CURRENT formula (TUNE xg_*) is calibrated on real shots and the maximum-likelihood refit, with a per-band check.
Result on 1,733 shots: the current formula is too flat (0.06-0.20 band predicted 0.078 / 0.136, actual 0.099 / 0.180; 0.35+ predicted 0.54, actual 0.38).
Refit: z0 -0.20, d -0.138, a 0.779, h -0.946, blocker x0.756, closing weight 0.405 (log-lik -439.7 against -451.4; StatsBomb's own xG -414.3).
The refit is NOT switched on: the shooting gate was balanced against the old scale and has to be re-derived from real data first.
"""
import sys, glob, json
import pathlib; ROOT = pathlib.Path(__file__).resolve().parents[1]; sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / 'scripts'))
import numpy as np, norms
from scipy.optimize import minimize
L,W=105.,68.; GH=3.66
def feat(s):
    C=np.array([s["x"],s["y"]]); dx,dy=L-C[0],C[1]-W/2; d=np.hypot(dx,dy)
    ang=abs(np.arctan2(W/2+GH-C[1],dx)-np.arctan2(W/2-GH-C[1],dx))
    a,b=np.array([L,W/2-GH]),np.array([L,W/2+GH]); cr=lambda p,q,r:(q[0]-p[0])*(r[1]-p[1])-(q[1]-p[1])*(r[0]-p[0])
    defs=[p for p,tm,k in s["ff"] if not tm and not k]; nb=0
    for o in defs:
        if np.hypot(*(o-C))<2.0: nb+=1; continue
        s1,s2,s3=cr(C,a,o),cr(a,b,o),cr(b,C,o)
        if (s1>=0 and s2>=0 and s3>=0) or (s1<=0 and s2<=0 and s3<=0): nb+=1
    near=min(np.hypot(*(o-C)) for o in defs) if defs else 9.
    return d,ang,nb,min(1.,near/3.),float(s["head"])
rows=[]
for f in sorted(glob.glob(r"D:\Projects\Inside-the-game\reference_data\pl1516\*.json")):
    ev=json.load(open(f,encoding="utf-8"))
    if not (isinstance(ev,list) and ev and "type" in ev[0]): continue
    for R in norms.from_statsbomb(ev).values():
        for s in R:
            if s["k"]=="shot" and s["kind"]=="Open Play" and s["ff"]:
                rows.append(feat(s)+(float(s["outcome"]=="Goal"),float(s["xg"])))
A=np.array(rows); print("open-play shots with freeze frames:",len(A),"goals",int(A[:,5].sum()),"goal rate %.3f"%A[:,5].mean())
d,ang,nb,cl,hd,y,sbxg=A.T
def pred(th,d=d,ang=ang,nb=nb,cl=cl,hd=hd):
    z0,cd,ca,ch,lb,kc=th
    z=z0+cd*d+ca*ang+ch*hd
    odds=np.exp(z)*np.exp(lb)**nb*(1-kc+kc*cl)     # lb = log blocker factor, kc = weight of the closing-down term
    return odds/(1+odds)
cur=np.array([-2.08,-.07,1.89,-.2,np.log(.72),.25])
def band(p,name):
    print(name,"mean p %.3f vs goal %.3f"%(p.mean(),y.mean()))
    for lo,hi in ((0,.03),(.03,.06),(.06,.1),(.1,.2),(.2,.35),(.35,1.1)):
        m=(p>=lo)&(p<hi); 
        if m.sum(): print(f"   {lo:.2f}-{hi:.2f} n={m.sum():4d} predicted {p[m].mean():.3f} actual {y[m].mean():.3f}")
band(pred(cur),"current engine xG on real shots:")
nll=lambda th: -(y*np.log(np.clip(pred(th),1e-6,1))+(1-y)*np.log(np.clip(1-pred(th),1e-6,1))).sum()
res=minimize(nll,cur,method="Nelder-Mead",options=dict(maxiter=4000,xatol=1e-4,fatol=1e-4))
print("fit:",np.round(res.x,3),"nll %.1f vs current %.1f vs StatsBomb xG %.1f"%(res.fun,nll(cur),-(y*np.log(np.clip(sbxg,1e-6,1))+(1-y)*np.log(np.clip(1-sbxg,1e-6,1))).sum()))
band(pred(res.x),"refit:")
