# Архитектура Helpdesk / ES DESK

## Общая схема

Helpdesk / ES DESK - FastAPI-приложение с Telegram-ботом и server-rendered web UI.

Поток данных:
1. Клиент пишет или нажимает кнопку в Telegram.
2. `api/app/routers/telegram.py` принимает update/callback.
3. Telegram services создают/обновляют `Ticket`, `TicketMessage`, `TelegramContact`, `ClientProfile`.
4. Web UI через API читает списки и карточки заявок.
5. Ответы сотрудников уходят клиенту через `telegram_bot.py`.

## Backend

Основные файлы:
- `api/app/main.py` - FastAPI app, HTML routes, startup, router registration.
- `api/app/db.py` - engine/session/Base.
- `api/app/models.py` - ORM models.
- `api/app/schemas.py` - Pydantic response/request schemas.
- `api/app/auth.py` - session auth, current user, role helpers.
- `api/app/config.py` - env settings.

Routers:
- `routers/tickets.py` - ticket API orchestration.
- `routers/telegram.py` - Telegram update orchestration.
- `routers/users.py` - users, statuses, completion stats.
- `routers/settings*.py` - admin settings APIs.
- `routers/mass_notifications.py` - Telegram mass notifications.

Services:
- Tickets: `ticket_list.py`, `ticket_messages.py`, `ticket_lifecycle.py`, `ticket_problem_flow.py`, `ticket_attachments.py`, `ticket_admin_actions.py`, `ticket_special.py`, `ticket_subject.py`.
- Telegram: `telegram_onboarding.py`, `telegram_menu_flow.py`, `telegram_client_close.py`, `telegram_automation.py`, `telegram_attachment_policy.py`, `telegram_attachments.py`, `telegram_text_format.py`, `telegram_bot.py`, `telegram_polling.py`.
- Settings/support: `button_config.py`, `client_card.py`, `schema_evolution.py`, `seed_data.py`, `assignment.py`, `auto_close.py`, `bitrix_client.py`.

## DB и schema evolution

DB: PostgreSQL через Docker Compose.

Startup в `main.py`:
- `Base.metadata.create_all(bind=engine)`;
- затем `ensure_*` функции из `schema_evolution.py` добавляют недостающие таблицы/колонки;
- `ensure_initial_seed_data()` и defaults карточки клиента.

Важно:
- не делать миграции вручную в коде бизнес-логики;
- если нужна новая колонка - добавлять модель + `schema_evolution.py`;
- после изменения models/schema evolution нужен ручной restart API и smoke.

## Frontend

Frontend не SPA-фреймворк:
- HTML из `api/app/templates/*.html`;
- JS из `api/app/static/*.js`;
- общий CSS в `styles.css`.

Основные страницы:
- `/tickets` - `tickets.html` + `list.js`.
- `/tickets/{id}` - `ticket.html` + `ticket.js` + `ticket_*` modules.
- `/settings*` - settings templates + `settings_*` scripts.
- `/statuses` - `statuses.html` + `statuses.js`.
- `/mass-notifications` - `mass_notifications.html` + `mass_notifications.js`.

## App shell

`api/app/static/app_shell.js` сохраняет sidebar между некоторыми HTML-страницами и заменяет только `#appContent`.

См. `docs/FRONTEND_APP_SHELL.md`.

## Telegram

Long polling запускается в `main.py` если:
- `TELEGRAM_POLLING_ENABLED=true`;
- `TELEGRAM_BOT_TOKEN` задан.

Транспорт Telegram API: `api/app/services/telegram_bot.py`.

## Конфиги кнопок

Есть два слоя:
- legacy/reference txt в `TG buttons/` через `button_config.py`;
- DB settings через `BotButtonSetting`, `BotMenuProfile`, `BotMenuDestinationBinding`.

DB-only/hybrid runtime сосредоточен в `telegram_menu_flow.py` и settings routers.

## Что не трогать без отдельной задачи

- `/tickets` app-shell/partial navigation: сложный state tab/page/q/search/pagination.
- `/tickets/{id}` partial navigation: чат, polling, attachments, composer, cleanup.
- Telegram runtime menu/callback/group_task вместе с unrelated UI задачами.
- `schema_evolution.py` без явного schema task.
