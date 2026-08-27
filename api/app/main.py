import asyncio
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
from .db import Base, engine, get_db
from .models import User
from .routers.employee_notification_preferences import router as employee_notification_preferences_router
from .routers.management_users import router as management_users_router
from .routers.mass_notifications import router as mass_notifications_router
from .routers.settings import router as settings_router
from .routers.settings_automatic_messages import router as settings_automatic_messages_router
from .routers.settings_bot_button_items import router as settings_bot_button_items_router
from .routers.settings_bot_menu_profiles import router as settings_bot_menu_profiles_router
from .routers.settings_bot_button_structure import router as settings_bot_button_structure_router
from .routers.settings_client_card import router as settings_client_card_router
from .routers.settings_notification_bot_basic import router as settings_notification_bot_basic_router
from .routers.settings_users import router as settings_users_router
from .routers.telegram import router as telegram_router
from .routers.tickets import router as tickets_router
from .routers.users import router as users_router
from .services.button_config import clear_button_config_cache, load_button_config
from .services.call_center_employee_resolver import start_periodic_refresh_loop, stop_periodic_refresh_loop
from .services.schema_evolution import (
    ensure_bot_menu_level_layouts_table,
    ensure_bot_menu_profiles_tables,
    ensure_bot_button_settings_table,
    ensure_automatic_message_templates_table,
    ensure_client_card_field_config_columns,
    ensure_call_center_employee_resolver_state_table,
    ensure_client_profile_columns,
    ensure_employee_notification_preferences_table,
    ensure_telegram_contact_company_user_unique,
    ensure_telegram_contact_columns,
    ensure_telegram_notification_accounts_table,
    ensure_ticket_attachment_table,
    ensure_ticket_attachment_preload_table,
    ensure_ticket_columns,
    ensure_ticket_message_columns,
    ensure_user_columns,
)
from .services.seed_data import ensure_client_card_defaults, ensure_initial_seed_data
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
app.include_router(telegram_router)
app.include_router(users_router)
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
app.include_router(management_users_router)
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")
telegram_polling_task: asyncio.Task | None = None
employee_telegram_polling_task: asyncio.Task | None = None
call_center_employee_refresh_thread = None


@app.on_event("startup")
async def on_startup():
    global telegram_polling_task, employee_telegram_polling_task, call_center_employee_refresh_thread

    settings.validate_runtime_guards()

    last_error = None
    for _ in range(20):
        try:
            Base.metadata.create_all(bind=engine)
            ensure_client_profile_columns()
            ensure_telegram_contact_columns()
            ensure_telegram_contact_company_user_unique()
            ensure_telegram_notification_accounts_table()
            ensure_user_columns()
            ensure_ticket_columns()
            ensure_ticket_message_columns()
            ensure_ticket_attachment_table()
            ensure_ticket_attachment_preload_table()
            ensure_bot_button_settings_table()
            ensure_automatic_message_templates_table()
            ensure_bot_menu_level_layouts_table()
            ensure_bot_menu_profiles_tables()
            ensure_client_card_field_config_columns()
            ensure_call_center_employee_resolver_state_table()
            ensure_employee_notification_preferences_table()
            ensure_client_card_defaults()
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
        ensure_initial_seed_data()

    if settings.telegram_polling_enabled and settings.telegram_bot_token and telegram_polling_task is None:
        telegram_polling_task = asyncio.create_task(telegram_polling_loop())
    if (
        settings.employee_telegram_polling_enabled
        and settings.employee_telegram_bot_token
        and employee_telegram_polling_task is None
    ):
        employee_telegram_polling_task = asyncio.create_task(employee_telegram_polling_loop())

    if call_center_employee_refresh_thread is None:
        call_center_employee_refresh_thread = start_periodic_refresh_loop()


@app.on_event("shutdown")
async def on_shutdown():
    global telegram_polling_task, employee_telegram_polling_task, call_center_employee_refresh_thread

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
    stop_periodic_refresh_loop()
    call_center_employee_refresh_thread = None


@app.get("/health")
def health():
    return {"ok": True}


@app.post("/api/v1/button-config/reload")
def reload_button_config(user: CurrentUser = Depends(get_current_user)):
    if not is_admin(user):
        raise HTTPException(status_code=403, detail={"error": {"code": "forbidden", "message": "Только администратор", "details": {}}})
    clear_button_config_cache()
    config = load_button_config()
    return {"ok": True, "menus": len(config.menus), "buttons": len(config.buttons)}


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
        },
        "effective_user": {
            "id": current_user.id,
            "role": current_user.role,
            "full_name": effective.full_name if effective else None,
            "login": effective.login if effective else None,
        },
        "is_real_admin": bool(current_user.is_real_admin),
        "session_login_ts": int(request.session.get("login_ts") or 0),
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
    return FileResponse(str(BASE_DIR / "templates" / "management.html"))


@app.get("/management/users")
def management_users_page(request: Request, db: Session = Depends(get_db)):
    user = _require_page_auth(request, db)
    if user is None:
        return _redirect_login("/management/users")
    if not user.is_real_admin:
        return RedirectResponse(url="/tickets", status_code=303)
    return FileResponse(str(BASE_DIR / "templates" / "management_users.html"))
