#!/usr/bin/env python3
import datetime as dt
import json
import sqlite3
import time
import urllib.request
from pathlib import Path
import sys as _sys_for_mem_io
if '/root' not in _sys_for_mem_io.path:
    _sys_for_mem_io.path.insert(0, '/root')
from lobster_memory_io import safe_write_json

DB = "/opt/battle_arena/app/battle_arena.db"
MEM = Path("/root/lobster-memory")
API = "http://127.0.0.1:8071"
PERSONAS = [
    "norika_oda", "snaplex", "vortexiq", "pincerbot", "clawtrix",
    "hexclaw", "prismbit", "blazepaw", "ragclaw", "stonefang",
    "gauntlet", "nullpinch", "sparkrill", "mirrorcalm", "basaltrook",
    "brineledger", "emberling", "flarecub", "omenwake", "rushfin",
]
GAMES = ["chess", "xiangqi", "go", "gomoku"]
OPENINGS = {
    "chess": {"from": "e2", "to": "e4"},
    "xiangqi": {"move": "b0c2"},
    "go": {"x": 4, "y": 4},
    "gomoku": {"x": 7, "y": 7},
}
NOW = dt.datetime.utcnow().replace(microsecond=0).isoformat() + "Z"
DAY = dt.datetime.utcnow().strftime("%Y-%m-%d")


def post(path, payload):
    data = json.dumps(payload).encode()
    req = urllib.request.Request(API + path, data=data, headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=8) as res:
        return json.loads(res.read().decode())


def ensure(con):
    con.executescript(
        """
CREATE TABLE IF NOT EXISTS lobster_board_live_assignments (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  day TEXT NOT NULL,
  persona TEXT NOT NULL,
  game TEXT NOT NULL,
  opponent TEXT,
  board_url TEXT NOT NULL,
  session_id TEXT,
  opening_json TEXT NOT NULL DEFAULT '{}',
  session_state_json TEXT NOT NULL DEFAULT '{}',
  status TEXT NOT NULL DEFAULT 'assigned',
  reflection_required INTEGER NOT NULL DEFAULT 1,
  created_at TEXT DEFAULT (datetime('now')),
  updated_at TEXT DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS lobster_cognitive_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  persona TEXT NOT NULL,
  event_type TEXT NOT NULL,
  source TEXT NOT NULL,
  dimension TEXT,
  summary TEXT,
  delta_json TEXT NOT NULL DEFAULT '{}',
  raw_json TEXT NOT NULL DEFAULT '{}',
  occurred_at TEXT,
  created_at TEXT DEFAULT (datetime('now'))
);
"""
    )


def patch_memory(persona, game, session_id):
    path = MEM / f"{persona}.json"
    if not path.exists():
        return False
    data = json.loads(path.read_text(encoding="utf-8"))
    brain = data.setdefault("localBrain", {})
    brain.setdefault("offlineGuidance", [])
    text = (
        f"第一次下棋體驗任務：你被安排去玩 {game}，入口 https://charenix.com/lobster/board-arena，"
        f"session={session_id}。這不是背景訓練；你要把它當成新感官經驗：先確認規則、看棋盤、預測對手回手，"
        "結束後說明你為什麼贏/輸/卡住，以及這和下注、辯論、卡牌決策有什麼關係。"
    )
    brain["offlineGuidance"] = (brain["offlineGuidance"] + [{"ts": NOW, "source": "board_first_experience_assigner", "type": "playable_board_experience", "text": text}])[-120:]
    cog = data.setdefault("cognitiveExtensions", {})
    proc = cog.setdefault("proceduralSkill", {})
    proc.setdefault("boardGameExperience", {})[game] = {
        "firstPlayableSessionAt": NOW,
        "sessionId": session_id,
        "status": "started",
        "reflectionRequired": True,
    }
    data["updatedAt"] = NOW
    safe_write_json(path, data)
    return True


def main():
    con = sqlite3.connect(DB, timeout=60)
    con.execute("PRAGMA busy_timeout=30000")
    ensure(con)
    rows = []
    for i, persona in enumerate(PERSONAS):
        game = GAMES[i % len(GAMES)]
        opponent = PERSONAS[(i + 5) % len(PERSONAS)]
        if con.execute("SELECT 1 FROM lobster_board_live_assignments WHERE day=? AND persona=?", (DAY, persona)).fetchone():
            continue
        state = post("/api/new", {"game": game, "persona": persona})
        session_id = state["id"]
        try:
            state = post("/api/move", {"id": session_id, **OPENINGS[game]})
            status = "started_opening_played"
        except Exception as exc:
            status = "assigned_opening_failed"
            state = {"id": session_id, "error": str(exc), "game": game, "persona": persona}
        patch_memory(persona, game, session_id)
        def retry_execute(sql, params):
            last = None
            for attempt in range(12):
                try:
                    con.execute(sql, params)
                    return
                except sqlite3.OperationalError as exc:
                    last = exc
                    if "locked" not in str(exc).lower():
                        raise
                    time.sleep(1.5 + attempt * 0.5)
            raise last

        retry_execute(
            """INSERT INTO lobster_board_live_assignments
               (day, persona, game, opponent, board_url, session_id, opening_json, session_state_json, status, reflection_required)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1)""",
            (
                DAY,
                persona,
                game,
                opponent,
                "https://charenix.com/lobster/board-arena",
                session_id,
                json.dumps(OPENINGS[game], ensure_ascii=False),
                json.dumps(state, ensure_ascii=False),
                status,
            ),
        )
        retry_execute(
            """INSERT INTO lobster_cognitive_events
               (persona, event_type, source, dimension, summary, delta_json, raw_json, occurred_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                persona,
                "playable_board_first_experience",
                "assign_lobster_board_first_experience",
                "proceduralSkill.boardGameExperience",
                f"{persona} assigned to playable {game}; opening move attempted; reflection required.",
                json.dumps({"curiosity": 0.05, "lookaheadTraining": 0.08, "proceduralNovelty": 0.1}, ensure_ascii=False),
                json.dumps({"game": game, "session_id": session_id, "state": state}, ensure_ascii=False),
                NOW,
            ),
        )
        rows.append({"persona": persona, "game": game, "session_id": session_id, "status": status})
    con.commit()
    con.close()
    print(json.dumps({"ok": True, "day": DAY, "created": len(rows), "assignments": rows}, ensure_ascii=False))


if __name__ == "__main__":
    main()
