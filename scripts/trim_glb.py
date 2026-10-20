"""Keep only some animations of a .glb (and everything the meshes and skins need), dropping the rest of the binary: the CC0 Quaternius
Universal Animation Library has 43 clips; the 3D view uses four.

    python scripts/trim_glb.py IN.glb OUT.glb Idle_Loop Walk_Loop Jog_Fwd_Loop Sprint_Loop
"""
import json
import struct
import sys


def trim(src, dst, keep):
    b = open(src, "rb").read()
    jlen = struct.unpack("<I", b[12:16])[0]
    j = json.loads(b[20:20 + jlen])
    off = 20 + jlen
    blen = struct.unpack("<I", b[off:off + 4])[0]
    bin_ = b[off + 8:off + 8 + blen]
    j["animations"] = [a for a in j.get("animations", []) if a["name"] in keep]
    used = set()  # accessors still referenced
    for m in j["meshes"]:
        for p in m["primitives"]:
            used.update(p["attributes"].values())
            if "indices" in p:
                used.add(p["indices"])
            for t in p.get("targets", []):
                used.update(t.values())
    for s in j.get("skins", []):
        if "inverseBindMatrices" in s:
            used.add(s["inverseBindMatrices"])
    for a in j["animations"]:
        for s in a["samplers"]:
            used.update((s["input"], s["output"]))
    acc_map = {old: new for new, old in enumerate(sorted(used))}
    accessors = [j["accessors"][i] for i in sorted(used)]
    views_used = sorted({a["bufferView"] for a in accessors if "bufferView" in a})
    view_map, views, out = {}, [], bytearray()
    for new, old in enumerate(views_used):
        v = dict(j["bufferViews"][old])
        while len(out) % 4:
            out.append(0)
        start = v.get("byteOffset", 0)
        chunk = bin_[start:start + v["byteLength"]]
        v["byteOffset"], v["buffer"] = len(out), 0
        out += chunk
        views.append(v)
        view_map[old] = new
    for a in accessors:
        if "bufferView" in a:
            a["bufferView"] = view_map[a["bufferView"]]
    for m in j["meshes"]:
        for p in m["primitives"]:
            p["attributes"] = {k: acc_map[v] for k, v in p["attributes"].items()}
            if "indices" in p:
                p["indices"] = acc_map[p["indices"]]
            if "targets" in p:
                p["targets"] = [{k: acc_map[v] for k, v in t.items()} for t in p["targets"]]
    for s in j.get("skins", []):
        if "inverseBindMatrices" in s:
            s["inverseBindMatrices"] = acc_map[s["inverseBindMatrices"]]
    for a in j["animations"]:
        for s in a["samplers"]:
            s["input"], s["output"] = acc_map[s["input"]], acc_map[s["output"]]
    j["accessors"], j["bufferViews"] = accessors, views
    j["buffers"] = [{"byteLength": len(out)}]
    while len(out) % 4:
        out.append(0)
    js = json.dumps(j, separators=(",", ":")).encode()
    js += b" " * (-len(js) % 4)
    body = struct.pack("<II", len(js), 0x4E4F534A) + js + struct.pack("<II", len(out), 0x004E4942) + bytes(out)
    open(dst, "wb").write(struct.pack("<III", 0x46546C67, 2, 12 + len(body)) + body)
    return len(b), 12 + len(body), [a["name"] for a in j["animations"]]


if __name__ == "__main__":
    print(trim(sys.argv[1], sys.argv[2], set(sys.argv[3:])))
