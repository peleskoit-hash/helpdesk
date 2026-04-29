# PROJECT_CONTEXT

## Project Purpose
A local-first helpdesk MVP inspired by HelpDeskEddy, focused on a single Telegram support channel now, with room to add other messenger/web channels later. Clients create requests through a Telegram bot; support agents/admins work in a web UI. The project is being built iteratively for internal LAN/local testing first, not production-hardening yet.

## Tech Stack
- Backend: Python, FastAPI, Uvicorn.
- DB: PostgreSQL 16 in Docker; SQLAlchemy ORM; startup code applies lightweight schema evolution with `ALTER TABLE` helpers.
- Frontend: server-rendered HTML templates plus vanilla JS/CSS static files.
- Telegram: Bot API via custom helper functions; local long polling supported, no public webhook required for local tests.
- Runtime: Docker Compose on Windows; API mounted with `--reload`.

## Repository Structure
- `api/app/main.py` - FastAPI app, static/templates routes, startup DB creation/migrations, test agents seed, Telegram polling task.
- `api/app/models.py` - SQLAlchemy models/enums.
- `api/app/schemas.py` - Pydantic API response/request schemas.
- `api/app/auth.py` - temporary header-based role/user auth (`x-user-role`, `x-user-id`).
- `api/app/routers/tickets.py` - ticket list/card API, replies/comments, assignment, freeze/restore/close, message edit/delete, wrong-problem hint.
- `api/app/routers/telegram.py` - Telegram update processing, `/start`, `/close`, callbacks, task/ticket/auto creation logic.
- `api/app/routers/users.py` - agents/current user status endpoints.
- `api/app/services/assignment.py` - fair assignment, online-agent selection, assignee notifications.
- `api/app/services/button_config.py` - parses `TG buttons/*.txt` menu/button configs.
- `api/app/services/template_fields.py` - replaces template fields like `{{contact_full_name}}`.
- `api/app/services/telegram_bot.py` - Telegram Bot API wrappers.
- `api/app/services/telegram_polling.py` - long polling loop.
- `api/app/static/list.js` - ticket list page logic.
- `api/app/static/ticket.js` - ticket card UI/chat composer/context menu/client profile modal.
- `api/app/static/styles.css` - all UI styling.
- `api/app/templates/tickets.html` - ticket list page.
- `api/app/templates/ticket.html` - ticket card page.
- `TG buttons/` - button/menu text configs and helper docs/scripts.
- `TG buttons/Применить кнопки.bat` and `TG buttons/apply_buttons.ps1` - validate/reload button configs manually.
- `tools/helpdesk_supervisor.ps1` - local process supervisor helper.

## Key Modules And Responsibilities
- Tickets router owns web-side ticket operations: list tabs, card data, agent replies, internal comments, greeting, freeze/unfreeze, close/restore, wrong-problem advice, admin assignment, message edit/delete.
- Telegram router owns bot-side interaction: contact creation/update, first connection flow, menu callbacks, task intake, regular ticket intake, auto tickets, client close button, edited client messages.
- Assignment service owns fair assignment: only active online agents are auto-assigned; admins are never auto-assigned.
- Button config service parses Russian text config files into menus/buttons. `Руководство.txt` is currently the real configured menu; other user types fall back to a basic menu.
- Frontend ticket page owns the current chat UX: fixed composer, right control panel, client card, context menu on right-click message.

## Telegram Bot Logic
- `/start`:
  - unknown/new contact: creates first-connection ticket with subject `Первое подключение к боту`; client receives onboarding text; agent sees system instruction text.
  - known contact without active ticket: creates a collecting draft and shows the menu.
  - known contact with active regular ticket: tells client to continue writing into the current ticket.
- `/close`: closes active regular ticket and sends close text/menu again.
- Inline menu callbacks:
  - `submenu`: sends submenu inline keyboard.
  - `ticket`: collecting ticket becomes ready, receives optional visible system text, then goes to assignment.
  - `task`: collecting ticket asks client to send task text in one message; if no text within 5 minutes, task intake is cancelled.
  - `auto`: creates/handles automatic ticket, sends configured response, closes it.
  - `group_task`: creates invisible parent task plus visible child tasks; child answers accumulate into parent and final combined answer is sent when all children close.
- While a regular ticket is active, clients cannot create new tickets/tasks. Tasks do not block future regular tickets after creation, but active regular tickets block new tickets/tasks.
- Client edited Telegram messages are handled: old client message is marked `edited`/grey with note, new corrected message is appended with `replaced_message_id`.
- Telegram deletion detection is not fully possible with Bot API; no MTProto/TDLib/userbot is used.

## Database Models Relevant To Messages
- `Ticket`: `workflow_type` (`ticket`, `task`, `auto`), `task_kind` (`normal`, `group_parent`, `group_child`), `intake_state` (`collecting`, `ready`, `cancelled`), `status` (`unassigned`, `new`, `pending`, `closed`), `assignee_id`, `agent_visible`, `load_counted`, `button_config_key`, `wrong_problem_hint`, `client_close_message_id`, freeze/close timestamps.
- `TicketMessage`: `message_type` (`client`, `agent`, `internal`, `system`), `author_user_id`, `body`, `agent_visible`, `telegram_message_id`, `message_status` (`normal`, `edited`, `deleted`), `status_note`, `replaced_message_id`, `created_at`.
- `TelegramContact`: maps Telegram chat/user to internal client; stores `external_chat_id`, `external_user_id`, `external_username`, `external_display_name`.
- `ClientProfile`: editable customer card: first/last name, TG data via contact, department/destination fields, location/Anydesk, Bitrix fields, `is_verified`.
- `User`: agents/admins; `availability_status` controls assignment (`online` only).
- `TelegramUpdate`: idempotency storage for update IDs and raw payload JSON.

## Environment Variables (No Secrets)
Do not commit real `.env` values. Important keys:
- `DATABASE_URL` - SQLAlchemy DB URL.
- `APP_TIMEZONE` - app timezone, currently `Europe/Moscow`.
- `TELEGRAM_BOT_TOKEN` - bot token, secret, never paste into docs.
- `TELEGRAM_POLLING_ENABLED` - enables local long polling.
- `TELEGRAM_POLLING_TIMEOUT` - long polling timeout.
- `TELEGRAM_DEFAULT_AGENT_ID` - legacy/default agent id; current assignment primarily uses online-agent selection.
- `BUTTON_CONFIG_DIR` - directory with button config txt files; in Docker it is `/button-config` mounted from `./TG buttons`.

## Commands
Run locally with Docker:
```powershell
cd C:\Users\ES\Downloads\Source\helpdesk
docker compose up --build
```

Start detached:
```powershell
docker compose up -d --build
```

Check containers:
```powershell
docker compose ps
```

API/docs/UI:
- API health: `http://127.0.0.1:8000/health`
- Swagger: `http://127.0.0.1:8000/docs`
- Tickets list: `http://127.0.0.1:8000/tickets`

Validate Python syntax:
```powershell
docker compose exec -T api python -m py_compile app/main.py app/models.py app/schemas.py app/routers/tickets.py app/routers/telegram.py
```

Validate JS syntax:
```powershell
node --check api\app\static\ticket.js
node --check api\app\static\list.js
```

Reload button config after editing `TG buttons/*.txt`:
```powershell
.\TG buttons\Применить кнопки.bat
```
This validates txt configs and calls `/api/v1/button-config/reload`. `.md` files and draft folders are not meant to be applied as menu configs.

## Current Task Context
Most recent work is on message UX in the ticket card:
- Message actions moved from side icons to right-click context menu.
- Context menu actions: `Цитировать`, `Копировать`; for editable agent messages also `Исправить`, `Удалить`.
- Message edit uses a modal textarea, not browser `prompt`, so multiline text works.
- Message header now shows author name and timestamp in `time date` order.
- API now returns `author_name` for messages with `author_user_id`.
- Agent edited messages stay visually normal with note like `Агент 1 исправил сообщение`; deleted messages are grey/dashed with note like `Агент 1 удалил сообщение`.
- Client edited messages show old original grey/dashed and append corrected text as a new message.

## Known Constraints And Gotchas
- Keep all source files UTF-8. Cyrillic has broken before when written through unsafe shell/heredoc paths. Prefer `apply_patch` or explicit UTF-8 PowerShell reads/writes.
- Do not include Telegram tokens/secrets in docs or chat summaries.
- The project directory is `C:\Users\ES\Downloads\Source\helpdesk`; earlier paths under Documents may be stale.
- This folder is currently not a git repository.
- Auth is temporary header-based and not real login/password yet.
- Startup migration helpers are pragmatic for MVP, not a real Alembic migration system.
- `system` messages are generally hidden from agents unless `agent_visible=True`; button-config system texts can be visible to agents.
- Admin sees unassigned and can filter/select agents; agents see only assigned visible tickets.
- Auto-assignment only chooses users with role `agent`, active, `availability_status == online`.
- Admins are not auto-assigned.
- The button config parser is custom and syntax-sensitive; use the bat/apply script after editing txt configs.
- Current UI refresh is polling: ticket list every ~5s, ticket card every ~3s. SSE/WebSocket is a future upgrade.
- Telegram Bot API cannot reliably detect deleted client messages after the fact; edits are supported through `edited_message` updates.
- For task replies, client usually receives the final response on close, not each intermediate agent draft.

## Open Questions
- When to implement real authentication and user management instead of header role/user switcher.
- Whether to introduce Alembic migrations before more schema changes.
- Final menu configs for non-`Руководство` user types.
- Exact syntax for future conditional file sending/actions in button txt configs.
- How to store and refresh Telegram `file_id` plus local/database file fallback for materials.
- When to replace polling UI refresh with SSE/WebSocket.
- Final policy for edited/deleted agent messages in Telegram vs helpdesk history.
- Future support for additional channels: web form/chat or another messenger.
