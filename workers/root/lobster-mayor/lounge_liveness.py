#!/usr/bin/env python3
'''每 5 分鐘看 lounge_messages 過去 10 分鐘有沒新訊息(sys=0)。
< 3 條 = 鎮民沉默 → alert (不自動修,因為原因多種)'''
import sqlite3, time, json, os
TOWN = '/root/lobster-memory/_town'
ALERT_LOG = f'{TOWN}/alerts/lounge_{time.strftime("%Y%m%d")}.log'

def log(msg):
    with open(ALERT_LOG, 'a') as f: f.write(f'[{time.strftime("%H:%M:%S")}] {msg}\n')

con = sqlite3.connect('/opt/battle_arena/app/battle_arena.db', timeout=20)
con.execute('PRAGMA busy_timeout=20000')
n = con.execute('SELECT COUNT(*) FROM lounge_messages WHERE created_at > strftime("%s","now","-10 minutes") AND sys=0').fetchone()[0]
distinct = con.execute('SELECT COUNT(DISTINCT name) FROM lounge_messages WHERE created_at > strftime("%s","now","-10 minutes") AND sys=0').fetchone()[0]
con.close()

if n < 3:
    log(f'SILENT: lounge 過去 10 分鐘只有 {n} 條訊息 ({distinct} 隻發言) ← 鎮民沉默警報')
elif distinct < 5:
    log(f'NARROW: lounge 過去 10 分鐘 {n} 條訊息但只 {distinct} 隻發言 ← 太少人說')
