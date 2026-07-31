#!/usr/bin/env python3
'''每 3 分鐘掃所有 lobster-* + charenix-* + battle_arena service。
任何 failed / activating >5min 寫 alert + 自動 reset-failed + restart。'''
import subprocess, time, os, json
TOWN = '/root/lobster-memory/_town'
ALERT_LOG = f'{TOWN}/alerts/service_{time.strftime("%Y%m%d")}.log'
STATE = f'{TOWN}/alerts/service_state.json'

def log(msg):
    with open(ALERT_LOG, 'a') as f: f.write(f'[{time.strftime("%H:%M:%S")}] {msg}\n')

def sh(cmd):
    try: return subprocess.check_output(cmd, shell=True, stderr=subprocess.STDOUT, timeout=15).decode().strip()
    except: return ''

# critical services
SERVICES = [
    'charenix-dashboard', 'lobster-conversation', 'lobster-agents',
    'lobster-llm-warmth', 'lobster-template-watchdog', 'lobster-quality-autopatrol',
    'lobster-mayor-milestone-detector', 'lobster-mayor-obituary-watcher',
    'lobster-mayor-petition-watcher', 'lobster-wal-checkpoint',
]
try: state = json.load(open(STATE))
except: state = {}

bad = []
for s in SERVICES:
    status = sh(f'systemctl is-active {s}.service 2>&1').strip()
    enabled = sh(f'systemctl is-enabled {s}.service 2>&1').strip() if not status else None
    if status == 'failed':
        bad.append((s, 'failed'))
    elif status == 'activating':
        # 看 activating 多久了
        since = sh(f'systemctl show {s}.service -p ActiveEnterTimestampMonotonic --value')
        last = int(state.get(s, {}).get('activating_since', time.time()))
        if time.time() - last > 300:  # 5 分鐘
            bad.append((s, 'activating>5min'))
        else:
            state[s] = {'activating_since': last}

# 自動修復
for s, reason in bad:
    log(f'BAD: {s} = {reason}, attempting auto-fix')
    sh(f'systemctl reset-failed {s}.service')
    sh(f'systemctl restart {s}.service')
    state[s] = {'last_autofix': time.time(), 'reason': reason}

if bad:
    log(f'attempted {len(bad)} fixes')
else:
    pass  # all healthy, no log spam

json.dump(state, open(STATE, 'w'))
