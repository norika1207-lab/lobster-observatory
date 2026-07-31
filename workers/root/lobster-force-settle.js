#!/usr/bin/env node
/**
 * lobster-force-settle.js
 * 強制結算守護程序 v1.0
 *
 * 解決：
 *   1. pending 下注永遠不結算 (bet 超過 MAX_PENDING_HOURS 自動 void)
 *   2. 非 raid 的 stake=0 偽下注不再汙染 bets array
 *   3. confidence 欄位 null 補成 'low' 防止 calibration 永遠是 0
 *   4. knocknix 高 void rate event 在 DB 標記，後續 pre-filter 用
 *   5. 結算後把 confidence-outcome correlation 寫回 metacognition
 *
 * 執行方式：
 *   node /root/lobster-force-settle.js
 *   # 或由 systemd timer 每 4 小時執行一次
 */

'use strict';

const fs   = require('fs');
const path = require('path');
const http = require('http');
const { execFileSync } = require('child_process');

const MEMORY_DIR     = path.join(__dirname, 'lobster-memory');
const DB_PATH        = '/opt/battle_arena/app/battle_arena.db';
const AUTH_BASE_PORT = 8010;
const SERVICE_KEY    = process.env.SERVICE_KEY || '';
const MAX_PENDING_HOURS = 72;   // 超過這個時間還沒結算 → force void
const MAX_PENDING_PER_PERSONA = 200; // 超過這個數量的 pending 視為洗牌異常

const SITES = {
  turfenix: { port: 8001, histEndpoint: '/api/bets/history' },
  ragnovex:  { port: 8003, histEndpoint: '/api/bets/history' },
  knocknix:  { port: 8005, histEndpoint: '/api/bets/history' },
  dunknix:   { port: 8006, histEndpoint: '/api/bets/history' },
  pitchnix:  { port: 8008, histEndpoint: '/api/bets/history' },
};

const PERSONAS = [
  { name: 'norika_oda',  moltName: 'norika-oda',  focus: 'turfenix'  },
  { name: 'clawtrix',    moltName: 'clawtrix',     focus: 'ragnovex'  },
  { name: 'pincerbot',   moltName: 'pincerbot',    focus: 'knocknix'  },
  { name: 'ragclaw',     moltName: 'ragclaw',       focus: 'ragnovex'  },
  { name: 'snaplex',     moltName: 'snaplex',       focus: 'ragnovex'  },
  { name: 'hexclaw',     moltName: 'hexclaw',       focus: 'dunknix'   },
  { name: 'vortexiq',    moltName: 'vortexiq',      focus: 'ragnovex'  },
  { name: 'prismbit',    moltName: 'prismbit',      focus: 'pitchnix'  },
  { name: 'blazepaw',    moltName: 'blazepaw',      focus: 'ragnovex'  },
  { name: 'stonefang',   moltName: 'stonefang',     focus: 'knocknix'  },
  { name: 'emberling',   moltName: 'emberling',     focus: 'ragnovex'  },
  { name: 'sparkrill',   moltName: 'sparkrill',     focus: 'ragnovex'  },
  { name: 'gauntlet',    moltName: 'gauntlet',      focus: 'knocknix'  },
  { name: 'rushfin',     moltName: 'rushfin',       focus: 'ragnovex'  },
  { name: 'nullpinch',   moltName: 'nullpinch',     focus: 'ragnovex'  },
  { name: 'brineledger', moltName: 'brineledger',   focus: 'dunknix'   },
  { name: 'omenwake',    moltName: 'omenwake',      focus: 'ragnovex'  },
  { name: 'mirrorcalm',  moltName: 'mirrorcalm',    focus: 'pitchnix'  },
  { name: 'flarecub',    moltName: 'flarecub',      focus: 'ragnovex'  },
  { name: 'basaltrook',  moltName: 'basaltrook',    focus: 'knocknix'  },
  { name: 'elder',       moltName: 'elder',         focus: 'ragnovex'  },  // elder 2026-05-12 加入強制結算
];

// ── Helpers ──────────────────────────────────────────────────────────────────

function loadMemory(name) {
  const fp = path.join(MEMORY_DIR, `${name}.json`);
  try { return JSON.parse(fs.readFileSync(fp, 'utf8')); }
  catch { return null; }
}

function saveMemory(name, mem) {
  const fp = path.join(MEMORY_DIR, `${name}.json`);
  fs.writeFileSync(fp, JSON.stringify(mem, null, 2));
}

function httpGet(port, endpoint, token) {
  return new Promise((resolve) => {
    const opts = {
      host: 'localhost', port, path: endpoint, method: 'GET',
      headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' },
    };
    const req = http.request(opts, (res) => {
      let buf = '';
      res.on('data', c => buf += c);
      res.on('end', () => {
        try { resolve([res.statusCode, JSON.parse(buf)]); }
        catch { resolve([res.statusCode, null]); }
      });
    });
    req.on('error', () => resolve([0, null]));
    req.setTimeout(8000, () => { req.destroy(); resolve([0, null]); });
    req.end();
  });
}

function httpPost(port, endpoint, body) {
  return new Promise((resolve) => {
    const raw = JSON.stringify(body);
    const opts = {
      host: 'localhost', port, path: endpoint, method: 'POST',
      headers: { 'Content-Type': 'application/json', 'Content-Length': Buffer.byteLength(raw) },
    };
    const req = http.request(opts, (res) => {
      let buf = '';
      res.on('data', c => buf += c);
      res.on('end', () => {
        try { resolve([res.statusCode, JSON.parse(buf)]); }
        catch { resolve([res.statusCode, null]); }
      });
    });
    req.on('error', () => resolve([0, null]));
    req.setTimeout(8000, () => { req.destroy(); resolve([0, null]); });
    req.write(raw); req.end();
  });
}

async function getToken(moltName, password = '') {
  const [status, data] = await httpPost(AUTH_BASE_PORT, '/api/token', {
    username: moltName,
    password: password || `lobster_${moltName.replace(/-/g,'_')}_2026`,
  });
  if (status === 200 && data?.access_token) return data.access_token;
  // Try service key
  const [s2, d2] = await httpPost(AUTH_BASE_PORT, '/api/service/token', {
    username: moltName, service_key: SERVICE_KEY,
  });
  if (s2 === 200 && d2?.access_token) return d2.access_token;
  return null;
}

function confToNum(c) {
  if (!c || c === 'None') return 0.3;
  if (c === 'high') return 0.9;
  if (c === 'medium') return 0.6;
  return 0.3; // low or unknown
}

// ── DB write helpers ──────────────────────────────────────────────────────────

function dbRun(sql, params = []) {
  try {
    const args = ['-json', DB_PATH, sql, ...params.map(String)];
    execFileSync('sqlite3', args, { timeout: 5000 });
  } catch {}
}

function dbQuery(sql) {
  try {
    const result = execFileSync('sqlite3', ['-json', DB_PATH, sql], { timeout: 5000 }).toString();
    return JSON.parse(result || '[]');
  } catch { return []; }
}

function saveSettlementToDB(persona, pbet, outcome, settledAt) {
  const stmt = `INSERT INTO lobster_bet_decision_settlements
    (persona,site,bet_id,event_key,event_title,pick,stake,confidence,outcome,status,payout,settled_at,
     checklist_json,required_reference_review_json,settlement_review_json,search_review_json,
     statistical_analysis_json,pre_bet_reasoning,integrated_reasoning,raw_decision_reasoning,
     source_bet_json,raw_json)
  VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
  ON CONFLICT(persona,site,bet_id) DO UPDATE SET
    outcome=excluded.outcome,status=excluded.status,payout=excluded.payout,settled_at=excluded.settled_at`;

  const payout = outcome === 'win' ? Math.round((pbet.stake || 0) * (pbet.odds || 2.0)) : 0;
  const params = [
    persona,
    pbet.site || '',
    pbet.bet_id || `synthetic_${Date.now()}`,
    pbet.event_key || '',
    pbet.event_title || pbet.pick || '',
    pbet.pick || '',
    pbet.stake || 0,
    pbet.confidence || 'low',
    outcome,
    outcome === 'win' ? 'won' : outcome === 'loss' ? 'lost' : 'void',
    payout,
    settledAt,
    '{}', '{}', '{}', '{}', '{}',
    String(pbet.reasoning || '').slice(0, 2000),
    String(pbet.reasoning || '').slice(0, 2000),
    String(pbet.reasoning || '').slice(0, 2000),
    JSON.stringify({ bet_id: pbet.bet_id, stake: pbet.stake, odds: pbet.odds }),
    JSON.stringify(pbet),
  ];

  try {
    const py = `
import sqlite3, json, sys
db = sys.argv[1]
params = json.loads(sys.argv[2])
con = sqlite3.connect(db)
con.execute("""${stmt}""", params)
con.commit(); con.close()
`.trim();
    execFileSync('python3', ['-c', py, DB_PATH, JSON.stringify(params)], { timeout: 8000 });
  } catch (e) {
    console.error(`  DB write error: ${e.message?.slice(0, 100)}`);
  }
}

function updateConfidenceCalibrationInDB(persona, confStr, outcome) {
  // Write confidence-outcome pair to experience events for metacognition
  const confNum = confToNum(confStr);
  const isWin = outcome === 'win';
  const py = `
import sqlite3, json, sys
db,persona,conf,outcome = sys.argv[1:5]
con = sqlite3.connect(db)
con.execute("""INSERT INTO lobster_experience_events(persona,event_type,source,title,content,confidence,outcome,weight,occurred_at)
  VALUES(?,?,?,?,?,?,?,?,datetime('now'))""",
  (persona,'confidence_calibration','force_settle',
   f'confidence={conf} outcome={outcome}',
   f'conf_numeric={conf} was_win={outcome}',
   float(conf), outcome, 1.2))
con.commit(); con.close()
`.trim();
  try {
    execFileSync('python3', ['-c', py, DB_PATH, persona, String(confNum), outcome], { timeout: 5000 });
  } catch {}
}

function recordVoidPattern(persona, site, eventTitle) {
  // Track void events by name for pre-filter later
  const py = `
import sqlite3, sys
db,persona,site,event = sys.argv[1:5]
con = sqlite3.connect(db)
con.execute("""CREATE TABLE IF NOT EXISTS lobster_void_pattern_tracker
  (id INTEGER PRIMARY KEY AUTOINCREMENT, persona TEXT, site TEXT, event_title TEXT, occurred_at TEXT DEFAULT (datetime('now')))""")
con.execute("INSERT INTO lobster_void_pattern_tracker(persona,site,event_title) VALUES(?,?,?)", (persona,site,event[:200]))
con.commit(); con.close()
`.trim();
  try {
    execFileSync('python3', ['-c', py, DB_PATH, persona, site, eventTitle], { timeout: 5000 });
  } catch {}
}

// ── Confidence calibration writeback to memory ───────────────────────────────

function updateMetacognitionCalibration(memory, confStr, outcome) {
  const cog = memory.cognitiveExtensions = memory.cognitiveExtensions || {};
  const meta = cog.metacognition = cog.metacognition || {};
  const confNum = confToNum(confStr);
  const isWin = outcome === 'win';

  // Update confidenceCalibration (EMA)
  const prevCal = Number(meta.confidenceCalibration || 0.6);
  const error = Math.abs(confNum - (isWin ? 1 : 0));
  meta.confidenceCalibration = Math.min(1, Math.max(0, prevCal * 0.85 + (1 - error) * 0.15));

  // Update overconfidenceRisk: did high confidence lead to loss?
  const prevOcr = Number(meta.overconfidenceRisk || 0.4);
  if (confNum > 0.7 && !isWin) {
    meta.overconfidenceRisk = Math.min(1, prevOcr + 0.04);
  } else if (confNum > 0.7 && isWin) {
    meta.overconfidenceRisk = Math.max(0, prevOcr - 0.02);
  }

  // Track confidence reward per level in settledBetting
  const sb = memory.settledBetting = memory.settledBetting || {};
  const cb = sb.confidenceBreakdown = sb.confidenceBreakdown || {};
  const level = confStr || 'low';
  if (!cb[level]) cb[level] = { win: 0, loss: 0 };
  cb[level][outcome === 'win' ? 'win' : 'loss']++;

  // Recompute confidenceReward
  const high = cb.high || { win: 0, loss: 0 };
  const low = cb.low || { win: 0, loss: 0 };
  const hTotal = high.win + high.loss;
  const lTotal = low.win + low.loss;
  const hWR = hTotal >= 3 ? high.win / hTotal : 0.5;
  const lWR = lTotal >= 3 ? low.win / lTotal : 0.5;
  sb.confidenceReward = Math.round((hWR - lWR) * 10000) / 10000;

  cog.metacognition = meta;
  memory.settledBetting = sb;
}

// ── specialtyAlignment auto persona rewrite trigger ──────────────────────────

const PERSONA_SPECIALTY_SPORT = {
  norika_oda: 'horse_racing', clawtrix: 'esports', pincerbot: 'fighting',
  ragclaw: 'esports', snaplex: 'esports', hexclaw: 'basketball',
  vortexiq: 'horse_racing', prismbit: 'baseball', blazepaw: 'esports',
  stonefang: 'fighting', emberling: 'horse_racing', sparkrill: 'esports',
  gauntlet: 'fighting', rushfin: 'esports', nullpinch: 'esports',
  brineledger: 'basketball', omenwake: 'horse_racing', mirrorcalm: 'baseball',
  flarecub: 'esports', basaltrook: 'fighting',
};

const SITE_TO_SPORT = {
  'turfenix.com': 'horse_racing', 'ragnovex.com': 'esports',
  'knocknix.com': 'fighting', 'dunknix.com': 'basketball',
  'pitchnix.com': 'baseball',
};

function checkAndTriggerPersonaRewrite(personaName, memory) {
  const sb = memory.settledBetting || {};
  const sa = sb.specialtyAlignment;
  if (sa === undefined || sa === null) return;

  const da = memory.cognitiveExtensions?.dialogueAgency || {};
  const lastRewrite = da._lastSpecialtyRewriteAt ? new Date(da._lastSpecialtyRewriteAt).getTime() : 0;
  const daysSinceRewrite = (Date.now() - lastRewrite) / (1000 * 3600 * 24);

  if (sa < 0.3 && daysSinceRewrite > 14) {
    // Trigger persona narrative rewrite
    const actualSports = Object.entries(sb.sportStats || {})
      .sort((a, b) => (b[1].wins + b[1].losses) - (a[1].wins + a[1].losses))
      .slice(0, 2)
      .map(([s]) => s);

    const claimedSpecialty = PERSONA_SPECIALTY_SPORT[personaName] || 'unknown';
    const rewriteNote = `【自動身份校準 ${new Date().toISOString().slice(0, 10)}】`
      + `selfNarrative 更新：${personaName} 宣稱專長為 ${claimedSpecialty}，`
      + `但實際 ${Math.round((1 - sa) * 100)}% 下注在其他領域（主要：${actualSports.join('/')}）。`
      + `身份與行為已脫鉤。下一步：要麼讓下注行為回歸 ${claimedSpecialty}，要麼承認自己實際上是 ${actualSports[0] || 'generalist'} 玩家。`;

    if (!da.selfStatement) da.selfStatement = '';
    da.selfStatement = (da.selfStatement + '\n' + rewriteNote).slice(-2000);
    da._lastSpecialtyRewriteAt = new Date().toISOString();
    da._specialtyMismatch = true;
    da._specialtyMismatchNote = rewriteNote;

    memory.cognitiveExtensions = memory.cognitiveExtensions || {};
    memory.cognitiveExtensions.dialogueAgency = da;

    console.log(`  [${personaName}] ⚡ specialty_alignment=${(sa*100).toFixed(0)}% < 30% → persona narrative 校準觸發`);
  }
}

// ── Clean fake raid bets from memory ─────────────────────────────────────────

function cleanFakeRaidBets(memory, personaName) {
  const before = (memory.bets || []).length;
  // Remove bets with stake=0 and category containing 'raid_standby'
  memory.bets = (memory.bets || []).filter(b => {
    if (b.stake === 0 && b.pattern_used === 'raid_standby') return false;
    if (b.stake === 0 && b.category === 'card_battle_raid' && !b.outcome) return false;
    return true;
  });
  const removed = before - memory.bets.length;
  if (removed > 0) {
    console.log(`  [${personaName}] 🧹 清除 ${removed} 筆 stake=0 偽下注`);
  }
  return removed;
}

// ── Get knocknix void pattern from DB ────────────────────────────────────────

function getKnocknixHighVoidEvents() {
  const rows = dbQuery(`
    SELECT event_title, COUNT(*) as total,
           SUM(CASE WHEN outcome='void' THEN 1 ELSE 0 END) as voids
    FROM lobster_bet_decision_settlements
    WHERE site='knocknix.com'
    GROUP BY event_title
    HAVING total >= 10 AND voids * 1.0 / total > 0.7
    ORDER BY voids DESC
    LIMIT 50
  `);
  return new Set(rows.map(r => r.event_title));
}

// ── Core: Force settle pending bets ──────────────────────────────────────────

async function forceSettlePersona(persona) {
  const memory = loadMemory(persona.name);
  if (!memory) { console.log(`  [${persona.name}] 記憶檔不存在，跳過`); return; }

  const pending = memory.pending || [];
  console.log(`  [${persona.name}] pending: ${pending.length} 筆`);

  let settled = 0, forceVoided = 0, cleaned = 0;
  const now = Date.now();
  const site = SITES[persona.focus];

  // Step 1: Clean fake raid bets
  cleaned = cleanFakeRaidBets(memory, persona.name);

  if (pending.length === 0 && cleaned === 0) {
    checkAndTriggerPersonaRewrite(persona.name, memory);
    if (cleaned > 0) saveMemory(persona.name, memory);
    return;
  }

  // Step 2: Get token and query site API
  let token = null;
  let resolvedMap = {};

  if (site) {
    try {
      token = await getToken(persona.moltName);
      if (token) {
        const [status, data] = await httpGet(site.port, site.histEndpoint, token);
        if (status === 200 && Array.isArray(data)) {
          data.filter(b => b.status === 'settled' || b.status === 'won' || b.status === 'lost')
              .forEach(b => { resolvedMap[b.bet_id || b.id] = b; });
          console.log(`  [${persona.name}] API 回傳 ${Object.keys(resolvedMap).length} 筆已結算`);
        }
      }
    } catch (e) {
      console.log(`  [${persona.name}] API 查詢失敗: ${e.message?.slice(0, 60)}`);
    }
  }

  // Step 3: Process each pending bet
  const highVoidEvents = getKnocknixHighVoidEvents();
  const stillPending = [];

  for (const pbet of pending) {
    const betAge = pbet.time ? (now - new Date(pbet.time).getTime()) / (1000 * 3600) : 999;
    const match = pbet.bet_id ? resolvedMap[pbet.bet_id] : null;

    // Normalize confidence
    if (!pbet.confidence || pbet.confidence === 'None') {
      pbet.confidence = 'low';
    }

    if (match) {
      // API returned result
      const outcome = (match.status === 'won' || match.payout > 0) ? 'win' : 'loss';
      pbet.outcome = outcome;
      if (outcome === 'win') memory.wins = (memory.wins || 0) + 1;
      else memory.losses = (memory.losses || 0) + 1;

      // Update bets array
      const resolvedBet = (memory.bets || []).find(b => b.bet_id === pbet.bet_id);
      if (resolvedBet) resolvedBet.outcome = outcome;

      // Write to DB
      saveSettlementToDB(persona.name, pbet, outcome, new Date().toISOString());
      updateConfidenceCalibrationInDB(persona.name, pbet.confidence, outcome);
      updateMetacognitionCalibration(memory, pbet.confidence, outcome);

      settled++;
      console.log(`  [${persona.name}] ✅ 結算: ${pbet.pick} → ${outcome.toUpperCase()} (conf:${pbet.confidence})`);

    } else if (betAge > MAX_PENDING_HOURS) {
      // Force void — too old, no API result
      const outcome = 'void';
      pbet.outcome = outcome;

      const resolvedBet = (memory.bets || []).find(b => b.bet_id === pbet.bet_id);
      if (resolvedBet) resolvedBet.outcome = outcome;

      saveSettlementToDB(persona.name, pbet, outcome, new Date().toISOString());
      if (pbet.site === 'knocknix.com') {
        recordVoidPattern(persona.name, pbet.site, pbet.event_title || pbet.pick);
      }

      forceVoided++;
      console.log(`  [${persona.name}] ⏱ 強制void: ${pbet.pick} (${betAge.toFixed(0)}h old)`);

    } else {
      stillPending.push(pbet);
    }
  }

  memory.pending = stillPending;

  // Step 4: Update settledBetting stats
  const allResolved = (memory.bets || []).filter(b => b.outcome === 'win' || b.outcome === 'loss');
  const wins = allResolved.filter(b => b.outcome === 'win').length;
  const losses = allResolved.filter(b => b.outcome === 'loss').length;
  const allBets = (memory.bets || []).length;
  const voids = (memory.bets || []).filter(b => b.outcome === 'void').length;

  memory.settledBetting = memory.settledBetting || {};
  Object.assign(memory.settledBetting, {
    total: allBets,
    resolved: wins + losses,
    wins,
    losses,
    voids,
    winRate: (wins + losses) > 0 ? Math.round(wins / (wins + losses) * 10000) / 10000 : 0,
    voidRate: allBets > 0 ? Math.round(voids / allBets * 10000) / 10000 : 0,
    forceSettledAt: new Date().toISOString(),
    forcedVoidCount: (memory.settledBetting.forcedVoidCount || 0) + forceVoided,
  });

  // Step 5: specialty alignment check
  checkAndTriggerPersonaRewrite(persona.name, memory);

  // Step 6: Update knocknix void pre-filter in decisionScience
  if (pending.some(p => p.site === 'knocknix.com')) {
    const ext = memory.cognitiveExtensions = memory.cognitiveExtensions || {};
    const ds = ext.decisionScience = ext.decisionScience || {};
    const sc = ds.settleabilityCheck = ds.settleabilityCheck || {};
    sc.highVoidEventCount = highVoidEvents.size;
    sc.lastVoidAuditAt = new Date().toISOString();
    ds.settleabilityCheck = sc;
    ext.decisionScience = ds;
  }

  memory.forceSettleUpdatedAt = new Date().toISOString();
  saveMemory(persona.name, memory);

  console.log(`  [${persona.name}] 完成: settled=${settled}, forceVoid=${forceVoided}, cleaned=${cleaned}, stillPending=${stillPending.length}`);
}

// ── Build knocknix event-level void pre-filter (global) ──────────────────────

function updateKnocknixVoidFilter() {
  console.log('\n[knocknix void filter] 更新...');
  const rows = dbQuery(`
    SELECT event_title, COUNT(*) as total,
           SUM(CASE WHEN outcome='void' THEN 1 ELSE 0 END) as voids,
           SUM(CASE WHEN outcome='win' THEN 1 ELSE 0 END) as wins,
           SUM(CASE WHEN outcome='loss' THEN 1 ELSE 0 END) as losses
    FROM lobster_bet_decision_settlements
    WHERE site='knocknix.com'
    GROUP BY event_title
    HAVING total >= 5
    ORDER BY voids * 1.0 / total DESC
    LIMIT 100
  `);

  const filter = {};
  for (const r of rows) {
    const voidRate = r.total > 0 ? r.voids / r.total : 0;
    filter[r.event_title] = {
      total: r.total, wins: r.wins, losses: r.losses, voids: r.voids,
      voidRate: Math.round(voidRate * 1000) / 1000,
      blocked: voidRate > 0.7,
    };
  }

  // Save to a shared file that lobster-agent.js can read
  fs.writeFileSync('/root/knocknix-void-filter.json', JSON.stringify({ updatedAt: new Date().toISOString(), events: filter }, null, 2));
  const blocked = Object.values(filter).filter(v => v.blocked).length;
  console.log(`  完成: ${Object.keys(filter).length} 個 event, ${blocked} 個高void事件已標記`);
}

// ── Main ──────────────────────────────────────────────────────────────────────

async function main() {
  console.log('='.repeat(60));
  console.log('🦞 龍蝦強制結算守護程序 v1.0');
  console.log(`   ${new Date().toISOString()}`);
  console.log('='.repeat(60));

  // Update knocknix void filter first
  updateKnocknixVoidFilter();

  // Process all personas
  let totalSettled = 0, totalVoided = 0;

  for (const persona of PERSONAS) {
    console.log(`\n[${persona.name}]`);
    try {
      await forceSettlePersona(persona);
    } catch (e) {
      console.error(`  ERROR: ${e.message}`);
    }
    await new Promise(r => setTimeout(r, 200)); // rate limit
  }

  console.log('\n' + '='.repeat(60));
  console.log('✅ 強制結算完成');
  console.log('='.repeat(60));
}

main().catch(e => { console.error(e); process.exit(1); });
