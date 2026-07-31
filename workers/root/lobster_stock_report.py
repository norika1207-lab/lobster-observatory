#!/usr/bin/env python3
# 每日龍蝦股票勝率/估值報表 (norika 2026-06-10) — 純讀取,不下單
import sqlite3, json, datetime
DB='/opt/battle_arena/app/battle_arena.db'
con=sqlite3.connect(DB); con.row_factory=sqlite3.Row
sb=con.execute('SELECT * FROM lobster_stock_scoreboard ORDER BY win_rate DESC').fetchall()
valid=[r for r in sb if r['closed_trades']>=10]
now=datetime.datetime.now().strftime('%Y-%m-%d %H:%M')
L=[]
L.append('# 龍蝦股票每日報表 '+now)
L.append('')
n=len(valid)
win=[r for r in valid if r['win_rate']>50]
pos=[r for r in valid if r['avg_all_ret']>0]
avg_wr=sum(r['win_rate'] for r in valid)/n if n else 0
avg_ret=sum(r['avg_all_ret'] for r in valid)/n if n else 0
L.append('## 整體')
L.append('有效樣本(平倉>=10): %d 隻'%n)
L.append('勝率>50%%(贏家): %d 隻 / 勝率<50%%: %d 隻'%(len(win),n-len(win)))
L.append('總報酬為正: %d 隻 / 為負: %d 隻'%(len(pos),n-len(pos)))
L.append('全體平均勝率: %.1f%%  平均報酬: %.2f%%'%(avg_wr,avg_ret))
L.append('基準: 隨機買股約50%%勝率。全體%.1f%%代表%s'%(avg_wr,'優於隨機' if avg_wr>50 else '略輸隨機,靠風控守住'))
L.append('')
L.append('## 真會買 (跟單對象, 勝率>=60% 且平倉>=15)')
for r in valid:
    if r['win_rate']>=60 and r['closed_trades']>=15:
        L.append('  %-12s 勝率%.1f%%  平倉%d  平均報酬%.2f%%  高信心勝率%.1f%%'%(r['persona'],r['win_rate'],r['closed_trades'],r['avg_closed_ret'],r['hc_win_rate']))
L.append('')
L.append('## 在燒錢 (高頻低勝率, 平倉>=300 且勝率<40%)')
for r in valid:
    if r['closed_trades']>=300 and r['win_rate']<40:
        L.append('  %-12s 勝率%.1f%%  平倉%d  總報酬%.2f%%'%(r['persona'],r['win_rate'],r['closed_trades'],r['avg_all_ret']))
L.append('')
L.append('## 全榜')
L.append('龍蝦         平倉 勝率%% 平均報酬%% 總報酬%%')
for r in valid:
    L.append('  %-12s %4d %5.1f %7.2f %7.2f'%(r['persona'],r['closed_trades'],r['win_rate'],r['avg_closed_ret'],r['avg_all_ret']))
txt='\n'.join(L)
open('/root/lobster_stock_report.txt','w').write(txt)
# 存歷史
import os
os.makedirs('/root/lobster_reports',exist_ok=True)
open('/root/lobster_reports/report_'+datetime.datetime.now().strftime('%Y%m%d')+'.txt','w').write(txt)
print(txt)
