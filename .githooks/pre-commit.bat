@echo off
set PYTHONUTF8=1
python tools\check_staged_encoding.py
if errorlevel 1 exit /b 1
python tools\check_mojibake.py api\app "TG buttons" README.md PROJECT_CONTEXT.md functional-contract-v1.md CODEX_CONTEXT.md AGENTS.md
if errorlevel 1 exit /b 1
exit /b 0
