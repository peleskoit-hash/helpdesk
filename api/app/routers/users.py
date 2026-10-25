from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..auth import CurrentUser, get_current_user, hash_password, is_admin, is_readonly_chiki_piki, verify_password
from ..db import get_db
from ..models import StatsCompletion, TelegramNotificationAccount, User
from ..routers.settings_users import _ensure_unique_login, _telegram_notification_display
from ..schemas import (
    AgentOut,
    AppearanceSessionOut,
    AppearanceSessionCreate,
    CompletionPeriodStats,
    CompletionStatsOut,
    SelfAccountChangeOut,
    SelfLoginUpdate,
    SelfPasswordUpdate,
    SelfProfileOut,
    StatusBoardAgentOut,
    TelegramNotificationSummaryOut,
    UserAvatarOut,
    UserPreferencesOut,
    UserPreferencesUpdate,
    UserStatusOut,
    UserStatusUpdate,
    dump_ticket_list_columns,
)
from ..services.assignment import assign_unassigned_fairly
from ..services.user_appearance import (
    APPEARANCE_FIELDS,
    AppearanceSessionExpired,
    accept_appearance_write,
    appearance_colors,
    appearance_revision,
    convert_legacy_appearance,
    COLOR_FIELDS,
    register_appearance_session,
)
from ..services.statistics_lifecycle import set_user_availability_status
from ..services.user_avatars import (
    MAX_AVATAR_BYTES,
    AvatarValidationError,
    avatar_url,
    delete_avatar_file,
    detect_avatar_type,
    media_type_for_kind,
    resolve_avatar_path,
    save_avatar_file,
    validate_avatar_upload,
)


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
        _error(404, "user_not_found", "Пользователь не найден")
    return user


def _real_user_model(db: Session, current_user: CurrentUser) -> User:
    user = db.execute(select(User).where(User.id == current_user.real_user_id)).scalar_one_or_none()
    if user is None:
        _error(404, "user_not_found", "Пользователь не найден")
    return user


def _status_out(user: User) -> UserStatusOut:
    return UserStatusOut(
        id=user.id,
        role=user.role,
        role_label={"admin": "Администратор", "agent": "Тех. поддержка", "trainee": "Стажёр", "chiki_piki": "Чики-пики"}.get(user.role, user.role),
        full_name=user.full_name,
        availability_status=user.availability_status or "offline",
    )


def _self_profile_out(user: User, notification_account: TelegramNotificationAccount | None) -> SelfProfileOut:
    avatar_path = (user.avatar_path or "").strip() or None
    return SelfProfileOut(
        id=user.id,
        full_name=user.full_name,
        login=user.login,
        role=user.role,
        legacy_ticket_count=int(user.legacy_ticket_count or 0),
        avatar_path=avatar_path,
        avatar_present=avatar_path is not None,
        avatar_url=avatar_url(user.id, avatar_path),
        telegram_notification=TelegramNotificationSummaryOut(
            bound=notification_account is not None,
            display=_telegram_notification_display(notification_account) if notification_account else None,
        ),
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


@router.get("/me", response_model=UserStatusOut)
def get_me(db: Session = Depends(get_db), current_user: CurrentUser = Depends(get_current_user)):
    return _status_out(_current_user_model(db, current_user))


@router.get("/me/profile", response_model=SelfProfileOut)
def get_my_profile(
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
):
    user = _real_user_model(db, current_user)
    notification_account = db.execute(
        select(TelegramNotificationAccount).where(TelegramNotificationAccount.bound_user_id == user.id)
    ).scalar_one_or_none()
    return _self_profile_out(user, notification_account)


@router.patch("/me/login", response_model=SelfAccountChangeOut)
def update_my_login(
    payload: SelfLoginUpdate,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
):
    user = _real_user_model(db, current_user)
    login = payload.login.strip()
    if not login:
        _error(422, "login_required", "Login обязателен")
    try:
        _ensure_unique_login(db, login, exclude_user_id=user.id)
    except HTTPException as exc:
        if exc.status_code == 409:
            _error(409, "login_already_used", "Этот логин уже используется")
        raise
    user.login = login
    user.updated_at = datetime.utcnow()
    db.commit()
    return SelfAccountChangeOut(message="Данные изменены. Войдите снова.")


@router.patch("/me/password", response_model=SelfAccountChangeOut)
def update_my_password(
    payload: SelfPasswordUpdate,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
):
    user = _real_user_model(db, current_user)
    if not verify_password(payload.current_password, user.password_hash):
        _error(400, "current_password_invalid", "Текущий пароль указан неверно")
    if payload.new_password != payload.new_password_confirmation:
        _error(400, "password_confirmation_mismatch", "Новый пароль и подтверждение не совпадают")
    user.password_hash = hash_password(payload.new_password)
    user.updated_at = datetime.utcnow()
    db.commit()
    return SelfAccountChangeOut(message="Данные изменены. Войдите снова.")


@router.put("/me/avatar", response_model=UserAvatarOut)
async def update_my_avatar(
    avatar: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
):
    user = _real_user_model(db, current_user)
    content = await avatar.read(MAX_AVATAR_BYTES + 1)
    if isinstance(content, str):
        content = content.encode("utf-8")
    try:
        kind, _media_type = validate_avatar_upload(content, avatar.content_type)
        new_avatar_path = save_avatar_file(content, kind)
    except AvatarValidationError as exc:
        _error(exc.status_code, exc.code, exc.message)

    old_avatar_path = user.avatar_path
    user.avatar_path = new_avatar_path
    user.updated_at = datetime.utcnow()
    try:
        db.commit()
    except Exception:
        delete_avatar_file(new_avatar_path)
        raise
    if old_avatar_path != new_avatar_path:
        delete_avatar_file(old_avatar_path)
    return UserAvatarOut(avatar_present=True, avatar_url=avatar_url(user.id, new_avatar_path))


@router.delete("/me/avatar", response_model=UserAvatarOut)
def delete_my_avatar(
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
):
    user = _real_user_model(db, current_user)
    old_avatar_path = user.avatar_path
    if old_avatar_path:
        user.avatar_path = None
        user.updated_at = datetime.utcnow()
        db.commit()
        delete_avatar_file(old_avatar_path)
    return UserAvatarOut(avatar_present=False, avatar_url=None)


@router.get("/{user_id}/avatar")
def get_user_avatar(
    user_id: str,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
):
    user = db.execute(select(User).where(User.id == user_id)).scalar_one_or_none()
    if user is None:
        _error(404, "user_not_found", "Пользователь не найден")
    if user.id != current_user.real_user_id and not current_user.is_real_admin:
        _error(403, "forbidden", "Недостаточно прав для просмотра аватара")

    path = resolve_avatar_path(user.avatar_path)
    if path is None or not path.is_file():
        _error(404, "avatar_not_found", "Аватар не найден")
    kind = detect_avatar_type(path.read_bytes()[:16])
    if kind is None:
        _error(404, "avatar_not_found", "Аватар не найден")
    return FileResponse(str(path), media_type=media_type_for_kind(kind))


def _preferences_out(user: User) -> UserPreferencesOut:
    return UserPreferencesOut(
        appearance_revision=appearance_revision(user),
        send_by_enter=bool(user.send_by_enter),
        color_enabled=bool(user.color_enabled),
        task_panel_enabled=bool(user.task_panel_enabled),
        theme_preference=user.theme_preference if user.theme_preference in {"light", "dark"} else "light",
        **appearance_colors(user),
        accent_preference=user.accent_preference,
        accent_intensity_light=user.accent_intensity_light,
        accent_intensity_dark=user.accent_intensity_dark,
        ticket_list_columns=dump_ticket_list_columns(user.ticket_list_columns_json),
        client_card_collapsed_blocks=user.client_card_collapsed_blocks_json or {},
    )


@router.get("/me/preferences", response_model=UserPreferencesOut)
def get_my_preferences(
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
):
    return _preferences_out(_real_user_model(db, current_user))


@router.post("/me/preferences/appearance-session", response_model=AppearanceSessionOut)
def create_my_appearance_session(
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
    payload: AppearanceSessionCreate | None = None,
):
    initial = payload.preferences.model_dump(exclude_unset=True) if payload else {}
    if payload and (not initial or set(initial) - set(APPEARANCE_FIELDS)):
        _error(422, "invalid_initial_appearance", "Only appearance preferences may initialize a session")
    user = _real_user_model(db, current_user)
    writer_id = register_appearance_session(
        db, user, initial, payload.sequence if payload else 0, payload.expected_revision if payload else None,
    )
    return AppearanceSessionOut(writer_id=writer_id, preferences=_preferences_out(user) if payload else None)


@router.patch("/me/preferences", response_model=UserPreferencesOut)
def update_my_preferences(
    payload: UserPreferencesUpdate,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
):
    user = _real_user_model(db, current_user)
    updates = payload.model_dump(exclude_unset=True)
    updates.pop("appearance_write", None)
    has_appearance = any(field in updates for field in APPEARANCE_FIELDS)
    if has_appearance and payload.appearance_write is None:
        _error(422, "appearance_write_required", "Appearance settings require appearance_write")
    if payload.appearance_write and has_appearance:
        try:
            accepted = accept_appearance_write(
                db, user, str(payload.appearance_write.writer_id), payload.appearance_write.sequence,
            )
        except AppearanceSessionExpired:
            preferences = _preferences_out(user).model_dump(mode="json")
            db.rollback()
            raise HTTPException(status_code=409, detail={
                "error": {"code": "appearance_session_expired"}, "preferences": preferences,
            })
        if not accepted:
            updates = {field: value for field, value in updates.items() if field not in APPEARANCE_FIELDS}
    if "send_by_enter" in updates:
        user.send_by_enter = bool(updates["send_by_enter"])
    if "color_enabled" in updates:
        user.color_enabled = bool(updates["color_enabled"])
    if "task_panel_enabled" in updates:
        user.task_panel_enabled = bool(updates["task_panel_enabled"])
    if "theme_preference" in updates:
        user.theme_preference = updates["theme_preference"]
    for field in ("accent_preference", "accent_intensity_light", "accent_intensity_dark"):
        if field in updates:
            setattr(user, field, updates[field])
    convert_legacy_appearance(user, updates)
    for field in COLOR_FIELDS:
        if field in updates:
            setattr(user, field, updates[field])
    if "client_card_collapsed_blocks" in updates:
        user.client_card_collapsed_blocks_json = {
            **(user.client_card_collapsed_blocks_json or {}), **updates["client_card_collapsed_blocks"],
        }
    if "ticket_list_columns" in updates:
        user.ticket_list_columns_json = dump_ticket_list_columns(updates["ticket_list_columns"])
    if updates:
        user.updated_at = datetime.utcnow()
    if updates or payload.appearance_write:
        db.commit()
        db.refresh(user)
    return _preferences_out(user)


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

    completions = db.execute(
        select(StatsCompletion).where(
            StatsCompletion.company_id == user.company_id,
            StatsCompletion.completed_by_user_id == user.id,
            StatsCompletion.completed_at.is_not(None),
            StatsCompletion.completed_at >= start_min,
            StatsCompletion.is_completed.is_(True),
            StatsCompletion.object_type.in_(["ticket", "task"]),
        )
    ).scalars().all()

    def build_period_stats(period_start: datetime | None = None) -> CompletionPeriodStats:
        tasks = 0
        tickets_count = 0
        for item in completions:
            counted_at = item.completed_at
            if counted_at is None:
                continue
            if counted_at.tzinfo is None:
                counted_at = counted_at.replace(tzinfo=ZoneInfo("UTC"))
            counted_msk = counted_at.astimezone(MSK_TZ)
            if period_start and counted_msk < period_start:
                continue
            if item.object_type == "task":
                tasks += 1
            elif item.object_type == "ticket":
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
        select(User).where(User.role.in_(["agent", "trainee"]), User.is_active.is_(True)).order_by(User.created_at.asc(), User.id.asc())
    ).scalars().all()
    return [
        AgentOut(
            id=agent.id,
            full_name=agent.full_name,
            role=agent.role,
            role_label={"agent": "Тех. поддержка", "trainee": "Стажёр"}.get(agent.role, agent.role),
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
        "agent": "Тех. поддержка",
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


@router.get("/status-board", response_model=list[StatusBoardAgentOut])
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
        StatusBoardAgentOut(
            id=user.id,
            full_name=user.full_name,
            role=user.role,
            role_label={"agent": "Тех. поддержка", "trainee": "Стажёр"}.get(user.role, user.role),
            availability_status=user.availability_status or "offline",
            updated_at=user.updated_at,
            avatar_present=bool((user.avatar_path or "").strip()),
            avatar_url=avatar_url(user.id, user.avatar_path),
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
        _error(403, "readonly_role", "Роль Чики-пики не может менять статус")
    set_user_availability_status(db, user, payload.availability_status, now=datetime.utcnow())
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
        _error(404, "user_not_found", "Пользователь не найден")
    if not user.is_active:
        _error(400, "user_inactive", "Нельзя изменить статус неактивного пользователя")
    if user.role not in {"agent", "trainee"}:
        _error(400, "invalid_role", "Статус можно менять только сотрудникам поддержки")
    set_user_availability_status(db, user, payload.availability_status, now=datetime.utcnow())
    if user.role == "agent" and payload.availability_status == "online":
        db.flush()
        assign_unassigned_fairly(db, user.company_id, notify_client=True)
    db.commit()
    db.refresh(user)
    return _status_out(user)
