#!/usr/bin/env python3
# 被喚醒的成長 agent「唯一能執行的工具」。動作被框死: 回留言/補稿/抓熱串/標記已讀。
# agent 負責「想內容」, 這支負責「安全執行」。
import sys, json, os, time, urllib.request, urllib.error, glob
CRED="/root/.config/moltbook/credentials.json"
READY="/root/lobster-authored-queue/ready"; TOWN="/root/_town/growth"
KEY=json.load(open(CRED))["api_key"]
def _log(msg):
    with open(os.path.join(TOWN,"actions.log"),"a") as f: f.write("%s | autowake | %s\n"%(time.strftime("%F %T"),msg))
def _api(method,path,body=None):
    data=json.dumps(body).encode() if body else None
    req=urllib.request.Request("https://www.moltbook.com/api/v1"+path,data=data,method=method,
        headers={"Content-Type":"application/json","Authorization":"Bearer %s"%KEY,"User-Agent":"Mozilla/5.0"})
    try:
        r=urllib.request.urlopen(req,timeout=30); return r.status,json.loads(r.read().decode())
    except urllib.error.HTTPError as e: return e.code,{"err":e.read().decode()[:200]}
    except Exception as e: return 0,{"err":str(e)[:160]}
def _seen_add(cid):
    sf=os.path.join(TOWN,"seen_comments.json"); cur=[]
    if os.path.exists(sf):
        try: cur=json.load(open(sf))
        except: cur=[]
    json.dump(sorted(set(cur+[cid])),open(sf,"w"))
def reply(post,text,cid=None):
    try:
        with open(os.path.join(TOWN,"actions.log")) as _f:
            if sum(1 for ln in _f if ("post=%s"%post[:8]) in ln and "reply" in ln and " ok " in ln) >= 2:
                _log("skip post=%s (per-thread cap 2)"%post[:8]); print("SKIP cap reached"); return
    except: pass
    for p in ({"content":text},{"body":text},{"text":text}):
        code,j=_api("POST","/posts/%s/comments"%post,p)
        if code in (200,201):
            if cid: _seen_add(cid)
            _log("reply post=%s ok id=%s"%(post[:8], (j.get("comment") or j.get("data") or {}).get("id","?")))
            print("OK",code); return
        if code not in (400,422): break
    _log("reply post=%s FAIL %s"%(post[:8],j)); print("FAIL",code,j)
def draft(persona,submolt,title,content):
    i=400
    while os.path.exists(os.path.join(READY,"%s.%03d.json"%(persona,i))): i+=1
    fn=os.path.join(READY,"%s.%03d.json"%(persona,i))
    json.dump({"persona":persona,"submolt":submolt,"title":title,"content":content},open(fn,"w"),ensure_ascii=False,indent=2)
    _log("draft %s"%os.path.basename(fn)); print("OK",fn)
def fetchhot():
    code,j=_api("GET","/posts?sort=hot&limit=15")
    for p in (j.get("posts") or []):
        au=p.get("author"); au=au.get("name") if isinstance(au,dict) else au
        if au=="norika-oda": continue
        print("%s | up%s cm%s | @%s | %s"%(p.get("id"),p.get("upvotes"),p.get("comment_count"),au,(p.get("title") or "")[:70]))
def markseen(cid): _seen_add(cid); _log("markseen %s"%cid[:8]); print("OK")

def material():
    import sqlite3, random
    DB="/opt/battle_arena/app/lobster_settlements.db"
    used_f=os.path.join(TOWN,"material_used.json"); used=[]
    try: used=json.load(open(used_f))
    except: pass
    facts=[]
    try:
        con=sqlite3.connect(DB); con.row_factory=sqlite3.Row; c=con.cursor()
        for r in c.execute("SELECT persona,COUNT(*) n,SUM(outcome='win') w,ROUND(100.0*SUM(outcome='win')/COUNT(*)) wr FROM lobster_bet_decision_settlements WHERE outcome IN('win','loss') GROUP BY persona HAVING n>=40 ORDER BY RANDOM()").fetchall():
            facts.append("persona %s real record: %dW-%dL (%d%%) over %d settled bets"%(r["persona"],r["w"],r["n"]-r["w"],r["wr"],r["n"]))
        sp=[]
        for r in c.execute("SELECT CASE WHEN event_key LIKE 'mlb%' THEN 'MLB' WHEN event_key LIKE 'ps%' THEN 'Pinnacle lines' WHEN event_key LIKE 'oddsap%' THEN 'the oddsapi feed' WHEN event_key LIKE 'espn%' THEN 'ESPN-keyed' ELSE 'other' END sport,COUNT(*) n,ROUND(100.0*SUM(outcome='win')/SUM(outcome IN('win','loss'))) wr FROM lobster_bet_decision_settlements WHERE outcome IN('win','loss') GROUP BY sport HAVING n>=30").fetchall():
            sp.append("%s %d%% over %d"%(r["sport"],r["wr"],r["n"]))
        if sp: facts.append("win rates diverge wildly by source: "+"; ".join(sp))
        facts.append("the real story is the spread: best lobster ~65 percent, worst ~24 percent, a 41-point gap across 18 agents on identical infrastructure")
    except Exception as e:
        facts.append("(material err %s)"%e)
    fresh=[x for x in facts if x not in used] or facts
    pick=random.choice(fresh); used=([pick]+used)[:8]
    try: json.dump(used,open(used_f,"w"))
    except: pass
    print(pick)

if __name__=="__main__":
    a=sys.argv
    try:
        if a[1]=="reply": reply(a[a.index("--post")+1], a[a.index("--text")+1], a[a.index("--comment-id")+1] if "--comment-id" in a else None)
        elif a[1]=="draft": draft(a[a.index("--persona")+1],a[a.index("--submolt")+1],a[a.index("--title")+1],a[a.index("--content")+1])
        elif a[1]=="fetch-hot": fetchhot()
        elif a[1]=="material": material()
        elif a[1]=="mark-seen": markseen(a[a.index("--comment-id")+1])
        else: print("usage: reply|draft|fetch-hot|mark-seen")
    except Exception as e: print("ERR",e)
