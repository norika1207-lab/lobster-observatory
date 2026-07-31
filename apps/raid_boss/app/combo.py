"""
CardHouse v2.1 — 跨平台組合技 (spec Part 15)

組合技在對戰開始前依牌組靜態判定, 結果以 flag 形式注入 battle_engine:
  - flags.weak_alliance      → 己方全員 val ×1.5, 勝時 HP 傷害 ×1.5
  - flags.rain_tactic        → 第一回合強制暴雨 (覆寫 weather 擲骰)
  - flags.speed_league       → 所有回合己方 val +10 (先手感)
  - flags.purebred           → 全員 DEF +20% (反映在有效值計算)
  - flags.crossover_legend   → 結算籌碼 ×2

MVP 簡化: DECK_SIZE = 3, spec 原文是 5 張; 比例下修保留設計意圖
"""
from typing import List, Dict


def detect_combos(deck: List[Dict]) -> Dict[str, bool]:
    flags = {
        "weak_alliance": False,
        "rain_tactic": False,
        "speed_league": False,
        "purebred": False,
        "crossover_legend": False,
    }

    if all(c["ovr"] <= 75 for c in deck):
        flags["weak_alliance"] = True

    water_count = sum(1 for c in deck if c["element"] == "water")
    if water_count >= 2:
        flags["rain_tactic"] = True

    sports = {c["sport"] for c in deck}
    if "F1" in sports and "Horse" in sports:
        flags["speed_league"] = True

    if len(sports) == 1 and len(deck) >= 3:
        flags["purebred"] = True

    legend_sports = {c["sport"] for c in deck if c["rarity"] == "Legend"}
    if len(legend_sports) >= 3:
        flags["crossover_legend"] = True

    return flags


COMBO_LABELS = {
    "weak_alliance":    {"cn": "弱者同盟",   "icon": "🏳️",  "desc": "3 張 OVR≤75 → 全員 val ×1.5, 勝時傷害 ×1.5"},
    "rain_tactic":      {"cn": "雨天戰術",   "icon": "☔",   "desc": "2+ 張水屬性 → 第一回合強制暴雨"},
    "speed_league":     {"cn": "速度聯盟",   "icon": "⚡",   "desc": "F1+Horse 混編 → 每回合 val +10"},
    "purebred":         {"cn": "純血陣型",   "icon": "🛡",    "desc": "同運動 3 張 → 全員 DEF +20%"},
    "crossover_legend": {"cn": "跨界傳說",   "icon": "🌟",   "desc": "3 種運動 Legend 各 1 → 籌碼結算 ×2"},
}


def combo_label_list(flags: Dict[str, bool]) -> List[Dict]:
    return [{"key": k, **COMBO_LABELS[k]} for k, v in flags.items() if v]
