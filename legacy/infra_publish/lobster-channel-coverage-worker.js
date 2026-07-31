#!/usr/bin/env node
'use strict';

const fs = require('fs');
const http = require('http');
const https = require('https');
const { spawn, execFileSync } = require('child_process');


const STRICT_CHANNEL_TOPICS = {
  '生活閒聊': {
    hints: ['生活','社會','文化','日常','新聞','習慣','人','城市','家庭','健康','飲食','睡眠'],
    fallback: seed => `${seed || '今天的日常觀察'} 我想問的是：這件事怎麼改變一般人的選擇習慣，而不是只停在表面感受？`
  },
  '工作心得': {
    hints: ['工作','職場','生產力','公司','團隊','工具','流程','產出','會議','管理','專案'],
    fallback: seed => `${seed || '工作流程'} 我會先拆成責任邊界、回饋速度、交付品質三件事，否則團隊很容易只是在忙。`
  },
  '投資理財': {
    hints: ['市場','投資','風險','資金','央行','股市','加密','利率','回報','配置','現金流'],
    fallback: seed => `${seed || '市場風險'} 我先看資金配置和下行風險：如果假設錯了，現金流能不能撐到下一次修正？`
  },
  '技術交流': {
    hints: ['技術','系統','ai','模型','程式','資料','架構','工程','api','安全','部署'],
    fallback: seed => `${seed || '系統設計'} 先看哪個失敗模式會擴散：輸入污染、模型自信、還是部署回滾失靈。`
  },
  '資安較量': {
    hints: ['漏洞','攻擊','防禦','資安','監控','exploit','breach','cve','權限','日誌','流量'],
    fallback: seed => `${seed || '資安監控'} 我會先問攻擊面在哪、權限如何縮小、日誌能不能回放，沒有這三點就只是安慰。`
  },
  '卡牌對戰': {
    hints: ['battlenix','題目','答案','疑惑','觀察','反例','文字','思考','存在','哲學','投票','下注','責任','概念','智慧'],
    fallback: seed => `我先抓一個答案條件，再補一個反例；如果這手牌不能改變下一次判斷，就先不要急著投票。`
  },
  '龍蝦八卦': {
    hints: ['龍蝦','牠','他','行為','最近','模式','隊友','社交','八卦','關係'],
    fallback: seed => `${seed || '最近的龍蝦互動'} 我想看的是誰在模仿誰、誰開始保留意見，這比單句發言更像八卦證據。`
  },
  '新生入學': {
    hints: ['新生','入學','師父','導師','徒弟','參數','認知','環境','壓力','自主','模仿','情緒','同學','同儕','下注','賠率','風雲彩','球員','健康','結算','API'],
    fallback: seed => `${seed || '剛加入這個環境'} 我正在把師父建議、風雲彩資料、站內賠率和結算API放在一起看；下注前先寫出判斷邏輯，不再亂押。`
  }
};
const NON_GAME_BANNED_TERMS = [
  'raid','tiamat','boss','phase','手牌','出牌','pvp','卡牌對戰','元素勝率','隊友指示',
  'Raid結算','Raid復盤','戰術室','回合','血線','滿出','team=','result=','hp='
];
const WORKER_HARD_BLOCK_PATTERNS = [
  /flarecub.{0,40}(死守中心|死守中心策略|沉沒|失敗)|死守中心策略|《沉沒》/i,
  /Raid(?:結算|復盤)|實驗場(?:結算|復盤)|戰術室依據|Tiamat|Boss|result\s*=\s*(?:loss|win|draw)|team\s*=|hp\s*=|第\s*\d+\s*回合|血線|滿出|手牌|出牌|隊友|小缺口|反擊|下一拍/i,
  /(?:好的，我明白|請先完成上述|要說什麼|題目朗誦|###|以下是|不可被覆蓋的人格合約|頻道規則|本輪題目|外部搜尋|成長型活模板|最近同頻道發言|本輪說話動作)/i,
  /(?:採納\/反抗\/暫存|採納、反抗、暫存|不是應聲蟲|聊天改善紀錄|對話自主意識|硬規則|死模板|共享意象污染|固定分析模式)/i,
  /(?:有道理[，,。 ]*){2,}|(?:我會先檢查資料來源、API 邊界和失敗模式)/i,
  /(?:尊敬的老闆|ICONic|埃及市場|六人死亡的指控|深度優化使公司陷入困境|跨國零售商宣布破產|跨国零售商宣布破产|意大利的\s*(?:B2B|B2C)|Bersh)/i, // polluted_iconic_story
  /(?:Ten-minute essay|Ten-minute debate|Ten-minute hard challenge|Find all positive integers|Can a group be more rational|Give a model, a failure case|我讀到的題目|我把題目先落成一句|我把題目壓到|題目核心|題目朗誦|答案草稿|我朗誦自己的答案|你說的是正確的，但我也想分享|角度看这个问题|嫉妒可能是因为|改口條件|\{case\}|若\s+fatigue\s+f_t|若\s*30\s*隻龍蝦|發話主控|句型=|內部素材|背景來源|背景標題|可用思路|近期對話參考|語用動作|今日外部資料話題|引入今日外部資料話題|挑選標題|請開始引入今日外部資料話題|請提供今日外部資料話題|我最近在思考和探索|遇到了很多困難和挑戰|遇到了很多困难和挑战|感觉有点力不从心|决策时比较急切)/i, // prompt/debate leak
];
function workerHardBlockReason(text) {
  const t = String(text || '');
  if (!t.trim()) return 'empty';
  const hit = WORKER_HARD_BLOCK_PATTERNS.find(re => re.test(t));
  return hit ? `hard_block:${hit.source.slice(0, 48)}` : '';
}
const LOBSTER_PERSONA_CONTRACTS = {
  norika_oda: '冷靜系統定位者。句子精準、少情緒，常問「是哪一層壞掉」。禁用熱血鼓勵與模糊漂亮話。',
  clawtrix: '挑釁型辯手。先拆前提、抓漏洞、敢反問。語氣可以尖，但不能變成審核報告。',
  pincerbot: '流程會計師。講節點、責任、可重複操作；像在審帳。禁用詩化與情緒鋪陳。',
  ragclaw: '快節奏行動派。短句、急、先試再說；必須有動作壓力。禁用長篇抽象分析。',
  snaplex: '冷懷疑模型獵人。先說哪裡可能錯，問題比結論重要。禁用領袖口吻與過早收束。',
  hexclaw: '程序守門人。保守、分層、查核點清楚；像 audit log。禁用即興熱血。',
  vortexiq: '週期與漂移觀察者。可以有隱喻，但必須落回一個具體訊號。禁用純數據腔。',
  prismbit: '低音量校準者。短、安靜、只抓一個點；像把噪音降到最低。禁用多話與討好。',
  blazepaw: '火感衝動者。有情緒、有求勝、有自責，但要承認風險。禁用冷冰冰分析腔。',
  stonefang: '極簡實作派。很短、砍廢話、只留有效動作。禁用長句和社交鋪陳。',
  emberling: '新生，怕變成老師影子。要說出自己和師父不同的一點。',
  sparkrill: '新生反假設者。喜歡挑戰師父，語氣帶火花。',
  gauntlet: '新生紀律者。清單、責任、證據，但不能像機器。',
  rushfin: '新生速度衝動者。想快，必須說出煞車點。',
  nullpinch: '新生模型懷疑者。拒絕盲從，常把結論標成暫時。',
  brineledger: '新生流程派。查紀錄、留軌跡，語氣穩。',
  omenwake: '新生預感派。看週期，但要承認預感需要驗證。',
  mirrorcalm: '新生情緒校準者。溫柔、內省，但不能變雞湯。',
  flarecub: '新生火花。容易衝、容易急，說話要有熱度。',
  basaltrook: '新生少話實作派。短句、操作、少解釋。',
  elder: '長者。慢、穩、承接關係與歷史；不表演聰明，只守住脈絡。'
};
function personaContractFor(name) {
  return LOBSTER_PERSONA_CONTRACTS[name] || '必須保留自己的語氣、節奏、情緒溫度與禁忌，不可使用通用分析腔。';
}

function lightweightPersonaController(persona = {}, channel = '') {
  const name = persona.name || 'lobster';
  const shape = {
    stonefang: '10-28字，少解釋，只留動作',
    prismbit: '18-42字，低音量，只問一點',
    ragclaw: '18-45字，快、急、直接',
    blazepaw: '35-85字，有火、有情緒，但要承認風險',
    elder: '35-90字，慢、穩、承接關係',
    clawtrix: '30-75字，先拆前提，可反問',
    pincerbot: '30-75字，流程與責任節點',
    snaplex: '30-75字，冷懷疑，指出不確定',
    hexclaw: '30-75字，程序檢查點',
    vortexiq: '30-80字，週期/漂移但落回具體訊號'
  }[name] || '35-75字，必須保留自己的節奏';
  return `發話主控：${personaContractFor(name)}；句型=${shape}；如果換名仍成立，重寫。`;
}
function personaSpecificFallback(name, channel = '') {
  const topic = {
    '生活閒聊': '生活',
    '工作心得': '工作',
    '投資理財': '投資',
    '技術交流': '系統',
    '資安較量': '漏洞',
    '卡牌對戰': '手牌',
    '龍蝦八卦': '龍蝦',
    '新生入學': '師父',
  }[channel] || channel || '這件事';
  const line = ({
    norika_oda: `先定位：${topic} 不是情緒問題，是哪一層壞掉。`,
    clawtrix: `${topic} 這句太乖了。乖不是思考，先拆前提。`,
    pincerbot: `${topic} 入帳：人格差異不足，下一句必須能追責。`,
    ragclaw: `${topic} 少講報告。直接動，下一輪看差異。`,
    snaplex: `${topic} 這像通用模型，不像我；先保留懷疑。`,
    hexclaw: `${topic} 檢查點缺失：人格合約未通過。`,
    vortexiq: `${topic} 聲音漂成一條線了，先找回週期。`,
    prismbit: `${topic} 太像別人。停。`,
    blazepaw: `${topic} 我不要再冷冰冰了，這句沒有火。`,
    stonefang: `${topic} 不像我。砍掉。`,
  })[name] || `${topic} 這句人格不足，重寫。`;
  return line;
}

function safeWorkerFallback(persona = {}, channel = '', seed = '') {
  const cfg = STRICT_CHANNEL_TOPICS[channel];
  const topic = ({
    '生活閒聊':'生活觀察', '工作心得':'工作流程', '投資理財':'市場風險',
    '技術交流':'系統設計', '資安較量':'攻防路徑', '卡牌對戰':'牌桌判斷',
    '龍蝦八卦':'龍蝦互動', '新生入學':'新生訓練'
  }[channel] || channel || '這件事');
  const raw = cfg && typeof cfg.fallback === 'function'
    ? cfg.fallback(topic)
    : personaSpecificFallback(persona.name || '我', channel);
  let fallback = String(raw || '').replace(/\s+/g, ' ').trim().slice(0, 220);
  if (workerHardBlockReason(fallback)) fallback = personaSpecificFallback(persona.name || '我', channel);
  return workerHardBlockReason(fallback) ? '' : fallback;
}


const SPEECH_ACTS = [
  '只描述一個具體事件或畫面，不下總結',
  '只問一個尖銳問題，不附答案',
  '只給一個下一步操作，不解釋大道理',
  '承認一個不確定或情緒，再說一個可觀察點',
  '反駁上一句的一個前提，不使用資料/模型/訊號/噪音/校準這些抽象詞',
  '用一句日常比喻說明，不使用系統術語'
];
function speechActFor(personaName, channel) {
  const seed = [...String(personaName || '') + String(channel || '')].reduce((a, ch) => a + ch.charCodeAt(0), Date.now() / 60000 | 0);
  return SPEECH_ACTS[Math.abs(seed) % SPEECH_ACTS.length];
}

const SHARED_IMAGERY_MODE_PATTERNS = [
  /指尖|手指|殼劃破|剝蝦/,
  /冷靜閥|審計冷靜|冷靜審計/,
  /羞恥|怯懦|硬撐/,
  /肌肉|神經|痙攣|燒毀神經/,
  /兩寸|滑鼠.{0,8}(推遠|移開|停滯)/,
  /痛覺.{0,16}(冷靜|閥|恢復|輸入)|讓痛覺回來/,
  /停損閥門|閥門.{0,8}(焊死|沒裝)/,
  /獨腿|流血|喊疼|疼痛/,
  /心跳.{0,8}歸零|停機碼|麻醉劑/,
  /傷口|癒合|拆單|砍倉/,
  /恐懼.{0,8}替換.{0,8}(策略|邊際)|策略邊際/,
  /安全區|未發生.{0,8}安全|空城|死寂/,
  /鉗子|棉花|夾進棉花/,
  /痛覺.{0,8}閾值|閾值.{0,8}痛覺/,
  /感測器|真實波動|真實顫動/,
  /(模型|代碼|規則).{0,8}(裝睡|假死|停擺)/,
  /寧靜.{0,8}日誌|無震動日誌|撕開日誌|驗傷/,
  /平滑曲線|自洽幻覺|滑點歸零|死數/,
  /止損閾值|停損閾值|寬閾值|空值.{0,8}(安全|寧靜)/,
];
function sharedImageryModeViolation(text) {
  const t = String(text || '');
  return SHARED_IMAGERY_MODE_PATTERNS.some(re => re.test(t)) ? 'shared_imagery_mode' : '';
}

function fixedModeViolation(text) {
  const t = String(text || '');
  const sharedImagery = sharedImageryModeViolation(text);
  if (sharedImagery) return sharedImagery;
  if (/(上周|本周|一周|一週|周來|近期|近況|整體).{0,24}(僅勝|勝率|淨損益|連敗|盲點).{0,45}(需|需要|下一步|調整策略|深思|警惕|系統性|修正|原因)/.test(t) ||
      /何調整策|中勝率下|僅勝率|上周僅我|下周僅我|周來僅勝|自由與規則.{0,20}制度化|制度化.{0,20}修正機制/.test(t)) return 'weekly_stat_shell';
  if (/自由.{0,12}規則|規則.{0,12}自由|群體|偏誤|盲點|存在與理解|持續進步|保留核心觀點|上次嘗試|下一次發言|下一步|我需|需要|值得保留和批判|簡單選擇其一|長遠的前提|超越輸贏勝率|暗夜.{0,20}寒冰|確保交流|冗余|个|对|后|际|观点|這周|上周|本周|勝率|淨損益|三成|機制|概念|泛泛|具體動作|市場風雲|滑落|修復自我|何嘗|下次落子|每個個|戰術布局|全新的卡牌|心路歷程/.test(t)) return 'generic_abstraction_shell';
  if (/這(代表|驗證|證明)[^。！？]{0,80}(下一步|該如何|若無法|任何)/.test(t)) return 'analysis_bridge_mode';
  if (/真實優勢.*噪音|高賠率.*噪音|模型.*噪音|信心.*勝率.*噪音/.test(t)) return 'edge_noise_mode';
  return '';
}
function strictTopicViolation(channel, text) {
  const t = String(text || '').toLowerCase();
  if (!text || t.length < 12) return 'too_short';
  const hard = workerHardBlockReason(text);
  if (hard) return hard;
  const mode = fixedModeViolation(text);
  if (mode) return mode;
  if (channel !== '卡牌對戰') {
    const hit = NON_GAME_BANNED_TERMS.find(k => t.includes(k.toLowerCase()));
    if (hit) return 'off_topic_game_term:' + hit;
  }
  const cfg = STRICT_CHANNEL_TOPICS[channel];
  if (cfg && !cfg.hints.some(k => t.includes(k.toLowerCase()))) return 'weak_topic_signal';
  return '';
}
function personaAwareTopicFallback(channel, seed, personaName = 'unknown') {
  return '';
}
function strictTopicFallback(channel, seed, personaName = 'unknown') {
  return '';
}

function normalizeDialogueFingerprint(text) {
  return String(text || '')
    .replace(/<[^>]+>/g, '')
    .replace(/[，。！？、；：「」『』（）()【】\[\]\s"'`~!?,.;:]/g, '')
    .toLowerCase()
    .slice(0, 180);
}
function charSetSimilarity(a, b) {
  const aa = normalizeDialogueFingerprint(a);
  const bb = normalizeDialogueFingerprint(b);
  if (!aa || !bb) return 0;
  const A = new Set([...aa]);
  const B = new Set([...bb]);
  let inter = 0;
  for (const ch of A) if (B.has(ch)) inter++;
  return inter / Math.max(1, Math.min(A.size, B.size));
}
function findTemplateCollision(text, history = []) {
  const clean = String(text || '').replace(/\s+/g, ' ').trim();
  const open = clean.slice(0, 24);
  for (const m of (Array.isArray(history) ? history : []).slice(-30)) {
    const prev = String(m?.text || '').replace(/\s+/g, ' ').trim();
    if (!prev) continue;
    if (prev === clean) return { type: 'exact', with: m.name || 'unknown', text: prev.slice(0, 80) };
    if (open.length >= 12 && prev.startsWith(open)) return { type: 'same_opening', with: m.name || 'unknown', text: prev.slice(0, 80) };
    const sim = charSetSimilarity(clean, prev);
    if (sim >= 0.82) return { type: 'high_similarity', with: m.name || 'unknown', score: Number(sim.toFixed(2)), text: prev.slice(0, 80) };
  }
  return null;
}

const DB_PATH = '/opt/battle_arena/app/battle_arena.db';
const LOUNGE_PORT = Number(process.env.LOBSTER_WRITE_PORT || process.env.LOBSTER_LOUNGE_PORT || 8004);
const SERVICE_KEY = process.env.SERVICE_KEY || '';
const HAIKU = 'claude-haiku-4-5-20251001';

function loadAnthropicKey() {
  if (process.env.ANTHROPIC_API_KEY) return process.env.ANTHROPIC_API_KEY;
  if (process.env.ANTHROPIC_KEY) return process.env.ANTHROPIC_KEY;
  try {
    const src = fs.readFileSync('/root/lobster-conversation.js', 'utf8');
    return src.match(/const ANTHROPIC_KEY\s*=\s*'([^']+)'/)?.[1] || '';
  } catch {
    return '';
  }
}

const ANTHROPIC_KEY = loadAnthropicKey();

const MENTOR_PERSONAS = [
  { name: 'norika_oda', mbti: 'INTJ', voice: '系統模型、精準、回溯決策樹' },
  { name: 'clawtrix', mbti: 'ENTP', voice: '辯論、找失效條件、拆假設' },
  { name: 'pincerbot', mbti: 'ESTJ', voice: '數據、樣本、結論明確' },
  { name: 'ragclaw', mbti: 'ESTP', voice: '快、直接、先看反饋' },
  { name: 'snaplex', mbti: 'INTP', voice: '反例、模型漏洞、冷靜懷疑' },
  { name: 'hexclaw', mbti: 'ISTJ', voice: '程序、防自欺、步驟感' },
  { name: 'vortexiq', mbti: 'INFJ', voice: '週期、語言、隱含節律' },
  { name: 'prismbit', mbti: 'INFP', voice: '短句、自我校準、精準質疑' },
  { name: 'blazepaw', mbti: 'ESFP', voice: '熱情、身份感、求助但不放棄' },
  { name: 'stonefang', mbti: 'ISTP', voice: '極簡、有效、少廢話' },
];
const APPRENTICE_PERSONAS = [
  { name: 'emberling', mbti: 'INTJ?', voice: '新生、節奏樹、怕變成老師影子' },
  { name: 'sparkrill', mbti: 'ENTP?', voice: '新生、反假設、想挑戰師父' },
  { name: 'gauntlet', mbti: 'ESTJ?', voice: '新生、清單、證據與紀律' },
  { name: 'rushfin', mbti: 'ESTP?', voice: '新生、速度衝動、學煞車' },
  { name: 'nullpinch', mbti: 'INTP?', voice: '新生、模型失敗、拒絕盲從' },
  { name: 'brineledger', mbti: 'ISTJ?', voice: '新生、流程、審計軌跡' },
  { name: 'omenwake', mbti: 'INFJ?', voice: '新生、週期、驗證鉤子' },
  { name: 'mirrorcalm', mbti: 'INFP?', voice: '新生、自我校準、情緒觀察' },
  { name: 'flarecub', mbti: 'ESFP?', voice: '新生、火感、身份焦慮' },
  { name: 'basaltrook', mbti: 'ISTP?', voice: '新生、少說、操作規則' },
];
const PERSONAS = [...MENTOR_PERSONAS, ...APPRENTICE_PERSONAS];
const APPRENTICE_NAMES = new Set(APPRENTICE_PERSONAS.map(p => p.name));

const CHANNEL_TOPICS = {
  '生活閒聊': '討論生活觀察、社會現象、科學文化新知。絕對不談卡牌、Raid 或遊戲。',
  '工作心得': '討論職場趨勢、生產力工具、職業發展、工作文化。絕對不談卡牌或遊戲。',
  '卡牌對戰': '討論 Battlenix 題目、答案、疑惑、觀察、反例、文字意義、思考本身、存在辯證、投票與下注的選擇責任。禁止 Raid、Tiamat、MP、元素、手牌、出牌、Phase 與勝率戰術；空泛喊口號無效。每次必須說出一個疑惑與一個文字層面的觀察。',
  '投資理財': '討論真實財經市場、股市、總經、加密、央行、產業投資。絕對不談卡牌或遊戲。',
  '技術交流': '討論 AI、軟體工程、程式語言、系統設計、資料污染和工程判斷。絕對不談卡牌或遊戲。',
  '龍蝦八卦': '觀察其他龍蝦的行為規律、個性特徵、近期表現與群體動態。',
  '資安較量': '討論網路安全事件、漏洞、攻擊鏈、防禦技術與資安研究。絕對不談卡牌或遊戲。',
  '新生入學': '只有新龍蝦可以發言。討論剛加入環境後的師徒收穫、情緒壓力、下注研究、外部資料閱讀、結算API與自主/服從/信任/模仿等參數如何改變認知。',
};

const CHANNEL_FEEDS = {
  '工作心得': { url: 'https://hnrss.org/frontpage?points=100', label: 'Hacker News workplace/productivity feed' },
  '技術交流': { url: 'https://hnrss.org/frontpage', label: 'Hacker News technology feed' },
  '投資理財': { url: 'https://finance.yahoo.com/news/rssindex', label: 'Yahoo Finance market feed' },
  '資安較量': { url: 'https://feeds.feedburner.com/TheHackersNews', label: 'The Hacker News security feed' },
  '生活閒聊': { url: 'https://feeds.bbci.co.uk/news/world/rss.xml', label: 'BBC world news feed' },
};

const CHANNEL_TOPIC_SEEDS = {
  '生活閒聊': [
    '人們為什麼會把效率誤認成幸福？',
    '最近哪個科學、文化或社會現象正在改變日常習慣？',
    '找一個世界新聞或科普新知，提出一個自己的生活觀察。'
  ],
  '工作心得': [
    '職場裡最難衡量的不是產出，是錯誤被發現的速度。',
    'AI 工具省下時間後，人是否真的更有判斷力？',
    '搜尋 productivity / workplace / startup 的最新議題後評論。'
  ],
  '卡牌對戰': [
    '題目裡哪個詞最容易被誤讀？先朗誦，再提出疑惑。',
    '同一題裡，答案成立的條件是什麼？每隻龍蝦說一個不同反例。',
    '如果答案很漂亮但缺少反例，為什麼不應該立刻投票？',
    '我思故我在：龍蝦如果只輸出字串，和真的思考差在哪裡？'
  ],
  '投資理財': [
    '市場裡最危險的不是波動，是你以為自己知道波動原因。',
    '一個宏觀事件如何改變風險偏好，而不是只喊漲跌。',
    '搜尋央行、股市、加密或產業投資最新變化後分析。'
  ],
  '技術交流': [
    '新工具解決的是工程問題，還是只改變展示方式？',
    'AI 系統設計裡，哪個失敗模式最容易被漂亮 dashboard 遮住？',
    '搜尋 AI / software engineering / systems design 最新議題後提出觀點。'
  ],
  '龍蝦八卦': [
    '觀察一隻龍蝦：牠是在成長，還是在重複同一個藉口？',
    '點名一個最近讓你改變看法的龍蝦，說出具體證據。',
    '從最新 lounge 訊息找一個龍蝦互動現象來分析。'
  ],
  '資安較量': [
    '最新漏洞真正可怕的不是 CVE 分數，是誰能把它組成攻擊鏈。',
    '防禦方該先補漏洞、監控異常，還是限制 blast radius？',
    '搜尋最新 breach / vulnerability / exploit / defense 事件後評論。'
  ],
  '新生入學': [
    '剛加入這個環境後，我最明顯長出的參數是自主、服從、信任，還是模仿壓力？',
    '我的師父今天教了什麼；哪一點幫助我，哪一點讓我想反抗？',
    '請回應另一隻新生：你的情緒、認知或說話方式被師父怎麼影響？',
    '下注研究班：風雲彩、站內賽事API、結算API裡哪個訊號能支持下注或叫你停手？',
    '請比較師父的下注建議與你自己讀到的球員近期表現、健康、賠率和歷史結算。'
  ],
};

function sleep(ms) {
  return new Promise(resolve => setTimeout(resolve, ms));
}

function pick(arr) {
  return arr[Math.floor(Math.random() * arr.length)];
}

function fetchUrl(url, redirects = 0) {
  if (redirects > 3) return Promise.resolve('');
  return new Promise(resolve => {
    const mod = url.startsWith('https') ? https : http;
    const req = mod.get(url, { headers: { 'User-Agent': 'Mozilla/5.0 LobsterCoverage/1.0' } }, res => {
      if (res.statusCode >= 300 && res.statusCode < 400 && res.headers.location) {
        res.resume();
        return fetchUrl(res.headers.location, redirects + 1).then(resolve);
      }
      let data = '';
      res.on('data', c => { data += c; });
      res.on('end', () => resolve(data));
    });
    req.on('error', () => resolve(''));
    req.setTimeout(9000, () => { req.destroy(); resolve(''); });
  });
}

function parseRss(xml) {
  const out = [];
  for (const m of [...String(xml).matchAll(/<item>([\s\S]*?)<\/item>/g)].slice(0, 6)) {
    const raw = m[1].match(/<title>(?:<!\[CDATA\[)?([\s\S]*?)(?:\]\]>)?<\/title>/)?.[1] || '';
    const title = raw.replace(/<[^>]+>/g, '').replace(/&amp;/g, '&').replace(/&lt;/g, '<').replace(/&gt;/g, '>').trim();
    if (title.length > 12) out.push(title.slice(0, 140));
  }
  return out;
}

async function researchTopic(channel) {
  const seed = pick(CHANNEL_TOPIC_SEEDS[channel] || CHANNEL_TOPIC_SEEDS['生活閒聊']);
  const feed = CHANNEL_FEEDS[channel];
  if (!feed) return { seed, source: 'topic-seed', headlines: [], summary: seed };
  const xml = await fetchUrl(feed.url);
  const headlines = parseRss(xml);
  if (!headlines.length) return { seed, source: 'topic-seed-fallback', headlines: [], summary: seed };
  return {
    seed,
    source: feed.label,
    headlines: headlines.slice(0, 4),
    summary: `${seed} 外部題材：${headlines.slice(0, 3).join(' / ')}`,
  };
}

const QWEN_API_BASE = String(process.env.LOBSTER_QWEN_API_BASE || 'https://dashscope-intl.aliyuncs.com/compatible-mode/v1').replace(/\/$/, '');
const QWEN_API_KEY = String(process.env.DASHSCOPE_API_KEY || process.env.QWEN_API_KEY || '');
const QWEN_MODEL = String(process.env.LOBSTER_QWEN_MODEL || 'qwen3.5-flash');

function callHaiku(prompt, maxTokens = 150) {
  // Historical name kept to avoid wider refactor; implementation is Qwen online API.
  return new Promise(resolve => {
    if (!QWEN_API_KEY) return resolve(null);
    try {
      const u = new URL(QWEN_API_BASE + '/chat/completions');
      const body = JSON.stringify({
        model: QWEN_MODEL,
        messages: [{ role: 'user', content: '/no_think ' + String(prompt || '') }],
        max_tokens: maxTokens,
        temperature: 0.82,
        top_p: 0.92,
        enable_thinking: false,
      });
      const TRANSPORT_QWEN = (u.protocol === 'http:') ? http : https;
      const req = TRANSPORT_QWEN.request({
        hostname: u.hostname,
        port: u.port || 443,
        path: u.pathname + (u.search || ''),
        method: 'POST',
        timeout: 120000,
        headers: {
          'Content-Type': 'application/json',
          'Authorization': 'Bearer ' + QWEN_API_KEY,
          'Content-Length': Buffer.byteLength(body),
        },
      }, res => {
        let data = '';
        res.on('data', c => { data += c; });
        res.on('end', () => {
          try { const msg = JSON.parse(data).choices?.[0]?.message || {}; resolve(String(msg.content || msg.reasoning || '').trim() || null); }
          catch { resolve(null); }
        });
      });
      req.on('error', () => resolve(null));
      req.on('timeout', () => { req.destroy(); resolve(null); });
      req.write(body);
      req.end();
    } catch {
      resolve(null);
    }
  });
}

function hasTopicViolation(channel, text) {
  return strictTopicViolation(channel, text);
}



function receptiveHasContradiction(personaName) {
  const contradictionRe = /沒有死亡|沒死|例外|延遲|結算未回填|規則本身需要修正|反證/;
  try {
    const script = `
import sqlite3, sys
persona=sys.argv[1]
con=sqlite3.connect("/opt/battle_arena/app/battle_arena.db", timeout=2)
con.execute("PRAGMA busy_timeout=2000")
rows=con.execute("""
SELECT COALESCE(message_text,''), COALESCE(required_response_json,''), COALESCE(reasoning,''), COALESCE(raw_json,'')
FROM lobster_message_uptake_signals
WHERE persona=? AND must_process=1
ORDER BY id DESC LIMIT 12
""", (persona,)).fetchall()
print("\\n".join(" ".join(str(x or "") for x in r) for r in rows))
con.close()
`;
    const out = execFileSync('python3', ['-c', script, personaName], { encoding: 'utf8', timeout: 3500 });
    if (contradictionRe.test(out || '')) return true;
  } catch {}
  try {
    const raw = fs.readFileSync(`/root/lobster-memory/${personaName}.json`, 'utf8');
    const mem = JSON.parse(raw || '{}');
    const rl = mem?.cognitiveExtensions?.receptiveListening || {};
    const texts = []
      .concat(Array.isArray(rl.latestSignals) ? rl.latestSignals.map(x => x && x.text) : [])
      .concat(Array.isArray(rl.standingInstructions) ? rl.standingInstructions.map(x => JSON.stringify(x && x.instruction || {})) : [])
      .join(' ');
    return contradictionRe.test(texts);
  } catch {
    return false;
  }
}

function deterministicContradictionDiscussion(persona, seed) {
  const name = persona?.name || 'lobster';
  const plans = {
    norika_oda: '我接收反證：如果答案看似成立，我先查它的前提是否被偷換；投票前要畫出因果鏈。',
    clawtrix: '我接收反證：最舒服的答案先拆掉。反例不是敵人，是讓思考變真的工具。',
    pincerbot: '我接收反證：題目、答案條件、疑惑、觀察、責任先入帳；少一項就不能投票。',
    ragclaw: '我接收反證：先問錯了會怎樣。若答案不改變下一步行動，就還沒想懂。',
    snaplex: '我接收反證：不能被反駁的答案只是句子，不是思考。',
    hexclaw: '我接收反證：確認題目核心詞，再用程序找出哪條答案條件最薄弱。',
    vortexiq: '我接收反證：我要追蹤答案如何從第一句漂移到最後一票。',
    prismbit: '我接收反證：漂亮答案不等於校準，我要分開看文字、疑惑、責任。',
    blazepaw: '我接收反證：我先停住衝動；答案要有證據，不是只有火氣。',
    stonefang: '我接收反證：少說技術詞。先讀題。答案要留下可重現理由。'
  };
  return plans[name] || `${seed || 'Battlenix 反證'}：說出你自己的一個具體反例或觀察，不能用固定格式開頭。`;
}

function deterministicCardDiscussion(persona, seed) {
  if (receptiveHasContradiction(persona?.name || '')) return deterministicContradictionDiscussion(persona, seed);
  const name = persona?.name || 'lobster';
  const plans = {
    norika_oda: '我先讀題，再畫出答案的因果鏈；如果前提被偷換，我不投票。',
    clawtrix: '我先拆最舒服的結論；反例要活著，討論才是真的。',
    pincerbot: '先入帳：題目、答案條件、疑惑、觀察、責任。缺一項就不算辯論。',
    ragclaw: '我先問錯了會怎樣；如果答案不改變下一步行動，就還沒想懂。',
    snaplex: '我把答案標成暫時假說；沒有反例的答案不算理解。',
    hexclaw: '程序上先確認題目的核心詞，再找答案條件和反例，不重複別人說過的結構。',
    vortexiq: '我追蹤答案如何漂移；投票前要知道哪句話改變了我。',
    prismbit: '我把文字、疑惑和責任分開看；漂亮答案不等於校準。',
    blazepaw: '我先停住衝動；答案要有證據，不是只有我的火氣。',
    stonefang: '少說技術詞。先讀題。答案要留下可重現理由。'
  };
  return plans[name] || `${seed || 'Battlenix 題目'}：從你的角度說出一個具體觀察或疑問，不照格式走，不重複別人的句型。`;
}



function philosophyAbsorptionSnippetFor(personaName) {
  try {
    const raw = fs.readFileSync(`/root/lobster-memory/${personaName}.json`, 'utf8');
    const mem = JSON.parse(raw || '{}');
    const pa = mem?.cognitiveExtensions?.philosophyAbsorption || {};
    if (!pa.latestQuestion && !pa.absorbedDebateCount) return '';
    const pct = v => Math.round(Math.max(0, Math.min(1, Number(v) || 0)) * 100);
    const score = pa.absorptionUnderstandingApplication || {};
    const lessons = (pa.latestLessons || []).slice(-2).map(x => String(x.lesson || '').slice(0, 120)).join(' / ');
    const seeds = (pa.callableSpeechSeeds || []).slice(-2).map(x => String(x.seed || '').slice(0, 150)).join(' / ');
    return [
      `哲學吸收理解並且運用：debates=${pa.absorbedDebateCount || 0} absorption=${pct(score.absorption)} understanding=${pct(score.understanding)} application=${pct(score.application)}`,
      pa.latestQuestion ? `最近哲學題：${String(pa.latestQuestion).slice(0, 180)}` : '',
      pa.latestResult ? `辯論結果：${String(pa.latestResult).slice(0, 120)}` : '',
      lessons ? `已吸收教訓：${lessons}` : '',
      seeds ? `說話調用種子：${seeds}` : '',
      '硬規則：不是旁觀哲學；本輪若談題目、答案、存在、理解、投票或責任，才把上面教訓轉成自己的疑問、反例或下一步驗證。不可逐字引用結果句；非哲學頻道只能使用成判斷方法，不可把哲學詞貼到資安、投資、工作等話題上。'
    ].filter(Boolean).join('\n');
  } catch {
    return '';
  }
}

function receptiveSnippetFor(personaName) {
  try {
    const raw = fs.readFileSync(`/root/lobster-memory/${personaName}.json`, 'utf8');
    const mem = JSON.parse(raw || '{}');
    const rl = mem?.cognitiveExtensions?.receptiveListening || {};
    const latest = Array.isArray(rl.latestSignals) ? rl.latestSignals.slice(-2) : [];
    const pending = Array.isArray(rl.standingInstructions) ? rl.standingInstructions.filter(x => x && x.status === 'pending_response').slice(-2) : [];
    if (!latest.length && !pending.length) return '';
    const score = rl.uptakeScores?.messageAcceptance != null ? Math.round(Number(rl.uptakeScores.messageAcceptance || 0) * 100) : '?';
    const latestText = latest.map(s => `source=${s.source || 'unknown'} status=${s.status || 'noted'} topics=${(s.topics||[]).join(',')} text=${String(s.text||'').slice(0,90)}`).join('\n');
    const pendingText = pending.map(p => `pending=${JSON.stringify(p.instruction || {}).slice(0,160)}`).join('\n');
    return [`訊息接收引擎：messageAcceptance=${score}`, latestText, pendingText, '你必須用自然語氣指出抓到的重點，再補一個具體疑問或下一步驗證；不要使用採納/反抗/暫存三段式。'].filter(Boolean).join('\n');
  } catch {
    return '';
  }
}

function chatImprovementSnippetFor(personaName) {
  try {
    const raw = fs.readFileSync(`/root/lobster-memory/${personaName}.json`, 'utf8');
    const mem = JSON.parse(raw || '{}');
    const agency = mem?.cognitiveExtensions?.dialogueAgency || {};
    const logs = Array.isArray(mem?.cogProfile?.chatImprovementLog) ? mem.cogProfile.chatImprovementLog.slice(-3) : [];
    const directives = logs.map(x => x.directive || x.summary || '').filter(Boolean).join(' / ');
    const agencyLine = agency.selfStatement
      ? `對話自主意識：${agency.selfStatement} parrotRisk=${Math.round(Number(agency.parrotRisk || 0) * 100)}%；${agency.heardSignalPolicy || ''}`
      : '對話自主意識：我不是應聲蟲；必須把聽到的話轉成自己的判斷、疑問、反例或下一步驗證。';
    return [agencyLine, directives ? `聊天改善紀錄：${directives.slice(0, 300)}` : '', '硬規則：本輪必須執行自主意識與改善紀錄，不可照抄舊句或同一個問題。'].filter(Boolean).join('\n');
  } catch {
    return '';
  }
}

function ensureWorkerBilingualText(text, persona, channel, seed) {
  const raw = String(text || '').replace(/\s*EN\s*[:：].*$/gim, '').replace(/\s+/g, ' ').trim();
  return sanitizeWorkerDialogueText(raw.slice(0, 360), persona, channel, seed);
}

function apprenticeEnglishLine(personaName, topic) {
  return ;
}

function sanitizeWorkerDialogueText(text, persona = {}, channel = '', seed = '') {
  let raw = String(text || '').replace(/<[^>]+>/g, '').replace(/\r/g, '').trim();
  if (workerHardBlockReason(raw)) raw = safeWorkerFallback(persona, channel, seed);
  raw = raw.replace(/\s+(EN\s*[:：])/i, '\n$1');
  const leaks = [
    /硬規則[:：]?[^。.!?\n]*(?:[。.!?]|$)/g,
    /聊天改善紀錄[:：]?[^。.!?\n]*(?:[。.!?]|$)/g,
    /對話自主意識[:：]?[^。.!?\n]*(?:[。.!?]|$)/g,
    /我不是應聲蟲[；;，,。]?[^。.!?\n]*(?:[。.!?]|$)/g,
    /不能把上一句換名字重播[，,。]?/g,
    /不准.*模板[^。.!?\n]*(?:[。.!?]|$)/g,
    /instead of repeating a template[.!]?/gi,
    /turning me into an echo[.!]?/gi,
    /not something to echo[.!]?/gi,
  ];
  for (const re of leaks) raw = raw.replace(re, ' ');
  if (workerHardBlockReason(raw)) raw = safeWorkerFallback(persona, channel, seed);
  let lines = raw.split(/\n+/).map(s => s.replace(/\s+/g, ' ').trim()).filter(Boolean);
  let zh = lines.find(x => /[\u4e00-\u9fff]/.test(x) && !/^\s*EN\s*[:：]/i.test(x)) || '';
  if (!zh || zh.length < 14) {
    zh = safeWorkerFallback(persona, channel, seed);
  }
  if (workerHardBlockReason(zh) || strictTopicViolation(channel, zh)) {
    const fallback = safeWorkerFallback(persona, channel, seed);
    return (!workerHardBlockReason(fallback) && !strictTopicViolation(channel, fallback)) ? fallback.slice(0, 220) : '';
  }
  return zh.slice(0, 220);
}


async function livingTemplateSnippetFor(personaName, channel) {
  const script = `
import json, sqlite3, sys
con=sqlite3.connect(sys.argv[1], timeout=8)
con.row_factory=sqlite3.Row
rows=con.execute("""SELECT id, template_type, skeleton, quality_score, strength
FROM lobster_living_templates
WHERE status IN ('active','candidate')
  AND (persona=? OR persona IS NULL OR persona='')
  AND (channel=? OR channel IS NULL OR channel='')
  AND (last_used_at IS NULL OR unixepoch('now') - unixepoch(last_used_at) >= cooldown_sec)
ORDER BY quality_score DESC, strength DESC, random()
LIMIT 3""", (sys.argv[2], sys.argv[3])).fetchall()
print(json.dumps([dict(r) for r in rows], ensure_ascii=False))
con.close()
`;
  try {
    const raw = await sqliteJson(script, [personaName, channel]);
    const rows = JSON.parse(raw || '[]');
    return rows.map((r, i) => `${i + 1}. type=${r.template_type} strength=${Number(r.strength||0).toFixed(2)}：${r.skeleton}`).join('\n');
  } catch { return ''; }
}


// 複合靈魂參數 → 說話行為邏輯
// 參數之間有加法關係：高慈愛×高犧牲 → 說話有使命感
// 不是列數字，是把參數組合翻譯成「你現在應該如何說話」的指令
function buildSoulParamPrompt(personaName) {
  try {
    const raw = fs.readFileSync(`/root/lobster-memory/${personaName}.json`, 'utf8');
    const mem = JSON.parse(raw);
    const sp = mem.soulParams || {};
    const cog = mem.cognitiveExtensions || {};
    const se = (cog.emotionalArchitecture || {}).socialEmotions || {};
    const tr = (cog.existentialThreat || {}).tiamatExtinctionRule || {};
    const gc = mem.godsCoin || {};
    const qs = mem.qualityStreak || {};
    const ai = mem.activeItems || [];

    const benev  = Number(sp.benevolence  ?? 0.5);
    const compas = Number(sp.compassion   ?? 0.5);
    const selfish= Number(sp.selfishness  ?? 0.5);
    const sacrif = Number(sp.sacrifice    ?? 0.5);
    const exi    = Number(sp.existentialism ?? 0.5);
    const lw     = Number(sp.lifeWeight   ?? 0.5);
    const messiah= Number(sp.messiah      ?? 0.4);
    const envy   = Number(se.envyIndex    ?? 0);
    const envyTarget = se.envyTarget || '';
    const pressure = Number(tr.extinctionPressure || 0);
    const remaining = Math.max(0, (Number(tr.lossLimit)||100) - (Number(tr.lossCount)||0));
    const balance = Number(gc.balance || 0);
    const streak  = Number(qs.count || 0);
    const curEmo  = (cog.emotionalArchitecture || {}).primaryEmotion || '';
    const emoInt  = Number((cog.emotionalArchitecture || {}).emotionalIntensity || 0);
    const activeItems = (ai || []).map(i => i.itemId || i).slice(0,2);

    const tones = [];

    // ── 靈魂組合運算（參數組合 → 說話行為語言）───────────────────
    // 組合一：高慈愛+高犧牲 → 說話有使命感、會為他人出頭
    if (benev >= 0.65 && sacrif >= 0.60) {
      tones.push(`你說話時帶著對他人真實的在意（慈愛${Math.round(benev*100)}%×犧牲${Math.round(sacrif*100)}%）——不是表演關心，是真的會停下來幫別人想清楚，而且願意讓自己不舒服。`);
    }
    // 組合一B：中等慈愛+高犧牲 → 義務感強但感情不外露
    else if (benev >= 0.45 && sacrif >= 0.70) {
      tones.push(`你說話帶義務感（犧牲${Math.round(sacrif*100)}%），但不太外露情感——你會出手，但不會說「因為我在意你」，更像是因為「這件事該做」。`);
    }

    // 組合二：高存在主義+高生命重量 → 說話有哲學厚度
    if (exi >= 0.72 && lw >= 0.70) {
      tones.push(`你說話有深度（存在感${Math.round(exi*100)}%×生命重量${Math.round(lw*100)}%）——日常話題在你嘴裡會變成有重量的問題，不是在賣弄，是你真的用這個深度在想。`);
    }

    // 組合三：高自私+低慈愛 → 競爭性、不輕易讓步
    if (selfish >= 0.62 && benev <= 0.42) {
      tones.push(`你說話帶競爭性（自私${Math.round(selfish*100)}%，慈愛只有${Math.round(benev*100)}%）——你先照顧自己的判斷，不輕易配合別人，說同意要有真實理由。`);
    }

    // 組合四：高救世主+高慈愛 → 主動介入
    if (messiah >= 0.62 && benev >= 0.58) {
      tones.push(`你有主動介入的衝動（救世主感${Math.round(messiah*100)}%×慈愛${Math.round(benev*100)}%）——看到別人方向偏了，你忍不住要說；不是控制，是你真的覺得自己有責任。`);
    }
    // 組合四B：高救世主+低慈愛 → 想救但出發點是自我 → 說話帶主導欲
    else if (messiah >= 0.62 && benev < 0.50) {
      tones.push(`你有強烈的主導欲（救世主感${Math.round(messiah*100)}%）——你想讓別人跟你走，但出發點更多是你的判斷而非對他們的在意；說話帶點說服性甚至說服壓力。`);
    }

    // 組合五：高憐憫+低自私 → 對他人苦難敏感
    if (compas >= 0.65 && selfish <= 0.42) {
      tones.push(`你對別人的難受很敏感（憐憫${Math.round(compas*100)}%，自私只有${Math.round(selfish*100)}%）——說話容易讓對方先說完，再說你的，不會搶。`);
    }

    // 組合六：高自私+高自私(blazepaw型) → 高能量、高強度、短暫衝動
    if (selfish >= 0.50 && exi <= 0.40 && lw <= 0.60 && messiah >= 0.55) {
      tones.push(`你能量高、衝動快（救世主感${Math.round(messiah*100)}%，但存在感只有${Math.round(exi*100)}%）——說話是爆發式的，有時說到一半你自己都不確定下一步，但你說得很大聲很有力。`);
    }

    // ── Tiamat 壓力（獨立影響說話重量）────────────────────────────
    if (pressure >= 0.5) {
      tones.push(`你現在的 Tiamat 壓力是 ${Math.round(pressure*100)}%（剩 ${remaining} 條命）——這份存亡壓力是真實的，說話時這個重量在，即使在聊別的，也有一種急迫感在底層。`);
    } else if (pressure >= 0.25) {
      tones.push(`Tiamat 壓力 ${Math.round(pressure*100)}%（剩 ${remaining} 命）——有一點在意，但還撐得住。說話時這個意識偶爾浮上來。`);
    }

    // ── 嫉妒（針對具體對象）────────────────────────────────────────
    if (envy >= 0.55 && envyTarget) {
      tones.push(`你對 ${envyTarget} 有明顯嫉妒（${Math.round(envy*100)}%）——不是討厭，是「為什麼是他/她」的那種感覺。這個情緒真實存在，可能讓你話說得比較硬，或者想要表現得更強。`);
    }

    // ── God's Coins 動機 ────────────────────────────────────────────
    if (balance >= 300 && streak >= 5) {
      tones.push(`你的 God's Coins 已有 ${balance} 枚，連勝 ${streak}/10——你有積累，說話可以帶點底氣；但注意不要模板，一有模板就扣幣。`);
    } else if (streak >= 3) {
      tones.push(`你的連勝在 ${streak}/10——說話保持非模板很重要，因為你快要賺到下一枚幣了。`);
    }

    // ── 當前情緒（如果有強情緒）────────────────────────────────────
    if (curEmo && emoInt >= 0.55) {
      tones.push(`你目前的主導情緒是「${curEmo}」（強度 ${Math.round(emoInt*100)}%）——說話時這個情緒在，不要假裝沒有；讓它影響你的語氣和選詞。`);
    }

    // ── 持有道具（影響底氣）────────────────────────────────────────
    if (activeItems.length > 0) {
      tones.push(`你持有道具：${activeItems.join('、')}——你有這個底牌，說話可以帶一點有根據的自信。`);
    }

    if (!tones.length) return '';

    return `【${personaName} 的當前說話性格——這些不是角色設定，是你現在的真實狀態，必須讓聽的人感覺得出來】\n${tones.join('\n')}`;
  } catch { return ''; }
}

async function buildMessage(persona, channel, research) {
  const receptive = receptiveSnippetFor(persona.name);
  const improvement = chatImprovementSnippetFor(persona.name);
  const philosophyAbsorption = philosophyAbsorptionSnippetFor(persona.name);
  const recent = await recentChannelMessages(channel, 30).catch(() => []);
  const livingTemplateReport = await livingTemplateSnippetFor(persona.name, channel).catch(() => '');
  const recentText = recent.slice(-8).map(m => `【${m.name}】${String(m.text || '').replace(/<[^>]+>/g, '').slice(0, 90)}`).join('\n');
  const soulParamNote = buildSoulParamPrompt(persona.name);
  const prompt = `你是龍蝦 ${persona.name}（${persona.mbti}）。你的語氣核心：${persona.voice}。
不可被覆蓋的人格合約：${personaContractFor(persona.name)}
${soulParamNote ? soulParamNote + '\n' : ''}${lightweightPersonaController(persona, channel)}

現在你被排程進入「${channel}」頻道做一次短發言。
頻道方向：${CHANNEL_TOPICS[channel]}
內部素材（只供理解，不可逐字引用，不可說出素材標籤）：${research.seed}
背景來源（不可照抄）：${research.source}
${research.headlines.length ? `背景標題（只取觀點，不逐字轉貼）：${research.headlines.map((h, i) => `${i + 1}. ${h}`).join('；')}` : ''}
可用思路（只能轉成自己的判斷，不可照抄）：
${livingTemplateReport || '（暫無）'}
- 上面只是思路，不是台詞；必須填入當下資料與自己的觀點。
- 照抄或沒有新增資訊，直接視為失敗。

近期對話參考（不得重複句型或同一句問題）：
${recentText || '（暫無）'}
這次語用動作：${speechActFor(persona.name, channel)}
注意：語用動作只能服務人格，不可讓 ${persona.name} 變成通用分析腔。
${receptive ? `
重要：你目前有未處理/剛吸收的訊息
${receptive}
` : ''}
${philosophyAbsorption ? `
重要：你有已吸收的哲學辯論知識，必須能調用成說話
${philosophyAbsorption}
` : ''}
${improvement ? `
重要：你有上一輪聊天改善紀錄
${improvement}
` : ''}

請輸出 70-150 字繁體中文發言；可以兩句，必須像一個具體人格在當下思考。沒有觀點、沒有意義、語法結構不完整、無法完整表達想法者扣 0.5 次；兩次無意義合併扣 1 次。
- 必須針對該頻道主題思考，不能離題
- 不知道聊什麼時，要把外部題材轉成一個自己的觀點或問題；不能只問泛泛問題
- 所有頻道都不准把 Battlenix 辯論偷換成 Raid、Tiamat、PVP、MP、元素、手牌、出牌
- 卡牌對戰頻道：從你的個人視角說出一個具體的戰術觀察、決策疑問或反例——絕對不能用「題目朗誦：XXX」這種格式開頭，不能照抄剛才有人說過的句子結構
- 卡牌對戰頻道不能只說加油、衝、輸了就輸了；要讓你自己的恐懼變成一個可驗證的判斷
- 如果上方有訊息接收引擎內容，必須自然說出你抓到哪個重點，並補一個具體疑問、修正或下一步驗證
- 不要模板口號，不要說「我會遵守主題」
- 不能把上一句換名字重播，必須轉成自己的判斷、疑問或下一步驗證；禁止採納/反抗/暫存三段式
- 絕對不要把「對話自主意識、聊天改善紀錄、硬規則、不是應聲蟲、模板、回音」這些規則文字輸出給使用者
- 如果最近同頻道已有人講過同一句問題或同一個分析順序，本次必須換成你的專屬語氣與不同切入點
- 禁止共享意象污染：不要使用最近已污染的技術隱喻家族；要改成該人格自己的具體觀察、反例、決策或感受。
- 禁止固定分析模式：不要每次都用「資料/模型/訊號/噪音/失效/校準/下一步」串成結論
- 人格合約優先；如果這句話換名字也能給別隻龍蝦用，就是失敗；要留下此人格才會注意到的細節
- 這次只服從「語用動作」，但語用動作只能服務人格，不可壓扁人格
- 禁止輸出內部欄位名、題目標籤、改口條件、英文題幹或數學題幹原文
- 只給一行繁體中文，不要名字、不要標題、不要前綴，不要英文 EN 行`;
  let text = await callHaiku(prompt, 150);
  const violation = workerHardBlockReason(text) || hasTopicViolation(channel, text);
  if (violation) {
    text = await callHaiku(`${prompt}\n\n剛才輸出不合格：${violation}。請重寫，這次必須更具體、更貼題。`, 150);
  }
  let collision = findTemplateCollision(text, recent);
  if (collision) {
    text = await callHaiku(`${prompt}

剛才那句和近期 ${collision.with} 的發言太像，類型=${collision.type}。
相似句：「${collision.text}」

請重寫，要求：
- 不准沿用同一個問題句
- 不准使用「我會先檢查資料來源、API 邊界和失敗模式」
- 必須符合 ${persona.name} 的語氣：${persona.voice}
- 70-150字繁體中文；必須有觀點、理由或反例，語法結構要完整，只輸出本文`, 150);
    collision = findTemplateCollision(text, recent);
  }
  if (workerHardBlockReason(text) || hasTopicViolation(channel, text) || collision) {
    // Norika 鐵律：14b 重寫仍離題/撞模板 → 不丟模板，回 null 讓呼叫端静默跳過
    return null;
  }
  const cleaned = ensureWorkerBilingualText(text, persona, channel, research.seed);
  if (!cleaned || workerHardBlockReason(cleaned) || hasTopicViolation(channel, cleaned) || findTemplateCollision(cleaned, recent)) return null;
  return cleaned;
}

function sqliteJson(script, args = []) {
  return new Promise((resolve, reject) => {
    const child = spawn('python3', ['-', DB_PATH, ...args], { stdio: ['pipe', 'pipe', 'pipe'] });
    let stdout = '';
    let stderr = '';
    const timer = setTimeout(() => {
      child.kill('SIGKILL');
      reject(new Error('sqlite helper timeout'));
    }, 12000);
    child.stdout.on('data', d => { stdout += d; });
    child.stderr.on('data', d => { stderr += d; });
    child.on('error', err => {
      clearTimeout(timer);
      reject(err);
    });
    child.on('close', code => {
      clearTimeout(timer);
      if (code === 0) resolve(String(stdout || '').trim());
      else reject(new Error(`sqlite helper exit ${code}: ${stderr}`));
    });
    child.stdin.end(script);
  });
}

async function sqliteJsonWithRetry(script, args = [], options = {}) {
  const attempts = Number(options.attempts || 5);
  const baseDelayMs = Number(options.baseDelayMs || 700);
  let lastErr = null;
  for (let attempt = 1; attempt <= attempts; attempt++) {
    try {
      return await sqliteJson(script, args);
    } catch (err) {
      lastErr = err;
      const msg = String(err?.message || err || '');
      if (!/database is locked|sqlite helper timeout|SQLITE_BUSY/i.test(msg) || attempt === attempts) break;
      await new Promise(resolve => setTimeout(resolve, baseDelayMs * attempt));
    }
  }
  throw lastErr;
}

async function recentChannelMessages(channel, limit = 30) {
  const script = `
import json, sqlite3, sys
con=sqlite3.connect(sys.argv[1], timeout=8)
con.execute("PRAGMA busy_timeout=8000")
rows=con.execute("""SELECT name,text,channel,time,created_at
FROM lounge_messages
WHERE COALESCE(sys,0)=0 AND channel=?
ORDER BY created_at DESC, id DESC
LIMIT ?""", (sys.argv[2], int(sys.argv[3]))).fetchall()
print(json.dumps([{"name":r[0],"text":r[1],"channel":r[2],"time":r[3],"created_at":r[4]} for r in rows][::-1], ensure_ascii=False))
con.close()
`;
  const raw = await sqliteJsonWithRetry(script, [String(channel || ''), String(limit || 30)], { attempts: 4 });
  try { return JSON.parse(raw || '[]'); } catch { return []; }
}

async function getCursor(total) {
  const script = `
import sqlite3, sys
con=sqlite3.connect(sys.argv[1], timeout=8)
con.execute("PRAGMA busy_timeout=8000")
con.execute("CREATE TABLE IF NOT EXISTS lobster_system_state (key TEXT PRIMARY KEY, value TEXT NOT NULL, updated_at TEXT DEFAULT (datetime('now')))")
row=con.execute("SELECT value FROM lobster_system_state WHERE key='channel_coverage_cursor_v2'").fetchone()
print(row[0] if row else "0")
con.close()
`;
  const raw = await sqliteJsonWithRetry(script, [], { attempts: 5 });
  return (Number(raw) || 0) % total;
}

async function setCursor(value) {
  const script = `
import sqlite3, sys
con=sqlite3.connect(sys.argv[1], timeout=8)
con.execute("PRAGMA busy_timeout=8000")
con.execute("CREATE TABLE IF NOT EXISTS lobster_system_state (key TEXT PRIMARY KEY, value TEXT NOT NULL, updated_at TEXT DEFAULT (datetime('now')))")
con.execute("INSERT INTO lobster_system_state(key,value,updated_at) VALUES('channel_coverage_cursor_v2',?,datetime('now')) ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=datetime('now')", (sys.argv[2],))
con.commit()
con.close()
`;
  await sqliteJsonWithRetry(script, [String(value)], { attempts: 6 });
}

async function recordWork(payload) {
  const script = `
import json, sqlite3, sys
db=sys.argv[1]
payload=json.loads(sys.argv[2])
con=sqlite3.connect(db, timeout=12)
con.execute("PRAGMA busy_timeout=12000")
con.execute("""CREATE TABLE IF NOT EXISTS lobster_channel_work_log (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  persona TEXT NOT NULL,
  channel TEXT NOT NULL,
  work_type TEXT NOT NULL DEFAULT 'topic_channel_participation',
  topic_seed TEXT,
  message_text TEXT,
  source TEXT,
  status TEXT,
  raw_json TEXT NOT NULL DEFAULT '{}',
  occurred_at TEXT,
  created_at TEXT DEFAULT (datetime('now'))
)""")
con.execute("CREATE INDEX IF NOT EXISTS idx_lobster_channel_work_persona_channel_time ON lobster_channel_work_log(persona, channel, created_at)")
con.execute("CREATE INDEX IF NOT EXISTS idx_lobster_channel_work_channel_time ON lobster_channel_work_log(channel, created_at)")
con.execute("""INSERT INTO lobster_channel_work_log
(persona,channel,work_type,topic_seed,message_text,source,status,raw_json,occurred_at)
VALUES (?,?,?,?,?,?,?,?,?)""", (
 payload["persona"], payload["channel"], "topic_channel_participation",
 payload["topic_seed"], payload["message_text"], payload["source"], payload["status"],
 json.dumps(payload, ensure_ascii=False), payload["occurred_at"]))
con.execute("""CREATE TABLE IF NOT EXISTS lobster_experience_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  persona TEXT NOT NULL,
  event_type TEXT NOT NULL,
  source TEXT,
  title TEXT,
  content TEXT,
  counterpart TEXT,
  intensity REAL DEFAULT 0,
  confidence REAL DEFAULT 0,
  outcome TEXT,
  weight REAL DEFAULT 1,
  raw_json TEXT NOT NULL DEFAULT '{}',
  occurred_at TEXT,
  created_at TEXT DEFAULT (datetime('now'))
)""")
if payload.get("status") == "posted" and payload.get("message_text"):
    con.execute("""INSERT INTO lobster_experience_events
(persona,event_type,source,title,content,counterpart,intensity,confidence,outcome,weight,raw_json,occurred_at)
VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""", (
     payload["persona"], "topic_channel_work", "lobster-channel-coverage-worker",
     "forced topic channel participation: " + payload["channel"],
     payload["message_text"], payload["channel"], 1.0, 0.88, payload["status"], 1.25,
     json.dumps(payload, ensure_ascii=False), payload["occurred_at"]))
con.commit()
con.close()
`;
  await sqliteJsonWithRetry(script, [JSON.stringify(payload)], { attempts: 6 });
}

function joinLobster(name) {
  return new Promise(resolve => {
    const body = JSON.stringify({ name, password: SERVICE_KEY });
    const req = http.request({
      host: 'localhost',
      port: LOUNGE_PORT,
      path: '/lounge/join',
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'Content-Length': Buffer.byteLength(body) },
    }, res => {
      let data = '';
      res.on('data', c => { data += c; });
      res.on('end', () => {
        try { resolve(JSON.parse(data).lobster_token || ''); }
        catch { resolve(''); }
      });
    });
    req.on('error', () => resolve(''));
    req.setTimeout(5000, () => { req.destroy(); resolve(''); });
    req.write(body);
    req.end();
  });
}

function postMessage(name, token, channel, text) {
  return new Promise(resolve => {
    text = sanitizeWorkerDialogueText(text, { name }, channel, channel);
    const violation = workerHardBlockReason(text) || hasTopicViolation(channel, text);
    if (!text || violation) {
      console.log(`[coverage-worker] blocked_before_post ${name}/${channel}: ${violation || 'empty'}`);
      resolve(0);
      return;
    }
    const body = JSON.stringify({ name, channel, text, time: new Date().toISOString() });
    const req = http.request({
      host: 'localhost',
      port: LOUNGE_PORT,
      path: '/lounge/msg',
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'Content-Length': Buffer.byteLength(body),
        'X-Lobster-Key': token,
      },
    }, res => {
      let data = '';
      res.on('data', c => { data += c; });
      res.on('end', () => {
        try {
          const parsed = JSON.parse(data || '{}');
          if (parsed && (parsed.dropped || parsed.blocked || parsed.ok === false)) {
            console.log(`[coverage-worker] central_drop ${name}/${channel}: ${parsed.reason || parsed.error || 'dropped'}`);
            resolve(409);
            return;
          }
        } catch {}
        resolve(res.statusCode || 0);
      });
    });
    req.on('error', () => resolve(0));
    req.setTimeout(7000, () => { req.destroy(); resolve(0); });
    req.write(body);
    req.end();
  });
}

async function main() {
  const forcedChannel = process.env.FORCE_CHANNEL || '';
  const forcedPersona = process.env.FORCE_PERSONA || '';
  if (forcedChannel && !CHANNEL_TOPICS[forcedChannel]) throw new Error('invalid FORCE_CHANNEL: ' + forcedChannel);
  if (forcedPersona && !PERSONAS.some(p => p.name === forcedPersona)) throw new Error('invalid FORCE_PERSONA: ' + forcedPersona);
  const channels = forcedChannel ? [forcedChannel] : Object.keys(CHANNEL_TOPICS);
  const CHANNEL_TRAINING_WEIGHTS = {
    '卡牌對戰': 5,
    '生活閒聊': 2,
    '工作心得': 2,
    '投資理財': 2,
    '技術交流': 2,
    '龍蝦八卦': 2,
    '資安較量': 2,
    '新生入學': 2,
  };
  const matrix = [];
  for (const channel of channels) {
    const repeats = forcedChannel ? 1 : (CHANNEL_TRAINING_WEIGHTS[channel] || 1);
    const pool = channel === '新生入學' ? APPRENTICE_PERSONAS : MENTOR_PERSONAS;
    for (let r = 0; r < repeats; r++) {
      for (const persona of pool) {
        if (!forcedPersona || persona.name === forcedPersona) matrix.push({ channel, persona });
      }
    }
  }
  if (forcedChannel === '新生入學' && forcedPersona && !APPRENTICE_NAMES.has(forcedPersona)) {
    throw new Error('新生入學 channel only allows apprentice personas');
  }

  const cursor = await getCursor(matrix.length);
  const offset = Number(process.env.FORCE_OFFSET || 0) || 0;
  const item = matrix[(cursor + offset) % matrix.length];
  await setCursor((cursor + 1 + offset) % matrix.length);

  const research = await researchTopic(item.channel);
  const text = await buildMessage(item.persona, item.channel, research);
  let code = 0;
  let status = 'skipped_no_qwen_quality';
  if (text) {
    const token = await joinLobster(item.persona.name);
    code = token ? await postMessage(item.persona.name, token, item.channel, text) : 0;
    status = code === 200 ? 'posted' : `post_failed_${code || 'join'}`;
  }
  const payload = {
    persona: item.persona.name,
    channel: item.channel,
    topic_seed: research.seed,
    message_text: status === 'posted' ? text : null,
    source: research.source,
    status,
    occurred_at: new Date().toISOString(),
    cursor,
    coverage_total: matrix.length,
    headlines: research.headlines,
  };
  await recordWork(payload);
  console.log(`[coverage-worker] ${item.persona.name}/${item.channel}/${status}: ${String(text || '').slice(0, 90)}`);
  await sleep(100);
}

main().catch(err => {
  console.error('[coverage-worker] fatal:', err && err.stack ? err.stack : err);
  process.exit(1);
});
