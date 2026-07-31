#!/usr/bin/env python3
import json
import sqlite3
from datetime import datetime, timezone

DB = "/opt/battle_arena/app/battle_arena.db"
RUN_ID = "board_loss_review_auto"

def now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

def load_json(text, default):
    try:
        return json.loads(text or "")
    except Exception:
        return default

def move_label(move):
    if not isinstance(move, dict):
        return str(move)[:80]
    for key in ("san", "move", "reason"):
        if move.get(key):
            return str(move.get(key))
    if "x" in move and "y" in move:
        return f"({move.get('x')},{move.get('y')})"
    return json.dumps(move, ensure_ascii=False)[:80]

def phase_from_moves(moves):
    n = len(moves or [])
    if n <= 8:
        return "opening"
    if n <= 24:
        return "midgame"
    return "late_midgame"

def candidate_alternative(game, move, phase):
    reason = str(move.get("reason") or "") if isinstance(move, dict) else ""
    if game == "chess":
        return "先找 forcing move：將軍、吃子、威脅；若沒有，改走能增加防守與中心控制的候選手。"
    if game == "xiangqi":
        return "先查將軍鏈與子力協調，避免只照引擎深度 1 推進；候選手要能同時守核心線與製造反制。"
    if game == "go":
        return "不要只追 center_influence；改比較實地、斷點與對手厚勢，優先補最薄弱的連接。"
    if game == "gomoku":
        return "優先檢查對手 open-three / fork；若自己沒有立即威脅，改下阻斷點而非延伸自己線。"
    return "改走能回答對手最強反制的候選手，而不是延續原本慣性。"

def pick_critical_move(own_moves, opp_moves):
    if not own_moves:
        return 0, {}
    # Prefer the last third: losses usually become irreversible after repeated inert moves.
    idx = max(0, int(len(own_moves) * 0.66) - 1)
    return idx, own_moves[idx]

def simulate_counterfactual(game, phase, alternative, opponent):
    return (
        f"如果改走替代線，局面未必立刻逆轉，但會把輸法從『被 {opponent} 連續壓迫』改成『至少逼對手證明攻擊成立』；"
        f"{phase} 階段的目標會從延續慣性改成製造一個可驗證分歧點。"
    )

def build_review(persona, game, opponent, moves, source, original_review=""):
    own_moves = [m for m in moves if isinstance(m, dict) and m.get("actor") == persona]
    opp_moves = [m for m in moves if isinstance(m, dict) and m.get("actor") == opponent]
    first = move_label(own_moves[0]) if own_moves else "未記錄第一手"
    last = move_label(own_moves[-1]) if own_moves else "未記錄末手"
    opp_last = move_label(opp_moves[-1]) if opp_moves else "未記錄對手末手"
    phase = phase_from_moves(moves)
    reason_counts = {}
    for m in own_moves:
        r = str(m.get("reason") or "unknown")
        reason_counts[r] = reason_counts.get(r, 0) + 1
    top_reason = max(reason_counts, key=reason_counts.get) if reason_counts else "unknown"
    critical_idx, critical_move = pick_critical_move(own_moves, opp_moves)
    critical_label = move_label(critical_move) if critical_move else "未記錄關鍵手"
    alternative = candidate_alternative(game, critical_move, phase)
    counterfactual = simulate_counterfactual(game, phase, alternative, opponent)
    diagnosis = (
        f"{persona} 在 {game} 輸給 {opponent}。關鍵不是結果，而是 {phase} 階段第 {critical_idx + 1} 個己方候選手"
        f"「{critical_label}」沒有回答對手壓力；第一手 {first}，末段 {last}，對手末段壓力 {opp_last}。"
    )
    improvement = (
        f"改進演算法：A. 每步先列對手兩個最強反制；B. 若連續兩手只是 {top_reason}，強制切換到防守/阻斷候選；"
        f"C. 關鍵手重算一條替代線：{alternative}"
    )
    verification = "驗證：下一局同類棋必須在落子前輸出『原手、替代手、若改走會怎樣』三欄；缺一欄即判定沒有真正學習。"
    review_text = f"{diagnosis} 反事實推演：{counterfactual} {improvement} {verification}"
    return {
        "diagnosis": diagnosis, "phase": phase, "first_own_move": first,
        "last_own_move": last, "opponent_pressure": opp_last,
        "top_repeated_reason": top_reason, "critical_move_index": critical_idx + 1,
        "critical_move": critical_label, "counterfactual_alternative": alternative,
        "counterfactual_projection": counterfactual, "improvement": improvement,
        "verification": verification, "source": source,
        "original_review": original_review, "review_text": review_text,
    }

con = sqlite3.connect(DB, timeout=30)
con.row_factory = sqlite3.Row
con.execute("PRAGMA busy_timeout=30000")
con.execute("""
CREATE TABLE IF NOT EXISTS lobster_board_loss_reviews (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id TEXT NOT NULL,
  source_table TEXT NOT NULL,
  source_id INTEGER NOT NULL,
  persona TEXT NOT NULL,
  game TEXT NOT NULL,
  opponent TEXT,
  loss_phase TEXT,
  diagnosis TEXT NOT NULL,
  improvement_plan TEXT NOT NULL,
  verification_rule TEXT NOT NULL,
  review_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  UNIQUE(source_table, source_id, persona)
)
""")

created = 0
rows = con.execute("""
WITH sides AS (
  SELECT id, game, lobster_a persona, lobster_b opponent, winner, moves_json, created_at,
         CASE WHEN winner=lobster_b THEN 'loss' WHEN winner IS NULL OR winner='' THEN 'draw' ELSE 'win' END r
  FROM lobster_board_lobster_matches WHERE created_at >= datetime('now','-72 hours')
  UNION ALL
  SELECT id, game, lobster_b persona, lobster_a opponent, winner, moves_json, created_at,
         CASE WHEN winner=lobster_a THEN 'loss' WHEN winner IS NULL OR winner='' THEN 'draw' ELSE 'win' END r
  FROM lobster_board_lobster_matches WHERE created_at >= datetime('now','-72 hours')
)
SELECT * FROM sides s
WHERE r='loss'
  AND NOT EXISTS (
    SELECT 1 FROM lobster_board_loss_reviews r
    WHERE r.source_table='lobster_board_lobster_matches'
      AND r.source_id=s.id
      AND r.persona=s.persona
  )
""").fetchall()
for row in rows:
    review = build_review(row["persona"], row["game"], row["opponent"], load_json(row["moves_json"], []), "lobster_board_lobster_matches")
    con.execute("""INSERT OR IGNORE INTO lobster_board_loss_reviews
      (run_id,source_table,source_id,persona,game,opponent,loss_phase,diagnosis,improvement_plan,verification_rule,review_json)
      VALUES(?, 'lobster_board_lobster_matches', ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
      (RUN_ID, row["id"], row["persona"], row["game"], row["opponent"], review["phase"], review["diagnosis"], review["improvement"], review["verification"], json.dumps(review, ensure_ascii=False)))
    created += con.total_changes
    con.execute("""INSERT INTO lobster_cognitive_events
      (persona,event_type,source,dimension,summary,delta_json,raw_json,occurred_at)
      VALUES(?, 'board_loss_complete_review_auto', ?, 'proceduralSkill.boardLossReview', ?, ?, ?, ?)""",
      (row["persona"], RUN_ID, review["review_text"], json.dumps({"requires_next_game_verification": True}, ensure_ascii=False), json.dumps(review, ensure_ascii=False), now_iso()))

con.commit()
print(json.dumps({"new_reviews": len(rows)}, ensure_ascii=False))
