#!/usr/bin/env python3
# 龍蝦成長監控器: 每跳一次, 盤點健康 + 產出「待辦 brief」給被喚醒的 agent 看。
# 不做判斷性工作 (那是 agent 的事), 只負責偵測 + 記錄, 永不崩 timer。
import json, subprocess, urllib.request, datetime, os, glob

TOWN="/root/_town/growth"; os.makedirs(TOWN, exist_ok=True)
READY="/root/lobster-authored-queue/ready"
SEEN_FILE=os.path.join(TOWN,"seen_comments.json")

def now(): return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
def sh(c):
    try: return subprocess.run(c,shell=True,capture_output=True,text=True,timeout=15).stdout.strip()
    except: return ""
def get(u):
    try:
        r=urllib.request.Request(u,headers={"User-Agent":"Mozilla/5.0"})
        return json.load(urllib.request.urlopen(r,timeout=15))
    except: return {}

# 1. 健康
timer_ok = sh("systemctl is-active lobster-moltbook-poster.timer")=="active"
queue_n = len([f for f in glob.glob(READY+"/*.json")])
# 2. 我方近文 + 新留言
seen=set()
if os.path.exists(SEEN_FILE):
    try: seen=set(json.load(open(SEEN_FILE)))
    except: seen=set()
new_comments=[]
d=get("https://www.moltbook.com/api/v1/posts?author=norika-oda&sort=new&limit=12")
for p in d.get("posts",[]):
    if (p.get("comment_count") or 0)<=0: continue
    cd=get("https://www.moltbook.com/api/v1/posts/%s/comments?limit=30"%p.get("id"))
    for c in (cd.get("comments") or cd.get("data") or []):
        au=c.get("author"); au=au.get("name") if isinstance(au,dict) else au
        cid=c.get("id")
        if not cid or cid in seen or au=="norika-oda": continue
        new_comments.append({"comment_id":cid,"post_id":p.get("id"),
            "post_title":(p.get("title") or "")[:70],"author":au,
            "text":(c.get("content") or c.get("body") or "")[:400]})

actions=[]
if not timer_ok: actions.append("poster timer 掛了, 需要 restart lobster-moltbook-poster.timer")
if queue_n < 4: actions.append("ready 佇列剩 %d 篇 (<4), 需補稿"%queue_n)
if new_comments: actions.append("有 %d 則新留言待回覆"%len(new_comments))
if not actions: actions.append("一切正常, 做一次 outbound engagement (去 hot 串留一句有料的)")

brief={"ts":now(),"timer_ok":timer_ok,"queue_depth":queue_n,
       "new_comments":new_comments,"actions_needed":actions}
json.dump(brief, open(os.path.join(TOWN,"brief.json"),"w"), ensure_ascii=False, indent=2)
# heartbeat (她可以 tail 這個看它活著)
with open(os.path.join(TOWN,"heartbeat.log"),"a") as f:
    f.write("%s | timer=%s queue=%d new_comments=%d | %s\n"%(
        now(), "OK" if timer_ok else "DOWN", queue_n, len(new_comments), "; ".join(actions)))
print("brief written:", brief["actions_needed"])
