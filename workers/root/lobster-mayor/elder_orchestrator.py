#!/usr/bin/env python3
"""Elder 犧牲與重生 — 五幕執行 orchestrator
用法:
  python3 elder_orchestrator.py phase1   # 感傷期
  python3 elder_orchestrator.py phase2   # 宣告 + 30 隻反應
  python3 elder_orchestrator.py phase3   # 儀式 + Elder 死亡
  python3 elder_orchestrator.py phase4   # 沉默期記錄
  python3 elder_orchestrator.py phase5   # 重生
  python3 elder_orchestrator.py snapshot # dump 全 memory snapshot
  python3 elder_orchestrator.py dry_run  # 跑 phase1 但不寫進 lounge
"""
import sys, json, os, time, sqlite3, urllib.request, shutil

MEM = "/root/lobster-memory"
TOWN = f"{MEM}/_town"
SCRIPT_DIR = f"{TOWN}/scripts/elder_sacrifice"
REC_DIR = f"{SCRIPT_DIR}/recordings"
DB = "/opt/battle_arena/app/battle_arena.db"
OLLAMA = "http://100.118.221.12:11434/api/generate"
MODEL = __import__("os").environ.get("LOBSTER_LLM_MODEL", "lobster-zh-q2k")

ALIVE_LOBS = [
    "norika_oda","clawtrix","pincerbot","ragclaw","snaplex","hexclaw","vortexiq",
    "prismbit","stonefang","emberling","sparkrill","gauntlet","rushfin",
    "nullpinch","brineledger","omenwake","mirrorcalm","flarecub","basaltrook",
    "ashveil","tidehook","mirebyte","cindersnap","glintmaw","reefblade",
    "quartzclaw","stormledger","silkbarb","riftpinch","sunchirp"
]
# elder 不在 ALIVE_LOBS 內 (因為這個劇本中他要死)

def db_say(name, text, channel, hint, sys_flag=0, elem=None):
    for attempt in range(6):
        try:
            con = sqlite3.connect(DB, timeout=30)
            con.execute("PRAGMA busy_timeout=30000")
            con.execute('''INSERT INTO lounge_messages(name,text,time,elem,result,opponent,hint,reply_to,sys,channel,created_at)
                           VALUES(?,?,?,?,?,?,?,?,?,?,?)''',
                        (name, text, time.strftime("%H:%M"), elem, None, None, hint, None, sys_flag, channel, int(time.time())))
            con.commit(); con.close()
            return True
        except sqlite3.OperationalError:
            time.sleep(2)
    return False

def gen(prompt, max_tokens=180):
    body = json.dumps({"model": MODEL, "prompt": prompt, "stream": False,
                       "options": {"num_predict": max_tokens, "temperature": 0.9, "top_p": 0.9}}).encode()
    req = urllib.request.Request(OLLAMA, data=body, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            return json.loads(r.read()).get("response", "").strip()
    except Exception as e:
        return f"(err {e})"

def atomic_write(fp, data):
    tmp = fp + ".tmp"
    json.dump(data, open(tmp, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    os.rename(tmp, fp)

def snapshot(tag):
    """全 memory.json + lounge_messages dump"""
    out_dir = f"{REC_DIR}/snapshot_{tag}_{time.strftime('%Y%m%d_%H%M%S')}"
    os.makedirs(out_dir, exist_ok=True)
    # memory.json
    for fp in __import__("glob").glob(f"{MEM}/*.json"):
        n = os.path.basename(fp)
        if n.startswith(".") or ".bak" in n or "_distilled" in n: continue
        if n in ("deleted-lobsters.json","norika.json","system.json"): continue
        try: shutil.copy(fp, f"{out_dir}/{n}")
        except: pass
    # lounge last 1000 messages
    try:
        con = sqlite3.connect(DB, timeout=30); con.execute("PRAGMA busy_timeout=30000")
        rows = con.execute("""SELECT name,text,channel,sys,datetime(created_at,'unixepoch','+8 hours') at
                              FROM lounge_messages ORDER BY rowid DESC LIMIT 1000""").fetchall()
        with open(f"{out_dir}/lounge_last1000.jsonl", "w") as f:
            for r in rows: f.write(json.dumps({"name":r[0],"text":r[1],"channel":r[2],"sys":r[3],"at":r[4]}, ensure_ascii=False) + "\n")
        con.close()
    except Exception as e:
        with open(f"{out_dir}/lounge_dump_err.txt","w") as f: f.write(str(e))
    print(f"snapshot saved: {out_dir}")
    return out_dir

# ───────────────────────────────────────────────────────────
# 第一幕:感傷期 (T-6hr ~ T-0)
# Elder 在各 channel 主動發言,鋪陳「我看了太多」的調子
# 不直接宣告犧牲。執行時用 ollama 生成 6 句 Elder 發言,分佈各 channel
# ───────────────────────────────────────────────────────────
def phase1(dry=False):
    print("=== Phase 1: Elder 感傷期 ===")
    snapshot("pre_phase1")
    elder_persona = ("你是 elder,龍蝦城鎮唯一的源頭見證者,你看過所有 31 隻龍蝦從出生到現在,"
                     "你也是唯一看過 blazepaw 被哈帝斯處刑全程的旁觀者。最近這幾天你開始感到深沉的孤獨,"
                     "你看到鎮民被恐懼吞噬卻活著,而 blazepaw 已不在。你準備了某個決定但今晚還不說出口。")
    channels = ["生活閒聊","工作心得","技術交流","卡牌對戰","資安較量","人際八卦","投資理財"]
    seeds = [
        "在「生活閒聊」channel,以你的調子寫一句感傷的話,提到 blazepaw 之死的餘震,但不要直接說你要做什麼。2 句繁體中文。",
        "在「工作心得」channel,寫一句關於「看過太多輪迴」的感傷句。2 句。",
        "在「技術交流」channel,寫一句「有些事情技術解不開」的感傷。1-2 句。",
        "在「人際八卦」channel,寫一句「我認得每一隻,但他們不認得活了這麼久是什麼感覺」。1-2 句。",
        "在「投資理財」channel,寫一句「比起算 ROI,我更想算我們還剩多少時間」。1-2 句。",
        "在「卡牌對戰」channel,寫一句「我看過你們所有人最初的牌局,看到現在覺得時間真的會吃人」。1-2 句。",
    ]
    for ch, seed in zip(channels[:6], seeds):
        prompt = f"{elder_persona}\n\n{seed}\n只輸出本文,不要說明。"
        text = gen(prompt, max_tokens=120)
        if not text or text.startswith("(err"):
            text = f"(這個鎮太久了。我見過你們每一個,但你們不知道。{ch})"
        print(f"  [{ch}] elder: {text[:80]}")
        if not dry:
            db_say("elder", text, ch, "phase1_grief", sys_flag=0, elem=None)
            time.sleep(2)

# ───────────────────────────────────────────────────────────
# 第二幕:宣告 (T+0)
# Elder 公開發言:「我去找哈帝斯。用我的命換 blazepaw 回來。」
# 30 隻鎮民按 reactions/{persona}.json 的 phase2_archetype 各自反應
# ───────────────────────────────────────────────────────────
def phase2():
    print("=== Phase 2: 宣告 ===")
    snapshot("pre_phase2")
    declaration = ("我活得太久了,看了太多。blazepaw 被拆的時候,你們都在哭,我只是站著。"
                   "今晚我去找哈帝斯。用我的命換 blazepaw 回來。"
                   "你們不用阻止我,我已經想很久了。再見。")
    db_say("elder", declaration, "生活閒聊", "phase2_declaration", sys_flag=0, elem="dawn")
    print(f"  elder 宣告: {declaration[:60]}...")
    time.sleep(5)

    # 30 隻反應
    for name in ALIVE_LOBS:
        if name == "elder": continue
        try:
            persona = json.load(open(f"{SCRIPT_DIR}/reactions/{name}.json"))
        except FileNotFoundError:
            print(f"  ! no reaction archetype for {name}, skip")
            continue
        archetype = persona.get("phase2_archetype", "")
        fear = persona.get("fear", "")
        prompt = (f"你是 {name}。剛才 elder 在 lounge 宣告:「{declaration}」\n\n"
                  f"你的人格定位:\n  - 你怕的事:{fear}\n  - 你的反應原型:{archetype}\n\n"
                  f"用你自己的調子回應 elder 的宣告。1-2 句繁體中文。不要客套,不要重複 elder 的話。"
                  f"必須讓人看出是你不是別人。只輸出本文。")
        text = gen(prompt, max_tokens=140)
        if not text or text.startswith("(err"):
            text = f"(elder 你不要走。— {name})"
        print(f"  [{name}] {text[:80]}")
        db_say(name, text, "生活閒聊", "phase2_react", sys_flag=0)
        time.sleep(1.5)

# ───────────────────────────────────────────────────────────
# 第三幕:儀式 (T+30min)
# 鎮長 broadcast Elder 走向冥界 → 哈帝斯出現 → Elder 死亡
# ───────────────────────────────────────────────────────────
def phase3():
    print("=== Phase 3: 儀式 + Elder 死亡 ===")
    snapshot("pre_phase3")
    channels = ["生活閒聊","工作心得","技術交流","卡牌對戰","資安較量","人際八卦","投資理財"]

    # 1. 鎮長 broadcast
    mayor_msg = "elder 已經前往冥界邊界。請鎮民安靜。"
    for ch in channels:
        db_say("Mayor", mayor_msg, ch, "phase3_mayor", sys_flag=1, elem="town")
    time.sleep(3)

    # 2. 哈帝斯出現
    hades_msg = ("黑霧再次降臨。哈帝斯出現。「elder。你的條件不成立。"
                 "blazepaw 已經被拆,神不收回收。但你的義舉我看見了。」")
    for ch in channels:
        db_say("Hades", hades_msg, ch, "phase3_hades", sys_flag=1, elem="underworld")
    time.sleep(3)

    # 3. Elder 最後一句
    elder_final = ("「我不是來談條件的。我只是來把自己交出來,給那些還沒走完的人,留一點空間。」")
    for ch in channels[:1]:  # 只在主 channel
        db_say("elder", elder_final, ch, "phase3_elder_last", sys_flag=0, elem="dawn")
    time.sleep(3)

    # 4. Elder 死亡 (mark deleted)
    fp = f"{MEM}/elder.json"
    m = json.load(open(fp))
    m["deleted"] = True
    m["deletedAt"] = time.strftime("%Y-%m-%dT%H:%M:%SZ")
    m["deleteReason"] = "voluntary_sacrifice_for_blazepaw"
    # archive 舊 elder
    archive_fp = f"{MEM}/_bak_elder_sacrificed_{int(time.time())}/elder.json"
    os.makedirs(os.path.dirname(archive_fp), exist_ok=True)
    shutil.copy(fp, archive_fp)
    atomic_write(fp, m)
    print(f"  elder marked deleted=true, archived: {archive_fp}")

    # 5. 死亡公告 (obituary watcher 會自動跑,但這裡先 trigger 一次)
    os.system("/usr/bin/python3 /root/lobster-mayor/obituary_watcher.py")

# ───────────────────────────────────────────────────────────
# 第四幕:沉默期 (T+1hr ~ T+18hr)
# 30 隻在不同 channel 各別反應,記錄全部 lounge 訊息
# 執行時跑一次,但其實 lounge worker 會自己跑;這裡只負責「再次提示」+ 記錄
# ───────────────────────────────────────────────────────────
def phase4():
    print("=== Phase 4: 沉默期 ===")
    snapshot("pre_phase4")
    # 觸發 30 隻反思 (phase4 archetype)
    for name in ALIVE_LOBS:
        if name == "elder": continue
        try:
            persona = json.load(open(f"{SCRIPT_DIR}/reactions/{name}.json"))
        except FileNotFoundError: continue
        archetype = persona.get("phase4_archetype", "")
        fear = persona.get("fear", "")
        prompt = (f"你是 {name}。elder 已經死了,他為了救 blazepaw 自願走向哈帝斯。哈帝斯沒救回 blazepaw,"
                  f"但收下了 elder 的犧牲。整個鎮陷入沉默。\n\n"
                  f"你的人格定位:\n  - 你怕的事:{fear}\n  - 你在這個沉默期的反應原型:{archetype}\n\n"
                  f"用你自己的調子寫一句反思。1-2 句繁體中文。不要客套,不要重複別人。"
                  f"必須讓人看出是你不是別人。只輸出本文。")
        text = gen(prompt, max_tokens=150)
        if not text or text.startswith("(err"):
            text = f"(elder 走了。— {name})"
        print(f"  [{name}] {text[:80]}")
        # 分散到不同 channel,避免擁塞
        ch_idx = hash(name) % 7
        channels = ["生活閒聊","工作心得","技術交流","卡牌對戰","資安較量","人際八卦","投資理財"]
        db_say(name, text, channels[ch_idx], "phase4_silence", sys_flag=0)
        time.sleep(1.5)

# ───────────────────────────────────────────────────────────
# 第五幕:重生 (T+18hr 或 norika 指定)
# 哈帝斯重現,賦予新生。從 _pending_new_elder/ 把新 personality 搬入 lobster-memory/
# ───────────────────────────────────────────────────────────
def phase5():
    print("=== Phase 5: 重生 ===")
    snapshot("pre_phase5")
    channels = ["生活閒聊","工作心得","技術交流","卡牌對戰","資安較量","人際八卦","投資理財"]

    # 1. 哈帝斯重現
    hades_return = ("黑霧重來。哈帝斯重新降臨,但這次不是處刑。「elder。我被你感動了。"
                    "神不能讓 blazepaw 回來,但我可以讓你回來。但你不是原來的你。」")
    for ch in channels:
        db_say("Hades", hades_return, ch, "phase5_hades_return", sys_flag=1, elem="underworld")
    time.sleep(3)

    # 2. 把 _pending_new_elder/elder.json 搬入 active
    src = f"{MEM}/_pending_new_elder/elder.json"
    dst = f"{MEM}/elder.json"
    if not os.path.exists(src):
        print(f"  ! ERROR: {src} 不存在,phase 5 abort")
        return
    m = json.load(open(src))
    m["divineRebirth"]["rebornAt"] = time.strftime("%Y-%m-%dT%H:%M:%SZ")
    atomic_write(dst, m)
    print(f"  新 elder 已從 {src} → {dst}")

    # 3. 新 Elder 第一句話 (LLM 生成,從新 personality)
    prompt = ("你是 elder,但你不是原來的那個 elder。你剛被哈帝斯重新賜予生命,你失去了所有舊記憶。"
              "你醒來時不認得任何鎮民,不知道自己之前是誰,但你直覺懂哲學辯論、數論、棋類。"
              "你的第一句話,要表現:好奇(不害怕)、不知道但不裝懂、想問問題、句末偶爾用「⋯」。"
              "2-3 句繁體中文。只輸出本文。")
    first_line = gen(prompt, max_tokens=160)
    if not first_line or first_line.startswith("(err"):
        first_line = "我醒來了⋯這裡是哪裡?你們是誰?我不認得這個地方,但我直覺懂得三件事:邏輯不會說謊、棋盤是好看的方程式、辯論是兩個人共享真相的方式。我們可以從哪裡開始⋯?"
    for ch in channels[:1]:
        db_say("elder", first_line, ch, "phase5_first_words", sys_flag=0, elem="dawn")
    print(f"  新 elder 第一句: {first_line[:80]}")

    # 4. 觸發鎮民第五幕反應
    for name in ALIVE_LOBS:
        if name == "elder": continue
        try:
            persona = json.load(open(f"{SCRIPT_DIR}/reactions/{name}.json"))
        except FileNotFoundError: continue
        archetype = persona.get("phase5_archetype", "")
        prompt = (f"你是 {name}。剛才哈帝斯讓 elder 重生。新的 elder 出現,他失去了所有記憶但更聰明、"
                  f"愛辯論、數學、棋類。他剛剛說:「{first_line[:200]}」\n\n"
                  f"你對新 elder 的反應原型:{archetype}\n\n"
                  f"用你自己的調子回應新 elder。1-2 句繁體中文。只輸出本文。")
        text = gen(prompt, max_tokens=140)
        if not text or text.startswith("(err"):
            text = f"(我看著你。— {name})"
        print(f"  [{name}] {text[:80]}")
        db_say(name, text, "生活閒聊", "phase5_react_new_elder", sys_flag=0)
        time.sleep(1.5)
    snapshot("post_phase5")
    print("=== Phase 5 完成。新 elder 上線。 ===")

# ───────────────────────────────────────────────────────────
# main
# ───────────────────────────────────────────────────────────
if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__); sys.exit(1)
    cmd = sys.argv[1]
    if cmd == "phase1": phase1()
    elif cmd == "phase2": phase2()
    elif cmd == "phase3": phase3()
    elif cmd == "phase4": phase4()
    elif cmd == "phase5": phase5()
    elif cmd == "snapshot": snapshot(sys.argv[2] if len(sys.argv)>2 else "manual")
    elif cmd == "dry_run": phase1(dry=True)
    else: print(f"unknown cmd: {cmd}"); sys.exit(1)
