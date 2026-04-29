[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8

$ProjectDir = "C:\Users\ES\Downloads\Source\helpdesk"
$DockerDesktopPath = "C:\Program Files\Docker\Docker\Docker Desktop.exe"
$LogDir = Join-Path $ProjectDir "logs"
$LogFile = Join-Path $LogDir "helpdesk-supervisor.log"

New-Item -ItemType Directory -Force -Path $LogDir | Out-Null

function Write-Log($Message) {
    $stamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    "$stamp $Message" | Tee-Object -FilePath $LogFile -Append | Out-Null
}

function Test-DockerReady {
    docker info *> $null
    return ($LASTEXITCODE -eq 0)
}

function Start-DockerDesktopIfNeeded {
    if (Test-DockerReady) {
        Write-Log "Docker Engine уже доступен."
        return $true
    }

    if (Test-Path $DockerDesktopPath) {
        Write-Log "Docker Engine недоступен, запускаю Docker Desktop."
        Start-Process -FilePath $DockerDesktopPath -WindowStyle Hidden
    } else {
        Write-Log "Docker Desktop не найден по пути: $DockerDesktopPath"
    }

    for ($i = 1; $i -le 36; $i++) {
        Start-Sleep -Seconds 5
        if (Test-DockerReady) {
            Write-Log "Docker Engine стал доступен."
            return $true
        }
        Write-Log "Ожидаю Docker Engine... попытка $i/36"
    }

    Write-Log "Docker Engine не стал доступен за 180 секунд."
    return $false
}

function Start-HelpdeskCompose {
    Set-Location $ProjectDir
    Write-Log "Запускаю docker compose up -d."
    docker compose up -d *> $null
    if ($LASTEXITCODE -ne 0) {
        Write-Log "Ошибка docker compose up -d, код: $LASTEXITCODE"
        return $false
    }
    Write-Log "docker compose up -d выполнен."
    return $true
}

function Test-ApiHealth {
    try {
        $result = Invoke-RestMethod -Uri "http://127.0.0.1:8000/health" -TimeoutSec 5
        return ($result.ok -eq $true)
    } catch {
        return $false
    }
}

function Ensure-ApiHealthy {
    for ($i = 1; $i -le 12; $i++) {
        Start-Sleep -Seconds 5
        if (Test-ApiHealth) {
            Write-Log "API healthy."
            return $true
        }
        Write-Log "Ожидаю API /health... попытка $i/12"
    }

    Write-Log "API не отвечает, пробую перезапустить контейнер api."
    Set-Location $ProjectDir
    docker compose restart api *> $null
    Start-Sleep -Seconds 10

    if (Test-ApiHealth) {
        Write-Log "API healthy после перезапуска."
        return $true
    }

    Write-Log "API всё ещё не отвечает после перезапуска."
    return $false
}

try {
    Write-Log "=== Helpdesk supervisor start ==="
    if (-not (Start-DockerDesktopIfNeeded)) { exit 1 }
    if (-not (Start-HelpdeskCompose)) { exit 1 }
    if (-not (Ensure-ApiHealthy)) { exit 1 }
    Write-Log "Helpdesk supervisor done: OK."
    exit 0
} catch {
    Write-Log "Критическая ошибка supervisor: $($_.Exception.Message)"
    exit 1
}
