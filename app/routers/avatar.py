# app/routers/avatar.py
"""
Patient profile pictures.

Mounted under /patients. Files are stored on the server filesystem
(uploads/avatars/{patient_id}.jpg — gitignored), normalised with Pillow
(EXIF-rotated, RGB, max 512px, JPEG) so the app always gets a small square-ish
image regardless of what the phone uploads.

Access: a patient may upload/delete only her own picture; viewing follows the
same scope as the profile (patient self, or her own hospital's dashboard).
"""

import logging
from pathlib import Path
from uuid import UUID

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.utils.access import get_patient_scoped
from app.utils.auth import get_current_user

router = APIRouter()
logger = logging.getLogger(__name__)

AVATAR_DIR = Path(__file__).resolve().parent.parent.parent / "uploads" / "avatars"
SUPPORTED_TYPES = {"jpg", "jpeg", "png", "webp"}
MAX_BYTES = 5 * 1024 * 1024
MAX_SIDE = 512


def _avatar_path(patient_id) -> Path:
    return AVATAR_DIR / f"{patient_id}.jpg"


@router.post(
    "/{patient_id}/avatar",
    summary="Upload profile picture",
    description=(
        "Patient-only, own record only. Multipart field `image` (jpg/png/webp, "
        "≤5MB). The image is auto-rotated, converted to JPEG and resized to "
        "max 512px. Re-uploading replaces the previous picture."
    ),
)
async def upload_avatar(
    patient_id: UUID,
    image: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    patient = get_patient_scoped(patient_id, current_user, db)
    if current_user.get("type") != "patient":
        raise HTTPException(status_code=403, detail="Patients only")

    ext = (image.filename or "photo.jpg").rsplit(".", 1)[-1].lower()
    if ext not in SUPPORTED_TYPES:
        raise HTTPException(
            status_code=422,
            detail=f"Unsupported image format '{ext}' — use one of {sorted(SUPPORTED_TYPES)}",
        )
    raw = await image.read()
    if not raw:
        raise HTTPException(status_code=422, detail="Empty image file")
    if len(raw) > MAX_BYTES:
        raise HTTPException(status_code=422, detail="Image too large (max 5MB)")

    try:
        import io
        from PIL import Image, ImageOps

        img = Image.open(io.BytesIO(raw))
        img = ImageOps.exif_transpose(img)          # respect phone orientation
        img = img.convert("RGB")
        img.thumbnail((MAX_SIDE, MAX_SIDE))
        AVATAR_DIR.mkdir(parents=True, exist_ok=True)
        img.save(_avatar_path(patient.id), "JPEG", quality=85)
    except HTTPException:
        raise
    except Exception:  # noqa: BLE001 — corrupt/non-image bytes
        logger.exception("Avatar processing failed | patient=%s", patient.id)
        raise HTTPException(status_code=422, detail="Could not read the image — is it a valid photo?")

    return {"uploaded": True, "avatar_url": f"/patients/{patient.id}/avatar"}


@router.get(
    "/{patient_id}/avatar",
    summary="Get profile picture",
    description=(
        "Returns the profile picture as JPEG. Same scope as the profile: the "
        "patient herself, or her hospital. 404 if none uploaded — the app "
        "shows the initial-letter avatar instead."
    ),
)
def get_avatar(
    patient_id: UUID,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    patient = get_patient_scoped(patient_id, current_user, db)
    path = _avatar_path(patient.id)
    if not path.is_file():
        raise HTTPException(status_code=404, detail="No profile picture")
    return FileResponse(path, media_type="image/jpeg",
                        headers={"Cache-Control": "private, max-age=300"})


@router.delete(
    "/{patient_id}/avatar",
    summary="Remove profile picture",
    description="Patient-only, own record only. Deletes the picture; the app falls back to the initial avatar.",
)
def delete_avatar(
    patient_id: UUID,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    patient = get_patient_scoped(patient_id, current_user, db)
    if current_user.get("type") != "patient":
        raise HTTPException(status_code=403, detail="Patients only")
    path = _avatar_path(patient.id)
    if path.is_file():
        path.unlink()
    return {"deleted": True}
