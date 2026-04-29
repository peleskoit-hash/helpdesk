[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$projectDir = Split-Path -Parent $scriptDir
Set-Location $projectDir

try {
    Write-Host "Проверяем конфигурацию кнопок..."
    docker compose exec api python -m app.button_config_validator --config-dir /button-config
    if ($LASTEXITCODE -ne 0) {
        Write-Host ""
        Write-Host "Кнопки НЕ применены. Исправьте ошибки выше и запустите этот файл снова."
        Write-Host ""
        Read-Host "Нажмите Enter, чтобы закрыть окно"
        exit 1
    }

    Write-Host ""
    Write-Host "Применяем кнопки..."
    $result = Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:8000/api/v1/button-config/reload"
    if (-not $result.ok) {
        throw "API вернул ошибку"
    }

    Write-Host ""
    Write-Host "Кнопки успешно применены. Меню: $($result.menus), кнопок: $($result.buttons)"
    Write-Host ""
    Read-Host "Нажмите Enter, чтобы закрыть окно"
    exit 0
} catch {
    Write-Host ""
    Write-Host "Кнопки НЕ применены. Ошибка применения кнопок: $($_.Exception.Message)"
    Write-Host ""
    Read-Host "Нажмите Enter, чтобы закрыть окно"
    exit 1
}
