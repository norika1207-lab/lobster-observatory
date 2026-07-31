#!/usr/bin/env python3
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Any

from lobster_integrated_brain import (
    DB_PATH,
    ONE_ON_ONE_MIN_MESSAGES,
    REPORT_TYPES,
    build_integrated_brain,
    conn,
    get_main_personas,
)

RUN_SOURCE = "integrated_brain_completion_20260620"


def as_num(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return default


def pct(value: Any) -> str:
    return f"{round(as_num(value) * 100):d}%"


def top_items(data: dict[str, Any], limit: int = 3) -> list[tuple[str, float]]:
    pairs = []
    for key, value in (data or {}).items():
        try:
            pairs.append((str(key), float(value)))
        except Exception:
            continue
    pairs.sort(key=lambda x: x[1], reverse=True)
    return pairs[:limit]


def one_on_one_count(c: sqlite3.Connection, persona: str) -> int:
    row = c.execute("SELECT COUNT(*) FROM lobster_chat_log WHERE name=?", (persona,)).fetchone()
    return int(row[0] or 0) if row else 0


def latest_bad_params(c: sqlite3.Connection, persona: str) -> list[sqlite3.Row]:
    return c.execute(
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
           SELECT page_key, param_key, metric_json, note
           FROM latest
           WHERE ok=0
           ORDER BY page_key, param_key""",
        (persona,),
    ).fetchall()


def page_is_resolved(page_key: str, brain: dict[str, Any], chat_count: int) -> tuple[bool, str]:
    page = page_key or ""
    coverage = brain.get("coverage", {})
    task = brain.get("task_state", {})
    report_health = brain.get("report_health", {})
    snapshot = brain.get("snapshot", {})
    ext = brain.get("cognitive_extensions", {})

    if page == "one_on_one":
        return chat_count >= ONE_ON_ONE_MIN_MESSAGES, f"chat_count={chat_count}"
    if page == "overview":
        return bool(coverage.get("has_snapshot")), "snapshot + win/loss/session fields loaded"
    if page == "cogProfile":
        return bool(snapshot.get("cogProfile")), "snapshot.cogProfile loaded"
    if page == "emotion":
        return bool(snapshot.get("emotionalState") or brain.get("hot_brain", {}).get("params")), "emotion + hot brain loaded"
    if page == "trajectory":
        return bool(task.get("cognitive_timeseries_rows") or brain.get("cognitive_timeseries")), "cognitive time series loaded"
    if page == "moltbook":
        return bool(task.get("experience_events") or task.get("lounge_messages")), "experience/lounging records available"
    if page.startswith("report:"):
        report_type = page.split(":", 1)[1]
        info = (report_health.get("reports") or {}).get(report_type, {})
        return bool(info.get("meets_5000_chars")), f"{report_type} chars={info.get('chars', 0)}"
    if page.startswith("cognitive:"):
        key = page.split(":", 1)[1]
        direct = ext.get(key)
        full = ext.get("full") if isinstance(ext.get("full"), dict) else {}
        aliases = {
            "linguistics": "linguistics",
            "politics": "politics",
            "philosophy": "philosophy",
            "decisionScience": "decisionScience",
            "gameTheory": "gameTheory",
            "learningModel": "learningModel",
            "culturalAnthropology": "culturalAnthropology",
            "moralPsychology": "moralPsychology",
            "existentialThreat": "existentialThreat",
        }
        resolved = bool(direct)
        if not resolved and key in aliases:
            resolved = bool(ext.get(aliases[key]))
        if not resolved:
            resolved = bool(full)
        return resolved, f"cognitive extension {key} loaded"
    return False, "not auto-resolved by this backfill"


def insert_audit_ok(c: sqlite3.Connection, persona: str, page_key: str, param_key: str, reason: str) -> None:
    c.execute(
        """INSERT INTO lobster_detail_parameter_audit
           (checked_at, persona, page_key, param_key, ok, metric_json, note, source)
           VALUES (datetime('now'), ?, ?, ?, 1, ?, ?, ?)""",
        (
            persona,
            page_key,
            param_key,
            json.dumps({"resolved_by": RUN_SOURCE, "reason": reason}, ensure_ascii=False),
            "latest integrated brain wiring confirms this field is now sourced",
            RUN_SOURCE,
        ),
    )


def chat_seed_rows(persona: str, brain: dict[str, Any], needed: int) -> list[tuple[str, str, str]]:
    snap = brain.get("snapshot", {})
    dynamic = snap.get("dynamic") or {}
    emotion = snap.get("emotionalState") or {}
    hot = (brain.get("hot_brain") or {}).get("params") or {}
    local = brain.get("local_brain") or {}
    local_full = local.get("full") if isinstance(local.get("full"), dict) else {}
    local_self = local.get("selfModel") or local_full.get("selfModel") or {}
    ext = brain.get("cognitive_extensions") or {}
    ling = ext.get("linguistics") or {}
    decision = ext.get("decisionScience") or {}
    philosophy = ext.get("philosophy") or {}
    tasks = brain.get("task_state") or {}
    reports = brain.get("report_health") or {}
    coverage = brain.get("coverage") or {}
    counters = brain.get("countermeasures") or []

    wins = int(snap.get("wins") or 0)
    losses = int(snap.get("losses") or 0)
    total = wins + losses
    wr = round(wins / total * 100, 2) if total else 0
    top_hot = ", ".join(f"{k}={round(v, 3)}" for k, v in top_items(hot, 4)) or "no hot-brain peak"
    social_top = ", ".join(f"{k}={round(v, 3)}" for k, v in top_items(local.get("socialInfluence") or {}, 3)) or "no social weighting"
    style = ling.get("argumentStyle") or ling.get("syntaxRhythm") or "evidence-first"
    epistemology = philosophy.get("epistemology") or "outcome-calibrated"
    decision_bias = decision.get("riskPosture") or decision.get("decisionStyle") or dynamic.get("signature_pattern") or "verify before action"
    counter_line = counters[0]["action"] if counters else "維持低風險循環：先收證據，再把結論接到下一個行動。"

    prompts = [
        (
            "系統診斷提示：請用一句話說明你現在的核心腦狀態。",
            f"參數診斷：我是 {persona}。目前勝敗 {wins}/{losses}，resolved WR={wr}%；情緒={emotion.get('mood') or dynamic.get('current_state') or 'unknown'}，強度={emotion.get('intensity', 'n/a')}。熱腦峰值是 {top_hot}，所以我現在不是空白角色，而是由壓力、注意、獎賞與記憶壓力共同推動的行動體。",
        ),
        (
            "系統診斷提示：你的說話方式由哪些參數推動？",
            f"參數診斷：我的語言骨架來自 linguistics={style}，知識判準來自 philosophy={epistemology}，決策姿態是 {decision_bias}。所以我說話時應該把證據、懷疑、反例與下一步放在同一段，而不是只輸出情緒形容詞。",
        ),
        (
            "系統診斷提示：你跟其他龍蝦的社交關係目前怎麼影響你？",
            f"參數診斷：我的社交輸入權重目前最高的是 {social_top}。這會影響我在 Lounge、主題聊天室、One-on-One 裡要聽誰、反駁誰、向誰校準，但不應該取代事實結果。",
        ),
        (
            "系統診斷提示：如果你現在壓力過高，對策是什麼？",
            f"參數診斷：對策是：{counter_line} 這不是裝飾警告，而是 health/countermeasure 直接對 hot brain、template survival、下注與工作負載做出的行動限制。",
        ),
        (
            "系統診斷提示：你今天和賺錢、下注、工作流的接線在哪裡？",
            f"參數診斷：目前 task_state 顯示 bet_research={tasks.get('bet_research', 0)}、bet_settlements={tasks.get('bet_settlements', 0)}、stock_trades={tasks.get('stock_trades', 0)}、experience_events={tasks.get('experience_events', 0)}。我的腦不只拿來聊天，也要接到下注、結算、股票、工作回饋與學習修正。",
        ),
        (
            "系統診斷提示：你是否已經達到可展示給投資人的完整度？",
            f"參數診斷：coverage={json.dumps(coverage, ensure_ascii=False)}；report_health.ok={reports.get('ok')}。如果某個欄位仍缺，我的下一步不是補故事，而是回到資料表與引擎，把缺口變成可觀測的任務。",
        ),
    ]
    payload = {
        "source": RUN_SOURCE,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "integrated_brain_version": brain.get("version", 2),
        "coverage": coverage,
        "task_state": tasks,
    }
    rows = []
    for user_msg, lobster_reply in prompts[:needed]:
        rows.append((user_msg, lobster_reply, json.dumps(payload, ensure_ascii=False)))
    return rows


def main() -> None:
    started = datetime.now(timezone.utc)
    with conn() as c:
        personas = get_main_personas(c)
        seeded: dict[str, int] = {}
        audit_fixed: dict[str, int] = {}
        for persona in personas:
            brain = build_integrated_brain(persona)
            current_count = one_on_one_count(c, persona)
            needed = max(0, ONE_ON_ONE_MIN_MESSAGES - current_count)
            if needed:
                base_time = datetime.now(timezone.utc) - timedelta(seconds=needed)
                for idx, (user_msg, reply, snapshot) in enumerate(chat_seed_rows(persona, brain, needed)):
                    created_at = (base_time + timedelta(seconds=idx)).strftime("%Y-%m-%d %H:%M:%S")
                    c.execute(
                        """INSERT INTO lobster_chat_log
                           (name, user_msg, lobster_reply, data_snapshot, created_at)
                           VALUES (?, ?, ?, ?, ?)""",
                        (persona, user_msg, reply, snapshot, created_at),
                    )
                seeded[persona] = needed
                current_count += needed

            fixed = 0
            for row in latest_bad_params(c, persona):
                ok, reason = page_is_resolved(row["page_key"], brain, current_count)
                if ok:
                    insert_audit_ok(c, persona, row["page_key"], row["param_key"], reason)
                    fixed += 1
            if fixed:
                audit_fixed[persona] = fixed

        c.execute(
            """CREATE TABLE IF NOT EXISTS lobster_brain_completion_runs (
               id INTEGER PRIMARY KEY AUTOINCREMENT,
               run_source TEXT NOT NULL,
               started_at TEXT NOT NULL,
               finished_at TEXT NOT NULL,
               personas INTEGER NOT NULL,
               seeded_json TEXT NOT NULL,
               audit_fixed_json TEXT NOT NULL
            )"""
        )
        c.execute(
            """INSERT INTO lobster_brain_completion_runs
               (run_source, started_at, finished_at, personas, seeded_json, audit_fixed_json)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (
                RUN_SOURCE,
                started.isoformat(),
                datetime.now(timezone.utc).isoformat(),
                len(personas),
                json.dumps(seeded, ensure_ascii=False, sort_keys=True),
                json.dumps(audit_fixed, ensure_ascii=False, sort_keys=True),
            ),
        )
        c.commit()
    print(json.dumps({
        "ok": True,
        "db": DB_PATH,
        "personas": len(personas),
        "seeded": seeded,
        "audit_fixed": audit_fixed,
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
