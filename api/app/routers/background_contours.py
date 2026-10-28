from fastapi import APIRouter, Depends, File, Form, Response, UploadFile
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from ..auth import CurrentUser, get_current_user
from ..db import get_db
from ..schemas import BackgroundContourComposition, BackgroundContourListOut, BackgroundContourOut
from ..services import background_contours as contours


router = APIRouter(prefix="/api/v1/background-contours", tags=["background-contours"])


def _error(exc: contours.ContourError):
    return JSONResponse(status_code=exc.status_code, content={"detail": {"error": {
        "code": exc.code, "message": str(exc),
    }}})


@router.get("", response_model=BackgroundContourListOut)
def list_contours(db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    try:
        return {"items": contours.list_contours(db, user)}
    except contours.ContourError as exc:
        return _error(exc)


@router.post("", response_model=BackgroundContourOut, status_code=201)
def create_contour(
    name: str = Form(..., min_length=1, max_length=120),
    rotation: float = Form(0, ge=0, le=360, allow_inf_nan=False),
    rings: int = Form(0, ge=0, le=2),
    distance: float = Form(20, ge=0, le=200, allow_inf_nan=False, description="Percent of contour size"),
    base_scale: float = Form(100, ge=50, le=200, allow_inf_nan=False, description="Percent"),
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    try:
        composition = BackgroundContourComposition(rotation=rotation, rings=rings, distance=distance, base_scale=base_scale)
        return contours.create_contour(db, user, name, file.file, composition)
    except contours.ContourError as exc:
        return _error(exc)


@router.get("/{contour_id}/image")
def get_contour_image(contour_id: str, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    try:
        content, kind = contours.image_content(db, contour_id)
        return Response(content, media_type=f"image/{kind}", headers={
            "Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff",
        })
    except contours.ContourError as exc:
        return _error(exc)


@router.delete("/{contour_id}", status_code=204)
def delete_contour(contour_id: str, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    # User confirmation belongs to the future UI; authorization is always server-side.
    try:
        contours.delete_contour(db, user, contour_id)
        return Response(status_code=204)
    except contours.ContourError as exc:
        return _error(exc)
