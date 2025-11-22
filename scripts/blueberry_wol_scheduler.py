#!/usr/bin/env python3
"""
BlueberryPie WoL Scheduler
在 BlueberryPie (Raspberry Pi) 上運行，負責：
1. 從 Windows 主機獲取排程
2. 在指定時間發送 WoL 喚醒封包

部署在 BlueberryPie 上：
    pip3 install wakeonlan requests
    python3 blueberry_wol_scheduler.py

配置：
    編輯下方的 CONFIG 區塊
"""

import socket
import struct
import time
import json
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

# === 配置區 ===
CONFIG = {
    # Windows 主機 MAC 地址（用於 WoL）
    'TARGET_MAC': 'AA:BB:CC:DD:EE:FF',  # 請替換為你的 Windows MAC 地址

    # Windows 主機 IP（用於獲取排程）
    'WINDOWS_IP': '192.168.1.100',  # 請替換

    # ALAS WebUI 端口
    'WEBUI_PORT': 22267,

    # 排程文件路徑（如果使用 SMB 共享）
    'SCHEDULE_FILE': None,  # '/mnt/windows/alas_schedule.json'

    # 提前喚醒時間（分鐘）
    'WAKE_BUFFER_MINUTES': 5,

    # 檢查間隔（秒）
    'CHECK_INTERVAL': 60,

    # 廣播地址（通常是 255.255.255.255 或子網廣播）
    'BROADCAST_IP': '255.255.255.255',

    # WoL 端口
    'WOL_PORT': 9,
}


def send_wol(mac_address, broadcast='255.255.255.255', port=9):
    """
    發送 Wake-on-LAN 魔術封包

    Args:
        mac_address: MAC 地址，格式 'AA:BB:CC:DD:EE:FF' 或 'AA-BB-CC-DD-EE-FF'
        broadcast: 廣播地址
        port: 目標端口（通常是 7 或 9）
    """
    # 清理 MAC 地址
    mac = mac_address.replace(':', '').replace('-', '').upper()

    if len(mac) != 12:
        raise ValueError(f"無效的 MAC 地址: {mac_address}")

    # 構建魔術封包：6 個 0xFF + 16 次 MAC 地址
    mac_bytes = bytes.fromhex(mac)
    magic_packet = b'\xff' * 6 + mac_bytes * 16

    # 發送 UDP 封包
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    sock.sendto(magic_packet, (broadcast, port))
    sock.close()

    print(f"[WoL] 已發送喚醒封包到 {mac_address}")


def read_schedule_from_file(file_path):
    """從本地文件讀取排程（用於 SMB 共享）"""
    try:
        with open(file_path, 'r') as f:
            data = json.load(f)
        return data.get('next_run'), data.get('tasks', [])
    except Exception as e:
        print(f"[ERROR] 讀取排程文件失敗: {e}")
        return None, []


def read_schedule_from_api(ip, port=22267):
    """
    嘗試從 Windows 主機讀取排程
    注意：這需要你在 Windows 上部署一個簡單的 API
    """
    import urllib.request
    import urllib.error

    url = f"http://{ip}:{port}/api/schedule"

    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'BlueberryPie-WoL'})
        with urllib.request.urlopen(req, timeout=5) as response:
            data = json.loads(response.read().decode())
            return data.get('next_run'), data.get('tasks', [])
    except urllib.error.URLError:
        # WebUI 可能沒有這個 API，這是預期的
        return None, []
    except Exception as e:
        print(f"[WARN] 無法從 API 獲取排程: {e}")
        return None, []


def check_host_alive(ip, port=22267, timeout=2):
    """檢查主機是否在線"""
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        result = sock.connect_ex((ip, port))
        sock.close()
        return result == 0
    except:
        return False


class WoLScheduler:
    def __init__(self, config):
        self.config = config
        self.next_wake_time = None
        self.schedule_file = Path('./wol_schedule.json')

    def load_schedule(self):
        """載入本地保存的排程"""
        if self.schedule_file.exists():
            try:
                with open(self.schedule_file, 'r') as f:
                    data = json.load(f)
                    time_str = data.get('next_wake_time')
                    if time_str:
                        self.next_wake_time = datetime.fromisoformat(time_str)
                        print(f"[INFO] 載入排程: {self.next_wake_time}")
            except Exception as e:
                print(f"[WARN] 載入排程失敗: {e}")

    def save_schedule(self):
        """保存排程到本地"""
        try:
            data = {
                'next_wake_time': self.next_wake_time.isoformat() if self.next_wake_time else None,
                'updated_at': datetime.now().isoformat()
            }
            with open(self.schedule_file, 'w') as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            print(f"[WARN] 保存排程失敗: {e}")

    def update_schedule_from_host(self):
        """
        從 Windows 主機更新排程
        優先使用文件共享，否則嘗試 API
        """
        next_run = None
        tasks = []

        # 方法1: 從共享文件讀取
        if self.config.get('SCHEDULE_FILE'):
            next_run, tasks = read_schedule_from_file(self.config['SCHEDULE_FILE'])

        # 方法2: 從 API 讀取
        if not next_run:
            next_run, tasks = read_schedule_from_api(
                self.config['WINDOWS_IP'],
                self.config['WEBUI_PORT']
            )

        if next_run:
            if isinstance(next_run, str):
                next_run = datetime.fromisoformat(next_run)

            # 計算喚醒時間（提前幾分鐘）
            buffer = timedelta(minutes=self.config['WAKE_BUFFER_MINUTES'])
            self.next_wake_time = next_run - buffer
            self.save_schedule()

            print(f"[INFO] 更新排程成功")
            print(f"  下一個任務: {next_run}")
            print(f"  喚醒時間: {self.next_wake_time}")

            return True

        return False

    def should_wake(self):
        """檢查是否需要喚醒"""
        if not self.next_wake_time:
            return False

        now = datetime.now()
        # 在喚醒時間的前後 2 分鐘內觸發
        time_diff = (self.next_wake_time - now).total_seconds()
        return -120 <= time_diff <= 0

    def wake_host(self):
        """喚醒主機"""
        print(f"[INFO] 正在喚醒主機...")

        # 發送多次 WoL 封包以確保成功
        for i in range(3):
            send_wol(
                self.config['TARGET_MAC'],
                self.config.get('BROADCAST_IP', '255.255.255.255'),
                self.config.get('WOL_PORT', 9)
            )
            time.sleep(1)

        print(f"[INFO] 已發送 WoL 封包")

        # 清除當前喚醒時間
        self.next_wake_time = None
        self.save_schedule()

    def run(self):
        """主循環"""
        print("=" * 50)
        print("BlueberryPie WoL Scheduler")
        print("=" * 50)
        print(f"目標 MAC: {self.config['TARGET_MAC']}")
        print(f"Windows IP: {self.config['WINDOWS_IP']}")
        print(f"檢查間隔: {self.config['CHECK_INTERVAL']} 秒")
        print("=" * 50)

        self.load_schedule()

        while True:
            now = datetime.now()
            print(f"\n[{now.strftime('%Y-%m-%d %H:%M:%S')}] 檢查中...")

            # 檢查主機是否在線
            host_alive = check_host_alive(
                self.config['WINDOWS_IP'],
                self.config['WEBUI_PORT']
            )

            if host_alive:
                print(f"  主機狀態: 在線")
                # 主機在線時更新排程
                self.update_schedule_from_host()
            else:
                print(f"  主機狀態: 離線")

                # 檢查是否需要喚醒
                if self.should_wake():
                    print(f"  → 到達喚醒時間，正在喚醒...")
                    self.wake_host()

                    # 等待主機啟動後更新排程
                    print(f"  等待主機啟動 (60 秒)...")
                    time.sleep(60)

                    if check_host_alive(self.config['WINDOWS_IP'], self.config['WEBUI_PORT']):
                        self.update_schedule_from_host()
                else:
                    if self.next_wake_time:
                        remaining = (self.next_wake_time - now).total_seconds()
                        if remaining > 0:
                            print(f"  下次喚醒: {self.next_wake_time}")
                            print(f"  剩餘時間: {remaining/60:.1f} 分鐘")

            time.sleep(self.config['CHECK_INTERVAL'])


def main():
    import argparse

    parser = argparse.ArgumentParser(description='BlueberryPie WoL Scheduler')
    parser.add_argument('--mac', help='目標 MAC 地址')
    parser.add_argument('--ip', help='Windows 主機 IP')
    parser.add_argument('--wake-now', action='store_true', help='立即發送 WoL')
    parser.add_argument('--config', help='配置文件路徑')

    args = parser.parse_args()

    # 載入配置
    config = CONFIG.copy()

    if args.config and os.path.exists(args.config):
        with open(args.config, 'r') as f:
            config.update(json.load(f))

    if args.mac:
        config['TARGET_MAC'] = args.mac
    if args.ip:
        config['WINDOWS_IP'] = args.ip

    # 立即喚醒模式
    if args.wake_now:
        send_wol(config['TARGET_MAC'], config.get('BROADCAST_IP', '255.255.255.255'))
        return

    # 運行調度器
    scheduler = WoLScheduler(config)
    try:
        scheduler.run()
    except KeyboardInterrupt:
        print("\n[INFO] 停止調度器")


if __name__ == "__main__":
    main()
