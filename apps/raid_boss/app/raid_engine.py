"""
Raid Boss Engine — 最多 3 名玩家 vs 永久大魔王 (AI)

核心規則：
  - 大魔王 HP 300 + 每次挑戰失敗 +8 (上限 600)
  - 大魔王每回合從普通牌池隨機抽 2 張牌，各發動 1 次攻擊（固定 2 次/回）
  - 抽到爛卡 ATK 低，抽到強卡 ATK 高；技能（力量爆發等）可補強弱牌
  - 玩家各自獨立出牌 (主力/Buffer/Debuffer)，傷害各自結算打 Boss
  - 連攜技能牌：低機率出現，發動後全隊合體 ATK ×1.5
  - 大魔王三階段：300→200 正常 / 200→100 激怒 / 100→0 狂暴
"""
import random
import uuid
import time
from typing import List, Dict, Optional, Tuple

from cards import (
    get_card_by_id, element_multiplier,
    normal_pool_ids, legendary_pool_ids,
)

# ─── 基本常數 ───────────────────────────────────────────────
RAIDER_INITIAL_HP   = 128   # 118→128，不要一下就死觸發，要先撐住才能發揮策略
RAIDER_INITIAL_MP   = 100
RAIDER_MP_PER_ROUND = 8     # 每回合回 8 MP，讓 buffer/debuffer 能持續用
BOSS_BASE_HP        = 300
BOSS_MAX_HP         = 400
BOSS_HP_FAIL_INC    = 8
BOSS_BASE_ATK       = 29    # 抽不到牌時的後備值（通常用不到）
BOSS_BASE_DEF       = 28    # 30→28，玩家傷害更有感
BOSS_RAGE_PER_ROUND = 10    # 12→10，Rage 爆發慢一點
BOSS_RAGE_MAX       = 100
MAX_ROUNDS          = 30    # 25→30，給最後 1 人翻盤空間
ROUND_SECONDS       = 60    # 玩家共享 60 秒，逾時未提交者自動 PASS
HAND_SIZE           = 8
CHAIN_SKILL_CHANCE  = 0.08
RAIDER_TRIPLE_HIT_CHANCE = 0.12   # 玩家三連擊機率
RAIDER_DOUBLE_HIT_CHANCE = 0.22   # 玩家雙擊機率（在非三擊情況下）
# Boss 固定每回合 2 次攻擊（不再用 RHYTHM_WEIGHTS），蓄力機率獨立判斷
BOSS_CHARGE_PROB    = 0.12  # 12% 機率蓄力（跳過本回合，下回合 ×1.5）
RAIDER_DEF_PCT      = 0.25  # 玩家基礎 25% 減傷（Buffer 卡的防禦值在此基礎上疊加）

# 每次攻擊的類型：單體（強）或範圍（弱），各自獨立抽
BOSS_ATK_MODES = [
    {"id": "single_strike", "label": "單體強擊", "targets": "single", "mult": 1.00, "weight": 45},
    {"id": "single_pierce", "label": "單體穿刺", "targets": "single", "mult": 0.90, "weight": 20, "pierce": True},
    {"id": "aoe_sweep",     "label": "範圍掃射", "targets": "all",    "mult": 0.40, "weight": 25},
    {"id": "aoe_curse",     "label": "範圍詛咒", "targets": "all",    "mult": 0.28, "weight": 10, "debuff": "atk_down"},
]
_BOSS_ATK_WEIGHTS = [m["weight"] for m in BOSS_ATK_MODES]

# Phase 加成
PHASE_BONUS = {
    "phase1": {"atk_mult": 1.0,  "rage_inc": 0,   "def_mult": 1.0},
    "phase2": {"atk_mult": 1.15, "rage_inc": 8,   "def_mult": 1.0},
    "phase3": {"atk_mult": 1.3,  "rage_inc": 10,  "def_mult": 0.65},  # Phase3 DEF 降低，給最後 1 人破防機會（但非太低）
}

# ─── 魔王專屬技能（每回合最多一個，隨機抽）──────────────────
# 卡牌跟玩家一樣是運動員，但賦予更高攻擊力 + 技能
BOSS_SKILLS = [
    {
        "id":    "power_surge",
        "label": "力量爆發",
        "icon":  "⚡",
        "desc":  "本回合 ATK ×1.6",
        "effect": "atk_boost",
        "value": 1.6,
        "weight": 25,
    },
    {
        "id":    "buffer_shatter",
        "label": "Buffer 粉碎",
        "icon":  "💔",
        "desc":  "削除全場玩家 Buffer 效果，本回合 Buffer 無效",
        "effect": "nullify_buffer",
        "value": 1,
        "weight": 12,
    },
    {
        "id":    "debuff_reflect",
        "label": "Debuff 反制",
        "icon":  "🛡️",
        "desc":  "本回合免疫玩家 Debuffer，並反彈 50% Debuff 效果給施法者",
        "effect": "reflect_debuff",
        "value": 0.5,
        "weight": 20,
    },
    {
        "id":    "dual_blade",
        "label": "二刀流",
        "icon":  "⚔️⚔️",
        "desc":  "本回合 + 下回合各追加一次額外單體攻擊",
        "effect": "dual_blade",
        "value": 2,   # 持續 2 回合
        "weight": 20,
    },
    {
        "id":    "regeneration",
        "label": "王者再生",
        "icon":  "💜",
        "desc":  "回復 HP 20 + 本輪 Rage 清零（用 MP 概念理解：Rage 是消耗品）",
        "effect": "regen",
        "value": 20,
        "weight": 15,
    },
]
_BOSS_SKILL_WEIGHTS = [s["weight"] for s in BOSS_SKILLS]
# 每 3 回合才觸發一次技能（避免過於壓迫）
BOSS_SKILL_INTERVAL = 3

WEATHER_TYPES = [
    {"id":"clear",   "en":"Clear Sky",    "icon":"☀️", "element":None,   "bonus":0},
    {"id":"gale",    "en":"Gale Force",   "icon":"🌪️", "element":"wind",  "bonus":8},
    {"id":"breeze",  "en":"Breeze",       "icon":"🍃", "element":"wind",  "bonus":4},
    {"id":"storm",   "en":"Thunderstorm", "icon":"⛈️", "element":"water", "bonus":8},
    {"id":"drizzle", "en":"Drizzle",      "icon":"🌧️", "element":"water", "bonus":4},
    {"id":"inferno", "en":"Inferno",      "icon":"🔥", "element":"fire",  "bonus":8},
    {"id":"ember",   "en":"Ember Ash",    "icon":"🌋", "element":"fire",  "bonus":4},
    {"id":"quake",   "en":"Earthquake",   "icon":"🌋", "element":"earth", "bonus":8},
    {"id":"tremor",  "en":"Tremor",       "icon":"💫", "element":"earth", "bonus":4},
]
_WEATHER_WEIGHTS = [40, 6, 9, 6, 9, 6, 9, 6, 9]

BOSS_WEAKNESS_INTERVAL = 3
BOSS_WEAKNESS_BONUS = 1.20
TEAM_ELEMENT_SYNC_BONUS = 1.50
BOSS_WEAKNESS_ELEMENTS = ["wind", "water", "fire", "earth"]
BOSS_WEAKNESS_META = {
    "wind":  {"cn": "風", "icon": "💨"},
    "water": {"cn": "水", "icon": "💧"},
    "fire":  {"cn": "火", "icon": "🔥"},
    "earth": {"cn": "土", "icon": "🌿"},
}
BOSS_WEAKNESS_HINTS = {
    ("water", "wind"): "狂風擾吹，加上暴雨，Tiamat感到不悅",
    ("fire", "wind"): "焚風捲起火星，Tiamat的鱗片微微收緊",
    ("earth", "wind"): "碎石被狂風捲上天際，Tiamat低聲咆哮",
    ("fire", "water"): "蒸氣與烈焰交錯，Tiamat暫時失去平衡",
    ("earth", "water"): "濕土吞住龍爪，Tiamat的步伐變沉",
    ("earth", "fire"): "熔岩壓過裂地，Tiamat的怒火出現縫隙",
}

RAIDS: Dict[str, "RaidState"] = {}

def _now() -> int:
    return int(time.time())

def _gen_id() -> str:
    return uuid.uuid4().hex[:12]


# ─── Boss 難度持久化 (單一 dict，程式重啟歸零；之後可換 DB) ────────
_BOSS_PERSISTENT: Dict = {
    "fail_count":   0,
    "kill_count":   0,
    "hp_bonus":     0,   # fail_count * BOSS_HP_FAIL_INC
    "era":          1,   # 每次被打倒 era++
    "last_winner":  None,
}

def boss_current_max_hp() -> int:
    return min(BOSS_MAX_HP, BOSS_BASE_HP + _BOSS_PERSISTENT["hp_bonus"])

def on_raid_fail():
    _BOSS_PERSISTENT["fail_count"] += 1
    _BOSS_PERSISTENT["hp_bonus"]   += BOSS_HP_FAIL_INC

def on_raid_win(winner_names: List[str]):
    _BOSS_PERSISTENT["kill_count"] += 1
    _BOSS_PERSISTENT["era"]        += 1
    _BOSS_PERSISTENT["hp_bonus"]   = 0   # 重置
    _BOSS_PERSISTENT["fail_count"] = 0
    _BOSS_PERSISTENT["last_winner"] = ", ".join(winner_names)

def boss_status() -> Dict:
    return {
        **_BOSS_PERSISTENT,
        "current_max_hp": boss_current_max_hp(),
    }


# ─── 連攜技能牌 ─────────────────────────────────────────────
CHAIN_CARD = {
    "id":       "CHAIN_SKILL",
    "name":     "連攜技",
    "name_short": "CHAIN",
    "sport":    "RAID",
    "ovr":      99,
    "level":    50,
    "rarity":   "raid",
    "element":  "fire",
    "atk":      0,
    "def":      0,
    "mp_cost":  20,
    "role":     "chain",
    "buff_effect":   {"type": "chain_trigger", "value": 1, "desc": "發動全隊連攜 ATK×1.5"},
    "debuff_effect": None,
}


# ─── RaidState ───────────────────────────────────────────────
class RaidState:
    def __init__(self, raider_names: List[str], seed: Optional[int] = None,
                 human_names: Optional[List[str]] = None):
        assert 1 <= len(raider_names) <= 3
        self.id         = _gen_id()
        self.raiders    = raider_names          # ["alice","bob","carol"]
        # 真人玩家集合（不會被 ai_auto_submit 代替出牌）；預設全員為真人
        self.human_raiders: set = set(human_names) if human_names is not None else set(raider_names)
        self.rng        = random.Random(seed) if seed is not None else random.Random()

        n = len(raider_names)
        # HP scale 依玩家人數；多段攻擊使多人傷害倍增，故 2v1/3v1 HP 拉高補償
        hp_scale = {1: 0.25, 2: 0.84, 3: 2.04}[n]
        self.boss_max_hp = int(boss_current_max_hp() * hp_scale)
        self.boss_hp     = self.boss_max_hp
        self.boss_rage   = 0
        self.boss_charge = False  # 蓄力狀態
        self.boss_debuffs: Dict = {}  # {"atk_down": rounds_remaining}
        self.boss_skill_active: Optional[Dict] = None   # 本回合技能
        self.dual_blade_remaining: int = 0              # 二刀流剩餘回合

        # 玩家狀態
        self.hp: Dict[str, int] = {n: RAIDER_INITIAL_HP for n in raider_names}
        self.mp: Dict[str, int] = {n: RAIDER_INITIAL_MP for n in raider_names}
        self.alive: Dict[str, bool] = {n: True for n in raider_names}
        self.aggro: Dict[str, int]  = {n: 0 for n in raider_names}   # 仇恨值

        # 手牌
        self.normal_pool    = normal_pool_ids()
        self.legendary_pool = legendary_pool_ids()
        self.hand: Dict[str, List[str]] = {n: [] for n in raider_names}
        for name in raider_names:
            self._init_hand(name)

        # Boss 手牌（8張，和玩家一樣可見）
        self.boss_hand_ids: List[str] = []
        self.boss_drawn_ids: List[str] = []
        self._init_boss_hand()

        self.round     = 1
        self.weather   = self._roll_weather()
        self.boss_weakness: Optional[Dict] = None
        self.pending: Dict[str, Optional[Dict]] = {n: None for n in raider_names}
        self.swap_used: Dict[str, bool] = {n: False for n in raider_names}
        self.rounds_log: List[Dict] = []
        self.winner: Optional[str] = None   # "raiders" | "boss"
        self.created_at     = _now()
        self.last_action_at = self.created_at
        self.round_started_at = self.created_at
        self.chain_active   = False   # 本回合連攜觸發
        self.team_fighting_spirit: int = 0  # 鬥氣值 0=未觸發 >0=激活中 -1=已耗盡
        self.boss_drawn: List[Dict] = []         # 本回合 Boss 抽到的 2 張牌
        self._boss_hit_this_round: List[str] = []  # 本回合已被打過的玩家
        self.ally_intents: Dict[str, Dict] = {}  # AI 隊友預亮牌意圖
        # 隱藏技能：個人三槽同屬性 → 該次傷害 1.5 倍（不公開給前端）
        self.double_atk_active: bool = False     # 保留欄位名相容舊 API，不公開觸發狀態
        self.double_atk_cooldown: int = 0        # 保留欄位名相容舊 API
        self._compute_ally_intents()

    # ── 手牌管理 ─────────────────────────────────────────────
    def _init_hand(self, name: str):
        for _ in range(4):
            self.hand[name].append(self._draw_by_role(name, "attacker"))
        for _ in range(2):
            self.hand[name].append(self._draw_by_role(name, "supporter"))
        for _ in range(2):
            self.hand[name].append(self._draw_by_role(name, "disruptor"))

    def _draw_by_role(self, name: str, role: str) -> str:
        wanted = {"attacker": ("attacker","dual"), "supporter": ("supporter","dual"),
                  "disruptor": ("disruptor","dual")}.get(role, ())
        avail = [cid for cid in self.normal_pool
                 if cid not in self.hand[name]
                 and (get_card_by_id(cid) or {}).get("role") in wanted]
        if not avail:
            avail = [cid for cid in self.normal_pool if cid not in self.hand[name]]
        if not avail:
            avail = self.normal_pool
        return self.rng.choice(avail)

    def _draw_card(self, name: str) -> str:
        # 8% 機率抽到連攜牌
        if self.rng.random() < CHAIN_SKILL_CHANCE:
            return "CHAIN_SKILL"
        avail = [cid for cid in self.normal_pool if cid not in self.hand[name]]
        if not avail:
            avail = self.normal_pool
        return self.rng.choice(avail)

    def _replenish_hand(self, name: str, played: List[str]):
        for cid in played:
            if cid in self.hand[name]:
                self.hand[name].remove(cid)

        # 統計補牌前各 role 缺口，維持 4 attacker : 2 supporter : 2 disruptor
        def _count_role(r):
            return sum(1 for cid in self.hand[name]
                       if cid != "CHAIN_SKILL"
                       and (get_card_by_id(cid) or {}).get("role") in r)

        targets = {"attacker": 4, "supporter": 2, "disruptor": 2}
        role_keys = {"attacker": ("attacker","dual"), "supporter": ("supporter","dual"), "disruptor": ("disruptor","dual")}

        for role, target in targets.items():
            cur = _count_role(role_keys[role])
            while cur < target and len(self.hand[name]) < HAND_SIZE:
                self.hand[name].append(self._draw_by_role(name, role))
                cur += 1

        # 剩餘空位用隨機牌（含連攜牌機率）填滿；任何補牌路徑都不可超過 HAND_SIZE。
        while len(self.hand[name]) < HAND_SIZE:
            self.hand[name].append(self._draw_card(name))
        if len(self.hand[name]) > HAND_SIZE:
            self.hand[name] = self.hand[name][:HAND_SIZE]

    # ── Boss 抽牌 ─────────────────────────────────────────────
    def _init_boss_hand(self):
        pool = list(self.normal_pool)
        self.rng.shuffle(pool)
        self.boss_hand_ids = pool[:8]

    def boss_hand_as_cards(self) -> List[Dict]:
        return [c for cid in self.boss_hand_ids if (c := get_card_by_id(cid))]

    def _boss_draw_cards(self) -> List[Dict]:
        """從 boss 手牌抽 2 張，補牌維持 8 張；每張獨立抽是否為多段攻擊卡"""
        if len(self.boss_hand_ids) < 2:
            self._init_boss_hand()
        drawn_ids = self.rng.sample(self.boss_hand_ids, min(2, len(self.boss_hand_ids)))
        drawn_cards = []
        for cid in drawn_ids:
            c = get_card_by_id(cid)
            if not c:
                continue
            card = dict(c)   # shallow copy，避免污染共享 card 資料
            roll = self.rng.random()
            if roll < 0.05:
                card["boss_hits"] = 3   # 三回攻擊 5%
            elif roll < 0.20:
                card["boss_hits"] = 2   # 二回攻擊 15%
            else:
                card["boss_hits"] = 1
            drawn_cards.append(card)
        # 移除已出的牌
        for cid in drawn_ids:
            if cid in self.boss_hand_ids:
                self.boss_hand_ids.remove(cid)
        # 補牌
        existing = set(self.boss_hand_ids)
        avail = [cid for cid in self.normal_pool if cid not in existing]
        self.rng.shuffle(avail)
        self.boss_hand_ids.extend(avail[:len(drawn_ids)])
        self.boss_drawn_ids = drawn_ids
        return drawn_cards

    # ── 天氣 ──────────────────────────────────────────────────
    def _roll_weather(self) -> Dict:
        return self.rng.choices(WEATHER_TYPES, weights=_WEATHER_WEIGHTS, k=1)[0]

    def _roll_boss_weakness(self) -> Dict:
        elems = self.rng.sample(BOSS_WEAKNESS_ELEMENTS, 2)
        key = tuple(sorted(elems))
        return {
            "elements": elems,
            "bonus_pct": 20,
            "hint": BOSS_WEAKNESS_HINTS.get(key, "異樣的氣流纏上Tiamat，牠顯得煩躁"),
            "icons": [BOSS_WEAKNESS_META[e]["icon"] for e in elems],
            "names": [BOSS_WEAKNESS_META[e]["cn"] for e in elems],
        }

    def _current_boss_weakness(self) -> Optional[Dict]:
        if self.winner or self.round % BOSS_WEAKNESS_INTERVAL != 0:
            return None
        if not self.boss_weakness:
            self.boss_weakness = self._roll_boss_weakness()
        return self.boss_weakness

    # ── 狀態 ──────────────────────────────────────────────────
    def _phase(self) -> str:
        ratio = self.boss_hp / self.boss_max_hp
        if ratio > 2/3:   return "phase1"
        elif ratio > 1/3: return "phase2"
        else:             return "phase3"

    def _boss_effective_atk(self) -> int:
        mult = PHASE_BONUS[self._phase()]["atk_mult"]
        debuff = 0.75 if self.boss_debuffs.get("atk_down", 0) > 0 else 1.0
        return int(BOSS_BASE_ATK * mult * debuff)

    def _boss_effective_def(self) -> int:
        mult = PHASE_BONUS[self._phase()]["def_mult"]
        return int(BOSS_BASE_DEF * mult)

    def hand_as_cards(self, name: str) -> List[Dict]:
        result = []
        for cid in self.hand[name]:
            if cid == "CHAIN_SKILL":
                result.append(CHAIN_CARD)
            else:
                c = get_card_by_id(cid)
                if c:
                    result.append(c)
        return result

    def public_state(self, viewer: Optional[str] = None) -> Dict:
        self._auto_pass_expired()
        alive_raiders = [n for n in self.raiders if self.alive[n]]
        # Non-player viewer (spectator) — treat as None for hand/intent access
        if viewer and viewer not in self.raiders:
            viewer = None
        return {
            "id":           self.id,
            "raiders":      self.raiders,
            "alive":        self.alive,
            "round":        self.round,
            "max_rounds":   MAX_ROUNDS,
            "boss": {
                "hp":       self.boss_hp,
                "max_hp":   self.boss_max_hp,
                "rage":     self.boss_rage,
                "phase":    self._phase(),
                "charge":   self.boss_charge,
                "debuffs":  self.boss_debuffs,
                "atk":         self._boss_effective_atk(),
                "def":         self._boss_effective_def(),
                "era":         _BOSS_PERSISTENT["era"],
                "fail_streak": _BOSS_PERSISTENT["fail_count"],
                "skill":       self.boss_skill_active,
                "dual_blade_remaining": self.dual_blade_remaining,
                "drawn_cards": [
                    {"name": c.get("name_short", c.get("name","?")), "atk": c.get("atk",0), "element": c.get("element",""), "hits": c.get("boss_hits", 1)}
                    for c in self.boss_drawn
                ],
                "weakness": self._current_boss_weakness(),
            },
            "raiders_hp":   self.hp,
            "raiders_mp":   self.mp,
            "aggro":        self.aggro,
            "weather":      self.weather,
            "chain_active": self.chain_active,
            "pending_submitted": {n: (self.pending[n] is not None) for n in self.raiders},
            "round_seconds_total": ROUND_SECONDS,
            "round_seconds_left": self._round_seconds_left(),
            # 自己看完整手牌，也能看到隊友手牌（用來協調策略）
            "my_hand":      self.hand_as_cards(viewer) if viewer else [],
            "teammates_hands": {
                n: self.hand_as_cards(n)
                for n in self.raiders if n != viewer and self.alive.get(n)
            },
            "teammates_played": {
                n: self.pending[n] for n in self.raiders if n != viewer and self.pending[n]
            },
            "ally_intents": {
                n: self.ally_intents.get(n, {})
                for n in self.raiders if n != viewer and self.alive.get(n)
            },
            "winner":       self.winner,
            "team_fighting_spirit": self.team_fighting_spirit,
            "double_atk_active":   False,
            "double_atk_cooldown": 0,
            "dead_count": sum(1 for n in self.raiders if not self.alive[n]),
            "rounds_log":   self.rounds_log[-5:],  # 最近 5 回合
            "boss_hand":    self.boss_hand_as_cards(),
            "boss_drawn_ids": self.boss_drawn_ids,
            "swap_used":    self.swap_used,
        }


    def _round_seconds_left(self) -> int:
        return max(0, ROUND_SECONDS - (_now() - self.round_started_at))

    def _auto_pass_expired(self) -> bool:
        """When the shared player timer expires, PASS every alive non-submitter and resolve."""
        if self.winner or self._round_seconds_left() > 0:
            return False
        changed = False
        for n in self.raiders:
            if self.alive[n] and self.pending[n] is None:
                self.pending[n] = {"action": "pass", "auto": "timeout"}
                changed = True
        if changed:
            self.last_action_at = _now()
        all_submitted = all(self.pending[n] is not None for n in self.raiders if self.alive[n])
        if all_submitted:
            self._resolve_round()
            return True
        return False

    # ── 出牌 ──────────────────────────────────────────────────
    def submit_action(self, name: str, action: Dict) -> Dict:
        """action = {action:"play"|"pass", attacker, buffer, debuffer} or {action:"swap", cards:[...]}"""
        if self._auto_pass_expired():
            return {"ok": True, "resolved": True, "timeout": True, "log": self.rounds_log[-1].get("log", []) if self.rounds_log else []}
        if self.winner:
            return {"ok": False, "msg": "已結束"}
        if name not in self.raiders:
            return {"ok": False, "msg": "不在此場"}
        if not self.alive[name]:
            self.pending[name] = {"action": "dead"}
            return {"ok": True, "auto": True}
        if self.pending[name] is not None:
            return {"ok": False, "msg": "已送出"}

        act = action.get("action", "pass")

        if act == "swap":
            if self.swap_used[name]:
                return {"ok": False, "msg": "本回合已換牌"}
            cards = action.get("cards", [])[:3]
            for cid in cards:
                if cid in self.hand[name]:
                    self.hand[name].remove(cid)
                    self.hand[name].append(self._draw_card(name))
            # 確保主攻不少於 3 張
            atk_cnt = sum(1 for cid in self.hand[name]
                          if (get_card_by_id(cid) or CHAIN_CARD if cid == "CHAIN_SKILL" else get_card_by_id(cid) or {}).get("role") in ("attacker","dual","chain"))
            while atk_cnt < 3:
                self.hand[name].append(self._draw_by_role(name, "attacker"))
                atk_cnt += 1
            self.swap_used[name] = True
            return {"ok": True, "hand": self.hand_as_cards(name)}

        self.pending[name] = action
        self.last_action_at = _now()

        all_submitted = all(
            self.pending[n] is not None for n in self.raiders if self.alive[n]
        )
        if all_submitted:
            return {"ok": True, "resolved": True, "log": self._resolve_round()}
        return {"ok": True, "waiting": True}

    def _pick_action_for(self, name: str, target_element: Optional[str] = None) -> Dict:
        """Phase-aware + element-coordinated card selection for a single AI raider."""
        phase = self._phase()
        hand = self.hand[name]

        def card_atk(cid: str) -> int:
            return (get_card_by_id(cid) or {}).get("atk", 0) if cid != "CHAIN_SKILL" else 0

        def card_elem(cid: str) -> Optional[str]:
            return (get_card_by_id(cid) or {}).get("element") if cid != "CHAIN_SKILL" else None

        def buf_score(cid: str) -> float:
            be = (get_card_by_id(cid) or {}).get("buff_effect") or {}
            t, v = be.get("type",""), be.get("value", 0)
            return float(v) if "atk" in t else (v * 0.5 if t else 0.0)

        def deb_score(cid: str) -> float:
            de = (get_card_by_id(cid) or {}).get("debuff_effect") or {}
            t, v = de.get("type",""), de.get("value", 0)
            return float(v) if "def" in t else (v * 0.3 if t else 0.0)

        attackers  = [c for c in hand if c != "CHAIN_SKILL" and (get_card_by_id(c) or {}).get("role") in ("attacker","dual")]
        supporters = [c for c in hand if (get_card_by_id(c) or {}).get("role") in ("supporter","dual")]
        disruptors = [c for c in hand if (get_card_by_id(c) or {}).get("role") in ("disruptor","dual")]

        att = None
        if attackers:
            if phase == "phase3":
                # Phase 3 暴怒：使出最強攻擊牌
                att = max(attackers, key=card_atk)
            elif target_element and self.double_atk_cooldown == 0:
                # 嘗試配合隊友屬性觸發三人同元素協同
                same_elem = [c for c in attackers if card_elem(c) == target_element]
                if same_elem:
                    att = max(same_elem, key=card_atk)
                else:
                    att = max(attackers, key=card_atk)
            else:
                att = max(attackers, key=card_atk)

        buf_pool = [c for c in supporters if c != att]
        buf = None
        if buf_pool:
            if phase == "phase3":
                buf = max(buf_pool, key=buf_score)
            else:
                # Phase 1/2：只用普通 buffer，保留最強的給後期
                buf_sorted = sorted(buf_pool, key=buf_score)
                buf = buf_sorted[0] if buf_sorted else None

        deb_pool = [c for c in disruptors if c not in (att, buf)]
        deb = None
        if deb_pool:
            if phase == "phase3":
                # Phase 3 優先降 Boss DEF
                deb = max(deb_pool, key=deb_score)
            else:
                deb = deb_pool[0]

        if not att:
            return {"action": "pass"}
        act: Dict = {"action": "play"}
        if att: act["attacker"] = att
        if buf: act["buffer"] = buf
        if deb: act["debuffer"] = deb
        return act

    def _coordinated_element(self, exclude: Optional[str] = None) -> Optional[str]:
        """Find the most common element among AI attackers for team element coordination."""
        from collections import Counter
        elems: List[str] = []
        for n in self.raiders:
            if not self.alive[n] or n == exclude:
                continue
            for cid in self.hand[n]:
                if cid == "CHAIN_SKILL":
                    continue
                c = get_card_by_id(cid)
                if c and c.get("role") in ("attacker","dual") and c.get("element"):
                    elems.append(c["element"])
                    break
        if not elems:
            return None
        return Counter(elems).most_common(1)[0][0]

    def ai_auto_submit(self, human_names: List[str]) -> bool:
        """Auto-submit actions for alive raiders not in human_names. Returns True if round resolved."""
        if self.winner:
            return False
        # 協調屬性：找所有 AI 玩家中最多人使用的元素（用於三人同元素協同）
        target_elem = None
        if self.double_atk_cooldown == 0:
            target_elem = self._coordinated_element()
        changed = False
        for n in self.raiders:
            if n in human_names:
                continue
            if not self.alive[n] or self.pending[n] is not None:
                continue
            self.pending[n] = self._pick_action_for(n, target_elem)
            changed = True
        if changed:
            all_submitted = all(self.pending[n] is not None for n in self.raiders if self.alive[n])
            if all_submitted:
                self._resolve_round()
                return True
        return False

    def _compute_ally_intents(self):
        """Pre-compute AI ally card picks for display — does NOT commit to pending."""
        target_elem = None
        if self.double_atk_cooldown == 0:
            target_elem = self._coordinated_element()
        self.ally_intents = {}
        for n in self.raiders:
            if not self.alive[n]:
                continue
            act = self._pick_action_for(n, target_elem)
            intent: Dict = {k: v for k, v in act.items() if k != "action"}
            self.ally_intents[n] = intent

    # ── 回合結算 ──────────────────────────────────────────────
    def _resolve_round(self) -> List[str]:
        log = [f"=== Round {self.round} ==="]

        # 0a. 鬥氣觸發：只要有隊員陣亡即啟動，首次觸發設為 100
        _dead_at_start = sum(1 for n in self.raiders if not self.alive[n])
        if _dead_at_start >= 1 and self.team_fighting_spirit == 0:
            self.team_fighting_spirit = 80   # 4回合（80/20=4），比5回合短，不能靠鬥氣撐整場
            _mult = "×2.0" if _dead_at_start >= 2 else "×1.5"
            log.append(f"🔥 【鬥氣】隊伍鬥氣激活！全員 ATK {_mult}（鬥氣 80 / 4回合）")

        phase = self._phase()
        phase_cfg = PHASE_BONUS[phase]

        # 0. 魔王技能抽取（每 BOSS_SKILL_INTERVAL 回合一次）
        self.boss_skill_active = None
        if self.round % BOSS_SKILL_INTERVAL == 0:
            skill = self.rng.choices(BOSS_SKILLS, weights=_BOSS_SKILL_WEIGHTS, k=1)[0]
            self.boss_skill_active = skill
            log.append(f"🌑 魔王技能：{skill['icon']} {skill['label']} — {skill['desc']}")
            if skill["effect"] == "regen":
                heal = int(skill["value"])
                self.boss_hp = min(self.boss_max_hp, self.boss_hp + heal)
                self.boss_rage = 0
                log.append(f"  魔王回復 {heal} HP，Rage 清零")
            elif skill["effect"] == "dual_blade":
                self.dual_blade_remaining = int(skill["value"])

        # 1. 連攜檢查
        self.chain_active = any(
            self.pending[n] and self.pending[n].get("action") == "play"
            and "CHAIN_SKILL" in [
                self.pending[n].get("attacker"),
                self.pending[n].get("buffer"),
                self.pending[n].get("debuffer"),
            ]
            for n in self.raiders if self.alive[n]
        )
        if self.chain_active:
            log.append("💥 連攜技發動！全隊攻擊 ×1.5")

        # 1b. 隱藏技能重置：實際判斷在每位玩家自己的三槽牌結算中完成。
        self.double_atk_active = False

        # 2. 玩家攻擊 Boss
        total_dmg_to_boss = 0
        for name in self.raiders:
            if not self.alive[name]:
                continue
            act = self.pending[name]
            if not act or act.get("action") == "pass":
                log.append(f"  {name} Pass")
                continue

            att_id  = act.get("attacker")
            buf_id  = act.get("buffer")
            deb_id  = act.get("debuffer")
            played  = [c for c in [att_id, buf_id, deb_id] if c]

            slot_cards = [get_card_by_id(cid) for cid in [att_id, buf_id, deb_id] if cid and cid != "CHAIN_SKILL"]
            personal_element_sync = (
                len(slot_cards) == 3
                and all(c and c.get("element") for c in slot_cards)
                and len({c["element"] for c in slot_cards}) == 1
            )

            # 連攜牌放主攻槽時，自動從手牌找最強普通主攻補上
            effective_att_id = att_id
            if att_id == "CHAIN_SKILL":
                fallback = [cid for cid in self.hand[name]
                            if cid != "CHAIN_SKILL"
                            and (get_card_by_id(cid) or {}).get("role") in ("attacker","dual")]
                if fallback:
                    effective_att_id = max(fallback, key=lambda cid: (get_card_by_id(cid) or {}).get("atk", 0))
                    log.append(f"  ⚡ {name} 連攜牌觸發，自動補上主攻 {(get_card_by_id(effective_att_id) or {}).get('name_short','?')}")
                else:
                    effective_att_id = None

            # MP 消耗（連攜牌在任何槽都扣 20 MP）
            mp_cost = 20 if "CHAIN_SKILL" in played else 0
            for cid in [buf_id, deb_id]:
                if cid and cid != "CHAIN_SKILL":
                    c = get_card_by_id(cid)
                    if c:
                        mp_cost += c.get("mp_cost", 0)
            mp_cost = min(mp_cost, self.mp[name])
            self.mp[name] -= mp_cost

            # 計算傷害
            atk = 0
            if effective_att_id and effective_att_id != "CHAIN_SKILL":
                c = get_card_by_id(effective_att_id)
                if c:
                    atk = c.get("atk", 0)
                    # 天氣加成
                    if self.weather.get("element") and c.get("element") == self.weather["element"]:
                        atk += self.weather["bonus"]

            # Buffer 效果（魔王 buffer_shatter 技能時無效）
            buff_atk_pct = 0
            skill_eff = (self.boss_skill_active or {}).get("effect", "")
            if buf_id and buf_id != "CHAIN_SKILL" and skill_eff != "nullify_buffer":
                c = get_card_by_id(buf_id)
                if c and c.get("buff_effect"):
                    btype = c["buff_effect"].get("type","")
                    bval  = c["buff_effect"].get("value", 0)
                    if btype == "self_atk_pct":    buff_atk_pct += bval
                    elif btype == "self_atk_flat": atk += bval
            elif buf_id and skill_eff == "nullify_buffer":
                log.append(f"  💔 {name} Buffer 被粉碎，無效！")

            atk_eff = int(atk * (1 + buff_atk_pct / 100))
            weakness = self._current_boss_weakness()
            if weakness and effective_att_id and effective_att_id != "CHAIN_SKILL":
                wc = get_card_by_id(effective_att_id)
                if wc and wc.get("element") in weakness.get("elements", []):
                    atk_eff = int(atk_eff * BOSS_WEAKNESS_BONUS)
                    log.append(f"  {name} 的元素刺入裂隙，Tiamat鱗片震顫")
            # 鬥氣：有隊員陣亡時激活，1死=×1.5(+50%)，2死=×2.0(+100%)
            if self.team_fighting_spirit > 0:
                _dc = sum(1 for n2 in self.raiders if not self.alive[n2])
                _fs_mult = 2.0 if _dc >= 2 else 1.5
                atk_eff = int(atk_eff * _fs_mult)

            # Debuffer 對 boss（魔王 reflect_debuff 技能時反彈）
            boss_def_reduction = 0
            if deb_id and deb_id != "CHAIN_SKILL":
                c = get_card_by_id(deb_id)
                if c and c.get("debuff_effect"):
                    dtype = c["debuff_effect"].get("type","")
                    dval  = c["debuff_effect"].get("value", 0)
                    if skill_eff == "reflect_debuff":
                        # 反彈 50% 效果給施法者（HP 直傷）
                        reflect_dmg = int(dval * 0.5)
                        self._deal_damage_to_raider(name, reflect_dmg, log)
                        log.append(f"  🛡️ {name} Debuff 被反彈！自損 {reflect_dmg} HP")
                    else:
                        if dtype in ("opp_def_pct", "opp_def_flat"):
                            boss_def_reduction += dval
                        elif dtype == "opp_hp_direct":
                            self.boss_hp -= dval
                            self.aggro[name] += dval
                            log.append(f"  {name} 直傷 Boss {dval} HP")

            # Debuffer 對 boss 降 Rage
            if deb_id and deb_id != "CHAIN_SKILL" and skill_eff != "reflect_debuff":
                c = get_card_by_id(deb_id)
                if c:
                    dtype = (c.get("debuff_effect") or {}).get("type", "")
                    if dtype == "opp_mp_drain":
                        rage_drain = min(20, self.boss_rage)
                        self.boss_rage -= rage_drain
                        log.append(f"  {name} 削減 Boss Rage -{rage_drain}")

            boss_def_eff = max(0, self._boss_effective_def() - boss_def_reduction)
            raw_dmg = max(2, atk_eff - int(boss_def_eff * 0.5))
            dmg = int(raw_dmg * 0.8)

            if self.chain_active:
                dmg = int(dmg * 1.5)

            # 玩家多段攻擊判定（三擊 12%，雙擊 22%，合計 34% 觸發）
            hit_roll = self.rng.random()
            if hit_roll < RAIDER_TRIPLE_HIT_CHANCE:
                dmg = dmg * 3
                log.append(f"  ⚔⚔⚔ {name} 三連擊！")
            elif hit_roll < RAIDER_TRIPLE_HIT_CHANCE + RAIDER_DOUBLE_HIT_CHANCE:
                dmg = dmg * 2
                log.append(f"  ⚔⚔ {name} 雙擊！")

            # 隱藏技能：個人三槽同屬性，該次傷害提高。
            if personal_element_sync:
                dmg = int(dmg * TEAM_ELEMENT_SYNC_BONUS)

            self.boss_hp    -= dmg
            self.aggro[name] += dmg
            total_dmg_to_boss += dmg
            log.append(f"  {name} → Boss -{dmg} HP (ATK={atk_eff} vs DEF={boss_def_eff})")

            self._replenish_hand(name, played)

        log.append(f"  Boss HP: {max(0, self.boss_hp)}/{self.boss_max_hp}")

        # 3. 檢查 Boss 死亡
        if self.boss_hp <= 0:
            self.boss_hp = 0
            self.winner  = "raiders"
            winners = [n for n in self.raiders if self.alive[n]]
            on_raid_win(winners)
            log.append(f"🏆 大魔王倒下！勝利者：{', '.join(winners)}")
            self._finalize(log)
            return log

        # 4. Boss 抽 2 張牌；每張依 boss_hits 執行 1/2/3 次攻擊（蓄力時下回合 ×1.5）
        rage_inc = BOSS_RAGE_PER_ROUND + phase_cfg["rage_inc"]
        self.boss_drawn = self._boss_draw_cards()

        def _hits_label(card: Dict) -> str:
            h = card.get("boss_hits", 1)
            nm = card.get("name_short", card.get("name", "?"))
            suffix = f" ×{h}" if h > 1 else ""
            return f"{nm}(ATK {card.get('atk',0)}){suffix}"

        drawn_names = " + ".join(_hits_label(c) for c in self.boss_drawn)
        log.append(f"🎴 魔王出牌：{drawn_names}")

        self._boss_hit_this_round = []   # 每回合重置

        def _exec_card_attacks(card: Dict, charge_mult: float = 1.0):
            hits = card.get("boss_hits", 1)
            if hits > 1:
                icons = "⚔⚔" if hits == 2 else "⚔⚔⚔"
                log.append(f"  {icons} {card.get('name_short','?')} 多段攻擊 ×{hits}！")
            for _ in range(hits):
                self._boss_attack(log, charge_mult=charge_mult, boss_card=card)

        if self.boss_charge:
            log.append("⚡ 魔王蓄力完畢，兩張同時爆發！")
            for card in self.boss_drawn:
                _exec_card_attacks(card, charge_mult=1.5)
            self.boss_charge = False
        elif self.rng.random() < BOSS_CHARGE_PROB:
            self.boss_charge = True
            self.boss_rage  += 20
            log.append("🔴 魔王蓄力中！下回合兩張爆發…")
        else:
            for card in self.boss_drawn:
                _exec_card_attacks(card)

        # 4b. 二刀流技能：用本回合第一張牌追加一次攻擊
        if self.dual_blade_remaining > 0:
            extra_card = self.boss_drawn[0] if self.boss_drawn else None
            log.append(f"⚔️⚔️ 二刀流追加攻擊！（剩 {self.dual_blade_remaining} 回合）")
            self._boss_attack(log, extra_blade=True, boss_card=extra_card)
            self.dual_blade_remaining -= 1

        # 5. Boss Rage 累積
        self.boss_rage = min(BOSS_RAGE_MAX, self.boss_rage + rage_inc)
        if self.boss_rage >= BOSS_RAGE_MAX:
            log.append("🌑 魔王憤怒爆發！額外範圍攻擊！")
            self._boss_attack(log, force_aoe=True, charge_mult=0.7)
            self.boss_rage = 0

        # 6. Debuff 倒計時
        for k in list(self.boss_debuffs.keys()):
            self.boss_debuffs[k] -= 1
            if self.boss_debuffs[k] <= 0:
                del self.boss_debuffs[k]

        # 6b. 鬥氣消耗（每回合 -20）
        if self.team_fighting_spirit > 0:
            self.team_fighting_spirit = max(0, self.team_fighting_spirit - 20)
            if self.team_fighting_spirit > 0:
                log.append(f"🔥 【鬥氣】全隊鬥氣剩餘 {self.team_fighting_spirit}/80（還剩 {self.team_fighting_spirit//20} 回合）")
            else:
                self.team_fighting_spirit = -1  # 耗盡標記
                log.append(f"💨 【鬥氣】全隊鬥氣耗盡，能力恢復正常")

        # 7. 檢查全滅
        alive_count = sum(1 for n in self.raiders if self.alive[n])
        if alive_count == 0:
            self.winner = "boss"
            on_raid_fail()
            log.append(f"💀 全滅！大魔王繼續成長 (HP上限 +{BOSS_HP_FAIL_INC})")
            self._finalize(log)
            return log

        # 8. 回合數限制
        if self.round >= MAX_ROUNDS:
            self.winner = "boss"
            on_raid_fail()
            log.append("⌛ 超過回合上限，大魔王獲勝")
            self._finalize(log)
            return log

        # 9. Phase 轉換提示
        new_phase = self._phase()
        if new_phase == "phase2" and len(self.rounds_log) > 0:
            prev_hp_ratio = (self.boss_hp + total_dmg_to_boss) / self.boss_max_hp
            if prev_hp_ratio > 2/3:
                log.append("🔥 大魔王進入激怒狀態！")
        if new_phase == "phase3":
            prev_hp_ratio = (self.boss_hp + total_dmg_to_boss) / self.boss_max_hp
            if prev_hp_ratio > 1/3:
                log.append("💥 大魔王進入狂暴狀態！但防禦降低，破綻期！")

        # 10. 回合結束：MP 回復
        for name in self.raiders:
            if self.alive[name]:
                self.mp[name] = min(RAIDER_INITIAL_MP, self.mp[name] + RAIDER_MP_PER_ROUND)

        # 11. 換回合
        self.rounds_log.append({
            "round": self.round,
            "boss_hp": self.boss_hp,
            "boss_rage": self.boss_rage,
            "raiders_hp": dict(self.hp),
            "raiders_mp": dict(self.mp),
            "log": log[:],
        })
        self.round     += 1
        self.weather    = self._roll_weather()
        self.boss_weakness = self._roll_boss_weakness() if self.round % BOSS_WEAKNESS_INTERVAL == 0 else None
        self.round_started_at = _now()
        self.chain_active = False
        self.pending    = {n: None for n in self.raiders}
        self.swap_used  = {n: False for n in self.raiders}
        self._compute_ally_intents()  # 新回合：重新預亮牌

        return log

    def _boss_attack(self, log: List[str], charge_mult: float = 1.0,
                     force_aoe: bool = False, extra_blade: bool = False,
                     boss_card: Optional[Dict] = None):
        alive = [n for n in self.raiders if self.alive[n]]
        if not alive:
            return

        if force_aoe:
            atk_type = next(m for m in BOSS_ATK_MODES if m["id"] == "aoe_sweep")
        else:
            atk_type = self.rng.choices(BOSS_ATK_MODES, weights=_BOSS_ATK_WEIGHTS, k=1)[0]

        # 用抽到的卡牌 ATK 作為基礎，fallback 到 BOSS_BASE_ATK
        phase_mult = PHASE_BONUS[self._phase()]["atk_mult"]
        debuff_mult = 0.75 if self.boss_debuffs.get("atk_down", 0) > 0 else 1.0
        base_atk = boss_card.get("atk", BOSS_BASE_ATK) if boss_card else BOSS_BASE_ATK
        # 天氣加成：Boss 的卡也吃天氣
        if boss_card and self.weather.get("element") and boss_card.get("element") == self.weather["element"]:
            base_atk += self.weather["bonus"]
        skill_eff = (self.boss_skill_active or {}).get("effect", "")
        skill_mult = (self.boss_skill_active or {}).get("value", 1.0) if skill_eff == "atk_boost" else 1.0
        boss_atk = int(base_atk * phase_mult * debuff_mult * charge_mult * skill_mult)
        if skill_eff == "atk_boost" and not extra_blade:
            card_name = boss_card.get("name_short", boss_card.get("name","?")) if boss_card else "?"
            log.append(f"  ⚡ 力量爆發！{card_name} ATK 強化至 {boss_atk}")
        raw_dmg  = max(2, int(boss_atk * atk_type["mult"]))

        if atk_type["targets"] == "single":
            # 優先打本回合尚未被打過的玩家；若全都被打過才重複鎖目標
            untouched = [n for n in alive if n not in self._boss_hit_this_round]
            candidates = untouched if untouched else alive
            # aggro 最高者；平局時隨機
            max_aggro = max(self.aggro[n] for n in candidates)
            top = [n for n in candidates if self.aggro[n] == max_aggro]
            target = self.rng.choice(top)
            self._boss_hit_this_round.append(target)
            dmg = raw_dmg
            self._deal_damage_to_raider(target, dmg, log)
            log.append(f"    ⚔️  {atk_type['label']} → {target} -{dmg} HP")
        else:
            # 範圍：打所有存活玩家
            for name in alive:
                self._deal_damage_to_raider(name, raw_dmg, log)
            log.append(f"    💥 {atk_type['label']} → 全場各 -{raw_dmg} HP")

        # debuff 效果
        if atk_type.get("debuff") == "atk_down":
            for name in alive:
                # 玩家 ATK 無直接欄位，用 log 標記，前端顯示
                pass
            log.append("    🌑 範圍詛咒：全場 ATK-20% 持續 1 回合")

    def _deal_damage_to_raider(self, name: str, dmg: int, log: List[str]):
        # 基礎減傷
        dmg = max(1, int(dmg * (1 - RAIDER_DEF_PCT)))
        # 鬥氣：輕微防禦加成，承受傷害 ÷1.2（死了還是痛，不能變鐵壁）
        if self.team_fighting_spirit > 0:
            dmg = max(1, int(dmg / 1.2))
        self.hp[name] = max(0, self.hp[name] - dmg)
        if self.hp[name] == 0:
            self.alive[name] = False
            log.append(f"    💀 {name} 陣亡！")

    def _finalize(self, log: List[str]):
        self.rounds_log.append({
            "round": self.round,
            "boss_hp": self.boss_hp,
            "boss_rage": self.boss_rage,
            "raiders_hp": dict(self.hp),
            "log": log[:],
        })


# ─── 公開 API ────────────────────────────────────────────────
def create_raid(raider_names: List[str], seed: Optional[int] = None,
                human_names: Optional[List[str]] = None) -> RaidState:
    raid = RaidState(raider_names, seed, human_names=human_names)
    RAIDS[raid.id] = raid
    return raid

def get_raid(raid_id: str) -> Optional[RaidState]:
    return RAIDS.get(raid_id)

def cleanup_old_raids(max_age_seconds: int = 3600):
    now = _now()
    dead = [rid for rid, r in RAIDS.items()
            if now - r.last_action_at > max_age_seconds]
    for rid in dead:
        del RAIDS[rid]
