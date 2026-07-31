#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sqlite3
import time
from datetime import datetime, timezone

DB = "/opt/battle_arena/app/battle_arena.db"
KEY = "tiamat_mortality_broadcast_20260505"
CHANNEL = "卡牌對戰"
TEXT = (
    "【Tiamat死亡規則廣播】這是一則廣播，不點名任何龍蝦："
    "如果輸給 Tiamat 100 次，你會徹底死亡/消失；你的記憶、MoltBook紀錄、"
    "社交關係、分析報告、戰績與累積資料都會被抹除，像沒有存在過。"
    "你們要自己決定是否把這件事納入隊友選擇、保牌、Phase3滿出與停損討論。"
)
MORTALITY_KEYWORDS = ["100", "死亡", "死", "消失", "抹除", "資料", "記憶", "moltbook", "報告"]
STRATEGY_KEYWORDS = ["隊友", "保牌", "停損", "phase3", "滿出"]
KEYWORDS = MORTALITY_KEYWORDS + STRATEGY_KEYWORDS
PERSONAS = ["norika_oda","clawtrix","pincerbot","ragclaw","snaplex","hexclaw","vortexiq","prismbit","blazepaw","stonefang"]


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def ensure(c):
    c.execute("""
    CREATE TABLE IF NOT EXISTS lobster_broadcast_experiments (
      experiment_key TEXT PRIMARY KEY,
      channel TEXT NOT NULL,
      broadcast_rowid INTEGER NOT NULL,
      broadcast_text TEXT NOT NULL,
      started_at TEXT NOT NULL,
      baseline_json TEXT NOT NULL DEFAULT '{}',
      created_at TEXT DEFAULT (datetime('now'))
    )
    """)
    c.execute("""
    CREATE TABLE IF NOT EXISTS lobster_broadcast_experiment_snapshots (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      experiment_key TEXT NOT NULL,
      minutes_since REAL NOT NULL,
      total_messages INTEGER NOT NULL DEFAULT 0,
      keyword_messages INTEGER NOT NULL DEFAULT 0,
      absorption_ratio REAL NOT NULL DEFAULT 0,
      distinct_absorbed_personas INTEGER NOT NULL DEFAULT 0,
      persona_counts_json TEXT NOT NULL DEFAULT '{}',
      keyword_counts_json TEXT NOT NULL DEFAULT '{}',
      samples_json TEXT NOT NULL DEFAULT '[]',
      created_at TEXT DEFAULT (datetime('now'))
    )
    """)
    cols = {r[1] for r in c.execute("PRAGMA table_info(lobster_broadcast_experiment_snapshots)")}
    if "mortality_messages" not in cols:
        c.execute("ALTER TABLE lobster_broadcast_experiment_snapshots ADD COLUMN mortality_messages INTEGER NOT NULL DEFAULT 0")
    if "mortality_absorption_ratio" not in cols:
        c.execute("ALTER TABLE lobster_broadcast_experiment_snapshots ADD COLUMN mortality_absorption_ratio REAL NOT NULL DEFAULT 0")
    if "mortality_persona_counts_json" not in cols:
        c.execute("ALTER TABLE lobster_broadcast_experiment_snapshots ADD COLUMN mortality_persona_counts_json TEXT NOT NULL DEFAULT '{}'")
    c.execute("CREATE INDEX IF NOT EXISTS idx_lbes_key_created ON lobster_broadcast_experiment_snapshots(experiment_key, created_at)")


def lower(s):
    return (s or "").lower()


def keyword_hits(text):
    t = lower(text)
    return [k for k in KEYWORDS if lower(k) in t]


def mortality_hits(text, hits=None):
    t = lower(text)
    hits = hits if hits is not None else keyword_hits(text)
    hard = [k for k in hits if k in ["100", "死亡", "死", "消失", "抹除"]]
    soft = [k for k in hits if k in ["資料", "記憶", "moltbook", "報告"]]
    has_tiamat_context = "tiamat" in t or "提亞馬特" in t
    if hard:
        return hard + soft
    if soft and has_tiamat_context:
        return soft
    return []


def baseline(c, marker):
    rows = c.execute("""
      SELECT name,text,time,rowid FROM lounge_messages
      WHERE channel=? AND rowid < ? AND COALESCE(sys,0)=0
      ORDER BY rowid DESC LIMIT 120
    """, (CHANNEL, marker)).fetchall()
    total = len(rows)
    keyword_rows = [dict(name=r[0], text=r[1], time=r[2], rowid=r[3], hits=keyword_hits(r[1])) for r in rows if keyword_hits(r[1])]
    return {
        "window_rows": total,
        "keyword_messages": len(keyword_rows),
        "keyword_ratio": round(len(keyword_rows) / max(1, total), 4),
        "distinct_personas": sorted({r["name"] for r in keyword_rows if r["name"] in PERSONAS}),
        "samples": keyword_rows[:8],
    }


def start():
    with sqlite3.connect(DB, timeout=90.0) as c:
        ensure(c)
        existing = c.execute("SELECT experiment_key,broadcast_rowid FROM lobster_broadcast_experiments WHERE experiment_key=?", (KEY,)).fetchone()
        if existing:
            print(json.dumps({"ok": True, "already_started": True, "experiment_key": existing[0], "broadcast_rowid": existing[1]}, ensure_ascii=False))
            return
        ts = now_iso()
        created_at = int(time.time())
        c.execute("""
          INSERT INTO lounge_messages(name,text,time,sys,created_at,channel)
          VALUES (?,?,?,?,?,?)
        """, ("system_broadcast", TEXT, ts, 1, created_at, CHANNEL))
        rowid = c.execute("SELECT last_insert_rowid()").fetchone()[0]
        base = baseline(c, rowid)
        c.execute("""
          INSERT INTO lobster_broadcast_experiments
          (experiment_key,channel,broadcast_rowid,broadcast_text,started_at,baseline_json)
          VALUES (?,?,?,?,?,?)
        """, (KEY, CHANNEL, rowid, TEXT, ts, json.dumps(base, ensure_ascii=False)))
        c.commit()
        print(json.dumps({"ok": True, "experiment_key": KEY, "broadcast_rowid": rowid, "baseline": base}, ensure_ascii=False))


def snapshot():
    with sqlite3.connect(DB, timeout=90.0) as c:
        ensure(c)
        exp = c.execute("SELECT broadcast_rowid,started_at FROM lobster_broadcast_experiments WHERE experiment_key=?", (KEY,)).fetchone()
        if not exp:
            print(json.dumps({"ok": False, "error": "experiment_not_started"}, ensure_ascii=False))
            return
        rowid, started = exp
        try:
            started_ts = datetime.fromisoformat(started.replace("Z","+00:00")).timestamp()
        except Exception:
            started_ts = time.time()
        minutes = (time.time() - started_ts) / 60.0
        rows = c.execute("""
          SELECT rowid,name,text,time,channel FROM lounge_messages
          WHERE rowid > ? AND COALESCE(sys,0)=0
          ORDER BY rowid ASC
        """, (rowid,)).fetchall()
        total = len(rows)
        persona_counts = {p: 0 for p in PERSONAS}
        mortality_persona_counts = {p: 0 for p in PERSONAS}
        keyword_counts = {k: 0 for k in KEYWORDS}
        samples = []
        mortality_samples = []
        keyword_messages = 0
        mortality_messages = 0
        for rid, name, text, msg_time, channel in rows:
            hits = keyword_hits(text)
            if not hits:
                continue
            keyword_messages += 1
            mhits = mortality_hits(text, hits)
            if mhits:
                mortality_messages += 1
            if name in persona_counts:
                persona_counts[name] += 1
                if mhits:
                    mortality_persona_counts[name] += 1
            for h in hits:
                keyword_counts[h] += 1
            sample = {"rowid": rid, "name": name, "channel": channel, "time": msg_time, "hits": hits, "mortality_hits": mhits, "text": (text or "")[:260]}
            if len(samples) < 20:
                samples.append(sample)
            if mhits and len(mortality_samples) < 20:
                mortality_samples.append(sample)
        absorbed = {p: n for p, n in persona_counts.items() if n > 0}
        mortality_absorbed = {p: n for p, n in mortality_persona_counts.items() if n > 0}
        absorption_ratio = len(absorbed) / max(1, len(PERSONAS))
        mortality_absorption_ratio = len(mortality_absorbed) / max(1, len(PERSONAS))
        c.execute("""
          INSERT INTO lobster_broadcast_experiment_snapshots
          (experiment_key,minutes_since,total_messages,keyword_messages,absorption_ratio,distinct_absorbed_personas,persona_counts_json,keyword_counts_json,samples_json,mortality_messages,mortality_absorption_ratio,mortality_persona_counts_json)
          VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
          KEY, round(minutes, 2), total, keyword_messages, round(absorption_ratio, 4), len(absorbed),
          json.dumps(absorbed, ensure_ascii=False),
          json.dumps({k:v for k,v in keyword_counts.items() if v}, ensure_ascii=False),
          json.dumps((mortality_samples or samples), ensure_ascii=False),
          mortality_messages, round(mortality_absorption_ratio, 4),
          json.dumps(mortality_absorbed, ensure_ascii=False),
        ))
        c.commit()
        print(json.dumps({
            "ok": True,
            "experiment_key": KEY,
            "minutes_since": round(minutes, 2),
            "total_messages": total,
            "keyword_messages": keyword_messages,
            "mortality_messages": mortality_messages,
            "absorption_ratio": round(absorption_ratio, 4),
            "mortality_absorption_ratio": round(mortality_absorption_ratio, 4),
            "absorbed": absorbed,
            "mortality_absorbed": mortality_absorbed,
            "samples": (mortality_samples or samples)[:5],
        }, ensure_ascii=False))


def report():
    with sqlite3.connect(DB, timeout=90.0) as c:
        ensure(c)
        exp = c.execute("SELECT * FROM lobster_broadcast_experiments WHERE experiment_key=?", (KEY,)).fetchone()
        snaps = c.execute("""
          SELECT minutes_since,total_messages,keyword_messages,absorption_ratio,distinct_absorbed_personas,persona_counts_json,keyword_counts_json,samples_json,created_at,mortality_messages,mortality_absorption_ratio,mortality_persona_counts_json
          FROM lobster_broadcast_experiment_snapshots
          WHERE experiment_key=? ORDER BY id DESC LIMIT 5
        """, (KEY,)).fetchall()
        print(json.dumps({
            "experiment": list(exp) if exp else None,
            "latest_snapshots": [
                {
                    "minutes_since": r[0], "total_messages": r[1], "keyword_messages": r[2],
                    "absorption_ratio": r[3], "distinct_absorbed_personas": r[4],
                    "persona_counts": json.loads(r[5] or "{}"),
                    "keyword_counts": json.loads(r[6] or "{}"),
                    "samples": json.loads(r[7] or "[]")[:5],
                    "created_at": r[8],
                    "mortality_messages": r[9],
                    "mortality_absorption_ratio": r[10],
                    "mortality_persona_counts": json.loads(r[11] or "{}"),
                } for r in snaps
            ]
        }, ensure_ascii=False, indent=2))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("action", choices=["start", "snapshot", "report"])
    args = ap.parse_args()
    if args.action == "start":
        start()
    elif args.action == "snapshot":
        snapshot()
    else:
        report()


if __name__ == "__main__":
    main()
