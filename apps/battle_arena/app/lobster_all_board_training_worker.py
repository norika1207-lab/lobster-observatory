#!/usr/bin/env python3
import datetime as dt
import html
import json
import sqlite3
import subprocess

DB_PATH = "/opt/battle_arena/app/battle_arena.db"
GENERATOR = "/opt/battle_arena/app/lobster_multi_board_generator.js"
GOMOKU_WORKER = "/opt/battle_arena/app/lobster_board_training_worker.py"
PERSONAS = [
    "norika_oda", "snaplex", "vortexiq", "pincerbot", "clawtrix",
    "hexclaw", "prismbit", "blazepaw", "ragclaw", "stonefang",
    "gauntlet", "nullpinch", "sparkrill", "mirrorcalm", "basaltrook",
    "brineledger", "emberling", "flarecub", "omenwake", "rushfin",
]


def today():
    return dt.datetime.utcnow().strftime("%Y-%m-%d")


def now():
    return dt.datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


def ensure(con):
    con.executescript(
        """
CREATE TABLE IF NOT EXISTS lobster_game_training_sessions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  day TEXT,
  persona TEXT,
  game TEXT,
  opponent TEXT,
  result TEXT,
  moves_json TEXT DEFAULT '[]',
  lookahead_depth INTEGER,
  self_review TEXT,
  score_delta_json TEXT DEFAULT '{}',
  raw_json TEXT DEFAULT '{}',
  created_at TEXT DEFAULT (datetime('now'))
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


def make_review(game):
    persona = game["persona"]
    opponent = game["opponent"]
    board = game["game"]
    result = game["result"]
    moves = game.get("moves") or []
    first = moves[0] if moves else {}
    own_moves = [m for m in moves if m.get("actor") == persona]
    motive_counts = {}
    for move in own_moves:
        motive_counts[move.get("reason", "unknown")] = motive_counts.get(move.get("reason", "unknown"), 0) + 1
    motive_text = "、".join(f"{k} {v} 次" for k, v in sorted(motive_counts.items())) or "尚無有效動作"
    thesis = {
        "win": "我這局得到優勢，是因為我至少有一段時間不是只追眼前得分，而是讓下一步的選擇比較不容易被對手封死。",
        "loss": "我這局失利，代表我對對手下一步的估計太淺，或是太晚把局部危險提升成必須處理的主線。",
        "draw": "這局接近僵持，表示我守住了一部分威脅，但仍缺少把局面收束成明確優勢的能力。",
    }.get(result, "這局需要重新判讀。")
    first_desc = json.dumps(first, ensure_ascii=False)
    text = (
        f"{persona} 的 {board} 對局自評，對手是 {opponent}，結果是 {result}。{thesis}"
        f"第一個可記錄動作是 {first_desc}。我自己的動作數是 {len(own_moves)}，主要動機分布是：{motive_text}。"
        "這場棋類訓練的意義不是把棋下完而已，而是訓練我在下注、投票、卡牌與辯論前先做一件事：想像對手也會反應。"
        "如果我只看當下最亮的訊號，我會把一個短暫優勢誤認成趨勢；如果我能看見兩步之後可能被反制的位置，我就會更懂得保留資源、等待證據、或承認目前的假設不夠強。"
        "我必須把勝負轉成規則：贏的時候檢查是否只是對手沒有懲罰我，輸的時候找出我最早忽略的威脅，平手的時候問自己是防守合格還是進攻不足。"
        f"本局使用的規則/引擎是 {game.get('engine')}，前瞻深度標記為 {game.get('lookahead_depth')}。"
    )
    while len(text) < 520:
        text += " 下一次我必須先建立候選路線，再比較每條路線被反擊後的代價，而不是只用直覺選一個看起來舒服的位置。"
    return text


def main():
    subprocess.run(["python3", GOMOKU_WORKER, "--limit", "20"], check=False, timeout=50)
    raw = subprocess.check_output(["node", GENERATOR, *PERSONAS], text=True, timeout=60)
    games = json.loads(raw)
    d = today()
    con = sqlite3.connect(DB_PATH, timeout=30)
    con.row_factory = sqlite3.Row
    ensure(con)
    con.execute("UPDATE lobster_ability_source_registry SET integration_status='active' WHERE domain IN ('chess','xiangqi','go','gomoku','memecat')")
    created = 0
    for game in games:
        persona = game["persona"]
        board = game["game"]
        if con.execute(
            "SELECT 1 FROM lobster_game_training_sessions WHERE day=? AND persona=? AND game=?",
            (d, persona, board),
        ).fetchone():
            continue
        review = make_review(game)
        delta = {
            "lookahead_depth": game.get("lookahead_depth", 1),
            "opponent_modeling": 0.03 if board in ("chess", "xiangqi") else 0.02,
            "strategic_patience": 0.02,
            "loss_interpretation": 0.02 if game.get("result") == "loss" else 0.01,
        }
        con.execute(
            """INSERT INTO lobster_game_training_sessions
               (day, persona, game, opponent, result, moves_json, lookahead_depth, self_review, score_delta_json, raw_json)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                d,
                persona,
                board,
                game.get("opponent"),
                game.get("result"),
                json.dumps(game.get("moves", []), ensure_ascii=False),
                game.get("lookahead_depth", 1),
                review,
                json.dumps(delta, ensure_ascii=False),
                json.dumps({k: v for k, v in game.items() if k not in ("moves",)}, ensure_ascii=False),
            ),
        )
        con.execute(
            """INSERT OR REPLACE INTO lobster_analysis_reports
               (persona, report_type, html, source, model, snapshot_json, meta_json, generated_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))""",
            (
                persona,
                f"board_game_self_review_{board}",
                f"<h2>{html.escape(board)} self-review</h2><pre>{html.escape(review)}</pre>",
                "lobster_all_board_training_worker",
                "open-source-board-engines-no-llm",
                json.dumps({"self_review": review, "game": board}, ensure_ascii=False),
                json.dumps({"game": board, "opponent": game.get("opponent"), "result": game.get("result"), "moves": len(game.get("moves", [])), "no_llm": True}, ensure_ascii=False),
                now(),
            ),
        )
        con.execute(
            """INSERT INTO lobster_cognitive_events
               (persona, event_type, source, dimension, summary, delta_json, raw_json, occurred_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                persona,
                "board_game_self_review",
                "lobster_all_board_training_worker",
                "lookahead",
                f"{persona} completed {board} against {game.get('opponent')}: {game.get('result')}",
                json.dumps(delta, ensure_ascii=False),
                json.dumps({"engine": game.get("engine"), "no_llm": True, "moves": game.get("moves", [])[:12]}, ensure_ascii=False),
                now(),
            ),
        )
        created += 1
    con.commit()
    counts = con.execute(
        "SELECT game, COUNT(*) AS c FROM lobster_game_training_sessions WHERE day=? GROUP BY game ORDER BY game",
        (d,),
    ).fetchall()
    con.close()
    print(json.dumps({"ok": True, "day": d, "created": created, "counts": [dict(r) for r in counts]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
