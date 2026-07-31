#!/usr/bin/env python3
import json
import sqlite3
import time
import urllib.request

DB = "/opt/battle_arena/app/battle_arena.db"
MAIN_EXCLUDES = {"system", "deleted-lobsters", "lobster", "elder", "sunchirp"}
API = "http://127.0.0.1:8004/api/lobster-detail-sandbox-db/{}?refresh=1"
MAX_REFRESH_PER_RUN = 8


def load_personas(conn):
    names = set()
    for table in ["lobster_agent_memory_snapshots", "lobster_cognitive_extensions", "lobster_hot_brain_parameters", "lobster_local_brain"]:
        if not table_exists(conn, table):
            continue
        try:
            for row in conn.execute(f"SELECT DISTINCT persona FROM {table}"):
                name = str(row[0] or "").strip()
                if name and name not in MAIN_EXCLUDES:
                    names.add(name)
        except sqlite3.Error:
            pass
    return sorted(names)


def scalar(conn, sql, args=()):
    try:
        row = conn.execute(sql, args).fetchone()
        return row[0] if row else None
    except sqlite3.Error:
        return None


def table_exists(conn, name):
    return bool(conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone())


def source_signature(conn, persona):
    parts = {}
    for table, expr, where, col in [
        ("lobster_agent_memory_snapshots", "MAX(updated_at)", "persona=?", "persona"),
        ("lobster_analysis_reports", "MAX(updated_at)", "persona=?", "persona"),
        ("lobster_chat_log", "MAX(rowid)", "name=?", "name"),
        ("lounge_messages", "MAX(id)", "name=?", "name"),
        ("lobster_experience_events", "MAX(id)", "persona=?", "persona"),
        ("lobster_pre_bet_research", "MAX(id)", "persona=?", "persona"),
        ("lobster_cognitive_signal_timeseries", "MAX(id)", "persona=?", "persona"),
        ("lobster_distilled_signals", "MAX(rowid)", "persona=?", "persona"),
        ("lobster_decision_hooks", "MAX(rowid)", "persona=?", "persona"),
        ("lobster_message_uptake_signals", "MAX(id)", "persona=?", "persona"),
    ]:
        if table_exists(conn, table):
            parts[table] = scalar(conn, f"SELECT {expr} FROM {table} WHERE {where}", (persona,))
    return json.dumps(parts, sort_keys=True, ensure_ascii=False)


def ensure_state(conn):
    conn.execute("""CREATE TABLE IF NOT EXISTS lobster_detail_cache_state (
      key TEXT PRIMARY KEY,
      value TEXT NOT NULL,
      updated_at TEXT NOT NULL DEFAULT (datetime('now'))
    )""")


def refresh_one(persona):
    with urllib.request.urlopen(API.format(urllib.parse.quote(persona)), timeout=25) as res:
        res.read()


def main():
    started = time.time()
    with sqlite3.connect(DB, timeout=10) as conn:
        ensure_state(conn)
        planned = []
        refreshed = []
        skipped = []
        for persona in load_personas(conn):
            sig = source_signature(conn, persona)
            state_key = f"source_signature:{persona}"
            old = scalar(conn, "SELECT value FROM lobster_detail_cache_state WHERE key=?", (state_key,))
            if old == sig:
                skipped.append(persona)
                continue
            planned.append((persona, state_key, sig))

    total_planned = len(planned)
    planned = planned[:MAX_REFRESH_PER_RUN]
    errors = []
    committed = []
    for persona, state_key, sig in planned:
        try:
            refresh_one(persona)
            refreshed.append(persona)
            committed.append((state_key, sig))
        except Exception as exc:
            errors.append({"persona": persona, "error": str(exc)[:180]})

    if committed:
        with sqlite3.connect(DB, timeout=10) as conn:
            ensure_state(conn)
            for state_key, sig in committed:
                conn.execute("""INSERT INTO lobster_detail_cache_state(key,value,updated_at)
                                VALUES(?,?,datetime('now'))
                                ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at""", (state_key, sig))
            conn.commit()
    print(json.dumps({"ok": not errors, "refreshed": refreshed, "skipped": skipped, "planned_total": total_planned, "max_per_run": MAX_REFRESH_PER_RUN, "errors": errors, "ms": round((time.time()-started)*1000, 2)}, ensure_ascii=False))


if __name__ == "__main__":
    import urllib.parse
    main()
