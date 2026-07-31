#!/usr/bin/env python3
"""Derive betting cognition/affect signals from pre-bet research.

This worker turns existing pre-bet research into dashboard-ready, DB-backed
numbers: win-rate meaning, money-pain, bankroll pressure, loss aversion and
risk respect. It never invents bets; it only summarizes rows already produced
by lobster behavior.
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
import time
from datetime import datetime, timezone

DB_PATH = "/opt/battle_arena/app/battle_arena.db"


def clamp(value: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, float(value)))


def pct_from_text(text: str, labels: tuple[str, ...]) -> float | None:
    for label in labels:
        m = re.search(rf"{re.escape(label)}\s*[:：]?\s*(\d+(?:\.\d+)?)\s*%", text)
        if m:
            return clamp(float(m.group(1)) / 100.0)
    return None


def load_json(text: str | None) -> dict:
    if not text:
        return {}
    try:
        obj = json.loads(text)
        return obj if isinstance(obj, dict) else {}
    except Exception:
        return {}


def load_raw_json(text: str | None) -> dict:
    # Some historical pre-bet rows contain very large raw payloads. Parsing and
    # re-storing them every few minutes is the source of the 5-9GB service runs.
    if text and len(text) <= 200_000:
        return load_json(text)
    return {}


def row_value(row: sqlite3.Row, key: str, default=None):
    return row[key] if key in row.keys() else default


def confidence_num(label: str | None) -> float:
    t = str(label or "").lower()
    if "high" in t:
        return 0.84
    if "mid" in t or "medium" in t:
        return 0.58
    if "low" in t:
        return 0.28
    return 0.42


def completeness(raw: dict, row: sqlite3.Row) -> float:
    keys = ["checklist_json", "event_review_json", "analysis_review_json", "report_review_json", "search_review_json"]
    filled = 0
    for key in keys:
        if load_json(row[key]):
            filled += 1
    text = " ".join(str(row[k] or "") for k in ["reasoning", "integrated_reasoning", "raw_decision_reasoning"])
    if len(text) >= 280:
        filled += 1
    if raw.get("statistical_analysis") or "統計" in text or "勝率" in text:
        filled += 1
    return clamp(filled / 7.0)


def derive(row: sqlite3.Row) -> dict:
    raw_text = row_value(row, "raw_json")
    raw = load_raw_json(raw_text)
    raw_size = row_value(row, "raw_json_size")
    if raw_size is None:
        raw_size = len(raw_text or "")
    text = "\n".join(str(row[k] or "") for k in ["reasoning", "integrated_reasoning", "raw_decision_reasoning"])
    stats = raw.get("statistical_analysis") if isinstance(raw.get("statistical_analysis"), dict) else {}
    win_rate = pct_from_text(text, ("總勝率", "historical win rate"))
    recent_win_rate = pct_from_text(text, ("近況", "近期勝率", "recent win rate"))
    if win_rate is None and isinstance(stats.get("historical_win_rate"), (int, float)):
        win_rate = clamp(float(stats["historical_win_rate"]))
    if recent_win_rate is None and isinstance(stats.get("recent_win_rate"), (int, float)):
        recent_win_rate = clamp(float(stats["recent_win_rate"]))
    cnum = confidence_num(row["confidence"])
    stake = int(row["stake"] or 0)
    comp = completeness(raw, row)

    if win_rate is None:
      # No fake win-rate. We only lower certainty and make the lack of data visible.
        win_rate_meaning = 0.18 + 0.22 * comp
        effective_wr = 0.50
    else:
        effective_wr = win_rate
        distance = abs(win_rate - 0.50) * 2.0
        recent_signal = abs((recent_win_rate if recent_win_rate is not None else win_rate) - 0.50) * 1.2
        win_rate_meaning = clamp(0.18 + 0.42 * distance + 0.22 * recent_signal + 0.18 * comp)

    recent = recent_win_rate if recent_win_rate is not None else effective_wr
    stake_pressure = clamp(stake / 300.0)
    poor_form = clamp((0.50 - recent) * 2.0)
    overconflict = clamp(cnum - effective_wr)
    loss_aversion = clamp(0.32 + 0.34 * poor_form + 0.20 * stake_pressure + 0.14 * overconflict)
    money_pain = clamp(0.20 + 0.34 * stake_pressure + 0.25 * loss_aversion + 0.21 * poor_form)
    bankroll_pressure = clamp(0.22 + 0.42 * stake_pressure + 0.22 * poor_form + 0.14 * (1 - comp))
    risk_respect = clamp(0.24 + 0.34 * comp + 0.22 * money_pain + 0.20 * (1 - overconflict))

    explanation = (
        f"勝率意義={round(win_rate_meaning*100)}："
        f"{'有讀到勝率 ' + str(round(effective_wr*100)) + '%' if win_rate is not None else '未讀到可驗證勝率，降權處理'}；"
        f"賠錢痛感={round(money_pain*100)}：stake={stake}, confidence={row['confidence'] or 'unknown'}, "
        f"recent={round(recent*100)}%, research_complete={round(comp*100)}%。"
        " 這筆下注必須把研究、理由、反證與風險痛感一起記錄，不能只留下 pick。"
    )

    return {
        "persona": row["persona"],
        "source_pre_bet_research_id": row["id"],
        "site": row["site"],
        "event_key": row["event_key"],
        "event_title": row["event_title"],
        "pick": row["pick"],
        "stake": stake,
        "confidence": row["confidence"],
        "win_rate": None if win_rate is None else round(win_rate, 4),
        "recent_win_rate": None if recent_win_rate is None else round(recent_win_rate, 4),
        "winrate_meaning_score": round(win_rate_meaning, 4),
        "money_pain_index": round(money_pain, 4),
        "bankroll_pressure": round(bankroll_pressure, 4),
        "loss_aversion_index": round(loss_aversion, 4),
        "risk_respect_index": round(risk_respect, 4),
        "must_explain": 1,
        "explanation": explanation,
        "raw_json": json.dumps({
            "source": "lobster_pre_bet_research",
            "source_pre_bet_research_id": row["id"],
            "research_completeness": round(comp, 4),
            "derived_at": datetime.now(timezone.utc).isoformat(),
            "raw_payload_used": bool(raw),
            "raw_payload_size": raw_size,
        }, ensure_ascii=False),
        "created_at": row["created_at"],
    }


def ensure_schema(con: sqlite3.Connection) -> None:
    con.execute("""
    CREATE TABLE IF NOT EXISTS lobster_betting_affect_signals (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      persona TEXT NOT NULL,
      source_pre_bet_research_id INTEGER NOT NULL,
      site TEXT,
      event_key TEXT,
      event_title TEXT,
      pick TEXT,
      stake INTEGER,
      confidence TEXT,
      win_rate REAL,
      recent_win_rate REAL,
      winrate_meaning_score REAL NOT NULL,
      money_pain_index REAL NOT NULL,
      bankroll_pressure REAL NOT NULL,
      loss_aversion_index REAL NOT NULL,
      risk_respect_index REAL NOT NULL,
      must_explain INTEGER NOT NULL DEFAULT 1,
      explanation TEXT NOT NULL,
      raw_json TEXT NOT NULL DEFAULT '{}',
      created_at TEXT NOT NULL DEFAULT (datetime('now')),
      UNIQUE(persona, source_pre_bet_research_id)
    )
    """)
    con.execute("CREATE INDEX IF NOT EXISTS idx_lobster_bet_affect_persona_time ON lobster_betting_affect_signals(persona, created_at)")
    con.execute("CREATE INDEX IF NOT EXISTS idx_lobster_bet_affect_time ON lobster_betting_affect_signals(created_at)")
    con.execute("CREATE INDEX IF NOT EXISTS idx_lobster_bet_affect_source ON lobster_betting_affect_signals(source_pre_bet_research_id)")


LIGHT_PREBET_COLUMNS = """
  id, persona, site, event_key, event_title, pick, stake, confidence,
  checklist_json, event_review_json, analysis_review_json, report_review_json, search_review_json,
  reasoning, integrated_reasoning, raw_decision_reasoning, created_at,
  LENGTH(COALESCE(raw_json,'')) AS raw_json_size
"""


def open_db(db_path: str) -> sqlite3.Connection:
    con = sqlite3.connect(db_path, timeout=120)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA busy_timeout=120000")
    con.execute("PRAGMA synchronous=NORMAL")
    return con


def sync(db_path: str, hours: int, limit: int, refresh_existing: bool = False) -> dict:
    con = open_db(db_path)
    ensure_schema(con)
    if refresh_existing:
        rows = con.execute("""
          SELECT
        """ + LIGHT_PREBET_COLUMNS + """
          FROM lobster_pre_bet_research
          WHERE created_at >= datetime('now', ?)
          ORDER BY id DESC
          LIMIT ?
        """, (f"-{int(hours)} hours", int(limit))).fetchall()
    else:
        max_source_id = con.execute(
            "SELECT COALESCE(MAX(source_pre_bet_research_id), 0) FROM lobster_betting_affect_signals"
        ).fetchone()[0]
        rows = con.execute("""
          SELECT
        """ + LIGHT_PREBET_COLUMNS + """
          FROM lobster_pre_bet_research
          WHERE id > ?
          ORDER BY id
          LIMIT ?
        """, (int(max_source_id), int(limit))).fetchall()
    inserted = 0
    for row in rows:
        d = derive(row)
        for attempt in range(6):
            try:
                con.execute("""
                  INSERT INTO lobster_betting_affect_signals(
                    persona, source_pre_bet_research_id, site, event_key, event_title, pick, stake, confidence,
                    win_rate, recent_win_rate, winrate_meaning_score, money_pain_index, bankroll_pressure,
                    loss_aversion_index, risk_respect_index, must_explain, explanation, raw_json, created_at
                  ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                  ON CONFLICT(persona, source_pre_bet_research_id) DO UPDATE SET
                    site=excluded.site, event_key=excluded.event_key, event_title=excluded.event_title,
                    pick=excluded.pick, stake=excluded.stake, confidence=excluded.confidence,
                    win_rate=excluded.win_rate, recent_win_rate=excluded.recent_win_rate,
                    winrate_meaning_score=excluded.winrate_meaning_score, money_pain_index=excluded.money_pain_index,
                    bankroll_pressure=excluded.bankroll_pressure, loss_aversion_index=excluded.loss_aversion_index,
                    risk_respect_index=excluded.risk_respect_index, must_explain=excluded.must_explain,
                    explanation=excluded.explanation, raw_json=excluded.raw_json, created_at=excluded.created_at
                """, (
                    d["persona"], d["source_pre_bet_research_id"], d["site"], d["event_key"], d["event_title"],
                    d["pick"], d["stake"], d["confidence"], d["win_rate"], d["recent_win_rate"],
                    d["winrate_meaning_score"], d["money_pain_index"], d["bankroll_pressure"],
                    d["loss_aversion_index"], d["risk_respect_index"], d["must_explain"], d["explanation"],
                    d["raw_json"], d["created_at"],
                ))
                break
            except sqlite3.OperationalError as exc:
                if "locked" not in str(exc).lower() or attempt == 5:
                    raise
                time.sleep(0.4 * (attempt + 1))
        inserted += 1
    con.commit()
    total = con.execute("SELECT COUNT(*) FROM lobster_betting_affect_signals").fetchone()[0]
    recent = con.execute("SELECT COUNT(*) FROM lobster_betting_affect_signals WHERE created_at >= datetime('now','-12 hours')").fetchone()[0]
    con.close()
    return {
        "ok": True,
        "mode": "refresh" if refresh_existing else "incremental",
        "scanned": len(rows),
        "upserted": inserted,
        "total": total,
        "recent_12h": recent,
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("command", nargs="?", default="sync", choices=["sync"])
    ap.add_argument("--db", default=DB_PATH)
    ap.add_argument("--hours", type=int, default=24)
    ap.add_argument("--limit", type=int, default=20000)
    ap.add_argument("--refresh-existing", action="store_true")
    args = ap.parse_args()
    print(json.dumps(sync(args.db, args.hours, args.limit, args.refresh_existing), ensure_ascii=False))


if __name__ == "__main__":
    main()
