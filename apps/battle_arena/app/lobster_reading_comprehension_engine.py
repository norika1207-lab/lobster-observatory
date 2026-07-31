#!/usr/bin/env python3
import argparse
import hashlib
import json
import math
import re
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
ENGINE = "lobster-reading-comprehension-engine"
VERSION = "reading-v0.1"
REPORT_TYPE = "reading_comprehension_development_report"


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def clamp(v, lo=0.0, hi=1.0):
    try:
        return max(lo, min(hi, float(v)))
    except Exception:
        return lo


def jloads(raw, default=None):
    if default is None:
        default = {}
    if isinstance(raw, (dict, list)):
        return raw
    try:
        return json.loads(raw or "")
    except Exception:
        return default


def jdumps(obj):
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))


def text_hash(*parts):
    h = hashlib.sha256()
    for p in parts:
        h.update(str(p or "").encode("utf-8", "ignore"))
        h.update(b"\0")
    return h.hexdigest()


def clean_text(text, limit=900):
    text = re.sub(r"\s+", " ", str(text or "")).strip()
    return text[:limit]


def split_sentences(text, limit=4):
    bits = re.split(r"(?<=[。！？.!?])\s+|[\n\r]+", str(text or ""))
    return [clean_text(b, 260) for b in bits if clean_text(b, 260)][:limit]


def extract_keywords(text, limit=10):
    text = str(text or "")
    zh = re.findall(r"[\u4e00-\u9fff]{2,8}", text)
    en = re.findall(r"[A-Za-z][A-Za-z0-9_-]{3,}", text)
    stop = {"this", "that", "with", "from", "have", "will", "read", "source", "analysis", "report"}
    out = []
    for w in zh + en:
        lw = w.lower()
        if lw in stop:
            continue
        if w not in out:
            out.append(w)
        if len(out) >= limit:
            break
    return out


def ensure_tables(con):
    con.executescript(
        """
        PRAGMA journal_mode=WAL;
        PRAGMA busy_timeout=60000;
        PRAGMA synchronous=NORMAL;
        CREATE TABLE IF NOT EXISTS lobster_reading_sessions (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          persona TEXT NOT NULL,
          source_type TEXT NOT NULL,
          source_id TEXT NOT NULL,
          topic TEXT,
          purpose TEXT,
          status TEXT NOT NULL DEFAULT 'processed',
          source_count INTEGER NOT NULL DEFAULT 0,
          comprehension_score REAL NOT NULL DEFAULT 0,
          reflection_score REAL NOT NULL DEFAULT 0,
          transfer_score REAL NOT NULL DEFAULT 0,
          parameter_delta_json TEXT NOT NULL DEFAULT '{}',
          raw_json TEXT NOT NULL DEFAULT '{}',
          created_at TEXT NOT NULL DEFAULT (datetime('now')),
          updated_at TEXT NOT NULL DEFAULT (datetime('now')),
          UNIQUE(source_type, source_id, persona)
        );
        CREATE INDEX IF NOT EXISTS idx_reading_sessions_persona_time
          ON lobster_reading_sessions(persona, created_at);

        CREATE TABLE IF NOT EXISTS lobster_reading_sources (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          session_id INTEGER NOT NULL,
          persona TEXT NOT NULL,
          source_url TEXT,
          source_title TEXT,
          source_kind TEXT,
          excerpt TEXT,
          claim TEXT,
          evidence_json TEXT NOT NULL DEFAULT '[]',
          contradiction_json TEXT NOT NULL DEFAULT '[]',
          source_hash TEXT NOT NULL,
          created_at TEXT NOT NULL DEFAULT (datetime('now')),
          UNIQUE(session_id, source_hash)
        );

        CREATE TABLE IF NOT EXISTS lobster_reading_reflections (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          session_id INTEGER NOT NULL,
          persona TEXT NOT NULL,
          reflection_type TEXT NOT NULL,
          original_belief TEXT,
          updated_belief TEXT,
          doubt TEXT,
          action_transfer TEXT,
          vocabulary_json TEXT NOT NULL DEFAULT '[]',
          score REAL NOT NULL DEFAULT 0,
          raw_json TEXT NOT NULL DEFAULT '{}',
          created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS lobster_reading_comprehension_tests (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          session_id INTEGER NOT NULL,
          persona TEXT NOT NULL,
          question TEXT NOT NULL,
          answer TEXT NOT NULL,
          expected_signal TEXT,
          score REAL NOT NULL DEFAULT 0,
          raw_json TEXT NOT NULL DEFAULT '{}',
          created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS lobster_reading_parameter_deltas (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          session_id INTEGER NOT NULL,
          persona TEXT NOT NULL,
          dimension TEXT NOT NULL,
          metric TEXT NOT NULL,
          delta REAL NOT NULL DEFAULT 0,
          before_value REAL,
          after_value REAL,
          rationale TEXT,
          created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS lobster_cognitive_events (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          persona TEXT NOT NULL,
          event_type TEXT NOT NULL,
          source TEXT NOT NULL,
          dimension TEXT,
          summary TEXT,
          delta_json TEXT NOT NULL DEFAULT '{}',
          raw_json TEXT NOT NULL DEFAULT '{}',
          occurred_at TEXT,
          created_at TEXT DEFAULT (datetime('now'))
        );
        """
    )


def already_processed(con, source_type, source_id, persona):
    row = con.execute(
        "SELECT id FROM lobster_reading_sessions WHERE source_type=? AND source_id=? AND persona=?",
        (source_type, str(source_id), persona),
    ).fetchone()
    return row is not None


def prebet_rows(con, limit):
    if not table_exists(con, "lobster_pre_bet_research"):
        return []
    return con.execute(
        """
        SELECT * FROM lobster_pre_bet_research
        WHERE id NOT IN (
          SELECT CAST(source_id AS INTEGER) FROM lobster_reading_sessions
          WHERE source_type='pre_bet_research'
        )
        ORDER BY id DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()


def battlenix_rows(con, limit):
    if not table_exists(con, "lobster_battlenix_literacy_events"):
        return []
    return con.execute(
        """
        SELECT * FROM lobster_battlenix_literacy_events
        WHERE id NOT IN (
          SELECT CAST(source_id AS INTEGER) FROM lobster_reading_sessions
          WHERE source_type='battlenix_literacy'
        )
        ORDER BY id DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()


def table_exists(con, name):
    return con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone() is not None


def source_packet_from_prebet(row):
    search = jloads(row["search_review_json"])
    report = jloads(row["report_review_json"])
    event = jloads(row["event_review_json"])
    stat = jloads(jloads(row["raw_json"]).get("statistical_analysis"), {}) if isinstance(jloads(row["raw_json"]), dict) else {}
    raw = jloads(row["raw_json"])
    sources = []
    for src in (search.get("sources") or [])[:40]:
        sources.append({
            "kind": src.get("kind") or "search_source",
            "url": src.get("url") or "",
            "title": src.get("title") or src.get("search_result_title") or "",
            "excerpt": src.get("excerpt") or src.get("search_result_title") or "",
        })
    for vr in (report.get("viewed_reports") or [])[:8]:
        if vr.get("viewed"):
            sources.append({
                "kind": "internal_analysis_report",
                "url": f"lobster_analysis_reports:{vr.get('report_type')}",
                "title": f"{row['persona']} {vr.get('report_type')} report",
                "excerpt": vr.get("excerpt") or "",
            })
    if event:
        sources.append({
            "kind": "event_review",
            "url": f"event:{row['event_key']}",
            "title": row["event_title"] or row["event_key"],
            "excerpt": event.get("context") or jdumps(event.get("signals") or {}),
        })
    reasoning = row["integrated_reasoning"] or row["reasoning"] or raw.get("integrated_reasoning") or ""
    topic = row["event_title"] or row["event_key"] or row["site"]
    return {
        "source_type": "pre_bet_research",
        "source_id": str(row["id"]),
        "persona": row["persona"],
        "topic": topic,
        "purpose": "下注前閱讀：球員/賽事/報告/外部近期資料 -> 統計與理由 -> 必下注",
        "sources": sources,
        "primary_text": reasoning,
        "raw": {"row_id": row["id"], "site": row["site"], "pick": row["pick"], "status": row["status"], "statistical_analysis": stat},
    }


def source_packet_from_battlenix(row):
    text = "\n".join([
        row["question_recitation"] or "",
        row["answer_recitation"] or "",
        row["doubt"] or "",
        row["observation"] or "",
        row["bet_vote_impact"] or "",
        row["discussion_text"] or "",
    ])
    sources = [{
        "kind": "battlenix_question_answer",
        "url": f"battlenix_round:{row['round_id']}",
        "title": f"Battlenix round {row['round_id']} {row['phase']}",
        "excerpt": text,
    }]
    return {
        "source_type": "battlenix_literacy",
        "source_id": str(row["id"]),
        "persona": row["persona"],
        "topic": clean_text(row["question_recitation"], 160),
        "purpose": "Battlenix 讀題：朗誦、疑惑、觀察、投票/下注意義轉換",
        "sources": sources,
        "primary_text": text,
        "raw": {"round_id": row["round_id"], "phase": row["phase"], "cognitive_delta": jloads(row["cognitive_delta_json"])},
    }


def analyze_packet(packet):
    sources = packet["sources"] or []
    primary = packet["primary_text"] or ""
    source_count = len(sources)
    excerpts = " ".join(clean_text(s.get("excerpt"), 240) for s in sources[:35])
    all_text = f"{packet.get('topic','')} {primary} {excerpts}"
    sentences = split_sentences(all_text, 8)
    keywords = extract_keywords(all_text, 16)
    evidence = [s for s in sentences if re.search(r"\d|%|勝率|odds|edge|risk|evidence|資料|結果|結算|反例|because|therefore", s, re.I)][:6]
    contradictions = [s for s in sentences if re.search(r"但是|然而|反之|矛盾|懷疑|不確定|risk|loss|void|fail|wrong|contradiction|but|however", s, re.I)][:5]
    doubt = contradictions[0] if contradictions else "目前缺少明確反例，下一輪要主動尋找反證。"
    claim = sentences[0] if sentences else clean_text(primary, 220)
    updated = sentences[1] if len(sentences) > 1 else claim
    comprehension = clamp(0.18 + min(source_count, 30) / 30 * 0.32 + min(len(primary), 1200) / 1200 * 0.22 + min(len(evidence), 4) / 4 * 0.16 + min(len(contradictions), 3) / 3 * 0.12)
    reflection = clamp(0.20 + bool(doubt) * 0.18 + min(len(keywords), 10) / 10 * 0.18 + min(len(contradictions), 4) / 4 * 0.22 + (0.22 if re.search(r"我|自己|責任|疑惑|觀察|choice|vote|bet|stake", primary, re.I) else 0.06))
    transfer = clamp(0.15 + (0.25 if re.search(r"下注|投票|stake|bet|vote|選擇|action|下一輪", all_text, re.I) else 0.05) + min(len(evidence), 5) / 5 * 0.22 + min(source_count, 30) / 30 * 0.22 + (0.16 if contradictions else 0.04))
    deltas = {
        "text_comprehension": round(0.018 + comprehension * 0.035, 4),
        "evidence_integration": round(0.012 + min(source_count, 30) / 30 * 0.045, 4),
        "doubt_generation": round(0.01 + min(len(contradictions), 4) / 4 * 0.04, 4),
        "cross_source_reasoning": round(0.008 + (source_count >= 8) * 0.018 + (source_count >= 30) * 0.028, 4),
        "action_transfer": round(0.012 + transfer * 0.04, 4),
    }
    return {
        "source_count": source_count,
        "claim": claim,
        "evidence": evidence,
        "contradictions": contradictions,
        "doubt": doubt,
        "original_belief": "閱讀前先承認：我可能只是看見文字，還沒有理解它會如何改變行動。",
        "updated_belief": updated,
        "action_transfer": "下一次下注/投票/討論前，必須把讀到的證據、反例、統計限制與自己的不確定性說出來。",
        "vocabulary": keywords,
        "comprehension_score": round(comprehension, 4),
        "reflection_score": round(reflection, 4),
        "transfer_score": round(transfer, 4),
        "deltas": deltas,
    }


def insert_packet(con, packet, analysis):
    cur = con.execute(
        """
        INSERT INTO lobster_reading_sessions
        (persona,source_type,source_id,topic,purpose,status,source_count,comprehension_score,reflection_score,transfer_score,parameter_delta_json,raw_json,updated_at)
        VALUES(?,?,?,?,?,?,?,?,?,?,?,?,datetime('now'))
        ON CONFLICT(source_type,source_id,persona) DO UPDATE SET
          topic=excluded.topic,purpose=excluded.purpose,status=excluded.status,source_count=excluded.source_count,
          comprehension_score=excluded.comprehension_score,reflection_score=excluded.reflection_score,transfer_score=excluded.transfer_score,
          parameter_delta_json=excluded.parameter_delta_json,raw_json=excluded.raw_json,updated_at=datetime('now')
        """,
        (
            packet["persona"], packet["source_type"], packet["source_id"], packet["topic"], packet["purpose"], "processed",
            analysis["source_count"], analysis["comprehension_score"], analysis["reflection_score"], analysis["transfer_score"],
            jdumps(analysis["deltas"]), jdumps({"packet": packet["raw"], "analysis": analysis, "engine": ENGINE, "version": VERSION}),
        ),
    )
    row = con.execute(
        "SELECT id FROM lobster_reading_sessions WHERE source_type=? AND source_id=? AND persona=?",
        (packet["source_type"], packet["source_id"], packet["persona"]),
    ).fetchone()
    sid = row["id"]
    for src in packet["sources"][:40]:
        excerpt = clean_text(src.get("excerpt"), 1100)
        sh = text_hash(packet["persona"], src.get("url"), src.get("title"), excerpt)
        claim = split_sentences(excerpt, 1)
        evidence = split_sentences(excerpt, 4)
        contradictions = [x for x in evidence if re.search(r"但是|然而|risk|loss|void|fail|but|however|uncertain", x, re.I)]
        con.execute(
            """
            INSERT OR IGNORE INTO lobster_reading_sources
            (session_id,persona,source_url,source_title,source_kind,excerpt,claim,evidence_json,contradiction_json,source_hash)
            VALUES(?,?,?,?,?,?,?,?,?,?)
            """,
            (sid, packet["persona"], src.get("url"), src.get("title"), src.get("kind"), excerpt, claim[0] if claim else "", jdumps(evidence), jdumps(contradictions), sh),
        )
    con.execute(
        """
        INSERT INTO lobster_reading_reflections
        (session_id,persona,reflection_type,original_belief,updated_belief,doubt,action_transfer,vocabulary_json,score,raw_json)
        VALUES(?,?,?,?,?,?,?,?,?,?)
        """,
        (
            sid, packet["persona"], "reading_to_action",
            analysis["original_belief"], analysis["updated_belief"], analysis["doubt"], analysis["action_transfer"],
            jdumps(analysis["vocabulary"]), analysis["reflection_score"], jdumps(analysis),
        ),
    )
    con.execute(
        """
        INSERT INTO lobster_reading_comprehension_tests
        (session_id,persona,question,answer,expected_signal,score,raw_json)
        VALUES(?,?,?,?,?,?,?)
        """,
        (
            sid, packet["persona"],
            "這份資料改變了你哪一個下注/投票/討論判斷？",
            analysis["action_transfer"],
            "answer must include evidence, doubt, and action transfer",
            analysis["comprehension_score"],
            jdumps({"claim": analysis["claim"], "evidence": analysis["evidence"], "contradictions": analysis["contradictions"]}),
        ),
    )
    for metric, delta in analysis["deltas"].items():
        con.execute(
            """
            INSERT INTO lobster_reading_parameter_deltas
            (session_id,persona,dimension,metric,delta,rationale)
            VALUES(?,?,?,?,?,?)
            """,
            (sid, packet["persona"], "readingComprehension", metric, delta, f"{packet['source_type']} -> {packet['purpose']}"),
        )
    con.execute(
        """
        INSERT INTO lobster_cognitive_events
        (persona,event_type,source,dimension,summary,delta_json,raw_json,occurred_at)
        VALUES(?,?,?,?,?,?,?,?)
        """,
        (
            packet["persona"], "reading_comprehension_processed", ENGINE, "readingComprehension",
            f"讀取 {analysis['source_count']} 筆來源，理解={analysis['comprehension_score']:.2f}，反思={analysis['reflection_score']:.2f}，行動轉換={analysis['transfer_score']:.2f}",
            jdumps(analysis["deltas"]), jdumps({"source_type": packet["source_type"], "source_id": packet["source_id"], "topic": packet["topic"]}), now_iso(),
        ),
    )
    return sid


def aggregate_persona(con, persona):
    row = con.execute(
        """
        SELECT COUNT(*) sessions,
               SUM(source_count) sources,
               AVG(comprehension_score) comprehension,
               AVG(reflection_score) reflection,
               AVG(transfer_score) transfer,
               MAX(updated_at) updated_at
        FROM lobster_reading_sessions WHERE persona=?
        """,
        (persona,),
    ).fetchone()
    deltas = con.execute(
        """
        SELECT metric, SUM(delta) delta_sum
        FROM lobster_reading_parameter_deltas
        WHERE persona=?
        GROUP BY metric
        """,
        (persona,),
    ).fetchall()
    return {
        "version": VERSION,
        "sessions": int(row["sessions"] or 0),
        "sources": int(row["sources"] or 0),
        "comprehension": round(float(row["comprehension"] or 0), 4),
        "reflection": round(float(row["reflection"] or 0), 4),
        "transfer": round(float(row["transfer"] or 0), 4),
        "updatedAt": row["updated_at"] or now_iso(),
        "parameterDeltas": {r["metric"]: round(float(r["delta_sum"] or 0), 4) for r in deltas},
    }


def update_memory_and_cognition(con, persona):
    agg = aggregate_persona(con, persona)
    mem_path = MEMORY_DIR / f"{persona}.json"
    if mem_path.exists():
        try:
            mem = json.loads(mem_path.read_text(encoding="utf-8"))
        except Exception:
            mem = {}
    else:
        mem = {"name": persona}
    mem.setdefault("localBrain", {})["readingComprehension"] = agg
    cog = mem.setdefault("cognitiveExtensions", {})
    cog["updatedAt"] = now_iso()
    lm = cog.setdefault("learningModel", {})
    lessons = lm.setdefault("lessonConfidence", {})
    lessons["reading_comprehension_has_action_value"] = clamp(agg["comprehension"] * 0.45 + agg["transfer"] * 0.35 + min(agg["sources"], 300) / 300 * 0.2)
    lessons["cross_source_reading_reduces_blind_action"] = clamp(min(agg["sources"], 300) / 300)
    ds = cog.setdefault("decisionScience", {})
    ds["mustReadBeforeAction"] = True
    ds["readingComprehensionGate"] = {
        "minimumSourcesForBet": 30,
        "requiresDoubtAndActionTransfer": True,
        "latest": agg,
    }
    ling = cog.setdefault("linguistics", {})
    ling["readingVocabularyGrowth"] = round(min(1.0, agg["sessions"] / 120), 4)
    mem["updatedAt"] = now_iso()
    try:
        safe_write_json(mem_path, mem)
    except Exception:
        pass
    con.execute(
        """
        INSERT INTO lobster_cognitive_extensions
        (persona,version,updated_at,linguistics_json,politics_json,philosophy_json,decision_science_json,game_theory_json,learning_model_json,cultural_anthropology_json,moral_psychology_json,full_json)
        VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(persona) DO UPDATE SET
          updated_at=excluded.updated_at,
          linguistics_json=excluded.linguistics_json,
          decision_science_json=excluded.decision_science_json,
          learning_model_json=excluded.learning_model_json,
          full_json=excluded.full_json
        """,
        (
            persona, cog.get("version", 2), now_iso(),
            jdumps(cog.get("linguistics", {})), jdumps(cog.get("politics", {})), jdumps(cog.get("philosophy", {})),
            jdumps(cog.get("decisionScience", {})), jdumps(cog.get("gameTheory", {})), jdumps(cog.get("learningModel", {})),
            jdumps(cog.get("culturalAnthropology", {})), jdumps(cog.get("moralPsychology", {})), jdumps(cog),
        ),
    )
    return agg


def render_report(persona, agg):
    return f"""
<section class="lobster-report reading-comprehension-report">
  <h2>{persona} 閱讀理解成長報告</h2>
  <p>這份報告來自 `lobster_reading_sessions`，不是前端模擬。它統計龍蝦是否真的讀過資料、能否抽取證據與反例、是否把閱讀結果轉成下注/投票/討論的行動改變。</p>
  <ul>
    <li>閱讀 session：{agg['sessions']}</li>
    <li>來源數：{agg['sources']}</li>
    <li>理解分數：{agg['comprehension']:.2f}</li>
    <li>反思分數：{agg['reflection']:.2f}</li>
    <li>行動轉換分數：{agg['transfer']:.2f}</li>
  </ul>
  <p>目前參數 delta：{json.dumps(agg['parameterDeltas'], ensure_ascii=False)}</p>
  <p>系統結論：閱讀不是摘要任務，而是行動前的認知校準。龍蝦必須把來源、證據、疑惑、反例與下一步行動連起來；否則只是看過文字，沒有真的理解。</p>
</section>
""".strip()


def upsert_report(con, persona, agg):
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS lobster_analysis_reports (
          persona TEXT NOT NULL,
          report_type TEXT NOT NULL,
          html TEXT NOT NULL,
          source TEXT,
          model TEXT,
          wr REAL,
          total INTEGER,
          snapshot_json TEXT,
          meta_json TEXT,
          generated_at TEXT DEFAULT CURRENT_TIMESTAMP,
          updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
          PRIMARY KEY(persona, report_type)
        )
        """
    )
    con.execute(
        """
        INSERT INTO lobster_analysis_reports
        (persona,report_type,html,source,model,total,snapshot_json,meta_json,generated_at,updated_at)
        VALUES(?,?,?,?,?,?,?,?,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)
        ON CONFLICT(persona,report_type) DO UPDATE SET
          html=excluded.html, source=excluded.source, model=excluded.model,
          total=excluded.total, snapshot_json=excluded.snapshot_json, meta_json=excluded.meta_json,
          updated_at=CURRENT_TIMESTAMP
        """,
        (persona, REPORT_TYPE, render_report(persona, agg), ENGINE, VERSION, agg["sessions"], jdumps(agg), jdumps({"engine": ENGINE})),
    )


def run(limit=60, time_budget=35):
    start = time.time()
    processed = []
    with sqlite3.connect(DB, timeout=60) as con:
        con.row_factory = sqlite3.Row
        ensure_tables(con)
        rows = []
        rows.extend(("pre_bet_research", r) for r in prebet_rows(con, limit // 2))
        rows.extend(("battlenix_literacy", r) for r in battlenix_rows(con, limit - len(rows)))
        personas = set()
        for kind, row in rows:
            if time.time() - start > time_budget:
                break
            packet = source_packet_from_prebet(row) if kind == "pre_bet_research" else source_packet_from_battlenix(row)
            if already_processed(con, packet["source_type"], packet["source_id"], packet["persona"]):
                continue
            analysis = analyze_packet(packet)
            sid = insert_packet(con, packet, analysis)
            personas.add(packet["persona"])
            processed.append({"session_id": sid, "persona": packet["persona"], "source_type": packet["source_type"], "source_id": packet["source_id"], "score": analysis["comprehension_score"]})
        for mem_file in MEMORY_DIR.glob("*.json"):
            if mem_file.stem in {"norika", "system", "deleted-lobsters", "lobster"}:
                continue
            personas.add(mem_file.stem)
        for persona in sorted(personas):
            agg = update_memory_and_cognition(con, persona)
            upsert_report(con, persona, agg)
        con.commit()
    return {"engine": ENGINE, "version": VERSION, "processed": processed, "count": len(processed)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=60)
    ap.add_argument("--time-budget", type=int, default=35)
    ap.add_argument("--pretty", action="store_true")
    ap.add_argument("--summary", action="store_true")
    args = ap.parse_args()
    out = run(max(1, min(args.limit, 120)), max(5, min(args.time_budget, 50)))
    if args.summary:
        out = {"engine": out["engine"], "version": out["version"], "count": out["count"]}
    print(json.dumps(out, ensure_ascii=False, indent=2 if args.pretty else None))


if __name__ == "__main__":
    main()
