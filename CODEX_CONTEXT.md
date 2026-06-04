# CODEX_CONTEXT.md

Короткий входной контекст для Codex по проекту Helpdesk / ES DESK. Если задача узкая, сначала прочитать этот файл, затем один тематический документ из `docs/`.

## Проект

Локальный helpdesk для Telegram-обращений с web-интерфейсом операторов и админов.

Основные сценарии:
- клиент работает через Telegram-бота;
- заявка/задача попадает в web-интерфейс;
- сотрудник отвечает, комментирует, закрывает, замораживает или возвращает заявку;
- админ управляет пользователями, карточкой клиента, меню/кнопками бота и массовыми уведомлениями.

## Стек

- Backend: FastAPI.
- ORM/DB: SQLAlchemy + PostgreSQL в Docker Compose.
- Auth: server-side session cookie, login/logout.
- Telegram: Bot API, long polling.
- Frontend: server-rendered HTML templates + vanilla JS modules + CSS.
- Schema evolution: startup helpers в `api/app/services/schema_evolution.py`.

## Корень проекта

```powershell
Set-Location "C:\Users\ES\Downloads\Source\helpdesk"
Test-Path .\api\app\main.py
```

Не искать корень заново, если этот путь доступен.

## Главные директории

- `api/app/main.py` - FastAPI app, HTML pages, startup, routers.
- `api/app/models.py` - SQLAlchemy models.
- `api/app/schemas.py` - Pydantic schemas.
- `api/app/auth.py` - session auth and role guards.
- `api/app/routers/` - HTTP/API routers.
- `api/app/services/` - business logic services.
- `api/app/templates/` - HTML pages.
- `api/app/static/` - vanilla JS/CSS frontend.
- `TG buttons/` - legacy/reference bot button text configs.
- `tools/` - checks, including mojibake/encoding guards.
- `docs/` - Codex-oriented project docs.

## Запуск и базовые проверки

```powershell
docker compose up --build
```

URLs:
- Web/API: `http://127.0.0.1:8000`
- Swagger: `http://127.0.0.1:8000/docs`

Типовые проверки:
```powershell
python -m py_compile api/app/routers/tickets.py api/app/routers/telegram.py
node --check api/app/static/list.js
python tools/check_mojibake.py <changed files>
python tools/check_staged_encoding.py
```

## Роли

- `admin` - полный доступ и настройки.
- `agent` - рабочий доступ к своим заявкам/задачам, участвует в автоназначении.
- `trainee` - как agent, но не автоназначается.
- `chiki_piki` - read-only observer по `ClientProfile.destination == "Колл Центр"`.

Подробнее: `docs/ROLES_AND_PERMISSIONS.md`.

## Основные flow

- Tickets: `docs/TICKETS_FLOW.md`.
- Telegram: `docs/TELEGRAM_FLOW.md`.
- Bot buttons/settings: `docs/BOT_BUTTONS_SETTINGS.md`.
- App shell / partial navigation: `docs/FRONTEND_APP_SHELL.md`.
- Общая архитектура: `docs/ARCHITECTURE.md`.
- Рабочие правила Codex: `docs/CODEX_WORKFLOW.md`.
- Предрелизные хвосты: `docs/PRE_RELEASE_BACKLOG.md`.

## Важные модули tickets

Router: `api/app/routers/tickets.py` должен оставаться тонким orchestrator.

Уже вынесены:
- `api/app/services/ticket_list.py`
- `api/app/services/ticket_messages.py`
- `api/app/services/ticket_lifecycle.py`
- `api/app/services/ticket_problem_flow.py`
- `api/app/services/ticket_attachments.py`
- `api/app/services/ticket_admin_actions.py`
- `api/app/services/ticket_special.py`
- `api/app/services/ticket_subject.py`
- `api/app/services/auto_close.py`
- `api/app/services/client_card.py`

## Важные модули Telegram

Router: `api/app/routers/telegram.py` всё ещё большой, но часть логики уже вынесена.

Services:
- `telegram_onboarding.py`
- `telegram_menu_flow.py`
- `telegram_client_close.py`
- `telegram_automation.py`
- `telegram_attachment_policy.py`
- `telegram_attachments.py`
- `telegram_text_format.py`
- `telegram_bot.py`
- `telegram_polling.py`

## Frontend

- `/tickets`: `api/app/static/list.js`.
- `/tickets/{id}`: `api/app/static/ticket.js` + `ticket_*` modules.
- Sidebar: `api/app/static/sidebar.js`.
- Theme/logout cleanup: `api/app/static/theme.js`.
- App shell: `api/app/static/app_shell.js`.
- Bot buttons editor: `settings_bot_buttons.js`, `bot_buttons_*` modules.

## Правила, которые нельзя ломать

- Не расширять доступ ролей случайно.
- Не отправлять Telegram-сообщения клиенту дважды.
- Не ломать `group_task` parent/child semantics.
- Не менять URL/API response format без отдельной задачи.
- Не смешивать refactor и bugfix без явного разрешения.
- Не коммитить `.env`, логи, storage attachments, db dumps, pycache.
- Не использовать `window.location.href` для app-shell routes, кроме fallback.
- Не добавлять raw HTML в Telegram без sanitizing через `telegram_text_format.py`.

## Кодировка

Проект должен быть UTF-8 без BOM. Русские тексты обязательно проверять.

```powershell
python tools/check_mojibake.py <changed files>
python tools/check_staged_encoding.py
```

Для project hooks:
```powershell
git config core.hooksPath .githooks
```

Если hook мешает commit - сначала исправить причину, а не отключать проверку.

## Commit workflow

- Перед изменениями: `git status --short`.
- Если есть чужие/несвязанные изменения - не трогать и не добавлять.
- Один commit = один логический блок.
- Перед commit: relevant `py_compile`, `node --check`, mojibake/encoding checks.
- UI/Telegram задачи коммитить только после ручного smoke, если пользователь этого требует.
## Обязательное правило против mojibake

Если задача меняет любой человекочитаемый текст, русские строки, UI labels, Telegram messages, HTML templates, JS strings, docs или markdown, Codex обязан перед ответом и перед commit выполнить encoding/mojibake-проверки и вручную посмотреть diff.

Текстоопасные файлы:
- `*.js`, `*.html`, `*.css`, `*.md`;
- `*.py`, если менялись сообщения, ошибки, Telegram-тексты или UI-строки;
- txt-конфиги кнопок, templates и любые файлы с русским текстом.

Обязательные проверки:
```powershell
python tools/check_mojibake.py <all changed text files>
python tools/check_staged_encoding.py
git diff --check
node --check <changed js files>
python -m py_compile <changed py files>
```

Даже если `check_mojibake.py` passed, нужно вручную просмотреть добавленные/изменённые русские строки в `git diff` и убедиться, что нет mojibake-паттернов: кириллическая `Р`/`С` в начале бессмысленных фрагментов, латинские `D-with-stroke`/`N-with-tilde`/`A-with-circumflex` последовательности, двойные UTF-8 артефакты или `????`.

Правила записи файлов:
- не использовать `Set-Content` / `Out-File` без явного UTF-8;
- предпочтительно использовать `apply_patch` или `Path.write_text(..., encoding="utf-8")`;
- не сохранять файлы в Windows-1251/ANSI;
- не добавлять BOM;
- не менять окончания строк массово без необходимости.

Если в diff видны кракозябры или есть сомнение в кодировке, commit запрещён: остановиться, показать файл/строку и исправить причину.
