"""Administrator-only endpoints.

The admin flag lives on the local account (models/user.py). These routes exist so
the flag has something to gate; there is no admin screen in the UI yet.
"""
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import AuthenticatedUser, get_current_user
from app.core.database import get_db
from app.models.user import User

router = APIRouter(prefix="/api/admin", tags=["admin"])


async def require_admin(current_user: AuthenticatedUser = Depends(get_current_user)) -> AuthenticatedUser:
    """Reject anyone whose token does not carry the admin flag."""
    if not current_user.user_metadata.get("is_admin"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Administrator access required.",
        )
    return current_user


@router.get("/users")
async def list_users(
    _admin: AuthenticatedUser = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """List the app's local accounts."""
    accounts = (await db.execute(select(User).order_by(User.created_at))).scalars().all()
    return {
        "count": len(accounts),
        "users": [
            {
                "id": account.id,
                "email": account.email,
                "full_name": account.full_name,
                "is_admin": account.is_admin,
                "created_at": account.created_at.isoformat() if account.created_at else None,
            }
            for account in accounts
        ],
    }
