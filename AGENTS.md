# AGENTS.md

## PROJECT_ROOT
- `C:\Users\ES\Downloads\Source\helpdesk`
- Перед началом каждой задачи:
  1. `Set-Location "C:\Users\ES\Downloads\Source\helpdesk"`
  2. `Test-Path .\api\app\main.py` должно вернуть `True`.

## 1) Ask-Mode Before Large/Risky Tasks
Перед крупными/рискованными задачами сначала делать диагностику/план без изменений и дождаться подтверждения:
- архитектурные изменения;
- dev/prod;
- sidebar/layout;
- Telegram runtime;
- DB-only;
- крупный refactor;
- баги без понятной причины.

Только после утверждения плана переходить к реализации.

## 2) One Request = One Technical Step
Один запрос должен быть ограничен одним техническим шагом:
- один модуль;
- один bugfix;
- один endpoint;
- один UI-блок;
- один refactor-slice.

Если задача расползается — остановиться и предложить следующий короткий шаг.

## 3) Bug Workflow (No Guess Fixes)
При баг-репорте нельзя чинить “похожее место” по догадке.

Обязательная последовательность:
1. Воспроизвести сценарий.
2. Проверить UI/Console.
3. Проверить Network: status/response.
4. Проверить backend logs/traceback (для 5xx).
5. Если баг после рефакторинга — проверить routes/imports/schema.
6. Назвать точную причину.
7. Только после этого вносить фикс.

После фикса обязательно показать:
- точную причину;
- что изменено;
- как проверено;
- есть ли похожие места в коде.

Запрещено:
- фиксы “наугад”;
- fallback-заглушки вместо устранения причины;
- маскировка ошибок пустым UI;
- commit без минимального smoke-подтверждения.

## 4) Modularity by Default
Новые фичи не добавлять в монолиты без необходимости:
- `settings.py`
- `telegram.py`
- `tickets.py`
- `ticket.js`
- `settings_bot_buttons.js`
- `styles.css`

Backend:
- thin router;
- business logic в service;
- отдельный router/service на домен.

Frontend:
- крупные UI-блоки — в отдельный JS-файл;
- общий state/context передавать явно;
- не дублировать sidebar logic.

## 5) Refactor Rules
Если задача “вынести код”:
- код должен исчезнуть из исходного файла;
- дублей быть не должно;
- показать строки до/после;
- показать функции, удалённые из исходника;
- показать `git diff --stat`.

## 6) Tests and Checks
Для bugfix/refactor недостаточно только `py_compile`/`node --check`.
Нужен релевантный smoke:
- UI smoke;
- endpoint smoke;
- registered routes;
- response schema;
- похожие паттерны/сценарии.

## 7) Best-of-N for Architecture
Для архитектурных решений сначала давать 2–3 варианта:
- быстрый безопасный;
- правильный архитектурный;
- компромиссный.

Далее выбрать один и реализовывать только его, без параллельной реализации всех вариантов.

## 8) Commit Safety
Перед commit обязательно:
1. `git status`
2. Убедиться, что в commit нет:
   - `.env`
   - `logs/**`
   - `storage/attachments/**`
   - `api/helpdesk.db`
   - `**/__pycache__/**`
   - `**/*.pyc`
   - временных архивов/дампов/секретов
3. Commit только после успешных проверок.
4. После commit показать:
   - hash;
   - `git show --stat --oneline HEAD`.

## Encoding
- Рабочая кодировка проекта: UTF-8.
- Не вносить изменения, создающие mojibake.
