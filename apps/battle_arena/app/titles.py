"""
100 player titles — tier 1 (noob) to tier 100 (legend)
Every 5 tiers = one rank band. Used for random display in matches.
"""

TITLES = [
    # ── 第 1-10 tier：完全菜鳥 ──
    {"tier": 1,  "name": "牌路盲",       "en": "Lost Newbie"},
    {"tier": 2,  "name": "手氣靠天",     "en": "Pure Luck"},
    {"tier": 3,  "name": "蒙眼出牌",     "en": "Blind Dealer"},
    {"tier": 4,  "name": "抽卡祈禱師",   "en": "Card Pray-er"},
    {"tier": 5,  "name": "紙牌小白",     "en": "Total Beginner"},
    {"tier": 6,  "name": "搞不清楚",     "en": "Clueless"},
    {"tier": 7,  "name": "隨機應戰",     "en": "Random Fighter"},
    {"tier": 8,  "name": "亂打一通",     "en": "Button Masher"},
    {"tier": 9,  "name": "剛入門",       "en": "Just Started"},
    {"tier": 10, "name": "見習牌童",     "en": "Card Squire"},

    # ── 第 11-20 tier：菜鳥 ──
    {"tier": 11, "name": "初出茅廬",     "en": "Fresh Out"},
    {"tier": 12, "name": "半路出家",     "en": "Late Bloomer"},
    {"tier": 13, "name": "靠運氣的人",   "en": "Lucky Star"},
    {"tier": 14, "name": "地下賽新人",   "en": "Underground Rookie"},
    {"tier": 15, "name": "還在摸索",     "en": "Still Learning"},
    {"tier": 16, "name": "偶爾能贏",     "en": "Sometimes Wins"},
    {"tier": 17, "name": "見習挑戰者",   "en": "Apprentice"},
    {"tier": 18, "name": "街頭學徒",     "en": "Street Learner"},
    {"tier": 19, "name": "銅牌競技者",   "en": "Bronze Rival"},
    {"tier": 20, "name": "菜鳥進場",     "en": "Rookie Enters"},

    # ── 第 21-30 tier：普通 ──
    {"tier": 21, "name": "普通牌手",     "en": "Average Joe"},
    {"tier": 22, "name": "及格線上",     "en": "Just Passing"},
    {"tier": 23, "name": "賽場常客",     "en": "Regular Player"},
    {"tier": 24, "name": "老實打牌",     "en": "Honest Fighter"},
    {"tier": 25, "name": "中規中矩",     "en": "By The Book"},
    {"tier": 26, "name": "不上不下",     "en": "Middle Ground"},
    {"tier": 27, "name": "穩定輸出",     "en": "Steady Striker"},
    {"tier": 28, "name": "能打能跑",     "en": "Fight or Flight"},
    {"tier": 29, "name": "場上老臉",     "en": "Familiar Face"},
    {"tier": 30, "name": "入門老鳥",     "en": "Seasoned Beginner"},

    # ── 第 31-40 tier：有點實力 ──
    {"tier": 31, "name": "銀牌競技者",   "en": "Silver Rival"},
    {"tier": 32, "name": "有點心得",     "en": "Getting It"},
    {"tier": 33, "name": "算牌新手",     "en": "Counting Cards"},
    {"tier": 34, "name": "策略見習",     "en": "Tactic Learner"},
    {"tier": 35, "name": "敢賭敢輸",     "en": "Brave Gambler"},
    {"tier": 36, "name": "反應不錯",     "en": "Quick Reflexes"},
    {"tier": 37, "name": "上升中",       "en": "On The Rise"},
    {"tier": 38, "name": "讓人刮目",     "en": "Eye-Opener"},
    {"tier": 39, "name": "初顯鋒芒",     "en": "Sharpening"},
    {"tier": 40, "name": "潛力待發",     "en": "Untapped Potential"},

    # ── 第 41-50 tier：中上水準 ──
    {"tier": 41, "name": "金牌競技者",   "en": "Gold Rival"},
    {"tier": 42, "name": "場上老手",     "en": "Battle Vet"},
    {"tier": 43, "name": "出奇制勝",     "en": "Surprise Attack"},
    {"tier": 44, "name": "穩打穩紮",     "en": "Grounded Fighter"},
    {"tier": 45, "name": "讀牌高手",     "en": "Card Reader"},
    {"tier": 46, "name": "壓力不倒",     "en": "Pressure Proof"},
    {"tier": 47, "name": "算計精準",     "en": "Precise Calculator"},
    {"tier": 48, "name": "看穿套路",     "en": "Pattern Breaker"},
    {"tier": 49, "name": "有備而來",     "en": "Prepared"},
    {"tier": 50, "name": "半個頂尖",     "en": "Half Elite"},

    # ── 第 51-60 tier：強 ──
    {"tier": 51, "name": "白金競技者",   "en": "Platinum Rival"},
    {"tier": 52, "name": "常勝客",       "en": "Frequent Winner"},
    {"tier": 53, "name": "全場矚目",     "en": "Crowd Puller"},
    {"tier": 54, "name": "不容小覷",     "en": "Don't Underestimate"},
    {"tier": 55, "name": "場外情報員",   "en": "Intel Broker"},
    {"tier": 56, "name": "主動出擊",     "en": "First Mover"},
    {"tier": 57, "name": "讀心術師",     "en": "Mind Reader"},
    {"tier": 58, "name": "算牌機器",     "en": "Card Machine"},
    {"tier": 59, "name": "場上掌控者",   "en": "Field Controller"},
    {"tier": 60, "name": "策略大師候補", "en": "Master Candidate"},

    # ── 第 61-70 tier：精英 ──
    {"tier": 61, "name": "鑽石競技者",   "en": "Diamond Rival"},
    {"tier": 62, "name": "元素駕馭者",   "en": "Element Master"},
    {"tier": 63, "name": "天氣預報官",   "en": "Weather Commander"},
    {"tier": 64, "name": "先手必勝",     "en": "First Blood"},
    {"tier": 65, "name": "神算子",       "en": "Oracle"},
    {"tier": 66, "name": "乘虛而入",     "en": "Gap Seeker"},
    {"tier": 67, "name": "逆轉勝王",     "en": "Comeback King"},
    {"tier": 68, "name": "一眼看穿",     "en": "See-Through"},
    {"tier": 69, "name": "場上獵人",     "en": "Arena Hunter"},
    {"tier": 70, "name": "超出預期",     "en": "Beyond Expectation"},

    # ── 第 71-80 tier：大師 ──
    {"tier": 71, "name": "星耀競技者",   "en": "Star Rival"},
    {"tier": 72, "name": "傳說牌手",     "en": "Legend Handler"},
    {"tier": 73, "name": "降臨使者",     "en": "Descending Force"},
    {"tier": 74, "name": "鋒利如刃",     "en": "Sharp As Blade"},
    {"tier": 75, "name": "不敗心態",     "en": "Undefeated Mind"},
    {"tier": 76, "name": "賭場惡夢",     "en": "Casino Nightmare"},
    {"tier": 77, "name": "讀牌之神",     "en": "God of Reading"},
    {"tier": 78, "name": "算無遺策",     "en": "Flawless Plan"},
    {"tier": 79, "name": "場上教主",     "en": "Arena Prophet"},
    {"tier": 80, "name": "近乎完美",     "en": "Near Perfect"},

    # ── 第 81-90 tier：宗師 ──
    {"tier": 81, "name": "宗師境界",     "en": "Grandmaster"},
    {"tier": 82, "name": "超凡入聖",     "en": "Transcendent"},
    {"tier": 83, "name": "場外傳說",     "en": "Off-Arena Legend"},
    {"tier": 84, "name": "讓人窒息",     "en": "Suffocating"},
    {"tier": 85, "name": "常勝將軍",     "en": "Conquering General"},
    {"tier": 86, "name": "無懈可擊",     "en": "Impeccable"},
    {"tier": 87, "name": "神乎其技",     "en": "Godlike Skill"},
    {"tier": 88, "name": "超越極限",     "en": "Beyond The Limit"},
    {"tier": 89, "name": "近神之境",     "en": "Near Deity"},
    {"tier": 90, "name": "令人仰望",     "en": "Revered"},

    # ── 第 91-100 tier：神話 ──
    {"tier": 91, "name": "凰者降臨",     "en": "Phoenix Descends"},
    {"tier": 92, "name": "混沌主宰",     "en": "Chaos Lord"},
    {"tier": 93, "name": "絕頂之巔",     "en": "Absolute Apex"},
    {"tier": 94, "name": "無人能敵",     "en": "Invincible"},
    {"tier": 95, "name": "終焉之王",     "en": "King of Endings"},
    {"tier": 96, "name": "亂世霸主",     "en": "Chaos Overlord"},
    {"tier": 97, "name": "傳說締造者",   "en": "Legend Maker"},
    {"tier": 98, "name": "超神降臨",     "en": "God Mode"},
    {"tier": 99, "name": "混沌之源",     "en": "Source of Chaos"},
    {"tier":100, "name": "CHARENIX之神", "en": "God of Charenix"},
]

# Quick lookup by tier
TITLE_BY_TIER = {t["tier"]: t for t in TITLES}

def get_title(tier: int) -> dict:
    tier = max(1, min(100, tier))
    return TITLE_BY_TIER[tier]

def random_title_pair() -> tuple[dict, dict]:
    """
    Generate two tiers for a match.
    40% chance: same bracket (both low / both mid / both high)
    60% chance: independent random
    """
    import random
    if random.random() < 0.40:
        # same bracket — pick a range, both from it
        bracket_start = random.choice([1, 1, 1, 10, 20, 30, 40, 50, 60, 70, 80, 90])
        bracket_end   = min(100, bracket_start + random.randint(15, 25))
        t1 = random.randint(bracket_start, bracket_end)
        t2 = random.randint(bracket_start, bracket_end)
    else:
        t1 = random.randint(1, 100)
        t2 = random.randint(1, 100)
    return get_title(t1), get_title(t2)


if __name__ == "__main__":
    print(f"Total titles: {len(TITLES)}")
    for t in TITLES[::10]:
        print(f"  Tier {t['tier']:3d} | {t['name']:12s} | {t['en']}")
    print("\nSample pair:", random_title_pair())
