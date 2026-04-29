from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..auth import CurrentUser, get_current_user
from ..db import get_db
from ..models import User
from ..schemas import AgentOut, UserStatusOut, UserStatusUpdate
from ..services.assignment import assign_unassigned_fairly


router = APIRouter(prefix="/api/v1/users", tags=["users"])


def _error(status_code: int, code: str, message: str):
    raise HTTPException(status_code=status_code, detail={"error": {"code": code, "message": message, "details": {}}})


def _current_user_model(db: Session, current_user: CurrentUser) -> User:
    user = db.execute(select(User).where(User.id == current_user.id)).scalar_one_or_none()
    if user is None:
        _error(404, "user_not_found", "Пользователь не найден")
    return user


def _status_out(user: User) -> UserStatusOut:
    return UserStatusOut(
        id=user.id,
        role=user.role,
        full_name=user.full_name,
        availability_status=user.availability_status or "offline",
    )


@router.get("/me", response_model=UserStatusOut)
def get_me(db: Session = Depends(get_db), current_user: CurrentUser = Depends(get_current_user)):
    return _status_out(_current_user_model(db, current_user))


@router.get("/agents", response_model=list[AgentOut])
def list_agents(db: Session = Depends(get_db), current_user: CurrentUser = Depends(get_current_user)):
    agents = db.execute(
        select(User).where(User.role == "agent", User.is_active.is_(True)).order_by(User.full_name.asc())
    ).scalars().all()
    return [
        AgentOut(id=agent.id, full_name=agent.full_name, availability_status=agent.availability_status or "offline")
        for agent in agents
    ]


@router.patch("/me/status", response_model=UserStatusOut)
def update_my_status(
    payload: UserStatusUpdate,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
):
    user = _current_user_model(db, current_user)
    user.availability_status = payload.availability_status
    if payload.availability_status == "online":
        assign_unassigned_fairly(db, user.company_id, notify_client=True)
    db.commit()
    db.refresh(user)
    return _status_out(user)
