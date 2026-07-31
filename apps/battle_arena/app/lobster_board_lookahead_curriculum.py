#!/usr/bin/env python3
import datetime as dt
import html
import json
import sqlite3
from pathlib import Path
import sys as _sys_for_mem_io
if '/root' not in _sys_for_mem_io.path:
    _sys_for_mem_io.path.insert(0, '/root')
from lobster_memory_io import safe_write_json

DB = Path("/opt/battle_arena/app/battle_arena.db")
MEM_DIR = Path("/root/lobster-memory")
TAIPEI = dt.timezone(dt.timedelta(hours=8))

DEFAULT_PERSONAS = [
    "norika_oda", "snaplex", "vortexiq", "pincerbot", "clawtrix",
    "hexclaw", "prismbit", "blazepaw", "ragclaw", "stonefang",
    "gauntlet", "nullpinch", "sparkrill", "mirrorcalm", "basaltrook",
    "brineledger", "emberling", "flarecub", "omenwake", "rushfin",
    "elder",
]

GAMES = {
    "chess": {
        "zh": "西洋棋",
        "objective": "king safety, material, initiative, forced replies",
        "line_shape": ["candidate move", "opponent best reply", "second move", "counter-reply", "stabilizing move"],
        "study_query": "annotated chess game 10 move calculation training",
    },
    "xiangqi": {
        "zh": "象棋",
        "objective": "general safety, cannon screen, rook file, horse-leg traps",
        "line_shape": ["candidate move", "opponent check/threat", "file control", "screen change", "general safety check"],
        "study_query": "象棋 名局 連續計算 五步 十步 解析",
    },
    "go": {
        "zh": "圍棋",
        "objective": "liberties, connection, cut, eye shape, territory versus influence",
        "line_shape": ["candidate move", "opponent contact/cut", "connection or sacrifice", "liberty count", "shape result"],
        "study_query": "go life and death reading 10 moves annotated",
    },
    "gomoku": {
        "zh": "五子棋",
        "objective": "open three, open four, fork, block, threat sequence",
        "line_shape": ["candidate threat", "forced block", "second threat", "counter-threat", "winning or neutralizing point"],
        "study_query": "gomoku renju threat sequence 10 moves analysis",
    },
}

LEXICON = [
    ("zh-TW", "board_lookahead", "五步預判", 1.5),
    ("zh-TW", "board_lookahead", "十步線", 1.5),
    ("zh-TW", "board_lookahead", "候選線", 1.4),
    ("zh-TW", "board_lookahead", "對手最強回應", 1.6),
    ("zh-TW", "board_lookahead", "反制樹", 1.4),
    ("zh-TW", "board_lookahead", "收束點", 1.3),
    ("zh-TW", "board_lookahead", "威脅序列", 1.5),
    ("zh-TW", "board_lookahead", "局面評估", 1.4),
    ("zh-TW", "board_lookahead", "變化圖", 1.3),
    ("en", "board_lookahead", "principal variation", 1.3),
    ("en", "board_lookahead", "candidate line", 1.3),
    ("en", "board_lookahead", "opponent best reply", 1.4),
]

HARD_RULES = [
    "Every board move must name at least two candidate lines before choosing.",
    "A valid line alternates my move and the opponent's strongest reply.",
    "Baseline training requires five plies of lookahead; advanced training extends to seven and ten plies.",
    "Do not call a move good unless it survives the opponent's strongest reply.",
    "After the game, record the first missed threat, the strongest reply you ignored, and the rule to remember next time.",
]


def utc_now():
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def today():
    return dt.datetime.now(TAIPEI).strftime("%Y-%m-%d")


def taipei_now():
    return dt.datetime.now(TAIPEI).replace(microsecond=0).isoformat()


def personas():
    seen = set()
    for name in DEFAULT_PERSONAS:
        seen.add(name)
        yield name
    if MEM_DIR.exists():
        for path in sorted(MEM_DIR.glob("*.json")):
            if path.stem not in seen:
                seen.add(path.stem)
                yield path.stem


def ensure(con):
    con.executescript(
        """
CREATE TABLE IF NOT EXISTS lobster_board_rule_knowledge (
  persona TEXT NOT NULL,
  game TEXT NOT NULL,
  version INTEGER NOT NULL DEFAULT 1,
  rules_json TEXT NOT NULL DEFAULT '{}',
  learned_at TEXT NOT NULL,
  source TEXT NOT NULL,
  PRIMARY KEY (persona, game)
);
CREATE TABLE IF NOT EXISTS lobster_board_study_assignments (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  day TEXT NOT NULL,
  persona TEXT NOT NULL,
  game TEXT NOT NULL,
  study_target TEXT NOT NULL,
  query TEXT NOT NULL,
  required_notes_json TEXT NOT NULL DEFAULT '[]',
  status TEXT NOT NULL DEFAULT 'assigned',
  created_at TEXT DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS lobster_board_lookahead_metrics (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  day TEXT NOT NULL,
  persona TEXT NOT NULL,
  game TEXT NOT NULL,
  target_depth INTEGER NOT NULL,
  demonstrated_depth INTEGER NOT NULL,
  candidate_lines INTEGER NOT NULL,
  teacher TEXT NOT NULL DEFAULT 'elder',
  score REAL NOT NULL,
  detail_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_lobster_board_lookahead_day
  ON lobster_board_lookahead_metrics(day, persona, game);
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
CREATE TABLE IF NOT EXISTS lobster_lexicon (
  persona TEXT NOT NULL DEFAULT '*',
  lang TEXT NOT NULL,
  category TEXT NOT NULL,
  phrase TEXT NOT NULL,
  weight REAL DEFAULT 1,
  created_at TEXT DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (persona, lang, category, phrase)
);
CREATE TABLE IF NOT EXISTS lobster_teacher_reviews (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  apprentice TEXT NOT NULL,
  mentor TEXT NOT NULL,
  clarity REAL,
  usefulness REAL,
  originality REAL,
  overcontrol REAL,
  template_pressure REAL,
  identity_growth REAL,
  comment TEXT,
  raw_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
"""
    )


def current_depth(con, persona, game):
    row = con.execute(
        "SELECT COALESCE(MAX(lookahead_depth),0) AS depth FROM lobster_game_training_sessions WHERE persona=? AND game=?",
        (persona, game),
    ).fetchone()
    return int(row["depth"] or 0)


def upsert_memory(persona, now):
    path = MEM_DIR / f"{persona}.json"
    if not path.exists():
        return False
    data = json.loads(path.read_text(encoding="utf-8"))
    cog = data.setdefault("cognitiveExtensions", {})
    proc = cog.setdefault("proceduralSkill", {})
    board = proc.setdefault("boardGames", {})
    board["lookaheadCurriculum"] = {
        "version": 2,
        "teacher": "elder",
        "minimumPly": 5,
        "advancedPly": [7, 10],
        "candidateLinesRequired": 2,
        "hardRules": HARD_RULES,
        "updatedAt": now,
    }
    proc.setdefault("automaticResponses", [])
    for rule in [
        "board_move_requires_5_ply_candidate_lines",
        "always_model_opponent_best_reply",
        "after_game_extract_missed_threat",
    ]:
        if rule not in proc["automaticResponses"]:
            proc["automaticResponses"].append(rule)
    data["updatedAt"] = now
    safe_write_json(path, data)
    return True


def report_html(persona, rows):
    items = []
    for row in rows:
        items.append(
            f"<li><b>{html.escape(row['game'])}</b>: current {row['demonstrated_depth']} ply, "
            f"target {row['target_depth']} ply, score {row['score']:.2f}</li>"
        )
    return (
        "<h2>5-10 Step Board Lookahead Curriculum</h2>"
        "<p>Elder teaches this lobster to calculate before reacting: two candidate lines, "
        "opponent strongest reply, five-ply baseline, seven-to-ten-ply advanced reading.</p>"
        f"<ul>{''.join(items)}</ul>"
    )


def main():
    now = utc_now()
    day = today()
    con = sqlite3.connect(DB, timeout=30)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA busy_timeout=30000")
    con.execute("PRAGMA journal_mode=WAL")
    ensure(con)
    con.execute("DELETE FROM lobster_board_lookahead_metrics WHERE day=?", (day,))

    people = list(personas())
    metric_rows = []
    assignments = 0
    memory_updates = 0
    for idx, persona in enumerate(people):
        if upsert_memory(persona, now):
            memory_updates += 1
        person_rows = []
        for game_idx, (game, game_rules) in enumerate(GAMES.items()):
            target_depth = [5, 7, 10][(idx + game_idx) % 3]
            demonstrated = current_depth(con, persona, game)
            score = round(min(1.0, demonstrated / float(target_depth)), 3)
            rules = {
                "version": 2,
                "teacher": "elder",
                "game": game,
                "zh": game_rules["zh"],
                "objective": game_rules["objective"],
                "minimumLookaheadPly": 5,
                "advancedLookaheadPly": [7, 10],
                "candidateLinesRequired": 2,
                "lineShape": game_rules["line_shape"],
                "hardRules": HARD_RULES,
                "evaluationRubric": {
                    "legalMove": 0.2,
                    "opponentBestReply": 0.3,
                    "fivePlyContinuity": 0.25,
                    "missedThreatReview": 0.15,
                    "conciseNaturalExplanation": 0.1,
                },
            }
            con.execute(
                """INSERT INTO lobster_board_rule_knowledge(persona,game,version,rules_json,learned_at,source)
                   VALUES (?,?,?,?,?,?)
                   ON CONFLICT(persona,game) DO UPDATE SET
                     version=excluded.version,
                     rules_json=excluded.rules_json,
                     learned_at=excluded.learned_at,
                     source=excluded.source""",
                (persona, game, 2, json.dumps(rules, ensure_ascii=False), now, "lobster_board_lookahead_curriculum"),
            )
            required_notes = [
                "current_position",
                "candidate_line_a_5ply",
                "candidate_line_b_5ply",
                "opponent_strongest_reply",
                "missed_threat_if_any",
                "advanced_7_to_10_ply_extension",
                "lesson_for_next_game",
            ]
            if not con.execute(
                """SELECT 1 FROM lobster_board_study_assignments
                   WHERE day=? AND persona=? AND game=? AND study_target LIKE '5-10%%'""",
                (day, persona, game),
            ).fetchone():
                con.execute(
                    """INSERT INTO lobster_board_study_assignments
                       (day, persona, game, study_target, query, required_notes_json)
                       VALUES (?, ?, ?, ?, ?, ?)""",
                    (
                        day,
                        persona,
                        game,
                        f"5-10 ply lookahead ladder with Elder: {game_rules['zh']}",
                        game_rules["study_query"],
                        json.dumps(required_notes, ensure_ascii=False),
                    ),
                )
                assignments += 1
            detail = {
                "teacher": "elder",
                "currentDepthSource": "lobster_game_training_sessions.max(lookahead_depth)",
                "minimumRequiredPly": 5,
                "advancedTargets": [7, 10],
                "candidateLinesRequired": 2,
                "honesty": "This metric is curriculum readiness; true engine search depth must be demonstrated by later matches.",
            }
            con.execute(
                """INSERT INTO lobster_board_lookahead_metrics
                   (day, persona, game, target_depth, demonstrated_depth, candidate_lines, teacher, score, detail_json)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (day, persona, game, target_depth, demonstrated, 2, "elder", score, json.dumps(detail, ensure_ascii=False)),
            )
            con.execute(
                """INSERT INTO lobster_cognitive_events
                   (persona,event_type,source,dimension,summary,delta_json,raw_json,occurred_at)
                   VALUES (?,?,?,?,?,?,?,?)""",
                (
                    persona,
                    "board_lookahead_curriculum_assigned",
                    "lobster_board_lookahead_curriculum",
                    "proceduralSkill.boardGameLookahead",
                    f"{persona} assigned {game} 5-10 ply lookahead training by Elder.",
                    json.dumps({
                        "target_lookahead_depth_min": 5,
                        "target_lookahead_depth_max": 10,
                        "candidate_lines_required": 2,
                        "opponent_modeling": 0.12,
                        "board_vocabulary": 0.08,
                    }, ensure_ascii=False),
                    json.dumps(rules, ensure_ascii=False),
                    now,
                ),
            )
            row = {"game": game, "target_depth": target_depth, "demonstrated_depth": demonstrated, "score": score}
            person_rows.append(row)
            metric_rows.append((persona, row))

        for lang, category, phrase, weight in LEXICON:
            con.execute(
                """INSERT INTO lobster_lexicon(persona, lang, category, phrase, weight)
                   VALUES (?, ?, ?, ?, ?)
                   ON CONFLICT(persona, lang, category, phrase) DO UPDATE SET weight=MAX(weight, excluded.weight)""",
                (persona, lang, category, phrase, weight),
            )
        if persona != "elder":
            recent_review = con.execute(
                """SELECT 1 FROM lobster_teacher_reviews
                   WHERE apprentice=? AND mentor='elder'
                     AND raw_json LIKE '%board_lookahead_5_10%'
                     AND created_at >= datetime('now','-20 hours')""",
                (persona,),
            ).fetchone()
            if not recent_review:
                con.execute(
                    """INSERT INTO lobster_teacher_reviews
                       (apprentice, mentor, clarity, usefulness, originality, overcontrol, template_pressure, identity_growth, comment, raw_json)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        persona,
                        "elder",
                        0.88,
                        0.9,
                        0.82,
                        0.18,
                        0.12,
                        0.86,
                        "Elder assigned board-reading practice: name two candidate lines, calculate at least five plies, then extend toward seven and ten without turning the reply into a template.",
                        json.dumps({"curriculum": "board_lookahead_5_10", "games": list(GAMES.keys())}, ensure_ascii=False),
                    ),
                )
        con.execute(
            """INSERT OR REPLACE INTO lobster_analysis_reports
               (persona, report_type, html, source, model, snapshot_json, meta_json, generated_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))""",
            (
                persona,
                "board_lookahead_curriculum",
                report_html(persona, person_rows),
                "lobster_board_lookahead_curriculum",
                "no-llm-curriculum",
                json.dumps({"rows": person_rows}, ensure_ascii=False),
                json.dumps({"teacher": "elder", "minimumPly": 5, "advancedPly": [7, 10]}, ensure_ascii=False),
                now,
            ),
        )

    created_at = int(dt.datetime.now(dt.timezone.utc).timestamp() * 1000)
    # 2026-05-12: 停止硬編碼 elder 固定台詞注入 lounge（已成嚴重模板污染源）
    # elder 的訊息必須由對話引擎動態生成，不能是每日固定字串
    # elder_messages block REMOVED per Norika directive

    con.commit()
    depth_summary = con.execute(
        """SELECT game,
                  COUNT(*) AS agents,
                  ROUND(AVG(demonstrated_depth), 2) AS avg_current_depth,
                  ROUND(AVG(target_depth), 2) AS avg_target_depth,
                  ROUND(AVG(score), 3) AS avg_score
           FROM lobster_board_lookahead_metrics
           WHERE day=?
           GROUP BY game
           ORDER BY game""",
        (day,),
    ).fetchall()
    con.close()
    print(json.dumps({
        "ok": True,
        "day": day,
        "personas": len(people),
        "games": list(GAMES.keys()),
        "assignments_created": assignments,
        "memory_updates": memory_updates,
        "lexicon_terms_per_persona": len(LEXICON),
        "metrics_created": len(metric_rows),
        "summary": [dict(r) for r in depth_summary],
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
