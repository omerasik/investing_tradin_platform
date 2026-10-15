<#
.SYNOPSIS
    Hourly dead-man check for the Windows R0/U2 recorders (read-only; opt-in ping).

.DESCRIPTION
    2026-10-14 22:27Z: both Windows recorders stopped at the disk floor and nothing
    said so for five hours. This check reports, per archive root, whether a recorder
    holds the root lock, how old the newest finalized manifest is, and free disk
    against the recorder floor, then exits 0 (OK) or 1 (ATTENTION).

    With a healthchecks.io-compatible URL in -UrlFile (a check of its own, period
    1 h, grace 20 min; never the OCI checks) the report is posted as a success or
    /fail ping. A sleeping, shut-down or offline PC sends nothing, so the check
    alerts by itself. Only status text leaves the PC: no market data, no credential.

    It starts, stops and changes nothing: no recorder, service, setting or file.
    It is service health only, never coverage evidence or acceptance.

    Owner setup (nothing here runs it for you):
      1. Create the check; save its Ping URL with Notepad as the -UrlFile path
         (default ~\.trade_platform\deadman-ping-url), then restrict it:
           icacls "$HOME\.trade_platform\deadman-ping-url" /inheritance:r /grant:r "${env:USERNAME}:(R,W)"
      2. Run once by hand:  pwsh -NoProfile -File scripts\windows_capture_deadman.ps1
      3. Print the hourly Scheduled Task command (-ShowTaskCommand) and run it yourself.

.PARAMETER NoPing
    Report and exit; never post, even when the URL file exists.

.PARAMETER ShowTaskCommand
    Print (never run) an hourly per-user Scheduled Task command for this script.
#>
[CmdletBinding()]
param(
    [string]$UrlFile = (Join-Path $HOME '.trade_platform\deadman-ping-url'),
    [string]$R0Root = (Join-Path $HOME '.trade_platform\capture'),
    [string]$U2Root = (Join-Path $HOME '.trade_platform\capture-universe-r1b'),
    [string]$Python = (Join-Path (Split-Path -Parent $PSScriptRoot) '.venv\Scripts\python.exe'),
    # One missed hourly hand-off plus slack, as on OCI (trade-capture-health.sh).
    [int]$MaxManifestAgeSeconds = 4500,
    # The recorders' own floor (DEFAULT_MIN_FREE_GIB); below it they stop with DISK_BUDGET_STOP.
    [double]$MinFreeGB = 20,
    [switch]$NoPing,
    [switch]$ShowTaskCommand
)

$ErrorActionPreference = 'Stop'
$repo = Split-Path -Parent $PSScriptRoot
$cli = Join-Path $repo 'scripts\capture_bybit_public.py'

if ($ShowTaskCommand) {
    $self = Join-Path $PSScriptRoot 'windows_capture_deadman.ps1'
    Write-Output 'Owner opt-in (not executed). To run the dead-man check hourly at HH:15, run yourself:'
    Write-Output "  Register-ScheduledTask -TaskName 'trade-platform-capture-deadman' -User `$env:USERNAME -Trigger (New-ScheduledTaskTrigger -Once -At (Get-Date -Minute 15 -Second 0) -RepetitionInterval (New-TimeSpan -Hours 1)) -Action (New-ScheduledTaskAction -Execute 'pwsh.exe' -Argument '-NoProfile -WindowStyle Hidden -File `"$self`"')"
    Write-Output 'To remove it: Unregister-ScheduledTask -TaskName trade-platform-capture-deadman'
    return
}

$lines = [System.Collections.Generic.List[string]]::new()
$fail = $false
$now = [DateTimeOffset]::UtcNow
$lines.Add("deadman_at     $($now.ToString('yyyy-MM-ddTHH:mm:ssZ')) host=$([Environment]::MachineName)")

foreach ($pair in @(@('r0', $R0Root), @('u2', $U2Root))) {
    $label, $root = $pair
    $status = @(& $Python -I -B $cli --root $root status --recent 1 2>&1 | ForEach-Object { "$_" })
    $lock = if ($status.Count -gt 0) { $status[0] } else { "status failed (exit $LASTEXITCODE)" }
    $lines.Add("lock_$label        $lock")
    if ($lock -notmatch 'RUNNING pid=') { $fail = $true }
    # Only the last two UTC days: a running recorder finalizes a manifest every hour.
    $days = @($now.UtcDateTime.ToString('yyyy-MM-dd'), $now.UtcDateTime.AddDays(-1).ToString('yyyy-MM-dd'))
    $newest = $days | ForEach-Object {
        Get-ChildItem -Path (Join-Path $root "v1\exchange=*\instrument=*\date=$_\session=*\MANIFEST.json") -File -ErrorAction SilentlyContinue
    } | Sort-Object LastWriteTimeUtc | Select-Object -Last 1
    if ($null -eq $newest) {
        $lines.Add("manifest_$label    none in the last two UTC days under $root")
        $fail = $true
    } else {
        $age = [int]($now.UtcDateTime - $newest.LastWriteTimeUtc).TotalSeconds
        $lines.Add("manifest_$label    newest $age s ago")
        if ($age -gt $MaxManifestAgeSeconds) { $fail = $true }
    }
}

$drive = [System.IO.DriveInfo]::new([System.IO.Path]::GetPathRoot((Resolve-Path -LiteralPath $HOME).Path))
$freeGiB = $drive.AvailableFreeSpace / 1GB
$lines.Add([string]::Format([cultureinfo]::InvariantCulture,
    'disk_free      {0:F1} GiB on {1} (recorder floor {2:F1} GiB, usable {3:F1} GiB)',
    $freeGiB, $drive.Name, $MinFreeGB, ($freeGiB - $MinFreeGB)))
if ($freeGiB -le $MinFreeGB) { $fail = $true }

$lines.Add($(if ($fail) { 'service        ATTENTION' } else { 'service        OK' }))
$lines.Add('evidence       NOT JUDGED HERE: a running recorder is not proven coverage')
$lines | ForEach-Object { Write-Output $_ }

if (-not $NoPing -and (Test-Path -LiteralPath $UrlFile)) {
    $outcome = if ($fail) { 'fail' } else { 'success' }
    $offsite = Join-Path $repo 'deploy\oci\capture_offsite.py'
    ($lines -join "`n") | & $Python -I -B $offsite ping $outcome --url-file $UrlFile `
        --context "windows-capture-deadman $outcome" 2>&1 | ForEach-Object { Write-Output "alert_ping     $_" }
}
exit $(if ($fail) { 1 } else { 0 })
