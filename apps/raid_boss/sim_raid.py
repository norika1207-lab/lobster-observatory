"""Raid Boss 勝率模擬"""
import sys, statistics
sys.path.insert(0, 'app')
from raid_engine import create_raid

def ai_pick(raid, name):
    """模擬聰明玩家：最強主攻 + 最佳 Buffer (ATK 加成優先) + 最佳 Debuffer (降 DEF 優先)"""
    hand = raid.hand_as_cards(name)
    used = set()

    # 主攻：ATK 最高
    attackers = [c for c in hand if c.get('role') in ('attacker','dual') and c['id'] != 'CHAIN_SKILL']
    att = max(attackers, key=lambda c: c.get('atk', 0)) if attackers else None
    if att: used.add(att['id'])

    # Buffer：優先選有 self_atk_pct/flat buff 的
    buffers = [c for c in hand if c.get('role') in ('supporter','dual')
               and c['id'] != 'CHAIN_SKILL' and c['id'] not in used]
    def buf_score(c):
        be = c.get('buff_effect') or {}
        t = be.get('type','')
        v = be.get('value', 0)
        return v if 'atk' in t else (v * 0.5 if t else 0)
    buf = max(buffers, key=buf_score) if buffers else None
    if buf: used.add(buf['id'])

    # Debuffer：優先選降 Boss DEF 的
    debuffers = [c for c in hand if c.get('role') in ('disruptor','dual')
                 and c['id'] != 'CHAIN_SKILL' and c['id'] not in used]
    def deb_score(c):
        de = c.get('debuff_effect') or {}
        t = de.get('type','')
        v = de.get('value', 0)
        return v if 'def' in t else (v * 0.3 if t else 0)
    deb = max(debuffers, key=deb_score) if debuffers else None

    return {
        'action': 'play',
        'attacker': att['id'] if att else None,
        'buffer':   buf['id'] if buf else None,
        'debuffer': deb['id'] if deb else None,
    }

results = {'raiders': 0, 'boss': 0}
rounds_list = []
spirit_count = 0
timeouts = 0

for i in range(200):
    names = ['p1', 'p2', 'p3']
    r = create_raid(names, seed=i)
    safety = 0
    while not r.winner and safety < 200:
        safety += 1
        for name in names:
            if not r.alive[name]: continue
            if r.pending[name] is not None: continue
            r.submit_action(name, ai_pick(r, name))
    if not r.winner:
        timeouts += 1
        continue
    results[r.winner] += 1
    rounds_list.append(r.round)
    if r.team_fighting_spirit != 0:
        spirit_count += 1

total = results['raiders'] + results['boss']
avg_r = sum(rounds_list) / len(rounds_list) if rounds_list else 0

print(f'=== 200 場 3v1 Raid 模擬 ===')
print(f'玩家勝  : {results["raiders"]:3d} ({results["raiders"]/total*100:.1f}%)')
print(f'Boss 勝 : {results["boss"]:3d} ({results["boss"]/total*100:.1f}%)')
print(f'平均回合數  : {avg_r:.1f}')
print(f'鬥氣觸發場次: {spirit_count} ({spirit_count/total*100:.1f}%)')
if rounds_list:
    print(f'回合數分布  : min={min(rounds_list)}  median={statistics.median(rounds_list):.0f}  max={max(rounds_list)}')
if timeouts:
    print(f'⚠️ 超時場次: {timeouts}')
