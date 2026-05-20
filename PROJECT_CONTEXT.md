# PROJECT_CONTEXT

## Project Purpose
Локальный helpdesk для Telegram-обращений с web-интерфейсом операторов/админов. Архитектура рассчитана на дальнейшее добавление других каналов.

## Tech Stack
- Backend: FastAPI + SQLAlchemy
- DB: PostgreSQL (Docker Compose) + startup schema evolution
- Frontend: Jinja templates + vanilla JS/CSS
- Telegram: Bot API long polling

## Repository Structure
- `api/app/main.py` — app, роуты страниц, startup/evolution, polling task
- `api/app/models.py` / `schemas.py` — ORM и API-схемы
- `api/app/auth.py` — временная header-auth
- `api/app/routers/tickets.py` — списки, карточка, reply/comment, close/freeze/restore, admin close/delete, special
- `api/app/routers/telegram.py` — обработка updates, /start, /close, callbacks/menu, task/auto/group flows
- `api/app/routers/users.py` — пользователи/статусы
- `api/app/routers/settings.py` — users settings + client-card settings API
- `api/app/services/assignment.py` — автоназначение и forced assignee fallback
- `api/app/services/button_config.py` — парсинг TG buttons txt
- `api/app/services/template_fields.py` — шаблонные поля `{{...}}`
- `api/app/services/bitrix_client.py` — Bitrix lookup automation
- `api/app/services/telegram_bot.py` / `telegram_polling.py`
- `api/app/services/auto_close.py` — таймер автозакрытия обычных ticket
- `api/app/static/*.js` + `styles.css`
- `api/app/templates/*.html`
- `TG buttons/` — меню/документация/скрипт применения

## Current Functional Scope
- Роли: `admin`, `agent`, `trainee`, `chiki_piki`.
- Очереди: Нераспределённые, Активные, Особые, Замороженные, Закрытые.
- Attachments и stickers в карточке.
- Special queue для admin.
- Forced assignee из TG buttons.
- Автоматизация `bitrix_deal_phone_lookup`.
- Настройки: пользователи, карточка клиента (блоки/поля/custom fields).
- Автотаймер закрытия только для обычных non-special ticket.

## Important Rules (current)
- Агенты видят все закрытые заявки.
- Вернуть в работу агент может только заявку, которую закрывал сам.
- `task/auto` restore запрещён.
- `chiki_piki` — только read-only, только заявки по `ClientProfile.destination == "Колл Центр"`.
- Предсоздание меню закрывается через 10 минут без выбора конечной проблемы.
- Special-заявки не участвуют в автозакрытии.

## Env (no secrets)
- `DATABASE_URL`
- `APP_TIMEZONE`
- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_POLLING_ENABLED`
- `TELEGRAM_POLLING_TIMEOUT`
- `BUTTON_CONFIG_DIR` (в Docker: `/button-config`)
- `BITRIX_WEBHOOK_BASE` (без токена в доках)

## Commands
```powershell
docker compose up --build
```

```powershell
docker compose exec -T api python -m py_compile app/main.py app/models.py app/schemas.py app/routers/tickets.py app/routers/telegram.py
```

```powershell
node --check api\app\static\ticket.js
node --check api\app\static\list.js
```

```powershell
.\TG buttons\Применить кнопки.bat
```

## Deferred / Open
- Детект удаления клиентских сообщений в Telegram через MTProto/TDLib — отложено (не внедрено в проект).
- Переход с header-auth на полноценный login/logout.

## Для Codex / локальной разработки
- Перед любой задачей перейти в `PROJECT_ROOT`: `C:\Users\ES\Downloads\Source\helpdesk`.
- Проверить наличие корня проекта командой: `Test-Path .\api\app\main.py`.
- Все относительные пути в промтах считать от `PROJECT_ROOT`.
