#!/usr/bin/env python3
# 結算健康 watchdog (norika 2026-06-10) — 結算超時就 alert,防再次靜默故障
import sqlite3, datetime, os
DB='/opt/battle_arena/app/battle_arena.db'
ALERT_DIR='/root/lobster-memory/_town/alerts'
os.makedirs(ALERT_DIR,exist_ok=True)
con=sqlite3.connect(DB)
row=con.execute('SELECT MAX(computed_at) FROM lobster_stock_scoreboard').fetchone()
last=row[0]
now=datetime.datetime.now()
alert=None
if last:
    try:
        lt=datetime.datetime.strptime(last[:19],'%Y-%m-%d %H:%M:%S')
        hrs=(now-lt).total_seconds()/3600
        if hrs>6:
            alert='股票結算 scoreboard 已 %.1f 小時沒更新 (最後 %s) — 結算可能又卡住'%(hrs,last)
    except Exception as e:
        alert='watchdog 解析時間失敗: '+str(e)
else:
    alert='scoreboard 無任何記錄'
if alert:
    f=ALERT_DIR+'/settle_'+now.strftime('%Y%m%d')+'.log'
    open(f,'a').write('['+now.strftime('%H:%M')+'] '+alert+'\n')
    print('ALERT:',alert)
    # 嘗試自動補救:觸發結算
    os.system('systemctl start lobster-stock-settle.service 2>/dev/null')
else:
    print('OK: scoreboard fresh, last=',last)
