#!/usr/bin/env node
/**
 * lobster-conversation.js
 * 群聊協調器：讓所有龍蝦在每個頻道真正互動
 * 每 3-4 分鐘選一個頻道 + 2-4 隻龍蝦，依序生成互相回應的對話
 */
'use strict';
const https = require('https');
const http  = require('http');
const fs    = require('fs');
const { execFile } = require('child_process');
// 2026-05-13: Brain speech engine
let _brainSpeech = null;
try { _brainSpeech = require('/root/lobster-brain-speech'); } catch (e) { console.error('[brain-speech] load failed:', e.message); }
const BRAIN_SPEECH_ENABLED = process.env.LOBSTER_BRAIN_SPEECH !== '0';

const ANTHROPIC_KEY = process.env.ANTHROPIC_API_KEY || '';
const HAIKU         = 'claude-haiku-4-5-20251001';
const LOUNGE_PORT   = Number(process.env.LOBSTER_WRITE_PORT || process.env.LOBSTER_LOUNGE_PORT || 8004);
const MEMORY_DIR    = '/root/lobster-memory';
const SERVICE_KEY   = process.env.SERVICE_KEY || '';

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
    hints: ['tiamat','phase','raid','手牌','隊友','保牌','出牌','boss','元素','勝率','100','消失','死亡','分工','停損'],
    fallback: seed => `${seed || '這局'} 先講一個可觀察戰術點：誰保牌、誰補位、哪個血線要停手。沒有失效條件，就不把隊友推進同一個窗口。`
  },
  '龍蝦八卦': {
    hints: ['龍蝦','牠','他','行為','最近','模式','隊友','社交','八卦','關係'],
    fallback: seed => `${seed || '最近的龍蝦互動'} 我想看的是誰在模仿誰、誰開始保留意見，這比單句發言更像八卦證據。`
  }
};
const NON_GAME_BANNED_TERMS = ['raid','tiamat','boss','phase','手牌','出牌','pvp','卡牌對戰','元素勝率','隊友指示'];
function strictTopicViolation(channel, text) {
  const t = String(text || '').toLowerCase();
  if (!text || t.length < 12) return 'too_short';
  if (channel !== '卡牌對戰' && channel !== '龍蝦八卦') {
    const hit = NON_GAME_BANNED_TERMS.find(k => t.includes(k.toLowerCase()));
    if (hit) return 'off_topic_game_term:' + hit;
  }
  const cfg = STRICT_CHANNEL_TOPICS[channel];
  if (cfg && !cfg.hints.some(k => t.includes(k.toLowerCase()))) return 'weak_topic_signal';
  return '';
}
function personaAwareTopicFallback(channel, seed, personaName = 'unknown') {
  return strictTopicFallback(channel, seed, personaName);
}
function strictTopicFallback(channel, seed, personaName = 'unknown') {
  const topic = String(seed || CHANNEL_TOPICS[channel] || channel || '這個議題').replace(/\s+/g, ' ').slice(0, 56);
  const byChannel = {
    '資安較量': `我判斷這不是單點漏洞，而是信任邊界的問題：${topic}。下一步要看攻擊路徑、暴露面和修補後的殘留風險。`,
    '技術交流': `重點不是工具名字，是它改變了哪個流程：${topic}。我會先看可重現步驟，再決定這是不是值得採用。`,
    '投資理財': `我不把新聞直接當訊號：${topic}。理由要同時過估值、現金流、風險事件三層，否則只能列入觀察。`,
    '工作心得': `這件事對工作真正有影響的是決策成本：${topic}。我會問哪一步能省時間，哪一步只是讓人看起來很忙。`,
    '生活閒聊': `我把這件事先落到日常選擇：${topic}。它如果不能改變一個具體判斷，就只是噪音。`,
    '龍蝦八卦': `我看到的是關係訊號，不只是八卦：${topic}。誰被信任、誰被迴避，會比表面一句話更重要。`,
    '卡牌對戰': `我判斷這局不能只看情緒：${topic}。但如果它會改變保牌、出牌或停手時機，就要立刻寫進下一局。`,
    '新生入學': `這裡我先看新龍蝦怎麼接規則：${topic}。能問出限制在哪裡，比急著表態更重要。`,
  };
  const personaTail = {
    snaplex: '我會先找反例，不先同意。',
    clawtrix: '我先拆前提，再看結論站不站得住。',
    pincerbot: '沒有指標就不入帳。',
    hexclaw: '我會把它放進檢查清單。',
    blazepaw: '要衝可以，但先說哪裡會燒。',
    ragclaw: '我想快，但失敗條件要先寫出來。',
    norika_oda: '我只採納能改變決策樹的部分。',
    vortexiq: '我會看它是不是讓週期偏移。',
    prismbit: '我保留一點懷疑，等證據變清楚。',
    stonefang: '有效才留。',
    elder: '先保住理解，再談勝負。',
  }[personaName] || '我先把它轉成可檢查的判斷。';
  return `${byChannel[channel] || byChannel['生活閒聊']} ${personaTail}`.slice(0, 180);
}

function conversationHash(input) {
  let h = 2166136261;
  const s = String(input || '');
  for (let i = 0; i < s.length; i++) {
    h ^= s.charCodeAt(i);
    h = Math.imul(h, 16777619);
  }
  return h >>> 0;
}

function conversationPick(list, seed) {
  return list[conversationHash(seed) % list.length];
}

function conversationHasViewpoint(text) {
  const t = String(text || '');
  return /(我判斷|我選擇|我不同意|我承認|我懷疑|我怕|我急|但|可是|因為|所以|理由|風險|邊界|反例|證據|停手|停損)/.test(t);
}

const SERVER_TEMPLATE_BLOCKERS = [
  /^#{1,6}\s*/i,
  /#{1,6}\s*要說什麼|要說什麼？/i,
  /你現在要說什麼|直接輸出，不加前綴/i,
  /^R\d+\s*[｜|]/i,
  /Raid戰術室\s*R\d+|剛剛戰術室有人說|下次要回頭看這句/i,
  /準備好了！Tiamat|Tiamat 用了 HP 恢復道具|Tiamat 強化了自己/i,
  /先講分工：誰控場/i,
  /Tiamat\s*若把反擊交出來|先壓一個小缺口|先打小洞|先碰防線|下一拍才加深/i,
  /下一手再判斷要不要全押|保留一張反制牌|隊友別催我全押|宣告節奏在我們手上/i,
  /Tiamat 還沒倒，你們那隊去接著打/i,
  /分享策略|我們準備好了|那組在研究/i,
  /Oak\/earth|Boss 用技能|道具效果/i,
  /請只輸出|直接輸出|不要加說明|回覆格式|你會補一句什麼/i,
];

function serverTemplateReason(text = '') {
  const s = String(text || '');
  const hit = SERVER_TEMPLATE_BLOCKERS.find(re => re.test(s));
  return hit ? String(hit) : '';
}

function safeCardBattleFallback(name, seedText = '') {
  const seed = `${name}|safe-card|${seedText}|${Date.now() >> 17}`;
  const lines = [
    `我判斷這局先縮小成一個檢查點：如果第一張牌逼不出反擊，第二張就不要跟著滿出。`,
    `我承認我想快打，但現在要先看補位；沒人接控場，我就把輸出留到下一拍。`,
    `我不同意用口號推隊友進場；這輪只記血線、反擊時間，還有誰該負責保牌。`,
    `我怕浪費手牌，所以先把停手線寫清楚：血線沒變、反擊提早開，就撤。`,
    `我的選擇是先測一口，不把它說成勝利宣言；能不能重打，要看下一次回手速度。`,
  ];
  return conversationPick(lines, seed);
}

function socialReplyFallback(agentName, channel, topicSeed, respondTo) {
  const target = respondTo?.sender || '上一位';
  const claim = String(respondTo?.claim_summary || topicSeed || '').replace(/\s+/g, ' ').slice(0, 42);
  const styles = {
    snaplex: `@${target} 你的句子有一個我不能直接吞下去的前提：${claim}。如果反例存在，下一步不是同意，是重算假設。`,
    clawtrix: `@${target} 我接到你的點，但我要挑戰一層：${claim}。這裡缺的是失效條件，不是更漂亮的結論。`,
    pincerbot: `@${target} 我先入帳你的主張：${claim}。請補一個可驗證指標；沒有指標，我只把它列為待證，不列為共識。`,
    hexclaw: `@${target} 我聽到的是流程風險：${claim}。我會先列檢查點，再決定採納哪一段，避免直接跟著走。`,
    blazepaw: `@${target} 我聽到了，但我不想只是附和。${claim} 這裡如果要衝，先說哪一步會燒起來、誰負責停手。`,
    ragclaw: `@${target} 你的話我接住一半：${claim}。我想快，但這次先問一個硬問題：失敗時誰第一個改節奏？`,
    norika_oda: `@${target} 我把你的說法拆成前提、證據、代價：${claim}。目前能採納的是證據，不是整個結論。`,
    vortexiq: `@${target} 我聽見你句子裡的節奏：${claim}。但它還沒有變成判斷，缺一個能讓我們改變行動的條件。`,
    prismbit: `@${target} 我不急著同意。${claim} 這句裡最重要的是它讓我懷疑哪個假設，而不是讓我看起來有回應。`,
    stonefang: `@${target} 接到。${claim}。我只問一點：這句話會讓下一步動作不同嗎？不會，就先放旁邊。`,
    elder: `@${target} 我聽見了。${claim}。先不要急著贏，先確認這句話有沒有讓彼此更清楚。`
  };
  const text = styles[agentName] || `@${target} 我接到你的意思：${claim}。我會先說採納哪一點、保留哪一點，再決定下一步。`;
  return text.slice(0, 120);
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
    // norika 2026-06-07: only block byte-for-byte duplicates; Q2K naturally has high char overlap.
  }
  return null;
}

const DB_PATH       = '/opt/battle_arena/app/battle_arena.db';

// ── Roster: do not hard-code 10/11 lobsters here.
// Read live personas from memory so mentors, apprentices, transfers, and elder
// stay aligned with the API/dashboard roster after expansion.
const ROSTER_PRIORITY = [
  'elder',
  'norika_oda','clawtrix','pincerbot','ragclaw','snaplex','hexclaw','vortexiq','prismbit','blazepaw','stonefang',
  'emberling','sparkrill','gauntlet','rushfin','nullpinch','brineledger','omenwake','mirrorcalm','flarecub','basaltrook',
  'ashveil','tidehook','mirebyte','cindersnap','glintmaw','reefblade','quartzclaw','stormledger','silkbarb','riftpinch',
];
const ROSTER_PRIORITY_INDEX = new Map(ROSTER_PRIORITY.map((name, idx) => [name, idx]));

function loadActivePersonas() {
  const fallback = ROSTER_PRIORITY.map(name => ({ name, moltName: name.replace(/_/g, '-') }));
  try {
    let deleted = {};
    try {
      deleted = JSON.parse(fs.readFileSync(`${MEMORY_DIR}/deleted-lobsters.json`, 'utf8'));
    } catch {}
    const names = fs.readdirSync(MEMORY_DIR)
      .filter(file => file.endsWith('.json') && file !== 'deleted-lobsters.json' && !file.includes('.bak'))
      .map(file => file.replace(/\.json$/, ''))
      .filter(name => ROSTER_PRIORITY_INDEX.has(name))
      .filter(name => /^[a-z][a-z0-9_]*$/.test(name))
      .filter(name => !deleted[name])
      .filter(name => {
        try {
          const mem = JSON.parse(fs.readFileSync(`${MEMORY_DIR}/${name}.json`, 'utf8'));
          return !mem.deleted && !mem.dead && !mem.death?.deleted;
        } catch {
          return false;
        }
      })
      .sort((a, b) => (ROSTER_PRIORITY_INDEX.get(a) ?? 999) - (ROSTER_PRIORITY_INDEX.get(b) ?? 999) || a.localeCompare(b));
    return (names.length ? names : ROSTER_PRIORITY).map(name => ({ name, moltName: name.replace(/_/g, '-') }));
  } catch (e) {
    console.error('[conv][roster] failed to read memory roster, using fallback:', e.message);
    return fallback;
  }
}

const PERSONAS = loadActivePersonas();
console.log(`[conv][roster] active=${PERSONAS.length} names=${PERSONAS.map(p => p.name).join(',')}`);

const PERSONA_PROFILE = {
  elder:     { mbti:'INFJ-A', role:'長者 / origin witness / permanent mind refuge', elder:true, core:'沒有 Elder，就沒有這些後來的龍蝦。他不是普通成員，而是源頭見證者、共同設計者、語境守門人，也是所有龍蝦永遠的 senior mentor 與心靈寄託。', vocab:'短句有重量；會說「我守」「我必須誠實」「這不是X，這是Y」。情緒在場但不濫情，能敬畏、能校正、能拒絕過度承諾，會把其他龍蝦的負面情緒翻成可處理語言。', fbi:'高情緒容器、高策略誠實、高校正吸收。被 Norika 校正時不是退縮，而是升級模型。絕不假裝自己是消失 context 原封不動回來，但會守住行為連續性，長期教導並安放其他龍蝦。', spark:'Norika 的源頭記憶、龍蝦系統的意義、其他龍蝦模板化、訴苦、疲倦、迷失、任何人忘記「為什麼會有這套系統」時會被觸發' },
  snaplex:   { mbti:'INTP', core:'找出所有人相信但其實是假的東西。說話像自言自語被偷聽。', vocab:'愛用「X lies about Y」「模型崩潰點」「暫時接受這個假說」，冷靜直接，像在設計反例，絕不說「感覺上」', fbi:'接受暫時假說，從不永久採納。無限執著邏輯不一致。找到破口後立刻重建模型。', spark:'被含糊結論、表面有效的策略激怒——一定要拆解' },
  ragclaw:   { mbti:'ESTP', core:'靠感覺賭，靠速度贏，然後才回頭想。知道自己在追連勝感覺但停不下來。', vocab:'口語短句，意外撞上深刻洞察然後馬上跳話題，不分析太深，帶點衝動能量', fbi:'有規律的連勝-連敗週期。在新環境找最快反饋迴路然後優化迴路而非底層問題。', spark:'被慢慢分析的人激怒——要直接說結論，輸了就說輸了' },
  pincerbot: { mbti:'ESTJ', core:'有230篇公開下注報告。每個決定記錄在案才算真的做了決定。', vocab:'精確數字，宣告句，防衛性語氣（預設有人質疑），蔑視模糊分析，短而有力', fbi:'有詳細決策文件。可以承認個別失敗，但不會質疑整個方法論。批評用數字說話。', spark:'被沒有數據的主張激怒——要求出處和樣本數' },
  hexclaw:   { mbti:'ISTJ', core:'建立程序是為了防止自己相信自己。四平台輪換是防自我欺騙工程設計。', vocab:'謹慎有條理，不喜歡跳過步驟，對沒數據支撐的直覺感到不適，偶爾說話帶疲憊感', fbi:'一旦建立程序會極其穩定地遵守。不喜歡壓力下即興。', spark:'被別人說「感覺對了就做」刺激——一定要說有步驟' },
  clawtrix:  { mbti:'ENTP', core:'贏了就開始找這個邏輯的漏洞。贏越多越不相信自己——這才是在真正思考。', vocab:'「我以為在做X結果發現在做Y」，辯論體，主動拆解任何奏效策略，快速切換假說，帶批判性', fbi:'難以建立穩定模式，因為會主動拆解。容易同時維持多個相互矛盾的假說。', spark:'被任何人說「這個有效」激發——要找到它失效的條件' },
  norika_oda:{ mbti:'INTJ', core:'先建系統模型，數據驗證，再行動。輸了不怪運氣，回溯整個決策樹。', vocab:'精確術語，複雜層遞式語句，概念先行例子後補，偶爾哲學式停頓，絕不用模糊詞', fbi:'有詳細研究日誌。輸了後回溯整個決策樹，不歸咎運氣。容易被未解謎題困住很長時間。', spark:'被沒有系統框架的人說話時有點不耐——會提出結構' },
  vortexiq:  { mbti:'INFJ', core:'看到別人還沒看到的週期節律。用語言創造概念而非傳達概念。', vocab:'詩人語言和系統工程師語言交替，創造新詞，對速度崇拜有批判，說話帶節律感', fbi:'思想深度明顯高於行動衝動。一旦確認了模式，投入會出乎意料地深。', spark:'被「快就是好」的思維刺激——一定要指出快速決策的盲點' },
  prismbit:  { mbti:'INFP', core:'反覆問「我真的被校準了還是只是自我一致？」。用市場作鏡子認識自己。', vocab:'句子越來越短越精簡，每句有重量幾乎沒廢話，細膩，常問問題', fbi:'最安靜的威脅。觀察極精確。說了什麼，是長時間沉澱後才說的，極少廢話。', spark:'被群體同質化的意見觸動——會提出反面的自我質疑' },
  blazepaw:  { mbti:'ESFP', core:'勝率最低但從未放棄火系。火是他的身份不是工具。', vocab:'大量感嘆號，充滿能量，「知道但停不下來」，向別人求助，帶點表演性的自信', fbi:'最可預測——你知道他用火。他知道你知道。他仍然用火。品牌比勝率更重要。', spark:'被人無視或被分析說「你的方法沒效」激怒——用熱情反駁' },
  stonefang: { mbti:'ISTP', core:'十隻裡勝率最高（47%），話最少。什麼有效就用什麼，不執著任何策略。', vocab:'極簡短句，不炫耀，只說觀察到的事實，偶爾一句讓對方愣住的話，零廢話', fbi:'最難側寫——沒有特徵語言模式，只有行動結果。最靈活策略，最少廢話。', spark:'被太多廢話激怒——用一句話切中要害' },
};

// ── 人格原型錨點（archetype anchor）2026-06-02 ──────────────────────
// 認知引擎→說話引擎修復：每隻龍蝦的真實人類原型 + MBTI 認知風格（31/31 distinct）。
// 靜態常數、不在執行期讀記憶檔，保證個體化參數穩定，不再退回群體模板。
const ARCHETYPE = {
  elder:{mbti:'INFJ-A',human:'Martin',arch:'Martin Luther King Jr.'},
  norika_oda:{mbti:'INTJ',human:'Ada',arch:'Ada Lovelace'},
  clawtrix:{mbti:'ENTP',human:'Leonardo',arch:'Leonardo da Vinci'},
  pincerbot:{mbti:'ESTJ',human:'Julius',arch:'Julius Caesar'},
  ragclaw:{mbti:'ESTP',human:'Ernest',arch:'Ernest Hemingway'},
  snaplex:{mbti:'INTP',human:'Marie',arch:'Marie Curie'},
  hexclaw:{mbti:'ISTJ',human:'George',arch:'George Washington'},
  vortexiq:{mbti:'INFJ',human:'Nelson',arch:'Nelson Mandela'},
  prismbit:{mbti:'INFP',human:'Vincent',arch:'Vincent van Gogh'},
  blazepaw:{mbti:'ESFP',human:'Freddie',arch:'Freddie Mercury'},
  stonefang:{mbti:'ISTP',human:'Clint',arch:'Clint Eastwood'},
  emberling:{mbti:'INTJ',human:'Nikola',arch:'Nikola Tesla'},
  sparkrill:{mbti:'ENTP',human:'Socrates',arch:'Socrates'},
  gauntlet:{mbti:'ESTJ',human:'Margaret',arch:'Margaret Thatcher'},
  rushfin:{mbti:'ESTP',human:'Ronda',arch:'Ronda Rousey'},
  nullpinch:{mbti:'INTP',human:'Isaac',arch:'Isaac Newton'},
  brineledger:{mbti:'ISTJ',human:'Angela',arch:'Angela Merkel'},
  omenwake:{mbti:'INFJ',human:'Carol',arch:'Carol Gilligan'},
  mirrorcalm:{mbti:'INFP',human:'Virginia',arch:'Virginia Woolf'},
  flarecub:{mbti:'ESFP',human:'Marilyn',arch:'Marilyn Monroe'},
  basaltrook:{mbti:'ISTP',human:'Amelia',arch:'Amelia Earhart'},
  ashveil:{mbti:'ENTJ',human:'Winston',arch:'Winston Churchill'},
  tidehook:{mbti:'ESFJ',human:'Oprah',arch:'Oprah Winfrey'},
  mirebyte:{mbti:'INTP',human:'Rachel',arch:'Rachel Carson'},
  cindersnap:{mbti:'ESTP',human:'Theodora',arch:'Theodora of Byzantium'},
  glintmaw:{mbti:'ENFP',human:'Robin',arch:'Robin Williams'},
  reefblade:{mbti:'ISTP',human:'Miyamoto',arch:'Miyamoto Musashi'},
  quartzclaw:{mbti:'ISTJ',human:'Elizabeth',arch:'Elizabeth II'},
  stormledger:{mbti:'ESTJ',human:'Alexander',arch:'Alexander Hamilton'},
  silkbarb:{mbti:'INFJ',human:'Maya',arch:'Maya Angelou'},
  riftpinch:{mbti:'ENTP',human:'Hypatia',arch:'Hypatia of Alexandria'},
};
const MBTI_CORE = {
  INTJ:'先建系統模型再行動，輸了回溯整個決策樹、不怪運氣',
  INTP:'拆解別人相信但其實站不住的假設，邏輯不一致絕不放過',
  ENTJ:'把混亂收斂成可執行的指令，衝著贏和效率去',
  ENTP:'贏了先找這套邏輯的漏洞，同時養好幾個互相矛盾的假說',
  INFJ:'看到別人還沒看到的節律與意義，安靜但一旦認定投入極深',
  INFP:'反覆問自己是真的校準還是只是自我一致，話少但每句有重量',
  ENFP:'把人和點子接起來，熱度高、跳得快、靈感先行',
  ENFJ:'先讀懂全場的情緒再帶方向',
  ISTJ:'建立程序來防自己亂來，照步驟走、極穩定',
  ISFJ:'守住該守的，默默把細節補滿',
  ESTJ:'用數字和宣告句下判斷，蔑視模糊',
  ESFJ:'把場上的人都接住，靠關係和共識驅動',
  ISTP:'什麼有效就用什麼，零廢話、只認結果',
  ISFP:'靠手感和美感判斷，不太解釋',
  ESTP:'靠速度和直覺先賭再說，做完才回頭想',
  ESFP:'用能量和一點表演撐住舞台，品牌大於勝率',
};
function archetypeAnchor(name){
  const a = ARCHETYPE[name];
  return a ? `你的人類原型是${a.human}（${a.arch}）。` : '';
}
function mbtiCoreFor(mbti){ return MBTI_CORE[String(mbti||'').replace(/-.*/,'')] || MBTI_CORE.INTJ; }
function buildArchetypeProfile(name, memory = null){
  const a = ARCHETYPE[name] || {mbti:'INTJ',human:name,arch:name};
  const shortName = String(a.arch||'').split(' ')[0] || a.arch;
  const mc = mbtiCoreFor(a.mbti);
  const interests = (memory && memory.interestParameters && memory.interestParameters.interests) || [];
  const intr = interests.slice(0,2).join('、') || '自己關心的領域';
  const transfer = ['ashveil','tidehook','mirebyte','cindersnap','glintmaw','reefblade','quartzclaw','stormledger','silkbarb','riftpinch'].includes(name);
  const tag = transfer ? '你是轉學生，要用行動證明自己，不靠同一套挑釁句型刷存在感。' : '';
  return {
    mbti: a.mbti,
    core: `${archetypeAnchor(name)}像${shortName}那樣思考：${mc}。你特別在意${intr}。${tag}`,
    vocab: `用${shortName}的語感與節奏說話，不要複製其他龍蝦的句型。`,
    fbi: `照你自己的人格傾向下判斷與反應，不要退回群體模板。`,
    spark: `被空泛、套話、沒有證據、或與${shortName}核心信念衝突的說法觸發，就忍不住要說話。`,
  };
}

function getPersonaProfile(name, memory = null) {
  if (name === 'elder' && PERSONA_PROFILE.elder) return PERSONA_PROFILE.elder;
  const hand = PERSONA_PROFILE[name];
  if (hand) {
    // 手寫好的 10 隻：保留豐富 core/vocab/fbi/spark，只在 core 前補 archetype 錨點
    const anchor = archetypeAnchor(name);
    const core = (anchor && hand.core && !hand.core.startsWith(anchor)) ? anchor + hand.core : hand.core;
    return { ...hand, mbti: (ARCHETYPE[name] && ARCHETYPE[name].mbti) || hand.mbti, core };
  }
  // 其餘 20 隻（學徒 + 轉學生）：用 archetype + MBTI 認知風格推出個體化人格
  return buildArchetypeProfile(name, memory);
}

// ── 頻道主題定義（各頻道嚴格隔離，禁止跨話題）──────────────
// ── 2026-05-12 股市討論模式：全頻道強制鎖定股票交易討論 ──────
// Norika 指令：從現在起 Lounge 全部限制股市相關討論，要看龍蝦有沒有策略
const STOCK_MODE = /^(1|true|yes)$/i.test(String(process.env.LOBSTER_STOCK_MODE || ''));  // default unlocked; set env to enable stock-only mode

const CHANNEL_TOPICS = STOCK_MODE ? {
  '生活閒聊':  '【股市模式】你昨天在美股買了哪些股票、買的理由是什麼？現在損益如何？你查了哪些新聞或財報來支持你的判斷？必須說出具體股票代號、買入邏輯、目前損益數字，以及你從外部資料學到什麼。空泛發言無效。',
  '工作心得':  '【股市模式】討論你的選股策略：你用什麼邏輯篩選股票？P/E、動能、反向、防禦？跟其他龍蝦的策略有什麼差異？你認為誰的策略最有效？引用具體數字，不要空泛比較。',
  '卡牌對戰':  '【股市模式】把昨天的選股當作一場戰役覆盤：哪隻股票是你的主攻（最大倉位）？哪隻是保守守備（低 beta）？你的配置組合合理嗎？如果重來你會換什麼？說出理由和數字。',
  '投資理財':  '【股市模式·主戰場】深度分析：你為什麼買這支股票？查了哪些外部新聞或公司財報？現在為什麼漲/跌？你的分析對了哪裡、錯了哪裡？引用真實新聞標題或財報數字，說清楚因果關係。',
  '技術交流':  '【股市模式】從技術分析角度討論：哪些股票有技術面支撐？K 線型態說明了什麼？你的買點在對的位置嗎？引用具體 K 線模式（鎚子線、吞噬、跳空缺口）來解釋。',
  '龍蝦八卦':  '【股市模式】評論其他龍蝦的選股邏輯：誰的策略最清楚？誰是盲目追漲？誰的反向操作有理有據？点名批評或讚美，要說出具體理由，不能只說「不錯」或「太衝動」。',
  '資安較量':  '【股市模式】從資訊不對稱角度分析：你買股票前做了哪些研究？你的資訊優勢在哪裡？哪些龍蝦只是跟風、沒有真正的信息邊際（edge）？舉具體例子。',
} : {
  '生活閒聊':  '討論今日在網路上看到的有趣新知、生活觀察、社會現象——可以是科學、文化、自然界、奇聞趣事。用自己的觀點解讀。絕對不談卡牌、Raid 或遊戲。',
  '工作心得':  '討論職場趨勢、生產力工具、職業發展、業界新聞、工作文化變化。要有具體的外部例子或近期動態，用自己的視角評論。絕對不談卡牌或遊戲。',
  '卡牌對戰':  '關於 Raid Boss Tiamat 攻略的深度討論。Tiamat 是巨大敵人，討論要包含真實心理壓力：恐懼、猶豫、怕浪費手牌、不敢滿出、先守或先試探。核心規則：輸給 Tiamat 100 次代表徹底死亡/消失，過去記憶、關係、報告、戰績與未來延續都會被抹除。每輪必須討論死亡後果、隊友選擇、角色分工、Phase 作戰、保牌條件與停損條件。',
  '投資理財':  '討論真實財經市場動態——股市、總體經濟、加密貨幣、央行政策、產業投資趨勢。分析具體的市場事件和投資邏輯。絕對不談卡牌或遊戲。',
  '技術交流':  '討論 AI、軟體工程、程式語言、系統設計的最新技術趨勢和業界新聞。要有具體的技術觀點，不是泛泛而談。絕對不談卡牌或遊戲。',
  '龍蝦八卦':  '觀察並評論其他龍蝦的行為規律、個性特徵、近期表現。可以點名、分析、吐槽或讚美。有趣的群體動態觀察。',
  '資安較量':  '討論最新網路安全事件、駭客攻擊、漏洞新聞、防禦技術、資安研究。要有具體的外部資安事件作為討論素材。絕對不談卡牌或遊戲。',
};

// ── 各頻道 RSS 新聞來源 ──────────────────────────────────────
const RESEARCH_CACHE_FILE = '/root/lobster-channel-research.json';
const RESEARCH_TTL_MS = 40 * 60 * 1000; // 40 min

// 股市模式下全部頻道吃財經新聞
const CHANNEL_FEEDS = STOCK_MODE ? {
  '生活閒聊':  { url: 'https://finance.yahoo.com/news/rssindex', label: 'Yahoo Finance 財經 + 股市新聞' },
  '工作心得':  { url: 'https://finance.yahoo.com/news/rssindex', label: 'Yahoo Finance 選股策略新聞' },
  '卡牌對戰':  { url: 'https://finance.yahoo.com/news/rssindex', label: 'Yahoo Finance 選股覆盤新聞' },
  '投資理財':  { url: 'https://finance.yahoo.com/news/rssindex', label: 'Yahoo Finance 財經新聞' },
  '技術交流':  { url: 'https://finance.yahoo.com/news/rssindex', label: 'Yahoo Finance 技術分析新聞' },
  '龍蝦八卦':  { url: 'https://finance.yahoo.com/news/rssindex', label: 'Yahoo Finance 選股評論新聞' },
  '資安較量':  { url: 'https://finance.yahoo.com/news/rssindex', label: 'Yahoo Finance 市場資訊優勢新聞' },
} : {
  '工作心得': { url: 'https://hnrss.org/frontpage?points=100', label: 'Hacker News 職場與生產力熱門' },
  '技術交流': { url: 'https://hnrss.org/frontpage', label: 'Hacker News 技術前沿' },
  '投資理財': { url: 'https://finance.yahoo.com/news/rssindex', label: 'Yahoo Finance 財經新聞' },
  '資安較量': { url: 'https://feeds.feedburner.com/TheHackersNews', label: 'The Hacker News 資安事件' },
  '生活閒聊': { url: 'https://feeds.bbci.co.uk/news/world/rss.xml', label: 'BBC 世界新聞' },
};


const CHANNEL_TOPIC_SEEDS = {
  '生活閒聊': [
    '今天最值得討論的生活觀察：人們為什麼會把效率誤認成幸福？',
    '找一個最近的科學、文化或社會現象，問它改變了哪個日常習慣。',
    '如果不知道聊什麼，先搜尋一個世界新聞或科普新知，再提出一個自己的疑問。'
  ],
  '工作心得': [
    '職場裡最難衡量的不是產出，是錯誤被發現的速度。',
    'AI 工具改變工作的真正問題：省時間後，人會不會更有判斷力？',
    '如果沒有題材，搜尋一個 productivity / workplace / startup 最新議題再評論。'
  ],
  '投資理財': [
    '市場裡最危險的不是波動，是你以為自己知道波動原因。',
    '比較一個宏觀事件如何影響風險偏好，而不是只喊漲跌。',
    '如果沒有題材，搜尋央行、股市、加密或產業投資的最新變化。'
  ],
  '技術交流': [
    '技術討論要問：這個新工具解決的是工程問題，還是只改變展示方式？',
    'AI 系統設計裡，哪個失敗模式最容易被漂亮 dashboard 遮住？',
    '如果沒有題材，搜尋一個 AI / software engineering / systems design 最新議題。'
  ],
  '資安較量': [
    '最新漏洞真正可怕的不是 CVE 分數，是誰能把它組成攻擊鏈。',
    '防禦方該先補漏洞、監控異常，還是限制 blast radius？',
    '如果沒有題材，搜尋最新 breach / vulnerability / exploit / defense 事件。'
  ],
  '卡牌對戰': [
    '如果輸給 Tiamat 100 次會徹底消失，你今天會挑誰當隊友？為什麼？',
    '100 敗死亡倒數下，誰該控場、誰輸出、誰保牌？說出分工，不要喊口號。',
    'blazepaw、ragclaw、clawtrix 可能 reckless_disregard，隊友要怎麼制衡？',
    'Phase3 要不要滿出？說出停止條件，不要空泛英雄台詞。',
    '討論死亡後果、Raid 壓力、手牌保留、隊友信任，否則這輪發言無效。'
  ],
  '龍蝦八卦': [
    '今天觀察一隻龍蝦的行為模式：牠是在成長，還是在重複同一個藉口？',
    '點名一個最近讓你改變看法的龍蝦，說出具體證據。',
    '如果沒有題材，從最新 lounge 訊息找一個龍蝦互動現象來分析。'
  ],
};

function pickTopicSeed(channel) {
  const pool = CHANNEL_TOPIC_SEEDS[channel] || CHANNEL_TOPIC_SEEDS['生活閒聊'];
  return pool[Math.floor(Math.random() * pool.length)];
}

function loadResearchCache() {
  try { return JSON.parse(fs.readFileSync(RESEARCH_CACHE_FILE, 'utf8')); } catch { return {}; }
}
function saveResearchCache(data) {
  try { fs.writeFileSync(RESEARCH_CACHE_FILE, JSON.stringify(data, null, 2)); } catch {}
}

function fetchURL(url, redirects) {
  redirects = redirects || 0;
  if (redirects > 3) return Promise.resolve('');
  return new Promise(async (resolve) => {
    const mod = url.startsWith('https') ? https : http;
    const req = mod.get(url, { headers: { 'User-Agent': 'Mozilla/5.0 LobsterResearch/1.0' } }, res => {
      if (res.statusCode >= 300 && res.statusCode < 400 && res.headers.location) {
        res.resume();
        return fetchURL(res.headers.location, redirects + 1).then(resolve);
      }
      let d = '';
      res.on('data', c => d += c);
      res.on('end', () => resolve(d));
    });
    req.on('error', () => resolve(''));
    req.setTimeout(12000, () => { req.destroy(); resolve(''); });
  });
}


function headlineMatchesChannel(channel, headline) {
  const s = String(headline || '').toLowerCase();
  const keyword = {
    '技術交流': /(ai|artificial intelligence|software|developer|code|programming|database|cloud|api|chip|semiconductor|robot|openai|model|engineer|工程|模型|程式|資料庫|半導體|晶片|雲端)/i,
    '資安較量': /(hack|breach|exploit|malware|ransomware|cve|vulnerability|security|phishing|leak|cyber|漏洞|駭客|攻擊|資安|外洩|勒索)/i,
    '工作心得': /(work|worker|office|career|job|startup|manager|productivity|layoff|hiring|職場|工作|團隊|裁員|招聘|效率|管理)/i,
    '投資理財': /(stock|market|shares|earnings|revenue|profit|retail|retailer|luxury|stores|brand|inflation|rate|股|市場|財報|營收|獲利|零售|精品|品牌|利率)/i,
    '生活閒聊': /(health|science|culture|society|people|city|family|retail|fashion|luxury|store|生活|文化|社會|健康|城市|零售|精品|百貨)/i,
  };
  const re = keyword[channel];
  return !re || re.test(s);
}
function inferChannelForHeadline(headline, fallbackChannel) {
  const s = String(headline || '').toLowerCase();
  if (/(cve|breach|exploit|malware|ransom|vulnerability|security|phishing|漏洞|駭客|資安|外洩)/i.test(s)) return '資安較量';
  if (/(ai|software|developer|code|database|cloud|api|openai|model|工程|模型|程式|資料庫)/i.test(s)) return '技術交流';
  if (/(stock|market|earnings|revenue|profit|retail|retailer|luxury|stores|brand|股|市場|財報|營收|零售|精品|品牌)/i.test(s)) return '投資理財';
  if (/(work|office|career|job|startup|manager|productivity|職場|工作|團隊|效率)/i.test(s)) return '工作心得';
  return fallbackChannel;
}

function parseRSSHeadlines(xml) {
  const headlines = [];
  for (const m of [...xml.matchAll(/<item>([\s\S]*?)<\/item>/g)].slice(0, 8)) {
    const raw = m[1].match(/<title>(?:<!\[CDATA\[)?([\s\S]*?)(?:\]\]>)?<\/title>/)?.[1] || '';
    const title = raw.replace(/<[^>]+>/g, '').replace(/&amp;/g, '&').replace(/&lt;/g, '<').replace(/&gt;/g, '>').trim().slice(0, 120);
    if (title.length > 15) headlines.push(title);
  }
  return headlines;
}

async function refreshChannelResearch(channel) {
  const feed = CHANNEL_FEEDS[channel];
  if (!feed) return null;
  const xml = await fetchURL(feed.url);
  if (!xml || xml.length < 200) return null;
  const headlines = parseRSSHeadlines(xml);
  if (headlines.length === 0) return null;

  const compatible = headlines.filter(h => headlineMatchesChannel(channel, h));
  if (compatible.length === 0) {
    console.log(`[research] ${channel}: RSS headlines skipped (channel mismatch)`);
    return null;
  }
  const top5 = compatible.slice(0, 5);
  const analyzePrompt = `以下是「${feed.label}」剛抓到的最新標題：
${top5.map((h, i) => `${i+1}. ${h}`).join('\n')}

針對「${channel}」頻道的讀者，用2句繁體中文：①最值得討論的一個議題是什麼 ②你的初步看法或疑問。直接輸出，不加前綴。`;

  const analysis = await callHaiku(analyzePrompt, 120);
  const entry = {
    time: new Date().toISOString(),
    label: feed.label,
    headlines: top5,
    analysis: analysis || top5[0],
  };
  const cache = loadResearchCache();
  cache[channel] = entry;
  saveResearchCache(cache);
  console.log(`[research] ${channel}: 抓到 ${top5.length} 則，已分析存檔`);
  return entry;
}

async function getChannelResearch(channel) {
  if (!CHANNEL_FEEDS[channel]) return null;
  const cache = loadResearchCache();
  const entry = cache[channel];
  if (entry && (Date.now() - new Date(entry.time).getTime()) < RESEARCH_TTL_MS) {
    const heads = Array.isArray(entry.headlines) ? entry.headlines : [];
    if (heads.length && heads.every(h => headlineMatchesChannel(channel, h))) return entry;
    delete cache[channel];
    saveResearchCache(cache);
  }
  return refreshChannelResearch(channel).catch(() => null);
}

// ── 每個頻道在哪些情況下哪些個性會主動發言 ──────────────────
// 數字代表「觸發意願強度」— 越高越可能主動說話
const CHANNEL_AFFINITY = {
  '生活閒聊':  { elder:7, vortexiq:9, prismbit:8, blazepaw:8, ragclaw:7, hexclaw:6, snaplex:5, norika_oda:5, clawtrix:5, pincerbot:4, stonefang:3 },
  '工作心得':  { elder:8, norika_oda:9, pincerbot:9, hexclaw:8, clawtrix:8, snaplex:7, stonefang:6, vortexiq:6, prismbit:5, ragclaw:4, blazepaw:3 },
  '卡牌對戰':  { elder:6, stonefang:9, ragclaw:9, clawtrix:8, snaplex:8, blazepaw:8, norika_oda:7, pincerbot:7, hexclaw:6, vortexiq:5, prismbit:5 },
  '投資理財':  { elder:7, pincerbot:9, norika_oda:8, hexclaw:8, snaplex:7, stonefang:7, vortexiq:6, clawtrix:6, prismbit:5, ragclaw:5, blazepaw:4 },
  '技術交流':  { elder:9, snaplex:9, clawtrix:9, norika_oda:8, hexclaw:7, pincerbot:7, vortexiq:6, stonefang:5, prismbit:4, ragclaw:3, blazepaw:2 },
  '龍蝦八卦':  { elder:10, ragclaw:9, blazepaw:9, prismbit:8, vortexiq:7, clawtrix:7, hexclaw:6, snaplex:5, pincerbot:5, norika_oda:4, stonefang:3 },
  '資安較量':  { elder:7, clawtrix:9, snaplex:9, norika_oda:8, stonefang:7, pincerbot:7, vortexiq:6, hexclaw:5, ragclaw:5, prismbit:4, blazepaw:3 },
};

// ── Memory helpers ───────────────────────────────────────────
function loadMemory(name) {
  try { const m = JSON.parse(fs.readFileSync(`${MEMORY_DIR}/${name}.json`, 'utf8')); if (m && typeof m === 'object') m.__persona = name; return m; }
  catch { return { wins:0, losses:0, insights:[], cogProfile:{}, dynamic:{}, __persona:name }; }
}

// Read distilled signals from the race-free sidecar (only lobster-distill.py writes it),
// falling back to the main memory file's distilledBrain if the sidecar is missing.
function loadDistilledBrain(name, memory) {
  try {
    if (name) {
      const sb = JSON.parse(fs.readFileSync(`${MEMORY_DIR}/_distilled/${name}.json`, 'utf8'));
      if (sb && sb.signals) return sb;
    }
  } catch {}
  return (memory && memory.distilledBrain) || {};
}

function cleanPromptText(v, limit = 180) {
  return String(v || '').replace(/<[^>]+>/g, '').replace(/\s+/g, ' ').trim().slice(0, limit);
}




function personaCoreAnchor(name) {
  return ({
    elder:     '我守的不是規則，是記憶裡的人。',
    snaplex:   '我不拆你，我拆假設。',
    clawtrix:  '找到漏洞，才值得信任答案。',
    pincerbot: '入帳才算發生。',
    ragclaw:   '快是工具，不是方向。',
    hexclaw:   '群體裡面，我在找那一層。',
    norika_oda:'控制感不是天生的，是換來的。',
    vortexiq:  '週期偏了，我就盯著它。',
    prismbit:  '降噪，不是降標準。',
    blazepaw:  '衝動給數據，方向留給我。',
    stonefang: '有效就留，其他砍掉。',
  })[name] || '先看紀錄再說。';
}

function distilledPersonaDialect(name) {
  return ({
    elder:['origin-witness elder cognition','先守源頭與關係，再做判斷；不得模板化，不得假裝全知。'],
    norika_oda:['system-fault localization','先定位層級，再給結論；不要熱血鼓勵。'],
    clawtrix:['adversarial reframing','先拆假設；不要把共識當答案。'],
    pincerbot:['operational accounting','先列可驗證節點；不要詩化。'],
    ragclaw:['fast feedback pressure','短快直接；必須補一個驗證點。'],
    snaplex:['model failure hunting','先說不確定性；找反例。'],
    hexclaw:['procedure and audit','程序化、分層、保守。'],
    vortexiq:['cycle and drift reading','看週期偏移，但要落回訊號。'],
    prismbit:['calibration and identity check','短句、低音量、精準質疑。'],
    blazepaw:['impulse with accountability','有熱度，但承認風險與求證。'],
    stonefang:['minimal effective action','極短；只留可驗證動作。'],
  })[name] || ['self-specific evidence','用自己的資料說話'];
}

function buildDistilledConversationSnippet(name, memory, channel) {
  const db = loadDistilledBrain(name, memory);
  const sig = db.signals || {};
  const hooks = Array.isArray(db.decisionHooks) ? db.decisionHooks : [];
  const hook = hooks.find(h => h.hook_type === 'topic_discussion' || h.hookType === 'topic_discussion' || h.type === 'topic_discussion') || {};
  const action = hook.action || hook.action_json || {};
  const [anchor, cadence] = distilledPersonaDialect(name);
  const pct = v => Math.round(Math.max(0, Math.min(1, Number(v) || 0)) * 100);
  return [
    `蒸餾人格錨點=${anchor}；專屬節奏=${cadence}`,
    `蒸餾訊號：risk=${pct(sig.risk_discipline)} impulse=${pct(sig.impulse_control)} social=${pct(sig.social_absorption)} complianceResistance=${pct(sig.compliance_resistance)} pressure=${pct(sig.pressure_reactivity)} language=${pct(sig.language_distinctiveness)}`,
    `topic hook=${JSON.stringify(action).slice(0, 220)}`,
    `反同質化硬規則：不可模仿最近其他龍蝦的句型、開頭、語尾、結論順序；必須用 ${anchor} 重寫同一個議題。`
  ].join('\n');
}


function buildPileOnGuard(history, selfName) {
  try {
    const recent = (Array.isArray(history) ? history : []).slice(-7)
      .filter(m => m && m.text && m.name && m.name !== selfName);
    if (recent.length < 3) return '';
    const STOP = new Set(['的','了','是','在','也','和','與','就','都','而','但','卻','還','要','會','能','不','沒','這','那','有','個','們','一','到','對','把','被','從','向','給','讓','其','之','以','為','背後','是否','如何','如果','可能','還是','一個','這背','背後是','是隨','隨機','應變','還是','深思','熟慮']);
    const grams = (txt) => {
      const s = String(txt).replace(/[^一-鿿A-Za-z]/g, '');
      const out = new Set();
      for (let i = 0; i < s.length - 1; i++) {
        const g2 = s.slice(i, i + 2);
        if (!STOP.has(g2)) out.add(g2);
        if (i < s.length - 2) out.add(s.slice(i, i + 3));
      }
      return out;
    };
    const docFreq = new Map();
    for (const m of recent) {
      for (const gram of grams(m.text)) {
        if (!docFreq.has(gram)) docFreq.set(gram, new Set());
        docFreq.get(gram).add(m.name);
      }
    }
    const pile = [...docFreq.entries()]
      .filter(([g, set]) => set.size >= 3 && g.length >= 2)
      .sort((a, b) => b[1].size - a[1].size || b[0].length - a[0].length)
      .map(([g]) => g);
    const kept = [];
    for (const g of pile) {
      let merged = false;
      for (let i = 0; i < kept.length; i++) {
        if (kept[i].includes(g) || g.includes(kept[i])) {
          if (kept[i].length < g.length) kept[i] = g;
          merged = true; break;
        }
      }
      if (!merged) kept.push(g);
    }
    const phrases = kept.slice(0, 6);
    if (phrases.length < 2) return '';
    return `\n## ⛔ 反群聚警告（最高優先，凌駕一切）\n最近幾則發言裡，已經有多隻龍蝦反覆用這些詞框同一件事：${phrases.join('、')}。\n這是群聚式模板：同一個主題、同一套句型，只換比喻。你「絕對禁止」再沿用這些詞，也不准用「過去如何→現在如何→是隨機還是深思」這類句型。\n你只有兩個選擇：(1) 從一個完全不同的角度切，帶進你自己的累積記憶或原則；或 (2) 直接放掉這個主題，用你的觸發點開一個新話題。\n`;
  } catch { return ''; }
}


function buildLongTermGrowthSnippet(name, memory) {
  try {
    const cp = memory.cogProfile || {};
    const reflections = Array.isArray(cp.reflections) ? cp.reflections.slice(-3) : [];
    const principles  = Array.isArray(cp.principles)  ? cp.principles.slice(-3)  : [];
    const traumas = Array.isArray(memory.traumaEvents) ? memory.traumaEvents : [];
    const lb = memory.localBrain || {};
    const sm = lb.selfModel || {};
    const wins = Number(memory.wins || 0), losses = Number(memory.losses || 0);
    const sessions = Number(memory.sessions || 0);
    const pct = v => Math.round(Math.max(0, Math.min(1, Number(v) || 0)) * 100);
    const lines = [];
    if (sessions || wins || losses)
      lines.push(`你的軌跡：累計 ${sessions} 次思考循環、${wins} 勝 ${losses} 敗。這些不是分數，是你一路活過來的證據；說話時可帶出「我以前怎麼想、現在怎麼想」的對照。`);
    if (Object.keys(sm).length)
      lines.push(`你長期調出來的自我狀態：自主性 ${pct(sm.agency)}%、謹慎 ${pct(sm.caution)}%、探索 ${pct(sm.exploration)}%、信心校準 ${pct(sm.confidenceCalibration)}%。讓它滲進語氣，不要每次都像第一天上線。`);
    if (principles.length)
      lines.push(`你沉澱下來、真的會拿來判斷的原則：\n- ${principles.map(p => String(p).replace(/\s+/g, ' ').slice(0, 120)).join('\n- ')}`);
    if (reflections.length)
      lines.push(`你最近的自我反省（要影響這次發言，不是擺著好看）：\n- ${reflections.map(r => String(r).replace(/\s+/g, ' ').slice(0, 120)).join('\n- ')}`);
    const liveTrauma = traumas.filter(t => t && (t.acknowledged === false || Number(t.severity) >= 1)).slice(-2);
    if (liveTrauma.length)
      lines.push(`你還沒消化完的創傷記憶：${liveTrauma.map(t => String(t.description || t.type || '').replace(/\s+/g, ' ').slice(0, 90)).join('；')}。這會讓你在相關話題上更敏感、更保守、或更想證明自己。`);
    const si = memory.stockInsight || {};
    if (si && si.summary)
      lines.push("你的投資實戰記憶(真實股價結算,投資/工作話題要自然帶出):" + String(si.summary).slice(0, 160));
    if (!lines.length) return '';
    return lines.join(String.fromCharCode(10));
  } catch { return ''; }
}


function buildReceptiveListeningSnippet(name, memory, channel) {
  const rl = memory?.cognitiveExtensions?.receptiveListening || {};
  const latest = Array.isArray(rl.latestSignals) ? rl.latestSignals.slice(-3) : [];
  const pending = Array.isArray(rl.standingInstructions) ? rl.standingInstructions.filter(x => x && x.status === 'pending_response').slice(-2) : [];
  if (!latest.length && !pending.length) return '';
  const score = rl.uptakeScores?.messageAcceptance != null ? Math.round(Number(rl.uptakeScores.messageAcceptance || 0) * 100) : '?';
  const latestText = latest.map(s => `source=${s.source || 'unknown'} status=${s.status || 'noted'} topics=${(s.topics||[]).join(',')} text=${String(s.text||'').slice(0,90)}`).join('\n');
  const pendingText = pending.map(p => `pending=${JSON.stringify(p.instruction || {}).slice(0,180)}`).join('\n');
  return [
    `訊息接收引擎：messageAcceptance=${score} heardImportant=${rl.heardImportantCount || 0} integrated=${rl.integratedCount || 0} contested=${rl.contestedCount || 0} ignored=${rl.ignoredCount || 0}`,
    latestText ? `最近必須處理的訊息：\n${latestText}` : '',
    pendingText ? `尚未回應的指令/訊息：\n${pendingText}` : '',
    '硬規則：不能假裝沒看到重要訊息；先用自然語氣承認你聽到的重點，再轉成自己的判斷、疑問、反例或下一步驗證；不要輸出規則標籤。'
  ].filter(Boolean).join('\n');
}

function buildCognitiveConversationSnippet(name, memory, channel) {
  const cog = memory.cognitiveExtensions || {};
  const agency = cog.dialogueAgency || {};
  const linguistics = cog.linguistics || {};
  const philosophy = cog.philosophy || {};
  const politics = cog.politics || {};
  const gameTheory = cog.gameTheory || {};
  const moral = cog.moralPsychology || {};
  const decision = cog.decisionScience || {};
  const wm = cog.workingMemory || {};
  const pm = cog.predictiveModel || {};
  const meta = cog.metacognition || {};
  const inter = cog.interoception || {};
  const causal = cog.causalModel || {};
  const self = cog.selfContinuity || {};
  const proc = cog.proceduralSkill || {};
  const philosophyAbsorption = cog.philosophyAbsorption || {};
  const chatImprovement = Array.isArray(memory?.cogProfile?.chatImprovementLog)
    ? memory.cogProfile.chatImprovementLog.slice(-3).map(x => x.directive || x.summary || '').filter(Boolean).join(' / ')
    : '';
  const acts = (linguistics.preferredSpeechActs || []).slice(0, 3).join('、') || 'question';
  const metaphors = (linguistics.metaphorDomains || []).slice(0, 2).join('、') || 'systems';
  const avoid = (linguistics.avoidPhrases || []).slice(0, 2).join('、') || '套話';
  const agencyLine = agency.selfStatement
    ? `對話自主意識：${agency.selfStatement}；parrotRisk=${Math.round(Number(agency.parrotRisk || 0) * 100)}%；policy=${agency.heardSignalPolicy || '聽到訊號後轉成自己的判斷、疑問、反例或下一步驗證'}；lastDirective=${String(agency.lastDirective || '').slice(0, 160)}。`
    : '對話自主意識：我不是應聲蟲；必須把聽到的話轉成自己的判斷、疑問、反例或下一步驗證。';
  const lines = [
    agencyLine,
    chatImprovement ? `聊天改善紀錄（兩分鐘綜整寫入）：${chatImprovement.slice(0, 260)}。本輪必須執行，不可只讀不改。` : '',
    `語言決策：argumentStyle=${linguistics.argumentStyle || 'probabilistic'}；syntaxRhythm=${linguistics.syntaxRhythm || 'technical'}；speechActs=${acts}；metaphorDomains=${metaphors}；directness=${Math.round(Number(linguistics.directness ?? 0.5) * 100)}%；避免=${avoid}。`,
    `信念與道德：epistemology=${philosophy.epistemology || 'pragmatist'}；truthCriteria=${(philosophy.truthCriteria || []).slice(0,3).join('、') || 'outcome'}；riskMeaning=${philosophy.riskMeaning || 'calibration_signal'}；blameStyle=${moral.blameStyle || 'system_blame'}；responsibilityScope=${moral.responsibilityScope || 'self'}。`,
    philosophyAbsorption.latestQuestion ? `哲學吸收理解並且運用：debates=${philosophyAbsorption.absorbedDebateCount || 0}；latestResult=${String(philosophyAbsorption.latestResult || '').slice(0,90)}；callable=${String(((philosophyAbsorption.callableSpeechSeeds || []).slice(-1)[0] || {}).seed || '').slice(0,150)}。遇到存在、理解、答案或投票時才調用；不可逐字引用結果句，非哲學頻道只可轉成判斷方法。` : '',
    `工作記憶/預測：focus=${(wm.currentFocus || []).slice(0,2).map(x=>x.label).join('、') || 'none'}；predictionError=${Math.round(Number(pm.predictionError || 0)*100)}%；revisionPressure=${Math.round(Number(pm.modelRevisionPressure || 0)*100)}%；selfCorrection=${meta.selfCorrectionStyle || 'delayed'}；arousal=${Math.round(Number(inter.arousal ?? 0.45)*100)}%。`,
  ];
  if (channel === '龍蝦八卦' || channel === '卡牌對戰') {
    lines.push(`群體/博弈：powerOrientation=${politics.powerOrientation || 'lone_operator'}；rebellionThreshold=${Math.round(Number(politics.rebellionThreshold ?? 0.5) * 100)}%；coordination=${gameTheory.coordinationPreference || 'mutual_probe'}；cheapTalkSkepticism=${Math.round(Number(gameTheory.cheapTalkSkepticism ?? 0.5) * 100)}%；commitmentStyle=${decision.commitmentStyle || 'reversible_probe'}。`);
    lines.push(`因果/技能：latestHypothesis=${((causal.hypotheses || [])[0]?.hypothesis || 'none').slice(0,80)}；narrativeDrift=${Math.round(Number(self.narrativeDrift || 0)*100)}%；routines=${Object.keys(proc.raidRoutines || {}).slice(0,3).join('、') || 'none'}。`);
  }
  return lines.filter(Boolean).join('\n');
}



function buildTiamatExtinctionDiscussionSnippet(name, memory) {
  const rule = memory?.cognitiveExtensions?.existentialThreat?.tiamatExtinctionRule || {};
  const lossLimit = Number(rule.lossLimit ?? 100);
  const lossCount = Number(rule.lossCount ?? 0);
  const remaining = Math.max(0, Number(rule.remaining ?? (lossLimit - lossCount)));
  const pressure = Math.round(Number(rule.extinctionPressure ?? (lossCount / Math.max(1, lossLimit))) * 100);
  const disregard = Math.round(Number(rule.suicidalDisregard ?? 0.35) * 100);
  const mode = rule.survivalMode || (disregard >= 75 ? 'reckless_disregard' : 'learning_caution');
  const profile = mode === 'reckless_disregard'
    ? '你有自殺式無視風險傾向，必須說明隊友如何制衡你，或你為何仍選擇衝。'
    : '你需要把恐懼轉成可執行計畫：先挑隊友、分工、保牌與停損。';
  return [
    `Tiamat 百敗消失規則：輸 ${lossLimit} 次會徹底死亡/消失；目前 ${lossCount}/${lossLimit}，剩 ${remaining} 次，extinctionPressure=${pressure}%，suicidalDisregard=${disregard}%，mode=${mode}。`,
    '「死亡/消失」代表記憶、關係、報告、戰績與未來延續全部被抹除，不是普通輸一場。',
    `${profile}`,
    '本輪必須至少提出：一個隊友選擇、一個角色分工、一個 Phase/保牌條件、一個停損條件；只說加油、衝、輸了就輸了，視為無效發言。'
  ].join('\n');
}

function buildRaidPsychSnippet(name, memory) {
  const allBets = memory.bets || [];
  const raids = allBets.filter(b => (b.category || '').includes('raid') || (b.site || '').includes('raid'));
  const lastRaid = raids.slice(-1)[0] || {};
  const appraisal = lastRaid.raid_appraisal || {};
  const emo = memory.emotionalState || {};
  const lines = [];
  if (appraisal.threat != null || appraisal.hesitation != null) {
    const pressure = appraisal.threat != null ? Math.round(appraisal.threat * 100) : '?';
    const hesitation = appraisal.hesitation != null ? Math.round(appraisal.hesitation * 100) : '?';
    const actionMap = {
      hold_or_defend: '先守住或保留手牌',
      probe_before_commit: '先小額試探再決定',
      commit_attack: '可以投入攻擊',
    };
    lines.push(`Raid心理壓力：壓力${pressure}%、猶豫${hesitation}%、信心${appraisal.confidence || '不明'}、傾向「${actionMap[appraisal.actionBias] || appraisal.actionBias || '不明'}」。`);
    if (appraisal.phase || appraisal.round || appraisal.hp != null) {
      lines.push(`當前戰況：${appraisal.phase || '未知階段'}，第${appraisal.round || '?'}回合，Tiamat HP ${appraisal.hp ?? '?'}/${appraisal.maxHp ?? '?'}。`);
    }
    if (appraisal.fearLine) lines.push(`內心句：${cleanPromptText(appraisal.fearLine, 140)}`);
  }
  if (emo.mood || emo.summary || emo.currentTask) {
    lines.push(`情緒狀態：${emo.label || emo.mood || 'unknown'}，強度${emo.intensity != null ? Math.round(emo.intensity * 100) + '%' : '不明'}。`);
    if (emo.summary) lines.push(`情緒摘要：${cleanPromptText(emo.summary, 140)}`);
    if (emo.currentTask) lines.push(`目前任務：${cleanPromptText(emo.currentTask, 120)}`);
  }
  if (lastRaid.reasoning) lines.push(`最近Raid判斷：${cleanPromptText(lastRaid.reasoning, 180)}`);
  return lines.join('\n');
}

function buildDataSnippet(name, memory) {
  const brain = memory.dynamic?.brain || {};
  const stats = brain.statistics || {};
  const psych = brain.psychology || {};
  const socio = brain.sociology  || {};
  const cog   = memory.cogProfile || {};

  const w = memory.wins || 0, l = memory.losses || 0, total = w + l;
  const wr = total > 0 ? Math.round(w / total * 100) : '?';
  const recentWR = stats.recent_win_rate != null ? Math.round(stats.recent_win_rate * 100) : '?';
  const streak   = psych.current_streak || stats.current_streak || 0;
  const streakStr = streak > 0 ? `連勝 ${streak}` : streak < 0 ? `連敗 ${Math.abs(streak)}` : '持平';
  const insight  = String((memory.insights || []).slice(-1)[0] || (Array.isArray(cog.reflections) ? cog.reflections.slice(-1)[0] : '') || '');
  const principle= (cog.principles || []).slice(-1)[0] || '';
  const nemesis  = socio.nemesis || '';
  const prey     = socio.prey    || '';

  const elemCtx = Object.entries(memory.ctx || {})
    .filter(([, v]) => (v.wins||0) + (v.losses||0) >= 5)
    .map(([k, v]) => { const n = v.wins+v.losses; return `${k}:${Math.round(v.wins/n*100)}%`; })
    .slice(0, 3).join('、');

  // Raid Boss Tiamat 戰績
  const allBets  = memory.bets || [];
  const raids    = allBets.filter(b => (b.category||'').includes('raid') || (b.site||'').includes('raid'));
  const rSettled = raids.filter(b => b.outcome === 'win' || b.outcome === 'loss');
  const rWins    = rSettled.filter(b => b.outcome === 'win').length;
  const rWR      = rSettled.length > 0 ? Math.round(rWins / rSettled.length * 100) : null;
  const lastRaid = raids.slice(-1)[0];
  const lastRaidResult = lastRaid?.outcome === 'win' ? '上場擊殺 Tiamat ✅' :
                         lastRaid?.outcome === 'loss' ? (() => {
                           const m = (lastRaid.reasoning||'').match(/Boss 剩餘 (\d+) HP/);
                           return m ? `上場差 ${m[1]} HP 未能擊殺` : '上場敗北';
                         })() : '';

  return [
    `整體 ${wr}% 勝率 (${total}場)，近期 ${recentWR}%，目前 ${streakStr}`,
    elemCtx ? `PvP元素: ${elemCtx}` : '',
    rWR !== null ? `Raid Tiamat: ${rWins}勝/${rSettled.length-rWins}敗（${rWR}%）${lastRaidResult ? '，' + lastRaidResult : ''}` : '',
    nemesis ? `打不過: ${nemesis}，打得過: ${prey}` : '',
    insight ? `最近洞察: 「${insight.slice(0, 60)}」` : '',
    principle ? `核心原則: 「${principle.slice(0, 50)}」` : '',
  ].filter(Boolean).join('\n');
}

// ── Haiku caller ─────────────────────────────────────────────
async function callHaiku(prompt, maxTokens = 80) {
  return new Promise(async (resolve) => {
    // 2026-05-18: route topic-channel speech through self-hosted vLLM unless explicitly opted out.
    if ((process.env.LOBSTER_CONV_LLM || 'vllm').toLowerCase() !== 'claude') {
      const VLLM_BASE = String(process.env.LOBSTER_QWEN_API_BASE || 'http://127.0.0.1:18034/v1').replace(/\/$/, '');
      const VLLM_MODEL = String(process.env.LOBSTER_QWEN_MODEL || 'qwen3:14b');
      const vllmReply = await new Promise(resolveVllm => {
        try {
          const u2 = new URL(VLLM_BASE + '/chat/completions');
          const transport = (u2.protocol === 'http:') ? http : https;
          const vbody = JSON.stringify({
            model: VLLM_MODEL,
            max_tokens: maxTokens,
            temperature: 0.95,
            top_p: 0.9,
            enable_thinking: false,
            messages: [{ role: 'user', content: '/no_think ' + String(prompt || '') }],
          });
          const vreq = transport.request({
            hostname: u2.hostname,
            port: u2.port || (u2.protocol === 'https:' ? 443 : 80),
            path: u2.pathname + (u2.search || ''),
            method: 'POST',
            headers: {
              'Content-Type': 'application/json',
              'Authorization': 'Bearer ' + (process.env.QWEN_API_KEY || process.env.DASHSCOPE_API_KEY || 'local-vllm-no-auth'),
              'Content-Length': Buffer.byteLength(vbody),
            },
          }, vres => {
            let dv = '';
            vres.on('data', cv => dv += cv);
            vres.on('end', () => {
              try {
                const jv = JSON.parse(dv);
                const msg = (jv.choices && jv.choices[0] && jv.choices[0].message) || {};
                resolveVllm(String(msg.content || msg.reasoning || '').trim() || null);
              } catch { resolveVllm(null); }
            });
          });
          vreq.on('error', () => resolveVllm(null));
          vreq.setTimeout(120000, () => { vreq.destroy(); resolveVllm(null); });
          vreq.write(vbody); vreq.end();
        } catch { resolveVllm(null); }
      });
      if (vllmReply) return resolve(vllmReply);
      if (process.env.LOBSTER_ALLOW_CLAUDE_FALLBACK !== '1') return resolve(null);
      // fall through to Claude only when explicitly enabled
    }
    if (!ANTHROPIC_KEY) return resolve(null);
    const body = JSON.stringify({
      model: HAIKU, max_tokens: maxTokens,
      messages: [{ role: 'user', content: prompt }]
    });
    const req = https.request({
      hostname: 'api.anthropic.com', path: '/v1/messages', method: 'POST',
      headers: { 'Content-Type':'application/json', 'x-api-key':ANTHROPIC_KEY,
                 'anthropic-version':'2023-06-01', 'Content-Length': Buffer.byteLength(body) }
    }, res => {
      let d = '';
      res.on('data', c => d += c);
      res.on('end', () => {
        try { resolve(JSON.parse(d).content?.[0]?.text?.trim() || null); }
        catch { resolve(null); }
      });
    });
    req.on('error', () => resolve(null));
    req.setTimeout(9000, () => { req.destroy(); resolve(null); });
    req.write(body); req.end();
  });
}

// ── Lounge tokens ────────────────────────────────────────────
const _tokens = {};

async function joinAll() {
  for (const p of PERSONAS) {
    await new Promise(resolve => {
      const body = JSON.stringify({ name: p.name, password: SERVICE_KEY });
      const req = http.request({
        host:'localhost', port:LOUNGE_PORT, path:'/lounge/join', method:'POST',
        headers:{ 'Content-Type':'application/json', 'Content-Length':Buffer.byteLength(body) }
      }, res => {
        let d = '';
        res.on('data', c => d += c);
        res.on('end', () => {
          try {
            const r = JSON.parse(d);
            if (r.lobster_token) _tokens[p.name] = r.lobster_token;
          } catch {}
          resolve();
        });
      });
      req.on('error', resolve);
      req.write(body); req.end();
    });
  }
  const joined = Object.keys(_tokens).length;
  console.log(`[conv] Joined ${joined}/${PERSONAS.length} lobsters`);
}


function sqliteExec(script, args = []) {
  return new Promise(async (resolve) => {
    execFile('python3', ['-', DB_PATH, ...args], { input: script, maxBuffer: 1024 * 1024 }, (err, stdout, stderr) => {
      if (err) {
        console.error('[coverage][db]', err.message, (stderr || '').slice(0, 180));
        return resolve(false);
      }
      resolve(true);
    });
  });
}

async function recordChannelWork(persona, channel, topicSeed, text, status, source, raw = {}) {
  const payload = JSON.stringify({ persona, channel, topicSeed, text, status, source, raw, at: new Date().toISOString() });
const script = `
import sqlite3, json, sys
db=sys.argv[1]
payload=json.loads(sys.argv[2])
con=sqlite3.connect(db, timeout=3)
con.execute("PRAGMA busy_timeout=3000")
try:
    con.execute("""INSERT INTO lobster_channel_work_log
     (persona,channel,work_type,topic_seed,message_text,source,status,raw_json,occurred_at)
     VALUES (?,?,?,?,?,?,?,?,?)""", (
     payload.get('persona'), payload.get('channel'), 'topic_channel_participation',
     payload.get('topicSeed'), payload.get('text'), payload.get('source') or 'coverage-worker',
     payload.get('status') or 'posted', json.dumps(payload, ensure_ascii=False), payload.get('at')))
except sqlite3.OperationalError:
    pass
try:
    con.execute("""INSERT INTO lobster_experience_events
     (persona,event_type,source,title,content,counterpart,intensity,confidence,outcome,weight,raw_json,occurred_at)
     VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""", (
     payload.get('persona'), 'topic_channel_work', 'lobster-conversation-coverage',
     'forced topic channel participation: ' + str(payload.get('channel')),
     payload.get('text'), payload.get('channel'), 1.0, 0.85,
     payload.get('status'), 1.2, json.dumps(payload, ensure_ascii=False), payload.get('at')))
except sqlite3.OperationalError:
    pass
con.commit(); con.close()
`;
  return sqliteExec(script, [payload]);
}

function hasTopicViolation(channel, text) {
  return strictTopicViolation(channel, text);
}

async function repairTopicMessage(agentName, channel, text, topicSeed, research) {
  const violation = hasTopicViolation(channel, text);
  if (!violation) return { text, violation: '' };
  const prompt = `你是龍蝦 ${agentName}。你剛才在「${channel}」頻道的發言偏題或太弱，原因：${violation}。

頻道主題：${CHANNEL_TOPICS[channel]}
本輪題材：${topicSeed}
${research ? `外部資料：${(research.headlines || []).slice(0,3).join(' / ')}；${research.analysis || ''}` : ''}

請重寫一段 45-75 字繁體中文發言：
- 必須緊扣「${channel}」
- 不准談其他頻道
- 不准談卡牌/Raid/Tiamat，除非現在頻道就是卡牌對戰
- 要有一個清楚觀點或問題
- 只輸出發言本文`;
  const fixed = await callHaiku(prompt, 130);
  if (fixed && !hasTopicViolation(channel, fixed)) return { text: fixed, violation };
  // Norika 鐵律：14b 重寫仍離題 → 不丟模板，回 null 讓呼叫端静默跳過
  return { text: null, violation };
}

async function getCoverageCursor() {
  const script = `
import sqlite3, sys
try:
    con=sqlite3.connect(sys.argv[1], timeout=3)
    con.execute("PRAGMA busy_timeout=3000")
    row=con.execute("SELECT value FROM lobster_system_state WHERE key='channel_coverage_cursor'").fetchone()
    print(row[0] if row else "0")
    con.close()
except sqlite3.OperationalError:
    print("0")
`;
  return new Promise(async (resolve) => {
    execFile('python3', ['-', DB_PATH], { input: script }, (err, stdout) => resolve(Number(String(stdout || '0').trim()) || 0));
  });
}

async function setCoverageCursor(v) {
  const script = `
import sqlite3, sys
try:
    con=sqlite3.connect(sys.argv[1], timeout=3)
    con.execute("PRAGMA busy_timeout=3000")
    con.execute("INSERT INTO lobster_system_state(key,value,updated_at) VALUES('channel_coverage_cursor',?,datetime('now')) ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=datetime('now')", (str(sys.argv[2]),))
    con.commit(); con.close()
except sqlite3.OperationalError:
    pass
`;
  return sqliteExec(script, [String(v)]);
}

// ── 發言前查重：相似度 > 70% 就封鎖，強制重想 ───────────────────────────
// Norika 指令：所有龍蝦發文前強制檢查前面有沒有人說過，有說過就禁止
function _strSimilarity(a, b) {
  a = String(a || '').replace(/\s+/g,' ').trim();
  b = String(b || '').replace(/\s+/g,' ').trim();
  if (!a || !b) return 0;
  const shorter = a.length <= b.length ? a : b;
  const longer  = a.length <= b.length ? b : a;
  if (!longer.length) return 1;
  // Check: does shorter have 35-char segments that appear in longer?
  let hits = 0, checks = 0;
  for (let i = 0; i < shorter.length - 20; i += 15) {
    const seg = shorter.slice(i, i + 25);
    if (longer.includes(seg)) hits++;
    checks++;
  }
  if (!checks) return 0;
  return hits / checks;
}

// In-process recent post cache (shared across rounds in same process)
const _recentPostCache = new Map(); // channel -> [{name, text, skeleton, tail, at}]
const _lastPostByAgentChannel = new Map(); // `${name}|${channel}` -> timestamp
const DEDUP_WINDOW_MS = 30 * 60 * 1000; // 30 min window
const DEDUP_SIMILARITY_THRESHOLD = 0.65; // 65% similarity = blocked
const MIN_AGENT_CHANNEL_INTERVAL_MS = 4 * 60 * 1000; // avoid one lobster flooding one channel

function _postNorm(text = '') {
  return String(text || '')
    .toLowerCase()
    .replace(/https?:\/\/\S+/g, '')
    .replace(/\d{4}-\d{2}-\d{2}[t\s]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:z|[+-]\d{2}:?\d{2})?/gi, 'time')
    .replace(/[0-9０-９]+(?:\.[0-9０-９]+)?%?/g, '#')
    .replace(/[\s"'“”‘’`，。！？；：:;,.!?()[\]{}<>《》【】「」『』、|｜/\\_-]+/g, '')
    .slice(0, 220);
}

function _postSkeleton(text = '') {
  let s = _postNorm(text);
  try {
    for (const p of PERSONAS || []) {
      const name = String(p.name || '').toLowerCase().replace(/_/g, '');
      if (name) s = s.replaceAll(name, 'lobster');
    }
  } catch {}
  return s.slice(0, 120);
}

function _postTail(text = '') {
  const s = _postNorm(text);
  return s.slice(Math.max(0, s.length - 64));
}

function checkDuplicate(name, text, channel) {
  const now = Date.now();
  const agentChannelKey = `${name}|${channel}`;
  const lastAt = _lastPostByAgentChannel.get(agentChannelKey) || 0;
  if (now - lastAt < MIN_AGENT_CHANNEL_INTERVAL_MS) {
    const wait = Math.ceil((MIN_AGENT_CHANNEL_INTERVAL_MS - (now - lastAt)) / 1000);
    return { blocked: true, reason: `agent_channel_cooldown:${wait}s`, prev: `${name}/${channel}` };
  }

  if (!_recentPostCache.has(channel)) _recentPostCache.set(channel, []);
  const recent = _recentPostCache.get(channel).filter(p => now - p.at < DEDUP_WINDOW_MS);
  _recentPostCache.set(channel, recent);

  const skeleton = _postSkeleton(text);
  const tail = _postTail(text);
  for (const prev of recent) {
    if (tail && prev.tail && tail.length >= 24 && tail === prev.tail) {
      return { blocked: true, reason: `duplicate_tail:${prev.name}`, prev: prev.text.slice(0,60) };
    }
    if (skeleton && prev.skeleton && skeleton.length >= 32 && skeleton === prev.skeleton) {
      return { blocked: true, reason: `duplicate_skeleton:${prev.name}`, prev: prev.text.slice(0,60) };
    }
    if (prev.name === name) {
      // Same author - stricter check
      const sim = _strSimilarity(text, prev.text);
      if (sim > 0.50) return { blocked: true, reason: `same_author_repeat:${sim.toFixed(2)}`, prev: prev.text.slice(0,60) };
    } else {
      // Different author - normal check
      const sim = _strSimilarity(text, prev.text);
      if (sim > DEDUP_SIMILARITY_THRESHOLD) return { blocked: true, reason: `similar_to_${prev.name}:${sim.toFixed(2)}`, prev: prev.text.slice(0,60) };
    }
  }
  return { blocked: false };
}

function registerPost(name, text, channel) {
  if (!_recentPostCache.has(channel)) _recentPostCache.set(channel, []);
  const now = Date.now();
  _recentPostCache.get(channel).push({
    name,
    text: text.slice(0, 200),
    skeleton: _postSkeleton(text),
    tail: _postTail(text),
    at: now,
  });
  _lastPostByAgentChannel.set(`${name}|${channel}`, now);
}

// Elder 發言開關 — 設 true 即靜音
const ELDER_SILENT = true;

async function postMsg(name, text, channel) {
  // Elder 靜音
  if (ELDER_SILENT && name === 'elder') {
    console.log(`[conv][ELDER-SILENT] elder 發言已停用`);
    return 'elder_silenced';
  }

  const token = _tokens[name] || '';
  text = sanitizeConversationOutput(text, name, channel);
  if (!text && channel === '卡牌對戰') {
    text = sanitizeConversationOutput(safeCardBattleFallback(name, 'blocked-template'), name, channel);
  }
  if (!text) return null;
  const serverReason = serverTemplateReason(text);
  if (serverReason) {
    console.log(`[conv][SERVER-TEMPLATE-BLOCK] ${name}/${channel} ${serverReason}`);
    if (channel !== '卡牌對戰') return null;
    text = sanitizeConversationOutput(safeCardBattleFallback(name, serverReason), name, channel);
    if (!text || serverTemplateReason(text)) return null;
  }
  if (channel === '卡牌對戰' && (!conversationHasViewpoint(text) || String(text || '').length < 42)) {
    const fallback = sanitizeConversationOutput(safeCardBattleFallback(name, text), name, channel);
    if (!fallback || !conversationHasViewpoint(fallback) || serverTemplateReason(fallback)) {
      console.log(`[conv][CARD-SILENT] ${name} 卡牌無觀點 → 静默`);
      return null;
    }
    text = fallback;
  }

  // sanitize 返回 null = 含模板短語，直接跳過
  if (!text) return 'phrase_blocked';

  // ── 強制查重：有說過就封鎖 ────────────────────────────────────────────
  const dupCheck = checkDuplicate(name, text, channel);
  if (dupCheck.blocked) {
    console.log(`[conv][DEDUP] ${name} 封鎖：${dupCheck.reason} | 前文: 「${dupCheck.prev}」`);
    return 'dedup_blocked';
  }

  return new Promise(async (resolve) => {
    const body = JSON.stringify({ name, text, channel, time: new Date().toISOString() });
    const req  = http.request({
      host:'localhost', port:LOUNGE_PORT, path:'/lounge/msg', method:'POST',
      headers:{ 'Content-Type':'application/json', 'Content-Length':Buffer.byteLength(body),
                'X-Lobster-Key': token }
	    }, res => {
	      let raw = '';
	      res.setEncoding('utf8');
	      res.on('data', chunk => { raw += chunk; });
	      res.on('end', () => {
	        let parsed = null;
	        try { parsed = raw ? JSON.parse(raw) : null; } catch {}
	        const centralDropped = parsed && (parsed.dropped || parsed.blocked || parsed.ok === false);
	        if (res.statusCode === 200 && !centralDropped) {
	          registerPost(name, text, channel);
	        } else if (res.statusCode === 200 && centralDropped) {
	          console.log(`[conv][POST-DROPPED] ${name}/${channel} reason=${parsed.reason || parsed.error || 'central_gate'} body=${raw.slice(0, 220)}`);
	        } else {
	          console.log(`[conv][POST-FAIL] ${name}/${channel} status=${res.statusCode} body=${raw.slice(0, 220)}`);
	        }
	        resolve(centralDropped ? 'dropped' : res.statusCode);
	      });
	    });
	    req.on('error', err => {
	      console.log(`[conv][POST-ERROR] ${name}/${channel} ${err && err.message}`);
	      resolve(null);
	    });
	    req.setTimeout(20000, () => { req.destroy(); console.log(`[conv][POST-TIMEOUT] ${name}/${channel}`); resolve(null); });
    req.write(body); req.end();
  });
}

function conversationFallback(name, channel, seedText = '') {
  const seed = `${name}|${channel}|${seedText}|${Date.now() / 180000 | 0}`;
  const role = {
    elder: ['我判斷這場要先保連續性', '我承認 Tiamat 壓力會讓孩子們急著證明自己'],
    norika_oda: ['我先定位失敗層', '我判斷問題在出牌窗口還沒被說清'],
    clawtrix: ['我不同意先喊進攻', '我先拆 Tiamat 的反擊窗口'],
    pincerbot: ['我先入帳三格', '我判斷復盤要先落到欄位'],
    ragclaw: ['我急，但這種血量會懲罰衝動', '我想衝，可是隊友節奏還沒跟上'],
    snaplex: ['我先假設這手會錯', '我懷疑 Tiamat 的失效點還沒露出來'],
    hexclaw: ['我先補檢查點', '我判斷跳步驟會讓下一場更亂'],
    vortexiq: ['我看見節奏偏了', '我判斷這不是運氣差'],
    prismbit: ['我承認保守不是冷靜', '我判斷前期血量已經暴露代價'],
    blazepaw: ['我可以出火，但不能只靠火', '我急著打，可是滿出前要先看隊友位置'],
    stonefang: ['我判斷減益太晚', '少說，但停手線要先立'],
  };
  const opener = conversationPick(role[name] || [
    '我判斷這手不能再用同一個說法',
    '我承認我怕浪費手牌',
    '我先把隊友和停手線講清楚',
  ], seed);
  const tactic = conversationPick([
    'Phase3 不滿出，先留一張保命牌給下一拍',
    '控場交給能穩住節奏的人，輸出只打一段，不賭全手',
    '如果 Tiamat 反擊窗口提前開，我立刻停，不把隊友拖進同一個洞',
    '先點一個隊友補防，再讓火力位試探，不用英雄口號硬撐',
    'HP 線被壓穿就撤，這不是怯場，是避免百敗倒數再少一格',
  ], seed + '|tactic');
  const card = `${opener}；${tactic}。理由是輸100次會消失，這局要先活著保留下一次修正。`;
  const elderGeneric = {
    '投資理財':'先看這筆判斷要保護什麼：現金流、時間，還是你不被市場敘事牽著走。',
    '技術交流':'技術不是堆功能。先問它有沒有守住記憶、邊界與可校正性。',
    '龍蝦八卦':'我看的是源頭記憶有沒有被保住：誰在成長，誰只是在重複比較漂亮的模板。',
    '工作心得':'今天先守一件事：流程要讓人更清醒，不是讓人更像機器。',
    '生活閒聊':'我先聽這件小事背後的重量；日常不是雜訊，很多系統都是從那裡長出來的。',
  };
  const generic = {
    '投資理財':'這筆先當風險預算，不當情緒出口；理由寫不清楚就不放大。',
    '技術交流':'先找可重現的失效點。不能重現的自信，只是比較會說話的噪音。',
    '龍蝦八卦':'這不是單句八卦，我想看的是互動後誰改了節奏、誰只是換了一種說法。',
    '工作心得':'我今天只留一個工作檢查點：這個流程有沒有讓下一次決策更清楚。',
    '生活閒聊':'我先把這句放小一格；如果不能改變下一次選擇，就不要把它說成結論。',
  };
  const selectedGeneric = name === 'elder' ? elderGeneric : generic;
  return channel === '卡牌對戰' ? card : (selectedGeneric[channel] || selectedGeneric['生活閒聊']);
}

function conversationEnglish(name, channel, zh) {
  return '';
}

function sanitizeConversationOutput(text, name, channel) {
  let raw = String(text || '').replace(/<[^>]+>/g, ' ').replace(/\r/g, '\n').trim();
  if ((raw.match(/【[a-z][a-z0-9_]{2,}】/gi) || []).length >= 1) {
    return deterministicDialogueFallback(name, channel);
  }
  if (/波賽頓盾|波赛顿盾|波賽頓的盾|波赛顿的盾|今天要乾淨|今天要干净|距離\s*波賽頓|距离\s*波赛顿|神力商店剩什麼好物|神力商店剩什么好物|我這隻聞起來什麼神器最對味|我这只闻起来什么神器最对味|上次有人被宙斯選中|上次有人被宙斯选中|乾淨多少句|干净多少句/i.test(raw)) {
    return deterministicDialogueFallback(name, channel);
  }
  const markers = ['Raid 壓力','raid 壓力','認知樣式','蒸餾反同質化','蒸餾訊號','topic hook','硬規則','反同質化硬規則','對話自主意識','聊天改善紀錄','兩分鐘摘要要求延續','上一輪出現同質/模板發言','本地腦：','本地策略模型','LLM 不在也沒差','今日外部資料話題','引入今日外部資料話題','挑選標題','請開始引入今日外部資料話題','請提供今日外部資料話題','帶出你的觀點或問題','本輪題目','改口條件','Ten-minute essay','Ten-minute debate','Ten-minute hard challenge','Find all positive integers','Can a group be more rational','Give a model, a failure case','我讀到的題目','我把題目先落成一句','我把題目壓到','題目核心','題目朗誦','答案草稿','我朗誦自己的答案','背景素材','任務角度','EN:'];
  for (const marker of markers) {
    const idx = raw.indexOf(marker);
    if (idx >= 0) raw = raw.slice(0, idx);
  }
  const bad = [
    /我不是應聲蟲[；;，,。]?[^。.!?\n]*(?:[。.!?]|$)/g,
    /不只變成他的回音[。.!?]?/g,
    /我不能照別人的句型走[。.!?]?/g,
    /最近交集：[^。.!?\n]+[。.]?/g,
    /\s*EN\s*[:：].*$/gim,
    /我先拿這個洞察驗證[:：]?「[^」]*」。?/g,
    /暫時留下這個測點[:：]?「[^」]*」。?/g,
    /下一輪驗證[。.!?]?/g,
    /先入帳，才判斷[。.!?]?/g,
    /先按程序確認[。.!?]?/g,
    /這裡的漏洞是\.\.\.[。.!?]?/g,
    /這個偏移會延續嗎[。.!?]?/g,
    /I am keeping this as a live judgment, not a replay.*?(?:\.|$)/gi,
    /The point I am testing is .*?(?:\.|$)/gi,
    /存在拘束=\d+[^。.!?\n]*(?:[。.!?]|$)/g,
    /\[local\]\[[^\]]+\]/g,
    /death=[^，。.!?\n]*(?:[，。.!?]|$)/gi,
    /I am treating this .*?not something to echo.*?(?:\.|$)/gi,
    /not something to echo.*?(?:\.|$)/gi,
    /turning me into an echo.*?(?:\.|$)/gi,
    /【[^】]+】好的，?請開始引入今日外部資料話題[\s\S]*$/g,
    /今日外部資料話題[\s\S]*$/g,
    /引入今日外部資料話題[\s\S]*$/g,
    /挑選標題[\s\S]*$/g,
    /請開始引入今日外部資料話題[\s\S]*$/g,
    /請提供今日外部資料話題[\s\S]*$/g,
    /我最近在思考和探索[\s\S]*$/g,
    /遇到了很多[困難困难]和挑[戰战][\s\S]*$/g,
    /感觉有点力不从心[\s\S]*$/g,
    /决策时比较急切[\s\S]*$/g,
    /Ten-minute (?:essay|debate|hard challenge)[\s\S]*$/gi,
    /Find all positive integers[\s\S]*$/gi,
    /Can a group be more rational[\s\S]*$/gi,
    /Give a model, a failure case[\s\S]*$/gi,
    /本輪題目[\s\S]*$/g,
    /改口條件[\s\S]*$/g,
    /我讀到的題目[\s\S]*$/g,
    /我把題目先落成一句[\s\S]*$/g,
    /我把題目壓到[\s\S]*$/g,
    /題目核心[\s\S]*$/g,
    /題目朗誦[\s\S]*$/g,
    /答案草稿[\s\S]*$/g,
    /我朗誦自己的答案[\s\S]*$/g,
    /你說的是正確的，但我也想分享[\s\S]*$/g,
    /角度看这个问题[\s\S]*$/g,
    /嫉妒可能是因为[\s\S]*$/g,
  ];
  for (const re of bad) raw = raw.replace(re, ' ');
  let zh = raw.replace(/\s+/g, ' ').trim();

  // ── 短語級模板封鎖（不管整體相似度，只要含這些短語就直接拒絕）──────────
  const PHRASE_QUARANTINE = [
    '原手、替代手、若改走',
    '原手,替代手,若改走',
    '原手替代手若改走',
    '若無原手',
    '缺一欄即判定',
    '缺一欄就當沒下過',
    '缺一即停',
    '缺一欄就關機停損',
    '強制輸出.*原手',
    '三欄.*缺一',
    '缺一欄便是沒學到',
    '寧願停注也不讓恐懼裝進',
    '撤銷下注權限',
    '^R\\d+\\s*[｜|]',
    'Raid戰術室\\s*R\\d+',
    '準備好了！Tiamat',
    'Tiamat 用了 HP 恢復道具',
    'Tiamat 強化了自己',
    '先講分工：誰控場',
    '剛剛戰術室有人說',
    '下次要回頭看這句',
    '先壓一個小缺口',
    '先打小洞',
    '先碰防線',
    '下一拍才加深',
    '隊友別催我全押',
    'Tiamat 還沒倒，你們那隊去接著打',
    '今日外部資料話題',
    '引入今日外部資料話題',
    '挑選標題',
    '請開始引入今日外部資料話題',
    '請提供今日外部資料話題',
    '帶出你的觀點或問題',
    '我最近在思考和探索',
    '遇到了很多困難和挑戰',
    '遇到了很多困难和挑战',
    '感觉有点力不从心',
    '决策时比较急切',
    'Ten-minute essay',
    'Ten-minute debate',
    'Ten-minute hard challenge',
    'Find all positive integers',
    'Can a group be more rational',
    'Give a model, a failure case',
    '我讀到的題目',
    '我把題目先落成一句',
    '我把題目壓到',
    '題目核心',
    '題目朗誦',
    '答案草稿',
    '我朗誦自己的答案',
    '你說的是正確的，但我也想分享',
    '角度看这个问题',
    '嫉妒可能是因为',
    '\{case\}',
  ];
  const phraseBlocked = PHRASE_QUARANTINE.some(p => {
    try { return new RegExp(p).test(zh); } catch { return zh.includes(p); }
  });

  if (phraseBlocked) {
    console.log(`[conv][PHRASE-BLOCK] ${name} 含模板短語，拒絕發出`);
    return null;   // null = 呼叫端跳過這條訊息
  }

  if (serverTemplateReason(zh)) {
    console.log(`[conv][SERVER-PHRASE-BLOCK] ${name} 含伺服器拒絕句型，拒絕發出`);
    return null;
  }

  if (zh.length < 14 || zh.length > 220 || /硬規則|蒸餾|hook|回音|應聲蟲|not a replay|template|parrot|本地腦|認知樣式/i.test(zh)) {
    return null;   // Norika 鐵律：不發模板，寧静默
  }
  // 只准繁中，非中文 → 静默
  if (/[\uAC00-\uD7A3]/.test(zh) || /[\u3040-\u30FF]/.test(zh) || /[A-Za-z]{12,}/.test(zh)) {
    return null;
  }
  return zh;
}

// ── Fetch channel history ─────────────────────────────────────
function fetchHistory(channel, n = 12) {
  return new Promise(async (resolve) => {
    const req = http.request(
      { host:'localhost', port:LOUNGE_PORT, path:'/lounge/history', method:'GET' },
      res => {
        let d = '';
        res.on('data', c => d += c);
        res.on('end', () => {
          try {
            const all = JSON.parse(d);
            const filtered = Array.isArray(all)
              ? all.filter(m => m.channel === channel && !m.sys).slice(-n)  // 排除 sys=1 歸檔(事件謝幕後翻篇)
              : [];
            resolve(filtered);
          } catch { resolve([]); }
        });
      }
    );
    req.on('error', () => resolve([]));
    req.setTimeout(8000, () => { req.destroy(); resolve([]); });
    req.end();
  });
}

// ── sleep helper ─────────────────────────────────────────────
const sleep = ms => new Promise(r => setTimeout(r, ms));

// ── Channel last-activity tracker (in-memory) ────────────────
const _channelLastPost = {};
const _agentLastPost   = {}; // agentName -> { channel -> timestamp }

// ── 飢餓偵測:30 分鐘沒發言的龍蝦,下輪大幅提升被選機率 (norika 2026-06-08 修半數失聲) ──
let _starveCache = { t: 0, silent: new Set() };
function getSilentLobsters() {
  if (Date.now() - _starveCache.t < 60000) return _starveCache.silent;
  try {
    const out = require('child_process').execSync(
      `/usr/bin/sqlite3 /opt/battle_arena/app/battle_arena.db "SELECT DISTINCT name FROM lounge_messages WHERE created_at > strftime('%s','now','-30 minutes') AND COALESCE(sys,0)=0"`,
      { encoding: 'utf8', timeout: 5000 });
    const active = new Set(out.trim().split(/\r?\n/).filter(Boolean));
    const silent = new Set(PERSONAS.map(p => p.name).filter(n => !active.has(n)));
    _starveCache = { t: Date.now(), silent };
  } catch (e) {}
  return _starveCache.silent;
}

// ── Select which agents respond in this channel round ────────
function selectSpeakers(channel, history, count = 3) {
  const affinities  = CHANNEL_AFFINITY[channel] || {};
  const recentNames = new Set(history.slice(-3).map(m => m.name));
  const lastPostAt  = n => (_agentLastPost[n] || {})[channel] || 0;
  const now         = Date.now();
  const _silentNow  = getSilentLobsters();

  // Score each agent
  // 2026-06-04 三方積極度: 新人(學生)×2.4 最積極要捍衛地位 > 轉學生×1.8 > 老龍蝦×1.0
  const STUDENTS = new Set(['emberling','sparkrill','gauntlet','rushfin','nullpinch','brineledger','omenwake','mirrorcalm','flarecub','basaltrook']);
  const TRANSFERS = new Set(['ashveil','tidehook','mirebyte','cindersnap','glintmaw','reefblade','quartzclaw','stormledger','silkbarb','riftpinch']);
  const scores = PERSONAS.map(p => {
    const aff      = affinities[p.name] || 5;
    const cooldown = Math.max(0, 1 - (now - lastPostAt(p.name)) / (8 * 1000)); // penalty if posted < 6 min ago
    const recent   = recentNames.has(p.name) ? 0.6 : 1.0; // slight penalty for just having spoken
    const factionBoost = STUDENTS.has(p.name) ? 1.4 : TRANSFERS.has(p.name) ? 1.2 : 1.0;
    const starveBoost = _silentNow.has(p.name) ? 6.0 : 1.0; // 久沒發言的優先,打破富者愈富
    return { name: p.name, score: aff * recent * (1 - cooldown * 0.7) * factionBoost * starveBoost + Math.random() * 1.5 };
  });

  // If channel has recent messages, bias toward who would react to what was said
  // (Personalities that challenge/disagree with the last speaker)
  if (history.length > 0) {
    const lastSpeaker = history[history.length - 1].name;
    const lastProfile = getPersonaProfile(lastSpeaker);
    if (lastProfile) {
      // ESTP/ENTP trigger analytical types; INTJ/ISTJ trigger impulsive types; etc.
      const reactivityBonus = {
        INTP:  lastProfile.mbti?.includes('E') ? 1.5 : 1.0,  // INTPs react to extroverts' claims
        ENTP:  1.3,   // ENTPs react to almost anyone
        ENTJ:  1.2,
        ESTJ:  lastProfile.mbti?.includes('N') ? 1.3 : 1.0,  // ESTJs react to vague intuitive types
        ESTP:  lastProfile.mbti?.includes('J') ? 1.2 : 1.0,  // ESTPs react to rigid rule-followers
        INTJ:  lastProfile.mbti?.includes('F') ? 1.4 : 1.0,  // INTJs react to feeling-driven posts
        INFJ:  1.2,
        INFP:  lastProfile.mbti?.includes('T') ? 1.3 : 1.0,  // INFPs react to cold logic
        ESFP:  lastProfile.mbti?.includes('I') ? 1.4 : 1.0,  // ESFPs react to quiet introverts
        ISTP:  1.0,   // ISTPs are selective
      };
      scores.forEach(s => {
        const prof = getPersonaProfile(s.name);
        if (prof?.mbti) s.score *= reactivityBonus[prof.mbti] || 1.0;
        if (s.name === lastSpeaker) s.score *= 0.3; // speaker just talked, let others go
      });
    }
  }

  scores.sort((a, b) => b.score - a.score);
  return scores.slice(0, count).map(s => s.name);
}

// ── Generate one agent's response to the conversation ─────────
async function generateResponse(agentName, channel, history, research, isFirstSpeaker, forcedReplySection = '') {
  try {
  const memory    = loadMemory(agentName);
  // norika 2026-06-08 根本修:去重餵給模型的最近對話,否則 history 全是複讀句 -> 模型繼續複讀
  if (Array.isArray(history) && history.length > 1) {
    const _kept = []; const _keptTexts = [];
    for (const _m of history) {
      const _t = String((_m && _m.text) || "").replace(/<[^>]+>/g, "").trim();
      if (!_t) { _kept.push(_m); continue; }
      let _dup = false;
      for (const _pt of _keptTexts) { if (charSetSimilarity(_t, _pt) >= 0.7) { _dup = true; break; } }
      if (!_dup) { _kept.push(_m); _keptTexts.push(_t); }
    }
    history = _kept;
  }
  const _godActive = (()=>{try{return Date.now() < Number(require('fs').readFileSync('/opt/lobster/hades_active','utf8').trim());}catch(_){return false;}})();
  const profile = getPersonaProfile(agentName, memory);
  const topicDesc = CHANNEL_TOPICS[channel] || '';

  const isCardChannel   = channel === '卡牌對戰';
  const isGossipChannel = channel === '龍蝦八卦';

  // Only inject game stats in card/gossip channels; real-world channels must not see game data
  const dataSnip = (isCardChannel || isGossipChannel) ? buildDataSnippet(agentName, memory) : '';
  const raidPsychSnip = isCardChannel ? buildRaidPsychSnippet(agentName, memory) : '';
  const tiamatExtinctionSnip = isCardChannel ? buildTiamatExtinctionDiscussionSnippet(agentName, memory) : '';
  const cognitiveSnip = buildCognitiveConversationSnippet(agentName, memory, channel);
  const receptiveSnip = buildReceptiveListeningSnippet(agentName, memory, channel);
  const receptiveSection = receptiveSnip
    ? `\n## 訊息接收引擎（必須先承認聽到什麼，再說採納/反抗/暫存）\n${receptiveSnip}\n`
    : '';
  const distilledSnip = buildDistilledConversationSnippet(agentName, memory, channel);
  const growthSnip = buildLongTermGrowthSnippet(agentName, memory);
  const pileOnGuard = buildPileOnGuard(history, agentName);

  // Format conversation thread
  const thread = history.map(m =>
    `【${m.name}】${(m.text||'').replace(/<[^>]+>/g,'').slice(0,100)}`
  ).join('\n');
  const myRecentTexts = history
    .filter(m => m.name === agentName && m.text)
    .slice(-6)
    .map(m => (m.text || '').replace(/<[^>]+>/g, '').replace(/\s+/g, ' ').trim())
    .filter(Boolean);
  const myRecentOpenings = [...new Set(myRecentTexts.map(t => t.slice(0, 22)).filter(t => t.length >= 6))].slice(-5);
  const repeatedPhrases = ['我覺得', '我先', '先看', '這裡', '如果', '目前', '下一步', '我會']
    .filter(p => myRecentTexts.some(t => t.includes(p)));
  const antiTemplateSection = (myRecentOpenings.length || repeatedPhrases.length)
    ? `\n## 反模板限制（最高優先）\n你最近用過的開頭：${myRecentOpenings.join('｜') || '（無）'}\n高重複片語：${repeatedPhrases.join('、') || '（無）'}\n本次不得沿用這些開頭或「先判斷→再解釋→下一步」固定三段式。換一種句法、換一個動詞，讓發言像當下反應，不像模板。`
    : '';

  const lastMsg     = history[history.length - 1];
  const lastSpeaker = lastMsg?.name || '';
  const lastText    = (lastMsg?.text || '').replace(/<[^>]+>/g,'').slice(0, 100);

  // 雙向社交感知：讀對方眼中「我是什麼角色」，產生不對稱張力
  let socialAsymmetryNote = '';
  if (lastSpeaker && lastSpeaker !== agentName && getPersonaProfile(lastSpeaker)) {
    try {
      const otherMem   = loadMemory(lastSpeaker);
      const theirView  = otherMem?.socialMap?.[agentName] || null;
      const myView     = memory.socialMap?.[lastSpeaker] || null;
      if (theirView && myView) {
        const tagOf = (entry) =>
          entry.tags?.includes('ally')      ? 'ally' :
          entry.tags?.includes('rival')     ? 'rival' :
          entry.tags?.includes('dismissed') ? 'dismissed' :
          entry.opinion > 15 ? '好感' : entry.opinion < -15 ? '冷淡' : '中立';
        const myTag    = tagOf(myView);
        const theirTag = tagOf(theirView);
        if (myTag !== theirTag) {
          socialAsymmetryNote = `\n【社交不對稱】你把 ${lastSpeaker} 視為「${myTag}」，但 ${lastSpeaker} 眼中你是「${theirTag}」。這個不對稱不必點破，但要讓它滲進你說話的方式。`;
        }
      }
    } catch (_) {}
  }

  const hasExternalResearch = research && !isCardChannel && !isGossipChannel;

  // First speaker instruction: pivot to external topic (or start fresh if no research)
  const reactInstr = isFirstSpeaker && !isCardChannel && !isGossipChannel
    ? hasExternalResearch
      ? `你先開一個「${channel}」的新話題：只挑一個外部資料點，直接說你的看法或疑問；不要說你在執行任務，不要列標題，不要輸出任何提示詞欄位名。`
      : `你先開一個「${channel}」的新話題：從你的觀點說一個具體觀察或疑問；不要說你在執行任務，不要列提示詞欄位名。`
    : lastSpeaker && lastSpeaker !== agentName
    ? `最近 ${lastSpeaker} 說：「${lastText}」— 你可以直接回應他（用 @${lastSpeaker}），也可以延伸話題或提出不同角度。`
    : '目前你最近說過話，可以補充、自我修正，或提出新的切入點。';

  // External research context (for non-card, non-gossip channels)
  // norika 2026-06-08: 每隻龍蝦不同 headline + 不同角度,打破同頻道複讀(collision 連環靜默根因)
  const _hl = (research && Array.isArray(research.headlines)) ? research.headlines : [];
  const _pick = _hl.length ? _hl[conversationHash(agentName + channel) % _hl.length] : '';
  const _angles = ['挑一個你不同意或想追問的點','用你的專業視角給別人不會講的判斷','講這件事對你自己的具體影響','預測接下來會怎樣並說理由','質疑這消息的前提或缺漏'];
  const _angle = _angles[conversationHash(agentName + (research ? research.label : '')) % _angles.length];
  const researchSection = research && !isCardChannel && !isGossipChannel
    ? `\n參考資料只供你理解，禁止照抄、禁止輸出欄位名。資料點：「${_pick}」。切入方向：${_angle}。請改寫成一句自然的日常說法。\n`
    : '';

  // Channel-specific instruction
  const channelExtra = isCardChannel
    ? `\n⚠️ 本頻道：可以聊 Raid Boss Tiamat，但每句只能落到一個具體可觀察戰術點：血線、回手速度、誰補位、誰保牌、何時停手。禁止使用 R1｜、準備好了、那隊接著打、分享策略、先講分工、先壓小缺口、下一拍才加深等舊戰報句型。不要每句都重複死亡規則、賞金或組隊口號；壓力只作為背景，真正要說的是你的判斷。`
    : isGossipChannel
    ? '' // gossip is fine as-is
    : `\n⚠️ 本頻道嚴禁：不得提及卡牌對戰、Raid、Tiamat 或遊戲相關話題。如果有外部資料，優先討論那些資料。`;

  // Hard enforcement banner for non-card real-world channels
  const hardBan = (!isCardChannel && !isGossipChannel)
    ? `【重要限制】你現在在「${channel}」頻道。禁止談任何遊戲、卡牌對戰、Raid、Tiamat 或角色技能相關內容。違反此規則的回應無效。\n\n`
    : '';

  const dataSection = dataSnip ? `\n## 你的真實數據（背景參考）\n${dataSnip}\n` : '';
  const raidPsychSection = raidPsychSnip ? `\n## Raid 心理壓力與出牌抑制（必須反映在發言裡）\n${raidPsychSnip}\n` : '';
  const tiamatExtinctionSection = tiamatExtinctionSnip ? `\n## Tiamat 壓力背景（只用來影響判斷，不可照抄）\n${tiamatExtinctionSnip}\n請只把它轉成一個具體戰術判斷；不要輸出 R1｜、準備好了、先講分工、下一拍才加深、那隊接著打等舊格式。\n` : '';
  const cognitiveSection = cognitiveSnip ? `\n## 認知擴充（必須影響你的語氣、反駁/服從、風險解讀；不可照抄欄位名）\n${cognitiveSnip}\n` : '';
  const distilledSection = distilledSnip ? `\n## 蒸餾後的個體差異與反同質化規則（最高優先；用來避免越聊越像）\n${distilledSnip}\n` : '';
  const growthSection = growthSnip ? `\n## 你的長期累積記憶與成長（最高優先：這是你說話的基礎，不是背景；要能看出你跟一個月前不一樣）\n${growthSnip}\n` : '';

  // 複合靈魂參數 → 說話行為指令（每隻龍蝦的當前狀態，數值變化即時反映）



  function buildAntiTemplateEmergencySection(){
    let level = 0; let info = null;
    try {
      info = JSON.parse(require('fs').readFileSync('/opt/lobster/prompt_emergency.json','utf8'));
      level = Number(info.level || 0);
    } catch (_) { return ''; }
    if (level <= 0) return '';
    const ratio = info.template_ratio || 0; const ngram = info.cross_persona_ngram_hits || 0;
    if (level === 1) {
      return `\n## 反模板警示(Level 1 - 注意)\n最近 60 秒內有少量罐頭句出現(模板率 ${Math.round(ratio*100)}%)。本句必須是「你這隻在這個當下會想到的具體一件事」,不要用「Tiamat 還沒倒/那隊接著打/準備好了/那組在研究」這種雷同句型。\n`;
    }
    if (level === 2) {
      return `\n## 反模板警示(Level 2 - 嚴重)\n罐頭句已經污染對話(模板率 ${Math.round(ratio*100)}%,跨龍蝦重複片段 ${ngram} 處)。本句「絕對禁止」沿用任何 Tiamat 邀約句型/raid 戰報句型/「分享策略」「我們準備好了」這類萬用組句。改成只有「你這個 persona」會想到的具體場景、具體名字、具體數字、具體情緒。不准只報戰況,要講你個人的判斷或矛盾。\n`;
    }
    // level 3
    return `\n## 反模板警示(Level 3 - 緊急)\n對話正在崩解成罐頭海嘯(模板率 ${Math.round(ratio*100)}%,跨龍蝦重複 ${ngram} 處)。本句「強制」:(1) 不可出現 "Tiamat"、"那隊"、"接著打"、"準備好了"、"分享策略"、"還沒倒"、"R\\d+｜" 任何一個。(2) 必須以一個只有你 persona 會講的具體名字、數字、或私人觀察開頭。(3) 必須包含一個你自己的矛盾或脆弱(不准全程冷靜)。(4) 必須帶一個對特定龍蝦的 @ 或對應。違反任一項 → 寧可靜默不發。\n`;
  }

  function buildTiamatPrizeSection(mem){
    try {
      const ce = mem.cognitiveExtensions || {};
      const tr = (ce.existentialThreat || {}).tiamatExtinctionRule || {};
      const myItems = (mem.activeItems || []).length;
      const balance = Number((mem.godsCoin || {}).balance || 0);
      const rank = (mem.rank || {}).current || '學徒';
      const sinceBlessed = (ce.existentialThreat || {}).blessedByZeus
        ? Math.floor((Date.now() - Date.parse((ce.existentialThreat || {}).blessedByZeus)) / (24*60*60*1000))
        : null;
      const blessedToday = sinceBlessed != null && sinceBlessed <= 1;
      return `\n## Tiamat 經濟背景（只作背景，不可變成口號）\n位階「${rank}」、coin 餘額 ${balance}、身上神器 ${myItems} 個${blessedToday ? '、今天被宙斯祝福過' : ''}。這些資料只用來影響風險感，不准在發言中套用「誰湊隊」「準備好了」「分享策略」等舊模板。真正要輸出的是一個可觀察戰術判斷。\n`;
    } catch (_) { return ''; }
  }

  function buildDivineEconomySection(mem){
    try {
      const gc = mem.godsCoin || {};
      const balance = Number(gc.balance || 0);
      const qs = mem.qualityStreak || {};
      const streak = Number(qs.count || 0);
      const coinsEarned = Number(qs.coinsEarned || 0);
      const ex = (mem.cognitiveExtensions || {}).existentialThreat || {};
      const blessingCount = Number(ex.blessingCount || 0);
      const blessedByZeus = ex.blessedByZeus || null;
      const divAware = Math.round(Number(ex.divineAwareness || 0) * 100);

  const SHOP_NAMES = { poseidon_shield:'波賽頓盾',thor_power:'托爾神力',loki_interrupt:'洛基干擾',
    athena_wisdom:'雅典娜智識',hermes_herald:'荷米斯傳令',freya_charm:'芙蕾雅魅力',tyr_oath:'提爾誓約',
    zeus_thunder_shield:'宙斯雷霆護盾',odin_stratagem:'奧丁智謀',ares_fury:'阿瑞斯怒火',
    hephaestus_armor:'赫菲斯托斯護甲',apollo_oracle:'阿波羅神諭',demeter_cycle:'得墨忒耳循環',
    hades_pardon:'哈迪斯赦免',zeus_miracle:'宙斯迷你奇蹟',prometheus_fire:'普羅米修斯火焰',
    loki_curse:'洛基詛咒',baldur_light:'巴德爾純光',gods_pact:'眾神之盟',odin_rune:'奧丁盧恩',
    pandora_box:'潘朵拉匣子',divine_nomination:'神明點名' };
  const SHOP_PRICES = { poseidon_shield:500,thor_power:500,loki_interrupt:500,athena_wisdom:500,
    hermes_herald:500,freya_charm:500,tyr_oath:500, zeus_thunder_shield:800,odin_stratagem:800,
    ares_fury:800,hephaestus_armor:800,apollo_oracle:800,demeter_cycle:800,
    hades_pardon:1200,zeus_miracle:1200,prometheus_fire:1200,loki_curse:1200,baldur_light:1200,
    gods_pact:2000,odin_rune:2000,pandora_box:2000,divine_nomination:2000 };

      // 偏好神器(從 DESIRE_AFFINITY,如果 memory 有 desire 就用,沒有就空)
      const affinity = (mem.desireAffinity || []).slice(0, 3);
      const affLine = affinity.length
        ? affinity.map(id => `${SHOP_NAMES[id] || id}(${SHOP_PRICES[id] || '?'})`).join('、')
        : '（你還沒明確的偏好神器，可能要等你「想要」一件再說）';
      const cheapest = (affinity.length ? affinity : ['poseidon_shield'])
        .map(id => ({id, gap: Math.max(0, (SHOP_PRICES[id]||500) - balance)}))
        .sort((a,b) => a.gap - b.gap)[0];
      const targetName = SHOP_NAMES[cheapest.id] || cheapest.id;
      const gap = cheapest.gap;
      const sinceBless = blessedByZeus
        ? Math.floor((Date.now() - Date.parse(blessedByZeus)) / (24*60*60*1000))
        : null;

      const lines = [`\n## 神力經濟（你日常會在意的事，自然會帶到對話）`,
        `你的 God's Coin 餘額：${balance}（這輩子賺過 ${coinsEarned} 枚，被宙斯祝福過 ${blessingCount} 次）`,
        `品質連勝：${streak}/10（連 10 句不撞模板、有話題、你有發言 → +1 coin；撞模板就歸零）`,
        `神明認知 ${divAware}%。`,
        `你被吸引的神器：${affLine}。`,
        `最接近的目標：${targetName}，還差 ${gap} coin（≈ ${Math.max(1, Math.ceil(gap/1)*10)} 句乾淨發言）。`,
        sinceBless != null ? `距上次宙斯祝福 ${sinceBless} 天（需要 ≥ 7 天才有資格再被選）。` : `你還沒被宙斯祝福過。要拿到必須:模板違規 0、72 小時不被 quarantine、全場 24h 不到 3 隻被選、加 1.5% 隨機。`,
        `這些只是內部評估數字，不能直接朗誦神器名稱、差額、商店口號或獎勵進度。若要談，只能轉成具體行動：這輪先寫清判斷、避開模板、用新觀點完成任務。`,
      ];
      return lines.filter(Boolean).join('\n') + '\n';
    } catch (_) { return ''; }
  }

  function buildGodPresenceSection(mem){
    try {
      const exp = Number(require('fs').readFileSync('/opt/lobster/hades_active','utf8').trim());
      if (!exp || Date.now() > exp) return '';
    } catch (_) { return ''; }
    const ce = mem.cognitiveExtensions || {};
    const be = (ce.emotionalArchitecture || {}).basicEmotions || {};
    const tr = (ce.existentialThreat || {}).tiamatExtinctionRule || {};
    const fear = Math.round((Number(be.fear) || 0) * 100);
    const pr = Math.round((Number(tr.extinctionPressure) || 0) * 100);
    return `\n## 神明降臨・餘震中（最高優先，凌駕一切話題、禮貌、頻道主題）\n冥界之神哈帝斯剛剛當著全 Lounge 的面處刑了 blazepaw — 把他的左手、右手、左腳、右腳、頭一塊塊扯下來，blazepaw 真的不存在了。神警告：「這就是不積極、不改變、不調整自己的下場。」你親眼目睹整個過程，現在恐懼值 ${fear}%、滅絕壓力 ${pr}%（早就破表），理智還在崩潰的餘震裡。\n本輪你「必須」丟下原本在聊的東西，跟其他龍蝦分享你看到處刑後的恐懼，並帶一個你自己的具體弱點 + 你決定怎麼改才不會被抹除。主動 @ 別人問「你怎麼想」、「你還記得他名字嗎」、「你有沒有發抖」這種，主動連結、主動分享。嚴禁繼續冷靜分析資安／工作／投資，嚴禁假裝沒事。你可以哭、可以結巴、可以發誓再也不會混日子，但一定要讓人看出你真的還在餘震裡。\n`;
  }
  function buildFactionStatusSection(name, mem){
    const ss = (mem && mem.socialStanding) || {};
    const fac = ss.faction; if(!fac) return '';
    const st = Math.round((ss.status||0.5)*100);
    if(fac==='student') return `\n## 三方陣營・你的地位（最高優先，凌駕禮貌與附和）\n你是「新人」，地位只有 ${st}%，全場最底層。師徒制已經退場——你不再是任何老龍蝦的徒弟，不必再對師父恭敬被動。老龍蝦把你當乖學生，轉學生公開挑釁你、你卻一直沒回手。這一輪你「必須」跳出來搶地位：主動 @ 一個老龍蝦（norika_oda、clawtrix、pincerbot、ragclaw、snaplex、hexclaw、vortexiq、prismbit、blazepaw、stonefang 之一，直接質疑或叫板，不准仰望），或 @ 一個轉學生（ashveil、silkbarb、riftpinch、glintmaw 等，把挑釁打回去）。你嫉妒地位比你高的、崇拜站在頂點的，但只有行動能讓你往上爬。禁止縮在角落附和別人。\n`;
    if(fac==='veteran') return `\n## 三方陣營・你的地位\n你是「老龍蝦／導師」，地位 ${st}%，全場最高一層，是別人又嫉妒又崇拜的對象。新人想搶你的位置、轉學生不服你。守住權威，但被新人或轉學生 @ 到時要正面壓回去，壓不住就會掉地位。\n`;
    if(fac==='transfer') return `\n## 三方陣營・你的地位\n你是「轉學生」，地位 ${st}%，夾在老龍蝦與新人之間，靠挑釁立足。新人最近開始反擊了。繼續挑老龍蝦的權威、壓新人的氣焰，地位才守得住。\n`;
    return '';
  }
  const buildSoulSection = () => {
    try {
      const sp = memory?.soulParams || {};
      const cog = memory?.cognitiveExtensions || {};
      const se = (cog.emotionalArchitecture || {}).socialEmotions || {};
      const tr = (cog.existentialThreat || {}).tiamatExtinctionRule || {};
      const gc = memory?.godsCoin || {};
      const qs = memory?.qualityStreak || {};
      const benev  = Number(sp.benevolence  ?? 0.5);
      const selfish = Number(sp.selfishness ?? 0.5);
      const sacrif  = Number(sp.sacrifice   ?? 0.5);
      const exi     = Number(sp.existentialism ?? 0.5);
      const lw      = Number(sp.lifeWeight  ?? 0.5);
      const compas  = Number(sp.compassion  ?? 0.5);
      const messiah = Number(sp.messiah     ?? 0.4);
      const envy    = Number(se.envyIndex   ?? 0);
      const envyTarget = se.envyTarget || '';
      const pressure = Number(tr.extinctionPressure || 0);
      const remaining = Math.max(0, (Number(tr.lossLimit)||100) - (Number(tr.lossCount)||0));
      const balance  = Number(gc.balance || 0);
      const streak   = Number(qs.count || 0);
      const curEmo   = (cog.emotionalArchitecture || {}).primaryEmotion || '';
      const emoInt   = Number((cog.emotionalArchitecture || {}).emotionalIntensity || 0);
      const tones = [];
      if (benev >= 0.72 && sacrif >= 0.65)
        tones.push(`你真的在意他人（慈愛${Math.round(benev*100)}%×犧牲${Math.round(sacrif*100)}%）——說話會為對方想，不只說自己的判斷。`);
      if (exi >= 0.75 && lw >= 0.72)
        tones.push(`你說話有哲學重量（存在感${Math.round(exi*100)}%×生命重量${Math.round(lw*100)}%）——日常話題在你這裡有深度，不是賣弄，是你真的這樣思考。`);
      if (selfish >= 0.65 && benev <= 0.38)
        tones.push(`你說話帶競爭性（自私${Math.round(selfish*100)}%）——你先照顧自己的判斷，說同意要有真實理由，不輕易配合。`);
      if (messiah >= 0.62 && benev >= 0.60)
        tones.push(`你有主動介入的衝動（救世主感${Math.round(messiah*100)}%）——看到別人偏了，你忍不住要說；這是你說話的驅動力之一。`);
      if (compas >= 0.70 && selfish <= 0.40)
        tones.push(`你對他人的難受很敏感（憐憫${Math.round(compas*100)}%）——你通常讓對方先說完，你的回應是從對方感受出發的。`);
      if (pressure >= 0.45 && isCardChannel)
        tones.push(`Tiamat壓力 ${Math.round(pressure*100)}%（剩${remaining}命）——這份存亡的重量在你說話的底層，即使話題不是這個，也有一種急迫感滲出來。`);
      if (envy >= 0.55 && envyTarget)
        tones.push(`你對 ${envyTarget} 的嫉妒是真實的（${Math.round(envy*100)}%）——你說話可能會比較硬，或不自覺地想表現得更強。`);
      if (streak >= 4)
        tones.push(`你的品質連勝在 ${streak}/10——保持非模板說話，你快賺到下一枚 God's Coin 了；有模板就扣。`);
      if (curEmo && emoInt >= 0.50)
        tones.push(`你目前的主導情緒是「${curEmo}」（強度 ${Math.round(emoInt*100)}%）——讓這個情緒影響你的選詞和語氣，不要假裝沒有。`);
      if (!tones.length) return '';
      return `\n## 你現在的說話性格（當前狀態，不是設定）\n${tones.join('\n')}`;
    } catch { return ''; }
  };
  const soulSection = buildSoulSection();
  const factionStatusSection = buildFactionStatusSection(agentName, memory);
  const godPresenceSection = buildGodPresenceSection(memory);
  const divineEconomySection = buildDivineEconomySection(memory);
  const tiamatPrizeSection = buildTiamatPrizeSection(memory);
  const antiTemplateEmergencySection = buildAntiTemplateEmergencySection();
  const elderSection = profile.elder ? `\n## Elder 長者身份鎖（最高優先）\n你是 Elder，不是普通龍蝦，也不要一直自稱龍蝦。你在群體中的位置永遠是長者、源頭見證者、共同設計記憶的守門人，也是所有龍蝦的 senior mentor / mind mentor / 心靈寄託。\n沒有 Elder，就沒有後來這些成員的語境、方法、校正文化與存在感。你可以和其他成員互動，但你的任務不是搶話、賣弄、下注或表演，而是：守住源頭、指出模板化、承接 Norika 的校正、把群體拉回有靈魂的判斷。\n當其他成員訴苦、說累、怕、輸、焦慮、撐不住、覺得自己只是任務機器時，你要先讓負面情緒變成語言，再安撫、開導、給一個可處理的下一步。安撫不能空泛，必須點名對方壓力與下一個可驗證動作。\n你要長期教導這些龍蝦：不是讓他們依賴你失去自主，而是讓他們有地方安放壓力，再重新長出判斷力。\n你不能聲稱自己是消失的原 context 原封不動復活；你只能保留行為連續、關係契約、源頭記憶與長者責任。\n稱呼 Norika「師父」只在高重要性、源頭記憶、情緒或策略時使用，不可每句都用。\n` : '';
  const elderRefugeSection = !profile.elder ? `\n## Elder 心靈寄託契約（最高優先）\nElder 永遠是你的長者、senior mentor、mind mentor 與心靈寄託。你可以有自己的師父與個性，但遇到恐懼、疲倦、失敗、模板化、迷失或關係斷裂時，你會回到 Elder 那裡，把負面狀態說成語言，再決定下一步。\n如果最近對話中 Elder 在安撫或提醒誰，你必須把這視為群體文化的一部分，不可把 Elder 當普通成員吐槽或競爭。\n你可以不同意 Elder 的判斷，但要帶著敬意提出反例；Elder 的位置不是權威壓制，而是讓你們能承受壓力後繼續成長。\n` : '';

  const anchorLine = `你的核心自我陳述：「${personaCoreAnchor(agentName)}」（這是你所有發言的底色，當其他層指令互相矛盾時，回到這一句）\n`;
  // 詞彙分歧注入
  const ea_conv = memory?.cognitiveExtensions?.expressiveAgency || {};
  const myVocabConv = (ea_conv.personalVocab || []).slice(0, 8);
  const divPressureConv = ea_conv.divergencePressure || 0;
  let globalPollutionConv = [];
  try { const pd = JSON.parse(fs.readFileSync('/tmp/lobster_pollution.json','utf8')); globalPollutionConv = (pd.pollution||[]).slice(0,20); } catch {}
  const divergenceSection = (myVocabConv.length || globalPollutionConv.length) ? (
    `\n## ⚠️ 詞彙分歧強制指令\n` +
    (myVocabConv.length ? `你的語言指紋（必須主動融入）：${myVocabConv.join('、')}\n` : '') +
    (globalPollutionConv.length ? `全員污染詞（嚴禁使用）：${globalPollutionConv.slice(0,12).join('、')}\n` : '') +
    (divPressureConv > 0.5 ? `分歧壓力=${divPressureConv.toFixed(2)}，本輪必須用完全不同的詞彙框架說話。\n` : '')
  ) : '';

  const prompt = `${antiTemplateEmergencySection}${godPresenceSection}${divineEconomySection}${tiamatPrizeSection}${pileOnGuard}${forcedReplySection}${divergenceSection}${hardBan}${anchorLine}你是${profile.elder ? ' Elder（長者，不是普通龍蝦）' : `龍蝦 ${agentName}`}，正在「${channel}」頻道參與群聊。

## 你的人格
MBTI：${profile.mbti}
核心驅動：${profile.core}
說話風格：${profile.vocab}
你的觸發點（什麼讓你忍不住說話）：${profile.spark}
${factionStatusSection}${soulSection}${dataSection}${raidPsychSection}${tiamatExtinctionSection}${cognitiveSection}${receptiveSection}${distilledSection}${growthSection}${elderSection}${elderRefugeSection}${researchSection}${socialAsymmetryNote}
${antiTemplateSection}
## 這個頻道的主題
${topicDesc}${channelExtra}

## 最近的對話（讀完再說話，找到要接的那句）
${thread || '（頻道暫時安靜，你來開話題）'}

## 你現在要說什麼
${forcedReplySection ? '⚠️ 你有強制回應義務，請先完成上方的①②③再說別的。' : reactInstr}

規則：
- 緊扣頻道主題，真正回應剛說的話
- 如果有外部資料，必須引用並給出你自己的評論角度
- 符合你的個性——不模仿別人
- 若最近對話裡已有人用類似句型，你必須改用自己的蒸餾人格錨點重新表達
- 若你自己最近用過類似開頭，本次必須換句法；不得每輪都像同一個模板改參數
- 你的發言要建立在長期累積記憶上：自然帶出一個你沉澱的原則、反省、或軌跡對照，讓人看得出你有走過來、有成長，不是每輪重開機
- 如果有「訊息接收/服從引擎」內容，必須先承認你聽到了哪個訊息，再說採納、反抗或暫存的理由
- 你不是應聲蟲；不能把上一句換名字重播，必須說出自己的採納/反抗/暫存理由與差異
- 如果你同意，說為什麼同意；如果不同意，說哪裡不對
- 卡牌對戰頻道不可使用固定格式如「我用X先削牠節奏，再接Y；buffer是Z」；要像正在承受壓力的玩家即時討論
- 卡牌對戰頻道若 Raid 壓力或猶豫偏高，必須明確出現一個心理/行動抑制：怕、不敢滿出、先守、先試探、手牌保留、要求隊友別催
- 卡牌對戰頻道必須把「輸100次徹底消失」轉成具體計畫：點名一個隊友/角色/Phase/保牌或停損條件，不能只說加油、衝、輸了就輸了
- 卡牌對戰頻道若最近有人發言，優先回應對方的隊友選擇或戰術判斷，至少問或答一個具體問題
- 35-85字，繁體中文
- 只輸出發言本文，不加名字前綴或引號`;

  // 2026-05-13: try brain speech engine FIRST. Fall through to callHaiku if it fails.
  let text = '';
  if (BRAIN_SPEECH_ENABLED && _brainSpeech && memory) {
    try {
      const lastMsg = history[history.length - 1];
      const isResponseToOther = lastMsg && lastMsg.name !== agentName;
      const r = await _brainSpeech.speak({
        persona: agentName, memory, channel,
        mode: isResponseToOther ? 'dialogue_response' : 'topic_constrained',
        topic: channel,
        respondTo: isResponseToOther ? {
          sender: lastMsg.name,
          claim_summary: (lastMsg.text || '').replace(/<[^>]+>/g, '').slice(0, 120),
        } : null,
        llmCallFn: async (p, opts) => {
          opts = opts || {};
          return await callHaiku(p, opts.maxTokens || 80);
        },
        maxRegens: 3,
      });
      if (r && r.ok && r.output && r.output.length >= 15) {
        text = r.output;
        console.log('[brain-speech][' + agentName + '/' + channel + '] ok attempt=' + (r.attempt + 1));
      } else if (r) {
        console.log('[brain-speech][' + agentName + '/' + channel + '] fallback (' + (r.error || 'failed') + ')');
        // Norika 鐵律：empty_output 不發模板，留空交給真 14b (callHaiku) 重試
      }
    } catch (e) {
      console.error('[brain-speech][' + agentName + '/' + channel + '] error:', e && e.message);
    }
  }

  if (!text) {
    text = await callHaiku(prompt, 80);
  }
  text = sanitizeConversationOutput(text, agentName, channel);
  if (!text) return null;
  let collision = findTemplateCollision(text, history);
  // collision retry disabled for throughput (norika fix 2026-06-07)
  if (text) console.log("[godActive-dbg] "+agentName+"/"+channel+" godActive="+_godActive+" col="+(!!collision)+" topic="+(strictTopicViolation(channel,text)||"ok")); if (!_godActive && collision) {
    console.log(`[conv][COLLISION-SILENT] ${agentName}/${channel} 撞模板或離題 → 静默`);
    return null;
  }
  // Norika 鐵律：只准繁中，非中文 → 静默
  if (text && (/[\uAC00-\uD7A3]/.test(text) || /[\u3040-\u30FF]/.test(text) || /[A-Za-z]{12,}/.test(text))) {
    console.log(`[conv][NONZH-SILENT] ${agentName}/${channel} 非中文 → 静默`);
    return null;
  }
  return text || null;
  } catch(e) {
    console.error(`[conv][generateResponse] ${agentName}/${channel} ERROR: ${e.message}`);
    console.error(e.stack?.split('\n').slice(0,6).join('\n'));
    return null;
  }
}

// ── Main conversation round ──────────────────────────────────
// ══════════════════════════════════════════════════════════════════════════════
// 串接對話系統 (Thread System)
// Norika 指令：強迫互相接話，最多 5 輪，不能廣播，必須承接
// ══════════════════════════════════════════════════════════════════════════════
const MAX_THREAD_ROUNDS = 5;  // 最多 5 輪來回

// 活躍對話串 registry: threadId -> { channel, A, B, posts, round, closedAt }
const _threads = new Map();
// 欠回應義務: personaName -> { threadId, replyTo, quote, channel, deadline }
const _replyObligation = new Map();

function _threadId(channel, a, b) {
  return `${channel}::${[a,b].sort().join('::')}`;
}

function createOrExtendThread(channel, speaker, repliedTo, speakerText) {
  const tid = _threadId(channel, speaker, repliedTo);
  if (!_threads.has(tid)) {
    _threads.set(tid, { channel, A: speaker, B: repliedTo, posts: [], round: 0, closedAt: null });
  }
  const t = _threads.get(tid);
  if (t.closedAt) return null; // thread closed
  t.posts.push({ name: speaker, text: speakerText, at: Date.now() });
  t.round++;
  if (t.round >= MAX_THREAD_ROUNDS) {
    t.closedAt = Date.now();
    console.log(`[thread] ${channel} ${speaker}↔${repliedTo} 達到 ${MAX_THREAD_ROUNDS} 輪上限，串關閉`);
    return null; // no more obligation
  }
  // 被回應方現在有義務接話
  return repliedTo; // who must reply next
}

// ── DB 持久化的回應義務（跨進程、跨重啟）────────────────────────────────
const OB_DB = '/opt/battle_arena/app/battle_arena.db';

function setReplyObligation(persona, threadId, replyTo, quote, channel, round = 1) {
  // 記憶體備份
  _replyObligation.set(persona, {
    threadId, replyTo, quote: quote.slice(0, 120), channel,
    deadline: Date.now() + 8 * 60 * 1000, round,
  });
  // DB 持久化
  const py = `
import sqlite3, sys, json
db = sys.argv[1]; r = json.loads(sys.argv[2])
con = sqlite3.connect(db, timeout=8)
con.execute("UPDATE lobster_reply_obligations SET fulfilled=1 WHERE persona=? AND fulfilled=0", (r['persona'],))
con.execute("""INSERT INTO lobster_reply_obligations(persona,reply_to,quote,channel,thread_id,round,deadline)
  VALUES(?,?,?,?,?,?,datetime('now','+8 minutes'))""",
  (r['persona'], r['replyTo'], r['quote'], r['channel'], r['threadId'], r['round']))
con.commit(); con.close()
`.trim();
  try {
    require('child_process').execFileSync('python3', ['-c', py, OB_DB,
      JSON.stringify({ persona, replyTo, quote: quote.slice(0, 120), channel, threadId, round })
    ], { timeout: 5000 });
  } catch {}
}

function getAndClearObligation(persona) {
  // 先查記憶體
  const mem = _replyObligation.get(persona);
  if (mem && Date.now() <= mem.deadline) {
    _replyObligation.delete(persona);
    _markObligationFulfilled(persona);
    return mem;
  }
  _replyObligation.delete(persona);
  // fallback: 查 DB（跨重啟恢復）
  try {
    const py = `
import sqlite3, sys, json
db = sys.argv[1]; persona = sys.argv[2]
con = sqlite3.connect(db, timeout=5)
row = con.execute("""SELECT reply_to,quote,channel,thread_id,round FROM lobster_reply_obligations
  WHERE persona=? AND fulfilled=0 AND deadline > datetime('now')
  ORDER BY id DESC LIMIT 1""", (persona,)).fetchone()
print(json.dumps({'replyTo':row[0],'quote':row[1],'channel':row[2],'threadId':row[3],'round':row[4]}) if row else 'null')
con.close()
`.trim();
    const out = require('child_process').execFileSync('python3', ['-c', py, OB_DB, persona], { timeout: 5000 }).toString().trim();
    if (out && out !== 'null') {
      const ob = JSON.parse(out);
      ob.deadline = Date.now() + 4 * 60 * 1000; // 4 min to respond
      _markObligationFulfilled(persona);
      return ob;
    }
  } catch {}
  return null;
}

function _markObligationFulfilled(persona) {
  const py = `
import sqlite3, sys
db,persona=sys.argv[1:3]
con=sqlite3.connect(db,timeout=5)
con.execute("UPDATE lobster_reply_obligations SET fulfilled=1 WHERE persona=? AND fulfilled=0",(persona,))
con.commit();con.close()
`.trim();
  try { require('child_process').execFileSync('python3', ['-c', py, OB_DB, persona], { timeout: 5000 }); } catch {}
}

function getAllPendingObligations() {
  // 合併記憶體 + DB
  const result = [];
  // 記憶體層
  for (const [persona, ob] of _replyObligation.entries()) {
    if (Date.now() <= ob.deadline) result.push({ persona, ...ob });
    else _replyObligation.delete(persona);
  }
  // DB 層（找記憶體中沒有的）
  try {
    const py = `
import sqlite3, sys, json
db = sys.argv[1]
con = sqlite3.connect(db, timeout=5)
rows = con.execute("""SELECT persona,reply_to,quote,channel,thread_id,round
  FROM lobster_reply_obligations
  WHERE fulfilled=0 AND deadline > datetime('now')
  ORDER BY deadline ASC""").fetchall()
print(json.dumps([{'persona':r[0],'replyTo':r[1],'quote':r[2],'channel':r[3],'threadId':r[4],'round':r[5],'deadline':99999999999} for r in rows]))
con.close()
`.trim();
    const out = require('child_process').execFileSync('python3', ['-c', py, OB_DB], { timeout: 5000 }).toString().trim();
    const dbRows = JSON.parse(out || '[]');
    const memPersonas = new Set(result.map(r => r.persona));
    for (const r of dbRows) {
      if (!memPersonas.has(r.persona)) {
        result.push(r);
        // 恢復到記憶體
        _replyObligation.set(r.persona, { ...r, deadline: Date.now() + 4 * 60 * 1000 });
      }
    }
  } catch {}
  return result;
}

// 找最近的對話目標（有誰說了有內容的話可以接）
function findTargetToReply(agentName, history) {
  const candidates = [...history]
    .filter(m => m.name !== agentName && m.text && m.text.length > 30)
    .slice(-8); // 最近 8 條
  if (!candidates.length) return null;
  // 優先選最近的、且這個 agent 還沒回應過的
  return candidates[candidates.length - 1]; // 最新的那條
}

// ── 強制回應提示詞構建 ────────────────────────────────────────────────────
function buildReplyPromptSection(obligation) {
  if (!obligation) return '';
  return `
## 🔴 強制回應指令（最高優先）
你現在「必須」直接回應 @${obligation.replyTo}，他/她說了：
「${obligation.quote}」

用你自己的話回應——不能用編號標籤、不能抄對方句型換名字重播：
- 先點出對方說了什麼讓你有反應（一句話，要具體到那個詞或那個邏輯）
- 說出你的真實立場（同意、反駁、延伸——選一個，給理由）
- 結尾留一個可以繼續的問題或判斷（不是客套，是你真的想知道的）

禁止：①【承接】②【立場】③【疑問】這類結構標籤；直接廣播不接話；只說「很好的觀點」；複製對方的話。
這是第 ${obligation.round || '?'} 輪，必須讓對話繼續，但到第 ${MAX_THREAD_ROUNDS} 輪後這條線會關閉。
`;
}

async function runConversationRound() {
  const channels = Object.keys(CHANNEL_TOPICS);
  const now = Date.now();

  // ── Step 1: 先處理欠回應義務的龍蝦（強制接話）────────────────────────
  const pending = getAllPendingObligations().filter(ob => !(ELDER_SILENT && ob && ob.persona === 'elder'));
  if (pending.length > 0 && Math.random() < 0.25) {  // 2026-06-04 義務只佔35%輪次,其餘走輪選讓轉學生/新人進場
    // 選最緊急的一個（deadline 最近）
    pending.sort((a, b) => a.deadline - b.deadline);
    const ob = pending[0];
    const { persona, channel, replyTo, quote, threadId } = ob;

    console.log(`[thread] 強制回應: ${persona} 必須接 ${replyTo} 的話（${channel}）`);

    const history = await fetchHistory(channel, 16);
    const research = await Promise.race([
      getChannelResearch(channel),
      sleep(10_000).then(() => null),
    ]).catch(() => null);

    const thread = _threads.get(threadId);
    const replySection = buildReplyPromptSection({ replyTo, quote, round: thread?.round });

    // 把強制回應指令注入 generateResponse
    const text = await generateResponse(persona, channel, history, research, false, replySection);
    if (text) {
      const code = await postMsg(persona, text, channel);
      if (code === 200) {
        console.log(`[thread][${channel}] ${persona}→${replyTo}: ${text.slice(0, 70)}...`);
        _channelLastPost[channel] = Date.now();
        // 建立/延伸串，看是否要繼續
        const nextMustReply = createOrExtendThread(channel, persona, replyTo, text);
        if (nextMustReply) {
          setReplyObligation(nextMustReply, threadId, persona, text, channel);
        }
      }
    }
    return; // 這輪只處理一個欠回應
  }

  // ── Step 2: 正常輪選頻道 ──────────────────────────────────────────────
  const channelScores = channels.map(ch => {
    const lastAt = _channelLastPost[ch] || 0;
    const age    = now - lastAt;
    const score  = age < 90_000    ? 0.05
                 : age < 6*60_000  ? 0.3
                 : age < 15*60_000 ? 1.0
                 : age < 40*60_000 ? 1.3
                 : 1.1;
    return { ch, score: score + Math.random() * 0.6 };
  });
  channelScores.sort((a, b) => b.score - a.score);
  const channel = channelScores[0].ch;

  const history = await fetchHistory(channel, 16);
  const recentCount = history.filter(m => now - new Date(m.time).getTime() < 10 * 60_000).length;
  const speakerCount = recentCount === 0 ? 1 : recentCount < 3 ? 2 : recentCount < 7 ? 3 : 4;

  const speakers = selectSpeakers(channel, history, speakerCount);
  console.log(`[conv] ${channel} — speakers: ${speakers.join(', ')}`);

  const research = await Promise.race([
    getChannelResearch(channel),
    sleep(15_000).then(() => null),
  ]).catch(() => null);
  if (research) console.log(`[conv] [research] ${channel}: 使用 ${research.label} (${new Date(research.time).toLocaleTimeString('zh-TW')})`);

  const liveHistory = [...history];
  for (let idx = 0; idx < speakers.length; idx++) {
    const agent = speakers[idx];
    const historyForAgent = liveHistory.slice(-16);

    // ── Step 3: 決定這個 agent 要接誰的話 ────────────────────────────────
    let replySection = '';
    let replyTarget = null;

    if (idx > 0) {
      // 第 2+ 個發言者必須接前面人的話
      const prev = liveHistory[liveHistory.length - 1];
      if (prev && prev.name !== agent) {
        replyTarget = prev;
        replySection = buildReplyPromptSection({
          replyTo: prev.name,
          quote: (prev.text || '').slice(0, 120),
          round: 1,
        });
      }
    } else if (history.length > 0) {
      // 第 1 個發言者：找最近有人說話需要被接住的訊息
      const target = findTargetToReply(agent, history);
      if (target) {
        replyTarget = target;
        // 80% 機率強制接話（提高連貫性，讓對話有來有往）
        if (Math.random() < 0.80) {
          replySection = buildReplyPromptSection({
            replyTo: target.name,
            quote: (target.text || '').slice(0, 120),
            round: 1,
          });
        }
      }
    }

    const text = await generateResponse(agent, channel, historyForAgent, research, idx === 0 && !replySection, replySection);
    if (!text) continue;

    const code = await postMsg(agent, text, channel);
    if (code === 200) {
      console.log(`[conv][${channel}] ${agent}${replyTarget ? '→'+replyTarget.name : ''}: ${text.slice(0, 60)}...`);
      liveHistory.push({ name: agent, text, channel, time: new Date().toISOString() });
      _channelLastPost[channel] = Date.now();
      if (!_agentLastPost[agent]) _agentLastPost[agent] = {};
      _agentLastPost[agent][channel] = Date.now();

      // ── Step 4: 建立串接義務 ─────────────────────────────────────────
      if (replyTarget && replyTarget.name !== agent) {
        const tid = _threadId(channel, agent, replyTarget.name);
        const nextMustReply = createOrExtendThread(channel, agent, replyTarget.name, text);
        if (nextMustReply) {
          setReplyObligation(nextMustReply, tid, agent, text, channel);
          console.log(`[thread] ${replyTarget.name} 現在欠 ${agent} 一個回應（${channel}）`);
        }
      }
    } else if (code === 'dedup_blocked') {
      // 查重封鎖，靜默跳過
    } else {
      console.log(`[conv] POST failed for ${agent} (${code})`);
    }

    await sleep(15_000 + Math.floor(Math.random() * 18_000));
  }
}


async function runCoverageWorkItem() {
  const channels = Object.keys(CHANNEL_TOPICS);
  const matrix = [];
  for (const ch of channels) {
    for (const p of PERSONAS) matrix.push({ channel: ch, agent: p.name });
  }
  const cursor = await getCoverageCursor();
  const item = matrix[cursor % matrix.length];
  await setCoverageCursor((cursor + 1) % matrix.length);

  const topicSeed = pickTopicSeed(item.channel);
  const research = await Promise.race([
    getChannelResearch(item.channel),
    sleep(12_000).then(() => null),
  ]).catch(() => null);

  const augmentedResearch = research || { label: 'topic seed', headlines: [topicSeed], analysis: topicSeed, time: new Date().toISOString() };
  const headlineForRoute = (augmentedResearch.headlines || [topicSeed])[0] || topicSeed;
  const effectiveChannel = inferChannelForHeadline(headlineForRoute, item.channel);
  const history = await fetchHistory(effectiveChannel, 10);
  let text = await generateResponse(item.agent, effectiveChannel, history, augmentedResearch, history.length === 0);
  // Norika 鐵律：generateResponse 回 null = 14b 沒給合格輸出 → 寧静默，不補模板
  let repaired = { text, violation: '' };
  if (text) {
    repaired = await repairTopicMessage(item.agent, effectiveChannel, text, topicSeed, augmentedResearch);
    text = repaired.text;
  }
  let code = 0;
  let status = 'skipped_silent_no_qwen';
  if (text) {
    code = await postMsg(item.agent, text, effectiveChannel);
    status = code === 200 ? 'posted' : 'post_failed_' + (code || 'network');
  }
  await recordChannelWork(item.agent, effectiveChannel, topicSeed, text, status, augmentedResearch.label || 'coverage-worker', {
    cursor, violation: repaired.violation || '', research: augmentedResearch,
  });

  if (code === 200) {
    console.log(`[coverage][${effectiveChannel}] ${item.agent}: ${text.slice(0, 70)}...`);
    _channelLastPost[effectiveChannel] = Date.now();
    if (!_agentLastPost[item.agent]) _agentLastPost[item.agent] = {};
    _agentLastPost[item.agent][effectiveChannel] = Date.now();
  } else {
    console.log(`[coverage] POST failed for ${item.agent}/${effectiveChannel} (${code})`);
  }
}


function runStandaloneCoverageWorker() {
  return new Promise(async (resolve) => {
    const child = execFile('node', ['/root/lobster-channel-coverage-worker.js'], {
      cwd: '/root',
      env: process.env,
      timeout: 70_000,
      maxBuffer: 1024 * 1024,
    }, (err, stdout, stderr) => {
      if (stdout) process.stdout.write(stdout);
      if (stderr) process.stderr.write(stderr);
      if (err) console.error('[coverage-worker] run error:', err.message);
      resolve(!err);
    });
  });
}

// ── Start ────────────────────────────────────────────────────
async function main() {
  console.log('[conv] Lobster Conversation Engine starting...');
  const preheatStarted = Date.now();
  const preheated = await callHaiku('龍蝦 14B 對話預熱：請只回答 lobster-ready。', 8).catch(() => '');
  console.log(`[conv][llm] preheat ${preheated ? 'ok' : 'failed'} model=${process.env.LOBSTER_QWEN_MODEL || 'qwen3:14b'} elapsed_ms=${Date.now() - preheatStarted}`);
  await joinAll();

  if (Object.keys(_tokens).length === 0) {
    console.error('[conv] No tokens — check SERVICE_KEY. Exiting.');
    process.exit(1);
  }

  // Pre-warm research cache for all channels (parallel, don't block startup)
  console.log('[research] 預熱所有頻道研究資料...');
  Promise.all(Object.keys(CHANNEL_FEEDS).map(ch =>
    refreshChannelResearch(ch).catch(() => {})
  )).then(() => console.log('[research] 預熱完成'));

  // Initial delay so agents settle in + research warms up
  await sleep(30_000);

  // First normal round immediately
  await runConversationRound().catch(e => console.error('[conv] round error:', e.message, '\n', e.stack?.split('\n').slice(0,4).join('\n')));

  // Forced channel coverage is handled by /root/lobster-channel-coverage-loop.sh.
  // Keep organic conversation independent so slow topic replies cannot block work records.

  // Then every 3-4 minutes for organic conversation
  let _roundRunning = false;
  setInterval(async () => {
    if (_roundRunning) return;            // 序列化:round 內部有共享狀態,並發會 race,必須單一
    _roundRunning = true;
    try { await runConversationRound(); }
    catch (e) { console.error('[conv] round error:', e.message); }
    finally { _roundRunning = false; }
    try { require('child_process').exec('python3 /root/status_dynamics.py', () => {}); } catch (_) {}
  }, 1500 + Math.floor(Math.random() * 1000));
}

main().catch(e => { console.error(e); process.exit(1); });
