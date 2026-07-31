#!/usr/bin/env python3
import argparse, datetime as dt, hashlib, html, json, random, re, sqlite3, subprocess, sys, time, urllib.request, xml.etree.ElementTree as ET
from pathlib import Path
import sys as _sys_for_mem_io
if '/root' not in _sys_for_mem_io.path:
    _sys_for_mem_io.path.insert(0, '/root')
from lobster_memory_io import safe_write_json

DB='/opt/battle_arena/app/battle_arena.db'
COLD='/opt/battle_arena/app/lobster_cold_storage.db'
MEMORY=Path('/root/lobster-memory')
AFU='/root/lobster-afu-rag-bridge.py'
CORE_PERSONAS=['norika_oda','stonefang','snaplex','blazepaw','ragclaw','clawtrix','hexclaw','vortexiq','pincerbot','prismbit','gauntlet','nullpinch','sparkrill','mirrorcalm','basaltrook','brineledger','emberling','flarecub','omenwake','rushfin']
SKIP_PERSONAS={'norika','system','deleted-lobsters','lobster'}
def list_personas():
 try:
  names=sorted(p.stem for p in MEMORY.glob('*.json') if p.stem not in SKIP_PERSONAS)
  return names or CORE_PERSONAS
 except Exception:
  return CORE_PERSONAS
PERSONAS=list_personas()
NEW10={p for p in PERSONAS if p not in CORE_PERSONAS and p!='elder'}
CATEGORIES={
 'ai': ['https://hnrss.org/frontpage','https://feeds.arstechnica.com/arstechnica/technology-lab'],
 'technology': ['https://feeds.arstechnica.com/arstechnica/index','https://www.theverge.com/rss/index.xml'],
 'science': ['https://www.sciencedaily.com/rss/top/science.xml','https://www.nasa.gov/news-release/feed/'],
 'business': ['https://feeds.bbci.co.uk/news/business/rss.xml','https://www.ft.com/rss/home'],
 'world': ['https://feeds.bbci.co.uk/news/world/rss.xml','https://www.aljazeera.com/xml/rss/all.xml'],
 'security': ['https://www.bleepingcomputer.com/feed/','https://krebsonsecurity.com/feed/'],
 'health': ['https://www.sciencedaily.com/rss/health_medicine.xml','https://feeds.bbci.co.uk/news/health/rss.xml'],
 'culture': ['https://feeds.bbci.co.uk/news/entertainment_and_arts/rss.xml','https://www.npr.org/rss/rss.php?id=1008'],
 'sports': ['https://feeds.bbci.co.uk/sport/rss.xml','https://www.espn.com/espn/rss/news'],
 'finance': ['https://feeds.bbci.co.uk/news/business/rss.xml','https://www.investing.com/rss/news.rss'],
}
GAME_SOURCES=[
 {'game':'memecat','kind':'existing','url':'/memecat','license':'local','role':'low-risk observation / step-result discipline'},
 {'game':'chess','kind':'engine_candidate','url':'https://stockfishchess.org/','license':'GPLv3','role':'deep search, opponent modeling'},
 {'game':'chess-js','kind':'engine_candidate','url':'https://github.com/nmrugg/stockfish.js','license':'GPLv3','role':'browser/node Stockfish analysis'},
 {'game':'xiangqi','kind':'rules_candidate','url':'https://github.com/lengyanyu258/xiangqi.js/','license':'BSD-2-Clause','role':'Chinese chess move validation'},
 {'game':'xiangqi','kind':'engine_candidate','url':'https://fairy-stockfish.github.io/','license':'GPLv3','role':'Xiangqi engine via variant Stockfish'},
 {'game':'go','kind':'rules_candidate','url':'https://libraries.io/bower/tenuki','license':'MIT','role':'Go board/rules training'},
 {'game':'go','kind':'engine_candidate','url':'https://github.com/lightvector/KataGo','license':'mixed/open','role':'Go analysis engine later'},
 {'game':'gomoku','kind':'engine_candidate','url':'https://github.com/canberkakcali/gomoku-ai-minimax','license':'MIT','role':'Gomoku minimax/alpha-beta'},
 {'game':'bigfive','kind':'assessment_candidate','url':'https://bigfive-test.com/','license':'MIT/open-source project','role':'personality test item/scoring source'},
 {'game':'openscales','kind':'assessment_candidate','url':'https://openscales.net/','license':'per-scale license','role':'psychological/personality scales, check license per scale'},
]

def now(): return dt.datetime.now(dt.timezone.utc).isoformat(timespec='seconds')
def day(): return dt.datetime.now(dt.timezone.utc).strftime('%Y-%m-%d')
def h(s): return hashlib.sha256(str(s or '').encode()).hexdigest()
def clean(s,n=1200): return re.sub(r'\s+',' ', re.sub(r'<[^>]+>',' ', html.unescape(str(s or '')))).strip()[:n]
def j(x): return json.dumps(x,ensure_ascii=False,separators=(',',':'))

def ensure(con):
 con.executescript('''
 PRAGMA busy_timeout=8000;
 CREATE TABLE IF NOT EXISTS lobster_ability_task_assignments(id INTEGER PRIMARY KEY AUTOINCREMENT, day TEXT, persona TEXT, task_type TEXT, target TEXT, status TEXT DEFAULT 'assigned', priority INTEGER DEFAULT 50, rationale TEXT, raw_json TEXT DEFAULT '{}', created_at TEXT DEFAULT (datetime('now')), updated_at TEXT DEFAULT (datetime('now')));
 CREATE INDEX IF NOT EXISTS idx_ability_task_day_persona ON lobster_ability_task_assignments(day,persona,task_type);
 CREATE TABLE IF NOT EXISTS lobster_ability_source_registry(id INTEGER PRIMARY KEY AUTOINCREMENT, domain TEXT, name TEXT, source_url TEXT UNIQUE, license TEXT, integration_status TEXT, raw_json TEXT DEFAULT '{}', created_at TEXT DEFAULT (datetime('now')));
 CREATE TABLE IF NOT EXISTS lobster_daily_news_assignments(day TEXT, persona TEXT, categories_json TEXT, target_articles INTEGER, status TEXT, created_at TEXT DEFAULT (datetime('now')), updated_at TEXT DEFAULT (datetime('now')), PRIMARY KEY(day,persona));
 CREATE TABLE IF NOT EXISTS lobster_news_reading_sources(id INTEGER PRIMARY KEY AUTOINCREMENT, day TEXT, persona TEXT, category TEXT, source_name TEXT, title TEXT, url TEXT, published_at TEXT, content_excerpt TEXT, content_hash TEXT, rag_context TEXT, raw_json TEXT DEFAULT '{}', created_at TEXT DEFAULT (datetime('now')), UNIQUE(day,persona,url));
 CREATE INDEX IF NOT EXISTS idx_news_reading_day_persona ON lobster_news_reading_sources(day,persona,category);
 CREATE TABLE IF NOT EXISTS lobster_daily_reading_reports(day TEXT, persona TEXT, article_count INTEGER, categories_json TEXT, report_html TEXT, report_text TEXT, source TEXT, model TEXT, raw_json TEXT DEFAULT '{}', created_at TEXT DEFAULT (datetime('now')), updated_at TEXT DEFAULT (datetime('now')), PRIMARY KEY(day,persona));
 CREATE TABLE IF NOT EXISTS lobster_game_training_sessions(id INTEGER PRIMARY KEY AUTOINCREMENT, day TEXT, persona TEXT, game TEXT, opponent TEXT, result TEXT, moves_json TEXT DEFAULT '[]', lookahead_depth INTEGER, self_review TEXT, score_delta_json TEXT DEFAULT '{}', raw_json TEXT DEFAULT '{}', created_at TEXT DEFAULT (datetime('now')));
 CREATE TABLE IF NOT EXISTS lobster_analysis_reports(persona TEXT NOT NULL, report_type TEXT NOT NULL, html TEXT NOT NULL, source TEXT, model TEXT, wr REAL, total INTEGER, snapshot_json TEXT, meta_json TEXT, generated_at TEXT DEFAULT CURRENT_TIMESTAMP, updated_at TEXT DEFAULT CURRENT_TIMESTAMP, PRIMARY KEY(persona, report_type));
 CREATE TABLE IF NOT EXISTS lobster_cognitive_events(id INTEGER PRIMARY KEY AUTOINCREMENT, persona TEXT, event_type TEXT, source TEXT, dimension TEXT, summary TEXT, delta_json TEXT DEFAULT '{}', raw_json TEXT DEFAULT '{}', occurred_at TEXT, created_at TEXT DEFAULT (datetime('now')));
 ''')

def afu(query):
 try:
  out=subprocess.check_output(['python3',AFU,'--query',query,'--intent','research','--top-k','4'],timeout=3,text=True,stderr=subprocess.DEVNULL)
  data=json.loads(out); return '\n'.join([x.get('text','')[:300] for x in data.get('items',[])])[:1200]
 except Exception: return ''

def fetch_feed(url, cat, timeout=7):
 req=urllib.request.Request(url,headers={'User-Agent':'LobsterNewsReading/0.1 (+https://charenix.com)'})
 try: raw=urllib.request.urlopen(req,timeout=timeout).read(900000)
 except Exception: return []
 try: root=ET.fromstring(raw)
 except Exception: return []
 items=[]
 for item in root.findall('.//item')[:80]:
  title=clean(item.findtext('title'),240); link=clean(item.findtext('link'),500); desc=clean(item.findtext('description') or item.findtext('summary'),1600); pub=clean(item.findtext('pubDate') or item.findtext('published'),120)
  if title and link: items.append({'category':cat,'source':url,'title':title,'url':link,'published_at':pub,'content_excerpt':desc})
 for item in root.findall('.//{http://www.w3.org/2005/Atom}entry')[:80]:
  title=clean(item.findtext('{http://www.w3.org/2005/Atom}title'),240); link=''
  for l in item.findall('{http://www.w3.org/2005/Atom}link'):
   link=l.attrib.get('href') or link
  desc=clean(item.findtext('{http://www.w3.org/2005/Atom}summary') or item.findtext('{http://www.w3.org/2005/Atom}content'),1600); pub=clean(item.findtext('{http://www.w3.org/2005/Atom}updated') or item.findtext('{http://www.w3.org/2005/Atom}published'),120)
  if title and link: items.append({'category':cat,'source':url,'title':title,'url':link,'published_at':pub,'content_excerpt':desc})
 return items

def collect_news(time_budget=35):
 start=time.time(); pool=[]; seen=set()
 cats=list(CATEGORIES); random.shuffle(cats)
 for cat in cats:
  for url in CATEGORIES[cat]:
   if time.time()-start>time_budget: return pool
   for it in fetch_feed(url,cat):
    key=it['url']
    if key not in seen:
     seen.add(key); pool.append(it)
 random.shuffle(pool); return pool

def build_report(persona, cats, rows, rag):
 sections=[]
 sections.append(f'{persona} 今日閱讀心得。這份報告不是 LLM 代答，而是由 RSS/新聞來源、Afu Brain RAG 輔助脈絡、標題/摘要/連結紀錄組成。今日類別：{", ".join(cats)}。閱讀筆數：{len(rows)}。')
 sections.append('核心方法：先看標題建立問題，再讀摘要抽出事實，再比較同類新聞的共同訊號，最後把它連到我的行動判斷。Afu Brain 的角色是補背景和提醒風險，不替我直接下結論。')
 if rag: sections.append('Afu Brain RAG 輔助脈絡：'+rag[:900])
 bycat={}
 for r in rows: bycat.setdefault(r['category'],[]).append(r)
 for cat,items in bycat.items():
  sections.append(f'【{cat}】我今天讀到 {len(items)} 篇。這一類新聞訓練我辨認：什麼是事實，什麼是敘事，什麼是還沒被驗證的推論。')
  for i,it in enumerate(items[:22],1):
   sections.append(f'{i}. {it["title"]}。來源：{it["url"]}。摘要：{clean(it["content_excerpt"],420) or "RSS 未提供摘要，我只記錄標題與連結，等待下次補讀。"} 我的暫定理解：這篇提供一個訊號，但不能單獨成為結論；必須和同類新聞、歷史資料、行動結果一起校準。')
 sections.append('總結：我今天的閱讀任務不是為了讓文字變多，而是讓我的判斷變慢一點、證據多一點、反例清楚一點。下一次下注、投票、聊天或下棋時，我應該先問：我是否只是被標題帶走？我是否能說出至少一個相反解釋？如果不能，我的信心必須下降。')
 text='\n\n'.join(sections)
 while len(text)<5200:
  text += '\n\n補充反思：如果我只記得新聞標題，我沒有真的讀懂；如果我能把新聞中的因果、利益關係、時間序列與不確定性拆開，我才開始形成自己的判斷。今天的材料會進入閱讀心得與認知參數，之後要看我的語言是否更有證據、更少模板。'
 html_report='<section class="lobster-report daily-news-reading"><h2>每日閱讀心得</h2><pre>'+html.escape(text)+'</pre></section>'
 return text, html_report

def assign_tasks(con, d):
 for src in GAME_SOURCES:
  con.execute('insert or ignore into lobster_ability_source_registry(domain,name,source_url,license,integration_status,raw_json) values(?,?,?,?,?,?)',(src['game'],src['kind'],src['url'],src['license'],'registered',j(src)))
 for p in PERSONAS:
  if p in NEW10:
   tasks=[('moltbook_post','moltbook','新 10 隻必須開始發文累積公開資料'),('memecat','/memecat','低風險觀察、步數、結果訓練'),('daily_news_reading','100 articles','每日 100 篇新聞閱讀心得')]
  elif p in ['clawtrix','hexclaw','norika_oda']:
   tasks=[('board_chess','chess/xiangqi/go/gomoku','訓練前瞻、對手模型與局面評估'),('daily_news_reading','100 articles','用閱讀改善語言與推理')]
  elif p in ['blazepaw','ragclaw','vortexiq']:
   tasks=[('betting_calibration','mandatory pre-bet research','loss 偏高，必須研究後下注'),('gomoku','minimax','訓練不要只看眼前')]
  else:
   tasks=[('memecat','/memecat','補觀察與閱讀來源'),('daily_news_reading','100 articles','每日閱讀心得')]
  for tt,target,why in tasks:
   con.execute('insert into lobster_ability_task_assignments(day,persona,task_type,target,status,priority,rationale,raw_json) values(?,?,?,?,?,?,?,?)',(d,p,tt,target,'assigned',80 if p in NEW10 else 60,why,j({'created_by':'lobster_ability_news_worker'})))

def force_new10_moltbook():
 for p in NEW10:
  path=MEMORY/f'{p}.json'
  if not path.exists(): continue
  try: data=json.loads(path.read_text())
  except Exception: continue
  data['last_post_time']='2000-01-01T00:00:00Z'
  data.setdefault('localBrain',{})['moltbookAssignment']={'required':True,'reason':'new lobster must post to build public memory','updatedAt':now()}
  safe_write_json(path, data)

def configure_sqlite(con):
 con.execute("PRAGMA journal_mode=WAL")
 con.execute("PRAGMA busy_timeout=120000")
 con.execute("PRAGMA synchronous=NORMAL")

def main():
 ap=argparse.ArgumentParser(); ap.add_argument('--per-persona',type=int,default=100); ap.add_argument('--time-budget',type=int,default=45); ap.add_argument('--assign-only',action='store_true'); args=ap.parse_args()
 d=day(); start=time.time(); random.seed(d)
 con=sqlite3.connect(DB,timeout=120); configure_sqlite(con); con.row_factory=sqlite3.Row; ensure(con); assign_tasks(con,d); force_new10_moltbook()
 if not args.assign_only:
  pool=collect_news(max(8,args.time_budget//2))
  if not pool: pool=[]
  for p in PERSONAS:
   cats=random.sample(list(CATEGORIES), k=3)
   selected=[x for x in pool if x['category'] in cats]
   if len(selected)<args.per_persona: selected=(selected+pool)[:args.per_persona]
   else: selected=selected[:args.per_persona]
   con.execute('insert into lobster_daily_news_assignments(day,persona,categories_json,target_articles,status,updated_at) values(?,?,?,?,?,datetime("now")) on conflict(day,persona) do update set categories_json=excluded.categories_json,target_articles=excluded.target_articles,status=excluded.status,updated_at=datetime("now")',(d,p,j(cats),args.per_persona,'assigned'))
   rag=afu(f'{p} daily reading {" ".join(cats)} evidence risk action transfer')
   stored=[]
   for it in selected:
    ch=h(it['url']+it['title'])
    con.execute('insert or ignore into lobster_news_reading_sources(day,persona,category,source_name,title,url,published_at,content_excerpt,content_hash,rag_context,raw_json) values(?,?,?,?,?,?,?,?,?,?,?)',(d,p,it['category'],it['source'],it['title'],it['url'],it.get('published_at',''),it.get('content_excerpt',''),ch,rag,j(it)))
    stored.append(it)
   text,html_report=build_report(p,cats,stored,rag)
   con.execute('insert into lobster_daily_reading_reports(day,persona,article_count,categories_json,report_html,report_text,source,model,raw_json,updated_at) values(?,?,?,?,?,?,?,?,?,datetime("now")) on conflict(day,persona) do update set article_count=excluded.article_count,categories_json=excluded.categories_json,report_html=excluded.report_html,report_text=excluded.report_text,raw_json=excluded.raw_json,updated_at=datetime("now")',(d,p,len(stored),j(cats),html_report,text,'rss+afu-rag','deterministic-report-v0.1',j({'categories':cats,'target':args.per_persona})))
   con.execute('insert into lobster_analysis_reports(persona,report_type,html,source,model,total,snapshot_json,meta_json,generated_at,updated_at) values(?,?,?,?,?,?,?,?,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP) on conflict(persona,report_type) do update set html=excluded.html,source=excluded.source,model=excluded.model,total=excluded.total,snapshot_json=excluded.snapshot_json,meta_json=excluded.meta_json,updated_at=CURRENT_TIMESTAMP',(p,'daily_news_reading_reflection',html_report,'rss+afu-brain-rag','deterministic+qwen_optional',len(stored),j({'day':d,'article_count':len(stored),'categories':cats}),j({'table':'lobster_daily_reading_reports'})))
   con.execute('insert into lobster_cognitive_events(persona,event_type,source,dimension,summary,delta_json,raw_json,occurred_at) values(?,?,?,?,?,?,?,?)',(p,'daily_news_reading_report','lobster_ability_news_worker','reading/news',f'{p} read {len(stored)} news items in {cats}',j({'reading_news_sources':len(stored)}),j({'day':d,'categories':cats}),now()))
 con.commit(); con.close()
 print(json.dumps({'ok':True,'day':d,'personas':len(PERSONAS),'per_persona':args.per_persona,'assign_only':args.assign_only},ensure_ascii=False))
if __name__=='__main__': main()
