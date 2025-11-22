#!/usr/bin/env python
"""
ALAS Auto Sleep/Wake Manager
自動休眠喚醒管理器

功能：
1. 獲取下一個排程時間
2. 設置 Windows 任務計劃在該時間喚醒
3. 檢測 ALAS 是否需要重啟
4. 執行休眠

使用方式：
    python auto_sleep_wake.py [alas_path] [config_name]

    alas_path: ALAS 安裝路徑，默認當前目錄
    config_name: 配置名稱，默認 'alas'
"""

import os
import sys
import subprocess
import operator
import ctypes
import time
import json
import socket
import struct
from datetime import datetime, timedelta
from pathlib import Path

# === 配置區 ===
DEFAULT_CONFIG_NAME = 'alas'
WAKE_BUFFER_MINUTES = 5  # 提前喚醒分鐘數
MIN_SLEEP_MINUTES = 10   # 最小休眠時間（小於此值不休眠）
TASK_NAME = 'ALAS_AutoWake'


def setup_alas_path(alas_path=None):
    """設置 ALAS 路徑"""
    if alas_path:
        os.chdir(alas_path)
    sys.path.insert(0, os.getcwd())


def get_schedule(config_name='alas'):
    """獲取排程列表，返回下一個任務時間"""
    from module.config.config import AzurLaneConfig

    cfg = AzurLaneConfig(config_name)
    cfg.get_next_task()

    pending = cfg.pending_task
    waiting = cfg.waiting_task

    # 合併所有任務並找出最早的
    all_tasks = pending + waiting

    if not all_tasks:
        return None, []

    # 按時間排序
    all_tasks = sorted(all_tasks, key=operator.attrgetter("next_run"))
    next_task = all_tasks[0]

    return next_task.next_run, all_tasks


def check_alas_running():
    """
    檢查 ALAS WebUI 是否運行
    返回: (webui_running, scheduler_running)
    """
    import psutil

    webui_running = False
    gui_process = None

    for proc in psutil.process_iter(['pid', 'name', 'cmdline']):
        try:
            cmdline = proc.info.get('cmdline', []) or []
            cmdline_str = ' '.join(cmdline).lower()

            # 檢查 gui.py 或相關進程
            if 'gui.py' in cmdline_str or 'alas' in proc.info['name'].lower():
                webui_running = True
                gui_process = proc
                break
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue

    # 檢查調度器是否真正在運行（通過檢查子進程）
    scheduler_running = False
    if gui_process:
        try:
            children = gui_process.children(recursive=True)
            # 如果有子進程在運行任務，認為調度器正在運行
            for child in children:
                cmdline = child.cmdline()
                if cmdline and ('alas' in ' '.join(cmdline).lower()):
                    scheduler_running = True
                    break
        except:
            pass

    return webui_running, scheduler_running


def check_webui_port(port=22267):
    """檢查 WebUI 端口是否響應"""
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(2)
        result = sock.connect_ex(('127.0.0.1', port))
        sock.close()
        return result == 0
    except:
        return False


def trigger_alas_start(config_name='alas'):
    """
    觸發 ALAS 調度器啟動
    方法1: 寫入 reloadalas 文件（需要 WebUI 重啟）
    方法2: 直接啟動 alas.py
    """
    # 方法1: 寫入 reloadalas 文件
    reloadalas_path = Path('./config/reloadalas')
    reloadalas_path.parent.mkdir(exist_ok=True)

    with open(reloadalas_path, 'w') as f:
        f.write(f"{config_name}\n")

    print(f"[INFO] 已寫入 {reloadalas_path}，等待 WebUI 重啟時自動啟動")
    return True


def start_alas_directly(config_name='alas'):
    """
    直接啟動 ALAS 主循環（不通過 WebUI）
    適用於 WebUI 未運行的情況
    """
    try:
        # 使用 subprocess 啟動
        python_exe = sys.executable
        alas_script = os.path.join(os.getcwd(), 'alas.py')

        if os.path.exists(alas_script):
            # 後台啟動
            subprocess.Popen(
                [python_exe, alas_script, config_name],
                creationflags=subprocess.CREATE_NEW_CONSOLE if os.name == 'nt' else 0
            )
            print(f"[INFO] 已直接啟動 ALAS: {config_name}")
            return True
    except Exception as e:
        print(f"[ERROR] 啟動 ALAS 失敗: {e}")
    return False


def start_webui():
    """啟動 WebUI (gui.py)"""
    try:
        python_exe = sys.executable
        gui_script = os.path.join(os.getcwd(), 'gui.py')

        if os.path.exists(gui_script):
            subprocess.Popen(
                [python_exe, gui_script],
                creationflags=subprocess.CREATE_NEW_CONSOLE if os.name == 'nt' else 0
            )
            print("[INFO] 已啟動 WebUI")
            return True
    except Exception as e:
        print(f"[ERROR] 啟動 WebUI 失敗: {e}")
    return False


def create_wake_task(wake_time: datetime):
    """
    創建 Windows 任務計劃，設置喚醒時間
    """
    if os.name != 'nt':
        print("[WARN] 非 Windows 系統，跳過任務計劃創建")
        return False

    # 刪除舊任務
    subprocess.run(
        ['schtasks', '/delete', '/tn', TASK_NAME, '/f'],
        capture_output=True
    )

    # 格式化時間
    wake_str = wake_time.strftime('%Y-%m-%dT%H:%M:%S')
    date_str = wake_time.strftime('%Y/%m/%d')
    time_str = wake_time.strftime('%H:%M')

    # 創建任務（執行此腳本來啟動 ALAS）
    script_path = os.path.abspath(__file__)
    python_exe = sys.executable
    alas_path = os.getcwd()

    # 使用 schtasks 創建任務
    cmd = [
        'schtasks', '/create',
        '/tn', TASK_NAME,
        '/tr', f'"{python_exe}" "{script_path}" "{alas_path}" --wake-action',
        '/sc', 'once',
        '/sd', date_str,
        '/st', time_str,
        '/rl', 'HIGHEST',  # 最高權限
        '/f'
    ]

    result = subprocess.run(cmd, capture_output=True, text=True)

    if result.returncode == 0:
        print(f"[INFO] 已創建喚醒任務: {TASK_NAME}")
        print(f"[INFO] 喚醒時間: {wake_time}")

        # 設置任務喚醒計算機
        # 使用 PowerShell 修改任務設置
        ps_cmd = f'''
$settings = New-ScheduledTaskSettingsSet -WakeToRun
$task = Get-ScheduledTask -TaskName "{TASK_NAME}"
Set-ScheduledTask -TaskName "{TASK_NAME}" -Settings $settings
'''
        subprocess.run(['powershell', '-Command', ps_cmd], capture_output=True)
        print("[INFO] 已設置任務喚醒計算機")
        return True
    else:
        print(f"[ERROR] 創建任務失敗: {result.stderr}")
        return False


def hibernate_system():
    """使系統進入休眠狀態"""
    if os.name != 'nt':
        print("[WARN] 非 Windows 系統，無法休眠")
        return False

    print("[INFO] 系統將在 5 秒後進入休眠...")
    time.sleep(5)

    # 使用 shutdown 命令進入休眠
    # /h = hibernate, /f = force
    result = subprocess.run(['shutdown', '/h'], capture_output=True)
    return result.returncode == 0


def sleep_system():
    """使系統進入睡眠狀態（比休眠更快喚醒）"""
    if os.name != 'nt':
        print("[WARN] 非 Windows 系統，無法睡眠")
        return False

    print("[INFO] 系統將在 5 秒後進入睡眠...")
    time.sleep(5)

    # 使用 PowerShell 進入睡眠
    ps_cmd = 'Add-Type -AssemblyName System.Windows.Forms; [System.Windows.Forms.Application]::SetSuspendState("Suspend", $false, $false)'
    result = subprocess.run(['powershell', '-Command', ps_cmd], capture_output=True)
    return result.returncode == 0


def wake_action(alas_path, config_name='alas'):
    """
    喚醒後執行的動作
    """
    print(f"[INFO] 系統已喚醒，執行啟動動作...")
    print(f"[INFO] ALAS 路徑: {alas_path}")
    print(f"[INFO] 配置名稱: {config_name}")

    setup_alas_path(alas_path)

    # 等待系統完全啟動
    time.sleep(10)

    # 檢查 WebUI 是否運行
    webui_running = check_webui_port()

    if webui_running:
        print("[INFO] WebUI 已運行，觸發調度器啟動")
        trigger_alas_start(config_name)
    else:
        print("[INFO] WebUI 未運行，啟動 WebUI")
        # 寫入 reloadalas 以便 WebUI 啟動時自動運行調度器
        trigger_alas_start(config_name)
        start_webui()

    print("[INFO] 喚醒動作完成")


def main():
    import argparse

    parser = argparse.ArgumentParser(description='ALAS Auto Sleep/Wake Manager')
    parser.add_argument('alas_path', nargs='?', default='.', help='ALAS 安裝路徑')
    parser.add_argument('config_name', nargs='?', default='alas', help='配置名稱')
    parser.add_argument('--wake-action', action='store_true', help='執行喚醒動作（由任務計劃調用）')
    parser.add_argument('--no-sleep', action='store_true', help='只設置喚醒任務，不休眠')
    parser.add_argument('--use-hibernate', action='store_true', help='使用休眠而非睡眠')
    parser.add_argument('--check-only', action='store_true', help='只檢查狀態')

    args = parser.parse_args()

    # 喚醒動作模式
    if args.wake_action:
        wake_action(args.alas_path, args.config_name)
        return

    # 設置路徑
    setup_alas_path(args.alas_path)

    print("=" * 50)
    print("ALAS Auto Sleep/Wake Manager")
    print("=" * 50)

    # 獲取排程
    print(f"\n[INFO] 正在獲取排程 (config: {args.config_name})...")
    next_run, all_tasks = get_schedule(args.config_name)

    if not next_run:
        print("[WARN] 沒有找到任何排程任務")
        return

    now = datetime.now()
    print(f"[INFO] 當前時間: {now}")
    print(f"[INFO] 下一個任務時間: {next_run}")

    # 顯示接下來的任務
    print(f"\n[INFO] 接下來的任務:")
    for i, task in enumerate(all_tasks[:5]):
        status = "⏰" if task.next_run > now else "✅"
        print(f"  {status} {task.command}: {task.next_run}")

    # 檢查 ALAS 狀態
    print(f"\n[INFO] 檢查 ALAS 狀態...")
    webui_port_open = check_webui_port()
    print(f"  WebUI 端口 (22267): {'運行中' if webui_port_open else '未響應'}")

    if args.check_only:
        return

    # 計算休眠時間
    wake_time = next_run - timedelta(minutes=WAKE_BUFFER_MINUTES)
    sleep_duration = (wake_time - now).total_seconds() / 60

    print(f"\n[INFO] 計算休眠時間:")
    print(f"  喚醒時間: {wake_time}")
    print(f"  休眠時長: {sleep_duration:.1f} 分鐘")

    if sleep_duration < MIN_SLEEP_MINUTES:
        print(f"[WARN] 休眠時間小於 {MIN_SLEEP_MINUTES} 分鐘，不休眠")
        return

    # 創建喚醒任務
    if not create_wake_task(wake_time):
        print("[ERROR] 創建喚醒任務失敗，取消休眠")
        return

    # 休眠
    if not args.no_sleep:
        if args.use_hibernate:
            hibernate_system()
        else:
            sleep_system()
    else:
        print("[INFO] --no-sleep 模式，跳過休眠")


if __name__ == "__main__":
    main()
