from fastapi import APIRouter, Depends, File, UploadFile, status

from app.core.auth import AuthenticatedUser, get_current_user
from app.services.upload import upload_service

router = APIRouter(prefix="/api/upload", tags=["upload"])


@router.post("/avatar", status_code=status.HTTP_200_OK)
async def upload_avatar(
    file: UploadFile = File(...),
    current_user: AuthenticatedUser = Depends(get_current_user),
):
    """
    Upload a user profile picture to Cloudinary.
    Requires a verified Supabase user to avoid unauthenticated cloud uploads.
    """
    return await upload_service.upload_avatar(file)
