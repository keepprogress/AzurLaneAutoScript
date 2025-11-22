# ALAS Sleep Daemon - Scheduled Task Installer
# Run as Administrator
# Usage: Right-click -> Run as Administrator

$ErrorActionPreference = "Stop"

# Check admin rights
$isAdmin = ([Security.Principal.WindowsPrincipal] [Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) {
    Write-Host "ERROR: Administrator privileges required!" -ForegroundColor Red
    Write-Host "Please right-click this script -> Run as Administrator" -ForegroundColor Yellow
    pause
    exit 1
}

# Get ALAS path
$scriptPath = Split-Path -Parent $MyInvocation.MyCommand.Path
$alasPath = Split-Path -Parent $scriptPath

Write-Host "========================================" -ForegroundColor Cyan
Write-Host "ALAS Sleep Daemon Installer" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "ALAS Path: $alasPath" -ForegroundColor Green

# Find Python
$pythonPaths = @(
    "$alasPath\toolkit\python.exe",
    "$alasPath\python\python.exe",
    "python.exe"
)

$pythonExe = $null
foreach ($p in $pythonPaths) {
    if (Test-Path $p) {
        $pythonExe = (Resolve-Path $p).Path
        break
    }
}

if (-not $pythonExe) {
    $pythonExe = (Get-Command python -ErrorAction SilentlyContinue).Source
}

if (-not $pythonExe) {
    Write-Host "ERROR: Python not found!" -ForegroundColor Red
    pause
    exit 1
}

Write-Host "Python Path: $pythonExe" -ForegroundColor Green
Write-Host ""

# Task settings
$taskName = "ALAS_SleepDaemon"
$scriptFile = "$alasPath\scripts\auto_sleep_wake.py"

# Remove old task
Write-Host "Removing old task (if exists)..." -ForegroundColor Yellow
Unregister-ScheduledTask -TaskName $taskName -Confirm:$false -ErrorAction SilentlyContinue

# Create task
Write-Host "Creating new task..." -ForegroundColor Yellow

$action = New-ScheduledTaskAction `
    -Execute $pythonExe `
    -Argument "scripts\auto_sleep_wake.py --daemon" `
    -WorkingDirectory $alasPath

$trigger = New-ScheduledTaskTrigger -AtLogOn

$principal = New-ScheduledTaskPrincipal `
    -UserId $env:USERNAME `
    -RunLevel Highest `
    -LogonType Interactive

$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit (New-TimeSpan -Days 0) `
    -RestartCount 3 `
    -RestartInterval (New-TimeSpan -Minutes 1)

Register-ScheduledTask `
    -TaskName $taskName `
    -Action $action `
    -Trigger $trigger `
    -Principal $principal `
    -Settings $settings `
    -Description "ALAS Auto Sleep/Wake Daemon"

Write-Host ""
Write-Host "========================================" -ForegroundColor Green
Write-Host "Installation Complete!" -ForegroundColor Green
Write-Host "========================================" -ForegroundColor Green
Write-Host ""
Write-Host "Task Name: $taskName" -ForegroundColor Cyan
Write-Host "Trigger: At user logon" -ForegroundColor Cyan
Write-Host "Run Level: Highest (Administrator)" -ForegroundColor Cyan
Write-Host ""
Write-Host "Options:" -ForegroundColor Yellow
Write-Host "  1. Re-login or restart to auto-start" -ForegroundColor White
Write-Host "  2. Or start now: schtasks /run /tn $taskName" -ForegroundColor White
Write-Host ""

$runNow = Read-Host "Start now? (Y/N)"
if ($runNow -eq "Y" -or $runNow -eq "y") {
    Write-Host "Starting..." -ForegroundColor Yellow
    Start-ScheduledTask -TaskName $taskName
    Write-Host "Started!" -ForegroundColor Green
}

Write-Host ""
pause
