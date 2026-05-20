# PROJECT_CONTEXT

## Project Purpose
Локальный helpdesk для Telegram-обращений с web-интерфейсом операторов/админов. Архитектура рассчитана на дальнейшее разделение dev/prod (один Windows 10 ПК) и расширение каналов.

## Tech Stack
- Backend: FastAPI + SQLAlchemy
- DB: PostgreSQL (Docker Compose) + startup schema evolution
- Frontend: Jinja templates + vanilla JS/CSS
- Telegram: Bot API long polling

## Repository Structure
- `api/app/main.py` — app, роуты страниц/API, startup/evolution, polling task
- `api/app/models.py` / `schemas.py` — ORM и API-схемы
- `api/app/auth.py` — session auth / current user / role guards
- `api/app/routers/tickets.py` — списки, карточка, reply/comment, close/freeze/restore, admin close/delete, special
- `api/app/routers/telegram.py` — обработка updates, /start, /close, callbacks/menu, task/auto/group flows
- `api/app/routers/users.py` — пользователи/статусы/completion stats
- `api/app/routers/settings.py` — настройки users/client-card/bot-buttons
- `api/app/services/button_config.py` — поддержка txt как fallback/эталона
- `api/app/services/telegram_menu_flow.py` — DB-only/hybrid резолв меню и действий
- `api/app/services/telegram_automation.py` / `bitrix_client.py`
- `api/app/services/auto_close.py` — таймер автозакрытия
- `api/app/services/schema_evolution.py` — эволюция схемы
- `api/app/static/*.js` + `styles.css`
- `api/app/templates/*.html`
- `TG buttons/` — резервные txt-конфиги и материалы

## Current Functional Scope
- Реализованы login/logout/session auth (`/login`, `/logout`).
- Роли: `admin`, `agent`, `trainee`, `chiki_piki`.
- Очереди: Нераспределённые, Активные, Особые, Замороженные, Закрытые.
- Attachments и stickers в карточке.
- Special queue для admin.
- Forced assignee, freeze_on_create, confirmation, system/client text, files, conditional files.
- Настройки: пользователи, карточка клиента, кнопки бота.
- Автоматизация `bitrix_deal_phone_lookup`.
- Completion stats для agent/trainee.
- Onsite trigger "Иду к вам" для ticket.

## Bot Menu Model
- Runtime-флаг: `USE_DB_BUTTONS`.
- `ClientProfile.destination` (поле "Куда?") выбирает профиль меню.
- Menu profiles:
  - один профиль меню ↔ много значений "Куда?";
  - одно значение "Куда?" ↔ только один профиль меню.
- `Базовое` используется как fallback.

## Bot Button Settings (DB)
Для конечных кнопок:
- `label_override` / name override;
- `is_enabled`;
- `instruction_text`;
- `system_text`;
- `client_text`;
- confirmation;
- fixed assignee;
- `freeze_on_create`;
- files + conditional files;
- `automation_key`;
- layout rows.

## Env (no secrets)
- `DATABASE_URL`
- `APP_TIMEZONE`
- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_POLLING_ENABLED`
- `TELEGRAM_POLLING_TIMEOUT`
- `BUTTON_CONFIG_DIR`
- `BITRIX_WEBHOOK_BASE`
- `USE_DB_BUTTONS`

## Commands
```powershell
docker compose up --build
```

```powershell
docker compose exec -T api python -m py_compile app/main.py app/models.py app/schemas.py app/routers/tickets.py app/routers/telegram.py app/routers/settings.py
```

```powershell
node --check api\app\static\ticket.js
node --check api\app\static\list.js
node --check api\app\static\sidebar.js
```

## Для Codex / локальной разработки
- Перед любой задачей перейти в `PROJECT_ROOT`: `C:\Users\ES\Downloads\Source\helpdesk`.
- Проверить наличие корня проекта командой: `Test-Path .\api\app\main.py`.
- Все относительные пути в промтах считать от `PROJECT_ROOT`.
- При баг-репортах сначала делать диагностику (UI -> JS -> API -> backend -> DB/response), и только потом фикс.
- При рефакторинге выносить код без дублей: перенесённые функции должны исчезать из исходного файла.
- Соблюдать принцип: одна задача = один технический шаг.
- Перед commit: `git status`, исключить `.env`, `logs/**`, `storage/attachments/**`, `api/helpdesk.db`, `__pycache__`, `*.pyc`.
