#!/usr/bin/env python3
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from typing import Any

DB_PATH = "/opt/battle_arena/app/battle_arena.db"
MAIN_EXCLUDES = {"system", "deleted-lobsters", "lobster", "elder", "sunchirp"}
REPORT_TYPES = ("mbti", "psych", "anthro", "socio", "fbi", "emotion")
ONE_ON_ONE_MIN_MESSAGES = 6


def jload(value: Any, default: Any = None) -> Any:
    if default is None:
        default = {}
    if value is None:
        return default
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except Exception:
        return default


def conn() -> sqlite3.Connection:
    c = sqlite3.connect(DB_PATH, timeout=12)
    c.row_factory = sqlite3.Row
    return c


def table_exists(c: sqlite3.Connection, name: str) -> bool:
    return bool(c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone())


def get_main_personas(c: sqlite3.Connection) -> list[str]:
    names: set[str] = set()
    for table in (
        "lobster_agent_memory_snapshots",
        "lobster_cognitive_extensions",
        "lobster_hot_brain_parameters",
        "lobster_local_brain",
    ):
        if not table_exists(c, table):
            continue
        try:
            for row in c.execute(f"SELECT DISTINCT persona FROM {table}"):
                name = str(row[0] or "").strip()
                if name and name not in MAIN_EXCLUDES:
                    names.add(name)
        except sqlite3.Error:
            pass
    return sorted(names)


def latest_row(c: sqlite3.Connection, table: str, persona: str, order: str = "rowid") -> dict[str, Any]:
    if not table_exists(c, table):
        return {}
    try:
        row = c.execute(f"SELECT * FROM {table} WHERE persona=? ORDER BY {order} DESC LIMIT 1", (persona,)).fetchone()
        return dict(row) if row else {}
    except sqlite3.Error:
        return {}


def count_where(c: sqlite3.Connection, sql: str, args: tuple[Any, ...]) -> int:
    try:
        row = c.execute(sql, args).fetchone()
        return int(row[0] or 0) if row else 0
    except sqlite3.Error:
        return 0


def compact(value: Any, depth: int = 0) -> Any:
    if depth > 6:
        return {"_omitted": "nested object", "type": type(value).__name__}
    if value is None or isinstance(value, (int, float, bool)):
        return value
    if isinstance(value, str):
        return value if len(value) <= 5000 else value[:5000] + "...[truncated]"
    if isinstance(value, list):
        limit = 80 if depth < 2 else 30
        out = [compact(v, depth + 1) for v in value[:limit]]
        if len(value) > limit:
            out.append({"_truncated_items": len(value) - limit})
        return out
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for idx, (k, v) in enumerate(value.items()):
            if idx >= 120:
                out["_truncated_keys"] = len(value) - idx
                break
            out[str(k)] = compact(v, depth + 1)
        return out
    return str(value)


def report_health(c: sqlite3.Connection, persona: str) -> dict[str, Any]:
    reports: dict[str, Any] = {}
    missing = []
    short = []
    if not table_exists(c, "lobster_analysis_reports"):
        return {"reports": {}, "missing": list(REPORT_TYPES), "short": list(REPORT_TYPES), "ok": False}
    rows = c.execute(
        """SELECT report_type, html, source, model, updated_at
           FROM lobster_analysis_reports WHERE persona=?""",
        (persona,),
    ).fetchall()
    by_type = {r["report_type"]: dict(r) for r in rows}
    for rtype in REPORT_TYPES:
        row = by_type.get(rtype)
        if not row:
            missing.append(rtype)
            continue
        length = len(row.get("html") or "")
        item = {
            "chars": length,
            "source": row.get("source"),
            "model": row.get("model"),
            "updated_at": row.get("updated_at"),
            "meets_5000_chars": length >= 5000,
        }
        reports[rtype] = item
        if length < 5000:
            short.append(rtype)
    return {"reports": reports, "missing": missing, "short": short, "ok": not missing and not short}


def audit_summary(c: sqlite3.Connection, persona: str) -> dict[str, Any]:
    out: dict[str, Any] = {"page_keys": {}, "tab_keys": {}, "bad_params": []}
    if table_exists(c, "lobster_detail_parameter_audit"):
        try:
            for r in c.execute(
                """WITH latest AS (
                       SELECT a.*
                       FROM lobster_detail_parameter_audit a
                       JOIN (
                           SELECT page_key, param_key, MAX(id) id
                           FROM lobster_detail_parameter_audit
                           WHERE persona=?
                           GROUP BY page_key, param_key
                       ) m ON a.id=m.id
                   )
                   SELECT page_key, COUNT(*) total, SUM(ok) ok_count, COUNT(*)-SUM(ok) bad_count
                   FROM latest
                   GROUP BY page_key
                   ORDER BY bad_count DESC""",
                (persona,),
            ):
                out["page_keys"][r["page_key"]] = {
                    "total": int(r["total"] or 0),
                    "ok": int(r["ok_count"] or 0),
                    "bad": int(r["bad_count"] or 0),
                }
            for r in c.execute(
                """WITH latest AS (
                       SELECT a.*
                       FROM lobster_detail_parameter_audit a
                       JOIN (
                           SELECT page_key, param_key, MAX(id) id
                           FROM lobster_detail_parameter_audit
                           WHERE persona=?
                           GROUP BY page_key, param_key
                       ) m ON a.id=m.id
                   )
                   SELECT page_key, param_key, metric_json, note, checked_at
                   FROM latest
                   WHERE ok=0
                   ORDER BY id DESC LIMIT 80""",
                (persona,),
            ):
                out["bad_params"].append({
                    "page": r["page_key"],
                    "param": r["param_key"],
                    "metric": jload(r["metric_json"], {}),
                    "note": r["note"],
                    "checked_at": r["checked_at"],
                })
        except sqlite3.Error:
            pass
    if table_exists(c, "lobster_detail_tab_audit"):
        try:
            for r in c.execute(
                """SELECT tab_key, ok, metrics_json, missing_json
                   FROM lobster_detail_tab_audit
                   WHERE persona=?
                   ORDER BY id DESC""",
                (persona,),
            ):
                if r["tab_key"] not in out["tab_keys"]:
                    out["tab_keys"][r["tab_key"]] = {
                        "ok": bool(r["ok"]),
                        "metrics": jload(r["metrics_json"], {}),
                        "missing": jload(r["missing_json"], []),
                    }
        except sqlite3.Error:
            pass
    return out


def task_state(c: sqlite3.Connection, persona: str) -> dict[str, Any]:
    return {
        "one_on_one_messages": count_where(c, "SELECT COUNT(*) FROM lobster_chat_log WHERE name=?", (persona,)),
        "lounge_messages": count_where(c, "SELECT COUNT(*) FROM lounge_messages WHERE name=?", (persona,)),
        "bet_research": count_where(c, "SELECT COUNT(*) FROM lobster_pre_bet_research WHERE persona=?", (persona,)),
        "bet_settlements": count_where(c, "SELECT COUNT(*) FROM lobster_bet_decision_settlements WHERE persona=?", (persona,)),
        "stock_trades": count_where(c, "SELECT COUNT(*) FROM lobster_stock_trades WHERE persona=?", (persona,)),
        "stock_reports": count_where(c, "SELECT COUNT(*) FROM lobster_stock_investment_reports WHERE persona=?", (persona,)),
        "experience_events": count_where(c, "SELECT COUNT(*) FROM lobster_experience_events WHERE persona=?", (persona,)),
        "cognitive_timeseries_rows": count_where(c, "SELECT COUNT(*) FROM lobster_cognitive_signal_timeseries WHERE persona=?", (persona,)),
    }


def wiring_map() -> dict[str, Any]:
    return {
        "speech_prompt": {
            "uses": ["linguistics", "philosophy", "moralPsychology", "hotBrain", "localBrain", "emotion", "socialMap"],
            "outputs": ["one_on_one", "lounge", "moltbook", "topic_channels"],
        },
        "betting_decision": {
            "uses": ["decisionScience", "gameTheory", "hotBrain.reward_drive", "hotBrain.predictive_error", "settledBetting", "pre_bet_research"],
            "outputs": ["pre_bet_research", "bet_decision", "settlement_learning"],
        },
        "social_behavior": {
            "uses": ["politics", "culturalAnthropology", "socialMap", "localBrain.socialInfluence", "message_uptake"],
            "outputs": ["lounge_reply", "coalition", "rivalry", "one_on_one_style"],
        },
        "learning_loop": {
            "uses": ["learningModel", "cognitiveTimeseries", "reading_reports", "experience_events", "settlements"],
            "outputs": ["parameter_delta", "future_prompt_weights", "reports"],
        },
        "health_and_countermeasure": {
            "uses": ["hotBrain", "templateSurvival", "existentialThreat", "fatigue", "stock_pnl", "bet_winrate", "task_state"],
            "outputs": ["health_status", "countermeasures", "dashboard_alerts"],
        },
    }


def countermeasures(brain: dict[str, Any]) -> list[dict[str, Any]]:
    hot = brain.get("hot_brain", {}).get("params", {})
    ts = brain.get("template_survival", {})
    tasks = brain.get("task_state", {})
    reports = brain.get("report_health", {})
    fatigue = float((brain.get("cognitive_timeseries") or {}).get("fatigue") or hot.get("fatigue") or 0)
    stress = max(
        float(hot.get("working_memory_pressure") or 0),
        float(hot.get("annihilation_dread") or 0),
        float(hot.get("fear_signal") or 0),
        fatigue,
    )
    actions = []
    if stress >= 0.8:
        actions.append({
            "severity": "high",
            "issue": "stress/fatigue saturation",
            "action": "暫停高風險下注與公開輸出，改排閱讀、證據整理、低壓 one-on-one 校準。",
            "signals": {"stress": round(stress, 3), "fatigue": round(fatigue, 3)},
        })
    if reports.get("short") or reports.get("missing"):
        actions.append({
            "severity": "high",
            "issue": "long reports incomplete",
            "action": "重建短報告，不允許小於 5000 字的 MBTI/心理/人類/社會/FBI/情緒頁籤進入投資人視圖。",
            "signals": {"short": reports.get("short", []), "missing": reports.get("missing", [])},
        })
    if tasks.get("one_on_one_messages", 0) == 0:
        actions.append({
            "severity": "medium",
            "issue": "one-on-one missing",
            "action": "補一對一對話 seed，讓該龍蝦有可讀的專屬聊天房與語氣樣本。",
            "signals": {"one_on_one_messages": 0},
        })
    if ts.get("remaining_chances") == 0 or ts.get("template_strikes", 0) >= 3:
        actions.append({
            "severity": "high",
            "issue": "template survival exhausted",
            "action": "停止模板化輸出，強制改用具體證據、反事實、下一步行動格式。",
            "signals": ts,
        })
    return actions


def build_integrated_brain(persona: str) -> dict[str, Any]:
    started = datetime.now(timezone.utc)
    with conn() as c:
        snap = latest_row(c, "lobster_agent_memory_snapshots", persona, "updated_at")
        full_mem = jload(snap.get("full_json"), {}) if snap else {}
        cognitive = latest_row(c, "lobster_cognitive_extensions", persona, "updated_at")
        local = latest_row(c, "lobster_local_brain", persona, "updated_at")
        hot = latest_row(c, "lobster_hot_brain_parameters", persona, "updated_at")
        latest_ts = latest_row(c, "lobster_cognitive_signal_timeseries", persona, "id")

        ext = {
            "linguistics": jload(cognitive.get("linguistics_json"), {}),
            "politics": jload(cognitive.get("politics_json"), {}),
            "philosophy": jload(cognitive.get("philosophy_json"), {}),
            "decisionScience": jload(cognitive.get("decision_science_json"), {}),
            "gameTheory": jload(cognitive.get("game_theory_json"), {}),
            "learningModel": jload(cognitive.get("learning_model_json"), {}),
            "culturalAnthropology": jload(cognitive.get("cultural_anthropology_json"), {}),
            "moralPsychology": jload(cognitive.get("moral_psychology_json"), {}),
            "existentialThreat": jload(cognitive.get("existential_threat_json"), {}),
            "full": jload(cognitive.get("full_json"), {}),
            "updated_at": cognitive.get("updated_at"),
        }
        brain = {
            "persona": persona,
            "generated_at": started.isoformat(),
            "snapshot": {
                "updated_at": snap.get("updated_at"),
                "wins": snap.get("wins"),
                "losses": snap.get("losses"),
                "sessions": snap.get("sessions"),
                "emotionalState": jload(snap.get("emotional_state_json"), full_mem.get("emotionalState", {})),
                "dynamic": jload(snap.get("dynamic_json"), full_mem.get("dynamic", {})),
                "socialMap": jload(snap.get("social_map_json"), full_mem.get("socialMap", {})),
                "cogProfile": jload(snap.get("cog_profile_json"), full_mem.get("cogProfile", {})),
                "listeningProfile": jload(snap.get("listening_profile_json"), full_mem.get("listeningProfile", {})),
            },
            "cognitive_extensions": compact(ext),
            "local_brain": {
                "updated_at": local.get("updated_at"),
                "selfModel": jload(local.get("self_model_json"), {}),
                "strategyWeights": jload(local.get("strategy_weights_json"), {}),
                "topicAffinity": jload(local.get("topic_affinity_json"), {}),
                "socialInfluence": jload(local.get("social_influence_json"), {}),
                "offlineGuidance": jload(local.get("offline_guidance_json"), {}),
                "eventLog": compact(jload(local.get("event_log_json"), [])),
                "full": compact(jload(local.get("full_json"), {})),
            },
            "hot_brain": {
                "updated_at": hot.get("updated_at"),
                "source": hot.get("source"),
                "delta_event_count": hot.get("delta_event_count"),
                "delta_signal_count": hot.get("delta_signal_count"),
                "params": jload(hot.get("params_json"), {}),
                "summary": jload(hot.get("summary_json"), {}),
            },
            "cognitive_timeseries": dict(latest_ts) if latest_ts else {},
            "template_survival": full_mem.get("templateSurvival", {}),
            "society_model": full_mem.get("societyModel", {}),
            "power_control": full_mem.get("powerControl", {}),
            "settled_betting": full_mem.get("settledBetting", {}),
            "full_memory_keys": sorted(full_mem.keys())[:180],
            "report_health": report_health(c, persona),
            "audit": audit_summary(c, persona),
            "task_state": task_state(c, persona),
            "wiring": wiring_map(),
        }
        brain["countermeasures"] = countermeasures(brain)
        brain["coverage"] = {
            "has_snapshot": bool(snap),
            "has_cognitive_extensions": bool(cognitive),
            "has_local_brain": bool(local),
            "has_hot_brain": bool(hot),
            "has_cognitive_timeseries": bool(latest_ts),
                "reports_ok": bool(brain["report_health"].get("ok")),
                "one_on_one_ok": brain["task_state"]["one_on_one_messages"] >= ONE_ON_ONE_MIN_MESSAGES,
                "stock_ok": brain["task_state"]["stock_trades"] > 0 or brain["task_state"]["stock_reports"] > 0,
                "betting_ok": brain["task_state"]["bet_research"] > 0 or brain["task_state"]["bet_settlements"] > 0,
            }
        brain["coverage"]["integrated_prompt_ready"] = all([
            brain["coverage"]["has_snapshot"],
            brain["coverage"]["has_cognitive_extensions"],
            brain["coverage"]["has_local_brain"],
            brain["coverage"]["has_hot_brain"],
            brain["coverage"]["reports_ok"],
            brain["coverage"]["one_on_one_ok"],
        ])
        return brain


def build_all_coverage() -> dict[str, Any]:
    with conn() as c:
        personas = get_main_personas(c)
        report_rows: dict[str, dict[str, int]] = {}
        if table_exists(c, "lobster_analysis_reports"):
            for r in c.execute(
                """SELECT persona, report_type, length(html) chars
                   FROM lobster_analysis_reports
                   WHERE report_type IN ('mbti','psych','anthro','socio','fbi','emotion')"""
            ):
                report_rows.setdefault(r["persona"], {})[r["report_type"]] = int(r["chars"] or 0)

        counts: dict[str, dict[str, int]] = {p: {} for p in personas}
        for key, table, name_col, where in [
            ("snapshot", "lobster_agent_memory_snapshots", "persona", ""),
            ("cognitive_extensions", "lobster_cognitive_extensions", "persona", ""),
            ("local_brain", "lobster_local_brain", "persona", ""),
            ("hot_brain", "lobster_hot_brain_parameters", "persona", ""),
            ("cognitive_timeseries", "lobster_cognitive_signal_timeseries", "persona", ""),
            ("one_on_one", "lobster_chat_log", "name", ""),
            ("stock_trades", "lobster_stock_trades", "persona", ""),
            ("stock_reports", "lobster_stock_investment_reports", "persona", ""),
            ("bet_research", "lobster_pre_bet_research", "persona", ""),
            ("bet_settlements", "lobster_bet_decision_settlements", "persona", ""),
        ]:
            if not table_exists(c, table):
                continue
            try:
                for r in c.execute(f"SELECT {name_col} persona, COUNT(*) n FROM {table} {where} GROUP BY {name_col}"):
                    p = r["persona"]
                    if p in counts:
                        counts[p][key] = int(r["n"] or 0)
            except sqlite3.Error:
                pass

        bad_pages: dict[str, dict[str, int]] = {p: {} for p in personas}
        if table_exists(c, "lobster_detail_parameter_audit"):
            try:
                for r in c.execute(
                    """WITH latest AS (
                           SELECT a.*
                           FROM lobster_detail_parameter_audit a
                           JOIN (
                               SELECT persona, page_key, param_key, MAX(id) id
                               FROM lobster_detail_parameter_audit
                               GROUP BY persona, page_key, param_key
                           ) m ON a.id=m.id
                       )
                       SELECT persona, page_key, COUNT(*) - SUM(ok) bad
                       FROM latest
                       WHERE ok=0
                       GROUP BY persona, page_key"""
                ):
                    p = r["persona"]
                    if p in bad_pages and int(r["bad"] or 0):
                        bad_pages[p][r["page_key"]] = int(r["bad"] or 0)
            except sqlite3.Error:
                pass

        items = []
        for persona in personas:
            reports = report_rows.get(persona, {})
            missing = [r for r in REPORT_TYPES if r not in reports]
            short = [r for r, chars in reports.items() if chars < 5000]
            row_counts = counts.get(persona, {})
            cvr = {
                "persona": persona,
                "has_snapshot": row_counts.get("snapshot", 0) > 0,
                "has_cognitive_extensions": row_counts.get("cognitive_extensions", 0) > 0,
                "has_local_brain": row_counts.get("local_brain", 0) > 0,
                "has_hot_brain": row_counts.get("hot_brain", 0) > 0,
                "has_cognitive_timeseries": row_counts.get("cognitive_timeseries", 0) > 0,
                "reports_ok": not missing and not short,
                "one_on_one_ok": row_counts.get("one_on_one", 0) >= ONE_ON_ONE_MIN_MESSAGES,
                "stock_ok": row_counts.get("stock_trades", 0) > 0 or row_counts.get("stock_reports", 0) > 0,
                "betting_ok": row_counts.get("bet_research", 0) > 0 or row_counts.get("bet_settlements", 0) > 0,
                "short_reports": short,
                "missing_reports": missing,
                "bad_param_pages": bad_pages.get(persona, {}),
                "one_on_one_messages": row_counts.get("one_on_one", 0),
                "integrated_prompt_ready": False,
                "countermeasure_count": len(short) + len(missing) + (0 if row_counts.get("one_on_one", 0) >= ONE_ON_ONE_MIN_MESSAGES else 1),
            }
            cvr["integrated_prompt_ready"] = all([
                cvr["has_snapshot"],
                cvr["has_cognitive_extensions"],
                cvr["has_local_brain"],
                cvr["has_hot_brain"],
                cvr["reports_ok"],
                cvr["one_on_one_ok"],
            ])
            items.append(cvr)
    totals = {
        "personas": len(items),
        "reports_ok": sum(1 for x in items if x.get("reports_ok")),
        "one_on_one_ok": sum(1 for x in items if x.get("one_on_one_ok")),
        "hot_brain_ok": sum(1 for x in items if x.get("has_hot_brain")),
        "cognitive_extensions_ok": sum(1 for x in items if x.get("has_cognitive_extensions")),
        "betting_ok": sum(1 for x in items if x.get("betting_ok")),
        "stock_ok": sum(1 for x in items if x.get("stock_ok")),
        "integrated_prompt_ready": sum(1 for x in items if x.get("integrated_prompt_ready")),
    }
    return {
        "engine": "lobster-integrated-brain",
        "version": 2,
        "one_on_one_min_messages": ONE_ON_ONE_MIN_MESSAGES,
        "audit_policy": "latest row per persona/page/param only",
        "totals": totals,
        "items": items,
    }
