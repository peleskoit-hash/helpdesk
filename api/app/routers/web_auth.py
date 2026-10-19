from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from pydantic import BaseModel, Field, SecretStr
from sqlalchemy.orm import Session

from ..config import settings
from ..db import get_db
from ..models import WebClientSession
from ..services import web_auth

class WebAuthRoute(APIRoute):
    def get_route_handler(self):
        handler = super().get_route_handler()

        async def safe_handler(request):
            try:
                response = await handler(request)
            except RequestValidationError:
                # FastAPI's default validation details can echo a password/body.
                response = JSONResponse(status_code=422, content={"detail": "Invalid WEB authentication request"})
            except HTTPException as exc:
                exc.headers = {**(exc.headers or {}), "Cache-Control": "no-store"}
                raise
            response.headers["Cache-Control"] = "no-store"
            return response

        return safe_handler


router = APIRouter(prefix="/api/v1/web/auth", tags=["web-auth"], route_class=WebAuthRoute)


class LoginPayload(BaseModel):
    login: str = Field(max_length=255)
    password: SecretStr = Field(max_length=1024)


class WebClientOut(BaseModel):
    client_id: str


@router.post("/login", response_model=WebClientOut, dependencies=[Depends(web_auth.require_web_csrf)])
def login(payload: LoginPayload, request: Request, response: Response, db: Session = Depends(get_db)):
    token, client_id = web_auth.login(db, payload.login, payload.password.get_secret_value(),
                                     request.client.host if request.client else "unknown")
    response.set_cookie(web_auth.COOKIE_NAME, token, httponly=True, samesite="strict",
                        secure=settings.is_prod or settings.session_cookie_secure, path=web_auth.COOKIE_PATH)
    response.headers["Cache-Control"] = "no-store"
    return WebClientOut(client_id=client_id)


@router.post("/logout", status_code=204)
def logout(response: Response, client: web_auth.CurrentWebClient = Depends(web_auth.get_current_web_client),
           db: Session = Depends(get_db)):
    session = db.get(WebClientSession, client.session_hash)
    if session is not None:
        db.delete(session)
    db.commit()
    response.delete_cookie(web_auth.COOKIE_NAME, path=web_auth.COOKIE_PATH,
                           httponly=True, samesite="strict",
                           secure=settings.is_prod or settings.session_cookie_secure)
    response.headers["Cache-Control"] = "no-store"


@router.get("/me", response_model=WebClientOut)
def me(response: Response, client: web_auth.CurrentWebClient = Depends(web_auth.get_current_web_client)):
    response.headers["Cache-Control"] = "no-store"
    return WebClientOut(client_id=client.id)
