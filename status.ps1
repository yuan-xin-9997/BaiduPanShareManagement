$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$PidFile = Join-Path $Root 'logs\server.pid'
$Config = Get-Content (Join-Path $Root 'config\app.json') -Raw | ConvertFrom-Json
if (-not (Test-Path $PidFile)) { Write-Output '状态：未运行'; exit 1 }
$ServerPid = [int](Get-Content $PidFile -Raw)
if (-not (Get-Process -Id $ServerPid -ErrorAction SilentlyContinue)) { Write-Output '状态：进程不存在'; exit 1 }
try {
    $Health = Invoke-WebRequest -Uri "http://127.0.0.1:$($Config.port)/api/bootstrap" -UseBasicParsing -TimeoutSec 3
    Write-Output "状态：运行中 PID=$ServerPid HTTP=$($Health.StatusCode)"
} catch {
    Write-Output "状态：进程存在但 HTTP 不可用 PID=$ServerPid"
    exit 1
}
