"""Uvicorn entry point: `uvicorn app.main:app --app-dir backend --port 8100`."""

from app.interface.api import app, create_app

__all__ = ["app", "create_app"]
