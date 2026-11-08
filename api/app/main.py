import asyncio
from datetime import datetime, timezone
from pathlib import Path

from fastapi import Depends, FastAPI, Form, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session
from starlette.middleware.sessions import SessionMiddleware

from .auth import (
    CurrentUser,
    authenticate_user,
    get_current_user,
    get_optional_current_user,
    is_admin,
    login_user,
    logout_user,
)
from .config import settings
from .db import Base, engine, get_db, SessionLocal
from .models import User
from .routers.background_contours import router as background_contours_router
from .routers.employee_notification_preferences import router as employee_notification_preferences_router
from .routers.integrations import router as integrations_router
from .routers.internal_tasks import router as internal_tasks_router
from .routers.management_integrations import router as management_integrations_router
from .routers.management_feedback import router as management_feedback_router
from .routers.management_saved_files import router as management_saved_files_router
from .routers.management_statistics import router as management_statistics_router
from .routers.management_users import router as management_users_router
from .routers.mass_notifications import router as mass_notifications_router
from .routers.quick_replies import router as quick_replies_router
from .routers.reviews import router as reviews_router
from .routers.settings import router as settings_router
from .routers.settings_automatic_messages import router as settings_automatic_messages_router
from .routers.settings_bot_button_items import router as settings_bot_button_items_router
from .routers.settings_bot_menu_profiles import router as settings_bot_menu_profiles_router
from .routers.settings_bot_button_structure import router as settings_bot_button_structure_router
from .routers.settings_client_card import router as settings_client_card_router
from .routers.settings_notification_bot_basic import router as settings_notification_bot_basic_router
from .routers.settings_users import router as settings_users_router
from .routers.telegram import router as telegram_router
from .routers.telegram_groups import router as telegram_groups_router
from .routers.tickets import router as tickets_router
from .routers.ticket_client_avatars import router as ticket_client_avatars_router
from .routers.users import router as users_router
from .routers.web_auth import router as web_auth_router
from .routers.web_menu import router as web_menu_router
from .routers.web_tasks import router as web_tasks_router
from .routers.web_task_results import router as web_task_results_router
from .routers.web_history import router as web_history_router
from .routers.web_chat import router as web_chat_router
from .routers.web_composer_draft import router as web_composer_draft_router
from .services.web_tasks import web_task_cleanup_loop
from .services import telegram_client_avatar_cleanup  # Register post-commit cleanup for every closing path.
from .services.bitrix_password_migration import migrate_legacy_bitrix_passwords
from .services.call_center_employee_resolver import start_periodic_refresh_loop, stop_periodic_refresh_loop
from .services.schema_evolution import (
    ensure_background_contours_table,
    ensure_web_task_results_tables,
    ensure_bot_menu_level_layouts_table,
    ensure_bot_menu_profiles_tables,
    ensure_bot_button_settings_table,
    ensure_telegram_group_registry_tables,
    ensure_automatic_message_templates_table,
    ensure_client_card_field_config_columns,
    ensure_call_center_employee_resolver_state_table,
    ensure_client_profile_columns,
    ensure_employee_notification_preferences_table,
    ensure_internal_task_tables,
    ensure_integration_logs_table,
    ensure_quick_reply_tables,
    ensure_saved_files_table,
    ensure_stats_foundation_tables,
    ensure_telegram_contact_company_user_unique,
    ensure_telegram_contact_columns,
    ensure_telegram_avatar_cleanup_state_table,
    ensure_telegram_notification_accounts_table,
    ensure_ticket_attachment_table,
    ensure_ticket_attachment_preload_table,
    ensure_ticket_columns,
    ensure_ticket_feedback_tables,
    ensure_ticket_message_columns,
    ensure_ticket_reviews_tables,
    ensure_user_columns,
)
from .services.seed_data import ensure_demo_seed_data, ensure_system_seed_data
from .services.bot_button_saved_file_migration import migrate_legacy_bot_button_pdf_saved_files
from .services.integration_tasks import cleanup_old_integration_logs_on_startup
from .services.internal_task_scheduler import internal_task_scheduler_loop
from .services.employee_telegram_bot import set_employee_bot_commands
from .services.employee_telegram_polling import employee_telegram_polling_loop
from .services.telegram_bot import TelegramApiError, set_bot_commands
from .services.telegram_polling import telegram_polling_loop


BASE_DIR = Path(__file__).resolve().parent

app = FastAPI(title="Helpdesk MVP API", version="0.1.0")
app.add_middleware(
    SessionMiddleware,
    secret_key=settings.session_secret_key,
    session_cookie=settings.session_cookie_name,
    same_site="lax",
    https_only=settings.session_cookie_secure,
)
app.include_router(tickets_router)
app.include_router(ticket_client_avatars_router)
app.include_router(telegram_router)
app.include_router(telegram_groups_router)
app.include_router(users_router)
app.include_router(background_contours_router)
app.include_router(web_auth_router)
app.include_router(web_menu_router)
app.include_router(web_tasks_router)
app.include_router(web_task_results_router)
app.include_router(web_history_router)
app.include_router(web_chat_router)
app.include_router(web_composer_draft_router)
app.include_router(settings_router)
app.include_router(settings_automatic_messages_router)
app.include_router(settings_bot_button_items_router)
app.include_router(settings_bot_menu_profiles_router)
app.include_router(settings_bot_button_structure_router)
app.include_router(settings_users_router)
app.include_router(settings_client_card_router)
app.include_router(settings_notification_bot_basic_router)
app.include_router(employee_notification_preferences_router)
app.include_router(mass_notifications_router)
app.include_router(internal_tasks_router)
app.include_router(management_integrations_router)
app.include_router(management_feedback_router)
app.include_router(management_saved_files_router)
app.include_router(management_statistics_router)
app.include_router(management_users_router)
app.include_router(reviews_router)
app.include_router(integrations_router)
app.include_router(quick_replies_router)
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")
telegram_polling_task: asyncio.Task | None = None
employee_telegram_polling_task: asyncio.Task | None = None
internal_task_scheduler_task: asyncio.Task | None = None
web_task_cleanup_task: asyncio.Task | None = None
call_center_employee_refresh_thread = None


@app.on_event("startup")
async def on_startup():
    global telegram_polling_task, employee_telegram_polling_task, internal_task_scheduler_task, call_center_employee_refresh_thread
    global web_task_cleanup_task

    settings.validate_runtime_guards()

    last_error = None
    for _ in range(20):
        try:
            # Technical chat, composer drafts and persistent numbering schemas are applied explicitly.
            Base.metadata.create_all(bind=engine, tables=[
                table for table in Base.metadata.sorted_tables if table.name not in {"web_chat_messages", "ticket_number_counter", "web_client_drafts", "employee_notification_periods", "employee_notification_backlog"}
            ])
            ensure_client_profile_columns()
            ensure_telegram_contact_columns()
            ensure_telegram_avatar_cleanup_state_table()
            ensure_telegram_contact_company_user_unique()
            ensure_telegram_notification_accounts_table()
            ensure_user_columns()
            ensure_ticket_columns()
            ensure_ticket_message_columns()
            ensure_ticket_attachment_table()
            ensure_web_task_results_tables()
            ensure_ticket_attachment_preload_table()
            ensure_ticket_reviews_tables()
            ensure_ticket_feedback_tables()
            ensure_bot_button_settings_table()
            ensure_telegram_group_registry_tables()
            ensure_automatic_message_templates_table()
            ensure_bot_menu_level_layouts_table()
            ensure_bot_menu_profiles_tables()
            ensure_client_card_field_config_columns()
            ensure_call_center_employee_resolver_state_table()
            ensure_employee_notification_preferences_table()
            ensure_internal_task_tables()
            ensure_integration_logs_table()
            ensure_saved_files_table()
            ensure_background_contours_table()
            ensure_stats_foundation_tables()
            ensure_quick_reply_tables()
            with SessionLocal() as db:
                migrate_legacy_bitrix_passwords(db)
                migrate_legacy_bot_button_pdf_saved_files(db)
                db.commit()
            ensure_system_seed_data()
            cleanup_old_integration_logs_on_startup()
            from .routers.telegram import recover_incomplete_group_automations
            with SessionLocal() as db:
                recover_incomplete_group_automations(db)
            last_error = None
            break
        except OperationalError as exc:
            last_error = exc
            await asyncio.sleep(1)

    if last_error is not None:
        raise last_error

    if settings.telegram_bot_token:
        try:
            set_bot_commands()
        except TelegramApiError as exc:
            print(f"Telegram command menu setup error: {exc}")

    if settings.employee_telegram_bot_token:
        try:
            set_employee_bot_commands()
        except TelegramApiError as exc:
            print(f"Employee Telegram command menu setup error: {exc}")

    if settings.initial_seed_enabled_effective:
        ensure_demo_seed_data()

    if settings.telegram_polling_enabled and settings.telegram_bot_token and telegram_polling_task is None:
        telegram_polling_task = asyncio.create_task(telegram_polling_loop())
    if (
        settings.employee_telegram_polling_enabled
        and settings.employee_telegram_bot_token
        and employee_telegram_polling_task is None
    ):
        employee_telegram_polling_task = asyncio.create_task(employee_telegram_polling_loop())

    if internal_task_scheduler_task is None:
        internal_task_scheduler_task = asyncio.create_task(internal_task_scheduler_loop())
    if web_task_cleanup_task is None:
        web_task_cleanup_task = asyncio.create_task(web_task_cleanup_loop())

    if call_center_employee_refresh_thread is None:
        call_center_employee_refresh_thread = start_periodic_refresh_loop()


@app.on_event("shutdown")
async def on_shutdown():
    global telegram_polling_task, employee_telegram_polling_task, internal_task_scheduler_task, call_center_employee_refresh_thread
    global web_task_cleanup_task

    if telegram_polling_task is not None:
        telegram_polling_task.cancel()
        try:
            await telegram_polling_task
        except asyncio.CancelledError:
            pass
        telegram_polling_task = None
    if employee_telegram_polling_task is not None:
        employee_telegram_polling_task.cancel()
        try:
            await employee_telegram_polling_task
        except asyncio.CancelledError:
            pass
        employee_telegram_polling_task = None
    if internal_task_scheduler_task is not None:
        internal_task_scheduler_task.cancel()
        try:
            await internal_task_scheduler_task
        except asyncio.CancelledError:
            pass
        internal_task_scheduler_task = None
    if web_task_cleanup_task is not None:
        web_task_cleanup_task.cancel()
        try:
            await web_task_cleanup_task
        except asyncio.CancelledError:
            pass
        web_task_cleanup_task = None
    stop_periodic_refresh_loop()
    call_center_employee_refresh_thread = None


@app.get("/health")
def health():
    return {"ok": True}


@app.get("/")
def index():
    return RedirectResponse(url="/tickets", status_code=303)


def _redirect_login(next_path: str = "/tickets") -> RedirectResponse:
    return RedirectResponse(url=f"/login?next={next_path}", status_code=303)


def _require_page_auth(request: Request, db: Session) -> CurrentUser | None:
    return get_optional_current_user(request, db)


@app.get("/login")
def login_page(request: Request):
    if request.session.get("auth_user_id"):
        return RedirectResponse(url="/tickets", status_code=303)
    next_path = request.query_params.get("next") or "/tickets"
    html = (BASE_DIR / "templates" / "login.html").read_text(encoding="utf-8")
    html = html.replace("{{LOGIN_ERROR}}", "")
    html = html.replace("{{LOGIN_VALUE}}", "")
    html = html.replace("{{NEXT_VALUE}}", next_path if next_path.startswith("/") else "/tickets")
    return HTMLResponse(content=html)


@app.post("/login")
def login_submit(
    request: Request,
    db: Session = Depends(get_db),
    login: str = Form(default=""),
    username: str = Form(default=""),
    password: str = Form(default=""),
    next: str = Form(default="/tickets"),
):
    login_value = (login or username or "").strip()
    user = authenticate_user(db, login_value, password)
    if user is None:
        html = (BASE_DIR / "templates" / "login.html").read_text(encoding="utf-8")
        html = html.replace("{{LOGIN_ERROR}}", "Неверный логин или пароль, либо пользователь отключён.")
        html = html.replace("{{LOGIN_VALUE}}", login_value)
        html = html.replace("{{NEXT_VALUE}}", next or "/tickets")
        return HTMLResponse(content=html, status_code=401)
    user.last_login_at = datetime.now(timezone.utc)
    db.commit()
    login_user(request, user)
    safe_next = next if next and next.startswith("/") else "/tickets"
    return RedirectResponse(url=safe_next, status_code=303)


@app.post("/logout")
def logout(request: Request):
    logout_user(request)
    return RedirectResponse(url="/login", status_code=303)


@app.get("/api/v1/auth/session")
def auth_session(request: Request, db: Session = Depends(get_db), current_user: CurrentUser = Depends(get_current_user)):
    effective = db.get(User, current_user.id)
    real = db.get(User, current_user.real_user_id)
    return {
        "real_user": {
            "id": current_user.real_user_id,
            "role": current_user.real_role,
            "full_name": real.full_name if real else None,
            "login": real.login if real else None,
            "availability_status": real.availability_status if real else None,
        },
        "effective_user": {
            "id": current_user.id,
            "role": current_user.role,
            "full_name": effective.full_name if effective else None,
            "login": effective.login if effective else None,
            "availability_status": effective.availability_status if effective else None,
        },
        "is_real_admin": bool(current_user.is_real_admin),
        "session_login_ts": int(request.session.get("login_ts") or 0),
        "app_timezone": settings.app_timezone,
    }


@app.get("/tickets")
def tickets_page(request: Request, db: Session = Depends(get_db)):
    if _require_page_auth(request, db) is None:
        return _redirect_login("/tickets")
    return FileResponse(str(BASE_DIR / "templates" / "tickets.html"))


@app.get("/tickets/{ticket_id}")
def ticket_page(ticket_id: str, request: Request, db: Session = Depends(get_db)):
    if _require_page_auth(request, db) is None:
        return _redirect_login(f"/tickets/{ticket_id}")
    return FileResponse(str(BASE_DIR / "templates" / "ticket.html"))


@app.get("/settings")
def settings_page(request: Request, db: Session = Depends(get_db)):
    user = _require_page_auth(request, db)
    if user is None:
        return _redirect_login("/settings")
    if not user.is_real_admin:
        return RedirectResponse(url="/tickets", status_code=303)
    return FileResponse(str(BASE_DIR / "templates" / "settings.html"))


@app.get("/settings/users")
def settings_users_page(request: Request, db: Session = Depends(get_db)):
    user = _require_page_auth(request, db)
    if user is None:
        return _redirect_login("/settings/users")
    if not user.is_real_admin:
        return RedirectResponse(url="/tickets", status_code=303)
    return FileResponse(str(BASE_DIR / "templates" / "settings_users.html"))


@app.get("/settings/client-card")
def settings_client_card_page(request: Request, db: Session = Depends(get_db)):
    user = _require_page_auth(request, db)
    if user is None:
        return _redirect_login("/settings/client-card")
    if not user.is_real_admin:
        return RedirectResponse(url="/tickets", status_code=303)
    return FileResponse(str(BASE_DIR / "templates" / "settings_client_card.html"))


@app.get("/settings/bot-buttons")
def settings_bot_buttons_page(request: Request, db: Session = Depends(get_db)):
    user = _require_page_auth(request, db)
    if user is None:
        return _redirect_login("/settings/bot-buttons")
    if not user.is_real_admin:
        return RedirectResponse(url="/tickets", status_code=303)
    return FileResponse(str(BASE_DIR / "templates" / "settings_bot_buttons.html"))


@app.get("/settings/automatic-messages")
def settings_automatic_messages_page(request: Request, db: Session = Depends(get_db)):
    user = _require_page_auth(request, db)
    if user is None:
        return _redirect_login("/settings/automatic-messages")
    if not user.is_real_admin:
        return RedirectResponse(url="/tickets", status_code=303)
    return FileResponse(str(BASE_DIR / "templates" / "settings_automatic_messages.html"))


@app.get("/settings/notification-bot-basic")
def settings_notification_bot_basic_page(request: Request, db: Session = Depends(get_db)):
    user = _require_page_auth(request, db)
    if user is None:
        return _redirect_login("/settings/notification-bot-basic")
    if not user.is_real_admin:
        return RedirectResponse(url="/tickets", status_code=303)
    return FileResponse(str(BASE_DIR / "templates" / "settings_notification_bot_basic.html"))


@app.get("/profile/notification-bot")
def profile_notification_bot_page(request: Request, db: Session = Depends(get_db)):
    if _require_page_auth(request, db) is None:
        return _redirect_login("/profile/notification-bot")
    return FileResponse(str(BASE_DIR / "templates" / "profile_notification_bot.html"))


@app.get("/profile")
def profile_page(request: Request, db: Session = Depends(get_db)):
    if _require_page_auth(request, db) is None:
        return _redirect_login("/profile")
    return FileResponse(str(BASE_DIR / "templates" / "profile.html"))


@app.get("/quick-replies")
def quick_replies_page(request: Request, db: Session = Depends(get_db)):
    if _require_page_auth(request, db) is None:
        return _redirect_login("/quick-replies")
    return FileResponse(str(BASE_DIR / "templates" / "quick_replies.html"))


@app.get("/reviews")
def reviews_page(request: Request, db: Session = Depends(get_db)):
    user = _require_page_auth(request, db)
    if user is None:
        return _redirect_login("/reviews")
    if user.is_real_admin or user.real_role not in {"agent", "trainee"}:
        return RedirectResponse(url="/tickets", status_code=303)
    return FileResponse(str(BASE_DIR / "templates" / "reviews.html"))


@app.get("/management/reviews")
def management_reviews_page(request: Request, db: Session = Depends(get_db)):
    user = _require_page_auth(request, db)
    if user is None:
        return _redirect_login("/management/reviews")
    if not user.is_real_admin:
        return RedirectResponse(url="/tickets", status_code=303)
    return FileResponse(str(BASE_DIR / "templates" / "reviews.html"))


@app.get("/management/feedback")
def management_feedback_page(request: Request, db: Session = Depends(get_db)):
    user = _require_page_auth(request, db)
    if user is None:
        return _redirect_login("/management/feedback")
    if not user.is_real_admin:
        return RedirectResponse(url="/tickets", status_code=303)
    return FileResponse(str(BASE_DIR / "templates" / "management_feedback.html"))


@app.get("/management/statistics")
def management_statistics_page(request: Request, db: Session = Depends(get_db)):
    user = _require_page_auth(request, db)
    if user is None:
        return _redirect_login("/management/statistics")
    if not user.is_real_admin:
        return RedirectResponse(url="/tickets", status_code=303)
    return FileResponse(str(BASE_DIR / "templates" / "management_statistics.html"))


@app.get("/management/tasks")
def management_tasks_page(request: Request, db: Session = Depends(get_db)):
    user = _require_page_auth(request, db)
    if user is None:
        return _redirect_login("/management/tasks")
    if not user.is_real_admin:
        return RedirectResponse(url="/tickets", status_code=303)
    return FileResponse(str(BASE_DIR / "templates" / "management_tasks.html"))


@app.get("/management/saved-files")
def management_saved_files_page(request: Request, db: Session = Depends(get_db)):
    user = _require_page_auth(request, db)
    if user is None:
        return _redirect_login("/management/saved-files")
    if not user.is_real_admin:
        return RedirectResponse(url="/tickets", status_code=303)
    return FileResponse(str(BASE_DIR / "templates" / "management_saved_files.html"))


@app.get("/statuses")
def statuses_page(request: Request, db: Session = Depends(get_db)):
    user = _require_page_auth(request, db)
    if user is None:
        return _redirect_login("/statuses")
    if not user.is_real_admin:
        return RedirectResponse(url="/tickets", status_code=303)
    return FileResponse(str(BASE_DIR / "templates" / "statuses.html"))


@app.get("/mass-notifications")
def mass_notifications_page(request: Request, db: Session = Depends(get_db)):
    user = _require_page_auth(request, db)
    if user is None:
        return _redirect_login("/mass-notifications")
    if not user.is_real_admin:
        return RedirectResponse(url="/tickets", status_code=303)
    return FileResponse(str(BASE_DIR / "templates" / "mass_notifications.html"))


@app.get("/management")
def management_page(request: Request, db: Session = Depends(get_db)):
    user = _require_page_auth(request, db)
    if user is None:
        return _redirect_login("/management")
    if not user.is_real_admin:
        return RedirectResponse(url="/tickets", status_code=303)
    query = f"?{request.url.query}" if request.url.query else ""
    return RedirectResponse(url=f"/settings{query}", status_code=303)


@app.get("/management/integrations")
def management_integrations_page(request: Request, db: Session = Depends(get_db)):
    user = _require_page_auth(request, db)
    if user is None:
        return _redirect_login("/management/integrations")
    if not user.is_real_admin:
        return RedirectResponse(url="/tickets", status_code=303)
    return FileResponse(str(BASE_DIR / "templates" / "management_integrations.html"))


@app.get("/management/users")
def management_users_page(request: Request, db: Session = Depends(get_db)):
    user = _require_page_auth(request, db)
    if user is None:
        return _redirect_login("/management/users")
    if not user.is_real_admin:
        return RedirectResponse(url="/tickets", status_code=303)
    return FileResponse(str(BASE_DIR / "templates" / "management_users.html"))
