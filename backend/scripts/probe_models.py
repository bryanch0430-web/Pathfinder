import asyncio, time
from app.config import get_settings
from app.core.llm import LLMClient

MODELS = [
    "claude-sonnet-5.5",
    "gemini-3.5-flash-lite",
    "gpt-5-nano",
    "gpt-5-mini",
    "deepseek-v4-flash",
    "gemini-3.8-flash",
    "gpt-4o-mini",
]

async def one(model: str):
    s = get_settings()
    llm = LLMClient(s)
    started = time.perf_counter()
    try:
        reply = await llm.text(
            purpose="probe",
            system="Reply with exactly OK.",
            user="Reply OK.",
            model=model,
            temperature=0.0,
            max_tokens=8,
        )
        return model, "ok", reply.text[:20], round((time.perf_counter()-started)*1000)
    except Exception as exc:
        return model, "fail", str(exc)[:120], round((time.perf_counter()-started)*1000)
    finally:
        await llm.aclose()

async def main():
    for result in await asyncio.gather(*(one(m) for m in MODELS)):
        print(result, flush=True)

asyncio.run(main())
