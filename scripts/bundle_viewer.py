"""Bundle a match and the viewer into ONE self-contained HTML file (shareable, works offline, no server).

    python scripts/bundle_viewer.py out/match_12.json            -> out/match_12_viewer.html

Frames are stored as int16 (0.05 m resolution) in base64 and heavy event fields (freeze frames, rationale) are dropped.
"""
import sys, json, base64, pathlib
import numpy as np

src = pathlib.Path(sys.argv[1])
d = json.loads(src.read_text())
fr = np.array(d["frames"])
q = np.concatenate([fr[:, 1:3] * 20, fr[:, 3:5], fr[:, 5:49] * 20], axis=1).round().astype(np.int16)  # ball xy, carrier, poss, 22 x (x, y)
slim = [{k: v for k, v in e.items() if k not in ("freeze_frame", "why", "players", "bench")} for e in d["events"]]
meta = {k: v for k, v in d["meta"].items() if k != "frame_cols"}
blob = base64.b64encode(q.tobytes()).decode()
embed = f"""<script>
window.__EMBED__ = () => {{
  const meta = {json.dumps(meta, separators=(',', ':'))}, events = {json.dumps(slim, separators=(',', ':'))}, n = {len(q)};
  const raw = Uint8Array.from(atob("{blob}"), c => c.charCodeAt(0)), q = new Int16Array(raw.buffer), F = new Float32Array(n * 49), frames = [];
  for (let i = 0; i < n; i++) {{
    const o = i * 49, k = i * 48;
    F[o] = i * meta.dt; F[o + 1] = q[k] / 20; F[o + 2] = q[k + 1] / 20; F[o + 3] = q[k + 2]; F[o + 4] = q[k + 3];
    for (let j = 0; j < 44; j++) F[o + 5 + j] = q[k + 4 + j] / 20;
    frames.push(F.subarray(o, o + 49));
  }}
  return {{ meta, events, frames }};
}};
</script>
"""
html = (pathlib.Path(__file__).resolve().parents[1] / "viewer" / "index.html").read_text(encoding="utf-8")
out = src.with_name(src.stem + "_viewer.html")
out.write_text(html.replace("<script>\nconst $ =", embed + "<script>\nconst $ =", 1), encoding="utf-8")
print(out, f"{out.stat().st_size / 1e6:.1f} MB")
