"""Re-cover one kind of moment in the stored site data (web/data/<match>/match.json) without rebuilding the whole site: the packs are
recomputed from server/data/<match>.json.gz, the newsroom covers them again, and those items in the feed are replaced. Use after a change
to how a kind of moment is packed or written.

    python scripts/refresh_moments.py shape_change match_1 match_7 RED-ASH HAR-STB WMW-OAK
    python scripts/refresh_moments.py '!shape_change' match_1 ...     # every kind except one
    python scripts/refresh_moments.py recaps match_1 ...              # the half-time and full-time reports instead
"""
import asyncio
import gzip
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from build_site import newsroom, recaps  # noqa: E402
from newsroom.alerts import alerts  # noqa: E402
from newsroom.moments import moments  # noqa: E402


def refresh_press(mid):
    """Re-cover only the lines that talk about pressing in a moment without pressing facts (the check added on 2026-10-25)."""
    from newsroom.agents import unsupported_pressing
    with gzip.open(ROOT / "server" / "data" / f"{mid}.json.gz", "rt", encoding="utf-8") as fh:
        match = json.load(fh)
    packs = sorted(moments(match) + alerts(match), key=lambda p: p["moment"]["t"])
    path = ROOT / "web" / "data" / mid / "match.json"
    doc = json.loads(path.read_text(encoding="utf-8"))
    key = lambda m: (m["type"], round(m["t"], 1), m.get("kind"))
    by_key = {key(p["moment"]): p for p in packs}
    bad = {key(it["moment"]) for items in doc["newsroom"].values() for it in items
           if key(it["moment"]) in by_key and unsupported_pressing(it["line"], by_key[key(it["moment"])])}
    redo = [by_key[k] for k in sorted(bad, key=lambda k: k[1])]
    new = asyncio.run(newsroom(redo)) if redo else {site: [] for site in doc["newsroom"]}
    for site, items in doc["newsroom"].items():
        doc["newsroom"][site] = sorted([it for it in items if key(it["moment"]) not in bad] + new[site], key=lambda it: it["moment"]["t"])
    path.write_text(json.dumps(doc, separators=(",", ":")), encoding="utf-8")
    print(mid, len(redo), "moments with an unsupported pressing claim re-covered,", sum(it["fallback"] for s_ in new for it in new[s_]), "fell back")


def refresh(kind, mid):
    if kind == "press":
        return refresh_press(mid)
    if kind == "recaps":
        with gzip.open(ROOT / "server" / "data" / f"{mid}.json.gz", "rt", encoding="utf-8") as fh:
            match = json.load(fh)
        path = ROOT / "web" / "data" / mid / "match.json"
        doc = json.loads(path.read_text(encoding="utf-8"))
        doc["recaps"] = asyncio.run(recaps(match))
        path.write_text(json.dumps(doc, separators=(",", ":")), encoding="utf-8")
        print(mid, len(doc["recaps"]), "recaps rewritten,", sum(not r["approved"] for r in doc["recaps"]), "held")
        return
    hit = (lambda k: k != kind[1:]) if kind.startswith("!") else (lambda k: k == kind)
    with gzip.open(ROOT / "server" / "data" / f"{mid}.json.gz", "rt", encoding="utf-8") as fh:
        match = json.load(fh)
    packs = [p for p in sorted(moments(match) + alerts(match), key=lambda p: p["moment"]["t"]) if hit(p["moment"]["type"])]
    path = ROOT / "web" / "data" / mid / "match.json"
    doc = json.loads(path.read_text(encoding="utf-8"))
    new = asyncio.run(newsroom(packs)) if packs else {site: [] for site in doc["newsroom"]}
    for site, items in doc["newsroom"].items():
        doc["newsroom"][site] = sorted([it for it in items if not hit(it["moment"]["type"])] + new[site], key=lambda it: it["moment"]["t"])
    path.write_text(json.dumps(doc, separators=(",", ":")), encoding="utf-8")
    held = sum(it["fallback"] for site in new for it in new[site])
    print(mid, len(packs), kind, "moments re-covered,", held, "fell back to the template")


if __name__ == "__main__":
    for m in sys.argv[2:]:
        refresh(sys.argv[1], m)
