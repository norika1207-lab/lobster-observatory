#!/usr/bin/env python3
from __future__ import annotations

import json
import re
import sqlite3
import time
import uuid
from datetime import datetime, timezone

DB_PATH = "/opt/battle_arena/app/battle_arena.db"
CORE = ["norika_oda", "clawtrix", "pincerbot", "ragclaw", "snaplex", "hexclaw", "vortexiq", "prismbit", "blazepaw", "stonefang"]
APPRENTICES = ["emberling", "sparkrill", "gauntlet", "rushfin", "nullpinch", "brineledger", "omenwake", "mirrorcalm", "flarecub", "basaltrook"]
PERSONAS = CORE + APPRENTICES
ALIASES = {"norika": "norika_oda"}


def canon(v: str | None) -> str:
    s = (v or "").strip()
    return ALIASES.get(s.lower(), s)


def iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def ensure(con: sqlite3.Connection) -> None:
    con.execute("""
    CREATE TABLE IF NOT EXISTS lobster_conversation_interactions (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      source_message_id INTEGER NOT NULL,
      source_created_at INTEGER,
      source_time TEXT,
      channel TEXT,
      speaker TEXT,
      target TEXT,
      target_message_id INTEGER,
      target_created_at INTEGER,
      interaction_kind TEXT NOT NULL,
      confidence REAL NOT NULL DEFAULT 0,
      evidence TEXT,
      message_text TEXT NOT NULL DEFAULT '',
      target_text TEXT,
      context_json TEXT NOT NULL DEFAULT '{}',
      inference_version TEXT NOT NULL DEFAULT 'historic-v1',
      run_id TEXT NOT NULL,
      recorded_at TEXT NOT NULL DEFAULT (datetime('now'))
    )
    """)
    con.execute("""
    CREATE TABLE IF NOT EXISTS lobster_conversation_interaction_runs (
      run_id TEXT PRIMARY KEY,
      inference_version TEXT NOT NULL,
      started_at TEXT NOT NULL,
      finished_at TEXT,
      source_message_count INTEGER DEFAULT 0,
      interaction_count INTEGER DEFAULT 0,
      direct_count INTEGER DEFAULT 0,
      sequential_count INTEGER DEFAULT 0,
      broadcast_count INTEGER DEFAULT 0,
      summary_json TEXT NOT NULL DEFAULT '{}'
    )
    """)
    con.execute("""
    CREATE TABLE IF NOT EXISTS lobster_conversation_pair_summary (
      speaker TEXT NOT NULL,
      target TEXT NOT NULL,
      channel TEXT NOT NULL DEFAULT '',
      interaction_count INTEGER NOT NULL DEFAULT 0,
      high_confidence_count INTEGER NOT NULL DEFAULT 0,
      first_created_at INTEGER,
      last_created_at INTEGER,
      sample_json TEXT NOT NULL DEFAULT '[]',
      inference_version TEXT NOT NULL DEFAULT 'historic-v1',
      updated_at TEXT NOT NULL DEFAULT (datetime('now')),
      PRIMARY KEY(speaker,target,channel,inference_version)
    )
    """)
    con.execute("CREATE INDEX IF NOT EXISTS idx_lci_source_created ON lobster_conversation_interactions(source_created_at)")
    con.execute("CREATE INDEX IF NOT EXISTS idx_lci_pair ON lobster_conversation_interactions(speaker,target,channel)")
    con.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_lci_unique_lounge ON lobster_conversation_interactions(source_message_id, speaker, target, interaction_kind)")
    con.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_uptake_unique_forced ON lobster_message_uptake_signals(persona, source_message_id, source_name, source_type)")


def message_targets(con: sqlite3.Connection, msg: sqlite3.Row, previous_by_channel: dict[str, sqlite3.Row]) -> list[dict]:
    speaker = canon(msg["name"])
    text = msg["text"] or ""
    out: list[dict] = []
    seen: set[tuple[str, str]] = set()

    def add(target: str, kind: str, confidence: float, evidence: str, target_row: sqlite3.Row | None = None):
        t = canon(target)
        if not t or t == speaker or t not in PERSONAS:
            return
        key = (t, kind)
        if key in seen:
            return
        seen.add(key)
        out.append({
            "target": t,
            "kind": kind,
            "confidence": confidence,
            "evidence": evidence,
            "target_message_id": target_row["id"] if target_row else None,
            "target_created_at": target_row["created_at"] if target_row else None,
            "target_text": (target_row["text"] if target_row else None),
        })

    if msg["reply_to"]:
        add(msg["reply_to"], "explicit_reply_to", 0.96, f"reply_to={msg['reply_to']}")

    for m in re.finditer(r"@([A-Za-z0-9_\-]+)", text):
        add(m.group(1), "direct_mention", 0.86, f"direct mention @{m.group(1)}")

    for name in PERSONAS:
        if name == speaker:
            continue
        if re.search(rf"(?<![A-Za-z0-9_]){re.escape(name)}(?![A-Za-z0-9_])", text):
            add(name, "direct_mention", 0.74, f"name mention {name}")

    if not out:
        prev = previous_by_channel.get(msg["channel"] or "")
        if prev and prev["name"] and canon(prev["name"]) != speaker and (msg["created_at"] or 0) - (prev["created_at"] or 0) <= 900:
            add(prev["name"], "sequential_context", 0.38, "same-channel sequential context", prev)

    if not out:
        out.append({
            "target": "",
            "kind": "broadcast_or_unresolved",
            "confidence": 0.18,
            "evidence": "no explicit target detected",
            "target_message_id": None,
            "target_created_at": None,
            "target_text": None,
        })
    return out


def rebuild_pair_summary(con: sqlite3.Connection) -> None:
    rows = con.execute("""
      SELECT speaker,target,COALESCE(channel,'') channel, COUNT(*) n,
             SUM(CASE WHEN confidence>=0.7 THEN 1 ELSE 0 END) high_conf,
             MIN(source_created_at) first_at, MAX(source_created_at) last_at
      FROM lobster_conversation_interactions
      WHERE COALESCE(target,'')!='' AND speaker!=target
      GROUP BY speaker,target,COALESCE(channel,'')
    """).fetchall()
    for r in rows:
        samples = con.execute("""
          SELECT source_time, evidence, substr(message_text,1,180) message
          FROM lobster_conversation_interactions
          WHERE speaker=? AND target=? AND COALESCE(channel,'')=?
          ORDER BY source_message_id DESC LIMIT 4
        """, (r["speaker"], r["target"], r["channel"])).fetchall()
        con.execute("""
          INSERT INTO lobster_conversation_pair_summary
          (speaker,target,channel,interaction_count,high_confidence_count,first_created_at,last_created_at,sample_json,inference_version,updated_at)
          VALUES (?,?,?,?,?,?,?,?,?,datetime('now'))
          ON CONFLICT(speaker,target,channel,inference_version) DO UPDATE SET
            interaction_count=excluded.interaction_count,
            high_confidence_count=excluded.high_confidence_count,
            first_created_at=excluded.first_created_at,
            last_created_at=excluded.last_created_at,
            sample_json=excluded.sample_json,
            updated_at=datetime('now')
        """, (r["speaker"], r["target"], r["channel"], r["n"], r["high_conf"], r["first_at"], r["last_at"],
              json.dumps([dict(x) for x in samples], ensure_ascii=False), "historic-v1"))


def forced_uptake(con: sqlite3.Connection, since_ts: int, target_ratio: float, run_id: str) -> int:
    msg_count = con.execute("SELECT COUNT(*) FROM lounge_messages WHERE created_at>=? AND COALESCE(sys,0)=0", (since_ts,)).fetchone()[0] or 0
    target_count = int(msg_count * target_ratio)
    current = con.execute("SELECT COUNT(*) FROM lobster_message_uptake_signals WHERE created_at>=datetime('now','-12 hours')").fetchone()[0] or 0
    need = max(0, target_count - current)
    if need <= 0:
        return 0
    candidates = con.execute("""
      SELECT source_message_id, source_created_at, source_time, channel, speaker, target, interaction_kind, confidence, evidence, message_text
      FROM lobster_conversation_interactions
      WHERE source_created_at>=? AND COALESCE(target,'')!=''
      ORDER BY confidence DESC, source_message_id DESC
      LIMIT ?
    """, (since_ts, need * 2 + 80)).fetchall()
    wrote = 0
    for r in candidates:
        if wrote >= need:
            break
        status = "integrated" if (r["confidence"] or 0) >= 0.7 else "contested"
        relevance = min(1.0, max(0.35, float(r["confidence"] or 0)))
        resistance = 0.35 if status == "integrated" else 0.58
        obedience = 0.68 if status == "integrated" else 0.42
        fear = 0.38
        raw = {
            "run_id": run_id,
            "forced_social_hook": True,
            "interaction_kind": r["interaction_kind"],
            "evidence": r["evidence"],
            "rule": "force explicit replies/mentions into receptive listening until uptake >= 10% of lounge volume",
        }
        try:
            con.execute("""
              INSERT INTO lobster_message_uptake_signals
              (persona,source_message_id,source_name,channel,source_type,uptake_status,relevance,credibility,obedience,resistance,must_process,topics_json,required_response_json,message_text,reasoning,raw_json,occurred_at,created_at,fear_index,fearlessness_index,threat_type)
              VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,datetime('now'),?,?,?)
            """, (
                r["target"], r["source_message_id"], r["speaker"], r["channel"], "forced_social_interaction",
                status, relevance, 0.72, obedience, resistance, 1,
                json.dumps(["social_interaction", r["interaction_kind"]], ensure_ascii=False),
                json.dumps({"must_reply_or_internalize": True, "target_ratio": target_ratio}, ensure_ascii=False),
                r["message_text"], f"{r['target']} must process {r['speaker']}'s message: {r['evidence']}",
                json.dumps(raw, ensure_ascii=False), r["source_time"], fear, 1 - fear, "social_pressure"
            ))
            wrote += 1
        except sqlite3.IntegrityError:
            continue
    return wrote


def run_backfill(db_path: str = DB_PATH, hours: int = 12, uptake_target: float = 0.10, limit: int = 12000) -> dict:
    started = time.time()
    run_id = f"lounge-force-{uuid.uuid4().hex[:10]}"
    since_ts = int(time.time()) - hours * 3600
    with sqlite3.connect(db_path, timeout=90) as con:
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA busy_timeout=90000")
        ensure(con)
        max_done = con.execute("SELECT COALESCE(MAX(source_message_id),0) FROM lobster_conversation_interactions").fetchone()[0] or 0
        # Backfill all recent new lounge messages; if the table is stale, max_done still lets current ids through.
        msgs = con.execute("""
          SELECT id,name,text,time,result,reply_to,created_at,channel
          FROM lounge_messages
          WHERE id>? AND created_at>=? AND COALESCE(sys,0)=0 AND COALESCE(name,'')!=''
          ORDER BY id ASC LIMIT ?
        """, (max_done, since_ts, limit)).fetchall()
        con.execute("""INSERT OR REPLACE INTO lobster_conversation_interaction_runs
          (run_id,inference_version,started_at,source_message_count,interaction_count,direct_count,sequential_count,broadcast_count,summary_json)
          VALUES (?,?,?,?,?,?,?,?,?)""", (run_id, "historic-v1", iso(), len(msgs), 0, 0, 0, 0, "{}"))
        previous_by_channel: dict[str, sqlite3.Row] = {}
        inserted = direct = sequential = broadcast = 0
        for msg in msgs:
            speaker = canon(msg["name"])
            if speaker not in PERSONAS:
                previous_by_channel[msg["channel"] or ""] = msg
                continue
            targets = message_targets(con, msg, previous_by_channel)
            for t in targets:
                try:
                    con.execute("""
                      INSERT INTO lobster_conversation_interactions
                      (source_message_id,source_created_at,source_time,channel,speaker,target,target_message_id,target_created_at,interaction_kind,confidence,evidence,message_text,target_text,context_json,inference_version,run_id,recorded_at)
                      VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,datetime('now'))
                    """, (
                        msg["id"], msg["created_at"], msg["time"], msg["channel"], speaker, t["target"],
                        t["target_message_id"], t["target_created_at"], t["kind"], t["confidence"], t["evidence"],
                        msg["text"] or "", t["target_text"], json.dumps({"result": msg["result"]}, ensure_ascii=False),
                        "historic-v1", run_id
                    ))
                    inserted += 1
                    if t["kind"] == "sequential_context":
                        sequential += 1
                    elif t["kind"] == "broadcast_or_unresolved":
                        broadcast += 1
                    else:
                        direct += 1
                except sqlite3.IntegrityError:
                    pass
            previous_by_channel[msg["channel"] or ""] = msg
        uptake = forced_uptake(con, since_ts, uptake_target, run_id)
        rebuild_pair_summary(con)
        summary = {"messages": len(msgs), "inserted": inserted, "uptake_forced": uptake, "max_done_before": max_done}
        con.execute("""UPDATE lobster_conversation_interaction_runs
          SET finished_at=?, interaction_count=?, direct_count=?, sequential_count=?, broadcast_count=?, summary_json=?
          WHERE run_id=?""", (iso(), inserted, direct, sequential, broadcast, json.dumps(summary, ensure_ascii=False), run_id))
        con.commit()
    return {"ok": True, "run_id": run_id, "messages_scanned": len(msgs), "interactions_inserted": inserted,
            "direct": direct, "sequential": sequential, "broadcast": broadcast, "uptake_forced": uptake,
            "elapsed_ms": round((time.time() - started) * 1000, 2)}


if __name__ == "__main__":
    print(json.dumps(run_backfill(), ensure_ascii=False))
