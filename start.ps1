$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$Logs = Join-Path $Root 'logs'
$PidFile = Join-Path $Logs 'server.pid'
New-Item -ItemType Directory -Force -Path $Logs | Out-Null

if (Test-Path $PidFile) {
    $OldPid = [int](Get-Content $PidFile -Raw)
    if (Get-Process -Id $OldPid -ErrorAction SilentlyContinue) {
        Write-Output "服务已运行，PID=$OldPid"
        exit 0
    }
    Remove-Item -LiteralPath $PidFile -Force
}

$Python = if ($env:PYTHON) { $env:PYTHON } else { 'python' }
$Info = New-Object System.Diagnostics.ProcessStartInfo
$Info.FileName = $Python
$Info.Arguments = ('"{0}" --config "{1}"' -f (Join-Path $Root 'run.py'), (Join-Path $Root 'config\app.json'))
$Info.WorkingDirectory = $Root
$Info.UseShellExecute = $false
$Info.CreateNoWindow = $true
$Process = New-Object System.Diagnostics.Process
$Process.StartInfo = $Info
if (-not $Process.Start()) { throw '无法启动服务进程' }
$Process.Id | Set-Content -LiteralPath $PidFile -Encoding ascii
Write-Output "服务已启动，PID=$($Process.Id)"
