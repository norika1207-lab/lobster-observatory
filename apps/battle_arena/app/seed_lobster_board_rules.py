#!/usr/bin/env python3
import datetime as dt
import glob
import json
import os
import sqlite3
import time
from pathlib import Path
import sys as _sys_for_mem_io
if '/root' not in _sys_for_mem_io.path:
    _sys_for_mem_io.path.insert(0, '/root')
from lobster_memory_io import safe_write_json

DB = "/opt/battle_arena/app/battle_arena.db"
MEM_DIR = Path("/root/lobster-memory")
NOW = dt.datetime.utcnow().replace(microsecond=0).isoformat() + "Z"

BOARD_RULES = {
    "version": 1,
    "updatedAt": NOW,
    "source": "seed_lobster_board_rules",
    "hardRules": [
        "Do not play a board game by vibes. First identify legal moves, objective, opponent threat, two candidate lines, and at least five plies of lookahead; advanced agents extend one line to ten plies.",
        "Every move must record a reason: attack, defense, capture/material, territory, influence, connection, block, probe, or endgame.",
        "After every game, write a self-review explaining why the result happened, which opponent reply was underestimated, and what five-to-ten-ply rule should be remembered.",
        "Search famous international games only as study material; do not copy a move without explaining why it applies to the current board.",
    ],
    "games": {
        "chess": {
            "zh": "西洋棋：8x8。目標是將死對方國王；國王不能走進被攻擊格。棋子：王、后、車、象、馬、兵。兵向前、斜吃，抵達底線升變。每步先檢查王安全，再比較吃子、控制中心、子力活動與王翼安全。",
            "objective": "checkmate the opposing king while preserving king safety",
            "mustKnow": ["legal move", "check", "checkmate", "stalemate", "castling", "promotion", "material", "center control", "king safety"],
            "moveQuestions": ["Is my king safe after this move?", "What is the opponent's strongest reply?", "Does this improve material, mobility, or king pressure?"],
            "studyTargets": ["World Chess Championship", "Candidates Tournament", "Chess Olympiad", "Tata Steel Chess", "Sinquefield Cup"],
        },
        "xiangqi": {
            "zh": "象棋：9x10。目標是將死或困死對方將/帥。將帥在九宮內，士守九宮，象不能過河，馬走日但會蹩馬腳，車直線，炮隔子打，兵卒過河後可橫走。每步先看將帥安全、炮架、車線、馬腿與過河兵威脅。",
            "objective": "checkmate or immobilize the opposing general",
            "mustKnow": ["palace", "flying general", "cannon screen", "horse leg block", "river", "advisor", "elephant cannot cross river"],
            "moveQuestions": ["Did I expose flying-general danger?", "Am I creating or removing a cannon screen?", "Which file or rank becomes open after this move?"],
            "studyTargets": ["World Xiangqi Championship", "Asian Xiangqi Championship", "Chinese National Xiangqi Championship", "碧桂園杯", "五羊杯"],
        },
        "go": {
            "zh": "圍棋：通常19路，訓練先用9路。黑白輪流落子，目標是圍地並保持棋串有氣。沒有氣的棋被提掉；劫不能立即重複。每步先看氣、連接、切斷、眼形、厚薄、實地與外勢。",
            "objective": "maximize territory/influence while keeping groups alive",
            "mustKnow": ["liberty", "capture", "ko", "territory", "influence", "connection", "cut", "eyes", "life and death"],
            "moveQuestions": ["Does this group have enough liberties?", "Am I connecting, cutting, or only making empty shape?", "Is territory or influence more urgent here?"],
            "studyTargets": ["LG Cup", "Samsung Cup", "Ing Cup", "MLily Cup", "Nongshim Cup", "Honinbo", "Kisei"],
        },
        "gomoku": {
            "zh": "五子棋：通常15x15，先連成五子者勝。重點不是只下自己的線，也要封對方活三、活四、雙三、雙四。每步先看自己是否能成五，再看對方是否下一手能成五。",
            "objective": "make five in a row before the opponent",
            "mustKnow": ["open three", "open four", "double three", "double four", "block", "fork", "five in a row"],
            "moveQuestions": ["Can I win immediately?", "Can the opponent win immediately?", "Does this create a fork or only one weak line?"],
            "studyTargets": ["Gomoku World Championship", "Renju World Championship", "International Gomoku Federation games"],
        },
    },
    "researchProtocol": {
        "enabledForHighSkillAgents": True,
        "minimumStudyItemsBeforeClaimingExpertise": 3,
        "queryExamples": [
            "famous World Chess Championship annotated game",
            "World Xiangqi Championship famous game analysis",
            "famous LG Cup Go game review",
            "Renju World Championship game analysis",
        ],
        "requiredNotes": ["source_url", "players", "opening_or_theme", "turning_point", "five_ply_candidate_line", "ten_ply_extension_if_possible", "lesson_for_next_move"],
    },
}


def ensure_db(con):
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


def persona_files():
    for path in sorted(MEM_DIR.glob("*.json")):
        if path.name == "lobster.db":
            continue
        yield path


def normalize_persona(path):
    return path.stem


def main():
    con = sqlite3.connect(DB, timeout=30)
    con.execute("PRAGMA busy_timeout=30000")
    ensure_db(con)
    day = dt.datetime.utcnow().strftime("%Y-%m-%d")
    personas = []
    for path in persona_files():
        persona = normalize_persona(path)
        personas.append(persona)
        data = json.loads(path.read_text(encoding="utf-8"))
        cog = data.setdefault("cognitiveExtensions", {})
        cog["version"] = max(2, int(cog.get("version") or 1))
        cog["updatedAt"] = NOW
        proc = cog.setdefault("proceduralSkill", {})
        proc["boardGames"] = BOARD_RULES
        proc.setdefault("automaticResponses", [])
        for rule in [
            "before_board_move_check_legal_objective_opponent_reply",
            "after_board_game_write_result_cause_and_next_rule",
            "study_famous_games_before_claiming_pattern",
        ]:
            if rule not in proc["automaticResponses"]:
                proc["automaticResponses"].append(rule)
        lm = cog.setdefault("learningModel", {})
        lm.setdefault("stablePrinciples", [])
        for principle in [
            "board_games_require_legal_move_before_intuition",
            "lookahead_means_predicting_opponent_reply_not_only_my_best_move",
            "famous_games_are_evidence_sources_not_templates_to_copy",
        ]:
            if principle not in lm["stablePrinciples"]:
                lm["stablePrinciples"].append(principle)
        brain = data.setdefault("localBrain", {})
        brain.setdefault("offlineGuidance", [])
        guidance = {
            "ts": NOW,
            "source": "board_rule_seed",
            "type": "hard_board_rules",
            "text": "棋類硬規則已寫入：下棋前先確認合法步、勝利目標、對手最強回應與兩條候選路線；聰明龍蝦可搜尋國際名局，但必須摘錄來源、轉折點與能套用到當前局面的理由。",
        }
        brain["offlineGuidance"] = (brain["offlineGuidance"] + [guidance])[-120:]
        data["updatedAt"] = NOW
        safe_write_json(path, data)

        for game, rules in BOARD_RULES["games"].items():
            con.execute(
                """INSERT INTO lobster_board_rule_knowledge(persona,game,version,rules_json,learned_at,source)
                   VALUES (?,?,?,?,?,?)
                   ON CONFLICT(persona,game) DO UPDATE SET version=excluded.version,rules_json=excluded.rules_json,learned_at=excluded.learned_at,source=excluded.source""",
                (persona, game, BOARD_RULES["version"], json.dumps(rules, ensure_ascii=False), NOW, "seed_lobster_board_rules"),
            )
        # Only one study assignment per game/person/day; INSERT OR IGNORE via uniqueness is not available, so skip manually.
        for game, rules in BOARD_RULES["games"].items():
            target = rules["studyTargets"][hash(persona + game) % len(rules["studyTargets"])]
            query = f"{target} famous game analysis"
            if not con.execute(
                "SELECT 1 FROM lobster_board_study_assignments WHERE day=? AND persona=? AND game=?",
                (day, persona, game),
            ).fetchone():
                con.execute(
                    """INSERT INTO lobster_board_study_assignments(day,persona,game,study_target,query,required_notes_json)
                       VALUES (?,?,?,?,?,?)""",
                    (
                        day,
                        persona,
                        game,
                        target,
                        query,
                        json.dumps(BOARD_RULES["researchProtocol"]["requiredNotes"], ensure_ascii=False),
                    ),
                )
        con.execute(
            """INSERT INTO lobster_cognitive_events(persona,event_type,source,dimension,summary,delta_json,raw_json,occurred_at)
               VALUES (?,?,?,?,?,?,?,?)""",
            (
                persona,
                "board_rules_seeded",
                "seed_lobster_board_rules",
                "proceduralSkill.boardGames",
                "Board-game hard rules and famous-game study protocol written into memory.",
                json.dumps({"proceduralSkill.boardGames": 1, "lookaheadTraining": 0.08}, ensure_ascii=False),
                json.dumps(BOARD_RULES, ensure_ascii=False),
                NOW,
            ),
        )

    con.commit()
    con.close()
    print(json.dumps({"ok": True, "personas": len(personas), "games": list(BOARD_RULES["games"].keys()), "updatedAt": NOW}, ensure_ascii=False))


if __name__ == "__main__":
    main()
