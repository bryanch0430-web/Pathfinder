import json
from typing import List, Union
from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    APP_NAME: str = "Pathfinder API"
    APP_ENV: str = "development"
    DEBUG: bool = True
    PORT: int = 8000
    LOG_LEVEL: str = "INFO"
    VERSION: str = "0.1.0"

    # Feedback delivery: server-only credentials, sent over HTTPS on Render.
    RESEND_API_KEY: str = ""
    FEEDBACK_TO_EMAIL: str = "maityrohit021@gmail.com"
    FEEDBACK_FROM_EMAIL: str = "Pathfinder <onboarding@resend.dev>"

    # CORS configuration strictly restricted to frontend origins
    CORS_ORIGINS: Union[List[str], str] = [
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    ]

    # Database connection string (PostgreSQL / Supabase compatible, SQLite fallback for zero-config dev)
    DATABASE_URL: str = "sqlite+aiosqlite:///./pathfinder.db"

    # Supabase Configuration
    SUPABASE_URL: str = ""
    SUPABASE_KEY: str = ""
    SUPABASE_SECRET: str = ""
    SUPABASE_JWT_SECRET: str = ""

    # LLM Provider Configuration
    LLM_PROVIDER: str = "groq"

    # DeepSeek's OpenAI-compatible Chat Completions API (server-side only).
    DEEPSEEK_API_KEY: str = ""
    DEEPSEEK_BASE_URL: str = "https://api.deepseek.com"
    DEEPSEEK_MODEL: str = "deepseek-flash"
    DEEPSEEK_FAST_MODEL: str = "deepseek-flash"

    # Groq LLM Configuration (Primary)
    GROQ_API_KEY: str = ""
    # Optional second key from another Groq account: used when the first hits its rate or daily limit.
    GROQ_API_KEY2: str = ""
    GROQ_MODEL: str = "openai/gpt-oss-120b"
    # Smaller Groq model for JSON extraction/intent nodes; empty uses GROQ_MODEL.
    GROQ_FAST_MODEL: str = "openai/gpt-oss-20b"

    # Google Gemini LLM Configuration (Fallback)
    GEMINI_API_KEY: str = ""
    GEMINI_MODEL: str = "gemini-3.1-flash-lite"

    # Tavily Web Search Configuration
    TAVILY_API_KEY: str = ""

    # Optional free openrouteservice key for road distance and duration.
    ORS_API_KEY: str = ""
    GOOGLE_MAPS_API_KEY: str = ""
    GOOGLE_PLACES_API_KEY: str = ""
    GOOGLE_ROUTES_ENABLED: bool = False
    YOUTUBE_API_KEY: str = ""
    # Weather, holidays and exchange rates from free public APIs (services/enrichment.py).
    ENRICHMENT_ENABLED: bool = True

    # Cloudinary Configuration
    CLOUDINARY_CLOUD_NAME: str = ""
    CLOUDINARY_API_KEY: str = ""
    CLOUDINARY_API_SECRET: str = ""
    CLOUDINARY_URL: str = ""

    # LangSmith Observability Configuration
    LANGSMITH_TRACING: bool = False
    LANGSMITH_ENDPOINT: str = "https://api.smith.langchain.com"
    LANGSMITH_API_KEY: str = ""
    LANGSMITH_PROJECT: str = "Pathfinder"

    # Local accounts: a users table in the app database plus its own signed tokens,
    # so the app can be signed into without Supabase. Guest mode stays the default.
    LOCAL_AUTH_ENABLED: bool = True
    LOCAL_AUTH_SECRET: str = "pathfinder-local-dev-secret-change-me"
    LOCAL_AUTH_TOKEN_HOURS: int = 24 * 14

    # System 1 router. Off matches the original LangGraph app: a follow-up is revised or
    # answered by the planner, with no rules, Jev, or clarification gate in front.
    ROUTER_ENABLED: bool = False
    # Jev is off unless this is turned on and both the URL and the key are set.
    DECISION_ENABLED: bool = False
    DECISION_API_URL: str = ""
    DECISION_API_KEY: str = ""
    DECISION_MODEL: str = "jev-latest"
    DECISION_AUTH_PREFIX: str = "Bearer"
    DECISION_PATH: str = "/v1/systemone"
    DECISION_TIMEOUT_SECONDS: float = 8.0
    ROUTER_THRESHOLD: float = 0.8
    ROUTER_RULES_FIRST: bool = True

    # Optional live lookups on the plan and modify paths. Empty keys skip the call.
    SERPAPI_KEY: str = ""
    SERPAPI_KEY_POOL: str = ""
    ENABLE_HOTELS: bool = True
    ENABLE_FLIGHTS: bool = True
    ENABLE_ROUTES: bool = False
    ENABLE_YOUTUBE: bool = False
    ENABLE_NOTES: bool = False
    ENABLE_BILIBILI: bool = False
    ENABLE_ROUTE_MATRIX: bool = False
    ENABLE_TIMEZONE: bool = False
    ENABLE_ELEVATION: bool = False

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    @field_validator("CORS_ORIGINS", mode="before")
    @classmethod
    def parse_cors_origins(cls, v: Union[str, List[str]]) -> List[str]:
        if isinstance(v, str):
            v_trimmed = v.strip()
            if v_trimmed.startswith("[") and v_trimmed.endswith("]"):
                try:
                    v = json.loads(v_trimmed)
                except Exception:
                    v = v_trimmed.strip("[]").split(",")
            else:
                v = v_trimmed.split(",")
        # A browser's Origin header never has a trailing slash or quotes, and CORS needs an exact match:
        # "https://pathfinder-0.vercel.app/" pasted into Render blocked every request from the site.
        return [o for o in (str(origin).strip().strip("'\"").strip().rstrip("/") for origin in v) if o]


settings = Settings()

# Automatically sync LangSmith observability environment variables for LangGraph/LangChain runtime
import os

if settings.LANGSMITH_TRACING and settings.LANGSMITH_API_KEY:
    os.environ["LANGSMITH_TRACING"] = "true"
    os.environ["LANGCHAIN_TRACING_V2"] = "true"
    os.environ["LANGSMITH_ENDPOINT"] = settings.LANGSMITH_ENDPOINT
    os.environ["LANGSMITH_API_KEY"] = settings.LANGSMITH_API_KEY
    os.environ["LANGSMITH_PROJECT"] = settings.LANGSMITH_PROJECT

