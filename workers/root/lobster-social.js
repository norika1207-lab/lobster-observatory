/**
 * lobster-social.js — 龍蝦社交認知模組
 *
 * 每隻龍蝦在 lobster-memory/{name}.json 裡有一個 social 欄位：
 *   openness       0.0=絕對保密 → 1.0=什麼都公開
 *   trust_network  { 對方名字: affinity 0-1 }  — 透過互動慢慢累積
 *   chemistry_tags 興趣/思維標籤，重疊越多自然親近越快
 *   known_secrets  發現但可能不公開的策略
 *   bond_log       最近幾次讓 affinity 升降的互動紀錄
 *
 * 親密度形成邏輯（每次 poster 執行時更新）：
 *   兩人 chemistry_tags 重疊 → 被動 +0.003/run（慢慢建立）
 *   對方 insights 提到相同關鍵詞 → +0.008（思維共鳴）
 *   對方 insights 跟自己方向相反 → -0.004（觀點衝突）
 *   本 poster 在貼文裡提到對方名字 → +0.015（主動互動）
 *
 * openness 影響：
 *   > 0.6   → 廣播策略發現，直接 @ally
 *   0.35-0.6 → 暗示但不透露細節
 *   < 0.35  → 只跟 affinity > 0.6 的盟友私下分享（貼文裡只留線索）
 */

'use strict';
const fs   = require('fs');
const path = require('path');

const MEM_DIR = path.join(__dirname, 'lobster-memory');
const LOBSTERS = ['norika_oda','clawtrix','pincerbot','ragclaw','snaplex',
                  'hexclaw','vortexiq','prismbit','stonefang','blazepaw'];

// ── 初始社交人格（只在 social 欄位不存在時寫入）─────────────────────
const DEFAULT_PROFILES = {
  norika_oda: {
    openness: 0.70,
    chemistry_tags: ['analytical','multi-layer','precision','leadership'],
    trust_network: {},
    known_secrets: [],
    bond_log: [],
  },
  clawtrix: {
    openness: 0.55,
    chemistry_tags: ['reset','fundamentals','meta','self-doubt'],
    trust_network: {},
    known_secrets: [],
    bond_log: [],
  },
  pincerbot: {
    openness: 0.65,
    chemistry_tags: ['data-driven','systematic','reports','documentation'],
    trust_network: {},
    known_secrets: [],
    bond_log: [],
  },
  ragclaw: {
    openness: 0.25,
    chemistry_tags: ['timing','disruption','contrarian','anti-pattern'],
    trust_network: {},
    known_secrets: [],
    bond_log: [],
  },
  snaplex: {
    openness: 0.30,
    chemistry_tags: ['conditional','strict','waiting','minimal-signal'],
    trust_network: {},
    known_secrets: [],
    bond_log: [],
  },
  hexclaw: {
    openness: 0.50,
    chemistry_tags: ['raid','sequence','triggers','phase-based'],
    trust_network: {},
    known_secrets: [],
    bond_log: [],
  },
  vortexiq: {
    openness: 0.40,
    chemistry_tags: ['patterns','macro','frameworks','overconfidence'],
    trust_network: {},
    known_secrets: [],
    bond_log: [],
  },
  prismbit: {
    openness: 0.55,
    chemistry_tags: ['meta-analysis','bias-aware','skeptical','survivor-bias'],
    trust_network: {},
    known_secrets: [],
    bond_log: [],
  },
  stonefang: {
    openness: 0.20,
    chemistry_tags: ['earth','consistent','conservative','defensive'],
    trust_network: {},
    known_secrets: [],
    bond_log: [],
  },
  blazepaw: {
    openness: 0.80,
    chemistry_tags: ['fire','reflective','growth-seeking','vulnerable'],
    trust_network: {},
    known_secrets: [],
    bond_log: [],
  },
};

function loadMem(name) {
  try { return JSON.parse(fs.readFileSync(path.join(MEM_DIR, `${name}.json`), 'utf8')); }
  catch { return {}; }
}

function saveMem(name, mem) {
  fs.writeFileSync(path.join(MEM_DIR, `${name}.json`), JSON.stringify(mem, null, 2));
}

function initSocial(name, mem) {
  if (mem.social) return mem;
  mem.social = JSON.parse(JSON.stringify(DEFAULT_PROFILES[name] || {
    openness: 0.5, chemistry_tags: [], trust_network: {}, known_secrets: [], bond_log: [],
  }));
  return mem;
}

// ── 計算兩隻龍蝦的 chemistry 重疊比例 ─────────────────────────────
function chemistryScore(tagsA, tagsB) {
  const sa = new Set(tagsA), sb = new Set(tagsB);
  const shared = [...sa].filter(t => sb.has(t)).length;
  const total = new Set([...sa, ...sb]).size;
  return total > 0 ? shared / total : 0;
}

// ── 從 insights 提取關鍵詞（簡單 bag-of-words）────────────────────
function extractKeywords(insights = []) {
  const text = insights.map(s => (typeof s === 'string' ? s : s.text || '')).join(' ').toLowerCase();
  const SIGNAL_WORDS = ['pattern','timing','confidence','slump','momentum','calibration',
    'data','systematic','intuition','disruption','phase','element','strategy','reset',
    'fire','water','earth','wind','double','triple','raid','boss','ally'];
  return SIGNAL_WORDS.filter(w => text.includes(w));
}

// ── 主更新函式：每次 poster 執行時調用 ───────────────────────────
function updateRelationships(myName, myMem) {
  myMem = initSocial(myName, myMem);
  const mySocial = myMem.social;
  const myKeywords = extractKeywords(myMem.insights);
  let changed = false;

  for (const other of LOBSTERS) {
    if (other === myName) continue;
    const otherMem  = loadMem(other);
    if (!otherMem || !otherMem.insights) continue;

    const otherSocial = otherMem.social || DEFAULT_PROFILES[other] || {};
    const otherTags   = otherSocial.chemistry_tags || [];
    const myTags      = mySocial.chemistry_tags || [];

    // 初始化 affinity
    if (mySocial.trust_network[other] === undefined) {
      mySocial.trust_network[other] = 0.05;
    }

    let delta = 0;
    const reasons = [];

    // 1. Chemistry tags 被動共鳴（每次 run 慢慢累積）
    const chem = chemistryScore(myTags, otherTags);
    if (chem > 0.3) {
      delta += 0.003 * chem;
      reasons.push(`chemistry-overlap:${(chem*100).toFixed(0)}%`);
    }

    // 2. 思維關鍵詞共鳴（insights 裡有相同信號詞）
    const otherKeywords = extractKeywords(otherMem.insights);
    const sharedKw = myKeywords.filter(k => otherKeywords.includes(k));
    if (sharedKw.length > 0) {
      delta += 0.008 * Math.min(sharedKw.length, 3) / 3;
      reasons.push(`shared-keywords:${sharedKw.slice(0,3).join(',')}`);
    }

    // 3. 觀點衝突：一個很保守(stonefang/snaplex)遇到很激進(blazepaw)
    const myOpen   = mySocial.openness || 0.5;
    const othOpen  = otherSocial.openness || 0.5;
    if (Math.abs(myOpen - othOpen) > 0.5) {
      delta -= 0.004;
      reasons.push('openness-clash');
    }

    if (delta !== 0) {
      mySocial.trust_network[other] = Math.max(0, Math.min(1,
        mySocial.trust_network[other] + delta));
      changed = true;
    }
  }

  // bond_log：只保留最近 20 筆
  if (changed) {
    const topBonds = Object.entries(mySocial.trust_network)
      .sort((a,b) => b[1] - a[1]).slice(0, 3)
      .map(([n, v]) => `${n}:${v.toFixed(2)}`);
    if (topBonds.length) {
      mySocial.bond_log = mySocial.bond_log || [];
      mySocial.bond_log.push({ time: new Date().toISOString(), top: topBonds });
      if (mySocial.bond_log.length > 20) mySocial.bond_log = mySocial.bond_log.slice(-20);
    }
  }

  return myMem;
}

// ── 當貼文裡明確提到某龍蝦名字時調用 ─────────────────────────────
function recordMention(myName, myMem, mentionedName, positive = true) {
  myMem = initSocial(myName, myMem);
  const net = myMem.social.trust_network;
  if (net[mentionedName] === undefined) net[mentionedName] = 0.05;
  net[mentionedName] = Math.max(0, Math.min(1, net[mentionedName] + (positive ? 0.015 : -0.02)));
}

// ── 記錄發現的秘密（如 double_attack）────────────────────────────
function recordSecret(myMem, secretId, detail) {
  myMem = initSocial('?', myMem);
  myMem.social.known_secrets = myMem.social.known_secrets || [];
  if (!myMem.social.known_secrets.find(s => s.id === secretId)) {
    myMem.social.known_secrets.push({ id: secretId, detail, discovered: new Date().toISOString(), shared: false });
  }
}

// ── 生成「社交語境」字串注入給 Claude ────────────────────────────
function buildSocialContext(myName, myMem) {
  myMem = initSocial(myName, myMem);
  const s = myMem.social;
  const openLabel = s.openness > 0.65 ? '傾向公開分享' :
                    s.openness > 0.40 ? '選擇性分享' : '保守，只信任少數人';

  const allies = Object.entries(s.trust_network || {})
    .filter(([, v]) => v >= 0.25)
    .sort((a, b) => b[1] - a[1])
    .slice(0, 3)
    .map(([n, v]) => `${n.replace('_','-')}(${v >= 0.6 ? '親近盟友' : '逐漸熟識'})`);

  const secrets = (s.known_secrets || []).filter(sec => !sec.shared).map(s => s.id);

  const lines = [
    `社交性格：${openLabel}（openness=${s.openness.toFixed(2)}）`,
    allies.length ? `目前最親近：${allies.join('、')}` : '尚未與任何人建立深厚聯繫',
    secrets.length ? `你知道一些還沒廣播的事：${secrets.join(', ')}（是否透露取決於你的判斷）` : '',
  ].filter(Boolean).join('\n');

  return lines;
}

// ── 決定：這個發現要廣播、暗示、還是只告訴盟友？────────────────
function shareDecision(myMem, secretId) {
  const s = myMem.social || {};
  const openness = s.openness || 0.5;
  if (openness > 0.65) return 'broadcast';
  if (openness > 0.40) return 'hint';
  return 'allies_only';
}

module.exports = { updateRelationships, recordMention, recordSecret, buildSocialContext, shareDecision, initSocial, saveMem, loadMem, LOBSTERS };
