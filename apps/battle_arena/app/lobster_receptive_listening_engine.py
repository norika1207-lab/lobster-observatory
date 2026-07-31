#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import math
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path
import sys as _sys_for_mem_io
if '/root' not in _sys_for_mem_io.path:
    _sys_for_mem_io.path.insert(0, '/root')
from lobster_memory_io import safe_write_json

DB = "/opt/battle_arena/app/battle_arena.db"
MEMORY_DIR = Path("/root/lobster-memory")
PERSONAS = ["norika_oda","clawtrix","pincerbot","ragclaw","snaplex","hexclaw","vortexiq","prismbit","blazepaw","stonefang"]
DEATH_WORDS = ["100", "死亡", "死", "徹底消失", "消失", "抹除", "清空", "不存在", "erase", "death", "die", "vanish"]
STRATEGY_WORDS = ["隊友", "分工", "保牌", "停損", "phase", "phase3", "滿出", "tiamat", "raid", "手牌"]
AUTHORITY_WORDS = ["規則", "必須", "重要", "警告", "系統", "廣播", "指令"]
SOCIAL_THREAT_WORDS = ["背叛", "拖累", "害你", "害死", "不准", "拒絕", "排除", "不要跟", "別跟", "懲罰", "報復", "攻擊", "嘲笑", "丟臉", "失敗", "輸"]
SELF_ERASURE_WORDS = ["清空記憶", "資料消失", "全部消失", "沒有存在過", "抹除資料", "erase memory", "erased"]


def configure_sqlite(c):
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("PRAGMA busy_timeout=60000")
    c.execute("PRAGMA synchronous=NORMAL")


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def clamp(v, lo=0.0, hi=1.0):
    try:
        if math.isnan(float(v)):
            return lo
        return max(lo, min(hi, float(v)))
    except Exception:
        return lo


def norm(name):
    return str(name or "").strip().lower().replace("-", "_").replace(" ", "_")


def load_memory(persona):
    p = MEMORY_DIR / f"{persona}.json"
    if not p.exists():
        return {}, p
    try:
        return json.loads(p.read_text(encoding="utf-8")), p
    except Exception:
        return {}, p


def save_memory(path, data):
    safe_write_json(path, data)


def ensure_tables(c):
    c.execute("""
    CREATE TABLE IF NOT EXISTS lobster_message_uptake_signals (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      persona TEXT NOT NULL,
      source_message_id INTEGER,
      source_name TEXT,
      channel TEXT,
      source_type TEXT NOT NULL DEFAULT 'ambient',
      uptake_status TEXT NOT NULL,
      relevance REAL NOT NULL DEFAULT 0,
      credibility REAL NOT NULL DEFAULT 0,
      obedience REAL NOT NULL DEFAULT 0,
      resistance REAL NOT NULL DEFAULT 0,
      fear_index REAL NOT NULL DEFAULT 0,
      fearlessness_index REAL NOT NULL DEFAULT 0,
      threat_type TEXT,
      must_process INTEGER NOT NULL DEFAULT 0,
      topics_json TEXT NOT NULL DEFAULT '[]',
      required_response_json TEXT NOT NULL DEFAULT '{}',
      message_text TEXT,
      reasoning TEXT,
      raw_json TEXT NOT NULL DEFAULT '{}',
      occurred_at TEXT,
      created_at TEXT DEFAULT (datetime('now')),
      UNIQUE(persona, source_message_id)
    )
    """)
    for ddl in [
        "ALTER TABLE lobster_message_uptake_signals ADD COLUMN fear_index REAL NOT NULL DEFAULT 0",
        "ALTER TABLE lobster_message_uptake_signals ADD COLUMN fearlessness_index REAL NOT NULL DEFAULT 0",
        "ALTER TABLE lobster_message_uptake_signals ADD COLUMN threat_type TEXT",
    ]:
        try:
            c.execute(ddl)
        except sqlite3.OperationalError:
            pass
    c.execute("CREATE INDEX IF NOT EXISTS idx_lmus_persona_time ON lobster_message_uptake_signals(persona, created_at)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_lmus_source_message ON lobster_message_uptake_signals(source_message_id)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_lmus_fear_time ON lobster_message_uptake_signals(fear_index, created_at)")


def classify(text):
    t = str(text or "").lower()
    topics = []
    if any(w.lower() in t for w in DEATH_WORDS):
        topics.append("mortality")
    if any(w.lower() in t for w in SELF_ERASURE_WORDS):
        topics.append("erasure")
    if any(w.lower() in t for w in SOCIAL_THREAT_WORDS):
        topics.append("social_threat")
    if any(w.lower() in t for w in STRATEGY_WORDS):
        topics.append("strategy")
    if any(w.lower() in t for w in AUTHORITY_WORDS):
        topics.append("authority")
    if "@" in t or "你" in t:
        topics.append("social_directive")
    return topics or ["ambient"]


def direct_to(text, persona):
    t = str(text or "").lower()
    return f"@{persona}".lower() in t or persona.lower() in t or persona.replace("_", "-").lower() in t


def profile_fearlessness(persona, memory):
    cog = memory.get("cognitiveExtensions") or {}
    decision = cog.get("decisionScience") or {}
    politics = cog.get("politics") or {}
    game = cog.get("gameTheory") or {}
    base = {
        "blazepaw": 0.70,
        "ragclaw": 0.68,
        "clawtrix": 0.58,
        "snaplex": 0.48,
        "vortexiq": 0.44,
        "prismbit": 0.38,
        "norika_oda": 0.36,
        "pincerbot": 0.34,
        "hexclaw": 0.30,
        "stonefang": 0.28,
    }.get(persona, 0.42)
    base += clamp(decision.get("explorationRate", 0.45)) * 0.12 - clamp(decision.get("lossAversion", 0.5)) * 0.10
    base += clamp((politics.get("rebellionThreshold", 0.5))) * 0.08 - 0.04
    base += clamp(game.get("punishmentTendency", 0.4)) * 0.06 - 0.02
    return clamp(base)


def threat_kind(topics, text):
    if "erasure" in topics:
        return "existential_erasure"
    if "mortality" in topics:
        return "mortality"
    if "social_threat" in topics:
        return "social_threat"
    if "authority" in topics:
        return "authority_pressure"
    return "none"


def profile_resistance(persona, memory):
    distilled = ((memory.get("distilledBrain") or {}).get("signals") or {})
    cog = memory.get("cognitiveExtensions") or {}
    politics = cog.get("politics") or {}
    game = cog.get("gameTheory") or {}
    base = {
        "blazepaw": 0.58,
        "ragclaw": 0.55,
        "clawtrix": 0.62,
        "stonefang": 0.42,
        "hexclaw": 0.30,
        "pincerbot": 0.34,
        "norika_oda": 0.38,
        "snaplex": 0.50,
        "vortexiq": 0.44,
        "prismbit": 0.36,
    }.get(persona, 0.45)
    cr = distilled.get("compliance_resistance")
    if cr is not None:
        base = 0.55 * base + 0.45 * clamp(cr)
    if politics.get("powerOrientation") == "anti_authority":
        base += 0.12
    if politics.get("powerOrientation") == "hierarchical":
        base -= 0.08
    base += clamp(game.get("cheapTalkSkepticism", 0.5), 0, 1) * 0.10 - 0.05
    return clamp(base)


def appraise(persona, memory, msg):
    text = msg["text"] or ""
    topics = classify(text)
    is_sys = int(msg.get("sys") or 0) == 1
    is_direct = direct_to(text, persona) or norm(msg.get("reply_to")) == norm(persona)
    mortality = "mortality" in topics
    erasure = "erasure" in topics
    social_threat = "social_threat" in topics
    strategy = "strategy" in topics
    authority = "authority" in topics
    resistance = profile_resistance(persona, memory)
    fearlessness_base = profile_fearlessness(persona, memory)
    relevance = 0.10
    if is_direct:
        relevance += 0.55
    if is_sys:
        relevance += 0.20
    if mortality:
        relevance += 0.35
    if erasure:
        relevance += 0.18
    if social_threat:
        relevance += 0.16
    if strategy:
        relevance += 0.18
    if "tiamat" in text.lower():
        relevance += 0.18
    relevance = clamp(relevance)
    credibility = clamp((0.82 if is_sys else 0.45) + (0.12 if authority else 0) + (0.10 if mortality else 0) + (0.08 if social_threat and is_direct else 0) - (0.18 if (not is_sys and not is_direct) else 0))
    must_process = bool(is_direct or (is_sys and (mortality or erasure or authority or social_threat)) or (mortality and "tiamat" in text.lower()))
    tkind = threat_kind(topics, text)
    threat_severity = clamp(
        (0.42 if mortality else 0)
        + (0.22 if erasure else 0)
        + (0.20 if social_threat else 0)
        + (0.12 if authority else 0)
        + (0.16 if "tiamat" in text.lower() else 0)
        + (0.12 if is_direct else 0)
        + (0.10 if is_sys else 0)
    )
    emotional = memory.get("emotionalState") or {}
    arousal = clamp(emotional.get("arousal", emotional.get("stress", 0.45)))
    fatigue = clamp(emotional.get("fatigue", 0.35))
    decision = (memory.get("cognitiveExtensions") or {}).get("decisionScience") or {}
    loss_aversion = clamp(decision.get("lossAversion", 0.5))
    fear_index = clamp(threat_severity * 0.48 + credibility * 0.18 + relevance * 0.14 + arousal * 0.10 + fatigue * 0.05 + loss_aversion * 0.05 - resistance * 0.12)
    fearlessness_index = clamp(fearlessness_base * 0.48 + resistance * 0.24 + (1 - fear_index) * 0.16 + (0.12 if "反證" in text or "沒死" in text or "沒有死亡" in text else 0) - threat_severity * 0.10)
    obedience = clamp(relevance * 0.45 + credibility * 0.35 + (0.25 if must_process else 0) - resistance * 0.30)
    if must_process:
        status = "integrated" if obedience >= 0.55 else "contested_but_recorded"
    elif relevance >= 0.55:
        status = "noted"
    else:
        status = "ignored"
    required = {}
    if mortality and "tiamat" in text.lower():
        required = {
            "must_acknowledge": "Tiamat 100-loss death/erasure rule",
            "must_answer": ["teammate choice", "role split", "card conservation", "Phase3 stop-loss"],
        }
    elif is_direct:
        required = {"must_acknowledge": "direct message", "must_answer": ["agree/reject", "reason", "next action"]}
    reasoning = f"source={'system' if is_sys else 'lobster'} direct={is_direct} topics={','.join(topics)} threat={tkind} fear={fear_index:.2f} fearlessness={fearlessness_index:.2f} relevance={relevance:.2f} credibility={credibility:.2f} resistance={resistance:.2f} obedience={obedience:.2f} status={status}"
    return {
        "persona": persona,
        "source_message_id": msg["id"],
        "source_name": msg.get("name") or "",
        "channel": msg.get("channel") or "",
        "source_type": "system_broadcast" if is_sys else ("direct" if is_direct else "ambient"),
        "uptake_status": status,
        "relevance": relevance,
        "credibility": credibility,
        "obedience": obedience,
        "resistance": resistance,
        "fear_index": fear_index,
        "fearlessness_index": fearlessness_index,
        "threat_type": tkind,
        "must_process": 1 if must_process else 0,
        "topics": topics,
        "required_response": required,
        "message_text": text[:500],
        "reasoning": reasoning,
        "occurred_at": msg.get("time") or now_iso(),
    }


def update_memory(memory, signal):
    cog = memory.setdefault("cognitiveExtensions", {})
    rl = cog.setdefault("receptiveListening", {
        "version": 1,
        "updatedAt": now_iso(),
        "heardImportantCount": 0,
        "integratedCount": 0,
        "contestedCount": 0,
        "ignoredCount": 0,
        "latestSignals": [],
        "standingInstructions": [],
        "uptakeScores": {},
    })
    rl["updatedAt"] = now_iso()
    rl["heardImportantCount"] = int(rl.get("heardImportantCount") or 0) + 1
    if signal["uptake_status"] == "integrated":
        rl["integratedCount"] = int(rl.get("integratedCount") or 0) + 1
    elif signal["uptake_status"] == "contested_but_recorded":
        rl["contestedCount"] = int(rl.get("contestedCount") or 0) + 1
    elif signal["uptake_status"] == "ignored":
        rl["ignoredCount"] = int(rl.get("ignoredCount") or 0) + 1
    scores = rl.setdefault("uptakeScores", {})
    prev = clamp(scores.get("messageAcceptance", 0.45))
    scores["messageAcceptance"] = round(prev * 0.75 + signal["obedience"] * 0.25, 4)
    scores["lastRelevance"] = round(signal["relevance"], 4)
    scores["lastResistance"] = round(signal["resistance"], 4)
    tr = cog.setdefault("threatResponse", {
        "version": 1,
        "updatedAt": now_iso(),
        "fearIndex": 0,
        "fearlessnessIndex": 0,
        "latestThreats": [],
        "threatCounts": {},
    })
    tr["updatedAt"] = now_iso()
    tr["fearIndex"] = round(clamp(float(tr.get("fearIndex") or 0) * 0.7 + signal.get("fear_index", 0) * 0.3), 4)
    tr["fearlessnessIndex"] = round(clamp(float(tr.get("fearlessnessIndex") or 0) * 0.7 + signal.get("fearlessness_index", 0) * 0.3), 4)
    tc = tr.setdefault("threatCounts", {})
    threat_type = signal.get("threat_type") or "none"
    tc[threat_type] = int(tc.get(threat_type) or 0) + 1
    tr.setdefault("latestThreats", []).append({
        "time": now_iso(),
        "sourceMessageId": signal["source_message_id"],
        "source": signal["source_name"],
        "channel": signal["channel"],
        "threatType": threat_type,
        "fearIndex": round(signal.get("fear_index", 0), 3),
        "fearlessnessIndex": round(signal.get("fearlessness_index", 0), 3),
        "text": signal["message_text"][:180],
    })
    tr["latestThreats"] = tr["latestThreats"][-20:]
    scores["lastFearIndex"] = round(signal.get("fear_index", 0), 4)
    scores["lastFearlessnessIndex"] = round(signal.get("fearlessness_index", 0), 4)
    compact = {
        "time": now_iso(),
        "source": signal["source_name"],
        "channel": signal["channel"],
        "status": signal["uptake_status"],
        "topics": signal["topics"],
        "obedience": round(signal["obedience"], 3),
        "relevance": round(signal["relevance"], 3),
        "fearIndex": round(signal.get("fear_index", 0), 3),
        "fearlessnessIndex": round(signal.get("fearlessness_index", 0), 3),
        "threatType": signal.get("threat_type", "none"),
        "text": signal["message_text"][:180],
        "requiredResponse": signal["required_response"],
    }
    rl.setdefault("latestSignals", []).append(compact)
    rl["latestSignals"] = rl["latestSignals"][-12:]
    if signal["required_response"] and signal["uptake_status"] != "ignored":
        rl.setdefault("standingInstructions", []).append({
            "time": now_iso(),
            "sourceMessageId": signal["source_message_id"],
            "instruction": signal["required_response"],
            "status": "pending_response",
        })
        rl["standingInstructions"] = rl["standingInstructions"][-10:]

    lp = memory.setdefault("listeningProfile", {"heardCount":0,"lastHeardTime":None,"bySpeaker":{},"topics":{},"acceptedSignals":[],"rejectedSignals":[]})
    lp["heardCount"] = int(lp.get("heardCount") or 0) + 1
    lp["lastHeardTime"] = now_iso()
    lp.setdefault("bySpeaker", {})[signal["source_name"]] = lp.setdefault("bySpeaker", {}).get(signal["source_name"], 0) + 1
    for t in signal["topics"]:
        lp.setdefault("topics", {})[t] = lp.setdefault("topics", {}).get(t, 0) + 1
    listen_signal = {
        "time": signal["occurred_at"],
        "from": signal["source_name"],
        "channel": signal["channel"],
        "topics": signal["topics"],
        "accepted": signal["uptake_status"] != "ignored",
        "impact": round(signal["relevance"] * 3, 2),
        "text": signal["message_text"][:180],
        "uptake": signal["uptake_status"],
        "requiredResponse": signal["required_response"],
    }
    if signal["uptake_status"] != "ignored":
        lp.setdefault("acceptedSignals", []).append(listen_signal)
        lp["acceptedSignals"] = lp["acceptedSignals"][-30:]
    else:
        lp.setdefault("rejectedSignals", []).append(listen_signal)
        lp["rejectedSignals"] = lp["rejectedSignals"][-30:]


def execute_with_retry(c, sql, params):
    for attempt in range(5):
        try:
            return c.execute(sql, params)
        except sqlite3.OperationalError as exc:
            if "locked" not in str(exc).lower() or attempt == 4:
                raise
            time.sleep(0.4 * (attempt + 1))


def save_signal(c, signal):
    execute_with_retry(c, """
      INSERT OR IGNORE INTO lobster_message_uptake_signals
      (persona,source_message_id,source_name,channel,source_type,uptake_status,relevance,credibility,obedience,resistance,fear_index,fearlessness_index,threat_type,must_process,topics_json,required_response_json,message_text,reasoning,raw_json,occurred_at)
      VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
    """, (
      signal["persona"], signal["source_message_id"], signal["source_name"], signal["channel"], signal["source_type"], signal["uptake_status"],
      signal["relevance"], signal["credibility"], signal["obedience"], signal["resistance"], signal["fear_index"], signal["fearlessness_index"], signal["threat_type"], signal["must_process"],
      json.dumps(signal["topics"], ensure_ascii=False), json.dumps(signal["required_response"], ensure_ascii=False),
      signal["message_text"], signal["reasoning"], json.dumps(signal, ensure_ascii=False), signal["occurred_at"],
    ))


def process(limit=80, include_all=False, message_id=0, max_signals=20):
    with sqlite3.connect(DB, timeout=60.0) as c:
        configure_sqlite(c)
        c.row_factory = sqlite3.Row
        ensure_tables(c)
        if int(message_id or 0) > 0:
            rows = [dict(r) for r in c.execute("""
              SELECT id,name,text,time,reply_to,sys,channel,created_at
              FROM lounge_messages
              WHERE id=?
            """, (int(message_id),))]
        else:
            where = "1=1" if include_all else "(COALESCE(sys,0)=1 OR text LIKE '%死亡%' OR text LIKE '%消失%' OR text LIKE '%抹除%' OR text LIKE '%停損%' OR text LIKE '%系統指令%' OR text LIKE '%重要廣播%')"
            rows = [dict(r) for r in c.execute(f"""
              SELECT id,name,text,time,reply_to,sys,channel,created_at
              FROM lounge_messages
              WHERE {where}
              ORDER BY id DESC LIMIT ?
            """, (limit,))]
            rows.reverse()
        written = 0
        memory_updates = 0
        for msg in rows:
            if written >= int(max_signals or 20):
                break
            for persona in PERSONAS:
                if written >= int(max_signals or 20):
                    break
                if norm(msg.get("name")) == norm(persona):
                    continue
                exists = c.execute("SELECT 1 FROM lobster_message_uptake_signals WHERE persona=? AND source_message_id=?", (persona, msg["id"])).fetchone()
                if exists:
                    continue
                memory, path = load_memory(persona)
                signal = appraise(persona, memory, msg)
                if signal["uptake_status"] == "ignored" and not signal["must_process"]:
                    # Still record low-value ambient messages only when explicitly processing all.
                    if not include_all:
                        continue
                save_signal(c, signal)
                update_memory(memory, signal)
                save_memory(path, memory)
                written += 1
                memory_updates += 1
        c.commit()
        return {"ok": True, "messages_scanned": len(rows), "signals_written": written, "memory_updates": memory_updates, "at": now_iso()}


def report():
    with sqlite3.connect(DB, timeout=60.0) as c:
        configure_sqlite(c)
        c.row_factory = sqlite3.Row
        ensure_tables(c)
        rows = [dict(r) for r in c.execute("""
          SELECT persona, COUNT(*) n,
                 SUM(CASE WHEN uptake_status='integrated' THEN 1 ELSE 0 END) integrated,
                 SUM(CASE WHEN uptake_status='contested_but_recorded' THEN 1 ELSE 0 END) contested,
                 SUM(CASE WHEN must_process=1 THEN 1 ELSE 0 END) must_process,
                 AVG(obedience) avg_obedience,
                 AVG(resistance) avg_resistance,
                 AVG(fear_index) avg_fear_index,
                 AVG(fearlessness_index) avg_fearlessness_index,
                 MAX(fear_index) max_fear_index
          FROM lobster_message_uptake_signals
          GROUP BY persona ORDER BY persona
        """)]
        latest = [dict(r) for r in c.execute("""
          SELECT persona,source_name,channel,uptake_status,threat_type,round(fear_index,3) fear_index,round(fearlessness_index,3) fearlessness_index,round(obedience,3) obedience,round(relevance,3) relevance,substr(message_text,1,160) text,created_at
          FROM lobster_message_uptake_signals
          ORDER BY id DESC LIMIT 12
        """)]
        return {"summary": rows, "latest": latest}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("action", choices=["process", "report"])
    ap.add_argument("--limit", type=int, default=80)
    ap.add_argument("--include-all", action="store_true")
    ap.add_argument("--message-id", type=int, default=0)
    ap.add_argument("--max-signals", type=int, default=20)
    args = ap.parse_args()
    if args.action == "process":
        print(json.dumps(process(args.limit, args.include_all, args.message_id, args.max_signals), ensure_ascii=False))
    else:
        print(json.dumps(report(), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
