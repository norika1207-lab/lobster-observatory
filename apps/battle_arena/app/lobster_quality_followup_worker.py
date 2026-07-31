#!/usr/bin/env python3
"""Follow-up quality worker for Lobster growth telemetry.

This worker turns the 24h report follow-ups into durable DB records:

1. pre-bet research quality audit
2. stalled/high-fatigue intervention plans
3. social uptake timeseries

It reads recent live data, writes compact dashboard-ready tables, and avoids
mutating raw source rows.
"""

from __future__ import annotations

import datetime as dt
import json
import sqlite3
import time
from typing import Any


DB_PATH = "/opt/battle_arena/app/battle_arena.db"
TAIPEI = dt.timezone(dt.timedelta(hours=8))
PERSONAS = [
    "norika_oda", "snaplex", "vortexiq", "pincerbot", "clawtrix",
    "hexclaw", "prismbit", "blazepaw", "ragclaw", "stonefang",
    "gauntlet", "nullpinch", "sparkrill", "mirrorcalm", "basaltrook",
    "brineledger", "emberling", "flarecub", "omenwake", "rushfin",
]


def now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def window_start(hours: int = 24) -> str:
    return (dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=hours)).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def safe_json(text: Any, default: Any) -> Any:
    try:
        if text in (None, ""):
            return default
        return json.loads(text)
    except Exception:
        return default


def json_nonempty(text: Any) -> bool:
    obj = safe_json(text, {})
    if isinstance(obj, dict):
        return bool(obj)
    if isinstance(obj, list):
        return bool(obj)
    return bool(obj)


def ensure_schema(con: sqlite3.Connection) -> None:
    con.executescript(
        """
PRAGMA busy_timeout=60000;

CREATE TABLE IF NOT EXISTS lobster_prebet_quality_audit (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  audit_run_id TEXT NOT NULL,
  persona TEXT NOT NULL,
  pre_bet_id INTEGER NOT NULL,
  event_key TEXT,
  site TEXT,
  event_title TEXT,
  quality_score REAL NOT NULL,
  grade TEXT NOT NULL,
  reasoning_chars INTEGER NOT NULL DEFAULT 0,
  has_checklist INTEGER NOT NULL DEFAULT 0,
  has_event_review INTEGER NOT NULL DEFAULT 0,
  has_analysis_review INTEGER NOT NULL DEFAULT 0,
  has_report_review INTEGER NOT NULL DEFAULT 0,
  has_search_review INTEGER NOT NULL DEFAULT 0,
  has_integrated_reasoning INTEGER NOT NULL DEFAULT 0,
  issues_json TEXT NOT NULL DEFAULT '[]',
  source_created_at TEXT,
  audited_at TEXT NOT NULL DEFAULT (datetime('now')),
  UNIQUE(audit_run_id, pre_bet_id)
);
CREATE INDEX IF NOT EXISTS idx_lobster_prebet_quality_audit_persona_time
  ON lobster_prebet_quality_audit(persona, audited_at);

CREATE TABLE IF NOT EXISTS lobster_prebet_quality_summary (
  persona TEXT PRIMARY KEY,
  window_hours INTEGER NOT NULL,
  sample_count INTEGER NOT NULL DEFAULT 0,
  avg_quality_score REAL NOT NULL DEFAULT 0,
  min_quality_score REAL NOT NULL DEFAULT 0,
  max_quality_score REAL NOT NULL DEFAULT 0,
  weak_count INTEGER NOT NULL DEFAULT 0,
  missing_search_count INTEGER NOT NULL DEFAULT 0,
  missing_report_count INTEGER NOT NULL DEFAULT 0,
  short_reasoning_count INTEGER NOT NULL DEFAULT 0,
  latest_audit_run_id TEXT NOT NULL,
  summary_json TEXT NOT NULL DEFAULT '{}',
  updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS lobster_social_uptake_timeseries (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  persona TEXT NOT NULL,
  window_hours INTEGER NOT NULL,
  total_interactions INTEGER NOT NULL DEFAULT 0,
  targeted_interactions INTEGER NOT NULL DEFAULT 0,
  direct_interactions INTEGER NOT NULL DEFAULT 0,
  unresolved_interactions INTEGER NOT NULL DEFAULT 0,
  partner_count INTEGER NOT NULL DEFAULT 0,
  avg_confidence REAL NOT NULL DEFAULT 0,
  targeted_ratio REAL NOT NULL DEFAULT 0,
  direct_ratio REAL NOT NULL DEFAULT 0,
  unresolved_ratio REAL NOT NULL DEFAULT 0,
  window_start TEXT NOT NULL,
  window_end TEXT NOT NULL,
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_lobster_social_uptake_timeseries_persona_time
  ON lobster_social_uptake_timeseries(persona, created_at);

CREATE TABLE IF NOT EXISTS lobster_agent_intervention_plan (
  persona TEXT PRIMARY KEY,
  status TEXT NOT NULL,
  priority TEXT NOT NULL,
  triggers_json TEXT NOT NULL DEFAULT '[]',
  plan_json TEXT NOT NULL DEFAULT '[]',
  source_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS lobster_quality_followup_runs (
  run_id TEXT PRIMARY KEY,
  window_hours INTEGER NOT NULL,
  prebet_audits INTEGER NOT NULL DEFAULT 0,
  social_rows INTEGER NOT NULL DEFAULT 0,
  intervention_rows INTEGER NOT NULL DEFAULT 0,
  summary_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
"""
    )


def prebet_quality(row: sqlite3.Row) -> dict[str, Any]:
    checklist = json_nonempty(row["checklist_json"])
    event_review = json_nonempty(row["event_review_json"])
    analysis_review = json_nonempty(row["analysis_review_json"])
    report_review = json_nonempty(row["report_review_json"])
    search_review = json_nonempty(row["search_review_json"])
    reasoning = (row["integrated_reasoning"] or row["reasoning"] or row["raw_decision_reasoning"] or "").strip()
    reasoning_chars = len(reasoning)
    score = 0.0
    score += 0.14 if checklist else 0
    score += 0.14 if event_review else 0
    score += 0.16 if analysis_review else 0
    score += 0.16 if report_review else 0
    score += 0.18 if search_review else 0
    score += 0.12 if reasoning_chars >= 300 else 0.05 if reasoning_chars >= 120 else 0
    score += 0.10 if row["integrated_reasoning"] else 0
    issues = []
    if not checklist:
        issues.append("missing_checklist")
    if not event_review:
        issues.append("missing_event_review")
    if not analysis_review:
        issues.append("missing_analysis_review")
    if not report_review:
        issues.append("missing_report_review")
    if not search_review:
        issues.append("missing_search_review")
    if reasoning_chars < 300:
        issues.append("reasoning_too_short")
    if not row["integrated_reasoning"]:
        issues.append("missing_integrated_reasoning")
    if score >= 0.82:
        grade = "strong"
    elif score >= 0.62:
        grade = "adequate"
    elif score >= 0.42:
        grade = "weak"
    else:
        grade = "bad"
    return {
        "score": round(score, 4),
        "grade": grade,
        "reasoning_chars": reasoning_chars,
        "has_checklist": int(checklist),
        "has_event_review": int(event_review),
        "has_analysis_review": int(analysis_review),
        "has_report_review": int(report_review),
        "has_search_review": int(search_review),
        "has_integrated_reasoning": int(bool(row["integrated_reasoning"])),
        "issues": issues,
    }


def audit_prebet(con: sqlite3.Connection, run_id: str, hours: int) -> int:
    if not has_table(con, "lobster_pre_bet_research"):
        return 0
    rows = con.execute(
        """
        SELECT id, persona, event_key, site, event_title,
               checklist_json, event_review_json, analysis_review_json,
               report_review_json, search_review_json,
               reasoning, integrated_reasoning, raw_decision_reasoning,
               created_at
        FROM lobster_pre_bet_research AS p
        WHERE p.id > 0
        ORDER BY p.id DESC
        LIMIT 800
        """,
    ).fetchall()
    created = 0
    summaries: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        q = prebet_quality(row)
        before = con.total_changes
        con.execute(
            """
            INSERT OR IGNORE INTO lobster_prebet_quality_audit
            (audit_run_id, persona, pre_bet_id, event_key, site, event_title, quality_score, grade,
             reasoning_chars, has_checklist, has_event_review, has_analysis_review, has_report_review,
             has_search_review, has_integrated_reasoning, issues_json, source_created_at, audited_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                row["persona"],
                row["id"],
                row["event_key"],
                row["site"],
                row["event_title"],
                q["score"],
                q["grade"],
                q["reasoning_chars"],
                q["has_checklist"],
                q["has_event_review"],
                q["has_analysis_review"],
                q["has_report_review"],
                q["has_search_review"],
                q["has_integrated_reasoning"],
                json.dumps(q["issues"], ensure_ascii=False),
                row["created_at"],
                now_iso(),
            ),
        )
        if con.total_changes > before:
            created += 1
        summaries.setdefault(row["persona"], []).append(q)
        if created and created % 250 == 0:
            con.commit()
    for persona, items in summaries.items():
        scores = [x["score"] for x in items]
        weak = [x for x in items if x["grade"] in ("weak", "bad")]
        summary = {
            "grades": {g: sum(1 for x in items if x["grade"] == g) for g in ("strong", "adequate", "weak", "bad")},
            "most_common_issues": {},
        }
        issue_counts: dict[str, int] = {}
        for x in items:
            for issue in x["issues"]:
                issue_counts[issue] = issue_counts.get(issue, 0) + 1
        summary["most_common_issues"] = dict(sorted(issue_counts.items(), key=lambda kv: kv[1], reverse=True)[:8])
        con.execute(
            """
            INSERT INTO lobster_prebet_quality_summary
            (persona, window_hours, sample_count, avg_quality_score, min_quality_score, max_quality_score,
             weak_count, missing_search_count, missing_report_count, short_reasoning_count,
             latest_audit_run_id, summary_json, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(persona) DO UPDATE SET
              window_hours=excluded.window_hours,
              sample_count=excluded.sample_count,
              avg_quality_score=excluded.avg_quality_score,
              min_quality_score=excluded.min_quality_score,
              max_quality_score=excluded.max_quality_score,
              weak_count=excluded.weak_count,
              missing_search_count=excluded.missing_search_count,
              missing_report_count=excluded.missing_report_count,
              short_reasoning_count=excluded.short_reasoning_count,
              latest_audit_run_id=excluded.latest_audit_run_id,
              summary_json=excluded.summary_json,
              updated_at=excluded.updated_at
            """,
            (
                persona,
                hours,
                len(items),
                round(sum(scores) / max(1, len(scores)), 4),
                min(scores),
                max(scores),
                len(weak),
                sum(1 for x in items if "missing_search_review" in x["issues"]),
                sum(1 for x in items if "missing_report_review" in x["issues"]),
                sum(1 for x in items if "reasoning_too_short" in x["issues"]),
                run_id,
                json.dumps(summary, ensure_ascii=False),
                now_iso(),
            ),
        )
    return len(rows)


def has_table(con: sqlite3.Connection, table: str) -> bool:
    return con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone() is not None


def update_social_uptake(con: sqlite3.Connection, hours: int) -> int:
    if not has_table(con, "lobster_conversation_interactions"):
        return 0
    end = now_iso()
    start = window_start(hours)
    rows = con.execute(
        """
        SELECT speaker AS persona, COUNT(*) AS total,
               SUM(CASE WHEN target IS NOT NULL AND target != '' THEN 1 ELSE 0 END) AS targeted,
               SUM(CASE WHEN interaction_kind IN ('explicit_reply_to','direct_mention','instruction_reference','quoted_previous_message') THEN 1 ELSE 0 END) AS direct,
               SUM(CASE WHEN interaction_kind='broadcast_or_unresolved' THEN 1 ELSE 0 END) AS unresolved,
               COUNT(DISTINCT target) AS partners,
               AVG(confidence) AS avg_conf
        FROM lobster_conversation_interactions
        WHERE recorded_at >= datetime('now', ?)
        GROUP BY speaker
        """,
        (f"-{hours} hours",),
    ).fetchall()
    inserted = 0
    for row in rows:
        persona = row["persona"]
        if not persona:
            continue
        total = int(row["total"] or 0)
        targeted = int(row["targeted"] or 0)
        direct = int(row["direct"] or 0)
        unresolved = int(row["unresolved"] or 0)
        con.execute(
            """
            INSERT INTO lobster_social_uptake_timeseries
            (persona, window_hours, total_interactions, targeted_interactions, direct_interactions,
             unresolved_interactions, partner_count, avg_confidence, targeted_ratio, direct_ratio,
             unresolved_ratio, window_start, window_end, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                persona,
                hours,
                total,
                targeted,
                direct,
                unresolved,
                int(row["partners"] or 0),
                round(float(row["avg_conf"] or 0), 4),
                round(targeted / max(1, total), 4),
                round(direct / max(1, total), 4),
                round(unresolved / max(1, total), 4),
                start,
                end,
                end,
            ),
        )
        inserted += 1
    return inserted


def latest_social_cache(con: sqlite3.Connection) -> dict[str, Any]:
    if not has_table(con, "lobster_social_intelligence_dashboard_cache"):
        return {}
    row = con.execute(
        "SELECT payload_json FROM lobster_social_intelligence_dashboard_cache ORDER BY updated_at DESC LIMIT 1"
    ).fetchone()
    if not row:
        return {}
    return safe_json(row["payload_json"], {})


def latest_fatigue(con: sqlite3.Connection) -> dict[str, float]:
    out = {}
    if not has_table(con, "lobster_cognitive_signal_timeseries"):
        return out
    for p in PERSONAS:
        row = con.execute(
            "SELECT fatigue FROM lobster_cognitive_signal_timeseries WHERE persona=? ORDER BY created_at DESC,id DESC LIMIT 1",
            (p,),
        ).fetchone()
        if row and row["fatigue"] is not None:
            out[p] = float(row["fatigue"])
    return out


def update_interventions(con: sqlite3.Connection) -> int:
    social = latest_social_cache(con)
    agents = social.get("agents") or []
    fatigue = latest_fatigue(con)
    rows = 0
    for a in agents:
        persona = a.get("persona")
        if not persona:
            continue
        score = float(a.get("score") or 0)
        stagnation = float(a.get("stagnation_risk") or 0)
        state = str(a.get("growth_state") or "")
        f = fatigue.get(persona, 0)
        triggers = []
        plan = []
        priority = "watch"
        if state == "stalled" or stagnation >= 70:
            triggers.append({"type": "stagnation", "value": stagnation, "state": state})
            plan.extend([
                "Reduce raw output volume for 24h; prioritize high-quality replies.",
                "Assign one direct partner and require explicit uptake of the partner's last claim.",
                "Require one evidence citation or memory reference before public response.",
            ])
            priority = "high"
        if score < 35:
            triggers.append({"type": "low_social_score", "value": score})
            plan.append("Run social repair task: reply to a named partner with a concrete agreement, disagreement, or question.")
            priority = "high" if priority == "high" else "medium"
        if f >= 0.9:
            triggers.append({"type": "fatigue", "value": round(f, 3)})
            plan.append("Apply cooldown: shift from quantity tasks to reading, reflection, and summary-only tasks.")
            priority = "high" if priority == "high" else "medium"
        if not triggers:
            triggers.append({"type": "normal_monitoring", "value": score})
            plan.append("Continue ordinary training; review if direct uptake or quality score drops.")
        con.execute(
            """
            INSERT INTO lobster_agent_intervention_plan
            (persona, status, priority, triggers_json, plan_json, source_json, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(persona) DO UPDATE SET
              status=excluded.status,
              priority=excluded.priority,
              triggers_json=excluded.triggers_json,
              plan_json=excluded.plan_json,
              source_json=excluded.source_json,
              updated_at=excluded.updated_at
            """,
            (
                persona,
                state or "unknown",
                priority,
                json.dumps(triggers, ensure_ascii=False),
                json.dumps(plan, ensure_ascii=False),
                json.dumps({"score": score, "stagnation_risk": stagnation, "fatigue": f}, ensure_ascii=False),
                now_iso(),
                now_iso(),
            ),
        )
        rows += 1
    return rows


def main() -> None:
    run_id = f"quality-followup-{dt.datetime.now(TAIPEI).strftime('%Y%m%d-%H%M%S')}"
    hours = 24
    con = sqlite3.connect(DB_PATH, timeout=5)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA busy_timeout=5000")
    ensure_schema(con)
    audits = audit_prebet(con, run_id, hours)
    social_rows = update_social_uptake(con, hours)
    interventions = update_interventions(con)
    summary = {
        "prebet_audits": audits,
        "social_rows": social_rows,
        "interventions": interventions,
    }
    con.execute(
        """
        INSERT INTO lobster_quality_followup_runs
        (run_id, window_hours, prebet_audits, social_rows, intervention_rows, summary_json, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (run_id, hours, audits, social_rows, interventions, json.dumps(summary, ensure_ascii=False), now_iso()),
    )
    con.commit()
    con.close()
    print(json.dumps({"ok": True, "run_id": run_id, **summary}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    for attempt in range(1, 4):
        try:
            main()
            break
        except sqlite3.OperationalError as exc:
            if "locked" not in str(exc).lower() and "busy" not in str(exc).lower():
                raise
            if attempt == 3:
                print(json.dumps({
                    "ok": False,
                    "deferred": True,
                    "run": "lobster-quality-followup",
                    "reason": "battle_arena.db busy; skipped this cycle instead of failing systemd",
                    "error": str(exc),
                    "checkedAt": now_iso(),
                }, ensure_ascii=False))
                break
            time.sleep(3 * attempt)
