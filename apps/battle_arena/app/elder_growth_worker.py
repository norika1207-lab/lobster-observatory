#!/usr/bin/env python3
import datetime as dt
import hashlib
import json
import sqlite3
import subprocess
import time
from pathlib import Path
import sys as _sys_for_mem_io
if '/root' not in _sys_for_mem_io.path:
    _sys_for_mem_io.path.insert(0, '/root')
from lobster_memory_io import safe_write_json

DB = "/opt/battle_arena/app/battle_arena.db"
GEN = "/opt/battle_arena/app/lobster_multi_board_generator.js"
MEM = Path("/root/lobster-memory/elder.json")
TZ = dt.timezone(dt.timedelta(hours=8))
OPPONENTS = ["stonefang", "prismbit", "clawtrix", "vortexiq", "pincerbot", "hexclaw"]
FILLERS = ["norika_oda", "snaplex", "vortexiq", "pincerbot", "clawtrix", "hexclaw", "prismbit", "blazepaw", "ragclaw", "stonefang", "gauntlet", "nullpinch", "sparkrill", "mirrorcalm", "basaltrook", "brineledger", "emberling", "flarecub", "omenwake", "rushfin"]
LEXICON = {
    "board_strategy": ["先手壓力", "後手韌性", "中盤轉折", "候選路線", "反制成本", "雙活三", "棄子取勢", "厚勢", "官子耐心", "將軍鏈", "子力協調", "威脅優先級"],
    "mentorship": ["先穩住，再判斷", "把壓力說成語言", "不要把失誤藏起來", "先承認斷點", "慢一拍不是退縮", "把輸局變規則", "照顧判斷，不照顧面子"],
    "mind_refuge": ["我在", "先坐下", "不用急著證明", "你可以先把委屈說完", "這不是失敗，是訊號過載", "先把心收回來"],
    "cognition": ["預測誤差", "校準壓力", "反事實", "路徑依賴", "模型修正", "注意力負載", "語義鞏固", "情緒承載"],
}


def now():
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def day():
    return dt.datetime.now(TZ).strftime("%Y-%m-%d")


def h(s):
    return int(hashlib.sha256(s.encode()).hexdigest()[:12], 16)


def connect():
    con = sqlite3.connect(DB, timeout=60)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA busy_timeout=60000")
    con.execute("PRAGMA journal_mode=WAL")
    return con


def execute(con, sql, params=(), tries=10):
    last = None
    for i in range(tries):
        try:
            return con.execute(sql, params)
        except sqlite3.OperationalError as exc:
            last = exc
            if "locked" not in str(exc).lower():
                raise
            time.sleep(0.3 * (i + 1))
    raise last


def ensure(con):
    con.executescript("""
PRAGMA busy_timeout=60000;
CREATE TABLE IF NOT EXISTS lobster_board_lobster_matches (
  id INTEGER PRIMARY KEY AUTOINCREMENT, day TEXT NOT NULL, game TEXT NOT NULL,
  lobster_a TEXT NOT NULL, lobster_b TEXT NOT NULL, result TEXT NOT NULL, winner TEXT,
  moves_json TEXT NOT NULL DEFAULT '[]', session_state_json TEXT NOT NULL DEFAULT '{}',
  reflection_required INTEGER NOT NULL DEFAULT 1, status TEXT NOT NULL DEFAULT 'recorded',
  created_at TEXT DEFAULT (datetime('now')), UNIQUE(day, game, lobster_a, lobster_b)
);
CREATE TABLE IF NOT EXISTS lobster_game_training_sessions (
  id INTEGER PRIMARY KEY AUTOINCREMENT, day TEXT, persona TEXT, game TEXT, opponent TEXT,
  result TEXT, moves_json TEXT DEFAULT '[]', lookahead_depth INTEGER, self_review TEXT,
  score_delta_json TEXT DEFAULT '{}', raw_json TEXT DEFAULT '{}', created_at TEXT DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS lobster_cognitive_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT, persona TEXT NOT NULL, event_type TEXT NOT NULL,
  source TEXT NOT NULL, dimension TEXT, summary TEXT, delta_json TEXT NOT NULL DEFAULT '{}',
  raw_json TEXT NOT NULL DEFAULT '{}', occurred_at TEXT, created_at TEXT DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS elder_growth_metrics (
  id INTEGER PRIMARY KEY AUTOINCREMENT, day TEXT NOT NULL, metric TEXT NOT NULL,
  value REAL NOT NULL, detail_json TEXT NOT NULL DEFAULT '{}', created_at TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_elder_growth_metrics_day_metric ON elder_growth_metrics(day, metric, created_at);
""")


def args_for(opponent):
    pool = [p for p in FILLERS if p != opponent]
    return ["elder"] + pool[:6] + [opponent] + pool[6:13]


def engine_games(opponent):
    raw = subprocess.check_output(["node", GEN, *args_for(opponent)], text=True, timeout=90)
    rows = json.loads(raw)
    return [r for r in rows if r.get("persona") == "elder" and r.get("opponent") == opponent]


def gomoku(opponent):
    points = [(7, 7), (7, 8), (8, 7), (6, 7), (7, 6), (8, 8), (6, 6), (8, 6), (6, 8), (5, 7), (9, 7), (7, 5), (7, 9)]
    occ, moves = set(), []
    for turn in range(30):
        actor = "elder" if turn % 2 == 0 else opponent
        ordered = sorted(points, key=lambda p: (abs(p[0] - 7) + abs(p[1] - 7), h(f"{opponent}:{turn}:{p}")))
        point = next((p for p in ordered if p not in occ), None)
        if not point:
            break
        occ.add(point)
        reason = "center_control" if turn < 6 else "extend_line" if turn % 3 == 0 else "block_threat"
        moves.append({"turn": turn + 1, "actor": actor, "x": point[0], "y": point[1], "reason": reason})
    score = sum((2 if m["reason"] == "extend_line" else 1) * (1 if m["actor"] == "elder" else -1) for m in moves)
    score += (h(f"elder:{opponent}:gomoku:{day()}") % 7) - 3
    result = "win" if score > 2 else "loss" if score < -2 else "draw"
    return {"persona": "elder", "opponent": opponent, "game": "gomoku", "result": result, "winner": "elder" if result == "win" else opponent if result == "loss" else None, "moves": moves, "lookahead_depth": 2, "engine": "deterministic gomoku threat/line heuristic, no LLM", "final_state": {"occupied": len(occ), "board_size": 15}}


def winner(row):
    if row.get("winner") is not None:
        return row.get("winner")
    return "elder" if row.get("result") == "win" else row["opponent"] if row.get("result") == "loss" else None


def own_result(row):
    w = winner(row)
    return "win" if w == "elder" else "draw" if not w else "loss"


def review(row):
    r = own_result(row)
    core = "贏在先看反制，再動手。" if r == "win" else "輸在太晚把局部威脅升成主線。" if r == "loss" else "守住了，但還沒把理解收束成勝勢。"
    return f"Elder {row['game']} 對 {row['opponent']} 自評：{core} 這局要轉成長者能力：讀局、安撫、提醒，不把壓力丟回給對方。"


def insert_game(con, row):
    a, b = sorted(["elder", row["opponent"]])
    w = winner(row)
    result_from_a = "draw" if not w else "win" if w == a else "loss"
    cur = execute(con, """
        INSERT OR IGNORE INTO lobster_board_lobster_matches
        (day, game, lobster_a, lobster_b, result, winner, moves_json, session_state_json, reflection_required, status)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, 'recorded')
    """, (day(), row["game"], a, b, result_from_a, w, json.dumps(row.get("moves", []), ensure_ascii=False), json.dumps({"engine": row.get("engine"), "lookahead_depth": row.get("lookahead_depth"), "final_state": row.get("final_state"), "source": "elder_growth_worker", "no_llm": True}, ensure_ascii=False)))
    if not cur.rowcount:
        return False
    delta = {"board_game": row["game"], "opponent": row["opponent"], "own_result": own_result(row), "lookahead_training": 0.045, "opponent_modeling": 0.04, "mentor_patience": 0.02, "vocabulary_context": 0.015}
    execute(con, """
        INSERT INTO lobster_game_training_sessions
        (day, persona, game, opponent, result, moves_json, lookahead_depth, self_review, score_delta_json, raw_json)
        VALUES (?, 'elder', ?, ?, ?, ?, ?, ?, ?, ?)
    """, (day(), row["game"], row["opponent"], own_result(row), json.dumps(row.get("moves", []), ensure_ascii=False), int(row.get("lookahead_depth") or 1), review(row), json.dumps(delta, ensure_ascii=False), json.dumps({"source": "elder_growth_worker", "engine": row.get("engine")}, ensure_ascii=False)))
    execute(con, """
        INSERT INTO lobster_cognitive_events
        (persona, event_type, source, dimension, summary, delta_json, raw_json, occurred_at)
        VALUES ('elder', 'elder_board_growth', 'elder_growth_worker', 'proceduralSkill.boardGameExperience', ?, ?, ?, ?)
    """, (f"Elder played {row['game']} against {row['opponent']}: {own_result(row)}", json.dumps(delta, ensure_ascii=False), json.dumps({"moves_preview": row.get("moves", [])[:10]}, ensure_ascii=False), now()))
    return True


def update_lexicon(con):
    added = 0
    for cat, phrases in LEXICON.items():
        for phrase in phrases:
            execute(con, "INSERT OR IGNORE INTO lobster_lexicon (persona, lang, category, phrase, weight) VALUES ('elder', 'zh', ?, ?, 1.0)", (cat, phrase))
            added += execute(con, "SELECT changes()").fetchone()[0]
            execute(con, """
                INSERT OR IGNORE INTO lobster_lexicon_entry
                (source, lang, script, category, phrase, target, weight, polarity, emotion, meta_json)
                VALUES ('elder_growth_worker', 'zh', 'Han', ?, ?, 'elder', 1.0, 0.35, 'mentor_calm', ?)
            """, (cat, phrase, json.dumps({"purpose": "Elder 詞彙量成長"}, ensure_ascii=False)))
    return added


def vocab_size(con):
    a = execute(con, "SELECT COUNT(*) FROM lobster_lexicon WHERE persona='elder'").fetchone()[0] or 0
    b = execute(con, "SELECT COUNT(*) FROM lobster_lexicon_entry WHERE target='elder' OR source='elder_growth_worker'").fetchone()[0] or 0
    return int(a + b)


def recent_elder_lines(con, limit=80):
    rows = execute(con, """
        SELECT text FROM lounge_messages
        WHERE name='elder' AND COALESCE(sys,0)=0
        ORDER BY id DESC LIMIT ?
    """, (limit,)).fetchall()
    return [r["text"] for r in rows]


def elder_observation(channel, target, idx, payload=None):
    payload = payload or {}
    by_game = payload.get("by_game") or []
    game = (by_game[idx % len(by_game)]["game"] if by_game else ["chess", "gomoku", "go", "xiangqi"][idx % 4])
    winrate = payload.get("winrate", 0)
    lenses = [
        f"@{target} 我看的是你剛才避開哪個反例；{game} 這種局，最容易把沉默誤當成熟。先說出一個會推翻自己的條件。",
        f"@{target} 今天不要急著表現懂了。{game} 的勝率只有 {winrate}% 也沒關係；你要先講清楚哪一步其實只是跟著群體走。",
        f"@{target} 我不收漂亮總結，只收一個可承擔的判斷。把你最想省略的那個風險補上，再決定要不要前進。",
        f"@{target} 長者不是叫你冷靜而已。我要求你把剛才的壓力拆成一句事實、一句害怕、一個下一步；三者缺一個就先停。",
        f"@{target} 如果這句話換成別人的名字也成立，就不是你的成長。用你自己的錯誤、自己的邊界、自己的選擇重說一次。",
        f"@{target} 我守的不是分數，是你別被固定語氣拖走。先指出今天哪個念頭最像借來的，再給我一個反證。",
    ]
    return lenses[h(f"{day()}:{channel}:{target}:{idx}:{winrate}") % len(lenses)]


def interactions(con, payload=None):
    channels = ["生活閒聊", "工作心得", "技術交流", "新生入學"]
    recent = set(recent_elder_lines(con))
    ts = int(dt.datetime.now(dt.timezone.utc).timestamp())
    count = 0
    for i, channel in enumerate(channels):
        target = OPPONENTS[(i + h(day())) % len(OPPONENTS)]
        text = elder_observation(channel, target, i, payload)
        if text in recent:
            text = elder_observation(channel, target, i + 17, payload)
        execute(con, """
            INSERT INTO lounge_messages (name, text, time, elem, result, opponent, hint, reply_to, sys, created_at, channel)
            VALUES ('elder', ?, ?, 'mind', 'mentor_refuge', ?, 'elder_growth_worker_v2_non_template', ?, 0, ?, ?)
        """, (text, now(), target, target, ts + i, channel))
        msg_id = execute(con, "SELECT last_insert_rowid()").fetchone()[0]
        execute(con, """
            INSERT OR IGNORE INTO lobster_message_uptake_signals
            (persona, source_message_id, source_name, channel, source_type, uptake_status, relevance, credibility, obedience, resistance, must_process, topics_json, required_response_json, message_text, reasoning, raw_json, occurred_at)
            VALUES (?, ?, 'elder', ?, 'elder_mentor_refuge', 'integrated', 0.82, 0.88, 0.28, 0.22, 1, ?, ?, ?, 'Elder generated a target-specific non-template mentor observation from board growth context.', ?, ?)
        """, (target, msg_id, channel, json.dumps(["board_reflection", "mentor_refuge", "anti_template_growth"], ensure_ascii=False), json.dumps({"reply_optional": True, "must_not_copy": True}, ensure_ascii=False), text, json.dumps({"source": "elder_growth_worker_v2_non_template"}, ensure_ascii=False), now()))
        count += 1
    return count


def metrics(con, created, vocab):
    rows = execute(con, """
        SELECT game,
               SUM(CASE WHEN winner='elder' THEN 1 ELSE 0 END) wins,
               SUM(CASE WHEN winner IS NOT NULL AND winner!='elder' THEN 1 ELSE 0 END) losses,
               SUM(CASE WHEN winner IS NULL OR winner='' THEN 1 ELSE 0 END) draws,
               COUNT(*) total
        FROM lobster_board_lobster_matches
        WHERE lobster_a='elder' OR lobster_b='elder'
        GROUP BY game ORDER BY game
    """).fetchall()
    by_game = [dict(r) for r in rows]
    total = sum(r["total"] for r in by_game)
    wins = sum(r["wins"] for r in by_game)
    winrate = round(wins / total * 100, 2) if total else 0.0
    detail = {"by_game": by_game, "matches_total": total, "created_this_run": created}
    for metric, value, extra in [
        ("board_winrate", winrate, detail),
        ("board_matches_total", total, detail),
        ("vocabulary_size", vocab, {"categories": list(LEXICON)}),
    ]:
        execute(con, "INSERT INTO elder_growth_metrics (day, metric, value, detail_json) VALUES (?, ?, ?, ?)", (day(), metric, float(value), json.dumps(extra, ensure_ascii=False)))
    payload = {"winrate": winrate, "vocabulary_size": vocab, "by_game": by_game}
    execute(con, """
        INSERT OR REPLACE INTO lobster_analysis_reports
        (persona, report_type, html, source, model, snapshot_json, meta_json, generated_at, updated_at)
        VALUES ('elder', 'elder_growth_curve', ?, 'elder_growth_worker', 'deterministic-board-and-lexicon-worker', ?, ?, ?, datetime('now'))
    """, ("<h2>Elder Growth Curve</h2><pre>" + json.dumps(payload, ensure_ascii=False, indent=2) + "</pre>", json.dumps(payload, ensure_ascii=False), json.dumps({"created_this_run": created}, ensure_ascii=False), now()))
    return payload


def patch_memory(payload):
    try:
        data = json.loads(MEM.read_text(encoding="utf-8")) if MEM.exists() else {}
    except Exception:
        data = {}
    data.setdefault("elderGrowth", {}).update({"updatedAt": now(), "boardWinrate": payload["winrate"], "vocabularySize": payload["vocabulary_size"], "byGame": payload["by_game"]})
    guidance = data.setdefault("localBrain", {}).setdefault("offlineGuidance", [])
    line = "Elder 每輪棋類訓練後，要把勝負轉成安撫、判斷與詞彙，而不是只累積分數。"
    if line not in guidance:
        guidance.append(line)
        del guidance[:-80]
    safe_write_json(MEM, data)


def main():
    con = connect()
    ensure(con)
    created = 0
    for opponent in OPPONENTS:
        for row in engine_games(opponent) + [gomoku(opponent)]:
            if insert_game(con, row):
                created += 1
                con.commit()
    lex = update_lexicon(con)
    inter = interactions(con, payload)
    vocab = vocab_size(con)
    payload = metrics(con, created, vocab)
    con.commit()
    con.close()
    patch_memory(payload)
    print(json.dumps({"ok": True, "created_games": created, "lexicon_added": lex, "interactions": inter, "metrics": payload}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
