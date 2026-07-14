$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$PidFile = Join-Path $Root 'logs\server.pid'
if (-not (Test-Path $PidFile)) { Write-Output '服务未运行'; exit 0 }
$ServerPid = [int](Get-Content $PidFile -Raw)
$Process = Get-Process -Id $ServerPid -ErrorAction SilentlyContinue
if ($Process) { Stop-Process -Id $ServerPid -Force; Write-Output "服务已停止，PID=$ServerPid" }
else { Write-Output 'PID 文件已过期' }
Remove-Item -LiteralPath $PidFile -Force -ErrorAction SilentlyContinue
