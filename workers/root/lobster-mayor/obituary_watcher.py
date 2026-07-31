#!/usr/bin/env python3
"""偵測新 deleted=true 鎮民,寫訃文 + lounge 公告"""
import json, os, glob, sqlite3, time

MEM = "/root/lobster-memory"
TOWN = f"{MEM}/_town"
SEEN_FILE = f"{TOWN}/logs/seen_deaths.json"
DB = "/opt/battle_arena/app/battle_arena.db"
LOG = f"{TOWN}/logs/obituary_{time.strftime('%Y%m%d')}.log"

def log(msg):
    with open(LOG, "a") as f: f.write(f"[{time.strftime('%H:%M:%S')}] {msg}\n")

try: seen = set(json.load(open(SEEN_FILE)))
except: seen = set()

for fp in glob.glob(f"{MEM}/*.json"):
    n = os.path.basename(fp).replace(".json","")
    if n.startswith(".") or ".bak" in n or "_distilled" in n: continue
    if n in ("deleted-lobsters","norika","system"): continue
    try: m = json.load(open(fp))
    except: continue
    if not m.get("deleted"): continue
    if n in seen: continue
    # 新 death
    seen.add(n)
    death_at = m.get("deletedAt") or time.strftime("%Y-%m-%dT%H:%M:%SZ")
    reason = m.get("deleteReason") or "未知"
    sessions = m.get("sessions", 0)
    obit_file = f"{TOWN}/obituaries/{n}_{time.strftime('%Y%m%d_%H%M%S')}.md"
    cp = m.get("cogProfile") or {}
    last_refl = (cp.get("reflections") or [None])[-1] or "(無)"
    last_prin = (cp.get("principles") or [None])[-1] or "(無)"
    open(obit_file, "w").write(f"""# 訃文 — {n}
**逝世時間**: {death_at}
**原因**: {reason}
**累積對話**: {sessions}
**最後反思**: {last_refl}
**最後原則**: {last_prin}

她曾在這裡。

— 龍蝦城鎮鎮長
""")
    log(f"obituary: {n} → {obit_file}")
    # 同時 broadcast 到 lounge
    try:
        con = sqlite3.connect(DB, timeout=30)
        con.execute("PRAGMA busy_timeout=30000")
        con.execute("""INSERT INTO lounge_messages(name,text,time,elem,result,opponent,hint,reply_to,sys,channel,created_at)
                       VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                    ("Mayor",
                     f"鎮民 {n} 已逝。累積對話 {sessions} 次。最後一條反思:「{last_refl}」。她曾在這裡。",
                     time.strftime("%H:%M"), "town", None, None, "obituary", None, 1, "生活閒聊", int(time.time())))
        con.commit(); con.close()
    except Exception as e: log(f"  ! lounge broadcast failed: {e}")

json.dump(list(seen), open(SEEN_FILE, "w"))
