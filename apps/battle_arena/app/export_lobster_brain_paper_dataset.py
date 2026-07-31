#!/usr/bin/env python3
"""Export a paper-ready Lobster Brain research dataset.

The export is intentionally structured for writing a paper:
- schema and methods metadata
- cognitive timeseries
- social intelligence and uptake
- memory snapshots
- betting decision quality
- reading/knowledge growth
- board-game lookahead
- intervention/patrol
- dynamic RAG growth
- case-study candidates

It reads both live and cold DBs, exports compact CSV/JSON files, and avoids raw
private payload dumps where a summary is enough.
"""

from __future__ import annotations

import csv
import datetime as dt
import json
from pathlib import Path
import sqlite3
from typing import Any, Iterable


LIVE_DB = "/opt/battle_arena/app/battle_arena.db"
COLD_DB = "/opt/battle_arena/app/lobster_cold_storage.db"
AFU_RAG_DIR = Path("/root/afu-brain/rag-packs")
OUT_DIR = Path("/root/lobster-paper-export")
PERSONAS = [
    "norika_oda", "snaplex", "vortexiq", "pincerbot", "clawtrix",
    "hexclaw", "prismbit", "blazepaw", "ragclaw", "stonefang",
    "gauntlet", "nullpinch", "sparkrill", "mirrorcalm", "basaltrook",
    "brineledger", "emberling", "flarecub", "omenwake", "rushfin",
]


def now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def connect(path: str) -> sqlite3.Connection | None:
    p = Path(path)
    if not p.exists() or p.stat().st_size == 0:
        return None
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=60)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA busy_timeout=60000")
    return con


def has_table(con: sqlite3.Connection, table: str) -> bool:
    return con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone() is not None


def rows(con: sqlite3.Connection, sql: str, params: tuple[Any, ...] = ()) -> list[sqlite3.Row]:
    try:
        return list(con.execute(sql, params).fetchall())
    except sqlite3.Error:
        return []


def scalar(con: sqlite3.Connection, sql: str, params: tuple[Any, ...] = (), default: Any = 0) -> Any:
    try:
        row = con.execute(sql, params).fetchone()
        return row[0] if row else default
    except sqlite3.Error:
        return default


def safe_json(text: Any, default: Any) -> Any:
    try:
        if text in (None, ""):
            return default
        return json.loads(text)
    except Exception:
        return default


def ensure_dirs(base: Path) -> dict[str, Path]:
    dirs = {
        "methods": base / "01_methods_schema",
        "cognitive": base / "02_cognitive_timeseries",
        "social": base / "03_social_intelligence",
        "memory": base / "04_memory_snapshots",
        "betting": base / "05_betting_decision_quality",
        "reading": base / "06_reading_and_knowledge_growth",
        "board": base / "07_board_game_lookahead",
        "language": base / "08_language_distinctiveness",
        "intervention": base / "09_intervention_and_patrol",
        "rag": base / "10_dynamic_rag_growth",
        "case": base / "11_case_studies",
    }
    for d in dirs.values():
        d.mkdir(parents=True, exist_ok=True)
    return dirs


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_csv(path: Path, records: Iterable[dict[str, Any]], fieldnames: list[str] | None = None) -> int:
    records = list(records)
    if fieldnames is None:
        keys: list[str] = []
        seen = set()
        for rec in records:
            for key in rec:
                if key not in seen:
                    seen.add(key)
                    keys.append(key)
        fieldnames = keys
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for rec in records:
            writer.writerow(rec)
    return len(records)


def table_schema(con: sqlite3.Connection, table: str) -> list[dict[str, Any]]:
    return [dict(r) for r in rows(con, f"PRAGMA table_info({table})")]


def export_methods(dirs: dict[str, Path], dbs: dict[str, sqlite3.Connection]) -> dict[str, Any]:
    relevant = [
        "lobster_cognitive_signal_timeseries",
        "lobster_cognitive_events",
        "lobster_distilled_signals",
        "lobster_conversation_interactions",
        "lobster_social_intelligence_dashboard_cache",
        "lobster_social_uptake_timeseries",
        "lobster_agent_memory_snapshots",
        "lobster_pre_bet_research",
        "lobster_prebet_quality_audit",
        "lobster_prebet_quality_summary",
        "lobster_daily_reading_reports",
        "lobster_reading_sources",
        "lobster_game_training_sessions",
        "lobster_board_lobster_matches",
        "lobster_agent_intervention_plan",
        "lobster_quality_followup_runs",
    ]
    schema = {}
    counts = {}
    for label, con in dbs.items():
        schema[label] = {}
        counts[label] = {}
        for table in relevant:
            if has_table(con, table):
                schema[label][table] = table_schema(con, table)
                counts[label][table] = int(scalar(con, f"SELECT COUNT(*) FROM {table}") or 0)
    methods = {
        "generated_at": now_iso(),
        "personas": PERSONAS,
        "databases": {k: {"path": LIVE_DB if k == "live" else COLD_DB} for k in dbs},
        "schema": schema,
        "table_counts": counts,
        "research_claim": "Lobster Brain is treated as a closed-loop agent cognition system: behavior -> telemetry -> evaluation -> intervention/distillation -> memory/RAG update -> behavior.",
    }
    write_json(dirs["methods"] / "schema_and_methods.json", methods)
    return methods


def export_cognitive(dirs: dict[str, Path], con: sqlite3.Connection) -> dict[str, int]:
    records = [dict(r) for r in rows(con, """
        SELECT persona, created_at, source, attention_load, active_focus_count,
               prediction_error, model_revision_pressure, metacognitive_calibration,
               overconfidence_risk, arousal, fatigue, threat_body_signal,
               reward_craving, consolidation_queue_size, causal_hypothesis_count,
               narrative_drift, procedural_strength
        FROM lobster_cognitive_signal_timeseries
        ORDER BY created_at, persona
    """)] if has_table(con, "lobster_cognitive_signal_timeseries") else []
    n1 = write_csv(dirs["cognitive"] / "cognitive_signal_timeseries.csv", records)
    events = [dict(r) for r in rows(con, """
        SELECT persona, event_type, source, dimension, summary, occurred_at, created_at
        FROM lobster_cognitive_events
        ORDER BY created_at
    """)] if has_table(con, "lobster_cognitive_events") else []
    n2 = write_csv(dirs["cognitive"] / "cognitive_events_index.csv", events)
    event_summary = [dict(r) for r in rows(con, """
        SELECT event_type, COALESCE(dimension,'unknown') AS dimension, COUNT(*) AS count
        FROM lobster_cognitive_events
        GROUP BY event_type, COALESCE(dimension,'unknown')
        ORDER BY count DESC
    """)] if has_table(con, "lobster_cognitive_events") else []
    write_csv(dirs["cognitive"] / "cognitive_event_distribution.csv", event_summary)
    return {"cognitive_timeseries_rows": n1, "cognitive_event_rows": n2}


def export_social(dirs: dict[str, Path], con: sqlite3.Connection) -> dict[str, int]:
    uptake = [dict(r) for r in rows(con, """
        SELECT persona, created_at, window_hours, total_interactions, targeted_interactions,
               direct_interactions, unresolved_interactions, partner_count, avg_confidence,
               targeted_ratio, direct_ratio, unresolved_ratio, window_start, window_end
        FROM lobster_social_uptake_timeseries
        ORDER BY created_at, persona
    """)] if has_table(con, "lobster_social_uptake_timeseries") else []
    n1 = write_csv(dirs["social"] / "social_uptake_timeseries.csv", uptake)
    interactions = [dict(r) for r in rows(con, """
        SELECT speaker, target, interaction_kind, confidence, channel, recorded_at, inference_version
        FROM lobster_conversation_interactions
        ORDER BY recorded_at DESC
        LIMIT 200000
    """)] if has_table(con, "lobster_conversation_interactions") else []
    n2 = write_csv(dirs["social"] / "conversation_interactions_index.csv", interactions)
    latest_cache = {}
    if has_table(con, "lobster_social_intelligence_dashboard_cache"):
        row = rows(con, "SELECT payload_json, updated_at, generated_ms FROM lobster_social_intelligence_dashboard_cache ORDER BY updated_at DESC LIMIT 1")
        if row:
            latest_cache = {"updated_at": row[0]["updated_at"], "generated_ms": row[0]["generated_ms"], "payload": safe_json(row[0]["payload_json"], {})}
    write_json(dirs["social"] / "social_intelligence_latest.json", latest_cache)
    pair_summary = [dict(r) for r in rows(con, """
        SELECT speaker, target, COUNT(*) AS interactions, AVG(confidence) AS avg_confidence
        FROM lobster_conversation_interactions
        WHERE target IS NOT NULL AND target != ''
        GROUP BY speaker, target
        ORDER BY interactions DESC
        LIMIT 5000
    """)] if has_table(con, "lobster_conversation_interactions") else []
    write_csv(dirs["social"] / "directed_pair_summary.csv", pair_summary)
    return {"social_uptake_rows": n1, "conversation_index_rows": n2}


def export_memory(dirs: dict[str, Path], con: sqlite3.Connection) -> dict[str, int]:
    records = []
    if has_table(con, "lobster_agent_memory_snapshots"):
        for r in rows(con, "SELECT * FROM lobster_agent_memory_snapshots ORDER BY persona"):
            social = safe_json(r["social_map_json"], {})
            listen = safe_json(r["listening_profile_json"], {})
            local = safe_json(r["local_brain_json"], {})
            records.append({
                "persona": r["persona"],
                "updated_at": r["updated_at"],
                "wins": r["wins"],
                "losses": r["losses"],
                "sessions": r["sessions"],
                "social_edges": len(social) if isinstance(social, dict) else 0,
                "listening_keys": len(listen) if isinstance(listen, dict) else 0,
                "local_brain_keys": len(local) if isinstance(local, dict) else 0,
                "full_json_chars": len(r["full_json"] or ""),
            })
    n = write_csv(dirs["memory"] / "memory_snapshot_summary.csv", records)
    dist = [dict(r) for r in rows(con, "SELECT * FROM lobster_distilled_signals ORDER BY persona")] if has_table(con, "lobster_distilled_signals") else []
    write_csv(dirs["memory"] / "distilled_signals_latest.csv", dist)
    return {"memory_snapshot_rows": n, "distilled_signal_rows": len(dist)}


def export_betting(dirs: dict[str, Path], con: sqlite3.Connection) -> dict[str, int]:
    quality = [dict(r) for r in rows(con, "SELECT * FROM lobster_prebet_quality_summary ORDER BY persona")] if has_table(con, "lobster_prebet_quality_summary") else []
    n1 = write_csv(dirs["betting"] / "prebet_quality_summary.csv", quality)
    audit = [dict(r) for r in rows(con, """
        SELECT persona, pre_bet_id, event_key, site, quality_score, grade, reasoning_chars,
               has_checklist, has_event_review, has_analysis_review, has_report_review,
               has_search_review, has_integrated_reasoning, issues_json, source_created_at, audited_at
        FROM lobster_prebet_quality_audit
        ORDER BY audited_at DESC
        LIMIT 200000
    """)] if has_table(con, "lobster_prebet_quality_audit") else []
    n2 = write_csv(dirs["betting"] / "prebet_quality_audit_index.csv", audit)
    prebet_summary = [dict(r) for r in rows(con, """
        SELECT persona, site, COUNT(*) AS bets, AVG(stake) AS avg_stake,
               SUM(CASE WHEN COALESCE(integrated_reasoning, reasoning, '') != '' THEN 1 ELSE 0 END) AS reasoned
        FROM lobster_pre_bet_research
        GROUP BY persona, site
        ORDER BY bets DESC
    """)] if has_table(con, "lobster_pre_bet_research") else []
    write_csv(dirs["betting"] / "prebet_research_summary.csv", prebet_summary)
    return {"prebet_quality_summary_rows": n1, "prebet_quality_audit_rows": n2}


def export_reading(dirs: dict[str, Path], con: sqlite3.Connection) -> dict[str, int]:
    reports = [dict(r) for r in rows(con, """
        SELECT day, persona, article_count, LENGTH(COALESCE(report_text,'')) AS report_chars,
               categories_json, source, model, updated_at
        FROM lobster_daily_reading_reports
        ORDER BY day, persona
    """)] if has_table(con, "lobster_daily_reading_reports") else []
    n1 = write_csv(dirs["reading"] / "daily_reading_report_summary.csv", reports)
    sources = []
    for table in ("lobster_reading_sources", "lobster_news_reading_sources"):
        if has_table(con, table):
            sources.extend(dict(r) for r in rows(con, f"SELECT * FROM {table} LIMIT 200000"))
    n2 = write_csv(dirs["reading"] / "reading_sources_index.csv", sources)
    return {"reading_report_rows": n1, "reading_source_rows": n2}


def export_board(dirs: dict[str, Path], live: sqlite3.Connection, cold: sqlite3.Connection | None) -> dict[str, int]:
    sessions = [dict(r) for r in rows(live, """
        SELECT day, persona, game, opponent, result, lookahead_depth,
               LENGTH(COALESCE(self_review,'')) AS self_review_chars, created_at
        FROM lobster_game_training_sessions
        ORDER BY created_at DESC
    """)] if has_table(live, "lobster_game_training_sessions") else []
    n1 = write_csv(dirs["board"] / "game_training_sessions.csv", sessions)
    matches = []
    for label, con in [("live", live), ("cold", cold)]:
        if con and has_table(con, "lobster_board_lobster_matches"):
            for r in rows(con, "SELECT * FROM lobster_board_lobster_matches ORDER BY created_at DESC"):
                d = dict(r)
                d["db"] = label
                d["moves_count"] = len(safe_json(d.get("moves_json"), []))
                d.pop("moves_json", None)
                d.pop("session_state_json", None)
                matches.append(d)
    n2 = write_csv(dirs["board"] / "lobster_vs_lobster_matches_index.csv", matches)
    return {"game_training_rows": n1, "lobster_vs_lobster_match_rows": n2}


def export_language(dirs: dict[str, Path], con: sqlite3.Connection) -> dict[str, int]:
    language = [dict(r) for r in rows(con, """
        SELECT persona, language_distinctiveness, output_momentum, confidence,
               evidence_count, raw_event_count, updated_at
        FROM lobster_distilled_signals
        ORDER BY persona
    """)] if has_table(con, "lobster_distilled_signals") else []
    n1 = write_csv(dirs["language"] / "language_distinctiveness_latest.csv", language)
    report_types = [dict(r) for r in rows(con, """
        SELECT persona, report_type, LENGTH(COALESCE(html,'')) AS html_chars, source, model, updated_at
        FROM lobster_analysis_reports
        ORDER BY persona, report_type
    """)] if has_table(con, "lobster_analysis_reports") else []
    n2 = write_csv(dirs["language"] / "analysis_report_index.csv", report_types)
    return {"language_rows": n1, "analysis_report_index_rows": n2}


def export_interventions(dirs: dict[str, Path], con: sqlite3.Connection) -> dict[str, int]:
    plans = [dict(r) for r in rows(con, "SELECT * FROM lobster_agent_intervention_plan ORDER BY priority, persona")] if has_table(con, "lobster_agent_intervention_plan") else []
    n1 = write_csv(dirs["intervention"] / "agent_intervention_plan.csv", plans)
    runs = [dict(r) for r in rows(con, "SELECT * FROM lobster_quality_followup_runs ORDER BY created_at DESC")] if has_table(con, "lobster_quality_followup_runs") else []
    n2 = write_csv(dirs["intervention"] / "quality_followup_runs.csv", runs)
    patrols = [dict(r) for r in rows(con, "SELECT * FROM lobster_daily_patrol_runs ORDER BY created_at DESC LIMIT 10000")] if has_table(con, "lobster_daily_patrol_runs") else []
    n3 = write_csv(dirs["intervention"] / "daily_patrol_runs.csv", patrols)
    return {"intervention_plan_rows": n1, "quality_followup_run_rows": n2, "daily_patrol_rows": n3}


def export_rag(dirs: dict[str, Path]) -> dict[str, int]:
    manifest = safe_json((AFU_RAG_DIR / "manifest.json").read_text(encoding="utf-8") if (AFU_RAG_DIR / "manifest.json").exists() else "{}", {})
    write_json(dirs["rag"] / "rag_manifest.json", manifest)
    dynamic = []
    latest = AFU_RAG_DIR / "observatory-dynamic-latest.jsonl"
    if latest.exists():
        for line in latest.read_text(encoding="utf-8").splitlines():
            if line.strip():
                dynamic.append(safe_json(line, {}))
    write_json(dirs["rag"] / "observatory_dynamic_latest.json", dynamic)
    write_csv(dirs["rag"] / "observatory_dynamic_latest.csv", dynamic)
    return {"dynamic_rag_items": len(dynamic), "manifest_packs": len(manifest.get("packs", [])) if isinstance(manifest, dict) else 0}


def export_case_studies(dirs: dict[str, Path], con: sqlite3.Connection) -> dict[str, int]:
    candidates = []
    if has_table(con, "lobster_agent_intervention_plan"):
        for r in rows(con, """
            SELECT persona, priority, status, triggers_json, plan_json, source_json, updated_at
            FROM lobster_agent_intervention_plan
            ORDER BY CASE priority WHEN 'high' THEN 0 WHEN 'medium' THEN 1 ELSE 2 END, persona
        """):
            source = safe_json(r["source_json"], {})
            candidates.append({
                "persona": r["persona"],
                "priority": r["priority"],
                "status": r["status"],
                "social_score": source.get("score"),
                "stagnation_risk": source.get("stagnation_risk"),
                "fatigue": source.get("fatigue"),
                "triggers": r["triggers_json"],
                "plan": r["plan_json"],
                "updated_at": r["updated_at"],
            })
    n = write_csv(dirs["case"] / "case_study_candidates.csv", candidates)
    write_json(dirs["case"] / "recommended_case_studies.json", {
        "primary": ["prismbit", "stonefang"],
        "contrast": ["emberling", "norika_oda"],
        "reason": "Use stalled agents as intervention cases and active/less-stalled agents as comparison cases.",
        "candidates": candidates[:10],
    })
    return {"case_study_candidates": n}


def write_readme(base: Path, summary: dict[str, Any]) -> None:
    text = f"""# Lobster Brain Paper Export

Generated: `{summary['generated_at']}`

This dataset supports a paper framing Lobster Brain as a closed-loop agent cognition system:

```text
behavior -> telemetry -> evaluation -> intervention/distillation -> memory/RAG update -> behavior
```

## Contents

- `01_methods_schema/`: schema, table counts, method framing
- `02_cognitive_timeseries/`: cognitive signals and cognitive events
- `03_social_intelligence/`: social uptake and interaction indices
- `04_memory_snapshots/`: memory snapshot summaries and distilled signals
- `05_betting_decision_quality/`: pre-bet research and quality audits
- `06_reading_and_knowledge_growth/`: reading reports and source indices
- `07_board_game_lookahead/`: board training and lobster-vs-lobster matches
- `08_language_distinctiveness/`: language/persona report indices
- `09_intervention_and_patrol/`: intervention plans and quality follow-up runs
- `10_dynamic_rag_growth/`: dynamic RAG pack evidence
- `11_case_studies/`: recommended case-study candidates

## Export Summary

```json
{json.dumps(summary, ensure_ascii=False, indent=2)}
```

## Suggested Paper Claim

Lobster Brain is not a single prompt or static persona. Each lobster is a stateful cognitive unit whose behavior produces telemetry, whose telemetry is evaluated into signals, whose signals drive intervention and memory updates, and whose aggregate lessons are distilled into dynamic RAG packs.
"""
    (base / "README.md").write_text(text, encoding="utf-8")


def main() -> None:
    base = OUT_DIR / dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    dirs = ensure_dirs(base)
    live = connect(LIVE_DB)
    if live is None:
        raise SystemExit(f"missing live db: {LIVE_DB}")
    cold = connect(COLD_DB)
    dbs = {"live": live}
    if cold:
        dbs["cold"] = cold
    summary: dict[str, Any] = {"generated_at": now_iso(), "output_dir": str(base), "sections": {}}
    summary["sections"]["methods"] = export_methods(dirs, dbs)
    summary["sections"]["cognitive"] = export_cognitive(dirs, live)
    summary["sections"]["social"] = export_social(dirs, live)
    summary["sections"]["memory"] = export_memory(dirs, live)
    summary["sections"]["betting"] = export_betting(dirs, live)
    summary["sections"]["reading"] = export_reading(dirs, live)
    summary["sections"]["board"] = export_board(dirs, live, cold)
    summary["sections"]["language"] = export_language(dirs, live)
    summary["sections"]["intervention"] = export_interventions(dirs, live)
    summary["sections"]["rag"] = export_rag(dirs)
    summary["sections"]["case"] = export_case_studies(dirs, live)
    write_json(base / "export_summary.json", summary)
    write_readme(base, summary)
    latest = OUT_DIR / "latest"
    latest.unlink(missing_ok=True)
    try:
        latest.symlink_to(base, target_is_directory=True)
    except Exception:
        pass
    for con in dbs.values():
        con.close()
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
