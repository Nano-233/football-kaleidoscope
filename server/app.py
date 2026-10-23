"""The match centre's backend: serves the site (web/) and the newsroom desk.

    python -m server.app                 # http://localhost:8080 ; FOUNDRY_PROJECT_ENDPOINT / FOUNDRY_MODEL as for the newsroom
    POST /api/ask  {"match": "match_7", "question": "...", "profile": {...}, "t": 3600}
         -> the answer, the agents' steps, the moments it refers to; t (seconds) answers from what had happened by then
    GET  /api/live?match=match_7&persona=fan&from=1800&speed=10
         -> server-sent events: the match clock, and each moment as it arrives, as the newsroom drafts, checks and publishes it

Answers are cached on disk (server/cache), so a question asked once costs nothing again; at most ASK_SLOTS questions run at a time.
"""
import asyncio
import gzip
import hashlib
import json
import os
import pathlib
import time

import aiohttp
from aiohttp import web
from xml.sax.saxutils import escape

from newsroom.agents import cover
from newsroom.clients import make_client
from newsroom.desk import Desk
from newsroom.localize import translate
from newsroom.alerts import alerts
from newsroom.moments import moments, template_line
from matchsim.whatif import FORMATIONS, TACTIC_KEYS, cached_what_if, load_snapshots

ROOT = pathlib.Path(__file__).resolve().parents[1]
DATA, CACHE, WEB = ROOT / "server" / "data", ROOT / "server" / "cache", ROOT / "web"
ASK_SLOTS = int(os.environ.get("ASK_SLOTS", "2"))  # ponytail: one process-wide limit; per-user quotas if it is ever opened wider
PROFILE_KEYS = ("persona", "depth", "club", "player", "language", "units", "metric")
_matches, _gate, _client = {}, asyncio.Semaphore(ASK_SLOTS), None
# What the public demo may spend a day (UTC) on the model and speech; cached answers, the replay and everything precomputed stay free.
BUDGET = dict(ask=int(os.environ.get("ASKS_PER_DAY", "300")), live=int(os.environ.get("LIVE_RUNS_PER_DAY", "30")),
              translate=int(os.environ.get("TRANSLATIONS_PER_DAY", "3000")), speak=int(os.environ.get("SPEECH_PER_DAY", "1500")))
_spent = {}  # ponytail: in memory, one replica (deploy.ps1 max 1); a restart or scale to zero resets it, the Azure budget alert is the backstop


WHAT = dict(ask="questions", live="live runs", translate="translations", speak="spoken lines")


def spend(kind, n=1):
    """Count n model-backed requests of this kind against today's allowance, or refuse with 429."""
    key = (time.strftime("%Y-%m-%d", time.gmtime()), kind)
    if _spent.get(key, 0) + n > BUDGET[kind]:
        raise web.HTTPTooManyRequests(text=f"The free demo has used today's allowance of new {WHAT[kind]}; cached answers, the replay and the stored "
                                           "newsroom still work. It resets at midnight UTC.")
    _spent[key] = _spent.get(key, 0) + n


def match(mid):
    if mid not in _matches:
        path = DATA / f"{pathlib.Path(mid).name}.json.gz"
        if not path.exists():
            raise web.HTTPNotFound(text=f"no match {mid}")
        with gzip.open(path, "rt", encoding="utf-8") as fh:
            _matches[mid] = json.load(fh)
        if _matches[mid]["meta"].get("recipe"):
            load_snapshots(_matches[mid]["meta"]["recipe"], path.with_name(f"{path.name.split('.')[0]}.snaps"))
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
    data = [m["meta"]["duration"], len(m["events"])]  # a rebuilt match is a new match: its old answers do not apply
    key = hashlib.sha1(json.dumps([body.get("match"), data, q.lower(), prof, None if t is None else int(t // 60)], sort_keys=True).encode()).hexdigest()
    hit = CACHE / f"{key}.json"
    stream = request.query.get("stream") == "1"
    if hit.exists():
        out = dict(json.loads(hit.read_text(encoding="utf-8")), cached=True)
        if not stream:
            return web.json_response(out)
    else:
        out = None
        spend("ask")
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
        spend("translate", len(todo))
        async with _gate:
            _client = _client or make_client()
            new = await translate(_client, lang, [texts[i] for i in todo])
        CACHE.mkdir(parents=True, exist_ok=True)
        for i, t in zip(todo, new):
            got[keys[i]] = t
            (CACHE / f"tr-{keys[i]}.json").write_text(json.dumps(t, ensure_ascii=False), encoding="utf-8")
    return web.json_response(dict(texts=[got[k] for k in keys]))


_live_gate = asyncio.Semaphore(1)  # ponytail: one live run at a time for the whole site (each costs model calls); a queue if it is ever opened wider
LIVE_SLOTS, OVERLAY_S = 2, 10  # moments drafted at once; how long a published line stays on screen (as scripts/build_site.py)


async def live(request):
    """Plays a match's event stream on the server's clock (from `from`, at `speed` x) and runs the newsroom on each moment the moment it
    arrives: stats and tactics analysts, commentator, fact-checker with rewrites, all live on Foundry, nothing cached. The fact pack holds
    the numbers as they stood at the moment (newsroom/moments.py), so the desk never sees the rest of the match."""
    global _client
    m = match(request.query.get("match", ""))
    voice = "fan" if request.query.get("persona") == "fan" else "analyst"
    t0 = max(0., min(float(request.query.get("from", 0)), m["meta"]["duration"]))
    speed = max(1., min(float(request.query.get("speed", 10)), 30.))
    if _live_gate.locked():
        raise web.HTTPConflict(text="another live run is on; try again in a few minutes")
    spend("live")
    async with _live_gate:
        resp = web.StreamResponse(headers={"Content-Type": "text/event-stream", "Cache-Control": "no-cache"})
        await resp.prepare(request)
        send = lambda kind, data: resp.write(f"event: {kind}\ndata: {json.dumps(data, default=str)}\n\n".encode())
        _client = _client or make_client()
        loop, gate, jobs = asyncio.get_running_loop(), asyncio.Semaphore(LIVE_SLOTS), []
        w0, dur = loop.time(), m["meta"]["duration"]
        now = lambda: t0 + (loop.time() - w0) * speed
        packs = sorted((await loop.run_in_executor(None, moments, m)) + (await loop.run_in_executor(None, alerts, m)), key=lambda p: p["moment"]["t"])
        packs = [p for p in packs if p["moment"]["t"] >= t0]  # moments and the alerts the readers raise, each found from the data up to it

        async def one(p):
            arrived = loop.time()
            async with gate:
                await send("drafting", dict(moment=p["moment"], waited=round(loop.time() - arrived, 1)))
                try:
                    it = await cover(_client, p, voice)
                except Exception as ex:  # the model failed after its retries (newsroom/agents._run): the plain line from the facts goes out
                    it = dict(moment=p["moment"], persona=voice, line=template_line(p), approved=True, fallback=True, issues=[f"newsroom failed: {ex}"], rewrites=0, trail={})
            it["overlay"] = dict(t_in=it["moment"]["t"], t_out=it["moment"]["t"] + OVERLAY_S, kind=it["moment"]["type"])
            await send("published", dict(it, latency_s=round(loop.time() - arrived, 1), published_at=round(now(), 1)))
        try:
            await send("start", dict(t=t0, speed=speed, moments=len(packs)))
            i = 0
            while now() < dur and (i < len(packs) or not all(j.done() for j in jobs)):
                while i < len(packs) and packs[i]["moment"]["t"] <= now():
                    await send("moment", packs[i]["moment"])
                    jobs.append(asyncio.create_task(one(packs[i])))
                    i += 1
                await send("clock", dict(t=round(now(), 1)))
                await asyncio.sleep(1.)
            await asyncio.gather(*jobs)
            await send("end", dict(t=round(now(), 1)))
            await resp.write_eof()
        except (ConnectionResetError, asyncio.CancelledError):  # the viewer left: stop paying for drafts nobody will read
            for j in jobs:
                j.cancel()
            raise
        return resp


_sim_gate = asyncio.Semaphore(1)  # a what-if uses every core for about a minute


async def assistant(request):
    """POST {"match": "match_7", "t": 3600, "team": 1} -> server-sent events: each step of the bench assistant (reading the game, every what-if
    it runs), then the advice: two changes tested by replaying the rest of the match with and without each, the pick made from the results."""
    global _client
    body = await request.json()
    m = match(str(body.get("match", "")))
    if not m["meta"].get("recipe"):
        raise web.HTTPBadRequest(text="this match cannot be replayed")
    t = max(300., min(float(body.get("t", 300)), m["meta"]["duration"] - 300))
    team = 1 if int(body.get("team", 0)) else 0
    key = hashlib.sha1(json.dumps(["assistant", body.get("match"), [m["meta"]["duration"], len(m["events"])], team, int(t // 60)]).encode()).hexdigest()
    hit = CACHE / f"{key}.json"
    resp = web.StreamResponse(headers={"Content-Type": "text/event-stream", "Cache-Control": "no-cache"})
    if hit.exists():
        await resp.prepare(request)
        await resp.write(f"event: advice\ndata: {json.dumps(dict(json.loads(hit.read_text(encoding='utf-8')), cached=True), default=str)}\n\n".encode())
        await resp.write_eof()
        return resp
    spend("ask")
    if _sim_gate.locked():
        raise web.HTTPConflict(text="the simulator is busy with another what-if; try again in a minute")
    await resp.prepare(request)
    send = lambda kind, data: resp.write(f"event: {kind}\ndata: {json.dumps(data, default=str)}\n\n".encode())
    q_ = asyncio.Queue()

    async def run():
        global _client
        async with _sim_gate, _gate:
            _client = _client or make_client()
            return await Desk(_client, m, {"persona": "analyst", "depth": "standard"}, until=t, on_step=q_.put_nowait).advise(team)
    job = asyncio.create_task(run())
    quiet = time.time()
    while not job.done() or not q_.empty():
        try:
            await send("step", await asyncio.wait_for(q_.get(), timeout=.5))
            quiet = time.time()
        except asyncio.TimeoutError:
            if time.time() - quiet > 15:  # a comment line keeps the connection open (Container Apps drops one idle for 240 s)
                await resp.write(b": keepalive\n\n")
                quiet = time.time()
    try:
        out = job.result()
    except Exception as ex:
        await send("error", dict(error=str(ex)))
        await resp.write_eof()
        return resp
    CACHE.mkdir(parents=True, exist_ok=True)
    hit.write_text(json.dumps(out, default=str), encoding="utf-8")
    await send("advice", dict(out, cached=False))
    await resp.write_eof()
    return resp


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
    # the replays take minutes on a small server: a space every 15 s keeps the connection open (Container Apps drops one idle for 240 s);
    # JSON allows leading whitespace, so the client reads the body as usual
    resp = web.StreamResponse(headers={"Content-Type": "application/json", "Cache-Control": "no-cache"})
    await resp.prepare(request)
    async with _sim_gate:
        job = asyncio.get_running_loop().run_in_executor(None, cached_what_if, recipe, t, team, change)
        while True:
            try:
                out = await asyncio.wait_for(asyncio.shield(job), timeout=15)
                break
            except asyncio.TimeoutError:
                await resp.write(b" ")
            except Exception as ex:
                out = dict(error=str(ex))
                break
    if "error" not in out:
        stored = sum(1 for e in m["events"] if e["t"] < out["at"]["t"])
        out = dict(out, replay_matches=abs(stored - out["at"]["events"]) <= 2, team_name=m["meta"][("home", "away")[team]]["name"])
    await resp.write(json.dumps(out).encode())
    await resp.write_eof()
    return resp


# spoken commentary: Azure AI Speech on the same Foundry resource, signed in with the same identity (no keys)
VOICES = {"English": ("en-GB", "en-GB-RyanNeural", "en-GB-SoniaNeural"), "Español": ("es-ES", "es-ES-AlvaroNeural", "es-ES-ElviraNeural"),
          "Français": ("fr-FR", "fr-FR-HenriNeural", "fr-FR-DeniseNeural"), "Deutsch": ("de-DE", "de-DE-ConradNeural", "de-DE-KatjaNeural"),
          "Português": ("pt-BR", "pt-BR-AntonioNeural", "pt-BR-FranciscaNeural"), "Italiano": ("it-IT", "it-IT-DiegoNeural", "it-IT-ElsaNeural"),
          "中文": ("zh-CN", "zh-CN-YunxiNeural", "zh-CN-XiaoxiaoNeural"), "日本語": ("ja-JP", "ja-JP-KeitaNeural", "ja-JP-NanamiNeural"),
          "العربية": ("ar-SA", "ar-SA-HamedNeural", "ar-SA-ZariyahNeural")}
_cred = None


async def speak(request):
    """POST {"text": "...", "language": "English", "persona": "fan"} -> audio/mpeg. The fan's voice is quicker and brighter, the analyst's calm."""
    global _cred
    body = await request.json()
    text = str(body.get("text", ""))[:600]
    lang, persona = str(body.get("language", "English")), str(body.get("persona", "fan"))
    loc, male, female = VOICES.get(lang, VOICES["English"])
    voice = male if persona != "analyst" else female
    key = hashlib.sha1(json.dumps([text, voice, persona]).encode()).hexdigest()
    hit = CACHE / f"tts-{key}.mp3"
    if not hit.exists():
        rid, host = os.environ.get("SPEECH_RESOURCE_ID", ""), os.environ.get("SPEECH_ENDPOINT", "")
        if not (text and rid and host):
            raise web.HTTPNotImplemented(text="speech is not configured (SPEECH_RESOURCE_ID, SPEECH_ENDPOINT)")
        spend("speak")
        if _cred is None:
            from azure.identity import DefaultAzureCredential
            _cred = DefaultAzureCredential()
        tok = await asyncio.get_running_loop().run_in_executor(None, lambda: _cred.get_token("https://cognitiveservices.azure.com/.default").token)
        prosody = 'rate="+12%" pitch="+6%"' if persona == "fan" else 'rate="-4%"'
        ssml = f'<speak version="1.0" xml:lang="{loc}"><voice name="{voice}"><prosody {prosody}>{escape(text)}</prosody></voice></speak>'
        async with aiohttp.ClientSession() as http:
            async with http.post(f"{host.rstrip('/')}/tts/cognitiveservices/v1", data=ssml.encode(), headers={
                    "Authorization": f"Bearer aad#{rid}#{tok}", "Content-Type": "application/ssml+xml",
                    "X-Microsoft-OutputFormat": "audio-24khz-48kbitrate-mono-mp3", "User-Agent": "matchday-newsroom"}) as r:
                if r.status != 200:
                    raise web.HTTPBadGateway(text=f"speech service {r.status}")
                audio = await r.read()
        CACHE.mkdir(parents=True, exist_ok=True)
        hit.write_bytes(audio)
    return web.FileResponse(hit, headers={"Content-Type": "audio/mpeg"})


async def index(request):
    return web.FileResponse(WEB / "index.html")


async def _revalidate(request, response):
    """The page's own code is checked with the server on every load (a 304 when unchanged), so a deploy is seen at once."""
    if request.path == "/" or request.path.endswith((".html", ".css", ".js", ".json")):
        response.headers["Cache-Control"] = "no-cache"


def app():
    a = web.Application(client_max_size=64 * 1024)
    a.on_response_prepare.append(_revalidate)
    a.router.add_post("/api/ask", ask)
    a.router.add_post("/api/translate", tr)
    a.router.add_post("/api/whatif", whatif)
    a.router.add_post("/api/speak", speak)
    a.router.add_get("/api/live", live)
    a.router.add_post("/api/assistant", assistant)
    a.router.add_get("/", index)
    a.router.add_static("/", WEB)
    return a


if __name__ == "__main__":
    web.run_app(app(), port=int(os.environ.get("PORT", "8080")))
