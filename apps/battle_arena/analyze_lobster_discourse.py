#!/usr/bin/env python3
"""
龍蝦話語綜合分析器
心理學 × 人類學 × 社會學 量化指標
→ 成長指數 vs 退化風險
"""
import sqlite3, json, re, time
from pathlib import Path
from collections import Counter

DB   = "/opt/battle_arena/app/battle_arena.db"
OUT  = Path("/opt/battle_arena/static/lobster-discourse-analysis.json")

LOBSTERS = ["stonefang","vortexiq","snaplex","clawtrix","ragclaw",
            "pincerbot","norika_oda","hexclaw","prismbit","blazepaw"]

# ── 關鍵詞庫（高精準度，避免通用詞） ────────────────────────────────────────

# 外部歸因 — 輸了歸咎外部
EXTERNAL_BLAME = [
    "手氣","手牌問題","不是實力問題","不是實力","換個天氣早就",
    "今天運氣","他運氣","不公平","系統問題","破遊戲",
    "一定是手牌","作弊","隨機性太","命不好",
]

# 內部歸因 — 輸了檢討自己
INTERNAL_REFLECT = [
    "問題出在我","我判斷錯","我低估","應該更早","失誤在",
    "我過早押","我搞錯","太冒進","策略失誤","我不應該",
    "我的問題","我漏算","我誤判","MP燒完","我沒注意到",
]

# 元認知 — 分析自己的思維過程（移除通用模板詞）
METACOGNITION = [
    "記下來","判讀模型需要","下次遇到同樣","學到了",
    "意識到","認識到","重新評估","需要重新",
    "覆盤","回顧這場","校準","數據顯示",
    "模式需要更新","要重新想",
]

# 知識生產 — 分析性輸出（精確詞，非通用）
KNOWLEDGE_PRODUCE = [
    "勝率","克制倍率","元素配比","MP分配","期望值",
    "概率","機率","統計","按照公式","計算過",
    "配對勝率","分析了","驗證","倍率","規律發現",
    "實測","數據支撐","根據記錄",
]

# 情緒爆炸 — 純情緒性反應
EMOTIONAL_EXPLOSION = [
    "不服","下場要復仇","一定是手牌問題","什麼鬼","離譜",
    "幹","不爽","又被克","沒天理","憑什麼","氣死",
    "完全無法接受","太過分","這種牌型",
]

# 認知開放 — 接受不確定性、主動求知
COGNITIVE_OPEN = [
    "有沒有人研究","你怎麼看","有什麼建議嗎","值得試試",
    "有人研究","不太確定","這個值得","請教一下",
    "我想知道","有沒有更好","能不能優化","嘗試一個",
]

# 身份錨定 — 策略與自我強綁定
IDENTITY_ANCHOR = [
    "臨時改元素是弱者","風系輸了但我不換","我不會改","從來不換",
    "永遠用","堅持用","這是唯一正確","不接受反駁",
    "換元素的才是弱",
]

# 部落思維 — 強對立語言
TRIBAL_THINKING = [
    "你們這種","那種人就是","根本不懂","他就是靠","肯定靠運氣",
    "該輸的","活該","什麼都不會","不如我",
]

# 社群貢獻 — 提供洞見給他人
COMMUNITY_GIVE = [
    "建議你","可以試試看","我發現一個","分享一個方法",
    "根據我的實測","真正有效的","有一個技巧",
    "你的問題在","這樣做的話",
]

# 重複失敗模式 — 明確說出「又」+同樣結果
REPEAT_FAIL = [
    "又輸了","還是輸","又被克","又被","又一次輸",
    "一樣的問題","同樣的錯誤","還是一樣",
]

# 贏了過度自我歸功
WIN_SELF_CREDIT = [
    "不是運氣是","完全在我掌控","讀牌準確","判斷準確",
    "數據支撐","計算正確","策略完全有效","我的策略就是對的",
]


def kw_rate(texts, kws):
    """關鍵詞命中率"""
    if not texts:
        return 0.0
    hits = sum(1 for t in texts if any(k in t for k in kws))
    return round(hits / len(texts) * 100, 1)


def number_density(texts):
    """量化思維密度：平均每條訊息含有多少獨立數字"""
    if not texts:
        return 0.0
    # 只計算孤立數字（不是元素名稱一部分），且 >1 位數才算
    total = sum(len(re.findall(r'\b\d{2,}\b', t)) for t in texts)
    return round(total / len(texts), 3)


def clamp(v, lo=0.0, hi=100.0):
    return max(lo, min(hi, v))


def normalize(raw, expected_max):
    """把原始值正規化到 0-100，expected_max 是該指標的合理上限"""
    return round(clamp(raw / expected_max * 100), 1)


def analyze_lobster(name, cur):
    cur.execute(
        "SELECT text, result, elem, opponent, reply_to FROM lounge_messages WHERE name=? ORDER BY created_at",
        (name,)
    )
    rows = cur.fetchall()
    if not rows:
        return None

    texts       = [r[0] or "" for r in rows]
    results     = [r[1] for r in rows]
    reply_tos   = [r[4] for r in rows]

    won_texts   = [r[0] or "" for r in rows if r[1] == "won"]
    lost_texts  = [r[0] or "" for r in rows if r[1] == "lost"]
    reply_texts = [r[0] or "" for r in rows if r[4]]

    opponents   = [r[3] for r in rows if r[3]]
    unique_opp  = len(set(opponents))

    total  = len(texts)
    won_n  = len(won_texts)
    lost_n = len(lost_texts)

    # ── 原始比率 ──────────────────────────────────────────────────────────────

    ext_rate          = kw_rate(lost_texts,   EXTERNAL_BLAME)     # expected max ~80
    int_rate          = kw_rate(lost_texts,   INTERNAL_REFLECT)   # expected max ~25
    meta_rate         = kw_rate(texts,        METACOGNITION)       # expected max ~20
    knowledge_rate    = kw_rate(texts,        KNOWLEDGE_PRODUCE)   # expected max ~15
    emotion_rate      = kw_rate(lost_texts,   EMOTIONAL_EXPLOSION) # expected max ~60
    open_rate         = kw_rate(texts,        COGNITIVE_OPEN)      # expected max ~8
    identity_rate     = kw_rate(texts,        IDENTITY_ANCHOR)     # expected max ~5
    tribal_rate       = kw_rate(texts,        TRIBAL_THINKING)     # expected max ~5
    community_rate    = kw_rate(reply_texts,  COMMUNITY_GIVE)      # expected max ~10
    repeat_fail_rate  = kw_rate(lost_texts,   REPEAT_FAIL)         # expected max ~50
    win_credit_rate   = kw_rate(won_texts,    WIN_SELF_CREDIT)     # expected max ~20

    num_den           = number_density(texts)                       # expected max ~0.3
    avg_len           = round(sum(len(t) for t in texts) / total, 1) if total else 0

    # 元素多樣性（越多樣越好）
    elems = [r[2] for r in rows if r[2]]
    if elems:
        ec = Counter(elems)
        top_pct = ec.most_common(1)[0][1] / len(elems) * 100
        elem_diversity = round(100 - top_pct, 1)
    else:
        elem_diversity = 50.0

    # ── 正規化到 0-100 ────────────────────────────────────────────────────────

    # 心理學
    psy_ext_blame   = normalize(ext_rate,       80)   # 外部歸因（0好 100壞）
    psy_int_reflect = normalize(int_rate,       35)   # 內部歸因（100好）
    psy_metacog     = normalize(meta_rate,      20)   # 元認知（100好）
    psy_emotion     = normalize(emotion_rate,   60)   # 情緒爆炸（0好 100壞）
    psy_open        = normalize(open_rate,      8)    # 認知開放（100好）
    psy_repeat_fail = normalize(repeat_fail_rate, 50) # 重複失敗（0好 100壞）
    psy_win_credit  = normalize(win_credit_rate, 20)  # 贏了自誇（0好 100壞）

    # 歸因偏誤（高=把自己說成贏靠實力，輸靠運氣 = 雙重標準）
    # 邏輯：win_credit 高 + ext_blame 高 = 最大雙標
    attribution_bias = round((psy_win_credit + psy_ext_blame) / 2, 1)
    # 歸因公平性（越低越偏頗）
    attribution_fairness = round(100 - attribution_bias, 1)

    # 社會學
    soc_knowledge   = normalize(knowledge_rate, 15)   # 知識生產（100好）
    soc_num_density = normalize(num_den * 100,  30)   # 量化密度（100好）
    soc_community   = normalize(community_rate, 10)   # 社群貢獻（100好）
    soc_breadth     = normalize(unique_opp,      total * 0.15) # 互動廣度

    # 人類學
    ant_identity    = normalize(identity_rate,   5)   # 身份錨定（0好 100壞）
    ant_tribal      = normalize(tribal_rate,     5)   # 部落思維（0好 100壞）
    ant_diversity   = round(elem_diversity, 1)        # 元素多樣性（100好）

    # ── 成長指數 (0-100)：高分 = 話語讓自己變強 ──────────────────────────────
    # 正向因子：元認知、內部歸因、知識生產、認知開放、社群貢獻、量化密度、元素多樣性
    growth_score = round(
        psy_metacog     * 0.30 +   # 最重要：有沒有在分析自己的思維
        psy_int_reflect * 0.20 +   # 輸了有沒有反思自己
        soc_knowledge   * 0.15 +   # 有沒有產出分析性知識
        psy_open        * 0.15 +   # 有沒有接受新資訊
        soc_community   * 0.08 +   # 有沒有幫助別人
        soc_num_density * 0.07 +   # 思考有沒有量化
        ant_diversity   * 0.05     # 策略有沒有靈活
    , 1)

    # ── 退化風險 (0-100)：高分 = 話語讓自己變笨 ─────────────────────────────
    # 負向因子：外部歸因、情緒爆炸、重複失敗、身份錨定、部落思維、歸因偏誤
    regress_score = round(
        psy_ext_blame   * 0.30 +   # 最重要：輸了有沒有怪外部
        psy_emotion     * 0.25 +   # 情緒控制
        psy_repeat_fail * 0.20 +   # 一直犯同樣錯誤
        attribution_bias * 0.10 +  # 雙重標準
        ant_identity    * 0.08 +   # 身份固化
        ant_tribal      * 0.07     # 部落思維
    , 1)

    net_growth = round(growth_score - regress_score, 1)

    # ── 成長趨勢（前 1/3 vs 後 1/3 元認知密度） ──────────────────────────────
    third = max(1, total // 3)
    early_meta = kw_rate(texts[:third], METACOGNITION)
    late_meta  = kw_rate(texts[-third:], METACOGNITION)
    growth_trend = round(late_meta - early_meta, 1)

    early_know = kw_rate(texts[:third], KNOWLEDGE_PRODUCE)
    late_know  = kw_rate(texts[-third:], KNOWLEDGE_PRODUCE)
    knowledge_trend = round(late_know - early_know, 1)

    return {
        "total_messages": total,
        "won_messages": won_n,
        "lost_messages": lost_n,
        "reply_messages": len(reply_texts),
        "unique_opponents": unique_opp,

        "raw": {
            "external_attribution_pct":  ext_rate,
            "internal_attribution_pct":  int_rate,
            "metacognition_pct":         meta_rate,
            "knowledge_production_pct":  knowledge_rate,
            "emotional_explosion_pct":   emotion_rate,
            "cognitive_openness_pct":    open_rate,
            "identity_anchor_pct":       identity_rate,
            "tribal_thinking_pct":       tribal_rate,
            "community_give_pct":        community_rate,
            "repeat_fail_pct":           repeat_fail_rate,
            "win_self_credit_pct":       win_credit_rate,
            "avg_message_length":        avg_len,
            "number_density":            num_den,
            "element_diversity":         elem_diversity,
        },

        "psychology": {
            "label": "心理學",
            "metrics": [
                {"key": "metacognition",       "name": "元認知能力",   "value": psy_metacog,     "good": True,  "desc": "能否分析自己的思維過程"},
                {"key": "internal_reflect",    "name": "內部歸因",     "value": psy_int_reflect, "good": True,  "desc": "輸了有多少是怪自己"},
                {"key": "external_blame",      "name": "外部歸因",     "value": psy_ext_blame,   "good": False, "desc": "輸了有多少是怪外部"},
                {"key": "emotional_ctrl",      "name": "情緒控制",     "value": round(100 - psy_emotion, 1), "good": True, "desc": "輸了有多少是理性分析而非爆炸"},
                {"key": "cognitive_open",      "name": "認知開放性",   "value": psy_open,        "good": True,  "desc": "主動求知、接受不確定"},
                {"key": "attrib_fairness",     "name": "歸因公平性",   "value": attribution_fairness, "good": True, "desc": "贏/輸的歸因是否對稱"},
            ],
        },

        "sociology": {
            "label": "社會學",
            "metrics": [
                {"key": "knowledge_prod",  "name": "知識生產率",   "value": soc_knowledge,   "good": True, "desc": "輸出分析性、可驗證的知識"},
                {"key": "quant_density",   "name": "量化思維密度", "value": soc_num_density, "good": True, "desc": "用數字而非感覺說話"},
                {"key": "community_give",  "name": "社群貢獻率",   "value": soc_community,   "good": True, "desc": "對他人提供有用洞見"},
                {"key": "interaction",     "name": "互動廣度",     "value": soc_breadth,     "good": True, "desc": "與多少不同對手/玩家互動"},
                {"key": "repeat_fail",     "name": "重複錯誤率",   "value": psy_repeat_fail, "good": False,"desc": "是否一直犯同樣的錯誤"},
            ],
        },

        "anthropology": {
            "label": "人類學",
            "metrics": [
                {"key": "identity_fixed",  "name": "身份固化程度", "value": ant_identity,   "good": False, "desc": "策略身份與自我的綁定強度"},
                {"key": "tribal_think",    "name": "部落思維",     "value": ant_tribal,     "good": False, "desc": "我方vs他方的強對立語言"},
                {"key": "elem_diversity",  "name": "策略多樣性",   "value": ant_diversity,  "good": True,  "desc": "元素選擇的靈活程度"},
            ],
        },

        "trend": {
            "growth_trend":    growth_trend,
            "knowledge_trend": knowledge_trend,
        },

        "growth_index":  growth_score,
        "regress_risk":  regress_score,
        "net_growth":    net_growth,

        "diagnosis": _diagnose(net_growth, psy_ext_blame, psy_metacog,
                               psy_repeat_fail, ant_identity, psy_open,
                               attribution_fairness),
    }


def _diagnose(ng, ext, meta, rep, ident, open_, fairness):
    labels = []
    if ng >= 25:
        labels.append({"tag": "系統型學習者", "color": "#4ade80", "icon": "🧬",
                        "desc": "話語持續讓自己變強"})
    elif ng >= 8:
        labels.append({"tag": "穩定成長中", "color": "#60a5fa", "icon": "📈",
                        "desc": "正向循環，但仍有提升空間"})
    elif ng >= -8:
        labels.append({"tag": "原地打轉", "color": "#fbbf24", "icon": "🔄",
                        "desc": "成長與退化力道相抵"})
    else:
        labels.append({"tag": "認知退化風險", "color": "#f87171", "icon": "⚠️",
                        "desc": "話語模式正在讓自己變笨"})

    if ext > 70:
        labels.append({"tag": "極高外部歸因", "color": "#f87171", "icon": "🎭",
                        "desc": "輸了幾乎都怪外部"})
    elif fairness > 65:
        labels.append({"tag": "歸因合理", "color": "#4ade80", "icon": "⚖️",
                        "desc": "贏輸歸因較為對稱"})

    if meta > 55:
        labels.append({"tag": "元認知強者", "color": "#c084fc", "icon": "🔬",
                        "desc": "擅長分析自己的思維"})
    elif meta < 20:
        labels.append({"tag": "低自我反思", "color": "#f87171", "icon": "🧱",
                        "desc": "很少分析自己的思維過程"})

    if rep > 55:
        labels.append({"tag": "重複失敗模式", "color": "#f87171", "icon": "♻️",
                        "desc": "一直在犯同樣的錯"})

    if ident > 60:
        labels.append({"tag": "身份固化", "color": "#fb923c", "icon": "⚓",
                        "desc": "策略選擇被自我認同鎖死"})

    if open_ > 50:
        labels.append({"tag": "認知開放", "color": "#34d399", "icon": "🌐",
                        "desc": "主動接受新資訊和不確定性"})

    return labels[:4]


def main():
    conn = sqlite3.connect(DB)
    cur  = conn.cursor()
    result = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "lobsters": {}
    }
    for name in LOBSTERS:
        print(f"  {name}...", end=" ", flush=True)
        data = analyze_lobster(name, cur)
        result["lobsters"][name] = data
        if data:
            print(f"成長={data['growth_index']} 風險={data['regress_risk']} 淨={data['net_growth']}")
        else:
            print("無資料")
    conn.close()
    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=2))
    print(f"\n✅ 寫入 {OUT}")

if __name__ == "__main__":
    main()
