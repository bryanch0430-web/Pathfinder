"""Re-shoot the /guide screenshots by driving the real app in headless Chrome.

Needs the frontend (5173) and backend (8000) running with real LLM keys. Uses only
the stdlib plus `websockets` (already in the backend venv via uvicorn[standard]):

    backend/.venv/Scripts/python frontend/scripts/capture_guide.py [--only one_shot|build|pages]

Every run plans real trips in a fresh guest profile, so it takes several minutes (`pages` takes seconds).
Run it against the production preview (`npm run build`, then `npm run preview -- --port 5173 --strictPort`)
when you'll be editing files meanwhile: the dev server reloads the page on every save.
Shots land in frontend/public/guide/<name>.png. After a run, look at every shot and
make sure its caption in pages/GuidePage.tsx still says what the picture shows.
"""

import argparse
import asyncio
import base64
import json
import shutil
import subprocess
import tempfile
import time
import urllib.request
from datetime import date, timedelta
from pathlib import Path

import websockets

APP = "http://localhost:5173"
OUT = Path(__file__).resolve().parents[1] / "public" / "guide"
CHROME = next((p for p in (
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    shutil.which("google-chrome") or "", shutil.which("chromium") or "",
) if p and Path(p).exists()), None)
PORT = 9333

# Dates relative to today, so the Tokyo trip sits inside the forecast window and the
# Kyoto trip lands on months-ahead "typical" weather.
KYOTO_START = (date.today() + timedelta(days=12)).isoformat()
TOKYO_START = (date.today() + timedelta(days=3)).isoformat()


class Page:
    def __init__(self, ws):
        self.ws, self.next_id = ws, 0

    async def send(self, method: str, **params):
        self.next_id += 1
        await self.ws.send(json.dumps({"id": self.next_id, "method": method, "params": params}))
        while True:
            message = json.loads(await self.ws.recv())
            if message.get("id") == self.next_id:
                if "error" in message:
                    raise RuntimeError(f"{method}: {message['error']}")
                return message.get("result", {})

    async def js(self, expression: str):
        result = await self.send("Runtime.evaluate", expression=expression, awaitPromise=True, returnByValue=True)
        if "exceptionDetails" in result:
            raise RuntimeError(result["exceptionDetails"])
        return result.get("result", {}).get("value")

    async def wait(self, expression: str, timeout: float = 30, label: str = ""):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if await self.js(expression):
                return
            await asyncio.sleep(0.5)
        raise TimeoutError(label or expression)

    async def idle(self, timeout: float = 240):
        """Wait for the planner to finish thinking/streaming."""
        await asyncio.sleep(1.5)
        await self.wait("!document.querySelector('.tv-chat__thinking') && !document.querySelector('.tv-dots[aria-label=Writing]')",
                        timeout, "planner idle")
        await asyncio.sleep(1.5)

    async def click(self, text: str, selector: str = "button, summary, [role=tab]", nth: int = 0):
        # Waits for the element to be enabled: a click on a disabled button is silently dropped.
        find = f"""[...document.querySelectorAll({json.dumps(selector)})]
              .filter(el => el.offsetParent !== null && el.textContent.trim().includes({json.dumps(text)}))[{nth}]"""
        try:
            await self.wait(f"(() => {{ const el = {find}; return !!el && !el.disabled; }})()", 20)
        except TimeoutError:
            raise LookupError(f"No enabled element with text {text!r} in {selector!r}") from None
        await self.js(f"(() => {{ const el = {find}; el.scrollIntoView({{block: 'center'}}); el.click(); }})()")
        await asyncio.sleep(0.6)

    async def type(self, selector: str, text: str, enter: bool = False):
        """Set a React-controlled field's value; `enter` presses the send button of that composer."""
        ok = await self.js(f"""(() => {{ const el = document.querySelector({json.dumps(selector)});
            if (!el) return false;
            const proto = el.tagName === 'TEXTAREA' ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
            Object.getOwnPropertyDescriptor(proto, 'value').set.call(el, {json.dumps(text)});
            el.dispatchEvent(new Event('input', {{bubbles: true}})); return true; }})()""")
        if not ok:
            raise LookupError(selector)
        await asyncio.sleep(0.4)
        if enter:
            # The send button is disabled while a trip loads, and the welcome screen shows no "thinking" row
            # for idle() to wait on: a click then is dropped without a trace.
            send = f"""document.querySelector({json.dumps(selector)}).closest('.tv-chat__composer')
                .querySelector('button.tv-comp__send, button[type=submit]')"""
            await self.wait(f"!{send}.disabled", 60, "send button enabled")
            await self.js(f"{send}.click()")
            await asyncio.sleep(0.6)

    async def brief(self, label: str, value: str):
        """Fill one field of the trip brief, found by the start of its label."""
        ok = await self.js(f"""(() => {{
            const label = [...document.querySelectorAll('.tv-onboarding label')].find(l => l.textContent.trim().startsWith({json.dumps(label)}));
            const el = label?.querySelector('input, select'); if (!el) return false;
            const proto = el.tagName === 'SELECT' ? HTMLSelectElement.prototype : HTMLInputElement.prototype;
            Object.getOwnPropertyDescriptor(proto, 'value').set.call(el, {json.dumps(value)});
            el.dispatchEvent(new Event('input', {{bubbles: true}})); el.dispatchEvent(new Event('change', {{bubbles: true}}));
            return true; }})()""")
        if not ok:
            raise LookupError(f"brief field {label!r}")
        await asyncio.sleep(0.2)

    async def scroll_chat(self, where: str = "bottom"):
        await self.js(f"""(() => {{ const el = document.querySelector('.tv-chat__scroll'); if (!el) return;
            el.scrollTop = {'el.scrollHeight' if where == 'bottom' else '0'}; }})()""")
        await asyncio.sleep(0.5)

    async def shot(self, name: str):
        await asyncio.sleep(0.8)
        data = await self.send("Page.captureScreenshot", format="png")
        OUT.mkdir(parents=True, exist_ok=True)
        (OUT / f"{name}.png").write_bytes(base64.b64decode(data["data"]))
        print("saved", name)


async def start_trip(page: Page, message: str):
    await page.idle(60)  # the planner ignores sends while it is still loading the trip
    await page.type(".tv-chat__composer textarea", message, enter=True)
    await page.wait("[...document.querySelectorAll('.tv-msg2--user')].length > 0", 20, "message sent")
    await page.idle()
    await page.wait("!!document.querySelector('.tv-onboarding')", 60, "trip brief")


async def say(page: Page, message: str, composer: str = ".tv-chat__composer textarea"):
    """Send a chat message and wait for the reply to finish."""
    await page.type(composer, message, enter=True)
    await page.idle(300)
    await page.scroll_chat("bottom")


async def brief_shot(page: Page, name: str):
    await page.js("document.querySelector('.tv-onboarding').scrollIntoView({block: 'center'})")
    await page.shot(name)


async def next_step(page: Page):
    await page.click("Next", ".tv-onboarding button")


async def open_studio(page: Page):
    await page.click("Open studio", ".tv-chat__bar button")
    await page.wait("!!document.querySelector('.tv-studio')", 30, "studio")
    await asyncio.sleep(3)


async def one_shot(page: Page):
    await page.js("location.href = '/create'")
    await page.wait("!!document.querySelector('.tv-chat__composer textarea')", 60, "planner")
    await page.idle(60)
    await asyncio.sleep(3)  # the welcome's entrance
    await page.shot("01-welcome")

    # The brief, one step at a time.
    await start_trip(page, "Plan 3 days in Kyoto from Delhi. I love food and old temples.")
    await page.brief("Start date", KYOTO_START)
    await brief_shot(page, "02-brief-step-1")
    await next_step(page)
    await page.brief("Adults", "2")
    await page.brief("Total budget", "120000")
    await page.brief("Currency", "INR")
    await brief_shot(page, "02b-brief-step-2")
    await next_step(page)
    await page.brief("What interests you?", "food, temples")
    await page.brief("Anything to avoid?", "crowded attractions")
    await brief_shot(page, "02c-brief-step-3")
    await next_step(page)
    await brief_shot(page, "02d-guide-picker")
    await page.click("Continue to planning options")
    await page.idle()
    await page.wait("!!document.querySelector('.tv-planning-choice')", 60, "planning choice")
    await page.js("document.querySelector('.tv-planning-choice').scrollIntoView({block: 'center'})")
    await page.shot("03-planning-choice")

    await page.click("Generate full itinerary")
    await page.idle(300)
    await page.js("[...document.querySelectorAll('.tv-msg2--agent')].at(-1).scrollIntoView({block: 'start'})")
    await asyncio.sleep(0.5)
    await page.shot("04-one-shot-itinerary")
    await page.js("document.querySelector('.tv-trip-card').scrollIntoView({block: 'center'})")
    await asyncio.sleep(3)  # the card's photos
    await page.shot("05-trip-card")

    # Changes: asked for, offered and agreed to, and the budget. Each one leaves a receipt.
    await say(page, "Can you add the Arashiyama bamboo grove on the morning of day 2?")
    await page.shot("05b-change-receipt")
    await say(page, "Day 1 looks busy. Is there anything you'd move to another day?")
    await page.shot("05c-offer")
    await say(page, "yes")
    await page.shot("05d-yes-applied")
    await say(page, "My budget is 150,000 INR")
    await page.shot("05e-budget-from-chat")

    await open_studio(page)
    await page.click("Plan", "[role=tab]")
    await asyncio.sleep(2)
    await page.shot("06-studio-plan")
    await page.click("Sketch", "[role=tab]")
    await asyncio.sleep(5)  # the guide draws the overview
    await page.shot("07-studio-sketch")
    await page.click("", "[aria-label='Next page']")
    await asyncio.sleep(5)  # the guide draws the day
    await page.shot("07c-sketch-day")
    # Base-map tiles are slow in headless Chrome and sometimes never arrive: wait for real tiles, and
    # reopen the tab once if they don't come.
    tiles = """[...document.querySelectorAll('.tv-studio .gm-style img')]
        .filter(img => img.complete && img.naturalWidth >= 128).length >= 6"""
    for attempt in range(2):
        await page.click("Map", "[role=tab]")
        try:
            await page.wait(tiles, 45, "map tiles")
            break
        except TimeoutError:
            if attempt:
                raise
            await page.click("Plan", "[role=tab]")
    await asyncio.sleep(3)
    await page.shot("08-studio-map")
    await page.click("3D", "[role=tab]")
    await asyncio.sleep(8)
    await page.shot("09-studio-3d")

    await page.click("Plan", "[role=tab]")
    await page.click("Export", ".tv-export__trigger")
    await page.shot("10-export-menu")
    # A scripted click isn't a user's click, so the menu holds the file behind its Save button:
    # the same state a slow export ends in.
    await page.click("Everything (.json)", ".tv-export__panel button")
    await page.wait("!!document.querySelector('.tv-export__status a')", 30, "export ready")
    await page.shot("10b-export-ready")
    await page.js("document.dispatchEvent(new KeyboardEvent('keydown', {key: 'Escape', bubbles: true}))")
    await asyncio.sleep(0.5)

    await page.click("Budget", ".tv-studio__bar button")
    await page.wait("[...document.querySelectorAll('button')].some(b => b.textContent.includes('Suggest amounts'))", 60, "budget")
    await page.shot("11-budget-empty")
    await page.click("Suggest amounts")
    await page.wait("[...document.querySelectorAll('button')].some(b => b.textContent.includes('Use all suggestions'))", 180, "suggestions")
    await page.shot("12-budget-suggestions")
    await page.js("document.querySelector('.tv-budget__body').scrollTop = 620")
    await page.shot("13-budget-rows")
    await page.click("Close ×")


async def build(page: Page):
    await page.js("location.href = '/create'")
    await page.wait("!!document.querySelector('.tv-chat__composer textarea')", 60, "planner")
    await asyncio.sleep(2)
    await page.click("New trip")
    await asyncio.sleep(3)
    await start_trip(page, "3 days in Tokyo from Delhi")
    await page.brief("Start date", TOKYO_START)
    await next_step(page)
    await page.brief("Total budget", "60000")
    await page.brief("Currency", "JPY")
    await next_step(page)
    await page.brief("What interests you?", "food, quiet gardens")
    await page.brief("Anything to avoid?", "nightclubs")
    await next_step(page)
    await page.click("Octa-san", ".tv-onboarding__guide")
    await page.click("Continue to planning options")
    await page.idle()
    await page.wait("!!document.querySelector('.tv-planning-choice')", 60, "planning choice")
    await page.click("Build with Octa-san")
    await page.idle(300)
    await page.wait("!!document.querySelector('.tv-copilot')", 60, "build dock")
    await page.scroll_chat("bottom")
    await page.shot("14-build-day-1")

    await page.click("", ".tv-copilot__chips button")  # the first suggestion
    await page.idle()
    await page.scroll_chat("bottom")
    await page.shot("15-build-added")

    await page.click("Day plan", ".tv-copilot__toggle")
    await page.shot("16-build-day-plan")
    planned = await page.js("document.querySelector('.tv-copilot__plan li > span')?.firstChild?.textContent")
    await page.click("Day plan", ".tv-copilot__toggle")  # closed again: the conversation keeps the screen

    await page.click("", "[aria-label='Next day']")  # a local switch: no request
    await page.click("", ".tv-copilot__chips button")
    await page.idle()
    await say(page, "I never want museums on this trip. Also add a great local lunch spot today.")
    await page.shot("17-build-preferences")

    # A move onto another day, then a place that breaks the traveler's own rule.
    await say(page, f"Move {planned} to day 3")
    await page.shot("17b-build-move")
    await say(page, "Add the Tokyo National Museum to day 2")
    await page.shot("18-build-add-anyway")

    await page.click("Budget", ".tv-chat__bar button")
    await page.wait("!!document.querySelector('.tv-budget__summary')", 60, "budget")
    await asyncio.sleep(2)
    await page.shot("19-build-budget-sync")
    await page.click("Close ×")

    # The studio with the chat open beside the sketch: a change drawn live.
    await open_studio(page)
    await page.click("Sketch", "[role=tab]")
    await page.click("DAY 1", ".tv-studio__daywin button")
    await asyncio.sleep(5)
    await page.click("Chat", ".tv-studio__windows button")
    await asyncio.sleep(1.5)
    await page.type(".tv-studio .tv-chat__composer textarea", "Add a quiet garden to day 1 if it fits.", enter=True)
    try:
        await page.wait("!!document.querySelector('.tv-sketch .tv-guide.is-drawing')", 120, "live draw")
        await asyncio.sleep(0.6)
    except TimeoutError:
        print("no live draw seen; the agent may have declined the change")
    await page.shot("20-build-live-sketch")
    await page.idle(300)
    await asyncio.sleep(2)
    await page.shot("21-build-live-sketch-done")

    trip_path = await page.js("location.pathname")
    await page.click("", "[aria-label='Back to the chat']")
    await asyncio.sleep(2)
    await page.click("Day plan", ".tv-copilot__toggle")
    await page.click("Finish itinerary")
    await page.idle(300)
    await page.click("Day plan", ".tv-copilot__toggle")
    await page.scroll_chat("bottom")
    await page.shot("22-build-complete")

    # Two trips now exist for this guest: the library has something to show.
    await page.js("location.href = '/trips'")
    await asyncio.sleep(8)
    await page.shot("25-trips")

    # The same trip on a phone held upright: portrait sketch pages, bottom sheets, and the chat.
    await page.send("Emulation.setDeviceMetricsOverride", width=390, height=844, deviceScaleFactor=2, mobile=True)
    await page.js(f"localStorage.setItem('tripverse-studio-tab', 'sketch'); location.href = {json.dumps(trip_path)}")
    await page.wait("!!document.querySelector('.tv-sketch__page')", 60, "phone sketch")
    await asyncio.sleep(3)
    await page.click("", "[aria-label='Next page']")
    await asyncio.sleep(5)
    await page.shot("23-phone-sketch")
    await page.js("location.href = '/create'")
    await page.wait("!!document.querySelector('.tv-copilot')", 60, "phone chat")
    await page.idle(60)
    await page.scroll_chat("bottom")
    await page.shot("23b-phone-chat")
    await page.send("Emulation.setDeviceMetricsOverride", width=1440, height=900, deviceScaleFactor=1, mobile=False)


async def pages(page: Page):
    """The site's other pages. No planning, so this takes seconds."""
    for path, name, settle in (("/", "00-home", 6), ("/explore", "24-explore", 6), ("/credits", "26-credits", 4)):
        await page.js(f"location.href = {json.dumps(path)}")
        await asyncio.sleep(settle)  # entrance motion and photography
        await page.shot(name)


async def main(only: str | None):
    if not CHROME:
        raise SystemExit("Chrome or Edge not found")
    profile = tempfile.mkdtemp(prefix="pathfinder-guide-")
    chrome = subprocess.Popen([CHROME, "--headless=new", f"--remote-debugging-port={PORT}", f"--user-data-dir={profile}",
                               "--window-size=1440,900", "--hide-scrollbars", "--use-angle=swiftshader",
                               "--enable-unsafe-swiftshader", "about:blank"])
    try:
        for _ in range(40):
            try:
                targets = json.load(urllib.request.urlopen(f"http://127.0.0.1:{PORT}/json"))
                target = next(t for t in targets if t["type"] == "page")
                break
            except Exception:
                time.sleep(0.5)
        # No keepalive pings: a heavy page (the home page's scroll scenes under software GL) can keep Chrome
        # from answering one in time, and the run died mid-shoot.
        async with websockets.connect(target["webSocketDebuggerUrl"], max_size=None, ping_interval=None) as ws:
            page = Page(ws)
            await page.send("Page.enable")
            await page.send("Emulation.setDeviceMetricsOverride", width=1440, height=900, deviceScaleFactor=1, mobile=False)
            await page.send("Page.navigate", url=f"{APP}/create")
            await asyncio.sleep(3)
            try:
                if only in (None, "one_shot"):
                    await one_shot(page)
                if only in (None, "build"):
                    await build(page)
                if only in (None, "pages"):
                    await pages(page)
            except Exception:
                await page.shot("_failure")  # what the page showed when a step gave up; not used by the guide
                raise
    finally:
        chrome.terminate()
        shutil.rmtree(profile, ignore_errors=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--only", choices=["one_shot", "build", "pages"])
    asyncio.run(main(parser.parse_args().only))
