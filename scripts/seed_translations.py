"""Fills the translation cache for every language by using the app as a reader would: each match's feed and reports, and every view
and tab for the labels, through a local server (python -m server.app on :8090, with Foundry). Then copies the cache into
server/tr_seed/, which ships with the image, so a fresh deploy answers translations at once instead of asking the model again.

    python scripts/seed_translations.py [http://localhost:8090/]
"""
import asyncio
import json
import pathlib
import shutil
import sys

from playwright.async_api import async_playwright

ROOT = pathlib.Path(__file__).resolve().parents[1]
URL = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8090/"
LANGS = ["Español", "Français", "Deutsch", "Português", "Italiano", "中文", "日本語", "العربية"]
TABS = {"fan": ["Live", "Ask", "Recap", "Players", "Stats", "Clips"], "analyst": ["Feed", "Board", "What if", "Ask", "Players", "Recap", "Stats", "Clips"],
        "scout": ["Players", "Ask", "Clips"]}
IDLE = "trQueue.size === 0 && trBusy === 0"


async def settle(page, timeout=300):
    """Until the page has nothing left to translate: idle for three seconds running, after the data has loaded."""
    await page.wait_for_function("typeof S !== 'undefined' && !!S.D", timeout=120_000)
    await asyncio.sleep(2)  # pretranslate runs just after the load
    quiet = 0
    for _ in range(timeout):
        quiet = quiet + 1 if await page.evaluate(IDLE) else 0
        if quiet >= 3:
            return
        await asyncio.sleep(1)
    print("  still translating after", timeout, "s", flush=True)


async def one_language(browser, lang, matches):
    ctx = await browser.new_context(viewport={"width": 1536, "height": 864})
    await ctx.add_init_script(f"try {{ localStorage.setItem('mn-profile', JSON.stringify({{language: {json.dumps(lang)}}})); "
                              "localStorage.setItem('mn-welcomed', '1') } catch (e) {}")
    page = await ctx.new_page()
    for i, m in enumerate(matches):
        await page.goto(f"{URL}?m={m}&p=fan&t=7000", wait_until="load", timeout=180_000)
        await settle(page)  # the feeds and the reports of this match
        if i == 0:  # the labels: every view and tab (they are the same in every match), the settings and the welcome card
            await page.evaluate("document.getElementById('welcome').hidden = false; document.getElementById('settings').open = true; relabel()")
            await settle(page)
            for persona, tabs in TABS.items():
                for tab in tabs:
                    await page.evaluate(f"setPersona('{persona}'); S.tab = {json.dumps(tab)}; setTabs(); render(true)")
                    await settle(page, 120)
        print(lang, m, "done", flush=True)
    await ctx.close()


async def main():
    matches = [m["id"] for m in json.loads((ROOT / "web/data/index.json").read_text(encoding="utf-8"))]
    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        await asyncio.gather(*(one_language(browser, lang, matches) for lang in LANGS))
        await browser.close()
    seed = ROOT / "server" / "tr_seed"
    seed.mkdir(exist_ok=True)
    n = 0
    for f in (ROOT / "server" / "cache").glob("tr-*.json"):
        shutil.copy2(f, seed / f.name); n += 1
    print(n, "translations in", seed)


if __name__ == "__main__":
    asyncio.run(main())
