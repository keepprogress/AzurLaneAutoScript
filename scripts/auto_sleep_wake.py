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
    方式1 (推薦): daemon 模式持續運行（監控 + 自動休眠）
        python auto_sleep_wake.py --daemon

    方式2: 純監控模式（只監控調度器，停止後自動重啟，不休眠）
        python auto_sleep_wake.py --monitor-only

    方式3: 單次執行（由任務計劃喚醒後自動繼續循環）
        python auto_sleep_wake.py

    方式4: 檢查狀態
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
    'MAX_SLEEP_HOURS': 0,          # 最大休眠時間，0=無限制（不限制休眠多久）
    'TASK_NAME': 'ALAS_AutoWake',
    'WEBUI_PORT': 22267,
    'WAIT_FOR_TASK_COMPLETE': 300, # 等待任務完成的檢查間隔（秒）
    'DAEMON_CHECK_INTERVAL': 60,   # daemon 模式檢查間隔（秒）
    'USE_HIBERNATE': False,        # True=休眠, False=睡眠
    'KILL_AUDIO_BEFORE_SLEEP': True,  # 睡眠前殺掉音訊進程
    'RESTART_DELAY_SECONDS': 5,       # 監控模式：檢測到停止後等待重啟的秒數

    # === 自動休眠時間限制 ===
    'AUTO_SLEEP_SCHEDULE': True,   # 啟用時間限制
    'SLEEP_DAYS': [0, 1, 2, 3, 4, 5], # 允許自動休眠的星期（0=週一, 4=週五）
    'SLEEP_START_HOUR': 1,         # 開始自動休眠的時間（01:00）
    'SLEEP_END_HOUR': 19,          # 結束自動休眠的時間（19:00）
    # 說明：週一到週五 01:00-19:00 之間會自動休眠，其他時間不休眠
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


def check_scheduler_running(config_name: str = 'alas') -> bool:
    """
    檢查是否有正在運行中的任務（從配置文件判斷）

    ALAS UI 邏輯：
    - pending_task[0] = 運行中（如果調度器進程活著）
    - pending_task[1:] = 隊列中
    - waiting_task = 等待中

    從配置判斷：
    - 有 pending 任務 = 有任務正在運行或等待執行
    - 沒有 pending 任務 = 運行中無任務

    Returns:
        True = 有任務在運行中（pending > 0）
        False = 運行中無任務（pending == 0）
    """
    return check_pending_tasks(config_name) > 0


def is_within_sleep_schedule() -> Tuple[bool, str]:
    """
    檢查當前時間是否在允許自動休眠的時段內

    Returns:
        (is_allowed, reason)
    """
    if not CONFIG['AUTO_SLEEP_SCHEDULE']:
        return True, "時間限制已停用"

    now = datetime.now()
    current_day = now.weekday()  # 0=週一, 6=週日
    current_hour = now.hour

    # 檢查星期
    if current_day not in CONFIG['SLEEP_DAYS']:
        day_names = ['週一', '週二', '週三', '週四', '週五', '週六', '週日']
        return False, f"今天是{day_names[current_day]}，不在自動休眠日"

    # 檢查時間
    start_hour = CONFIG['SLEEP_START_HOUR']
    end_hour = CONFIG['SLEEP_END_HOUR']

    if start_hour <= end_hour:
        # 正常情況：例如 01:00 - 19:00
        in_range = start_hour <= current_hour < end_hour
    else:
        # 跨夜情況：例如 22:00 - 06:00
        in_range = current_hour >= start_hour or current_hour < end_hour

    if not in_range:
        return False, f"當前 {now.strftime('%H:%M')} 不在自動休眠時段 ({start_hour:02d}:00-{end_hour:02d}:00)"

    return True, f"在自動休眠時段內 ({start_hour:02d}:00-{end_hour:02d}:00)"


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


def click_webui_start_button(config_name: str = 'alas') -> bool:
    """
    通過 win32gui + 相對座標點擊 WebUI 的「啟動」按鈕

    使用 win32gui 查找窗口，然後用相對座標點擊
    ALAS WebUI 的啟動按鈕位置固定：水平 43%，垂直 10%
    """
    try:
        import win32gui
        import win32con
        import pyautogui
    except ImportError:
        logger.error("需要安裝 pyautogui 和 pywin32: pip install pyautogui pywin32")
        return False

    try:
        # 查找 ALAS 窗口
        hwnd = win32gui.FindWindow(None, "Alas")
        if not hwnd:
            # 部分匹配搜索
            def enum_handler(hw, extra):
                if win32gui.IsWindowVisible(hw):
                    title = win32gui.GetWindowText(hw)
                    if 'Alas' in title or 'ALAS' in title:
                        extra.append((hw, title))
                return True

            hwnds = []
            win32gui.EnumWindows(enum_handler, hwnds)
            if hwnds:
                hwnd, title = hwnds[0]
                logger.info(f"找到窗口: {title}")
            else:
                logger.error("找不到 ALAS 窗口")
                return False

        # 激活窗口
        try:
            if win32gui.IsIconic(hwnd):
                win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
            win32gui.SetForegroundWindow(hwnd)
            time.sleep(0.3)
        except Exception as e:
            logger.warning(f"激活窗口失敗: {e}")

        # 使用相對座標點擊啟動按鈕
        rect = win32gui.GetWindowRect(hwnd)
        left, top, right, bottom = rect
        width = right - left
        height = bottom - top

        # ALAS WebUI 的啟動按鈕位置：水平 43%，垂直 10%
        button_x = left + int(width * 0.43)
        button_y = top + int(height * 0.10)

        logger.info(f"點擊啟動按鈕: ({button_x}, {button_y})")
        pyautogui.click(button_x, button_y)
        return True

    except Exception as e:
        logger.error(f"GUI 點擊失敗: {e}")
        return False


def start_scheduler_via_processmanager(config_name: str = 'alas') -> bool:
    """
    通過 ProcessManager 直接啟動調度器（最快、最可靠）

    這是最推薦的方式：
    - 毫秒級響應（無需啟動新進程）
    - 100% 成功率（直接調用內部 API）
    - 不依賴 GUI/WebUI
    - 支援任意配置名稱

    注意：需要 State.init() 初始化 multiprocessing.Manager
    """
    try:
        from module.webui.process_manager import ProcessManager
        from module.webui.setting import State

        # 確保 State 已初始化
        if not State._init:
            logger.info("初始化 State.manager...")
            State.init()

        # 獲取或創建 ProcessManager
        manager = ProcessManager.get_manager(config_name)

        # 檢查是否已在運行
        if manager.alive:
            logger.info(f"調度器 [{config_name}] 已在運行（ProcessManager）")
            return True

        # 啟動調度器
        logger.info(f"通過 ProcessManager 啟動調度器 [{config_name}]...")
        manager.start(func=None)  # None 會自動使用 get_config_mod(config_name)

        # 等待啟動
        for i in range(10):
            time.sleep(0.5)
            if manager.alive:
                logger.info(f"調度器 [{config_name}] 已成功啟動（ProcessManager）")
                return True

        logger.warning("ProcessManager 啟動超時，調度器可能未成功啟動")
        return False

    except ImportError as e:
        logger.warning(f"無法導入 ProcessManager 模組: {e}")
        return False
    except Exception as e:
        logger.error(f"ProcessManager 啟動失敗: {e}")
        import traceback
        logger.debug(traceback.format_exc())
        return False


def start_scheduler_directly(config_name: str = 'alas', debug: bool = True) -> bool:
    """
    直接啟動 ALAS 調度器（不依賴 WebUI）- 備援方案

    這是備援方式，直接執行 alas.py
    注意：alas.py 不接受命令行參數，默認使用 'alas' 配置
    如果需要其他配置，需要通過 WebUI 或 ProcessManager 啟動
    """
    try:
        # 優先使用 ALAS 自帶的 Python，而不是系統 Python
        alas_python = Path(os.getcwd()) / 'toolkit' / 'python.exe'
        if alas_python.exists():
            python_exe = str(alas_python)
        else:
            # 備選：嘗試其他可能的位置
            alas_python_alt = Path(os.getcwd()) / 'python' / 'python.exe'
            if alas_python_alt.exists():
                python_exe = str(alas_python_alt)
            else:
                python_exe = sys.executable
                logger.warning(f"找不到 ALAS 自帶 Python，使用系統 Python: {python_exe}")

        alas_script = Path(os.getcwd()) / 'alas.py'

        if debug:
            logger.info(f"[DEBUG] Python 執行檔: {python_exe}")
            logger.info(f"[DEBUG] alas.py 路徑: {alas_script}")
            logger.info(f"[DEBUG] 工作目錄: {os.getcwd()}")

        if not alas_script.exists():
            logger.error(f"找不到 alas.py: {alas_script}")
            return False

        # 注意：alas.py 默認只使用 'alas' 配置
        if config_name != 'alas':
            logger.warning(f"alas.py 只支援 'alas' 配置，當前請求 '{config_name}'")
            logger.warning("如需使用其他配置，請通過 WebUI 啟動")

        if os.name == 'nt':
            # Windows: 新視窗執行
            logger.info(f"[DEBUG] 執行命令: {python_exe} {alas_script}")
            process = subprocess.Popen(
                [python_exe, str(alas_script)],
                creationflags=subprocess.CREATE_NEW_CONSOLE,
                cwd=os.getcwd()
            )
            logger.info(f"[DEBUG] 進程已啟動, PID: {process.pid}")

            # 等待一小段時間，檢查進程是否立即退出
            import time
            time.sleep(2)

            poll_result = process.poll()
            if poll_result is not None:
                logger.error(f"[DEBUG] 進程已退出，返回碼: {poll_result}")
                return False
            else:
                logger.info(f"[DEBUG] 進程仍在運行")
        else:
            process = subprocess.Popen(
                [python_exe, str(alas_script)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                cwd=os.getcwd()
            )
            logger.info(f"[DEBUG] 進程已啟動, PID: {process.pid}")

        logger.info(f"已直接啟動調度器")
        return True
    except Exception as e:
        logger.error(f"啟動調度器失敗: {e}")
        import traceback
        logger.error(f"[DEBUG] 詳細錯誤: {traceback.format_exc()}")
        return False


def list_alas_related_processes():
    """
    列出所有可能與 ALAS 相關的進程（用於調試）
    """
    try:
        import psutil
        logger.info("=== 搜索 ALAS 相關進程 ===")
        found = []
        for proc in psutil.process_iter(['pid', 'name', 'cmdline']):
            try:
                cmdline = proc.info.get('cmdline', []) or []
                if not cmdline:
                    continue
                cmdline_str = ' '.join(cmdline).lower()

                # 查找包含 alas 或 azurlane 的進程
                if 'alas' in cmdline_str or 'azurlane' in cmdline_str:
                    found.append({
                        'pid': proc.pid,
                        'name': proc.name(),
                        'cmdline': cmdline
                    })
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue

        if found:
            for p in found:
                logger.info(f"  PID: {p['pid']}, Name: {p['name']}")
                logger.info(f"    Cmdline: {p['cmdline']}")
        else:
            logger.info("  沒有找到相關進程")
        logger.info("=" * 30)
        return found
    except Exception as e:
        logger.error(f"列出進程失敗: {e}")
        return []


def check_alas_process_running(config_name: str = 'alas', debug: bool = False) -> bool:
    """
    檢查 ALAS 調度器進程是否在運行

    檢測方式：
    1. 找 alas.py 進程（直接啟動）
    2. 找 ProcessManager 啟動的調度器進程（通過進程樹深度判斷）

    WebUI 進程結構：
    - gui.py (WebUI 主進程)
      └── spawn 進程 (ProcessManager 創建，用於調度器)
           └── spawn 進程 (實際執行任務的進程)

    只有當存在 gui.py → spawn → spawn 這樣的三層結構時，才認為調度器在運行

    Returns:
        True = 調度器進程正在運行
        False = 調度器進程未運行
    """
    try:
        import psutil

        # 方法1：檢查 alas.py 直接啟動的進程
        for proc in psutil.process_iter(['pid', 'name', 'cmdline']):
            try:
                cmdline = proc.info.get('cmdline', []) or []
                if not cmdline:
                    continue

                cmdline_str = ' '.join(cmdline).lower()

                # 排除這個腳本本身和其他輔助腳本
                if any(x in cmdline_str for x in ['auto_sleep_wake', 'check_running_task', 'alas_watchdog']):
                    continue

                # 檢查是否是 alas.py 進程（直接啟動）
                if 'alas.py' in cmdline_str and 'python' in cmdline_str and 'gui.py' not in cmdline_str:
                    if debug:
                        logger.debug(f"找到 alas.py 進程: PID={proc.pid}")
                    return True

            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue

        # 方法2：檢查 WebUI 啟動的調度器
        # 查找 gui.py 進程
        gui_process = None
        for proc in psutil.process_iter(['pid', 'name', 'cmdline']):
            try:
                cmdline = proc.info.get('cmdline', []) or []
                cmdline_str = ' '.join(cmdline).lower()
                if 'gui.py' in cmdline_str:
                    gui_process = proc
                    break
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue

        if gui_process:
            # 查找 gui.py 的子進程
            try:
                children = gui_process.children(recursive=False)
                for child in children:
                    try:
                        child_cmdline = ' '.join(child.cmdline() or []).lower()
                        # 檢查是否是 multiprocessing spawn 進程
                        if 'multiprocessing' in child_cmdline and 'spawn' in child_cmdline:
                            # 檢查這個 spawn 進程的子進程數量
                            # WebUI 總是有 1 個基礎子進程（用於日誌等）
                            # 調度器啟動後會多 1 個子進程
                            # 所以：子進程數 > 1 = 調度器運行中
                            grandchildren = child.children(recursive=False)
                            grandchild_count = len(grandchildren)
                            if debug:
                                logger.debug(f"spawn 進程 PID={child.pid}, 子進程數={grandchild_count}")
                            if grandchild_count > 1:
                                logger.debug(f"找到調度器進程: 子進程數={grandchild_count} > 1")
                                return True
                    except (psutil.NoSuchProcess, psutil.AccessDenied):
                        continue
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass

        return False
    except ImportError:
        logger.warning("psutil 未安裝，無法檢測進程狀態")
        return False
    except Exception as e:
        logger.warning(f"檢測進程狀態失敗: {e}")
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
    """
    使系統進入睡眠/休眠狀態

    關鍵：睡眠後不能再執行任何代碼！
    用超長 sleep 卡住，防止極少數「假睡」情況下繼續執行
    """
    if os.name != 'nt':
        logger.warning("非 Windows 系統，無法睡眠")
        return False

    # 檢查並處理睡眠阻礙因素
    if CONFIG['KILL_AUDIO_BEFORE_SLEEP']:
        blockers = check_sleep_blockers()
        if blockers:
            logger.warning(f"檢測到睡眠阻礙: {blockers}")
            kill_audio_requests()

    logger.info(f"系統即將進入{'休眠' if use_hibernate else '睡眠'}，5秒後執行...")
    time.sleep(5)

    if use_hibernate:
        os.system('shutdown /h /f')
    else:
        # 最可靠的 S3 睡眠方式，使用 ctypes 直接調用 Windows API
        import ctypes
        ctypes.windll.PowrProf.SetSuspendState(0, 1, 0)

    # 絕對不能再執行任何程式碼！否則假睡時會繼續跑
    # 用一個超長 sleep 卡住，防止極少數「假睡」情況下繼續執行
    # 真正睡著後這行永遠不會執行到
    time.sleep(999999999)
    return True


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
    """
    確保 ALAS 調度器正在運行

    啟動優先順序：
    1. GUI 點擊（如果 WebUI 運行中）
    2. ProcessManager（GUI 失敗時備援）
    3. 直接執行 alas.py（最終備援）
    4. 啟動 WebUI + reloadalas
    """
    # 檢查調度器進程是否在運行
    if check_alas_process_running(config_name):
        logger.info(f"調度器 [{config_name}] 已在運行")
        return True

    # 調度器未運行，啟動它
    logger.info(f"調度器 [{config_name}] 未運行，正在啟動...")

    # 方法1：GUI 點擊（如果 WebUI 運行中）
    webui_running = check_webui_port(CONFIG['WEBUI_PORT'])
    if webui_running:
        logger.info("嘗試方法1: GUI 點擊啟動按鈕...")
        if click_webui_start_button(config_name):
            time.sleep(3)
            if check_alas_process_running(config_name):
                logger.info(f"調度器 [{config_name}] 已啟動（GUI 點擊）")
                return True

    # 方法2：ProcessManager（GUI 失敗時備援）
    logger.info("嘗試方法2: ProcessManager 直接啟動...")
    if start_scheduler_via_processmanager(config_name):
        time.sleep(2)
        if check_alas_process_running(config_name):
            logger.info(f"調度器 [{config_name}] 已啟動（ProcessManager）")
            return True

    # 方法3：直接啟動 alas.py（最終備援）
    logger.info("嘗試方法3: 直接啟動 alas.py...")
    if start_scheduler_directly(config_name):
        for i in range(15):
            time.sleep(2)
            if check_alas_process_running(config_name):
                logger.info(f"調度器 [{config_name}] 已啟動（直接執行）")
                return True

    # 方法4：啟動 WebUI + reloadalas
    if not webui_running:
        logger.info("嘗試方法4: 啟動 WebUI...")
        trigger_alas_start(config_name)
        start_webui()

        for i in range(30):
            time.sleep(2)
            if check_webui_port(CONFIG['WEBUI_PORT']):
                logger.info("WebUI 已啟動，調度器應該會自動啟動")
                return True

        logger.error("WebUI 啟動失敗")

    logger.warning("所有啟動方法都失敗，請手動在 WebUI 點擊啟動按鈕")
    return False


def calculate_sleep_plan(config_name: str) -> Tuple[bool, Optional[datetime], str]:
    """
    計算休眠計劃

    休眠條件（全部滿足才休眠）：
    0. 在允許自動休眠的時段內（預設：週一到週五 01:00-19:00）
    1. 運行中/隊列中無任務（pending_task == 0）
    2. 等待中有任務（waiting_task > 0）
    3. 等待中的第一個任務距離現在 > MIN_SLEEP_MINUTES（預設 10 分鐘）

    ALAS 任務狀態說明（從 alas.json 配置判斷）：
    - pending_task[0] = 運行中（UI 顯示）
    - pending_task[1:] = 隊列中（UI 顯示）
    - waiting_task = 等待中
    - 只要 pending > 0，就表示有任務需要執行，不應休眠

    Returns:
        (should_sleep, wake_time, reason)
    """
    # 條件 0：檢查是否在允許自動休眠的時段內
    schedule_ok, schedule_reason = is_within_sleep_schedule()
    if not schedule_ok:
        return False, None, schedule_reason

    try:
        from module.config.config import AzurLaneConfig

        cfg = AzurLaneConfig(config_name)
        cfg.get_next_task()

        pending = cfg.pending_task   # 時間已到（運行中 + 隊列中）
        waiting = cfg.waiting_task   # 時間未到（等待中）

    except Exception as e:
        logger.error(f"獲取排程失敗: {e}")
        return False, None, f"獲取排程失敗: {e}"

    now = datetime.now()

    # 條件 1：運行中/隊列中無任務（pending == 0）
    if len(pending) > 0:
        task_names = [t.command for t in pending[:3]]
        return False, None, f"有 {len(pending)} 個任務運行中/待執行: {task_names}"

    # 條件 2：必須有等待中的任務
    if len(waiting) == 0:
        return False, None, "沒有等待中的任務"

    # 取第一個等待中的任務（已按時間排序）
    next_task = waiting[0]
    next_run = next_task.next_run

    # 計算喚醒時間（提前 WAKE_BUFFER_MINUTES 分鐘）
    wake_time = next_run - timedelta(minutes=CONFIG['WAKE_BUFFER_MINUTES'])
    sleep_minutes = (wake_time - now).total_seconds() / 60

    # 條件 3：等待中任務必須 > MIN_SLEEP_MINUTES 分鐘
    if sleep_minutes < CONFIG['MIN_SLEEP_MINUTES']:
        return False, None, f"下個任務 {next_task.command} 只剩 {sleep_minutes:.1f} 分鐘，不休眠"

    # 可選條件：最大休眠時間限制（0 = 無限制）
    if CONFIG['MAX_SLEEP_HOURS'] > 0:
        max_minutes = CONFIG['MAX_SLEEP_HOURS'] * 60
        if sleep_minutes > max_minutes:
            return False, None, f"下個任務 {next_task.command} 在 {sleep_minutes/60:.1f} 小時後，超過限制不休眠"

    # 格式化顯示
    if sleep_minutes >= 60:
        duration_str = f"{sleep_minutes/60:.1f} 小時"
    else:
        duration_str = f"{sleep_minutes:.0f} 分鐘"

    return True, wake_time, f"可休眠 {duration_str}，{wake_time.strftime('%H:%M')} 喚醒執行 {next_task.command}"


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

    關鍵改進：直接進入永久 daemon 模式，而不是只跑一次循環
    這樣才能實現真正的永續自動化
    """
    logger.info("=" * 50)
    logger.info("系統已喚醒 → 自動接力進入永久 daemon 模式")
    logger.info("=" * 50)

    setup_alas_path(alas_path)

    # 等待系統完全啟動（網路、服務等）
    logger.info("等待系統完全啟動 (15秒)...")
    time.sleep(15)

    # 確保 ALAS 運行
    ensure_alas_running(config_name)

    # 等待一段時間讓剛醒來的任務有時間進入 pending → running
    logger.info("等待任務開始執行 (60秒)...")
    time.sleep(60)

    # 直接進入無限 daemon，永不結束！
    # 這是關鍵：確保喚醒後能持續自動休眠循環
    logger.info("進入永久 daemon 模式...")
    daemon_mode(alas_path, config_name)


def daemon_mode(alas_path: str, config_name: str):
    """
    Daemon 模式：持續運行，自動管理休眠循環

    這是最推薦的運行方式：
    1. 持續監控 ALAS 狀態
    2. 條件滿足時創建喚醒任務並休眠
    3. 喚醒後由 wake_action() 重新進入此函數，實現永續循環

    注意：sleep_system() 內有無限阻塞，真正睡著後不會返回
    """
    logger.info("=" * 50)
    logger.info("=== 永久 Daemon 模式啟動，從此無需手動干預 ===")
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

            if should_sleep and wake_time:
                logger.info("條件滿足，準備進入休眠...")

                # 創建喚醒任務（喚醒後執行 --wake-action，會重新進入 daemon_mode）
                if not create_wake_task(wake_time, alas_path, config_name):
                    logger.error("創建喚醒任務失敗，跳過本次休眠")
                    time.sleep(CONFIG['DAEMON_CHECK_INTERVAL'])
                    continue

                # 進入睡眠（內部有無限阻塞，真正睡著後不會返回）
                sleep_system(use_hibernate=CONFIG['USE_HIBERNATE'])

                # 如果執行到這裡，說明沒有真正睡著（極少數情況）
                # 等待一段時間後繼續循環
                logger.warning("睡眠命令執行但系統未睡著，30秒後重試...")
                time.sleep(30)
            else:
                time.sleep(CONFIG['DAEMON_CHECK_INTERVAL'])

        except KeyboardInterrupt:
            logger.info("收到中斷信號，停止 daemon")
            break
        except Exception as e:
            logger.error(f"發生錯誤: {e}")
            time.sleep(60)


def monitor_mode(alas_path: str, config_name: str):
    """
    純監控模式：只監控調度器狀態，停止後自動重啟（不休眠）

    功能：
    1. 持續監控調度器進程是否運行
    2. 檢測到停止後等待 RESTART_DELAY_SECONDS 秒再重啟
    3. 不執行任何休眠相關邏輯

    使用方式：
        python auto_sleep_wake.py --monitor-only
    """
    logger.info("=" * 50)
    logger.info("=== 純監控模式啟動（只監控重啟，不休眠）===")
    logger.info("=" * 50)
    logger.info(f"ALAS 路徑: {alas_path}")
    logger.info(f"配置名稱: {config_name}")
    logger.info(f"檢查間隔: {CONFIG['DAEMON_CHECK_INTERVAL']} 秒")
    logger.info("=" * 50)

    setup_alas_path(alas_path)

    # 啟動時列出所有相關進程（調試用）
    logger.info("首次啟動，列出所有 ALAS 相關進程...")
    list_alas_related_processes()

    # 追蹤上一次狀態，用於日誌輸出
    last_running_state = None
    check_count = 0

    while True:
        try:
            now = datetime.now()
            is_running = check_alas_process_running(config_name)
            check_count += 1

            # 每次檢查都輸出日誌
            status_str = "運行中" if is_running else "已停止"
            logger.info(f"[{now.strftime('%H:%M:%S')}] 第 {check_count} 次檢查: 調度器{status_str}")

            # 狀態變化時額外提醒
            if last_running_state is not None and is_running != last_running_state:
                if is_running:
                    logger.info(">>> 狀態變化: 調度器已啟動")
                else:
                    logger.warning(">>> 狀態變化: 調度器已停止！")
            last_running_state = is_running

            if not is_running:
                # 調度器停止，立即重啟
                if not check_alas_process_running(config_name):
                    logger.info("正在重啟調度器...")
                    started = False

                    # 方法1：GUI 點擊（如果 WebUI 運行中）
                    webui_running = check_webui_port(CONFIG['WEBUI_PORT'])
                    if webui_running:
                        logger.info("嘗試方法1: GUI 點擊啟動按鈕...")
                        if click_webui_start_button(config_name):
                            time.sleep(3)
                            if check_alas_process_running(config_name):
                                logger.info("調度器已成功重啟（GUI 點擊）")
                                last_running_state = True
                                started = True

                    # 方法2：ProcessManager（GUI 失敗時備援）
                    if not started:
                        logger.info("嘗試方法2: ProcessManager 直接啟動...")
                        if start_scheduler_via_processmanager(config_name):
                            time.sleep(2)
                            if check_alas_process_running(config_name):
                                logger.info("調度器已成功重啟（ProcessManager）")
                                last_running_state = True
                                started = True

                    # 方法3：直接啟動 alas.py（最終備援）
                    if not started:
                        logger.info("嘗試方法3: 直接啟動 alas.py...")
                        if start_scheduler_directly(config_name):
                            for i in range(10):
                                time.sleep(2)
                                if check_alas_process_running(config_name):
                                    logger.info("調度器已成功重啟（直接執行）")
                                    last_running_state = True
                                    started = True
                                    break

                    if not started:
                        logger.error("所有啟動方法都失敗，將在下一個循環重試")
                else:
                    logger.info("調度器已被其他方式啟動")
                    last_running_state = True

            time.sleep(CONFIG['DAEMON_CHECK_INTERVAL'])

        except KeyboardInterrupt:
            logger.info("收到中斷信號，停止監控")
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
    # Daemon 模式（推薦，監控 + 自動休眠）
    python auto_sleep_wake.py --daemon

    # 純監控模式（只監控重啟，不休眠）
    python auto_sleep_wake.py --monitor-only

    # 單次執行
    python auto_sleep_wake.py

    # 只檢查狀態
    python auto_sleep_wake.py --check-only

    # 指定 ALAS 路徑
    python auto_sleep_wake.py "C:\\ALAS" --daemon
    python auto_sleep_wake.py "C:\\ALAS" --monitor-only
        '''
    )
    parser.add_argument('alas_path', nargs='?', default='.', help='ALAS 安裝路徑')
    parser.add_argument('config_name', nargs='?', default='alas', help='配置名稱')
    parser.add_argument('--wake-action', action='store_true', help='執行喚醒動作（由任務計劃調用）')
    parser.add_argument('--daemon', action='store_true', help='Daemon 模式持續運行（監控 + 自動休眠）')
    parser.add_argument('--monitor-only', action='store_true', help='只監控調度器狀態，停止後自動重啟（不休眠）')
    parser.add_argument('--no-sleep', action='store_true', help='只設置喚醒任務，不休眠')
    parser.add_argument('--use-hibernate', action='store_true', help='使用休眠而非睡眠')
    parser.add_argument('--check-only', action='store_true', help='只檢查狀態')

    args = parser.parse_args()

    # 更新配置
    if args.use_hibernate:
        CONFIG['USE_HIBERNATE'] = True

    alas_path = str(Path(args.alas_path).resolve())

    # 喚醒動作模式（由任務計劃調用，會自動進入 daemon_mode）
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

    # 純監控模式（只監控重啟，不休眠）
    if args.monitor_only:
        monitor_mode(alas_path, args.config_name)
        return

    # Daemon 模式（監控 + 自動休眠）
    if args.daemon:
        daemon_mode(alas_path, args.config_name)
        return

    # 單次執行模式
    run_single_cycle(alas_path, args.config_name, args.no_sleep)


if __name__ == "__main__":
    main()
