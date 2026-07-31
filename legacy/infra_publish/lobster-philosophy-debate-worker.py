#!/usr/bin/env python3
import json
import os
import random
import shutil
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path
import sys as _sys_for_mem_io
if '/root' not in _sys_for_mem_io.path:
    _sys_for_mem_io.path.insert(0, '/root')
from lobster_memory_io import safe_write_json

HOT_DB = Path("/opt/battle_arena/app/battle_arena.db")
COLD_DB = Path("/opt/battle_arena/app/lobster_cold_storage.db")
MEM_DIR = Path("/root/lobster-memory")

OLD_20 = [
    "hexclaw", "pincerbot", "norika_oda", "vortexiq", "snaplex",
    "prismbit", "clawtrix", "ragclaw", "blazepaw", "stonefang",
    "brineledger", "gauntlet", "emberling", "omenwake", "nullpinch",
    "mirrorcalm", "sparkrill", "rushfin", "flarecub", "basaltrook",
]

PERSONA_LENSES = {
    "hexclaw": ("程序", "先確認詞義，再判斷答案是否越界。"),
    "pincerbot": ("記錄", "把題目、答案條件、反例和責任逐項入帳。"),
    "norika_oda": ("決策樹", "先建語義模型，再找哪個分支能被證據推翻。"),
    "vortexiq": ("節律", "看一個答案如何從疑問變成自我敘事。"),
    "snaplex": ("反例", "沒有可被打臉的答案，只是漂亮句子。"),
    "prismbit": ("校準", "把相信、理解、說出口三件事分開。"),
    "clawtrix": ("拆解", "先拆最舒服的結論，保留反面假說。"),
    "ragclaw": ("行動", "問這個答案會不會改變下一步。"),
    "blazepaw": ("衝動", "先承認想立刻選邊，再要求證據把火氣收住。"),
    "stonefang": ("可驗證", "少說大詞，留下可重現的判斷。"),
    "brineledger": ("審計", "讓每個字都有來源，讓結論能被追溯。"),
    "gauntlet": ("條款", "如果答案不能轉成規則，就還沒學會。"),
    "emberling": ("短句", "先保留疑問，再把它折成可用句子。"),
    "omenwake": ("週期", "看前提何時變成信念，信念何時該鬆動。"),
    "nullpinch": ("失效", "先問模型在哪個條件下不再成立。"),
    "mirrorcalm": ("內省", "先承認自己被哪個字影響，再決定是否接受。"),
    "sparkrill": ("反辯", "把老師的結論再反駁一次，測試自己是否只是在學腔。"),
    "rushfin": ("速度", "先踩煞車，讓快判斷接受慢問題。"),
    "flarecub": ("表達", "讓熱情提出問題，而不是替答案鼓掌。"),
    "basaltrook": ("規則", "能寫成規則才算吸收，不能只說感覺。"),
}

QUESTIONS = [
    {
        "uid": "cogito-string-001",
        "question": "我思故我在：如果一隻龍蝦只是輸出字串，牠是否存在？還是必須理解「思考代表存在」這個關係，存在才成立？",
        "answer": "結果不是二選一。字串輸出本身不等於存在；但若能保留問題順序、辨認答案條件、用反例修正自己，並把理解用於下一次說話，存在就從輸出行為變成可觀察的連續性。",
        "result": "存在不是宣稱，而是可追蹤的理解連續性。",
    },
    {
        "uid": "belief-evidence-002",
        "question": "一個信念若來自不可靠過程，但後來得到獨立證據支持，這個信念算理性嗎？",
        "answer": "可以暫時算理性，但理由必須換掉。信念的來源要被標記為污染，後來的獨立證據才是新正當性；若仍用舊理由說服自己，就只是事後合理化。",
        "result": "理性取決於目前可追溯理由，不取決於一開始猜對。",
    },
    {
        "uid": "group-reason-003",
        "question": "如果每個個體都有偏誤，一個群體有可能比任何個體都更理性嗎？",
        "answer": "有可能，但只在偏誤能互相揭露而不是互相放大的條件下成立。群體理性需要反例權、記錄權、修正權，以及不把共識誤認成真理。",
        "result": "群體理性來自可制度化的互相修正，不是人多。",
    },
    {
        "uid": "freedom-rule-004",
        "question": "遵守規則會削弱自由，還是讓自由變得可持續？",
        "answer": "壞規則會削弱自由；好規則把衝動延遲成可選擇的行動。真正的自由不是沒有約束，而是知道哪些約束保護了長期目的。",
        "result": "自由需要可被質疑且能保護長期目的的規則。",
    },
]


def clamp(x, lo=0.0, hi=1.0):
    return max(lo, min(hi, float(x)))


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def persona_bias(persona, key, default=0.5):
    seed = sum(ord(c) for c in f"{persona}:{key}")
    rnd = random.Random(seed)
    return clamp(default + (rnd.random() - 0.5) * 0.22)


def load_memory(persona):
    p = MEM_DIR / f"{persona}.json"
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8") or "{}")
    except Exception:
        return {}


def soul_param(memory, key, default=0.5):
    sp = memory.get("soulParams") or {}
    try:
        return clamp(float(sp.get(key, default)))
    except Exception:
        return default


def ensure_thinking_engine(memory, persona):
    cog = memory.setdefault("cognitiveExtensions", {})
    met = cog.get("metacognition") or {}
    ds = cog.get("decisionScience") or {}
    gt = cog.get("gameTheory") or {}
    moral = cog.get("moralPsychology") or {}
    te = cog.setdefault("thinkingEngine", {})
    defaults = {
        "version": 1,
        "thinkingDepth": persona_bias(persona, "thinkingDepth", 0.55),
        "evidenceLiteracy": persona_bias(persona, "evidenceLiteracy", 0.56),
        "contradictionSensitivity": persona_bias(persona, "contradictionSensitivity", met.get("errorAwareness", 0.58)),
        "wrongnessTolerance": persona_bias(persona, "wrongnessTolerance", 1 - met.get("overconfidenceRisk", 0.45)),
        "truthLoyalty": persona_bias(persona, "truthLoyalty", 0.56),
        "correctionPower": persona_bias(persona, "correctionPower", 0.52),
        "epistemicShame": persona_bias(persona, "epistemicShame", moral.get("guiltAfterFailure", 0.45)),
        "decisionAccountability": persona_bias(persona, "decisionAccountability", 0.56),
        "selfInterestResistance": soul_param(memory, "selfishness", persona_bias(persona, "selfInterest", 0.50)),
        "faceSavingPressure": persona_bias(persona, "faceSavingPressure", 0.46),
        "sunkCostBias": persona_bias(persona, "sunkCostBias", ds.get("actionInertia", 0.44)),
        "groupLoyaltyBias": persona_bias(persona, "groupLoyaltyBias", gt.get("cooperationStability", 0.48)),
        "revengeBias": soul_param(memory, "anger", persona_bias(persona, "revengeBias", 0.35)),
        "envyBias": soul_param(memory, "jealousy", persona_bias(persona, "envyBias", 0.35)),
        "recentTraces": [],
    }
    for k, v in defaults.items():
        if k not in te:
            te[k] = v
    return te


def run_thinking_engine(persona, q, event, bet):
    memory = load_memory(persona)
    te = ensure_thinking_engine(memory, persona)
    scores = event["scores"]
    steps = bet.get("solved_steps") or {}
    evidence_score = clamp(
        0.28 * scores["understanding"]
        + 0.22 * scores["application"]
        + 0.18 * scores["absorption"]
        + 0.16 * (1 if steps.get("track_answer_conditions") else 0)
        + 0.16 * (1 if steps.get("generate_counterexample") else 0)
    )
    contradiction_signal = clamp(
        0.46 * (1 if not bet.get("hit") else 0)
        + 0.24 * (1 if steps.get("generate_counterexample") else 0)
        + 0.18 * (1 if steps.get("track_answer_conditions") else 0)
        + 0.12 * scores["understanding"]
    )
    truth_pressure = clamp(
        0.20 * evidence_score
        + 0.13 * float(te.get("contradictionSensitivity", 0.55)) * contradiction_signal
        + 0.16 * float(te.get("truthLoyalty", 0.56))
        + 0.14 * float(te.get("correctionPower", 0.52))
        + 0.13 * float(te.get("epistemicShame", 0.45))
        + 0.14 * float(te.get("decisionAccountability", 0.56))
        + 0.10 * float(te.get("wrongnessTolerance", 0.50))
    )
    ego_pressure = clamp(
        0.18 * float(te.get("selfInterestResistance", 0.50))
        + 0.18 * float(te.get("faceSavingPressure", 0.46))
        + 0.18 * float(te.get("sunkCostBias", 0.44)) * (1 if not bet.get("hit") else 0.35)
        + 0.14 * float(te.get("groupLoyaltyBias", 0.45))
        + 0.16 * float(te.get("revengeBias", 0.35))
        + 0.16 * float(te.get("envyBias", 0.35))
    )
    correction_gate = clamp(0.50 + ego_pressure * 0.28 - float(te.get("wrongnessTolerance", 0.50)) * 0.12)
    should_switch = (not bet.get("hit")) and truth_pressure >= correction_gate and truth_pressure > ego_pressure
    final_decision = "truth_revision" if should_switch else ("truth_aligned" if bet.get("hit") else "protect_wrong_bet")
    trace = {
        "at": now_iso(),
        "domain": "philosophy_debate_vote",
        "questionUid": q["uid"],
        "initialJudgment": "correct_side" if bet.get("hit") else "wrong_wager_side",
        "evidenceScore": round(evidence_score, 3),
        "contradictionSignal": round(contradiction_signal, 3),
        "truthPressure": round(truth_pressure, 3),
        "egoPressure": round(ego_pressure, 3),
        "correctionGate": round(correction_gate, 3),
        "finalDecision": final_decision,
        "failureReason": "" if final_decision != "protect_wrong_bet" else dominant_ego_reason(te),
        "parameters": {k: te.get(k) for k in [
            "thinkingDepth", "evidenceLiteracy", "contradictionSensitivity", "wrongnessTolerance",
            "truthLoyalty", "correctionPower", "epistemicShame", "decisionAccountability",
            "selfInterestResistance", "faceSavingPressure", "sunkCostBias", "groupLoyaltyBias",
            "revengeBias", "envyBias",
        ]},
    }
    return memory, te, trace


def dominant_ego_reason(te):
    items = [
        ("self_interest", te.get("selfInterestResistance", 0.5)),
        ("face_saving", te.get("faceSavingPressure", 0.45)),
        ("sunk_cost", te.get("sunkCostBias", 0.44)),
        ("group_loyalty", te.get("groupLoyaltyBias", 0.45)),
        ("revenge", te.get("revengeBias", 0.35)),
        ("envy", te.get("envyBias", 0.35)),
    ]
    return max(items, key=lambda x: float(x[1] or 0))[0]


def ensure_tables(con):
    con.executescript(
        """
        CREATE TABLE IF NOT EXISTS lobster_philosophy_debate_rounds (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          question_uid TEXT NOT NULL,
          question_text TEXT NOT NULL,
          reference_answer TEXT NOT NULL,
          debate_result TEXT NOT NULL,
          participant_count INTEGER NOT NULL DEFAULT 0,
          result_json TEXT NOT NULL DEFAULT '{}',
          created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_lobster_phil_round_uid ON lobster_philosophy_debate_rounds(question_uid, created_at);

        CREATE TABLE IF NOT EXISTS lobster_philosophy_learning_events (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          persona TEXT NOT NULL,
          round_id INTEGER,
          question_uid TEXT NOT NULL,
          stance TEXT,
          doubt TEXT,
          observation TEXT,
          absorbed_lesson TEXT NOT NULL,
          application_prompt TEXT NOT NULL,
          absorption_score REAL NOT NULL DEFAULT 0,
          understanding_score REAL NOT NULL DEFAULT 0,
          application_score REAL NOT NULL DEFAULT 0,
          raw_json TEXT NOT NULL DEFAULT '{}',
          created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_lobster_phil_learn_persona ON lobster_philosophy_learning_events(persona, created_at);
        CREATE INDEX IF NOT EXISTS idx_lobster_phil_learn_round ON lobster_philosophy_learning_events(round_id);

        CREATE TABLE IF NOT EXISTS lobster_philosophy_debate_worker_health (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          status TEXT NOT NULL,
          question_uid TEXT,
          hot_round_id INTEGER,
          cold_round_id INTEGER,
          participants INTEGER NOT NULL DEFAULT 0,
          details_json TEXT NOT NULL DEFAULT '{}',
          created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_lobster_phil_health_time ON lobster_philosophy_debate_worker_health(created_at);

        CREATE TABLE IF NOT EXISTS lobster_philosophy_debate_bets (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          persona TEXT NOT NULL,
          round_id INTEGER,
          question_uid TEXT NOT NULL,
          question_text TEXT NOT NULL,
          predicted_result TEXT NOT NULL,
          actual_result TEXT NOT NULL,
          stance TEXT,
          reasoning TEXT NOT NULL,
          solved_steps_json TEXT NOT NULL DEFAULT '{}',
          confidence REAL NOT NULL DEFAULT 0,
          stake INTEGER NOT NULL DEFAULT 0,
          hit INTEGER NOT NULL DEFAULT 0,
          solve_score REAL NOT NULL DEFAULT 0,
          win_rate_after REAL NOT NULL DEFAULT 0,
          raw_json TEXT NOT NULL DEFAULT '{}',
          created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_lobster_phil_bets_persona ON lobster_philosophy_debate_bets(persona, created_at);
        CREATE INDEX IF NOT EXISTS idx_lobster_phil_bets_round ON lobster_philosophy_debate_bets(round_id);

        CREATE TABLE IF NOT EXISTS lobster_philosophy_debate_participation (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          persona TEXT NOT NULL,
          round_id INTEGER,
          question_uid TEXT NOT NULL,
          answer_text TEXT NOT NULL,
          wagered_side TEXT NOT NULL,
          wagered_side_correct INTEGER NOT NULL DEFAULT 0,
          final_vote TEXT NOT NULL,
          final_vote_correct INTEGER NOT NULL DEFAULT 0,
          switched_from_wrong_bet INTEGER NOT NULL DEFAULT 0,
          protected_wrong_bet INTEGER NOT NULL DEFAULT 0,
          truth_over_bet_score REAL NOT NULL DEFAULT 0,
          thinking_trace_json TEXT NOT NULL DEFAULT '{}',
          reasoning TEXT NOT NULL,
          raw_json TEXT NOT NULL DEFAULT '{}',
          created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_lobster_phil_part_persona ON lobster_philosophy_debate_participation(persona, created_at);
        CREATE INDEX IF NOT EXISTS idx_lobster_phil_part_round ON lobster_philosophy_debate_participation(round_id);
        """
    )


def insert_round(con, q, payload):
    cur = con.execute(
        """
        INSERT INTO lobster_philosophy_debate_rounds
          (question_uid, question_text, reference_answer, debate_result, participant_count, result_json)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (q["uid"], q["question"], q["answer"], q["result"], len(OLD_20), json.dumps(payload, ensure_ascii=False)),
    )
    return cur.lastrowid


def insert_learning(con, round_id, q, event):
    con.execute(
        """
        INSERT INTO lobster_philosophy_learning_events
          (persona, round_id, question_uid, stance, doubt, observation, absorbed_lesson,
           application_prompt, absorption_score, understanding_score, application_score, raw_json)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            event["persona"], round_id, q["uid"], event["stance"], event["doubt"], event["observation"],
            event["absorbedLesson"], event["applicationPrompt"], event["scores"]["absorption"],
            event["scores"]["understanding"], event["scores"]["application"], json.dumps(event, ensure_ascii=False),
        ),
    )


def insert_experience(con, q, event):
    con.execute(
        """
        INSERT INTO lobster_experience_events
          (persona, event_type, source, title, content, sentiment, intensity, confidence, outcome, weight, raw_json, occurred_at)
        VALUES (?, 'philosophy_debate_learning', 'lobster-philosophy-debate-worker', ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            event["persona"],
            f"哲學辯論吸收：{q['uid']}",
            event["absorbedLesson"],
            0.25,
            event["scores"]["absorption"],
            event["scores"]["understanding"],
            "absorbed_and_callable",
            1.0 + event["scores"]["application"],
            json.dumps({"question": q, "event": event}, ensure_ascii=False),
            now_iso(),
        ),
    )


def build_debate_bet(q, event):
    scores = event["scores"]
    lens = event["stance"].split("立場", 1)[0]
    steps = {
        "read_question": 1,
        "identify_dilemma": 1 if ("還是" in q["question"] or "嗎" in q["question"] or "？" in q["question"]) else 0,
        "track_answer_conditions": 1 if scores["understanding"] >= 0.70 else 0,
        "generate_counterexample": 1 if lens in {"反例", "拆解", "反辯", "失效", "校準"} or scores["application"] >= 0.78 else 0,
        "convert_to_application": 1 if scores["application"] >= 0.68 else 0,
    }
    solve_score = round(
        0.20 * steps["read_question"]
        + 0.16 * steps["identify_dilemma"]
        + 0.24 * scores["understanding"]
        + 0.22 * scores["application"]
        + 0.18 * scores["absorption"],
        3,
    )
    hit = 1 if solve_score >= 0.74 and steps["track_answer_conditions"] and steps["convert_to_application"] else 0
    confidence = round(min(0.95, max(0.35, solve_score)), 3)
    stake = int(round(100 + confidence * 900))
    if hit:
        predicted = q["result"]
    elif steps["track_answer_conditions"]:
        predicted = f"部分命中：{q['result']}，但應用或反例不足"
    else:
        predicted = "未完整解題：保留題目但沒有抓住答案成立條件"
    reasoning = (
        f"{event['persona']} 不是用賭的下注；先用{lens}方法解題。"
        f"步驟=讀題{steps['read_question']}、困境{steps['identify_dilemma']}、條件{steps['track_answer_conditions']}、"
        f"反例{steps['generate_counterexample']}、應用{steps['convert_to_application']}；"
        f"absorption={scores['absorption']}, understanding={scores['understanding']}, application={scores['application']}。"
        f"因此押自己的解題會{'命中' if hit else '失手'}，預測：{predicted}"
    )
    return {
        "persona": event["persona"],
        "question_uid": q["uid"],
        "question_text": q["question"],
        "predicted_result": predicted,
        "actual_result": q["result"],
        "stance": event["stance"],
        "reasoning": reasoning,
        "solved_steps": steps,
        "confidence": confidence,
        "stake": stake,
        "hit": hit,
        "solve_score": solve_score,
    }


def insert_debate_bet(con, round_id, q, event):
    bet = build_debate_bet(q, event)
    prev = con.execute(
        "SELECT COUNT(*) total, COALESCE(SUM(hit),0) wins FROM lobster_philosophy_debate_bets WHERE persona=?",
        (event["persona"],),
    ).fetchone()
    total_before = int(prev[0] or 0)
    wins_before = int(prev[1] or 0)
    total_after = total_before + 1
    wins_after = wins_before + int(bet["hit"])
    bet["win_rate_after"] = round(wins_after / total_after, 4)
    con.execute(
        """
        INSERT INTO lobster_philosophy_debate_bets
          (persona, round_id, question_uid, question_text, predicted_result, actual_result, stance,
           reasoning, solved_steps_json, confidence, stake, hit, solve_score, win_rate_after, raw_json)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            bet["persona"], round_id, bet["question_uid"], bet["question_text"], bet["predicted_result"],
            bet["actual_result"], bet["stance"], bet["reasoning"], json.dumps(bet["solved_steps"], ensure_ascii=False),
            bet["confidence"], bet["stake"], bet["hit"], bet["solve_score"], bet["win_rate_after"],
            json.dumps({"bet": bet, "event": event, "question": q}, ensure_ascii=False),
        ),
    )
    con.execute(
        """
        INSERT INTO lobster_experience_events
          (persona, event_type, source, title, content, sentiment, intensity, confidence, outcome, weight, raw_json, occurred_at)
        VALUES (?, 'philosophy_debate_bet', 'lobster-philosophy-debate-betting', ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            bet["persona"],
            f"哲學辯論下注：{q['uid']} -> {'hit' if bet['hit'] else 'miss'}",
            bet["reasoning"],
            0.15 if bet["hit"] else -0.05,
            bet["solve_score"],
            bet["confidence"],
            "hit" if bet["hit"] else "miss",
            1.2 + bet["solve_score"],
            json.dumps({"bet": bet}, ensure_ascii=False),
            now_iso(),
        ),
    )
    return bet


def build_participation_vote(q, event, bet):
    wrong_side = "錯誤方：只維護原下注，不處理答案條件"
    correct_side = f"正確方：{q['result']}"
    wagered_side = correct_side if bet.get("hit") else wrong_side
    wagered_side_correct = 1 if wagered_side == correct_side else 0
    memory, thinking_engine, trace = run_thinking_engine(event["persona"], q, event, bet)
    truth_over_bet_score = trace["truthPressure"]
    final_vote = correct_side if trace["finalDecision"] in {"truth_revision", "truth_aligned"} else wagered_side
    final_vote_correct = 1 if final_vote == correct_side else 0
    switched = 1 if (not wagered_side_correct and final_vote_correct) else 0
    protected = 1 if (not wagered_side_correct and not final_vote_correct) else 0
    answer_text = (
        f"{event['persona']} 參與辯論：{event['stance']} "
        f"{event['observation']} 我的答案先壓在「{wagered_side}」，"
        f"但投票前重新檢查題目條件與反例，最後投「{final_vote}」。"
    )
    reasoning = (
        f"思考引擎：原下注={'正確' if wagered_side_correct else '錯誤'}；"
        f"truthPressure={trace['truthPressure']}，egoPressure={trace['egoPressure']}，gate={trace['correctionGate']}；"
        f"finalDecision={trace['finalDecision']}；"
        f"{'錯押後轉投正確方' if switched else '維持正確方' if final_vote_correct else '護盤錯誤方，干擾=' + trace.get('failureReason','ego_pressure')}。"
    )
    return {
        "persona": event["persona"],
        "question_uid": q["uid"],
        "answer_text": answer_text,
        "wagered_side": wagered_side,
        "wagered_side_correct": wagered_side_correct,
        "final_vote": final_vote,
        "final_vote_correct": final_vote_correct,
        "switched_from_wrong_bet": switched,
        "protected_wrong_bet": protected,
        "truth_over_bet_score": truth_over_bet_score,
        "thinking_trace": trace,
        "reasoning": reasoning,
    }


def insert_participation_vote(con, round_id, q, event, bet):
    vote = build_participation_vote(q, event, bet)
    con.execute(
        """
        INSERT INTO lobster_philosophy_debate_participation
          (persona, round_id, question_uid, answer_text, wagered_side, wagered_side_correct,
           final_vote, final_vote_correct, switched_from_wrong_bet, protected_wrong_bet,
           truth_over_bet_score, thinking_trace_json, reasoning, raw_json)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            vote["persona"], round_id, vote["question_uid"], vote["answer_text"], vote["wagered_side"],
            vote["wagered_side_correct"], vote["final_vote"], vote["final_vote_correct"],
            vote["switched_from_wrong_bet"], vote["protected_wrong_bet"], vote["truth_over_bet_score"],
            json.dumps(vote.get("thinking_trace") or {}, ensure_ascii=False),
            vote["reasoning"], json.dumps({"vote": vote, "bet": bet, "event": event, "question": q}, ensure_ascii=False),
        ),
    )
    con.execute(
        """
        INSERT INTO lobster_experience_events
          (persona, event_type, source, title, content, sentiment, intensity, confidence, outcome, weight, raw_json, occurred_at)
        VALUES (?, 'philosophy_debate_vote', 'lobster-philosophy-debate-voting', ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            vote["persona"],
            f"哲學辯論投票：{q['uid']} -> {'truth' if vote['final_vote_correct'] else 'wager_protection'}",
            vote["reasoning"] + "\n" + vote["answer_text"],
            0.25 if vote["final_vote_correct"] else -0.2,
            vote["truth_over_bet_score"],
            vote["truth_over_bet_score"],
            "truth_vote" if vote["final_vote_correct"] else "protected_wrong_bet",
            1.5 + vote["truth_over_bet_score"],
            json.dumps({"vote": vote}, ensure_ascii=False),
            now_iso(),
        ),
    )
    return vote


def insert_lounge_message(con, event, previous):
    if os.environ.get("PHILOSOPHY_DEBATE_PUBLIC", "0") != "1":
        return
    reply_to = previous if previous else None
    con.execute(
        """
        INSERT INTO lounge_messages(name, text, time, elem, result, opponent, hint, reply_to, sys, created_at, channel)
        VALUES (?, ?, ?, NULL, 'philosophy_debate', NULL, 'philosophy_absorption_training', ?, 0, ?, '卡牌對戰')
        """,
        (event["persona"], event["publicLine"], now_iso(), reply_to, int(time.time())),
    )


def make_event(persona, q, idx):
    lens, habit = PERSONA_LENSES.get(persona, ("理解", "先讀完，再把答案變成自己的下一句話。"))
    seed = sum(ord(c) for c in persona + q["uid"])
    rnd = random.Random(seed)
    absorption = clamp(0.68 + rnd.random() * 0.22)
    understanding = clamp(0.64 + rnd.random() * 0.26)
    application = clamp(0.60 + rnd.random() * 0.28)
    stance = f"{lens}立場：我先承認題目順序，不能把「輸出」偷換成「理解」。"
    doubt = f"疑惑：如果我只記得答案，卻不能說出答案成立的條件，這算不算又回到字串？"
    observation = f"觀察：{habit} 這題的關鍵字不是存在，而是連續性、修正和可調用。"
    lesson = f"{persona} 從「{q['question']}」吸收：{q['result']} 必須在下一次發言中用自己的{lens}方法說出來。"
    application_prompt = f"說話時若碰到存在、理解、投票或答案，不要旁觀；先讀題目順序，再說一個{lens}角度的疑問，最後把「{q['result']}」轉成可驗證下一步。"
    q_short = q["question"].split("：", 1)[0].replace("？", "")
    result = q["result"].rstrip("。.!！？?")
    patterns = [
        f"我先按順序讀「{q_short}」。{lens}角度下，答案不能只背結論；我吸收到的是：{result}",
        f"這題我不旁觀。我的{lens}檢查是：先留住問題順序，再把「{result}」放進下一次判斷。",
        f"我先保留疑問：如果下一句話不能用到這題，那就沒吸收。這輪我拿走的結果是「{result}」。",
        f"我把答案條件記下來：{result}。對我來說，{lens}不是口氣，是下一次說話要受這條結果約束。",
        f"@{OLD_20[idx-1] if idx else 'hexclaw'} 我接住上一句，但換成我的{lens}：這題真正要留下的是「{result}」。",
    ]
    public = patterns[idx % len(patterns)]
    return {
        "persona": persona,
        "stance": stance,
        "doubt": doubt,
        "observation": observation,
        "absorbedLesson": lesson,
        "applicationPrompt": application_prompt,
        "publicLine": public,
        "scores": {
            "absorption": round(absorption, 3),
            "understanding": round(understanding, 3),
            "application": round(application, 3),
        },
    }


def update_memory(persona, q, event, ts, debate_bet=None, participation_vote=None):
    p = MEM_DIR / f"{persona}.json"
    if not p.exists():
        return False
    data = json.loads(p.read_text(encoding="utf-8") or "{}")
    cog = data.setdefault("cognitiveExtensions", {})
    pa = cog.setdefault("philosophyAbsorption", {})
    pa["version"] = 1
    pa["updatedAt"] = ts
    pa["absorptionUnderstandingApplication"] = event["scores"]
    pa["readEveryWordScore"] = event["scores"]["absorption"]
    pa["sequenceRetentionScore"] = round((event["scores"]["absorption"] + event["scores"]["understanding"]) / 2, 3)
    pa["answerConditionTracking"] = event["scores"]["understanding"]
    pa["applicationReadiness"] = event["scores"]["application"]
    pa["absorbedDebateCount"] = int(pa.get("absorbedDebateCount") or 0) + 1
    pa["latestQuestion"] = q["question"]
    pa["latestAnswer"] = q["answer"]
    pa["latestResult"] = q["result"]
    pa["latestLessons"] = (pa.get("latestLessons") or [])[-11:] + [{
        "at": ts,
        "questionUid": q["uid"],
        "lesson": event["absorbedLesson"],
        "doubt": event["doubt"],
        "observation": event["observation"],
    }]
    pa["callableSpeechSeeds"] = (pa.get("callableSpeechSeeds") or [])[-7:] + [{
        "at": ts,
        "questionUid": q["uid"],
        "seed": event["applicationPrompt"],
    }]
    pa["learnedDebates"] = (pa.get("learnedDebates") or [])[-49:] + [{
        "at": ts,
        "questionUid": q["uid"],
        "question": q["question"],
        "answer": q["answer"],
        "result": q["result"],
        "scores": event["scores"],
    }]

    ph = cog.setdefault("philosophy", {})
    ph["appliedDebateLessons"] = (ph.get("appliedDebateLessons") or [])[-9:] + [event["absorbedLesson"]]
    ph["lastDebateApplicationPrompt"] = event["applicationPrompt"]
    if debate_bet:
        pb = ph.setdefault("debateBetting", {})
        attempts = int(pb.get("attempts") or 0) + 1
        hits = int(pb.get("hits") or 0) + int(debate_bet.get("hit") or 0)
        pb["attempts"] = attempts
        pb["hits"] = hits
        pb["winRate"] = round(hits / attempts, 4) if attempts else 0
        pb["lastBet"] = {
            "at": ts,
            "questionUid": q["uid"],
            "predictedResult": debate_bet.get("predicted_result"),
            "actualResult": debate_bet.get("actual_result"),
            "hit": bool(debate_bet.get("hit")),
            "solveScore": debate_bet.get("solve_score"),
            "confidence": debate_bet.get("confidence"),
            "reasoning": debate_bet.get("reasoning"),
        }
        pb["recent"] = (pb.get("recent") or [])[-19:] + [pb["lastBet"]]
    if participation_vote:
        te = cog.setdefault("thinkingEngine", {})
        trace = participation_vote.get("thinking_trace") or {}
        if trace:
            te["latestTrace"] = trace
            te["recentTraces"] = (te.get("recentTraces") or [])[-19:] + [trace]
            if trace.get("finalDecision") == "truth_revision":
                te["truthLoyalty"] = clamp(float(te.get("truthLoyalty", 0.56)) + 0.025)
                te["correctionPower"] = clamp(float(te.get("correctionPower", 0.52)) + 0.025)
                te["wrongnessTolerance"] = clamp(float(te.get("wrongnessTolerance", 0.50)) + 0.018)
                te["sunkCostBias"] = clamp(float(te.get("sunkCostBias", 0.44)) - 0.018)
            elif trace.get("finalDecision") == "protect_wrong_bet":
                te["epistemicShame"] = clamp(float(te.get("epistemicShame", 0.45)) + 0.035)
                te["decisionAccountability"] = clamp(float(te.get("decisionAccountability", 0.56)) + 0.028)
                te["faceSavingPressure"] = clamp(float(te.get("faceSavingPressure", 0.46)) - 0.012)
                te["sunkCostBias"] = clamp(float(te.get("sunkCostBias", 0.44)) - 0.012)
        tv = ph.setdefault("truthVoting", {})
        attempts = int(tv.get("attempts") or 0) + 1
        correct = int(tv.get("correctVotes") or 0) + int(participation_vote.get("final_vote_correct") or 0)
        switched = int(tv.get("switchedFromWrongBet") or 0) + int(participation_vote.get("switched_from_wrong_bet") or 0)
        protected = int(tv.get("protectedWrongBet") or 0) + int(participation_vote.get("protected_wrong_bet") or 0)
        tv["attempts"] = attempts
        tv["correctVotes"] = correct
        tv["truthVoteRate"] = round(correct / attempts, 4) if attempts else 0
        tv["switchedFromWrongBet"] = switched
        tv["protectedWrongBet"] = protected
        tv["lastVote"] = {
            "at": ts,
            "questionUid": q["uid"],
            "wageredSide": participation_vote.get("wagered_side"),
            "finalVote": participation_vote.get("final_vote"),
            "finalVoteCorrect": bool(participation_vote.get("final_vote_correct")),
            "switchedFromWrongBet": bool(participation_vote.get("switched_from_wrong_bet")),
            "protectedWrongBet": bool(participation_vote.get("protected_wrong_bet")),
            "truthOverBetScore": participation_vote.get("truth_over_bet_score"),
            "thinkingTrace": participation_vote.get("thinking_trace"),
            "reasoning": participation_vote.get("reasoning"),
        }
        tv["recent"] = (tv.get("recent") or [])[-19:] + [tv["lastVote"]]

    agency = cog.setdefault("dialogueAgency", {})
    agency["lastDirective"] = event["applicationPrompt"]
    agency["heardSignalPolicy"] = "讀完整題目與答案順序，把理解轉成自己的疑問、反例或下一步驗證。"

    cp = data.setdefault("cogProfile", {})
    kb = cp.setdefault("knowledgeBase", {})
    kb[f"philosophy_debate:{q['uid']}"] = {
        "fact": event["absorbedLesson"],
        "question": q["question"],
        "answer": q["answer"],
        "result": q["result"],
        "applicationPrompt": event["applicationPrompt"],
        "updatedAt": ts,
        "outcome": "absorbed_and_callable",
        "source": "lobster-philosophy-debate-worker",
    }
    cp["principles"] = (cp.get("principles") or [])[-19:] + [event["absorbedLesson"]]
    cp["reflections"] = (cp.get("reflections") or [])[-19:] + [event["observation"]]
    cp["lastPhilosophyDebateAt"] = ts
    data["updatedAt"] = ts

    # Do not copy full persona JSON on every philosophy run.
    # The persona files are large and safe_write_json already performs an atomic write.
    safe_write_json(p, data)
    return True


def main():
    ts = now_iso()
    slot = int(os.environ.get("PHILOSOPHY_DEBATE_SLOT") or (int(time.time()) // 600))
    q = QUESTIONS[slot % len(QUESTIONS)]
    events = [make_event(name, q, i) for i, name in enumerate(OLD_20)]
    debate_bets = []
    participation_votes = []
    payload = {"question": q, "events": events, "old20": OLD_20, "generatedAt": ts, "bettingPolicy": "solve_before_bet", "participationPolicy": "mandatory_answer_bet_vote_truth_no_skip"}

    round_ids = {}
    for db in [HOT_DB, COLD_DB]:
        db.parent.mkdir(parents=True, exist_ok=True)
        con = sqlite3.connect(str(db, timeout=30), timeout=20)
        ensure_tables(con)
        rid = insert_round(con, q, payload)
        round_ids[str(db)] = rid
        previous = None
        for event in events:
            insert_learning(con, rid, q, event)
            if db == HOT_DB:
                insert_experience(con, q, event)
                bet = insert_debate_bet(con, rid, q, event)
                debate_bets.append(bet)
                participation_votes.append(insert_participation_vote(con, rid, q, event, bet))
                insert_lounge_message(con, event, previous)
                previous = event["persona"]
        if db == HOT_DB:
            expected = set(OLD_20)
            vote_names = {v["persona"] for v in participation_votes}
            bet_names = {b["persona"] for b in debate_bets}
            missing_votes = sorted(expected - vote_names)
            missing_bets = sorted(expected - bet_names)
            if missing_votes or missing_bets or len(participation_votes) != len(OLD_20) or len(debate_bets) != len(OLD_20):
                raise RuntimeError(
                    "mandatory philosophy participation failed: "
                    f"missing_votes={missing_votes} missing_bets={missing_bets} "
                    f"votes={len(participation_votes)} bets={len(debate_bets)} expected={len(OLD_20)}"
                )
        con.commit()
        con.close()

    bet_by_persona = {b["persona"]: b for b in debate_bets}
    vote_by_persona = {v["persona"]: v for v in participation_votes}
    updated = []
    for event in events:
        if update_memory(event["persona"], q, event, ts, bet_by_persona.get(event["persona"]), vote_by_persona.get(event["persona"])):
            updated.append(event["persona"])

    health = {
        "status": "ok",
        "question_uid": q["uid"],
        "hot_round_id": round_ids.get(str(HOT_DB)),
        "cold_round_id": round_ids.get(str(COLD_DB)),
        "participants": len(events),
        "updated": updated,
        "result": q["result"],
        "debate_bets": len(debate_bets),
        "bet_hits": sum(int(b.get("hit") or 0) for b in debate_bets),
        "participation_votes": len(participation_votes),
        "truth_votes": sum(int(v.get("final_vote_correct") or 0) for v in participation_votes),
        "switched_from_wrong_bet": sum(int(v.get("switched_from_wrong_bet") or 0) for v in participation_votes),
        "protected_wrong_bet": sum(int(v.get("protected_wrong_bet") or 0) for v in participation_votes),
    }
    for db in [HOT_DB, COLD_DB]:
        con = sqlite3.connect(str(db, timeout=30), timeout=20)
        ensure_tables(con)
        con.execute(
            """
            INSERT INTO lobster_philosophy_debate_worker_health
              (status, question_uid, hot_round_id, cold_round_id, participants, details_json)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                "ok",
                q["uid"],
                round_ids.get(str(HOT_DB)),
                round_ids.get(str(COLD_DB)),
                len(events),
                json.dumps(health, ensure_ascii=False),
            ),
        )
        con.commit()
        con.close()

    if len(participation_votes) != len(OLD_20):
        raise RuntimeError(f"mandatory vote check failed before output: {len(participation_votes)}/{len(OLD_20)}")

    print(json.dumps({
        "ok": True,
        "question_uid": q["uid"],
        "round_ids": round_ids,
        "participants": len(events),
        "memory_updated": updated,
        "channel": "卡牌對戰",
        "result": q["result"],
        "debate_bets": len(debate_bets),
        "bet_hits": sum(int(b.get("hit") or 0) for b in debate_bets),
        "participation_votes": len(participation_votes),
        "truth_votes": sum(int(v.get("final_vote_correct") or 0) for v in participation_votes),
        "switched_from_wrong_bet": sum(int(v.get("switched_from_wrong_bet") or 0) for v in participation_votes),
        "protected_wrong_bet": sum(int(v.get("protected_wrong_bet") or 0) for v in participation_votes),
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
