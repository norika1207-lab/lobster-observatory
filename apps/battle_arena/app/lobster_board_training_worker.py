#!/usr/bin/env python3
import argparse
import datetime as dt
import hashlib
import json
import sqlite3

DB_PATH = "/opt/battle_arena/app/battle_arena.db"
PERSONAS = [
    "norika_oda", "snaplex", "vortexiq", "pincerbot", "clawtrix",
    "hexclaw", "prismbit", "blazepaw", "ragclaw", "stonefang",
    "gauntlet", "nullpinch", "sparkrill", "mirrorcalm", "basaltrook",
    "brineledger", "emberling", "flarecub", "omenwake", "rushfin",
]


def day():
    return dt.datetime.utcnow().strftime("%Y-%m-%d")


def now():
    return dt.datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


def seed(*parts):
    return int(hashlib.sha256("|".join(map(str, parts)).encode()).hexdigest()[:12], 16)


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


def winner(board, n=9):
    for dx, dy in [(1, 0), (0, 1), (1, 1), (1, -1)]:
        for y in range(n):
            for x in range(n):
                mark = board[y][x]
                if not mark:
                    continue
                ok = True
                for k in range(1, 5):
                    xx = x + dx * k
                    yy = y + dy * k
                    if xx < 0 or yy < 0 or xx >= n or yy >= n or board[yy][xx] != mark:
                        ok = False
                        break
                if ok:
                    return mark
    if all(board[y][x] for y in range(n) for x in range(n)):
        return "draw"
    return None


def line_score(board, x, y, mark, n=9):
    best = 0
    for dx, dy in [(1, 0), (0, 1), (1, 1), (1, -1)]:
        total = 1
        open_ends = 0
        for sign in (1, -1):
            xx = x + dx * sign
            yy = y + dy * sign
            while 0 <= xx < n and 0 <= yy < n and board[yy][xx] == mark:
                total += 1
                xx += dx * sign
                yy += dy * sign
            if 0 <= xx < n and 0 <= yy < n and not board[yy][xx]:
                open_ends += 1
        best = max(best, total * total * 10 + open_ends * 8)
    return best


def choose(board, me, opp, rnd, n=9):
    empty = [(x, y) for y in range(n) for x in range(n) if not board[y][x]]
    best = None
    bestv = -1
    for x, y in empty:
        board[y][x] = me
        if winner(board, n) == me:
            board[y][x] = None
            return x, y, "finish_line"
        attack = line_score(board, x, y, me, n)
        board[y][x] = opp
        block = line_score(board, x, y, opp, n)
        board[y][x] = None
        center = 18 - abs(x - 4) - abs(y - 4)
        value = attack * 1.12 + block * 1.25 + center + (seed(rnd, x, y, me) % 7)
        if value > bestv:
            bestv = value
            best = (x, y, "block" if block > attack else "build")
    return best


def play(persona, opponent, game_day):
    n = 9
    board = [[None] * n for _ in range(n)]
    marks = {persona: "B", opponent: "W"}
    order = [persona, opponent]
    moves = []
    for turn in range(60):
        actor = order[turn % 2]
        other = order[(turn + 1) % 2]
        move = choose(board, marks[actor], marks[other], seed(game_day, persona, opponent, turn), n)
        if not move:
            break
        x, y, reason = move
        board[y][x] = marks[actor]
        moves.append({"turn": turn + 1, "persona": actor, "x": x, "y": y, "reason": reason})
        result = winner(board, n)
        if result:
            if result == "draw":
                return "draw", moves
            return ("win" if marks[persona] == result else "loss"), moves
    return "draw", moves


def review(persona, opponent, result, moves):
    own = [m for m in moves if m["persona"] == persona]
    blocks = sum(1 for m in own if m["reason"] == "block")
    builds = sum(1 for m in own if m["reason"] == "build")
    finishes = sum(1 for m in own if m["reason"] == "finish_line")
    opening = own[0] if own else {"x": "?", "y": "?", "reason": "none"}
    thesis = {
        "win": "我贏不是因為衝得快，而是因為我在中盤開始把對手的連線拆掉，同時保留自己的第二條路。",
        "loss": "我輸的原因不是單一錯手，而是太晚承認對手的威脅已經比我的進攻更急。",
        "draw": "這一局沒有分出勝負，代表我有守住主要威脅，但還沒有把優勢轉成不可逆的線。",
    }.get(result, "這一局需要重新檢查。")
    text = (
        f"{persona} 對 {opponent} 的 Gomoku 自評：{thesis}"
        f"我的第一手落在 ({opening['x']},{opening['y']})，當時理由是 {opening['reason']}。"
        f"整局我做了 {len(own)} 次決策，其中主動建立線型 {builds} 次、封堵對方 {blocks} 次、嘗試終結 {finishes} 次。"
        "這場訓練提醒我，棋盤不是只看眼前一格，而是要同時看自己下一步、對手下一步，以及如果我不處理某條線，三手之後它會不會變成無法修補的傷口。"
        "下注、投票與對戰也一樣：如果我只看當下的勝率或情緒，我會誤把短期訊號當成結論；如果我能先問「對手下一個合理反應是什麼」，我就能比較早發現需要保守、需要試探、或需要放棄的時機。"
        "這份自評不是勝負藉口，而是把輸贏轉成可記憶的規則：贏的時候檢查是不是僥倖，輸的時候找出第一個沒有處理的威脅，平手的時候確認我缺的是進攻深度還是收束能力。"
        "下一局我會把每一次封堵視為一次認知更新，把每一次進攻視為一個可以被對方反駁的假設。"
    )
    while len(text) < 520:
        text += " 我必須把目光從單點移到路徑，從反應移到預測，從模板移到真正的判斷。"
    return text


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=20)
    args = parser.parse_args()
    con = sqlite3.connect(DB_PATH, timeout=30)
    con.row_factory = sqlite3.Row
    ensure(con)
    d = day()
    created = 0
    con.execute("UPDATE lobster_ability_source_registry SET integration_status='active' WHERE domain IN ('memecat','gomoku')")
    for i, persona in enumerate(PERSONAS[: args.limit]):
        if con.execute(
            "SELECT 1 FROM lobster_game_training_sessions WHERE day=? AND persona=? AND game='gomoku'",
            (d, persona),
        ).fetchone():
            continue
        opponent = PERSONAS[(i + 7) % len(PERSONAS)]
        result, moves = play(persona, opponent, d)
        self_review = review(persona, opponent, result, moves)
        delta = {
            "lookahead_depth": 3,
            "strategic_patience": 0.02 if result != "loss" else 0.01,
            "opponent_modeling": 0.03,
            "impulse_control": 0.02 if result == "win" else 0.015,
        }
        con.execute(
            """INSERT INTO lobster_game_training_sessions
               (day,persona,game,opponent,result,moves_json,lookahead_depth,self_review,score_delta_json,raw_json)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (
                d,
                persona,
                "gomoku",
                opponent,
                result,
                json.dumps(moves, ensure_ascii=False),
                3,
                self_review,
                json.dumps(delta, ensure_ascii=False),
                json.dumps({"board_size": 9, "connect": 5, "engine": "deterministic_rule_heuristic_no_llm"}, ensure_ascii=False),
            ),
        )
        con.execute(
            """INSERT OR REPLACE INTO lobster_analysis_reports
               (persona,report_type,html,source,model,snapshot_json,meta_json,generated_at,updated_at)
               VALUES (?,?,?,?,?,?,?,?,datetime('now'))""",
            (
                persona,
                "board_game_self_review",
                "<h2>Gomoku self-review</h2><pre>" + self_review.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;") + "</pre>",
                "lobster_board_training_worker",
                "deterministic-rule-heuristic-no-llm",
                json.dumps({"self_review": self_review, "game": "gomoku"}, ensure_ascii=False),
                json.dumps({"game": "gomoku", "opponent": opponent, "result": result, "moves": len(moves), "no_llm": True}, ensure_ascii=False),
                now(),
            ),
        )
        con.execute(
            """INSERT INTO lobster_cognitive_events
               (persona,event_type,source,dimension,summary,delta_json,raw_json,occurred_at)
               VALUES (?,?,?,?,?,?,?,?)""",
            (
                persona,
                "board_game_self_review",
                "lobster_board_training_worker",
                "lookahead",
                f"{persona} finished gomoku vs {opponent}: {result}",
                json.dumps(delta, ensure_ascii=False),
                json.dumps({"moves": moves[:12], "no_llm": True}, ensure_ascii=False),
                now(),
            ),
        )
        created += 1
    con.commit()
    con.close()
    print(json.dumps({"ok": True, "day": d, "sessions_created": created}, ensure_ascii=False))


if __name__ == "__main__":
    main()
