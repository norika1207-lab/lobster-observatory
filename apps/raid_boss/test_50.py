#!/usr/bin/env python3
"""
AI vs AI 50 場壓力測試
顯示: Buffer/Debuffer 使用統計、傷害貢獻、追加下注紀錄
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "app"))

import battle_engine as be

N = 50
results = {"A": 0, "B": 0, "DRAW": 0, "DRAW_KO": 0}
rounds_list = []
total_buf_a = total_deb_a = 0
total_buf_b = total_deb_b = 0
total_dmg_a = total_dmg_b = 0
total_weather_bonus_a = total_weather_bonus_b = 0
total_legendary_bonus_a = total_legendary_bonus_b = 0
raise_events = []
total_initial_bet = 0
total_final_bet = 0

print(f"{'='*70}")
print(f"  CardHouse AI vs AI — {N} 場壓力測試")
print(f"{'='*70}\n")

for i in range(N):
    m = be.start_match("AI_A", "AI_B", bet=500)
    total_initial_bet += m.bet

    while not m.winner:
        for side in ("a", "b"):
            if m.pending[side] is not None:
                continue
            pick = be.ai_pick_move(m, side)
            if pick["action"] == "pass":
                be.pass_turn(m.id, side)
            else:
                be.play_turn(m.id, side,
                             pick.get("attacker"), pick.get("buffer"), pick.get("debuffer"),
                             pick.get("raise_amount", 0))

    # Collect stats
    winner = m.winner or "DRAW"
    key = winner if winner in results else "DRAW"
    results[key] = results.get(key, 0) + 1
    rounds_list.append(m.round)
    total_final_bet += m.bet

    for rlog in m.rounds_log:
        pa = rlog["actions"]["a"]
        pb = rlog["actions"]["b"]
        total_dmg_a += rlog["damage_to_a"]
        total_dmg_b += rlog["damage_to_b"]
        if pa.get("action") == "play":
            if pa.get("buffer"):   total_buf_a += 1
            if pa.get("debuffer"): total_deb_a += 1
        if pb.get("action") == "play":
            if pb.get("buffer"):   total_buf_b += 1
            if pb.get("debuffer"): total_deb_b += 1
        # Parse weather/legendary bonuses from log lines
        for line in rlog.get("log", []):
            if "A 元素加成" in line:
                n = int(line.split("+")[-1]) if "+" in line else 0
                total_weather_bonus_a += n
            if "B 元素加成" in line:
                n = int(line.split("+")[-1]) if "+" in line else 0
                total_weather_bonus_b += n
            if "傳說卡 · A" in line:
                n = int(line.split("+")[-1]) if "+" in line else 0
                total_legendary_bonus_a += n
            if "傳說卡 · B" in line:
                n = int(line.split("+")[-1]) if "+" in line else 0
                total_legendary_bonus_b += n
            if "追加下注" in line and "+" in line:
                side_tag = "A" if " A " in line else "B"
                try:
                    amt = int(line.split("+")[1].split(" ")[0])
                    raise_events.append((i + 1, side_tag, amt))
                except Exception:
                    pass

    # Per-game summary line
    hp_bar = f"A={m.hp['a']:>4} HP  B={m.hp['b']:>4} HP"
    bet_str = f"底池 {m.bet}"
    ra = f"raises A={m.raises_a} B={m.raises_b}"
    print(f"  [{i+1:>2}] 贏家={winner:<8} R{m.round:<3} {hp_bar}  {bet_str}  {ra}")

print(f"\n{'='*70}")
print(f"  統計摘要 ({N} 場)")
print(f"{'='*70}")

total_rounds = sum(rounds_list)
avg_rounds = total_rounds / N
print(f"\n  勝負分佈:")
print(f"    A 勝: {results['A']:>3} ({results['A']/N*100:.1f}%)")
print(f"    B 勝: {results['B']:>3} ({results['B']/N*100:.1f}%)")
print(f"    平局: {results.get('DRAW', 0) + results.get('DRAW_KO', 0):>3}")
print(f"\n  回合數: 平均 {avg_rounds:.1f}  最短 {min(rounds_list)}  最長 {max(rounds_list)}")

print(f"\n  Buffer/Debuffer 使用次數 (全 {N} 場):")
print(f"    A Buffer: {total_buf_a}  A Debuffer: {total_deb_a}")
print(f"    B Buffer: {total_buf_b}  B Debuffer: {total_deb_b}")
print(f"    每場均使用 Buffer: A={total_buf_a/N:.1f}  B={total_buf_b/N:.1f}")
print(f"    每場均使用 Debuffer: A={total_deb_a/N:.1f}  B={total_deb_b/N:.1f}")

print(f"\n  傷害總量:")
print(f"    對 A 傷害: {total_dmg_a}  (天氣加成 {total_weather_bonus_a}, 傳說加成 {total_legendary_bonus_a})")
print(f"    對 B 傷害: {total_dmg_b}  (天氣加成 {total_weather_bonus_b}, 傳說加成 {total_legendary_bonus_b})")
if total_dmg_a + total_dmg_b > 0:
    weather_pct = (total_weather_bonus_a + total_weather_bonus_b) / (total_dmg_a + total_dmg_b) * 100
    legend_pct = (total_legendary_bonus_a + total_legendary_bonus_b) / (total_dmg_a + total_dmg_b) * 100
    print(f"    天氣加成佔總傷 {weather_pct:.1f}%  傳說加成佔 {legend_pct:.1f}%")

print(f"\n  下注追加:")
print(f"    初始底池: {total_initial_bet}  最終底池: {total_final_bet}")
print(f"    追加事件 {len(raise_events)} 次  平均每場 {len(raise_events)/N:.1f} 次")
if raise_events:
    a_raises = sum(1 for _, s, _ in raise_events if s == "A")
    b_raises = sum(1 for _, s, _ in raise_events if s == "B")
    avg_amt = sum(amt for _, _, amt in raise_events) / len(raise_events)
    print(f"    A 追加 {a_raises} 次  B 追加 {b_raises} 次  均額 {avg_amt:.0f} Pit")
    print(f"\n  追加下注詳情 (前 20 筆):")
    for game, side, amt in raise_events[:20]:
        print(f"    場次 {game:>2}  {side}  +{amt} Pit")

print(f"\n{'='*70}\n")
