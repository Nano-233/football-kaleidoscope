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
from matchsim.whatif import FORMATIONS, TACTIC_KEYS, cached_what_if

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
    stream = request.query.get("stream") == "1"
    if hit.exists():
        out = dict(json.loads(hit.read_text(encoding="utf-8")), cached=True)
        if not stream:
            return web.json_response(out)
    else:
        out = None
    if not stream:
        out = await _answer(m, prof, t, q, hit)
        return web.json_response(out, dumps=lambda o: json.dumps(o, default=str))
    # server-sent events: each step as it happens, then the answer
    resp = web.StreamResponse(headers={"Content-Type": "text/event-stream", "Cache-Control": "no-cache"})
    await resp.prepare(request)
    send = lambda kind, data: resp.write(f"event: {kind}\ndata: {json.dumps(data, default=str)}\n\n".encode())
    if out is None:
        q_ = asyncio.Queue()
        job = asyncio.create_task(_answer(m, prof, t, q, hit, on_step=q_.put_nowait))
        while not job.done() or not q_.empty():
            try:
                await send("step", await asyncio.wait_for(q_.get(), timeout=.5))
            except asyncio.TimeoutError:
                pass
        out = job.result()
    await send("answer", out)
    await resp.write_eof()
    return resp


async def _answer(m, prof, t, q, hit, on_step=None):
    global _client
    async with _gate:
        _client = _client or make_client()
        out = await Desk(_client, m, prof, until=t, on_step=on_step).ask(q)
    CACHE.mkdir(parents=True, exist_ok=True)
    hit.write_text(json.dumps(out, default=str), encoding="utf-8")
    return dict(out, cached=False)


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


_sim_gate = asyncio.Semaphore(1)  # a what-if uses every core for about a minute


async def whatif(request):
    """POST {"match": "match_7", "t": 3600, "team": 1, "change": {"press": .8, "formation": "3-4-3"}} -> as it was / with the change."""
    body = await request.json()
    m = match(str(body.get("match", "")))
    recipe = m["meta"].get("recipe")
    if not recipe:
        raise web.HTTPBadRequest(text="this match cannot be replayed")
    t = max(60., min(float(body.get("t", 60)), m["meta"]["duration"] - 300))
    team = 1 if int(body.get("team", 0)) else 0
    ch = body.get("change") or {}
    change = {k: max(0., min(1., float(ch[k]))) for k in TACTIC_KEYS if k in ch}
    if ch.get("formation") in FORMATIONS:
        change["formation"] = ch["formation"]
    if not change:
        raise web.HTTPBadRequest(text="nothing to change")
    async with _sim_gate:
        out = await asyncio.get_running_loop().run_in_executor(None, cached_what_if, recipe, t, team, change)
    stored = sum(1 for e in m["events"] if e["t"] < out["at"]["t"])
    return web.json_response(dict(out, replay_matches=abs(stored - out["at"]["events"]) <= 2, team_name=m["meta"][("home", "away")[team]]["name"]))


async def index(request):
    return web.FileResponse(WEB / "index.html")


def app():
    a = web.Application(client_max_size=64 * 1024)
    a.router.add_post("/api/ask", ask)
    a.router.add_post("/api/translate", tr)
    a.router.add_post("/api/whatif", whatif)
    a.router.add_get("/", index)
    a.router.add_static("/", WEB)
    return a


if __name__ == "__main__":
    web.run_app(app(), port=int(os.environ.get("PORT", "8080")))
