"""模擬 30 場 AI vs AI — 看 HP 分布 / 遊戲長度 / 三槽疊加比例"""
import sys, os, random
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "app"))

from battle_engine import (
    start_match, play_turn, pass_turn, ai_pick_move_strategy,
    STRATEGY_IDS, MATCHES, MAX_ROUNDS
)

def run_match(strat_a: str, strat_b: str, seed: int):
    m = start_match("A", "B", bet=100, seed=seed)
    mid = m.id
    turn = 0
    while not m.winner and turn < 50:
        for side, strat in (("a", strat_a), ("b", strat_b)):
            if m.winner: break
            if m.pending[side] is not None: continue
            move = ai_pick_move_strategy(m, side, strat)
            try:
                if move.get("action") == "pass":
                    pass_turn(mid, side)
                else:
                    play_turn(mid, side,
                              move.get("attacker"), move.get("buffer"), move.get("debuffer"),
                              raise_amount=move.get("raise_amount", 0))
            except Exception as e:
                pass_turn(mid, side)
        turn += 1
    return m

random.seed(42)
strats = STRATEGY_IDS
results = []
full_games = 0
three_slot_uses = 0
any_slot_uses = 0
slot_combos = {"main":0, "main+buf":0, "main+deb":0, "triple":0, "buf_only":0, "deb_only":0}

for i in range(30):
    sa, sb = random.sample(strats, 2)
    m = run_match(sa, sb, seed=100 + i)
    dur = m.round
    if dur >= MAX_ROUNDS: full_games += 1
    results.append({
        "strat": f"{sa} vs {sb}",
        "winner": m.winner,
        "rounds": m.round,
        "hp_a": m.hp["a"],
        "hp_b": m.hp["b"],
        "mp_a_final": m.mp["a"],
        "mp_b_final": m.mp["b"],
    })
    # 分析每回合的 slot 使用
    for log in m.rounds_log:
        for side in ("a", "b"):
            act = log["actions"][side]
            if not act or act.get("action") != "play": continue
            has_m = bool(act.get("attacker"))
            has_b = bool(act.get("buffer"))
            has_d = bool(act.get("debuffer"))
            any_slot_uses += 1
            if has_m and has_b and has_d:
                three_slot_uses += 1; slot_combos["triple"] += 1
            elif has_m and has_b: slot_combos["main+buf"] += 1
            elif has_m and has_d: slot_combos["main+deb"] += 1
            elif has_m: slot_combos["main"] += 1
            elif has_b: slot_combos["buf_only"] += 1
            elif has_d: slot_combos["deb_only"] += 1

print("=== 30 場 AI vs AI 模擬 ===")
print(f"打到 20 回合上限: {full_games}/30 ({full_games/30*100:.0f}%)")
avg_rounds = sum(r["rounds"] for r in results) / len(results)
print(f"平均回合數: {avg_rounds:.1f}")
print(f"\n=== Slot 使用分布（共 {any_slot_uses} 次出牌）===")
for k,v in slot_combos.items():
    print(f"  {k:10s}: {v:4d} ({v/any_slot_uses*100:5.1f}%)")
print(f"\n=== 前 10 場結果 ===")
for r in results[:10]:
    print(f"  [{r['rounds']:2d} 回] {r['strat']:30s} → 贏家 {r['winner'] or '?'}  HP A={r['hp_a']:3d} B={r['hp_b']:3d}  殘 MP A={r['mp_a_final']:3d} B={r['mp_b_final']:3d}")

# HP 分布
a_hps = [r["hp_a"] for r in results]
b_hps = [r["hp_b"] for r in results]
print(f"\n=== HP 分布 ===")
print(f"  A 方終局 HP: min={min(a_hps)} avg={sum(a_hps)/len(a_hps):.1f} max={max(a_hps)}")
print(f"  B 方終局 HP: min={min(b_hps)} avg={sum(b_hps)/len(b_hps):.1f} max={max(b_hps)}")
ko_games = sum(1 for r in results if r["hp_a"] == 0 or r["hp_b"] == 0)
print(f"  有人歸零 (KO): {ko_games}/30")
