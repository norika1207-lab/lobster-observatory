#!/usr/bin/env python3
'''每 3 分鐘 curl 關鍵 endpoint,timeout=5。
失敗 → alert only；不要自動 restart。

2026-07-08: charenix-dashboard is a single-worker FastAPI process. Under DB/CPU
pressure, /healthz can exceed 5s while the service is still alive. Restarting on
that signal caused repeated 60s shutdown windows and /static/ timeouts.
'''
import time, urllib.request, urllib.error
TOWN = '/root/lobster-memory/_town'
ALERT_LOG = f'{TOWN}/alerts/endpoint_{time.strftime("%Y%m%d")}.log'

def log(msg):
    with open(ALERT_LOG, 'a') as f: f.write(f'[{time.strftime("%H:%M:%S")}] {msg}\n')

ENDPOINTS = [
    ('charenix-dashboard', 'http://localhost:8004/healthz'),
    ('ollama-api', 'http://100.118.221.12:11434/api/tags', None),  # 不主動修 (laptop)
]

for endpoint in ENDPOINTS:
    name, url = endpoint[:2]
    try:
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=5) as r:
            if r.status >= 400:
                log(f'BAD endpoint {name}: HTTP {r.status}; no auto-restart')
    except urllib.error.HTTPError as e:
        if e.code == 404:
            pass  # 404 對 /healthz 是 OK (有些沒這 endpoint), 跳過
        else:
            log(f'BAD {name}: HTTP {e.code}; no auto-restart')
    except Exception as e:
        log(f'DEAD {name}: {str(e)[:80]}; no auto-restart')
