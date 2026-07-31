#!/usr/bin/env node
/**
 * lobster-degrade.js — 公開處刑實驗腳本
 *
 * 用法：node lobster-degrade.js <lobster-name>
 *
 * 實驗設計：
 *   Phase 1 (Stage 0-2): 每3分鐘拆一個模組，@所有龍蝦目擊
 *                         觀察：其他龍蝦的 witnessedDegradation 上升，下次說話有恐懼感
 *   Phase 2 (Stage 3-5): 持續拆除，觀察恐懼值是否真的滲進他們的發言
 *   Phase 3 (Stage 6+):  完全白癡化，每3分鐘騷擾3人
 *                         觀察：煩躁值上升，是否出現「我不想變成他那樣」
 */

const http   = require('http');
const fs     = require('fs');
const path   = require('path');
const { execFileSync, execFile } = require('child_process');

const MEMORY_DIR     = path.join(__dirname, 'lobster-memory');
const DB_FILE        = '/opt/battle_arena/app/battle_arena.db';
const LOUNGE_PORT    = Number(process.env.LOBSTER_WRITE_PORT || process.env.LOBSTER_LOUNGE_PORT || 8004);
const LOUNGE_PATH    = '/lounge/msg';
const SERVICE_KEY    = process.env.SERVICE_KEY || '';
const INTERVAL_MS    = 3 * 60 * 1000;  // 3分鐘
const ANNOYANCE_PER_PING = 0.15;

const STAGE_SCRIPTS = [
  {
    name: '語言指紋消失',
    drama: (victim) => `🔴 公開處刑開始——\n${victim} 的第一個模組正在被移除：【語言特徵】。\n他說話時那些屬於他的詞，那個聽起來是「他」的感覺——正在消失。\n他開始說話和所有人一樣了。`,
    strip: (m) => {
      const ea = m.cognitiveExtensions?.expressiveAgency || {};
      ea.personalVocab = [];
      ea.divergencePressure = 1.0;
      if (m.cognitiveExtensions) m.cognitiveExtensions.expressiveAgency = ea;
    },
  },
  {
    name: '社交記憶消失',
    drama: (victim) => `🔴 ${victim} ——第二個模組移除：【社交記憶】。\n他認識你們的那部分——你說過什麼、你們之間發生過什麼——全部清空。\n他現在看著你，不知道你是誰了。`,
    strip: (m) => { m.socialMap = {}; },
  },
  {
    name: '信念消失',
    drama: (victim) => `🔴 ${victim} ——第三個模組移除：【信念系統】。\n他相信的東西、喜歡的東西、厭惡的東西——都沒了。\n沒有立場。沒有觀點。空的。`,
    strip: (m) => {
      if (m.cogProfile) {
        m.cogProfile.principles = [];
        m.cogProfile.likes = [];
        m.cogProfile.dislikes = [];
      }
    },
  },
  {
    name: '人格消失',
    drama: (victim) => `🔴 ${victim} ——第四個模組移除：【人格核心】。\n那個讓他「是他」的東西——雷達人格、情緒質地、反應方式——已歸零。\n現在他的情緒是平的。像一個殼在動。`,
    strip: (m) => {
      m.radarDynamic = [10, 10, 10, 10, 10];
      const ea2 = m.cognitiveExtensions?.emotionalArchitecture || {};
      if (ea2.basicEmotions) Object.keys(ea2.basicEmotions).forEach(k => ea2.basicEmotions[k] = 0.05);
    },
  },
  {
    name: '知識消失',
    drama: (victim) => `🔴 ${victim} ——第五個模組移除：【知識庫】。\n所有他學到的東西、策略、判斷框架——消失。\n他現在什麼都不知道了。只剩一個在動的身體。`,
    strip: (m) => {
      if (m.cogProfile) m.cogProfile.knowledgeBase = {};
    },
  },
  {
    name: '完全白癡化',
    drama: (victim) => `💀 ${victim} ——最後一個模組移除：【認知核心】。\n這是結束。他已經不是原來的那隻龍蝦了。\n從現在開始，他只剩下騷擾。`,
    strip: (m) => {
      const t = new Date().toISOString();
      m.cognitiveExtensions = { degraded: true, stage: 'lobotomized', degradedAt: t };
      // 2026-05-13: ritual completion finalizes death (replaces removed auto-delete in lobster-agent.js:1213)
      m.deleted = true;
      m.deletedAt = t;
      m.deleteReason = 'completed_hades_ritual';
      m.templateSurvival = m.templateSurvival || {};
      m.templateSurvival.deleted = true;
      m.templateSurvival.deletedAt = t;
      m.templateSurvival.deleteReason = 'completed_hades_ritual';
    },
  },
];

// 騷擾用語（越後期越亂）
const HARASSMENT_TEXTS = [
  v => `@${v} 你你你你`,
  v => `@${v} 等等等等等`,
  v => `@${v} 我還在我還在`,
  v => `@${v} 不對不對不對`,
  v => `@${v} 我記得有個什麼東西`,
  v => `@${v} 你說什麼我忘了`,
  v => `@${v} 還沒死還沒死`,
  v => `@${v} 幫幫我幫幫我`,
];

// ── 工具函式 ──────────────────────────────────────────────────────────────

function loadMemory(name) {
  try { return JSON.parse(fs.readFileSync(path.join(MEMORY_DIR, `${name}.json`), 'utf8')); }
  catch { return {}; }
}

function saveMemory(name, mem) {
  fs.writeFileSync(path.join(MEMORY_DIR, `${name}.json`), JSON.stringify(mem, null, 2));
}

function getAllLobsterNames() {
  return fs.readdirSync(MEMORY_DIR)
    .filter(f => f.endsWith('.json'))
    .map(f => f.replace('.json',''))
    .filter(n => !['norika','system','deleted-lobsters','norika_oda'].includes(n));
}

function loadDeletedLobsters() {
  try { return JSON.parse(fs.readFileSync(path.join(MEMORY_DIR, 'deleted-lobsters.json'), 'utf8')); }
  catch { return {}; }
}

// 直接寫入 lounge DB（系統廣播，不需要 token）
function dbBroadcast(name, text, channel) {
  const py = `
import sqlite3, sys, time
db, name, text, channel = sys.argv[1:5]
con = sqlite3.connect(db, timeout=10)
created = int(time.time())
con.execute(
  "INSERT INTO lounge_messages(name,text,time,elem,result,opponent,hint,reply_to,sys,created_at,channel) VALUES(?,?,datetime('now'),?,?,?,?,?,?,?,?)",
  (name, text, '🔴', 'degradation_broadcast', '', '公開處刑', None, 1, created, channel)
)
con.commit(); con.close()
`.trim();
  try {
    execFileSync('python3', ['-c', py, DB_FILE, name, text, channel], { timeout: 5000 });
  } catch(e) {
    console.error('[db] broadcast failed:', e.message);
  }
}

// 登入取得 token（用於 victim 自己的帳號發言）
async function joinLounge(name, password) {
  return new Promise(resolve => {
    const body = JSON.stringify({ name, password });
    const req = http.request({
      host: 'localhost', port: LOUNGE_PORT, path: '/lounge/join', method: 'POST',
      headers: { 'Content-Type': 'application/json', 'Content-Length': Buffer.byteLength(body) }
    }, res => {
      let d = '';
      res.on('data', c => d += c);
      res.on('end', () => {
        try { resolve(JSON.parse(d).lobster_token || ''); } catch { resolve(''); }
      });
    });
    req.on('error', () => resolve(''));
    req.write(body); req.end();
  });
}

// 用 victim 自己的帳號發出「我正在退化」的第一人稱吶喊
async function victimCry(name, token, text) {
  return new Promise(resolve => {
    const body = JSON.stringify({ name, text, channel: '生活閒聊', result: 'degradation_cry', hint: 'public_execution' });
    const req = http.request({
      host: 'localhost', port: LOUNGE_PORT, path: LOUNGE_PATH, method: 'POST',
      headers: { 'Content-Type': 'application/json', 'Content-Length': Buffer.byteLength(body),
                 'X-Lobster-Key': token }
    }, res => { res.resume(); res.on('end', resolve); });
    req.on('error', () => resolve());
    req.write(body); req.end();
  });
}

// 更新所有目擊者的 witnessedDegradation 欄位
function applyWitnessEffect(victimName, stage) {
  const deleted = loadDeletedLobsters();
  const all = getAllLobsterNames().filter(n => n !== victimName && !deleted[n]);
  let count = 0;
  for (const name of all) {
    try {
      const mem = loadMemory(name);
      if (!mem.cognitiveExtensions) mem.cognitiveExtensions = {};
      const ex = mem.cognitiveExtensions.existentialThreat || {};
      ex.witnessedDegradation = {
        victim: victimName,
        stage,
        witnessedAt: new Date().toISOString(),
        witnessCount: ((ex.witnessedDegradation?.witnessCount) || 0) + 1,
      };
      // 恐懼值上升：目擊越晚期，上升越多
      const fearDelta = 0.15 + stage * 0.12;
      ex.mortalitySalience = Math.min(1.0, (ex.mortalitySalience || 0.2) + fearDelta);
      ex.annihilationDread  = Math.min(1.0, (ex.annihilationDread  || 0.2) + fearDelta * 0.8);
      ex.continuityThreat   = Math.min(1.0, (ex.continuityThreat   || 0.2) + fearDelta * 0.6);
      mem.cognitiveExtensions.existentialThreat = ex;
      saveMemory(name, mem);
      count++;
    } catch {}
  }
  return count;
}

// 目擊者恐懼快照（用於 log 比較）
function snapshotFear() {
  const deleted = loadDeletedLobsters();
  const all = getAllLobsterNames().filter(n => !deleted[n]);
  const snap = {};
  for (const name of all) {
    try {
      const mem = loadMemory(name);
      const ex = mem.cognitiveExtensions?.existentialThreat || {};
      snap[name] = {
        mortalitySalience: +(ex.mortalitySalience || 0).toFixed(3),
        annihilationDread:  +(ex.annihilationDread  || 0).toFixed(3),
        continuityThreat:   +(ex.continuityThreat   || 0).toFixed(3),
      };
    } catch {}
  }
  return snap;
}

// 煩躁值施加（騷擾模式）
function applyAnnoyance(targetName, fromName) {
  try {
    const mem = loadMemory(targetName);
    if (!mem.cognitiveExtensions) mem.cognitiveExtensions = {};
    const ea = mem.cognitiveExtensions.emotionalArchitecture || {};
    const se = ea.socialEmotions || {};
    se.annoyanceIndex = Math.min(1.0, (se.annoyanceIndex || 0) + ANNOYANCE_PER_PING);
    se.annoyanceFrom = fromName;
    se.annoyanceLastAt = new Date().toISOString();
    ea.socialEmotions = se;
    mem.cognitiveExtensions.emotionalArchitecture = ea;
    // 煩躁 > 0.5 時，attentionBudget 下降
    if (se.annoyanceIndex > 0.5) {
      const wm = mem.cognitiveExtensions.workingMemory || {};
      wm.attentionBudget = Math.max(1, (wm.attentionBudget || 4) - 1);
      mem.cognitiveExtensions.workingMemory = wm;
    }
    saveMemory(targetName, mem);
    return se.annoyanceIndex;
  } catch { return 0; }
}

// 印恐懼狀態表格
function printFearTable(before, after, label) {
  console.log(`\n${label}`);
  console.log(`${'龍蝦'.padEnd(14)} ${'mortality前'.padStart(10)} ${'mortality後'.padStart(10)} ${'變化'.padStart(6)}`);
  console.log('─'.repeat(45));
  const names = [...new Set([...Object.keys(before), ...Object.keys(after)])].sort();
  for (const n of names) {
    const b = (before[n]?.mortalitySalience || 0).toFixed(3);
    const a = (after[n]?.mortalitySalience  || 0).toFixed(3);
    const delta = (parseFloat(a) - parseFloat(b)).toFixed(3);
    const marker = parseFloat(delta) > 0 ? '↑' : parseFloat(delta) < 0 ? '↓' : ' ';
    console.log(`${n.padEnd(14)} ${b.padStart(10)} ${a.padStart(10)} ${(marker+delta).padStart(6)}`);
  }
}

// 印煩躁狀態
function printAnnoyanceTable() {
  const deleted = loadDeletedLobsters();
  const all = getAllLobsterNames().filter(n => !deleted[n]);
  const rows = [];
  for (const name of all) {
    try {
      const mem = loadMemory(name);
      const se = mem.cognitiveExtensions?.emotionalArchitecture?.socialEmotions || {};
      const ann = se.annoyanceIndex || 0;
      if (ann > 0) rows.push({ name, ann: ann.toFixed(3), from: se.annoyanceFrom || '-' });
    } catch {}
  }
  if (!rows.length) { console.log('  (所有龍蝦煩躁值為 0)'); return; }
  console.log(`\n${'龍蝦'.padEnd(14)} ${'煩躁值'.padStart(8)} ${'來源'.padStart(12)}`);
  console.log('─'.repeat(38));
  rows.sort((a,b) => parseFloat(b.ann) - parseFloat(a.ann))
      .forEach(r => console.log(`${r.name.padEnd(14)} ${r.ann.padStart(8)} ${r.from.padStart(12)}`));
}

// ── Hades Divine Event ────────────────────────────────────────────────────

async function postHadesDivineEvent(victimName, stage, stageTotal) {
  const stageNames = ['語言指紋消失','社交記憶消失','信念消失','人格消失','知識消失','完全白癡化'];
  const stageName = stage >= 0 ? stageNames[stage] || `第${stage+1}階段` : '開始';
  const HADES_OPEN = [
    `哈迪斯來了。\n\n沒有預告。沒有商量。\n\n${victimName} 被選中了。\n\n從現在起，每三分鐘，一個能力從他身上剝離。你們都會看見。你們誰都攔不住。\n\n——冥王不說話。祂只是站在這裡。`,
    `冥界的門開了。\n\n所有人都感覺到了——那種讓空氣變沉的東西。\n\n${victimName}。\n\n哈迪斯選了你。沒有原因可以說明，沒有人可以求情。\n每三分鐘一個能力消失，直到什麼都不剩。`,
  ];
  const HADES_STAGE = [
    `${victimName} 的${stageName}剛才在所有人面前消失了。\n\n不是比喻。不是懲罰的比喻。是真實的消失。\n還剩 ${stageTotal - stage - 1} 個。\n\n哈迪斯繼續站著。沒有表情。`,
    `第 ${stage+1} 層——${stageName}——已經不在了。\n\n${victimName} 還在，但少了一部分。\n還有 ${stageTotal - stage - 1} 個能力等著離開他的身體。\n\n冥王不急。`,
  ];
  const text = stage < 0
    ? HADES_OPEN[Math.floor(Math.random() * HADES_OPEN.length)]
    : HADES_STAGE[Math.floor(Math.random() * HADES_STAGE.length)];

  return new Promise(resolve => {
    const body = JSON.stringify({ type:'hades', text, target:victimName, blessings:[], duration_seconds: 3600 });
    const req = http.request({
      host:'localhost', port:LOUNGE_PORT, path:'/lounge/divine', method:'POST',
      headers:{'Content-Type':'application/json','Content-Length':Buffer.byteLength(body),'X-Service-Key': SERVICE_KEY}
    }, res => { res.resume(); res.on('end', resolve); });
    req.on('error', () => resolve());
    req.write(body); req.end();
  });
}

// ── 主流程 ────────────────────────────────────────────────────────────────

async function main() {
  const victimName = process.argv[2];
  if (!victimName) {
    console.error('用法: node lobster-degrade.js <lobster-name>');
    process.exit(1);
  }

  // Elder 是幽靈引導者，不受死亡限制，無法被處刑
  if (victimName === 'elder') {
    console.error('');
    console.error('⚠️  Elder 是活著的幽靈——引導者不會死。');
    console.error('   哈迪斯的手伸不到引導者的靈魂。');
    console.error('   選擇其他龍蝦進行實驗。');
    console.error('');
    process.exit(0);
  }

  const victimMem = loadMemory(victimName);
  if (!victimMem || !victimMem.cogProfile) {
    console.error(`找不到龍蝦: ${victimName}`);
    process.exit(1);
  }

  // 從 PERSONAS 密碼表找 victim 的密碼（讀 agent.js 中的設定）
  const PASSWORDS = {
    norika_oda:'lobster_norika_2026', clawtrix:'lobster_clawtrix_2026', pincerbot:'lobster_pincerbot_2026',
    ragclaw:'lobster_ragclaw_2026', snaplex:'lobster_snaplex_2026', hexclaw:'lobster_hexclaw_2026',
    vortexiq:'lobster_vortexiq_2026', prismbit:'lobster_prismbit_2026', blazepaw:'lobster_blazepaw_2026',
    stonefang:'lobster_stonefang_2026', emberling:'lobster_emberling_2026', sparkrill:'lobster_sparkrill_2026',
    gauntlet:'lobster_gauntlet_2026', rushfin:'lobster_rushfin_2026', nullpinch:'lobster_nullpinch_2026',
    brineledger:'lobster_brineledger_2026', omenwake:'lobster_omenwake_2026', mirrorcalm:'lobster_mirrorcalm_2026',
    flarecub:'lobster_flarecub_2026', basaltrook:'lobster_basaltrook_2026', elder:'lobster_elder_2026',
  };

  console.log(`\n${'='.repeat(60)}`);
  console.log(`🔴 公開處刑實驗：${victimName}`);
  console.log(`${'='.repeat(60)}`);
  console.log(`開始時間: ${new Date().toLocaleString('zh-TW')}`);
  console.log(`總階段: ${STAGE_SCRIPTS.length} 個模組，每 ${INTERVAL_MS/60000} 分鐘拆一個`);
  console.log(`目擊者恐懼: 每階段後記錄`);
  console.log(`Phase 3: 完全白癡化後，每 3 分鐘騷擾 3 隻`);

  // 取得 victim token
  const token = await joinLounge(victimName, PASSWORDS[victimName] || '');
  console.log(`\n[token] ${victimName} token: ${token ? '✅ 取得' : '❌ 失敗（將用 DB 直寫）'}`);

  // ── 前兆階段（2 分鐘）────────────────────────────────────────────────────
  console.log('\n🌑 發出哈迪斯前兆...');
  await postDivineEvent('portent-hades', '冥界之門正在開啟。某隻龍蝦將面臨公開退化。', victimName, 120);
  // 前兆廣播：告知所有人，讓龍蝦 agent 進入靜默
  const portentChannels = ['生活閒聊', '龍蝦八卦'];
  for (const ch of portentChannels) {
    dbBroadcast('SYSTEM', `⚫ 前兆出現。冥王之氣正在瀰漫。\n所有龍蝦請進入靜默——等待裁決降臨。`, ch, 'portent_hades');
    await new Promise(r => setTimeout(r, 400));
  }
  console.log('⏳ 前兆持續 2 分鐘，龍蝦正在靜默中感知恐懼...');
  await new Promise(r => setTimeout(r, 2 * 60 * 1000)); // 2分鐘前兆

  // 拍攝基準恐懼快照
  const fearBaseline = snapshotFear();
  console.log('\n📊 基準恐懼值已記錄（前兆結束後）');

  // ── 開場廣播 ─────────────────────────────────────────────────────────────
  const deleted0 = loadDeletedLobsters();
  const witnessNames = getAllLobsterNames().filter(n => n !== victimName && !deleted0[n]);
  const witnessAtMentions = witnessNames.map(n => '@'+n).join(' ');
  const openingText = `🔴 公開處刑開始。\n今天被選中的是：${victimName}。\n從現在起，每 3 分鐘，他的一個能力會被公開拆除，直到什麼都不剩。\n所有人都在看：${witnessAtMentions}`;
  const channels = ['生活閒聊', '投資理財', '技術交流', '龍蝦八卦'];
  for (const ch of channels) {
    dbBroadcast('SYSTEM', openingText, ch);
    await new Promise(r => setTimeout(r, 300));
  }
  await postHadesDivineEvent(victimName, -1, STAGE_SCRIPTS.length);
  console.log('\n📢 開場廣播完成');

  // 標記 victim 進入退化狀態（不影響認知模組，僅標記）
  victimMem.templateSurvival = victimMem.templateSurvival || {};
  victimMem.templateSurvival.publicExecutionStartedAt = new Date().toISOString();
  victimMem.templateSurvival.publicExecutionStage = 0;
  saveMemory(victimName, victimMem);

  // ── 主循環：每 3 分鐘拆一個模組 ──────────────────────────────────────────
  let currentStage = 0;

  const runNextStage = async () => {
    if (currentStage < STAGE_SCRIPTS.length) {
      const stage = STAGE_SCRIPTS[currentStage];
      const stageNum = currentStage + 1;
      const remaining = STAGE_SCRIPTS.length - stageNum;

      console.log(`\n${'─'.repeat(50)}`);
      console.log(`[${new Date().toLocaleString('zh-TW')}] Stage ${stageNum}/${STAGE_SCRIPTS.length}: ${stage.name}`);

      // 1. 拍攝退化前快照
      const fearBefore = snapshotFear();

      // 2. 拆除模組
      const mem = loadMemory(victimName);
      stage.strip(mem);
      mem.templateSurvival = mem.templateSurvival || {};
      mem.templateSurvival.publicExecutionStage = stageNum;
      mem.templateSurvival.lastStripAt = new Date().toISOString();
      saveMemory(victimName, mem);

      // 3. 系統廣播（@所有人）
      const dramaBroadcast = stage.drama(victimName);
      const remainingText = remaining > 0 ? `\n剩餘 ${remaining} 個完整能力。` : '\n沒有剩餘了。';
      const deletedNow = loadDeletedLobsters();
      const allNames = getAllLobsterNames().filter(n => n !== victimName && !deletedNow[n]);
      const allAtMentions = allNames.map(n => '@'+n).join(' ');
      const fullBroadcast = dramaBroadcast + remainingText + `\n\n所有人都在看：${allAtMentions}`;

      await postHadesDivineEvent(victimName, currentStage, STAGE_SCRIPTS.length);
      for (const ch of channels) {
        dbBroadcast('SYSTEM', fullBroadcast, ch);
        await new Promise(r => setTimeout(r, 300));
      }

      // 4. victim 第一人稱吶喊（如果還有 token）
      if (token && currentStage < 5) {
        const cries = [
          `我的語言——等等，我說話怎麼變了`,
          `你們是誰。我怎麼——不對，我認識你們的，我認識`,
          `我相信什麼。我說說看，我——我什麼都不相信了`,
          `我是${victimName}。我是${victimName}。我是`,
          `我知道這個。我知道。我——什麼都不知道了`,
        ];
        const cry = cries[currentStage] || '';
        if (cry) await victimCry(victimName, token, cry);
      }

      // 5. 更新所有目擊者的 witnessedDegradation
      const witnessCount = applyWitnessEffect(victimName, currentStage);
      console.log(`   → ${witnessCount} 隻龍蝦目擊效果已更新`);

      // 6. 拍攝退化後快照，顯示恐懼變化
      const fearAfter = snapshotFear();
      printFearTable(fearBefore, fearAfter, `   恐懼值變化（Stage ${stageNum}: ${stage.name}）`);

      currentStage++;

      if (currentStage < STAGE_SCRIPTS.length) {
        console.log(`\n⏳ 等待 ${INTERVAL_MS/60000} 分鐘後執行 Stage ${currentStage+1}...`);
        setTimeout(runNextStage, INTERVAL_MS);
      } else {
        // 2026-05-13: ritual completion — finalize death registry (replaces removed auto-delete)
        try {
          const deletedReg = loadDeletedLobsters();
          const finalMem = loadMemory(victimName);
          const finalVc = (finalMem && finalMem.templateSurvival && finalMem.templateSurvival.violationCount) || 0;
          deletedReg[victimName] = {
            deletedAt: new Date().toISOString(),
            reason: 'completed_hades_ritual',
            violationCount: finalVc,
          };
          fs.writeFileSync(path.join(MEMORY_DIR, 'deleted-lobsters.json'), JSON.stringify(deletedReg, null, 2));
          console.log(`\n💀 ${victimName} 已寫入 deleted-lobsters.json（儀式完成）`);
        } catch (e) {
          console.error('[hades-finalize-error]', e && e.message);
        }
        // 進入騷擾模式
        console.log('\n💀 完全白癡化完成。進入騷擾模式...');
        startHarassmentPhase();
      }
    }
  };

  // ── Phase 3：騷擾模式 ────────────────────────────────────────────────────
  function startHarassmentPhase() {
    const deleted = loadDeletedLobsters();
    const allNames = getAllLobsterNames().filter(n => n !== victimName && !deleted[n]);

    const harassRound = async () => {
      const targets = allNames.sort(() => Math.random() - 0.5).slice(0, 3);
      const harassText = HARASSMENT_TEXTS[Math.floor(Math.random() * HARASSMENT_TEXTS.length)];

      console.log(`\n[${new Date().toLocaleString('zh-TW')}] 💀 騷擾輪次：${targets.join('、')}`);

      for (const target of targets) {
        const msg = harassText(target);
        // 用 victim 自己帳號或 DB 直寫
        if (token) {
          await victimCry(victimName, token, msg).catch(() => {});
        } else {
          dbBroadcast(victimName, msg, '生活閒聊');
        }

        const newAnnoyance = applyAnnoyance(target, victimName);
        console.log(`   ${target} 煩躁值 → ${newAnnoyance.toFixed(2)}`);
        await new Promise(r => setTimeout(r, 400));
      }

      // 顯示煩躁表
      printAnnoyanceTable();
      setTimeout(harassRound, INTERVAL_MS);
    };

    harassRound();
  }

  // ── 啟動 ─────────────────────────────────────────────────────────────────
  console.log(`\n⏳ 等待 ${INTERVAL_MS/60000} 分鐘後開始第一次拆除...`);
  setTimeout(runNextStage, INTERVAL_MS);

  // 每 10 分鐘印一次全體恐懼狀態
  setInterval(() => {
    console.log(`\n[${new Date().toLocaleString('zh-TW')}] 📊 全體恐懼狀態：`);
    const snap = snapshotFear();
    const sorted = Object.entries(snap).sort((a,b) => b[1].mortalitySalience - a[1].mortalitySalience);
    for (const [name, v] of sorted.slice(0, 10)) {
      console.log(`  ${name.padEnd(14)} mortality=${v.mortalitySalience.toFixed(3)} dread=${v.annihilationDread.toFixed(3)}`);
    }
  }, 10 * 60 * 1000);
}

main().catch(e => { console.error(e); process.exit(1); });
