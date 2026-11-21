import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import admin, auth, exports, feedback, health, llm_keys, places, sketch_notes, trips, upload
from app.core.config import settings
from app.core.database import close_db, init_db

logging.basicConfig(level=getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO))
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info(f"Starting {settings.APP_NAME} (env={settings.APP_ENV}, port={settings.PORT})...")
    await init_db()
    yield
    logger.info(f"Shutting down {settings.APP_NAME}...")
    await close_db()



app = FastAPI(
    title=settings.APP_NAME,
    description="Backend gateway service for Pathfinder agentic travel-planning workspace",
    version=settings.VERSION,
    lifespan=lifespan,
    debug=settings.DEBUG,
)

# CORS configuration strictly restricted to configured origins
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["*"],
)

# Include Routers
app.include_router(health.router)
app.include_router(feedback.router)
app.include_router(auth.router)
app.include_router(admin.router)
app.include_router(llm_keys.router)
app.include_router(trips.router)
app.include_router(upload.router)
app.include_router(places.router)
app.include_router(exports.router)
app.include_router(sketch_notes.router)

if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app.main:app", host="0.0.0.0", port=settings.PORT, reload=settings.DEBUG)

