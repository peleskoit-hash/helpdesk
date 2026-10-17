from hashlib import sha256

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy.orm import Session

from ..auth import CurrentUser, get_current_user
from ..db import get_db
from ..services.ticket_client_avatars import contact_for_ticket, full_avatar_for_view
from .tickets import _get_ticket_or_404


router = APIRouter(prefix="/api/v1/tickets", tags=["tickets"])
_PRIVATE_HEADERS = {"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"}


@router.get("/{ticket_id}/client-avatar/thumbnail")
def thumbnail(ticket_id: str, request: Request, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    ticket = _get_ticket_or_404(db, ticket_id, user)
    contact = contact_for_ticket(db, ticket)
    if contact is None or not contact.avatar_thumbnail:
        raise HTTPException(404, "Изображение недоступно", headers=_PRIVATE_HEADERS)
    etag = '"' + sha256(contact.avatar_thumbnail).hexdigest() + '"'
    headers = {**_PRIVATE_HEADERS, "Cache-Control": "private, no-cache", "ETag": etag}
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers=headers)
    return Response(contact.avatar_thumbnail, media_type="image/webp", headers=headers)


@router.get("/{ticket_id}/client-avatar/full")
def full_photo(ticket_id: str, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    ticket = _get_ticket_or_404(db, ticket_id, user)
    image = full_avatar_for_view(contact_for_ticket(db, ticket))
    if image is None:
        raise HTTPException(404, "Изображение недоступно", headers=_PRIVATE_HEADERS)
    content, media_type = image
    return Response(content, media_type=media_type, headers=_PRIVATE_HEADERS)
