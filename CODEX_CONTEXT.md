# Codex Context

PROJECT_ROOT:
C:\Users\ES\Downloads\Source\helpdesk

STARTUP_COMMAND:
Set-Location "C:\Users\ES\Downloads\Source\helpdesk"

ROOT_CHECK:
Test-Path .\api\app\main.py

TEXT_ENCODING_RULES:
- Весь проект хранится и редактируется только в UTF-8 (без BOM).
- Перед завершением любой задачи запускать:
  python tools/check_mojibake.py api/app "TG buttons" README.md PROJECT_CONTEXT.md functional-contract-v1.md
- Если проверка нашла строки вида mojibake (Р..., С..., Ð..., Ñ...), задача не считается завершённой.
