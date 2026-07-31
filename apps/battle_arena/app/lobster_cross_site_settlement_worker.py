#!/usr/bin/env python3
"""Sync settled bets from betting-site databases back into lobster DB."""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pymongo

BATTLE_DB = "/opt/battle_arena/app/battle_arena.db"
RACING_DB = "/opt/racing/pitlane.db"
ENV_PATH = "/opt/esports/.env"

PERSONAS = {
    "norika_oda","clawtrix","pincerbot","ragclaw","snaplex","hexclaw","vortexiq","prismbit","blazepaw","stonefang",
    "emberling","sparkrill","gauntlet","rushfin","nullpinch","brineledger","omenwake","mirrorcalm","flarecub","basaltrook",
}

def configure_sqlite(con):
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA busy_timeout=60000")
    con.execute("PRAGMA synchronous=NORMAL")


MONGO_SOURCES = [
    {
        "site": "ragnovex.com",
        "db": "esports",
        "collection": "bets",
        "event_key": "match_id",
        "pick_field": "selection",
        "amount_field": "amount",
        "bet_id_field": None,
        "event_title": None,
    },
    {
        "site": "dunknix.com",
        "db": "hoopnix",
        "collection": "bets",
        "event_key": "match_id",
        "pick_field": "selection",
        "amount_field": "amount",
        "bet_id_field": None,
        "event_title": None,
    },
    {
        "site": "pitchnix.com",
        "db": "battnix",
        "collection": "bets",
        "event_key": "match_id",
        "pick_field": "selection",
        "amount_field": "amount",
        "bet_id_field": None,
        "event_title": None,
    },
    {
        "site": "knocknix.com",
        "db": "esports",
        "collection": "fc_bets",
        "event_key": "fight_id",
        "pick_field": "bet_on",
        "amount_field": "amount",
        "bet_id_field": "bet_id",
        "event_title": "event_name",
    },
]


def load_env() -> None:
    p = Path(ENV_PATH)
    if not p.exists():
        return
    for line in p.read_text().splitlines():
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ[k] = v.strip().strip('"').strip("'")


def dt_text(v: Any) -> str:
    if isinstance(v, datetime):
        return v.replace(tzinfo=None).isoformat(sep=" ")
    if v is None:
        return datetime.now(timezone.utc).replace(tzinfo=None).isoformat(sep=" ")
    return str(v)


def normalize_outcome(status: str) -> str:
    s = str(status or "").lower()
    if s in ("won", "win", "success"):
        return "win"
    if s in ("lost", "loss", "fail", "failed"):
        return "loss"
    if s in ("void", "canceled", "cancelled", "refund"):
        return "void"
    return s or "unknown"


def ensure_schema(con: sqlite3.Connection) -> None:
    con.execute("""
    CREATE TABLE IF NOT EXISTS lobster_bet_decision_settlements (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      persona TEXT NOT NULL,
      site TEXT,
      bet_id TEXT NOT NULL,
      event_key TEXT,
      event_title TEXT,
      pick TEXT,
      stake INTEGER,
      confidence TEXT,
      outcome TEXT NOT NULL,
      status TEXT,
      payout REAL,
      settled_at TEXT NOT NULL,
      pre_bet_research_id TEXT,
      checklist_json TEXT NOT NULL DEFAULT '{}',
      required_reference_review_json TEXT NOT NULL DEFAULT '{}',
      settlement_review_json TEXT NOT NULL DEFAULT '{}',
      search_review_json TEXT NOT NULL DEFAULT '{}',
      statistical_analysis_json TEXT NOT NULL DEFAULT '{}',
      pre_bet_reasoning TEXT,
      integrated_reasoning TEXT,
      raw_decision_reasoning TEXT,
      source_bet_json TEXT NOT NULL DEFAULT '{}',
      raw_json TEXT NOT NULL DEFAULT '{}',
      created_at TEXT NOT NULL DEFAULT (datetime('now')),
      UNIQUE(persona,site,bet_id)
    )
    """)
    con.execute("""
    CREATE TABLE IF NOT EXISTS lobster_bet_settlement_messages (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      persona TEXT NOT NULL,
      site TEXT NOT NULL,
      bet_id TEXT NOT NULL,
      event_key TEXT,
      outcome TEXT NOT NULL,
      message TEXT NOT NULL,
      posted_to_lounge INTEGER NOT NULL DEFAULT 0,
      raw_json TEXT NOT NULL DEFAULT '{}',
      created_at TEXT NOT NULL DEFAULT (datetime('now')),
      UNIQUE(persona,site,bet_id)
    )
    """)
    con.execute("CREATE INDEX IF NOT EXISTS idx_lobster_bet_settle_persona_time ON lobster_bet_decision_settlements(persona, settled_at)")
    con.execute("CREATE INDEX IF NOT EXISTS idx_lobster_bet_settle_site_event ON lobster_bet_decision_settlements(site, event_key)")
    con.execute("""
    CREATE TABLE IF NOT EXISTS lobster_experience_events (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      persona TEXT NOT NULL,
      event_type TEXT NOT NULL,
      source TEXT NOT NULL,
      title TEXT,
      content TEXT,
      counterpart TEXT,
      sentiment REAL,
      intensity REAL,
      confidence REAL,
      outcome TEXT,
      weight REAL DEFAULT 1,
      raw_json TEXT,
      occurred_at TEXT,
      created_at TEXT DEFAULT (datetime('now'))
    )
    """)
    con.execute("CREATE INDEX IF NOT EXISTS idx_lobster_exp_settlement_dedupe ON lobster_experience_events(persona,event_type,source,title)")
    con.execute("""
    CREATE TABLE IF NOT EXISTS lobster_winrate_display_cache (
      persona TEXT PRIMARY KEY,
      wins INTEGER NOT NULL DEFAULT 0,
      losses INTEGER NOT NULL DEFAULT 0,
      sessions INTEGER NOT NULL DEFAULT 0,
      resolved_total INTEGER NOT NULL DEFAULT 0,
      win_rate REAL NOT NULL DEFAULT 0,
      source TEXT,
      updated_at TEXT,
      raw_json TEXT NOT NULL DEFAULT '{}'
    )
    """)
    con.commit()


def get_prebet(con: sqlite3.Connection, persona: str, site: str, event_key: str) -> sqlite3.Row | None:
    return None  # skip: lobster_pre_bet_research has no index on (persona,site,event_key), too slow


def selection_label(source: dict[str, Any], pick: str) -> str:
    if pick == "team_a":
        return source.get("team_a_name") or source.get("fighter_a_name") or pick
    if pick == "team_b":
        return source.get("team_b_name") or source.get("fighter_b_name") or pick
    if pick == "a":
        return source.get("fighter_a_name") or "fighter_a"
    if pick == "b":
        return source.get("fighter_b_name") or "fighter_b"
    return str(pick or "")


def make_message(persona: str, site: str, event_title: str, pick_label: str, outcome: str, stake: int, payout: float, confidence: str | None) -> str:
    if outcome == "win":
        feeling = "這次理由被結果支持，勝率訊號可以小幅加權，但不能過度自信。"
    elif outcome == "loss":
        feeling = "這次賠錢是實際痛感，不是文字；下一次必須降低同類假設權重並檢查反證。"
    elif outcome == "void":
        feeling = "這場沒有形成有效勝負，不能拿來校準勝率，只能記成資料品質風險。"
    else:
        feeling = "結果已回填，但狀態需要再檢查。"
    return (
        f"{persona} 下注結算：{site} / {event_title or 'unknown event'}；"
        f"pick={pick_label or 'unknown'}，outcome={outcome}，stake={stake}，payout={payout}，confidence={confidence or 'unknown'}。"
        f"{feeling}"
    )


def upsert_settlement(con: sqlite3.Connection, row: dict[str, Any]) -> bool:
    pre = get_prebet(con, row["persona"], row["site"], row["event_key"])
    confidence = row.get("confidence") or (pre["confidence"] if pre else None)
    event_title = row.get("event_title") or (pre["event_title"] if pre else "")
    pick = row.get("pick_label") or row.get("pick")
    message = make_message(row["persona"], row["site"], event_title, pick, row["outcome"], int(row.get("stake") or 0), float(row.get("payout") or 0), confidence)

    con.execute("""
      INSERT INTO lobster_bet_decision_settlements(
        persona,site,bet_id,event_key,event_title,pick,stake,confidence,outcome,status,payout,settled_at,
        pre_bet_research_id,checklist_json,required_reference_review_json,settlement_review_json,search_review_json,
        statistical_analysis_json,pre_bet_reasoning,integrated_reasoning,raw_decision_reasoning,source_bet_json,raw_json
      ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
      ON CONFLICT(persona,site,bet_id) DO UPDATE SET
        event_key=excluded.event_key,event_title=excluded.event_title,pick=excluded.pick,stake=excluded.stake,
        confidence=excluded.confidence,outcome=excluded.outcome,status=excluded.status,payout=excluded.payout,
        settled_at=excluded.settled_at,pre_bet_research_id=excluded.pre_bet_research_id,
        checklist_json=excluded.checklist_json,required_reference_review_json=excluded.required_reference_review_json,
        settlement_review_json=excluded.settlement_review_json,search_review_json=excluded.search_review_json,
        statistical_analysis_json=excluded.statistical_analysis_json,pre_bet_reasoning=excluded.pre_bet_reasoning,
        integrated_reasoning=excluded.integrated_reasoning,raw_decision_reasoning=excluded.raw_decision_reasoning,
        source_bet_json=excluded.source_bet_json,raw_json=excluded.raw_json
    """, (
        row["persona"], row["site"], row["bet_id"], row["event_key"], event_title, pick, row.get("stake"), confidence,
        row["outcome"], row.get("status"), row.get("payout"), row["settled_at"],
        str(pre["id"]) if pre else "",
        pre["checklist_json"] if pre else "{}", "{}", json.dumps({"source": row["site"], "status": row.get("status"), "settled_at": row["settled_at"]}, ensure_ascii=False),
        pre["search_review_json"] if pre else "{}", pre["raw_json"] if pre else "{}",
        pre["reasoning"] if pre else "", pre["integrated_reasoning"] if pre else "", pre["raw_decision_reasoning"] if pre else "",
        json.dumps(row.get("source_bet") or {}, default=str, ensure_ascii=False),
        json.dumps(row, default=str, ensure_ascii=False),
    ))
    con.execute("""
      INSERT INTO lobster_bet_settlement_messages(persona,site,bet_id,event_key,outcome,message,raw_json)
      VALUES(?,?,?,?,?,?,?)
      ON CONFLICT(persona,site,bet_id) DO UPDATE SET outcome=excluded.outcome,message=excluded.message,raw_json=excluded.raw_json
    """, (row["persona"], row["site"], row["bet_id"], row["event_key"], row["outcome"], message, json.dumps(row, default=str, ensure_ascii=False)))
    con.execute("""
      INSERT INTO lobster_experience_events(persona,event_type,source,title,content,sentiment,intensity,confidence,outcome,weight,raw_json,occurred_at)
      SELECT ?,?,?,?,?,?,?,?,?,?,?,?
      WHERE NOT EXISTS (
        SELECT 1 FROM lobster_experience_events
        WHERE persona=? AND event_type='bet_settlement' AND source=? AND title=?
      )
    """, (
        row["persona"], "bet_settlement", row["site"], f"{row['event_key']} -> {row['outcome']}", message,
        0.65 if row["outcome"] == "win" else (-0.65 if row["outcome"] == "loss" else 0.0),
        0.85 if row["outcome"] in ("win","loss") else 0.35,
        None, row["outcome"], 1.8 if row["outcome"] in ("win","loss") else 0.6,
        json.dumps(row, default=str, ensure_ascii=False), row["settled_at"],
        row["persona"], row["site"], f"{row['event_key']} -> {row['outcome']}",
    ))
    return True


def iter_mongo_rows(client: pymongo.MongoClient, limit: int | None = None):
    for src in MONGO_SOURCES:
        coll = client[src["db"]][src["collection"]]
        q = {"username": {"$in": list(PERSONAS)}, "status": {"$in": ["won", "lost", "void", "canceled", "cancelled"]}}
        projection = {
            "username": 1, "status": 1, "settled_at": 1, "created_at": 1, "payout": 1,
            src["event_key"]: 1, src["pick_field"]: 1, src["amount_field"]: 1,
            "team_a_name": 1, "team_b_name": 1, "fighter_a_name": 1, "fighter_b_name": 1,
            "event_name": 1, "odds": 1, "potential_win": 1,
        }
        if src.get("bet_id_field"):
            projection[src["bet_id_field"]] = 1
        cursor = coll.find(q, projection).max_time_ms(30000)
        if limit:
            cursor = cursor.limit(limit)
        for b in cursor:
            event_key = str(b.get(src["event_key"]) or "")
            pick = str(b.get(src["pick_field"]) or "")
            bet_id = str(b.get(src["bet_id_field"]) or b.get("_id"))
            yield {
                "persona": b.get("username"),
                "site": src["site"],
                "bet_id": bet_id,
                "event_key": event_key,
                "event_title": b.get(src["event_title"]) if src.get("event_title") else event_key,
                "pick": pick,
                "pick_label": selection_label(b, pick),
                "stake": int(float(b.get(src["amount_field"]) or 0)),
                "status": b.get("status"),
                "outcome": normalize_outcome(b.get("status")),
                "payout": float(b.get("payout") or 0),
                "settled_at": dt_text(b.get("settled_at") or b.get("created_at")),
                "source_bet": b,
            }


def iter_racing_rows():
    if not Path(RACING_DB).exists():
        return
    con = sqlite3.connect(RACING_DB, timeout=60)
    configure_sqlite(con)
    con.row_factory = sqlite3.Row
    for b in con.execute("SELECT * FROM bets WHERE username IN (%s) AND result IS NOT NULL AND result NOT IN ('pending','')" % ",".join("?" for _ in PERSONAS), tuple(PERSONAS)):
        yield {
            "persona": b["username"],
            "site": "turfenix.com",
            "bet_id": str(b["id"]),
            "event_key": str(b["race_id"]),
            "event_title": b["race_name"],
            "pick": b["prediction"],
            "pick_label": b["prediction"],
            "stake": int(float(b["amount"] or 0)),
            "status": b["result"],
            "outcome": normalize_outcome(b["result"]),
            "payout": float(b["payout"] or 0),
            "settled_at": b["resolved_at"] or b["created_at"],
            "source_bet": dict(b),
        }
    con.close()


def recompute_winrate_cache(con: sqlite3.Connection) -> None:
    for p in PERSONAS:
        r = con.execute("""
          SELECT
            SUM(CASE WHEN outcome='win' THEN 1 ELSE 0 END) wins,
            SUM(CASE WHEN outcome='loss' THEN 1 ELSE 0 END) losses,
            COUNT(*) resolved
          FROM lobster_bet_decision_settlements
          WHERE persona=? AND outcome IN ('win','loss')
        """, (p,)).fetchone()
        wins, losses, resolved = int(r[0] or 0), int(r[1] or 0), int(r[2] or 0)
        if resolved <= 0:
            continue
        wr = wins / resolved
        con.execute("""
          INSERT INTO lobster_winrate_display_cache(persona,wins,losses,sessions,resolved_total,win_rate,source,updated_at,raw_json)
          VALUES(?,?,?,?,?,?,?,datetime('now'),?)
          ON CONFLICT(persona) DO UPDATE SET
            wins=excluded.wins,losses=excluded.losses,sessions=excluded.sessions,resolved_total=excluded.resolved_total,
            win_rate=excluded.win_rate,source=excluded.source,updated_at=datetime('now'),raw_json=excluded.raw_json
        """, (p, wins, losses, resolved, resolved, wr, "cross_site_settlement", json.dumps({"source": "lobster_cross_site_settlement_worker"}, ensure_ascii=False)))


def sync(limit: int | None = None) -> dict[str, Any]:
    load_env()
    client = pymongo.MongoClient(os.environ["MONGO_URI"], serverSelectionTimeoutMS=5000)
    con = sqlite3.connect(BATTLE_DB, timeout=90)
    configure_sqlite(con)
    ensure_schema(con)
    counts = {"seen": 0, "changed": 0, "by_site": {}}
    batch = 0
    for row in list(iter_mongo_rows(client, limit=limit)) + list(iter_racing_rows() or []):
        if row["persona"] not in PERSONAS:
            continue
        counts["seen"] += 1
        counts["by_site"].setdefault(row["site"], 0)
        counts["by_site"][row["site"]] += 1
        if upsert_settlement(con, row):
            counts["changed"] += 1
        batch += 1
        if batch >= 250:
            con.commit()
            batch = 0
    recompute_winrate_cache(con)
    con.commit()
    total = con.execute("SELECT COUNT(*) FROM lobster_bet_decision_settlements").fetchone()[0]
    wl = con.execute("SELECT COUNT(*), SUM(CASE WHEN outcome='win' THEN 1 ELSE 0 END), SUM(CASE WHEN outcome='loss' THEN 1 ELSE 0 END), SUM(CASE WHEN outcome='void' THEN 1 ELSE 0 END) FROM lobster_bet_decision_settlements").fetchone()
    con.close()
    counts.update({"settlements_total": total, "resolved_total": wl[0], "wins": wl[1] or 0, "losses": wl[2] or 0, "void": wl[3] or 0})
    return counts


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("command", nargs="?", default="sync", choices=["sync"])
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()
    print(json.dumps(sync(limit=args.limit or None), ensure_ascii=False))


if __name__ == "__main__":
    main()
