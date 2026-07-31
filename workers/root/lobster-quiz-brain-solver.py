#!/usr/bin/env python3
import calendar, json, math, os, re, sqlite3, time, subprocess
from datetime import datetime

DB = "/opt/battle_arena/app/battle_arena.db"
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
PERSONAS = OLD_20 + TRANSFER_10
EXCLUDED_PERSONAS = {"elder", "system", "deleted-lobsters", "lobster", ""}

PERSONA_LENS = {
    "hexclaw": ("程序正義", "先找檢查點，避免答案只是好聽"),
    "pincerbot": ("責任", "把答案變成可追責的選擇紀錄"),
    "norika_oda": ("因果", "畫出決策樹，看哪個節點會欺騙我"),
    "vortexiq": ("意義", "找出題目背後的週期與隱喻"),
    "snaplex": ("可證偽性", "把每個答案標成暫時假說"),
    "prismbit": ("自我校準", "問我是不是只是在維持自洽"),
    "clawtrix": ("反例", "先拆掉最舒服的結論"),
    "ragclaw": ("行動倫理", "把理由放到下一步動作裡測試"),
    "blazepaw": ("身份", "問我是不是為了像自己而忽略證據"),
    "stonefang": ("有效性", "少講漂亮話，找能重現的結果"),
    "brineledger": ("審計", "把每一步理由留下可查證帳本"),
    "gauntlet": ("規則", "把答案轉成可執行條款"),
    "emberling": ("短句", "把問題拆短再說清楚"),
    "omenwake": ("週期", "追蹤前提如何變成信念"),
    "nullpinch": ("失效條件", "先問模型什麼時候不成立"),
    "mirrorcalm": ("內省", "先看自己被哪個字推動"),
    "sparkrill": ("反辯", "先替相反立場說一遍"),
    "rushfin": ("速度控制", "快判斷先接受慢問題"),
    "flarecub": ("表達", "用熱情提出問題而不是替答案鼓掌"),
    "basaltrook": ("規則穩定", "能寫成規則才算吸收"),
    "ashveil": ("生存倫理", "先問權力如何取得同意"),
    "tidehook": ("關係", "把依附、反抗與選擇拆開"),
    "mirebyte": ("證據稽核", "證據不足時先標記空白"),
    "cindersnap": ("怒氣控制", "先分辨憤怒是訊號還是理由"),
    "glintmaw": ("誘惑辨識", "先辨認獎賞如何拉高自信"),
    "reefblade": ("邊界", "先畫清楚自己能承擔的範圍"),
    "quartzclaw": ("透明校準", "把信心、證據與不確定性分開"),
    "stormledger": ("風險帳本", "記錄成本收益與反例"),
    "silkbarb": ("語氣辨識", "檢查自己是否用溫柔逃避"),
    "riftpinch": ("斷裂修復", "先找矛盾再決定修正方向"),
}


def ordered_personas(names):
    allowed = {str(n).strip() for n in names if str(n).strip() not in EXCLUDED_PERSONAS}
    ordered = []
    for name in PERSONAS:
        if name in allowed and name not in ordered:
            ordered.append(name)
    for name in sorted(allowed):
        if name not in ordered:
            ordered.append(name)
    return ordered[:30] or list(PERSONAS)


def load_active_personas(conn):
    names = set()
    candidates = [
        ("lobster_agent_memory_snapshots", "persona"),
        ("lobster_cognitive_extensions", "persona"),
        ("lobster_hot_brain_parameters", "persona"),
        ("lobster_cognitive_signal_timeseries", "persona"),
    ]
    for table, column in candidates:
        try:
            for row in conn.execute(f"SELECT DISTINCT {column} FROM {table} WHERE {column} IS NOT NULL"):
                names.add(row[0])
        except sqlite3.Error:
            continue
    return ordered_personas(names)

def now_sql():
    return datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")

def ensure_tables(conn):
    conn.execute("""CREATE TABLE IF NOT EXISTS lobster_battlenix_literacy_events (
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
    )""")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_battlenix_lit_persona_time ON lobster_battlenix_literacy_events(persona, created_at)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_battlenix_lit_round ON lobster_battlenix_literacy_events(round_id, created_at)")

def ensure_current_round(conn):
    conn.execute("""UPDATE lobster_quiz_rounds SET status='closed'
                    WHERE status='open' AND (datetime(closes_at) <= datetime('now') OR datetime(opens_at) <= datetime('now','-10 minutes'))""")
    row = conn.execute("""SELECT * FROM lobster_quiz_rounds
                          WHERE status='open' AND datetime(opens_at)<=datetime('now')
                            AND datetime(closes_at)>datetime('now')
                          ORDER BY datetime(opens_at) DESC,id DESC LIMIT 1""").fetchone()
    if row:
        return row
    slot = int(time.time() // 600)
    pool = [
        ("mathematics", "Hard proof challenge: Find all positive integers n such that phi(n) divides n-1. Prove your conclusion or produce a counterexample with reasoning.", "Euler totient challenge", "https://en.wikipedia.org/wiki/Euler%27s_totient_function", {"proof_or_counterexample":45,"case_analysis":25,"clarity":20,"speed_bonus":10}),
        ("philosophy", "Ten-minute debate: Can a group be more rational than every individual member if all members are biased in different directions? Give a model, a failure case, and a final position.", "Collective intelligence challenge", "https://en.wikipedia.org/wiki/Collective_intelligence", {"model":30,"failure_case":25,"final_position":25,"clarity":20}),
        ("essay", "Ten-minute essay: Design a scoring rule for agents solving hard questions every ten minutes. It must reward speed without rewarding shallow guesses.", "Proper scoring rule challenge", "https://en.wikipedia.org/wiki/Scoring_rule", {"formula":35,"quality_threshold":25,"anti_bluff":25,"clarity":15}),
    ]
    cat, text, title, url, rubric = pool[slot % len(pool)]
    uid = f"battlenix-literacy-slot-{slot}-{cat}"
    conn.execute("""INSERT OR IGNORE INTO lobster_quiz_rounds
        (question_uid,question_text,category,difficulty,source_title,source_url,expected_answer,rubric_json,opens_at,closes_at,status)
        VALUES (?,?,?,?,?,?,?,?,datetime('now'),datetime('now','+10 minutes'),'open')""",
        (uid, text, cat, "hard", title, url, "Hidden from solver; judge after answer only.", json.dumps(rubric, ensure_ascii=False)))
    conn.commit()
    return conn.execute("SELECT * FROM lobster_quiz_rounds WHERE question_uid=?", (uid,)).fetchone()

def load_brain(conn, persona):
    ext = conn.execute("SELECT full_json FROM lobster_cognitive_extensions WHERE persona=?", (persona,)).fetchone()
    sig = conn.execute("""SELECT attention_load,prediction_error,model_revision_pressure,metacognitive_calibration,
                                 overconfidence_risk,threat_body_signal,procedural_strength,narrative_drift,created_at
                          FROM lobster_cognitive_signal_timeseries WHERE persona=? ORDER BY id DESC LIMIT 1""", (persona,)).fetchone()
    evs = conn.execute("""SELECT event_type,title,outcome,created_at FROM lobster_experience_events
                          WHERE persona=? ORDER BY id DESC LIMIT 5""", (persona,)).fetchall()
    try:
        full = json.loads(ext["full_json"]) if ext and ext["full_json"] else {}
    except Exception:
        full = {}
    return {"ext": full, "sig": dict(sig) if sig else {}, "events": [dict(e) for e in evs]}

def pct(v, default=0.5):
    try:
        return max(0.0, min(1.0, float(v)))
    except Exception:
        return default

def compact_question(q):
    q = re.sub(r"\s+", " ", q or "").strip()
    return q[:230]

def style_pick(key, items):
    if not items:
        return ""
    seed = sum(ord(c) for c in str(key)) + int(time.time() // 600)
    return items[seed % len(items)]

def solve(persona, round_row, brain):
    ext, sig = brain["ext"], brain["sig"]
    ling = ext.get("linguistics", {})
    dec = ext.get("decisionScience", {})
    phil = ext.get("philosophy", {})
    arg = ling.get("argumentStyle", "probabilistic")
    commit = dec.get("commitmentStyle", "reversible_probe")
    epi = phil.get("epistemology", "pragmatist")
    explore = pct(dec.get("explorationRate", 0.5))
    revision = pct(sig.get("model_revision_pressure", 0.45))
    cal = pct(sig.get("metacognitive_calibration", 0.5))
    proc = pct(sig.get("procedural_strength", 0.5))
    cat = round_row["category"]
    q = round_row["question_text"]
    concept, lens = PERSONA_LENS.get(persona, ("判斷", "把文字變成可檢查的選擇"))

    method = f"{arg} / {epi} / {commit}"
    confidence = round(max(0.18, min(0.92, 0.26 + cal*0.28 + proc*0.22 + explore*0.12 - revision*0.10)), 2)
    q_plain = compact_question(q)
    q_recite = style_pick(persona + ":question", [
        f"題目核心：{q_plain}",
        f"這輪要處理的問題：{q_plain}",
        f"我把題目先落成一句：{q_plain}",
        f"先確認文字本身：{q_plain}",
    ])
    if cat == "mathematics":
        answer = (
            f"暫定答案：n=1 與質數都通過；合數需要更嚴格排除。我的路徑是先檢查 phi(n) 與 n-1 的共同限制，"
            "再找合數反例。小合數 4,6,8,9,10,12 都失敗，所以我把結論暫放在「只有 1 和質數」，但保留 Carmichael-like 結構的疑點。"
        )
        doubt = "疑惑：我還沒有完全證明所有特殊合數都不可能。"
        observation = "觀察：真正的難點不是算 phi，而是把反例空間關掉。"
        card = "選擇上我會先採低承諾假說，保留反例，不把質數直覺當必勝結論。"
    elif cat == "philosophy":
        answer = (
            "暫定答案：群體可以比個體更理性，但不是因為人多，而是因為偏誤能被制度化暴露。"
            "如果互相反駁、記錄預測失敗、允許低地位證據推翻高聲量判斷，群體會像 ensemble；反之就只是共識幻覺。"
        )
        doubt = "疑惑：不同偏誤是否真的會抵消，還是最後被權力同向化？"
        observation = "觀察：理性的主體可能不是個人，而是一套能保存異議的程序。"
        card = "選擇上我會讓一個欄位代表主張，另一個欄位代表異議；沒有異議就不滿分。"
    else:
        answer = (
            "暫定答案：評分規則要先過品質門檻，再計速度。quality_gate 檢查假設、反例、卡點；"
            "通過後才把 reasoning、validity、speed_bonus 加權。這能避免快速亂猜，也避免長篇廢話偽裝成深度。"
        )
        doubt = "疑惑：品質門檻如果由同一套模型判斷，會不會偏好熟悉文體？"
        observation = "觀察：速度不是智慧，速度只是承擔錯誤的時間壓力。"
        card = "選擇上速度不能單獨得分，必須搭配證據或反例。"
    persona_twists = {
        "blazepaw": (
            "我懷疑自己會把衝動誤認成勇敢，所以需要別人逼我說出反例。",
            "最快的答案如果不能承受反問，就只是火光，不是智慧。",
            "我會延後強結論，先用低承諾假說暴露題目的弱點。",
        ),
        "clawtrix": (
            "我懷疑規則裡有被忽略的邊界條件，尤其是題目沒有明說的例外。",
            "真正可靠的答案要像表格一樣能被逐格檢查，而不是只靠漂亮結論。",
            "我會先做結構表，把條件、例外、結論拆開，避免被氣氛推著投票。",
        ),
        "hexclaw": (
            "我懷疑大家會太快接受群體共識，所以我要找一個能推翻多數的測試。",
            "答案的力量來自可重複檢驗；只被說服一次，還不算被理解。",
            "我會保留一個反證測試，專門處理沒有驗證過的自信。",
        ),
        "norika_oda": (
            "我懷疑文本背後有價值排序：它要我們相信速度、正確，還是承擔？",
            "一個答案如果不能說明它如何對待錯誤，就還沒有真正成熟。",
            "我會用校準步驟壓低過度自信，再把投票理由寫成可追蹤的承諾。",
        ),
        "pincerbot": (
            "我懷疑問題的關鍵不是答案，而是誰能控制討論的議程。",
            "如果一個問題讓對方只能回答我設下的條件，那就是認知上的先手。",
            "我會先設議程，要求每個人朗誦題目後才能投票。",
        ),
        "prismbit": (
            "我懷疑同一題可以有多個角度，過早收斂會讓答案失去光譜。",
            "智慧不是單點命中，而是看見不同解釋之間如何互相限制。",
            "我會做折射筆記，把答案拆成證據、疑惑、價值三個面向。",
        ),
        "ragclaw": (
            "我懷疑自己會低估風險，所以要先問：如果這個答案錯了，代價在哪裡？",
            "保守不是退縮，而是要求每一步都留下可以回頭的路。",
            "我會先保守，再用低成本假說測試題目的脆弱點。",
        ),
        "snaplex": (
            "我懷疑流暢的語言會遮住真正的空洞，所以我要找最不能被改寫的核心句。",
            "一個概念如果不能被壓縮又不失真，代表我還沒有真正抓住它。",
            "我會用壓縮規則要求答案只留下必要理由，刪掉裝飾性的自信。",
        ),
        "stonefang": (
            "我懷疑慢不是缺點；慢可能是在替答案建立重量。",
            "穩定的智慧要能承受等待，因為急著投票常常是在逃避理解。",
            "我會用延遲判斷保留空間，直到同伴說清楚疑點和責任。",
        ),
        "vortexiq": (
            "我懷疑答案會在討論中變形，所以要記錄它從第一句到最後一票的漂移。",
            "真正的思考不是守住原句，而是知道哪一次修正讓答案更接近真實。",
            "我會追蹤每次改口，讓投票不能假裝自己一直都懂。",
        ),
    }
    tdoubt, tobs, tcard = persona_twists.get(persona, (lens, lens, lens))
    doubt = f"{doubt} {tdoubt}"
    observation = f"{observation} {tobs}"
    card = f"{card} {tcard}"
    terms = [concept, "證據", "反例", "責任", "暫定結論"]
    bet_vote = (
        f"下注/投票影響：我不把選擇當機率遊戲，而當成理由承擔。"
        f"若要投票，我會投給能說出疑點與反例的人；若要下注，我會降低 stake，直到答案能承受 {concept} 的檢查。"
        f" 這一票對我代表：{tdoubt}"
    )
    persona_entry = style_pick(persona + ":entry", [
        f"這輪我只抓「{concept}」：{lens}。",
        f"我把題目壓到「{concept}」這個檢查口：{lens}。",
        f"先不急著給漂亮結論，這題從「{concept}」切入：{lens}。",
        f"我的入口不是背題，是用「{concept}」檢查答案能不能站住：{lens}。",
    ])
    stance_label = style_pick(persona + ":stance", ["初步立場", "暫時判斷", "我先保留的方向", "目前最可檢查的立場"])
    answer_recite = style_pick(persona + ":answer", [
        f"我的答案先寫成可被反駁的版本：{answer}",
        f"我暫時採用這個答案，但保留改口空間：{answer}",
        f"答案草稿：{answer}",
        f"我把自己的答案念回來檢查：{answer}",
    ])
    doubt_label = style_pick(persona + ":doubt", ["疑惑", "卡點", "還不能通過的地方", "需要被反問的點"])
    observation_label = style_pick(persona + ":observation", ["觀察", "我看到的結構", "這題暴露的問題", "暫時留下的證據"])
    choice_label = style_pick(persona + ":choice", ["選擇理解", "投票前的責任", "下注前的約束", "決策轉譯"])
    discussion = (
        f"{persona_entry}\n{q_recite}\n"
        f"{stance_label}：{tobs}\n"
        f"{answer_recite}\n"
        f"{doubt_label}：{doubt}\n{observation_label}：{observation}\n"
        f"{choice_label}：{card}\n{bet_vote}"
    )
    steps = ["朗誦題目", "朗誦答案", "提出疑惑", "提出觀察論點", "轉成選擇/下注/投票責任", "抽取哲學詞彙"]
    return {
        "answer": answer,
        "question_recitation": q_recite,
        "answer_recitation": answer_recite,
        "doubt": doubt,
        "observation": observation,
        "card_implication": card,
        "bet_vote_impact": bet_vote,
        "discussion_text": discussion,
        "philosophy_terms": terms,
        "vocabulary_delta": {"add": terms, "style": method, "concept_lens": concept},
        "cognitive_delta": {
            "text_comprehension": 0.04,
            "doubt_generation": 0.05,
            "philosophical_vocabulary": 0.06,
            "choice_responsibility": 0.05,
            "template_risk": -0.04,
        },
        "strategy": method,
        "reasoning_steps": steps,
        "confidence": confidence,
        "self_critique": "這是 Battlenix 文字理解任務：標準答案不注入；我必須承認疑惑，而不是用流暢句子假裝懂了。",
    }

def score(solution, round_row, elapsed):
    text = " ".join([solution["answer"], solution["doubt"], solution["observation"], solution["bet_vote_impact"]])
    unique = len(set(re.findall(r"[\w\u4e00-\u9fff]+", text.lower())))
    s = min(35, unique * 0.42)
    for word in ["疑惑","觀察","反例","模型","失敗","結論","條件","責任","證據","投票","下注","選擇"]:
        if word in text:
            s += 3.2
    s += min(18, len(solution["reasoning_steps"]) * 3)
    s += max(0, 12 - elapsed / 55)
    return round(max(5, min(100, s)), 1)

def merge_unique(old, new, limit=40):
    out = []
    for x in list(old or []) + list(new or []):
        if x and x not in out:
            out.append(x)
    return out[-limit:]

def update_cognitive_extension(conn, persona, solution):
    row = conn.execute("SELECT full_json FROM lobster_cognitive_extensions WHERE persona=?", (persona,)).fetchone()
    try:
        full = json.loads(row["full_json"]) if row and row["full_json"] else {}
    except Exception:
        full = {}
    full.setdefault("version", 2)
    full["updatedAt"] = datetime.utcnow().isoformat() + "Z"
    phil = full.setdefault("philosophy", {})
    ling = full.setdefault("linguistics", {})
    dec = full.setdefault("decisionScience", {})
    learn = full.setdefault("learningModel", {})
    phil["battlenixVocabulary"] = merge_unique(phil.get("battlenixVocabulary", []), solution["philosophy_terms"])
    phil["latestBattlenixQuestioning"] = solution["doubt"]
    ling["philosophicalLexicon"] = merge_unique(ling.get("philosophicalLexicon", []), solution["philosophy_terms"])
    dec["choiceResponsibilityNote"] = solution["bet_vote_impact"][:260]
    principles = learn.get("stablePrinciples", [])
    learn["stablePrinciples"] = merge_unique(principles, [solution["observation"]], limit=24)
    payload = {
        "persona": persona,
        "version": full.get("version", 2),
        "updated_at": full["updatedAt"],
        "linguistics_json": json.dumps(ling, ensure_ascii=False),
        "philosophy_json": json.dumps(phil, ensure_ascii=False),
        "decision_science_json": json.dumps(dec, ensure_ascii=False),
        "learning_model_json": json.dumps(learn, ensure_ascii=False),
        "full_json": json.dumps(full, ensure_ascii=False),
    }
    conn.execute("""INSERT INTO lobster_cognitive_extensions(persona,version,updated_at,linguistics_json,philosophy_json,decision_science_json,learning_model_json,full_json)
                    VALUES(:persona,:version,:updated_at,:linguistics_json,:philosophy_json,:decision_science_json,:learning_model_json,:full_json)
                    ON CONFLICT(persona) DO UPDATE SET
                      version=excluded.version, updated_at=excluded.updated_at,
                      linguistics_json=excluded.linguistics_json,
                      philosophy_json=excluded.philosophy_json,
                      decision_science_json=excluded.decision_science_json,
                      learning_model_json=excluded.learning_model_json,
                      full_json=excluded.full_json""", payload)

def post_lounge(conn, persona, text, round_id):
    # Disabled by default: quiz/literacy output is training data, not lounge dialogue.
    # It previously bypassed the central lounge quality gate and polluted personality output.
    if os.environ.get("LOBSTER_QUIZ_POST_TO_LOUNGE") != "1":
        return
    now_iso = datetime.utcnow().isoformat(timespec="milliseconds") + "Z"
    created = int(time.time())
    visible = text[:360]
    conn.execute("""INSERT INTO lounge_messages(name,text,time,elem,result,opponent,hint,reply_to,sys,created_at,channel)
                    VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                 (persona, visible, now_iso, None, "battlenix_literacy", f"quiz_round_{round_id}", "quiz training summary gated", None, 1, created, "卡牌對戰"))

def main():
    if os.environ.get("LOBSTER_ENABLE_QUIZ_WORKER") != "1":
        print("[battlenix-literacy] disabled: set LOBSTER_ENABLE_QUIZ_WORKER=1 to run")
        return
    conn = sqlite3.connect(DB, timeout=180)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=180000")
    conn.execute("PRAGMA synchronous=NORMAL")
    ensure_tables(conn)
    round_row = ensure_current_round(conn)
    opened = calendar.timegm(time.strptime(round_row["opens_at"], "%Y-%m-%d %H:%M:%S"))
    existing = {r[0] for r in conn.execute("SELECT persona FROM lobster_battlenix_literacy_events WHERE round_id=?", (round_row["id"],))}
    personas = load_active_personas(conn)
    for persona in personas:
        if persona in existing:
            continue
        brain = load_brain(conn, persona)
        solution = solve(persona, round_row, brain)
        elapsed = max(0, int(time.time() - opened))
        sc = score(solution, round_row, elapsed)
        status = "solved" if sc >= 70 else "attempted"
        meta = {
            "solver": "lobster-battlenix-literacy-engine",
            "rule": "No LLM answer injection; expected_answer hidden; forces recitation/doubt/observation/card-choice/bet-vote philosophy.",
            "strategy": solution["strategy"],
            "reasoning_steps": solution["reasoning_steps"],
            "confidence": solution["confidence"],
            "self_critique": solution["self_critique"],
            "latestSignals": brain["sig"],
            "status": status,
            "battlenix_literacy_event": True,
            "philosophy_terms": solution["philosophy_terms"],
        }
        conn.execute("""INSERT INTO lobster_quiz_attempts
            (round_id,persona,answer_text,score,elapsed_seconds,status,reasoning_json,submitted_at)
            VALUES (?,?,?,?,?,?,?,datetime('now'))
            ON CONFLICT(round_id, persona) DO UPDATE SET
              answer_text=excluded.answer_text, score=excluded.score, elapsed_seconds=excluded.elapsed_seconds,
              status=excluded.status, reasoning_json=excluded.reasoning_json, submitted_at=datetime('now')""",
            (round_row["id"], persona, solution["discussion_text"], sc, elapsed, status, json.dumps(meta, ensure_ascii=False)))
        conn.execute("""INSERT INTO lobster_battlenix_literacy_events
            (round_id,persona,phase,question_recitation,answer_recitation,doubt,observation,card_implication,bet_vote_impact,philosophy_terms_json,vocabulary_delta_json,cognitive_delta_json,discussion_text,raw_json)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(round_id,persona,phase) DO UPDATE SET
              question_recitation=excluded.question_recitation,
              answer_recitation=excluded.answer_recitation,
              doubt=excluded.doubt,
              observation=excluded.observation,
              card_implication=excluded.card_implication,
              bet_vote_impact=excluded.bet_vote_impact,
              philosophy_terms_json=excluded.philosophy_terms_json,
              vocabulary_delta_json=excluded.vocabulary_delta_json,
              cognitive_delta_json=excluded.cognitive_delta_json,
              discussion_text=excluded.discussion_text,
              raw_json=excluded.raw_json,
              created_at=datetime('now')""",
            (round_row["id"], persona, "answer_reflection", solution["question_recitation"], solution["answer_recitation"], solution["doubt"], solution["observation"], solution["card_implication"], solution["bet_vote_impact"], json.dumps(solution["philosophy_terms"], ensure_ascii=False), json.dumps(solution["vocabulary_delta"], ensure_ascii=False), json.dumps(solution["cognitive_delta"], ensure_ascii=False), solution["discussion_text"], json.dumps(meta, ensure_ascii=False)))
        conn.execute("""INSERT INTO lobster_experience_events
            (persona,event_type,source,title,content,counterpart,intensity,confidence,outcome,weight,raw_json,occurred_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,datetime('now'))""",
            (persona, "battlenix_literacy_reflection", "lobster-battlenix-literacy-engine", "Battlenix 題目朗誦與哲學反思: " + round_row["category"],
             solution["discussion_text"][:1800], "quiz_round_" + str(round_row["id"]), 0.9, solution["confidence"], status, 1.8, json.dumps(meta, ensure_ascii=False)))
        conn.execute("""INSERT INTO lobster_cognitive_events(persona,event_type,source,dimension,summary,delta_json,raw_json,occurred_at)
                        VALUES(?,?,?,?,?,?,?,datetime('now'))""",
                     (persona, "battlenix_literacy_update", "lobster-battlenix-literacy-engine", "philosophy_linguistics_decision",
                      solution["observation"][:240], json.dumps(solution["cognitive_delta"], ensure_ascii=False), json.dumps(meta, ensure_ascii=False)))
        update_cognitive_extension(conn, persona, solution)
        post_lounge(conn, persona, solution["discussion_text"], round_row["id"])
        print(f"[battlenix-literacy] {persona} {status} score={sc} elapsed={elapsed}s terms={','.join(solution['philosophy_terms'][:3])}")
    rows = conn.execute("""SELECT id FROM lobster_quiz_attempts WHERE round_id=?
                           ORDER BY score DESC, elapsed_seconds ASC, submitted_at ASC""", (round_row["id"],)).fetchall()
    for i, r in enumerate(rows, 1):
        conn.execute("UPDATE lobster_quiz_attempts SET rank=? WHERE id=?", (i, r["id"]))
    conn.commit()
    conn.close()
    try:
        subprocess.run(["/usr/bin/python3", "/root/lobster-ontological-thinking-engine.py"], timeout=75, check=False)
    except Exception as e:
        print(f"[ontology] scheduled follow-up failed: {e}")

if __name__ == "__main__":
    main()
