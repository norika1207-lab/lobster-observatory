"""Seed 287 matches + player styles into battle_arena.db
   Top 3 elite players are secretly AI bots with human names — nobody knows.
"""
import sqlite3, random, time, os

DB_PATH = os.path.join(os.path.dirname(__file__), "app/battle_arena.db")

# ── Player roster ──
ELITE = ["ZeroKelvin", "ShadowEdge", "NightViper"]          # secretly AI
ABOVE  = ["TylerWrath","Marcus_V","ElenaStorm","KaiserFang",
          "DarkMatter7","Reeve_UK","PhoenixRay"]
AVERAGE= ["Mike_Donovan","Sara_Lee","JackBlaze","OliverBeck",
          "CelesteFox","Ryan_O","NatalyaK","DragonPunch99",
          "BlazeRunner","PsychoWard","CosmicDrift"]
BELOW  = ["xXNoviceXx","RandomHero88","CardFish","LuckyDuck",
          "GrumpyCat77","Tommy_First","BetaGhost","NightOwl_EU",
          "SilverBullet","FrostByte99","LowRoller","QuietStorm",
          "HexBreaker","AceOfSpades","DiceRoller"]
NOOBS  = ["Player_001","NewKid2024","PitLosser","CardNub",
          "xXLoserXx","JustVibing","Learning_Bro","Unlucky_Sam","RandomClicks"]

ALL = ELITE + ABOVE + AVERAGE + BELOW + NOOBS

def target_wr(p):
    if p in ELITE:   return random.uniform(0.70, 0.85)
    if p in ABOVE:   return random.uniform(0.52, 0.64)
    if p in AVERAGE: return random.uniform(0.42, 0.51)
    if p in BELOW:   return random.uniform(0.25, 0.41)
    return random.uniform(0.14, 0.27)

# ── Playstyle per player ──
STYLES = {
    "ZeroKelvin":   ("ALL",     "三者兼備"),
    "ShadowEdge":   ("ATK+BUF", "主攻+Buffer"),
    "NightViper":   ("ATK+DEB", "主攻+Debuffer"),
    "TylerWrath":   ("ATK+BUF", "主攻+Buffer"),
    "Marcus_V":     ("ALL",     "三者兼備"),
    "ElenaStorm":   ("ATK+DEB", "主攻+Debuffer"),
    "KaiserFang":   ("ALL",     "三者兼備"),
    "DarkMatter7":  ("ATK+BUF", "主攻+Buffer"),
    "Reeve_UK":     ("ATK+DEB", "主攻+Debuffer"),
    "PhoenixRay":   ("BUF",     "Buffer專門"),
    "Mike_Donovan": ("ATK",     "純主攻"),
    "Sara_Lee":     ("ATK+BUF", "主攻+Buffer"),
    "JackBlaze":    ("ATK",     "純主攻"),
    "OliverBeck":   ("ATK+DEB", "主攻+Debuffer"),
    "CelesteFox":   ("BUF",     "Buffer專門"),
    "Ryan_O":       ("ATK",     "純主攻"),
    "NatalyaK":     ("ALL",     "三者兼備"),
    "DragonPunch99":("ATK+BUF", "主攻+Buffer"),
    "BlazeRunner":  ("DEB",     "Debuffer專門"),
    "PsychoWard":   ("ATK",     "純主攻"),
    "CosmicDrift":  ("ATK+DEB", "主攻+Debuffer"),
    "xXNoviceXx":   ("ATK",     "純主攻"),
    "RandomHero88": ("ATK",     "純主攻"),
    "CardFish":     ("ATK",     "純主攻"),
    "LuckyDuck":    ("ATK+BUF", "主攻+Buffer"),
    "GrumpyCat77":  ("DEB",     "Debuffer專門"),
    "Tommy_First":  ("ATK",     "純主攻"),
    "BetaGhost":    ("ATK",     "純主攻"),
    "NightOwl_EU":  ("ATK+DEB", "主攻+Debuffer"),
    "SilverBullet": ("ATK+BUF", "主攻+Buffer"),
    "FrostByte99":  ("ATK",     "純主攻"),
    "LowRoller":    ("ATK",     "純主攻"),
    "QuietStorm":   ("BUF",     "Buffer專門"),
    "HexBreaker":   ("DEB",     "Debuffer專門"),
    "AceOfSpades":  ("ATK",     "純主攻"),
    "DiceRoller":   ("ATK",     "純主攻"),
    "Player_001":   ("ATK",     "純主攻"),
    "NewKid2024":   ("ATK",     "純主攻"),
    "PitLosser":    ("ATK",     "純主攻"),
    "CardNub":      ("ATK",     "純主攻"),
    "xXLoserXx":    ("ATK",     "純主攻"),
    "JustVibing":   ("BUF",     "Buffer專門"),
    "Learning_Bro": ("ATK",     "純主攻"),
    "Unlucky_Sam":  ("ATK",     "純主攻"),
    "RandomClicks": ("ATK",     "純主攻"),
}

random.seed(42)
player_wr = {p: target_wr(p) for p in ALL}

now = int(time.time())
timestamps = sorted(random.randint(now - 86400*30, now) for _ in range(287))

matches = []
for ts in timestamps:
    pa, pb = random.sample(ALL, 2)
    skill_diff = (player_wr[pa] - player_wr[pb]) * 0.35
    prob_a = 0.5 + skill_diff + random.uniform(-0.18, 0.18)
    prob_a = max(0.05, min(0.95, prob_a))
    r = random.random()
    winner = "DRAW" if r < 0.04 else ("A" if r < prob_a + 0.04 else "B")
    rounds = random.randint(4, 20)
    bet    = random.choice([200, 500, 500, 1000, 1000, 2000])
    pot    = bet * 2 + random.choice([0, 200, 400, 500])
    mid    = f"seed_{ts}_{pa[:4]}{pb[:4]}"[:24]
    matches.append((mid, pa, pb, winner, rounds, bet, pot, ts))

conn = sqlite3.connect(DB_PATH)
conn.execute("DELETE FROM match_results WHERE id LIKE 'seed_%'")
conn.executemany("INSERT OR REPLACE INTO match_results VALUES (?,?,?,?,?,?,?,?)", matches)

# seed styles
conn.execute("DELETE FROM player_styles")
conn.executemany("INSERT OR REPLACE INTO player_styles VALUES (?,?,?)",
    [(p, s, l) for p, (s, l) in STYLES.items()])

conn.commit()

rows = conn.execute("""
    SELECT player, SUM(is_win) w, SUM(is_loss) l, COUNT(*) t
    FROM (
        SELECT player_a player, CASE WHEN winner='A' THEN 1 ELSE 0 END is_win, CASE WHEN winner='B' THEN 1 ELSE 0 END is_loss
        FROM match_results WHERE player_a NOT IN ('AI','TBD')
        UNION ALL
        SELECT player_b, CASE WHEN winner='B' THEN 1 ELSE 0 END, CASE WHEN winner='A' THEN 1 ELSE 0 END
        FROM match_results WHERE player_b NOT IN ('AI','TBD')
    ) GROUP BY player ORDER BY w*1.0/t DESC LIMIT 20
""").fetchall()
print(f"Seeded {len(matches)} matches, {len(STYLES)} player styles\nTop 20:")
for r in rows:
    style = STYLES.get(r[0], ("?","?"))[1]
    print(f"  {r[0]:20s} {r[1]}W/{r[2]}L {r[1]*100//(r[3] or 1)}%  [{style}]")
conn.close()
print("Done.")
