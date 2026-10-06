<#
.SYNOPSIS
    Start the R0 hourly segmented BTCUSDT recorder if none is running (opt-in operator helper).

.DESCRIPTION
    Public market data only. Starts exactly the canonical R0 operating mode:

        .venv\Scripts\python -u scripts/capture_bybit_public.py supervise --segment-seconds 3600

    detached, with stdout/stderr in ~/.trade_platform/logs/. It refuses to start a
    second recorder: it checks the process list (which also finds a recorder started
    before the archive-root lock existed) and the recorder itself refuses a held root.

    This script installs nothing: no service, no Scheduled Task, no startup entry, no
    system setting. -ShowLogonTaskCommand only PRINTS the command an owner could run
    to opt in to starting it at logon.

.PARAMETER CheckOnly
    Report whether a recorder is running and show `status`; start nothing.

.PARAMETER ShowLogonTaskCommand
    Print (never run) a per-user logon Scheduled Task command for this script.
#>
[CmdletBinding()]
param(
    [switch]$CheckOnly,
    [switch]$ShowLogonTaskCommand
)

$ErrorActionPreference = 'Stop'
$repo = Split-Path -Parent $PSScriptRoot
$python = Join-Path $repo '.venv\Scripts\python.exe'
$script = 'scripts/capture_bybit_public.py'

if ($ShowLogonTaskCommand) {
    $self = Join-Path $PSScriptRoot 'start_r0_capture.ps1'
    Write-Output 'Owner opt-in (not executed). To start R0 at your logon, run yourself:'
    Write-Output "  Register-ScheduledTask -TaskName 'trade-platform-r0-capture' -User `$env:USERNAME -Trigger (New-ScheduledTaskTrigger -AtLogOn -User `$env:USERNAME) -Action (New-ScheduledTaskAction -Execute 'pwsh.exe' -Argument '-NoProfile -WindowStyle Hidden -File `"$self`"')"
    Write-Output 'To remove it: Unregister-ScheduledTask -TaskName trade-platform-r0-capture'
    return
}

if (-not (Test-Path $python)) { throw "virtual environment not found: $python" }

$running = @(Get-CimInstance Win32_Process -Filter "Name like 'python%'" |
    Where-Object { $_.CommandLine -match 'capture_bybit_public\.py"?\s+(--root\s+\S+\s+)?(supervise|run)\b' })

if ($running.Count -gt 0) {
    Write-Output "R0 recorder already running ($($running.Count) process(es), incl. venv launcher):"
    $running | ForEach-Object { Write-Output "  pid $($_.ProcessId) since $($_.CreationDate)" }
} elseif ($CheckOnly) {
    Write-Output 'R0 recorder NOT running.'
} else {
    $logs = Join-Path $HOME '.trade_platform\logs'
    New-Item -ItemType Directory -Force $logs | Out-Null
    $stamp = [DateTime]::UtcNow.ToString('yyyyMMddTHHmmssZ')
    $process = Start-Process -FilePath $python -WorkingDirectory $repo -WindowStyle Hidden -PassThru `
        -ArgumentList '-u', $script, 'supervise', '--segment-seconds', '3600' `
        -RedirectStandardOutput (Join-Path $logs "r0-supervise-$stamp.log") `
        -RedirectStandardError (Join-Path $logs "r0-supervise-$stamp.err")
    Write-Output "Started R0 recorder: pid $($process.Id), logs $logs\r0-supervise-$stamp.*"
    Start-Sleep -Seconds 5
    if ($process.HasExited) { throw "recorder exited at once (code $($process.ExitCode)); see the logs" }
}

& $python $script status
