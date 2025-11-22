# ALAS Sleep Daemon - 任務計劃安裝腳本
# 需要以管理員身份運行
# 用法: 右鍵 → 以系統管理員身份執行

$ErrorActionPreference = "Stop"

# 檢查管理員權限
$isAdmin = ([Security.Principal.WindowsPrincipal] [Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) {
    Write-Host "錯誤: 需要管理員權限！" -ForegroundColor Red
    Write-Host "請右鍵點擊此腳本 → 以系統管理員身份執行" -ForegroundColor Yellow
    pause
    exit 1
}

# 獲取 ALAS 路徑
$scriptPath = Split-Path -Parent $MyInvocation.MyCommand.Path
$alasPath = Split-Path -Parent $scriptPath

Write-Host "========================================" -ForegroundColor Cyan
Write-Host "ALAS Sleep Daemon 安裝程式" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "ALAS 路徑: $alasPath" -ForegroundColor Green

# 查找 Python
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
    # 嘗試從 PATH 找
    $pythonExe = (Get-Command python -ErrorAction SilentlyContinue).Source
}

if (-not $pythonExe) {
    Write-Host "錯誤: 找不到 Python！" -ForegroundColor Red
    pause
    exit 1
}

Write-Host "Python 路徑: $pythonExe" -ForegroundColor Green
Write-Host ""

# 任務設定
$taskName = "ALAS_SleepDaemon"
$scriptFile = "$alasPath\scripts\auto_sleep_wake.py"

# 刪除舊任務
Write-Host "移除舊任務（如果存在）..." -ForegroundColor Yellow
Unregister-ScheduledTask -TaskName $taskName -Confirm:$false -ErrorAction SilentlyContinue

# 創建任務
Write-Host "創建新任務..." -ForegroundColor Yellow

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
    -Description "ALAS 自動休眠喚醒守護程式 - 監控調度器狀態並管理系統休眠"

Write-Host ""
Write-Host "========================================" -ForegroundColor Green
Write-Host "安裝完成！" -ForegroundColor Green
Write-Host "========================================" -ForegroundColor Green
Write-Host ""
Write-Host "任務名稱: $taskName" -ForegroundColor Cyan
Write-Host "觸發條件: 使用者登入時" -ForegroundColor Cyan
Write-Host "執行權限: 最高權限（管理員）" -ForegroundColor Cyan
Write-Host ""
Write-Host "你可以:" -ForegroundColor Yellow
Write-Host "  1. 重新登入或重啟電腦，任務會自動啟動" -ForegroundColor White
Write-Host "  2. 或立即手動啟動: schtasks /run /tn $taskName" -ForegroundColor White
Write-Host ""

$runNow = Read-Host "是否立即啟動？(Y/N)"
if ($runNow -eq "Y" -or $runNow -eq "y") {
    Write-Host "正在啟動..." -ForegroundColor Yellow
    Start-ScheduledTask -TaskName $taskName
    Write-Host "已啟動！" -ForegroundColor Green
}

Write-Host ""
pause
