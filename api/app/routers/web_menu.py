from typing import Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..db import get_db
from ..services.web_auth import CurrentWebClient, get_current_web_client
from ..services.web_menu import read_client_menu
from .web_auth import WebAuthRoute

router = APIRouter(prefix="/api/v1/web", tags=["web-menu"], route_class=WebAuthRoute)


class MenuButton(BaseModel):
    id: str
    label: str
    workflow_type: str
    style: Literal["primary", "success", "danger"] | None = None
    children: list[list["MenuButton"]] | None = None


class WebMenuOut(BaseModel):
    menu_type: str
    rows: list[list[MenuButton]]


@router.get("/menu", response_model=WebMenuOut)
def menu(client: CurrentWebClient = Depends(get_current_web_client), db: Session = Depends(get_db)):
    return read_client_menu(db, client.id)
