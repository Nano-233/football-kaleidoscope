"""Cover a simulated match with the newsroom and write out/<match>_newsroom.json.

    python scripts/run_newsroom.py out/match_12.json [persona]      # veteran | analyst | fan

Uses Microsoft Foundry when FOUNDRY_PROJECT_ENDPOINT is set (see newsroom/clients.py), otherwise the scripted offline client.
"""
import asyncio
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from newsroom.agents import cover  # noqa: E402
from newsroom.clients import make_client  # noqa: E402
from newsroom.moments import moments  # noqa: E402


async def main(path, persona):
    match = json.load(open(path, encoding="utf-8"))
    client = make_client()
    items = [await cover(client, pack, persona) for pack in moments(match)]
    out = pathlib.Path(path).with_name(pathlib.Path(path).stem + "_newsroom.json")
    out.write_text(json.dumps(items, indent=1), encoding="utf-8")
    for i in items:
        print(i["moment"]["clock"], "OK " if i["approved"] else "REJ", i["line"], i["issues"] or "")


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else "veteran"))
