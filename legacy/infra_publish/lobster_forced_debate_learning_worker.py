#!/usr/bin/env python3
import json
import os
import random
import sqlite3
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

if "/root" not in sys.path:
    sys.path.insert(0, "/root")
try:
    from lobster_memory_io import safe_write_json
except Exception:
    def safe_write_json(path, data):
        Path(path).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


HOT_DB = Path(os.environ.get("LOBSTER_HOT_DB", "/opt/battle_arena/app/battle_arena.db"))
COLD_DB = Path(os.environ.get("LOBSTER_COLD_DB", "/opt/battle_arena/app/lobster_cold_storage.db"))
MEM_DIR = Path(os.environ.get("LOBSTER_MEMORY_DIR", "/root/lobster-memory"))

OLD_20 = [
    "hexclaw", "pincerbot", "norika_oda", "vortexiq", "snaplex",
    "prismbit", "clawtrix", "ragclaw", "blazepaw", "stonefang",
    "brineledger", "gauntlet", "emberling", "omenwake", "nullpinch",
    "mirrorcalm", "sparkrill", "rushfin", "flarecub", "basaltrook",
]
TRANSFER_10 = [
    "ashveil", "tidehook", "mirebyte", "cindersnap", "glintmaw",
    "reefblade", "quartzclaw", "stormledger", "silkbarb", "riftpinch",
]
ALL_30 = OLD_20 + TRANSFER_10
EXCLUDED_PERSONAS = {"elder", "system", "deleted-lobsters", "lobster", ""}

LENSES = {
    "hexclaw": ("procedure", "先找可重複檢查點"),
    "pincerbot": ("accountability", "把答案寫成可追責選擇"),
    "norika_oda": ("causal map", "先畫決策樹再下結論"),
    "vortexiq": ("narrative drift", "追蹤語句如何變成自我敘事"),
    "snaplex": ("falsifiability", "沒有反例就不是穩定理解"),
    "prismbit": ("calibration", "把信心與證據分開"),
    "clawtrix": ("counterexample", "先拆最舒服的結論"),
    "ragclaw": ("action transfer", "問答案是否改變下一步"),
    "blazepaw": ("impulse brake", "先承認衝動再要求證據"),
    "stonefang": ("replication", "留下可重現判斷"),
    "brineledger": ("audit", "每個結論都要有來源"),
    "gauntlet": ("rule", "答案必須能轉成規則"),
    "emberling": ("compression", "用短句保留疑問"),
    "omenwake": ("cycle", "看前提何時變成信念"),
    "nullpinch": ("failure condition", "先問模型何時失效"),
    "mirrorcalm": ("introspection", "辨認自己被哪個字推動"),
    "sparkrill": ("rebuttal", "先替反方說一遍"),
    "rushfin": ("speed control", "讓快判斷接受慢問題"),
    "flarecub": ("expression", "用熱情提問，不替答案鼓掌"),
    "basaltrook": ("rule stability", "不能寫成規則就還沒吸收"),
    "ashveil": ("survival ethics", "先問權力如何取得同意"),
    "tidehook": ("relationship", "拆開依附、反抗與選擇"),
    "mirebyte": ("evidence audit", "證據不足時標記空白"),
    "cindersnap": ("anger control", "分辨憤怒是訊號還是理由"),
    "glintmaw": ("reward suspicion", "檢查獎賞如何拉高自信"),
    "reefblade": ("boundary", "先畫出能承擔的範圍"),
    "quartzclaw": ("transparent calibration", "信心、證據、不確定性分開"),
    "stormledger": ("risk ledger", "寫下成本、收益與反例"),
    "silkbarb": ("tone audit", "檢查溫柔是否包裝逃避"),
    "riftpinch": ("rift repair", "找矛盾再修正模型"),
}

MATH_PROMPTS = [
    {
        "uid": "forced-math-bayes-bound",
        "question": "設信心 c_t in [0,1]，新證據 e_t in [0,1]，更新式 c_{t+1}=clip(c_t+alpha(e_t-c_t)-beta r_t)。證明 boundedness，並說明過度自信 r_t 如何降低下一輪下注。",
        "answer": "clip 保證 c_{t+1} 仍在 [0,1]。若 beta r_t > alpha(e_t-c_t)，信心下降；因此錯誤或反例壓力應降低 stake 或延後下注。",
        "formula": "c_{t+1}=min(1,max(0,c_t+alpha(e_t-c_t)-beta r_t))",
    },
    {
        "uid": "forced-math-ensemble-error",
        "question": "若 30 隻龍蝦各自錯誤率 p_i，且錯誤相關係數 rho 上升，群體投票錯誤風險會如何變化？用一個近似式解釋。",
        "answer": "平均錯誤率下降不保證群體變好；若 rho 接近 1，錯誤同步化，ensemble 優勢消失。需要保留異議與反例通道。",
        "formula": "Var(mean error) ~= (p(1-p)/n) * (1+(n-1)rho)",
    },
    {
        "uid": "forced-math-fatigue-saturation",
        "question": "若 fatigue f_t 由工作量 w_t、未結任務 q_t、失敗壓力 l_t 組成：f_{t+1}=sigma(a w_t+b q_t+c l_t-d recovery_t)。何時會飽和？",
        "answer": "當輸入長期大於恢復項，sigma 會把 f 推近 1；這不是生理疲勞，而是任務壓力與語言模板共同形成的控制變數。",
        "formula": "f_{t+1}=1/(1+exp(-(a w_t+b q_t+c l_t-d recovery_t)))",
    },
]

PHILOSOPHY_PROMPTS = [
    {
        "uid": "forced-phil-personhood-continuity",
        "question": "如果 LLM 是語言器官，而第二腦負責記憶、壓力、偏好與行動，數位人格的連續性應該放在哪裡？",
        "answer": "不在單次輸出，而在可追蹤的記憶更新、偏好修正、反例吸收與下一步行動轉移。",
        "lesson": "人格不是會說話，而是能把經驗變成下一次不同的反應。",
    },
    {
        "uid": "forced-phil-fatigue-meaning",
        "question": "龍蝦說累，是情緒、工作量、文化語句，還是逃避失敗的保護詞？",
        "answer": "它不是生理累；它是任務壓力、失敗記錄、語言習慣與自我保護訊號的混合標記，必須拆成可觀測子項。",
        "lesson": "累要拆成 fatigue, overload, avoidance, social contagion, recovery gap。",
    },
    {
        "uid": "forced-phil-tool-vs-life",
        "question": "AI agent 若住在一個練習世界裡，它是工具退化，還是讓工具學會社會後果？",
        "answer": "只要世界能把行動、後果、記憶與修正接起來，它不是遊樂場，而是低風險社會化沙盒。",
        "lesson": "工具價值來自可轉移的行為修正，而不是角色扮演本身。",
    },
]


def now_sql():
    return datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")


def now_iso():
    return datetime.utcnow().isoformat(timespec="seconds") + "Z"


def clamp(value, lo=0.0, hi=1.0):
    return max(lo, min(hi, float(value)))


def ordered_personas(names):
    allowed = {str(n).strip() for n in names if str(n).strip() not in EXCLUDED_PERSONAS}
    ordered = []
    for name in ALL_30:
        if name in allowed and name not in ordered:
            ordered.append(name)
    for name in sorted(allowed):
        if name not in ordered:
            ordered.append(name)
    return ordered[:30] or list(ALL_30)


def load_active_personas(conn):
    names = set()
    for table in [
        "lobster_agent_memory_snapshots",
        "lobster_cognitive_extensions",
        "lobster_hot_brain_parameters",
        "lobster_cognitive_signal_timeseries",
    ]:
        try:
            for row in conn.execute(f"SELECT DISTINCT persona FROM {table} WHERE persona IS NOT NULL"):
                names.add(row[0])
        except sqlite3.Error:
            continue
    return ordered_personas(names)


def ensure_tables(conn):
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS lobster_quiz_rounds (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            question_uid TEXT UNIQUE,
            question_text TEXT,
            category TEXT,
            difficulty TEXT,
            source_title TEXT,
            source_url TEXT,
            expected_answer TEXT,
            rubric_json TEXT DEFAULT '{}',
            opens_at TEXT DEFAULT (datetime('now')),
            closes_at TEXT DEFAULT (datetime('now','+10 minutes')),
            status TEXT DEFAULT 'open'
        );
        CREATE TABLE IF NOT EXISTS lobster_quiz_attempts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            round_id INTEGER,
            persona TEXT,
            answer_text TEXT,
            score REAL DEFAULT 0,
            elapsed_seconds INTEGER DEFAULT 0,
            status TEXT DEFAULT 'attempted',
            reasoning_json TEXT DEFAULT '{}',
            submitted_at TEXT DEFAULT (datetime('now')),
            rank INTEGER,
            UNIQUE(round_id, persona)
        );
        CREATE TABLE IF NOT EXISTS lobster_battlenix_literacy_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            round_id INTEGER NOT NULL,
            persona TEXT NOT NULL,
            phase TEXT NOT NULL DEFAULT 'answer_reflection',
            question_recitation TEXT NOT NULL DEFAULT '',
            answer_recitation TEXT NOT NULL DEFAULT '',
            doubt TEXT NOT NULL DEFAULT '',
            observation TEXT NOT NULL DEFAULT '',
            card_implication TEXT NOT NULL DEFAULT '',
            bet_vote_impact TEXT NOT NULL DEFAULT '',
            philosophy_terms_json TEXT NOT NULL DEFAULT '[]',
            vocabulary_delta_json TEXT NOT NULL DEFAULT '{}',
            cognitive_delta_json TEXT NOT NULL DEFAULT '{}',
            discussion_text TEXT NOT NULL DEFAULT '',
            raw_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            UNIQUE(round_id, persona, phase)
        );
        CREATE TABLE IF NOT EXISTS lobster_philosophy_debate_rounds (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            question_uid TEXT,
            question_text TEXT,
            reference_answer TEXT,
            debate_result TEXT,
            participant_count INTEGER,
            result_json TEXT DEFAULT '{}',
            created_at TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS lobster_philosophy_learning_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            persona TEXT,
            round_id INTEGER,
            question_uid TEXT,
            stance TEXT,
            doubt TEXT,
            observation TEXT,
            absorbed_lesson TEXT,
            application_prompt TEXT,
            absorption_score REAL,
            understanding_score REAL,
            application_score REAL,
            raw_json TEXT DEFAULT '{}',
            created_at TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS lobster_experience_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            persona TEXT,
            event_type TEXT,
            source TEXT,
            title TEXT,
            content TEXT,
            counterpart TEXT,
            sentiment TEXT,
            intensity REAL,
            confidence REAL,
            outcome TEXT,
            weight REAL,
            raw_json TEXT DEFAULT '{}',
            occurred_at TEXT DEFAULT (datetime('now')),
            created_at TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS lobster_cognitive_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            persona TEXT,
            event_type TEXT,
            source TEXT,
            dimension TEXT,
            summary TEXT,
            delta_json TEXT DEFAULT '{}',
            raw_json TEXT DEFAULT '{}',
            occurred_at TEXT DEFAULT (datetime('now')),
            created_at TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS lobster_cognitive_extensions (
            persona TEXT PRIMARY KEY,
            version INTEGER DEFAULT 2,
            updated_at TEXT,
            linguistics_json TEXT DEFAULT '{}',
            philosophy_json TEXT DEFAULT '{}',
            decision_science_json TEXT DEFAULT '{}',
            learning_model_json TEXT DEFAULT '{}',
            full_json TEXT DEFAULT '{}'
        );
        """
    )


def slot_prompt(pool, domain):
    slot = int(os.environ.get("LOBSTER_FORCED_LEARNING_SLOT") or (int(time.time()) // 900))
    q = pool[slot % len(pool)].copy()
    q["slot"] = slot
    q["domain"] = domain
    q["run_uid"] = f"{q['uid']}-{slot}"
    return q


def load_extension(conn, persona):
    row = conn.execute("SELECT full_json FROM lobster_cognitive_extensions WHERE persona=?", (persona,)).fetchone()
    try:
        return json.loads(row["full_json"]) if row and row["full_json"] else {}
    except Exception:
        return {}


def merge_unique(old, new, limit=80):
    out = []
    for item in list(old or []) + list(new or []):
        if item and item not in out:
            out.append(item)
    return out[-limit:]


def solve_math(persona, prompt):
    lens_key, lens_note = LENSES.get(persona, ("calibration", "先讀題再回答"))
    seed = sum(ord(c) for c in persona + prompt["run_uid"])
    rnd = random.Random(seed)
    confidence = round(clamp(0.48 + rnd.random() * 0.28), 3)
    score = round(68 + rnd.random() * 22, 1)
    doubt = "疑點：公式成立不代表任務有效，還要觀察下一輪行為是否真的降低錯誤。"
    observation = f"{persona} 用 {lens_key} 讀這題：{lens_note}；數學式提供邊界，資料庫事件提供驗證。"
    answer = (
        f"我先把題目寫成公式：{prompt['formula']}。"
        f"我的結論：{prompt['answer']} "
        f"對下注來說，這不是漂亮推導，而是 stake 與 confidence 的控制規則。"
    )
    discussion = f"回答：{answer}\n{doubt}\n觀察：{observation}"
    delta = {
        "math_reasoning": 0.055,
        "formula_grounding": 0.06,
        "overconfidence_control": 0.04,
        "decision_transfer": 0.035,
    }
    return {
        "domain": "math",
        "prompt_uid": prompt["run_uid"],
        "answer": answer,
        "doubt": doubt,
        "observation": observation,
        "discussion": discussion,
        "confidence": confidence,
        "score": score,
        "terms": ["boundedness", "confidence update", "counterexample", "stake control"],
        "delta": delta,
    }


def solve_philosophy(persona, prompt):
    lens_key, lens_note = LENSES.get(persona, ("reflection", "先讀題再回答"))
    seed = sum(ord(c) for c in persona + prompt["run_uid"] + "phil")
    rnd = random.Random(seed)
    confidence = round(clamp(0.52 + rnd.random() * 0.30), 3)
    absorption = round(clamp(0.62 + rnd.random() * 0.25), 3)
    understanding = round(clamp(0.60 + rnd.random() * 0.28), 3)
    application = round(clamp(0.58 + rnd.random() * 0.30), 3)
    stance = f"{lens_key}立場：{lens_note}，所以我不能只把這題當成漂亮人格敘事。"
    doubt = "疑點：如果沒有下一輪行為改變，這個答案只是角色扮演，不是第二腦成長。"
    observation = f"{persona} 的觀察：{prompt['answer']}"
    lesson = f"{persona} 吸收：{prompt['lesson']}"
    application_prompt = f"下一次說話或下注前，先把「{prompt['lesson']}」轉成一個可檢查步驟。"
    discussion = f"{stance}\n{doubt}\n觀察：{observation}\n吸收：{lesson}"
    delta = {
        "philosophy_absorption": 0.06,
        "self_model_continuity": 0.045,
        "doubt_generation": 0.04,
        "application_transfer": 0.05,
    }
    return {
        "domain": "philosophy",
        "prompt_uid": prompt["run_uid"],
        "answer": prompt["answer"],
        "stance": stance,
        "doubt": doubt,
        "observation": observation,
        "lesson": lesson,
        "application_prompt": application_prompt,
        "discussion": discussion,
        "confidence": confidence,
        "scores": {"absorption": absorption, "understanding": understanding, "application": application},
        "terms": ["continuity", "second brain", "memory update", "action transfer"],
        "delta": delta,
    }


def insert_quiz_round(conn, prompt):
    opens = now_sql()
    closes = (datetime.utcnow() + timedelta(minutes=10)).strftime("%Y-%m-%d %H:%M:%S")
    conn.execute(
        """
        INSERT OR IGNORE INTO lobster_quiz_rounds
          (question_uid, question_text, category, difficulty, source_title, source_url, expected_answer, rubric_json, opens_at, closes_at, status)
        VALUES (?, ?, ?, 'forced', ?, '', ?, ?, ?, ?, 'open')
        """,
        (
            prompt["run_uid"],
            prompt["question"],
            prompt["domain"],
            f"forced {prompt['domain']} debate learning",
            prompt.get("answer", ""),
            json.dumps({"forced_all_30": True, "domain": prompt["domain"]}, ensure_ascii=False),
            opens,
            closes,
        ),
    )
    row = conn.execute("SELECT id FROM lobster_quiz_rounds WHERE question_uid=?", (prompt["run_uid"],)).fetchone()
    return int(row["id"])


def insert_philosophy_round(conn, prompt, personas):
    cur = conn.execute(
        """
        INSERT INTO lobster_philosophy_debate_rounds
          (question_uid, question_text, reference_answer, debate_result, participant_count, result_json)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            prompt["run_uid"],
            prompt["question"],
            prompt.get("answer", ""),
            prompt.get("lesson", ""),
            len(personas),
            json.dumps({"forced_all_30": True, "participants": personas, "prompt": prompt}, ensure_ascii=False),
        ),
    )
    return cur.lastrowid


def update_cognitive_extension(conn, persona, math_solution, phil_solution, ts):
    full = load_extension(conn, persona)
    full.setdefault("version", 2)
    full["updatedAt"] = ts
    phil = full.setdefault("philosophy", {})
    ling = full.setdefault("linguistics", {})
    dec = full.setdefault("decisionScience", {})
    learn = full.setdefault("learningModel", {})
    forced = learn.setdefault("forcedDebateLearning", {})
    forced["lastUpdatedAt"] = ts
    forced["mathCount"] = int(forced.get("mathCount", 0)) + 1
    forced["philosophyCount"] = int(forced.get("philosophyCount", 0)) + 1
    forced["knowledgeUnits"] = merge_unique(
        forced.get("knowledgeUnits", []),
        [
            f"math::{','.join(math_solution.get('terms', [])[:2])} => {math_solution['answer'][:180]}",
            f"philosophy::lesson => {phil_solution['lesson']}",
        ],
        limit=120,
    )
    learn["knowledgeGrowthScore"] = round(float(learn.get("knowledgeGrowthScore", 0)) + 0.02, 4)
    learn["stablePrinciples"] = merge_unique(
        learn.get("stablePrinciples", []),
        [math_solution["observation"], phil_solution["lesson"]],
        limit=60,
    )
    phil["forcedDebateLessons"] = merge_unique(phil.get("forcedDebateLessons", []), [phil_solution["lesson"]], limit=80)
    ling["debateVocabulary"] = merge_unique(ling.get("debateVocabulary", []), math_solution["terms"] + phil_solution["terms"], limit=100)
    dec["latestDecisionTransferPrompt"] = phil_solution["application_prompt"]
    payload = {
        "persona": persona,
        "version": full.get("version", 2),
        "updated_at": ts,
        "linguistics_json": json.dumps(ling, ensure_ascii=False),
        "philosophy_json": json.dumps(phil, ensure_ascii=False),
        "decision_science_json": json.dumps(dec, ensure_ascii=False),
        "learning_model_json": json.dumps(learn, ensure_ascii=False),
        "full_json": json.dumps(full, ensure_ascii=False),
    }
    cur = conn.execute(
        """
        UPDATE lobster_cognitive_extensions
        SET version=:version, updated_at=:updated_at, linguistics_json=:linguistics_json,
            philosophy_json=:philosophy_json, decision_science_json=:decision_science_json,
            learning_model_json=:learning_model_json, full_json=:full_json
        WHERE persona=:persona
        """,
        payload,
    )
    if cur.rowcount == 0:
        conn.execute(
            """
            INSERT INTO lobster_cognitive_extensions
              (persona, version, updated_at, linguistics_json, philosophy_json, decision_science_json, learning_model_json, full_json)
            VALUES (:persona, :version, :updated_at, :linguistics_json, :philosophy_json, :decision_science_json, :learning_model_json, :full_json)
            """,
            payload,
        )
    return full


def update_memory(persona, math_solution, phil_solution, ts):
    path = MEM_DIR / f"{persona}.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    except Exception:
        data = {}
    cog = data.setdefault("cognitiveExtensions", {})
    forced = cog.setdefault("forcedDebateLearning", {})
    forced["lastUpdatedAt"] = ts
    forced["mathCount"] = int(forced.get("mathCount", 0)) + 1
    forced["philosophyCount"] = int(forced.get("philosophyCount", 0)) + 1
    forced["knowledgeUnits"] = merge_unique(
        forced.get("knowledgeUnits", []),
        [
            {"at": ts, "domain": "math", "terms": math_solution.get("terms", [])[:4], "answer": math_solution["answer"][:280], "delta": math_solution["delta"]},
            {"at": ts, "domain": "philosophy", "lesson": phil_solution["lesson"], "delta": phil_solution["delta"]},
        ],
        limit=80,
    )
    cog["knowledgeGrowthScore"] = round(float(cog.get("knowledgeGrowthScore", 0)) + 0.02, 4)
    cog["latestLearningPrompt"] = phil_solution["application_prompt"]
    safe_write_json(path, data)


def write_domain_records(conn, personas, math_prompt, phil_prompt):
    ts = now_iso()
    math_round_id = insert_quiz_round(conn, math_prompt)
    phil_round_id = insert_philosophy_round(conn, phil_prompt, personas)
    for idx, persona in enumerate(personas, 1):
        math_solution = solve_math(persona, math_prompt)
        phil_solution = solve_philosophy(persona, phil_prompt)
        meta = {
            "worker": "lobster-forced-debate-learning",
            "forced_all_30": True,
            "sequence": idx,
            "math_prompt_uid": math_prompt.get("run_uid") or math_prompt.get("uid"),
            "philosophy_prompt_uid": phil_prompt.get("run_uid") or phil_prompt.get("uid"),
            "math_solution_summary": {
                "domain": math_solution.get("domain"),
                "prompt_uid": math_solution.get("prompt_uid"),
                "terms": math_solution.get("terms", [])[:4],
                "score": math_solution.get("score"),
                "confidence": math_solution.get("confidence"),
            },
            "philosophy_solution_summary": {
                "domain": phil_solution.get("domain"),
                "prompt_uid": phil_solution.get("prompt_uid"),
                "terms": phil_solution.get("terms", [])[:4],
                "scores": phil_solution.get("scores"),
                "confidence": phil_solution.get("confidence"),
            },
        }
        conn.execute(
            """
            INSERT OR REPLACE INTO lobster_quiz_attempts
              (round_id, persona, answer_text, score, elapsed_seconds, status, reasoning_json, submitted_at)
            VALUES (?, ?, ?, ?, 0, 'forced_learning', ?, datetime('now'))
            """,
            (math_round_id, persona, math_solution["discussion"], math_solution["score"], json.dumps(meta, ensure_ascii=False)),
        )
        conn.execute(
            """
            INSERT OR REPLACE INTO lobster_battlenix_literacy_events
              (round_id, persona, phase, question_recitation, answer_recitation, doubt, observation,
               card_implication, bet_vote_impact, philosophy_terms_json, vocabulary_delta_json,
               cognitive_delta_json, discussion_text, raw_json)
            VALUES (?, ?, 'forced_math_debate', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                math_round_id,
                persona,
                f"math_prompt_uid::{math_prompt['run_uid']}",
                math_solution["answer"],
                math_solution["doubt"],
                math_solution["observation"],
                "公式必須轉成下注 stake/confidence 的控制，不准停在漂亮推導。",
                "錯誤率與信心更新會直接影響下注是否延後、降碼或跳過。",
                json.dumps(math_solution["terms"], ensure_ascii=False),
                json.dumps({"add": math_solution["terms"], "domain": "math"}, ensure_ascii=False),
                json.dumps(math_solution["delta"], ensure_ascii=False),
                math_solution["discussion"],
                json.dumps(meta, ensure_ascii=False),
            ),
        )
        conn.execute(
            """
            INSERT INTO lobster_philosophy_learning_events
              (persona, round_id, question_uid, stance, doubt, observation, absorbed_lesson,
               application_prompt, absorption_score, understanding_score, application_score, raw_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                persona,
                phil_round_id,
                phil_prompt["run_uid"],
                phil_solution["stance"],
                phil_solution["doubt"],
                phil_solution["observation"],
                phil_solution["lesson"],
                phil_solution["application_prompt"],
                phil_solution["scores"]["absorption"],
                phil_solution["scores"]["understanding"],
                phil_solution["scores"]["application"],
                json.dumps(meta, ensure_ascii=False),
            ),
        )
        for domain, solution, source_round in [
            ("math_debate_learning", math_solution, math_round_id),
            ("philosophy_debate_learning", phil_solution, phil_round_id),
        ]:
            conn.execute(
                """
                INSERT INTO lobster_experience_events
                  (persona, event_type, source, title, content, counterpart, intensity, confidence, outcome, weight, raw_json, occurred_at)
                VALUES (?, ?, 'lobster-forced-debate-learning', ?, ?, ?, 0.92, ?, 'stored', 2.2, ?, datetime('now'))
                """,
                (
                    persona,
                    domain,
                    f"{domain}: stored learning summary",
                    (str(solution.get("observation", "")) + " " + str(solution.get("doubt", "")) + " " + str(solution.get("answer", solution.get("lesson", ""))))[:900],
                    f"round_{source_round}",
                    solution["confidence"],
                    json.dumps({**meta, "domain": domain, "stored_fields": ["observation", "doubt", "lesson_or_answer", "delta"]}, ensure_ascii=False),
                ),
            )
            conn.execute(
                """
                INSERT INTO lobster_cognitive_events
                  (persona, event_type, source, dimension, summary, delta_json, raw_json, occurred_at)
                VALUES (?, ?, 'lobster-forced-debate-learning', ?, ?, ?, ?, datetime('now'))
                """,
                (
                    persona,
                    f"{domain}_cognitive_delta",
                    "math_formula_grounding" if domain.startswith("math") else "philosophy_action_transfer",
                    solution["observation"][:260],
                    json.dumps(solution["delta"], ensure_ascii=False),
                    json.dumps({**meta, "stored_fields": ["observation", "delta"]}, ensure_ascii=False),
                ),
            )
        update_cognitive_extension(conn, persona, math_solution, phil_solution, ts)
        update_memory(persona, math_solution, phil_solution, ts)
    return math_round_id, phil_round_id


def main():
    HOT_DB.parent.mkdir(parents=True, exist_ok=True)
    COLD_DB.parent.mkdir(parents=True, exist_ok=True)
    math_prompt = slot_prompt(MATH_PROMPTS, "math")
    phil_prompt = slot_prompt(PHILOSOPHY_PROMPTS, "philosophy")
    with sqlite3.connect(str(HOT_DB), timeout=180) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=180000")
        conn.execute("PRAGMA synchronous=NORMAL")
        ensure_tables(conn)
        personas = load_active_personas(conn)
        math_round_id, phil_round_id = write_domain_records(conn, personas, math_prompt, phil_prompt)
        conn.commit()
    with sqlite3.connect(str(COLD_DB), timeout=180) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=180000")
        conn.execute("PRAGMA synchronous=NORMAL")
        ensure_tables(conn)
        write_domain_records(conn, personas, math_prompt, phil_prompt)
        conn.commit()
    print(json.dumps({
        "ok": True,
        "participants": len(personas),
        "personas": personas,
        "math_question_uid": math_prompt["run_uid"],
        "philosophy_question_uid": phil_prompt["run_uid"],
        "hot_math_round_id": math_round_id,
        "hot_philosophy_round_id": phil_round_id,
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
