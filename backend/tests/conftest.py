import json
import os
import re
from typing import Any
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

# Ensure test suite runs 100% offline with LangSmith tracing disabled
os.environ["LANGSMITH_TRACING"] = "false"
os.environ["LANGCHAIN_TRACING_V2"] = "false"
os.environ["ENRICHMENT_ENABLED"] = "false"  # weather/holiday/currency APIs; test_enrichment.py turns it on with mocks

from app.core.database import Base, get_db
from app.main import app
from app.services.llm.base import LLMProvider, LLMResult, ToolCall
from app.services.llm.service import llm_service

# In-memory SQLite async engine for lightning fast and isolated testing
TEST_DATABASE_URL = "sqlite+aiosqlite:///:memory:"

test_engine = create_async_engine(
    TEST_DATABASE_URL,
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)

TestingSessionLocal = async_sessionmaker(
    test_engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autocommit=False,
    autoflush=False,
)


class TestMockLLMProvider(LLMProvider):
    """
    Hermetic in-memory mock LLM provider for pytest.
    Guarantees that automated tests execute 100% offline with ZERO external API calls.
    """

    async def generate(
        self,
        prompt: str,
        system_instruction: str | None = None,
        temperature: float = 0.7,
        max_output_tokens: int | None = None,
        **kwargs,
    ) -> str:
        sys_str = (system_instruction or "").lower()
        prompt_str = prompt.lower()

        # 1. Onboarding understand_user_message node
        if "conversational understanding layer" in sys_str:
            user_msg = ""
            m = re.search(r'LATEST USER MESSAGE:\s*"(.*?)"', prompt, re.DOTALL)
            if m:
                user_msg = m.group(1).strip()
            else:
                user_msg = prompt.strip()

            dest = None
            dur = None
            origin = None
            lower_msg = user_msg.lower()

            if "switzerland" in lower_msg:
                dest = "Switzerland"
            elif "japan" in lower_msg:
                dest = "Japan"
            elif "tokyo" in lower_msg:
                dest = "Tokyo"
            elif "kyoto" in lower_msg:
                dest = "Kyoto"
            elif "rome" in lower_msg:
                dest = "Rome"
            elif "france" in lower_msg:
                dest = "France"
            elif "paris" in lower_msg and "from paris" not in lower_msg:
                dest = "Paris"

            dur_match = re.search(r"(\d+)\s*days?", lower_msg)
            if dur_match:
                dur = int(dur_match.group(1))

            if "from kolkata" in lower_msg or lower_msg == "kolkata":
                origin = "Kolkata"
            elif "from delhi" in lower_msg or lower_msg == "delhi":
                origin = "Delhi"
            elif "from new york" in lower_msg or lower_msg == "new york":
                origin = "New York"
            elif "from paris" in lower_msg:
                origin = "Paris"
            elif "san francisco" in lower_msg:
                origin = "San Francisco, CA"

            return json.dumps({
                "intent": "trip_information",
                "destination": dest,
                "duration_days": dur,
                "origin": origin,
            })

        # 2. Planning understand_trip node
        if "trip analysis engine" in sys_str or "planning_notes" in prompt_str:
            return json.dumps({
                "trip_type": "multi_city",
                "planning_notes": "Well-balanced voyage across key destinations.",
            })

        # 3. Planning research_destination node
        if "destination research" in sys_str or "candidate curation" in sys_str:
            dest_match = re.search(r"destination:\s*([^\n]+)", prompt_str)
            dest_name = dest_match.group(1).strip().capitalize() if dest_match else "Selected Destination"
            return json.dumps({
                "needs_search": False,
                "search_queries": [],
                "candidates": [
                    {
                        "name": dest_name,
                        "type": "city",
                        "reason": f"Prime highlight hub for your voyage in {dest_name}.",
                    }
                ],
                "assistant_response": f"I've curated {dest_name} as the top candidate for your voyage.",
            })

        # 4. Welcome greeting
        if "welcome" in prompt_str or "welcome" in sys_str:
            return "Welcome to Pathfinder! Where would you like to travel?"

        return "Got it! Let's continue planning your trip."

    async def generate_with_tools(
        self,
        prompt: str,
        system_instruction: str | None = None,
        tools: list[Any] | None = None,
        temperature: float = 0.7,
        max_output_tokens: int | None = None,
        **kwargs,
    ) -> LLMResult:
        prompt_str = prompt.lower()
        if any(kw in prompt_str for kw in ["current location", "where i am", "from here", "my location", "use current"]):
            return LLMResult(
                text="I'll help you use your current location as your departure point.",
                tool_calls=[ToolCall(name="get_user_location", args={})],
            )
        text = await self.generate(
            prompt=prompt,
            system_instruction=system_instruction,
            temperature=temperature,
            max_output_tokens=max_output_tokens,
            **kwargs,
        )
        return LLMResult(text=text, tool_calls=[])

    async def generate_stream(
        self,
        prompt: str,
        system_instruction: str | None = None,
        temperature: float = 0.7,
        max_output_tokens: int | None = None,
        **kwargs,
    ):
        tokens = ["Got ", "it! ", "Let's ", "continue ", "planning ", "your ", "trip."]
        for t in tokens:
            yield t


@pytest.fixture(autouse=True)
def mock_llm_provider():
    """Autouse fixture ensuring all tests use hermetic mock LLM provider without hitting external APIs."""
    orig_primary = llm_service._primary_provider
    orig_fallback = llm_service._fallback_provider
    llm_service.set_providers(TestMockLLMProvider(), fallback=None)
    yield
    llm_service.set_providers(orig_primary, fallback=orig_fallback)


@pytest_asyncio.fixture(autouse=True)
async def setup_db():
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


async def override_get_db():
    async with TestingSessionLocal() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


app.dependency_overrides[get_db] = override_get_db


@pytest_asyncio.fixture
async def client():
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as ac:
        yield ac
