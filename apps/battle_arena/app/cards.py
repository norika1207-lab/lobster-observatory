"""
CardHouse v4.0 — 動態卡牌載入
  F1:     硬編碼 2026 賽季 20 名車手 (stats from /opt/racing/services/card_engine.py)
  Horse:  MongoDB hr_races.horses，取 UR/SSR/SR/R top cards
  Esports: http://127.0.0.1:8003/api/cards/players (30 位)
"""
import sys, random
sys.path.insert(0, '/opt')
from typing import List, Dict, Optional
from skill_engine import apply_skill_module
from shared_modules.skills_engine.racing import F1_STATS as _F1_STATS, F1_LINEUP as _F1_LINEUP_DATA, F1_SKILL_MAP as _F1_SKILL_MAP

# Convert F1_LINEUP list-of-tuples to match original _F1_LINEUP format
_F1_LINEUP = _F1_LINEUP_DATA

RARITY_ORDER = ["silverstone", "monza", "suzuka", "spa", "monaco"]
ELEMENTS = ["wind", "fire", "water", "earth"]
ELEMENT_CN = {"wind": "風", "fire": "火", "water": "水", "earth": "土"}
ELEMENT_ICON = {"wind": "💨", "fire": "🔥", "water": "💧", "earth": "🌿"}
ELEMENT_COUNTER = {"wind": "fire", "fire": "water", "water": "earth", "earth": "wind"}


def element_multiplier(attacker: str, defender: str) -> float:
    if ELEMENT_COUNTER.get(attacker) == defender: return 1.30
    if ELEMENT_COUNTER.get(defender) == attacker: return 0.70
    return 1.00


def atk_of(ovr: int, level: int) -> int:
    return min(30, int(round(ovr * 0.2 + level * 0.2)))

def def_of(ovr: int, level: int) -> int:
    return min(22, int(round(ovr * 0.14 + level * 0.16)))

def rarity_rank(rarity: str) -> int:
    try: return RARITY_ORDER.index(rarity)
    except ValueError: return 0

def _ovr_to_rarity(ovr: int) -> tuple:
    """ovr → (rarity, level, mp_cost)"""
    if ovr >= 88: return ("monaco", 50, 20)
    if ovr >= 78: return ("spa", 45, 15)
    if ovr >= 70: return ("suzuka", 40, 10)
    if ovr >= 62: return ("monza", 30, 5)
    return ("silverstone", 20, 0)


# ═══════════════════ F1 2026 ═══════════════════
# Data imported from /opt/shared_modules/skills_engine/racing.py

def _build_f1_cards() -> List[Dict]:
    cards = []
    for acronym, full_name, display, team, number, element, accent in _F1_LINEUP:
        st = _F1_STATS.get(acronym)
        if not st:
            continue
        ovr = int(round((st["q"] + st["t"] + st["w"] + st["o"] + st["s"]) / 5))
        rarity, level, mp_cost = _ovr_to_rarity(ovr)
        # pick best skill for buff/debuff
        best_key = max(st, key=lambda k: st[k])
        btype, bval, bdesc, dtype, dval, ddesc = _F1_SKILL_MAP[best_key]
        # scale effects by rarity
        scale = {50: 1.0, 45: 0.9, 40: 0.8, 30: 0.65, 20: 0.5}[level]
        bval = int(round(bval * scale))
        dval = int(round(dval * scale))
        cards.append({
            "id": f"F_{acronym.upper()}",
            "sport": "F1",
            "name": full_name,
            "name_short": display,
            "team": team,
            "rarity": rarity,
            "element": element,
            "ovr": ovr,
            "level": level,
            "mp_cost": mp_cost,
            "image_url": f"/static/cards/f1/{acronym}.png",
            "tagline": f"#{number} {team}",
            "accent": accent,
            "buff_effect": {"type": btype, "value": bval, "desc": bdesc},
            "debuff_effect": {"type": dtype, "value": dval, "desc": ddesc},
        })
    return cards


# ═══════════════════ HORSE ═══════════════════

_HORSE_RARITY_MAP = {"UR": ("monaco",50,20), "SSR": ("spa",45,15), "SR": ("suzuka",40,10), "R": ("monza",30,5)}
_HORSE_SELECT = {"UR": 8, "SSR": 10, "SR": 8, "R": 6}   # how many per rarity

def _horse_element(card: Dict) -> str:
    stats = {"speed": card.get("speed",0), "burst": card.get("burst",0),
             "stamina": card.get("stamina",0), "track_affinity": card.get("track_affinity",0)}
    best = max(stats, key=stats.get)
    return {"speed": "wind", "burst": "fire", "stamina": "water", "track_affinity": "earth"}[best]

def _horse_effects(card: Dict, scale: float) -> tuple:
    burst = card.get("burst", 50)
    stamina = card.get("stamina", 50)
    consistency = card.get("consistency", 50)
    track = card.get("track_affinity", 50)
    best_stat = max(burst, stamina, track)
    if best_stat == burst:
        bval = int(round(25 * scale)); dval = int(round(20 * scale))
        return ("self_atk_pct", bval, f"主力 ATK +{bval}%", "opp_atk_pct", dval, f"對方 ATK -{dval}%")
    elif best_stat == stamina:
        bval = int(round(20 * scale)); dval = int(round(15 * scale))
        return ("self_def_pct", bval, f"主力 DEF +{bval}%", "opp_def_pct", dval, f"對方 DEF -{dval}%")
    else:
        bval = int(round(18 * scale)); dval = int(round(12 * scale))
        return ("self_hp_heal", bval, f"補 {bval} HP", "opp_hp_direct", dval, f"無視 DEF 扣 {dval} HP")

def _build_horse_cards() -> List[Dict]:
    try:
        from pymongo import MongoClient
        client = MongoClient(
            "mongodb://clawApp:9SoPkGneSXeT11stDJnYvwnzjTaYWTy0woXUwyDMuM4@127.0.0.1:27017/?authSource=admin",
            serverSelectionTimeoutMS=3000,
        )
        db = client["hr_races"]
        cards = []
        for rarity, (card_rarity, level, mp_cost) in _HORSE_RARITY_MAP.items():
            n = _HORSE_SELECT[rarity]
            horses = list(db.horses.find(
                {"card.rarity": rarity},
                {"id_horse":1,"name":1,"card":1,"_id":0}
            ).sort("card.overall", -1).limit(n))
            scale = {50:1.0, 45:0.9, 40:0.8, 30:0.65}[level]
            for h in horses:
                c = h.get("card", {})
                ovr_raw = c.get("overall", 60)
                ovr = min(99, ovr_raw + 18)
                element = _horse_element(c)
                btype, bval, bdesc, dtype, dval, ddesc = _horse_effects(c, scale)
                horse_id = str(h["id_horse"]).replace("hrs_", "")
                img_path = f"/static/cards/horse/{horse_id}.jpg"
                cards.append({
                    "id": f"H_{horse_id}",
                    "sport": "Horse",
                    "name": h["name"],
                    "name_short": h["name"].split()[0],
                    "team": "—",
                    "rarity": card_rarity,
                    "element": element,
                    "ovr": ovr,
                    "level": level,
                    "mp_cost": mp_cost,
                    "image_url": img_path if __import__("os").path.exists(f"/opt/battle_arena{img_path}") else "",
                    "tagline": f"OR {c.get('overall',0)}  {rarity}",
                    "accent": "#8B6914",
                    "buff_effect": {"type": btype, "value": bval, "desc": bdesc},
                    "debuff_effect": {"type": dtype, "value": dval, "desc": ddesc},
                })
        client.close()
        return cards
    except Exception as e:
        print(f"[cards] Horse MongoDB load failed: {e} — using fallback")
        return _HORSE_FALLBACK


# ═══════════════════ ESPORTS ═══════════════════

_ESPORT_GAME_ELEMENT = {
    "lol": "wind", "cs2": "fire", "valorant": "water",
    "dota2": "earth", "ow2": "fire", "apex": "wind",
}
_ESPORT_GAME_ACCENT = {
    "lol": "#C8AA6E", "cs2": "#F5A623", "valorant": "#FF4655",
    "dota2": "#C23B22", "ow2": "#F99E1A", "apex": "#DA292A",
}
_TIER_OVR_BASE = {"Icon": 90, "Legend": 80, "Elite": 70}

_SKILL_BUFF = {
    "legend_aura":  ("self_def_flat",  25, "主力 DEF +25"),
    "icon_moment":  ("self_atk_pct",   25, "主力 ATK +25%"),
    "patch_radar":  ("self_atk_flat",  30, "主力 ATK +30"),
    "comeback_king":("self_hp_heal",   20, "補 20 HP"),
    "history_track":("self_def_pct",   20, "主力 DEF +20%"),
    "clutch_king":  ("self_atk_pct",   30, "主力 ATK +30%"),
    "big_stage":    ("self_atk_pct",   20, "主力 ATK +20%"),
    "region_king":  ("self_def_flat",  20, "主力 DEF +20"),
    "meta_breaker": ("self_atk_flat",  25, "主力 ATK +25"),
    "peak_moment":  ("self_hp_heal",   15, "補 15 HP"),
    "mp_surge":     ("self_mp_heal",   25, "補 25 MP"),
}
_SKILL_DEBUFF = {
    "legend_aura":  ("opp_mp_drain",   20, "對方 MP -20"),
    "icon_moment":  ("opp_atk_pct",    20, "對方 ATK -20%"),
    "patch_radar":  ("opp_def_flat",   20, "對方 DEF -20"),
    "comeback_king":("opp_hp_direct",  15, "無視 DEF 扣 15 HP"),
    "history_track":("opp_def_pct",    15, "對方 DEF -15%"),
    "clutch_king":  ("opp_atk_pct",    25, "對方 ATK -25%"),
    "big_stage":    ("opp_atk_pct",    15, "對方 ATK -15%"),
    "region_king":  ("opp_mp_drain",   15, "對方 MP -15"),
    "meta_breaker": ("opp_atk_flat",   20, "對方 ATK -20"),
    "peak_moment":  ("opp_hp_direct",  10, "無視 DEF 扣 10 HP"),
    "mp_surge":     ("opp_mp_drain",   25, "對方 MP -25"),
}

def _esport_image(pid: str) -> str:
    import os
    for ext in ("png", "jpg", "svg"):
        if os.path.exists(f"/opt/esports/static/players/{pid}.{ext}"):
            return f"/static/cards/esports/{pid}.{ext}"
    return ""

def _build_esports_cards() -> List[Dict]:
    try:
        import urllib.request, json
        with urllib.request.urlopen("http://127.0.0.1:8003/api/cards/players", timeout=3) as r:
            players = json.loads(r.read())
        cards = []
        for p in players:
            tier = p.get("card_tier", "Elite")
            base = _TIER_OVR_BASE.get(tier, 70)
            clutch = p.get("clutch_rate", 0.5)
            ovr = min(99, base + int(clutch * 10))
            rarity, level, mp_cost = _ovr_to_rarity(ovr)
            games = p.get("games", [{}])
            game_id = games[0].get("game_id", "lol") if games else "lol"
            element = _ESPORT_GAME_ELEMENT.get(game_id, "wind")
            accent = _ESPORT_GAME_ACCENT.get(game_id, "#E2012D")
            skills = p.get("skills", [])
            btype = bdesc = dtype = ddesc = None
            bval = dval = 0
            for sk in skills:
                if sk in _SKILL_BUFF and btype is None:
                    btype, bval, bdesc = _SKILL_BUFF[sk]
                if sk in _SKILL_DEBUFF and dtype is None:
                    dtype, dval, ddesc = _SKILL_DEBUFF[sk]
                if btype and dtype:
                    break
            if not btype:
                btype, bval, bdesc = "self_atk_pct", 15, "主力 ATK +15%"
            if not dtype:
                dtype, dval, ddesc = "opp_atk_pct", 10, "對方 ATK -10%"
            # scale by level
            scale = {50:1.0, 45:0.9, 40:0.8, 30:0.65, 20:0.5}[level]
            bval = int(round(bval * scale))
            dval = int(round(dval * scale))
            tagline = f"{tier} · {games[0].get('game_name', game_id)}" if games else tier
            cards.append({
                "id": f"E_{p['id'].upper()}",
                "sport": "Esports",
                "name": p["name"],
                "name_short": p.get("display_name", p["id"]),
                "team": games[0].get("game_name", game_id) if games else game_id,
                "rarity": rarity,
                "element": element,
                "ovr": ovr,
                "level": level,
                "mp_cost": mp_cost,
                "image_url": _esport_image(p["id"]),
                "tagline": tagline,
                "accent": accent,
                "buff_effect": {"type": btype, "value": bval, "desc": bdesc},
                "debuff_effect": {"type": dtype, "value": dval, "desc": ddesc},
            })
        return cards
    except Exception as e:
        print(f"[cards] Esports API load failed: {e} — using fallback")
        return _ESPORTS_FALLBACK


# ═══════════════════ FALLBACKS ═══════════════════

_HORSE_FALLBACK = [
    {"id":"H_295952","sport":"Horse","name":"Seven Fires","name_short":"Seven Fires","team":"—","rarity":"monaco","element":"wind","ovr":99,"level":50,"mp_cost":20,"image_url":"","tagline":"UR · 賽馬","accent":"#8B6914","buff_effect":{"type":"self_hp_heal","value":20,"desc":"補 20 HP"},"debuff_effect":{"type":"opp_hp_direct","value":15,"desc":"無視 DEF 扣 15 HP"}},
    {"id":"H_292154","sport":"Horse","name":"Zighy","name_short":"Zighy","team":"—","rarity":"spa","element":"water","ovr":85,"level":45,"mp_cost":15,"image_url":"","tagline":"SSR · 賽馬","accent":"#8B6914","buff_effect":{"type":"self_atk_pct","value":23,"desc":"主力 ATK +23%"},"debuff_effect":{"type":"opp_atk_pct","value":18,"desc":"對方 ATK -18%"}},
    {"id":"H_230299","sport":"Horse","name":"Elysian Flame","name_short":"Elysian","team":"—","rarity":"suzuka","element":"fire","ovr":75,"level":40,"mp_cost":10,"image_url":"","tagline":"SR · 賽馬","accent":"#8B6914","buff_effect":{"type":"self_atk_flat","value":24,"desc":"主力 ATK +24"},"debuff_effect":{"type":"opp_def_flat","value":12,"desc":"對方 DEF -12"}},
]

_ESPORTS_FALLBACK = [
    {"id":"E_FAKER","sport":"Esports","name":"Lee Sang-hyeok","name_short":"Faker","team":"LoL","rarity":"monaco","element":"wind","ovr":97,"level":50,"mp_cost":20,"image_url":"/static/cards/esports/faker.png","tagline":"Icon · League of Legends","accent":"#C8AA6E","buff_effect":{"type":"self_def_flat","value":25,"desc":"主力 DEF +25"},"debuff_effect":{"type":"opp_mp_drain","value":20,"desc":"對方 MP -20"}},
    {"id":"E_S1MPL","sport":"Esports","name":"Oleksandr Kostyliev","name_short":"s1mple","team":"CS2","rarity":"monaco","element":"fire","ovr":97,"level":50,"mp_cost":20,"image_url":"/static/cards/esports/s1mple.jpg","tagline":"Icon · CS2","accent":"#F5A623","buff_effect":{"type":"self_atk_pct","value":25,"desc":"主力 ATK +25%"},"debuff_effect":{"type":"opp_atk_pct","value":20,"desc":"對方 ATK -20%"}},
    {"id":"E_ZYWOO","sport":"Esports","name":"Mathieu Herbaut","name_short":"ZywOo","team":"CS2","rarity":"monaco","element":"fire","ovr":97,"level":50,"mp_cost":20,"image_url":"/static/cards/esports/zywoo.jpg","tagline":"Icon · CS2","accent":"#F5A623","buff_effect":{"type":"self_hp_heal","value":20,"desc":"補 20 HP"},"debuff_effect":{"type":"opp_hp_direct","value":15,"desc":"無視 DEF 扣 15 HP"}},
]


# ═══════════════════ FIGHTING (UFC/Boxing/ONE) ═══════════════════

from fighting_legends import (
    UFC_MMA_LEGENDS, BOXING_LEGENDS, ONE_MUAYTHAI_LEGENDS, ONE_MMA_LEGENDS, SUMO_LEGENDS,
    FIGHTING_SKILL_BUFF, FIGHTING_SKILL_DEBUFF, STYLE_SKILL,
)

def _img_fighting(fid: str) -> str:
    import os
    for ext in ("png", "jpg"):
        p = f"/opt/battle_arena/static/cards/fighting/{fid}.{ext}"
        if os.path.exists(p):
            return f"/static/cards/fighting/{fid}.{ext}?v=20260423c"
    return ""

def _img_sumo(fid: str) -> str:
    import os
    for ext in ("png", "jpg"):
        p = f"/opt/battle_arena/static/cards/sumo/{fid}.{ext}"
        if os.path.exists(p):
            return f"/static/cards/sumo/{fid}.{ext}?v=20260423b"
    return ""

def _fight_effects(style: str, ovr: int, scale: float):
    skill = STYLE_SKILL.get(style, "default_atk")
    bt, bv0, bd = FIGHTING_SKILL_BUFF.get(skill, FIGHTING_SKILL_BUFF["default_atk"])
    dt, dv0, dd = FIGHTING_SKILL_DEBUFF.get(skill, FIGHTING_SKILL_DEBUFF["default_atk"])
    bv = int(round(bv0 * scale)); dv = int(round(dv0 * scale))
    # rebuild desc with actual scaled values
    b_desc = bd.replace(str(bv0), str(bv)) if str(bv0) in bd else f"{bd} ({bv})"
    d_desc = dd.replace(str(dv0), str(dv)) if str(dv0) in dd else f"{dd} ({dv})"
    return ({"type": bt, "value": bv, "desc": b_desc},
            {"type": dt, "value": dv, "desc": d_desc})

def _build_ufc_cards() -> List[Dict]:
    cards = []
    for (fid, full, short, disc, org, record, ovr, elem, accent, style) in UFC_MMA_LEGENDS:
        rarity, level, mp_cost = _ovr_to_rarity(ovr)
        scale = {50:1.0, 45:0.9, 40:0.8, 30:0.65, 20:0.5}[level]
        buff_eff, debuff_eff = _fight_effects(style, ovr, scale)
        cards.append({
            "id": f"UFC_{fid.upper()}",
            "sport": "UFC",
            "name": full,
            "name_short": short,
            "team": org,
            "rarity": rarity,
            "element": elem,
            "ovr": ovr,
            "level": level,
            "mp_cost": mp_cost,
            "image_url": _img_fighting(fid),
            "tagline": f"{record} · {disc}",
            "accent": accent,
            "buff_effect": buff_eff,
            "debuff_effect": debuff_eff,
        })
    return cards

def _build_boxing_cards() -> List[Dict]:
    cards = []
    for (fid, full, short, weight, org, record, ovr, elem, accent, style) in BOXING_LEGENDS:
        rarity, level, mp_cost = _ovr_to_rarity(ovr)
        scale = {50:1.0, 45:0.9, 40:0.8, 30:0.65, 20:0.5}[level]
        buff_eff, debuff_eff = _fight_effects(style, ovr, scale)
        cards.append({
            "id": f"BOX_{fid.upper()}",
            "sport": "Boxing",
            "name": full,
            "name_short": short,
            "team": org,
            "rarity": rarity,
            "element": elem,
            "ovr": ovr,
            "level": level,
            "mp_cost": mp_cost,
            "image_url": _img_fighting(fid),
            "tagline": f"{record} · {weight}",
            "accent": accent,
            "buff_effect": buff_eff,
            "debuff_effect": debuff_eff,
        })
    return cards

def _build_sumo_cards() -> List[Dict]:
    cards = []
    for (fid, full, short, rank, era, record, ovr, elem, accent, style) in SUMO_LEGENDS:
        rarity, level, mp_cost = _ovr_to_rarity(ovr)
        scale = {50:1.0, 45:0.9, 40:0.8, 30:0.65, 20:0.5}[level]
        buff_eff, debuff_eff = _fight_effects(style, ovr, scale)
        cards.append({
            "id": f"SUMO_{fid.upper()}",
            "sport": "Sumo",
            "name": full,
            "name_short": short,
            "team": rank,
            "rarity": rarity,
            "element": elem,
            "ovr": ovr,
            "level": level,
            "mp_cost": mp_cost,
            "image_url": _img_sumo(fid),
            "tagline": f"{record} · {era} {rank}",
            "accent": accent,
            "buff_effect": buff_eff,
            "debuff_effect": debuff_eff,
        })
    return cards

def _build_one_cards() -> List[Dict]:
    cards = []
    for legends, sport_tag, id_prefix in [
        (ONE_MUAYTHAI_LEGENDS, "ONE", "ONE"),
        (ONE_MMA_LEGENDS,      "ONE", "ONE"),
    ]:
        for row in legends:
            fid, full, short, disc, weight, ovr, elem, accent, style = row
            rarity, level, mp_cost = _ovr_to_rarity(ovr)
            scale = {50:1.0, 45:0.9, 40:0.8, 30:0.65, 20:0.5}[level]
            buff_eff, debuff_eff = _fight_effects(style, ovr, scale)
            cards.append({
                "id": f"ONE_{fid.upper()}",
                "sport": "ONE",
                "name": full,
                "name_short": short,
                "team": "ONE Championship",
                "rarity": rarity,
                "element": elem,
                "ovr": ovr,
                "level": level,
                "mp_cost": mp_cost,
                "image_url": _img_fighting(fid),
                "tagline": f"{disc} · {weight}",
                "accent": accent,
                "buff_effect": buff_eff,
                "debuff_effect": debuff_eff,
            })
    return cards


# ═══════════════════ MotoGP + NASCAR ═══════════════════

from historical_riders import (
    MOTOGP_LEGENDS, MOTOGP_MFR_COLOR,
    NASCAR_LEGENDS, NASCAR_MFR_COLOR,
)
from rider_skills import get_rider_skills
import os as _os

def _img_if_exists(rel_path: str) -> str:
    """檢查 static/... 檔案是否存在, 不存在回空字串避免前端 404"""
    full = _os.path.join("/opt/battle_arena", rel_path.lstrip("/"))
    return rel_path if _os.path.isfile(full) else ""


def _span_to_years(span: str) -> List[int]:
    """把 '1964-1977' 展開成 [1964, 1965, ..., 1977]"""
    try:
        a, b = span.split("-")
        return list(range(int(a), int(b) + 1))
    except Exception:
        return []


def _era_from_years(years: List[int]) -> str:
    """按黃金/古典/現代三期分 era"""
    if not years:
        return "modern"
    mid = years[len(years) // 2]
    if mid <= 1975:
        return "golden"
    if mid <= 2000:
        return "classic"
    return "modern"

# titles-based buff/debuff pick (more titles -> ATK% 主力 buff)
# career wins-based scaling
_MOTO_NASCAR_SKILL_MAP = {
    "atk":  ("self_atk_pct",  25, "self_atk_pct",  20, "對方 ATK"),
    "def":  ("self_def_pct",  25, "self_def_pct",  20, "對方 DEF"),
    "flat": ("self_atk_flat", 35, "opp_atk_flat",  15, ""),
    "def_flat":("self_def_flat",30,"opp_def_flat", 15, ""),
    "heal": ("self_hp_heal",  20, "opp_hp_direct", 12, ""),
}


def _pick_moto_skill(element: str, ovr: int, titles: int):
    """根據 element 決定技能類型，根據 ovr+titles scale 數值。"""
    # 元素 → 技能偏好
    if element == "fire":
        btype, bval0 = "self_atk_pct", 25
        dtype, dval0 = "opp_atk_pct", 20
        bdesc_fmt = "主力 ATK +{}%"
        ddesc_fmt = "對方 ATK -{}%"
    elif element == "water":
        btype, bval0 = "self_def_pct", 25
        dtype, dval0 = "opp_def_pct", 20
        bdesc_fmt = "主力 DEF +{}%"
        ddesc_fmt = "對方 DEF -{}%"
    elif element == "wind":
        btype, bval0 = "self_atk_flat", 35
        dtype, dval0 = "opp_atk_flat", 15
        bdesc_fmt = "主力 ATK +{}"
        ddesc_fmt = "對方 ATK -{}"
    else:  # earth
        btype, bval0 = "self_def_flat", 30
        dtype, dval0 = "opp_def_flat", 15
        bdesc_fmt = "主力 DEF +{}"
        ddesc_fmt = "對方 DEF -{}"
    return btype, bval0, bdesc_fmt, dtype, dval0, ddesc_fmt


def _build_motogp_cards() -> List[Dict]:
    cards = []
    for (rid, full_name, short, country, mfr, titles, wins,
         span, ovr, element, accent, era) in MOTOGP_LEGENDS:
        rarity, level, mp_cost = _ovr_to_rarity(ovr)
        scale = {50: 1.0, 45: 0.9, 40: 0.8, 30: 0.65, 20: 0.5}[level]
        btype, bval0, bdesc_fmt, dtype, dval0, ddesc_fmt = _pick_moto_skill(element, ovr, titles)
        bval = int(round(bval0 * scale))
        dval = int(round(dval0 * scale))
        team_color = MOTOGP_MFR_COLOR.get(mfr, accent)
        rskills = get_rider_skills(rid)
        years = _span_to_years(span)
        cards.append({
            "id": f"MG_{rid.upper()}",
            "sport": "MotoGP",
            "name": full_name,
            "name_short": short,
            "team": mfr,
            "country": country,
            "rarity": rarity,
            "element": element,
            "ovr": ovr,
            "level": level,
            "mp_cost": mp_cost,
            "image_url": _img_if_exists(f"/static/cards/motogp/{rid}.png"),
            "tagline": f"{titles}× WC · {wins}W · {span}",
            "accent": team_color,
            "buff_effect": {"type": btype, "value": bval, "desc": bdesc_fmt.format(bval)},
            "debuff_effect": {"type": dtype, "value": dval, "desc": ddesc_fmt.format(dval)},
            "passive": rskills["passive"],
            "skills": rskills["skills"],
            "career_span": span,
            "career_start": years[0] if years else None,
            "career_end":   years[-1] if years else None,
            "years_active": years,
            "era": era,
            "titles": titles,
            "career_wins": wins,
        })
    return cards


def _build_nascar_cards() -> List[Dict]:
    cards = []
    for (rid, full_name, short, country, mfr, titles, wins,
         span, ovr, element, accent, era) in NASCAR_LEGENDS:
        rarity, level, mp_cost = _ovr_to_rarity(ovr)
        scale = {50: 1.0, 45: 0.9, 40: 0.8, 30: 0.65, 20: 0.5}[level]
        btype, bval0, bdesc_fmt, dtype, dval0, ddesc_fmt = _pick_moto_skill(element, ovr, titles)
        bval = int(round(bval0 * scale))
        dval = int(round(dval0 * scale))
        team_color = NASCAR_MFR_COLOR.get(mfr, accent)
        rskills = get_rider_skills(rid)
        years = _span_to_years(span)
        cards.append({
            "id": f"NS_{rid.upper()}",
            "sport": "NASCAR",
            "name": full_name,
            "name_short": short,
            "team": mfr,
            "country": country,
            "rarity": rarity,
            "element": element,
            "ovr": ovr,
            "level": level,
            "mp_cost": mp_cost,
            "image_url": _img_if_exists(f"/static/cards/nascar/{rid}.png"),
            "tagline": f"{titles}× Cup · {wins}W · {span}",
            "accent": team_color,
            "buff_effect": {"type": btype, "value": bval, "desc": bdesc_fmt.format(bval)},
            "debuff_effect": {"type": dtype, "value": dval, "desc": ddesc_fmt.format(dval)},
            "passive": rskills["passive"],
            "skills": rskills["skills"],
            "career_span": span,
            "career_start": years[0] if years else None,
            "career_end":   years[-1] if years else None,
            "years_active": years,
            "era": era,
            "titles": titles,
            "career_wins": wins,
        })
    return cards


# 平衡 / role 分配 — 全部委派給 skill_engine.apply_skill_module


# ═══════════════════ NBA Basketball ═══════════════════

from nba_legends import NBA_LEGENDS, NBA_SKILL_BUFF, NBA_SKILL_DEBUFF
from mlb_legends import MLB_LEGENDS, MLB_SKILL_BUFF, MLB_SKILL_DEBUFF

def _img_basketball(fid: str) -> str:
    import os
    for ext in ("png", "jpg"):
        p = f"/opt/battle_arena/static/cards/basketball/{fid}.{ext}"
        if os.path.exists(p):
            return f"/static/cards/basketball/{fid}.{ext}?v=20260423"
    return ""

def _build_nba_cards() -> List[Dict]:
    cards = []
    for (fid, full, short, pos, era, titles, ovr, elem, accent, style) in NBA_LEGENDS:
        rarity, level, mp_cost = _ovr_to_rarity(ovr)
        scale = {50:1.0, 45:0.9, 40:0.8, 30:0.65, 20:0.5}[level]
        bt, bv0, bd = NBA_SKILL_BUFF.get(style, NBA_SKILL_BUFF["clutch"])
        dt, dv0, dd = NBA_SKILL_DEBUFF.get(style, NBA_SKILL_DEBUFF["clutch"])
        bv = int(round(bv0 * scale)); dv = int(round(dv0 * scale))
        b_desc = bd.replace(str(bv0), str(bv)) if str(bv0) in bd else f"{bd} ({bv})"
        d_desc = dd.replace(str(dv0), str(dv)) if str(dv0) in dd else f"{dd} ({dv})"
        cards.append({
            "id": f"NBA_{fid.upper()}",
            "sport": "NBA",
            "name": full,
            "name_short": short,
            "team": pos,
            "rarity": rarity,
            "element": elem,
            "ovr": ovr,
            "level": level,
            "mp_cost": mp_cost,
            "image_url": _img_basketball(fid),
            "tagline": f"{era} · {titles}",
            "accent": accent,
            "buff_effect": {"type": bt, "value": bv, "desc": b_desc},
            "debuff_effect": {"type": dt, "value": dv, "desc": d_desc},
        })
    return cards


# ═══════════════════ MLB Baseball ═══════════════════

def _img_baseball(fid: str) -> str:
    import os
    for ext in ("png", "jpg"):
        p = f"/opt/battle_arena/static/cards/baseball/{fid}.{ext}"
        if os.path.exists(p):
            return f"/static/cards/baseball/{fid}.{ext}?v=20260425"
    return ""

def _build_mlb_cards() -> List[Dict]:
    cards = []
    for (fid, full, short, pos, era, stat_line, ovr, elem, accent, style) in MLB_LEGENDS:
        rarity, level, mp_cost = _ovr_to_rarity(ovr)
        scale = {50: 1.0, 45: 0.9, 40: 0.8, 30: 0.65, 20: 0.5}[level]
        bt, bv0, bd = MLB_SKILL_BUFF.get(style, MLB_SKILL_BUFF["contact"])
        dt, dv0, dd = MLB_SKILL_DEBUFF.get(style, MLB_SKILL_DEBUFF["contact"])
        bv = int(round(bv0 * scale)); dv = int(round(dv0 * scale))
        b_desc = bd.replace(str(bv0), str(bv)) if str(bv0) in bd else f"{bd} ({bv})"
        d_desc = dd.replace(str(dv0), str(dv)) if str(dv0) in dd else f"{dd} ({dv})"
        cards.append({
            "id": f"MLB_{fid.upper()}",
            "sport": "MLB",
            "name": full,
            "name_short": short,
            "team": pos,
            "rarity": rarity,
            "element": elem,
            "ovr": ovr,
            "level": level,
            "mp_cost": mp_cost,
            "image_url": _img_baseball(fid),
            "tagline": f"{era} · {stat_line}",
            "accent": accent,
            "buff_effect": {"type": bt, "value": bv, "desc": b_desc},
            "debuff_effect": {"type": dt, "value": dv, "desc": d_desc},
        })
    return cards


# ═══════════════════ 初始化 ═══════════════════

CARDS: List[Dict] = []

def _init_cards():
    global CARDS
    f1 = _build_f1_cards()
    horse = _build_horse_cards()
    esports = _build_esports_cards()
    motogp = _build_motogp_cards()
    nascar = _build_nascar_cards()
    ufc = _build_ufc_cards()
    boxing = _build_boxing_cards()
    one = _build_one_cards()
    sumo = _build_sumo_cards()
    nba = _build_nba_cards()
    mlb = _build_mlb_cards()
    all_cards = f1 + horse + esports + motogp + nascar + ufc + boxing + one + sumo + nba + mlb
    CARDS = [apply_skill_module(c) for c in all_cards]
    from collections import Counter
    rc = Counter(c["role"] for c in CARDS)
    print(f"[cards] Loaded: F1={len(f1)}, Horse={len(horse)}, Esports={len(esports)}, MotoGP={len(motogp)}, NASCAR={len(nascar)}, UFC={len(ufc)}, Boxing={len(boxing)}, ONE={len(one)}, Sumo={len(sumo)}, NBA={len(nba)}, MLB={len(mlb)}, Total={len(CARDS)}")
    print(f"[cards] Roles: attacker={rc['attacker']}, supporter={rc['supporter']}, disruptor={rc['disruptor']}, dual={rc['dual']}")

_init_cards()

_CARDS_FULL: List[Dict] = []
_CARD_BY_ID: Dict[str, Dict] = {}

def _build_card_cache():
    global _CARDS_FULL, _CARD_BY_ID
    out = []
    for c in CARDS:
        c2 = dict(c)
        c2["atk"] = atk_of(c["ovr"], c["level"])
        c2["def"] = def_of(c["ovr"], c["level"])
        c2["element_cn"] = ELEMENT_CN[c["element"]]
        c2["element_icon"] = ELEMENT_ICON[c["element"]]
        c2["rarity_rank"] = rarity_rank(c["rarity"])
        c2["role"] = c.get("role", "attacker")
        out.append(c2)
    _CARDS_FULL = out
    _CARD_BY_ID = {c["id"]: c for c in out}

_build_card_cache()


# ═══════════════════ 公開 API ═══════════════════

def get_all_cards() -> List[Dict]:
    return _CARDS_FULL


def can_card_fill_slot(card: Dict, slot: str) -> bool:
    """slot: 'attacker' | 'buffer' | 'debuffer'"""
    role = card.get("role", "attacker")
    if slot == "attacker":
        return role in ("attacker", "dual")
    if slot == "buffer":
        return role in ("supporter", "dual")
    if slot == "debuffer":
        return role in ("disruptor", "dual")
    return False


def get_card_by_id(cid: str) -> Optional[Dict]:
    return _CARD_BY_ID.get(cid)


def full_pool_ids() -> List[str]:
    return [c["id"] for c in CARDS]

def normal_pool_ids() -> List[str]:
    return [c["id"] for c in CARDS if c["level"] < 50]

def legendary_pool_ids() -> List[str]:
    return [c["id"] for c in CARDS if c["level"] >= 50]


def get_seasons_for_sport(sport: str) -> List[int]:
    """回傳該項目所有出現過的賽季年份（降序）"""
    years = set()
    for c in CARDS:
        if c.get("sport") != sport:
            continue
        for y in c.get("years_active", []) or []:
            years.add(y)
    return sorted(years, reverse=True)


def filter_cards(sport: Optional[str] = None,
                 season: Optional[int] = None,
                 era: Optional[str] = None) -> List[Dict]:
    """按 sport / season(年份) / era(golden|classic|modern) 過濾"""
    out = get_all_cards()
    if sport:
        out = [c for c in out if c["sport"] == sport]
    if season is not None:
        out = [c for c in out if season in (c.get("years_active") or [])]
    if era:
        out = [c for c in out if c.get("era") == era]
    return out
