import base64
import logging
from typing import Dict, Any
from fastapi import HTTPException, UploadFile, status
import cloudinary
import cloudinary.uploader

from app.core.config import settings

logger = logging.getLogger(__name__)

MAX_FILE_SIZE = 10 * 1024 * 1024  # 10 MB
ALLOWED_CONTENT_TYPES = {
    "image/jpeg",
    "image/png",
    "image/webp",
    "image/gif",
    "image/avif",
    "image/svg+xml",
}


class UploadService:
    def __init__(self):
        self._is_configured = False

    def _configure_cloudinary(self) -> bool:
        """Configure Cloudinary credentials if available."""
        if settings.CLOUDINARY_URL:
            cloudinary.config(cloudinary_url=settings.CLOUDINARY_URL)
            self._is_configured = True
            return True

        if (
            settings.CLOUDINARY_CLOUD_NAME
            and settings.CLOUDINARY_API_KEY
            and settings.CLOUDINARY_API_SECRET
        ):
            cloudinary.config(
                cloud_name=settings.CLOUDINARY_CLOUD_NAME,
                api_key=settings.CLOUDINARY_API_KEY,
                api_secret=settings.CLOUDINARY_API_SECRET,
                secure=True,
            )
            self._is_configured = True
            return True

        self._is_configured = False
        return False

    async def upload_avatar(self, file: UploadFile) -> Dict[str, Any]:
        """
        Upload user profile picture to Cloudinary.
        Falls back gracefully to a data URL if Cloudinary is not configured.
        """
        # 1. Validate MIME type
        content_type = file.content_type or ""
        if content_type not in ALLOWED_CONTENT_TYPES and not content_type.startswith("image/"):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Unsupported image file format '{content_type}'. Please upload JPEG, PNG, WebP, or GIF.",
            )

        # 2. Read and validate size
        content = await file.read()
        if len(content) == 0:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Uploaded file is empty.",
            )

        if len(content) > MAX_FILE_SIZE:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"File exceeds maximum allowed size of {MAX_FILE_SIZE // (1024 * 1024)}MB.",
            )

        # 3. Check Cloudinary Configuration
        is_ready = self._configure_cloudinary()

        if is_ready:
            try:
                # Upload to Cloudinary with square avatar face-gravity crop
                result = cloudinary.uploader.upload(
                    content,
                    folder="pathfinder/avatars",
                    transformation=[
                        {"width": 400, "height": 400, "crop": "fill", "gravity": "face"}
                    ],
                    overwrite=True,
                    resource_type="image",
                )
                secure_url = result.get("secure_url") or result.get("url")
                public_id = result.get("public_id", "")
                logger.info(f"Successfully uploaded avatar to Cloudinary: public_id={public_id}")
                return {
                    "url": secure_url,
                    "public_id": public_id,
                    "provider": "cloudinary",
                }
            except Exception as e:
                logger.error(f"Cloudinary upload failed: {e}", exc_info=True)
                raise HTTPException(
                    status_code=status.HTTP_502_BAD_GATEWAY,
                    detail=f"Cloudinary upload error: {str(e)}",
                )

        # 4. Fallback: Data URI for development/demo when keys are not yet configured in .env
        logger.info("Cloudinary credentials not configured; serving base64 data URI fallback.")
        b64_str = base64.b64encode(content).decode("utf-8")
        data_uri = f"data:{content_type};base64,{b64_str}"
        return {
            "url": data_uri,
            "public_id": "dev_fallback",
            "provider": "fallback",
        }


upload_service = UploadService()
