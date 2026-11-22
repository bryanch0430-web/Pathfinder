import asyncio, time
from app.config import get_settings
from app.core.llm import LLMClient

MODELS = ["deepseek-v4-flash", "deepseek-v4.1-flash", "deepseek-v4.1-flash-1017", "deepseek-v4.1-flash-1101"]

async def one(model):
    s = get_settings(); llm = LLMClient(s); started = time.perf_counter()
    try:
        r = await llm.text(purpose="probe", system="Reply with exactly OK.", user="Reply OK.", model=model, temperature=0.0, max_tokens=8)
        return model, "ok", r.text[:30], round((time.perf_counter()-started)*1000)
    except Exception as exc:
        return model, "fail", str(exc)[:160], round((time.perf_counter()-started)*1000)
    finally:
        await llm.aclose()

async def main():
    for x in await asyncio.gather(*(one(m) for m in MODELS)):
        print(x, flush=True)
asyncio.run(main())
