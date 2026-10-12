"""The match centre's backend: serves the site (web/) and the newsroom desk.

    python -m server.app                 # http://localhost:8080 ; FOUNDRY_PROJECT_ENDPOINT / FOUNDRY_MODEL as for the newsroom
    POST /api/ask  {"match": "match_7", "question": "...", "profile": {...}, "t": 3600}
         -> the answer, the agents' steps, the moments it refers to; t (seconds) answers from what had happened by then

Answers are cached on disk (server/cache), so a question asked once costs nothing again; at most ASK_SLOTS questions run at a time.
"""
import asyncio
import gzip
import hashlib
import json
import os
import pathlib

from aiohttp import web

from newsroom.clients import make_client
from newsroom.desk import Desk
from newsroom.localize import translate

ROOT = pathlib.Path(__file__).resolve().parents[1]
DATA, CACHE, WEB = ROOT / "server" / "data", ROOT / "server" / "cache", ROOT / "web"
ASK_SLOTS = int(os.environ.get("ASK_SLOTS", "2"))  # ponytail: one process-wide limit; per-user quotas if it is ever opened wider
PROFILE_KEYS = ("persona", "depth", "club", "player", "language")
_matches, _gate, _client = {}, asyncio.Semaphore(ASK_SLOTS), None


def match(mid):
    if mid not in _matches:
        path = DATA / f"{pathlib.Path(mid).name}.json.gz"
        if not path.exists():
            raise web.HTTPNotFound(text=f"no match {mid}")
        with gzip.open(path, "rt", encoding="utf-8") as fh:
            _matches[mid] = json.load(fh)
    return _matches[mid]


async def ask(request):
    global _client
    body = await request.json()
    q = str(body.get("question", "")).strip()[:300]
    if not q:
        raise web.HTTPBadRequest(text="empty question")
    prof = {k: str(v)[:60] for k, v in (body.get("profile") or {}).items() if k in PROFILE_KEYS and v}
    m = match(str(body.get("match", "")))
    t = body.get("t")
    t = None if t is None else max(60., min(float(t), m["meta"]["duration"]))
    key = hashlib.sha1(json.dumps([body.get("match"), q.lower(), prof, None if t is None else int(t // 60)], sort_keys=True).encode()).hexdigest()
    hit = CACHE / f"{key}.json"
    if hit.exists():
        return web.json_response(dict(json.loads(hit.read_text(encoding="utf-8")), cached=True))
    async with _gate:
        _client = _client or make_client()
        out = await Desk(_client, m, prof, until=t).ask(q)
    CACHE.mkdir(parents=True, exist_ok=True)
    hit.write_text(json.dumps(out, default=str), encoding="utf-8")
    return web.json_response(dict(out, cached=False), dumps=lambda o: json.dumps(o, default=str))


async def tr(request):
    """POST {"language": "Español", "texts": [...]} -> {"texts": [...]}; each line cached on its own, so a line is translated once per language."""
    global _client
    body = await request.json()
    lang = str(body.get("language", ""))[:30]
    texts = [str(t)[:1500] for t in (body.get("texts") or [])][:30]
    if not lang or lang == "English" or not texts:
        return web.json_response(dict(texts=texts))
    keys = [hashlib.sha1(json.dumps([lang, t]).encode()).hexdigest() for t in texts]
    got = {k: json.loads((CACHE / f"tr-{k}.json").read_text(encoding="utf-8")) for k in keys if (CACHE / f"tr-{k}.json").exists()}
    todo = [i for i, k in enumerate(keys) if k not in got]
    if todo:
        async with _gate:
            _client = _client or make_client()
            new = await translate(_client, lang, [texts[i] for i in todo])
        CACHE.mkdir(parents=True, exist_ok=True)
        for i, t in zip(todo, new):
            got[keys[i]] = t
            (CACHE / f"tr-{keys[i]}.json").write_text(json.dumps(t, ensure_ascii=False), encoding="utf-8")
    return web.json_response(dict(texts=[got[k] for k in keys]))


async def index(request):
    return web.FileResponse(WEB / "index.html")


def app():
    a = web.Application(client_max_size=64 * 1024)
    a.router.add_post("/api/ask", ask)
    a.router.add_post("/api/translate", tr)
    a.router.add_get("/", index)
    a.router.add_static("/", WEB)
    return a


if __name__ == "__main__":
    web.run_app(app(), port=int(os.environ.get("PORT", "8080")))
