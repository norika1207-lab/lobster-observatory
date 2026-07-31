"""
CardHouse v3.0 — Session-based 戰鬥引擎

規則 (2026-04-17 定稿):
  20 回合上限, 任一方 HP ≤ 0 先輸; 20 回合結束 HP 高者勝
  HP 起始 100, MP 起始 100 (每回合 +10, 上限 100)
  雙方每回合手牌維持 7 張 (結束自動補到 7)
  每回合可 換牌 1 次 (最多 3 張, 丟回池隨機重抽)
  每回合出牌: 3 槽 (主力/Buffer/Debuffer), 可只 1 張, 可 Pass
  Pass → 不行動; 對方照打 (你 DEF=0 吃滿); MP +10 補償
  出牌扣 MP: 每張卡右上角 mp_cost, 放進任一槽都扣; MP 不足不能用

傷害公式 (同步互扣):
  有效 ATK_A = 主力ATK × (1 + self_buff_atk_pct) + self_buff_atk_flat - opp_debuff_atk_flat
  然後套屬性相剋 (attacker_element vs opp_主力_element)
  有效 DEF_A = 主力DEF × (1 + self_buff_def_pct) + self_buff_def_flat - opp_debuff_def_flat
  B 扣 HP = max(0, 有效 ATK_A - 有效 DEF_B) + opp_hp_direct
  若 A 只出 Buff/Debuff 沒出主力 ATK = 0
  若 A 沒出任何防禦 DEF = 0
"""
import random
import uuid
import time
from typing import List, Dict, Optional, Tuple
from cards import (
    get_card_by_id, element_multiplier,
    normal_pool_ids, legendary_pool_ids, get_all_cards,
)


HAND_SIZE = 8
MAX_SWAP_PER_ROUND = 3
MAX_ROUNDS = 20
INITIAL_HP = 100
INITIAL_MP = 100
MP_PER_ROUND = 0        # v5: MP 不再回復，初始 100 整場用
MP_CAP = 100
PASS_MP_BONUS = 0       # v5: Pass 不送 MP
TURN_TIMEOUT_SECONDS = 20

LEGENDARY_ROUNDS_PER_MATCH = 3
MAX_LEGENDARY_STACK = 3

HP_DAMAGE_RATIO = 0.8
PASS_SHIELD_DEF = 25
LEGENDARY_DMG_BONUS = 10
MAX_RAISES_PER_SIDE = 3

WEATHER_TYPES = [
    {"id":"clear",   "cn":"晴空萬里", "icon":"☀️",  "element":None,    "bonus":0},
    {"id":"gale",    "cn":"狂風大作", "icon":"🌪️",  "element":"wind",   "bonus":8},
    {"id":"breeze",  "cn":"微風輕拂", "icon":"🍃",  "element":"wind",   "bonus":4},
    {"id":"storm",   "cn":"暴雨傾盆", "icon":"⛈️",  "element":"water",  "bonus":8},
    {"id":"drizzle", "cn":"細雨霏霏", "icon":"🌧️",  "element":"water",  "bonus":4},
    {"id":"inferno", "cn":"烈火焚天", "icon":"🔥",  "element":"fire",   "bonus":8},
    {"id":"ember",   "cn":"餘燼飛揚", "icon":"🌋",  "element":"fire",   "bonus":4},
    {"id":"quake",   "cn":"山崩地裂", "icon":"🌋",  "element":"earth",  "bonus":8},
    {"id":"tremor",  "cn":"輕微震動", "icon":"💫",  "element":"earth",  "bonus":4},
]
_WEATHER_WEIGHTS = [40, 6, 9, 6, 9, 6, 9, 6, 9]  # 40% 晴, 6% 各強型, 9% 各弱型


MATCHES: Dict[str, "MatchState"] = {}


def _now() -> int:
    return int(time.time())


def _gen_id() -> str:
    return uuid.uuid4().hex[:12]


class MatchState:
    __slots__ = ("id", "player_a", "player_b", "bet",
                 "hp", "mp", "hand", "round", "swap_used", "pending",
                 "rounds_log", "winner", "created_at", "last_action_at",
                 "normal_pool", "legendary_pool",
                 "legendary_rounds", "legendary_count", "rng", "weather",
                 "raises_a", "raises_b",
                 "item_fx", "scramble_pending")

    def __init__(self, player_a: str, player_b: str, bet: int, seed: Optional[int]):
        self.id = _gen_id()
        self.player_a = player_a
        self.player_b = player_b
        self.bet = bet
        self.hp = {"a": INITIAL_HP, "b": INITIAL_HP}
        self.mp = {"a": INITIAL_MP, "b": INITIAL_MP}
        self.rng = random.Random(seed) if seed is not None else random.Random()
        self.normal_pool = normal_pool_ids()
        self.legendary_pool = legendary_pool_ids()

        self.legendary_rounds = {
            "a": set(self.rng.sample(range(1, MAX_ROUNDS + 1), LEGENDARY_ROUNDS_PER_MATCH)),
            "b": set(self.rng.sample(range(1, MAX_ROUNDS + 1), LEGENDARY_ROUNDS_PER_MATCH)),
        }
        self.legendary_count = {"a": 0, "b": 0}

        self.hand = {"a": [], "b": []}
        # v6: 發牌比例 attacker:supporter:disruptor = 4:2:2 (共 HAND_SIZE=8)
        # 用戶規則：主攻不可少，讓玩家每回合都有主力可打
        for _side in ("a", "b"):
            for _ in range(4):
                self.hand[_side].append(self._draw_normal_by_role(_side, "attacker"))
            for _ in range(2):
                self.hand[_side].append(self._draw_normal_by_role(_side, "supporter"))
            for _ in range(2):
                self.hand[_side].append(self._draw_normal_by_role(_side, "disruptor"))
        self.round = 1
        self._maybe_deal_legendary("a")
        self._maybe_deal_legendary("b")

        self.swap_used = {"a": False, "b": False}
        self.pending: Dict[str, Optional[Dict]] = {"a": None, "b": None}
        self.item_fx: Dict[str, Dict] = {
            "a": {"double_atk": 0, "skip_opp": 0},
            "b": {"double_atk": 0, "skip_opp": 0},
        }
        self.scramble_pending: Dict[str, bool] = {"a": False, "b": False}
        self.rounds_log: List[Dict] = []
        self.winner: Optional[str] = None
        self.created_at = _now()
        self.last_action_at = self.created_at
        self.weather = self._roll_weather()
        self.raises_a = 0
        self.raises_b = 0

    def _draw_normal(self, side: str) -> str:
        avail = [i for i in self.normal_pool if i not in self.hand[side]]
        if not avail: avail = self.normal_pool
        return self.rng.choice(avail)

    def _draw_normal_by_role(self, side: str, role: str) -> str:
        """Draw from normal pool filtered by role. Accepts 'attacker' | 'supporter' | 'disruptor'.
        'dual' cards count for all three. Falls back to any unheld card, then any card."""
        role_ok = {
            "attacker":  ("attacker", "dual"),
            "supporter": ("supporter", "dual"),
            "disruptor": ("disruptor", "dual"),
        }
        wanted = role_ok.get(role, ())
        avail = [cid for cid in self.normal_pool
                 if cid not in self.hand[side]
                 and (get_card_by_id(cid) or {}).get("role") in wanted]
        if avail:
            return self.rng.choice(avail)
        # fallback 1: any unheld card
        alt = [cid for cid in self.normal_pool if cid not in self.hand[side]]
        if alt:
            return self.rng.choice(alt)
        # fallback 2: any card
        return self.rng.choice(self.normal_pool)

    def _count_hand_role(self, side: str, role_keys: Tuple[str, ...]) -> int:
        n = 0
        for cid in self.hand[side]:
            r = (get_card_by_id(cid) or {}).get("role", "attacker")
            if r in role_keys:
                n += 1
        return n

    def _draw_legendary(self, side: str) -> str:
        avail = [i for i in self.legendary_pool if i not in self.hand[side]]
        if not avail: avail = self.legendary_pool
        return self.rng.choice(avail)

    def _roll_weather(self) -> Dict:
        return self.rng.choices(WEATHER_TYPES, weights=_WEATHER_WEIGHTS, k=1)[0]

    def _maybe_deal_legendary(self, side: str) -> Optional[str]:
        if self.round not in self.legendary_rounds[side]:
            return None
        if self.legendary_count[side] >= MAX_LEGENDARY_STACK:
            return None
        cid = self._draw_legendary(side)
        self.hand[side].append(cid)
        self.legendary_count[side] += 1
        return cid

    def hand_as_cards(self, side: str) -> List[Dict]:
        return [get_card_by_id(cid) for cid in self.hand[side]]

    def public_state(self) -> Dict:
        """整場狀態 (雙方手牌都公開, 符合 PDF 規範)"""
        return {
            "id": self.id,
            "player_a": self.player_a,
            "player_b": self.player_b,
            "bet": self.bet,
            "round": self.round,
            "max_rounds": MAX_ROUNDS,
            "hp_a": self.hp["a"], "hp_b": self.hp["b"],
            "mp_a": self.mp["a"], "mp_b": self.mp["b"],
            "hand_a": self.hand_as_cards("a"),
            "hand_b": self.hand_as_cards("b"),
            "swap_used_a": self.swap_used["a"], "swap_used_b": self.swap_used["b"],
            "legendary_count_a": self.legendary_count["a"],
            "legendary_count_b": self.legendary_count["b"],
            "legendary_rounds_a": sorted(self.legendary_rounds["a"]),
            "legendary_rounds_b": sorted(self.legendary_rounds["b"]),
            "pending_a": self.pending["a"] is not None,
            "pending_b": self.pending["b"] is not None,
            "pending_preview_a": self.pending["a"],
            "pending_preview_b": self.pending["b"],
            "rounds_log": self.rounds_log,
            "winner": self.winner,
            "turn_timeout": TURN_TIMEOUT_SECONDS,
            "last_action_at": self.last_action_at,
            "weather": self.weather,
            "raises_a": self.raises_a,
            "raises_b": self.raises_b,
            "item_fx_a": self.item_fx["a"],
            "item_fx_b": self.item_fx["b"],
            "scramble_pending_a": self.scramble_pending["a"],
            "scramble_pending_b": self.scramble_pending["b"],
        }


def apply_item_fx(match_id: str, side: str, item_type: str) -> Dict:
    """Apply a one-shot item effect to a live match. Returns updated public state."""
    m = get_match(match_id)
    if m.winner:
        raise MatchError("Match already ended")
    if side not in ("a", "b"):
        raise MatchError("Invalid side")
    if item_type == "heal_hp":
        m.hp[side] = min(INITIAL_HP, m.hp[side] + 15)
    elif item_type == "heal_mp":
        m.mp[side] = min(MP_CAP, m.mp[side] + 30)
    elif item_type == "double_atk":
        m.item_fx[side]["double_atk"] += 1
    elif item_type == "skip_opp":
        m.item_fx[side]["skip_opp"] += 1
    elif item_type == "scramble":
        opp = "b" if side == "a" else "a"
        m.scramble_pending[opp] = True
    else:
        raise MatchError(f"Unknown item: {item_type}")
    return m.public_state()


def clear_scramble_pending(match_id: str, side: str):
    m = get_match(match_id)
    m.scramble_pending[side] = False


def fold_match(match_id: str, side: str) -> Dict:
    m = get_match(match_id)
    if m.winner:
        raise MatchError("Match already ended")
    if side not in ("a", "b"):
        raise MatchError("side must be a or b")
    winner = "B" if side == "a" else "A"
    m.winner = winner
    m.rounds_log.append({
        "round": m.round,
        "log": [f"{'A' if side=='a' else 'B'} 棄牌認輸 🏳️"],
        "damage_to_a": 0, "damage_to_b": 0, "actions": {},
    })
    return m.public_state()


class MatchError(ValueError):
    pass


def start_match(player_a: str = "player_a", player_b: str = "player_b",
                bet: int = 100, seed: Optional[int] = None) -> MatchState:
    m = MatchState(player_a, player_b, bet, seed)
    MATCHES[m.id] = m
    return m


def get_match(match_id: str) -> MatchState:
    m = MATCHES.get(match_id)
    if not m:
        raise MatchError(f"match {match_id} not found")
    return m


def swap_cards(match_id: str, side: str, card_ids_to_swap: List[str]) -> Dict:
    m = get_match(match_id)
    if m.winner:
        raise MatchError("match ended")
    if side not in ("a", "b"):
        raise MatchError("side must be 'a' or 'b'")
    if m.swap_used[side]:
        raise MatchError("本回合已用過換牌")
    if len(card_ids_to_swap) > MAX_SWAP_PER_ROUND:
        raise MatchError(f"一次最多換 {MAX_SWAP_PER_ROUND} 張")
    if not card_ids_to_swap:
        raise MatchError("至少要選 1 張換")

    hand = m.hand[side]
    for cid in card_ids_to_swap:
        if cid not in hand:
            raise MatchError(f"卡 {cid} 不在你的手牌")

    n_swap = len(card_ids_to_swap)
    for cid in card_ids_to_swap:
        hand.remove(cid)

    # v6: 換牌後 attacker (含 dual) 不得低於 3 張
    # 先檢查剩下的手牌有幾個 attacker，若不足強制先抽 attacker
    remaining_att = m._count_hand_role(side, ("attacker", "dual"))
    MIN_ATTACKERS = 3
    force_att = max(MIN_ATTACKERS - remaining_att, 0)
    force_att = min(force_att, n_swap)  # 不能超過本次 swap 張數
    drawn: List[str] = []
    for _ in range(force_att):
        drawn.append(m._draw_normal_by_role(side, "attacker"))
        hand.append(drawn[-1])

    # 剩餘格子：保持 buffer/debuffer 平衡 — 看哪邊少就抽哪邊，都夠就隨機
    rest = n_swap - force_att
    for _ in range(rest):
        sup_ct = m._count_hand_role(side, ("supporter",))
        dis_ct = m._count_hand_role(side, ("disruptor",))
        if sup_ct < 2 and sup_ct <= dis_ct:
            role_want = "supporter"
        elif dis_ct < 2:
            role_want = "disruptor"
        else:
            role_want = m.rng.choice(("attacker", "supporter", "disruptor"))
        drawn.append(m._draw_normal_by_role(side, role_want))
        hand.append(drawn[-1])

    m.swap_used[side] = True
    m.last_action_at = _now()
    return {
        "swapped": card_ids_to_swap,
        "drawn": drawn,
        "forced_attackers": force_att,
        "new_hand": m.hand_as_cards(side),
    }


def play_turn(match_id: str, side: str,
              attacker_id: Optional[str], buffer_id: Optional[str],
              debuffer_id: Optional[str], raise_amount: int = 0) -> Dict:
    m = get_match(match_id)
    if m.winner:
        raise MatchError("match ended")
    if side not in ("a", "b"):
        raise MatchError("side must be 'a' or 'b'")
    if m.pending[side] is not None:
        raise MatchError("你本回合已經出牌或 Pass 了, 等對方")

    chosen = [c for c in [attacker_id, buffer_id, debuffer_id] if c]
    if not chosen:
        raise MatchError("至少要放 1 張卡")
    if len(set(chosen)) != len(chosen):
        raise MatchError("同一張卡不能同時放在兩個槽位")
    for cid in chosen:
        if cid not in m.hand[side]:
            raise MatchError(f"卡 {cid} 不在你的手牌")

    if attacker_id:
        att = get_card_by_id(attacker_id)
        if att is None:
            raise MatchError(f"卡 {attacker_id} 不存在")

    # v5: slot ↔ role 校驗
    from cards import can_card_fill_slot
    for slot_name, cid in (("attacker", attacker_id), ("buffer", buffer_id), ("debuffer", debuffer_id)):
        if not cid:
            continue
        c = get_card_by_id(cid)
        if not can_card_fill_slot(c, slot_name):
            raise MatchError(f"{c.get('name_short', cid)} ({c.get('role', '?')}) 不能放 {slot_name} 槽")

    total_mp = 0
    for cid in [buffer_id, debuffer_id]:  # attacker is free; only buf/deb cost MP
        if cid:
            c = get_card_by_id(cid)
            total_mp += c.get("mp_cost", 0)
    if total_mp > m.mp[side]:
        raise MatchError(f"MP 不足 (需 {total_mp}, 你剩 {m.mp[side]})")

    raise_amount = max(0, int(raise_amount))
    if raise_amount > 0:
        r_count = m.raises_a if side == "a" else m.raises_b
        if r_count >= MAX_RAISES_PER_SIDE:
            raise MatchError(f"本場最多追加 {MAX_RAISES_PER_SIDE} 次下注")

    m.pending[side] = {
        "action": "play",
        "attacker": attacker_id,
        "buffer": buffer_id,
        "debuffer": debuffer_id,
        "mp_spent": total_mp,
        "raise_amount": raise_amount,
    }
    m.last_action_at = _now()
    return _try_resolve_round(m)


def pass_turn(match_id: str, side: str) -> Dict:
    m = get_match(match_id)
    if m.winner:
        raise MatchError("match ended")
    if side not in ("a", "b"):
        raise MatchError("side must be 'a' or 'b'")
    if m.pending[side] is not None:
        raise MatchError("你本回合已經出牌或 Pass 了")
    m.pending[side] = {"action": "pass"}
    m.last_action_at = _now()
    return _try_resolve_round(m)


def _apply_buff(buff: Optional[Dict], result: Dict, is_self: bool) -> None:
    """把一個 buff/debuff 效果累加進 result dict"""
    if not buff:
        return
    t = buff["type"]
    v = buff["value"]
    if t == "self_atk_pct":
        result["atk_pct"] += v
    elif t == "self_def_pct":
        result["def_pct"] += v
    elif t == "self_atk_flat":
        result["atk_flat"] += v
    elif t == "self_def_flat":
        result["def_flat"] += v
    elif t == "self_hp_heal":
        result["hp_heal"] += v
    elif t == "self_mp_heal":
        result["mp_heal"] += v
    elif t == "opp_atk_pct":
        result["opp_atk_pct"] += v
    elif t == "opp_def_pct":
        result["opp_def_pct"] += v
    elif t == "opp_atk_flat":
        result["opp_atk_flat"] += v
    elif t == "opp_def_flat":
        result["opp_def_flat"] += v
    elif t == "opp_mp_drain":
        result["opp_mp_drain"] += v
    elif t == "opp_hp_direct":
        result["opp_hp_direct"] += v


def _collect_side_effects(pending: Dict) -> Dict:
    """從一方的 pending 動作收集 buff/debuff 總和"""
    out = {
        "atk_pct": 0, "def_pct": 0, "atk_flat": 0, "def_flat": 0,
        "hp_heal": 0, "mp_heal": 0,
        "opp_atk_pct": 0, "opp_def_pct": 0, "opp_atk_flat": 0, "opp_def_flat": 0,
        "opp_mp_drain": 0, "opp_hp_direct": 0,
    }
    if not pending or pending.get("action") != "play":
        return out
    if pending.get("buffer"):
        c = get_card_by_id(pending["buffer"])
        _apply_buff(c.get("buff_effect"), out, True)
    if pending.get("debuffer"):
        c = get_card_by_id(pending["debuffer"])
        _apply_buff(c.get("debuff_effect"), out, False)
    return out


def _try_resolve_round(m: MatchState) -> Dict:
    if m.pending["a"] is None or m.pending["b"] is None:
        return {
            "resolved": False,
            "waiting_for": [s for s in ("a", "b") if m.pending[s] is None],
        }

    pa = m.pending["a"]; pb = m.pending["b"]
    log: List[str] = [f"=== Round {m.round} ==="]

    m.mp["a"] -= pa.get("mp_spent", 0)
    m.mp["b"] -= pb.get("mp_spent", 0)
    if pa.get("mp_spent"):
        log.append(f"A 花 {pa['mp_spent']} MP")
    if pb.get("mp_spent"):
        log.append(f"B 花 {pb['mp_spent']} MP")

    # 追加下注
    for _side, _p in (("a", pa), ("b", pb)):
        _ra = _p.get("raise_amount", 0) if _p.get("action") == "play" else 0
        if _ra > 0:
            m.bet += _ra
            if _side == "a":
                m.raises_a += 1
            else:
                m.raises_b += 1
            log.append(f"💰 {_side.upper()} 追加下注 +{_ra} Pit → 底池 {m.bet}")

    if pa.get("action") == "pass":
        log.append("A Pass 本回合")
    if pb.get("action") == "pass":
        log.append("B Pass 本回合")

    eff_a = _collect_side_effects(pa)
    eff_b = _collect_side_effects(pb)

    att_a = get_card_by_id(pa["attacker"]) if pa.get("action") == "play" and pa.get("attacker") else None
    att_b = get_card_by_id(pb["attacker"]) if pb.get("action") == "play" and pb.get("attacker") else None

    atk_a_raw = att_a["atk"] if att_a else 0
    def_a_raw = att_a["def"] if att_a else 0
    atk_b_raw = att_b["atk"] if att_b else 0
    def_b_raw = att_b["def"] if att_b else 0

    if pa.get("action") == "pass":
        def_a_raw = PASS_SHIELD_DEF
    if pb.get("action") == "pass":
        def_b_raw = PASS_SHIELD_DEF

    atk_a = atk_a_raw * (1 + eff_a["atk_pct"]/100 - eff_b["opp_atk_pct"]/100) \
            + eff_a["atk_flat"] - eff_b["opp_atk_flat"]
    def_a = def_a_raw * (1 + eff_a["def_pct"]/100 - eff_b["opp_def_pct"]/100) \
            + eff_a["def_flat"] - eff_b["opp_def_flat"]
    atk_b = atk_b_raw * (1 + eff_b["atk_pct"]/100 - eff_a["opp_atk_pct"]/100) \
            + eff_b["atk_flat"] - eff_a["opp_atk_flat"]
    def_b = def_b_raw * (1 + eff_b["def_pct"]/100 - eff_a["opp_def_pct"]/100) \
            + eff_b["def_flat"] - eff_a["opp_def_flat"]

    atk_a = max(0, atk_a); def_a = max(0, def_a)
    atk_b = max(0, atk_b); def_b = max(0, def_b)

    em_ab = element_multiplier(att_a["element"], att_b["element"]) if att_a and att_b else 1.0
    em_ba = element_multiplier(att_b["element"], att_a["element"]) if att_a and att_b else 1.0
    if att_a:
        atk_a = atk_a * em_ab
    if att_b:
        atk_b = atk_b * em_ba

    if att_a:
        log.append(f"A 主力 {att_a['name_short']} ATK{att_a['atk']}/DEF{att_a['def']} {att_a['element_cn']}")
    if att_b:
        log.append(f"B 主力 {att_b['name_short']} ATK{att_b['atk']}/DEF{att_b['def']} {att_b['element_cn']}")
    if pa.get("buffer"):
        bc = get_card_by_id(pa["buffer"])
        log.append(f"A Buffer {bc['name_short']} → {bc['buff_effect']['desc']}")
    if pb.get("buffer"):
        bc = get_card_by_id(pb["buffer"])
        log.append(f"B Buffer {bc['name_short']} → {bc['buff_effect']['desc']}")
    if pa.get("debuffer"):
        dc = get_card_by_id(pa["debuffer"])
        log.append(f"A Debuffer {dc['name_short']} → {dc['debuff_effect']['desc']}")
    if pb.get("debuffer"):
        dc = get_card_by_id(pb["debuffer"])
        log.append(f"B Debuffer {dc['name_short']} → {dc['debuff_effect']['desc']}")

    if em_ab != 1.0 and att_a and att_b:
        log.append(f"屬性: {att_a['element_cn']}→{att_b['element_cn']} ×{em_ab}")
    if em_ba != 1.0 and att_a and att_b:
        log.append(f"屬性: {att_b['element_cn']}→{att_a['element_cn']} ×{em_ba}")

    log.append(f"有效戰力 → A: ATK {int(atk_a)} DEF {int(def_a)} | B: ATK {int(atk_b)} DEF {int(def_b)}")

    # DEF blocks at 50% efficiency so attacks always deal some damage
    raw_dmg_to_b = max(2, atk_a - def_b * 0.5) if att_a else 0
    raw_dmg_to_a = max(2, atk_b - def_a * 0.5) if att_b else 0
    dmg_to_b = int(round(raw_dmg_to_b * HP_DAMAGE_RATIO)) + eff_a["opp_hp_direct"]
    dmg_to_a = int(round(raw_dmg_to_a * HP_DAMAGE_RATIO)) + eff_b["opp_hp_direct"]

    # 道具特效：double_atk（傷害翻倍）/ skip_opp（對方本回合攻擊歸零）
    if m.item_fx["a"]["skip_opp"] > 0:
        dmg_to_a = 0; atk_b = 0
        log.append("🛡️ A 封鎖 · B 本回合攻擊無效")
        m.item_fx["a"]["skip_opp"] -= 1
    if m.item_fx["b"]["skip_opp"] > 0:
        dmg_to_b = 0; atk_a = 0
        log.append("🛡️ B 封鎖 · A 本回合攻擊無效")
        m.item_fx["b"]["skip_opp"] -= 1
    if m.item_fx["a"]["double_atk"] > 0:
        dmg_to_b *= 2
        log.append(f"⚔️ A 雙倍攻擊 · 傷害 ×2 → -{dmg_to_b}")
        m.item_fx["a"]["double_atk"] -= 1
    if m.item_fx["b"]["double_atk"] > 0:
        dmg_to_a *= 2
        log.append(f"⚔️ B 雙倍攻擊 · 傷害 ×2 → -{dmg_to_a}")
        m.item_fx["b"]["double_atk"] -= 1

    # 天氣加成（攻擊方元素 == 當前天氣元素 → bonus damage）
    w = m.weather
    if w["element"] and att_a and att_a["element"] == w["element"]:
        dmg_to_b += w["bonus"]
        log.append(f"{w['icon']} {w['cn']} · A 元素加成 +{w['bonus']}")
    if w["element"] and att_b and att_b["element"] == w["element"]:
        dmg_to_a += w["bonus"]
        log.append(f"{w['icon']} {w['cn']} · B 元素加成 +{w['bonus']}")

    # 傳說卡加成
    if att_a and att_a["level"] >= 50:
        dmg_to_b += LEGENDARY_DMG_BONUS
        log.append(f"★ 傳說卡 · A +{LEGENDARY_DMG_BONUS}")
    if att_b and att_b["level"] >= 50:
        dmg_to_a += LEGENDARY_DMG_BONUS
        log.append(f"★ 傳說卡 · B +{LEGENDARY_DMG_BONUS}")

    m.hp["b"] -= dmg_to_b
    m.hp["a"] -= dmg_to_a
    if dmg_to_b:
        log.append(f"A → B HP -{dmg_to_b}" + (f" (含無視 DEF {eff_a['opp_hp_direct']})" if eff_a['opp_hp_direct'] else ""))
    if dmg_to_a:
        log.append(f"B → A HP -{dmg_to_a}" + (f" (含無視 DEF {eff_b['opp_hp_direct']})" if eff_b['opp_hp_direct'] else ""))

    if eff_a["hp_heal"]:
        m.hp["a"] = min(INITIAL_HP, m.hp["a"] + eff_a["hp_heal"])
        log.append(f"A heals {eff_a['hp_heal']} HP")
    if eff_b["hp_heal"]:
        m.hp["b"] = min(INITIAL_HP, m.hp["b"] + eff_b["hp_heal"])
        log.append(f"B heals {eff_b['hp_heal']} HP")

    if eff_a["mp_heal"]:
        m.mp["a"] = min(MP_CAP, m.mp["a"] + eff_a["mp_heal"])
        log.append(f"A 補 {eff_a['mp_heal']} MP")
    # v5: self_mp_heal / opp_mp_drain 已從卡池移除；Pass MP 獎勵已歸零（MP 不回復）

    for side in ("a", "b"):
        p = m.pending[side]
        if not p or p.get("action") != "play":
            continue
        for cid in [p.get("attacker"), p.get("buffer"), p.get("debuffer")]:
            if cid and cid in m.hand[side]:
                m.hand[side].remove(cid)
                c = get_card_by_id(cid)
                if c and c["level"] >= 50:
                    m.legendary_count[side] = max(0, m.legendary_count[side] - 1)

    m.rounds_log.append({
        "round": m.round,
        "actions": {"a": pa, "b": pb},
        "damage_to_a": dmg_to_a,
        "damage_to_b": dmg_to_b,
        "hp_a": max(0, m.hp["a"]), "hp_b": max(0, m.hp["b"]),
        "mp_a": m.mp["a"], "mp_b": m.mp["b"],
        "log": log,
    })

    if m.hp["a"] <= 0 and m.hp["b"] <= 0:
        m.winner = "DRAW_KO"
    elif m.hp["a"] <= 0:
        m.winner = "B"
    elif m.hp["b"] <= 0:
        m.winner = "A"
    elif m.round >= MAX_ROUNDS:
        if m.hp["a"] > m.hp["b"]:
            m.winner = "A"
        elif m.hp["b"] > m.hp["a"]:
            m.winner = "B"
        else:
            m.winner = "DRAW"

    if m.winner:
        return {
            "resolved": True,
            "round": m.round,
            "log": log,
            "hp_a": max(0, m.hp["a"]), "hp_b": max(0, m.hp["b"]),
            "mp_a": m.mp["a"], "mp_b": m.mp["b"],
            "winner": m.winner,
            "match_over": True,
        }

    m.pending = {"a": None, "b": None}
    m.weather = m._roll_weather()
    m.swap_used = {"a": False, "b": False}
    m.round += 1

    m.mp["a"] = min(MP_CAP, m.mp["a"] + MP_PER_ROUND)
    m.mp["b"] = min(MP_CAP, m.mp["b"] + MP_PER_ROUND)

    for side in ("a", "b"):
        normal_in_hand = sum(1 for cid in m.hand[side]
                             if get_card_by_id(cid) and get_card_by_id(cid)["level"] < 50)
        while normal_in_hand < HAND_SIZE:
            m.hand[side].append(m._draw_normal(side))
            normal_in_hand += 1

    _dealt_a = m._maybe_deal_legendary("a")
    _dealt_b = m._maybe_deal_legendary("b")
    if _dealt_a:
        ca = get_card_by_id(_dealt_a)
        log.append(f"★ 傳說回合: A 抽到 Lv.50 {ca['name_short']}")
    if _dealt_b:
        cb = get_card_by_id(_dealt_b)
        log.append(f"★ 傳說回合: B 抽到 Lv.50 {cb['name_short']}")

    m.last_action_at = _now()

    return {
        "resolved": True,
        "round_just_ended": m.round - 1,
        "current_round": m.round,
        "log": log,
        "hp_a": m.hp["a"], "hp_b": m.hp["b"],
        "mp_a": m.mp["a"], "mp_b": m.mp["b"],
        "winner": None,
        "match_over": False,
    }


def list_active_matches() -> List[Dict]:
    return [
        {"id": m.id, "player_a": m.player_a, "player_b": m.player_b,
         "round": m.round, "winner": m.winner, "created_at": m.created_at}
        for m in MATCHES.values()
    ]


def evaluate_hand_strength(m: MatchState, side: str) -> float:
    """0-100 綜合手牌強度評分 (ATK/傳說/技能/天氣加成)"""
    hand = m.hand_as_cards(side)
    if not hand:
        return 0.0
    top_atk = max((c["atk"] for c in hand), default=0)
    has_legendary = any(c["level"] >= 50 for c in hand)
    best_buff = max((abs((c.get("buff_effect") or {}).get("value", 0)) for c in hand), default=0)
    best_deb = max((abs((c.get("debuff_effect") or {}).get("value", 0)) for c in hand), default=0)
    weather_elem = m.weather.get("element")
    weather_match = weather_elem and any(c.get("element") == weather_elem for c in hand)
    score = (top_atk / 30) * 50
    score += 20 if has_legendary else 0
    score += min(15, (best_buff + best_deb) * 1.5)
    score += 10 if weather_match else 0
    return min(100.0, score)


def ai_pick_move(m: MatchState, side: str) -> Dict:
    """智能 AI: 元素相剋 + 天氣意識 + 隨機擾動 + 手牌強時追加下注"""
    hand = m.hand_as_cards(side)
    mp_budget = m.mp[side]
    opp = "b" if side == "a" else "a"
    opp_hand = m.hand_as_cards(opp)
    weather_elem = m.weather.get("element")
    weather_bonus = m.weather.get("bonus", 0)

    opp_elements = [c.get("element") for c in opp_hand if c]
    most_common_opp = max(set(opp_elements), key=opp_elements.count) if opp_elements else None

    def att_score(c):
        base = c["atk"] + m.rng.uniform(-2, 2)
        if weather_elem and c.get("element") == weather_elem:
            base += weather_bonus * 0.6
        if most_common_opp:
            base *= element_multiplier(c.get("element"), most_common_opp)
        if c["level"] >= 50:
            base += LEGENDARY_DMG_BONUS * 0.6
        return base

    def buf_score(c):
        v = abs((c.get("buff_effect") or {}).get("value", 0))
        return v + m.rng.uniform(-1, 1)

    def deb_score(c):
        v = abs((c.get("debuff_effect") or {}).get("value", 0))
        return v + m.rng.uniform(-1, 1)

    # v5: 依 role 分池
    att_pool = [c for c in hand if c.get("role") in ("attacker", "dual")]
    buf_pool = [c for c in hand if c.get("role") in ("supporter", "dual") and c.get("buff_effect")]
    deb_pool = [c for c in hand if c.get("role") in ("disruptor", "dual") and c.get("debuff_effect")]

    att_scored = sorted([(att_score(c), c) for c in att_pool], reverse=True)
    buf_scored = sorted([(buf_score(c), c) for c in buf_pool], reverse=True)
    deb_scored = sorted([(deb_score(c), c) for c in deb_pool], reverse=True)

    pick = {"attacker": None, "buffer": None, "debuffer": None}
    used_ids: set = set()
    total_cost = 0

    # v6: 主攻不扣 MP，AI 預算只花在 buffer/debuffer
    for sc, c in att_scored:
        if c["id"] not in used_ids:
            pick["attacker"] = c["id"]; used_ids.add(c["id"]); break

    if m.rng.random() > 0.15:
        for sc, c in buf_scored:
            if c["id"] not in used_ids and total_cost + c["mp_cost"] <= mp_budget and sc > 0.3:
                pick["buffer"] = c["id"]; used_ids.add(c["id"]); total_cost += c["mp_cost"]; break

    if m.rng.random() > 0.15:
        for sc, c in deb_scored:
            if c["id"] not in used_ids and total_cost + c["mp_cost"] <= mp_budget and sc > 0.3:
                pick["debuffer"] = c["id"]; used_ids.add(c["id"]); total_cost += c["mp_cost"]; break

    if not any(pick.values()):
        return {"action": "pass"}

    # 手牌強時追加下注
    raise_count = m.raises_a if side == "a" else m.raises_b
    raise_amount = 0
    if raise_count < MAX_RAISES_PER_SIDE:
        strength = evaluate_hand_strength(m, side)
        if strength >= 85:
            raise_amount = 500
        elif strength >= 70:
            raise_amount = 200

    return {"action": "play", "raise_amount": raise_amount, **pick}


# ═══════════════════════════════════════════════════════════════
#  10 AI 策略人格
# ═══════════════════════════════════════════════════════════════

AI_STRATEGY_META = {
    "berserker": {"name": "狂戰士",   "emoji": "⚔️",  "desc": "純 ATK 至上，不顧一切代價"},
    "fortress":  {"name": "鋼鐵堡壘", "emoji": "🛡️",  "desc": "以防禦為核心，等待對手崩潰"},
    "drainer":   {"name": "吸魂者",   "emoji": "🫙",  "desc": "榨乾對方 MP，讓技能無從發動"},
    "weatherman":{"name": "天氣播報", "emoji": "🌪️",  "desc": "完全跟著天氣走，元素加成最大化"},
    "assassin":  {"name": "刺客",     "emoji": "🗡️",  "desc": "落後時爆發 + 大注，領先時保守"},
    "gambler":   {"name": "賭神",     "emoji": "🎲",  "desc": "完全隨機，但追加下注頻率最高"},
    "counter":   {"name": "剋星",     "emoji": "🔄",  "desc": "分析對手元素，精準剋制"},
    "healer":    {"name": "永生者",   "emoji": "💚",  "desc": "瘋狂回血，把戰局拖到最長"},
    "tactician": {"name": "謀略家",   "emoji": "🧠",  "desc": "動態分析全局，平衡最優解"},
    "veteran":   {"name": "老兵不死", "emoji": "🌟",  "desc": "積攢傳說卡機會，關鍵時機全開"},
}

_STRATEGY_DRIFT = {
    "berserker": 0.10, "fortress": 0.15, "drainer": 0.18,
    "weatherman": 0.28, "assassin": 0.14, "gambler": 0.38,
    "counter": 0.20, "healer": 0.20, "tactician": 0.13, "veteran": 0.22,
}


def _pick_base(hand, mp, used=None, cost=0):
    """Helper: pick best ATK card (only attacker/dual role) from hand within MP budget"""
    if used is None:
        used = set()
    for c in sorted(hand, key=lambda c: c["atk"], reverse=True):
        if c.get("role") not in ("attacker", "dual"):
            continue
        if c["id"] not in used and cost + c["mp_cost"] <= mp:
            return c
    return None


def _pick_buffer(hand, mp, used=None, cost=0, key=None):
    """Helper: pick best buffer (supporter/dual) from hand"""
    if used is None:
        used = set()
    pool = [c for c in hand if c.get("role") in ("supporter", "dual") and c.get("buff_effect")]
    if key:
        pool = sorted(pool, key=key, reverse=True)
    else:
        pool = sorted(pool, key=lambda c: abs(c["buff_effect"].get("value", 0)), reverse=True)
    for c in pool:
        if c["id"] not in used and cost + c["mp_cost"] <= mp:
            return c
    return None


def _pick_debuffer(hand, mp, used=None, cost=0, key=None):
    """Helper: pick best debuffer (disruptor/dual) from hand"""
    if used is None:
        used = set()
    pool = [c for c in hand if c.get("role") in ("disruptor", "dual") and c.get("debuff_effect")]
    if key:
        pool = sorted(pool, key=key, reverse=True)
    else:
        pool = sorted(pool, key=lambda c: abs(c["debuff_effect"].get("value", 0)), reverse=True)
    for c in pool:
        if c["id"] not in used and cost + c["mp_cost"] <= mp:
            return c
    return None


def _play(attacker=None, buffer=None, debuffer=None, raise_amount=0):
    if not any([attacker, buffer, debuffer]):
        return {"action": "pass"}
    return {"action": "play", "attacker": attacker, "buffer": buffer,
            "debuffer": debuffer, "raise_amount": raise_amount}


def _strat_berserker(m: MatchState, side: str) -> Dict:
    """純 ATK 至上，不用技能牌"""
    hand = m.hand_as_cards(side)
    mp = m.mp[side]
    c = _pick_base(hand, mp)
    return _play(attacker=c["id"]) if c else {"action": "pass"}


def _strat_fortress(m: MatchState, side: str) -> Dict:
    """最高 DEF 主力 + DEF buff，HP>65 且 MP<20 時 Pass"""
    hand = m.hand_as_cards(side)
    mp = m.mp[side]
    if m.hp[side] > 65 and mp < 20:
        return {"action": "pass"}
    used: set = set(); cost = 0; att_id = buf_id = None
    for c in sorted([c for c in hand if c.get("role") in ("attacker", "dual")],
                    key=lambda c: c["def"], reverse=True):
        if cost + c["mp_cost"] <= mp:
            att_id = c["id"]; used.add(c["id"]); cost += c["mp_cost"]; break
    def_buffs = sorted(
        [c for c in hand if c["id"] not in used
         and c.get("role") in ("supporter", "dual")
         and (c.get("buff_effect") or {}).get("type", "") in ("self_def_pct", "self_def_flat")],
        key=lambda c: abs((c.get("buff_effect") or {}).get("value", 0)), reverse=True)
    for c in def_buffs:
        if cost + c["mp_cost"] <= mp:
            buf_id = c["id"]; break
    return _play(attacker=att_id, buffer=buf_id)


def _strat_drainer(m: MatchState, side: str) -> Dict:
    """優先 opp_mp_drain debuffer，讓對方失去技能"""
    hand = m.hand_as_cards(side)
    mp = m.mp[side]
    used: set = set(); cost = 0; att_id = deb_id = None
    c = _pick_base(hand, mp, used, cost)
    if c:
        att_id = c["id"]; used.add(c["id"]); cost += c["mp_cost"]
    drains = sorted(
        [c for c in hand if c["id"] not in used
         and c.get("role") in ("disruptor", "dual")
         and (c.get("debuff_effect") or {}).get("type") == "opp_mp_drain"],
        key=lambda c: c["debuff_effect"]["value"], reverse=True)
    if not drains:
        drains = sorted(
            [c for c in hand if c["id"] not in used
             and c.get("role") in ("disruptor", "dual")
             and c.get("debuff_effect")],
            key=lambda c: abs((c.get("debuff_effect") or {}).get("value", 0)), reverse=True)
    for c in drains:
        if cost + c["mp_cost"] <= mp:
            deb_id = c["id"]; break
    return _play(attacker=att_id, debuffer=deb_id)


def _strat_weatherman(m: MatchState, side: str) -> Dict:
    """完全跟天氣元素走"""
    hand = m.hand_as_cards(side)
    mp = m.mp[side]
    we = m.weather.get("element")
    wb = m.weather.get("bonus", 0)
    scored = sorted([c for c in hand if c.get("role") in ("attacker", "dual")],
                    key=lambda c: c["atk"] + (wb * 1.5 + 8 if we and c.get("element") == we else 0),
                    reverse=True)
    att_id = None
    for c in scored:
        if c["mp_cost"] <= mp:
            att_id = c["id"]; break
    return _play(attacker=att_id)


def _strat_assassin(m: MatchState, side: str) -> Dict:
    """落後爆發 + 大注，領先保守"""
    opp = "b" if side == "a" else "a"
    hand = m.hand_as_cards(side)
    mp = m.mp[side]
    hp_diff = m.hp[side] - m.hp[opp]
    if hp_diff > 25 and mp < 25:
        return {"action": "pass"}
    used: set = set(); cost = 0; att_id = buf_id = None
    c = _pick_base(hand, mp, used, cost)
    if c:
        att_id = c["id"]; used.add(c["id"]); cost += c["mp_cost"]
    ra = 0
    if hp_diff < -20:
        for c in sorted([x for x in hand if x["id"] not in used
                         and x.get("role") in ("supporter", "dual")
                         and x.get("buff_effect")],
                        key=lambda c: abs((c.get("buff_effect") or {}).get("value", 0)), reverse=True):
            if cost + c["mp_cost"] <= mp:
                buf_id = c["id"]; break
        rc = m.raises_a if side == "a" else m.raises_b
        if rc < MAX_RAISES_PER_SIDE:
            ra = 500
    return _play(attacker=att_id, buffer=buf_id, raise_amount=ra)


def _strat_gambler(m: MatchState, side: str) -> Dict:
    """完全隨機選牌 + 頻繁大注"""
    hand = m.hand_as_cards(side)
    mp = m.mp[side]
    affordable = [c for c in hand if c["mp_cost"] <= mp]
    if not affordable or m.rng.random() < 0.12:
        return {"action": "pass"}
    m.rng.shuffle(affordable)
    _SLOT_ROLES = {
        "attacker": ("attacker", "dual"),
        "buffer":   ("supporter", "dual"),
        "debuffer": ("disruptor", "dual"),
    }
    pick: Dict = {"attacker": None, "buffer": None, "debuffer": None}
    cost = 0
    for c in affordable[:m.rng.randint(1, min(3, len(affordable)))]:
        role = c.get("role") or "attacker"
        # 找第一個 role-compatible 的空槽
        s = next((sl for sl in ("attacker", "buffer", "debuffer")
                  if not pick[sl] and role in _SLOT_ROLES[sl]), None)
        if s and cost + c["mp_cost"] <= mp:
            pick[s] = c["id"]; cost += c["mp_cost"]
    rc = m.raises_a if side == "a" else m.raises_b
    ra = m.rng.choice([0, 100, 200, 500]) if rc < MAX_RAISES_PER_SIDE and m.rng.random() < 0.45 else 0
    if not any(pick.values()):
        return {"action": "pass"}
    return {"action": "play", "raise_amount": ra, **pick}


def _strat_counter(m: MatchState, side: str) -> Dict:
    """分析對手最多元素 → 選剋制主力 + ATK debuff"""
    hand = m.hand_as_cards(side)
    mp = m.mp[side]
    opp = "b" if side == "a" else "a"
    opp_elems = [c.get("element") for c in m.hand_as_cards(opp) if c]
    target = max(set(opp_elems), key=opp_elems.count) if opp_elems else None
    scored = sorted([c for c in hand if c.get("role") in ("attacker", "dual")],
                    key=lambda c: c["atk"] * element_multiplier(c.get("element"), target),
                    reverse=True)
    used: set = set(); cost = 0; att_id = deb_id = None
    for c in scored:
        if cost + c["mp_cost"] <= mp:
            att_id = c["id"]; used.add(c["id"]); cost += c["mp_cost"]; break
    atk_debs = sorted(
        [c for c in hand if c["id"] not in used
         and c.get("role") in ("disruptor", "dual")
         and (c.get("debuff_effect") or {}).get("type", "") in ("opp_atk_pct", "opp_atk_flat")],
        key=lambda c: abs(c["debuff_effect"]["value"]), reverse=True)
    for c in atk_debs:
        if cost + c["mp_cost"] <= mp:
            deb_id = c["id"]; break
    return _play(attacker=att_id, debuffer=deb_id)


def _strat_healer(m: MatchState, side: str) -> Dict:
    """瘋狂回血，持久戰風格"""
    hand = m.hand_as_cards(side)
    mp = m.mp[side]
    hp = m.hp[side]
    if hp > 75 and mp < 12:
        return {"action": "pass"}
    used: set = set(); cost = 0; att_id = buf_id = None
    for c in sorted([c for c in hand if c.get("role") in ("attacker", "dual")],
                    key=lambda c: c["def"], reverse=True):
        if cost + c["mp_cost"] <= mp:
            att_id = c["id"]; used.add(c["id"]); cost += c["mp_cost"]; break
    if hp < 65:
        heals = sorted(
            [c for c in hand if c["id"] not in used
             and c.get("role") in ("supporter", "dual")
             and (c.get("buff_effect") or {}).get("type") == "self_hp_heal"],
            key=lambda c: c["buff_effect"]["value"], reverse=True)
        for c in heals:
            if cost + c["mp_cost"] <= mp:
                buf_id = c["id"]; break
    return _play(attacker=att_id, buffer=buf_id)


def _strat_tactician(m: MatchState, side: str) -> Dict:
    """動態全局評估（等同基礎智能 AI）"""
    return ai_pick_move(m, side)


def _strat_veteran(m: MatchState, side: str) -> Dict:
    """積累傳說卡機會，關鍵時機全開 + 加注"""
    hand = m.hand_as_cards(side)
    mp = m.mp[side]
    hp = m.hp[side]
    is_legendary_now = m.round in m.legendary_rounds[side]
    has_leg = any(c["level"] >= 50 for c in hand)
    used: set = set(); cost = 0; att_id = buf_id = None
    if has_leg and (is_legendary_now or hp < 40):
        legs = sorted([c for c in hand if c["level"] >= 50
                       and c.get("role") in ("attacker", "dual")],
                      key=lambda c: c["atk"], reverse=True)
        for c in legs:
            if cost + c["mp_cost"] <= mp:
                att_id = c["id"]; used.add(c["id"]); cost += c["mp_cost"]; break
        for c in sorted([x for x in hand if x["id"] not in used
                         and x.get("role") in ("supporter", "dual")
                         and x.get("buff_effect")],
                        key=lambda c: abs((c.get("buff_effect") or {}).get("value", 0)), reverse=True):
            if cost + c["mp_cost"] <= mp:
                buf_id = c["id"]; break
        rc = m.raises_a if side == "a" else m.raises_b
        ra = 200 if rc < MAX_RAISES_PER_SIDE else 0
        return _play(attacker=att_id, buffer=buf_id, raise_amount=ra)
    else:
        if mp < 15:
            return {"action": "pass"}
        normals = [c for c in hand if c["level"] < 50]
        c = _pick_base(normals, mp)
        return _play(attacker=c["id"]) if c else {"action": "pass"}


_STRAT_FUNCS = {
    "berserker":  _strat_berserker,
    "fortress":   _strat_fortress,
    "drainer":    _strat_drainer,
    "weatherman": _strat_weatherman,
    "assassin":   _strat_assassin,
    "gambler":    _strat_gambler,
    "counter":    _strat_counter,
    "healer":     _strat_healer,
    "tactician":  _strat_tactician,
    "veteran":    _strat_veteran,
}
STRATEGY_IDS = list(_STRAT_FUNCS.keys())


def ai_pick_move_strategy(m: MatchState, side: str, strategy_id: str) -> Dict:
    """
    以指定策略出牌。
    每回合有 drift_prob 機率隨機採用另一種策略（保持不可預測性）。
    """
    drift = _STRATEGY_DRIFT.get(strategy_id, 0.18)
    if m.rng.random() < drift:
        alt = m.rng.choice([s for s in STRATEGY_IDS if s != strategy_id])
        result = _STRAT_FUNCS[alt](m, side)
        result["_drifted_to"] = alt
    else:
        result = _STRAT_FUNCS[strategy_id](m, side)
    result["_strategy"] = strategy_id
    return result
