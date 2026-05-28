# Codex Context

PROJECT_ROOT:
C:\Users\ES\Downloads\Source\helpdesk

STARTUP_COMMAND:
Set-Location "C:\Users\ES\Downloads\Source\helpdesk"

ROOT_CHECK:
Test-Path .\api\app\main.py

SOURCE_OF_TRUTH:
- Подробные постоянные правила работы Codex находятся в `AGENTS.md`.

WORKFLOW_SHORT_RULES:
- Перед крупными/рискованными задачами сначала Ask-режим: диагностика/план без правок, затем реализация только после подтверждения.
- Одна задача = один технический шаг.
- При баге: сначала воспроизведение и диагностика (UI/Console/Network/API logs/routes/schema), потом фикс.
- Для новых фич — модульность по умолчанию, не раздувать монолиты.
- При выносе кода: без дублей, показывать строки до/после и `git diff --stat`.
- Перед commit: `git status`, исключить `.env/logs/storage/db/pycache`, коммитить только после проверок.

GIT_HOOKS:
- Включение project hooks: `git config core.hooksPath .githooks`
- Назначение pre-commit hook: двойная защита перед commit:
  - `tools/check_staged_encoding.py` (только staged файлы: UTF-8, без BOM, anti-mojibake).
  - `tools/check_mojibake.py` (проектная проверка по каталогу).
- Если hook блокирует commit: сначала исправить причину (битую кириллицу), а не отключать проверку.

POWERSHELL_UTF8:
- Для текущей сессии PowerShell (Windows) перед массовыми текстовыми правками:
  - `[Console]::InputEncoding  = [System.Text.UTF8Encoding]::new($false)`
  - `[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)`
  - `$OutputEncoding = [System.Text.UTF8Encoding]::new($false)`
- Для записи файлов в shell всегда указывать UTF-8 явно (`-Encoding utf8`) или использовать `apply_patch`.

NOTE:
- Не использовать устаревшие практики header-auth как “временную” auth-модель: в проекте используется session auth c login/logout.
