#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sqlite3
import time
from lobster_social_force_pipeline import run_backfill
from datetime import datetime, timezone
from pathlib import Path

DB_PATH = "/opt/battle_arena/app/battle_arena.db"

CORE_LOBSTERS = [
    "norika_oda", "clawtrix", "pincerbot", "ragclaw", "snaplex",
    "hexclaw", "vortexiq", "prismbit", "blazepaw", "stonefang",
]
APPRENTICE_LOBSTERS = [
    "emberling", "sparkrill", "gauntlet", "rushfin", "nullpinch",
    "brineledger", "omenwake", "mirrorcalm", "flarecub", "basaltrook",
]
KNOWN_LOBSTER_ORDER = CORE_LOBSTERS + APPRENTICE_LOBSTERS
CANONICAL_PERSONA_ALIASES = {"norika": "norika_oda"}
EXCLUDED_PERSONAS = {"test_lobster_unit"}


def canonical_persona(v):
    name = clean(v)
    return CANONICAL_PERSONA_ALIASES.get(name.lower(), name)


def is_excluded_persona(v):
    return clean(v).lower() in EXCLUDED_PERSONAS


def persona_sort_key(name):
    try:
        return (0, KNOWN_LOBSTER_ORDER.index(name))
    except ValueError:
        return (1, name)


def collect_lobster_personas(c):
    # Dashboard social/replay cohorts are intentionally limited to the 10 mentors
    # plus 10 apprentices. Conversation targets can include humans, test agents,
    # or external names; pulling those into cache fan-out makes the dashboard slow
    # and pollutes old/new lobster comparison.
    return list(KNOWN_LOBSTER_ORDER)


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def jload(v, default=None):
    if default is None:
        default = {}
    try:
        return json.loads(v) if v else default
    except Exception:
        return default


def clean(v):
    return (v or "").strip()


def clamp_limit(v, lo=20, hi=500):
    try:
        return max(lo, min(int(v or lo), hi))
    except Exception:
        return lo


def cache_key(prefix, **kwargs):
    parts = [prefix]
    for k in sorted(kwargs):
        v = kwargs[k]
        if k == "min_confidence":
            try:
                if float(v or 0) <= 0:
                    continue
                v = f"{float(v):.3f}".rstrip("0").rstrip(".")
            except Exception:
                continue
        if v not in ("", None):
            parts.append(f"{k}={v}")
    return "|".join(parts)


def ensure_tables(c):
    c.execute("""
    CREATE TABLE IF NOT EXISTS lobster_social_intelligence_dashboard_cache (
      cache_key TEXT PRIMARY KEY,
      persona TEXT NOT NULL DEFAULT '',
      limit_evidence INTEGER NOT NULL DEFAULT 4,
      source_max_id INTEGER NOT NULL DEFAULT 0,
      payload_json TEXT NOT NULL,
      generated_ms REAL NOT NULL DEFAULT 0,
      updated_at TEXT NOT NULL
    )
    """)
    c.execute("""
    CREATE TABLE IF NOT EXISTS lobster_conversation_interactions_dashboard_cache (
      cache_key TEXT PRIMARY KEY,
      channel TEXT NOT NULL DEFAULT '',
      speaker TEXT NOT NULL DEFAULT '',
      target TEXT NOT NULL DEFAULT '',
      kind TEXT NOT NULL DEFAULT '',
      min_confidence REAL NOT NULL DEFAULT 0,
      limit_rows INTEGER NOT NULL DEFAULT 140,
      source_max_id INTEGER NOT NULL DEFAULT 0,
      payload_json TEXT NOT NULL,
      generated_ms REAL NOT NULL DEFAULT 0,
      updated_at TEXT NOT NULL
    )
    """)
    c.execute("CREATE INDEX IF NOT EXISTS idx_lsi_cache_persona ON lobster_social_intelligence_dashboard_cache(persona)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_lcid_cache_filters ON lobster_conversation_interactions_dashboard_cache(channel,speaker,target,kind,min_confidence,limit_rows)")


def source_max_id(c, table):
    try:
        return int(c.execute(f"SELECT COALESCE(MAX(id),0) FROM {table}").fetchone()[0] or 0)
    except Exception:
        return 0


def build_conversation_payload(c, channel="", speaker="", target="", kind="", min_confidence=0.0, limit=140):
    started = time.time()
    params = []
    where = ["1=1"]
    if clean(channel):
        where.append("channel=?")
        params.append(clean(channel))
    if clean(speaker):
        where.append("speaker=?")
        params.append(clean(speaker))
    if clean(target):
        where.append("target=?")
        params.append(clean(target))
    if clean(kind):
        where.append("interaction_kind=?")
        params.append(clean(kind))
    try:
        mc = float(min_confidence or 0)
    except Exception:
        mc = 0.0
    if mc > 0:
        where.append("confidence>=?")
        params.append(mc)
    where_sql = " AND ".join(where)
    lim = clamp_limit(limit)
    out = {
        "source": "sqlite-cache:lobster_conversation_interactions_dashboard_cache",
        "filters": {"channel": channel, "speaker": speaker, "target": target, "kind": kind, "min_confidence": mc, "limit": lim},
        "run": {}, "totals": {}, "kind_counts": [], "channel_counts": [],
        "pair_rows": [], "recent": [], "edges": [], "personas": [], "channels": [],
    }
    tables = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if "lobster_conversation_interactions" not in tables:
        out["error"] = "lobster_conversation_interactions table missing"
        return out
    if "lobster_conversation_interaction_runs" in tables:
        r = c.execute("""SELECT * FROM lobster_conversation_interaction_runs
                         ORDER BY finished_at DESC, started_at DESC LIMIT 1""").fetchone()
        if r:
            d = dict(r)
            d["summary"] = jload(d.pop("summary_json", "{}"), {})
            out["run"] = d
    total = c.execute(f"SELECT COUNT(*) FROM lobster_conversation_interactions WHERE {where_sql}", params).fetchone()[0]
    high = c.execute(f"SELECT COUNT(*) FROM lobster_conversation_interactions WHERE {where_sql} AND confidence>=0.7", params).fetchone()[0]
    direct = c.execute(f"""SELECT COUNT(*) FROM lobster_conversation_interactions
                         WHERE {where_sql} AND interaction_kind IN
                         ('explicit_reply_to','direct_mention','instruction_reference','quoted_previous_message')""", params).fetchone()[0]
    unresolved = c.execute(f"SELECT COUNT(*) FROM lobster_conversation_interactions WHERE {where_sql} AND interaction_kind='broadcast_or_unresolved'", params).fetchone()[0]
    out["totals"] = {"interactions": int(total or 0), "high_confidence": int(high or 0), "direct_or_inferred": int(direct or 0), "broadcast_or_unresolved": int(unresolved or 0)}
    for r in c.execute(f"""SELECT interaction_kind, COUNT(*) n, AVG(confidence) avg_conf
                          FROM lobster_conversation_interactions
                          WHERE {where_sql}
                          GROUP BY interaction_kind ORDER BY n DESC""", params):
        out["kind_counts"].append({"kind": r["interaction_kind"], "count": int(r["n"] or 0), "avg_confidence": round(float(r["avg_conf"] or 0), 3)})
    for r in c.execute(f"""SELECT COALESCE(channel,'') channel, COUNT(*) n,
                                 SUM(CASE WHEN confidence>=0.7 THEN 1 ELSE 0 END) high_conf,
                                 AVG(confidence) avg_conf
                          FROM lobster_conversation_interactions
                          WHERE {where_sql}
                          GROUP BY COALESCE(channel,'') ORDER BY n DESC""", params):
        out["channel_counts"].append({"channel": r["channel"] or "(none)", "count": int(r["n"] or 0), "high_confidence": int(r["high_conf"] or 0), "avg_confidence": round(float(r["avg_conf"] or 0), 3)})
    edge_where = where_sql + " AND target IS NOT NULL AND target!=''"
    for r in c.execute(f"""SELECT speaker,target,COALESCE(channel,'') channel, COUNT(*) n,
                                 SUM(CASE WHEN confidence>=0.7 THEN 1 ELSE 0 END) high_conf,
                                 AVG(confidence) avg_conf,
                                 MAX(source_created_at) last_at
                          FROM lobster_conversation_interactions
                          WHERE {edge_where}
                          GROUP BY speaker,target,COALESCE(channel,'')
                          ORDER BY high_conf DESC, n DESC LIMIT 80""", params):
        out["edges"].append({"speaker": r["speaker"], "target": r["target"], "channel": r["channel"] or "(none)", "count": int(r["n"] or 0), "high_confidence": int(r["high_conf"] or 0), "avg_confidence": round(float(r["avg_conf"] or 0), 3), "last_at": r["last_at"]})
    if "lobster_conversation_pair_summary" in tables:
        pair_where = ["inference_version='historic-v1'"]
        pair_params = []
        if clean(channel):
            pair_where.append("channel=?")
            pair_params.append(clean(channel))
        if clean(speaker):
            pair_where.append("speaker=?")
            pair_params.append(clean(speaker))
        if clean(target):
            pair_where.append("target=?")
            pair_params.append(clean(target))
        pair_sql = " AND ".join(pair_where)
        for r in c.execute(f"""SELECT speaker,target,channel,interaction_count,high_confidence_count,
                                     first_created_at,last_created_at,sample_json
                              FROM lobster_conversation_pair_summary
                              WHERE {pair_sql}
                              ORDER BY high_confidence_count DESC, interaction_count DESC LIMIT 80""", pair_params):
            d = dict(r)
            d["samples"] = jload(d.pop("sample_json", "[]"), [])
            out["pair_rows"].append(d)
    for r in c.execute(f"""SELECT source_message_id,source_created_at,source_time,channel,speaker,target,
                                 target_message_id,target_created_at,interaction_kind,confidence,evidence,
                                 message_text,target_text,context_json
                          FROM lobster_conversation_interactions
                          WHERE {where_sql}
                          ORDER BY source_message_id DESC LIMIT ?""", params + [lim]):
        d = dict(r)
        d["context"] = jload(d.pop("context_json", "{}"), {})
        out["recent"].append(d)
    out["personas"] = collect_lobster_personas(c)
    out["channels"] = [r[0] or "" for r in c.execute("""SELECT DISTINCT channel FROM lobster_conversation_interactions
                                                       ORDER BY channel""")]
    out["generated_ms"] = round((time.time() - started) * 1000, 2)
    out["cache"] = {"updated_at": now_iso(), "source_max_id": source_max_id(c, "lobster_conversation_interactions")}
    return out


def upsert_conversation(c, **filters):
    started = time.time()
    payload = build_conversation_payload(c, **filters)
    key = cache_key("conversation", channel=filters.get("channel",""), speaker=filters.get("speaker",""),
                    target=filters.get("target",""), kind=filters.get("kind",""),
                    min_confidence=filters.get("min_confidence",0), limit=filters.get("limit",140))
    updated = now_iso()
    payload["cache"] = {"cache_key": key, "updated_at": updated, "source_max_id": source_max_id(c, "lobster_conversation_interactions"), "hit": True}
    c.execute("""
      INSERT INTO lobster_conversation_interactions_dashboard_cache
      (cache_key,channel,speaker,target,kind,min_confidence,limit_rows,source_max_id,payload_json,generated_ms,updated_at)
      VALUES (?,?,?,?,?,?,?,?,?,?,?)
      ON CONFLICT(cache_key) DO UPDATE SET
        channel=excluded.channel,speaker=excluded.speaker,target=excluded.target,kind=excluded.kind,
        min_confidence=excluded.min_confidence,limit_rows=excluded.limit_rows,source_max_id=excluded.source_max_id,
        payload_json=excluded.payload_json,generated_ms=excluded.generated_ms,updated_at=excluded.updated_at
    """, (key, filters.get("channel",""), filters.get("speaker",""), filters.get("target",""), filters.get("kind",""),
          float(filters.get("min_confidence",0) or 0), int(filters.get("limit",140) or 140),
          source_max_id(c, "lobster_conversation_interactions"), json.dumps(payload, ensure_ascii=False),
          round((time.time()-started)*1000, 2), updated))
    return key


def refresh_social(c):
    from lobster_social_intelligence_engine import compute_social_intelligence
    personas = collect_lobster_personas(c)
    keys = []
    for limit in (3, 4):
        for persona in [""] + personas:
            started = time.time()
            payload = compute_social_intelligence(DB_PATH, persona=persona, limit_evidence=limit)
            key = cache_key("social", persona=persona, limit_evidence=limit)
            updated = now_iso()
            payload["cache"] = {"cache_key": key, "updated_at": updated, "source_max_id": source_max_id(c, "lobster_conversation_interactions"), "hit": True}
            c.execute("""
              INSERT INTO lobster_social_intelligence_dashboard_cache
              (cache_key,persona,limit_evidence,source_max_id,payload_json,generated_ms,updated_at)
              VALUES (?,?,?,?,?,?,?)
              ON CONFLICT(cache_key) DO UPDATE SET
                persona=excluded.persona,limit_evidence=excluded.limit_evidence,source_max_id=excluded.source_max_id,
                payload_json=excluded.payload_json,generated_ms=excluded.generated_ms,updated_at=excluded.updated_at
            """, (key, persona, limit, source_max_id(c, "lobster_conversation_interactions"),
                  json.dumps(payload, ensure_ascii=False), round((time.time()-started)*1000, 2), updated))
            keys.append(key)
    return keys


def refresh_conversation(c):
    keys = []
    personas = collect_lobster_personas(c)
    channels = [r[0] or "" for r in c.execute("""SELECT DISTINCT channel FROM lobster_conversation_interactions
                                                 WHERE channel IS NOT NULL AND channel!='' ORDER BY channel""")]
    kinds = [r[0] for r in c.execute("""SELECT DISTINCT interaction_kind FROM lobster_conversation_interactions
                                        WHERE interaction_kind IS NOT NULL AND interaction_kind!='' ORDER BY interaction_kind""")]
    keys.append(upsert_conversation(c, limit=140))
    for ch in channels:
        keys.append(upsert_conversation(c, channel=ch, limit=140))
    for sp in personas:
        keys.append(upsert_conversation(c, speaker=sp, limit=140))
    for tg in personas:
        keys.append(upsert_conversation(c, target=tg, limit=140))
    for kd in kinds:
        keys.append(upsert_conversation(c, kind=kd, limit=140))
    for ch in channels:
        for sp in personas:
            keys.append(upsert_conversation(c, channel=ch, speaker=sp, limit=140))
    active_pairs = []
    if "lobster_conversation_interactions" in {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}:
        for r in c.execute("""SELECT DISTINCT speaker, target
                              FROM lobster_conversation_interactions
                              WHERE speaker IS NOT NULL AND speaker!=''
                                AND target IS NOT NULL AND target!=''
                              ORDER BY speaker, target"""):
            sp = canonical_persona(r[0])
            tg = canonical_persona(r[1])
            if sp and tg and sp != tg and sp in KNOWN_LOBSTER_ORDER and tg in KNOWN_LOBSTER_ORDER and not is_excluded_persona(sp) and not is_excluded_persona(tg):
                active_pairs.append((sp, tg))
    for sp, tg in sorted(set(active_pairs), key=lambda x: (persona_sort_key(x[0]), persona_sort_key(x[1]))):
        keys.append(upsert_conversation(c, speaker=sp, target=tg, limit=140))
    return keys


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", choices=["all", "social", "conversation"], default="all")
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()
    started = time.time()
    # Run forced interaction parsing before opening the cache connection.
    # Keeping both operations in one sqlite connection caused long write locks.
    force_summary = run_backfill(DB_PATH, hours=2, uptake_target=0.10, limit=3000)
    with sqlite3.connect(DB_PATH, timeout=30.0) as c:
        c.row_factory = sqlite3.Row
        ensure_tables(c)
        made = []
        skipped = []
        current_interaction_max = source_max_id(c, "lobster_conversation_interactions")
        if args.only in ("all", "social"):
            cached = c.execute("SELECT COUNT(*), COALESCE(MAX(source_max_id),0) FROM lobster_social_intelligence_dashboard_cache").fetchone()
            if (not args.force) and cached and int(cached[0] or 0) > 0 and int(cached[1] or 0) >= current_interaction_max:
                skipped.append("social")
            else:
                made.extend(refresh_social(c))
                c.commit()
        if args.only in ("all", "conversation"):
            cached = c.execute("SELECT COUNT(*), COALESCE(MAX(source_max_id),0) FROM lobster_conversation_interactions_dashboard_cache").fetchone()
            if (not args.force) and cached and int(cached[0] or 0) > 0 and int(cached[1] or 0) >= current_interaction_max:
                skipped.append("conversation")
            else:
                made.extend(refresh_conversation(c))
                c.commit()
    print(json.dumps({"ok": True, "cache_rows": len(made), "skipped": skipped, "source_max_id": current_interaction_max, "force_pipeline": force_summary, "elapsed_ms": round((time.time()-started)*1000,2)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
