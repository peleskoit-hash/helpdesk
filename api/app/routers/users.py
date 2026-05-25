from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import and_, select
from sqlalchemy.orm import Session

from ..auth import CurrentUser, get_current_user, is_admin, is_readonly_chiki_piki
from ..db import get_db
from ..models import Ticket, TicketTaskKind, TicketWorkflowType, User
from ..schemas import AgentOut, CompletionPeriodStats, CompletionStatsOut, UserStatusOut, UserStatusUpdate
from ..services.assignment import assign_unassigned_fairly


router = APIRouter(prefix="/api/v1/users", tags=["users"])
MSK_TZ = ZoneInfo("Europe/Moscow")


def _error(status_code: int, code: str, message: str):
    raise HTTPException(status_code=status_code, detail={"error": {"code": code, "message": message, "details": {}}})


def _current_user_model(db: Session, current_user: CurrentUser) -> User:
    user = db.execute(select(User).where(User.id == current_user.id)).scalar_one_or_none()
    if user is None and current_user.role == "admin":
        user = db.execute(
            select(User).where(User.role == "admin", User.is_active.is_(True)).order_by(User.created_at.asc()).limit(1)
        ).scalar_one_or_none()
    if user is None:
        _error(404, "user_not_found", "???????????? ?? ??????")
    return user


def _status_out(user: User) -> UserStatusOut:
    return UserStatusOut(
        id=user.id,
        role=user.role,
        role_label={"admin": "Администратор", "agent": "Сотрудник", "trainee": "Стажёр", "chiki_piki": "Чики-пики"}.get(user.role, user.role),
        full_name=user.full_name,
        availability_status=user.availability_status or "offline",
    )


def _completion_period_bounds() -> dict[str, datetime]:
    now = datetime.now(MSK_TZ)
    day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    week_start = day_start - timedelta(days=day_start.weekday())
    month_start = day_start.replace(day=1)
    year_start = day_start.replace(month=1, day=1)
    return {
        "day": day_start,
        "week": week_start,
        "month": month_start,
        "year": year_start,
    }


def _is_task_ticket(ticket: Ticket) -> bool:
    workflow_type = ticket.workflow_type or TicketWorkflowType.TICKET.value
    if workflow_type in {TicketWorkflowType.TASK.value, TicketWorkflowType.AUTO.value}:
        if (ticket.task_kind or TicketTaskKind.NORMAL.value) == TicketTaskKind.GROUP_PARENT.value:
            return False
        return True
    return False


@router.get("/me", response_model=UserStatusOut)
def get_me(db: Session = Depends(get_db), current_user: CurrentUser = Depends(get_current_user)):
    return _status_out(_current_user_model(db, current_user))


@router.get("/me/completion-stats", response_model=CompletionStatsOut)
def get_my_completion_stats(
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
):
    user = _current_user_model(db, current_user)
    display_name = (user.full_name or "").strip() or (user.login or "")
    role = user.role or current_user.role

    if role not in {"agent", "trainee"}:
        return CompletionStatsOut(
            display_name=display_name,
            role=role,
            total=None,
            calendar=None,
            legacy=int(user.legacy_ticket_count or 0),
        )

    period_starts = _completion_period_bounds()
    start_min = period_starts["year"]

    tickets = db.execute(
        select(Ticket).where(
            and_(
                Ticket.completed_by_user_id == user.id,
                Ticket.completed_counted_at.is_not(None),
                Ticket.completed_counted_at >= start_min,
            )
        )
    ).scalars().all()

    def build_period_stats(period_start: datetime | None = None) -> CompletionPeriodStats:
        tasks = 0
        tickets_count = 0
        for item in tickets:
            counted_at = item.completed_counted_at
            if counted_at is None:
                continue
            if counted_at.tzinfo is None:
                counted_at = counted_at.replace(tzinfo=ZoneInfo("UTC"))
            counted_msk = counted_at.astimezone(MSK_TZ)
            if period_start and counted_msk < period_start:
                continue
            if _is_task_ticket(item):
                tasks += 1
            elif (item.workflow_type or TicketWorkflowType.TICKET.value) == TicketWorkflowType.TICKET.value:
                tickets_count += 1
        return CompletionPeriodStats(total=tasks + tickets_count, tasks=tasks, tickets=tickets_count)

    return CompletionStatsOut(
        display_name=display_name,
        role=role,
        total=build_period_stats(None),
        calendar={
            "year": build_period_stats(period_starts["year"]),
            "month": build_period_stats(period_starts["month"]),
            "week": build_period_stats(period_starts["week"]),
            "day": build_period_stats(period_starts["day"]),
        },
        legacy=int(user.legacy_ticket_count or 0),
    )


@router.get("/agents", response_model=list[AgentOut])
def list_agents(db: Session = Depends(get_db), current_user: CurrentUser = Depends(get_current_user)):
    agents = db.execute(
        select(User).where(User.role.in_(["agent", "trainee"]), User.is_active.is_(True)).order_by(User.full_name.asc())
    ).scalars().all()
    return [
        AgentOut(
            id=agent.id,
            full_name=agent.full_name,
            role=agent.role,
            role_label={"agent": "Сотрудник", "trainee": "Стажёр"}.get(agent.role, agent.role),
            availability_status=agent.availability_status or "offline",
            updated_at=agent.updated_at,
        )
        for agent in agents
    ]


@router.get("/by-role", response_model=list[AgentOut])
def list_users_by_role(
    role: str = Query(..., pattern="^(admin|agent|trainee|chiki_piki)$"),
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
):
    users = db.execute(
        select(User)
        .where(User.role == role, User.is_active.is_(True))
        .order_by(User.full_name.asc())
    ).scalars().all()
    role_label_map = {
        "admin": "Администратор",
        "agent": "Сотрудник",
        "trainee": "Стажёр",
        "chiki_piki": "Чики-пики",
    }
    return [
        AgentOut(
            id=user.id,
            full_name=user.full_name,
            role=user.role,
            role_label=role_label_map.get(user.role, user.role),
            availability_status=user.availability_status or "offline",
            updated_at=user.updated_at,
        )
        for user in users
    ]


@router.get("/status-board", response_model=list[AgentOut])
def status_board(db: Session = Depends(get_db), current_user: CurrentUser = Depends(get_current_user)):
    if not is_admin(current_user):
        _error(403, "forbidden", "Только администратор может просматривать доску статусов")
    users = db.execute(
        select(User)
        .where(User.role.in_(["agent", "trainee"]), User.is_active.is_(True))
        .order_by(User.role.asc(), User.full_name.asc())
    ).scalars().all()
    users.sort(key=lambda u: (0 if u.role == "agent" else 1, (u.full_name or "").lower()))
    return [
        AgentOut(
            id=user.id,
            full_name=user.full_name,
            role=user.role,
            role_label={"agent": "Сотрудник", "trainee": "Стажёр"}.get(user.role, user.role),
            availability_status=user.availability_status or "offline",
            updated_at=user.updated_at,
        )
        for user in users
    ]


@router.patch("/me/status", response_model=UserStatusOut)
def update_my_status(
    payload: UserStatusUpdate,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
):
    user = _current_user_model(db, current_user)
    if is_readonly_chiki_piki(current_user):
        _error(403, "readonly_role", "???? ????-???? ?? ????? ?????? ??????")
    user.availability_status = payload.availability_status
    user.updated_at = datetime.utcnow()
    if user.role == "agent" and payload.availability_status == "online":
        db.flush()
        assign_unassigned_fairly(db, user.company_id, notify_client=True)
    db.commit()
    db.refresh(user)
    return _status_out(user)


@router.patch("/{user_id}/status", response_model=UserStatusOut)
def update_user_status(
    user_id: str,
    payload: UserStatusUpdate,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
):
    if not is_admin(current_user):
        _error(403, "forbidden", "Только администратор может менять статусы сотрудников")
    user = db.execute(select(User).where(User.id == user_id)).scalar_one_or_none()
    if user is None:
        _error(404, "user_not_found", "???????????? ?? ??????")
    if not user.is_active:
        _error(400, "user_inactive", "?????? ???????? ?????? ??????????? ????????????")
    if user.role not in {"agent", "trainee"}:
        _error(400, "invalid_role", "?????? ????? ?????? ?????? ??????????? ?????????")
    user.availability_status = payload.availability_status
    user.updated_at = datetime.utcnow()
    if user.role == "agent" and payload.availability_status == "online":
        db.flush()
        assign_unassigned_fairly(db, user.company_id, notify_client=True)
    db.commit()
    db.refresh(user)
    return _status_out(user)



