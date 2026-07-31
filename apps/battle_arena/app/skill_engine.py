"""
Charenix Skill Engine v1.0
─────────────────────────────────────────────────────────────────────────────
Single source of truth for:
  1. Skill catalogue (all possible buff/debuff effect types + metadata)
  2. Role assignment — based on actual card stats, not random hash
  3. Level scaling — ATK/DEF/skill values scale 1→50 via XP formula
  4. Effect computation — given card + level, produce final buff/debuff values

Skill system rules:
  attacker  — high ATK relative to DEF  (no buff/debuff cost, goes free)
  supporter — high DEF or healing element (buff only)
  disruptor — strong ovr element advantage (debuff only)
  dual      — high ovr, has both meaningful buff & debuff

Role thresholds are computed from the card's stat profile, not a hash.
"""

import sys
sys.path.insert(0, '/opt')
from typing import Dict, Optional, Tuple
from shared_modules.skills_engine.registry import SKILL_CATALOGUE, ELEMENT_SKILL

# Map role → which part of the skill is used
ROLE_SKILL_FILTER = {
    "attacker":  lambda s: (None, None),              # attacker uses neither (free)
    "supporter": lambda s: (s["buff"], None),          # only buff
    "disruptor": lambda s: (None, s["debuff"]),        # only debuff
    "dual":      lambda s: (s["buff"], s["debuff"]),   # both
}


# ═══════════════════════════════════════════════════════════════════════════
# LEVEL SCALING
# Base level from rarity (20/30/40/45/50) → can grow via XP to max 50
# ATK and DEF scale linearly from base_level to 50
# Skill values scale proportionally with level
# ═══════════════════════════════════════════════════════════════════════════

def _level_scale(level: int) -> float:
    """Normalize level 1-50 → 0.0-1.0 scale multiplier."""
    return max(0.1, min(1.0, level / 50))


def atk_at_level(ovr: int, level: int) -> int:
    """ATK grows from ~40% to 100% of max as level 1→50."""
    max_atk = min(30, int(round(ovr * 0.20 + 50 * 0.20)))
    min_atk = max(2, int(round(max_atk * 0.4)))
    scale = _level_scale(level)
    return int(round(min_atk + (max_atk - min_atk) * scale))


def def_at_level(ovr: int, level: int) -> int:
    """DEF grows from ~35% to 100% of max as level 1→50."""
    max_def = min(22, int(round(ovr * 0.14 + 50 * 0.16)))
    min_def = max(1, int(round(max_def * 0.35)))
    scale = _level_scale(level)
    return int(round(min_def + (max_def - min_def) * scale))


def skill_scale(base_val: int, level: int) -> int:
    """Scale a skill value by level. At level 50 = full value, level 1 = 30%."""
    low = max(1, int(round(base_val * 0.30)))
    scale = _level_scale(level)
    return int(round(low + (base_val - low) * scale))


def mp_at_level(base_mp: int, level: int) -> int:
    """MP cost scales up with level (higher level = more powerful = more MP)."""
    if base_mp == 0:
        return 0
    # At level 1: half mp, level 50: full mp
    low = max(0, int(base_mp * 0.5))
    scale = _level_scale(level)
    return int(round(low + (base_mp - low) * scale))


# ═══════════════════════════════════════════════════════════════════════════
# ROLE ASSIGNMENT — stat-based, deterministic
# Logic:
#   1. Compute atk_power = ATK / max_ATK (0-1)
#   2. Compute def_power = DEF / max_DEF (0-1)
#   3. Compute ovr_rank  = OVR / 99
#   4. Assign:
#      - atk_power > 0.65 AND def_power < 0.50 → attacker
#      - def_power > 0.60 AND atk_power < 0.55 → supporter
#      - ovr_rank > 0.72 AND both decent         → dual (legends)
#      - otherwise, element tips the balance     → disruptor (wind/fire) or supporter (water/earth)
# ═══════════════════════════════════════════════════════════════════════════

def assign_role(card: Dict) -> str:
    """
    Assign role from card stats. Called BEFORE nerf/scrub so values are raw.
    Returns: 'attacker' | 'supporter' | 'disruptor' | 'dual'

    Rules (stat-driven, deterministic):
      1. Elite ovr (>=0.86) + both effects → dual (legendary tier)
      2. fire  → attacker (pure offense, disruptor only if elite + heavy debuff)
      3. wind  → attacker (mobility), disruptor if mid-high ovr with debuff
      4. water → supporter (healer/tank)
      5. earth → disruptor if elite debuff, else supporter (def tank)
      ATK/DEF bias acts as tiebreaker: high atk/def ratio stays attacker even if element suggests support.
    """
    ovr      = card.get("ovr", 70)
    level    = card.get("level", 20)
    atk      = atk_at_level(ovr, level)
    defv     = def_at_level(ovr, level)
    element  = card.get("element", "fire")
    ovr_rank = ovr / 99.0

    has_buff   = bool(card.get("buff_effect"))
    has_debuff = bool(card.get("debuff_effect"))

    # atk/def bias: legendary ≈ 1.4, normal ≈ 1.2-1.3. >1.35 = ATK-leaning
    atk_def_bias = atk / max(1, defv)

    # 1. Legendary dual — top tier with both buff+debuff
    if ovr_rank >= 0.86 and has_buff and has_debuff:
        return "dual"

    # 2. Fire — aggressive attacker
    if element == "fire":
        # Elite fire with heavy debuff can be disruptor
        if has_debuff and ovr_rank >= 0.82 and not has_buff:
            return "disruptor"
        return "attacker"

    # 3. Wind — mobile attacker / mid-tier disruptor
    if element == "wind":
        if has_debuff and 0.72 <= ovr_rank < 0.86 and atk_def_bias < 1.40:
            return "disruptor"
        return "attacker"

    # 4. Water — supporter
    if element == "water":
        if has_buff:
            return "supporter"
        # Water without buff: fall back to attacker if ATK-leaning
        return "attacker" if atk_def_bias >= 1.35 else "supporter"

    # 5. Earth — tanky supporter / elite disruptor
    if element == "earth":
        if has_debuff and ovr_rank >= 0.78:
            return "disruptor"
        if has_buff:
            return "supporter"
        return "attacker" if atk_def_bias >= 1.35 else "supporter"

    return "attacker"


# ═══════════════════════════════════════════════════════════════════════════
# EFFECT BUILDER — pick skill for a card + scale by level
# ═══════════════════════════════════════════════════════════════════════════

def _pick_skill_key(card: Dict) -> str:
    """Choose skill key from element + ovr tier."""
    element = card.get("element", "fire")
    ovr     = card.get("ovr", 70)
    base = ELEMENT_SKILL.get(element, "strike")
    # legendary override: highest tier skill
    if ovr >= 88:
        if element in ("fire", "wind"): return "overload"
        if element in ("water",):       return "endurance"
        if element in ("earth",):       return "dominate"
    return base


def build_effects(card: Dict, level: Optional[int] = None) -> Tuple[Optional[Dict], Optional[Dict]]:
    """
    Return (buff_effect, debuff_effect) for a card at a given level.
    If level is None, uses card['level'].
    """
    lv = level if level is not None else card.get("level", 20)
    role = card.get("role") or assign_role(card)
    skill_key = _pick_skill_key(card)
    skill = SKILL_CATALOGUE.get(skill_key, SKILL_CATALOGUE["strike"])

    bt, bv0, bfmt = skill["buff"]
    dt, dv0, dfmt = skill["debuff"]
    bv = skill_scale(bv0, lv)
    dv = skill_scale(dv0, lv)
    buff   = {"type": bt, "value": bv, "desc": bfmt.format(bv)}
    debuff = {"type": dt, "value": dv, "desc": dfmt.format(dv)}

    if role == "attacker":
        return None, None
    elif role == "supporter":
        return buff, None
    elif role == "disruptor":
        return None, debuff
    else:  # dual
        return buff, debuff


# ═══════════════════════════════════════════════════════════════════════════
# APPLY TO CARD — replaces existing balance pipeline in cards.py
# ═══════════════════════════════════════════════════════════════════════════

# Nerf multipliers (same as v5 balance, kept for compatibility)
_NERF: Dict[str, float] = {
    "self_atk_flat": 0.43, "self_atk_pct": 0.48,
    "self_def_flat": 0.50, "self_def_pct": 0.48,
    "self_hp_heal":  0.50,
    "opp_atk_flat":  0.53, "opp_atk_pct":  0.50,
    "opp_def_flat":  0.53, "opp_def_pct":  0.50,
    "opp_hp_direct": 0.50,
}
_DESC_FMT: Dict[str, str] = {
    "self_atk_flat": "+{} ATK",    "self_atk_pct": "+{}% ATK",
    "self_def_flat": "+{} DEF",    "self_def_pct": "+{}% DEF",
    "self_hp_heal":  "Heal {} HP",
    "opp_atk_flat":  "Opp ATK -{}",  "opp_atk_pct":  "Opp ATK -{}%",
    "opp_def_flat":  "Opp DEF -{}",  "opp_def_pct":  "Opp DEF -{}%",
    "opp_hp_direct": "Pierce {} HP",
}
_REMOVED = {"self_mp_heal", "opp_mp_drain"}


def _nerf_effect(eff: Optional[Dict]) -> Optional[Dict]:
    if not eff: return None
    t = eff.get("type")
    if t in _REMOVED or t not in _NERF: return None
    v = max(1, int(round(eff["value"] * _NERF[t])))
    return {"type": t, "value": v, "desc": _DESC_FMT[t].format(v)}


def apply_skill_module(card: Dict) -> Dict:
    """
    Full pipeline:
      1. Assign role by stats
      2. Re-derive buff/debuff from skill catalogue (level-scaled)
      3. Nerf values for game balance
      4. Enforce role constraints (attacker=none, supporter=buff only, etc.)
    Returns mutated card dict.
    """
    # Step 1: role by stats (overrides old hash-based role)
    role = assign_role(card)
    card["role"] = role
    card["skill_key"] = _pick_skill_key(card)

    # Step 2: build clean effects from catalogue
    buff, debuff = build_effects(card, card.get("level", 20))

    # Step 3: nerf
    card["buff_effect"]   = _nerf_effect(buff)
    card["debuff_effect"] = _nerf_effect(debuff)

    # Step 4: role-enforce (if nerf removed something, downgrade role)
    has_b = bool(card.get("buff_effect"))
    has_d = bool(card.get("debuff_effect"))
    if role == "supporter" and not has_b:
        card["role"] = "attacker"
    elif role == "disruptor" and not has_d:
        card["role"] = "attacker"
    elif role == "dual":
        if has_b and not has_d:   card["role"] = "supporter"
        elif has_d and not has_b: card["role"] = "disruptor"
        elif not has_b and not has_d: card["role"] = "attacker"

    return card
