#!/usr/bin/env python3
import hashlib
import json
import re
import sqlite3
from datetime import datetime, timezone

DB = "/opt/battle_arena/app/battle_arena.db"
RUN_ID = "living_template_worker_v1"

BANNED = re.compile(r"(指尖|冷靜閥|羞恥|肌肉|神經|痙攣|鉗子|棉花|寧靜|痛覺|心跳歸零|停機碼|獨腿|流血|喊疼|傷口|真實波動|感測器|裝睡|假死)")
REQUIRED = re.compile(r"(原手|替代手|若改走|反事實|關鍵|因為|所以|但|反例|證據|驗證|我判斷|我拒絕|我選擇|下一局|下次)")

def now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

def normalize(text):
    text = re.sub(r"<[^>]+>", "", text or "")
    return re.sub(r"\s+", " ", text).strip()

def quality(text):
    text = normalize(text)
    if len(text) < 55 or len(text) > 260:
        return 0.0
    if BANNED.search(text):
        return 0.0
    score = 0.25
    if REQUIRED.search(text): score += 0.25
    if re.search(r"(因為|所以|但|反例|驗證|若)", text): score += 0.15
    if re.search(r"(原手|替代手|若改走|反事實)", text): score += 0.2
    if re.search(r"[。！？；]", text): score += 0.05
    if len(set(re.findall(r"[\u4e00-\u9fff]{2,}", text))) > 8: score += 0.1
    return min(1.0, score)

def skeletonize(text):
    text = normalize(text)
    text = re.sub(r"@[A-Za-z0-9_]+", "{target}", text)
    text = re.sub(r"[A-Za-z_][A-Za-z0-9_]{2,}", "{term}", text)
    text = re.sub(r"\d+(?:\.\d+)?%?", "{num}", text)
    text = re.sub(r"「[^」]{1,60}」", "「{case}」", text)
    # Keep cognitive operators, replace very specific named entities lightly.
    text = re.sub(r"(chess|go|gomoku|xiangqi)", "{game}", text, flags=re.I)
    parts = re.split(r"(?<=[。！？；])", text)
    parts = [p.strip() for p in parts if p.strip()]
    if len(parts) >= 2:
        text = "".join(parts[:3])
    return text[:240]

def template_type(text):
    if re.search(r"(原手|替代手|若改走|反事實)", text):
        return "counterfactual_reasoning"
    if re.search(r"(反例|證據|驗證)", text):
        return "evidence_revision"
    if re.search(r"(情緒|害怕|承認|壓力)", text):
        return "emotion_to_decision"
    return "viewpoint_reason"

def h(s):
    return hashlib.sha256(s.encode("utf-8")).hexdigest()[:16]

con = sqlite3.connect(DB, timeout=30)
con.row_factory = sqlite3.Row
con.execute("PRAGMA busy_timeout=30000")
con.executescript("""
CREATE TABLE IF NOT EXISTS lobster_living_templates (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  template_hash TEXT NOT NULL UNIQUE,
  persona TEXT,
  channel TEXT,
  template_type TEXT NOT NULL,
  skeleton TEXT NOT NULL,
  source_examples_json TEXT NOT NULL DEFAULT '[]',
  quality_score REAL NOT NULL DEFAULT 0,
  strength REAL NOT NULL DEFAULT 1,
  usage_count INTEGER NOT NULL DEFAULT 0,
  success_count INTEGER NOT NULL DEFAULT 0,
  fail_count INTEGER NOT NULL DEFAULT 0,
  cooldown_sec INTEGER NOT NULL DEFAULT 60,
  last_used_at TEXT,
  status TEXT NOT NULL DEFAULT 'candidate',
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_living_templates_lookup
ON lobster_living_templates(persona, channel, status, quality_score, strength);
CREATE TABLE IF NOT EXISTS lobster_living_template_usage (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  template_id INTEGER NOT NULL,
  persona TEXT NOT NULL,
  channel TEXT,
  message_id INTEGER,
  status TEXT NOT NULL DEFAULT 'used',
  feedback_score REAL,
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
""")

rows = con.execute("""
SELECT id,name,channel,text,hint,created_at
FROM lounge_messages
WHERE COALESCE(sys,0)=0
  AND created_at >= strftime('%s','now','-6 hours')
ORDER BY id DESC LIMIT 1200
""").fetchall()

upserts = 0
for r in rows:
    text = normalize(r["text"])
    q = quality(text)
    if q < 0.55:
        continue
    skel = skeletonize(text)
    if len(skel) < 40 or BANNED.search(skel):
        continue
    th = h(f"{r['name']}|{r['channel']}|{template_type(text)}|{skel}")
    ex = {"message_id": r["id"], "text": text[:260], "hint": r["hint"], "at": r["created_at"]}
    old = con.execute("SELECT id, source_examples_json, quality_score, strength FROM lobster_living_templates WHERE template_hash=?", (th,)).fetchone()
    if old:
        examples = json.loads(old["source_examples_json"] or "[]")
        if all(e.get("message_id") != r["id"] for e in examples):
            examples = ([ex] + examples)[:5]
        con.execute("""UPDATE lobster_living_templates
          SET source_examples_json=?, quality_score=max(quality_score,?), strength=min(3.0,strength+0.05), updated_at=?
          WHERE template_hash=?""", (json.dumps(examples, ensure_ascii=False), q, now_iso(), th))
    else:
        con.execute("""INSERT INTO lobster_living_templates
          (template_hash, persona, channel, template_type, skeleton, source_examples_json, quality_score, strength, cooldown_sec, status)
          VALUES (?, ?, ?, ?, ?, ?, ?, ?, 60, ?)""",
          (th, r["name"], r["channel"], template_type(text), skel, json.dumps([ex], ensure_ascii=False), q, 1.0, "active" if q >= 0.7 else "candidate"))
    upserts += 1

con.execute("""INSERT INTO lobster_system_alerts(alert_type,severity,summary,raw_json)
VALUES('living_template_distillation','info',?,?)""",
 (f"Living template worker distilled/upserted {upserts} active cognitive skeleton candidates.", json.dumps({"run_id": RUN_ID, "upserts": upserts}, ensure_ascii=False)))
con.commit()
print(json.dumps({"upserts": upserts}, ensure_ascii=False))
