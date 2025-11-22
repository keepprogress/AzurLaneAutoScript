#!/usr/bin/env python
"""
ALAS Schedule API Server
簡單的 HTTP API 服務器，提供排程資訊給 BlueberryPie

端點：
    GET /api/schedule     - 獲取排程
    GET /api/status       - 獲取 ALAS 狀態
    POST /api/start       - 觸發啟動調度器
    GET /api/health       - 健康檢查

使用方式：
    python schedule_api_server.py [--port 8080] [--alas-path /path/to/alas]

注意：
    此 API 服務器運行在獨立端口，不影響 WebUI (22267)
"""

import os
import sys
import json
import operator
import socket
import threading
from datetime import datetime
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import urlparse, parse_qs

# 默認配置
DEFAULT_PORT = 8080
DEFAULT_CONFIG = 'alas'


class ALASAPIHandler(BaseHTTPRequestHandler):
    """HTTP 請求處理器"""

    def log_message(self, format, *args):
        """自定義日誌格式"""
        print(f"[API] {self.address_string()} - {format % args}")

    def send_json(self, data, status=200):
        """發送 JSON 響應"""
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()
        self.wfile.write(json.dumps(data, default=str, ensure_ascii=False).encode('utf-8'))

    def send_error_json(self, message, status=500):
        """發送錯誤響應"""
        self.send_json({'error': message}, status)

    def do_GET(self):
        """處理 GET 請求"""
        parsed = urlparse(self.path)
        path = parsed.path
        params = parse_qs(parsed.query)

        config_name = params.get('config', [DEFAULT_CONFIG])[0]

        try:
            if path == '/api/schedule':
                self.handle_schedule(config_name)
            elif path == '/api/status':
                self.handle_status(config_name)
            elif path == '/api/health':
                self.handle_health()
            else:
                self.send_error_json('Not Found', 404)
        except Exception as e:
            self.send_error_json(str(e), 500)

    def do_POST(self):
        """處理 POST 請求"""
        parsed = urlparse(self.path)
        path = parsed.path
        params = parse_qs(parsed.query)

        config_name = params.get('config', [DEFAULT_CONFIG])[0]

        try:
            if path == '/api/start':
                self.handle_start(config_name)
            else:
                self.send_error_json('Not Found', 404)
        except Exception as e:
            self.send_error_json(str(e), 500)

    def do_OPTIONS(self):
        """處理 CORS 預檢請求"""
        self.send_response(200)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')
        self.end_headers()

    def handle_schedule(self, config_name):
        """獲取排程"""
        from module.config.config import AzurLaneConfig

        cfg = AzurLaneConfig(config_name)
        cfg.get_next_task()

        pending = cfg.pending_task
        waiting = cfg.waiting_task

        # 合併並排序
        all_tasks = sorted(pending + waiting, key=operator.attrgetter("next_run"))

        # 格式化任務
        tasks = []
        for task in all_tasks:
            tasks.append({
                'command': task.command,
                'next_run': task.next_run.isoformat() if isinstance(task.next_run, datetime) else str(task.next_run),
                'enable': task.enable
            })

        # 下一個任務
        next_run = None
        if all_tasks:
            next_run = all_tasks[0].next_run
            if isinstance(next_run, datetime):
                next_run = next_run.isoformat()

        self.send_json({
            'config': config_name,
            'next_run': next_run,
            'pending_count': len(pending),
            'waiting_count': len(waiting),
            'tasks': tasks,
            'timestamp': datetime.now().isoformat()
        })

    def handle_status(self, config_name):
        """獲取 ALAS 狀態"""
        status = {
            'config': config_name,
            'webui_port': 22267,
            'webui_running': check_port(22267),
            'timestamp': datetime.now().isoformat()
        }

        # 嘗試檢查進程狀態
        try:
            import psutil
            alas_processes = []
            for proc in psutil.process_iter(['pid', 'name', 'cmdline']):
                try:
                    cmdline = proc.info.get('cmdline', []) or []
                    cmdline_str = ' '.join(cmdline).lower()
                    if 'alas' in cmdline_str or 'gui.py' in cmdline_str:
                        alas_processes.append({
                            'pid': proc.pid,
                            'name': proc.info['name']
                        })
                except:
                    pass
            status['processes'] = alas_processes
        except ImportError:
            status['processes'] = None

        self.send_json(status)

    def handle_start(self, config_name):
        """觸發啟動調度器"""
        # 寫入 reloadalas 文件
        reloadalas_path = Path('./config/reloadalas')
        reloadalas_path.parent.mkdir(exist_ok=True)

        with open(reloadalas_path, 'a') as f:
            f.write(f"{config_name}\n")

        self.send_json({
            'success': True,
            'message': f'已觸發 {config_name} 啟動',
            'note': 'WebUI 重啟時將自動啟動調度器'
        })

    def handle_health(self):
        """健康檢查"""
        self.send_json({
            'status': 'ok',
            'timestamp': datetime.now().isoformat()
        })


def check_port(port, host='127.0.0.1'):
    """檢查端口是否開放"""
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(1)
        result = sock.connect_ex((host, port))
        sock.close()
        return result == 0
    except:
        return False


def run_server(port, alas_path):
    """運行 API 服務器"""
    # 設置 ALAS 路徑
    if alas_path:
        os.chdir(alas_path)
    sys.path.insert(0, os.getcwd())

    server = HTTPServer(('0.0.0.0', port), ALASAPIHandler)
    print(f"=" * 50)
    print(f"ALAS Schedule API Server")
    print(f"=" * 50)
    print(f"監聽端口: {port}")
    print(f"ALAS 路徑: {os.getcwd()}")
    print(f"")
    print(f"API 端點:")
    print(f"  GET  /api/schedule  - 獲取排程")
    print(f"  GET  /api/status    - 獲取狀態")
    print(f"  POST /api/start     - 觸發啟動")
    print(f"  GET  /api/health    - 健康檢查")
    print(f"=" * 50)

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[INFO] 停止服務器")
        server.shutdown()


def main():
    import argparse

    parser = argparse.ArgumentParser(description='ALAS Schedule API Server')
    parser.add_argument('--port', type=int, default=DEFAULT_PORT, help=f'監聽端口 (默認: {DEFAULT_PORT})')
    parser.add_argument('--alas-path', help='ALAS 安裝路徑')

    args = parser.parse_args()

    run_server(args.port, args.alas_path)


if __name__ == "__main__":
    main()
