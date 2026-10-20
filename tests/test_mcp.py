"""The MCP server lists its tools with described arguments and answers from the committed demo data (offline: no Foundry call)."""
import asyncio
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from server.mcp_server import mcp  # noqa: E402


async def main():
    tools = {t.name: t for t in await mcp.list_tools()}
    assert {"list_matches", "defensive_duties", "fatigue", "control_and_rhythm", "what_if", "ask_newsroom"} <= set(tools)
    assert tools["fatigue"].inputSchema["properties"]["match"]["description"]
    out = await mcp.call_tool("stat_leaders", {"match": "match_7", "minute": 60})
    data = json.loads((out[0] if isinstance(out, tuple) else out)[0].text)  # (content, structured) in newer SDKs
    assert "hard_passes_completed" in data
    print("mcp ok", len(tools), "tools")


asyncio.run(main())
