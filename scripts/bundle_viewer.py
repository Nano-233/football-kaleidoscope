"""Bundle a match and the viewer into ONE self-contained HTML file (shareable, works offline, no server).

    python scripts/bundle_viewer.py out/match_12.json
        -> out/match_12_viewer.html    full page
        -> out/match_12_artifact.html  same page as a fragment (no doctype/head/body tags) for hosts that add their own

Frames are stored as int16 (0.05 m resolution) in base64 and heavy event fields (freeze frames, rationale) are dropped.
"""
import sys, re, json, base64, pathlib
import numpy as np

src = pathlib.Path(sys.argv[1])
d = json.loads(src.read_text())
fr = np.array(d["frames"])
q = np.concatenate([fr[:, 1:3] * 20, fr[:, 3:5], fr[:, 5:49] * 20], axis=1).round().astype(np.int16)  # ball xy, carrier, poss, 22 x (x, y)
hd = (fr[:, 50:72] * 40).round().clip(-127, 127).astype(np.int8) if fr.shape[1] >= 72 else np.zeros((len(fr), 22), np.int8)  # facing, 1/40 rad
slim = [{k: v for k, v in e.items() if k not in ("freeze_frame", "why", "players", "bench")} for e in d["events"]]
meta = {k: v for k, v in d["meta"].items() if k != "frame_cols"}
blob = base64.b64encode(q.tobytes()).decode()
blob_h = base64.b64encode(hd.tobytes()).decode()
embed = f"""<script>
window.__EMBED__ = () => {{
  const meta = {json.dumps(meta, separators=(',', ':'))}, events = {json.dumps(slim, separators=(',', ':'))}, n = {len(q)};
  const bin = atob("{blob}"), raw = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) raw[i] = bin.charCodeAt(i);
  const q = new Int16Array(raw.buffer), F = new Float32Array(n * 72), frames = [];
  const binh = atob("{blob_h}"), hh = new Int8Array(binh.length);
  for (let i = 0; i < binh.length; i++) hh[i] = binh.charCodeAt(i) << 24 >> 24;
  for (let i = 0; i < n; i++) {{
    const o = i * 72, k = i * 48;
    F[o] = i * meta.dt; F[o + 1] = q[k] / 20; F[o + 2] = q[k + 1] / 20; F[o + 3] = q[k + 2]; F[o + 4] = q[k + 3];
    for (let j = 0; j < 44; j++) F[o + 5 + j] = q[k + 4 + j] / 20;
    for (let j = 0; j < 22; j++) F[o + 50 + j] = hh[i * 22 + j] / 40;
    frames.push(F.subarray(o, o + 72));
  }}
  return {{ meta, events, frames }};
}};
</script>
"""
html = (pathlib.Path(__file__).resolve().parents[1] / "viewer" / "index.html").read_text(encoding="utf-8")
marker = "<script>\nconst $ ="
assert marker in html
full = html.replace(marker, embed + marker, 1)
out = src.with_name(src.stem + "_viewer.html")
out.write_text(full, encoding="utf-8")
m = re.search(r"<head>(.*?)</head>\s*<body>(.*)</body>", full, flags=re.S | re.I)
frag = re.sub(r"<meta[^>]*>", "", m.group(1)) + m.group(2)
out2 = src.with_name(src.stem + "_artifact.html")
out2.write_text(frag, encoding="utf-8")
print(out, f"{out.stat().st_size / 1e6:.1f} MB", "|", out2.name, f"{out2.stat().st_size / 1e6:.1f} MB")
