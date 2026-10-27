"""The newsroom's analysis tools as an MCP server: any MCP client (GitHub Copilot agent mode in VS Code, Claude, a Foundry agent) can query
a match with the same deterministic tools the newsroom's own agents use, or put a question to the whole newsroom.

    python -m server.mcp_server            # stdio (VS Code: .vscode/mcp.json; Claude Desktop: command python, args -m server.mcp_server)
    python -m server.mcp_server --http     # streamable HTTP on :8787/mcp

Every analysis tool takes `match` (an id from list_matches) and an optional `minute` (answer from what had happened by then; 0 for the
whole match), then its own arguments. Distances are metres. ask_newsroom needs FOUNDRY_PROJECT_ENDPOINT (the multi-agent desk on Foundry);
what_if replays the match and takes about a minute.
"""
import gzip
import inspect
import json
import logging
import pathlib
import sys
import typing

from mcp.server.fastmcp import FastMCP
from pydantic import Field

from matchsim.whatif import FORMATIONS, TACTIC_KEYS, cached_what_if, load_snapshots, t_at_minute
from newsroom.clients import ScriptedClient, make_client
from newsroom.desk import Desk

for _lg in ("azure", "opentelemetry", "httpx", "agent_framework", "mcp.server.lowlevel"):  # stdio clients show stderr: keep it to warnings
    logging.getLogger(_lg).setLevel(logging.WARNING)
DATA = pathlib.Path(__file__).resolve().parent / "data"
_matches = {}
mcp = FastMCP("football-kaleidoscope", host="0.0.0.0", port=8787)
TOOLS = ("match_summary", "team_shape", "formation_changes", "pressing", "defensive_duties", "control_and_rhythm", "fatigue", "stat_leaders",
         "momentum", "passing_network", "player_report", "list_players", "find_moments", "moment_breakdown")


def load(mid):
    mid = pathlib.Path(str(mid)).name
    if mid not in _matches:
        path = DATA / f"{mid}.json.gz"
        if not path.exists():
            raise ValueError(f"no match {mid!r}; call list_matches")
        with gzip.open(path, "rt", encoding="utf-8") as fh:
            _matches[mid] = json.load(fh)
        if _matches[mid]["meta"].get("recipe"):
            load_snapshots(_matches[mid]["meta"]["recipe"], DATA / f"{mid}.snaps")
    return _matches[mid]


def desk(mid, minute=0, client=None):
    m = load(mid)
    until = t_at_minute(m["events"], int(minute) + 1, m["meta"]["half_time"]) if minute else None
    return Desk(client or ScriptedClient(), m, {"persona": "analyst"}, until=until)


@mcp.tool()
def list_matches() -> list:
    """The synthetic matches available: id, home and away team, final score."""
    out = []
    for p in sorted(DATA.glob("*.json.gz")):
        m = load(p.name.split(".")[0])
        out.append(dict(id=p.name.split(".")[0], home=m["meta"]["home"]["name"], away=m["meta"]["away"]["name"], score=m["meta"]["score"]))
    return out


def _described(ann):
    """Annotated[type, "text"] -> Annotated[type, Field(description="text")], so MCP clients see what each argument means."""
    if typing.get_origin(ann) is typing.Annotated:
        base, *meta = typing.get_args(ann)
        return typing.Annotated[base, Field(description=" ".join(str(x) for x in meta if isinstance(x, str)))]
    return ann


def _register(name, proto):
    sig = inspect.signature(proto)
    own = [p.replace(kind=inspect.Parameter.KEYWORD_ONLY, annotation=_described(p.annotation)) for p in sig.parameters.values()]
    head = [inspect.Parameter("match", inspect.Parameter.KEYWORD_ONLY, annotation=typing.Annotated[str, Field(description="match id from list_matches")]),
            inspect.Parameter("minute", inspect.Parameter.KEYWORD_ONLY, default=0,
                              annotation=typing.Annotated[int, Field(description="answer from what had happened by this minute; 0 for the whole match")])]

    def tool(**kw):
        mid, minute = kw.pop("match"), kw.pop("minute", 0)
        return desk(mid, minute).tool_fns[name](**kw)
    tool.__name__, tool.__doc__ = name, proto.__doc__
    tool.__signature__ = inspect.Signature(head + own, return_annotation=dict)
    mcp.add_tool(tool, name=name, description=inspect.cleandoc(proto.__doc__ or name))


_proto = Desk(ScriptedClient(), load(next(DATA.glob("*.json.gz")).name.split(".")[0]), {}).tool_fns
for _n in TOOLS:
    _register(_n, _proto[_n])


@mcp.tool()
def what_if(match: typing.Annotated[str, Field(description="match id from list_matches")],
            team: typing.Annotated[str, Field(description="home or away")],
            from_minute: typing.Annotated[int, Field(description="the minute the change is made")],
            press: float = -1., line_height: float = -1., compactness: float = -1., width: float = -1., directness: float = -1., risk: float = -1., shoot: float = -1.,
            formation: typing.Annotated[str, Field(description="e.g. 4-4-2; empty to leave it")] = "") -> dict:
    """Replay the match exactly to a minute, change one team's instructions (0-1 each; -1 leaves one as it was) or formation there, and play
    the rest 12 times with and 12 times without the change (the same random numbers for each pair): win / draw / loss, goals and xG after."""
    m = load(match)
    change = {k: v for k, v in dict(press=press, line_height=line_height, compactness=compactness, width=width, directness=directness, risk=risk, shoot=shoot).items()
              if v >= 0 and k in TACTIC_KEYS}
    if formation:
        if formation not in FORMATIONS:
            raise ValueError(f"unknown formation; one of {sorted(FORMATIONS)}")
        change["formation"] = formation
    if not change:
        raise ValueError("nothing to change")
    tm = 1 if str(team).lower() in ("1", "away", m["meta"]["away"]["name"].lower()) else 0
    return cached_what_if(m["meta"]["recipe"], t_at_minute(m["events"], int(from_minute), m["meta"]["half_time"]), tm, change)


@mcp.tool()
async def ask_newsroom(match: typing.Annotated[str, Field(description="match id from list_matches")],
                       question: typing.Annotated[str, Field(description="any question about the match")],
                       minute: typing.Annotated[int, Field(description="answer from what had happened by this minute; 0 for the whole match")] = 0) -> dict:
    """Put a question to the whole newsroom on Microsoft Foundry: an orchestrator sends it to the tactics analyst and the scout (who use the
    tools above), a writer drafts the answer and a fact-checker (an agent plus deterministic checks) passes it or sends it back."""
    r = await desk(match, minute, make_client()).ask(question)
    return {k: r[k] for k in ("answer", "held", "approved", "rewrites", "watch")} | dict(steps=[f"{s['agent']}: {s['tool']}" for s in r["steps"]])


if __name__ == "__main__":
    mcp.run(transport="streamable-http" if "--http" in sys.argv else "stdio")
