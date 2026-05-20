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
    $loginValue = Read-Host "Логин администратора"
    if ([string]::IsNullOrWhiteSpace($loginValue)) {
        throw "Логин не указан"
    }
    $securePassword = Read-Host "Пароль администратора" -AsSecureString
    $plainPassword = [System.Net.NetworkCredential]::new("", $securePassword).Password
    if ([string]::IsNullOrWhiteSpace($plainPassword)) {
        throw "Пароль не указан"
    }

    $session = New-Object Microsoft.PowerShell.Commands.WebRequestSession
    Invoke-WebRequest `
        -Method Post `
        -Uri "http://127.0.0.1:8000/login" `
        -WebSession $session `
        -Body @{
            login = $loginValue
            password = $plainPassword
            next = "/tickets"
        } `
        -ContentType "application/x-www-form-urlencoded" | Out-Null

    $sessionInfo = Invoke-RestMethod -Method Get -Uri "http://127.0.0.1:8000/api/v1/auth/session" -WebSession $session
    if (-not $sessionInfo.is_real_admin) {
        throw "Текущая учётная запись не является администратором"
    }

    $result = Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:8000/api/v1/button-config/reload" -WebSession $session
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
