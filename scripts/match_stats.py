"""Print team and player statistics for a simulated match (from events only).

    python scripts/match_stats.py out/match_5.json
"""
import sys, json, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from analytics.stats import match_stats

m = json.loads(pathlib.Path(sys.argv[1]).read_text())
s = match_stats(m)
home, away = m["meta"]["home"]["code"], m["meta"]["away"]["code"]
keys = ["goals", "xg", "shots", "shots_on_target", "big_chances", "passes", "pass_accuracy", "progressive_passes", "key_passes", "crosses", "take_ons", "take_on_rate",
        "dribbled_past", "tackles", "tackle_win_rate", "interceptions", "clearances", "blocks", "recoveries", "aerial_won", "aerial_win_rate", "pressures", "ppda",
        "fouls", "yellow_cards", "offsides", "corners", "saves"]
print(f"{'':20s}{home:>10s}{away:>10s}")
for k in keys:
    a, b = (s["teams"][t].get(k) for t in (0, 1))
    print(f"{k:20s}{str(a):>10s}{str(b):>10s}")
print("\ntop players by rating")
for pid, p in sorted(s["players"].items(), key=lambda kv: -(kv[1]["rating"] or 0))[:8]:
    print(f"  {p['rating']:>4}  {p['name']:24s} {['H','A'][p['team']]} {p['role']:3s} {p['minutes']:5.1f}' goals {p.get('goals',0)} xG {p.get('xg',0)} passes {p.get('passes_completed',0)}/{p.get('passes',0)} "
          f"take-ons {p.get('take_ons_won',0)}/{p.get('take_ons',0)} beaten {p.get('dribbled_past',0)} tackles {p.get('tackles_won',0)}/{p.get('tackles',0)}")
