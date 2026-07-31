#!/usr/bin/env python3
import json
import os
import sqlite3
import subprocess
from datetime import datetime, timezone
from pathlib import Path

DB = Path("/opt/battle_arena/app/battle_arena.db")
LOG = Path("/var/log/lobster-learning-watchdog.log")
EXPECTED = [
    "hexclaw", "pincerbot", "norika_oda", "vortexiq", "snaplex",
    "prismbit", "clawtrix", "ragclaw", "blazepaw", "stonefang",
    "brineledger", "gauntlet", "emberling", "omenwake", "nullpinch",
    "mirrorcalm", "sparkrill", "rushfin", "flarecub", "basaltrook",
    "ashveil", "tidehook", "mirebyte", "cindersnap", "glintmaw",
    "reefblade", "quartzclaw", "stormledger", "silkbarb", "riftpinch",
]
REQUIRED_TIMERS = [
    "lobster-forced-debate-learning.timer",
    "lobster-quiz-worker.timer",
    "lobster-force-bet-all.timer",
]
AUTO_REPAIR = os.environ.get("LOBSTER_LEARNING_WATCHDOG_AUTO_REPAIR") == "1"


def run(cmd, check=False):
    result = subprocess.run(cmd, text=True, capture_output=True)
    if check and result.returncode != 0:
        raise RuntimeError(f"{' '.join(cmd)} failed: {result.stderr.strip() or result.stdout.strip()}")
    return result


def systemctl(*args):
    return run(["systemctl", *args])


def count_recent(con, event_type, minutes=20):
    rows = con.execute(
        """
        SELECT persona, COUNT(*) c
        FROM lobster_experience_events
        WHERE event_type=?
          AND datetime(created_at) >= datetime('now', ?)
        GROUP BY persona
        """,
        (event_type, f"-{minutes} minutes"),
    ).fetchall()
    counts = {row[0]: int(row[1]) for row in rows}
    missing = [p for p in EXPECTED if p not in counts]
    return counts, missing


def active_state(unit):
    result = systemctl("is-active", unit)
    return result.stdout.strip() or "unknown"


def enabled_state(unit):
    result = systemctl("is-enabled", unit)
    return result.stdout.strip() or "unknown"


def ensure_timers():
    actions = []
    for timer in REQUIRED_TIMERS:
        state = active_state(timer)
        enabled = enabled_state(timer)
        if state != "active":
            if not AUTO_REPAIR:
                actions.append({
                    "timer": timer,
                    "action": "observe_only",
                    "active_state": state,
                    "enabled_state": enabled,
                })
                continue
            if enabled in {"disabled", "masked", "static", "not-found"}:
                actions.append({
                    "timer": timer,
                    "action": "skip_disabled",
                    "active_state": state,
                    "enabled_state": enabled,
                })
                continue
            systemctl("reset-failed", timer)
            systemctl("enable", "--now", timer)
            systemctl("restart", timer)
            actions.append({
                "timer": timer,
                "action": "restart",
                "previous_state": state,
                "enabled_state": enabled,
            })
    return actions


def start_if_not_active(service):
    state = active_state(service)
    if state == "active":
        return "already_active"
    if not AUTO_REPAIR:
        return f"observe_only:{state}"
    systemctl("reset-failed", service)
    result = systemctl("start", service)
    return "started" if result.returncode == 0 else f"start_failed:{result.stderr.strip()}"


def main():
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    report = {"at": now, "ok": True, "timerActions": ensure_timers(), "serviceActions": []}
    if not DB.exists():
        report["ok"] = False
        report["error"] = f"missing db: {DB}"
        LOG.parent.mkdir(parents=True, exist_ok=True)
        with LOG.open("a", encoding="utf-8") as f:
            f.write(json.dumps(report, ensure_ascii=False) + "\n")
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return

    with sqlite3.connect(str(DB), timeout=30) as con:
        math_counts, math_missing = count_recent(con, "math_debate_learning")
        phil_counts, phil_missing = count_recent(con, "philosophy_debate_learning")
        bet_rows = con.execute(
            """
            SELECT persona, COUNT(*) c
            FROM lobster_experience_events
            WHERE event_type IN ('bet_decision','bet_skip')
              AND datetime(created_at) >= datetime('now','-45 minutes')
            GROUP BY persona
            """
        ).fetchall()
        bet_counts = {row[0]: int(row[1]) for row in bet_rows}

    report["learning"] = {
        "mathPersonas": len(math_counts),
        "mathMissing": math_missing,
        "philosophyPersonas": len(phil_counts),
        "philosophyMissing": phil_missing,
    }
    report["betting"] = {
        "attemptPersonas": len(bet_counts),
        "attemptMissing": [p for p in EXPECTED if p not in bet_counts],
    }

    if math_missing or phil_missing:
        report["ok"] = False
        report["serviceActions"].append({
            "service": "lobster-forced-debate-learning.service",
            "reason": "missing_recent_math_or_philosophy_learning",
            "result": start_if_not_active("lobster-forced-debate-learning.service"),
        })

    LOG.parent.mkdir(parents=True, exist_ok=True)
    with LOG.open("a", encoding="utf-8") as f:
        f.write(json.dumps(report, ensure_ascii=False) + "\n")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
