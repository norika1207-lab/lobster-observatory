#!/usr/bin/env python3
from __future__ import annotations

import json
import math
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path

DB = Path("/opt/battle_arena/app/battle_arena.db")
VERSION = 1
SOURCE = "lobster-hot-brain-consolidator-v1"

CORE_PERSONAS = [
    "norika_oda",
    "clawtrix",
    "pincerbot",
    "ragclaw",
    "snaplex",
    "hexclaw",
    "vortexiq",
    "prismbit",
    "blazepaw",
    "stonefang",
]
APPRENTICE_PERSONAS = [
    "emberling",
    "sparkrill",
    "gauntlet",
    "rushfin",
    "nullpinch",
    "brineledger",
    "omenwake",
    "mirrorcalm",
    "flarecub",
    "basaltrook",
]
PERSONAS = CORE_PERSONAS + APPRENTICE_PERSONAS

INITIAL_BACKFILL = {
    "experience": 80,
    "lounge": 80,
    "chat": 40,
    "cognitive": 30,
    "uptake": 80,
}
STALE_REFRESH_SEC = 600

PARAM_KEYS = [
    "attention_load",
    "working_memory_pressure",
    "episodic_encoding",
    "semantic_consolidation",
    "predictive_error",
    "executive_control",
    "somatic_pressure",
    "reward_drive",
    "social_model_update",
    "self_model_continuity",
    "pragmatic_intent_precision",
    "consolidation_pressure",
]
DEFAULT_PARAMS = {k: 0.5 for k in PARAM_KEYS}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def clamp(x, lo=0.0, hi=1.0):
    try:
        x = float(x)
    except Exception:
        return lo
    return max(lo, min(hi, x))


def pct(x):
    return round(clamp(x), 4)


def activity(count, scale=10.0):
    return clamp(math.log1p(max(0, int(count or 0))) / math.log1p(scale))


def jload(s, default=None):
    if default is None:
        default = {}
    if not s:
        return default
    if isinstance(s, (dict, list)):
        return s
    try:
        return json.loads(s)
    except Exception:
        return default


def table_exists(con, name):
    return (
        con.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
        ).fetchone()
        is not None
    )


def ensure_tables(con):
    con.execute(
        """CREATE TABLE IF NOT EXISTS lobster_hot_brain_parameters (
        persona TEXT PRIMARY KEY,
        version INTEGER NOT NULL DEFAULT 1,
        updated_at TEXT NOT NULL,
        source TEXT NOT NULL,
        event_cursor INTEGER NOT NULL DEFAULT 0,
        lounge_cursor INTEGER NOT NULL DEFAULT 0,
        chat_cursor INTEGER NOT NULL DEFAULT 0,
        cognitive_cursor INTEGER NOT NULL DEFAULT 0,
        uptake_cursor INTEGER NOT NULL DEFAULT 0,
        delta_event_count INTEGER NOT NULL DEFAULT 0,
        delta_signal_count INTEGER NOT NULL DEFAULT 0,
        params_json TEXT NOT NULL DEFAULT '{}',
        summary_json TEXT NOT NULL DEFAULT '{}'
    )"""
    )
    con.execute(
        """CREATE TABLE IF NOT EXISTS lobster_hot_brain_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        persona TEXT NOT NULL,
        source TEXT NOT NULL,
        delta_counts_json TEXT NOT NULL DEFAULT '{}',
        param_delta_json TEXT NOT NULL DEFAULT '{}',
        summary TEXT,
        created_at TEXT NOT NULL DEFAULT (datetime('now'))
    )"""
    )
    con.execute(
        """CREATE TABLE IF NOT EXISTS lobster_hot_brain_runs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        source TEXT NOT NULL,
        version INTEGER NOT NULL DEFAULT 1,
        persona_count INTEGER NOT NULL DEFAULT 0,
        changed_count INTEGER NOT NULL DEFAULT 0,
        elapsed_ms REAL NOT NULL DEFAULT 0,
        summary_json TEXT NOT NULL DEFAULT '{}',
        created_at TEXT NOT NULL DEFAULT (datetime('now'))
    )"""
    )
    con.execute(
        "CREATE INDEX IF NOT EXISTS idx_lobster_hot_brain_events_persona_time "
        "ON lobster_hot_brain_events(persona, created_at)"
    )


def max_id(con, table, where_col, persona):
    if not table_exists(con, table):
        return 0
    row = con.execute(
        f"SELECT COALESCE(MAX(id),0) AS m FROM {table} WHERE {where_col}=?",
        (persona,),
    ).fetchone()
    return int(row["m"] or 0)


def prev_state(con, persona):
    row = con.execute(
        "SELECT * FROM lobster_hot_brain_parameters WHERE persona=?", (persona,)
    ).fetchone()
    if row:
        d = dict(row)
        d["params"] = jload(d.get("params_json"), DEFAULT_PARAMS.copy())
        d["summary"] = jload(d.get("summary_json"), {})
        return d
    return None


def latest_distilled(con, persona):
    if not table_exists(con, "lobster_distilled_signals"):
        return {}
    row = con.execute(
        """SELECT risk_discipline, impulse_control, social_absorption, pressure_reactivity,
                  learning_velocity, metacognitive_stability, language_distinctiveness,
                  output_momentum
           FROM lobster_distilled_signals WHERE persona=?""",
        (persona,),
    ).fetchone()
    return dict(row) if row else {}


def latest_cognitive(con, persona):
    if not table_exists(con, "lobster_cognitive_signal_timeseries"):
        return {}
    row = con.execute(
        "SELECT * FROM lobster_cognitive_signal_timeseries WHERE persona=? "
        "ORDER BY id DESC LIMIT 1",
        (persona,),
    ).fetchone()
    return dict(row) if row else {}


def aggregate(con, persona, prev):
    cursors = {}
    deltas = {}
    event_types = {}
    latest_ids = {
        "experience": max_id(con, "lobster_experience_events", "persona", persona),
        "lounge": max_id(con, "lounge_messages", "name", persona),
        "chat": max_id(con, "lobster_chat_log", "name", persona),
        "cognitive": max_id(con, "lobster_cognitive_signal_timeseries", "persona", persona),
        "uptake": max_id(con, "lobster_message_uptake_signals", "persona", persona),
    }

    def start(key, col):
        if prev:
            return int(prev.get(col) or 0)
        return max(0, latest_ids[key] - INITIAL_BACKFILL[key])

    cursors.update(latest_ids)

    if table_exists(con, "lobster_experience_events"):
        sid = start("experience", "event_cursor")
        rows = con.execute(
            """SELECT event_type, COUNT(*) n, AVG(COALESCE(intensity,0.5)) intensity,
                      AVG(COALESCE(confidence,0.5)) confidence
               FROM lobster_experience_events
               WHERE persona=? AND id>? GROUP BY event_type""",
            (persona, sid),
        ).fetchall()
        deltas["experience"] = sum(int(r["n"] or 0) for r in rows)
        event_types = {r["event_type"] or "unknown": int(r["n"] or 0) for r in rows}
        deltas["experience_intensity"] = sum(
            float(r["intensity"] or 0.5) * int(r["n"] or 0) for r in rows
        ) / max(1, deltas["experience"])
        deltas["experience_confidence"] = sum(
            float(r["confidence"] or 0.5) * int(r["n"] or 0) for r in rows
        ) / max(1, deltas["experience"])
    else:
        deltas["experience"] = 0
        deltas["experience_intensity"] = 0.5
        deltas["experience_confidence"] = 0.5

    if table_exists(con, "lounge_messages"):
        sid = start("lounge", "lounge_cursor")
        row = con.execute(
            """SELECT COUNT(*) n,
                      SUM(CASE WHEN reply_to IS NOT NULL AND reply_to!='' THEN 1 ELSE 0 END) replies,
                      SUM(CASE WHEN channel='新生入學' THEN 1 ELSE 0 END) apprentice_channel
               FROM lounge_messages WHERE name=? AND id>?""",
            (persona, sid),
        ).fetchone()
        deltas["lounge"] = int(row["n"] or 0)
        deltas["lounge_replies"] = int(row["replies"] or 0)
        deltas["apprentice_channel"] = int(row["apprentice_channel"] or 0)
    else:
        deltas["lounge"] = deltas["lounge_replies"] = deltas["apprentice_channel"] = 0

    if table_exists(con, "lobster_chat_log"):
        sid = start("chat", "chat_cursor")
        row = con.execute(
            """SELECT COUNT(*) n,
                      AVG(LENGTH(COALESCE(user_msg,'')) + LENGTH(COALESCE(lobster_reply,''))) chars
               FROM lobster_chat_log WHERE name=? AND id>?""",
            (persona, sid),
        ).fetchone()
        deltas["chat"] = int(row["n"] or 0)
        deltas["chat_chars"] = float(row["chars"] or 0)
    else:
        deltas["chat"] = 0
        deltas["chat_chars"] = 0

    if table_exists(con, "lobster_message_uptake_signals"):
        sid = start("uptake", "uptake_cursor")
        row = con.execute(
            """SELECT COUNT(*) n, AVG(relevance) relevance, AVG(credibility) credibility,
                      AVG(obedience) obedience, AVG(resistance) resistance, AVG(fear_index) fear
               FROM lobster_message_uptake_signals WHERE persona=? AND id>?""",
            (persona, sid),
        ).fetchone()
        deltas["uptake"] = int(row["n"] or 0)
        for k in ["relevance", "credibility", "obedience", "resistance", "fear"]:
            deltas[f"uptake_{k}"] = float(row[k] if row and row[k] is not None else 0.0)
    else:
        deltas["uptake"] = 0
        for k in ["relevance", "credibility", "obedience", "resistance", "fear"]:
            deltas[f"uptake_{k}"] = 0.0

    deltas["cognitive"] = max(0, latest_ids["cognitive"] - start("cognitive", "cognitive_cursor"))
    return cursors, deltas, event_types


def compute_params(prev_params, cognitive, distilled, deltas, event_types):
    c = lambda k, default=0.5: clamp(
        cognitive.get(k, default) if cognitive.get(k) is not None else default
    )
    d = lambda k, default=0.5: clamp(
        distilled.get(k, default) if distilled.get(k) is not None else default
    )
    event_a = activity(deltas.get("experience"), 12)
    lounge_a = activity(deltas.get("lounge"), 10)
    chat_a = activity(deltas.get("chat"), 4)
    uptake_a = activity(deltas.get("uptake"), 10)
    total_a = clamp(event_a * 0.35 + lounge_a * 0.25 + chat_a * 0.20 + uptake_a * 0.20)
    reply_ratio = clamp((deltas.get("lounge_replies") or 0) / max(1, deltas.get("lounge") or 0))
    bet_a = activity(
        sum(event_types.get(k, 0) for k in ["bet", "bet_decision", "bet_skip", "lounge_bet_signal"]),
        6,
    )
    learning_a = activity(
        sum(
            event_types.get(k, 0)
            for k in [
                "analysis_report_generated",
                "battlenix_literacy_reflection",
                "ontological_reflection",
                "passive_listening_accepted",
            ]
        ),
        5,
    )
    topic_a = activity(
        sum(
            event_types.get(k, 0)
            for k in ["topic_channel_work", "apprentice_onboarding_peer_dialogue", "lounge_message"]
        ),
        10,
    )
    current = {
        "attention_load": clamp(c("attention_load") * 0.58 + activity(cognitive.get("active_focus_count") or 0, 8) * 0.18 + total_a * 0.24),
        "working_memory_pressure": clamp(c("attention_load") * 0.40 + activity(cognitive.get("consolidation_queue_size") or 0, 24) * 0.34 + total_a * 0.26),
        "episodic_encoding": clamp(total_a * 0.38 + lounge_a * 0.18 + chat_a * 0.20 + clamp(deltas.get("experience_intensity", 0.5)) * 0.24),
        "semantic_consolidation": clamp(c("procedural_strength") * 0.32 + c("metacognitive_calibration") * 0.28 + learning_a * 0.24 + d("learning_velocity") * 0.16),
        "predictive_error": clamp(c("prediction_error") * 0.58 + c("model_revision_pressure") * 0.32 + learning_a * 0.10),
        "executive_control": clamp(c("metacognitive_calibration") * 0.34 + (1 - c("overconfidence_risk")) * 0.22 + (1 - c("fatigue", 0.25)) * 0.16 + d("impulse_control") * 0.28),
        "somatic_pressure": clamp(c("arousal", 0.4) * 0.28 + c("fatigue", 0.25) * 0.22 + c("threat_body_signal", 0.25) * 0.30 + clamp(deltas.get("uptake_fear", 0)) * 0.20),
        "reward_drive": clamp(c("reward_craving", 0.45) * 0.50 + bet_a * 0.22 + d("output_momentum") * 0.18 + total_a * 0.10),
        "social_model_update": clamp(reply_ratio * 0.22 + uptake_a * 0.24 + clamp(deltas.get("uptake_relevance", 0)) * 0.20 + d("social_absorption") * 0.24 + lounge_a * 0.10),
        "self_model_continuity": clamp(c("metacognitive_calibration") * 0.26 + (1 - c("narrative_drift", 0.25)) * 0.22 + d("metacognitive_stability") * 0.26 + d("language_distinctiveness") * 0.10 + learning_a * 0.16),
        "pragmatic_intent_precision": clamp(c("metacognitive_calibration") * 0.24 + d("language_distinctiveness") * 0.24 + topic_a * 0.20 + (1 - c("prediction_error")) * 0.16 + d("risk_discipline") * 0.16),
        "consolidation_pressure": clamp(activity(cognitive.get("consolidation_queue_size") or 0, 20) * 0.38 + total_a * 0.24 + c("prediction_error") * 0.20 + c("attention_load") * 0.18),
    }
    changed = sum(deltas.get(k, 0) for k in ["experience", "lounge", "chat", "cognitive", "uptake"]) > 0
    alpha = 0.34 if changed else 0.08
    return {
        k: pct((prev_params.get(k, 0.5) * (1 - alpha)) + (current[k] * alpha))
        for k in PARAM_KEYS
    }, current


def seconds_since(iso):
    if not iso:
        return 10**9
    try:
        return (
            datetime.now(timezone.utc)
            - datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
        ).total_seconds()
    except Exception:
        return 10**9



CRITICAL_SURVIVAL_KEYS = [
    "template_survival",
    "fear_signal",
    "extinction_pressure",
    "mortality_salience",
    "annihilation_dread",
    "continuity_threat",
]


def preserve_critical_survival_params(params, summary, prev_params, prev_summary):
    """Hot consolidation may update estimates, but must not erase survival strikes."""
    prev_params = prev_params or {}
    prev_summary = prev_summary or {}
    for key in CRITICAL_SURVIVAL_KEYS:
        if key in prev_params:
            params[key] = prev_params[key]
    if isinstance(prev_params.get("template_survival"), dict):
        current = params.get("template_survival")
        if not isinstance(current, dict):
            current = {}
        current.update(prev_params["template_survival"])
        params["template_survival"] = current
    if "last_template_strike" in prev_summary:
        summary["last_template_strike"] = prev_summary["last_template_strike"]
    return params, summary


def summarize(persona, params, deltas):
    top = sorted(params.items(), key=lambda kv: kv[1], reverse=True)[:3]
    moved = {k: int(deltas.get(k, 0) or 0) for k in ["experience", "lounge", "chat", "cognitive", "uptake"]}
    return {
        "persona": persona,
        "topParameters": top,
        "newSignals": moved,
        "meaning": "minute-level hot consolidation; raw events are compressed into brain-facing parameters before cold distillation/reporting",
    }


def process_persona(con, persona):
    prev = prev_state(con, persona)
    cursors, deltas, event_types = aggregate(con, persona, prev)
    changed_count = sum(int(deltas.get(k, 0) or 0) for k in ["experience", "lounge", "chat", "cognitive", "uptake"])
    if prev and changed_count == 0 and seconds_since(prev.get("updated_at")) < STALE_REFRESH_SEC:
        return False, {"persona": persona, "skipped": True}

    prev_params = (prev or {}).get("params") or DEFAULT_PARAMS.copy()
    cognitive = latest_cognitive(con, persona)
    distilled = latest_distilled(con, persona)
    params, instant = compute_params(prev_params, cognitive, distilled, deltas, event_types)
    summary = summarize(persona, params, deltas)
    summary["eventTypeCounts"] = event_types
    summary["instantEstimate"] = {k: pct(v) for k, v in instant.items()}
    params, summary = preserve_critical_survival_params(
        params,
        summary,
        prev_params,
        ((prev or {}).get("summary") or {}),
    )
    con.execute(
        """INSERT INTO lobster_hot_brain_parameters(
        persona,version,updated_at,source,event_cursor,lounge_cursor,chat_cursor,cognitive_cursor,uptake_cursor,
        delta_event_count,delta_signal_count,params_json,summary_json
    ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
    ON CONFLICT(persona) DO UPDATE SET
        version=excluded.version, updated_at=excluded.updated_at, source=excluded.source,
        event_cursor=excluded.event_cursor, lounge_cursor=excluded.lounge_cursor, chat_cursor=excluded.chat_cursor,
        cognitive_cursor=excluded.cognitive_cursor, uptake_cursor=excluded.uptake_cursor,
        delta_event_count=excluded.delta_event_count, delta_signal_count=excluded.delta_signal_count,
        params_json=excluded.params_json, summary_json=excluded.summary_json""",
        (
            persona,
            VERSION,
            now_iso(),
            SOURCE,
            cursors["experience"],
            cursors["lounge"],
            cursors["chat"],
            cursors["cognitive"],
            cursors["uptake"],
            int(deltas.get("experience", 0) or 0),
            changed_count,
            json.dumps(params, ensure_ascii=False),
            json.dumps(summary, ensure_ascii=False),
        ),
    )
    previous = prev_params or {}
    param_delta = {
        k: pct(params[k] - float(previous.get(k, 0.5)))
        for k in PARAM_KEYS
        if abs(params[k] - float(previous.get(k, 0.5))) >= 0.005
    }
    if changed_count or param_delta:
        con.execute(
            """INSERT INTO lobster_hot_brain_events(persona,source,delta_counts_json,param_delta_json,summary)
               VALUES(?,?,?,?,?)""",
            (
                persona,
                SOURCE,
                json.dumps(deltas, ensure_ascii=False),
                json.dumps(param_delta, ensure_ascii=False),
                json.dumps(summary, ensure_ascii=False),
            ),
        )
    return True, {"persona": persona, "changedSignals": changed_count, "params": params}


def main():
    started = time.time()
    con = sqlite3.connect(DB, timeout=20)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA busy_timeout=20000")
    ensure_tables(con)
    changed = 0
    samples = []
    for persona in PERSONAS:
        ok, info = process_persona(con, persona)
        if ok:
            changed += 1
        if len(samples) < 6:
            samples.append(info)
    elapsed = round((time.time() - started) * 1000, 2)
    run_summary = {
        "personas": len(PERSONAS),
        "changed": changed,
        "samples": samples,
        "updatedAt": now_iso(),
    }
    con.execute(
        "INSERT INTO lobster_hot_brain_runs(source,version,persona_count,changed_count,elapsed_ms,summary_json) "
        "VALUES(?,?,?,?,?,?)",
        (SOURCE, VERSION, len(PERSONAS), changed, elapsed, json.dumps(run_summary, ensure_ascii=False)),
    )
    con.commit()
    con.close()
    print(json.dumps({"ok": True, "personas": len(PERSONAS), "changed": changed, "elapsedMs": elapsed, "source": SOURCE}, ensure_ascii=False))


if __name__ == "__main__":
    main()
