# Codex workflow rules for Helpdesk / ES DESK

## Before work

Always start from project root:

```powershell
Set-Location "C:\Users\ES\Downloads\Source\helpdesk"
```

If available, do not rediscover root.

Run:
```powershell
git status --short
```

If there are unrelated uncommitted files:
- do not revert them;
- do not add them;
- mention them if commit/staging is requested.

## Scope discipline

One task = one technical slice.

Examples:
- one service extraction;
- one UI bugfix;
- one Telegram text change;
- one app-shell route expansion;
- one docs update.

Stop and ask if:
- a file contains mixed changes from different tasks and cannot be staged by file safely;
- a refactor starts pulling unrelated runtime;
- a requested commit would omit a required new import/service file.

## Bug workflow

Do not fix by guessing.

Preferred sequence:
1. Reproduce or inspect exact path.
2. Check UI/Console/Network when relevant.
3. Check backend traceback/logs/routes/schema.
4. Identify exact cause.
5. Make minimal fix.
6. Run targeted checks.
7. Tell user exact cause and manual smoke needed.

Avoid:
- masking errors with empty UI;
- fallback that hides root cause;
- changing business logic wider than the bug.

## Refactor workflow

When extracting code:
- new service/module must be used by old router;
- duplicated old functions must be removed;
- show functions moved/removed;
- verify imports and registered routes;
- do not change behavior unless task says bugfix too.

## Commit workflow

Before commit:
1. `git status --short`
2. `git diff --stat`
3. Verify files belong to one logical commit.
4. Run targeted checks.
5. Stage exact files only.
6. Run `python tools/check_staged_encoding.py` after staging if possible.
7. Commit.
8. Show hash and `git show --stat --oneline HEAD`.

Never commit:
- `.env`
- `logs/**`
- `storage/attachments/**`
- local DB dumps
- `__pycache__/**`
- `*.pyc`
- unrelated user changes.

## Encoding / mojibake

All text files must be UTF-8 without BOM.

Before/after touching Russian text:
```powershell
python tools/check_mojibake.py <changed files>
python tools/check_staged_encoding.py
```

Prefer:
- `apply_patch` for small edits;
- explicit UTF-8 when scripting file writes;
- avoid PowerShell operations that can recode Cyrillic unexpectedly.

## Checks by area

Backend Python:
```powershell
python -m py_compile <changed .py files>
```

Frontend JS:
```powershell
node --check <changed .js files>
```

Docs/HTML/CSS/JS/PY:
```powershell
python tools/check_mojibake.py <changed files>
python tools/check_staged_encoding.py
```

Search debug leftovers:
```powershell
rg -n "console\.log|debugger|print\(" <changed files>
```

## Manual smoke examples

Tickets:
- list tab/page/q/search;
- open card and Back;
- reply/comment/edit/delete;
- close/freeze/restore;
- group_task parent/children;
- chiki_piki visibility.

Telegram:
- `/start`;
- menu callback;
- ticket/task/group_task creation;
- attachment allowed/too large/forbidden;
- client close button;
- automation branch.

Settings:
- hard refresh;
- app shell transition;
- save/load values;
- no mojibake;
- no duplicated handlers.

## Response format after work

Keep concise:
- what changed;
- exact files;
- checks passed;
- manual smoke needed;
- commit hash/stat if commit was requested.

If nothing was changed, say so directly.
## Жёсткое правило для текстоопасных изменений

Если задача меняет пользовательский текст, русские строки, UI labels, Telegram messages, HTML templates, JS strings, CSS с content-текстом, docs или markdown, это текстоопасное изменение. Для него проверки кодировки обязательны перед ответом и перед commit.

Текстоопасными считать:
- `*.js`;
- `*.html`;
- `*.css`;
- `*.md`;
- `*.py`, если менялись сообщения, ошибки, Telegram-тексты или UI-строки;
- txt-конфиги кнопок;
- templates;
- любые файлы с русским текстом.

Обязательные проверки:
```powershell
python tools/check_mojibake.py <all changed text files>
python tools/check_staged_encoding.py
git diff --check
node --check <changed js files>
python -m py_compile <changed py files>
```

`check_mojibake.py` не заменяет ручной просмотр diff. Codex обязан посмотреть добавленные/изменённые русские строки в `git diff` и убедиться, что там нормальная кириллица, а не mojibake-паттерны:
- кириллическая `Р`/`С` в начале бессмысленных фрагментов;
- латинские `D-with-stroke`, `N-with-tilde`, `A-with-circumflex` последовательности;
- двойные UTF-8 артефакты;
- `????` вместо русских букв.

Правила записи файлов:
- не использовать `Set-Content` / `Out-File` без явного UTF-8;
- для небольших правок предпочитать `apply_patch`;
- для скриптовой записи использовать `Path.write_text(..., encoding="utf-8")`;
- если PowerShell должен писать файл, явно задавать UTF-8 без BOM;
- не сохранять файлы в Windows-1251/ANSI;
- не добавлять BOM;
- не менять окончания строк массово без необходимости.

Правило остановки:
- если в diff видны кракозябры или Codex не уверен в кодировке, commit запрещён;
- нужно остановиться, показать файл/строку и предложить или внести исправление;
- нельзя объяснять это “терминал так показывает”, пока diff/файл не проверен безопасным способом.

Правило отчёта:
Если задача затрагивала текст, в итоговом ответе явно написать:
- какие текстовые файлы проверены;
- что `check_mojibake.py` прошёл;
- что `check_staged_encoding.py` прошёл;
- что `git diff` просмотрен на кракозябры;
- что новых mojibake-строк не найдено.
