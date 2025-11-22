#!/usr/bin/env python
"""
ALAS Auto Sleep/Wake Manager v2.0
自動休眠喚醒管理器 - 完整循環版

核心改進：
1. 喚醒後自動繼續下一個休眠循環
2. 解決跨夜任務問題
3. 解決音訊佔用阻止睡眠問題
4. 支援 daemon 模式持續運行

運行方式：
    方式1 (推薦): daemon 模式持續運行
        python auto_sleep_wake.py --daemon

    方式2: 單次執行（由任務計劃喚醒後自動繼續循環）
        python auto_sleep_wake.py

    方式3: 檢查狀態
        python auto_sleep_wake.py --check-only

設置開機自啟動：
    1. Win+R → shell:startup
    2. 創建 start_alas_sleep_manager.bat:
       @echo off
       cd /d "C:\path\to\AzurLaneAutoScript"
       python scripts\auto_sleep_wake.py --daemon
"""

import os
import sys
import subprocess
import operator
import time
import json
import socket
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional, Tuple, List

# === 配置區 ===
CONFIG = {
    'DEFAULT_CONFIG_NAME': 'alas',
    'WAKE_BUFFER_MINUTES': 5,      # 提前喚醒分鐘數
    'MIN_SLEEP_MINUTES': 10,       # 最小休眠時間（小於此值不休眠）
    'MAX_SLEEP_HOURS': 8,          # 最大休眠時間（超過就不休眠，避免半夜無意義喚醒）
    'TASK_NAME': 'ALAS_AutoWake',
    'WEBUI_PORT': 22267,
    'WAIT_FOR_TASK_COMPLETE': 300, # 等待任務完成的檢查間隔（秒）
    'DAEMON_CHECK_INTERVAL': 60,   # daemon 模式檢查間隔（秒）
    'USE_HIBERNATE': False,        # True=休眠, False=睡眠
    'KILL_AUDIO_BEFORE_SLEEP': True,  # 睡眠前殺掉音訊進程
}

# 設置日誌
logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s] %(levelname)s: %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger('ALAS_SleepManager')


def setup_alas_path(alas_path: str = None):
    """設置 ALAS 路徑"""
    if alas_path and alas_path != '.':
        os.chdir(alas_path)
    sys.path.insert(0, os.getcwd())
    logger.info(f"工作目錄: {os.getcwd()}")


def get_schedule(config_name: str = 'alas') -> Tuple[Optional[datetime], List]:
    """獲取排程列表，返回下一個任務時間"""
    try:
        from module.config.config import AzurLaneConfig

        cfg = AzurLaneConfig(config_name)
        cfg.get_next_task()

        pending = cfg.pending_task
        waiting = cfg.waiting_task

        all_tasks = pending + waiting
        if not all_tasks:
            return None, []

        all_tasks = sorted(all_tasks, key=operator.attrgetter("next_run"))
        return all_tasks[0].next_run, all_tasks

    except Exception as e:
        logger.error(f"獲取排程失敗: {e}")
        return None, []


def check_webui_port(port: int = 22267) -> bool:
    """檢查 WebUI 端口是否響應"""
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(2)
        result = sock.connect_ex(('127.0.0.1', port))
        sock.close()
        return result == 0
    except:
        return False


def check_pending_tasks(config_name: str = 'alas') -> int:
    """檢查待執行任務數量"""
    try:
        from module.config.config import AzurLaneConfig
        cfg = AzurLaneConfig(config_name)
        cfg.get_next_task()
        return len(cfg.pending_task)
    except:
        return 0


def trigger_alas_start(config_name: str = 'alas') -> bool:
    """觸發 ALAS 調度器啟動"""
    reloadalas_path = Path('./config/reloadalas')
    reloadalas_path.parent.mkdir(exist_ok=True)

    with open(reloadalas_path, 'w') as f:
        f.write(f"{config_name}\n")

    logger.info(f"已寫入 reloadalas: {config_name}")
    return True


def start_webui() -> bool:
    """啟動 WebUI"""
    try:
        python_exe = sys.executable
        gui_script = Path(os.getcwd()) / 'gui.py'

        if gui_script.exists():
            if os.name == 'nt':
                subprocess.Popen(
                    [python_exe, str(gui_script)],
                    creationflags=subprocess.CREATE_NEW_CONSOLE
                )
            else:
                subprocess.Popen(
                    [python_exe, str(gui_script)],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL
                )
            logger.info("已啟動 WebUI")
            return True
    except Exception as e:
        logger.error(f"啟動 WebUI 失敗: {e}")
    return False


def create_wake_task(wake_time: datetime, alas_path: str, config_name: str) -> bool:
    """
    創建 Windows 任務計劃，設置喚醒時間
    關鍵改進：喚醒後會重新執行此腳本進入下一個循環
    """
    if os.name != 'nt':
        logger.warning("非 Windows 系統，跳過任務計劃創建")
        return False

    task_name = CONFIG['TASK_NAME']

    # 刪除舊任務
    subprocess.run(
        ['schtasks', '/delete', '/tn', task_name, '/f'],
        capture_output=True
    )

    # 處理跨夜問題：確保日期正確
    now = datetime.now()
    if wake_time < now:
        logger.warning(f"喚醒時間 {wake_time} 已過去，跳過")
        return False

    # 格式化時間（使用正確的日期格式）
    date_str = wake_time.strftime('%Y/%m/%d')
    time_str = wake_time.strftime('%H:%M:%S')

    # 腳本路徑
    script_path = Path(__file__).resolve()
    python_exe = sys.executable

    # 創建任務 - 喚醒後執行 --wake-action，它會啟動 ALAS 然後繼續下一個循環
    # 使用 cmd /c 確保路徑正確處理
    task_cmd = f'cmd /c "cd /d "{alas_path}" && "{python_exe}" "{script_path}" "{alas_path}" {config_name} --wake-action"'

    cmd = [
        'schtasks', '/create',
        '/tn', task_name,
        '/tr', task_cmd,
        '/sc', 'once',
        '/sd', date_str,
        '/st', time_str,
        '/rl', 'HIGHEST',
        '/f'
    ]

    result = subprocess.run(cmd, capture_output=True, text=True)

    if result.returncode != 0:
        logger.error(f"創建任務失敗: {result.stderr}")
        return False

    logger.info(f"已創建喚醒任務: {task_name}")
    logger.info(f"喚醒時間: {wake_time}")

    # 使用 PowerShell 設置 WakeToRun
    ps_script = f'''
try {{
    $task = Get-ScheduledTask -TaskName "{task_name}" -ErrorAction Stop
    $settings = $task.Settings
    $settings.WakeToRun = $true
    $settings.AllowStartIfOnBatteries = $true
    $settings.DontStopIfGoingOnBatteries = $true
    Set-ScheduledTask -TaskName "{task_name}" -Settings $settings
    Write-Output "OK"
}} catch {{
    Write-Output "ERROR: $_"
}}
'''
    ps_result = subprocess.run(
        ['powershell', '-Command', ps_script],
        capture_output=True,
        text=True
    )

    if 'OK' in ps_result.stdout:
        logger.info("已設置任務喚醒計算機 (WakeToRun=True)")
        return True
    else:
        logger.warning(f"設置 WakeToRun 可能失敗: {ps_result.stdout} {ps_result.stderr}")
        return True  # 任務已創建，即使 WakeToRun 設置失敗也繼續


def kill_audio_requests():
    """
    解決音訊佔用問題（Realtek 驅動常見問題）
    這會殺掉 audiodg.exe，強制釋放音訊佔用
    """
    if os.name != 'nt':
        return

    logger.info("正在釋放音訊佔用...")

    # 停止 Windows Audio 服務（會自動重啟）
    subprocess.run(
        ['net', 'stop', 'audiosrv'],
        capture_output=True
    )
    time.sleep(1)

    # 殺掉 audiodg.exe
    subprocess.run(
        ['taskkill', '/f', '/im', 'audiodg.exe'],
        capture_output=True
    )

    time.sleep(2)
    logger.info("音訊佔用已釋放")


def check_sleep_blockers() -> List[str]:
    """檢查阻止睡眠的因素"""
    blockers = []

    if os.name != 'nt':
        return blockers

    result = subprocess.run(
        ['powercfg', '/requests'],
        capture_output=True,
        text=True
    )

    output = result.stdout
    sections = ['DISPLAY:', 'SYSTEM:', 'AWAYMODE:', '執行:', 'PERFBOOST:']

    for section in sections:
        if section in output:
            idx = output.index(section)
            next_idx = len(output)
            for s in sections:
                if s != section and s in output:
                    si = output.index(s)
                    if si > idx and si < next_idx:
                        next_idx = si
            content = output[idx:next_idx]
            if '無。' not in content and '無' not in content.split('\n')[1] if len(content.split('\n')) > 1 else True:
                # 有佔用
                lines = content.strip().split('\n')
                for line in lines[1:]:
                    if line.strip() and '無' not in line:
                        blockers.append(f"{section} {line.strip()}")

    return blockers


def sleep_system(use_hibernate: bool = False) -> bool:
    """使系統進入睡眠/休眠狀態"""
    if os.name != 'nt':
        logger.warning("非 Windows 系統，無法睡眠")
        return False

    # 檢查並處理睡眠阻礙因素
    if CONFIG['KILL_AUDIO_BEFORE_SLEEP']:
        blockers = check_sleep_blockers()
        if blockers:
            logger.warning(f"檢測到睡眠阻礙: {blockers}")
            kill_audio_requests()

    logger.info(f"系統將在 5 秒後進入{'休眠' if use_hibernate else '睡眠'}...")
    time.sleep(5)

    if use_hibernate:
        result = subprocess.run(['shutdown', '/h'], capture_output=True)
    else:
        ps_cmd = 'Add-Type -AssemblyName System.Windows.Forms; [System.Windows.Forms.Application]::SetSuspendState("Suspend", $false, $false)'
        result = subprocess.run(['powershell', '-Command', ps_cmd], capture_output=True)

    return result.returncode == 0


def wait_for_alas_tasks_complete(config_name: str, timeout_minutes: int = 120) -> bool:
    """
    等待 ALAS 當前任務完成
    當沒有 pending 任務時返回 True
    """
    logger.info("等待 ALAS 任務完成...")
    start_time = time.time()
    timeout_seconds = timeout_minutes * 60

    while time.time() - start_time < timeout_seconds:
        pending_count = check_pending_tasks(config_name)

        if pending_count == 0:
            logger.info("所有待執行任務已完成")
            return True

        logger.info(f"還有 {pending_count} 個任務待執行，繼續等待...")
        time.sleep(CONFIG['WAIT_FOR_TASK_COMPLETE'])

    logger.warning("等待超時，強制進入下一階段")
    return False


def ensure_alas_running(config_name: str) -> bool:
    """確保 ALAS 正在運行"""
    webui_running = check_webui_port(CONFIG['WEBUI_PORT'])

    if not webui_running:
        logger.info("WebUI 未運行，正在啟動...")
        trigger_alas_start(config_name)
        start_webui()

        # 等待 WebUI 啟動
        for i in range(30):
            time.sleep(2)
            if check_webui_port(CONFIG['WEBUI_PORT']):
                logger.info("WebUI 已啟動")
                return True

        logger.error("WebUI 啟動失敗")
        return False

    # WebUI 運行中，確保調度器啟動
    trigger_alas_start(config_name)
    logger.info("ALAS 正在運行")
    return True


def calculate_sleep_plan(config_name: str) -> Tuple[bool, Optional[datetime], str]:
    """
    計算休眠計劃

    休眠條件很簡單：
    1. 有排程任務
    2. 下一個任務時間 > MIN_SLEEP_MINUTES（預設 10 分鐘）
    3. 下一個任務時間 < MAX_SLEEP_HOURS（預設 8 小時）

    不檢查 pending_task，因為：
    - 如果調度器在運行，它會自動執行 pending 任務
    - 如果調度器停止，pending 永遠不會清空，會導致永遠不休眠

    Returns:
        (should_sleep, wake_time, reason)
    """
    next_run, all_tasks = get_schedule(config_name)

    if not next_run:
        return False, None, "沒有排程任務"

    now = datetime.now()

    # 如果下一個任務時間已經過了（pending 狀態），找下一個 waiting 任務
    if next_run <= now:
        # 找第一個時間還沒到的任務
        for task in all_tasks:
            if task.next_run > now:
                next_run = task.next_run
                break
        else:
            # 所有任務時間都過了，不休眠（讓調度器去執行）
            return False, None, "所有任務時間已到，等待調度器執行"

    # 計算到下一個任務的時間
    wake_time = next_run - timedelta(minutes=CONFIG['WAKE_BUFFER_MINUTES'])
    sleep_minutes = (wake_time - now).total_seconds() / 60

    # 檢查最小休眠時間
    if sleep_minutes < CONFIG['MIN_SLEEP_MINUTES']:
        return False, None, f"距離下個任務只有 {sleep_minutes:.1f} 分鐘，不休眠"

    # 檢查最大休眠時間（避免半夜無意義喚醒）
    max_minutes = CONFIG['MAX_SLEEP_HOURS'] * 60
    if sleep_minutes > max_minutes:
        return False, None, f"距離下個任務 {sleep_minutes/60:.1f} 小時太長，保持運行"

    return True, wake_time, f"計劃休眠 {sleep_minutes:.1f} 分鐘，{wake_time.strftime('%H:%M')} 喚醒"


def run_single_cycle(alas_path: str, config_name: str, no_sleep: bool = False):
    """
    執行單次休眠-喚醒循環

    簡化邏輯：
    1. 確保 ALAS 運行
    2. 計算下一次喚醒時間（只看時間，不管 pending）
    3. 如果時間合適，創建喚醒任務並休眠
    """
    logger.info("=" * 50)
    logger.info("ALAS Auto Sleep/Wake Manager v2.0")
    logger.info("=" * 50)

    # 確保 ALAS 運行
    ensure_alas_running(config_name)

    # 計算休眠計劃（只基於時間條件）
    should_sleep, wake_time, reason = calculate_sleep_plan(config_name)

    logger.info(f"休眠計劃: {reason}")

    if not should_sleep:
        logger.info("不需要休眠，保持運行")
        return False

    # 顯示排程資訊
    next_run, all_tasks = get_schedule(config_name)
    logger.info(f"下一個任務: {all_tasks[0].command if all_tasks else 'N/A'} @ {next_run}")
    logger.info(f"喚醒時間: {wake_time}")

    if no_sleep:
        logger.info("--no-sleep 模式，跳過休眠")
        return False

    # 創建喚醒任務
    if not create_wake_task(wake_time, alas_path, config_name):
        logger.error("創建喚醒任務失敗，取消休眠")
        return False

    # 進入睡眠
    sleep_system(use_hibernate=CONFIG['USE_HIBERNATE'])
    return True


def wake_action(alas_path: str, config_name: str):
    """
    喚醒後執行的動作
    關鍵：執行完後會自動進入下一個循環
    """
    logger.info("=" * 50)
    logger.info("系統已喚醒 - 執行喚醒動作")
    logger.info("=" * 50)

    setup_alas_path(alas_path)

    # 等待系統完全啟動（網路、服務等）
    logger.info("等待系統完全啟動 (15秒)...")
    time.sleep(15)

    # 確保 ALAS 運行
    ensure_alas_running(config_name)

    # 等待一段時間讓任務開始執行
    logger.info("等待任務開始執行 (60秒)...")
    time.sleep(60)

    # 進入下一個循環
    logger.info("進入下一個休眠循環...")
    run_single_cycle(alas_path, config_name)


def daemon_mode(alas_path: str, config_name: str):
    """
    Daemon 模式：持續運行，自動管理休眠循環

    這是最推薦的運行方式，不依賴任務計劃喚醒
    而是持續監控並在適當時機觸發休眠
    """
    logger.info("=" * 50)
    logger.info("ALAS Auto Sleep/Wake Manager - Daemon Mode")
    logger.info("=" * 50)
    logger.info(f"ALAS 路徑: {alas_path}")
    logger.info(f"配置名稱: {config_name}")
    logger.info(f"檢查間隔: {CONFIG['DAEMON_CHECK_INTERVAL']} 秒")
    logger.info("=" * 50)

    setup_alas_path(alas_path)

    while True:
        try:
            now = datetime.now()
            logger.info(f"[{now.strftime('%H:%M:%S')}] 檢查狀態...")

            # 確保 ALAS 運行
            ensure_alas_running(config_name)

            # 檢查休眠計劃
            should_sleep, wake_time, reason = calculate_sleep_plan(config_name)
            logger.info(f"  {reason}")

            if should_sleep:
                logger.info("條件滿足，準備進入休眠...")

                # 創建喚醒任務（喚醒後會重啟這個 daemon）
                create_wake_task(wake_time, alas_path, config_name)

                # 創建一個標記文件，讓喚醒後的腳本知道要繼續 daemon 模式
                daemon_flag = Path(alas_path) / 'config' / '.daemon_mode'
                daemon_flag.write_text(config_name)

                # 進入睡眠
                sleep_system(use_hibernate=CONFIG['USE_HIBERNATE'])

                # 如果睡眠失敗（沒有真正睡著），繼續循環
                logger.info("睡眠命令已執行，如果沒有睡著則繼續監控...")

            time.sleep(CONFIG['DAEMON_CHECK_INTERVAL'])

        except KeyboardInterrupt:
            logger.info("收到中斷信號，停止 daemon")
            break
        except Exception as e:
            logger.error(f"發生錯誤: {e}")
            time.sleep(60)


def main():
    import argparse

    parser = argparse.ArgumentParser(
        description='ALAS Auto Sleep/Wake Manager v2.0',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog='''
使用範例:
    # Daemon 模式（推薦，持續運行）
    python auto_sleep_wake.py --daemon

    # 單次執行
    python auto_sleep_wake.py

    # 只檢查狀態
    python auto_sleep_wake.py --check-only

    # 指定 ALAS 路徑
    python auto_sleep_wake.py "C:\\ALAS" --daemon
        '''
    )
    parser.add_argument('alas_path', nargs='?', default='.', help='ALAS 安裝路徑')
    parser.add_argument('config_name', nargs='?', default='alas', help='配置名稱')
    parser.add_argument('--wake-action', action='store_true', help='執行喚醒動作（由任務計劃調用）')
    parser.add_argument('--daemon', action='store_true', help='Daemon 模式持續運行')
    parser.add_argument('--no-sleep', action='store_true', help='只設置喚醒任務，不休眠')
    parser.add_argument('--use-hibernate', action='store_true', help='使用休眠而非睡眠')
    parser.add_argument('--check-only', action='store_true', help='只檢查狀態')

    args = parser.parse_args()

    # 更新配置
    if args.use_hibernate:
        CONFIG['USE_HIBERNATE'] = True

    alas_path = str(Path(args.alas_path).resolve())

    # 檢查是否有 daemon 標記文件（從睡眠喚醒後）
    daemon_flag = Path(alas_path) / 'config' / '.daemon_mode'
    if daemon_flag.exists() and not args.wake_action:
        config_name = daemon_flag.read_text().strip() or args.config_name
        daemon_flag.unlink()
        logger.info("檢測到 daemon 標記，恢復 daemon 模式")
        daemon_mode(alas_path, config_name)
        return

    # 喚醒動作模式
    if args.wake_action:
        wake_action(alas_path, args.config_name)
        return

    # 設置路徑
    setup_alas_path(alas_path)

    # 檢查模式
    if args.check_only:
        logger.info("=" * 50)
        logger.info("狀態檢查")
        logger.info("=" * 50)

        webui_ok = check_webui_port(CONFIG['WEBUI_PORT'])
        logger.info(f"WebUI 端口 ({CONFIG['WEBUI_PORT']}): {'運行中' if webui_ok else '未響應'}")

        next_run, all_tasks = get_schedule(args.config_name)
        if next_run:
            logger.info(f"下一個任務: {all_tasks[0].command} @ {next_run}")
        else:
            logger.info("沒有排程任務")

        pending = check_pending_tasks(args.config_name)
        logger.info(f"待執行任務: {pending}")

        should_sleep, wake_time, reason = calculate_sleep_plan(args.config_name)
        logger.info(f"休眠計劃: {reason}")

        blockers = check_sleep_blockers()
        if blockers:
            logger.warning(f"睡眠阻礙: {blockers}")
        else:
            logger.info("無睡眠阻礙")

        return

    # Daemon 模式
    if args.daemon:
        daemon_mode(alas_path, args.config_name)
        return

    # 單次執行模式
    run_single_cycle(alas_path, args.config_name, args.no_sleep)


if __name__ == "__main__":
    main()
