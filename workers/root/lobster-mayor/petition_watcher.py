#!/usr/bin/env python3
"""掃過去 30 分鐘 lounge 訊息,找對鎮長的訴求/申請/抱怨,寫進 petitions"""
import sqlite3, json, os, time, re

TOWN = "/root/lobster-memory/_town"
DB = "/opt/battle_arena/app/battle_arena.db"
TODAY = time.strftime("%Y%m%d")
LOG = f"{TOWN}/logs/petition_{TODAY}.log"
PETITION_FILE = f"{TOWN}/petitions/petitions_{TODAY}.jsonl"

def log(msg):
    with open(LOG, "a") as f: f.write(f"[{time.strftime('%H:%M:%S')}] {msg}\n")

KEYWORDS = [
    r"鎮長", r"@mayor", r"我想要", r"我希望", r"申請", r"請求", r"我要求",
    r"不公平", r"抗議", r"投訴", r"為什麼.*只能", r"為什麼.*不能",
    r"能不能讓我", r"我可以.*嗎",
]

con = sqlite3.connect(DB, timeout=30)
con.execute("PRAGMA busy_timeout=30000")
con.row_factory = sqlite3.Row

# 抓過去 30 分鐘訊息
rows = con.execute("""SELECT name, text, channel, datetime(created_at,'unixepoch','+8 hours') at
                      FROM lounge_messages
                      WHERE created_at > strftime('%s','now','-30 minutes')
                      AND sys=0 AND name != 'Eos'""").fetchall()

count = 0
for r in rows:
    text = r["text"] or ""
    matched = [kw for kw in KEYWORDS if re.search(kw, text)]
    if not matched: continue
    petition = {
        "at": r["at"], "name": r["name"], "channel": r["channel"],
        "text": text[:400], "matched_keywords": matched,
        "status": "pending_mayor_review",
    }
    with open(PETITION_FILE, "a") as f:
        f.write(json.dumps(petition, ensure_ascii=False) + "\n")
    log(f"petition from {r['name']} [{r['channel']}]: {text[:80]} | matched={matched}")
    count += 1

if count: log(f"=== {count} petitions logged ===")
con.close()
