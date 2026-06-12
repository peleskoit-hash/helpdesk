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

## Work mode selection

Before a large task, choose one mode from [docs/CODEX_TOKEN_BUDGET.md](docs/CODEX_TOKEN_BUDGET.md):
- `MICRO FIX`
- `NARROW AUDIT`
- `FEATURE SLICE`
- `COMMIT / SPLIT COMMIT`

Use the smallest mode that fits the task.

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

Use selective staging for mixed files. Do not use `git add .` for split commits.

## Encoding / mojibake

All text files must be UTF-8 without BOM.

If a task touches user-facing text, Russian strings, UI labels, Telegram messages, HTML templates, JS strings, CSS content text, docs, markdown, or any other text-heavy file, encoding checks are mandatory before the answer and before commit.

Text-risky files include:
- `*.js`
- `*.html`
- `*.css`
- `*.md`
- `*.py` when messages/errors/Telegram text/UI strings changed
- txt button configs
- templates
- any files containing Russian text.

Required checks:

```powershell
python tools/check_mojibake.py <changed text files>
python tools/check_staged_encoding.py
git diff --check
node --check <changed js files>
python -m py_compile <changed py files>
```

`check_mojibake.py` does not replace manual diff review. Codex must inspect changed Russian lines in `git diff` and make sure there are no mojibake patterns like `?...`, `?...`, `?...`, `?...`, `?...`, or `????`.

Prefer:
- `apply_patch` for small edits;
- explicit UTF-8 when scripting file writes;
- avoid PowerShell commands that can recode Cyrillic unexpectedly.

File write rules:
- do not use `Set-Content` / `Out-File` without explicit UTF-8;
- for scripted writes prefer `Path.write_text(..., encoding="utf-8")`;
- do not save files as Windows-1251/ANSI;
- do not add BOM;
- do not mass-change line endings without need.

Stop rule:
- if mojibake is visible in diff or encoding is uncertain, do not commit;
- stop, show the file/line, and propose or make a fix;
- do not dismiss it as terminal-only until the file/diff is verified safely.

Report rule for text changes:
- list checked text files;
- confirm `check_mojibake.py` passed;
- confirm `check_staged_encoding.py` passed;
- confirm `git diff` was reviewed for mojibake;
- confirm no new mojibake strings were found.

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
rg -n "console\\.log|debugger|print\\(" <changed files>
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
