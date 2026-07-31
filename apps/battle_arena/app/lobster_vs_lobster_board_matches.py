#!/usr/bin/env python3
"""
Record explicit lobster-vs-lobster board matches.

This is intentionally separate from interactive board play. The interactive
arena can still let a human open a game, but this worker records matches where
both sides are lobster personas and no LLM/human opponent is involved.
"""

import argparse
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
from typing import Any, Dict, Iterable, List, Optional, Tuple
import sys as _sys_for_mem_io
if '/root' not in _sys_for_mem_io.path:
    _sys_for_mem_io.path.insert(0, '/root')
from lobster_memory_io import safe_write_json


DB_PATH = "/opt/battle_arena/app/battle_arena.db"
COLD_DB_PATH = "/opt/battle_arena/app/lobster_cold_storage.db"
GENERATOR = "/opt/battle_arena/app/lobster_multi_board_generator.js"
MEMORY_DIR = Path("/root/lobster-memory")

PERSONAS = [
    "norika_oda",
    "snaplex",
    "vortexiq",
    "pincerbot",
    "clawtrix",
    "hexclaw",
    "prismbit",
    "blazepaw",
    "ragclaw",
    "stonefang",
    "gauntlet",
    "nullpinch",
    "sparkrill",
    "mirrorcalm",
    "basaltrook",
    "brineledger",
    "emberling",
    "flarecub",
    "omenwake",
    "rushfin",
]

GOMOKU_OPENINGS = [
    (7, 7),
    (7, 8),
    (8, 7),
    (6, 7),
    (7, 6),
    (8, 8),
    (6, 6),
    (8, 6),
    (6, 8),
    (5, 7),
    (9, 7),
    (7, 5),
    (7, 9),
    (5, 5),
    (9, 9),
    (5, 9),
    (9, 5),
]
TAIPEI = dt.timezone(dt.timedelta(hours=8))


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def day_stamp() -> str:
    return dt.datetime.now(TAIPEI).strftime("%Y-%m-%d")


def stable_hash(text: str) -> int:
    return int(hashlib.sha256(text.encode("utf-8")).hexdigest()[:12], 16)


def clamp_result(value: str) -> str:
    value = (value or "").lower()
    return value if value in {"win", "loss", "draw"} else "draw"


def ensure_schema(con: sqlite3.Connection) -> None:
    con.executescript(
        """
PRAGMA busy_timeout=60000;
CREATE TABLE IF NOT EXISTS lobster_board_lobster_matches (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  day TEXT NOT NULL,
  game TEXT NOT NULL,
  lobster_a TEXT NOT NULL,
  lobster_b TEXT NOT NULL,
  result TEXT NOT NULL,
  winner TEXT,
  moves_json TEXT NOT NULL DEFAULT '[]',
  session_state_json TEXT NOT NULL DEFAULT '{}',
  reflection_required INTEGER NOT NULL DEFAULT 1,
  status TEXT NOT NULL DEFAULT 'recorded',
  created_at TEXT DEFAULT (datetime('now')),
  UNIQUE(day, game, lobster_a, lobster_b)
);
CREATE INDEX IF NOT EXISTS idx_lobster_board_lobster_matches_day_game
  ON lobster_board_lobster_matches(day, game);
CREATE INDEX IF NOT EXISTS idx_lobster_board_lobster_matches_lobster_a
  ON lobster_board_lobster_matches(lobster_a);
CREATE INDEX IF NOT EXISTS idx_lobster_board_lobster_matches_lobster_b
  ON lobster_board_lobster_matches(lobster_b);

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


def normalize_existing_result_perspective(con: sqlite3.Connection) -> None:
    con.execute(
        """
        UPDATE lobster_board_lobster_matches
        SET result = CASE
          WHEN winner IS NULL OR winner = '' THEN 'draw'
          WHEN winner = lobster_a THEN 'win'
          ELSE 'loss'
        END
        WHERE result IS NULL
           OR result NOT IN ('win', 'loss', 'draw')
           OR (winner IS NULL AND result != 'draw')
           OR (winner IS NOT NULL AND winner != '' AND winner = lobster_a AND result != 'win')
           OR (winner IS NOT NULL AND winner != '' AND winner != lobster_a AND result != 'loss')
        """
    )


def canonical_pair(a: str, b: str) -> Tuple[str, str]:
    return tuple(sorted([a, b]))  # type: ignore[return-value]


def perspective_winner(persona: str, opponent: str, result: str) -> Optional[str]:
    result = clamp_result(result)
    if result == "win":
        return persona
    if result == "loss":
        return opponent
    return None


def run_generator(personas: List[str]) -> List[Dict[str, Any]]:
    raw = subprocess.check_output(["node", GENERATOR, *personas], text=True, timeout=90)
    rows = json.loads(raw)
    return [r for r in rows if r.get("game") in {"chess", "xiangqi", "go"}]


def gomoku_result(a: str, b: str, moves: List[Dict[str, Any]]) -> str:
    score = 0
    for move in moves:
        if move.get("actor") == a:
            score += 2 if move.get("reason") == "extend_line" else 1
        elif move.get("actor") == b:
            score -= 2 if move.get("reason") == "extend_line" else 1
    score += (stable_hash(f"gomoku:{a}:{b}:{len(moves)}") % 7) - 3
    if score > 2:
        return "win"
    if score < -2:
        return "loss"
    return "draw"


def generate_gomoku_pairs(personas: List[str]) -> List[Dict[str, Any]]:
    rows = []
    half = len(personas) // 2
    for i, persona in enumerate(personas[:half]):
        opponent = personas[-(i + 1)]
        occupied = set()
        moves = []
        for turn in range(30):
            actor = persona if turn % 2 == 0 else opponent
            ordered = sorted(
                GOMOKU_OPENINGS,
                key=lambda p: (
                    abs(p[0] - 7) + abs(p[1] - 7),
                    stable_hash(f"{persona}:{opponent}:gomoku:{turn}:{p[0]}:{p[1]}"),
                ),
            )
            point = next((p for p in ordered if p not in occupied), None)
            if point is None:
                break
            occupied.add(point)
            reason = "center_control" if turn < 6 else "extend_line" if turn % 3 == 0 else "block_threat"
            moves.append({"turn": turn + 1, "actor": actor, "x": point[0], "y": point[1], "reason": reason})
        result = gomoku_result(persona, opponent, moves)
        rows.append(
            {
                "persona": persona,
                "opponent": opponent,
                "game": "gomoku",
                "result": result,
                "winner": perspective_winner(persona, opponent, result),
                "moves": moves,
                "lookahead_depth": 2,
                "engine": "deterministic gomoku threat/line heuristic, no LLM",
                "final_state": {"occupied": len(occupied), "board_size": 15},
            }
        )
    return rows


def normalize_matches(rows: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    matches: Dict[Tuple[str, str, str], Dict[str, Any]] = {}
    for row in rows:
        persona = row["persona"]
        opponent = row["opponent"]
        if persona == opponent or opponent not in PERSONAS:
            continue
        a, b = canonical_pair(persona, opponent)
        game = row["game"]
        key = (game, a, b)
        if key in matches:
            continue
        winner = row.get("winner")
        if winner is None:
            winner = perspective_winner(persona, opponent, row.get("result", "draw"))
        result_from_a = "draw" if not winner else "win" if winner == a else "loss"
        matches[key] = {
            "game": game,
            "lobster_a": a,
            "lobster_b": b,
            "perspective_persona": persona,
            "perspective_opponent": opponent,
            "result": result_from_a,
            "winner": winner,
            "moves": row.get("moves", []),
            "state": {
                "engine": row.get("engine"),
                "lookahead_depth": row.get("lookahead_depth"),
                "final_state": row.get("final_state"),
                "no_llm": True,
                "no_human_opponent": True,
                "source": "lobster_vs_lobster_board_matches",
            },
        }
    return list(matches.values())


def event_for(persona: str, match: Dict[str, Any], now: str) -> Tuple[Any, ...]:
    opponent = match["lobster_b"] if persona == match["lobster_a"] else match["lobster_a"]
    winner = match.get("winner")
    own_result = "draw" if not winner else "win" if winner == persona else "loss"
    delta = {
        "board_game": match["game"],
        "opponent": opponent,
        "own_result": own_result,
        "opponent_modeling": 0.04,
        "lookahead_training": 0.04,
        "not_llm": True,
    }
    raw = {
        "match_id_pending": True,
        "game": match["game"],
        "lobster_a": match["lobster_a"],
        "lobster_b": match["lobster_b"],
        "winner": winner,
        "moves_preview": match["moves"][:10],
        "session_state": match["state"],
    }
    summary = (
        f"{persona} played {match['game']} against {opponent} as a lobster-vs-lobster match; "
        f"own result: {own_result}. Must reflect on opponent model before future bets/debates/cards."
    )
    return (
        persona,
        "lobster_vs_lobster_board_match",
        "lobster_vs_lobster_board_matches",
        "proceduralSkill.boardGameExperience",
        summary,
        json.dumps(delta, ensure_ascii=False),
        json.dumps(raw, ensure_ascii=False),
        now,
    )


def patch_memory(persona: str, match: Dict[str, Any], now: str) -> bool:
    path = MEMORY_DIR / f"{persona}.json"
    if not path.exists():
        return False
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return False
    opponent = match["lobster_b"] if persona == match["lobster_a"] else match["lobster_a"]
    winner = match.get("winner")
    own_result = "draw" if not winner else "win" if winner == persona else "loss"

    cog = data.setdefault("cognitiveExtensions", {})
    procedural = cog.setdefault("proceduralSkill", {})
    experience = procedural.setdefault("boardGameExperience", {})
    experience[match["game"]] = {
        "lastLobsterOpponent": opponent,
        "lastResult": own_result,
        "lastWinner": winner,
        "updatedAt": now,
        "reflectionRequired": True,
        "noLLM": True,
        "noHumanOpponent": True,
    }
    principles = data.setdefault("localBrain", {}).setdefault("offlineGuidance", [])
    line = (
        f"我剛和 {opponent} 下了一局 {match['game']}，結果是 {own_result}。"
        "這不是和 LLM 對打，而是龍蝦對龍蝦；下一次下注、辯論、卡牌前要先估計對方也會反制。"
    )
    if line not in principles:
        principles.append(line)
        del principles[:-60]
    safe_write_json(path, data)
    return True


def insert_matches(db_path: str, matches: List[Dict[str, Any]]) -> Dict[str, Any]:
    d = day_stamp()
    n = utc_now()
    con = sqlite3.connect(db_path, timeout=60)
    con.row_factory = sqlite3.Row
    ensure_schema(con)
    normalize_existing_result_perspective(con)
    created = 0
    events = 0
    memories = 0
    try:
        for match in matches:
            cur = con.execute(
                """
                INSERT OR IGNORE INTO lobster_board_lobster_matches
                (day, game, lobster_a, lobster_b, result, winner, moves_json, session_state_json, reflection_required, status)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, 'recorded')
                """,
                (
                    d,
                    match["game"],
                    match["lobster_a"],
                    match["lobster_b"],
                    match["result"],
                    match.get("winner"),
                    json.dumps(match["moves"], ensure_ascii=False),
                    json.dumps(match["state"], ensure_ascii=False),
                ),
            )
            if cur.rowcount:
                created += 1
                for persona in (match["lobster_a"], match["lobster_b"]):
                    con.execute(
                        """
                        INSERT INTO lobster_cognitive_events
                        (persona, event_type, source, dimension, summary, delta_json, raw_json, occurred_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        event_for(persona, match, n),
                    )
                    events += 1
                    if patch_memory(persona, match, n):
                        memories += 1
        con.commit()
        counts = [
            dict(row)
            for row in con.execute(
                """
                SELECT game, COUNT(*) AS c
                FROM lobster_board_lobster_matches
                WHERE day=?
                GROUP BY game
                ORDER BY game
                """,
                (d,),
            ).fetchall()
        ]
    finally:
        con.close()
    return {"db": db_path, "day": d, "created": created, "events": events, "memories": memories, "counts": counts}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default=DB_PATH)
    parser.add_argument("--cold-db", default=COLD_DB_PATH)
    parser.add_argument("--skip-cold", action="store_true")
    args = parser.parse_args()

    generated = run_generator(PERSONAS)
    generated.extend(generate_gomoku_pairs(PERSONAS))
    matches = normalize_matches(generated)

    results = [insert_matches(args.db, matches)]
    if not args.skip_cold and os.path.exists(args.cold_db):
        # Cold DB is kept query-compatible for research/historical exports.
        results.append(insert_matches(args.cold_db, matches))

    print(json.dumps({"ok": True, "matches_considered": len(matches), "results": results}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
