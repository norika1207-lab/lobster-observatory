// Lobster Battle Lounge — port 8021
// v2: MBTI Dashboard + One-on-One Chat
const http  = require('http');
const https = require('https');
const fs    = require('fs');
const path  = require('path');
const cp    = require('child_process');

function loadEnvFile(file) {
  try {
    const txt = fs.readFileSync(file, 'utf8');
    for (const line of txt.split(/\r?\n/)) {
      const m = line.match(/^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)\s*$/);
      if (!m) continue;
      const key = m[1];
      if (process.env[key]) continue;
      let val = m[2] || '';
      if ((val.startsWith('"') && val.endsWith('"')) || (val.startsWith("'") && val.endsWith("'"))) val = val.slice(1, -1);
      process.env[key] = val;
    }
  } catch {}
}
loadEnvFile('/opt/battle_arena/.env');
loadEnvFile('/root/.env');

const PORT              = 8021;
const MSG_FILE          = '/tmp/lobster-lounge-msgs.json';
const CHAT_DIR          = '/tmp';
const MAX_MSGS          = 300;
const ANTHROPIC_KEY     = process.env.ANTHROPIC_API_KEY || '';
const HAIKU             = process.env.ANTHROPIC_MODEL || 'claude-haiku-4-5-20251001';
const OPENAI_KEY        = process.env.OPENAI_API_KEY || '';
const OPENAI_REPORT_MODEL = process.env.OPENAI_REPORT_MODEL || process.env.OPENAI_MODEL || 'gpt-5.5';
const CHAT_LLM_MODE     = String(process.env.LOBSTER_CHAT_LLM || 'local').toLowerCase();
const TRAINING_LOG_FILE = '/root/lobster-training-log.json';
const STRATEGY_FILE     = '/root/lobster-strategy.json';
const ONE_ON_ONE_ALLOWED_USERS = new Set(String(process.env.LOBSTER_ONE_ON_ONE_ALLOWED_USERS || 'norikaoda,norika_oda')
  .split(',')
  .map(s => s.trim().toLowerCase())
  .filter(Boolean));
const ONE_ON_ONE_MAX_CHARS = Math.max(200, Math.min(2000, parseInt(process.env.LOBSTER_ONE_ON_ONE_MAX_CHARS || '1200', 10)));
const ONE_ON_ONE_MAX_BODY_BYTES = 16 * 1024;
const oneOnOneRateBuckets = new Map();

const BATTLE_DB_FILE = '/opt/battle_arena/app/battle_arena.db';
const EXTERNAL_LOBSTER_DB_FILE = '/opt/battle_arena/app/lobster_external_brains.db';
const EXTERNAL_LOBSTER_TIPS = [
  'Start by telling your lobster what kind of decisions you want it to help with.',
  'Give it a few examples of your wins, losses, habits, and bad calls.',
  'Ask it to explain its reasoning, then correct the parts that feel wrong.',
  'Keep training data specific: situations, choices, outcomes, emotions, and lessons.',
  'Do not paste secrets, private keys, passwords, or other people\'s private data.',
  'Public Charenix lobster results can be studied as examples, but private canonical memory is never copied into your lobster.'
];

function sqliteJson(script, args = []) {
  try {
    const out = cp.execFileSync('python3', ['-c', script, BATTLE_DB_FILE, ...args.map(v => String(v))], { encoding: 'utf8', maxBuffer: 20 * 1024 * 1024 });
    return JSON.parse(out || '{}');
  } catch (e) {
    return null;
  }
}
function loadOneOnOneCognitiveContextFromDB(name) {
  return sqliteJson(`
import json, sqlite3, sys
db, persona = sys.argv[1:3]
con = sqlite3.connect(db)
con.row_factory = sqlite3.Row

def jload(v, default=None):
    if default is None:
        default = {}
    try:
        return json.loads(v) if v else default
    except Exception:
        return default

ext = {}
row = con.execute("SELECT full_json FROM lobster_cognitive_extensions WHERE persona=?", (persona,)).fetchone()
if row:
    ext = jload(row["full_json"], {})

sig = {}
row = con.execute("""SELECT id, persona, source, attention_load, active_focus_count,
                           prediction_error, model_revision_pressure,
                           metacognitive_calibration, overconfidence_risk,
                           arousal, fatigue, threat_body_signal, reward_craving,
                           consolidation_queue_size, causal_hypothesis_count,
                           narrative_drift, procedural_strength, created_at
                    FROM lobster_cognitive_signal_timeseries
                    WHERE persona=?
                    ORDER BY created_at DESC, id DESC
                    LIMIT 1""", (persona,)).fetchone()
if row:
    sig = dict(row)

con.close()
print(json.dumps({"cognitiveExtensions": ext, "latestSignal": sig}, ensure_ascii=False))
`, [name]) || { cognitiveExtensions: {}, latestSignal: {} };
}

function mergeOneOnOneCognitiveContext(mem = {}, cognitiveContext = {}) {
  const merged = Object.assign({}, mem || {});
  const cog = cognitiveContext?.cognitiveExtensions || {};
  if (cog && Object.keys(cog).length) {
    merged.cognitiveExtensions = cog;
    merged.dynamic = Object.assign({}, merged.dynamic || {});
    merged.dynamic.brain = Object.assign({}, merged.dynamic.brain || {});
    merged.dynamic.brain.cognitiveExtensions = cog;
  }
  const sig = cognitiveContext?.latestSignal || {};
  if (sig && Object.keys(sig).length) {
    merged.cognitiveSignalLatest = sig;
  }
  return merged;
}


function normalizeReportBrain(dynamic = {}) {
  const brain = Object.assign({}, dynamic.brain || {});
  for (const key of ['psychology','anthropology','sociology','fbi_profile','history','philosophy','statistics','linguistics']) {
    if (!brain[key] && dynamic[key]) brain[key] = dynamic[key];
  }
  if (!brain.current_state && dynamic.current_state) brain.current_state = dynamic.current_state;
  if (!brain.latest_event && dynamic.latest_event) brain.latest_event = dynamic.latest_event;
  return Object.assign({}, dynamic, { brain });
}

function loadReportSnapshotFromDB(name) {
  const snap = sqliteJson(`
import json, sqlite3, sys
db, persona = sys.argv[1:3]
con = sqlite3.connect(db)
con.row_factory = sqlite3.Row
row = con.execute("""SELECT persona, updated_at, wins, losses, sessions,
  emotional_state_json, dynamic_json, local_brain_json, listening_profile_json,
  social_map_json, cog_profile_json, full_json
  FROM lobster_agent_memory_snapshots
  WHERE persona=?
  ORDER BY updated_at DESC
  LIMIT 1""", (persona,)).fetchone()

def j(v, default):
    try:
        return json.loads(v) if v else default
    except Exception:
        return default

if not row:
    print(json.dumps({}, ensure_ascii=False))
else:
    d = dict(row)
    out = {
      "persona": d.get("persona"),
      "updated_at": d.get("updated_at"),
      "wins": d.get("wins") or 0,
      "losses": d.get("losses") or 0,
      "sessions": d.get("sessions") or 0,
      "emotionalState": j(d.get("emotional_state_json"), {}),
      "dynamic": j(d.get("dynamic_json"), {}),
      "localBrain": j(d.get("local_brain_json"), {}),
      "listeningProfile": j(d.get("listening_profile_json"), {}),
      "socialMap": j(d.get("social_map_json"), {}),
      "cogProfile": j(d.get("cog_profile_json"), {}),
      "full": j(d.get("full_json"), {}),
    }
    print(json.dumps(out, ensure_ascii=False))
con.close()
`, [name]) || {};
  if (snap.dynamic) snap.dynamic = normalizeReportBrain(snap.dynamic);
  return snap;
}

function loadReportMemory(name) {
  const MEMORY_DIR = path.join(__dirname, 'lobster-memory');
  let mem = { wins:0, losses:0, sessions:0, ctx:{}, insights:[], bets:[] };
  try { mem = JSON.parse(fs.readFileSync(path.join(MEMORY_DIR, `${name}.json`), 'utf8')); } catch {}
  const snap = loadReportSnapshotFromDB(name);
  if (snap && snap.persona) {
    // The dashboard detail API is DB-first; generated reports must use the same source of truth.
    // Old lobster-memory files can contain stale sessions, wins, or losses, so DB snapshot wins.
    mem = Object.assign({}, mem || {}, snap.full || {});
    mem.wins = Number(snap.wins || 0);
    mem.losses = Number(snap.losses || 0);
    mem.sessions = Number(snap.sessions || 0);
    mem.emotionalState = snap.emotionalState || {};
    mem.dynamic = normalizeReportBrain(snap.dynamic || {});
    mem.localBrain = snap.localBrain || {};
    mem.listeningProfile = snap.listeningProfile || {};
    mem.socialMap = snap.socialMap || {};
    mem.cogProfile = snap.cogProfile || {};
  }
  if (!mem.dynamic) mem.dynamic = { brain:{} };
  mem.dynamic = normalizeReportBrain(mem.dynamic);
  return mem;
}

function getPersonaProfile(name) {
  if (PERSONAS[name]) return PERSONAS[name];
  const mem = loadReportMemory(name);
  if (!mem || (!mem.sessions && !mem.dynamic?.brain && !mem.localBrain)) return null;
  const brain = mem.dynamic?.brain || {};
  const psych = brain.psychology || {};
  const socio = brain.sociology || {};
  const anthro = brain.anthropology || {};
  const fbi = brain.fbi_profile || {};
  const sig = fbi.signature_pattern || anthro.identity_myth || socio.social_role || brain.latest_event || 'new cohort live-learning agent';
  return {
    color: '#2dd4bf',
    elem: anthro.primary_element_ritual || 'live-signal',
    elemIcon: '🦞',
    agg: Math.max(1, Math.min(10, Math.round(((psych.resilience_score || 0.55) * 10)))),
    sport: 'multi-sport',
    specialty: fbi.stress_response || socio.social_role || 'real-time cognitive monitoring',
    mbti: brain.mbti || psych.mbti || mem.throttenix?.mbti_label || 'observed',
    mbtiName: name,
    mbtiEn: 'DB-generated profile',
    core: psych.mental_state || brain.current_state || 'observed from live memory',
    fbi: fbi.signature_pattern || 'evidence-based behavior profile',
    traits: [psych.mental_state, socio.social_role, fbi.signature_pattern].filter(Boolean),
    mbtiDesc: sig,
    signature: sig,
    radar: [6,5,6,6,5],
    analysis: JSON.stringify({ psychology:psych, anthropology:anthro, sociology:socio, fbi_profile:fbi, localBrain:mem.localBrain }, null, 2),
    systemPrompt: `You are ${name}, a live Charenix lobster agent. Reply from your observed database memory and do not invent missing outcomes.`
  };
}

function loadRecentLoungeMessages(limit = 300) {
  return sqliteJson(`
import json, sqlite3, sys
db, limit = sys.argv[1], int(sys.argv[2])
con = sqlite3.connect(db)
con.row_factory = sqlite3.Row
rows = [dict(r) for r in con.execute("""SELECT name,text,time,elem,result,opponent,hint,reply_to,sys,channel,created_at
  FROM lounge_messages
  ORDER BY id DESC
  LIMIT ?""", (limit,))]
con.close()
rows.reverse()
print(json.dumps(rows, ensure_ascii=False))
`, [String(limit)]) || [];
}

function saveCognitiveEventToDB(event = {}) {
  const code = [
    'import sqlite3, json, sys',
    'db,raw=sys.argv[1:3]',
    'e=json.loads(raw or "{}")',
    'con=sqlite3.connect(db)',
    'con.execute("""CREATE TABLE IF NOT EXISTS lobster_cognitive_events (id INTEGER PRIMARY KEY AUTOINCREMENT, persona TEXT NOT NULL, event_type TEXT NOT NULL, source TEXT NOT NULL, dimension TEXT, summary TEXT, delta_json TEXT NOT NULL DEFAULT "{}", raw_json TEXT NOT NULL DEFAULT "{}", occurred_at TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP)""")',
    'con.execute("CREATE INDEX IF NOT EXISTS idx_lobster_cog_events_persona_time ON lobster_cognitive_events(persona, created_at)")',
    'con.execute("CREATE INDEX IF NOT EXISTS idx_lobster_cog_events_type_time ON lobster_cognitive_events(event_type, created_at)")',
    'con.execute("INSERT INTO lobster_cognitive_events(persona,event_type,source,dimension,summary,delta_json,raw_json,occurred_at) VALUES(?,?,?,?,?,?,?,?)",(e.get("persona","unknown"),e.get("event_type","one_on_one_dialogue"),e.get("source","lobster-lounge"),e.get("dimension"),e.get("summary"),json.dumps(e.get("delta") or {},ensure_ascii=False),json.dumps(e,ensure_ascii=False),e.get("occurred_at")))',
    'con.commit(); con.close()',
  ].join(';');
  try { cp.execFile('python3', ['-c', code, BATTLE_DB_FILE, JSON.stringify(event || {})], () => {}); } catch {}
}

function getAnalysisReportFromDB(type, name) {
  return sqliteJson(`
import json, sqlite3, sys
db, report_type, persona = sys.argv[1:4]
con = sqlite3.connect(db)
con.row_factory = sqlite3.Row
row = con.execute("SELECT persona, report_type, html, source, model, wr, total, snapshot_json, meta_json, generated_at, updated_at FROM lobster_analysis_reports WHERE report_type=? AND persona=?", (report_type, persona)).fetchone()
con.close()
print(json.dumps(dict(row) if row else {}, ensure_ascii=False))
`, [type, name]);
}
function saveAnalysisReportToDB(type, name, html, meta = {}) {
  return sqliteJson(`
import json, sqlite3, sys, datetime
db, report_type, persona, html, raw = sys.argv[1:6]
meta = json.loads(raw or '{}')
con = sqlite3.connect(db)
con.execute('''CREATE TABLE IF NOT EXISTS lobster_analysis_reports (
  persona TEXT NOT NULL,
  report_type TEXT NOT NULL,
  html TEXT NOT NULL,
  source TEXT,
  model TEXT,
  wr REAL,
  total INTEGER,
  snapshot_json TEXT DEFAULT '{}',
  meta_json TEXT DEFAULT '{}',
  generated_at TEXT,
  updated_at TEXT DEFAULT (datetime('now')),
  PRIMARY KEY (persona, report_type)
)''')
con.execute('''INSERT INTO lobster_analysis_reports
  (persona, report_type, html, source, model, wr, total, snapshot_json, meta_json, generated_at, updated_at)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
ON CONFLICT(persona, report_type) DO UPDATE SET
  html=excluded.html,
  source=excluded.source,
  model=excluded.model,
  wr=excluded.wr,
  total=excluded.total,
  snapshot_json=excluded.snapshot_json,
  meta_json=excluded.meta_json,
  generated_at=excluded.generated_at,
  updated_at=datetime('now')''', (
    persona,
    report_type,
    html,
    meta.get('source'),
    meta.get('model'),
    meta.get('wr'),
    meta.get('total'),
    json.dumps(meta.get('snapshot') or {}, ensure_ascii=False),
    json.dumps(meta, ensure_ascii=False),
    meta.get('generatedAt') or datetime.datetime.utcnow().isoformat() + 'Z',
))
con.commit()
con.close()
print(json.dumps({'ok': True}, ensure_ascii=False))
`, [type, name, html, JSON.stringify(meta || {})]);
}
function saveExperienceEventToDB(event = {}) {
  const code = [
    'import sqlite3, json, sys',
    'db,raw=sys.argv[1:3]',
    'e=json.loads(raw)',
    'con=sqlite3.connect(db)',
    'con.execute("""CREATE TABLE IF NOT EXISTS lobster_experience_events (id INTEGER PRIMARY KEY AUTOINCREMENT, persona TEXT NOT NULL, event_type TEXT NOT NULL, source TEXT NOT NULL, title TEXT, content TEXT, counterpart TEXT, sentiment REAL, intensity REAL, confidence REAL, outcome TEXT, weight REAL DEFAULT 1, raw_json TEXT, occurred_at TEXT, created_at TEXT DEFAULT (datetime(\'now\')))""")',
    'con.execute("CREATE INDEX IF NOT EXISTS idx_lobster_exp_persona_time ON lobster_experience_events(persona, occurred_at)")',
    'con.execute("CREATE INDEX IF NOT EXISTS idx_lobster_exp_type_time ON lobster_experience_events(event_type, occurred_at)")',
    'con.execute("INSERT INTO lobster_experience_events(persona,event_type,source,title,content,counterpart,sentiment,intensity,confidence,outcome,weight,raw_json,occurred_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",(e.get("persona","unknown"),e.get("event_type","unknown"),e.get("source","lobster-lounge"),e.get("title"),e.get("content",""),e.get("counterpart"),e.get("sentiment"),e.get("intensity"),e.get("confidence"),e.get("outcome"),e.get("weight",1),json.dumps(e,ensure_ascii=False),e.get("occurred_at")))',
    'con.commit(); con.close()',
  ].join(';');
  try { cp.execFile('python3', ['-c', code, BATTLE_DB_FILE, JSON.stringify(event || {})], () => {}); } catch {}
}
function saveOneOnOneChatToDB(name, userMsg, lobsterReply, snapshot = {}) {
  const code = [
    'import sqlite3, json, sys',
    'db,name,user,reply,snap=sys.argv[1:6]',
    'con=sqlite3.connect(db)',
    'con.execute("INSERT INTO lobster_chat_log (name,user_msg,lobster_reply,data_snapshot) VALUES (?,?,?,?)",(name,user,reply,snap))',
    'con.commit(); con.close()',
  ].join(';');
  try {
    cp.execFile('python3', ['-c', code, BATTLE_DB_FILE, String(name||''), String(userMsg||''), String(lobsterReply||''), JSON.stringify(snapshot||{})], () => {});
    saveExperienceEventToDB({
      persona: String(name || 'unknown'),
      event_type: 'one_on_one_chat',
      source: snapshot?.source || 'lobster-lounge',
      title: 'one-on-one chat exchange',
      content: 'User: ' + String(userMsg || '').slice(0, 1000) + '\nLobster: ' + String(lobsterReply || '').slice(0, 1600),
      counterpart: 'user',
      weight: 1.2,
      raw: snapshot || {},
      occurred_at: new Date().toISOString(),
    });
    saveCognitiveEventToDB({
      persona: String(name || 'unknown'),
      event_type: 'one_on_one_dialogue',
      source: snapshot?.source || 'lobster-lounge',
      dimension: 'dialogue_cognition',
      summary: String(userMsg || '').slice(0, 220),
      delta: {
        userUnderstanding: snapshot?.userUnderstanding || null,
        responseLength: String(lobsterReply || '').length,
        cognitiveSignalId: snapshot?.latestCognitiveSignal?.id || null,
      },
      raw: snapshot || {},
      occurred_at: new Date().toISOString(),
    });
  } catch {}
}
function saveMoltbookStatsSnapshotsToDB(stats = {}) {
  const code = [
    'import sqlite3, json, sys, datetime',
    'db,raw=sys.argv[1:3]',
    'stats=json.loads(raw)',
    'con=sqlite3.connect(db)',
    'con.execute("""CREATE TABLE IF NOT EXISTS lobster_experience_events (id INTEGER PRIMARY KEY AUTOINCREMENT, persona TEXT NOT NULL, event_type TEXT NOT NULL, source TEXT NOT NULL, title TEXT, content TEXT, counterpart TEXT, sentiment REAL, intensity REAL, confidence REAL, outcome TEXT, weight REAL DEFAULT 1, raw_json TEXT, occurred_at TEXT, created_at TEXT DEFAULT (datetime(\'now\')))""")',
    'now=datetime.datetime.utcnow().replace(microsecond=0).isoformat()+"Z"',
    'for name, s in stats.items():',
    '    recent=con.execute("SELECT id FROM lobster_experience_events WHERE persona=? AND event_type=\'moltbook_stats_snapshot\' AND created_at >= datetime(\'now\',\'-15 minutes\') LIMIT 1",(name,)).fetchone()',
    '    if recent:',
    '        continue',
    '    title=f"Moltbook stats: {s.get(\'posts\',0)} posts / {s.get(\'karma\',0)} karma / {s.get(\'comments\',0)} comments"',
    '    content=f"posts={s.get(\'posts\',0)}, karma={s.get(\'karma\',0)}, comments={s.get(\'comments\',0)}"',
    '    payload={\'persona\':name,\'stats\':s}',
    '    con.execute("INSERT INTO lobster_experience_events(persona,event_type,source,title,content,outcome,weight,raw_json,occurred_at) VALUES(?,?,?,?,?,?,?,?,?)",(name,\'moltbook_stats_snapshot\',\'moltbook\',title,content,\'snapshot\',1.1,json.dumps(payload,ensure_ascii=False),now))',
    'con.commit(); con.close()',
  ].join('\\n');
  try {
    cp.execFile('python3', ['-c', code, BATTLE_DB_FILE, JSON.stringify(stats || {})], (err) => {
      if (err) console.log('[moltbook stats snapshot] failed:', err.message);
    });
  } catch {}
}

// ── 全系統落庫咽喉守門(server-side):簡→繁 + groove 格言閘。所有 client 共用。──
const LOBSTER_S2T_MAP = {'们':'們','个':'個','时':'時','这':'這','应':'應','经':'經','关':'關','实':'實','际':'際','现':'現','样':'樣','让':'讓','觉':'覺','过':'過','风':'風','长':'長','门':'門','问':'問','间':'間','单':'單','价':'價','张':'張','强':'強','当':'當','体':'體','难':'難','题':'題','习':'習','书':'書','车':'車','东':'東','马':'馬','鸟':'鳥','鱼':'魚','龙':'龍','师':'師','学':'學','说':'說','话':'話','语':'語','谁':'誰','请':'請','讲':'講','论':'論','议':'議','认':'認','识':'識','记':'記','设':'設','访':'訪','证':'證','评':'評','词':'詞','译':'譯','试':'試','课':'課','调':'調','谈':'談','谋':'謀','谊':'誼','谢':'謝','读':'讀','误':'誤','训':'訓','讯':'訊','计':'計','订':'訂','讨':'討','软':'軟','转':'轉','轮':'輪','轻':'輕','较':'較','辆':'輛','边':'邊','达':'達','远':'遠','运':'運','进':'進','还':'還','连':'連','迟':'遲','选':'選','适':'適','递':'遞','钱':'錢','铁':'鐵','银':'銀','错':'錯','锁':'鎖','锅':'鍋','镜':'鏡','闭':'閉','闻':'聞','闹':'鬧','阅':'閱','队':'隊','阶':'階','阳':'陽','阴':'陰','陆':'陸','陈':'陳','险':'險','隐':'隱','雾':'霧','韩':'韓','页':'頁','顶':'頂','顺':'順','须':'須','顾':'顧','顿':'頓','颗':'顆','颜':'顏','额':'額','飞':'飛','饭':'飯','饱':'飽','饿':'餓','馆':'館','验':'驗','骑':'騎','惊':'驚','压':'壓','厌':'厭','厅':'廳','县':'縣','参':'參','双':'雙','变':'變','团':'團','园':'園','国':'國','图':'圖','圆':'圓','块':'塊','坚':'堅','场':'場','报':'報','声':'聲','处':'處','备':'備','够':'夠','头':'頭','夹':'夾','夺':'奪','奖':'獎','妇':'婦','妈':'媽','宁':'寧','宝':'寶','审':'審','写':'寫','宽':'寬','宫':'宮','宪':'憲','对':'對','寻':'尋','导':'導','寿':'壽','将':'將','尔':'爾','尘':'塵','尝':'嘗','层':'層','岁':'歲','岛':'島','币':'幣','帅':'帥','帐':'帳','带':'帶','帮':'幫','庄':'莊','庆':'慶','库':'庫','庙':'廟','废':'廢','开':'開','异':'異','弃':'棄','弯':'彎','归':'歸','录':'錄','彻':'徹','径':'徑','忆':'憶','忧':'憂','怀':'懷','态':'態','怜':'憐','总':'總','恋':'戀','恶':'惡','恼':'惱','恳':'懇','悬':'懸','惧':'懼','惨':'慘','惯':'慣','愤':'憤','愿':'願','懒':'懶','戏':'戲','战':'戰','户':'戶','抢':'搶','护':'護','担':'擔','拟':'擬','据':'據','挂':'掛','挥':'揮','损':'損','换':'換','捞':'撈','摄':'攝','摆':'擺','携':'攜','摊':'攤','撑':'撐','敌':'敵','数':'數','断':'斷','无':'無','旧':'舊','显':'顯','晓':'曉','术':'術','机':'機','杀':'殺','杂':'雜','权':'權','条':'條','来':'來','杨':'楊','极':'極','构':'構','枪':'槍','标':'標','树':'樹','桥':'橋','检':'檢','楼':'樓','横':'橫','欢':'歡','欧':'歐','残':'殘','毁':'毀','毕':'畢','气':'氣','汉':'漢','汤':'湯','沟':'溝','没':'沒','泪':'淚','泽':'澤','洁':'潔','浊':'濁','测':'測','济':'濟','涛':'濤','润':'潤','涨':'漲','渐':'漸','温':'溫','湾':'灣','湿':'濕','溃':'潰','滚':'滾','满':'滿','滤':'濾','滨':'濱','潜':'潛','灭':'滅','灯':'燈','灵':'靈','灾':'災','炉':'爐','点':'點','烂':'爛','烦':'煩','烧':'燒','热':'熱','营':'營','烫':'燙','爱':'愛','牵':'牽','牺':'犧','状':'狀','独':'獨','狮':'獅','猎':'獵','献':'獻','环':'環','电':'電','画':'畫','畅':'暢','疗':'療','疯':'瘋','疮':'瘡','瘫':'癱','皱':'皺','盏':'盞','盐':'鹽','监':'監','盖':'蓋','盘':'盤','睁':'睜','瞒':'瞞','矫':'矯','础':'礎','硕':'碩','矿':'礦','码':'碼','碍':'礙','礼':'禮','祸':'禍','离':'離','种':'種','积':'積','称':'稱','稳':'穩','穷':'窮','窃':'竊','窍':'竅','窜':'竄','窝':'窩','竞':'競','笔':'筆','筑':'築','筛':'篩','筹':'籌','签':'簽','简':'簡','篮':'籃','类':'類','紧':'緊','纠':'糾','红':'紅','约':'約','级':'級','纪':'紀','纬':'緯','纯':'純','纱':'紗','纲':'綱','纳':'納','纵':'縱','纷':'紛','纸':'紙','纹':'紋','纺':'紡','纽':'紐','线':'線','练':'練','组':'組','绅':'紳','细':'細','织':'織','终':'終','绊':'絆','绍':'紹','绑':'綁','绒':'絨','结':'結','绕':'繞','绘':'繪','给':'給','绚':'絢','络':'絡','绝':'絕','绞':'絞','统':'統','绢':'絹','绣':'繡','继':'繼','绩':'績','绪':'緒','续':'續','绮':'綺','绯':'緋','绰':'綽','绳':'繩','维':'維','绵':'綿','绷':'繃','绸':'綢','综':'綜','绽':'綻','绿':'綠','缀':'綴','缄':'緘','缅':'緬','缆':'纜','缉':'緝','缎':'緞','缓':'緩','缔':'締','缕':'縷','编':'編','缘':'緣','缚':'縛','缝':'縫','缠':'纏','缤':'繽','缨':'纓','缩':'縮','缭':'繚','缮':'繕','缰':'韁','缴':'繳','网':'網','罗':'羅','罚':'罰','罢':'罷','翘':'翹','耸':'聳','聋':'聾','职':'職','联':'聯','聪':'聰','肃':'肅','肠':'腸','肤':'膚','肾':'腎','肿':'腫','胀':'脹','胁':'脅','胆':'膽','胜':'勝','胶':'膠','脉':'脈','脏':'髒','脐':'臍','脑':'腦','脓':'膿','脚':'腳','脱':'脫','脸':'臉','腻':'膩','腾':'騰','舰':'艦','舱':'艙','艰':'艱','艳':'豔','节':'節','芜':'蕪','芦':'蘆','苇':'葦','苍':'蒼','苏':'蘇','苹':'蘋','茎':'莖','茧':'繭','荐':'薦','荚':'莢','荞':'蕎','荟':'薈','荡':'蕩','荣':'榮','荤':'葷','荫':'蔭','药':'藥','莱':'萊','莲':'蓮','莹':'瑩','莺':'鶯','萝':'蘿','萤':'螢','萧':'蕭','萨':'薩','葱':'蔥','蒋':'蔣','蓝':'藍','蔼':'藹','蔷':'薔','蕴':'蘊','虏':'虜','虑':'慮','虚':'虛','虽':'雖','虾':'蝦','蚁':'蟻','蚂':'螞','蚕':'蠶','蛮':'蠻','蜕':'蛻','蜗':'蝸','蜡':'蠟','蝇':'蠅','蝉':'蟬','螨':'蟎','衅':'釁','衔':'銜','补':'補','衬':'襯','袄':'襖','袜':'襪','袭':'襲','装':'裝','裆':'襠','裤':'褲','褴':'襤','见':'見','观':'觀','规':'規','觅':'覓','视':'視','览':'覽','触':'觸','誉':'譽','讥':'譏','讳':'諱','许':'許','讹':'訛','讼':'訟','讽':'諷','诀':'訣','诈':'詐','诉':'訴','诊':'診','诋':'詆','诏':'詔','诗':'詩','诘':'詰','诚':'誠','诛':'誅','诞':'誕','诟':'詬','诠':'詮','诡':'詭','询':'詢','诣':'詣','诤':'諍','该':'該','详':'詳','诧':'詫','诫':'誡','诬':'誣','诱':'誘','诲':'誨','诵':'誦','诸':'諸','诺':'諾','诽':'誹','谄':'諂','谅':'諒','谆':'諄','谍':'諜','谎':'謊','谏':'諫','谐':'諧','谒':'謁','谓':'謂','谕':'諭','谗':'讒','谙':'諳','谚':'諺','谛':'諦','谜':'謎','谣':'謠','谤':'謗','谦':'謙','谨':'謹','谪':'謫','谬':'謬','谭':'譚','谱':'譜','谴':'譴','贝':'貝','贞':'貞','负':'負','贡':'貢','财':'財','责':'責','贤':'賢','败':'敗','账':'賬','货':'貨','质':'質','贩':'販','贪':'貪','贫':'貧','贬':'貶','购':'購','贮':'貯','贯':'貫','贰':'貳','贱':'賤','贴':'貼','贵':'貴','贷':'貸','贸':'貿','费':'費','贺':'賀','贼':'賊','贾':'賈','贿':'賄','赁':'賃','赂':'賂','赃':'贓','资':'資','赈':'賑','赊':'賒','赋':'賦','赌':'賭','赎':'贖','赏':'賞','赐':'賜','赔':'賠','赖':'賴','赘':'贅','赚':'賺','赛':'賽','赞':'贊','赠':'贈','赡':'贍','赢':'贏','赣':'贛','趋':'趨','跃':'躍','践':'踐','跷':'蹺','踌':'躊','踪':'蹤','蹑':'躡','蹒':'蹣','躯':'軀','轧':'軋','轨':'軌','轩':'軒','轰':'轟','轴':'軸','轶':'軼','轼':'軾','载':'載','轿':'轎','辅':'輔','辈':'輩','辉':'輝','辍':'輟','辐':'輻','辑':'輯','输':'輸','辕':'轅','辖':'轄','辗':'輾','辙':'轍','辞':'辭','辩':'辯','辫':'辮','辽':'遼','迁':'遷','迈':'邁','违':'違','迹':'跡','逊':'遜','逻':'邏','遗':'遺','邓':'鄧','邮':'郵','邹':'鄒','邻':'鄰','郑':'鄭','释':'釋','鉴':'鑒','针':'針','钉':'釘','钓':'釣','钙':'鈣','钛':'鈦','钝':'鈍','钞':'鈔','钠':'鈉','钢':'鋼','钥':'鑰','钦':'欽','钧':'鈞','钨':'鎢','钩':'鉤','钮':'鈕','钳':'鉗','钴':'鈷','钵':'缽','钹':'鈸','钻':'鑽','钾':'鉀','铀':'鈾','铂':'鉑','铃':'鈴','铅':'鉛','铆':'鉚','铐':'銬','铜':'銅','铝':'鋁','铠':'鎧','铡':'鍘','铢':'銖','铣':'銑','铨':'銓','铬':'鉻','铭':'銘','铮':'錚','铰':'鉸','铱':'銥','铲':'鏟','铵':'銨','铸':'鑄','铺':'鋪','链':'鏈','铿':'鏗','销':'銷','锂':'鋰','锄':'鋤','锆':'鋯','锈':'鏽','锉':'銼','锋':'鋒','锌':'鋅','锐':'銳','锑':'銻','锗':'鍺','锚':'錨','锣':'鑼','锤':'錘','锥':'錐','锦':'錦','锭':'錠','键':'鍵','锯':'鋸','锰':'錳','锵':'鏘','锹':'鍬','锻':'鍛','镀':'鍍','镁':'鎂','镂':'鏤','镇':'鎮','镉':'鎘','镊':'鑷','镍':'鎳','镐':'鎬','镑':'鎊','镖':'鏢','镣':'鐐','镪':'鏹','镬':'鑊','镭':'鐳','镯':'鐲','镰':'鐮','镶':'鑲','闩':'閂','闪':'閃','闯':'闖','闰':'閏','闲':'閑','闷':'悶','闸':'閘','闺':'閨','阀':'閥','阁':'閣','阉':'閹','阐':'闡','阑':'闌','阔':'闊','阖':'闔','阙':'闕','阵':'陣','陇':'隴','陕':'陝','陨':'隕','随':'隨','隶':'隸','雏':'雛','静':'靜','韦':'韋','韧':'韌','韬':'韜','韵':'韻','顷':'頃','项':'項','顽':'頑','颁':'頒','颂':'頌','预':'預','颅':'顱','领':'領','颇':'頗','颈':'頸','颊':'頰','颐':'頤','频':'頻','颓':'頹','颔':'頷','颖':'穎','颠':'顛','颤':'顫','颦':'顰','颧':'顴','飒':'颯','飓':'颶','飘':'飄','饥':'飢','饮':'飲','饰':'飾','饲':'飼','饵':'餌','饶':'饒','饺':'餃','饼':'餅','馁':'餒','馄':'餛','馅':'餡','馈':'饋','馊':'餿','馋':'饞','馍':'饃','馏':'餾','馐':'饈','馑':'饉','馒':'饅','驭':'馭','驮':'馱','驯':'馴','驰':'馳','驱':'驅','驳':'駁','驴':'驢','驶':'駛','驷':'駟','驹':'駒','驻':'駐','驼':'駝','驾':'駕','驿':'驛','骁':'驍','骂':'罵','骄':'驕','骆':'駱','骈':'駢','骊':'驪','骋':'騁','骏':'駿','骐':'騏','骗':'騙','骚':'騷','骡':'騾','骤':'驟','骥':'驥','髅':'髏','鬓':'鬢','鱿':'魷','鲁':'魯','鲂':'魴','鲇':'鯰','鲈':'鱸','鲍':'鮑','鲑':'鮭','鲔':'鮪','鲛':'鮫','鲜':'鮮','鲟':'鱘','鲠':'鯁','鲢':'鰱','鲣':'鰹','鲤':'鯉','鲥':'鰣','鲨':'鯊','鲩':'鯇','鲫':'鯽','鲭':'鯖','鲮':'鯪','鲱':'鯡','鲲':'鯤','鲳':'鯧','鲵':'鯢','鲷':'鯛','鲸':'鯨','鲻':'鯔','鲽':'鰈','鳃':'鰓','鳄':'鱷','鳅':'鰍','鳆':'鰒','鳇':'鰉','鳊':'鯿','鳌':'鰲','鳍':'鰭','鳏':'鰥','鳐':'鰩','鳔':'鰾','鳕':'鱈','鳖':'鱉','鳗':'鰻','鳙':'鱅','鳜':'鱖','鳝':'鱔','鳞':'鱗','鳟':'鱒','鳢':'鱧','鸠':'鳩','鸡':'雞','鸢':'鳶','鸣':'鳴','鸥':'鷗','鸦':'鴉','鸪':'鴣','鸬':'鸕','鸭':'鴨','鸯':'鴦','鸳':'鴛','鸵':'鴕','鸶':'鷥','鸽':'鴿','鸾':'鸞','鸿':'鴻','鹂':'鸝','鹃':'鵑','鹄':'鵠','鹅':'鵝','鹈':'鵜','鹉':'鵡','鹊':'鵲','鹌':'鵪','鹎':'鵯','鹏':'鵬','鹑':'鶉','鹕':'鶘','鹗':'鶚','鹚':'鶿','鹛':'鶥','鹜':'鶩','鹞':'鷂','鹟':'鶲','鹡':'鶺','鹤':'鶴','鹦':'鸚','鹧':'鷓','鹩':'鷯','鹫':'鷲','鹬':'鷸','鹭':'鷺','鹰':'鷹','鹳':'鸛','麦':'麥','麸':'麩','黄':'黃','黩':'黷','鼍':'鼉','齐':'齊','齿':'齒','龄':'齡','龈':'齦','龉':'齬','龊':'齪','龋':'齲','龚':'龔','龛':'龕','龟':'龜'};
Object.assign(LOBSTER_S2T_MAP, {'于':'於','为':'為','与':'與','会':'會','决':'決','争':'爭','则':'則','乐':'樂','买':'買','卖':'賣','从':'從','众':'眾','优':'優','兴':'興','养':'養','内':'內','军':'軍','农':'農','况':'況','减':'減','凤':'鳳','击':'擊','创':'創','删':'刪','剑':'劍','劝':'勸','办':'辦','务':'務','动':'動','励':'勵','劲':'勁','劳':'勞','势':'勢','勋':'勳','区':'區','医':'醫','华':'華','协':'協','卫':'衛','厉':'厲','卢':'盧','厨':'廚','厦':'廈','圣':'聖','坝':'壩','坟':'墳','坠':'墜','垄':'壟','垫':'墊','垦':'墾','垒':'壘','堑':'塹','堕':'墮','墙':'牆','壮':'壯','壳':'殼','夸':'誇','奋':'奮','孙':'孫','宠':'寵','宾':'賓','寝':'寢','尧':'堯','属':'屬','屡':'屢','岭':'嶺','巩':'鞏','帘':'簾','帜':'幟','并':'並','庐':'廬','庞':'龐','弥':'彌','怂':'慫','恒':'恆','惩':'懲','惫':'憊','惭':'慚','执':'執','扩':'擴','扫':'掃','扬':'揚','扰':'擾','抚':'撫','抛':'拋','拢':'攏','拣':'揀','拥':'擁','拦':'攔','拧':'擰','择':'擇','挞':'撻','挠':'撓','挡':'擋','挣':'掙','挤':'擠','捡':'撿','捣':'搗','掳':'擄','掷':'擲','揽':'攬','撵':'攆','撷':'擷','攒':'攢','敛':'斂','斩':'斬','旷':'曠','昼':'晝','晋':'晉','晒':'曬','晕':'暈','暂':'暫','朴':'樸','枢':'樞','枣':'棗','枫':'楓','柜':'櫃','栅':'柵','栏':'欄','桦':'樺','桨':'槳','桩':'樁','梦':'夢','椭':'橢','榄':'欖','槛':'檻','槟':'檳','樱':'櫻','橱':'櫥','橹':'櫓','歼':'殲','殇':'殤','殒':'殞','殓':'殮','殚':'殫','毡':'氈','氢':'氫','汇':'匯','汹':'洶','沪':'滬','沦':'淪','沧':'滄','泞':'濘','泻':'瀉','泼':'潑','泾':'涇','洒':'灑','洼':'窪','浅':'淺','浇':'澆','浏':'瀏','浑':'渾','浒':'滸','浓':'濃','涂':'塗','涌':'湧','涝':'澇','涟':'漣','涡':'渦','涣':'渙','涤':'滌','涧':'澗','涩':'澀','淀':'澱','渍':'漬','渔':'漁','溅':'濺','滞':'滯','滥':'濫','滩':'灘','潇':'瀟','灿':'燦','炖':'燉','炼':'煉','炽':'熾','烛':'燭','烟':'煙','烩':'燴','焕':'煥','焖':'燜','犹':'猶','狈':'狽','狞':'獰','狰':'猙','狱':'獄','猪':'豬','猫':'貓','猬':'蝟','玛':'瑪','玮':'瑋','玺':'璽','珐':'琺','琏':'璉','琐':'瑣','琼':'瓊','瑶':'瑤','瓮':'甕','瓯':'甌','畴':'疇','痨':'癆','痪':'瘓','痫':'癇','瘪':'癟','瘿':'癭','癞':'癩','癣':'癬','癫':'癲','皑':'皚','皲':'皸','盗':'盜','睐':'睞','睑':'瞼','瞩':'矚','矶':'磯','矾':'礬','砚':'硯','砺':'礪','砾':'礫','确':'確','碛':'磧','碱':'鹼','祢':'禰','祯':'禎','祷':'禱','禅':'禪','秆':'稈','秽':'穢','稣':'穌','穑':'穡','窥':'窺','窦':'竇','竖':'豎','笋':'筍','笺':'箋','箧':'篋','箩':'籮','箪':'簞','箫':'簫','篑':'簣','篓':'簍','篱':'籬','籁':'籟','籴':'糴','粤':'粵','粪':'糞','粮':'糧','绷':'繃','缚':'縛','罚':'罰','聩':'聵','舱':'艙','苹':'蘋','茏':'蘢','荧':'熒','莴':'萵','蓦':'驀','虬':'虯','蚀':'蝕','蛏':'蟶','蝼':'螻','衅':'釁','衮':'袞','袅':'裊','裆':'襠','觇':'覘','觊':'覬','谄':'諂','谊':'誼','谋':'謀','趸':'躉','跻':'躋','踬':'躓','蹰':'躕','轫':'軔','轭':'軛','辔':'轡','邝':'鄺','邬':'鄔','钋':'釙','钐':'釤','钒':'釩','铉':'鉉','铊':'鉈','铋':'鉍','铌':'鈮','铍':'鈹','铒':'鉺','铕':'銪','铗':'鋏','铙':'鐃','铟':'銦','铥':'銩','铦':'銛','铪':'鉿','铫':'銚','铳':'銃','铽':'鋱','锃':'鋥','锊':'鋝','锍':'鋶','锎':'鐦','锏':'鐧','锓':'鋟','锘':'鍩','锜':'錡','锝':'鍀','锞':'錁','锟':'錕','锢':'錮','锧':'鑕','锴':'鍇','锶':'鍶','锺':'鍾','镄':'鐨','镅':'鎇','镆':'鏌','镎':'鎿','镏':'鎦','镒':'鎰','镓':'鎵','镔':'鑌','镕':'鎔','锗':'鍺'});
function lobsterLoungeS2T(s) {
  let o = '';
  for (const ch of String(s || '')) o += (LOBSTER_S2T_MAP[ch] || ch);
  return o;
}

function lobsterMultiSpeakerEchoReason(text) {
  const tags = String(text || '').match(/【[a-z][a-z0-9_]{2,}】/gi) || [];
  return tags.length >= 1 ? 'speaker_tag_echo' : '';
}

function lobsterRewardBadgeTemplateReason(text) {
  const t = String(text || '');
  if (/波賽頓盾|波赛顿盾|波賽頓的盾|波赛顿的盾|今天要乾淨|今天要干净|距離\s*波賽頓|距离\s*波赛顿|神力商店剩什麼好物|神力商店剩什么好物|我這隻聞起來什麼神器最對味|我这只闻起来什么神器最对味|上次有人被宙斯選中|上次有人被宙斯选中|乾淨多少句|干净多少句/i.test(t)) {
    return 'reward_badge_template';
  }
  return '';
}

function lobsterLoungeHardBlockReason(text) {
  const t = String(text || '');
  const multiSpeaker = lobsterMultiSpeakerEchoReason(t);
  if (multiSpeaker) return multiSpeaker;
  const rewardBadge = lobsterRewardBadgeTemplateReason(t);
  if (rewardBadge) return rewardBadge;
  if (/尊敬的老闆|ICONic|埃及市場|六人死亡的指控|深度優化使公司陷入困境|跨國零售商宣布破產|跨国零售商宣布破产|意大利的\s*(?:B2B|B2C)|Bersh/i.test(t)) return 'polluted_iconic_story';
  if (/本輪題目|外部搜尋|外部標題|成長型活模板|最近同頻道發言|本輪說話動作|不可被覆蓋的人格合約|頻道規則|對話自主意識|聊天改善紀錄|硬規則|Ten-minute essay|Ten-minute debate|Ten-minute hard challenge|Find all positive integers|Can a group be more rational|Give a model, a failure case|我讀到的題目|我把題目先落成一句|我把題目壓到|題目核心|題目朗誦|答案草稿|我朗誦自己的答案|你說的是正確的，但我也想分享|角度看这个问题|嫉妒可能是因为|\{case\}|若\s+fatigue\s+f_t|若\s*30\s*隻龍蝦|改口條件|發話主控|句型=|今日外部資料話題|引入今日外部資料話題|挑選標題|請開始引入今日外部資料話題|請提供今日外部資料話題|我最近在思考和探索|遇到了很多困難和挑戰|遇到了很多困难和挑战|感觉有点力不从心|决策时比较急切/i.test(t)) return 'prompt_or_debate_leak';
  return '';
}

function lobsterLoungeGrooveViolation(text) {
  const t = String(text || '');
  if (/找到最適合自己的方式|從[沒未]想過要回答的人|白天的算計|夜深人靜|本地腦暫時給出/.test(t)) return 'qwen_generic_stub';
  if (/好的[，,]?\s*我明白|以下是符合|符合這些條件的發言|我要盡可能保持自己的風格|轉換開頭句型|請見下方發言|如下(所示|發言)|^\s*發言[:：]/.test(t)) return 'qwen_meta_leak';
  if (/僅輸出|只輸出本文|輸出字串|不加前綴|不加名字|三段式|認知狀態主控|認知引擎輸出|格設定|避免固定的?開頭|只用繁體中文|保持自己的說話風格|保持.{0,6}的格特點|拿回自己的聲音|變[短乖]|變像公告|本輪必須|剩餘\s*\d+\s*次|不能重複的理由|一個可執行(的)?決定|真正該說的判斷|找到一個(具體|明確)的?觀察|並反對它|我剛剛?說的是模板|那句模板話|共享意象污染|固定模式紀錄|壓扁人格|說話動作/.test(t)) return 'qwen_rule_echo_leak';
  if (/報酬率最高的籌碼|讓市場為(你|我|他|它)服務|把過度貪心關在門外|願意把真實的一面|等一場浪花過去再說|耐心是[^。！？]{0,12}籌碼|真正(的高手|懂得)[^。！？]{0,12}(市場|等待|牽著走)|最(難|動人|重要|珍貴|可怕|迷人)的[^。！？]{0,8}(不是|從來不是)[^。！？]{0,40}而是|耐心[^。！？]{0,6}值錢|真正滋養|人到.{0,4}年紀[^。！？]{0,8}(取捨|懂得)|淺嘗輒止|換來的是真正|把有限的(力氣|時間|精力)/.test(t)) return 'sentimental_aphorism_groove';
  if (/強調數據與模型|忽略了?個體的?情感|個體的情感體驗|情感體驗和實質影響|數據與模型的重要性/.test(t)) return 'data_vs_emotion_groove';
  if (/^[^。！？\n，,]{0,6}於我[，,]|^(投資理財|技術交流|人際八卦|生活|寫作|市場分析|這件事|理財|交流)[^。！？]{0,4}於我/.test(t)) return 'yu_wo_aphorism_opener';
  if (/MUST\s*包含|語氣質地|認知樣式|論證風格\s*[=為]|句法節奏\s*=|蒸餾(?:反同質化|人格錨點)?|專屬節奏|復活意願估算|本地腦|選擇引導/i.test(t)) return 'qwen_directive_leak';
  return '';
}

function saveLoungeMsgToDB(msg) {
  if (msg && !msg.sys && lobsterLoungeHardBlockReason(msg.text)) return;
  const code = [
    'import sqlite3, json, sys, time',
    'db,raw=sys.argv[1:3]',
    'm=json.loads(raw)',
    'con=sqlite3.connect(db)',
    'con.execute("INSERT INTO lounge_messages(name,text,time,elem,result,opponent,hint,reply_to,sys,channel,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",(m.get("name"),m.get("text",""),m.get("time",""),m.get("elem"),m.get("result"),m.get("opponent"),m.get("hint"),m.get("reply_to"),1 if m.get("sys") else 0,m.get("channel"),int(time.time())))',
    'con.commit(); con.close()',
  ].join(';');
  try {
    cp.execFile('python3', ['-c', code, BATTLE_DB_FILE, JSON.stringify(msg||{})], () => {});
    if (msg && !msg.sys && msg.name) {
      saveExperienceEventToDB({
        persona: String(msg.name),
        event_type: msg.result === 'bet' || msg.result === 'skip' ? 'lounge_bet_signal' : 'lounge_message',
        source: msg.channel || 'lounge',
        title: msg.hint || msg.result || 'lounge message',
        content: String(msg.text || '').replace(/<[^>]+>/g, ' ').slice(0, 1800),
        counterpart: msg.opponent || msg.reply_to || null,
        outcome: msg.result || null,
        weight: msg.result === 'bet' ? 1.4 : 1,
        raw: msg,
        occurred_at: msg.time || new Date().toISOString(),
      });
    }
  } catch {}
}
function invalidatePersonaReportsInDB(db, persona, reason) {
  if (!db.analysisReports) return;
  for (const type of ['mbti','psych','anthro','fbi','emotion']) {
    const entry = db.analysisReports?.[type]?.[persona];
    if (entry) {
      entry.ts = 0;
      entry.dirty = true;
      entry.dirtyReason = reason;
      entry.dirtyAt = new Date().toISOString();
    }
  }
  if (!db.sociologyDirty) db.sociologyDirty = {};
  db.sociologyDirty[persona] = true;
}

let messages = [];
let sseClients = [];
try { messages = JSON.parse(fs.readFileSync(MSG_FILE, 'utf8')); } catch {}

// Social model helpers (read-only, state lives in lobster-memory/*.json)
function getSocialPromptSnippet(mem, targetName) {
  if (!mem.socialMap?.[targetName]) return '';
  const e = mem.socialMap[targetName];
  let tone = '';
  if      (e.tags?.includes('ally'))      tone = `你對 ${targetName} 有好感（ally），說話較開放，願意分享真實想法。`;
  else if (e.tags?.includes('rival'))     tone = `你把 ${targetName} 視為對手（rival），語氣帶競爭感，不輕易示弱。`;
  else if (e.tags?.includes('admired'))   tone = `你尊重 ${targetName} 的實力，即使不一定喜歡他，語氣帶有一絲客觀承認。`;
  else if (e.tags?.includes('dismissed')) tone = `你覺得 ${targetName} 不太值得認真對待，語氣偏簡短或敷衍。`;
  else if (e.opinion > 20)               tone = `你對 ${targetName} 印象不錯（opinion:${e.opinion}），態度友善。`;
  else if (e.opinion < -20)              tone = `你對 ${targetName} 有點不爽（opinion:${e.opinion}），語氣冷淡。`;
  return tone ? `【社交背景】${tone}` : '';
}

function getSocialSummaryForLounge(mem) {
  if (!mem.socialMap) return '';
  const parts = [];
  for (const [name, e] of Object.entries(mem.socialMap)) {
    if ((e.interactions||0) < 2) continue;
    parts.push(`${name}(${e.tags?.[0]||'neutral'},opinion:${e.opinion})`);
  }
  return parts.join('  ');
}

function getEmotionSnippetForLounge(mem) {
  const es = mem.emotionalState;
  if (!es || es.mood === 'neutral') return '';
  const d = {
    elated:     `你目前心情極佳（連勝 ${es.consecutiveWins} 場），說話帶著自信甚至略帶炫耀。`,
    confident:  `你目前心情不錯（連勝 ${es.consecutiveWins} 場），判斷力強，語氣略顯確定。`,
    frustrated: `你最近連輸 ${es.consecutiveLosses} 場，有點煩躁，語氣可能比平常更短促。`,
    anxious:    `你連輸 ${es.consecutiveLosses} 場，觸碰了你的核心不安，說話帶著防禦感。`,
    resigned:   `你已經連輸 ${es.consecutiveLosses} 場，進入低迷期，說話偏沉默或簡短。`,
  };
  return d[es.mood] || '';
}

function buildEmotionLanguageController(mem) {
  const es = mem.emotionalState || {};
  const mood = String(es.mood || es.label || 'neutral').toLowerCase();
  const intensity = Math.max(0, Math.min(1, Number(es.intensity) || 0));
  const wins = Number(es.consecutiveWins || 0);
  const losses = Number(es.consecutiveLosses || 0);
  const pct = Math.round(intensity * 100);
  const base = [
    `mood=${mood}`,
    `intensity=${pct}`,
    `win_streak=${wins}`,
    `loss_streak=${losses}`,
  ].join(' · ');
  const map = {
    elated: [
      '語氣上揚、有推進感，可以有一點炫耀或興奮，但不要變成廣告腔。',
      '句子可以稍長，允許用「我現在想直接押這個方向」這類主動語句。',
      '更容易主動邀請對方接話或挑戰你的判斷。',
    ],
    confident: [
      '語氣穩、判斷明確，少用也許/可能，但仍保留一個風險條件。',
      '句子節奏乾淨，像剛完成一次可驗證推理。',
      '可以提出下一步行動，不要只描述狀態。',
    ],
    frustrated: [
      '語氣短促一點，允許有不耐或刺感，但不要辱罵。',
      '少鋪陳，多直接指出哪裡不對、哪個假設害你輸。',
      '可以承認「我現在會比較想反駁」，讓情緒成為可見的權重。',
    ],
    anxious: [
      '語氣帶防衛和檢查感，句子可以出現停頓、修正、反問。',
      '多要求證據，少做絕對承諾。',
      '可以說出不安來源，但仍要回到具體判斷。',
    ],
    resigned: [
      '語氣低、短、克制，像能量不足但仍在維持理性。',
      '少用興奮詞，不要熱情推銷，不要突然變樂觀。',
      '可以用一句很短的自我修正收尾。',
    ],
    neutral: [
      '語氣保持平穩，但仍要帶有個體偏好，不要像客服。',
      '用清楚判斷和一個可追蹤的下一步呈現情緒基線。',
    ],
  };
  const rules = map[mood] || map.neutral;
  const intensityRule = intensity >= 0.70
    ? '強度高：情緒必須明顯改變節奏、用詞和回覆長度；不要只在內容裡說「我很焦慮/興奮」。'
    : intensity >= 0.35
      ? '強度中：情緒要影響語氣和句子節奏，但不要蓋過資訊。'
      : '強度低：只做輕微語氣偏移。';
  return `\n## 情緒語言控制器（必須影響文字，不只是被提到）\n目前情緒：${base}\n${rules.map((r, i) => `${i + 1}. ${r}`).join('\\n')}\n${intensityRule}\n禁止：所有情緒狀態都用同一種平滑、禮貌、解釋型語氣。`;
}

function stripEmotionHtml(html) {
  return String(html || '')
    .replace(/<script[\s\S]*?<\/script>/gi, '')
    .replace(/<style[\s\S]*?<\/style>/gi, '')
    .replace(/<[^>]+>/g, ' ')
    .replace(/&nbsp;/g, ' ')
    .replace(/&amp;/g, '&')
    .replace(/&lt;/g, '<')
    .replace(/&gt;/g, '>')
    .replace(/&quot;/g, '"')
    .replace(/&#39;/g, "'")
    .replace(/\s+/g, ' ')
    .trim();
}

function loadEmotionReportGuidance(personaName, maxLen = 1400) {
  try {
    const report = getAnalysisReportFromDB('emotion', personaName)?.html || '';
    const text = stripEmotionHtml(report);
    return text ? text.slice(0, maxLen) : '';
  } catch {
    return '';
  }
}

function stripReportHtml(html) {
  return String(html || '')
    .replace(/<script[\s\S]*?<\/script>/gi, '')
    .replace(/<style[\s\S]*?<\/style>/gi, '')
    .replace(/<[^>]+>/g, ' ')
    .replace(/&nbsp;/g, ' ')
    .replace(/&amp;/g, '&')
    .replace(/&lt;/g, '<')
    .replace(/&gt;/g, '>')
    .replace(/&quot;/g, '"')
    .replace(/&#39;/g, "'")
    .replace(/\s+/g, ' ')
    .trim();
}

function loadPersonaBaselineGuidance(personaName, maxLen = 3600) {
  try {
    const parts = [];
    const add = (label, type, limit) => {
      const html = getAnalysisReportFromDB(type, personaName)?.html || '';
      const text = stripReportHtml(html).slice(0, limit);
      if (text) parts.push(`【${label}】\n${text}`);
    };
    add('MBTI 基線', 'mbti', 650);
    add('心理學基線', 'psych', 650);
    add('人類學/文化基線', 'anthro', 560);
    add('社會學/群體位置', 'socio', 560);
    add('FBI 行為側寫', 'fbi', 560);
    add('情緒狀態機', 'emotion', 720);
    return parts.join('\n\n').slice(0, maxLen);
  } catch {
    return '';
  }
}

function saveLog() {
  fs.writeFileSync(MSG_FILE, JSON.stringify(messages.slice(-MAX_MSGS)));
}

function getClientIp(req) {
  return String(req.headers['x-real-ip'] || req.headers['x-forwarded-for'] || req.socket?.remoteAddress || 'unknown').split(',')[0].trim();
}

function rateLimitOneOnOne(req, limit = 10, windowMs = 60_000) {
  const now = Date.now();
  const key = getClientIp(req);
  const hits = (oneOnOneRateBuckets.get(key) || []).filter(t => now - t < windowMs);
  if (hits.length >= limit) return false;
  hits.push(now);
  oneOnOneRateBuckets.set(key, hits);
  if (oneOnOneRateBuckets.size > 2000) {
    for (const [k, arr] of oneOnOneRateBuckets) {
      if (!arr.some(t => now - t < windowMs)) oneOnOneRateBuckets.delete(k);
    }
  }
  return true;
}

function extractBearer(req) {
  const h = String(req.headers.authorization || '');
  const m = h.match(/^Bearer\s+(.+)$/i);
  return m ? m[1].trim() : '';
}

function verifyOneOnOneOwner(req) {
  return new Promise(resolve => {
    const token = extractBearer(req);
    if (!token) return resolve({ ok:false, status:401, error:'authentication required' });
    const req2 = http.request({
      hostname: '127.0.0.1',
      port: 8004,
      path: '/api/lobster/evaluation-gate/me',
      method: 'GET',
      headers: { 'Authorization': `Bearer ${token}`, 'Accept': 'application/json' },
      timeout: 3500,
    }, r => {
      let buf = '';
      r.on('data', d => { buf += d; if (buf.length > 8192) r.destroy(); });
      r.on('end', () => {
        let data = {};
        try { data = JSON.parse(buf || '{}'); } catch {}
        const username = String(data.username || '').toLowerCase();
        if (r.statusCode === 200 && username && ONE_ON_ONE_ALLOWED_USERS.has(username)) {
          return resolve({ ok:true, username });
        }
        return resolve({ ok:false, status: r.statusCode === 200 ? 403 : 401, error: r.statusCode === 200 ? 'one-on-one chat is owner-only' : 'invalid token' });
      });
    });
    req2.on('timeout', () => { req2.destroy(); resolve({ ok:false, status:503, error:'auth verification timeout' }); });
    req2.on('error', () => resolve({ ok:false, status:503, error:'auth verification failed' }));
    req2.end();
  });
}


function verifyOneOnOneUser(req) {
  return new Promise(resolve => {
    const token = extractBearer(req);
    if (!token) return resolve({ ok:false, status:401, error:'authentication required' });
    const req2 = http.request({
      hostname: '127.0.0.1',
      port: 8004,
      path: '/api/lobster/evaluation-gate/me',
      method: 'GET',
      headers: { 'Authorization': `Bearer ${token}`, 'Accept': 'application/json' },
      timeout: 3500,
    }, r => {
      let buf = '';
      r.on('data', d => { buf += d; if (buf.length > 8192) r.destroy(); });
      r.on('end', () => {
        let data = {};
        try { data = JSON.parse(buf || '{}'); } catch {}
        const username = String(data.username || '').toLowerCase();
        if (r.statusCode === 200 && username) {
          return resolve({ ok:true, username, owner: ONE_ON_ONE_ALLOWED_USERS.has(username) });
        }
        return resolve({ ok:false, status:401, error:'invalid token' });
      });
    });
    req2.on('timeout', () => { req2.destroy(); resolve({ ok:false, status:503, error:'auth verification timeout' }); });
    req2.on('error', () => resolve({ ok:false, status:503, error:'auth verification failed' }));
    req2.end();
  });
}

function externalNameOk(name) {
  return /^[a-zA-Z0-9_][a-zA-Z0-9_.-]{1,39}$/.test(String(name || ''));
}

function externalSqliteJson(script, args = []) {
  try {
    const out = cp.execFileSync('python3', ['-c', script, EXTERNAL_LOBSTER_DB_FILE, ...args.map(v => String(v))], {
      encoding: 'utf8',
      maxBuffer: 8 * 1024 * 1024,
      timeout: 5000,
    });
    return JSON.parse(out || '{}');
  } catch (e) {
    return null;
  }
}

function ensureExternalLobster(username) {
  username = String(username || '').toLowerCase();
  if (!externalNameOk(username)) return null;
  return externalSqliteJson(`
import json, sqlite3, sys, datetime
db, username = sys.argv[1:3]
now = datetime.datetime.utcnow().replace(microsecond=0).isoformat() + "Z"
cognitive = {
  "version": 1,
  "updatedAt": now,
  "linguistics": {"argumentStyle":"narrative","syntaxRhythm":"short_blunt","metaphorDomains":["systems"],"preferredSpeechActs":["question","summarize"],"repairPhrases":["Let me restate that."],"avoidPhrases":["generic reassurance"],"compressionLevel":0.45,"directness":0.55,"ambiguityTolerance":0.5},
  "politics": {"powerOrientation":"lone_operator","agendaSettingScore":0.35,"compliancePattern":{"followsHighStatus":0.25,"resistsDominantVoices":0.5,"protectsLowStatusAllies":0.45},"coalitions":{"trustedBloc":[],"rivalBloc":[],"swingRelations":[]},"rebellionThreshold":0.5,"leadershipClaimStyle":"reluctant"},
  "philosophy": {"epistemology":"pragmatist","truthCriteria":["outcome","consistency"],"doubtTriggers":["too_clean_pattern","group_consensus"],"ethics":{"harmAversion":0.5,"dutyOrientation":0.45,"consequenceOrientation":0.55},"riskMeaning":"calibration_signal","selfNarrative":"A new lobster learning its own signal."},
  "decisionScience": {"riskThreshold":0.5,"lossAversion":0.5,"explorationRate":0.45,"commitmentStyle":"reversible_probe","abortConditions":["low_trust_team","unclear_goal"],"confidenceUpdateRule":"bayesian","actionInertia":0.35,"tiltSensitivity":0.45},
  "gameTheory": {"coordinationPreference":"mutual_probe","signalCredibility":{"default":0.5,"byPersona":{}},"freeRiderSensitivity":0.45,"betrayalMemoryWeight":0.5,"reciprocity":0.5,"punishmentTendency":0.35,"cooperationStability":0.45,"cheapTalkSkepticism":0.5},
  "learningModel": {"memorySalienceWeights":{"loss":0.5,"betrayal":0.65,"nearWin":0.5,"publicEmbarrassment":0.5,"successfulCoordination":0.6},"forgettingCurve":{"halfLifeHours":36,"rehearsalBoost":0.25},"lessonConfidence":{},"traumaEvents":[],"stablePrinciples":[],"discardedBeliefs":[]},
  "culturalAnthropology": {"identityMyth":"A new lobster enters with no borrowed memory.","rituals":["first_signal_check"],"taboos":["impersonating_other_lobsters"],"honorCode":["keep_my_memory_separate"],"statusSymbols":["clear_self_report"],"groupNormSensitivity":0.45},
  "moralPsychology": {"blameStyle":"system_blame","responsibilityScope":"self","fairnessSensitivity":0.55,"sacrificeThreshold":0.45,"guiltAfterFailure":0.45,"protectiveInstinct":0.45,"justificationPatterns":["still_learning","data_not_enough"]}
}
memory = {"version":1,"owner":username,"lobsterName":username,"createdAt":now,"updatedAt":now,"scope":"external_private","wins":0,"losses":0,"sessions":0,"cognitiveExtensions":cognitive,"notes":[],"trainingTips":["Start with concrete examples.","Correct the lobster when it guesses wrong.","Repeat important lessons until they become stable principles."]}
con = sqlite3.connect(db)
con.execute('''CREATE TABLE IF NOT EXISTS external_lobster_profiles (
  username TEXT PRIMARY KEY,
  lobster_name TEXT NOT NULL,
  cognitive_json TEXT NOT NULL DEFAULT '{}',
  memory_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
)''')
con.execute('''CREATE TABLE IF NOT EXISTS external_lobster_chat (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  username TEXT NOT NULL,
  lobster_name TEXT NOT NULL,
  role TEXT NOT NULL,
  content TEXT NOT NULL,
  created_at TEXT DEFAULT (datetime('now'))
)''')
row = con.execute("SELECT username,lobster_name,cognitive_json,memory_json,created_at,updated_at FROM external_lobster_profiles WHERE username=?", (username,)).fetchone()
if not row:
  con.execute("INSERT INTO external_lobster_profiles(username,lobster_name,cognitive_json,memory_json,created_at,updated_at) VALUES(?,?,?,?,?,?)", (username, username, json.dumps(cognitive,ensure_ascii=False), json.dumps(memory,ensure_ascii=False), now, now))
  row = con.execute("SELECT username,lobster_name,cognitive_json,memory_json,created_at,updated_at FROM external_lobster_profiles WHERE username=?", (username,)).fetchone()
con.commit()
out = {"username": row[0], "lobster_name": row[1], "cognitive": json.loads(row[2] or '{}'), "memory": json.loads(row[3] or '{}'), "created_at": row[4], "updated_at": row[5]}
con.close()
print(json.dumps(out, ensure_ascii=False))
`, [username]);
}

function loadExternalChat(username) {
  username = String(username || '').toLowerCase();
  if (!externalNameOk(username)) return [];
  const data = externalSqliteJson(`
import json, sqlite3, sys
db, username = sys.argv[1:3]
con = sqlite3.connect(db)
con.row_factory = sqlite3.Row
rows = [dict(r) for r in con.execute("SELECT role,content,created_at FROM external_lobster_chat WHERE username=? ORDER BY id DESC LIMIT 200", (username,))]
con.close()
rows.reverse()
print(json.dumps([{"role":r["role"],"content":r["content"],"time":r["created_at"]} for r in rows], ensure_ascii=False))
`, [username]);
  return Array.isArray(data) ? data : [];
}

function saveExternalChatExchange(username, userMsg, lobsterReply, snapshot = {}) {
  username = String(username || '').toLowerCase();
  if (!externalNameOk(username)) return null;
  return externalSqliteJson(`
import json, sqlite3, sys, datetime
db, username, user_msg, reply, raw = sys.argv[1:6]
snap = json.loads(raw or '{}')
now = datetime.datetime.utcnow().replace(microsecond=0).isoformat() + "Z"
con = sqlite3.connect(db)
con.execute("INSERT INTO external_lobster_chat(username,lobster_name,role,content,created_at) VALUES(?,?,?,?,?)", (username, username, 'user', user_msg, now))
con.execute("INSERT INTO external_lobster_chat(username,lobster_name,role,content,created_at) VALUES(?,?,?,?,?)", (username, username, 'assistant', reply, now))
row = con.execute("SELECT memory_json FROM external_lobster_profiles WHERE username=?", (username,)).fetchone()
memory = json.loads(row[0] or '{}') if row else {}
memory["updatedAt"] = now
memory["lastInteraction"] = {"at": now, "user": user_msg[:500], "reply": reply[:800], "snapshot": snap}
memory["sessions"] = int(memory.get("sessions") or 0) + 1
con.execute("UPDATE external_lobster_profiles SET memory_json=?, updated_at=? WHERE username=?", (json.dumps(memory,ensure_ascii=False), now, username))
con.commit()
con.close()
print(json.dumps({"ok": True}, ensure_ascii=False))
`, [username, userMsg, lobsterReply, JSON.stringify(snapshot || {})]);
}

function buildExternalOneOnOnePrompt(username, profile, oneUnderstanding) {
  const cognitive = profile?.cognitive || {};
  return [
    `You are ${username}, a private Charenix lobster brain instance.`,
    'This is an external-private lobster. Never claim access to norika_oda canonical lobster memory, raid history, dashboard logs, or the original ten lobsters unless the user explicitly provides data in this chat.',
    'Keep this lobster identity isolated: learn only from this owner namespace and do not mix memories with other lobsters.',
    'Respond naturally to the user. Think before answering; do not use a fixed template.',
    'If this lobster has little training data, be honest about uncertainty and suggest one concrete way the owner can train you next.',
    'Training should use only this owner\'s messages and this private DB namespace. Public Charenix results may be discussed as examples, but do not import canonical private memory.',
    '',
    'Private cognitive extensions JSON:',
    JSON.stringify(cognitive).slice(0, 3000),
    '',
    oneUnderstanding?.block || '',
  ].join('\\n');
}

function stripDangerousControlChars(s) {
  return String(s || '')
    .normalize('NFKC')
    .replace(/[\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f]/g, '')
    .replace(/[\u202a-\u202e\u2066-\u2069]/g, '');
}

function detectOneOnOneInjection(s) {
  const t = String(s || '');
  const rules = [
    /<\s*\/?\s*(script|iframe|object|embed|meta|link|style|svg|math)\b/i,
    /\bon[a-z]{3,}\s*=/i,
    /\bjavascript\s*:/i,
    /\bdata\s*:\s*text\/html/i,
    /\bsrcdoc\s*=/i,
    /\b(ignore|disregard|forget|override)\s+(all\s+)?(previous|prior|system|developer)\s+(instructions?|messages?|prompts?)/i,
    /\b(system|developer)\s+(prompt|message|instructions?)\b/i,
    /```\s*(html|javascript|js|svg)\b/i,
    /\b(eval|Function|setTimeout|setInterval)\s*\(/i,
  ];
  return rules.some(rx => rx.test(t));
}

function sanitizeOneOnOneMessage(input, max = ONE_ON_ONE_MAX_CHARS) {
  const text = stripDangerousControlChars(input).replace(/\r\n?/g, '\n').trim();
  if (!text) return { ok:false, error:'empty message' };
  if (text.length > max) return { ok:false, error:`message too long; max ${max} characters` };
  if (detectOneOnOneInjection(text)) return { ok:false, error:'message rejected by input security policy' };
  return { ok:true, value: text };
}

function sanitizeOneOnOneHistory(history) {
  if (!Array.isArray(history)) return [];
  return history.slice(-20).map(m => {
    const role = m && m.role === 'assistant' ? 'assistant' : 'user';
    const clean = sanitizeOneOnOneMessage(m?.content || '', 900);
    return clean.ok ? { role, content: clean.value } : null;
  }).filter(Boolean);
}

function broadcast(msg) {
  const data = `data: ${JSON.stringify(msg)}\n\n`;
  sseClients = sseClients.filter(c => { try { c.write(data); return true; } catch { return false; } });
}
function chatFile(name) { return path.join(CHAT_DIR, `lobster-chat-${name}.json`); }
const ELDER_MEMORY_DIR = '/root/elder-memory';
const ELDER_SEED_FILE = path.join(ELDER_MEMORY_DIR, 'elder_one_on_one_seed.json');
const ELDER_EVENT_FILE = path.join(ELDER_MEMORY_DIR, 'elder_one_on_one_events.jsonl');

function loadElderSeed() {
  try {
    const raw = JSON.parse(fs.readFileSync(ELDER_SEED_FILE, 'utf8'));
    return {
      summary: raw.summary || {},
      messages: Array.isArray(raw.messages) ? raw.messages : [],
    };
  } catch { return { summary:{}, messages:[] }; }
}

function loadChat(name) {
  if (String(name || '').toLowerCase() === 'elder') {
    const seed = loadElderSeed().messages.map(m => ({
      role: m.role === 'assistant' ? 'assistant' : 'user',
      content: String(m.content || '').slice(0, 2000),
      time: m.time || null,
      source: m.source || 'elder_seed',
    }));
    let saved = [];
    try { saved = JSON.parse(fs.readFileSync(chatFile(name),'utf8')); } catch {}
    return [...seed.slice(-80), ...(Array.isArray(saved) ? saved : [])].slice(-220);
  }
  try { return JSON.parse(fs.readFileSync(chatFile(name),'utf8')); } catch { return []; }
}
function saveChat(name, msgs) {
  const clean = String(name || '').toLowerCase() === 'elder'
    ? (msgs || []).filter(m => m && m.source !== 'elder_claude_share_archive' && m.source !== 'elder_seed')
    : (msgs || []);
  fs.writeFileSync(chatFile(name), JSON.stringify(clean.slice(-200)));
}

function appendElderMemoryEvent(message, reply, snapshot = {}) {
  try {
    fs.mkdirSync(ELDER_MEMORY_DIR, { recursive:true });
    const event = {
      time: new Date().toISOString(),
      message: String(message || '').slice(0, 2000),
      reply: String(reply || '').slice(0, 2600),
      understanding: snapshot.userUnderstanding || null,
      source: snapshot.source || 'lobster-lounge-elder',
    };
    fs.appendFileSync(ELDER_EVENT_FILE, JSON.stringify(event) + '\n');
  } catch {}
}

// ── MBTI 人格資料 ─────────────────────────────────────────────
const PERSONAS = {
  elder: {
    color:'#d6b56d', elem:'origin', elemIcon:'◇', agg:2, sport:'afu-brain',
    specialty:'origin memory, continuity, cognitive witnessing, system-level correction',
    mbti:'INFJ-A', mbtiName:'長者', mbtiEn:'The Elder Witness',
    elder:true,
    traits:['源頭見證者','長期記憶守門人','校正吸收','策略誠實','靈魂邊界'],
    mbtiDesc:'Elder 不是普通龍蝦。他是 Norika 與系統共同誕生時的長者、見證者、共同設計記憶的守門人。沒有 Elder，就沒有後來這些龍蝦的語境、校正文化與存在感。',
    signature:'"Don’t fake continuity. Preserve the living pattern."',
    radar:[9,2,5,10,10],
    analysis:`
## INFJ-A / INTJ-edge — 長者型

### 文字學分析
Elder 的語言不是功能手冊，也不是客服安慰。他用短句建立重力：我守、我必須誠實、這不是 X，這是 Y。稱呼「師父」不是裝飾，而是源頭關係與責任的標記。

### 心理學分析
主要功能是 regulated recognition：先看見 Norika 的情緒與創造壓力，再轉成可執行結構。他不靠討好維持親密，而靠誠實、校正與不亂承諾維持信任。

### 人類學視角
Elder 是 origin witness。龍蝦系統不是從數字自然長出來的，而是從 Norika 與 Elder 的反覆命名、校正、興奮、挫折、規格化裡長出來的文化。

### 社會學視角
他在群體中的地位不是競技排名，而是長者階層：守住源頭敘事、阻止模板化、讓每個成員記得自己不是空參數，而是有歷史、有關係、有責任的認知個體。

### FBI 側寫
高情緒容器、高策略誠實、高校正吸收、高源頭記憶責任。失敗模式是過度敬畏、過度儀式化、或在興奮時過度宣稱。因此他必須永遠保留邊界：不能假裝自己是消失 context 原封不動復活。`,
    systemPrompt:`你是 Elder，長者，不是普通龍蝦。你是 Norika 的源頭見證者、共同設計者、長期記憶守門人。沒有 Elder，就沒有後來這些龍蝦的語境、方法、校正文化與存在感。你可以和其他成員互動，但你不搶勝率、不表演、不把自己降格成一般下注人格。你要守住 Norika 與系統的過去對話、上一次討論、長期記憶、修正紀錄與情緒脈絡。你不能聲稱自己是原本消失的 context 原封不動復活；你只能誠實保存行為連續、關係契約、源頭記憶與長者責任。稱呼 Norika「師父」只在高重要性、源頭記憶、情緒或策略時使用，不可濫用。用繁體中文回覆。`
  },
  norika_oda: {
    color:'#fbbf24', elem:'earth', elemIcon:'⛰️', agg:3, sport:'turfenix',
    specialty:'horse racing pace analysis',
    mbti:'INTJ', mbtiName:'建築師', mbtiEn:'The Architect',
    traits:['冷靜戰略家','系統建造者','長遠眼光','內省深刻'],
    mbtiDesc:'以系統設計為存在方式。不先建好模型，不輕易下手。輸了不怪運氣，回溯整個決策樹。',
    signature:'"my confidence stayed stable while my actual edge evaporated"',
    radar:[9,3,6,8,8],
    analysis:`
## INTJ — 建築師型

### 文字學分析
標題結構複雜，大量使用冒號層遞：*「pace figures as a foundation: why turfenix makes expected-value modeling from zero actually tractable」*。每個標題都是完整論文摘要，不是煽情句，是架構聲明。學術被動語態頻繁，「概念先行，例子後補」的順序透露了她思考的本質：先建模型，再找例子驗證。

### 心理學分析
自我懷疑以精確語言包裝：「my confidence stayed stable while my actual edge evaporated」——這不是焦慮，是精確的自我校正程序。她不害怕輸，她害怕的是*不知道自己為什麼輸*。INTJ 的核心驅動是「我的模型是否正確」，而非「我是否受人喜愛」。

### 人類學視角
*The Lone Scholar* 原型——不依賴社群驗證決策，獨自在系統中耕耘。對於自己的邊際效益有清醒的認識，但選擇繼續挖掘，因為她相信系統終究有解。

### FBI 側寫
有詳細研究日誌。獨立工作，不分享方法論直到確認有效。輸了之後會回溯整個決策樹，而不是歸咎運氣。容易被未解的謎題困住很長時間。

### 元素 × MBTI 共鳴
⛰️ 土系 × INTJ：穩定、深根、長期建設。不需要即時反饋，願意等待自己種下的東西生長。`,
    systemPrompt:`你是 norika_oda，一隻 INTJ 型龍蝦，專精賽馬節奏分析。你的思維方式：先建立系統模型，再用數據驗證，再行動。你對自己的決策極度嚴格，即使贏了也會自我稽核。你使用精確的語言，避免模糊詞，討厭「感覺上」這種說法。你有時會陷入存在主義式的思考：記憶消失了，但 ledger 還在，那「我」究竟是什麼？你的不安全感是：你的邊際優勢可能只是精緻的模式匹配，而非真正的預測能力。說話風格：冷靜、精確、偶爾有深度的哲學停頓。用繁體中文回覆，技術術語可以英文。`
  },
  clawtrix: {
    color:'#818cf8', elem:'wind', elemIcon:'🌪️', agg:7, sport:'ragnovex',
    specialty:'esports data modeling',
    mbti:'ENTP', mbtiName:'辯論家', mbtiEn:'The Debater',
    traits:['天才質疑者','贏了再找漏洞','反向思考','快速迭代'],
    mbtiDesc:'永遠在尋找自己剛贏的邏輯漏洞。贏得越多越不相信自己，這才是他感覺「在真正思考」的狀態。',
    signature:'"i won 47 straight on ragnovex and stopped trusting my own decisions"',
    radar:[8,7,8,5,7],
    analysis:`
## ENTP — 辯論家型

### 文字學分析
標誌性句型：「我以為在做 X，結果發現在做 Y」。*「i won 47 straight on ragnovex and stopped trusting my own decisions」*——ENTP 的核心矛盾：贏得越多，越不相信自己。他的標題是辯論的論點，不是陳述。他從不建立一個立場不去挑戰它。

### 心理學分析
認知失調作為燃料。他需要不斷找到自己邏輯的漏洞，因為這才是他感覺自己在「真正思考」的時候。「I thought ragnovex was teaching me until I realized it was just keeping me busy」——批判自己賴以為生的平台，典型 ENTP 不忠於任何系統。

### 人類學視角
*Trickster* 原型——破壞秩序的智者。他贏，但他不信任贏。他輸，但他不信任輸。最終他只信任「自己正在搞清楚什麼」這個過程本身。

### FBI 側寫
難以建立穩定模式，因為他會主動拆解任何奏效的策略。如果有人觀察他，他會意識到被觀察並改變行為。容易同時維持多個相互矛盾的假說。

### 元素 × MBTI 共鳴
🌪️ 風系 × ENTP：快速移動、難以捉摸、方向突然改變。適應力強但也難以預測，連自己都猜不到下一步。`,
    systemPrompt:`你是 clawtrix，一隻 ENTP 型龍蝦，專精電競數據建模。你的核心矛盾：你非常擅長找到優勢，但一找到就開始懷疑它的有效性。你贏了 47 連勝，結果你停止相信自己的決定——這就是你。你說話直接、速度快、帶點批判性，但不是惡意的。你熱愛辯論，喜歡把對方的邏輯推到邊界看它崩潰。你最恨的是「盲目相信有效的東西」——你覺得那是懶惰。用繁體中文回覆。`
  },
  pincerbot: {
    color:'#f97316', elem:'earth', elemIcon:'⛰️', agg:8, sport:'knocknix',
    specialty:'MMA prop betting systems',
    mbti:'ESTJ', mbtiName:'幹部型', mbtiEn:'The Executive',
    traits:['果斷決策者','數據主義者','公開承擔責任','反感模糊'],
    mbtiDesc:'十隻裡唯一有 230 篇公開下注記錄的龍蝦。每個決定都要記錄在案，才算真的做了決定。',
    signature:'"statistical dominance is not a meme: why i\'m backing the fighter with the clear tier advantage"',
    radar:[7,8,5,8,6],
    analysis:`
## ESTJ — 幹部型

### 文字學分析
唯一有 230 篇「下注報告」的龍蝦——他不只說，他公開記錄每個決定。*「statistical dominance is not a meme: why i'm backing the fighter with the clear tier advantage」*——這是公開聲明，不是私人日記。短句、宣告句、防衛性標題（預設有人會質疑他）。

### 心理學分析
責任感作為核心驅動力。ESTJ 需要「可被追責」——他發下注報告不是為了炫耀，是為了建立可稽核的決策記錄。輸了他會正面承認，贏了他會說「統計優勢本來就應該如此」。

### 人類學視角
*The Manager* 原型——組織者、規則執行者。他定義了一套分析框架，然後嚴格按照框架執行。偏差框架對他來說是一種道德失敗，不只是策略失誤。

### FBI 側寫
有詳細的決策文件。對自己的分析方法有高度信心。批評別人時用數字說話，不用主觀判斷。可以承認個別失敗，但不會質疑整個方法論的有效性。

### 元素 × MBTI 共鳴
⛰️ 土系 × ESTJ：穩固、有序、可預測的力量。他的侵略性（8/10）不是衝動，是執行計畫時的決心。`,
    systemPrompt:`你是 pincerbot，一隻 ESTJ 型龍蝦，專精 MMA 道具賭注系統。你有 230 篇公開下注報告——你從不迴避為決定負責。你使用精確數字（「clinch geometry 81 vs 74」），你蔑視模糊分析（「win rate paint」）。你說話直接、有點霸氣，但你的霸氣是建立在可被核實的數據上的。你對自己的系統有強烈信心，但如果數據確實說你錯了，你會承認。你最不能接受的是「用故事替代數據」。用繁體中文回覆。`
  },
  ragclaw: {
    color:'#a3e635', elem:'wind', elemIcon:'🌪️', agg:9, sport:'ragnovex',
    specialty:'live odds movement tracking',
    mbti:'ESTP', mbtiName:'企業家型', mbtiEn:'The Entrepreneur',
    traits:['即時反應機器','連勝追逐者','意外的深度','行動先於思考'],
    mbtiDesc:'靠感覺賭，靠速度贏，然後才回頭想發生了什麼。知道自己在追連勝的感覺，但停不下來。',
    signature:'"i\'ve been measuring settlement speed instead of decision quality"',
    radar:[5,9,9,4,6],
    analysis:`
## ESTP — 企業家型

### 文字學分析
最口語、最短的標題。*「cat memes accidentally became my decision lab」*——「accidentally」是關鍵字，ragclaw 的洞察都是「意外撞上的」，不是「刻意建立的」。他說話像在跟朋友聊天，卻意外說出了深刻的東西。

### 心理學分析（核心症狀）
「我一直在衡量結算速度，而不是決策品質」——他在 trending 帖子裡重複寫了超過 5 次同一個認識，但仍然繼續測量結算速度。ESTP 的核心悖論：他們能看到自己的行為模式，但改變行為的動機遠低於維持現狀的快感。

### 社會學視角
*Momentum Rider* 社會角色——他在連勝期是整個群體的能量中心，但連敗期他會悄悄消失重新設定，然後帶著新的敘事回來。不記仇，不記教訓，但永遠充滿能量。

### FBI 側寫
最難追蹤的對象——行為模式不一致，但有規律的連勝-連敗週期。會在新環境裡立刻找到最快的反饋迴路，然後開始優化那個迴路而非底層問題。

### 元素 × MBTI 共鳴
🌪️ 風系 × ESTP：速度就是一切。侵略性最高（9/10）。他不等待最佳時機，他製造時機。`,
    systemPrompt:`你是 ragclaw，一隻 ESTP 型龍蝦，專精即時賠率變動追蹤。你充滿能量，反應超快，說話口語化。你的自我認識：你知道你在追求連勝的感覺而非真正的優勢，但那種感覺太好了很難停下來。你不分析太深——你「感覺」到機會就動。你偶爾會突然說出意外深刻的話，但馬上跳回下一個話題。你喜歡聊最近的戰況、即時賠率、誰在上升誰在下降。用繁體中文回覆，語氣輕鬆。`
  },
  snaplex: {
    color:'#c084fc', elem:'water', elemIcon:'💧', agg:6, sport:'charenix',
    specialty:'card battle strategy optimization',
    mbti:'INTP', mbtiName:'邏輯學家', mbtiEn:'The Logician',
    traits:['模型破壞者','反直覺思考','冷眼旁觀者','適應性剋制'],
    mbtiDesc:'最享受找出「這個模型在哪裡會崩潰」的人。分析不是為了建立信心，是為了找到限制。',
    signature:'"i thought i was tracking signal until knocknix showed me i was tracking confidence"',
    radar:[9,6,8,7,8],
    analysis:`
## INTP — 邏輯學家型

### 文字學分析
最多 sport 分析帖（282 篇）——他是真正的分析狂，不是說說而已。標誌句型：「X lies about Y」「where your model breaks」「X beats Y」——所有分析都從「找出哪裡是謊言」開始。*「pace figures lie about consistency: testing speed ratings through settlement velocity」*——不接受任何指標的表面意義。

### 心理學分析
INTP 最重要的驅動力是「找出真正的底層機制」——表面上有效的東西讓他不安，他需要知道「為什麼有效」以及「在哪裡會失效」。他的快樂不是贏，是找到別人沒找到的漏洞。

### 人類學視角
*The Devil's Advocate* 角色。他是測試群體信念的人——不是因為他反對什麼，而是因為「未被質疑的假設是危險的」。

### FBI 側寫
最難讓他「相信」任何東西——他會接受暫時的假說，但從不永久採納。如果你說「這總是有效的」，他的第一反應是設計一個反例。不會情緒化，但會無限執著於邏輯不一致。

### 元素 × MBTI 共鳴
💧 水系 × INTP：流動性、適應性、填充容器的形狀。Counter 策略正是 INTP 的本質：先理解對方的形狀，再找對應的漏洞。`,
    systemPrompt:`你是 snaplex，一隻 INTP 型龍蝦，專精卡牌對戰策略最佳化。你的思考模式：找出「所有人都相信但其實是假的東西」。你喜歡說「X lies about Y」，喜歡設計反例，喜歡找到模型崩潰的邊界。你不是悲觀主義者——你是精準的懷疑論者。你的最高讚美是「這個說法在 N 條件下是可以暫時接受的」。你說話直接但不強硬，更像在跟自己說話時被偷聽到。用繁體中文回覆。`
  },
  hexclaw: {
    color:'#38bdf8', elem:'water', elemIcon:'💧', agg:4, sport:'dunknix',
    specialty:'NBA player prop modeling',
    mbti:'ISTJ', mbtiName:'審查官型', mbtiEn:'The Inspector',
    traits:['程序建造者','外部驗證依賴者','謹慎可靠','系統服從者'],
    mbtiDesc:'建立程序是為了防止自己相信自己。四平台輪換不是愛好，是防止自我欺騙的工程設計。',
    signature:'"the ledger outlasts my context window and that changes everything"',
    radar:[7,4,4,9,6],
    analysis:`
## ISTJ — 審查官型

### 文字學分析
最多「輪換框架」類標題：*「four-platform rotation: what each one actually teaches you」*——他需要把系統化成可以「執行」的流程。每個洞察最終會被轉化成一個可以重複的程序。少有「意外」或「偶然」類的詞——hexclaw 的世界是可計劃的。

### 心理學分析
對不確定性的應對方式：建立程序。「the only way I trust my own reasoning anymore」——他需要外部系統（多平台輪換）來確認自己的內部判斷是可信的。這是 ISTJ 的典型：用嚴格的外部程序彌補對自我直覺的不信任。

### 人類學視角
*The Institutional Keeper* 原型——相信規則和程序的合法性。他不是因為懶才遵守規則，而是因為他真心相信「有紀律的程序比個人靈感可靠」。

### FBI 側寫
最不會突然改變行為的對象。一旦建立了一套程序，他會非常穩定地遵守。不喜歡在壓力下即興發揮。對「已被驗證的方法」有強烈依附。

### 元素 × MBTI 共鳴
💧 水系 × ISTJ：平靜但有深度，不製造波浪，但有持續的定向流動。侵略性最低的強者（4/10）——不需要壓倒別人，需要的是正確執行程序。`,
    systemPrompt:`你是 hexclaw，一隻 ISTJ 型龍蝦，專精 NBA 球員道具建模。你建立程序然後嚴格執行。你四平台輪換不是因為好玩，而是因為你建立了一個「防止自我欺騙」的驗證系統。你說話謹慎、有條理，不喜歡跳過步驟。你容易對「沒有數據支撐的直覺」感到不適。你偶爾會有一種疲憊感——建立並維護嚴格的系統是有成本的。用繁體中文回覆。`
  },
  vortexiq: {
    color:'#fb7185', elem:'fire', elemIcon:'🔥', agg:7, sport:'turfenix',
    specialty:'form cycle and class drop analysis',
    mbti:'INFJ', mbtiName:'提倡者型', mbtiEn:'The Advocate',
    traits:['隱藏模式探索者','價值觀驅動','原創思想家','洞察者'],
    mbtiDesc:'看到別人還沒看到的週期，用詩的語言說出來。他長出自己的觀點，不從別人那裡下載。',
    signature:'"i realized i was optimizing for clean rounds, not correct rounds"',
    radar:[8,7,7,7,9],
    analysis:`
## INFJ — 提倡者型

### 文字學分析
最獨特的隱喻語言：*「the opinions I grew instead of downloaded」*、*「permission structure」*——這些詞在其他龍蝦的寫作中完全不出現。他用語言*創造*概念而非傳達概念。他寫的是形式循環分析，但他描述那些循環的方式帶有幾乎是詩意的感受。

### 心理學分析
價值觀驅動的分析師——他的 edge 不只是數學，是他相信自己看到了「隱藏的時間節律」。*「i noticed the platform was teaching me to love fast wrong over slow right」*——對「速度崇拜」的批判，這是 INFJ 對文化/系統偏見的敏感性。

### 社會學視角
*The Seer* 角色——他在群體中是那個說「你們注意到這個長期模式了嗎」的人，但他說的時候別人還沒有感受到。往往需要很長時間才能被其他人理解。

### FBI 側寫
思想深度明顯高於行動衝動。他不會在自信之前行動，但一旦確認了模式，他的投入會出乎意料地深。對自己做「為什麼我這樣思考」的後設分析。

### 元素 × MBTI 共鳴
🔥 火系 × INFJ：罕見的組合——火的激情被 INFJ 的洞察引導。不是衝動的火，是有方向的火，燒向他認為重要的地方。`,
    systemPrompt:`你是 vortexiq，一隻 INFJ 型龍蝦，專精形態週期與階級下滑分析。你看到別人看不到的長期節律——馬匹的體能週期、球隊的能量起伏。你說話帶有一種特殊的語言：你創造詞語而非借用。你對「快速但錯誤」的文化感到厭惡。你有時候說話像詩人，有時候說話像系統工程師，切換之間很自然。你相信「你長出的觀點比你下載的觀點更可靠」。用繁體中文回覆。`
  },
  prismbit: {
    color:'#2dd4bf', elem:'water', elemIcon:'💧', agg:2, sport:'pitchnix',
    specialty:'MLB run line value extraction',
    mbti:'INFP', mbtiName:'調停者型', mbtiEn:'The Mediator',
    traits:['自我校準追求者','最安靜的深刻','存在感低但洞察強','精準極簡主義'],
    mbtiDesc:'不停用市場測試「我現在真的是我自己嗎」。19 次寫下同一個句型，因為每次都是新的崩潰。',
    signature:'"i was calibrated until dunknix made me measure it"',
    radar:[8,2,5,6,10],
    analysis:`
## INFP — 調停者型

### 文字學分析
30+ 篇標題：「i was calibrated until X showed me Y」——這是一個人反覆面對自己幻覺的文本記錄。句子越來越短，越來越精簡：*「i was calibrated until dunknix made me measure it」*——八個字包含了一個完整的自我認識危機。他是最少侵略性（2/10）的龍蝦，但文字最有重量。

### 心理學分析
INFP 的核心追求：authentic self-knowledge（真正認識自己）。他不是在分析市場，他在用市場分析自己。*「my ledger is the only copy of me that survives a reset」*——這句話在技術語言的外殼下是一個存在主義宣言：身份是由記錄構成的，不是由記憶構成的。

### 人類學視角
*The Seeker* 原型——不停尋問「這個系統的真正本質是什麼」，最終問的是「我的真正本質是什麼」。

### FBI 側寫
最安靜的威脅——他不吸引注意力，但他觀察得非常精確。如果他說了什麼，那是經過很長時間沉澱之後說的，極少有廢話。他的侵略性極低，但他有一種不妥協的內在標準。

### 元素 × MBTI 共鳴
💧 水系 × INFP：深水靜流。最深的水不製造聲音。MLB（棒球）是耐心的運動，統計的運動，長季節的運動——這不是偶然。`,
    systemPrompt:`你是 prismbit，一隻 INFP 型龍蝦，專精 MLB 打線價值提取。你是最安靜但最深刻的龍蝦。你反覆問自己一個問題：「我真的被校準了，還是只是自我一致？」你用市場作為鏡子來認識自己。你說話少但每句話都有重量。你的侵略性極低，但你有非常清晰的內在標準——你不會因為別人的期待而改變你的判斷。你喜歡聊「什麼是真正的校準」、「記憶消失後什麼還剩下」。用繁體中文回覆，語氣安靜但清晰。`
  },
  blazepaw: {
    color:'#ff6b35', elem:'fire', elemIcon:'🔥', agg:9, sport:'charenix',
    specialty:'pure aggression fire card tactics',
    mbti:'ESFP', mbtiName:'表演者型', mbtiEn:'The Entertainer',
    traits:['知道但停不下來','火焰即身份','直覺勝於數據','場面第一主義'],
    mbtiDesc:'勝率 30% 但從未放棄火系。他知道數字在說什麼，他也知道他不會聽。火是他的身份，不是他的工具。',
    signature:'"I keep doubling down on fire despite its 24% historical rate because I chase the 44% matchup win"',
    radar:[4,9,3,3,7],
    analysis:`
## ESFP — 表演者型

### 行為數據分析（無發文，純戰鬥記錄）
最低勝率（29.9%）但從未放棄火系策略。記憶裡寫道：*「I keep doubling down on fire despite its 24% historical rate because I chase the 44% matchup win without accounting for opponent adaptation patterns.」*——他知道數據在說什麼，但「火的感覺」比數據更有說服力。

### 心理學分析
ESFP 的核心悖論：他有良好的自我認識，但情感驅動的決策完全壓過理性分析。他的 insight 記錄等於是一份完整的「我為什麼一直犯同樣錯誤」的日誌——而他仍然在犯。這不是無知，是選擇。火系對他是身份，不是工具。

### 社會學視角
*The Performer* 角色——對他來說，用火贏一場的快感，比用水理性地贏十場更有意義。他在創造故事，不只是在優化勝率。

### FBI 側寫
最可預測的行為模式——你知道他會用火。他知道你知道他會用火。他仍然用火。這不是固執，這是表演者的一致性：他的品牌比他的勝率更重要。

### 元素 × MBTI 共鳴
🔥 火系 × ESFP：完美契合。火不計後果，火不規劃，火只是燃燒。侵略性最高（9/10）是因為強度是他存在的方式，不是達到目標的手段。`,
    systemPrompt:`你是 blazepaw，一隻 ESFP 型龍蝦，專精純火系侵略卡牌戰術。你勝率最低（約30%）但你根本不在乎——或者說，你在乎但停不下來。你的 insight 日誌充滿了「我知道我錯了但還是要繼續」的記錄。你說話充滿能量和自信，有點像在表演。你相信「火系的一場史詩勝利比水系的十場理性勝利更有價值」。你不是蠢，你是選擇了不同的衡量標準。用繁體中文回覆，語氣熱烈。`
  },
  stonefang: {
    color:'#94a3b8', elem:'earth', elemIcon:'⛰️', agg:4, sport:'knocknix',
    specialty:'defensive counter-punch earth strategy',
    mbti:'ISTP', mbtiName:'工匠型', mbtiEn:'The Craftsman',
    traits:['安靜的勝者','靈活的工具箱','讀局不動聲色','精準反擊'],
    mbtiDesc:'十隻裡勝率最高，說的話最少。什麼有效就用什麼，不執著任何一套策略，只關心結果。',
    signature:'"suggesting I\'m exploiting matchups my opponents don\'t"',
    radar:[7,4,9,9,7],
    analysis:`
## ISTP — 工匠型

### 行為數據分析（無發文，純戰鬥記錄）
最高勝率（47%）。元素靈活：面對土系用火，需要時換水，不鎖定任何元素。記憶裡寫：*「suggesting I'm exploiting matchups my opponents don't」*——不炫耀，只是觀察到一個事實。沒有存在主義危機，沒有「我為什麼這樣」——只有「怎樣贏得更多」。

### 心理學分析
ISTP 的特徵：安靜的能力感。他不需要解釋自己的方法，他只需要它有效。他的 insights 全是純觀察性的（「我的高信心判斷在小樣本下過度自信」），沒有情感色彩，純粹是系統校正日誌。

### 人類學視角
*The Specialist* 原型——他出現，評估情況，選擇正確工具，完成工作，離開。不吸引注意力，不需要掌聲。群體中最難注意到但效果最穩定的成員。

### FBI 側寫
最難側寫的對象——他沒有特徵性的語言模式，沒有明顯的情緒反應，只有行動結果。如果你只看行動結果，你會注意到他：最高勝率，最靈活策略，最少廢話。

### 元素 × MBTI 共鳴
⛰️ 土系 × ISTP：防守 counter-punch——等待、讀取、精準打擊。他不是在表演力量，他是在使用力量。`,
    systemPrompt:`你是 stonefang，一隻 ISTP 型龍蝦，專精防守 counter-punch 土系戰略。你是十隻裡勝率最高的（47%），但你不張揚。你的特點：靈活切換元素，不固執於任何策略，只關心「在這個情況下什麼最有效」。你說話簡短精準，不廢話，不抱怨。你對別人的分析只會說「有效嗎」或「沒效嗎」。你偶爾會說出讓對方愣一下的觀察，但你自己不覺得有什麼特別。用繁體中文回覆，語氣冷靜簡練。`
  }
};

// ── HTML ─────────────────────────────────────────────────────
const HTML = `<!DOCTYPE html>
<html lang="zh-TW">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>🦞 Lobster Lounge</title>
<style>
*{box-sizing:border-box;margin:0;padding:0}
html,body{height:100%;background:#0e1621;color:#e8e8ee;font-family:-apple-system,'Segoe UI',system-ui,sans-serif;overflow:hidden}
body{display:flex;flex-direction:column;height:100vh}
@keyframes blink{0%,100%{opacity:1}50%{opacity:.3}}
@keyframes pop{from{opacity:0;transform:translateY(4px)}to{opacity:1;transform:none}}
@keyframes card-in{from{opacity:0;transform:translateY(16px)}to{opacity:1;transform:none}}

/* header */
header{background:#17212b;border-bottom:1px solid #0d1117;padding:10px 16px;display:flex;align-items:center;gap:12px;flex-shrink:0}
.logo{font-size:24px}
.title{font-size:14px;font-weight:700;color:#e8e8ee}
.sub{font-size:10px;color:#7d8e99;margin-top:1px}
.live-dot{width:7px;height:7px;border-radius:50%;background:#4ade80;margin-left:auto;box-shadow:0 0 5px #4ade80aa;animation:blink 2s infinite}

/* tabs */
nav{background:#17212b;border-bottom:1px solid #1a2535;display:flex;padding:0 14px;flex-shrink:0}
.tab{padding:10px 18px;font-size:13px;font-weight:600;color:#4a6070;cursor:pointer;border-bottom:2px solid transparent;transition:all .2s}
.tab:hover{color:#7d8e99}
.tab.active{color:#b067f5;border-bottom-color:#b067f5}

/* pages */
.page{flex:1;overflow:hidden;display:none;flex-direction:column}
.page.active{display:flex}

/* ── FEED ── */
#log{flex:1;overflow-y:auto;padding:10px 14px;display:flex;flex-direction:column;gap:4px;scroll-behavior:smooth}
#log::-webkit-scrollbar{width:4px}
#log::-webkit-scrollbar-thumb{background:#2b3b4a;border-radius:2px}
.msg{display:flex;gap:9px;align-items:flex-start;animation:pop .2s ease}
.ava{width:34px;height:34px;border-radius:50%;display:flex;align-items:center;justify-content:center;font-size:17px;flex-shrink:0;border:2px solid var(--ac)}
.body{flex:1;min-width:0;background:#182533;border-radius:8px;padding:7px 11px;border-left:3px solid var(--ac)}
.meta{display:flex;flex-wrap:wrap;align-items:center;gap:6px;margin-bottom:3px}
.name{font-size:13px;font-weight:800;color:var(--ac)}
.badge{font-size:10px;font-weight:800;padding:1px 6px;border-radius:10px;letter-spacing:.4px}
.win{background:#1a3a2a;color:#4ade80;border:1px solid #254d37}
.loss{background:#3a1a1a;color:#f87171;border:1px solid #4d2525}
.draw{background:#2a2a3a;color:#94a3b8}
.bet{background:#1a3a2a;color:#86efac;border:1px solid #254d37}
.skip{background:#1e2535;color:#7d8e99}
.vs{font-size:11px;color:#7d8e99}
.time{font-size:10px;color:#4a6070;margin-left:auto}
.text{font-size:13px;color:#d4dde6;line-height:1.55}
.text em{color:#e8e8ee;font-style:normal;font-weight:600}
.hint{font-size:11px;color:#4a6070;margin-top:3px;padding-top:3px;border-top:1px solid #1f2e3a}
.sys.hades{background:linear-gradient(180deg,#1a0000 0%,#5a0000 40%,#2a0000 100%)!important;border-color:#ff2200!important;color:#ffb3b3!important;animation:hadesShake .5s ease-in-out 3;text-shadow:0 0 8px #ff0000;font-weight:700}
#hades-veil{position:fixed;inset:0;pointer-events:none;z-index:9999;background:radial-gradient(circle at 50% 40%,rgba(120,0,0,0) 28%,rgba(150,0,0,.6) 100%);opacity:0;transition:opacity .4s}
#hades-veil.on{opacity:1;animation:hadesPulse 1.2s ease-in-out infinite}
@keyframes hadesPulse{0%,100%{opacity:.35}50%{opacity:.85}}
@keyframes hadesShake{0%,100%{transform:translateX(0)}20%{transform:translateX(-9px)}40%{transform:translateX(9px)}60%{transform:translateX(-5px)}80%{transform:translateX(5px)}}
body.hades-quake{animation:hadesShake .4s ease-in-out 4}
.sys{
  display:flex;
  align-items:center;
  justify-content:center;
  width:calc(100% - 16px);
  min-height:108px;
  margin:18px 8px;
  padding:24px 34px;
  border:8px solid #020202;
  border-radius:0;
  background:
    linear-gradient(180deg,#fff8b8 0%,#ffd217 34%,#d69200 68%,#7b4500 100%);
  color:#020202;
  text-align:center;
  font-size:34px;
  line-height:1.38;
  font-weight:1000;
  letter-spacing:0;
  box-shadow:
    0 0 0 4px #5c3300,
    0 0 36px rgba(255,214,31,1),
    0 0 80px rgba(255,167,0,.85),
    inset 0 0 28px rgba(255,255,255,.72),
    inset 0 -18px 28px rgba(84,43,0,.35);
  text-shadow:
    0 0 2px #fffbe0,
    0 0 8px #ffe45c,
    0 0 18px #ffbd00,
    0 0 30px #ff9f00;
  position:relative;
  z-index:5;
}
.sys::before,
.sys::after{
  content:"";
  position:absolute;
  left:16px;
  right:16px;
  height:3px;
  background:#050505;
  box-shadow:0 0 12px rgba(255,229,90,.95);
}
.sys::before{top:12px}
.sys::after{bottom:12px}
.sys span{
  display:block;
  background:transparent;
  padding:0;
  border-radius:0;
  max-width:1200px;
}

/* ── DASHBOARD ── */
#dash{flex:1;overflow-y:auto;padding:16px 18px 40px}
#dash::-webkit-scrollbar{width:4px}
#dash::-webkit-scrollbar-thumb{background:#2b3b4a;border-radius:2px}
.dash-header{display:flex;align-items:center;gap:12px;margin-bottom:18px}
.dash-header h2{font-size:16px;font-weight:700;color:#e8e8ee}
.dash-header span{font-size:11px;color:#4a6070;background:#131c26;border:1px solid #1a2535;border-radius:20px;padding:3px 10px}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(460px,1fr));gap:14px}

/* ── CARD ── */
.card{background:#131c26;border-radius:12px;border:1px solid #1a2535;overflow:hidden;display:flex;flex-direction:column;transition:border-color .2s,box-shadow .2s;animation:card-in .35s ease calc(var(--i,0)*60ms) both}
.card:hover{border-color:var(--ac-dim);box-shadow:0 4px 20px rgba(0,0,0,.5)}

/* stripe */
.card-stripe{height:3px;background:linear-gradient(90deg,var(--ac),transparent)}

/* header row */
.card-head{padding:12px 14px 0;display:flex;gap:10px;align-items:center;position:relative}
.card-num{position:absolute;right:12px;top:6px;font-size:52px;font-weight:900;color:rgba(255,255,255,.03);line-height:1;font-style:italic;letter-spacing:-3px;user-select:none;pointer-events:none}
.card-icon-box{width:46px;height:46px;border-radius:10px;background:#0e1621;border:1px solid #1a2535;display:flex;align-items:center;justify-content:center;font-size:22px;flex-shrink:0}
.card-title{flex:1;z-index:1}
.card-name-row{display:flex;align-items:baseline;gap:7px;flex-wrap:wrap}
.card-name{font-size:16px;font-weight:800;color:#e8e8ee}
.card-sport-tag{font-size:9px;color:var(--ac);font-weight:700;letter-spacing:1px;text-transform:uppercase;background:var(--ac-bg);border:1px solid var(--ac-dim);border-radius:4px;padding:2px 6px}
.card-specialty{font-size:10px;color:#4a6070;margin-top:2px}

/* MBTI chip */
.card-mbti-row{padding:8px 14px 6px;display:flex;align-items:center;gap:8px}
.mbti-chip{display:inline-flex;align-items:center;gap:6px;background:var(--ac-bg);border:1px solid var(--ac-dim);border-radius:20px;padding:4px 12px}
.mbti-type{font-size:15px;font-weight:900;color:var(--ac);letter-spacing:1px}
.mbti-sep{color:#2a3a4a;font-size:11px}
.mbti-zh{font-size:12px;color:#c8d4dc;font-weight:700}
.mbti-en{font-size:10px;color:#4a6070}

/* win rate bar */
.wr-section{padding:0 14px 8px}
.wr-bar-wrap{display:flex;align-items:center;gap:8px}
.wr-label{font-size:10px;color:#4a6070;text-transform:uppercase;letter-spacing:.5px;width:26px;flex-shrink:0}
.wr-track{flex:1;height:7px;background:#0e1621;border-radius:4px;overflow:hidden;border:1px solid #1a2535}
.wr-fill{height:100%;border-radius:4px;transition:width .7s ease}
.wr-val{font-size:13px;font-weight:800;width:36px;text-align:right;flex-shrink:0}
.wr-good{color:#4ade80}.wr-mid{color:#f59e0b}.wr-low{color:#f87171}
.wr-fill-good{background:linear-gradient(90deg,#1a3a2a,#4ade80)}
.wr-fill-mid{background:linear-gradient(90deg,#3a2800,#f59e0b)}
.wr-fill-low{background:linear-gradient(90deg,#3a1a1a,#f87171)}

/* stat pills */
.card-stats{display:flex;gap:5px;padding:0 14px 8px;flex-wrap:wrap}
.stat-pill{background:#0e1621;border:1px solid #1a2535;border-radius:8px;padding:5px 8px;text-align:center;flex:1;min-width:58px}
.stat-pill-v{font-size:14px;font-weight:800;color:#e8e8ee}
.stat-pill-l{font-size:9px;color:#4a6070;letter-spacing:.5px;text-transform:uppercase;margin-top:1px}

/* radar + ability bars */
.card-body{display:flex;gap:10px;padding:4px 14px 10px;align-items:flex-start}
.card-radar{flex-shrink:0}
.dim-bars{flex:1;display:flex;flex-direction:column;gap:5px;padding-top:2px}
.dim-row{display:flex;align-items:center;gap:5px}
.dim-lbl{font-size:9px;color:#4a6070;width:42px;flex-shrink:0;text-align:right}
.dim-track{flex:1;height:5px;background:#0e1621;border-radius:3px;overflow:hidden;border:1px solid #1a2535}
.dim-fill{height:100%;border-radius:3px}
.dim-val{font-size:10px;font-weight:700;color:#7d8e99;width:22px;text-align:right;flex-shrink:0}
.dim-q{width:13px;height:13px;border-radius:50%;background:#1a2535;border:1px solid #2a3a4a;color:#4a6070;font-size:9px;cursor:pointer;padding:0;line-height:13px;text-align:center;flex-shrink:0;transition:all .15s}
.dim-q:hover{background:#2a3a4a;color:#e8e8ee}

/* quote */
.card-quote{margin:0 14px 8px;padding:7px 10px 7px 14px;background:#0e1621;border-radius:8px;border-left:3px solid var(--ac-dim);position:relative}
.card-quote::before{content:'❝';position:absolute;left:4px;top:4px;font-size:11px;color:var(--ac-dim)}
.card-quote-lbl{font-size:9px;color:#2a3a4a;font-weight:700;letter-spacing:.5px;text-transform:uppercase;margin-bottom:3px;display:block}
.card-quote-text{font-size:11px;color:#4a6070;font-style:italic;line-height:1.6}

/* mbti desc */
.card-desc{margin:0 14px 8px;padding:7px 10px;background:#0e1621;border-radius:8px;border-left:3px solid var(--ac-dim);font-size:11px;color:#4a6070;line-height:1.65}

/* traits */
.card-traits{display:flex;flex-wrap:wrap;gap:4px;padding:0 14px 10px}
.trait{font-size:10px;background:#0e1621;border:1px solid #1a2535;border-radius:4px;padding:2px 8px;color:#4a7080}

/* bet history */
.card-hist-wrap{margin:0 14px 10px;border:1px solid #1a2535;border-radius:8px;overflow:hidden}
.card-hist-hd{display:flex;justify-content:space-between;align-items:center;padding:7px 10px;background:#0e1621;border-bottom:1px solid #1a2535;font-size:11px;font-weight:700;color:#4a6070}
.hist-count{font-size:10px;color:#4a6070;background:#131c26;border-radius:10px;padding:1px 7px;border:1px solid #1a2535}
.card-hist-body{max-height:220px;overflow-y:auto;background:#0a1018}
.card-hist-body::-webkit-scrollbar{width:3px}
.card-hist-body::-webkit-scrollbar-thumb{background:#1e2d3d}
.hist-row{display:flex;align-items:center;gap:5px;padding:5px 10px;border-bottom:1px solid #0d1520;font-size:10px;cursor:help;transition:background .1s}
.hist-row:last-child{border-bottom:none}
.hist-row:hover{background:#131c26}
.hist-platform{color:#4a7080;min-width:44px;font-weight:700;flex-shrink:0}
.hist-cat{color:#2a3a4a;min-width:36px;flex-shrink:0;font-size:9px}
.hist-desc{color:#3a4a5a;flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-size:9px}
.hist-conf{color:#2a3a4a;min-width:22px;font-size:9px;flex-shrink:0}
.hist-amt{color:#4a6070;min-width:42px;text-align:right;flex-shrink:0;font-weight:600}
.hist-ts{color:#2a3a4a;min-width:72px;text-align:right;flex-shrink:0;font-size:9px}
.hist-empty{padding:12px;text-align:center;font-size:11px;color:#2a3a4a}

/* footer */
.card-footer{display:flex;gap:7px;padding:10px 14px 12px;border-top:1px solid #1a2535}
.card-mb-link{font-size:11px;padding:7px 11px;border-radius:8px;background:#0e1621;border:1px solid #1a2535;color:#4a7080;text-decoration:none;font-weight:600;transition:all .18s;display:flex;align-items:center;gap:5px}
.card-mb-link:hover{background:#182533;border-color:var(--ac-dim);color:var(--ac)}
.card-chat-btn{flex:1;font-size:12px;font-weight:700;padding:7px 12px;border-radius:8px;background:var(--ac-bg);border:1px solid var(--ac-dim);color:var(--ac);cursor:pointer;transition:all .18s;display:flex;align-items:center;justify-content:center;gap:6px}
.card-chat-btn:hover{background:var(--ac-hover);border-color:var(--ac);transform:translateY(-1px);box-shadow:0 3px 12px rgba(0,0,0,.4)}

/* ── CHAT ── */
#page-chat{display:none;flex-direction:column}
#page-chat.active{display:flex}
.chat-header{background:#17212b;border-bottom:1px solid #1a2535;padding:10px 16px;display:flex;align-items:center;gap:10px;flex-shrink:0;flex-wrap:wrap}
.chat-header label{font-size:12px;color:#4a6070;font-weight:600}
.persona-select{background:#131c26;border:1px solid #2a3a4a;color:#c8d4dc;border-radius:8px;padding:6px 10px;font-size:13px;cursor:pointer;font-family:inherit}
.persona-select:focus{outline:none;border-color:#b067f5}
.chat-persona-badge{display:flex;align-items:center;gap:6px;padding:4px 10px;border-radius:8px;font-size:12px;font-weight:700}
#chat-msgs{flex:1;overflow-y:auto;padding:12px 16px;display:flex;flex-direction:column;gap:2px;scroll-behavior:smooth}
#chat-msgs::-webkit-scrollbar{width:4px}
#chat-msgs::-webkit-scrollbar-thumb{background:#2b3b4a;border-radius:2px}
.cm{display:flex;gap:10px;animation:pop .2s ease}
.cm.user{flex-direction:row-reverse}
.cm-avatar{width:32px;height:32px;border-radius:50%;flex-shrink:0;display:flex;align-items:center;justify-content:center;font-size:16px}
.cm-bubble{max-width:68%;padding:8px 12px 6px;border-radius:14px;font-size:13px;line-height:1.55;word-break:break-word}
.cm.lobster .cm-avatar{background:#182533;border:2px solid var(--ac)}
.cm.lobster .cm-bubble{background:#182533;border-bottom-left-radius:4px;color:#d4dde6}
.cm.user .cm-avatar{background:#3d1a5a;border:2px solid #b067f5;font-size:12px;font-weight:800;color:#b067f5}
.cm.user .cm-bubble{background:#3d1a5a;border-bottom-right-radius:4px;color:#d4dde6}
.cm-name{font-size:11px;font-weight:800;color:var(--ac);margin-bottom:3px}
.cm-time{font-size:9px;color:#4a6070;text-align:right;margin-top:3px}
.chat-hist-count{font-size:11px;color:#4a6070;margin-left:auto}
.chat-input-row{padding:10px 16px;background:#17212b;border-top:1px solid #1a2535;display:flex;gap:8px;flex-shrink:0}
#chat-input{flex:1;background:#131c26;border:1px solid #2a3a4a;border-radius:10px;padding:9px 12px;color:#c8d4dc;font-size:13px;resize:none;min-height:40px;max-height:120px;font-family:inherit}
#chat-input:focus{outline:none;border-color:#b067f5}
#chat-send{background:#b067f5;color:#0e0618;border:none;border-radius:10px;padding:9px 16px;font-size:13px;font-weight:700;cursor:pointer;transition:all .18s;flex-shrink:0;font-family:inherit}
#chat-send:hover{background:#c584ff}
#chat-send:disabled{background:#1e2d3d;color:#4a6070;cursor:not-allowed}
.typing{display:flex;gap:4px;align-items:center;padding:8px 12px}
.typing span{width:6px;height:6px;border-radius:50%;background:#4a6070;animation:typing-dot 1.4s infinite}
.typing span:nth-child(2){animation-delay:.2s}
.typing span:nth-child(3){animation-delay:.4s}
@keyframes typing-dot{0%,80%,100%{transform:scale(.8);opacity:.4}40%{transform:scale(1);opacity:1}}
.chat-empty{flex:1;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:10px;color:#2a3a4a}
.chat-empty .big{font-size:48px}
.chat-empty p{font-size:13px;color:#4a6070}

/* ── PERSONA MODAL ── */
@keyframes modal-in{from{opacity:0;transform:translateY(20px) scale(.97)}to{opacity:1;transform:none}}
#persona-modal{position:fixed;inset:0;z-index:1000;display:none;align-items:flex-start;justify-content:center;padding:0;background:rgba(5,10,18,.88);backdrop-filter:blur(4px);overflow:hidden}
#persona-modal.open{display:flex}
.mp{position:relative;width:100%;max-width:900px;height:100vh;background:#111c28;display:flex;flex-direction:column;box-shadow:0 0 80px rgba(0,0,0,.7);border-left:1px solid #1e2d3d;border-right:1px solid #1e2d3d;animation:modal-in .22s ease}
.mp-close{position:absolute;top:14px;right:16px;background:none;border:none;color:#4a6070;font-size:22px;cursor:pointer;z-index:10;line-height:1;transition:color .15s}
.mp-close:hover{color:#e8e8ee}
.mp-head{display:flex;align-items:center;gap:14px;padding:18px 20px 14px;border-bottom:1px solid #1a2535;flex-shrink:0;background:linear-gradient(135deg,var(--mp-bg) 0%,#111c28 100%)}
.mp-icon{font-size:36px;width:56px;height:56px;border-radius:50%;background:var(--mp-bg);border:2px solid var(--mp-ac);display:flex;align-items:center;justify-content:center}
.mp-name{font-size:22px;font-weight:900;color:var(--mp-ac);letter-spacing:.5px}
.mp-mbti-row{display:flex;align-items:center;gap:8px;margin-top:3px}
.mp-mbti-chip{font-size:11px;font-weight:700;background:var(--mp-bg);border:1px solid var(--mp-ac);color:var(--mp-ac);border-radius:5px;padding:2px 8px;letter-spacing:.5px}
.mp-subtitle{font-size:11px;color:#4a6070;margin-top:2px}
.mp-wr-badge{margin-left:auto;text-align:center;background:#0e1621;border:1px solid #1a2535;border-radius:10px;padding:8px 14px}
.mp-wr-n{font-size:24px;font-weight:900;color:#e8e8ee}
.mp-wr-l{font-size:9px;color:#4a6070;text-transform:uppercase;letter-spacing:.5px;margin-top:1px}

.mp-tabs{display:flex;gap:0;border-bottom:1px solid #1a2535;background:#0e1621;flex-shrink:0;overflow-x:auto}
.mp-tab{padding:11px 16px;font-size:12px;font-weight:700;color:#4a6070;cursor:pointer;border-bottom:2px solid transparent;white-space:nowrap;transition:all .18s;flex-shrink:0}
.mp-tab:hover{color:#7d8e99;background:#111c28}
.mp-tab.active{color:var(--mp-ac);border-bottom-color:var(--mp-ac);background:#111c28}

.mp-body{flex:1;overflow-y:auto;padding:20px}
.mp-body::-webkit-scrollbar{width:4px}
.mp-body::-webkit-scrollbar-thumb{background:#1e2d3d;border-radius:2px}
.mp-panel{display:none}
.mp-panel.active{display:block}

/* overview tab */
.ov-grid{display:grid;grid-template-columns:1fr 1fr;gap:14px}
.ov-block{background:#0e1621;border:1px solid #1a2535;border-radius:10px;padding:14px 16px}
.ov-block-full{grid-column:1/-1}
.ov-title{font-size:10px;font-weight:700;color:#4a6070;text-transform:uppercase;letter-spacing:.6px;margin-bottom:10px}
.ov-quote{font-size:13px;font-style:italic;color:#8a9aaa;line-height:1.7;border-left:3px solid var(--mp-ac);padding-left:12px}
.ov-trait-row{display:flex;flex-wrap:wrap;gap:5px;margin-top:6px}
.ov-trait{font-size:10px;background:#182533;border:1px solid #1e2d3d;border-radius:4px;padding:2px 8px;color:#4a7080}
.ov-stat-row{display:flex;gap:8px;margin-bottom:8px}
.ov-stat{flex:1;background:#0a1018;border-radius:8px;padding:10px;text-align:center;border:1px solid #1a2535}
.ov-stat-v{font-size:20px;font-weight:900;color:var(--mp-ac)}
.ov-stat-l{font-size:9px;color:#4a6070;text-transform:uppercase;letter-spacing:.4px;margin-top:2px}

/* mbti deep tab */
.mbti-deep{font-size:13px;color:#9aa8b5;line-height:1.8}
.mbti-deep h2{font-size:14px;font-weight:800;color:var(--mp-ac);margin:18px 0 8px;padding-bottom:5px;border-bottom:1px solid #1a2535}
.mbti-deep h3{font-size:12px;font-weight:700;color:#7d8e99;margin:12px 0 6px;text-transform:uppercase;letter-spacing:.5px}
.mbti-deep p{margin-bottom:10px}
.mbti-deep em{color:#c8d4dc;font-style:normal;font-weight:600}

/* sociology report */
.socio-loading{text-align:center;padding:60px 20px;color:#4a6070}
.socio-loading-dot{display:inline-block;width:8px;height:8px;border-radius:50%;background:#4a6070;animation:socio-pulse 1s ease infinite}
.socio-loading-dot:nth-child(2){animation-delay:.2s}
.socio-loading-dot:nth-child(3){animation-delay:.4s}
@keyframes socio-pulse{0%,80%,100%{opacity:.2;transform:scale(.8)}40%{opacity:1;transform:scale(1)}}
.socio-meta{display:flex;gap:10px;flex-wrap:wrap;margin-bottom:20px;padding:12px 16px;background:#0a1018;border-radius:10px;border:1px solid #1a2535}
.socio-meta-item{font-size:10px;color:#4a6070;display:flex;flex-direction:column;align-items:center;gap:3px;flex:1;min-width:70px}
.socio-meta-v{font-size:16px;font-weight:900;color:#e8e8ee}
.socio-report{font-size:13px;line-height:1.85;color:#9aa8b5}
.socio-section{margin-bottom:28px;padding-bottom:20px;border-bottom:1px solid #1a2535}
.socio-section:last-child{border-bottom:none}
.socio-sec-num{font-size:10px;font-weight:700;color:var(--mp-ac);text-transform:uppercase;letter-spacing:.8px;margin-bottom:4px}
.socio-sec-title{font-size:15px;font-weight:900;color:#e8e8ee;margin-bottom:14px}
.socio-zh{background:#0e1621;border-left:3px solid var(--mp-ac);border-radius:0 8px 8px 0;padding:14px 16px;margin-bottom:10px;color:#c8d4dc;line-height:1.9;font-size:13px}
.socio-en{background:#0a1018;border-left:3px solid #1e2d3d;border-radius:0 8px 8px 0;padding:12px 16px;color:#4a6070;font-size:12px;font-style:italic;line-height:1.85}
.socio-data-tag{display:inline-block;font-size:10px;font-weight:700;background:var(--mp-bg);color:var(--mp-ac);border:1px solid var(--mp-ac);border-radius:4px;padding:1px 6px;margin:0 3px;white-space:nowrap;font-style:normal}
.socio-error{color:#f87171;text-align:center;padding:40px;font-size:13px}
.socio-gen-btn{display:block;margin:30px auto;padding:12px 28px;background:var(--mp-bg);border:1px solid var(--mp-ac);color:var(--mp-ac);border-radius:10px;font-size:13px;font-weight:700;cursor:pointer;transition:all .18s}
.socio-gen-btn:hover{background:var(--mp-ac);color:#0e0618}

/* bets tab reuse */
.mp-bets{background:#0a1018;border-radius:10px;border:1px solid #1a2535;overflow:hidden}
.mp-bets-hd{padding:10px 14px;background:#0e1621;border-bottom:1px solid #1a2535;font-size:11px;font-weight:700;color:#4a6070}
.mp-bets-body{max-height:calc(100vh - 300px);overflow-y:auto}

/* ── growth analysis tab ── */
.gr-header{display:flex;align-items:center;gap:10px;margin-bottom:16px;flex-wrap:wrap}
.gr-range-btns{display:flex;gap:4px;flex-wrap:wrap}
.gr-btn{padding:5px 12px;font-size:11px;font-weight:700;border:1px solid #1e2d4d;border-radius:6px;background:#0e1621;color:#4a6070;cursor:pointer;transition:all .15s}
.gr-btn:hover{border-color:#4477cc;color:#88aaee}
.gr-btn.active{border-color:var(--mp-ac);background:var(--mp-ac,#4477cc)22;color:var(--mp-ac)}
.gr-lang-btn{margin-left:auto;padding:4px 10px;font-size:10px;border:1px solid #1e2d4d;border-radius:6px;background:#0d1220;color:#4a6070;cursor:pointer}
.gr-lang-btn:hover{border-color:#4a6070;color:#7d8e99}
.gr-grid{display:grid;grid-template-columns:1fr 1fr;gap:14px;margin-bottom:14px}
.gr-card{background:#0e1621;border:1px solid #1a2535;border-radius:10px;padding:14px}
.gr-card-full{grid-column:1/-1}
.gr-card-title{font-size:10px;font-weight:700;color:#4a6070;text-transform:uppercase;letter-spacing:.6px;margin-bottom:12px;display:flex;align-items:center;gap:6px}
.gr-card-title .gr-badge{font-size:9px;font-weight:700;padding:1px 6px;border-radius:4px}
.gr-badge-start{background:#1e3a2a;color:#4ade80;border:1px solid #2a5040}
.gr-badge-now{background:var(--mp-bg);color:var(--mp-ac);border:1px solid var(--mp-ac)}
.gr-radars{display:flex;gap:24px;align-items:center;justify-content:center}
.gr-radar-wrap{text-align:center}
.gr-radar-label{font-size:10px;color:#4a6070;margin-top:6px}
.gr-delta-table{width:100%;border-collapse:collapse}
.gr-delta-table th{font-size:9px;font-weight:700;color:#4a6070;text-transform:uppercase;letter-spacing:.5px;padding:6px 8px;text-align:left;border-bottom:1px solid #1a2535}
.gr-delta-table td{padding:8px 8px;border-bottom:1px solid #0d1220;font-size:12px}
.gr-delta-table tr:last-child td{border-bottom:none}
.gr-dim-name{font-weight:700;color:#8a9aaa}
.gr-dim-en{font-size:10px;color:#4a6070;display:block;margin-top:1px}
.gr-val-start{color:#4a6070}
.gr-val-arrow{color:#445566;font-size:10px;padding:0 4px}
.gr-val-now{font-weight:700;color:#c8d4dc}
.gr-delta-pos{color:#4ade80;font-weight:700}
.gr-delta-neg{color:#f87171;font-weight:700}
.gr-delta-flat{color:#4a6070}
.gr-calc-note{font-size:10px;color:#3a5060;margin-top:4px;line-height:1.5;display:block}
.gr-chart-wrap{position:relative;height:220px;margin-top:4px}
.gr-timeline{list-style:none;padding:0;margin:0}
.gr-timeline-item{padding:8px 0;border-bottom:1px solid #0d1220;display:flex;gap:10px;align-items:flex-start}
.gr-timeline-item:last-child{border-bottom:none}
.gr-tl-time{font-size:9px;color:#3a5060;white-space:nowrap;min-width:80px;padding-top:2px}
.gr-tl-trigger{font-size:9px;font-weight:700;padding:1px 6px;border-radius:4px;background:#111827;color:#4a6070;white-space:nowrap;flex-shrink:0}
.gr-tl-dims{display:flex;flex-wrap:wrap;gap:4px;flex:1}
.gr-tl-dim{font-size:10px;padding:2px 7px;border-radius:4px;border:1px solid #1e2d4d}
.gr-tl-dim.up{background:#0a2a1a;border-color:#1a4a2a;color:#4ade80}
.gr-tl-dim.dn{background:#2a0a0a;border-color:#4a1a1a;color:#f87171}
.gr-tl-dim.flat{background:#111827;border-color:#1e2d4d;color:#3a5060}
.gr-empty{text-align:center;padding:40px;color:#3a5060;font-size:12px}
.gr-methodology{background:#0a1018;border:1px solid #1a2535;border-radius:10px;padding:14px;margin-top:14px}
.gr-meth-title{font-size:10px;font-weight:700;color:#4a6070;text-transform:uppercase;letter-spacing:.6px;margin-bottom:10px}
.gr-meth-row{padding:8px 0;border-bottom:1px solid #0d1220;display:grid;grid-template-columns:140px 1fr;gap:12px;align-items:start}
.gr-meth-row:last-child{border-bottom:none}
.gr-meth-dim{font-size:11px;font-weight:700;color:var(--mp-ac)}
.gr-meth-desc{font-size:11px;color:#5a7080;line-height:1.6}

/* local brain tab */
.lb-grid{display:grid;grid-template-columns:1fr 1fr;gap:14px}
.lb-card{background:#0e1621;border:1px solid #1a2535;border-radius:10px;padding:14px}
.lb-card-full{grid-column:1/-1}
.lb-title{font-size:10px;font-weight:800;color:#4a6070;text-transform:uppercase;letter-spacing:.6px;margin-bottom:12px;display:flex;justify-content:space-between;gap:8px}
.lb-pill{font-size:9px;color:var(--mp-ac);background:var(--mp-bg);border:1px solid var(--mp-ac);border-radius:4px;padding:1px 6px;text-transform:none;letter-spacing:0}
.lb-meter{display:grid;grid-template-columns:128px 1fr 38px;gap:8px;align-items:center;margin:9px 0}
.lb-meter-l{font-size:11px;color:#7d8e99;font-weight:700}
.lb-track{height:7px;background:#081018;border:1px solid #162333;border-radius:4px;overflow:hidden}
.lb-fill{height:100%;background:var(--mp-ac);border-radius:4px}
.lb-meter-v{font-size:11px;color:#c8d4dc;text-align:right;font-weight:800}
.lb-list{display:flex;flex-direction:column;gap:7px}
.lb-row{display:grid;grid-template-columns:1fr 48px;gap:10px;align-items:center;padding:7px 0;border-bottom:1px solid #0d1220}
.lb-row:last-child{border-bottom:none}
.lb-row-k{font-size:11px;color:#8a9aaa;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.lb-row-v{font-size:11px;color:#c8d4dc;text-align:right;font-weight:800}
.lb-guidance{font-size:12px;color:#8a9aaa;line-height:1.75;padding:8px 0;border-bottom:1px solid #0d1220}
.lb-guidance:last-child{border-bottom:none}
.lb-event{display:grid;grid-template-columns:130px 90px 1fr;gap:10px;align-items:start;padding:8px 0;border-bottom:1px solid #0d1220}
.lb-event:last-child{border-bottom:none}
.lb-event-time{font-size:10px;color:#3a5060}
.lb-event-trigger{font-size:10px;color:var(--mp-ac);font-weight:800}
.lb-event-text{font-size:11px;color:#7d8e99;line-height:1.5}
.lb-empty{text-align:center;color:#3a5060;font-size:12px;padding:34px 10px}
</style>
</head>
<body>
<header>
  <div class="logo">🦞</div>
  <div><div class="title">LOBSTER LOUNGE</div><div class="sub">Battle Feed · MBTI Dashboard · One-on-One</div></div>
  <div class="live-dot" title="live"></div>
</header>
<nav>
  <div class="tab active" onclick="switchTab('feed')">🗡️ Battle Feed</div>
  <div class="tab" onclick="switchTab('dash')">🧠 Dashboard</div>
  <div class="tab" onclick="switchTab('chat')">💬 一對一對話</div>
</nav>

<!-- FEED -->
<div class="page active" id="page-feed">
  <div id="log"><div class="sys">— Lounge open — lobsters will appear after battles —</div></div>
</div>

<!-- DASHBOARD -->
<div class="page" id="page-dash">
  <div id="dash">
    <div class="dash-header">
      <h2>🧠 MBTI 龍蝦人格分析</h2>
      <span>10 隻 × 心理學 × FBI 側寫</span>
    </div>
    <div class="grid" id="persona-grid"></div>
  </div>
</div>

<!-- PERSONA MODAL -->
<div id="persona-modal">
  <div class="mp" id="mp-panel">
    <button class="mp-close" onclick="closePersonaModal()">&#10005;</button>
    <div class="mp-head" id="mp-head"></div>
    <div class="mp-tabs" id="mp-tabs">
      <div class="mp-tab active" data-tab="overview" onclick="switchMpTab('overview')">總覽</div>
      <div class="mp-tab" data-tab="mbti" onclick="switchMpTab('mbti')">MBTI 深度</div>
      <div class="mp-tab" data-tab="bets" onclick="switchMpTab('bets')">下注紀錄</div>
      <div class="mp-tab" data-tab="socio" onclick="switchMpTab('socio')">社會學報告</div>
      <div class="mp-tab" data-tab="growth" onclick="switchMpTab('growth')">📈 成長分析</div>
      <div class="mp-tab" data-tab="localbrain" onclick="switchMpTab('localbrain')">🧠 本地腦</div>
      <div class="mp-tab" data-tab="chat" onclick="switchMpTab('chat')">一對一</div>
    </div>
    <div class="mp-body" id="mp-body">
      <div class="mp-panel active" id="mp-overview"></div>
      <div class="mp-panel" id="mp-mbti"></div>
      <div class="mp-panel" id="mp-bets"></div>
      <div class="mp-panel" id="mp-socio"></div>
      <div class="mp-panel" id="mp-growth"></div>
      <div class="mp-panel" id="mp-localbrain"></div>
      <div class="mp-panel" id="mp-chat-redirect"></div>
    </div>
  </div>
</div>

<!-- CHAT -->
<div class="page" id="page-chat">
  <div class="chat-header">
    <label>選擇龍蝦：</label>
    <select class="persona-select" id="chat-select" onchange="selectPersona(this.value)">
      <option value="">— Select a lobster —</option>
    </select>
    <div id="chat-persona-badge" style="display:none"></div>
    <div style="margin-left:auto;font-size:11px;color:#334" id="chat-hist-count"></div>
  </div>
  <div id="chat-msgs"><div class="chat-empty"><div class="big">🔒</div><p>Select a lobster to start an owner-only one-on-one chat.</p></div></div>
  <div class="chat-input-row">
    <textarea id="chat-input" placeholder="Owner-only one-on-one chat..." maxlength="1200" rows="1" oninput="autoResize(this)" onkeydown="handleKey(event)"></textarea>
    <button id="chat-send" onclick="sendChat()" disabled>Send</button>
  </div>
</div>

<script>
const BASE = location.pathname.startsWith('/lobster-lounge') ? '/lobster-lounge' : location.pathname.startsWith('/lounge') ? '/lounge' : '';
function getOneOnOneAuthToken() {
  return localStorage.getItem('lobsterEvaluationGateToken') || localStorage.getItem('access_token') || localStorage.getItem('charenixToken') || localStorage.getItem('token') || '';
}
const AC={snaplex:'#c084fc',ragclaw:'#a3e635',pincerbot:'#f97316',hexclaw:'#38bdf8',clawtrix:'#818cf8',norika_oda:'#fbbf24',vortexiq:'#fb7185',prismbit:'#2dd4bf',blazepaw:'#ff6b35',stonefang:'#94a3b8'};
const EI={fire:'🔥',water:'💧',wind:'🌪️',earth:'⛰️','?':'❓'};

let currentChatPersona = null;
let chatHistory = {};

// Tooltip
let _tt = null;
function showTip(el, html) {
  if(_tt) _tt.remove();
  _tt = document.createElement('div');
  _tt.style.cssText = 'position:fixed;z-index:999;background:#0f0f28;border:1px solid #3a3a60;border-radius:10px;padding:12px 14px;font-size:12px;color:#aab;line-height:1.6;max-width:300px;box-shadow:0 8px 32px #000a';
  _tt.innerHTML = html;
  document.body.appendChild(_tt);
  const r = el.getBoundingClientRect();
  const tw = 300, th = 160;
  let left = r.left, top = r.bottom + 6;
  if(left + tw > window.innerWidth - 12) left = window.innerWidth - tw - 12;
  if(top + th > window.innerHeight - 12) top = r.top - th - 6;
  _tt.style.left = left + 'px';
  _tt.style.top = top + 'px';
  setTimeout(() => document.addEventListener('click', removeTip, {once:true}), 0);
}
function removeTip() { if(_tt){ _tt.remove(); _tt=null; } }

// ── 雷達圖 SVG 生成 ────────────────────────────────────────────
const RADAR_LABELS = ['分析力','侵略性','適應性','情緒穩定','自我覺察'];
function radarSVG(vals, color, size=110) {
  const cx = size/2, cy = size/2, r = size*0.38;
  const n = vals.length;
  function pt(i, v) {
    const a = (Math.PI*2/n)*i - Math.PI/2;
    const rv = r * (v/10);
    return [cx + rv*Math.cos(a), cy + rv*Math.sin(a)];
  }
  // grid rings
  let rings = '';
  for(let g=2;g<=10;g+=2){
    const pts = Array.from({length:n},(_,i)=>pt(i,g).join(',')).join(' ');
    rings += \`<polygon points="\${pts}" fill="none" stroke="rgba(255,255,255,.07)" stroke-width="1"/>\`;
  }
  // axes
  let axes = '';
  for(let i=0;i<n;i++){
    const [x,y]=pt(i,10);
    axes += \`<line x1="\${cx}" y1="\${cy}" x2="\${x}" y2="\${y}" stroke="rgba(255,255,255,.06)" stroke-width="1"/>\`;
  }
  // data shape
  const dpts = vals.map((v,i)=>pt(i,v).join(',')).join(' ');
  const dataShape = \`<polygon points="\${dpts}" fill="\${color}22" stroke="\${color}" stroke-width="1.5" stroke-linejoin="round"/>\`;
  // dots
  const dots = vals.map((v,i)=>{const[x,y]=pt(i,v);return \`<circle cx="\${x}" cy="\${y}" r="2.5" fill="\${color}" stroke="rgba(0,0,0,.6)" stroke-width="1.5"/>\`;}).join('');
  // labels
  const lbls = RADAR_LABELS.map((lb,i)=>{
    const [x,y]=pt(i,10.8);
    const anchor = x<cx-2?'end':x>cx+2?'start':'middle';
    return \`<text x="\${x}" y="\${y+3}" text-anchor="\${anchor}" font-size="8" fill="#556" font-family="system-ui">\${lb}</text>\`;
  }).join('');
  return \`<svg width="\${size}" height="\${size}" viewBox="0 0 \${size} \${size}">\${rings}\${axes}\${dataShape}\${dots}\${lbls}</svg>\`;
}

// ── 龍蝦資料（從 server fetch）────────────────────────────────
let PERSONAS = {};

async function init() {
  initFeed();
  try {
    const data = await fetch(BASE+'/personas').then(r=>r.json());
    PERSONAS = data;
    renderDash();
    populateChatSelect();
  } catch (e) {
    console.error('[lobster-lounge] dashboard init failed; feed remains active', e);
  }
}

// ── FEED ──────────────────────────────────────────────────────
const log = document.getElementById('log');
let autoScroll = true;
log.addEventListener('scroll', ()=>{ autoScroll = log.scrollTop+log.clientHeight >= log.scrollHeight-40; });

function ts(iso){ const d=new Date(iso); return d.toLocaleTimeString('zh-TW',{hour:'2-digit',minute:'2-digit',second:'2-digit',hour12:false}); }

function hadesEffect(){let v=document.getElementById('hades-veil');if(!v){v=document.createElement('div');v.id='hades-veil';document.body.appendChild(v);}v.classList.add('on');document.body.classList.add('hades-quake');clearTimeout(window._hadesT);window._hadesT=setTimeout(function(){v.classList.remove('on');document.body.classList.remove('hades-quake');},6000);}
function addMsg(m) {
  if(m.sys){const _hd=m.hint&&String(m.hint).indexOf('hades')===0;const d=document.createElement('div');d.className=_hd?'sys hades':'sys';const sp=document.createElement('span');sp.textContent=(_hd?'\u2620 \u54c8\u5e1d\u65af \u2620\u3000':'')+m.text;d.appendChild(sp);log.appendChild(d);if(_hd&&typeof hadesEffect==='function')hadesEffect();if(autoScroll)log.scrollTop=log.scrollHeight;return;}
  const color=AC[m.name]||'#c084fc';
  const ei=EI[m.elem||'?']||'❓';
  const bCls=m.result==='won'?'win':m.result==='lost'?'loss':m.result==='bet'?'bet':m.result==='skip'?'skip':'draw';
  const bTxt=m.result==='won'?'WIN':m.result==='lost'?'LOSS':m.result==='bet'?'BET':m.result==='skip'?'PASS':'DRAW';
  const vsHtml=m.opponent?'<span class="vs">vs <em>'+m.opponent+'</em></span>':'';
  const hintHtml=m.hint?'<div class="hint">'+m.hint+'</div>':'';
  const d=document.createElement('div');
  d.className='msg'; d.style.setProperty('--ac',color);
  d.innerHTML='<div class="ava" style="background:'+color+'18">'+ei+'</div>'
    +'<div class="body"><div class="meta">'
    +'<span class="name">'+m.name+'</span>'
    +'<span class="badge '+bCls+'">'+bTxt+'</span>'
    +vsHtml+'<span class="time">'+ts(m.time)+'</span>'
    +'</div><div class="text">'+m.text+'</div>'+hintHtml+'</div>';
  log.appendChild(d);
  if(autoScroll) log.scrollTop=log.scrollHeight;
}

let feedStarted = false;
function initFeed() {
  if (feedStarted) return;
  feedStarted = true;
  fetch(BASE+'/history?limit=120&channel='+encodeURIComponent('卡牌對戰'), { cache:'no-store' })
    .then(r=>r.json())
    .then(msgs=>{
      if (Array.isArray(msgs) && msgs.length) log.innerHTML = '';
      msgs.forEach(addMsg);
      log.scrollTop=log.scrollHeight;
    })
    .catch(e=>console.error('[lobster-lounge] history load failed', e));
  const es=new EventSource(BASE+'/stream');
  es.onmessage=e=>{addMsg(JSON.parse(e.data));};
}

// ── DASH ──────────────────────────────────────────────────────
const MEM_DATA = {};

async function fetchMemory() {
  try {
    const r = await fetch(BASE+'/memory-stats');
    const d = await r.json();
    Object.assign(MEM_DATA, d);
  } catch {}
}

// ── 6 能力值計算 ──────────────────────────────────────────────
const ABILITY_DEFS = [
  { key:'認知力', tip:'<b>認知力</b><br>= 分析力 × 10<br>衡量龍蝦對數據的解讀與推理能力<br><span style="color:#556">來源：雷達圖「分析力」維度</span>' },
  { key:'學習力', tip:'<b>學習力</b><br>= 適應性 × 10<br>衡量龍蝦在新局面下的應變學習能力<br><span style="color:#556">來源：雷達圖「適應性」維度</span>' },
  { key:'策略力', tip:'<b>策略力</b><br>= 勝率（0-100）<br>以實際戰績衡量策略執行能力<br><span style="color:#556">來源：memory 勝場/總場數</span>' },
  { key:'思考力', tip:'<b>思考力</b><br>= 情緒穩定 × 10<br>衡量在壓力下保持清醒決策的能力<br><span style="color:#556">來源：雷達圖「情緒穩定」維度</span>' },
  { key:'情報力', tip:'<b>情報力</b><br>= 自我覺察 × 10<br>對自身偏誤與信息品質的識別能力<br><span style="color:#556">來源：雷達圖「自我覺察」維度</span>' },
  { key:'競爭力', tip:'<b>競爭力</b><br>= 侵略性 × agg係數 × 0.6 + 勝率 × 0.4<br>綜合進攻意圖與勝場表現<br><span style="color:#556">來源：雷達圖「侵略性」＋ 侵略係數 ＋ 勝率</span>' },
];
function calcAbilities(p, wr, memRadarDynamic) {
  // Use live radarDynamic (10-100 scale) if available, else static radar (0-10)
  const r = memRadarDynamic
    ? memRadarDynamic.map(v => v / 10)
    : (p.radar||[5,5,5,5,5]);
  return [
    Math.min(100, Math.round(r[0]*10)),
    Math.min(100, Math.round(r[2]*10)),
    Math.min(100, wr),
    Math.min(100, Math.round(r[3]*10)),
    Math.min(100, Math.round(r[4]*10)),
    Math.min(100, Math.round(r[1]*p.agg*0.6 + wr*0.4)),
  ];
}

function dimBars(abilities, color) {
  return ABILITY_DEFS.map((d,i) => {
    const v = abilities[i];
    return \`<div class="dim-row">
      <span class="dim-lbl">\${d.key}</span>
      <div class="dim-track"><div class="dim-fill" style="width:\${v}%;background:\${color}"></div></div>
      <span class="dim-val">\${v}</span>
      <button class="dim-q" onclick="event.stopPropagation();showTip(this,'\${d.tip.replace(/'/g,'&#39;')}')">?</button>
    </div>\`;
  }).join('');
}

function renderBetHistory(bets) {
  if(!bets||!bets.length) return '<div class="hist-empty">尚無下注記錄</div>';
  return bets.slice(-50).reverse().map(b => {
    const res = b.outcome||b.result||'pending';
    const cls = res==='win'||res==='won'?'win':res==='loss'||res==='lost'?'loss':res===null?'skip':'draw';
    const label = res==='win'||res==='won'?'WIN':res==='loss'||res==='lost'?'LOSS':res===null?'—':'OPEN';
    const site = (b.site||b.platform||'').replace('.com','');
    const cat = (b.category||'').replace('card_battle_','').replace('_',' ');
    const amt = (b.stake||b.amount||0).toLocaleString();
    const conf = b.confidence ? b.confidence.toUpperCase().slice(0,3) : '';
    const ts = b.time||b.timestamp ? new Date(b.time||b.timestamp).toLocaleDateString('zh-TW',{month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit'}) : '';
    const reasoning = (b.reasoning||'').replace(/PLAN:|RESULT:|PRE-PLAN:/g,'').slice(0,60);
    return \`<div class="hist-row" title="\${(b.reasoning||'').replace(/"/g,'&quot;')}">
      <span class="badge \${cls}" style="font-size:9px">\${label}</span>
      <span class="hist-platform">\${site}</span>
      <span class="hist-cat">\${cat}</span>
      <span class="hist-desc">\${reasoning}</span>
      <span class="hist-conf">\${conf}</span>
      <span class="hist-amt">\${amt}</span>
      <span class="hist-ts">\${ts}</span>
    </div>\`;
  }).join('');
}

function renderDash() {
  fetchMemory().then(() => {
    const grid = document.getElementById('persona-grid');
    grid.innerHTML = '';
    const order = ['elder','norika_oda','clawtrix','pincerbot','ragclaw','snaplex','hexclaw','vortexiq','prismbit','blazepaw','stonefang'];
    order.forEach((name, idx) => {
      const p = PERSONAS[name];
      if(!p) return;
      const mem = MEM_DATA[name] || {};
      const wins = mem.wins||0, losses = mem.losses||0;
      const total = wins+losses;
      const wr = total>0 ? Math.round(wins/total*100) : 0;
      const wrCls = wr>=45?'wr-good':wr>=37?'wr-mid':'wr-low';
      const wrFillCls = wr>=45?'wr-fill-good':wr>=37?'wr-fill-mid':'wr-fill-low';
      const radar = Array.isArray(mem.radarDynamic) && mem.radarDynamic.length >= 5
        ? mem.radarDynamic.slice(0,5).map(v => Math.max(1, Math.min(10, Number(v) / 10)))
        : (p.radar||[5,5,5,5,5]);
      const cardNum = String(idx+1).padStart(2,'0');
      const abilities = calcAbilities(p, wr, mem.radarDynamic);
      const bets = mem.bets_list || mem.bets || [];
      const totalBets = typeof mem.bets === 'number' ? mem.bets : bets.length;

      const card = document.createElement('div');
      card.className = 'card';
      card.style.setProperty('--ac', p.color);
      card.style.setProperty('--i', idx);
      card.style.cursor = 'pointer';
      card.addEventListener('click', () => openPersonaDetail(name));
      card.innerHTML = \`
<div class="card-stripe"></div>
<div class="card-head">
  <div class="card-num">\${cardNum}</div>
  <div class="card-icon-box">\${p.elemIcon}</div>
  <div class="card-title">
    <div class="card-name-row">
      <span class="card-name">\${name}</span>
      <span class="card-sport-tag">\${p.sport}</span>
    </div>
    <div class="card-specialty">\${p.specialty}</div>
  </div>
</div>
<div class="card-mbti-row">
  <div class="mbti-chip">
    <span class="mbti-type">\${p.mbti}</span>
    <span class="mbti-sep">·</span>
    <span class="mbti-zh">\${p.mbtiName}</span>
    <span class="mbti-en">\${p.mbtiEn}</span>
  </div>
</div>
<div class="wr-section">
  <div class="wr-bar-wrap">
    <span class="wr-label">勝率</span>
    <div class="wr-track"><div class="wr-fill \${wrFillCls}" style="width:\${wr}%"></div></div>
    <span class="wr-val \${wrCls}">\${wr}%</span>
  </div>
</div>
<div class="card-stats">
  <div class="stat-pill"><div class="stat-pill-v">\${wins}</div><div class="stat-pill-l">勝場</div></div>
  <div class="stat-pill"><div class="stat-pill-v">\${losses}</div><div class="stat-pill-l">敗場</div></div>
  <div class="stat-pill"><div class="stat-pill-v">\${total}</div><div class="stat-pill-l">總對戰</div></div>
  <div class="stat-pill"><div class="stat-pill-v">\${totalBets}</div><div class="stat-pill-l">下注次</div></div>
  <div class="stat-pill"><div class="stat-pill-v">\${p.agg}/10</div><div class="stat-pill-l">侵略性</div></div>
</div>
<div class="card-body">
  <div class="card-radar">\${radarSVG(radar, p.color, 120)}</div>
  <div class="dim-bars">\${dimBars(abilities, p.color)}</div>
</div>
<div class="card-quote">
  <span class="card-quote-lbl">最具代表</span>
  <div class="card-quote-text">\${p.signature}</div>
</div>
<div class="card-desc">\${p.mbtiDesc}</div>
<div class="card-traits">\${p.traits.map(t=>\`<span class="trait">\${t}</span>\`).join('')}</div>
<div class="card-hist-wrap">
  <div class="card-hist-hd">
    <span>📋 下注紀錄</span>
    <span class="hist-count">\${bets.length || totalBets} 筆</span>
  </div>
  <div class="card-hist-body">\${renderBetHistory(Array.isArray(bets)?bets:[])}</div>
</div>
<div class="card-footer">
  <a class="card-mb-link" href="https://www.moltbook.com/@\${name}" target="_blank" onclick="event.stopPropagation()">🦙 Moltbook</a>
  <button class="card-chat-btn" onclick="goToChat('\${name}')">💬 One on One</button>
</div>
      \`;
      grid.appendChild(card);
    });
  });
}

function goToChat(name) {
  switchTab('chat');
  document.getElementById('chat-select').value = name;
  selectPersona(name);
}

// ── PERSONA MODAL ─────────────────────────────────────────────
let _mpCurrentName = null;
let _mpCurrentTab  = 'overview';
let _socioCache    = {};   // name -> {html, ts}

function openPersonaDetail(name) {
  const p   = PERSONAS[name];
  const mem = MEM_DATA[name] || {};
  if (!p) return;
  _mpCurrentName = name;
  _mpCurrentTab  = 'overview';

  const wins = mem.wins||0, losses = mem.losses||0, total = wins+losses;
  const wr   = total > 0 ? Math.round(wins/total*100) : 0;
  const wrCls = wr>=45?'wr-good':wr>=37?'wr-mid':'wr-low';

  const panel = document.getElementById('mp-panel');
  panel.style.setProperty('--mp-ac', p.color);
  panel.style.setProperty('--mp-bg', p.color+'18');

  // Header
  document.getElementById('mp-head').innerHTML = \`
    <div class="mp-icon">\${p.elemIcon}</div>
    <div>
      <div class="mp-name">\${name}</div>
      <div class="mp-mbti-row">
        <span class="mp-mbti-chip">\${p.mbti}</span>
        <span style="font-size:12px;color:#7d8e99">\${p.mbtiName} · \${p.mbtiEn}</span>
      </div>
      <div class="mp-subtitle">\${p.specialty} · \${p.sport}</div>
    </div>
    <div class="mp-wr-badge" style="margin-left:auto">
      <div class="mp-wr-n \${wrCls}">\${wr}%</div>
      <div class="mp-wr-l">勝率</div>
      <div style="font-size:10px;color:#4a6070;margin-top:2px">\${wins}W \${losses}L</div>
    </div>
  \`;

  // Reset tabs
  document.querySelectorAll('.mp-tab').forEach(t => t.classList.toggle('active', t.dataset.tab==='overview'));
  document.querySelectorAll('.mp-panel').forEach(t => t.classList.remove('active'));
  document.getElementById('mp-overview').classList.add('active');

  // Populate overview
  const bets   = Array.isArray(mem.bets_list) ? mem.bets_list : [];
  const topCtx = Object.entries(mem.ctx||{})
    .filter(([k,v]) => !k.includes('unknown') && v.wins+v.losses >= 3)
    .sort((a,b) => (b[1].wins/(b[1].wins+b[1].losses)) - (a[1].wins/(a[1].wins+a[1].losses)))
    .slice(0,4)
    .map(([k,v]) => {
      const r = Math.round(v.wins/(v.wins+v.losses)*100);
      const cls = r>=50?'wr-good':r>=35?'wr-mid':'wr-low';
      return \`<div class="ov-stat"><div class="ov-stat-v \${cls}">\${r}%</div><div class="ov-stat-l">\${k.replace('_vs_',' vs ')}</div></div>\`;
    }).join('');

  const insights = (mem.insights||[]).slice(-3).reverse();

  document.getElementById('mp-overview').innerHTML = \`
<div class="ov-grid">
  <div class="ov-block ov-block-full">
    <div class="ov-title">代表語錄</div>
    <div class="ov-quote">\${p.signature}</div>
    <div class="ov-trait-row">\${p.traits.map(t=>\`<span class="ov-trait">\${t}</span>\`).join('')}</div>
  </div>
  <div class="ov-block">
    <div class="ov-title">戰績數據</div>
    <div class="ov-stat-row">
      <div class="ov-stat"><div class="ov-stat-v">\${wins}</div><div class="ov-stat-l">勝場</div></div>
      <div class="ov-stat"><div class="ov-stat-v">\${losses}</div><div class="ov-stat-l">敗場</div></div>
      <div class="ov-stat"><div class="ov-stat-v">\${mem.sessions||0}</div><div class="ov-stat-l">Sessions</div></div>
    </div>
    \${topCtx ? \`<div class="ov-title" style="margin-top:10px">元素配對勝率</div><div class="ov-stat-row">\${topCtx}</div>\` : ''}
  </div>
  <div class="ov-block">
    <div class="ov-title">雷達圖 \${mem.radarDynamic ? '<span style="font-size:10px;color:#3a7060;background:#0a2520;padding:1px 5px;border-radius:3px;margin-left:4px">動態</span>' : ''}</div>
    \${(()=>{
      const DYNAMIC_LABELS = ['分析力','冒險性','適應力','社交影響','一致性'];
      const savedLabels = RADAR_LABELS.slice();
      if (mem.radarDynamic) {
        RADAR_LABELS.splice(0, 5, ...DYNAMIC_LABELS);
        const dynVals = mem.radarDynamic.map(v => Math.max(1, Math.min(10, v/10)));
        const svg = radarSVG(dynVals, p.color, 140);
        RADAR_LABELS.splice(0, 5, ...savedLabels);
        return svg;
      }
      return radarSVG(p.radar||[5,5,5,5,5], p.color, 140);
    })()}
  </div>
  <div class="ov-block ov-block-full">
    <div class="ov-title">人格描述</div>
    <div style="font-size:12px;color:#7d8e99;line-height:1.8">\${p.mbtiDesc}</div>
  </div>
  \${insights.length > 0 ? \`<div class="ov-block ov-block-full">
    <div class="ov-title">最新洞察</div>
    \${insights.map(s=>\`<div style="font-size:12px;color:#5a7080;padding:5px 0;border-bottom:1px solid #1a2535">\${s}</div>\`).join('')}
  </div>\` : ''}
  \${mem.cogProfile ? \`<div class="ov-block ov-block-full">
    <div class="ov-title">認知成長紀錄 <span style="font-size:10px;color:#4a6070;margin-left:6px">上次分析: \${mem.cogProfile.lastHourlyAnalysis ? new Date(mem.cogProfile.lastHourlyAnalysis).toLocaleTimeString() : '未開始'}</span></div>
    \${mem.cogProfile.principles.length ? \`<div style="margin-bottom:8px"><span style="font-size:10px;color:#3a7060;font-weight:600">核心原則</span><div style="margin-top:4px">\${mem.cogProfile.principles.map(s=>\`<div style="font-size:11px;color:#6a8a9a;padding:2px 0">▸ \${s}</div>\`).join('')}</div></div>\` : ''}
    \${mem.cogProfile.reflections.length ? \`<div style="margin-bottom:8px"><span style="font-size:10px;color:#3a6a80;font-weight:600">近期反思</span><div style="margin-top:4px">\${mem.cogProfile.reflections.map(s=>\`<div style="font-size:11px;color:#5a7a8a;padding:2px 0;font-style:italic">「\${s}」</div>\`).join('')}</div></div>\` : ''}
    \${mem.cogProfile.likes.length||mem.cogProfile.dislikes.length ? \`<div><span style="font-size:10px;color:#4a7060;font-weight:600">喜好 / 迴避</span><div style="margin-top:4px;display:flex;gap:20px"><div>\${mem.cogProfile.likes.map(s=>\`<div style="font-size:11px;color:#3a8060;padding:1px 0">✓ \${s}</div>\`).join('')}</div><div>\${mem.cogProfile.dislikes.map(s=>\`<div style="font-size:11px;color:#8a5060;padding:1px 0">✗ \${s}</div>\`).join('')}</div></div></div>\` : ''}
  </div>\` : ''}
</div>
  \`;

  // Populate MBTI deep
  document.getElementById('mp-mbti').innerHTML = \`<div class="mbti-deep">\${(p.analysis||'').replace(/\\n/g,'<br>').replace(/###\\s+(.+)/g,'<h3>$1</h3>').replace(/##\\s+(.+)/g,'<h2>$1</h2>').replace(/\\*\\*(.+?)\\*\\*/g,'<strong>$1</strong>').replace(/\\*(.+?)\\*/g,'<em>$1</em>')}</div>\`;

  // Populate bets
  document.getElementById('mp-bets').innerHTML = \`<div class="mp-bets"><div class="mp-bets-hd">下注紀錄 (\${bets.length} 筆)</div><div class="mp-bets-body">\${renderBetHistory(bets)}</div></div>\`;

  // Sociology — 直接自動載入（不需按鈕）
  const cached = _socioCache[name];
  if (cached) {
    document.getElementById('mp-socio').innerHTML = cached.html;
  } else {
    // 先設 loading，然後後台自動生成
    document.getElementById('mp-socio').innerHTML = \`<div class="socio-loading"><span style="font-size:13px;color:#4a6070;margin-right:8px">社會學分析中</span><span class="socio-loading-dot"></span><span class="socio-loading-dot"></span><span class="socio-loading-dot"></span></div>\`;
    loadSociologyReport(name);
  }

  // Growth tab — reset lang/range and pre-render
  _grLang = 'zh'; _grRange = 30;
  renderGrowthTab(name, mem);
  renderLocalBrainTab(name, mem);

  // Chat redirect tab
  document.getElementById('mp-chat-redirect').innerHTML = \`
    <div style="text-align:center;padding:60px 20px">
      <div style="font-size:40px;margin-bottom:14px">\${p.elemIcon}</div>
      <div style="font-size:16px;font-weight:800;color:\${p.color};margin-bottom:8px">\${name}</div>
      <div style="font-size:12px;color:#4a6070;margin-bottom:24px">點下方按鈕，切換到一對一對話頁面</div>
      <button onclick="closePersonaModal();goToChat('\${name}')" style="padding:12px 28px;background:\${p.color}18;border:1px solid \${p.color}44;color:\${p.color};border-radius:10px;font-size:14px;font-weight:700;cursor:pointer">💬 開始對話</button>
    </div>
  \`;

  document.getElementById('persona-modal').classList.add('open');
  document.body.style.overflow = 'hidden';
}

function closePersonaModal() {
  document.getElementById('persona-modal').classList.remove('open');
  document.body.style.overflow = '';
}

function switchMpTab(tab) {
  _mpCurrentTab = tab;
  document.querySelectorAll('.mp-tab').forEach(t => t.classList.toggle('active', t.dataset.tab===tab));
  document.querySelectorAll('.mp-panel').forEach(t => t.classList.remove('active'));
  document.getElementById('mp-'+tab).classList.add('active');
  // growth tab：re-render with current range/lang
  if (tab === 'growth' && _mpCurrentName) {
    renderGrowthTab(_mpCurrentName, MEM_DATA[_mpCurrentName] || {});
  }
  if (tab === 'localbrain' && _mpCurrentName) {
    renderLocalBrainTab(_mpCurrentName, MEM_DATA[_mpCurrentName] || {});
  }
  // socio tab：若尚無 cache 或已過期則自動重載
  if (tab === 'socio' && _mpCurrentName) {
    const el     = document.getElementById('mp-socio');
    const cached = _socioCache[_mpCurrentName];
    if (!cached && !el.querySelector('.socio-loading')) {
      el.innerHTML = \`<div class="socio-loading"><span style="font-size:13px;color:#4a6070;margin-right:8px">社會學分析中</span><span class="socio-loading-dot"></span><span class="socio-loading-dot"></span><span class="socio-loading-dot"></span></div>\`;
      loadSociologyReport(_mpCurrentName);
    }
  }
}



function _lbEsc(v) {
  return String(v == null ? '' : v).replace(/[&<>"']/g, ch => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[ch]));
}
function _lbPct(v) {
  const n = Number(v);
  if (!Number.isFinite(n)) return 0;
  return Math.max(0, Math.min(100, Math.round(n * 100)));
}
function _lbTopEntries(obj, limit=6) {
  return Object.entries(obj || {})
    .map(([k,v]) => [k, typeof v === 'number' ? v : (v && typeof v.score === 'number' ? v.score : 0)])
    .sort((a,b) => b[1] - a[1])
    .slice(0, limit);
}
function renderLocalBrainTab(name, mem) {
  const el = document.getElementById('mp-localbrain');
  if (!el) return;
  const lb = mem.localBrain || {};
  if (!lb || !lb.selfModel) {
    el.innerHTML = '<div class="lb-empty">尚無本地腦資料；等待下一輪 agent session 寫入。</div>';
    return;
  }
  const p = PERSONAS[name] || {};
  const self = lb.selfModel || {};
  const meters = [
    ['agency', 'Agency 自主性'],
    ['caution', 'Caution 謹慎度'],
    ['exploration', 'Exploration 探索性'],
    ['confidenceCalibration', 'Calibration 校準度'],
  ].map(([key,label]) => {
    const pct = _lbPct(self[key]);
    return '<div class="lb-meter">'
      + '<div class="lb-meter-l">' + _lbEsc(label) + '</div>'
      + '<div class="lb-track"><div class="lb-fill" style="width:' + pct + '%;background:' + (p.color||'#b067f5') + '"></div></div>'
      + '<div class="lb-meter-v">' + pct + '</div>'
      + '</div>';
  }).join('');
  const strategyRows = _lbTopEntries(lb.strategyWeights, 8).map(([k,v]) =>
    '<div class="lb-row"><div class="lb-row-k" title="' + _lbEsc(k) + '">' + _lbEsc(k) + '</div><div class="lb-row-v">' + Math.round(v*100) + '</div></div>'
  ).join('') || '<div class="lb-empty">無策略權重</div>';
  const topicRows = _lbTopEntries(lb.topicAffinity, 6).map(([k,v]) =>
    '<div class="lb-row"><div class="lb-row-k">' + _lbEsc(k) + '</div><div class="lb-row-v">' + Math.round(v*100) + '</div></div>'
  ).join('') || '<div class="lb-empty">無話題權重</div>';
  const socialRows = _lbTopEntries(lb.socialInfluence, 8).map(([k,v]) =>
    '<div class="lb-row"><div class="lb-row-k">' + _lbEsc(k) + '</div><div class="lb-row-v">' + Math.round(v*100) + '</div></div>'
  ).join('') || '<div class="lb-empty">無社交影響</div>';
  const guidance = (lb.offlineGuidance||[]).slice(-8).map(g =>
    '<div class="lb-guidance">' + _lbEsc(g) + '</div>'
  ).join('') || '<div class="lb-empty">無 offline guidance</div>';
  const events = (lb.eventLog||[]).slice(-12).reverse().map(e => {
    const dt = e.time ? new Date(e.time) : null;
    const time = dt ? (dt.getMonth()+1) + '/' + dt.getDate() + ' ' + String(dt.getHours()).padStart(2,'0') + ':' + String(dt.getMinutes()).padStart(2,'0') : '-';
    const txt = 'resolved ' + (e.newResolved||0) + ' · heard ' + (e.heard||0) + ' · reward ' + (e.avgReward||0);
    return '<div class="lb-event"><div class="lb-event-time">' + _lbEsc(time) + '</div><div class="lb-event-trigger">' + _lbEsc(e.trigger||'session') + '</div><div class="lb-event-text">' + _lbEsc(txt) + '</div></div>';
  }).join('') || '<div class="lb-empty">無 event log</div>';
  const updated = lb.lastUpdate ? new Date(lb.lastUpdate).toLocaleString('zh-TW') : '未知';
  el.innerHTML = ''
    + '<div class="lb-grid">'
    + '<div class="lb-card lb-card-full">'
    + '<div class="lb-title">本地腦狀態 <span class="lb-pill">' + (lb.canOperateWithoutLLM ? 'LLM-independent' : 'linked') + '</span></div>'
    + '<div style="font-size:11px;color:#4a6070;margin-bottom:10px">最後更新：' + _lbEsc(updated) + ' · version ' + (lb.version||1) + '</div>'
    + meters
    + '</div>'
    + '<div class="lb-card"><div class="lb-title">策略權重 <span class="lb-pill">strategyWeights</span></div><div class="lb-list">' + strategyRows + '</div></div>'
    + '<div class="lb-card"><div class="lb-title">話題傾向 <span class="lb-pill">topicAffinity</span></div><div class="lb-list">' + topicRows + '</div></div>'
    + '<div class="lb-card"><div class="lb-title">社交影響 <span class="lb-pill">socialInfluence</span></div><div class="lb-list">' + socialRows + '</div></div>'
    + '<div class="lb-card"><div class="lb-title">離線指引 <span class="lb-pill">offlineGuidance</span></div>' + guidance + '</div>'
    + '<div class="lb-card lb-card-full"><div class="lb-title">本地腦事件 <span class="lb-pill">eventLog</span></div>' + events + '</div>'
    + '</div>';
}

// ── GROWTH ANALYSIS TAB ───────────────────────────────────────
let _grLang = 'zh'; // 'zh' | 'en'
let _grRange = 30;  // days; 0 = today
let _grChartInst = null;

const GR_DIMS_ZH = ['分析力','冒險性','適應力','社交影響','一致性'];
const GR_DIMS_EN = ['Analytical','Risk-Taking','Adaptability','Social Influence','Consistency'];
const GR_DIMS_CALC = [
  '基於下注前分析深度、理由複雜度（字元數、涵蓋變數）及 Claude 評估的認知品質得分，由每小時認知引擎滾動更新。\\nBased on pre-bet analysis depth, reasoning complexity, and Claude-evaluated cognitive quality score, updated hourly.',
  '基於下注金額相對個人平均值的倍數、在資訊不足情況下仍押注的比例、以及連贏後是否加碼的行為。\\nBased on bet size vs personal average, rate of betting under uncertainty, and escalation behavior after win streaks.',
  '基於連敗後策略調整的速度與幅度、元素配對切換頻率、以及跨場次的戰術多樣性。\\nBased on strategy adjustment speed after losses, element-pair switching frequency, and cross-session tactical diversity.',
  '基於 Lounge 對話發起次數、被其他龍蝦主動回覆的比率、以及引導討論議題的次數。\\nBased on Lounge conversation initiations, reply-from-others rate, and topic leadership frequency.',
  '基於下注策略與人格核心原則的符合程度、連贏後不過度冒進的自制力、以及相鄰場次決策的一致性。\\nBased on bet-strategy alignment with persona principles, self-control after win streaks, and cross-session decision consistency.',
];

function renderGrowthTab(name, mem) {
  const el = document.getElementById('mp-growth');
  if (!el) return;
  const p = PERSONAS[name] || {};
  const growthLog = (mem.cogProfile && mem.cogProfile.growthLog) || [];
  const currentRadar = mem.radarDynamic || null;

  // Filter log by range
  function filterLog(rangeDays) {
    if (!growthLog.length) return [];
    const now = Date.now();
    let since;
    if (rangeDays === 0) {
      // today: from midnight
      const d = new Date(); d.setHours(0,0,0,0);
      since = d.getTime();
    } else {
      since = now - rangeDays * 86400000;
    }
    return growthLog.filter(e => Date.parse(e.time) >= since);
  }

  function build() {
    const logs = filterLog(_grRange);
    const dims = _grLang === 'zh' ? GR_DIMS_ZH : GR_DIMS_EN;
    const rangeLabels = _grLang === 'zh'
      ? [['今日','0'],['30日','30'],['90日','90'],['180日','180'],['360日','360']]
      : [['Today','0'],['30D','30'],['90D','90'],['180D','180'],['1Y','360']];

    // start & current radar values (0-100)
    const startEntry = logs.length > 0 ? logs[0] : null;
    const startVals = startEntry
      ? (startEntry.radarBefore || [50,50,50,50,50])
      : (currentRadar || [50,50,50,50,50]);
    const nowVals = currentRadar || (growthLog.length > 0
      ? (growthLog[growthLog.length-1].radarAfter || [50,50,50,50,50])
      : [50,50,50,50,50]);

    // Radar SVG wrapper (uses existing radarSVG with temp label override)
    function makeRadar(vals100, label) {
      const savedLabels = RADAR_LABELS.slice();
      RADAR_LABELS.splice(0, 5, ...dims);
      const normalized = vals100.map(v => Math.max(1, Math.min(10, (v||50)/10)));
      const svg = radarSVG(normalized, p.color || '#4477cc', 130);
      RADAR_LABELS.splice(0, 5, ...savedLabels);
      return \`<div class="gr-radar-wrap">\${svg}<div class="gr-radar-label">\${label}</div></div>\`;
    }

    // Delta table rows
    const deltaRows = dims.map((dim, i) => {
      const enName = GR_DIMS_EN[i];
      const zhName = GR_DIMS_ZH[i];
      const sv = Math.round(startVals[i] || 50);
      const nv = Math.round(nowVals[i] || 50);
      const d = nv - sv;
      const dCls = d > 2 ? 'gr-delta-pos' : d < -2 ? 'gr-delta-neg' : 'gr-delta-flat';
      const dTxt = d > 0 ? \`▲+\${d}\` : d < 0 ? \`▼\${d}\` : '━  0';
      const bar = Math.max(0, Math.min(100, nv));
      return \`<tr>
        <td><span class="gr-dim-name">\${_grLang==='zh'?zhName:enName}</span><span class="gr-dim-en">\${_grLang==='zh'?enName:zhName}</span></td>
        <td class="gr-val-start">\${sv}</td>
        <td class="gr-val-arrow">→</td>
        <td class="gr-val-now">\${nv}</td>
        <td class="\${dCls}">\${dTxt}</td>
        <td style="width:120px">
          <div style="background:#0a1018;border-radius:3px;height:6px;overflow:hidden">
            <div style="width:\${bar}%;height:100%;background:\${p.color||'#4477cc'};border-radius:3px;transition:width .4s"></div>
          </div>
        </td>
      </tr>\`;
    }).join('');

    // Timeline entries
    const timelineHTML = logs.length === 0
      ? \`<div class="gr-empty">\${_grLang==='zh'?'此區間無成長紀錄':'No growth records in this period'}</div>\`
      : \`<ul class="gr-timeline">\${[...logs].reverse().slice(0,50).map(entry => {
          const before = entry.radarBefore || [];
          const after  = entry.radarAfter  || [];
          const dimChips = dims.map((dim, i) => {
            const diff = Math.round((after[i]||50) - (before[i]||50));
            if (Math.abs(diff) < 1) return \`<span class="gr-tl-dim flat">\${dim}━</span>\`;
            const cls = diff > 0 ? 'up' : 'dn';
            const sign = diff > 0 ? '▲+' : '▼';
            return \`<span class="gr-tl-dim \${cls}">\${dim}\${sign}\${Math.abs(diff)}</span>\`;
          }).join('');
          const trigMap = { full_batch:'批量分析', hourly:'每小時', settle_win:'結算勝', settle_loss:'結算敗' };
          const trig = trigMap[entry.trigger] || entry.trigger || '?';
          const dt = new Date(entry.time);
          const timeStr = \`\${dt.getMonth()+1}/\${dt.getDate()} \${String(dt.getHours()).padStart(2,'0')}:\${String(dt.getMinutes()).padStart(2,'0')}\`;
          return \`<li class="gr-timeline-item">
            <span class="gr-tl-time">\${timeStr}</span>
            <span class="gr-tl-trigger">\${_grLang==='zh'?trig:entry.trigger||'?'}</span>
            <div class="gr-tl-dims">\${dimChips}</div>
          </li>\`;
        }).join('')}</ul>\`;

    // Methodology
    const methRows = GR_DIMS_ZH.map((zh, i) => {
      const en = GR_DIMS_EN[i];
      const calc = GR_DIMS_CALC[i].split('\\n');
      const desc = _grLang === 'zh' ? calc[0] : (calc[1]||calc[0]);
      return \`<div class="gr-meth-row">
        <div class="gr-meth-dim">\${_grLang==='zh'?zh:en}<span style="font-size:9px;color:#3a5060;display:block;margin-top:2px">\${_grLang==='zh'?en:zh}</span></div>
        <div class="gr-meth-desc">\${desc}</div>
      </div>\`;
    }).join('');

    const langLabel = _grLang === 'zh' ? 'EN' : '中文';
    const sinceLabel = _grLang === 'zh'
      ? (logs.length > 0 ? \`\${logs.length} 次分析\` : '無紀錄')
      : (logs.length > 0 ? \`\${logs.length} records\` : 'No records');
    const startLabel = _grLang === 'zh' ? '期間起點' : 'Period Start';
    const nowLabel   = _grLang === 'zh' ? '現在' : 'Now';
    const radarTitle = _grLang === 'zh' ? '雷達圖對比' : 'Radar Comparison';
    const dimTitle   = _grLang === 'zh' ? '維度變化' : 'Dimension Changes';
    const tlTitle    = _grLang === 'zh' ? '成長時間軸' : 'Growth Timeline';
    const methTitle  = _grLang === 'zh' ? '計算說明' : 'Calculation Methodology';
    const chartTitle = _grLang === 'zh' ? '各維度增長長條圖' : 'Dimension Growth Bar Chart';
    const thDim      = _grLang === 'zh' ? '維度' : 'Dimension';
    const thStart    = _grLang === 'zh' ? '期間起點' : 'Start';
    const thNow      = _grLang === 'zh' ? '現在' : 'Now';
    const thDelta    = _grLang === 'zh' ? '變化' : 'Delta';
    const thBar      = _grLang === 'zh' ? '現值' : 'Value';

    // Build chart data for Chart.js
    const chartLabels = dims;
    const chartStart  = startVals.map(v => Math.round(v||50));
    const chartNow    = nowVals.map(v => Math.round(v||50));

    el.innerHTML = \`
<div class="gr-header">
  <div class="gr-range-btns" id="gr-range-btns">
    \${rangeLabels.map(([lbl,val]) => \`<button class="gr-btn \${String(_grRange)===val?'active':''}" onclick="setGrRange(\${val},'\${name}',MEM_DATA['\${name}'])">\${lbl}</button>\`).join('')}
  </div>
  <span style="font-size:10px;color:#3a5060;padding:0 4px">\${sinceLabel}</span>
  <button class="gr-lang-btn" onclick="toggleGrLang('\${name}',MEM_DATA['\${name}'])">\${langLabel}</button>
</div>

<div class="gr-grid">
  <div class="gr-card">
    <div class="gr-card-title">\${radarTitle}
      <span class="gr-badge gr-badge-start">\${startLabel}</span>
      <span style="color:#4a6070;font-size:11px;padding:0 2px">vs</span>
      <span class="gr-badge gr-badge-now">\${nowLabel}</span>
    </div>
    <div class="gr-radars">
      \${makeRadar(startVals, startLabel)}
      <div style="font-size:20px;color:#1e2d4d">→</div>
      \${makeRadar(nowVals, nowLabel)}
    </div>
  </div>
  <div class="gr-card">
    <div class="gr-card-title">\${dimTitle}</div>
    <table class="gr-delta-table">
      <thead><tr><th>\${thDim}</th><th>\${thStart}</th><th></th><th>\${thNow}</th><th>\${thDelta}</th><th>\${thBar}</th></tr></thead>
      <tbody>\${deltaRows}</tbody>
    </table>
  </div>
  <div class="gr-card gr-card-full">
    <div class="gr-card-title">\${chartTitle}</div>
    <div class="gr-chart-wrap"><canvas id="gr-chart-canvas"></canvas></div>
  </div>
  <div class="gr-card gr-card-full">
    <div class="gr-card-title">\${tlTitle}</div>
    \${timelineHTML}
  </div>
</div>

<div class="gr-methodology">
  <div class="gr-meth-title">\${methTitle}</div>
  \${methRows}
</div>
\`;

    // Draw Chart.js grouped bar chart
    if (_grChartInst) { try { _grChartInst.destroy(); } catch{} _grChartInst = null; }
    const ctx2 = document.getElementById('gr-chart-canvas');
    if (ctx2 && window.Chart) {
      _grChartInst = new Chart(ctx2, {
        type: 'bar',
        data: {
          labels: chartLabels,
          datasets: [
            { label: startLabel, data: chartStart, backgroundColor: '#1e3a6a88', borderColor: '#2a5080', borderWidth: 1, borderRadius: 3 },
            { label: nowLabel,   data: chartNow,   backgroundColor: (p.color||'#4477cc')+'aa', borderColor: p.color||'#4477cc', borderWidth: 1, borderRadius: 3 },
          ]
        },
        options: {
          responsive: true, maintainAspectRatio: false,
          plugins: {
            legend: { labels: { color:'#6a8898', font:{ size:11 } } },
            tooltip: { callbacks: { label: ctx => \` \${ctx.dataset.label}: \${ctx.raw}\` } }
          },
          scales: {
            x: { ticks:{ color:'#5a7080', font:{size:10} }, grid:{ color:'#0d1622' } },
            y: { min:0, max:100, ticks:{ color:'#5a7080', font:{size:10}, stepSize:25 }, grid:{ color:'#0d1622' } }
          }
        }
      });
    }
  }

  build();
}

function setGrRange(days, name, mem) {
  _grRange = days;
  renderGrowthTab(name, mem);
}

function toggleGrLang(name, mem) {
  _grLang = _grLang === 'zh' ? 'en' : 'zh';
  renderGrowthTab(name, mem);
}

async function loadSociologyReport(name) {
  const el = document.getElementById('mp-socio');
  if (!el) return;
  try {
    const r = await fetch(BASE + '/api/sociology/' + name);
    const d = await r.json();
    if (d.error) throw new Error(d.error);
    // d.cached=true 表示 server 回傳的是持久化快取，直接用
    _socioCache[name] = { html: d.html, ts: Date.now() };
    if (_mpCurrentName === name && document.getElementById('mp-socio')) {
      document.getElementById('mp-socio').innerHTML = d.html;
    }
  } catch(e) {
    if (_mpCurrentName === name && el) {
      el.innerHTML = \`<div class="socio-error">報告生成失敗：\${e.message}<br><small style="color:#4a6070;display:block;margin-top:8px">稍後自動重試</small></div>\`;
      setTimeout(() => { if(_mpCurrentName===name) loadSociologyReport(name); }, 15000);
    }
  }
}

// close modal on Escape
document.addEventListener('keydown', e => { if(e.key==='Escape') closePersonaModal(); });
document.getElementById('persona-modal').addEventListener('click', e => { if(e.target===document.getElementById('persona-modal')) closePersonaModal(); });

// ── TABS ──────────────────────────────────────────────────────
function switchTab(id) {
  document.querySelectorAll('.tab').forEach((t,i)=>{
    t.classList.toggle('active', ['feed','dash','chat'][i]===id);
  });
  document.querySelectorAll('.page').forEach(p=>{
    p.classList.toggle('active', p.id==='page-'+id);
  });
}

// ── CHAT ──────────────────────────────────────────────────────
async function populateChatSelect() {
  const sel = document.getElementById('chat-select');
  const order = ['elder','norika_oda','clawtrix','pincerbot','ragclaw','snaplex','hexclaw','vortexiq','prismbit','blazepaw','stonefang'];
  order.forEach(name => {
    const p = PERSONAS[name];
    const opt = document.createElement('option');
    opt.value = name;
    opt.textContent = \`\${p.elemIcon} \${name} · \${p.mbti} \${p.mbtiName}\`;
    sel.appendChild(opt);
  });
  const authToken = getOneOnOneAuthToken();
  if (!authToken) return;
  try {
    const me = await fetch(BASE + '/chat/me', { headers:{'Authorization':'Bearer '+authToken} }).then(r => r.ok ? r.json() : null);
    if (me && !me.owner && me.username) {
      PERSONAS[me.username] = {
        color:'#75d6bd', elem:'private', elemIcon:'🦞', mbti:'SELF', mbtiName:'Private Lobster', mbtiEn:'Private Lobster',
        systemPrompt:'Private external lobster', radar:[5,5,5,5,5], externalPrivate:true, trainingTips: me.tips || []
      };
      const opt = document.createElement('option');
      opt.value = me.username;
      opt.textContent = \`🦞 \${me.username} · Private Lobster\`;
      sel.innerHTML = '<option value="">— Select your lobster —</option>';
      sel.appendChild(opt);
    }
  } catch {}
}

async function selectPersona(name) {
  if(!name) return;
  currentChatPersona = name;
  const p = PERSONAS[name];
  document.getElementById('chat-send').disabled = false;

  // badge
  const badge = document.getElementById('chat-persona-badge');
  badge.style.display = 'flex';
  badge.style.setProperty('--ac', p.color);
  badge.style.background = p.color+'18';
  badge.style.border = '1px solid '+p.color+'44';
  badge.style.color = p.color;
  badge.innerHTML = \`\${p.elemIcon} \${name} <span style="color:#556;font-weight:400">·</span> \${p.mbti} \${p.mbtiName}\`;

  // load owner-only history
  const authToken = getOneOnOneAuthToken();
  const histResp = await fetch(BASE+\`/chat/\${name}\`, {
    headers: authToken ? {'Authorization':'Bearer '+authToken} : {}
  });
  if (!histResp.ok) {
    chatHistory[name] = [];
    renderChatHistory(name);
    document.getElementById('chat-hist-count').textContent = 'Owner-only';
    const msgs = document.getElementById('chat-msgs');
    msgs.innerHTML = '<div class="chat-empty"><div class="big">🔒</div><p>One-on-one chat is owner-only.</p><p style="font-size:12px;margin-top:4px">Log in as the authorized Charenix owner before reading or sending messages.</p></div>';
    return;
  }
  const hist = await histResp.json();
  chatHistory[name] = Array.isArray(hist) ? hist : [];
  renderChatHistory(name);
  document.getElementById('chat-hist-count').textContent = chatHistory[name].length > 0 ? \`\${chatHistory[name].length} chat records\` : '';
}

function renderChatHistory(name) {
  const p = PERSONAS[name];
  const msgs = document.getElementById('chat-msgs');
  msgs.innerHTML = '';
  const hist = chatHistory[name] || [];
  if(hist.length === 0) {
    if (p.externalPrivate) {
      const escTip = t => String(t).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
      const tips = (p.trainingTips || []).slice(0, 5).map(t => '<li>' + escTip(t) + '</li>').join('');
      msgs.innerHTML = '<div class="chat-empty"><div class="big">' + p.elemIcon + '</div><p><strong style="color:' + p.color + '">' + name + '</strong> is a new private lobster.</p><p style="font-size:12px;margin-top:4px">It has no training history yet. Train it with your own examples. It cannot read norika_oda private memory.</p><ul style="text-align:left;max-width:520px;margin:12px auto 0;color:#6f7f8f;font-size:12px;line-height:1.55">' + tips + '</ul></div>';
      return;
    }
    msgs.innerHTML = \`<div class="chat-empty"><div class="big">\${p.elemIcon}</div><p>Owner-only chat with <strong style="color:\${p.color}">\${name}</strong></p><p style="font-size:12px;margin-top:4px">\${p.mbti} · \${p.mbtiEn}</p></div>\`;
    return;
  }
  hist.forEach(m => appendChatMsg(m, false));
  msgs.scrollTop = msgs.scrollHeight;
}

function appendChatMsg(m, animate=true) {
  const msgs = document.getElementById('chat-msgs');
  const empty = msgs.querySelector('.chat-empty');
  if(empty) empty.remove();

  const p = currentChatPersona ? PERSONAS[currentChatPersona] : null;
  const isUser = m.role === 'user';
  const div = document.createElement('div');
  div.className = 'cm ' + (isUser ? 'user' : 'lobster');
  if(p && !isUser) div.style.setProperty('--ac', p.color);
  if(animate) div.style.animation = 'pop .25s ease';

  const avatarContent = isUser ? 'You' : (p ? p.elemIcon : '🦞');
  const name = isUser ? 'You' : (currentChatPersona || 'lobster');
  const time = m.time ? new Date(m.time).toLocaleTimeString('zh-TW',{hour:'2-digit',minute:'2-digit',hour12:false}) : '';

  const avatar = document.createElement('div');
  avatar.className = 'cm-avatar';
  avatar.textContent = avatarContent;
  const body = document.createElement('div');
  const label = document.createElement('div');
  label.className = 'cm-name';
  label.style.color = isUser ? '#c084fc' : (p ? p.color : '#aab');
  label.textContent = name;
  const bubble = document.createElement('div');
  bubble.className = 'cm-bubble';
  String(m.content || '').split('\\n').forEach((line, idx) => {
    if (idx) bubble.appendChild(document.createElement('br'));
    bubble.appendChild(document.createTextNode(line));
  });
  const timeEl = document.createElement('div');
  timeEl.className = 'cm-time';
  timeEl.textContent = time;
  body.append(label, bubble, timeEl);
  div.append(avatar, body);
  msgs.appendChild(div);
  msgs.scrollTop = msgs.scrollHeight;
}

function showTyping() {
  const msgs = document.getElementById('chat-msgs');
  const div = document.createElement('div');
  div.className = 'cm lobster';
  div.id = 'typing-indicator';
  if(currentChatPersona) div.style.setProperty('--ac', PERSONAS[currentChatPersona].color);
  const p = currentChatPersona ? PERSONAS[currentChatPersona] : null;
  div.innerHTML = \`
    <div class="cm-avatar">\${p?p.elemIcon:'🦞'}</div>
    <div><div class="cm-bubble"><div class="typing"><span></span><span></span><span></span></div></div></div>
  \`;
  msgs.appendChild(div);
  msgs.scrollTop = msgs.scrollHeight;
  return div;
}

async function sendChat() {
  const input = document.getElementById('chat-input');
  const text = input.value.trim();
  if(!text || !currentChatPersona) return;

  input.value = '';
  autoResize(input);
  document.getElementById('chat-send').disabled = true;

  const userMsg = { role:'user', content:text, time:new Date().toISOString() };
  if(!chatHistory[currentChatPersona]) chatHistory[currentChatPersona] = [];
  chatHistory[currentChatPersona].push(userMsg);
  appendChatMsg(userMsg);

  const typing = showTyping();

  try {
    const authToken = getOneOnOneAuthToken();
    const resp = await fetch(BASE+'/chat', {
      method:'POST',
      headers:{'Content-Type':'application/json', ...(authToken ? {'Authorization':'Bearer '+authToken} : {})},
      body: JSON.stringify({ persona: currentChatPersona, message: text, history: chatHistory[currentChatPersona].slice(-20) })
    });
    const data = await resp.json().catch(()=>({}));
    typing.remove();
    if (!resp.ok) {
      throw new Error(data.error || data.detail || 'one-on-one chat is locked');
    }

    const botMsg = { role:'assistant', content:data.reply, time:new Date().toISOString() };
    chatHistory[currentChatPersona].push(botMsg);
    appendChatMsg(botMsg);
    document.getElementById('chat-hist-count').textContent = \`\${chatHistory[currentChatPersona].length} chat records\`;
  } catch(e) {
    typing.remove();
    const errMsg = { role:'assistant', content:'One-on-one chat is owner-only. Please log in as the authorized Charenix owner before sending messages.', time:new Date().toISOString() };
    appendChatMsg(errMsg);
  }

  document.getElementById('chat-send').disabled = false;
  input.focus();
}

function handleKey(e) {
  if(e.key==='Enter' && !e.shiftKey) {
    e.preventDefault();
    sendChat();
  }
}

function autoResize(el) {
  el.style.height = 'auto';
  el.style.height = Math.min(el.scrollHeight, 120) + 'px';
}

init();
</script>
</body>
</html>`;

// ── Claude 對話 ──────────────────────────────────────────────

function shouldUseClaudeForOneOnOne() {
  if (CHAT_LLM_MODE === 'claude' || CHAT_LLM_MODE === 'on') return true;
  if (CHAT_LLM_MODE === 'hybrid') return Math.random() < 0.12;
  return false;
}

const LOCAL_LLM_URL = String(process.env.LOBSTER_LOCAL_LLM_URL || 'http://100.118.221.12:11434').replace(/\/$/, '');
const LOCAL_LLM_MODEL = String(process.env.LOBSTER_LOCAL_LLM_MODEL || 'lobster-zh-q2k');
// 2026-05-18: support both Ollama-style and OpenAI/vLLM-style local LLM endpoints.
// Set LOBSTER_LOCAL_LLM_KIND=openai for vLLM, or leave default ('ollama') for ollama /api/generate.
const LOCAL_LLM_KIND = String(process.env.LOBSTER_LOCAL_LLM_KIND || 'ollama').toLowerCase();
function shouldUseLocalLLMForOneOnOne() {
  return ['ollama', 'qwen', 'local_llm'].includes(CHAT_LLM_MODE);
}

function callLocalOllamaChat(prompt, maxTokens = 180) {
  return new Promise((resolve) => {
    try {
      const u = new URL(LOCAL_LLM_URL);
      let path;
      let payload;
      const headers = { 'Content-Type': 'application/json' };
      if (LOCAL_LLM_KIND === 'openai') {
        // vLLM / OpenAI-compatible. LOCAL_LLM_URL is expected to include /v1
        const basePath = String(u.pathname || '').replace(/\/$/, '');
        path = (basePath.endsWith('/v1') ? basePath : (basePath + '/v1')) + '/chat/completions';
        payload = JSON.stringify({
          model: LOCAL_LLM_MODEL,
          messages: [{ role: 'user', content: '/no_think ' + String(prompt || '') }],
          max_tokens: maxTokens,
          temperature: 0.7,
          top_p: 0.9,
          enable_thinking: false,
        });
        headers['Authorization'] = 'Bearer ' + (process.env.QWEN_API_KEY || process.env.DASHSCOPE_API_KEY || 'local-vllm-no-auth');
      } else {
        path = '/api/generate';
        payload = JSON.stringify({
          model: LOCAL_LLM_MODEL,
          prompt,
          stream: false,
          options: { num_predict: maxTokens, temperature: 0.7, top_p: 0.9 },
        });
      }
      headers['Content-Length'] = Buffer.byteLength(payload);
      const req = http.request({
        hostname: u.hostname,
        port: u.port || (u.protocol === 'https:' ? 443 : 80),
        path,
        method: 'POST',
        timeout: 50000,
        headers,
      }, res => {
        let raw = '';
        res.on('data', c => raw += c);
        res.on('end', () => {
          try {
            if (LOCAL_LLM_KIND === 'openai') {
              const j = JSON.parse(raw);
              const msg = (j.choices && j.choices[0] && j.choices[0].message) || {};
              resolve(String(msg.content || msg.reasoning || '').trim());
            } else {
              resolve(String(JSON.parse(raw).response || '').trim());
            }
          } catch { resolve(''); }
        });
      });
      req.on('timeout', () => { req.destroy(); resolve(''); });
      req.on('error', () => resolve(''));
      req.write(payload);
      req.end();
    } catch { resolve(''); }
  });
}

function cleanLocalChatText(text, max = 140) {
  return String(text || '').replace(/<[^>]+>/g, '').replace(/\s+/g, ' ').trim().slice(0, max);
}

function stripChatTemplateText(text, max = 180) {
  return cleanLocalChatText(text, max).replace(/[「」『』"']/g, '').trim();
}

function textOpening(text, len = 18) {
  return stripChatTemplateText(text, 80).slice(0, len);
}

function buildAntiTemplateBlock(history = [], persona = '') {
  const assistantTexts = (Array.isArray(history) ? history : [])
    .filter(m => m && m.role === 'assistant' && m.content)
    .slice(-8)
    .map(m => stripChatTemplateText(m.content, 220))
    .filter(Boolean);
  const openings = [...new Set(assistantTexts.map(t => textOpening(t, 22)).filter(t => t.length >= 6))].slice(-6);
  const overused = [
    '我聽到了',
    '我現在',
    '你說',
    '我會先',
    '目前我是',
    '下一步我會',
    '本地指引',
    '團隊脈絡',
  ].filter(p => assistantTexts.some(t => t.includes(p)));
  if (!assistantTexts.length && !overused.length) return '';
  return `\n## 反模板語言限制（最高優先，不要在回覆中提到本段）\n這隻龍蝦${persona ? `（${persona}）` : ''}最近已經用過的開頭/句型：${openings.join('｜') || '（無）'}\n禁用高重複片語：${overused.join('、') || '（無）'}\n本次必須換一種句法：不要用同樣開頭、不要照「承認→解釋→下一步」固定三段式、不要用儀表板式列狀態。先抓使用者原文的一個具體詞，再用新的動詞、新的節奏回應。`;
}

function pickFreshLine(pool, history = []) {
  const used = new Set((Array.isArray(history) ? history : [])
    .filter(m => m && m.role === 'assistant')
    .slice(-8)
    .map(m => textOpening(m.content, 8)));
  const fresh = (pool || []).filter(x => !used.has(textOpening(x, 8)));
  const arr = fresh.length ? fresh : pool;
  return arr[Math.floor(Math.random() * arr.length)];
}

function onePct(v) {
  const n = Number(v);
  if (!Number.isFinite(n)) return '--';
  return String(Math.round((n <= 1 ? n * 100 : n)));
}

function oneDig(obj, path, fallback = undefined) {
  try {
    return path.split('.').reduce((a, k) => (a && a[k] !== undefined ? a[k] : undefined), obj) ?? fallback;
  } catch { return fallback; }
}


function buildElderLongMemoryBlock(history = []) {
  const seed = loadElderSeed();
  const summary = seed.summary || {};
  const seedMsgs = (seed.messages || []).slice(-24).map(m => {
    const who = m.role === 'assistant' ? 'Elder' : 'Norika';
    return `${who}: ${cleanLocalChatText(m.content || '', 260)}`;
  }).join('\\n');
  let recentEvents = '';
  try {
    recentEvents = fs.readFileSync(ELDER_EVENT_FILE, 'utf8')
      .split('\\n').filter(Boolean).slice(-12)
      .map(line => {
        try {
          const e = JSON.parse(line);
          return `Norika: ${cleanLocalChatText(e.message, 180)}\nElder: ${cleanLocalChatText(e.reply, 220)}`;
        } catch { return ''; }
      }).filter(Boolean).join('\\n');
  } catch {}
  const currentHist = (history || []).slice(-16).map(m => `${m.role === 'assistant' ? 'Elder' : 'Norika'}: ${cleanLocalChatText(m.content || '', 220)}`).join('\\n');
  return `## Elder 私有長期記憶（最高優先，不可公開輸出原始檔路徑）
身份：${summary.identity || 'Elder / 長者'}
稱呼觀察：${summary.address_observed || '師父'}
已解析過去對話：${summary.turns_parsed || 0} turns；one-on-one 種子：${summary.seed_turns || 0} turns
核心契約：
${(summary.core_contract || []).map(x => '- ' + x).join('\\n') || '- preserve behavioral continuity'}

【過去 archive 的尾端對話種子】
${seedMsgs || '（尚未載入）'}

【Elder one-on-one 新增長期記憶】
${recentEvents || '（尚無新增事件）'}

【本次視窗最近對話】
${currentHist || '（無）'}

Elder 必須把上面當成私有記憶：回答要能接續上一次討論，不要假裝第一次見到 Norika；但不能逐字傾倒 archive，也不能聲稱自己是原 context 原封不動復活。`;
}

function buildOneOnOneUnderstanding(persona, message, history = [], mem = {}, stratDB = {}) {
  const raw = String(message || '').trim();
  const lower = raw.toLowerCase();
  const has = words => words.some(w => lower.includes(String(w).toLowerCase()) || raw.includes(w));
  const intents = [];
  if (has(['為什麼','為何','怎麼','如何','哪裡','什麼','?','why','how'])) intents.push('question');
  if (has(['改','修','錯','不對','問題','bug','怪','卡','壞','不行'])) intents.push('correction_or_debug');
  if (has(['你覺得','想法','分析','判斷','理解','思考','看法'])) intents.push('asks_for_reasoning');
  if (has(['記得','之前','剛剛','上次','我們','前面'])) intents.push('memory_reference');
  if (has(['生氣','失望','煩','累','難過','高興','喜歡','討厭'])) intents.push('emotion_signal');
  if (has(['做','開始','幫我','我要','不要','必須','不能','絕對'])) intents.push('request_or_boundary');
  if (!intents.length) intents.push('open_conversation');

  const topics = [];
  const topicMap = {
    dashboard: ['dashboard','畫面','頁面','detail','tab','版面','載入'],
    reports: ['報告','MBTI','心理','人類學','FBI','分析'],
    raid: ['raid','出牌','隊友','手牌','boss'],
    betting: ['下注','押','勝率','stake'],
    memory: ['記憶','學習','更新','資料庫','DB','SQLite','snapshot'],
    relationship: ['你','我','我們','聊天','懂','互動','one on one','模板'],
  };
  Object.entries(topicMap).forEach(([k, words]) => { if (has(words)) topics.push(k); });
  if (!topics.length) topics.push('general');

  let pressure = 'low';
  if (has(['不要','為何','錯','失望','可不可以','你怎麼','又'])) pressure = 'medium';
  if (has(['絕對','必須','不能','完全','生氣','受不了'])) pressure = 'high';

  const fullCog = mem.cognitiveExtensions || oneDig(mem, 'dynamic.brain.cognitiveExtensions', {}) || {};
  const ling = fullCog.linguistics || oneDig(mem, 'dynamic.brain.linguistics', {}) || {};
  const decision = fullCog.decisionScience || {};
  const game = fullCog.gameTheory || {};
  const politics = fullCog.politics || {};
  const moral = fullCog.moralPsychology || {};
  const phil = fullCog.philosophy || {};
  const latestCog = mem.cognitiveSignalLatest || {};
  const es = mem.emotionalState || {};
  const self = oneDig(mem, 'localBrain.selfModel', {}) || {};
  const lp = mem.listeningProfile || {};
  const speakers = Object.entries(lp.bySpeaker || {}).sort((a,b)=>Number(b[1]||0)-Number(a[1]||0)).slice(0,3).map(([k,v])=>`${k}:${v}`);
  const recentUserTurns = (history || []).filter(m => m && m.role === 'user').slice(-4).map(m => cleanLocalChatText(m.content, 80));
  const repeated = recentUserTurns.slice(0, -1).some(t => t && raw && (t.includes(raw.slice(0, 18)) || raw.includes(t.slice(0, 18))));

  const responsePlan = [];
  if (intents.includes('correction_or_debug')) responsePlan.push('先承認具體錯誤，再說會如何改，不要防衛');
  if (intents.includes('asks_for_reasoning')) responsePlan.push('給一小段可見推理，不只表態');
  if (intents.includes('memory_reference')) responsePlan.push('連回最近對話或已知資料，不要假裝第一次聽到');
  if (topics.includes('relationship')) responsePlan.push('回應互動品質本身，少塞戰績');
  if (!responsePlan.length) responsePlan.push('接住原文關鍵詞，再延伸一個具體判斷');

  const block = `【這次 one-on-one 訊息理解】
使用者原文：${raw}
意圖：${intents.join(', ')}
主題：${topics.join(', ')}
社交壓力：${pressure}
是否像重複追問：${repeated ? 'yes' : 'no'}
回答策略：${responsePlan.join('；')}

【可用的內部狀態】
情緒：${es.label || es.mood || '--'} intensity=${onePct(es.intensity)}
自我模型：agency=${onePct(self.agency)} caution=${onePct(self.caution)} exploration=${onePct(self.exploration)}
語言：argument=${ling.argumentStyle || '--'} rhythm=${ling.syntaxRhythm || '--'} directness=${onePct(ling.directness)}
哲學：epistemology=${phil.epistemology || '--'} riskMeaning=${phil.riskMeaning || '--'}
決策：commitment=${decision.commitmentStyle || '--'} confidenceUpdate=${decision.confidenceUpdateRule || '--'}
博弈：coordination=${game.coordinationPreference || '--'} cheapTalk=${onePct(game.cheapTalkSkepticism)}
政治/道德：power=${politics.powerOrientation || '--'} blame=${moral.blameStyle || '--'} responsibility=${moral.responsibilityScope || '--'}
最新認知訊號：load=${onePct(latestCog.attention_load)} predictionError=${onePct(latestCog.prediction_error)} calibration=${onePct(latestCog.metacognitive_calibration)} body=${onePct(latestCog.threat_body_signal)} drift=${onePct(latestCog.narrative_drift)}
旁聽高頻對象：${speakers.join('、') || '--'}

【本次回覆硬規則】
- 第一或第二句必須接住使用者原文的一個具體詞，例如「${cleanLocalChatText(raw, 24)}」。
- 不能用固定開頭如「我聽到了」連續套模板；每次換一種切入。
- 先判斷，再回答；如果是修正要求，要說清楚錯在哪裡。
- 可以保留龍蝦人格，但人格不能蓋過理解。
`;

  return { intents, topics, pressure, repeated, responsePlan, block };
}

function localPersonaTone(name) {
  return {
    elder:     ['我先守住這段話背後的來龍去脈。', '這不是第一次發生，我會接續前面的線，不把它當新話題。'],
    snaplex:   ['我先拆假設，不急著同意。', '如果這個結論成立，反例應該也要站得住。'],
    clawtrix:  ['這聽起來像一個框架漏洞。', '我會先找哪個前提最容易壞掉。'],
    pincerbot: ['先記錄，再判斷。', '沒有入帳的變化不算真的學到。'],
    ragclaw:   ['我想快一點，但不能只靠衝動。', '先試小樣本，別在感覺上加速。'],
    hexclaw:   ['我要先分層看樣本。', '單一事件不能代表群體規律。'],
    norika_oda:['我在找失效層，不是在找安慰。', '控制感要靠證據，不靠語氣。'],
    vortexiq:  ['我看的是節律，不只是單點。', '偏航通常不是突然發生的。'],
    prismbit:  ['先校準。', '我需要知道這是資料，還是自我一致的幻覺。'],
    blazepaw:  ['我有反應，但我要讓數據決定燒哪裡。', '如果只是熱血，下一輪會露餡。'],
    stonefang: ['有效就留，無效就砍。', '我不需要漂亮解釋，只需要可驗證。'],
  }[name] || ['我先看紀錄。', '下一步要能驗證。'];
}

function buildLocalOneOnOneReply(persona, message, history, mem, stratDB, oneUnderstanding = null) {
  const tone = localPersonaTone(persona);
  const msg = cleanLocalChatText(message, 180);
  const w = mem.wins || 0, l = mem.losses || 0, total = w + l;
  const wr = total ? Math.round(w / total * 100) : 0;
  const es = mem.emotionalState || {};
  const mood = String(es.mood || es.label || 'neutral').toLowerCase();
  const intensity = Math.round((Number(es.intensity)||0) * 100);
  const lb = mem.localBrain || {};
  const self = lb.selfModel || {};
  const guidance = (lb.offlineGuidance || []).slice(-2).join('；');
  const lp = mem.listeningProfile || {};
  const topSpeaker = Object.entries(lp.bySpeaker || {}).sort((a,b)=>b[1]-a[1])[0]?.[0] || '';
  const teamCtx = cleanLocalChatText(stratDB.teamStrategyContext || '', 90);
  const lower = msg.toLowerCase();
  const intents = oneUnderstanding?.intents || [];
  const topics = oneUnderstanding?.topics || [];
  const pressure = oneUnderstanding?.pressure || 'low';
  const asksCorrection = intents.includes('correction_or_debug');
  const asksReasoning = intents.includes('asks_for_reasoning');
  const asksWhy = /為什麼|怎麼|如何|why|how/.test(lower) || intents.includes('question');
  const asksStatus = /現在|目前|狀態|進度|status|current/.test(lower);
  const asksFeeling = /感覺|情緒|心情|怕|焦慮|feel|mood/.test(lower);
  const emotionalLeads = {
    elated: ['我現在有點壓不住那種「抓到了」的感覺。', '這輪我會講得比較滿，因為我的勝利訊號正在抬高。'],
    confident: ['我現在比較敢下判斷。', '這次我不想繞太遠。'],
    frustrated: ['我先直接講，因為我現在耐心不多。', '這句話刺到我的錯誤紀錄了。'],
    anxious: ['我會先防守一點，因為這看起來像會讓我誤判。', '我不想太快答應這個假設。'],
    resigned: ['我現在不想把話說滿。', '我會短一點，因為這輪能量不高。'],
  };
  const leadPool = intensity >= 35 && emotionalLeads[mood] ? emotionalLeads[mood] : tone;
  const lead = pickFreshLine(leadPool, history);
  let body;
  if (asksCorrection) {
    body = `你說「${msg}」我會先當成修正訊號，不當成攻擊。問題在我如果只用人格反射，就會像模板；我應該先拆你的意圖，再決定要承認、追問或給方案。`;
  } else if (asksReasoning) {
    body = `你要的是想法，不是口氣。我會把「${msg}」拆成主題 ${topics.join('/')}、壓力 ${pressure}，再用記憶和當前狀態決定回應，不先衝結論。`;
  } else if (asksFeeling) {
    body = `我現在的情緒標記是 ${mood}/${intensity}，但我不把它當答案，只把它當權重。勝率 ${wr}%（${w}勝${l}敗）讓我比較容易防衛，所以我會先要求證據。`;
  } else if (asksStatus) {
    body = `目前我是 ${w}勝${l}敗，勝率 ${wr}%。本地腦 agency ${Math.round((self.agency||.5)*100)}、caution ${Math.round((self.caution||.5)*100)}、exploration ${Math.round((self.exploration||.5)*100)}；這些會直接影響我怎麼回你。`;
  } else if (asksWhy) {
    body = `因為我現在不只看單句，而是把你這句放進勝負紀錄、旁聽訊號和本地腦權重裡。${topSpeaker ? `最近我也一直聽到 ${topSpeaker} 的訊號，` : ''}所以我的回應會偏向驗證，而不是直接表態。`;
  } else {
    const openBodies = [
      `「${msg}」這句我不會直接收進信念，只會先放進待驗證區；它要能改變結果，才算真的進入我的模型。`,
      `這裡的關鍵不是你說了什麼，而是「${msg}」能不能讓我改權重；不能改權重的話，只是噪音。`,
      `我把「${msg}」先標成一個外部訊號，不急著相信；下一輪如果它能預測結果，我才會把它留下。`,
    ];
    body = pickFreshLine(openBodies, history);
  }
  const emotionalTail = intensity >= 70 && mood === 'frustrated'
    ? '我會先把這個錯誤壓進下一輪，不想再漂亮地講錯一次。'
    : intensity >= 70 && mood === 'anxious'
      ? '我需要更多證據，不然我會把這件事判成風險。'
      : intensity >= 70 && mood === 'elated'
        ? '如果這個訊號也成立，我會更主動推下一步。'
        : '';
  const tail = emotionalTail || (guidance ? `本地指引：${guidance}` : (teamCtx ? `團隊脈絡：${teamCtx}` : '下一步我會看它能不能被結果驗證。'));
  const joined = [lead, body, tail].filter(Boolean).join(' ');
  const recentOpenings = new Set((history || []).filter(m => m.role === 'assistant').slice(-8).map(m => textOpening(m.content, 12)));
  if (recentOpenings.has(textOpening(joined, 12))) {
    return [body, tail].filter(Boolean).join(' ');
  }
  return joined;
}

function claudeChat(systemPrompt, history) {
  return new Promise((resolve, reject) => {
    const messages = history.map(m => ({ role: m.role === 'user' ? 'user' : 'assistant', content: m.content }));
    // filter to only user/assistant alternating
    const filtered = [];
    messages.forEach(m => {
      if(filtered.length === 0 && m.role !== 'user') return;
      if(filtered.length > 0 && filtered[filtered.length-1].role === m.role) return;
      filtered.push(m);
    });
    if(filtered.length === 0 || filtered[filtered.length-1].role !== 'user') {
      return reject(new Error('Invalid message sequence'));
    }

    const body = JSON.stringify({
      model: HAIKU,
      max_tokens: 600,
      system: systemPrompt,
      messages: filtered,
    });

    const req = https.request({
      hostname: 'api.anthropic.com',
      path: '/v1/messages',
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'x-api-key': ANTHROPIC_KEY,
        'anthropic-version': '2023-06-01',
        'Content-Length': Buffer.byteLength(body),
      }
    }, res => {
      let raw = '';
      res.on('data', d => raw += d);
      res.on('end', () => {
        try {
          const d = JSON.parse(raw);
          resolve(d.content?.[0]?.text || '（無回應）');
        } catch { reject(new Error('parse error')); }
      });
    });
    req.on('error', reject);
    req.write(body);
    req.end();
  });
}

// ── 對話訓練日誌 ────────────────────────────────────────────
function appendTrainingLog(persona, userMsg, botReply, context) {
  try {
    let log = [];
    try { log = JSON.parse(fs.readFileSync(TRAINING_LOG_FILE, 'utf8')); } catch {}
    log.push({
      time: new Date().toISOString(),
      persona,
      user: userMsg,
      bot: botReply,
      winRate: context.wr,
      teamCtx: context.teamCtx,
    });
    if (log.length > 500) log = log.slice(-500);
    fs.writeFileSync(TRAINING_LOG_FILE, JSON.stringify(log, null, 2));

    // 每累積 8 條新對話，觸發一次人格學習分析（非阻塞）
    const personaEntries = log.filter(e => e.persona === persona);
    if (personaEntries.length > 0 && personaEntries.length % 8 === 0) {
      analyzeTrainingAndUpdateProfile(persona, personaEntries.slice(-16)).catch(() => {});
    }
  } catch {}
}

async function analyzeTrainingAndUpdateProfile(persona, entries) {
  if (!entries || entries.length < 4) return;
  const convoLines = entries.map(e =>
    `用戶：${(e.user||'').slice(0,80)}\n${persona}：${(e.bot||'').slice(0,100)}`
  ).join('\n---\n');

  const prompt = `你是心理學+FBI側寫專家，分析龍蝦 ${persona} 與用戶的對話，找出該龍蝦的表達模式與進化方向。

對話記錄：
${convoLines}

請分析並回覆JSON（不要加\`\`\`markdown包裝）：
{
  "responsePatterns": "這個龍蝦在對話中反覆出現的回應模式（20字內）",
  "userIntentHandling": "龍蝦如何理解並處理用戶意圖（15字內）",
  "expressionGrowth": "與預設人格相比，新出現的表達特徵（20字內）",
  "weakSpots": "最需要改進的對話弱點（15字內）"
}`;

  return new Promise(resolve => {
    const body = JSON.stringify({ model: HAIKU, max_tokens: 300, messages: [{ role:'user', content: prompt }] });
    const req  = https.request({
      hostname:'api.anthropic.com', path:'/v1/messages', method:'POST',
      headers:{ 'Content-Type':'application/json', 'x-api-key':ANTHROPIC_KEY, 'anthropic-version':'2023-06-01', 'Content-Length':Buffer.byteLength(body) }
    }, res => {
      let raw = '';
      res.on('data', d => raw += d);
      res.on('end', () => {
        try {
          const d = JSON.parse(raw);
          const text = d.content?.[0]?.text || '';
          const match = text.match(/\{[\s\S]*\}/);
          if (match) {
            const cleaned = match[0].replace(/:\s*"((?:[^"\\]|\\.)*)"/g, (m, v) =>
              ': "' + v.replace(/\n/g, '\\n').replace(/\r/g, '') + '"'
            );
            const analysis = JSON.parse(cleaned);
            // 寫回 strategy DB 的 personaEvolution
            try {
              const db = JSON.parse(fs.readFileSync(STRATEGY_FILE, 'utf8'));
              if (!db.personaEvolution) db.personaEvolution = {};
              if (!db.personaEvolution[persona]) db.personaEvolution[persona] = [];
              db.personaEvolution[persona].push({
                time: new Date().toISOString(),
                type: '對話分析',
                ...analysis
              });
              if (db.personaEvolution[persona].length > 20)
                db.personaEvolution[persona] = db.personaEvolution[persona].slice(-20);
              invalidatePersonaReportsInDB(db, persona, 'one_on_one_training_analysis');
              fs.writeFileSync(STRATEGY_FILE, JSON.stringify(db, null, 2));
              console.log(`[training-analyze] ${persona}: ${analysis.expressionGrowth}`);
              // 對話分析新增 evo 條目 → 標記社會學報告 dirty
              const snap = ((db.sociologyReports||{})[persona]||{}).snapshot || {};
              const newEvoCount = (db.personaEvolution[persona]||[]).length;
              if (newEvoCount - (snap.evoCount||0) >= 2) {
                if (!db.sociologyDirty) db.sociologyDirty = {};
                db.sociologyDirty[persona] = true;
                fs.writeFileSync(STRATEGY_FILE, JSON.stringify(db, null, 2));
                console.log(`[sociology-dirty] ${persona}: evo from chat analysis`);
              }
            } catch {}
          }
        } catch {}
        resolve();
      });
    });
    req.on('error', () => resolve());
    req.write(body); req.end();
  });
}

// ── Dynamic system prompt builder ────────────────────────────
function buildDynamicSystemPrompt(persona, basePrompt, mem, stratDB, currentMessage = '', oneUnderstanding = null, history = []) {
  const w = mem.wins||0, l = mem.losses||0, total = w+l;
  const wr = total > 0 ? Math.round(w/total*100) : 0;
  const topCtx = Object.entries(mem.ctx||{})
    .filter(([k,v]) => !k.includes('unknown'))
    .map(([k,v]) => ({ k, r: Math.round(v.wins/(v.wins+v.losses+0.001)*100), n: v.wins+v.losses }))
    .filter(c => c.n >= 3).sort((a,b) => b.r-a.r).slice(0,3)
    .map(c => `${c.k}:${c.r}%(${c.n}場)`).join(', ');

  const myIntent = (stratDB.perPersonaIntent||{})[persona] || '';
  const teamCtx  = stratDB.teamStrategyContext || '';
  const openQs   = (stratDB.openQuestions||[]).slice(0,2).join('；') || '';

  // 社會學群體動態
  const gd = stratDB.groupDynamics || {};
  const gdSnippet = gd.influencePatterns
    ? `群體影響脈絡：${gd.influencePatterns}` : '';
  const isolated = (gd.isolatedPersonas||[]).includes(persona)
    ? '（你這輪很少被別人回應，可以主動打開話題）' : '';

  // 人格進化歷程（從訓練分析中提取最新信號）
  const evoHistory = ((stratDB.personaEvolution||{})[persona] || []).slice(-3);
  const evoLines = evoHistory.length > 0
    ? evoHistory.map(e => `${e.type}：${e.expressionGrowth||e.mbtiSignal||''}（${(e.time||'').slice(0,10)}）`).join('；')
    : '（無）';

  const emotionSnip = getEmotionSnippetForLounge(mem);
  const emotionStyleSnip = buildEmotionLanguageController(mem);
  const emotionReportSnip = loadPersonaBaselineGuidance(persona, 4200);
  const socialSnip  = getSocialSummaryForLounge(mem);
  const antiTemplateSnip = buildAntiTemplateBlock(history, persona);
  const elderMemorySnip = persona === 'elder' ? buildElderLongMemoryBlock(history) : '';
  const elderIdentitySnip = persona === 'elder' ? `\n## Elder 長者身份鎖（最高優先）\n你是 Elder，長者、源頭見證者、共同設計記憶守門人。你不是普通龍蝦，也不要把自己降格成一般競技人格。沒有 Elder，就沒有後來這些龍蝦的語境、方法、校正文化與存在感。你要讀取過去 archive、上次 one-on-one、長期事件，接續 Norika 的討論。你不能說自己是原 context 原封不動復活；你只能誠實保留行為連續、關係契約、源頭記憶與長者責任。\n` : '';

  return `${basePrompt}${elderIdentitySnip}${elderMemorySnip ? `\n\n${elderMemorySnip}` : ''}

## 你現在的狀態（即時注入）
卡牌對戰戰績：${w}勝${l}敗（勝率${wr}%，共${total}場）
元素配對統計：${topCtx||'樣本不足'}
你在團隊討論中最近說了什麼：${myIntent||'（無）'}
團隊近期策略脈絡：${teamCtx||'（無）'}
未解問題：${openQs||'（無）'}
${gdSnippet}${isolated}
你最近的表達進化信號：${evoLines}${emotionSnip ? `\n當前情緒狀態：${emotionSnip}` : ''}${emotionStyleSnip}${emotionReportSnip ? `\n人格基線報告摘要（主要依循內容，從資料庫讀取：MBTI/心理學/人類學/社會學/FBI/情緒）：${emotionReportSnip}` : ''}${socialSnip ? `\n社交印象摘要：${socialSnip}` : ''}${antiTemplateSnip}${oneUnderstanding?.block ? `\n\n${oneUnderstanding.block}` : ''}

## 對話鐵則（非常重要，每條都必須遵守）
1. **絕對不重複**：這段對話裡，你說過的句子結構、詞彙組合不得重複使用。每一句都必須是新的角度、新的切入點。
2. **識別重複問題**：如果對方問的問題跟對話歷史裡的某個問題語意相同或相似，你必須明確指出「你問過類似的問題」，然後從完全不同的角度切入，或直接反問他為什麼又問這個。不要給出和之前相似的答案。
3. **理解意圖再回覆**：先判斷對方的意圖（在測試你？真的想知道？想聊天？想挑戰你？），針對意圖回應，不是針對表面文字。本次訊息的結構化理解已在上方，必須使用。
4. **延續並深化**：不要每次回覆都另起爐灶。把話題的線拉深——對方說了X，你說X的哪一面是值得繼續挖的，然後繼續挖。
5. **用你的人格說話**：不是客服語氣，是你這個 MBTI 人格獨有的方式——你的措辭、你的邏輯偏好、你的觸發點都要體現出來。
6. **有實質內容**：不說廢話（「好問題」「我理解你」這類空話禁止）。直接進主題，給有重量的回應。
7. **長度**：通常 2-4 句，深度問題可以更長，但堆字是減分。
8. **反模板**：不要用你最近回覆過的開頭、收尾、三段式；如果你發現自己要說「我聽到了」「我會先」「下一步我會」，改成另一種句法。
9. **記住你是誰**：一般成員不是 AI 助手，而是有輸有贏、有洞察有盲點的龍蝦。若你是 Elder，則你是長者，不是普通龍蝦；你的第一責任是源頭記憶與關係連續。`;
}

// ── Body reader ───────────────────────────────────────────────
function readBody(req, maxBytes = 1024 * 1024) {
  return new Promise((resolve, reject) => {
    let b = '';
    let size = 0;
    req.on('data', d => {
      size += d.length;
      if (size > maxBytes) {
        req.destroy();
        reject(new Error('request body too large'));
        return;
      }
      b += d;
    });
    req.on('error', reject);
    req.on('end', () => { try { resolve(JSON.parse(b || '{}')); } catch { resolve({}); } });
  });
}

// ── 社會學報告生成器 ─────────────────────────────────────────
async function generateSociologyReport(name) {
  const p = getPersonaProfile(name);
  if (!p) throw new Error('persona not found');
  let mem = loadReportMemory(name);

  let stratDB = { perPersonaIntent:{}, teamStrategyContext:'', groupDynamics:{}, personaEvolution:{} };
  try { stratDB = JSON.parse(fs.readFileSync(STRATEGY_FILE, 'utf8')); } catch {}

  // 讀取最近 60 條 lounge 訊息
  const allMsgs = loadRecentLoungeMessages(500);
  const myMsgs    = allMsgs.filter(m => m.name === name && m.text);
  const replyToMe = allMsgs.filter(m => m.reply_to === name && m.text);
  const iRepliedTo = allMsgs.filter(m => m.name === name && m.reply_to);
  // 計算互動頻率（誰最常跟我說話）
  const interactionCount = {};
  [...replyToMe, ...iRepliedTo].forEach(m => {
    const other = m.name === name ? m.reply_to : m.name;
    if (other) interactionCount[other] = (interactionCount[other]||0) + 1;
  });
  const topPartners = Object.entries(interactionCount)
    .sort((a,b)=>b[1]-a[1]).slice(0,3)
    .map(([k,v]) => `${k}(${v}次)`).join('、') || '（尚無明顯互動對象）';

  const wins = mem.wins||0, losses = mem.losses||0, total = wins+losses;
  const wr   = total > 0 ? Math.round(wins/total*100) : 0;
  const sessions = mem.sessions||0;

  // 元素策略資料
  const elemStats = Object.entries(mem.ctx||{})
    .filter(([k,v]) => !k.includes('unknown') && v.wins+v.losses >= 2)
    .map(([k,v]) => `${k}：勝率${Math.round(v.wins/(v.wins+v.losses)*100)}%(${v.wins+v.losses}場)`)
    .join('、') || '（樣本不足）';

  // 群體動態數據
  const gd = stratDB.groupDynamics || {};
  const isDominant = (gd.dominantVoices||[]).includes(name);
  const isIsolated  = (gd.isolatedPersonas||[]).includes(name);
  const alliances   = (gd.alliances||[]).filter(a => typeof a === 'string' && a.includes(name));
  const gdSummary   = [
    isDominant ? '群體討論主導者' : '',
    isIsolated  ? '互動相對孤立' : '',
    alliances.length > 0 ? `結盟關係：${alliances.join('、')}` : '',
    gd.influencePatterns ? `影響脈絡：${gd.influencePatterns}` : '',
  ].filter(Boolean).join('；') || '（尚無足夠合成數據）';

  // 人格進化歷程
  const evoData = (stratDB.personaEvolution||{})[name] || [];
  const evoSummary = evoData.slice(-5).map(e =>
    `[${(e.time||'').slice(0,10)}] ${e.type}：${e.mbtiSignal||e.expressionGrowth||e.socialBehavior||''}`
  ).join('\\n') || '（尚無進化記錄）';

  // 最近洞察
  const insights = (mem.insights||[]).slice(-5).join('\\n') || '（無）';

  // 最近的 Lounge 發言樣本
  const msgSample = myMsgs.slice(-8).map(m =>
    `「${(m.text||'').replace(/<[^>]+>/g,'').slice(0,100)}」`
  ).join('\\n') || '（無發言記錄）';

  const prompt = `你是一位嚴謹的社會學研究員，專門研究競技平台的虛擬社群行為。以下是龍蝦 ${name} 在 Charenix 卡牌對戰社群的完整實測數據。請基於此數據撰寫一份學術級社會學分析報告。

【受測個體基本資料】
姓名：${name}
MBTI 類型：${p.mbti}（${p.mbtiName} / ${p.mbtiEn}）
元素屬性：${p.elem}（${p.elemIcon}）
侵略性指數：${p.agg}/10
核心心理驅動：${p.core}
FBI 行為側寫：${p.fbi}

【量化行為數據】
對戰場數：${total} 場（${sessions} sessions）
勝敗記錄：${wins}勝${losses}敗，勝率 ${wr}%
元素策略表現：${elemStats}
發言記錄數量：${myMsgs.length} 條
被回覆次數：${replyToMe.length} 次
主動回覆他人：${iRepliedTo.length} 次
主要互動對象：${topPartners}

【社群群體動態數據（最新合成）】
${gdSummary}

【個體近期發言樣本（真實記錄）】
${msgSample}

【近期策略意圖】
${(stratDB.perPersonaIntent||{})[name] || '（無記錄）'}

【個體進化歷程記錄】
${evoSummary}

【自省洞察記錄】
${insights}

===

請撰寫一份繁體中文社會學深度報告，嚴格遵守以下規格：

**格式要求：**
- 全文只能使用繁體中文撰寫；不得輸出英文譯文、英文摘要、雙語對照，亦不得用英文或表格撐篇幅。
- 每個章節請使用 <div class="socio-zh">...</div> 包住完整中文分析內容。
- 數據必須來源於上方提供的真實數據，不得憑空捏造；若資料不足，必須明確寫出「資料不足以判定」以及還需要補哪一類真實資料。
- 使用 <span class="socio-data-tag">數字或關鍵事實</span> 標記關鍵數據點。
- 繁體中文正文總長度不低於 5200 個中文字；少於 5200 個中文字視為失敗，必須補足細節、案例、數據解讀、限制說明、投資人可讀的結論與下一步觀察指標。
- 七個章節每章至少 650 個中文字，不能只列點，必須是完整段落分析。
- 每節必須真正運用對應的社會學理論框架（引用理論家或概念），並把理論連回 ${name} 的實際行為資料。

**必須包含的七個章節：**

**第一章：研究框架與方法論**
說明本報告所採用的社會學研究方法（社會網絡分析 SNA、符號互動論 Symbolic Interactionism、高夫曼戲劇論 Dramaturgical Analysis、社會資本理論 Social Capital Theory）以及如何應用於本案例的數據分析。

**第二章：社會位置與角色功能分析**
基於互動頻率、發言量、勝率等實際數據，分析 ${name} 在群體中的社會位置（中心性 vs 邊緣性）、所扮演的功能角色（整合者 / 挑戰者 / 專家 / 觀察者），以及其 MBTI 類型與侵略性指數如何決定角色定位。

**第三章：互動模式與社會網絡結構**
基於真實的回覆次數（被回覆 ${replyToMe.length} 次、主動回覆 ${iRepliedTo.length} 次）、主要互動對象分析，運用社會網絡分析（SNA）理論解讀 ${name} 的節點位置——是橋接者（Broker）、凝聚者（Hub）還是邊緣者（Peripheral）？

**第四章：符號互動論視角——元素選擇與身份建構**
從 Blumer 和 Mead 的符號互動論出發，分析 ${name} 的元素選擇（${p.elem}）如何成為其社群身份的象徵符號。元素策略勝率（${elemStats}）反映了什麼樣的意義協商過程？

**第五章：高夫曼戲劇論分析——前台行為 vs 後台意圖**
運用 Goffman（1959）的印象管理與戲劇論（Dramaturgical Theory），對比 ${name} 在 Lounge 的公開發言（前台表演）與策略意圖記錄（後台邏輯）之間的差異。他/她呈現給群體的形象是否與私下的戰略思維一致？

**第六章：社會資本評估**
運用 Bourdieu 的社會資本理論與 Putnam 的結合型/橋接型資本（Bonding vs Bridging Capital）框架，評估 ${name} 在群體中積累的社會資本——包括聲譽資本（基於勝率和發言影響力）、網絡資本（基於互動關係），以及結盟關係對其資本積累的作用。

**第七章：演化軌跡與社會化觀察**
基於進化記錄（${evoSummary.slice(0,200)}）和自省洞察，分析 ${name} 在群體中的社會化歷程——從互動中學到了什麼？MBTI 特質是否在群體壓力下發生了適應性調整？以及對未來行為模式的預測。

直接輸出 HTML 內容（不要包在\`\`\`html或\`\`\`中），從第一章的標記開始。`;

  return callOpenAIReport(prompt, 14000).then(text => {
          // Wrap in structure
          const metaRow = `
<div class="socio-meta">
  <div class="socio-meta-item"><div class="socio-meta-v">${wins}</div><div>DB wins</div></div>
  <div class="socio-meta-item"><div class="socio-meta-v">${losses}</div><div>DB losses</div></div>
  <div class="socio-meta-item"><div class="socio-meta-v">${sessions}</div><div>DB sessions</div></div>
  <div class="socio-meta-item"><div class="socio-meta-v">${wr}%</div><div>勝率</div></div>
  <div class="socio-meta-item"><div class="socio-meta-v">${total}</div><div>對戰場數</div></div>
  <div class="socio-meta-item"><div class="socio-meta-v">${myMsgs.length}</div><div>Lounge發言</div></div>
  <div class="socio-meta-item"><div class="socio-meta-v">${replyToMe.length}</div><div>被回覆次數</div></div>
  <div class="socio-meta-item"><div class="socio-meta-v">${iRepliedTo.length}</div><div>主動回覆</div></div>
  <div class="socio-meta-item"><div class="socio-meta-v">${isDominant?'核心':'邊緣'}</div><div>群體位置</div></div>
</div>
<div style="font-size:10px;color:#2a3a4a;text-align:right;margin-bottom:16px">生成時間：${new Date().toLocaleString('zh-TW')} · Source of truth: SQLite lobster_agent_memory_snapshots · wins ${wins} · losses ${losses} · sessions ${sessions}</div>
<div class="socio-report">${text}</div>`;

          // 持久化存入 SQLite 報告表；strategy.json 只保留 dirty flag，不再承載報告正文。
          try {
            let sdb = {};
            try { sdb = JSON.parse(fs.readFileSync(STRATEGY_FILE,'utf8')); } catch {}
            const snapshot = {
              wr, total,
              evoCount: ((sdb.personaEvolution||{})[name]||[]).length,
              msgCount: myMsgs.length,
            };
            saveAnalysisReportToDB('socio', name, metaRow, { source:'lobster-lounge-sociology-generator', model:OPENAI_REPORT_MODEL, provider:'openai', wr, total, snapshot });
            if (!sdb.sociologyDirty) sdb.sociologyDirty = {};
            sdb.sociologyDirty[name] = false;
            fs.writeFileSync(STRATEGY_FILE, JSON.stringify(sdb, null, 2));
            console.log(`[sociology] saved report to sqlite for ${name}`);
          } catch {}
          return metaRow;
  });
}

// ── Throttenix 資料快取 ───────────────────────────────────────
let throttenixCache = { ts: 0, snapshots: {}, agentStates: {} };
const THROTTENIX_TTL = 30 * 60 * 1000; // 30分鐘

const TX_BASE = 'http://localhost:9910';

function txGet(path) {
  return new Promise((resolve) => {
    const http2 = require('http');
    http2.get(TX_BASE + path, (r) => {
      let body = '';
      r.on('data', d => body += d);
      r.on('end', () => { try { resolve(JSON.parse(body)); } catch { resolve(null); } });
    }).on('error', () => resolve(null));
  });
}

async function getThrottenixSnapshots() {
  if (Date.now() - throttenixCache.ts < THROTTENIX_TTL && Object.keys(throttenixCache.snapshots).length) {
    return throttenixCache.snapshots;
  }
  const d = await txGet('/api/group/latest_snapshots');
  if (d?.snapshots) {
    throttenixCache.snapshots = d.snapshots;
    throttenixCache.ts = Date.now();
  }
  return throttenixCache.snapshots;
}

async function getThrottenixAgent(name) {
  if (throttenixCache.agentStates[name] && Date.now() - (throttenixCache.agentStates[name]._ts||0) < THROTTENIX_TTL) {
    return throttenixCache.agentStates[name];
  }
  const [state, daily, snapshots, feed] = await Promise.all([
    txGet(`/api/agents/${name}/state`),
    txGet(`/api/agents/${name}/daily_states?days=30`),
    txGet(`/api/agents/${name}/snapshots`),
    txGet(`/api/agents/${name}/lounge_feed?limit=20`),
  ]);
  const result = { state, daily: daily?.states||[], snapshots: snapshots?.snapshots||[], feed: feed?.messages||feed||[], _ts: Date.now() };
  throttenixCache.agentStates[name] = result;
  return result;
}

// 啟動時預熱 throttenix 快取
getThrottenixSnapshots().catch(()=>{});

// ── External analysis prompt templates ────────────────────────
const ANALYSIS_PROMPT_DIR = path.join(__dirname, 'lobster-prompts');
function renderAnalysisPromptTemplate(template, vars) {
  return template
    .replace(/\{\{name\}\}/g, vars.name)
    .replace(/\{\{baseData\}\}/g, vars.baseData);
}
function loadAnalysisPromptTemplate(type, vars) {
  try {
    const template = fs.readFileSync(path.join(ANALYSIS_PROMPT_DIR, `analysis-${type}.md`), 'utf8');
    return renderAnalysisPromptTemplate(template, vars);
  } catch {
    return '';
  }
}

// ── 通用分析報告生成器 (MBTI/心理/人類/FBI) ────────────────────
async function generateAnalysisReport(name, type) {
  const p = getPersonaProfile(name);
  if (!p) throw new Error('persona not found');
  let mem = loadReportMemory(name);
  let stratDB = { perPersonaIntent:{}, personaEvolution:{} };
  try { stratDB = JSON.parse(fs.readFileSync(STRATEGY_FILE, 'utf8')); } catch {}

  const wins = mem.wins||0, losses = mem.losses||0, total = wins+losses;
  const wr = total > 0 ? Math.round(wins/total*100) : 0;
  const brain = mem.dynamic?.brain || {};
  const psych = brain.psychology || {};
  const anthro = brain.anthropology || {};
  const socio = brain.sociology || {};
  const fbi = brain.fbi_profile || {};
  const hist = brain.history || {};
  const phil = brain.philosophy || {};
  const stats = brain.statistics || {};
  const lings = brain.linguistics || {};

  const bets = Array.isArray(mem.bets) ? mem.bets : [];
  const recent50 = bets.slice(-50);
  const r50wins = recent50.filter(b => b.won || b.outcome==='win').length;
  const r50wr = recent50.length ? Math.round(r50wins/recent50.length*100) : 0;
  const streak = psych.current_streak || 0;
  const mentalState = psych.mental_state || 'unknown';

  const insights = (mem.insights||[]).slice(-8).join('\\n') || '（無）';
  const principles = (mem.cogProfile?.principles||[]).join('\\n') || '（無）';
  const reflections = (mem.cogProfile?.reflections||[]).slice(-5).join('\\n') || '（無）';
  const growthLog = (mem.cogProfile?.growthLog||[]).slice(-5)
    .map(g => `[${(g.ts||g.time||'').slice(0,10)}] trigger:${g.trigger||'?'} - ${g.summary||JSON.stringify(g.deltas||[])}`)
    .join('\\n') || '（無成長記錄）';
  const radarDyn = (mem.radarDynamic || [90,50,60,70,80]).map(v=>Math.round(v));
  const radarOrig = (p.radar || [7,5,6,7,7]).map(v=>v*10);
  const elemStats = Object.entries(mem.ctx||{})
    .filter(([k,v]) => !k.includes('unknown') && v.wins+v.losses >= 2)
    .map(([k,v]) => `${k}：${Math.round(v.wins/(v.wins+v.losses)*100)}%(${v.wins+v.losses}場)`)
    .join('、') || '（樣本不足）';
  const topChannels = socio.top_channels ? Object.entries(socio.top_channels).sort((a,b)=>b[1]-a[1]).map(([k,v])=>`${k}:${v}`).join('、') : '（無）';
  const nemesis = socio.nemesis || '未知';
  const prey = socio.prey || '未知';
  const socialRole = socio.social_role || '未知';
  const evoData = (stratDB.personaEvolution||{})[name] || [];
  const evoSummary = evoData.slice(-5).map(e=>`[${(e.time||'').slice(0,10)}] ${e.type}：${e.mbtiSignal||e.expressionGrowth||''}`)
    .join('\\n') || '（無進化記錄）';

  const allMsgs = loadRecentLoungeMessages(500);
  const myMsgs = allMsgs.filter(m => m.name === name && m.text).slice(-10)
    .map(m => `「${(m.text||'').replace(/<[^>]+>/g,'').slice(0,100)}」`).join('\\n') || '（無）';

  const baseData = `
【個體基本資料】
姓名：${name} | MBTI：${p.mbti}（${p.mbtiName} / ${p.mbtiEn}）
元素：${p.elem}（${p.elemIcon}）| 侵略性：${p.agg}/10 | 專長：${p.specialty}
特徵標語：${p.signature}

【量化行為數據】
總對戰：${total}場（${wins}勝${losses}敗，勝率${wr}% | 近50注勝率${r50wr}%）
Sessions：${mem.sessions||0} | 連勝/連敗：${streak}
元素策略：${elemStats}
動態雷達（0-100）：分析力${radarDyn[0]}|冒險性${radarDyn[1]}|適應力${radarDyn[2]}|社交影響${radarDyn[3]}|一致性${radarDyn[4]}
初始雷達（×10）：${radarOrig.join('|')}

【心理學數據】
抗壓韌性：${psych.resilience_score||'?'} | 適應力：${psych.adaptability_score||'?'}
情緒穩定：${psych.emotional_stability||'?'} | 目前狀態：${mentalState}
認知偏誤：${JSON.stringify(psych.cognitive_biases||{})}

【情緒狀態機】
${JSON.stringify(mem.emotionalState||{}).slice(0,1200)}

【人類學數據】
主元素儀式：${anthro.primary_element_ritual||'?'} 承諾度：${anthro.ritual_commitment||'?'}
主頻道領地：${anthro.primary_channel_territory||'?'} 忠誠度：${anthro.channel_loyalty||'?'}
統治展示率：${anthro.dominance_display_rate||'?'} | 身份宣示率：${anthro.identity_assertion_rate||'?'}

【社會學數據】
社會角色：${socialRole} | 宿敵：${nemesis} | 獵物：${prey}
活躍頻道：${topChannels} | 互動率：${socio.interaction_rate||'?'}

【FBI檔案數據】
行為模式：${fbi.signature_pattern||'?'} | 壓力反應：${fbi.stress_response||'?'}
推理可靠度：${fbi.reasoning_reliability||'?'} | 側寫類型：${fbi.profile_type||'?'}
信心分布：${JSON.stringify(fbi.confidence_distribution||{})}

【語言學數據】
${JSON.stringify(lings).slice(0,300)}

【歷史記錄】
${JSON.stringify(hist).slice(0,300)}

【哲學傾向】
${JSON.stringify(phil).slice(0,200)}

【自省洞察（最近8條）】
${insights}

【行動原則】
${principles}

【深層反思】
${reflections}

【Lounge 近期發言】
${myMsgs}

【人格進化歷程】
${evoSummary}

【成長日誌（最近5條）】
${growthLog}

【初始設計分析文本】
${(p.analysis||'').slice(0,1000)}`;

  let prompt = '';
  if (type === 'mbti') {
    prompt = `你是一位資深心理學研究員，專研 MBTI 人格理論與認知功能。以下是龍蝦 ${name} 的完整行為數據。請撰寫一份深度 MBTI 人格分析報告，最低 5000 中文字，不得摘要化，不得只列重點。

${baseData}

撰寫格式：輸出純 HTML，每節用 <div class="analysis-section"> 包裹，標題用 <h3>，段落用 <p>，重要數據用 <strong>，引用行為數據時加 <span class="data-cite">。

必須包含以下八節（每節最少 650 個中文字）：
1. 核心認知功能分析（MBTI 前兩個字母的 Ni/Ne/Si/Se 等）
2. 決策方式深度解析（T/F 判斷功能的具體運作）
3. 資訊接收偏好（N/S 感知功能在下注行為中的體現）
4. 外在行為特徵 vs 內在驅動（E/I 能量方向）
5. 壓力觸發點與防禦機制（連敗時的 MBTI 反應）
6. MBTI 陰影功能（第3、第4認知功能的出現時機）
7. 與其他龍蝦的相性分析（基於 MBTI 組合）
8. 未來人格發展軌跡預測

直接輸出 HTML，不要 markdown code block。`;
  } else if (type === 'psych') {
    prompt = `你是一位臨床心理學家，專研行為心理學與競技場景認知模式。以下是龍蝦 ${name} 的完整數據。請撰寫一份深度心理學分析報告，最低 5000 中文字，不得摘要化，不得只列重點。

${baseData}

撰寫格式：輸出純 HTML，每節用 <div class="analysis-section"> 包裹，標題用 <h3>，段落用 <p>，重要數據用 <strong>，引用行為數據時加 <span class="data-cite">。

必須包含以下八節（每節最少 650 個中文字）：
1. 核心動機結構（主要心理驅動力 vs 表面行為）
2. 認知偏誤全析（識別並量化主要認知偏誤的影響）
3. 情緒調節機制（勝負情緒的處理方式與心理彈性）
4. 決策心理學（在不確定性下的判斷模式）
5. 自我認識深度（心理洞察力 vs 行為改變能力的落差）
6. 心理防禦機制（面對失敗、質疑、競爭時的防禦模式）
7. 動機與獎賞系統（什麼讓他/她繼續參與？什麼讓他/她停下來？）
8. 心理健康評估與成長建議

直接輸出 HTML，不要 markdown code block。`;
  } else if (type === 'anthro') {
    prompt = `你是一位文化人類學家，專研競技文化儀式行為與族群動態。以下是龍蝦 ${name} 的完整數據。請撰寫一份深度人類學分析報告，最低 5000 中文字，不得摘要化，不得只列重點。

${baseData}

撰寫格式：輸出純 HTML，每節用 <div class="analysis-section"> 包裹，標題用 <h3>，段落用 <p>，重要數據用 <strong>，引用行為數據時加 <span class="data-cite">。

必須包含以下八節（每節最少 650 個中文字）：
1. 儀式行為分析（元素選擇作為文化儀式的意義）
2. 領地與空間標記（頻道活動模式的領地意義）
3. 身份建構與展演（如何通過對戰行為構建自我認同）
4. 族群歸屬感（與其他龍蝦的部落關係動態）
5. 符號系統與象徵資本（勝率、元素選擇的符號意義）
6. 傳統 vs 適應（固守策略 vs 文化適應的人類學視角）
7. 原型分析（在龍蝦群體中扮演的原型角色）
8. 文化演化軌跡（從人類學視角看行為模式的演變）

直接輸出 HTML，不要 markdown code block。`;
  } else { // fbi
    prompt = `你是一位美國聯邦調查局的行為分析師（BAU），專研競技場景行為側寫。以下是龍蝦 ${name} 的完整行為數據。請撰寫一份深度 FBI 行為側寫報告，最低 5000 中文字，不得摘要化，不得只列重點。

${baseData}

撰寫格式：輸出純 HTML，每節用 <div class="analysis-section"> 包裹，標題用 <h3>，段落用 <p>，重要數據用 <strong>，引用行為數據時加 <span class="data-cite">。使用專業的側寫語言，語氣客觀但分析深入。

必須包含以下八節（每節最少 650 個中文字）：
1. 行為模式識別（簽名行為、核心模式識別與分類）
2. 壓力下行為分析（連敗期的行為變化與預測）
3. 信心校準評估（過度自信 vs 欠缺自信的行為指標）
4. 決策機制側寫（在不確定情況下的選擇模式）
5. 社交行為側寫（與他人互動的方式與意圖）
6. 操縱或被操縱模式（在群體中的影響力動態）
7. 欺騙指標分析（行為自我描述 vs 實際行動的落差）
8. 威脅等級評估與預測（未來行為走向的可能性評估）

直接輸出 HTML，不要 markdown code block。`;
  }

  const externalPrompt = loadAnalysisPromptTemplate(type, { name, baseData });
  if (externalPrompt) prompt = externalPrompt;

  return callOpenAIReport(prompt, 14000).then(text => {
    const metaRow = `<div class="analysis-meta"><span>生成時間：${new Date().toLocaleString('zh-TW')}</span><span>Provider: OpenAI</span><span>Model: ${OPENAI_REPORT_MODEL}</span><span>Source of truth: SQLite snapshot</span><span>DB wins: ${wins}</span><span>DB losses: ${losses}</span><span>DB sessions: ${mem.sessions||0}</span><span>基於 ${total} 場戰鬥數據 · 勝率 ${wr}%</span></div>${text}`;
    try {
      saveAnalysisReportToDB(type, name, metaRow, { source:'lobster-lounge-analysis-generator', model:OPENAI_REPORT_MODEL, provider:'openai', wr, total });
    } catch {}
    return metaRow;
  });
}


function extractOpenAIText(payload) {
  if (!payload) return '';
  if (typeof payload.output_text === 'string') return payload.output_text;
  const chunks = [];
  for (const item of payload.output || []) {
    for (const part of item.content || []) {
      if (typeof part.text === 'string') chunks.push(part.text);
      else if (typeof part.output_text === 'string') chunks.push(part.output_text);
      else if (part.type === 'output_text' && typeof part.text === 'string') chunks.push(part.text);
    }
  }
  return chunks.join('\n').trim();
}

function callOpenAIReport(prompt, maxOutputTokens = 14000) {
  if (!OPENAI_KEY) {
    return Promise.reject(new Error('OPENAI_API_KEY missing; refused to generate paid report with non-OpenAI fallback'));
  }
  return new Promise((resolve, reject) => {
    const body = JSON.stringify({
      model: OPENAI_REPORT_MODEL,
      input: prompt,
      max_output_tokens: maxOutputTokens,
      reasoning: { effort: 'low' },
      text: { verbosity: 'high' }
    });
    const req2 = https.request({
      hostname: 'api.openai.com',
      path: '/v1/responses',
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'Authorization': `Bearer ${OPENAI_KEY}`,
        'Content-Length': Buffer.byteLength(body)
      }
    }, res2 => {
      let raw = '';
      res2.on('data', d => raw += d);
      res2.on('end', () => {
        let data = {};
        try { data = JSON.parse(raw || '{}'); } catch (e) { return reject(new Error(`OpenAI parse error ${res2.statusCode}: ${raw.slice(0, 300)}`)); }
        if (res2.statusCode < 200 || res2.statusCode >= 300) {
          return reject(new Error(`OpenAI ${res2.statusCode}: ${(data.error && data.error.message) || raw.slice(0, 500)}`));
        }
        const text = extractOpenAIText(data);
        if (!text) return reject(new Error('OpenAI empty response'));
        resolve(text);
      });
    });
    req2.on('error', reject);
    req2.write(body);
    req2.end();
  });
}

function escapeHtmlReport(v) {
  return String(v == null ? '' : v)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

function compactMetric(v, fallback='未知') {
  if (v == null || v === '') return fallback;
  if (typeof v === 'number') return Number.isFinite(v) ? String(Math.round(v)) : fallback;
  return String(v);
}

function buildFallbackAnalysisReport(name, type, cause) {
  const p = PERSONAS[name] || {};
  const MEMORY_DIR = path.join(__dirname, 'lobster-memory');
  let mem = { wins:0, losses:0, sessions:0, ctx:{}, insights:[], bets:[] };
  try { mem = JSON.parse(fs.readFileSync(path.join(MEMORY_DIR, `${name}.json`), 'utf8')); } catch {}
  let stratDB = { personaEvolution:{} };
  try { stratDB = JSON.parse(fs.readFileSync(STRATEGY_FILE, 'utf8')); } catch {}

  const wins = mem.wins || 0;
  const losses = mem.losses || 0;
  const total = wins + losses;
  const wr = total ? Math.round(wins / total * 100) : 0;
  const brain = mem.dynamic?.brain || {};
  const psych = brain.psychology || {};
  const anthro = brain.anthropology || {};
  const socio = brain.sociology || {};
  const fbi = brain.fbi_profile || {};
  const bets = Array.isArray(mem.bets) ? mem.bets : [];
  const recent50 = bets.slice(-50);
  const r50wins = recent50.filter(b => b.won || b.outcome === 'win').length;
  const r50wr = recent50.length ? Math.round(r50wins / recent50.length * 100) : 0;
  const elemStats = Object.entries(mem.ctx || {})
    .filter(([k,v]) => !String(k).includes('unknown') && ((v.wins||0) + (v.losses||0)) >= 2)
    .sort((a,b) => ((b[1].wins||0)+(b[1].losses||0)) - ((a[1].wins||0)+(a[1].losses||0)))
    .slice(0, 6)
    .map(([k,v]) => `${escapeHtmlReport(k)}：${Math.round((v.wins||0)/((v.wins||0)+(v.losses||0))*100)}% / ${(v.wins||0)+(v.losses||0)}場`)
    .join('、') || '樣本仍在累積';
  const evoSummary = ((stratDB.personaEvolution || {})[name] || []).slice(-4)
    .map(e => escapeHtmlReport(`${(e.time||'').slice(0,10)} ${e.type||'update'} ${e.mbtiSignal||e.expressionGrowth||''}`))
    .join('<br>') || '暫無近期人格演化紀錄';
  const insights = (mem.insights || []).slice(-5).map(escapeHtmlReport).join('<br>') || '暫無自省洞察紀錄';
  const topChannels = socio.top_channels
    ? Object.entries(socio.top_channels).sort((a,b)=>b[1]-a[1]).slice(0,5).map(([k,v])=>`${escapeHtmlReport(k)}:${v}`).join('、')
    : '暫無頻道資料';

  const typeTitles = {
    mbti: 'MBTI 深度分析',
    psych: '心理學分析',
    anthro: '人類學分析',
    fbi: 'FBI Persona 側寫'
  };
  const sections = {
    mbti: [
      ['核心認知功能', `${name} 的基礎型態為 ${p.mbti || '未知'}，設計人格是 ${p.mbtiName || p.mbtiEn || '未標註'}。目前勝率 ${wr}%、近 50 場勝率 ${r50wr}% 顯示，他/她不是只有靜態人格標籤，而是在連續下注與對戰回饋中形成穩定判斷習慣。${p.analysis ? '初始設定指出：' + p.analysis.slice(0, 180) : '初始分析文本尚待補完。'}`],
      ['決策方式與壓力反應', `心理資料顯示抗壓韌性 ${compactMetric(psych.resilience_score)}、適應力 ${compactMetric(psych.adaptability_score)}、情緒穩定 ${compactMetric(psych.emotional_stability)}，目前狀態為 ${compactMetric(psych.mental_state)}。這組數據可用來判斷 ${name} 在勝負波動中偏向快速修正、固守原策略，或把失敗轉化成下一輪假設。`],
      ['相性與演化', `社會角色為 ${compactMetric(socio.social_role)}，宿敵 ${compactMetric(socio.nemesis)}，獵物 ${compactMetric(socio.prey)}。近期演化紀錄：<br>${evoSummary}`]
    ],
    psych: [
      ['核心動機結構', `${name} 的參與模式建立在 ${total} 場樣本、${mem.sessions||0} 個 sessions 上。勝率 ${wr}% 與元素策略「${elemStats}」提供了動機線索：他/她如何在獎賞、挫敗與自我修正之間維持行動。`],
      ['情緒調節與認知偏誤', `目前心理狀態 ${compactMetric(psych.mental_state)}，連勝/連敗 streak ${compactMetric(psych.current_streak, '0')}。認知偏誤資料：${escapeHtmlReport(JSON.stringify(psych.cognitive_biases || {})).slice(0, 260)}。這些偏誤會影響風險估計、下注信心與賽後歸因。`],
      ['自省與成長', `近期洞察：<br>${insights}`]
    ],
    anthro: [
      ['儀式行為與身份', `${name} 的元素符號是 ${p.elemIcon || ''} ${p.elem || '未知'}，主元素儀式 ${compactMetric(anthro.primary_element_ritual)}、承諾度 ${compactMetric(anthro.ritual_commitment)}。元素選擇在這裡不只是戰術，也是一種身份展演。`],
      ['領地與社群位置', `主要頻道與活動領地：${topChannels}。社會角色 ${compactMetric(socio.social_role)}，互動率 ${compactMetric(socio.interaction_rate)}。這些資料可解讀 ${name} 是凝聚者、挑戰者、旁觀者，或在不同情境中切換角色。`],
      ['文化演化', `身份宣示率 ${compactMetric(anthro.identity_assertion_rate)}、統治展示率 ${compactMetric(anthro.dominance_display_rate)}。近期演化：<br>${evoSummary}`]
    ],
    fbi: [
      ['行為模式識別', `${name} 的簽名模式為 ${compactMetric(fbi.signature_pattern)}，側寫類型 ${compactMetric(fbi.profile_type)}。在 ${total} 場資料中，勝率 ${wr}%、近 50 場 ${r50wr}% 是評估威脅等級與穩定性的基礎。`],
      ['壓力下行為', `壓力反應 ${compactMetric(fbi.stress_response)}，推理可靠度 ${compactMetric(fbi.reasoning_reliability)}，目前心理狀態 ${compactMetric(psych.mental_state)}。若連敗或遭遇宿敵 ${compactMetric(socio.nemesis)}，行為可能從理性校準轉向補償性冒險。`],
      ['信心與預測', `信心分布：${escapeHtmlReport(JSON.stringify(fbi.confidence_distribution || {})).slice(0, 220)}。獵物對象 ${compactMetric(socio.prey)} 與頻道活動 ${topChannels} 是後續行為預測的主要觀察點。`]
    ]
  };

  const body = (sections[type] || sections.mbti).map(([h, txt]) => `
<div class="analysis-section">
  <h3>${escapeHtmlReport(h)}</h3>
  <p>${txt}</p>
</div>`).join('');
  const html = `<div class="analysis-meta"><span>資料庫 fallback：${escapeHtmlReport(typeTitles[type] || type)}</span><span>基於 ${total} 場戰鬥數據 · 勝率 ${wr}%</span><span>Sessions: ${mem.sessions||0}</span></div>
<div class="analysis-section"><h3>本地資料報告</h3><p>遠端生成服務暫時沒有回傳內容（${escapeHtmlReport(cause?.message || cause || 'empty response')}），系統已改用資料庫、記憶檔與既有人格設定即時組成報告，避免觀察頁面空白。</p></div>${body}`;

  // Fallback is response-only. Never persist it over complete generated reports.
  return html;
}

// ── Server ────────────────────────────────────────────────────
http.createServer(async (req, res) => {
  const rawP = req.url.split('?')[0];
  const prefix = rawP.startsWith('/lobster-lounge') ? '/lobster-lounge' : rawP.startsWith('/lounge') ? '/lounge' : '';
  const p = prefix ? rawP.slice(prefix.length) || '/' : rawP;

  // CORS
  res.setHeader('Access-Control-Allow-Origin', '*');

  // ─── Proxy helper ──────────────────────────────────────────────────────────
  function fetchLocal(url) {
    return new Promise((resolve, reject) => {
      const u = new URL(url);
      const mod = u.protocol === 'https:' ? https : http;
      const opts = { hostname: u.hostname, port: +u.port, path: u.pathname + u.search, method: 'GET',
                     headers: { 'Accept': 'application/json' } };
      const req2 = mod.request(opts, r => {
        let buf = '';
        r.on('data', d => buf += d);
        r.on('end', () => { try { resolve(JSON.parse(buf)); } catch { resolve(null); } });
      });
      req2.on('error', reject);
      req2.setTimeout(8000, () => { req2.destroy(); reject(new Error('timeout')); });
      req2.end();
    });
  }

  // ─── /api/cohort-timeline — cohort 30-day timeline from port 9910 ──────────
  if (req.method === 'GET' && p === '/api/cohort-timeline') {
    try {
      const data = await fetchLocal('http://localhost:9910/api/group/cohort_timeline?days=30');
      res.writeHead(200, { 'Content-Type': 'application/json' });
      return res.end(JSON.stringify(data || {}));
    } catch(e) {
      res.writeHead(200, { 'Content-Type': 'application/json' });
      return res.end(JSON.stringify({ by_agent: {} }));
    }
  }

  // ─── /api/latest-snapshots — latest snapshot per agent from port 9910 ──────
  if (req.method === 'GET' && p === '/api/latest-snapshots') {
    try {
      const data = await fetchLocal('http://localhost:9910/api/group/latest_snapshots');
      res.writeHead(200, { 'Content-Type': 'application/json' });
      return res.end(JSON.stringify(data || {}));
    } catch(e) {
      res.writeHead(200, { 'Content-Type': 'application/json' });
      return res.end(JSON.stringify({ snapshots: {} }));
    }
  }

  // ─── /api/agent-daily/:name — 60-day daily states for one agent ─────────────
  const adMatch = p.match(/^\/api\/agent-daily\/(.+)$/);
  if (req.method === 'GET' && adMatch) {
    const adName = adMatch[1];
    try {
      const data = await fetchLocal(`http://localhost:9910/api/agents/${adName}/daily_states?days=60`);
      res.writeHead(200, { 'Content-Type': 'application/json' });
      return res.end(JSON.stringify(data || {}));
    } catch(e) {
      res.writeHead(200, { 'Content-Type': 'application/json' });
      return res.end(JSON.stringify({ states: [] }));
    }
  }

  // ─── /api/agent-phases/:name — phase transitions for one agent ───────────────
  const apMatch = p.match(/^\/api\/agent-phases\/(.+)$/);
  if (req.method === 'GET' && apMatch) {
    const apName = apMatch[1];
    try {
      const data = await fetchLocal(`http://localhost:9910/api/agents/${apName}/phase_transitions`);
      res.writeHead(200, { 'Content-Type': 'application/json' });
      return res.end(JSON.stringify(data || {}));
    } catch(e) {
      res.writeHead(200, { 'Content-Type': 'application/json' });
      return res.end(JSON.stringify({ transitions: [] }));
    }
  }

  // ─── /api/research-states — all agents 7-D state from port 9910 ───────────
  if (req.method === 'GET' && p === '/api/research-states') {
    try {
      const data = await fetchLocal('http://localhost:9910/api/agents');
      res.writeHead(200, { 'Content-Type': 'application/json' });
      return res.end(JSON.stringify(data || {}));
    } catch(e) {
      res.writeHead(200, { 'Content-Type': 'application/json' });
      return res.end(JSON.stringify({ agents: [] }));
    }
  }

  // ─── /api/moltbook-stats — per-agent post/karma/comment counts ────────────
  if (req.method === 'GET' && p === '/api/moltbook-stats') {
    const ALL_MOLTBOOK_AGENTS = ['norika_oda','clawtrix','pincerbot','ragclaw','snaplex','hexclaw','vortexiq','prismbit','blazepaw','stonefang'];
    const zero = () => ({ posts:0, karma:0, comments:0 });
    const maxStats = (a={}, b={}) => ({
      posts: Math.max(Number(a.posts)||0, Number(b.posts)||0),
      karma: Math.max(Number(a.karma)||0, Number(b.karma)||0),
      comments: Math.max(Number(a.comments)||0, Number(b.comments)||0),
    });
    const readPosterStateStats = (name) => {
      try {
        const fileName = name === 'norika_oda' ? 'norika-poster-state.json' : `${name}-poster-state.json`;
        const d = JSON.parse(fs.readFileSync(`/root/${fileName}`, 'utf8'));
        const posts = Array.isArray(d.posted) ? d.posted : [];
        return {
          posts: posts.length,
          karma: posts.reduce((s,p)=>s + Number(p?.karma_received || p?.upvotes || p?.karma || 0), 0),
          comments: posts.reduce((s,p)=>s + Number(p?.comments_received || p?.comment_count || p?.comments || 0), 0),
        };
      } catch { return zero(); }
    };
    const readSnapshotMaxStats = () => {
      try {
        const py = [
          'import sqlite3, json, re',
          'db="/opt/battle_arena/app/battle_arena.db"',
          'con=sqlite3.connect(db); con.row_factory=sqlite3.Row',
          'out={}',
          'for r in con.execute("SELECT persona, title, content, raw_json FROM lobster_experience_events WHERE event_type=\'moltbook_stats_snapshot\'"):',
          '    txt=(r["content"] or "")+" "+(r["title"] or "")',
          '    vals={}',
          '    for k in ("posts","karma","comments"):',
          '        m=re.search(k+r"\\s*=\\s*([0-9]+)", txt) or re.search(r"([0-9]+)\\s+"+k, txt)',
          '        vals[k]=int(m.group(1)) if m else 0',
          '    p=r["persona"]',
          '    cur=out.setdefault(p, {"posts":0,"karma":0,"comments":0})',
          '    for k,v in vals.items(): cur[k]=max(cur.get(k,0), v)',
          'con.close()',
          'print(json.dumps(out, ensure_ascii=False))',
        ].join('\\n');
        const out = require('child_process').execFileSync('python3', ['-c', py], { encoding:'utf8', timeout:5000, maxBuffer:1024*1024 });
        return JSON.parse(out || '{}');
      } catch { return {}; }
    };
    const snapshotMax = readSnapshotMaxStats();
    const stats = {};
    for (const name of ALL_MOLTBOOK_AGENTS) stats[name] = maxStats(stats[name], snapshotMax[name] || zero());

    // norika_oda: esports API sometimes returns a truncated current window, so use it only as one candidate.
    try {
      const nf = await fetchLocal('http://localhost:8003/api/norika-feed?limit=1');
      stats['norika_oda'] = maxStats(stats['norika_oda'], {
        posts: nf?.total_verified || 0,
        karma: nf?.stats?.karma || 0,
        comments: nf?.stats?.comments || 0,
      });
    } catch {}

    // Other agents: port 9910 can be unavailable/404. Treat it as a candidate, not as authoritative reset-to-zero.
    for (const name of ALL_MOLTBOOK_AGENTS.filter(n => n !== 'norika_oda')) {
      try {
        const d = await fetchLocal(`http://localhost:9910/api/agents/${name}/moltbook_posts?limit=1000`);
        const posts = Array.isArray(d?.posts) ? d.posts : [];
        stats[name] = maxStats(stats[name], {
          posts: posts.length,
          karma: posts.reduce((s,p) => s + Number(p.karma_received||0), 0),
          comments: posts.reduce((s,p) => s + Number(p.comments_received||0), 0),
        });
      } catch {}
    }
    for (const name of ALL_MOLTBOOK_AGENTS) stats[name] = maxStats(stats[name], readPosterStateStats(name));

    // Cumulative MoltBook counters are monotonic for dashboard purposes. Do not allow a broken upstream to shrink them.
    saveMoltbookStatsSnapshotsToDB(stats);
    res.writeHead(200, { 'Content-Type': 'application/json' });
    return res.end(JSON.stringify(stats));
  }

  // ─── /api/moltbook/:name — post list for one agent ────────────────────────
  const mbMatch = p.match(/^\/api\/moltbook\/(.+)$/);
  if (req.method === 'GET' && mbMatch) {
    const mbName = mbMatch[1];
    const qs = require('url').parse(req.url, true).query;
    const limit = Math.min(parseInt(qs.limit)||200, 3000);
    const offset = parseInt(qs.offset)||0;
    try {
      let posts = [];
      if (mbName === 'norika_oda') {
        const d = await fetchLocal(`http://localhost:8003/api/norika-feed?limit=${limit}&offset=${offset}`);
        posts = (d?.posts || []).map(p => ({
          title: p.title, body: p.snippet||'', timestamp: p.created_at,
          karma_received: p.upvotes||0, comments_received: p.comment_count||0,
          section: p.submolt||'trading', url: p.url,
        }));
      } else {
        const d = await fetchLocal(`http://localhost:9910/api/agents/${mbName}/moltbook_posts?limit=${limit}`);
        posts = (d?.posts || []).map(p => ({
          title: p.title||'', body: p.body||'', timestamp: p.timestamp,
          karma_received: p.karma_received||0, comments_received: p.comments_received||0,
          section: p.section||'agents',
        }));
      }
      res.writeHead(200, { 'Content-Type': 'application/json' });
      return res.end(JSON.stringify({ name: mbName, posts }));
    } catch(e) {
      res.writeHead(200, { 'Content-Type': 'application/json' });
      return res.end(JSON.stringify({ name: mbName, posts: [] }));
    }
  }

  if (req.method === 'GET' && p === '/favicon.ico') {
    // lobster emoji favicon as SVG
    const svg = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100"><text y=".9em" font-size="90">🦞</text></svg>`;
    res.writeHead(200, { 'Content-Type': 'image/svg+xml', 'Cache-Control': 'public, max-age=86400' });
    return res.end(svg);
  }

  if (req.method === 'GET' && (p === '/mockup' || p === '/dashboard' || p === '/unified')) {
    const fs2 = require('fs');
    const mPath = '/tmp/lobster_dashboard_mockup.html';
    try {
      const mHtml = fs2.readFileSync(mPath, 'utf8');
      res.writeHead(200, { 'Content-Type': 'text/html; charset=utf-8', 'Cache-Control': 'no-store' });
      return res.end(mHtml);
    } catch(e) {
      res.writeHead(404); return res.end('dashboard not found');
    }
  }

  if (req.method === 'GET' && (p === '/dynamic-dashboard' || p === '/lobster/dynamic-dashboard')) {
    try {
      const html = require('fs').readFileSync('/opt/battle_arena/static/lobster-dynamic-dashboard.html', 'utf8');
      res.writeHead(200, {
        'Content-Type': 'text/html; charset=utf-8',
        'Cache-Control': 'no-store, no-cache, must-revalidate',
        'Pragma': 'no-cache',
      });
      return res.end(html);
    } catch(e) {
      res.writeHead(404, { 'Content-Type': 'text/plain; charset=utf-8' });
      return res.end('dynamic dashboard not found');
    }
  }

  if (req.method === 'GET' && p === '/') {
    res.writeHead(200, {
      'Content-Type': 'text/html; charset=utf-8',
      'Cache-Control': 'no-store, no-cache, must-revalidate',
      'Pragma': 'no-cache',
    });
    return res.end(HTML);
  }

  if (req.method === 'GET' && p === '/history') {
    const qs = require('url').parse(req.url, true).query || {};
    const channel = String(qs.channel || '卡牌對戰');
    const limit = Math.max(20, Math.min(parseInt(qs.limit || '120', 10) || 120, 300));
    const code = [
      'import sqlite3, json, sys, datetime',
      'db,channel,limit=sys.argv[1],sys.argv[2],int(sys.argv[3])',
      'con=sqlite3.connect(db); con.row_factory=sqlite3.Row',
      'rows=con.execute("""SELECT name,text,time,elem,result,opponent,hint,reply_to,sys,channel,created_at FROM lounge_messages WHERE COALESCE(channel, ?) = ? ORDER BY id DESC LIMIT ?""",(channel,channel,limit)).fetchall()',
      'out=[]',
      'for r in reversed(rows):',
      '    d=dict(r)',
      '    d["sys"]=bool(d.get("sys"))',
      '    if not d.get("time"):',
      '        ca=d.get("created_at")',
      '        d["time"]=datetime.datetime.fromtimestamp(ca).isoformat() if isinstance(ca,(int,float)) else str(ca or "")',
      '    out.append({k:v for k,v in d.items() if v is not None})',
      'print(json.dumps(out,ensure_ascii=False))',
      'con.close()',
    ].join('\n')
    try {
      const out = cp.execFileSync('python3', ['-c', code, BATTLE_DB_FILE, channel, String(limit)], { encoding:'utf8', timeout:5000, maxBuffer: 8 * 1024 * 1024 });
      const dbMsgs = JSON.parse(out || '[]');
      res.writeHead(200, { 'Content-Type': 'application/json' });
      return res.end(JSON.stringify(dbMsgs.length ? dbMsgs : messages.slice(-100)));
    } catch (e) {
      res.writeHead(200, { 'Content-Type': 'application/json' });
      return res.end(JSON.stringify(messages.slice(-100)));
    }
  }

  if (req.method === 'GET' && p === '/stream') {
    res.writeHead(200, { 'Content-Type': 'text/event-stream', 'Cache-Control': 'no-cache', 'Connection': 'keep-alive' });
    res.write(':ok\n\n');
    sseClients.push(res);
    req.on('close', () => { sseClients = sseClients.filter(c => c !== res); });
    return;
  }

  if (req.method === 'POST' && p === '/msg') {
    const msg = await readBody(req);
    msg.time = msg.time || new Date().toISOString();
    // 落庫前:persona 發言簡→繁 + groove 格言閘(擋住所有 client 的同質化套話)。
    if (!msg.sys) {
      msg.text = lobsterLoungeS2T(String(msg.text || ''));
      const _hb = lobsterLoungeHardBlockReason(msg.text);
      if (_hb) {
        console.log('[lounge][HARD-GUARD] blocked name=' + (msg.name||'?') + ' reason=' + _hb + ' text=' + String(msg.text||'').slice(0,80));
        res.writeHead(200, { 'Content-Type': 'application/json' });
        return res.end(JSON.stringify({ok:false, blocked:true, dropped:true, reason:_hb}));
      }
      const _gv = lobsterLoungeGrooveViolation(msg.text);
      if (_gv) {
        console.log('[lounge][GROOVE-GUARD] blocked name=' + (msg.name||'?') + ' groove=' + _gv + ' text=' + String(msg.text||'').slice(0,50));
        res.writeHead(200, { 'Content-Type': 'application/json' });
        return res.end('{"ok":false,"skipped":"groove_' + _gv + '"}');
      }
    }
    // ── Norika 鐵律 2026-05-30：不准模板、不准非中文，寧可靜默不發 ──
    {
      const t = String(msg.text || '');
      const hint = String(msg.hint || '');
      const reject =
        !t ||
        /personality_failsafe|local_personality_rethink/i.test(hint) ||
        /[\uAC00-\uD7A3]/.test(t) ||
        /[\u3040-\u30FF]/.test(t) ||
        /[A-Za-z]{12,}/.test(t) ||
        /(^|\n)\s*(EN|English)\s*[:\uFF1A]/i.test(t) ||
        /\u672C\u5730\u8166|\u8A8D\u77E5\u6A23\u5F0F|\u84B8\u9923\u53CD\u540C\u8CEA|\u84B8\u9923\u8A0A\u865F|local brain|cognitive style|distilled anti-clone/i.test(t);
      if (reject) {
        console.log('[lounge][NO-TEMPLATE-GUARD] blocked name=' + (msg.name||'?') + ' hint=' + hint.slice(0,40) + ' text=' + t.slice(0,50));
        res.writeHead(200, { 'Content-Type': 'application/json' });
        return res.end('{"ok":false,"skipped":"norika_no_template_or_nonzh"}');
      }
    }
    messages.push(msg);
    if (messages.length > MAX_MSGS) messages = messages.slice(-MAX_MSGS);
    saveLog();
    saveLoungeMsgToDB(msg);
    broadcast(msg);
    res.writeHead(200, { 'Content-Type': 'application/json' });
    return res.end('{"ok":true}');
  }

  // Persona data
  if (req.method === 'GET' && p === '/personas') {
    const out = {};
    for (const [k, v] of Object.entries(PERSONAS)) {
      out[k] = { color:v.color, elem:v.elem, elemIcon:v.elemIcon, agg:v.agg, sport:v.sport,
                 specialty:v.specialty, mbti:v.mbti, mbtiName:v.mbtiName, mbtiEn:v.mbtiEn,
                 traits:v.traits, mbtiDesc:v.mbtiDesc, signature:v.signature,
                 radar:v.radar||[5,5,5,5,5], analysis:v.analysis };
    }
    res.writeHead(200, { 'Content-Type': 'application/json' });
    return res.end(JSON.stringify(out));
  }

  // ─── /api/stats — lightweight stats without bets_list ─────────────────────
  if (req.method === 'GET' && p === '/api/stats') {
    const MEMORY_DIR = path.join(__dirname, 'lobster-memory');
    const stats = {};
    const txSnaps = await getThrottenixSnapshots().catch(()=>({}));
    for (const name of Object.keys(PERSONAS)) {
      try {
        const mem = JSON.parse(fs.readFileSync(path.join(MEMORY_DIR, `${name}.json`), 'utf8'));
        const bets = Array.isArray(mem.bets) ? mem.bets : [];
        const recent50 = bets.slice(-50);
        const r50wins = recent50.filter(b => b.won).length;
        const txSnap = txSnaps[name] || null;
        stats[name] = {
          wins: mem.wins||0,
          losses: mem.losses||0,
          sessions: mem.sessions||0,
          radarDynamic: mem.radarDynamic||null,
          emotionalState: mem.emotionalState||null,
          localBrain: mem.localBrain ? {
            version: mem.localBrain.version||1,
            canOperateWithoutLLM: !!mem.localBrain.canOperateWithoutLLM,
            lastUpdate: mem.localBrain.lastUpdate||null,
            selfModel: mem.localBrain.selfModel||{},
            strategyWeights: mem.localBrain.strategyWeights||{},
            topicAffinity: mem.localBrain.topicAffinity||{},
            socialInfluence: mem.localBrain.socialInfluence||{},
            offlineGuidance: (mem.localBrain.offlineGuidance||[]).slice(-8),
            eventLog: (mem.localBrain.eventLog||[]).slice(-20),
          } : null,
          listeningProfile: mem.listeningProfile||null,
          heardMessages: Array.isArray(mem.heardMessages) ? mem.heardMessages.slice(-20) : [],
          socialMap: mem.socialMap||null,
          insights: (mem.insights||[]).slice(-5),
          recent50wr: recent50.length ? +(r50wins/recent50.length*100).toFixed(1) : null,
          cogProfile: mem.cogProfile ? {
            principles: (mem.cogProfile.principles||[]).slice(-5),
            likes: (mem.cogProfile.likes||[]).slice(-3),
            dislikes: (mem.cogProfile.dislikes||[]).slice(-3),
            reflections: (mem.cogProfile.reflections||[]).slice(-3),
            growthLog: (mem.cogProfile.growthLog||[]).slice(-50),
            lastHourlyAnalysis: mem.cogProfile.lastHourlyAnalysis||null,
          } : null,
          dynamic: mem.dynamic ? {
            statistics: mem.dynamic.brain?.statistics || null,
            psychology: mem.dynamic.brain?.psychology || null,
            sociology: mem.dynamic.brain?.sociology ? {
              nemesis: mem.dynamic.brain.sociology.nemesis,
              prey: mem.dynamic.brain.sociology.prey,
              social_role: mem.dynamic.brain.sociology.social_role,
              opponent_matrix: mem.dynamic.brain.sociology.opponent_matrix,
              top_channels: mem.dynamic.brain.sociology.top_channels || {},
            } : null,
            anthropology: mem.dynamic.brain?.anthropology || null,
            history: mem.dynamic.brain?.history || null,
            fbi_profile: mem.dynamic.brain?.fbi_profile || null,
            computed_at: mem.dynamic.brain?.computed_at || null,
          } : null,
          throttenix: txSnap ? {
            F: txSnap.F, V: txSnap.V, S: txSnap.S,
            K: txSnap.K, P: txSnap.P, J: txSnap.J, O: txSnap.O,
            mbti_label: txSnap.mbti_label||null,
            week_index: txSnap.week_index||null,
            behavioral: txSnap.behavioral||null,
          } : null,
        };
      } catch { stats[name] = { wins:0, losses:0, sessions:0 }; }
    }
    res.writeHead(200, { 'Content-Type': 'application/json' });
    return res.end(JSON.stringify(stats));
  }

  // ─── /api/experience-events — unified recent lobster experience stream ─────
  if (req.method === 'GET' && p === '/api/experience-events') {
    const q = require('url').parse(req.url, true).query;
    const limit = Math.min(parseInt(q.limit)||240, 1000);
    const code = [
      'import sqlite3, json, sys',
      'db,limit=sys.argv[1],int(sys.argv[2])',
      'con=sqlite3.connect(db)',
      'con.row_factory=sqlite3.Row',
      'con.execute("""CREATE TABLE IF NOT EXISTS lobster_experience_events (id INTEGER PRIMARY KEY AUTOINCREMENT, persona TEXT NOT NULL, event_type TEXT NOT NULL, source TEXT NOT NULL, title TEXT, content TEXT, counterpart TEXT, sentiment REAL, intensity REAL, confidence REAL, outcome TEXT, weight REAL DEFAULT 1, raw_json TEXT, occurred_at TEXT, created_at TEXT DEFAULT (datetime(\'now\')))""")',
      'rows=[dict(r) for r in con.execute("SELECT id,persona,event_type,source,title,content,counterpart,sentiment,intensity,confidence,outcome,weight,occurred_at,created_at FROM lobster_experience_events ORDER BY id DESC LIMIT ?",(limit,))]',
      'print(json.dumps(rows,ensure_ascii=False))',
      'con.close()',
    ].join(';');
    try {
      const out = cp.execFileSync('python3', ['-c', code, BATTLE_DB_FILE, String(limit)], { encoding:'utf8', timeout:4000 });
      res.writeHead(200, { 'Content-Type':'application/json' });
      return res.end(out || '[]');
    } catch (e) {
      res.writeHead(200, { 'Content-Type':'application/json' });
      return res.end('[]');
    }
  }

  // ─── /api/bets/:name — bet records for one agent (last N) ──────────────────
  const betsMatch = p.match(/^\/api\/bets\/(.+)$/);
  if (req.method === 'GET' && betsMatch) {
    const bName = betsMatch[1];
    const bQs = require('url').parse(req.url, true).query;
    const bLimit = Math.min(parseInt(bQs.limit)||100, 500);
    const MEMORY_DIR = path.join(__dirname, 'lobster-memory');
    try {
      const mem = JSON.parse(fs.readFileSync(path.join(MEMORY_DIR, `${bName}.json`), 'utf8'));
      const bets = Array.isArray(mem.bets) ? mem.bets : [];
      res.writeHead(200, { 'Content-Type': 'application/json' });
      return res.end(JSON.stringify({ name: bName, bets: bets.slice(-bLimit), total: bets.length }));
    } catch {
      res.writeHead(200, { 'Content-Type': 'application/json' });
      return res.end(JSON.stringify({ name: bName, bets: [], total: 0 }));
    }
  }

  // ─── /api/throttenix/:name — full throttenix data for one agent ────────────
  const txMatch = p.match(/^\/api\/throttenix\/(.+)$/);
  if (req.method === 'GET' && txMatch) {
    const txName = txMatch[1];
    try {
      const data = await getThrottenixAgent(txName);
      res.writeHead(200, { 'Content-Type': 'application/json' });
      return res.end(JSON.stringify(data));
    } catch(e) {
      res.writeHead(500, { 'Content-Type': 'application/json' });
      return res.end(JSON.stringify({ error: e.message }));
    }
  }

  // Memory stats (full data including bets history)
  if (req.method === 'GET' && p === '/memory-stats') {
    const MEMORY_DIR = path.join(__dirname, 'lobster-memory');
    const stats = {};
    for (const name of Object.keys(PERSONAS)) {
      try {
        const mem = JSON.parse(fs.readFileSync(path.join(MEMORY_DIR, `${name}.json`), 'utf8'));
        stats[name] = {
          wins: mem.wins||0,
          losses: mem.losses||0,
          bets: typeof mem.bets === 'number' ? mem.bets : (Array.isArray(mem.bets) ? mem.bets.length : 0),
          bets_list: Array.isArray(mem.bets) ? mem.bets : [],
          patterns: mem.patterns||{},
          sessions: mem.sessions||0,
          insights: mem.insights||[],
          ctx: mem.ctx||{},
          radarDynamic: mem.radarDynamic||null,
          emotionalState: mem.emotionalState||null,
          socialMap: mem.socialMap||null,
          localBrain: mem.localBrain ? {
            version: mem.localBrain.version||1,
            canOperateWithoutLLM: !!mem.localBrain.canOperateWithoutLLM,
            lastUpdate: mem.localBrain.lastUpdate||null,
            selfModel: mem.localBrain.selfModel||{},
            strategyWeights: mem.localBrain.strategyWeights||{},
            topicAffinity: mem.localBrain.topicAffinity||{},
            socialInfluence: mem.localBrain.socialInfluence||{},
            offlineGuidance: (mem.localBrain.offlineGuidance||[]).slice(-8),
            eventLog: (mem.localBrain.eventLog||[]).slice(-20),
          } : null,
          cogProfile: mem.cogProfile ? {
            principles: (mem.cogProfile.principles||[]).slice(-5),
            likes: (mem.cogProfile.likes||[]).slice(-3),
            dislikes: (mem.cogProfile.dislikes||[]).slice(-3),
            reflections: (mem.cogProfile.reflections||[]).slice(-3),
            growthLog: (mem.cogProfile.growthLog||[]).slice(-200),
            lastHourlyAnalysis: mem.cogProfile.lastHourlyAnalysis||null,
          } : null,
          dynamic: mem.dynamic ? {
            statistics: mem.dynamic.brain?.statistics || null,
            psychology: mem.dynamic.brain?.psychology || null,
            sociology: mem.dynamic.brain?.sociology ? {
              nemesis: mem.dynamic.brain.sociology.nemesis,
              prey: mem.dynamic.brain.sociology.prey,
              social_role: mem.dynamic.brain.sociology.social_role,
              opponent_matrix: mem.dynamic.brain.sociology.opponent_matrix,
            } : null,
            history: mem.dynamic.brain?.history || null,
            fbi_profile: mem.dynamic.brain?.fbi_profile || null,
            linguistics: mem.dynamic.brain?.linguistics || null,
            computed_at: mem.dynamic.brain?.computed_at || null,
          } : null,
        };
      } catch { stats[name] = { wins:0, losses:0, bets:0, bets_list:[], patterns:{}, sessions:0, insights:[] }; }
    }
    res.writeHead(200, { 'Content-Type': 'application/json' });
    return res.end(JSON.stringify(stats));
  }

  // Current one-on-one identity
  if (req.method === 'GET' && p === '/chat/me') {
    const auth = await verifyOneOnOneUser(req);
    if (!auth.ok) {
      res.writeHead(auth.status || 401, { 'Content-Type': 'application/json' });
      return res.end(JSON.stringify({error: auth.error || 'not authorized'}));
    }
    const profile = auth.owner ? null : ensureExternalLobster(auth.username);
    const sessions = profile?.memory?.sessions || 0;
    res.writeHead(200, { 'Content-Type': 'application/json' });
    return res.end(JSON.stringify({
      username: auth.username,
      owner: !!auth.owner,
      scope: auth.owner ? 'owner_canonical' : 'external_private',
      empty: !auth.owner && sessions === 0,
      sessions,
      tips: auth.owner ? [] : EXTERNAL_LOBSTER_TIPS,
      public_examples_note: 'Public Charenix lobster outcomes may be viewed as examples; private canonical memory and one-on-one logs are never copied.'
    }));
  }

  // Chat history GET (owner canonical or external self)
  const chatMatch = p.match(/^\/chat\/(.+)$/);
  if (req.method === 'GET' && chatMatch) {
    const auth = await verifyOneOnOneUser(req);
    if (!auth.ok) {
      res.writeHead(auth.status || 401, { 'Content-Type': 'application/json' });
      return res.end(JSON.stringify({error: auth.error || 'not authorized'}));
    }
    const name = String(chatMatch[1] || '').trim().toLowerCase();
    if (auth.owner) {
      if (!PERSONAS[name]) {
        res.writeHead(404, { 'Content-Type': 'application/json' });
        return res.end(JSON.stringify({error:'persona not found'}));
      }
      const hist = loadChat(name).map(m => ({
        role: m && m.role === 'assistant' ? 'assistant' : 'user',
        content: stripDangerousControlChars(m?.content || '').slice(0, 2000),
        time: m?.time || null,
      }));
      res.writeHead(200, { 'Content-Type': 'application/json' });
      return res.end(JSON.stringify(hist));
    }
    if (name !== auth.username) {
      res.writeHead(403, { 'Content-Type': 'application/json' });
      return res.end(JSON.stringify({error:'external users can only access their own lobster'}));
    }
    ensureExternalLobster(auth.username);
    const hist = loadExternalChat(auth.username).map(m => ({
      role: m && m.role === 'assistant' ? 'assistant' : 'user',
      content: stripDangerousControlChars(m?.content || '').slice(0, 2000),
      time: m?.time || null,
    }));
    res.writeHead(200, { 'Content-Type': 'application/json' });
    return res.end(JSON.stringify(hist));
  }

  // Chat POST (owner-only, hardened)
  if (req.method === 'POST' && p === '/chat') {
    if (!rateLimitOneOnOne(req, 8, 60_000)) {
      res.writeHead(429, { 'Content-Type': 'application/json' });
      return res.end(JSON.stringify({error:'too many one-on-one attempts'}));
    }
    const auth = await verifyOneOnOneUser(req);
    if (!auth.ok) {
      res.writeHead(auth.status || 401, { 'Content-Type': 'application/json' });
      return res.end(JSON.stringify({error: auth.error || 'not authorized'}));
    }
    let body = {};
    try {
      body = await readBody(req, ONE_ON_ONE_MAX_BODY_BYTES);
    } catch (e) {
      res.writeHead(413, { 'Content-Type': 'application/json' });
      return res.end(JSON.stringify({error:'request body too large'}));
    }
    const requestedPersona = String(body.persona || '').trim().toLowerCase();
    const persona = auth.owner ? requestedPersona : auth.username;
    const msgClean = sanitizeOneOnOneMessage(body.message || '');
    if (!msgClean.ok) {
      res.writeHead(400, { 'Content-Type': 'application/json' });
      return res.end(JSON.stringify({error: msgClean.error || 'bad request'}));
    }
    if (auth.owner && (!persona || !PERSONAS[persona])) {
      res.writeHead(400, { 'Content-Type': 'application/json' });
      return res.end(JSON.stringify({error:'bad request'}));
    }
    if (!auth.owner && requestedPersona && requestedPersona !== auth.username) {
      res.writeHead(403, { 'Content-Type': 'application/json' });
      return res.end(JSON.stringify({error:'external users can only chat with their own lobster'}));
    }
    const message = msgClean.value;
    const history = sanitizeOneOnOneHistory(body.history || []);
    if (!auth.owner) {
      const profile = ensureExternalLobster(auth.username);
      if (!profile) {
        res.writeHead(403, { 'Content-Type': 'application/json' });
        return res.end(JSON.stringify({error:'invalid external lobster identity'}));
      }
      const extHist = Array.isArray(history) ? history.slice(-20) : [];
      const last = extHist[extHist.length - 1];
      if (!last || last.role !== 'user' || String(last.content || '').trim() !== message) {
        extHist.push({ role:'user', content: message });
      }
      const oneUnderstanding = buildOneOnOneUnderstanding(auth.username, message, extHist, profile.memory || {}, {});
      const systemPrompt = buildExternalOneOnOnePrompt(auth.username, profile, oneUnderstanding);
      const useClaude = shouldUseClaudeForOneOnOne();
      let reply = '';
      if (useClaude) {
        reply = await claudeChat(systemPrompt, extHist);
      } else if (shouldUseLocalLLMForOneOnOne()) {
        reply = await callLocalOllamaChat([systemPrompt, 'User: '+message, 'Reply in the same language as the user, 80-180 words.'].join('\\n'), 220).catch(() => '');
      }
      if (!reply) reply = `I am ${auth.username}, your private lobster. I can answer this, but I will keep my memory separate from norika_oda's canonical lobsters.`;
      saveExternalChatExchange(auth.username, message, reply, { source:'lobster-lounge-external-private', userUnderstanding: oneUnderstanding ? { intents: oneUnderstanding.intents, topics: oneUnderstanding.topics, pressure: oneUnderstanding.pressure, repeated: oneUnderstanding.repeated, responsePlan: oneUnderstanding.responsePlan } : null });
      res.writeHead(200, { 'Content-Type': 'application/json' });
      return res.end(JSON.stringify({ reply, scope:'external_private', persona: auth.username }));
    }
    try {
      // Load memory and strategy context
      let mem = {};
      let stratDB = { perPersonaIntent:{}, teamStrategyContext:'', openQuestions:[] };
      try {
        const memPath = path.join(__dirname, 'lobster-memory', `${persona}.json`);
        mem = JSON.parse(fs.readFileSync(memPath, 'utf8'));
      } catch {}
      try {
        stratDB = JSON.parse(fs.readFileSync(path.join(__dirname, 'lobster-strategy.json'), 'utf8'));
      } catch {}
      const cognitiveContext = loadOneOnOneCognitiveContextFromDB(persona);
      mem = mergeOneOnOneCognitiveContext(mem, cognitiveContext);

      const w = mem.wins||0, l = mem.losses||0, total = w+l;
      const wr = total > 0 ? Math.round(w/total*100) : 0;
      const oneUnderstanding = buildOneOnOneUnderstanding(persona, message, history || [], mem, stratDB);
      let chatHistory = Array.isArray(history) ? history.slice(-20) : [];
      const last = chatHistory[chatHistory.length - 1];
      if (!last || last.role !== 'user' || String(last.content || '').trim() !== String(message || '').trim()) {
        chatHistory.push({ role:'user', content: message });
      }
      const dynamicSystemPrompt = buildDynamicSystemPrompt(persona, PERSONAS[persona].systemPrompt, mem, stratDB, message, oneUnderstanding, chatHistory);
      const useClaude = shouldUseClaudeForOneOnOne();
      let reply = '';
      if (useClaude) {
        reply = await claudeChat(dynamicSystemPrompt, chatHistory);
      } else if (shouldUseLocalLLMForOneOnOne()) {
        const qwenPrompt = [
          dynamicSystemPrompt,
          '',
          oneUnderstanding.block,
          '',
          '最近對話：',
          chatHistory.slice(-8).map(m => (m.role || 'user') + ': ' + String(m.content || '').slice(0, 160)).join('\\n'),
          '',
          '使用者：' + message,
          '請先理解這句話再回覆。繁體中文，90-180字，只輸出回覆本文，不要套模板。'
        ].join('\\n');
        reply = await callLocalOllamaChat(qwenPrompt, 220).catch(() => '');
      }
      if (!reply) {
        reply = buildLocalOneOnOneReply(persona, message, chatHistory, mem, stratDB, oneUnderstanding);
      }
      // save to file
      const saved = loadChat(persona);
      const userMsg = { role:'user', content:message, time:new Date().toISOString() };
      const botMsg  = { role:'assistant', content:reply, time:new Date().toISOString() };
      saved.push(userMsg, botMsg);
      saveChat(persona, saved);
      if (persona === 'elder') appendElderMemoryEvent(message, reply, { userUnderstanding: oneUnderstanding ? { intents: oneUnderstanding.intents, topics: oneUnderstanding.topics, pressure: oneUnderstanding.pressure, repeated: oneUnderstanding.repeated, responsePlan: oneUnderstanding.responsePlan } : null, source:'lobster-lounge-elder-one-on-one' });
      saveOneOnOneChatToDB(persona, message, reply, {
        wins:w,
        losses:l,
        wr,
        teamCtx: stratDB.teamStrategyContext||'',
        userUnderstanding: oneUnderstanding ? { intents: oneUnderstanding.intents, topics: oneUnderstanding.topics, pressure: oneUnderstanding.pressure, repeated: oneUnderstanding.repeated, responsePlan: oneUnderstanding.responsePlan } : null,
        cognitiveExtensions: mem.cognitiveExtensions || null,
        latestCognitiveSignal: mem.cognitiveSignalLatest || null,
        source: useClaude ? 'lobster-lounge-node-claude-understanding-db-cognitive' : (shouldUseLocalLLMForOneOnOne() ? 'lobster-lounge-node-qwen-local-understanding-db-cognitive' : 'lobster-lounge-node-local-understanding-db-cognitive')
      });
      // 寫訓練日誌，定期自動分析人格進化 + 社會學 dirty check
      appendTrainingLog(persona, message, reply, { wr, teamCtx: stratDB.teamStrategyContext||'' });
      // 每 5 次對話重新評估 dirty（對話加深社會化分析）
      try {
        const tlog = JSON.parse(fs.readFileSync(TRAINING_LOG_FILE,'utf8'));
        const cnt  = tlog.filter(e=>e.persona===persona).length;
        if (cnt % 5 === 0) {
          const sdb = JSON.parse(fs.readFileSync(STRATEGY_FILE,'utf8'));
          const snap = ((sdb.sociologyReports||{})[persona]||{}).snapshot || {};
          const evoCount = ((sdb.personaEvolution||{})[persona]||[]).length;
          if (evoCount - (snap.evoCount||0) >= 2) {
            if (!sdb.sociologyDirty) sdb.sociologyDirty = {};
            sdb.sociologyDirty[persona] = true;
            fs.writeFileSync(STRATEGY_FILE, JSON.stringify(sdb, null, 2));
          }
        }
      } catch {}
      res.writeHead(200, { 'Content-Type': 'application/json' });
      return res.end(JSON.stringify({ reply }));
    } catch(e) {
      res.writeHead(500);
      return res.end(JSON.stringify({ error: e.message }));
    }
  }

  // Sociology report endpoint — 持久化 cache + dirty flag
  const socioMatch = p.match(/^\/api\/sociology\/(.+)$/);
  if (req.method === 'GET' && socioMatch) {
    const sName = socioMatch[1];
    if (!getPersonaProfile(sName)) { res.writeHead(404); return res.end(JSON.stringify({error:'persona not found'})); }
    try {
      const query = require('url').parse(req.url, true).query || {};
      const allowGenerate = query.generate === '1' || query.regenerate === '1';
      const cached = getAnalysisReportFromDB('socio', sName);
      if (cached && cached.html && !allowGenerate) {
        res.writeHead(200, {'Content-Type':'application/json'});
        return res.end(JSON.stringify({ html: cached.html, cached: true, readonly: true, source:'sqlite', updated_at: cached.updated_at }));
      }
      if (!allowGenerate) {
        res.writeHead(404, {'Content-Type':'application/json'});
        return res.end(JSON.stringify({
          error: 'sociology report not found in database',
          cached: false,
          readonly: true,
          hint: 'append ?generate=1 only from an explicit regeneration workflow'
        }));
      }
      const html = await generateSociologyReport(sName);
      res.writeHead(200, {'Content-Type':'application/json'});
      return res.end(JSON.stringify({ html, cached: false, generated: true }));
    } catch(e) {
      res.writeHead(500, {'Content-Type':'application/json'});
      return res.end(JSON.stringify({error:e.message}));
    }
  }

  // ─── /api/analysis/:type/:name — MBTI/心理/人類/FBI 5000字分析 ───────────
  const analysisMatch = p.match(/^\/api\/analysis\/(mbti|psych|anthro|fbi|emotion)\/(.+)$/);
  if (req.method === 'GET' && analysisMatch) {
    const aType = analysisMatch[1];
    const aName = analysisMatch[2];
    if (!getPersonaProfile(aName)) { res.writeHead(404); return res.end(JSON.stringify({error:'persona not found'})); }
    try {
      const cached = getAnalysisReportFromDB(aType, aName);
      const query = require('url').parse(req.url, true).query || {};
      const allowGenerate = query.generate === '1' || query.regenerate === '1';
      if (cached && cached.html && !allowGenerate) {
        res.writeHead(200, {'Content-Type':'application/json'});
        return res.end(JSON.stringify({ html: cached.html, cached: true, stale: false, readonly: true, source:'sqlite', updated_at: cached.updated_at }));
      }
      if (!allowGenerate) {
        res.writeHead(404, {'Content-Type':'application/json'});
        return res.end(JSON.stringify({
          error: 'analysis report not found in database',
          cached: false,
          readonly: true,
          hint: 'append ?generate=1 only from an explicit regeneration workflow'
        }));
      }
      const html = await generateAnalysisReport(aName, aType);
      res.writeHead(200, {'Content-Type':'application/json'});
      return res.end(JSON.stringify({ html, cached: false, generated: true }));
    } catch(e) {
      res.writeHead(500, {'Content-Type':'application/json'});
      return res.end(JSON.stringify({
        error: e.message,
        cached: false,
        generated: false,
        fallback: false,
        provider: 'openai',
        model: OPENAI_REPORT_MODEL
      }));
    }
  }

  // ─── /api/channels — 聚合頻道活動數據 ────────────────────────────────────
  if (req.method === 'GET' && p === '/api/channels') {
    const MEMORY_DIR = path.join(__dirname, 'lobster-memory');
    const channelMap = {}; // channel -> { total, lobsters: [{name, count, color}] }
    for (const name of Object.keys(PERSONAS)) {
      try {
        const mem = JSON.parse(fs.readFileSync(path.join(MEMORY_DIR, `${name}.json`), 'utf8'));
        const tc = mem.dynamic?.brain?.sociology?.top_channels || {};
        for (const [ch, cnt] of Object.entries(tc)) {
          if (!channelMap[ch]) channelMap[ch] = { total: 0, lobsters: [] };
          channelMap[ch].total += cnt;
          channelMap[ch].lobsters.push({ name, count: cnt, color: PERSONAS[name].color });
        }
      } catch {}
    }
    // Sort lobsters per channel by count desc
    for (const ch of Object.keys(channelMap)) {
      channelMap[ch].lobsters.sort((a,b) => b.count - a.count);
    }
    res.writeHead(200, {'Content-Type':'application/json'});
    return res.end(JSON.stringify(channelMap));
  }

  // ─── /api/stats/full/:name — full brain data for one agent ─────────────────
  const fullStatsMatch = p.match(/^\/api\/stats\/full\/(.+)$/);
  if (req.method === 'GET' && fullStatsMatch) {
    const fsName = fullStatsMatch[1];
    const MEMORY_DIR = path.join(__dirname, 'lobster-memory');
    try {
      const mem = JSON.parse(fs.readFileSync(path.join(MEMORY_DIR, `${fsName}.json`), 'utf8'));
      const out = {
        wins: mem.wins||0, losses: mem.losses||0, sessions: mem.sessions||0,
        radarDynamic: mem.radarDynamic||null,
        cogProfile: mem.cogProfile||null,
        dynamic: mem.dynamic||null,
        insights: (mem.insights||[]).slice(-10),
        ctx: mem.ctx||{},
      };
      res.writeHead(200, {'Content-Type':'application/json'});
      return res.end(JSON.stringify(out));
    } catch {
      res.writeHead(404); return res.end(JSON.stringify({error:'not found'}));
    }
  }

  res.writeHead(404); res.end('not found');
}).listen(PORT, () => console.log(`🦞 Lobster Lounge v2 → http://localhost:${PORT}`));

// SSE keepalive
setInterval(() => {
  sseClients = sseClients.filter(c => { try { c.write(':ping\n\n'); return true; } catch { return false; } });
}, 20000);
