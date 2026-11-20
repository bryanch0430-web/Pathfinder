from fastapi import APIRouter
from app.core.config import settings
from app.core.database import check_database_connection

router = APIRouter()


@router.get("/")
async def get_root():
    return {
        "name": settings.APP_NAME,
        "environment": settings.APP_ENV,
        "version": settings.VERSION,
        "status": "ok",
    }


@router.get("/api/health")
async def get_health():
    db_health = await check_database_connection()
    return {
        "status": "healthy" if db_health["status"] == "connected" else "degraded",
        "app_name": settings.APP_NAME,
        "environment": settings.APP_ENV,
        "version": settings.VERSION,
        "database": db_health,
    }

