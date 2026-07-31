#!/usr/bin/env node
const fs = require('fs');
const path = require('path');
const http = require('http');
const cp = require('child_process');

const ROOT = '/root';
const MEMORY_DIR = path.join(ROOT, 'lobster-memory');
const STRATEGY_FILE = path.join(ROOT, 'lobster-strategy.json');
const PROMPT_DIR = path.join(ROOT, 'lobster-prompts');
const DB_FILE = '/opt/battle_arena/app/battle_arena.db';
const STATE_FILE = path.join(ROOT, 'lobster-report-worker-state.json');
const LOCK_FILE = '/tmp/lobster-report-worker.lock';
const QUEUE_FILE = path.join(ROOT, 'lobster-report-queue.json');

const LOCAL_LLM_URL = String(process.env.LOBSTER_REPORT_LLM_URL || process.env.LOBSTER_LOCAL_LLM_URL || 'http://100.118.221.12:11434').replace(/\/$/, '');
const LOCAL_LLM_MODEL = String(process.env.LOBSTER_REPORT_LLM_MODEL || process.env.LOBSTER_LOCAL_LLM_MODEL || 'lobster-zh-q2k');
const MAX_REPORTS_PER_RUN = Number(process.env.LOBSTER_REPORTS_PER_RUN || 2);
const MAX_REPORTS_PER_PERSONA_PER_DAY = Number(process.env.LOBSTER_REPORTS_PER_PERSONA_PER_DAY || 6);
const STALE_MS = Number(process.env.LOBSTER_REPORT_STALE_HOURS || 12) * 60 * 60 * 1000;
const QUEUE_RETRY_DELAY_MS = Number(process.env.LOBSTER_REPORT_QUEUE_RETRY_HOURS || 6) * 60 * 60 * 1000;
const MIN_CJK_CHARS = Number(process.env.LOBSTER_REPORT_MIN_CJK_CHARS || 1500);
const MIN_TOTAL_CHARS = Number(process.env.LOBSTER_REPORT_MIN_TOTAL_CHARS || 2200);
const TYPES = ['emotion', 'mbti', 'psych', 'anthro', 'fbi', 'socio'];
const MENTOR_PERSONAS = ['norika_oda','clawtrix','pincerbot','ragclaw','snaplex','hexclaw','vortexiq','prismbit','blazepaw','stonefang'];
const APPRENTICE_PERSONAS = ['emberling','sparkrill','gauntlet','rushfin','nullpinch','brineledger','omenwake','mirrorcalm','flarecub','basaltrook'];
const PERSONAS = [...MENTOR_PERSONAS, ...APPRENTICE_PERSONAS];

function readJson(file, fallback = {}) {
  try { return JSON.parse(fs.readFileSync(file, 'utf8')); } catch { return fallback; }
}
function writeJson(file, data) {
  fs.writeFileSync(file, JSON.stringify(data, null, 2));
}
function todayKey() {
  return new Date().toISOString().slice(0, 10);
}
function stripHtml(s) {
  return String(s || '').replace(/<[^>]+>/g, ' ').replace(/\s+/g, ' ').trim();
}
function quote(v) {
  return String(v == null ? '' : v).replace(/'/g, "''");
}
function getRecentEvents(name, limit = 30) {
  try {
    const sql = "SELECT event_type,source,title,content,outcome,weight,occurred_at FROM lobster_experience_events WHERE persona='" + quote(name) + "' ORDER BY id DESC LIMIT " + limit;
    const raw = cp.execFileSync('sqlite3', ['-json', DB_FILE, sql], { encoding: 'utf8', timeout: 2500 });
    return JSON.parse(raw || '[]');
  } catch {
    return [];
  }
}
function getPromptTemplate(type) {
  try { return fs.readFileSync(path.join(PROMPT_DIR, 'analysis-' + type + '.md'), 'utf8'); } catch { return ''; }
}
function getDbReportIndex() {
  try {
    const raw = cp.execFileSync('sqlite3', ['-json', DB_FILE, 'SELECT persona,report_type,length(html) AS html_len,updated_at FROM lobster_analysis_reports'], { encoding: 'utf8', timeout: 2500, maxBuffer: 1024 * 1024 });
    const rows = JSON.parse(raw || '[]');
    const idx = {};
    for (const r of rows) {
      if (!idx[r.report_type]) idx[r.report_type] = {};
      idx[r.report_type][r.persona] = { html: Number(r.html_len || 0) > 0, ts: Date.parse(r.updated_at || '') || 0 };
    }
    return idx;
  } catch {
    return {};
  }
}
function reportTitle(type) {
  return ({mbti:'MBTI 深度分析', psych:'心理學分析', anthro:'人類學分析', fbi:'FBI 側寫', emotion:'情緒狀態分析', socio:'社會學分析'})[type] || type;
}
function buildBaseData(name) {
  const mem = readJson(path.join(MEMORY_DIR, name + '.json'), {});
  const strat = readJson(STRATEGY_FILE, {});
  const wins = mem.wins || 0, losses = mem.losses || 0, total = wins + losses;
  const wr = total ? Math.round(wins / total * 100) : 0;
  const events = getRecentEvents(name, 36);
  const eventLines = events.map(e =>
    '[' + String(e.occurred_at || '').slice(0, 19) + '] ' +
    e.event_type + '/' + e.source + ': ' +
    stripHtml(e.title || e.content).slice(0, 180) +
    ' outcome=' + (e.outcome || '') + ' weight=' + (e.weight || 1)
  ).join('\n') || '（暫無統一事件）';
  const brain = mem.dynamic?.brain || {};
  const social = mem.socialMap || {};
  const listen = mem.listeningProfile || {};
  const localBrain = mem.localBrain || {};
  const molt = events.find(e => e.event_type === 'moltbook_stats_snapshot');
  const existing = Object.fromEntries(TYPES.map(t => [t, !!strat.analysisReports?.[t]?.[name]?.html]));
  const text = [
    '【資料來源】',
    '本報告由 VPS 排程器收集資料，交由本地筆電 LLM 生成，再寫回 /root/lobster-strategy.json。',
    '生成時間：' + new Date().toISOString(),
    '',
    '【基本狀態】',
    '龍蝦：' + name,
    '對戰：' + total + ' 場，' + wins + ' 勝 / ' + losses + ' 敗，勝率 ' + wr + '%',
    'Sessions：' + (mem.sessions || 0),
    'Moltbook snapshot：' + stripHtml(molt?.title || '（無最新 snapshot）'),
    '',
    '【情緒狀態機】',
    JSON.stringify(mem.emotionalState || {}, null, 2).slice(0, 2200),
    '',
    '【本地腦 localBrain】',
    JSON.stringify({
      selfModel: localBrain.selfModel,
      strategyWeights: localBrain.strategyWeights,
      topicAffinity: localBrain.topicAffinity,
      socialInfluence: localBrain.socialInfluence,
      offlineGuidance: (localBrain.offlineGuidance || []).slice(-8),
      eventLog: (localBrain.eventLog || []).slice(-12),
    }, null, 2).slice(0, 3200),
    '',
    '【dynamic.brain 摘要】',
    JSON.stringify({
      statistics: brain.statistics,
      psychology: brain.psychology,
      anthropology: brain.anthropology,
      sociology: brain.sociology,
      fbi_profile: brain.fbi_profile,
      linguistics: brain.linguistics,
    }, null, 2).slice(0, 3800),
    '',
    '【旁聽與社交】',
    'listeningProfile=' + JSON.stringify(listen, null, 2).slice(0, 1800),
    'socialMap=' + JSON.stringify(social, null, 2).slice(0, 2400),
    '',
    '【最近統一經驗事件】',
    eventLines,
    '',
    '【洞察與反思】',
    'insights=' + JSON.stringify((mem.insights || []).slice(-8), null, 2),
    'cogProfile=' + JSON.stringify({
      principles: mem.cogProfile?.principles,
      reflections: (mem.cogProfile?.reflections || []).slice(-8),
      growthLog: (mem.cogProfile?.growthLog || []).slice(-8),
    }, null, 2).slice(0, 2600),
    '',
    '【既有報告狀態】',
    JSON.stringify(existing, null, 2),
  ].join('\n');
  return { text, wr, total };
}
function buildPrompt(name, type) {
  const base = buildBaseData(name);
  const template = getPromptTemplate(type) || ('請為龍蝦 {{name}} 撰寫 ' + reportTitle(type) + '，最低 1500 中文字，輸出純 HTML。\n\n{{baseData}}');
  const prompt = template
    .replace(/\{\{name\}\}/g, name)
    .replace(/\{\{baseData\}\}/g, base.text);
  return {
    prompt: [
      prompt,
      '',
      '額外要求：',
      '1. 必須根據資料，不要憑空編造。',
      '2. 必須提到「聊天、Moltbook、下注、對戰、旁聽」如何形成動態人格。',
      '3. 請輸出可直接嵌入 dashboard 的 HTML，不要 markdown code block。',
      '4. 如果資料不足，要明確寫「資料不足」，但仍要基於現有資料做分析。',
      '5. 嚴禁 raw-dict-dump：輸出不得包含任何 JSON 物件、Python dict、curly-brace key:value 結構。所有來自 emotionalState/localBrain/societyModel/cognitiveExtensions 的 dict 數字必須翻譯成中文敘事（例：「outGroupSuspicion=0.56」→「對外群的懷疑度 0.56（中高）」）。違反即報告無效。',
      '6. 報告開頭必須有 <div class="analysis-meta-derived"> 區塊，列：主分類（依該報告領域，例如 socio = 沉默旁觀/程序性反抗/保護者/下注間接表態 四選一）、3-5 條中文敘事證據、1-2 條反例。',
      '7. 輸出前自查：掃描自己的回覆，若有 "{" 或 "=" 後接 dict/數字小數的片段，必須改寫成中文句子。',
    ].join('\n'),
    wr: base.wr,
    total: base.total,
  };
}
function callLocalLLM(prompt) {
  return new Promise((resolve, reject) => {
    const url = new URL(LOCAL_LLM_URL);
    const body = JSON.stringify({
      model: LOCAL_LLM_MODEL,
      prompt,
      stream: false,
      options: {
        temperature: 0.45,
        top_p: 0.9,
        repeat_penalty: 1.08,
        num_predict: Number(process.env.LOBSTER_REPORT_NUM_PREDICT || 2800),
      },
    });
    const req = http.request({
      hostname: url.hostname,
      port: url.port || 80,
      path: '/api/generate',
      method: 'POST',
      timeout: Number(process.env.LOBSTER_REPORT_TIMEOUT_MS || 900000),
      headers: {'Content-Type':'application/json','Content-Length':Buffer.byteLength(body)},
    }, res => {
      let raw = '';
      res.on('data', c => raw += c);
      res.on('end', () => {
        try {
          const d = JSON.parse(raw);
          const text = String(d.response || '').trim();
          if (!text) return reject(new Error('empty local LLM response'));
          resolve(text);
        } catch (e) {
          reject(e);
        }
      });
    });
    req.on('timeout', () => req.destroy(new Error('local LLM timeout')));
    req.on('error', reject);
    req.write(body);
    req.end();
  });
}
function chooseJobs() {
  const strategy = readJson(STRATEGY_FILE, {});
  const reports = strategy.analysisReports || {};
  const dbReports = getDbReportIndex();
  const state = readJson(STATE_FILE, {});
  const day = todayKey();
  if (state.day !== day) {
    state.day = day;
    state.personaCounts = {};
  }
  const queued = readJson(QUEUE_FILE, []);
  const queueJobs = Array.isArray(queued) ? queued.filter(queueJobEligible) : [];
  const jobs = [];
  for (const name of PERSONAS) {
    const count = state.personaCounts?.[name] || 0;
    if (count >= MAX_REPORTS_PER_PERSONA_PER_DAY) continue;
    for (const type of TYPES) {
      const strategyEntry = reports?.[type]?.[name];
      const dbEntry = dbReports?.[type]?.[name];
      const entry = strategyEntry?.html ? strategyEntry : (dbEntry || strategyEntry);
      const missing = !entry?.html;
      const dirty = !!entry?.dirty;
      const stale = entry?.ts ? (Date.now() - entry.ts > STALE_MS) : true;
      if (missing || dirty || stale) {
        const priority = (dirty ? 1000 : 0) + (missing ? 500 : 0) + (type === 'emotion' ? 80 : 0) + (Date.now() - (entry?.ts || 0)) / 3600000;
        jobs.push({ name, type, priority, reason: dirty ? 'dirty' : missing ? 'missing' : 'stale' });
        break;
      }
    }
  }
  for (const qj of queueJobs) {
    jobs.push({
      ...qj,
      priority: queueJobPriority(qj),
      reason: qj.reason || 'queued_long_report',
    });
  }
  jobs.sort((a,b) => b.priority - a.priority);
  return { jobs: jobs.slice(0, MAX_REPORTS_PER_RUN), state, queue: queued };
}
function saveReport(name, type, html, meta) {
  const strategy = readJson(STRATEGY_FILE, {});
  if (!strategy.analysisReports) strategy.analysisReports = {};
  if (!strategy.analysisReports[type]) strategy.analysisReports[type] = {};
  const wrapped = '<div class="analysis-meta"><span>本地筆電 LLM：' + LOCAL_LLM_MODEL + '</span><span>生成時間：' + new Date().toLocaleString('zh-TW') + '</span><span>基於 ' + meta.total + ' 場戰鬥 · 勝率 ' + meta.wr + '%</span></div>' + html;
  strategy.analysisReports[type][name] = {
    html: wrapped,
    ts: Date.now(),
    wr: meta.wr,
    total: meta.total,
    source: 'laptop-local-llm',
    model: LOCAL_LLM_MODEL,
    localEndpoint: LOCAL_LLM_URL,
    generatedAt: new Date().toISOString(),
    dirty: false,
  };
  writeJson(STRATEGY_FILE, strategy);
  const dbMeta = {
    source: (meta.reason === 'queued_long_report' || meta.reason === 'below_1500_cjk') ? 'queued-long-report-worker' : 'laptop-local-llm',
    model: LOCAL_LLM_MODEL,
    wr: meta.wr,
    total: meta.total,
    generatedAt: new Date().toISOString(),
    longReportQueue: meta.longReportQueue || false,
    reason: meta.reason || '',
  };
  saveReportToDB(name, type, wrapped, dbMeta);
  saveReportEvent(name, type, html);
}
function saveReportToDB(name, type, html, meta) {
  try {
    const py = [
      'import sqlite3,json,sys,datetime',
      'db,report_type,persona,html,raw=sys.argv[1:6]',
      'meta=json.loads(raw or "{}")',
      'con=sqlite3.connect(db)',
      'con.execute("""CREATE TABLE IF NOT EXISTS lobster_analysis_reports (persona TEXT NOT NULL, report_type TEXT NOT NULL, html TEXT NOT NULL, source TEXT, model TEXT, wr REAL, total INTEGER, snapshot_json TEXT DEFAULT "{}", meta_json TEXT DEFAULT "{}", generated_at TEXT, updated_at TEXT DEFAULT (datetime(\\\'now\\\')), PRIMARY KEY (persona, report_type))""")',
      'con.execute("""INSERT INTO lobster_analysis_reports(persona,report_type,html,source,model,wr,total,snapshot_json,meta_json,generated_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,datetime(\\\'now\\\')) ON CONFLICT(persona,report_type) DO UPDATE SET html=excluded.html, source=excluded.source, model=excluded.model, wr=excluded.wr, total=excluded.total, snapshot_json=excluded.snapshot_json, meta_json=excluded.meta_json, generated_at=excluded.generated_at, updated_at=datetime(\\\'now\\\')""", (persona, report_type, html, meta.get("source"), meta.get("model"), meta.get("wr"), meta.get("total"), json.dumps(meta.get("snapshot") or {}, ensure_ascii=False), json.dumps(meta, ensure_ascii=False), meta.get("generatedAt") or datetime.datetime.utcnow().isoformat()+"Z"))',
      'con.commit(); con.close()',
    ].join(';');
    cp.execFileSync('python3', ['-c', py, DB_FILE, type, name, html, JSON.stringify(meta || {})], { timeout: 5000, maxBuffer: 1024 * 1024 });
  } catch (e) {
    console.error('[report db save failed]', e.message);
  }
}

function cjkLength(text) {
  return Array.from(String(text || '')).filter(ch => ch >= '一' && ch <= '鿿').length;
}

function reportLengthStats(html) {
  const text = stripHtml(html);
  return { textLen: text.length, cjkLen: cjkLength(text) };
}

function reportMeetsLengthMinimum(html) {
  const stats = reportLengthStats(html);
  return stats.cjkLen >= MIN_CJK_CHARS || stats.textLen >= MIN_TOTAL_CHARS;
}

function queueJobEligible(job) {
  if (!job || !job.name || !job.type || job.doneAt) return false;
  const attempts = Number(job.attempts || 0);
  if (attempts < 3) return true;
  const last = Date.parse(job.lastTriedAt || '') || 0;
  return !last || (Date.now() - last) >= QUEUE_RETRY_DELAY_MS;
}

function queueJobPriority(job) {
  const attempts = Number(job.attempts || 0);
  const penalty = Math.max(0, attempts - 2) * 1000;
  return Number(job.priority || 0) - penalty;
}

function markQueueDone(job, html) {
  const q = readJson(QUEUE_FILE, []);
  if (!Array.isArray(q)) return;
  const stats = reportLengthStats(html);
  const textLen = stats.textLen;
  const cjkLen = stats.cjkLen;
  const now = new Date().toISOString();
  for (const item of q) {
    if (!item.doneAt && item.name === job.name && item.type === job.type) {
      item.generatedTextLen = textLen;
      item.generatedCjkLen = cjkLen;
      item.lastTriedAt = now;
      item.attempts = Number(item.attempts || 0) + 1;
      if (reportMeetsLengthMinimum(html)) {
        item.doneAt = now;
        item.status = 'generated';
      } else {
        item.status = 'needs_retry';
        item.priority = Number(item.priority || 10000) + 250;
        item.nextRetryAfter = new Date(Date.now() + QUEUE_RETRY_DELAY_MS).toISOString();
      }
      break;
    }
  }
  writeJson(QUEUE_FILE, q);
}

function saveReportEvent(name, type, html) {
  try {
    const event = {
      persona: name,
      event_type: 'analysis_report_generated',
      source: 'laptop-local-llm',
      title: reportTitle(type) + ' generated by ' + LOCAL_LLM_MODEL,
      content: stripHtml(html).slice(0, 1800),
      outcome: type,
      weight: 1.8,
      occurred_at: new Date().toISOString(),
      model: LOCAL_LLM_MODEL,
    };
    const py = [
      'import sqlite3,json,sys',
      'db,raw=sys.argv[1:3]',
      'e=json.loads(raw)',
      'con=sqlite3.connect(db)',
      'con.execute("""CREATE TABLE IF NOT EXISTS lobster_experience_events (id INTEGER PRIMARY KEY AUTOINCREMENT, persona TEXT NOT NULL, event_type TEXT NOT NULL, source TEXT NOT NULL, title TEXT, content TEXT, counterpart TEXT, sentiment REAL, intensity REAL, confidence REAL, outcome TEXT, weight REAL DEFAULT 1, raw_json TEXT, occurred_at TEXT, created_at TEXT DEFAULT (datetime(\'now\')))""")',
      'con.execute("INSERT INTO lobster_experience_events(persona,event_type,source,title,content,outcome,weight,raw_json,occurred_at) VALUES(?,?,?,?,?,?,?,?,?)",(e.get("persona"),e.get("event_type"),e.get("source"),e.get("title"),e.get("content"),e.get("outcome"),e.get("weight",1),json.dumps(e,ensure_ascii=False),e.get("occurred_at")))',
      'con.commit(); con.close()',
    ].join(';');
    cp.execFileSync('python3', ['-c', py, DB_FILE, JSON.stringify(event)], { timeout: 3000 });
  } catch (e) {
    console.error('[event save failed]', e.message);
  }
}
async function run() {
  if (fs.existsSync(LOCK_FILE)) {
    const age = Date.now() - fs.statSync(LOCK_FILE).mtimeMs;
    if (age < 2 * 60 * 60 * 1000) {
      console.log('[skip] worker lock exists');
      return;
    }
  }
  fs.writeFileSync(LOCK_FILE, String(process.pid));
  try {
    const { jobs, state } = chooseJobs();
    if (!jobs.length) {
      console.log('[ok] no report jobs');
      writeJson(STATE_FILE, state);
      return;
    }
    for (const job of jobs) {
      console.log('[job] ' + job.name + '/' + job.type + ' reason=' + job.reason + ' model=' + LOCAL_LLM_MODEL);
      const built = buildPrompt(job.name, job.type);
      built.reason = job.reason;
      built.longReportQueue = job.reason === 'queued_long_report' || job.reason === 'below_1500_cjk';
      let html;
      try {
        html = await callLocalLLM(built.prompt);
      } catch (e) {
        state.lastError = {
          ...job,
          at: new Date().toISOString(),
          model: LOCAL_LLM_MODEL,
          message: e.message || String(e),
        };
        writeJson(STATE_FILE, state);
        console.error('[job failed] ' + job.name + '/' + job.type + ' ' + state.lastError.message);
        break;
      }
      saveReport(job.name, job.type, html, built);
      markQueueDone(job, html);
      state.personaCounts = state.personaCounts || {};
      state.personaCounts[job.name] = (state.personaCounts[job.name] || 0) + 1;
      state.lastJob = { ...job, at: new Date().toISOString(), model: LOCAL_LLM_MODEL };
      delete state.lastError;
      writeJson(STATE_FILE, state);
      console.log('[saved] ' + job.name + '/' + job.type);
    }
  } finally {
    try { fs.unlinkSync(LOCK_FILE); } catch {}
  }
}

run().catch(e => {
  try { fs.unlinkSync(LOCK_FILE); } catch {}
  console.error('[fatal]', e.stack || e.message);
  process.exitCode = 1;
});
