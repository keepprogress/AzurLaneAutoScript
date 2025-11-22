#!/usr/bin/env python
"""
ALAS Watchdog - 監控並自動重啟 ALAS

功能：
1. 監控 ALAS WebUI 是否運行
2. 監控調度器是否停止
3. 自動重啟 WebUI 或觸發調度器啟動
4. 可選擇在所有任務完成後休眠

使用方式：
    python alas_watchdog.py [--alas-path /path/to/alas] [--config alas]

適合設置為 Windows 開機自啟動任務
"""

import os
import sys
import time
import socket
import subprocess
import json
from datetime import datetime, timedelta
from pathlib import Path


# === 配置 ===
CONFIG = {
    'CHECK_INTERVAL': 30,           # 檢查間隔（秒）
    'WEBUI_PORT': 22267,            # WebUI 端口
    'WEBUI_STARTUP_WAIT': 60,       # WebUI 啟動等待時間（秒）
    'MAX_RESTART_ATTEMPTS': 3,      # 最大重啟嘗試次數
    'RESTART_COOLDOWN': 300,        # 重啟冷卻時間（秒）
    'AUTO_SLEEP_ENABLED': False,    # 是否在任務完成後自動休眠
    'MIN_WAIT_MINUTES': 30,         # 最小等待時間才休眠（分鐘）
}


def check_port(port, host='127.0.0.1', timeout=2):
    """檢查端口是否響應"""
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        result = sock.connect_ex((host, port))
        sock.close()
        return result == 0
    except:
        return False


def get_schedule(config_name='alas'):
    """獲取排程"""
    try:
        from module.config.config import AzurLaneConfig
        import operator

        cfg = AzurLaneConfig(config_name)
        cfg.get_next_task()

        all_tasks = cfg.pending_task + cfg.waiting_task
        if all_tasks:
            all_tasks = sorted(all_tasks, key=operator.attrgetter("next_run"))
            return {
                'next_run': all_tasks[0].next_run,
                'next_task': all_tasks[0].command,
                'pending_count': len(cfg.pending_task),
                'waiting_count': len(cfg.waiting_task)
            }
    except Exception as e:
        print(f"[WARN] 獲取排程失敗: {e}")
    return None


def start_webui(alas_path):
    """啟動 WebUI"""
    gui_script = Path(alas_path) / 'gui.py'

    if not gui_script.exists():
        print(f"[ERROR] gui.py 不存在: {gui_script}")
        return False

    try:
        python_exe = sys.executable
        if os.name == 'nt':
            # Windows: 使用 pythonw.exe 隱藏控制台
            pythonw = python_exe.replace('python.exe', 'pythonw.exe')
            if os.path.exists(pythonw):
                python_exe = pythonw

            subprocess.Popen(
                [python_exe, str(gui_script)],
                cwd=alas_path,
                creationflags=subprocess.CREATE_NEW_CONSOLE
            )
        else:
            subprocess.Popen(
                [python_exe, str(gui_script)],
                cwd=alas_path,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL
            )

        print(f"[INFO] 已啟動 WebUI")
        return True
    except Exception as e:
        print(f"[ERROR] 啟動 WebUI 失敗: {e}")
        return False


def trigger_scheduler_start(config_name='alas'):
    """觸發調度器啟動（通過寫入 reloadalas 文件）"""
    try:
        reloadalas = Path('./config/reloadalas')
        reloadalas.parent.mkdir(exist_ok=True)

        # 檢查是否已經包含該配置
        existing = set()
        if reloadalas.exists():
            with open(reloadalas, 'r') as f:
                existing = set(line.strip() for line in f.readlines())

        if config_name not in existing:
            with open(reloadalas, 'a') as f:
                f.write(f"{config_name}\n")
            print(f"[INFO] 已觸發 {config_name} 啟動（等待 WebUI 重載）")

        return True
    except Exception as e:
        print(f"[ERROR] 觸發啟動失敗: {e}")
        return False


def sleep_system(use_hibernate=False):
    """使系統休眠或睡眠"""
    if os.name != 'nt':
        print("[WARN] 非 Windows 系統，跳過休眠")
        return False

    print("[INFO] 系統將在 10 秒後休眠...")
    time.sleep(10)

    if use_hibernate:
        subprocess.run(['shutdown', '/h'], capture_output=True)
    else:
        ps_cmd = 'Add-Type -AssemblyName System.Windows.Forms; [System.Windows.Forms.Application]::SetSuspendState("Suspend", $false, $false)'
        subprocess.run(['powershell', '-Command', ps_cmd], capture_output=True)

    return True


class ALASWatchdog:
    """ALAS 監控守護進程"""

    def __init__(self, alas_path, config_name='alas', config=None):
        self.alas_path = Path(alas_path).resolve()
        self.config_name = config_name
        self.config = config or CONFIG.copy()

        self.restart_count = 0
        self.last_restart_time = None

        # 設置工作目錄
        os.chdir(self.alas_path)
        sys.path.insert(0, str(self.alas_path))

    def check_webui(self):
        """檢查 WebUI 是否運行"""
        return check_port(self.config['WEBUI_PORT'])

    def check_should_restart(self):
        """檢查是否應該重啟"""
        now = datetime.now()

        # 檢查冷卻時間
        if self.last_restart_time:
            cooldown = timedelta(seconds=self.config['RESTART_COOLDOWN'])
            if now - self.last_restart_time < cooldown:
                return False, "重啟冷卻中"

        # 檢查重啟次數
        if self.restart_count >= self.config['MAX_RESTART_ATTEMPTS']:
            # 重置計數（如果已經過了冷卻時間）
            if self.last_restart_time:
                if now - self.last_restart_time > timedelta(hours=1):
                    self.restart_count = 0
                else:
                    return False, f"已達最大重啟次數 ({self.config['MAX_RESTART_ATTEMPTS']})"

        return True, None

    def restart_webui(self):
        """重啟 WebUI"""
        can_restart, reason = self.check_should_restart()
        if not can_restart:
            print(f"[WARN] 無法重啟: {reason}")
            return False

        print("[INFO] 正在重啟 WebUI...")
        self.restart_count += 1
        self.last_restart_time = datetime.now()

        # 觸發調度器自動啟動
        trigger_scheduler_start(self.config_name)

        # 啟動 WebUI
        if start_webui(str(self.alas_path)):
            print(f"[INFO] 等待 WebUI 啟動 ({self.config['WEBUI_STARTUP_WAIT']} 秒)...")
            time.sleep(self.config['WEBUI_STARTUP_WAIT'])

            if self.check_webui():
                print("[INFO] WebUI 啟動成功")
                self.restart_count = 0  # 重置計數
                return True
            else:
                print("[WARN] WebUI 啟動後未響應")

        return False

    def check_schedule_and_sleep(self):
        """檢查排程並決定是否休眠"""
        if not self.config.get('AUTO_SLEEP_ENABLED'):
            return

        schedule = get_schedule(self.config_name)
        if not schedule:
            return

        now = datetime.now()
        next_run = schedule['next_run']

        if isinstance(next_run, str):
            next_run = datetime.fromisoformat(next_run)

        wait_minutes = (next_run - now).total_seconds() / 60

        print(f"[INFO] 下一個任務: {schedule['next_task']} @ {next_run}")
        print(f"[INFO] 等待時間: {wait_minutes:.1f} 分鐘")

        # 如果等待時間足夠長，考慮休眠
        if wait_minutes > self.config['MIN_WAIT_MINUTES']:
            if schedule['pending_count'] == 0:
                print(f"[INFO] 沒有待執行任務，考慮休眠...")
                # 這裡可以調用 auto_sleep_wake.py

    def run(self):
        """主循環"""
        print("=" * 50)
        print("ALAS Watchdog")
        print("=" * 50)
        print(f"ALAS 路徑: {self.alas_path}")
        print(f"配置名稱: {self.config_name}")
        print(f"檢查間隔: {self.config['CHECK_INTERVAL']} 秒")
        print("=" * 50)

        while True:
            now = datetime.now()
            print(f"\n[{now.strftime('%H:%M:%S')}] 檢查中...")

            # 檢查 WebUI
            webui_running = self.check_webui()
            print(f"  WebUI: {'運行中' if webui_running else '未運行'}")

            if not webui_running:
                print("[WARN] WebUI 未運行，嘗試重啟...")
                self.restart_webui()
            else:
                # WebUI 運行中，檢查排程
                schedule = get_schedule(self.config_name)
                if schedule:
                    print(f"  待執行: {schedule['pending_count']}, 等待中: {schedule['waiting_count']}")
                    print(f"  下一個: {schedule['next_task']} @ {schedule['next_run']}")

                    # 如果有待執行任務但調度器可能停止，觸發啟動
                    if schedule['pending_count'] > 0:
                        trigger_scheduler_start(self.config_name)

                # 檢查是否應該休眠
                self.check_schedule_and_sleep()

            time.sleep(self.config['CHECK_INTERVAL'])


def main():
    import argparse

    parser = argparse.ArgumentParser(description='ALAS Watchdog')
    parser.add_argument('--alas-path', default='.', help='ALAS 安裝路徑')
    parser.add_argument('--config', default='alas', help='配置名稱')
    parser.add_argument('--check-interval', type=int, help='檢查間隔（秒）')
    parser.add_argument('--auto-sleep', action='store_true', help='啟用自動休眠')

    args = parser.parse_args()

    config = CONFIG.copy()
    if args.check_interval:
        config['CHECK_INTERVAL'] = args.check_interval
    if args.auto_sleep:
        config['AUTO_SLEEP_ENABLED'] = True

    watchdog = ALASWatchdog(args.alas_path, args.config, config)

    try:
        watchdog.run()
    except KeyboardInterrupt:
        print("\n[INFO] 停止監控")


if __name__ == "__main__":
    main()
