#!/usr/bin/env python3
import json
import sqlite3
import sys
import time
from datetime import datetime, timezone

sys.path.insert(0, "/opt/battle_arena/app")
from lobster_social_intelligence_engine import compute_social_intelligence  # noqa: E402

DB = "/opt/battle_arena/app/battle_arena.db"


def configure_sqlite(conn):
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=60000")
    conn.execute("PRAGMA synchronous=NORMAL")


def source_max_id(conn):
    row = conn.execute("SELECT MAX(id) FROM lobster_conversation_interactions").fetchone()
    return int(row[0] or 0)


def execute_with_retry(conn, sql, params):
    for attempt in range(6):
        try:
            return conn.execute(sql, params)
        except sqlite3.OperationalError as exc:
            if 'locked' not in str(exc).lower() or attempt == 5:
                raise
            time.sleep(0.5 * (attempt + 1))


def upsert(conn, key, persona, limit_evidence, payload, generated_ms, max_id):
    execute_with_retry(conn,
        """
        INSERT INTO lobster_social_intelligence_dashboard_cache
          (cache_key, persona, limit_evidence, source_max_id, payload_json, generated_ms, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(cache_key) DO UPDATE SET
          persona=excluded.persona,
          limit_evidence=excluded.limit_evidence,
          source_max_id=excluded.source_max_id,
          payload_json=excluded.payload_json,
          generated_ms=excluded.generated_ms,
          updated_at=excluded.updated_at
        """,
        (
            key,
            persona,
            limit_evidence,
            max_id,
            json.dumps(payload, ensure_ascii=False),
            generated_ms,
            datetime.now(timezone.utc).isoformat(),
        ),
    )


def main():
    limit_evidence = int(sys.argv[1]) if len(sys.argv) > 1 else 3
    with sqlite3.connect(DB, timeout=60.0) as conn:
        configure_sqlite(conn)
        max_id = source_max_id(conn)

    t0 = time.time()
    all_payload = compute_social_intelligence(DB, limit_evidence=limit_evidence)
    all_ms = round((time.time() - t0) * 1000, 2)
    agents = [a["persona"] for a in all_payload.get("agents", []) if a.get("persona")]

    with sqlite3.connect(DB, timeout=60.0) as conn:
        configure_sqlite(conn)
        upsert(conn, f"social|limit_evidence={limit_evidence}", "", limit_evidence, all_payload, all_ms, max_id)
        conn.commit()
    print(f"updated all limit={limit_evidence} agents={len(agents)} ms={all_ms}")

    for persona in agents:
        t1 = time.time()
        payload = compute_social_intelligence(DB, persona=persona, limit_evidence=limit_evidence)
        ms = round((time.time() - t1) * 1000, 2)
        key = f"social|limit_evidence={limit_evidence}|persona={persona}"
        with sqlite3.connect(DB, timeout=12.0) as conn:
            upsert(conn, key, persona, limit_evidence, payload, ms, max_id)
            conn.commit()
        print(f"updated {persona} limit={limit_evidence} ms={ms}")


if __name__ == "__main__":
    main()
