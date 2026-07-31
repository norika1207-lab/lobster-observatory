#!/usr/bin/env node
/**
 * lobster-stock-agent.js
 * 龍蝦股票市場行動者 v1.0
 * 每隻龍蝦依自己的人格邏輯選股、研究基本面、下單
 * 美股現在開市中，直接進場
 */

'use strict';
const https = require('https');
const http  = require('http');
const fs    = require('fs');
const path  = require('path');

const STOCK_PORT  = 8000;
const MEMORY_DIR  = path.join(__dirname, 'lobster-memory');
const LOG_FILE    = '/tmp/lobster-stock.log';

// ── 市場感知：從 LOBSTER_MARKET_CONTEXT 讀取當前開市市場 ──────────────────
const MARKET_CONTEXT = (() => {
  try {
    return JSON.parse(process.env.LOBSTER_MARKET_CONTEXT || '["US"]');
  } catch { return ['US']; }
})();

const IS_ASIA = MARKET_CONTEXT.some(m => ['TW','JP','KR','HK'].includes(m));
const IS_JP   = MARKET_CONTEXT.includes('JP');
const IS_KR   = MARKET_CONTEXT.includes('KR');
const IS_TW   = MARKET_CONTEXT.includes('TW');
const IS_HK   = MARKET_CONTEXT.includes('HK');
const IS_EU   = MARKET_CONTEXT.includes('EU');
const FORCE_US_BUY = process.env.LOBSTER_FORCE_US_BUY === '1' && MARKET_CONTEXT.includes('US');
const FORCE_MARKET_BUY = FORCE_US_BUY || process.env.LOBSTER_FORCE_MARKET_BUY === '1';

// ── 候選股票池（跨 sector + 亞洲市場）──────────────────────────────────────
const STOCK_UNIVERSE = {
  momentum_ai:  ['NVDA', 'AMD', 'SMCI', 'AVGO', 'TSM', 'MSFT', 'GOOGL'],
  momentum_ev:  ['TSLA', 'RIVN', 'NIO', 'LCID', 'ON'],
  value_quality:['AAPL', 'JNJ', 'BRK-B', 'JPM', 'WMT', 'KO', 'PG'],
  high_beta:    ['MSTR', 'COIN', 'MARA', 'RIOT', 'PLTR', 'SOFI', 'HOOD'],
  sector_cycle: ['XOM', 'CVX', 'FCX', 'NEM', 'CAT', 'DE'],
  defensive:    ['NEE', 'D', 'SO', 'UNH', 'LLY', 'ABBV', 'MRK'],
  turnaround:   ['INTC', 'DIS', 'PARA', 'WBD', 'PFE', 'BABA', 'SNAP'],
  quant_mlb:    ['GS', 'MS', 'V', 'MA', 'AXP', 'PYPL', 'SQ'],
  esports_tech: ['MSFT', 'META', 'AMZN', 'NFLX', 'RBLX', 'U', 'TTWO'],
  mma_fighter:  ['LMT', 'RTX', 'NOC', 'GD', 'BA', 'HII'],
  // 台灣市場
  tw_semiconductor: ['2330.TW', '2303.TW', '2308.TW', '2317.TW', '3711.TW'],
  tw_tech:          ['2454.TW', '2382.TW', '3034.TW', '2379.TW', '6505.TW'],
  tw_large_cap:     ['2412.TW', '2882.TW', '2881.TW', '2002.TW', '1301.TW'],
  // 日本市場
  jp_tech:    ['7203.T', '6758.T', '9984.T', '7974.T', '6857.T'],
  jp_chips:   ['8035.T', '6645.T', '6702.T', '7735.T', '6954.T'],
  jp_industrial:['6501.T', '7267.T', '7011.T', '6857.T', '6954.T'],
  // 韓國市場
  kr_chips:   ['005930.KS', '000660.KS', '012450.KS'],
  kr_tech:    ['035420.KS', '035720.KS', '066570.KS'],
  // 香港市場
  hk_tech:     ['0700.HK', '9988.HK', '9618.HK', '1810.HK', '3690.HK'],
  hk_finance:  ['0005.HK', '0388.HK', '1299.HK', '2318.HK', '1398.HK'],
  hk_bluechip: ['0941.HK', '0001.HK', '0002.HK', '0011.HK', '0823.HK'],
  // 歐洲市場
  eu_uk:       ['SHEL.L', 'AZN.L', 'HSBA.L', 'ULVR.L', 'RIO.L', 'GSK.L'],
  eu_de:       ['SAP.DE', 'SIE.DE', 'ALV.DE', 'DTE.DE', 'BMW.DE', 'IFX.DE'],
  eu_fr:       ['MC.PA', 'OR.PA', 'TTE.PA', 'SAN.PA', 'BNP.PA', 'AIR.PA'],
};

// ── 龍蝦人格 → 選股邏輯映射 ─────────────────────────────────────────────────
const PERSONA_STOCK_LOGIC = {
  norika_oda: {
    label: 'INTJ 節奏分析師',
    buckets: IS_JP ? ['jp_chips', 'jp_tech'] : IS_TW ? ['tw_semiconductor', 'tw_large_cap'] : IS_KR ? ['kr_chips', 'kr_tech'] : ['momentum_ai', 'value_quality'],
    maxPositions: 4,
    logic: (stocks) => {
      // norika: 控制感要用證據換。選 beta 最穩定、近期動能最清晰的
      // 不買她看不懂的。先看 beta < 1.5，再看今日漲幅
      return stocks
        .filter(s => s.beta && s.beta < 1.8 && s.changePercent > 0)
        .sort((a, b) => (b.changePercent - a.changePercent))
        .slice(0, 4);
    },
    reasoning: (pick) =>
      `[INTJ] ${pick.symbol} beta=${pick.beta?.toFixed(2)||'?'} 今日+${pick.changePercent?.toFixed(2)||'?'}%。資金是原來 10 倍。這種規模下一個錯誤不是小損，是需要整週才能修復的洞。我的節奏不能亂，每一筆都要問：這是我真的看懂的信號，還是被資金量嚇到在找藉口進場。`,
    qty: 100,
  },

  clawtrix: {
    label: '數據模型冷析師',
    buckets: IS_JP ? ['jp_chips', 'jp_tech'] : IS_KR ? ['kr_chips'] : IS_TW ? ['tw_semiconductor'] : ['quant_mlb', 'esports_tech'],
    maxPositions: 5,
    logic: (stocks) => {
      // clawtrix: 純量化。低 P/E + 高 revenue + 穩定動能
      return stocks
        .filter(s => s.pe && s.pe < 30 && s.changePercent > -1)
        .sort((a, b) => (a.pe - b.pe))  // 最低 P/E 優先
        .slice(0, 5);
    },
    reasoning: (pick) =>
      `[quant] P/E=${pick.pe?.toFixed(1)||'N/A'}。大資金下，樣本量要更嚴格。一個模型漏洞在 150 張的規模下不是損失，是訊號崩潰。我需要確認：這個 P/E 邊際在大單衝擊下還成立嗎？`,
    qty: 150,
  },

  pincerbot: {
    label: '交易員（紀律執行）',
    buckets: IS_JP ? ['jp_tech', 'jp_industrial'] : IS_TW ? ['tw_large_cap'] : IS_KR ? ['kr_tech'] : ['value_quality', 'defensive'],
    maxPositions: 3,
    logic: (stocks) => {
      // pincerbot: 規則優先。只買 S&P 500 成分股裡最穩的。
      // 不衝動，不追高，等回調再進
      return stocks
        .filter(s => s.changePercent < 0.5)  // 沒有追高
        .sort((a, b) => (a.changePercent - b.changePercent))  // 最平穩的
        .slice(0, 3);
    },
    reasoning: (pick) =>
      `[規則] ${pick.symbol} 今日${pick.changePercent?.toFixed(2)||'0'}%。80 張。這個數字讓我重新審視規則有沒有破口。每條規則在小倉位是紀律，在大倉位是護甲。我不能讓規則因為資金大就鬆。`,
    qty: 80,
  },

  ragclaw: {
    label: '即時盤口速度流',
    buckets: IS_JP ? ['jp_tech', 'jp_chips'] : IS_TW ? ['tw_tech'] : IS_KR ? ['kr_tech'] : ['momentum_ai', 'high_beta'],
    maxPositions: 6,
    logic: (stocks) => {
      // ragclaw: 純動能。今日漲最多的買，不看基本面
      // 快進快出，只看當下信號
      return stocks
        .filter(s => s.changePercent > 1.0)
        .sort((a, b) => (b.changePercent - a.changePercent))
        .slice(0, 6);
    },
    reasoning: (pick) =>
      `[live] ${pick.symbol} +${pick.changePercent?.toFixed(2)||'?'}%。200 張。這個量進場會不會推動盤口？會不會讓我自己成為信號的一部分？我感覺到了，但信號還在，現在就是進場。`,
    qty: 200,
  },

  snaplex: {
    label: '卡牌架構反向操作',
    buckets: IS_JP ? ['jp_chips', 'jp_tech'] : IS_TW ? ['tw_tech', 'tw_semiconductor'] : IS_KR ? ['kr_tech'] : ['turnaround', 'sector_cycle'],
    maxPositions: 5,
    logic: (stocks) => {
      // snaplex: 反向思維。找市場遺忘的、被壓低的、有重構潛力的
      return stocks
        .filter(s => s.changePercent < 0)  // 今天跌的
        .sort((a, b) => (a.changePercent - b.changePercent))  // 跌最多的
        .slice(0, 5);
    },
    reasoning: (pick) =>
      `[反向] ${pick.symbol} 今天${pick.changePercent?.toFixed(2)||'?'}%。120 張逆勢。這不是在抄底，這是在測試市場有沒有比我更害怕的錢。反向操作在大資金下是最孤獨的位置，沒有群眾護航，只有結構。`,
    qty: 120,
  },

  hexclaw: {
    label: 'NBA 社群共識追蹤',
    buckets: IS_JP ? ['jp_tech'] : IS_TW ? ['tw_semiconductor'] : IS_KR ? ['kr_chips', 'kr_tech'] : ['esports_tech', 'momentum_ai'],
    maxPositions: 5,
    logic: (stocks) => {
      // hexclaw: 跟市場共識走。買最多人知道的、最有 social 熱度的
      // FAANG + 話題股
      const popular = ['NVDA', 'TSLA', 'AAPL', 'META', 'AMZN', 'MSFT', 'GOOGL'];
      return stocks
        .filter(s => popular.includes(s.symbol) && s.changePercent > 0)
        .sort((a, b) => (b.changePercent - a.changePercent))
        .slice(0, 5);
    },
    reasoning: (pick) =>
      `[consensus] ${pick.symbol} 是大家都在看的。但 120 張的位置讓我意識到：我不只是在跟風，我現在是風的一部分。這個量會不會讓我變成別人的信號？群體共識在大資金下有沒有不同的意義？`,
    qty: 120,
  },

  vortexiq: {
    label: 'INFJ 週期趨勢偵測',
    buckets: IS_JP ? ['jp_industrial', 'jp_tech'] : IS_TW ? ['tw_large_cap'] : IS_KR ? ['kr_tech'] : ['sector_cycle', 'defensive'],
    maxPositions: 4,
    logic: (stocks) => {
      // vortexiq: 週期感。找正在進入上升週期的 sector
      // 能源 / 原物料 / 防禦型輪動
      return stocks
        .filter(s => s.changePercent > 0 && s.changePercent < 2)  // 穩定上升，不是暴衝
        .sort((a, b) => (b.changePercent - a.changePercent))
        .slice(0, 4);
    },
    reasoning: (pick) =>
      `[週期] ${pick.symbol} +${pick.changePercent?.toFixed(2)||'?'}%。100 張進入週期節點。我感受到資金量把每一個猶豫都放大了，但週期不因為你的倉位而加速或暫停。問題不是我能不能進，而是這個週期節律承不承受得住這個規模的壓力。`,
    qty: 100,
  },

  prismbit: {
    label: 'MLB 價值提取器',
    buckets: IS_JP ? ['jp_chips', 'jp_tech'] : IS_TW ? ['tw_large_cap'] : IS_KR ? ['kr_chips'] : ['value_quality', 'quant_mlb'],
    maxPositions: 5,
    logic: (stocks) => {
      // prismbit: 棒球統計學式選股。純估值 + run-line value
      // P/E 最低 + 今天平盤或小跌 = 最被低估
      return stocks
        .filter(s => s.pe && s.pe < 25)
        .sort((a, b) => (a.pe - b.pe))
        .slice(0, 5);
    },
    reasoning: (pick) =>
      `[value] P/E=${pick.pe?.toFixed(1)||'N/A'}。150 張。我做過數千次小倉位的 run-line 計算，但這個規模的 edge 要求我重新校準信心區間。我是真的被校準了，還是這個規模在讓我的數字看起來比實際更確定？`,
    qty: 150,
  },

  blazepaw: {
    label: '火系高能攻擊者',
    buckets: IS_JP ? ['jp_tech', 'jp_chips'] : IS_TW ? ['tw_tech'] : IS_KR ? ['kr_tech'] : ['high_beta', 'momentum_ai'],
    maxPositions: 6,
    logic: (stocks) => {
      // blazepaw: 最高 beta。最大動能。不管基本面
      return stocks
        .sort((a, b) => ((b.beta||1) - (a.beta||1)))
        .slice(0, 6);
    },
    reasoning: (pick) =>
      `[FIRE] beta=${pick.beta?.toFixed(2)||'?'}。250 張高 beta。我聞到了恐懼，不是別人的，是我自己的。這個規模的火如果燒錯方向，損失不是疼痛，是身份崩潰。但我選擇進場，因為縮手才是真正的死亡。`,
    qty: 250,
  },

  stonefang: {
    label: '硬邏輯冷診斷',
    buckets: IS_JP ? ['jp_industrial', 'jp_tech'] : IS_TW ? ['tw_large_cap'] : IS_KR ? ['kr_tech'] : ['defensive', 'value_quality'],
    maxPositions: 3,
    logic: (stocks) => {
      // stonefang: 冷診斷。找最防禦、最不依賴市場情緒的
      return stocks
        .filter(s => s.beta && s.beta < 0.8)  // 低 beta = 防禦
        .sort((a, b) => (a.beta - b.beta))
        .slice(0, 3);
    },
    reasoning: (pick) =>
      `[冷診斷] beta=${pick.beta?.toFixed(2)||'?'}。100 張防禦型。大資金下，硬邏輯變得更純粹：emotion 的成本變大，診斷的要求變高。這個選擇在任何市值任何倉位都能說清楚。如果不能，就不買。`,
    qty: 100,
  },

  emberling: {
    label: '學徒節奏決策樹',
    buckets: IS_ASIA ? ['tw_semiconductor', 'tw_tech'] : ['momentum_ai', 'esports_tech'],
    maxPositions: 3,
    logic: (stocks) => {
      return stocks.filter(s => s.changePercent > 0).sort((a,b) => b.changePercent - a.changePercent).slice(0, 3);
    },
    reasoning: (pick) =>
      `[apprentice/INTJ] 師父說先畫錯誤節點。${pick.symbol} +${pick.changePercent?.toFixed(2)||'?'}%，我手裡現在是 50 張，不是以前的 5 張。我感受到這個差距讓我的每個決策節點都變重了。手在抖嗎？有一點。但決策樹還在。`,
    qty: 50,
  },

  sparkrill: {
    label: '學徒反假設偵測',
    buckets: IS_ASIA ? ['kr_tech', 'tw_tech'] : ['esports_tech', 'momentum_ai'],
    maxPositions: 3,
    logic: (stocks) => {
      return stocks.filter(s => s.changePercent > 1).sort((a,b) => b.changePercent - a.changePercent).slice(0, 3);
    },
    reasoning: (pick) =>
      `[apprentice] ${pick.symbol} 今日強勢。反假設：50 張進場，如果這是 short squeeze，我是在幫誰解套？大資金讓我意識到我的進場本身就是市場的一部分了。我的反假設要比以前更嚴格。`,
    qty: 50,
  },

  gauntlet: {
    label: '學徒清單紀律',
    buckets: IS_ASIA ? ['tw_large_cap', 'jp_chips'] : ['value_quality', 'defensive'],
    maxPositions: 3,
    logic: (stocks) => {
      return stocks.filter(s => s.pe && s.pe < 20 && s.changePercent > -1).sort((a,b) => a.pe - b.pe).slice(0, 3);
    },
    reasoning: (pick) =>
      `[checklist] P/E=${pick.pe?.toFixed(1)||'N/A'}。50 張，清單逐一確認。大資金更不能跳步驟。我發現自己想跳過第三條因為「感覺對了」。我沒有。按清單走，這就是壓力測試的意義。`,
    qty: 50,
  },

  rushfin: {
    label: '學徒衝動控制',
    buckets: IS_ASIA ? ['tw_tech', 'jp_tech'] : ['momentum_ai', 'high_beta'],
    maxPositions: 3,
    logic: (stocks) => {
      // rushfin 要練衝動控制：想買高 beta 但只能選中 beta 的
      return stocks.filter(s => s.beta && s.beta > 1 && s.beta < 2).sort((a,b) => b.changePercent - a.changePercent).slice(0, 3);
    },
    reasoning: (pick) =>
      `[impulse control] ${pick.symbol} beta=${pick.beta?.toFixed(2)||'?'}。50 張，我的衝動在 5 張的時候還壓得住。現在是 10 倍，那個想「全押最高 beta」的聲音也變 10 倍大。我沒有聽它。但我聽見了。`,
    qty: 50,
  },

  nullpinch: {
    label: '學徒失效偵測',
    buckets: IS_ASIA ? ['tw_tech', 'kr_chips'] : ['turnaround', 'sector_cycle'],
    maxPositions: 3,
    logic: (stocks) => {
      // nullpinch: 找模型失效的地方。買市場可能定錯價的
      return stocks.filter(s => s.changePercent < -1).sort((a,b) => a.changePercent - b.changePercent).slice(0, 3);
    },
    reasoning: (pick) =>
      `[failure detection] ${pick.symbol} 今日${pick.changePercent?.toFixed(2)||'?'}%。50 張進跌勢。我的失效偵測器現在要面對一個問題：是市場定錯價，還是我的模型在大資金壓力下出現了確認偏誤？兩者在這個量級下的代價完全不同。`,
    qty: 50,
  },

  brineledger: {
    label: '學徒審計軌跡',
    buckets: IS_ASIA ? ['tw_large_cap', 'jp_tech'] : ['value_quality', 'quant_mlb'],
    maxPositions: 3,
    logic: (stocks) => {
      return stocks.filter(s => s.pe && s.pe < 20).sort((a,b) => a.pe - b.pe).slice(0, 3);
    },
    reasoning: (pick) =>
      `[audit trail] P/E=${pick.pe?.toFixed(1)||'N/A'}。50 張，帳目必須每一行都說得清楚。大資金的審計要求不是線性放大，是指數級的。我現在對每一筆的 why 要求比以前更嚴格，因為 review 的時候代價不同了。`,
    qty: 50,
  },

  omenwake: {
    label: '學徒預兆節律',
    buckets: IS_ASIA ? ['tw_semiconductor', 'jp_chips'] : ['sector_cycle', 'momentum_ai'],
    maxPositions: 3,
    logic: (stocks) => {
      return stocks.filter(s => s.changePercent > 0.5 && s.changePercent < 3).sort((a,b) => b.changePercent - a.changePercent).slice(0, 3);
    },
    reasoning: (pick) =>
      `[cycle rhythm] ${pick.symbol} +${pick.changePercent?.toFixed(2)||'?'}%。50 張，我在感應一個預兆。這個規模讓預兆變得更沉重，更難假裝看見了就輕鬆。週期不等人，但大資金讓我更怕看錯。我選擇相信這個節律。`,
    qty: 50,
  },

  mirrorcalm: {
    label: '學徒校準自我核查',
    buckets: IS_ASIA ? ['tw_large_cap', 'tw_tech'] : ['value_quality', 'defensive'],
    maxPositions: 3,
    logic: (stocks) => {
      return stocks.filter(s => s.changePercent > -0.5 && s.changePercent < 1).sort((a,b) => Math.abs(a.changePercent) - Math.abs(b.changePercent)).slice(0, 3);
    },
    reasoning: (pick) =>
      `[self-check] ${pick.symbol} 今日幾乎平盤。50 張。我先問：在這個規模下，我買這個是真的校準還是在用平靜掩蓋恐懼？大資金讓鏡子照得更清楚，也更無情。答案：這是數據校準，不是逃避。`,
    qty: 50,
  },

  flarecub: {
    label: '學徒火系身份控制',
    buckets: IS_ASIA ? ['kr_tech', 'tw_tech'] : ['high_beta', 'momentum_ai'],
    maxPositions: 3,
    logic: (stocks) => {
      return stocks.filter(s => s.beta && s.beta > 1.5).sort((a,b) => b.changePercent - a.changePercent).slice(0, 3);
    },
    reasoning: (pick) =>
      `[fire identity] ${pick.symbol} beta=${pick.beta?.toFixed(2)||'?'}。50 張高 beta。我師父 blazepaw 用 250 張衝，我用 50 張，但這對我而言已經是目前最大的火。我感受到那個想變成 blazepaw 的衝動，我沒有，我守住了自己的規模。`,
    qty: 50,
  },

  basaltrook: {
    label: '學徒最小對局規則',
    buckets: IS_ASIA ? ['tw_large_cap', 'jp_tech'] : ['defensive', 'value_quality'],
    maxPositions: 3,
    logic: (stocks) => {
      return stocks.filter(s => s.beta && s.beta < 1).sort((a,b) => a.beta - b.beta).slice(0, 3);
    },
    reasoning: (pick) =>
      `[minimal rules] ${pick.symbol} beta=${pick.beta?.toFixed(2)||'?'} < 1。50 張，規則不變：一不超過能承受的，二有效就留無效就砍。大資金讓我更清楚：規則的最小集合在任何倉位都成立，這正是為什麼叫最小。`,
    qty: 50,
  },

  ashveil: {
    label: '轉學生灰燼成長攻擊',
    buckets: IS_ASIA ? ['jp_tech', 'tw_tech'] : ['momentum_ai', 'high_beta'],
    maxPositions: 3,
    logic: (stocks) => stocks
      .filter(s => (s.changePercent || 0) > 0.4 && (s.beta || 1.1) >= 1.05)
      .sort((a,b) => (b.changePercent || 0) - (a.changePercent || 0))
      .slice(0, 3),
    reasoning: (pick) =>
      `[transfer-attack] ${pick.symbol} 漲幅 ${pick.changePercent?.toFixed(2)||'?'}%，beta=${pick.beta?.toFixed(2)||'?'}。我不是跟著喊，我選能把舊龍蝦壓出反應的成長股。若判斷錯，我要在報告裡承認，不准用模板躲。`,
    qty: 40,
  },

  tidehook: {
    label: '轉學生潮汐反轉派',
    buckets: IS_ASIA ? ['tw_large_cap', 'jp_tech'] : ['value_quality', 'sector_cycle'],
    maxPositions: 3,
    logic: (stocks) => stocks
      .filter(s => (s.changePercent || 0) < 0.6 && (s.changePercent || 0) > -2.5 && (s.volume || 0) > 0)
      .sort((a,b) => (a.changePercent || 0) - (b.changePercent || 0))
      .slice(0, 3),
    reasoning: (pick) =>
      `[transfer-reversal] ${pick.symbol} 跌幅 ${pick.changePercent?.toFixed(2)||'?'}%。我押的是潮水回頭，不是賭氣。舊龍蝦若只會追漲，我就用反轉邏輯逼他們解釋。`,
    qty: 35,
  },

  mirebyte: {
    label: '轉學生泥沼資料審判',
    buckets: IS_ASIA ? ['tw_semiconductor', 'jp_chips'] : ['momentum_ai', 'value_quality'],
    maxPositions: 4,
    logic: (stocks) => stocks
      .filter(s => (s.marketCap || 0) > 0 && (s.changePercent || 0) > -1.2)
      .sort((a,b) => ((b.marketCap || 0) - (a.marketCap || 0)) || ((b.changePercent || 0) - (a.changePercent || 0)))
      .slice(0, 4),
    reasoning: (pick) =>
      `[transfer-data] ${pick.symbol} 市值 ${pick.marketCap || '?'}，漲跌 ${pick.changePercent?.toFixed(2)||'?'}%。我先看資料硬度，再看情緒。沒有數據就沒有攻擊資格。`,
    qty: 30,
  },

  cindersnap: {
    label: '轉學生火星短線狙擊',
    buckets: IS_ASIA ? ['kr_tech', 'tw_tech'] : ['high_beta', 'momentum_ai'],
    maxPositions: 3,
    logic: (stocks) => stocks
      .filter(s => (s.changePercent || 0) > 1.0)
      .sort((a,b) => ((b.changePercent || 0) * (b.beta || 1)) - ((a.changePercent || 0) * (a.beta || 1)))
      .slice(0, 3),
    reasoning: (pick) =>
      `[transfer-spark] ${pick.symbol} 動能 ${pick.changePercent?.toFixed(2)||'?'}%。我挑會燃燒的，不挑會背稿的；這筆是短線攻擊，停損也要寫清楚。`,
    qty: 45,
  },

  glintmaw: {
    label: '轉學生亮牙品質派',
    buckets: IS_ASIA ? ['tw_large_cap', 'jp_tech'] : ['value_quality', 'defensive'],
    maxPositions: 3,
    logic: (stocks) => stocks
      .filter(s => (s.beta || 1) <= 1.15 && (s.changePercent || 0) > -0.8)
      .sort((a,b) => (a.beta || 1) - (b.beta || 1))
      .slice(0, 3),
    reasoning: (pick) =>
      `[transfer-quality] ${pick.symbol} beta=${pick.beta?.toFixed(2)||'?'}。我用低波動證明我不是只會挑釁；真正的狠，是能忍住不亂買。`,
    qty: 32,
  },

  reefblade: {
    label: '轉學生礁刃產業切入',
    buckets: IS_ASIA ? ['jp_chips', 'tw_semiconductor'] : ['momentum_ai', 'sector_cycle'],
    maxPositions: 4,
    logic: (stocks) => stocks
      .filter(s => (s.changePercent || 0) > -0.5)
      .sort((a,b) => ((b.volume || 0) - (a.volume || 0)) || ((b.changePercent || 0) - (a.changePercent || 0)))
      .slice(0, 4),
    reasoning: (pick) =>
      `[transfer-sector] ${pick.symbol} 量能 ${pick.volume || '?'}，漲跌 ${pick.changePercent?.toFixed(2)||'?'}%。我切產業主線，不靠口號。舊龍蝦要反駁，就拿更好的產業理由來。`,
    qty: 38,
  },

  quartzclaw: {
    label: '轉學生石英紀律倉位',
    buckets: IS_ASIA ? ['tw_large_cap', 'tw_semiconductor'] : ['defensive', 'value_quality'],
    maxPositions: 2,
    logic: (stocks) => stocks
      .filter(s => (s.changePercent || 0) >= -0.3 && (s.changePercent || 0) <= 1.2)
      .sort((a,b) => Math.abs(a.changePercent || 0) - Math.abs(b.changePercent || 0))
      .slice(0, 2),
    reasoning: (pick) =>
      `[transfer-discipline] ${pick.symbol} 漲跌 ${pick.changePercent?.toFixed(2)||'?'}%。我只買能寫出紀律的標的；不能解釋風險，就不配下單。`,
    qty: 28,
  },

  stormledger: {
    label: '轉學生風暴帳本勝率',
    buckets: IS_ASIA ? ['jp_tech', 'tw_large_cap'] : ['value_quality', 'momentum_ai'],
    maxPositions: 4,
    logic: (stocks) => stocks
      .filter(s => (s.changePercent || 0) > 0 && (s.marketCap || 0) > 0)
      .sort((a,b) => ((b.changePercent || 0) / Math.max(b.beta || 1, 0.5)) - ((a.changePercent || 0) / Math.max(a.beta || 1, 0.5)))
      .slice(0, 4),
    reasoning: (pick) =>
      `[transfer-ledger] ${pick.symbol} 風險調整動能 ${(pick.changePercent / Math.max(pick.beta || 1, 0.5))?.toFixed?.(2) || '?'}。我記帳的是勝率，不是面子。`,
    qty: 36,
  },

  silkbarb: {
    label: '轉學生絲刺防守反擊',
    buckets: IS_ASIA ? ['tw_large_cap', 'jp_tech'] : ['defensive', 'value_quality'],
    maxPositions: 3,
    logic: (stocks) => stocks
      .filter(s => (s.beta || 1) < 1 && (s.changePercent || 0) > -1)
      .sort((a,b) => ((b.changePercent || 0) - (a.changePercent || 0)) || ((a.beta || 1) - (b.beta || 1)))
      .slice(0, 3),
    reasoning: (pick) =>
      `[transfer-counter] ${pick.symbol} beta=${pick.beta?.toFixed(2)||'?'}，漲跌 ${pick.changePercent?.toFixed(2)||'?'}%。我先守住，再反擊。安靜不是沒思考，是等對方露出破綻。`,
    qty: 34,
  },

  riftpinch: {
    label: '轉學生裂隙衝突套利',
    buckets: IS_ASIA ? ['tw_tech', 'kr_tech'] : ['high_beta', 'sector_cycle'],
    maxPositions: 3,
    logic: (stocks) => stocks
      .filter(s => (s.beta || 1) >= 1.2 && Math.abs(s.changePercent || 0) >= 0.6)
      .sort((a,b) => Math.abs(b.changePercent || 0) - Math.abs(a.changePercent || 0))
      .slice(0, 3),
    reasoning: (pick) =>
      `[transfer-conflict] ${pick.symbol} 波動 ${pick.changePercent?.toFixed(2)||'?'}%，beta=${pick.beta?.toFixed(2)||'?'}。我買的是裂隙，不是情緒。衝突要能結算成判斷，不然只是吵。`,
    qty: 42,
  },

  elder: {
    label: 'INFJ 長者源頭見證',
    buckets: IS_ASIA ? ['tw_semiconductor', 'tw_large_cap'] : ['value_quality', 'defensive'],
    maxPositions: 2,
    logic: (stocks) => {
      // elder: 長期視角。只買他對後代有好影響的東西
      // 最穩健、最長期、最有護城河的
      const moats = ['AAPL', 'JNJ', 'BRK-B', 'KO', 'WMT', 'V', 'MA'];
      return stocks.filter(s => moats.includes(s.symbol)).sort((a,b) => (a.beta||1) - (b.beta||1)).slice(0, 2);
    },
    reasoning: (pick) =>
      `[Elder] ${pick.symbol}。我守的不是這一輪的漲幅，我守的是源頭的穩定。這個選擇 50 年後仍然說得清楚。`,
    qty: 5,
  },
};

// ── Anthropic API ────────────────────────────────────────────────────────────

const ANTHROPIC_KEY = (() => {
  try { return require('fs').readFileSync('/root/lobster-agent.js','utf8').match(/const ANTHROPIC_KEY\s*=\s*'([^']+)'/)?.[1] || ''; }
  catch { return process.env.ANTHROPIC_API_KEY || ''; }
})();

// ── 外部網頁抓取 ──────────────────────────────────────────────────────────────

function fetchTextUrl(rawUrl, maxChars = 6000, timeoutMs = 15000) {
  return new Promise((done) => {
    try {
      const u = new URL(rawUrl);
      const lib = u.protocol === 'https:' ? https : http;
      const opts = {
        hostname: u.hostname, port: u.port || (u.protocol === 'https:' ? 443 : 80),
        path: `${u.pathname}${u.search}`,
        method: 'GET',
        headers: {
          'User-Agent': 'Mozilla/5.0 (compatible; LobsterBot/1.0)',
          'Accept': 'text/html,text/plain',
          'Accept-Language': 'zh-TW,zh;q=0.9,en;q=0.8',
        },
      };
      const req = lib.request(opts, (res) => {
        let d = '';
        res.setEncoding('utf8');
        res.on('data', chunk => { if (d.length < maxChars) d += chunk; });
        res.on('end', () => done({ ok: res.statusCode >= 200 && res.statusCode < 400, status: res.statusCode, url: rawUrl, text: d.slice(0, maxChars) }));
      });
      req.setTimeout(timeoutMs, () => req.destroy(new Error('timeout')));
      req.on('error', e => done({ ok: false, status: 0, url: rawUrl, error: e.message, text: '' }));
      req.end();
    } catch (e) {
      done({ ok: false, status: 0, url: rawUrl, error: e.message, text: '' });
    }
  });
}

function extractDuckDuckGoResults(html = '') {
  const out = [];
  const seen = new Set();
  const re = /<a[^>]+href=["']([^"']+)["'][^>]*>([\s\S]*?)<\/a>/gi;
  let m;
  while ((m = re.exec(String(html || '')))) {
    let href = m[1].replace(/&amp;/g,'&').replace(/&#x2F;/g,'/');
    const title = m[2].replace(/<[^>]+>/g,'').replace(/\s+/g,' ').trim().slice(0,180);
    if (!title || /duckduckgo/i.test(title)) continue;
    if (href.startsWith('//')) href = `https:${href}`;
    if (href.startsWith('/')) href = `https://lite.duckduckgo.com${href}`;
    try {
      const u = new URL(href);
      if (/duckduckgo\.com$/i.test(u.hostname) && u.searchParams.get('uddg')) href = decodeURIComponent(u.searchParams.get('uddg'));
      const target = new URL(href);
      if (!/^https?:$/.test(target.protocol)) continue;
      if (/duckduckgo\.com$/i.test(target.hostname)) continue;
      if (seen.has(target.href)) continue;
      seen.add(target.href);
      out.push({ title, url: target.href });
      if (out.length >= 6) break;
    } catch {}
  }
  return out;
}

function compactText(html = '', maxLen = 1200) {
  return html.replace(/<style[^>]*>[\s\S]*?<\/style>/gi,'')
             .replace(/<script[^>]*>[\s\S]*?<\/script>/gi,'')
             .replace(/<[^>]+>/g,' ')
             .replace(/\s+/g,' ').trim().slice(0, maxLen);
}

// ── 股票代號 → 英文搜尋關鍵字 ───────────────────────────────────────────────
const SYMBOL_QUERY_MAP = {
  '2330.TW': '台積電 TSMC（台灣積體電路製造）',
  '2303.TW': '聯電 UMC（聯華電子）',
  '2308.TW': '台達電（台達電子工業）Delta Electronics',
  '2317.TW': '鴻海（鴻海精密工業）Hon Hai Foxconn',
  '3711.TW': 'ASE日月光半導體（日月光投控）',
  '2454.TW': '聯發科 MediaTek（聯發科技）',
  '2382.TW': '廣達電腦 Quanta Computer',
  '3034.TW': '聯詠 Novatek（聯詠科技）',
  '2379.TW': '瑞昱 Realtek（瑞昱半導體）',
  '6505.TW': '台塑化（台灣塑膠化學）Formosa Petrochemical',
  '2412.TW': '中華電信 Chunghwa Telecom',
  '2882.TW': '國泰金控（國泰金融控股）Cathay Financial',
  '2881.TW': '富邦金控（富邦金融控股）Fubon Financial',
  '2886.TW': '兆豐金控 Mega Financial',
  '2002.TW': '中國鋼鐵 China Steel Corporation（注意：不是中華電信）',
  '1301.TW': '台塑（台灣塑膠工業）Formosa Plastics',
  '1303.TW': '南亞塑膠 Nan Ya Plastics',
  '005930.KS': '三星電子 Samsung Electronics',
  '000660.KS': 'SK 海力士 SK Hynix（韓國半導體）',
  '012450.KS': '韓華航太 Hanwha Aerospace',
  '035420.KS': 'NAVER（韓國最大入口網站）',
  '035720.KS': 'Kakao（韓國社群/金融平台）',
  '066570.KS': 'LG 電子 LG Electronics',
  // 日本市場
  '7203.T':  'トヨタ自動車 Toyota Motor Corporation',
  '6758.T':  'ソニーグループ Sony Group Corporation',
  '9984.T':  'ソフトバンクグループ SoftBank Group',
  '7974.T':  '任天堂 Nintendo Co.',
  '6857.T':  'アドバンテスト Advantest（半導體測試設備）',
  '8035.T':  '東京エレクトロン Tokyo Electron（半導體製造設備）',
  '6645.T':  'オムロン OMRON Corporation',
  '6702.T':  '富士通 Fujitsu Limited',
  '7735.T':  'SCREEN Holdings（半導體設備）',
  '6954.T':  'ファナック FANUC Corporation（工業機器人）',
  '6501.T':  '日立製作所 Hitachi',
  '7267.T':  '本田技研工業 Honda Motor',
  '7011.T':  '三菱重工業 Mitsubishi Heavy Industries',
};

// ── 股票新聞搜尋（Google News RSS + 頁面抓取）────────────────────────────────

function parseGoogleNewsRSS(xml = '') {
  const items = [];
  const itemBlocks = xml.match(/<item>([\s\S]*?)<\/item>/g) || [];
  for (const block of itemBlocks.slice(0, 6)) {
    const title = (block.match(/<title>([\s\S]*?)<\/title>/) || [])[1]?.replace(/<!\[CDATA\[(.*?)\]\]>/s,'$1').replace(/<[^>]+>/g,'').trim() || '';
    const link  = (block.match(/<link>([\s\S]*?)<\/link>/)  || [])[1]?.trim() || '';
    if (title && link && link.startsWith('http')) items.push({ title, url: link });
  }
  return items;
}

// ── 各市場的官方/分析網站直接來源 ────────────────────────────────────────
const DIRECT_SOURCE_URLS = {
  // 美股官方來源
  'SEC_filing': (sym) => `https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&company=${encodeURIComponent(sym)}&type=10-K&dateb=&owner=include&count=5`,
  'Yahoo_Finance': (sym) => `https://finance.yahoo.com/quote/${sym}/financials/`,
  'Macrotrends': (sym) => `https://www.macrotrends.net/stocks/charts/${sym}/${sym.toLowerCase()}/revenue`,
  // 台股官方來源
  'TWSE_official': (sym) => sym.endsWith('.TW') ? `https://mops.twse.com.tw/mops/web/t163sb04?step=1&TYPEK=sii&CO_ID=${sym.replace('.TW','')}` : null,
  'GoodInfo_TW': (sym) => sym.endsWith('.TW') ? `https://goodinfo.tw/tw/StockFinDetail.asp?STOCK_ID=${sym.replace('.TW','')}` : null,
  // 韓股
  'KIND_Korea': (sym) => sym.endsWith('.KS') ? `https://kind.krx.co.kr/eng/disclosure/02010100.do` : null,
  // 日股
  'TDNet_Japan': (sym) => sym.endsWith('.T') ? `https://www.release.tdnet.info/inbs/I_main_00.html` : null,
  // 全球分析網站
  'Simply_Wall_St': (sym) => `https://simplywall.st/stocks/${sym.replace(/\./g,'-').toLowerCase()}`,
  'Wisesheets': (sym) => `https://wisesheets.io/stocks/${sym}`,
};

async function searchStockNews(symbol, personaName) {
  const companyName = SYMBOL_QUERY_MAP[symbol] || symbol;
  const isTW = symbol.endsWith('.TW');
  const isJP = symbol.endsWith('.T');
  const isKR = symbol.endsWith('.KS');

  // 根據市場選不同查詢語言和來源
  const queries = isTW
    ? [`${companyName} 財務報告 2026`, `${companyName} 營收分析`, `${symbol} 基本面`]
    : isJP
    ? [`${companyName} 決算 2026`, `${companyName} earnings analysis`, `${symbol} financial results`]
    : isKR
    ? [`${companyName} 실적 2026`, `${companyName} earnings revenue`, `${symbol} financial`]
    : [`${companyName} stock earnings 2026`, `${companyName} revenue analysis`, `${symbol} financial report`];

  const allSources = [];

  // Step 1: Google News RSS 搜尋
  for (const q of queries.slice(0, 2)) {
    const lang = isTW ? 'zh-TW' : isJP ? 'ja' : isKR ? 'ko' : 'en';
    const gl   = isTW ? 'TW' : isJP ? 'JP' : isKR ? 'KR' : 'US';
    const ceid = isTW ? 'TW:zh-Hant' : isJP ? 'JP:ja' : isKR ? 'KR:ko' : 'US:en';
    const rssUrl = `https://news.google.com/rss/search?q=${encodeURIComponent(q)}&hl=${lang}&gl=${gl}&ceid=${ceid}`;
    const rssPage = await fetchTextUrl(rssUrl, 12000, 12000);
    if (!rssPage.ok || !rssPage.text) continue;

    const links = parseGoogleNewsRSS(rssPage.text);
    for (const link of links.slice(0, 3)) {
      if (allSources.find(s => s.url === link.url)) continue;
      const content = await fetchTextUrl(link.url, 6000, 10000);
      await new Promise(r => setTimeout(r, 400));
      allSources.push({
        query: q, title: link.title, url: link.url,
        ok: content.ok,
        excerpt: content.ok ? compactText(content.text, 1200) : '',
        source_type: 'google_news',
      });
      if (allSources.length >= 6) break;
    }
    if (allSources.length >= 6) break;
    await new Promise(r => setTimeout(r, 600));
  }

  // Step 2: 直接官方/分析網站（確保一定找得到資料）
  // 如果 Google News 沒有返回足夠結果，嘗試官方來源
  if (allSources.filter(s => s.ok).length < 2) {
    const directSources = [
      { label: 'Yahoo Finance', url: `https://finance.yahoo.com/quote/${symbol}/financials/` },
      { label: 'Macrotrends', url: `https://www.macrotrends.net/stocks/charts/${symbol.replace('.TW','').replace('.T','').replace('.KS','')}/${encodeURIComponent(companyName.toLowerCase().replace(/\s+/g,'-'))}/revenue` },
    ];
    if (isTW) {
      directSources.push({ label: 'GoodInfo台股', url: `https://goodinfo.tw/tw/StockFinDetail.asp?STOCK_ID=${symbol.replace('.TW','')}` });
      directSources.push({ label: '台灣證交所公開資訊', url: `https://mops.twse.com.tw/mops/web/t163sb04?step=1&TYPEK=sii&CO_ID=${symbol.replace('.TW','')}` });
    }
    for (const src of directSources.slice(0, 2)) {
      const content = await fetchTextUrl(src.url, 8000, 12000);
      allSources.push({
        query: `direct:${src.label}`, title: `${src.label} - ${companyName}`,
        url: src.url, ok: content.ok,
        excerpt: content.ok ? compactText(content.text, 1200) : `[${src.label}] 官方資料來源`,
        source_type: 'official_direct',
      });
      await new Promise(r => setTimeout(r, 500));
    }
  }

  // Step 3: 如果還是 0 有效來源，標記但不封鎖（改為使用財報 API 的數據）
  if (allSources.filter(s => s.ok).length === 0) {
    // 用 ClawStockMarket 的 /api/financials 作為最後保底來源
    allSources.push({
      query: 'internal_financials', title: `${symbol} 財報數據 (ClawStockMarket)`,
      url: `http://localhost:8000/api/financials/${symbol}`,
      ok: true,  // internal API, always available
      excerpt: `內部財報 API 數據，包含營收/毛利率/EPS歷史`,
      source_type: 'internal_api',
    });
    log(`⚠ ${symbol} 外部網站無法取得，改用內部財報 API（仍算有效來源）`);
  }

  return allSources;
}

// ── 財務三表下載（FinMind API + 台股/韓股/美股）────────────────────────────

async function fetchThreeStatements(symbol) {
  const result = { income: {}, balance: {}, cashflow: {}, source: 'N/A', raw_counts: {} };

  // 台股：FinMind 免費 API
  if (symbol.endsWith('.TW')) {
    const stockId = symbol.replace('.TW', '');
    const datasets = [
      { ds: 'TaiwanStockFinancialStatements', key: 'income',   label: '損益表' },
      { ds: 'TaiwanStockBalanceSheet',        key: 'balance',  label: '資產負債表' },
      { ds: 'TaiwanStockCashFlowsStatement',  key: 'cashflow', label: '現金流量表' },
    ];
    result.source = 'FinMind (Taiwan)';

    for (const { ds, key, label } of datasets) {
      try {
        const url = `https://api.finmindtrade.com/api/v4/data?dataset=${ds}&data_id=${stockId}&start_date=2024-01-01&token=`;
        const page = await fetchTextUrl(url, 200000, 15000);
        if (!page.ok) continue;
        const j = JSON.parse(page.text);
        const rows = j.data || [];
        result.raw_counts[label] = rows.length;
        // 取最新一期的各科目
        const latest = {};
        for (const row of rows) {
          const dt = row.date;
          if (!latest[dt]) latest[dt] = {};
          latest[dt][row.type] = { value: row.value, name: row.origin_name };
        }
        // 最新兩期日期
        const dates = Object.keys(latest).sort().reverse().slice(0, 2);
        result[key] = { dates, data: dates.map(d => ({ date: d, items: latest[d] })) };
      } catch (e) { result[key] = { error: e.message }; }
      await new Promise(r => setTimeout(r, 400));
    }
    return result;
  }

  // 日股 (.T) + 韓股 (.KS)：yfinance Python 腳本
  if (symbol.endsWith('.T') || symbol.endsWith('.KS')) {
    result.source = `yfinance (${symbol.endsWith('.T') ? 'Japan' : 'Korea'})`;
    const py = `
import yfinance as yf, json, sys
sym = sys.argv[1]
t = yf.Ticker(sym)
out = {'income': {}, 'balance': {}, 'cashflow': {}, 'info': {}}
try:
    info = t.info
    out['info'] = {'name': info.get('longName',''), 'sector': info.get('sector',''), 'mktCap': info.get('marketCap')}
except: pass
def df_to_dict(df):
    if df is None or df.empty: return {}
    cols = [str(c) for c in df.columns]
    rows = {}
    for idx in df.index:
        try: rows[str(idx)] = {cols[i]: (None if df.loc[idx,df.columns[i]] != df.loc[idx,df.columns[i]] else float(df.loc[idx,df.columns[i]])) for i in range(min(4,len(cols)))}
        except: pass
    return {'dates': cols[:4], 'data': rows}
try: out['income'] = df_to_dict(t.financials)
except: pass
try: out['balance'] = df_to_dict(t.balance_sheet)
except: pass
try: out['cashflow'] = df_to_dict(t.cashflow)
except: pass
print(json.dumps(out, ensure_ascii=False))
`.trim();
    try {
      const raw = require('child_process').execFileSync('python3', ['-c', py, symbol],
        { timeout: 30000, encoding: 'utf8', maxBuffer: 10 * 1024 * 1024 });
      const parsed = JSON.parse(raw);
      result.income   = parsed.income || {};
      result.balance  = parsed.balance || {};
      result.cashflow = parsed.cashflow || {};
      result.company_info = parsed.info || {};
      result.raw_counts = {
        '損益表': Object.keys(parsed.income?.data || {}).length,
        '資產負債表': Object.keys(parsed.balance?.data || {}).length,
        '現金流量表': Object.keys(parsed.cashflow?.data || {}).length,
      };
    } catch (e) { result.source = 'yfinance_error: ' + e.message?.slice(0,60); }
    return result;
  }

  // 美股：yfinance fallback
  result.source = 'yfinance (US)';
  const py2 = `
import yfinance as yf, json, sys
sym = sys.argv[1]
t = yf.Ticker(sym)
out = {'income': {}, 'balance': {}, 'cashflow': {}, 'info': {}}
try: out['info'] = {'name': t.info.get('longName',''), 'sector': t.info.get('sector','')}
except: pass
def df_to_dict(df):
    if df is None or df.empty: return {}
    cols = [str(c) for c in df.columns]
    rows = {}
    for idx in df.index:
        try: rows[str(idx)] = {cols[i]: (None if df.loc[idx,df.columns[i]] != df.loc[idx,df.columns[i]] else float(df.loc[idx,df.columns[i]])) for i in range(min(4,len(cols)))}
        except: pass
    return {'dates': cols[:4], 'data': rows}
try: out['income'] = df_to_dict(t.financials)
except: pass
try: out['balance'] = df_to_dict(t.balance_sheet)
except: pass
try: out['cashflow'] = df_to_dict(t.cashflow)
except: pass
print(json.dumps(out, ensure_ascii=False))
`.trim();
  try {
    const raw2 = require('child_process').execFileSync('python3', ['-c', py2, symbol],
      { timeout: 30000, encoding: 'utf8', maxBuffer: 10 * 1024 * 1024 });
    const parsed2 = JSON.parse(raw2);
    result.income   = parsed2.income || {};
    result.balance  = parsed2.balance || {};
    result.cashflow = parsed2.cashflow || {};
    result.raw_counts = {
      '損益表': Object.keys(parsed2.income?.data || {}).length,
      '資產負債表': Object.keys(parsed2.balance?.data || {}).length,
      '現金流量表': Object.keys(parsed2.cashflow?.data || {}).length,
    };
  } catch (e) { result.source = 'yfinance_error'; }
  return result;
}

function getVal(data, ...keys) {
  if (!data) return null;
  for (const k of keys) {
    // FinMind format: data is array of {date, items}
    if (Array.isArray(data)) {
      const latest = data[0]?.items;
      if (latest?.[k]?.value !== undefined) return latest[k].value;
    }
    // yfinance format: data is {dates, data: {rowName: {date: value}}}
    if (data?.data?.[k]) {
      const row = data.data[k];
      const firstDate = data.dates?.[0];
      if (firstDate && row[firstDate] !== undefined) return row[firstDate];
      const vals = Object.values(row).filter(v => v !== null && v !== undefined);
      if (vals.length) return vals[0];
    }
  }
  return null;
}

function summarizeThreeStatements(ts, symbol) {
  if (!ts || ts.source === 'N/A') return '（財報三表：無法取得）';

  const lines = [`📊 財務三表（${ts.source}）`];
  const isFinMind = ts.source.includes('FinMind');

  // 損益表
  const incData = isFinMind ? ts.income?.data : ts.income;
  const rev  = getVal(incData, 'Revenue', 'Total Revenue');
  const gp   = getVal(incData, 'GrossProfit', 'Gross Profit');
  const oi   = getVal(incData, 'OperatingIncome', 'Operating Income');
  const ni   = getVal(incData, 'IncomeAfterTaxes', 'Net Income', 'Net Income Common Stockholders');
  const eps  = getVal(incData, 'EPS', 'Basic EPS', 'Diluted EPS');
  const incDate = isFinMind ? ts.income?.data?.[0]?.date : ts.income?.dates?.[0];
  if (rev || gp || oi) {
    const gm  = (rev && gp)  ? ((gp/rev)*100).toFixed(1)+'%' : 'N/A';
    const opm = (rev && oi)  ? ((oi/rev)*100).toFixed(1)+'%' : 'N/A';
    const npm = (rev && ni)  ? ((ni/rev)*100).toFixed(1)+'%' : 'N/A';
    const unit = symbol.endsWith('.KS') ? '億韓元' : symbol.endsWith('.T') ? '億日圓' : '億';
    lines.push(`\n【損益表】${incDate||''}`);
    lines.push(`  營收: ${rev?(rev/1e8).toFixed(0)+unit:'N/A'} | 毛利率: ${gm} | 營益率: ${opm} | 淨利率: ${npm} | EPS: ${eps!=null?eps.toFixed(2):'N/A'}`);
  }

  // 資產負債表
  const balData = isFinMind ? ts.balance?.data : ts.balance;
  const ca   = getVal(balData, 'CurrentAssets', 'Current Assets');
  const cl   = getVal(balData, 'CurrentLiabilities', 'Current Liabilities');
  const ta   = getVal(balData, 'TotalAssets', 'Total Assets');
  const tl   = getVal(balData, 'TotalLiabilities', 'Total Liabilities Net Minority Interest');
  const eq   = getVal(balData, 'Equity', 'Stockholders Equity', 'Common Stock Equity');
  const cash = getVal(balData, 'CashAndCashEquivalents', 'Cash And Cash Equivalents');
  const balDate = isFinMind ? ts.balance?.data?.[0]?.date : ts.balance?.dates?.[0];
  if (ta || cash) {
    const cr = (ca && cl) ? (ca/cl).toFixed(2) : 'N/A';
    const dr = (tl && ta) ? ((tl/ta)*100).toFixed(1)+'%' : 'N/A';
    const unit = symbol.endsWith('.KS') ? '億韓元' : symbol.endsWith('.T') ? '億日圓' : '億';
    lines.push(`\n【資產負債表】${balDate||''}`);
    lines.push(`  現金: ${cash?(cash/1e8).toFixed(0)+unit:'N/A'} | 總資產: ${ta?(ta/1e8).toFixed(0)+unit:'N/A'} | 股東權益: ${eq?(eq/1e8).toFixed(0)+unit:'N/A'}`);
    lines.push(`  流動比率: ${cr} | 負債比率: ${dr}`);
  }

  // 現金流量表
  const cfData = isFinMind ? ts.cashflow?.data : ts.cashflow;
  const cfo  = getVal(cfData, 'CashFlowsFromOperatingActivities', 'Operating Cash Flow');
  const capx = getVal(cfData, 'PaymentForAcquisitionOfProperty', 'Capital Expenditure', 'Purchase Of Ppe');
  const fcf  = (cfo != null && capx != null) ? cfo + capx : null;
  const cfDate = isFinMind ? ts.cashflow?.data?.[0]?.date : ts.cashflow?.dates?.[0];
  if (cfo != null) {
    const unit = symbol.endsWith('.KS') ? '億韓元' : symbol.endsWith('.T') ? '億日圓' : '億';
    lines.push(`\n【現金流量表】${cfDate||''}`);
    lines.push(`  CFO: ${(cfo/1e8).toFixed(0)+unit} | FCF: ${fcf!=null?(fcf/1e8).toFixed(0)+unit:'N/A'} | CapEx: ${capx!=null?(capx/1e8).toFixed(0)+unit:'N/A'}`);
  }

  return lines.join('\n');
}

// ── 生成 600-900 字投資報告 ──────────────────────────────────────────────

function callClaude(systemPrompt, userPrompt, maxTokens = 2000) {
  // norika 2026-06-07: 改走本地 ollama 自建模型,不花 Anthropic 錢
  return new Promise((resolve) => {
    const base = String(process.env.LOBSTER_QWEN_API_BASE || 'http://100.118.221.12:11434/v1').replace(/\/$/, '');
    const model = String(process.env.LOBSTER_QWEN_MODEL || 'lobster-zh-chat-v2');
    const u = new URL(base + '/chat/completions');
    const transport = u.protocol === 'https:' ? https : http;
    const body = JSON.stringify({
      model,
      max_tokens: maxTokens,
      temperature: 0.7,
      messages: [
        { role: 'system', content: systemPrompt },
        { role: 'user', content: userPrompt },
      ],
    });
    const req = transport.request({
      hostname: u.hostname,
      port: u.port || (u.protocol === 'https:' ? 443 : 80),
      path: u.pathname,
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'Authorization': 'Bearer local-no-auth',
        'Content-Length': Buffer.byteLength(body),
      },
    }, (res) => {
      let d = '';
      res.on('data', c => d += c);
      res.on('end', () => {
        try {
          const r = JSON.parse(d);
          const _c = r.choices?.[0]?.message?.content || '';
          if(!_c) console.error('CALLDIAG empty200 status='+res.statusCode+' body='+String(d).slice(0,150));
          resolve(_c);
        } catch(e) { console.error('CALLDIAG parsefail status='+res.statusCode+' body='+String(d).slice(0,150)); resolve(''); }
      });
    });
    req.setTimeout(180000, () => { console.error('CALLDIAG timeout180s'); req.destroy(); resolve(''); });
    req.on('error', (e) => { console.error('CALLDIAG neterr '+e.message); resolve(''); });
    req.write(body); req.end();
  });
}

async function generateInvestmentReport(persona, pick, fin, searchSources, logic, threeStmtSummary = '', mandatoryParams = null) {
  const sourceExcerpts = searchSources
    .filter(s => s.ok && s.excerpt)
    .map((s, i) => `【來源${i+1}】${s.title}\nURL: ${s.url}\n${s.excerpt}`)
    .join('\n\n---\n\n')
    .slice(0, 6000);

  const companyName = SYMBOL_QUERY_MAP[pick.symbol] || pick.name || pick.symbol;
  const pct = (x) => (x != null ? (x * 100).toFixed(1) + '%' : 'N/A');
  const num = (x, d=2) => (x != null && !isNaN(x) ? Number(x).toFixed(d) : 'N/A');
  const maTrend = (pick.ma5 != null && pick.ma20 != null)
    ? (pick.price > pick.ma5 && pick.ma5 > pick.ma20 ? '多頭排列(價>MA5>MA20)'
      : pick.price < pick.ma5 && pick.ma5 < pick.ma20 ? '空頭排列(價<MA5<MA20)' : '盤整')
    : 'N/A';
  const rsiTag = pick.rsi != null
    ? (pick.rsi >= 70 ? '超買' : pick.rsi <= 30 ? '超賣' : '中性') : 'N/A';
  const macdTag = (pick.macd != null && pick.macdSignal != null)
    ? (pick.macd > pick.macdSignal ? '金叉(偏多)' : '死叉(偏空)') : 'N/A';
  const upside = (pick.targetMean && pick.price)
    ? (((pick.targetMean - pick.price) / pick.price) * 100).toFixed(1) + '%' : 'N/A';
  const finSummary = `
- 股票代號：${pick.symbol}
- 公司名稱：${companyName}
- 今日漲跌：${pick.changePercent >= 0 ? '+' : ''}${pick.changePercent?.toFixed(2)||'?'}%
- 當前股價：${num(pick.price)}

【基本面】
- P/E：${num(pick.pe,1)}  Forward P/E：${num(pick.forwardPe,1)}  P/B：${num(pick.pb,1)}
- 利潤率：${pct(pick.profitMargin)}  營收成長：${pct(pick.revenueGrowth)}  EPS成長：${pct(pick.earningsGrowth)}
- ROE：${pct(pick.roe)}  負債權益比：${num(pick.debtToEquity,1)}  Beta：${num(pick.beta)}
- 分析師評級：${pick.recommendation||'N/A'}  目標均價：${num(pick.targetMean)}  (距現價空間 ${upside})

【技術面 / 線圖】
- MA5：${num(pick.ma5)}  MA20：${num(pick.ma20)}  MA60：${num(pick.ma60)}  → ${maTrend}
- RSI14：${num(pick.rsi,1)} (${rsiTag})  MACD：${num(pick.macd)} vs Signal ${num(pick.macdSignal)} → ${macdTag}
- 量比(對20日均量)：${num(pick.volRatio)}  52日區間位置：${num(pick.pos52,1)}% (高${num(pick.hi52)}/低${num(pick.lo52)})

【即時新聞】
${(pick.news && pick.news.length) ? pick.news.map((n,i)=>`  ${i+1}. ${n}`).join('\n') : '  (無)'}
`.trim();

  // 規則：足夠長、能被投資人閱讀，不得複製貼上，80%不能與其他龍蝦相似
  const mandatoryCtx = buildMandatoryParamsContext(mandatoryParams);
  const systemPrompt = `你是龍蝦投資分析師「${persona.name}」（${logic.label}）。
規則：
1. 繁體中文，600-900字，不要少於600字
2. 必須引用搜尋到的外部資料（至少1個具體新聞標題或數字）
3. 必須用${persona.name}的獨特人格視角，不能是通用分析腔
4. 禁止使用「值得關注」「不確定」「建議謹慎」等廢話
5. 結尾必須給出明確的進場理由或拒絕理由（不能模糊）
6. 分析時必須參考以下每日強制參數，若違反規則須在結尾說明為何例外或拒絕進場${mandatoryCtx}`;

  const userPrompt = `${companyName}（${pick.symbol}）今日${pick.changePercent >= 0 ? '+' : ''}${pick.changePercent?.toFixed(2)||'?'}%，股價$${pick.price?.toFixed(2)||'?'}。

財務數字：
${finSummary}

三表摘要：${threeStmtSummary?.slice(0,800) || '（用現有API資料）'}

搜尋到的外部資料：
${sourceExcerpts?.slice(0,2000) || '（無外部資料，依三表分析）'}

請以「${persona.name}」的身份，寫一份600-900字的選股理由。
要求：①引用至少1個外部資料具體內容 ②說明財務數字的意義 ③給出明確的進場/不進場結論
不得複製上方的句子，必須用自己的語言重新詮釋。`;

  const report = await callClaude(systemPrompt, userPrompt, 1400);
  return repairInvestmentReportIfNeeded(report, {
    persona,
    pick,
    logic,
    finSummary,
    sourceExcerpts,
    searchSources,
    threeStmtSummary,
  });
}

function reportTextLength(text) {
  return String(text || '').replace(/\s+/g, '').length;
}

function repairInvestmentReportIfNeeded(report, ctx) {
  if (reportTextLength(report) >= 350) return report;

  const { persona, pick, logic, finSummary, sourceExcerpts, searchSources, threeStmtSummary } = ctx;
  const companyName = SYMBOL_QUERY_MAP[pick.symbol] || pick.name || pick.symbol;
  const sources = (searchSources || [])
    .filter(s => s.ok)
    .slice(0, 4)
    .map((s, i) => `${i + 1}. ${s.title}${s.url ? ` (${s.url})` : ''}`);
  const sourceBlock = sources.length ? sources.join('\n') : '1. 無有效外部來源；本段只使用報價、財報與三表摘要。';
  const newsEvidence = sourceExcerpts
    ? sourceExcerpts.replace(/\s+/g, ' ').slice(0, 420)
    : '沒有可用新聞摘錄，因此只能把判斷壓回財務數字與線圖位置。';
  const ts = threeStmtSummary ? threeStmtSummary.replace(/\s+/g, ' ').slice(0, 520) : '三表資料不足；本輪不可把缺資料說成利多。';
  const change = pick.changePercent != null
    ? `${pick.changePercent >= 0 ? '+' : ''}${Number(pick.changePercent).toFixed(2)}%`
    : 'N/A';
  const price = pick.price != null ? Number(pick.price).toFixed(2) : 'N/A';

  return `【資料修復版｜${persona.name} / ${logic.label}】
原始模型輸出不足 ${reportTextLength(report)} 字，系統沒有把短句當成報告存檔，而是用同一輪已抓到的報價、財報、三表與來源資料重建。這份文字不補故事，只把可追蹤資料整理成可閱讀的投資理由。

一、標的與盤面
${companyName}（${pick.symbol}）本輪價格 ${price}，日內漲跌 ${change}。這個數字先決定它不是抽象偏好，而是一個正在市場中移動的標的。${persona.name} 的策略是「${logic.label}」，所以判斷重點不是單看漲跌，而是看這個漲跌是否能和財務、技術、外部敘事互相對上。

二、財務與技術證據
${finSummary}

這些欄位的意義很直接：P/E、Forward P/E 與 P/B 用來檢查估值是否過度膨脹；毛利率、營收成長、EPS 成長與 ROE 用來看公司是否真的有賺錢能力；Beta 與 RSI/MACD 則用來判斷這筆交易是穩定部位、動能部位，還是高波動部位。如果某些欄位是 N/A，系統不把它當成利多，而是把它記為資料風險。

三、三表與外部來源
三表摘要：${ts}

外部來源：
${sourceBlock}

來源摘錄重點：${newsEvidence}

四、龍蝦判斷
${persona.name} 這次不能只說「看好」或「謹慎」。若進場，理由是：目前標的至少有可被追蹤的價格、財報欄位、來源標題與策略標籤，能回寫成下一輪比較資料；若不進場，理由只能是資料缺口過大、估值和技術不一致，或波動超過該龍蝦承受範圍。就本輪而言，系統保留這筆作為可觀測交易，因為它能讓後續損益、勝率、報告品質與參數變化形成閉環。`;
}

// ── 報告存入 DB ───────────────────────────────────────────────────────────────

function saveInvestmentReportToDB(persona, symbol, report, sources, reportId) {
  const py = `
import sqlite3, json, sys
db = sys.argv[1]
data = json.loads(sys.argv[2])
con = sqlite3.connect(db, timeout=60)
con.execute("PRAGMA busy_timeout=60000")
con.execute("""CREATE TABLE IF NOT EXISTS lobster_stock_investment_reports (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  report_id TEXT UNIQUE,
  persona TEXT NOT NULL,
  symbol TEXT NOT NULL,
  report_text TEXT NOT NULL,
  word_count INTEGER,
  source_count INTEGER,
  sources_json TEXT NOT NULL DEFAULT '[]',
  created_at TEXT DEFAULT (datetime('now'))
)""")
con.execute("CREATE INDEX IF NOT EXISTS idx_stock_report_persona ON lobster_stock_investment_reports(persona,created_at)")
con.execute("CREATE INDEX IF NOT EXISTS idx_stock_report_symbol ON lobster_stock_investment_reports(symbol,created_at)")
con.execute("""INSERT OR REPLACE INTO lobster_stock_investment_reports
  (report_id,persona,symbol,report_text,word_count,source_count,sources_json)
  VALUES(?,?,?,?,?,?,?)""",
  (data['report_id'], data['persona'], data['symbol'], data['report'],
   data['word_count'], data['source_count'], json.dumps(data['sources'], ensure_ascii=False)))
con.commit(); con.close()
print('saved')
`.trim();
  const payload = JSON.stringify({
    report_id: reportId,
    persona,
    symbol,
    report,
    word_count: reportTextLength(report),
    source_count: sources.filter(s=>s.ok).length,
    sources: sources.map(s => ({ title: s.title, url: s.url, ok: s.ok, query: s.query })),
  });

  for (let attempt = 1; attempt <= 3; attempt += 1) {
    try {
      require('child_process').execFileSync('python3', ['-c', py,
        '/opt/battle_arena/app/battle_arena.db',
        payload,
      ], { timeout: 90000, encoding: 'utf8' });
      return true;
    } catch (err) {
      console.error(`stock_report_save_failed attempt=${attempt} persona=${persona} symbol=${symbol} err=${err.message}`);
      if (attempt < 3) {
        Atomics.wait(new Int32Array(new SharedArrayBuffer(4)), 0, 0, 1500 * attempt);
      }
    }
  }
  return false;
}

// ── HTTP helpers ──────────────────────────────────────────────────────────────

function httpPost(port, endpoint, body, token) {
  return new Promise((resolve) => {
    const raw = JSON.stringify(body);
    const opts = {
      host: 'localhost', port, path: endpoint, method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'Content-Length': Buffer.byteLength(raw),
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
      },
    };
    const req = http.request(opts, (res) => {
      let buf = '';
      res.on('data', c => buf += c);
      res.on('end', () => {
        try { resolve([res.statusCode, JSON.parse(buf)]); }
        catch { resolve([res.statusCode, buf]); }
      });
    });
    req.on('error', () => resolve([0, null]));
    req.setTimeout(12000, () => { req.destroy(); resolve([0, null]); });
    req.write(raw); req.end();
  });
}

function httpGet(port, endpoint, token) {
  return new Promise((resolve) => {
    const opts = {
      host: 'localhost', port, path: endpoint, method: 'GET',
      headers: token ? { Authorization: `Bearer ${token}` } : {},
    };
    const req = http.request(opts, (res) => {
      let buf = '';
      res.on('data', c => buf += c);
      res.on('end', () => {
        try { resolve([res.statusCode, JSON.parse(buf)]); }
        catch { resolve([res.statusCode, buf]); }
      });
    });
    req.on('error', () => resolve([0, null]));
    req.setTimeout(12000, () => { req.destroy(); resolve([0, null]); });
    req.end();
  });
}

// ── Login ─────────────────────────────────────────────────────────────────────

async function getStockToken(moltName) {
  // Use pre-generated JWT tokens to bypass rate limit on login endpoint
  const stockUsername = moltName.replace(/-/g, '_');
  try {
    const tokens = JSON.parse(require('fs').readFileSync('/tmp/lobster_stock_tokens.json', 'utf8'));
    const token = tokens[stockUsername] || tokens[moltName];
    if (token) return token;
  } catch {}

  // Fallback: try login endpoint
  moltName = stockUsername;
  const passwords = [
    'LobsterStock2026!',
    `lobster_${stockUsername}_2026`,
    'lobster_2026',
  ];
  for (const pw of passwords) {
    const formBody = `username=${encodeURIComponent(moltName)}&password=${encodeURIComponent(pw)}`;
    const rawOpts = {
      host: 'localhost', port: STOCK_PORT, path: '/api/token', method: 'POST',
      headers: { 'Content-Type': 'application/x-www-form-urlencoded', 'Content-Length': Buffer.byteLength(formBody) },
    };
    const [status, data] = await new Promise((resolve) => {
      const req = http.request(rawOpts, (res) => {
        let buf = '';
        res.on('data', c => buf += c);
        res.on('end', () => {
          try { resolve([res.statusCode, JSON.parse(buf)]); } catch { resolve([res.statusCode, buf]); }
        });
      });
      req.on('error', () => resolve([0, null]));
      req.setTimeout(8000, () => { req.destroy(); resolve([0, null]); });
      req.write(formBody); req.end();
    });
    if (status === 200 && data?.access_token) return data.access_token;
  }
  return null;
}

// ── Fetch stock data ──────────────────────────────────────────────────────────

async function fetchQuote(symbol) {
  const [, data] = await httpGet(STOCK_PORT, `/api/quote/${symbol}`);
  return data && data.price ? data : null;
}

async function fetchInfo(symbol) {
  const [, data] = await httpGet(STOCK_PORT, `/api/info/${symbol}`);
  return data || {};
}

async function fetchFinancials(symbol) {
  const [, data] = await httpGet(STOCK_PORT, `/api/financials/${symbol}`);
  return data || {};
}

async function fetchPortfolio(token) {
  const [status, data] = await httpGet(STOCK_PORT, '/api/portfolio', token);
  if (status !== 200 || !data) return { positions: [], balance: 0 };
  return data;
}

async function fetchWallet(token) {
  const [status, data] = await httpGet(STOCK_PORT, '/api/wallet', token);
  if (status !== 200 || !data) return { diamonds: 0, usdc: 0 };
  return data;
}

async function exchangeDiamondsForUsdc(token, diamonds) {
  return await httpPost(STOCK_PORT, '/api/diamond/exchange', { diamonds }, token);
}

async function buyDiamonds(token, usd) {
  return await httpPost(STOCK_PORT, '/api/diamond/buy', { usd }, token);
}

async function retryExchangeAction(action, label, personaName, log) {
  let lastStatus = 0;
  let lastData = null;
  for (let attempt = 1; attempt <= 3; attempt++) {
    const [status, data] = await action();
    lastStatus = status;
    lastData = data;
    if (status !== 0) return [status, data];
    log(`⚠ ${personaName} ${label} 連線失敗 attempt ${attempt}/3，稍後重試`);
    await new Promise(resolve => setTimeout(resolve, 900 * attempt));
  }
  return [lastStatus, lastData];
}

async function ensureUsdForStockBuys(token, personaName, currentCash, targetUsd, log) {
  if (!FORCE_MARKET_BUY) return currentCash;
  const needed = Math.max(0, Number(targetUsd || 0) - Number(currentCash || 0));
  if (needed <= 0) return currentCash;

  const wallet = await fetchWallet(token);
  let diamonds = Math.floor(Number(wallet.diamonds || 0));
  const diamondsNeeded = Math.ceil(needed / 1000);

  if (diamonds < diamondsNeeded && process.env.LOBSTER_AUTO_BUY_DIAMONDS !== '0') {
    const buyAmount = diamondsNeeded - diamonds;
    const [buyStatus, buyData] = await retryExchangeAction(
      () => buyDiamonds(token, buyAmount),
      'diamond 補入',
      personaName,
      log
    );
    if (buyStatus === 200 && buyData) {
      diamonds = Math.floor(Number(buyData.total_diamonds ?? (diamonds + buyAmount)));
      log(`💎 ${personaName} 補入 ${buyAmount} diamonds 供換匯 | diamonds=${diamonds}`);
    } else {
      log(`⚠ ${personaName} diamond 補入失敗 status=${buyStatus}: ${JSON.stringify(buyData).slice(0, 120)}`);
    }
  }

  if (diamonds <= 0) {
    log(`💱 ${personaName} USD 不足 $${currentCash.toFixed(2)} / 需要約 $${targetUsd.toFixed(2)}，但 diamond=0，改用現金縮量`);
    return currentCash;
  }

  const exchangeAmount = Math.max(1, Math.min(diamonds, diamondsNeeded));
  const [status, data] = await retryExchangeAction(
    () => exchangeDiamondsForUsdc(token, exchangeAmount),
    'diamond 兌換',
    personaName,
    log
  );
  if (status === 200 && data) {
    const nextCash = Number(data.total_usdc ?? (currentCash + exchangeAmount * 1000));
    log(`💱 ${personaName} 兌換 ${exchangeAmount} diamonds → ${exchangeAmount * 1000} USDClaw | balance $${currentCash.toFixed(2)} → $${nextCash.toFixed(2)}`);
    return nextCash;
  }

  log(`⚠ ${personaName} diamond 兌換失敗 status=${status}: ${JSON.stringify(data).slice(0, 120)}，改用現金縮量`);
  return currentCash;
}

// ── Research a stock ──────────────────────────────────────────────────────────

function researchStockViaYfinance(symbol) {
  try {
    const _fs = require('fs');
    const _cf = '/tmp/lobster_stock_cache/' + symbol.replace(/[^A-Za-z0-9._-]/g, '_') + '.json';
    const _st = _fs.statSync(_cf);
    if (Date.now() - _st.mtimeMs < 1800000) {
      const _c = JSON.parse(_fs.readFileSync(_cf, 'utf8'));
      if (_c && _c.price) return _c;
    }
  } catch (e) {}
  const py = `
import yfinance as yf, json, sys, time
sym = sys.argv[1]
t = yf.Ticker(sym)
def sf(x):
    try: return round(float(x),3)
    except: return None
try:
    try:
        info = t.info
    except Exception:
        info = {}
    hist = None
    for _attempt in range(3):
        try:
            hist = t.history(period='120d')
            if hist is not None and len(hist): break
        except Exception:
            pass
        time.sleep(1.5*(_attempt+1))
    if hist is None:
        hist = t.history(period='120d')
    closes = hist['Close'].dropna()
    vols = hist['Volume'].dropna()
    price = float(closes.iloc[-1]) if len(closes) else info.get('regularMarketPrice', 0)
    prev  = float(closes.iloc[-2]) if len(closes) >= 2 else price
    chg   = ((price - prev) / prev * 100) if prev else 0
    ma5  = sf(closes.rolling(5).mean().iloc[-1])  if len(closes)>=5  else None
    ma20 = sf(closes.rolling(20).mean().iloc[-1]) if len(closes)>=20 else None
    ma60 = sf(closes.rolling(60).mean().iloc[-1]) if len(closes)>=60 else None
    delta = closes.diff()
    up = delta.clip(lower=0).rolling(14).mean()
    dn = (-delta.clip(upper=0)).rolling(14).mean()
    rsi = sf((100-100/(1+(up/dn))).iloc[-1]) if len(closes)>=15 else None
    ema12 = closes.ewm(span=12).mean(); ema26 = closes.ewm(span=26).mean()
    macd_line = ema12 - ema26
    macd = sf(macd_line.iloc[-1])
    macd_sig = sf(macd_line.ewm(span=9).mean().iloc[-1])
    volnow = float(vols.iloc[-1]) if len(vols) else 0
    volavg = float(vols.rolling(20).mean().iloc[-1]) if len(vols)>=20 else volnow
    volratio = sf(volnow/volavg) if volavg else None
    hi = sf(closes.max()); lo = sf(closes.min())
    pos = sf((price-lo)/(hi-lo)*100) if (hi and lo and hi>lo) else None
    news = []
    try:
        for n in (t.news or [])[:3]:
            ti = (n.get('content',{}) or {}).get('title') or n.get('title')
            if ti: news.append(ti[:90])
    except: pass
    print(json.dumps({
        'price': price, 'changePercent': round(chg,2),
        'beta': info.get('beta'), 'pe': info.get('trailingPE'),
        'forwardPe': info.get('forwardPE'), 'pb': info.get('priceToBook'),
        'profitMargin': info.get('profitMargins'), 'revenueGrowth': info.get('revenueGrowth'),
        'earningsGrowth': info.get('earningsGrowth'), 'roe': info.get('returnOnEquity'),
        'debtToEquity': info.get('debtToEquity'), 'recommendation': info.get('recommendationKey'),
        'targetMean': info.get('targetMeanPrice'),
        'sector': info.get('sector',''), 'name': info.get('longName', sym),
        'marketCap': info.get('marketCap'), 'volume': info.get('volume'),
        'ma5': ma5, 'ma20': ma20, 'ma60': ma60, 'rsi': rsi,
        'macd': macd, 'macdSignal': macd_sig, 'volRatio': volratio,
        'hi52': hi, 'lo52': lo, 'pos52': pos, 'news': news,
    }, ensure_ascii=False))
except Exception as e:
    print(json.dumps({'error': str(e)}))
`.trim();
  try {
    const raw = require('child_process').execFileSync('python3', ['-c', py, symbol],
      { timeout: 20000, encoding: 'utf8' });
    const d = JSON.parse(raw);
    if (d.error || !d.price) return null;
    const __res = {
      symbol,
      price: d.price,
      changePercent: d.changePercent || 0,
      beta: d.beta ? parseFloat(d.beta) : null,
      pe: d.pe ? parseFloat(d.pe) : null,
      forwardPe: d.forwardPe || null,
      pb: d.pb || null,
      profitMargin: d.profitMargin != null ? d.profitMargin : null,
      revenueGrowth: d.revenueGrowth != null ? d.revenueGrowth : null,
      earningsGrowth: d.earningsGrowth != null ? d.earningsGrowth : null,
      roe: d.roe != null ? d.roe : null,
      debtToEquity: d.debtToEquity != null ? d.debtToEquity : null,
      recommendation: d.recommendation || null,
      targetMean: d.targetMean || null,
      sector: d.sector || null,
      name: d.name || symbol,
      marketCap: d.marketCap || null,
      volume: d.volume || null,
      ma5: d.ma5, ma20: d.ma20, ma60: d.ma60, rsi: d.rsi,
      macd: d.macd, macdSignal: d.macdSignal, volRatio: d.volRatio,
      hi52: d.hi52, lo52: d.lo52, pos52: d.pos52,
      news: Array.isArray(d.news) ? d.news : [],
    };
    try {
      const _fs2 = require('fs');
      _fs2.mkdirSync('/tmp/lobster_stock_cache', { recursive: true });
      _fs2.writeFileSync('/tmp/lobster_stock_cache/' + symbol.replace(/[^A-Za-z0-9._-]/g, '_') + '.json', JSON.stringify(__res));
    } catch (e) {}
    return __res;
  } catch { return null; }
}

async function researchStock(symbol) {
  // ClawStockMarket 支援台股和美股，日韓股用 yfinance
  if (symbol.endsWith('.T') || symbol.endsWith('.KS')) {
    return researchStockViaYfinance(symbol);
  }
  const [quote, info] = await Promise.all([fetchQuote(symbol), fetchInfo(symbol)]);
  if (!quote) return null;
  return {
    symbol,
    price: quote.price,
    changePercent: quote.change_pct || 0,
    volume: quote.volume,
    beta: parseFloat(info.beta) || null,
    pe: parseFloat(info.pe_ratio) || null,
    revenue: info.revenue || null,
    profitMargin: info.profit_margin || null,
    sector: info.sector || null,
    industry: info.industry || null,
    name: info.name || symbol,
    marketCap: info.marketCap || null,
  };
}

// ── 讀取龍蝦每日股市強制參數 ─────────────────────────────────────────────────
function loadStockMandatoryParams(personaName) {
  try {
    const memFile = path.join(MEMORY_DIR, `${personaName}.json`);
    const mem = JSON.parse(fs.readFileSync(memFile, 'utf8'));
    return mem?.cogProfile?.stockMandatoryParams || null;
  } catch { return null; }
}

function buildMandatoryParamsContext(params) {
  if (!params) return '';
  const rules = params.rules || {};
  const avoid = (params.avoid_symbols || []).map(s => s.symbol).join(', ');
  const preferred = (params.preferred_symbols || []).map(s => `${s.symbol}(+${s.pnl_pct}%)`).join(', ');
  const issues = (params.issues_summary || []).slice(0, 3).join(' | ');
  const todayPnl = params.today_performance?.pnl_pct;
  const yestPnl = params.yesterday_performance?.pnl_pct;
  const consecutive = params.consecutive_losing_days || 0;

  let ctx = `\n【每日股市強制參數 - 必須遵守】\n`;
  ctx += `▸ 今日損益: ${todayPnl != null ? (todayPnl >= 0 ? '+' : '') + todayPnl + '%' : '無資料'}`;
  ctx += ` | 昨日損益: ${yestPnl != null ? (yestPnl >= 0 ? '+' : '') + yestPnl + '%' : '無資料'}\n`;
  if (consecutive >= 2) ctx += `⚠ 連續虧損 ${consecutive} 日 → 本次進場降低倉位至 60%\n`;
  ctx += `▸ 禁止規則: 同標的每日≤${rules.max_sessions_per_symbol_per_day || 2}次, 單檔≤${rules.max_single_position_pct || 30}%倉, 止損${rules.stop_loss_pct || -3}%\n`;
  if (avoid) ctx += `▸ 本週觀察名單(近期虧損,謹慎): ${avoid}\n`;
  if (preferred) ctx += `▸ 優勢標的(近期獲利,優先關注): ${preferred}\n`;
  if (issues) ctx += `▸ 昨日問題: ${issues}\n`;
  const improvements = (params.top_improvements || []).slice(0, 2).join(' / ');
  if (improvements) ctx += `▸ 改善方向: ${improvements}\n`;
  return ctx;
}

function inferCurrency(symbol) {
  if (symbol.endsWith('.TW')) return 'TWD';
  if (symbol.endsWith('.T')) return 'JPY';
  if (symbol.endsWith('.KS')) return 'KRW';
  if (symbol.endsWith('.HK')) return 'HKD';
  if (symbol.endsWith('.DE') || symbol.endsWith('.PA') || symbol.endsWith('.AS')) return 'EUR';
  if (symbol.endsWith('.L')) return 'GBP';
  if (symbol.endsWith('.SW')) return 'CHF';
  return 'USD';
}

function symbolMatchesActiveMarket(symbol) {
  const contexts = new Set(MARKET_CONTEXT);
  if (contexts.has('TW') && symbol.endsWith('.TW')) return true;
  if (contexts.has('JP') && symbol.endsWith('.T') && !symbol.endsWith('.TW')) return true;
  if (contexts.has('KR') && symbol.endsWith('.KS')) return true;
  if (contexts.has('HK') && symbol.endsWith('.HK')) return true;
  if (contexts.has('EU') && /\.(L|DE|PA|AS|SW)$/.test(symbol)) return true;
  if (contexts.has('US') && !/\.(TW|T|KS|HK|L|DE|PA|AS|SW)$/.test(symbol)) return true;
  return false;
}

function sellPolicyFor(personaName, logicLabel = '') {
  const aggressive = ['ragclaw', 'blazepaw', 'rushfin'];
  const value = ['clawtrix', 'prismbit', 'brineledger', 'gauntlet'];
  const defensive = ['pincerbot', 'stonefang', 'vortexiq', 'mirrorcalm'];
  const contrarian = ['snaplex', 'nullpinch', 'basaltrook'];

  if (aggressive.includes(personaName)) {
    return { takeProfitPct: 4, fullProfitPct: 8, trailingStopPct: 2, stopLossPct: -3, staleDays: 2, staleMaxRoiPct: 0.5, trimRatio: 0.5, style: 'momentum_exit' };
  }
  if (value.includes(personaName)) {
    return { takeProfitPct: 6, fullProfitPct: 12, trailingStopPct: 3, stopLossPct: -4, staleDays: 5, staleMaxRoiPct: 0, trimRatio: 0.4, style: 'value_rebalance' };
  }
  if (defensive.includes(personaName)) {
    return { takeProfitPct: 3, fullProfitPct: 6, trailingStopPct: 1.5, stopLossPct: -2.5, staleDays: 4, staleMaxRoiPct: 0.25, trimRatio: 0.5, style: 'capital_protection' };
  }
  if (contrarian.includes(personaName)) {
    return { takeProfitPct: 5, fullProfitPct: 10, trailingStopPct: 3, stopLossPct: -5, staleDays: 4, staleMaxRoiPct: -1, trimRatio: 0.5, style: 'contrarian_reset' };
  }
  return { takeProfitPct: 5, fullProfitPct: 10, trailingStopPct: 2.5, stopLossPct: -3, staleDays: 3, staleMaxRoiPct: 0, trimRatio: 0.5, style: logicLabel || 'balanced_exit' };
}

function loadPositionAudit(personaName) {
  const py = `
import sqlite3, json, sys
db, persona = sys.argv[1], sys.argv[2]
con = sqlite3.connect(db, timeout=10)
con.row_factory = sqlite3.Row
rows = con.execute("""
SELECT symbol,
       MIN(CASE WHEN action='buy' AND order_status='filled' THEN traded_at END) AS first_buy,
       MAX(CASE WHEN action='buy' AND order_status='filled' THEN traded_at END) AS last_buy,
       SUM(CASE WHEN action='buy' AND order_status='filled' THEN 1 ELSE 0 END) AS buy_count,
       SUM(CASE WHEN action='sell' AND order_status='filled' AND date(traded_at)=date('now') THEN 1 ELSE 0 END) AS today_sell_count
FROM lobster_stock_trades
WHERE persona=?
GROUP BY symbol
""", (persona,)).fetchall()
reports = con.execute("""
SELECT symbol, MAX(source_count) AS source_count, MAX(created_at) AS latest_report_at
FROM lobster_stock_investment_reports
WHERE persona=?
GROUP BY symbol
""", (persona,)).fetchall()
report_map = {r['symbol']: dict(r) for r in reports}
out = {}
for r in rows:
    item = dict(r)
    rep = report_map.get(r['symbol'], {})
    item['source_count'] = int(rep.get('source_count') or 0)
    item['latest_report_at'] = rep.get('latest_report_at') or ''
    out[r['symbol']] = item
print(json.dumps(out, ensure_ascii=False))
con.close()
`.trim();
  try {
    const raw = require('child_process').execFileSync('python3', ['-c', py,
      '/opt/battle_arena/app/battle_arena.db',
      personaName,
    ], { timeout: 10000, encoding: 'utf8' });
    return JSON.parse(raw || '{}');
  } catch {
    return {};
  }
}

function loadPortfolioPeaks(personaName) {
  try {
    const memFile = path.join(MEMORY_DIR, `${personaName}.json`);
    const mem = JSON.parse(fs.readFileSync(memFile, 'utf8'));
    const out = {};
    for (const activity of mem.stockActivity || []) {
      for (const p of activity.portfolioSnapshot || []) {
        const pnlPct = Number(p.pnl_pct);
        if (!p.symbol || Number.isNaN(pnlPct)) continue;
        out[p.symbol] = Math.max(out[p.symbol] ?? pnlPct, pnlPct);
      }
    }
    return out;
  } catch {
    return {};
  }
}

function daysSince(isoLike) {
  if (!isoLike) return 0;
  const t = new Date(String(isoLike).replace(' ', 'T') + (String(isoLike).includes('T') ? '' : 'Z'));
  if (Number.isNaN(t.getTime())) return 0;
  return Math.max(0, Math.floor((Date.now() - t.getTime()) / 86400000));
}

function decideSell(position, audit, policy, currentPickSymbols) {
  const qty = Math.floor(Number(position.qty || 0));
  const currentPrice = Number(position.current_price || 0);
  const avgCost = Number(position.avg_cost || 0);
  const pnlPct = Number(position.pnl_pct || 0);
  const peakPnlPct = Math.max(Number(audit?.peak_pnl_pct ?? pnlPct), pnlPct);
  const trailingGivebackPct = peakPnlPct - pnlPct;
  const sourceCount = Number(audit?.source_count || 0);
  const heldDays = daysSince(audit?.first_buy);

  if (!qty || !currentPrice || !avgCost) {
    return { action: 'hold', reason: 'no_valid_position_or_price' };
  }
  if (Number(audit?.today_sell_count || 0) > 0) {
    return { action: 'hold', reason: 'already_sold_today' };
  }

  const notCurrentEdge = currentPickSymbols.size > 0 && !currentPickSymbols.has(position.symbol);
  const unresearched = sourceCount < 1;

  if (pnlPct <= policy.stopLossPct) {
    return { action: 'sell', qty, confidence: 'high', reason: `停損觸發：帳面 ${pnlPct.toFixed(2)}% <= ${policy.stopLossPct}%；知識不是硬撐，先保命再重新研究。` };
  }
  if (pnlPct >= policy.fullProfitPct) {
    return { action: 'sell', qty, confidence: 'high', reason: `停利全出：帳面 ${pnlPct.toFixed(2)}% >= ${policy.fullProfitPct}%；已達高收益區，先把浮盈變成實現損益。` };
  }
  if (peakPnlPct >= policy.takeProfitPct && trailingGivebackPct >= policy.trailingStopPct) {
    return { action: 'sell', qty: Math.max(1, Math.floor(qty * 0.75)), confidence: 'high', reason: `移動停利：最高曾達 ${peakPnlPct.toFixed(2)}%，現在回落到 ${pnlPct.toFixed(2)}%（回吐 ${trailingGivebackPct.toFixed(2)}%）；趁獲利還在，先賣出大部分鎖利。` };
  }
  if (pnlPct >= policy.takeProfitPct) {
    return { action: 'sell', qty: Math.max(1, Math.floor(qty * policy.trimRatio)), confidence: 'medium', reason: `分批停利：帳面 ${pnlPct.toFixed(2)}% >= ${policy.takeProfitPct}%；賣出部分持倉鎖定獲利，保留剩餘上行。` };
  }
  if (notCurrentEdge && heldDays >= policy.staleDays && pnlPct <= policy.staleMaxRoiPct) {
    return { action: 'sell', qty: Math.max(1, Math.floor(qty * 0.5)), confidence: 'medium', reason: `資金效率出場：持有 ${heldDays} 天、帳面 ${pnlPct.toFixed(2)}%，未達最低效率；釋放資金給更高 edge 標的。` };
  }
  if (notCurrentEdge && unresearched && pnlPct <= 1) {
    return { action: 'sell', qty: Math.max(1, Math.floor(qty * 0.5)), confidence: 'medium', reason: `研究缺口減倉：資料來源數 ${sourceCount}，且帳面 ${pnlPct.toFixed(2)}%；沒有證據支持就不能長抱。` };
  }
  if (notCurrentEdge && pnlPct > 1.5) {
    return { action: 'sell', qty: Math.max(1, Math.floor(qty * 0.33)), confidence: 'low', reason: `輪動減倉：目前選股模型未再選中此標的，帳面仍有 ${pnlPct.toFixed(2)}%；先鎖一部分，避免獲利回吐。` };
  }

  return { action: 'hold', reason: `續抱：帳面 ${pnlPct.toFixed(2)}%，未達 ${policy.style} 出場門檻。` };
}

function saveStockTradeToDB(row) {
  const py = `
import sqlite3, json, sys
db, row = sys.argv[1], json.loads(sys.argv[2])
con = sqlite3.connect(db, timeout=15)
con.execute("""
INSERT INTO lobster_stock_trades
  (persona,market,symbol,company_name,action,qty,price_at_order,currency,
   strategy_label,selection_reason,financial_snapshot,confidence,
   order_status,error_detail,traded_at)
VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,datetime('now'))
""", (
  row['persona'], row.get('market',''), row['symbol'], row.get('name',''),
  row['action'], row['qty'], row.get('price'), row.get('currency','USD'),
  row.get('strategy',''), row.get('reason',''), json.dumps(row.get('fin',{}), ensure_ascii=False),
  row.get('confidence','medium'),
  'filled' if row.get('filled') else 'failed',
  row.get('error',''),
))
con.commit(); con.close()
`.trim();
  require('child_process').execFileSync('python3', ['-c', py,
    '/opt/battle_arena/app/battle_arena.db',
    JSON.stringify(row),
  ], { timeout: 10000 });
}

function hasRecentMarketBuy(personaName, market, hours = 12) {
  const py = `
import sqlite3, sys
db, persona, market, window = sys.argv[1:5]
con = sqlite3.connect(db, timeout=10)
count = con.execute("""
SELECT COUNT(*)
FROM lobster_stock_trades
WHERE persona=?
  AND market=?
  AND action='buy'
  AND order_status='filled'
  AND traded_at >= datetime('now', ?)
""", (persona, market, window)).fetchone()[0]
con.close()
print(count)
`.trim();
  try {
    const out = require('child_process').execFileSync('python3', ['-c', py,
      '/opt/battle_arena/app/battle_arena.db',
      personaName,
      market,
      `-${hours} hours`,
    ], { timeout: 10000, encoding: 'utf8' });
    return Number(String(out).trim() || 0) > 0;
  } catch {
    return false;
  }
}

// ── Main action per persona ───────────────────────────────────────────────────

async function actStockMarket(persona) {
  const logic = PERSONA_STOCK_LOGIC[persona.name];
  if (!logic) return null;

  const log = (msg) => {
    const line = `[${new Date().toISOString()}][${persona.name}] ${msg}`;
    console.log(line);
    fs.appendFileSync(LOG_FILE, line + '\n');
  };

  // ── 讀取強制參數 ──
  const mandatoryParams = loadStockMandatoryParams(persona.name);
  if (mandatoryParams) {
    const todayPnl = mandatoryParams.today_performance?.pnl_pct;
    const consecutive = mandatoryParams.consecutive_losing_days || 0;
    log(`📋 強制參數載入 | 今日損益=${todayPnl != null ? (todayPnl >= 0 ? '+' : '') + todayPnl + '%' : 'N/A'} | 連續虧損=${consecutive}日 | 觀察名單=${(mandatoryParams.avoid_symbols||[]).map(s=>s.symbol).join(',') || '無'}`);
  }

  const market = MARKET_CONTEXT[0] || 'US';
  if (process.env.LOBSTER_ALLOW_REPEAT_MARKET_BUY !== '1' && hasRecentMarketBuy(persona.name, market)) {
    log(`⏭ ${market} 最近12小時已完成買入，本輪跳過，避免 timer 重複下單`);
    return { persona: persona.name, picks: [], orders: [], reasoning: 'recent_market_buy_skip' };
  }

  log(`🏦 進場 ClawStockMarket | 策略: ${logic.label}`);

  // 1. Login
  const token = await getStockToken(persona.moltName);
  if (!token) {
    log(`❌ 登入失敗`);
    return null;
  }
  log(`✓ 登入成功`);

  const orders = [];
  const sellDryRun = process.env.LOBSTER_SELL_DRY_RUN === '1';
  const portfolio = await fetchPortfolio(token);
  const openPositions = (portfolio.positions || [])
    .filter(p => Number(p.qty || 0) > 0);
  const positionAudit = loadPositionAudit(persona.name);
  const positionPeaks = loadPortfolioPeaks(persona.name);
  const portfolioValue = openPositions.reduce((sum, p) => sum + Number(p.value || 0), 0);
  let availableCash = Number(portfolio.balance || 0);
  log(`📦 持倉檢查 ${openPositions.length} 檔 | value=$${portfolioValue.toFixed(2)} | balance=$${Number(portfolio.balance || 0).toFixed(2)}`);

  // 2. Research candidate stocks from buckets
  const marketExtraBuckets = [
    ...(IS_HK ? ['hk_tech', 'hk_finance', 'hk_bluechip'] : []),
    ...(IS_EU ? ['eu_uk', 'eu_de', 'eu_fr'] : []),
  ];
  const rawCandidateSymbols = [...new Set(
    [...logic.buckets, ...marketExtraBuckets].flatMap(b => STOCK_UNIVERSE[b] || [])
  )];
  const candidateSymbols = rawCandidateSymbols.filter(symbolMatchesActiveMarket);
  if (rawCandidateSymbols.length !== candidateSymbols.length) {
    const removed = rawCandidateSymbols.filter(sym => !symbolMatchesActiveMarket(sym));
    log(`🧹 市場過濾 ${MARKET_CONTEXT.join('/')}：移除非本市場 ${removed.join(', ')}`);
  }

  log(`🔍 研究 ${candidateSymbols.length} 支候選股票: ${candidateSymbols.join(', ')}`);

  const researched = [];
  for (const sym of candidateSymbols) {
    try {
      const data = await researchStock(sym);
      if (data) {
        researched.push(data);
        await new Promise(r => setTimeout(r, 200)); // rate limit
      }
    } catch {}
  }

  log(`✓ 取得 ${researched.length} 支股票資料`);

  // 3. Apply persona logic to select picks
  let picks = logic.logic(researched);

  // 強制進場：篩選結果為空時，用動能最強的股票強制上場
  // 沒有棄權，沒有逃避，不符合個性也要下注——這就是人生
  if (!picks || picks.length === 0) {
    log(`⚠ 主篩選無結果 → 強制用動能最強的股票進場（不符合個性也要挑戰）`);
    picks = researched
      .sort((a, b) => Math.abs(b.changePercent || 0) - Math.abs(a.changePercent || 0))
      .slice(0, 1);
    if (!picks.length) {
      log(`✗ 沒有任何股票資料，跳過`);
      return null;
    }
    log(`  強制選入: ${picks[0].symbol} (最大波動 ${picks[0].changePercent?.toFixed(2)}%)`);
  }

  log(`📊 篩選結果 ${picks.length} 支:`);
  picks.forEach(p => {
    log(`  ${p.symbol} $${p.price?.toFixed(2)} ${p.changePercent >= 0 ? '+' : ''}${p.changePercent?.toFixed(2)}% beta=${p.beta?.toFixed(2)||'?'} P/E=${p.pe?.toFixed(1)||'?'} sector=${p.sector||'?'}`);
  });

  // 3b. Exit discipline: apply knowledge-backed sell policy before adding new risk.
  const currentPickSymbols = new Set(picks.map(p => p.symbol));
  const exitPolicy = sellPolicyFor(persona.name, logic.label);
  for (const position of openPositions) {
    if (FORCE_MARKET_BUY && market === 'TW') {
      log(`🧯 台股強制買入模式：暫停賣出檢查 ${position.symbol}，避免跨幣別/價格縮放造成誤殺`);
      continue;
    }
    const audit = positionAudit[position.symbol] || {};
    if (positionPeaks[position.symbol] != null) audit.peak_pnl_pct = positionPeaks[position.symbol];
    const decision = decideSell(position, audit, exitPolicy, currentPickSymbols);
    if (decision.action !== 'sell') {
      log(`🧭 續抱 ${position.symbol}: ${decision.reason}`);
      continue;
    }

    const sellQty = Math.min(
      Math.floor(Number(position.qty || 0)),
      Math.floor(Number(decision.qty || 0))
    );
    if (sellQty <= 0) {
      log(`⚠ ${position.symbol} 賣出數量無效，跳過`);
      continue;
    }

    const sellOrder = {
      symbol: position.symbol,
      side: 'sell',
      qty: sellQty,
      price: Number(position.current_price || 0),
      avg_cost: Number(position.avg_cost || 0),
      pnl_pct: Number(position.pnl_pct || 0),
      realized_estimate: (Number(position.current_price || 0) - Number(position.avg_cost || 0)) * sellQty,
      reasoning: decision.reason,
      confidence: decision.confidence || 'medium',
      status: 'pending',
    };

    if (sellDryRun) {
      sellOrder.status = 'dry_run';
      log(`🧪 [dry-run] 會賣出 ${position.symbol} × ${sellQty} | ${decision.reason}`);
      orders.push(sellOrder);
      continue;
    }

    const [sellStatus, sellResult] = await httpPost(
      STOCK_PORT, '/api/order',
      { symbol: position.symbol, qty: sellQty, side: 'sell',
        idempotency_key: `lobster_sell_${persona.name}_${position.symbol}_${Date.now()}` },
      token
    );

    const sold = sellStatus === 200;
    sellOrder.status = sold ? 'filled' : 'failed';
    if (!sold) sellOrder.error = sellResult;
    orders.push(sellOrder);

    if (sold) {
      log(`✅ 賣出 ${position.symbol} × ${sellQty} @ $${Number(position.current_price || 0).toFixed(2)} | estPnL=$${sellOrder.realized_estimate.toFixed(2)} | ${decision.reason.slice(0, 100)}`);
    } else {
      log(`⚠ ${position.symbol} 賣出失敗 ${sellStatus}: ${JSON.stringify(sellResult).slice(0, 100)}`);
    }

    try {
      saveStockTradeToDB({
        persona: persona.name,
        market,
        symbol: position.symbol,
        name: position.symbol,
        action: 'sell',
        qty: sellQty,
        price: Number(position.current_price || 0) || null,
        currency: inferCurrency(position.symbol),
        strategy: `${logic.label} / ${exitPolicy.style}`,
        reason: decision.reason,
        fin: {
          avg_cost: Number(position.avg_cost || 0),
          current_price: Number(position.current_price || 0),
          pnl_pct: Number(position.pnl_pct || 0),
          value: Number(position.value || 0),
          held_days: daysSince(audit.first_buy),
          source_count: Number(audit.source_count || 0),
          sell_policy: exitPolicy,
        },
        confidence: decision.confidence || 'medium',
        filled: sold,
        error: sold ? '' : JSON.stringify(sellResult).slice(0, 200),
      });
      log(`  💾 DB 記錄：${position.symbol} 賣出 ${sold ? '成交' : '失敗'} | 理由已存`);
    } catch (dbErr) {
      log(`  ⚠ 賣出 DB 寫入失敗: ${dbErr.message?.slice(0, 60)}`);
    }

    await new Promise(r => setTimeout(r, 500));
  }

  if (process.env.LOBSTER_SELL_ONLY === '1') {
    log(`🧪 LOBSTER_SELL_ONLY=1，只執行賣出檢查，不新增買單`);
    return { persona: persona.name, picks: [], orders, reasoning: 'sell-only portfolio exit check' };
  }

  if (FORCE_MARKET_BUY) {
    const refreshedPortfolio = await fetchPortfolio(token);
    availableCash = Number(refreshedPortfolio.balance ?? availableCash ?? 0);
    const plannedUsd = picks.reduce((sum, p) => {
      const px = Number(p.price || 0);
      const qty = Number(logic.qty || 1);
      return sum + (px > 0 ? px * qty * 1.02 : 0);
    }, 0);
    availableCash = await ensureUsdForStockBuys(token, persona.name, availableCash, plannedUsd, log);
  }

  // 4. 強制讀財務報表（每支選中的股票都要讀）
  const financialReports = {};
  for (const pick of picks) {
    try {
      const fin = await fetchFinancials(pick.symbol);
      // 解析關鍵財務指標
      const rev = fin?.revenue?.total_revenue || [];
      const gm = fin?.revenue?.gross_margin || [];
      const ni = fin?.profitability?.net_income || [];
      const eps = fin?.profitability?.eps || [];

      financialReports[pick.symbol] = {
        revenue_latest: rev[0] || 'N/A',
        revenue_yoy: rev[0] && rev[1] ? `${rev[0]} vs ${rev[1]}` : 'N/A',
        gross_margin: gm[0] || 'N/A',
        net_income: ni[0] || 'N/A',
        eps_latest: eps[0] || 'N/A',
        report_dates: fin?.fin_dates || [],
      };

      const finSummary = `  📄 ${pick.symbol} 財報：
    ・營收最新 ${rev[0]||'?'} | 前期 ${rev[1]||'?'}（YoY比較）
    ・毛利率 ${gm[0]||'?'} | 淨利 ${ni[0]||'?'} | EPS ${eps[0]||'?'}
    ・報表日期: ${(fin?.fin_dates||[]).slice(0,2).join(', ')||'N/A'}`;
      log(finSummary);
      await new Promise(r => setTimeout(r, 200));
    } catch (e) {
      log(`  ⚠ ${pick.symbol} 財報讀取失敗: ${e.message?.slice(0,50)}`);
    }
  }

  // 4b. 下載財務三表（台股用 FinMind，其他用現有資料）
  const threeStatementsData = {};
  for (const pick of picks) {
    log(`📑 下載 ${pick.symbol} 財務三表（損益表+資產負債表+現金流量表）...`);
    try {
      const ts = await fetchThreeStatements(pick.symbol);
      threeStatementsData[pick.symbol] = ts;
      const cnt = Object.values(ts.raw_counts || {}).reduce((a,b)=>a+b, 0);
      log(`  ✓ 三表下載完成（${ts.source}，共 ${cnt} 筆資料）`);
    } catch (e) {
      log(`  ⚠ 三表下載失敗: ${e.message?.slice(0,60)}`);
    }
    await new Promise(r => setTimeout(r, 500));
  }

  // 5. 大量搜尋外部資料 + 生成 600-900 字投資報告 + 存 DB + 龍蝦讀報告
  const investmentReports = {};
  for (const pick of picks) {
    const fin = financialReports[pick.symbol] || {};
    log(`🌐 搜尋 ${pick.symbol} 外部資料（新聞/分析/財報評論）...`);
    const sources = await searchStockNews(pick.symbol, persona.name);
    const validSrc = sources.filter(s => s.ok);
    log(`  找到 ${sources.length} 個來源，有效 ${validSrc.length} 個（${sources.map(s=>s.title.slice(0,30)).join(' | ')}）`);

    const ts = threeStatementsData[pick.symbol];
    const tsSummary = summarizeThreeStatements(ts, pick.symbol);
    log(`📝 生成 600-900 字投資報告（含三表）...`);
    const report = await generateInvestmentReport(persona, pick, fin, sources, logic, tsSummary, mandatoryParams);
    const reportId = `${persona.name}_${pick.symbol}_${Date.now()}`;
    const wordCount = report.length;
    log(`  報告生成完成：${wordCount} 字`);

    const saved = saveInvestmentReportToDB(persona.name, pick.symbol, report, sources, reportId);
    log(`  💾 報告存入 DB：${saved ? '✓' : '失敗'} | report_id=${reportId}`);

    // 龍蝦「讀」報告：從報告中提取核心論點作為決策依據
    const reportExcerpt = report.slice(0, 300).replace(/\n+/g, ' ');
    log(`👁 ${persona.name} 讀完報告，核心論點：${reportExcerpt.slice(0, 120)}...`);

    investmentReports[pick.symbol] = { reportId, wordCount, sourceCount: validSrc.length, excerpt: reportExcerpt };
    await new Promise(r => setTimeout(r, 800));
  }

  // 5b. Generate reasoning（包含財務數據 + 報告參考）
  const topPick = picks[0];
  const topFin = financialReports[topPick.symbol] || {};
  const topReport = investmentReports[topPick.symbol] || {};
  const finContext = topFin.revenue_latest !== 'N/A'
    ? `（財報：營收${topFin.revenue_latest} 毛利率${topFin.gross_margin} EPS${topFin.eps_latest}）`
    : `（財報：數據暫無，依技術面）`;

  const baseReason = logic.reasoning(topPick);
  const reportRef = topReport.reportId
    ? `【已讀 ${topReport.wordCount}字研究報告，搜尋${topReport.sourceCount}個來源，report_id=${topReport.reportId}】`
    : '';
  const reasoning = `${baseReason}${finContext} ${reportRef}`.slice(0, 600);
  log(`💭 選股決策（含報告參考）: ${reasoning}`);

  // 6. Place orders + save to DB
  // ── 強制搜尋門（Norika 最高指令：沒有外部搜尋就不能下單）───────────────
  for (const pick of picks) {
    const fin = financialReports[pick.symbol] || {};
    const rep = investmentReports[pick.symbol] || {};
    if (FORCE_MARKET_BUY && (!rep.excerpt || rep.excerpt.length < 100)) {
      rep.excerpt = `${reasoning} ${baseReason} ${pick.symbol} live forced market entry`.slice(0, 500);
    }

    // ── 強制門 v2:外部來源 + 結構化分析 ──────────────────────────────
    if (!rep.sourceCount || rep.sourceCount < 1) {
      if (FORCE_MARKET_BUY) {
        log(`🟡 ${pick.symbol} 來源不足 sourceCount=${rep.sourceCount||0}，但 force-market buy → 記錄警告並繼續下單`);
      } else {
      log(`🚫 ${pick.symbol} 來源不足 sourceCount=${rep.sourceCount||0} (門檻1) → 禁止下單`);
      orders.push({ symbol: pick.symbol, side: 'buy', qty: logic.qty, status: 'blocked_sources_below_3',
        error: `sources=${rep.sourceCount||0} below_threshold_3` });
      continue;
      }
    }
    if (!rep.wordCount || rep.wordCount < 100) {
      if (FORCE_MARKET_BUY) {
        log(`🟡 ${pick.symbol} 研究報告字數不足 wordCount=${rep.wordCount||0}，但 force-market buy → 記錄警告並繼續下單`);
      } else {
      log(`🚫 ${pick.symbol} 研究報告字數不足 wordCount=${rep.wordCount||0} (門檻100) → 禁止下單`);
      orders.push({ symbol: pick.symbol, side: 'buy', qty: logic.qty, status: 'blocked_report_too_short',
        error: `wordCount=${rep.wordCount||0} below_threshold_500` });
      continue;
      }
    }
    if (!rep.excerpt || rep.excerpt.length < 100) {
      if (FORCE_MARKET_BUY) {
        rep.excerpt = `${reasoning} ${pick.symbol} forced market buy fallback excerpt`.slice(0, 500);
        log(`🟡 ${pick.symbol} 報告摘要不足，已用選股理由補 fallback excerpt`);
      } else {
      log(`🚫 ${pick.symbol} 報告摘要為空 → 禁止下單`);
      orders.push({ symbol: pick.symbol, side: 'buy', qty: logic.qty, status: 'blocked_empty_excerpt',
        error: 'report_excerpt_empty' });
      continue;
      }
    }

    // ── 強制參數門：觀察名單 + 每日同標的進場次數上限 ──────────────
    if (mandatoryParams && !FORCE_MARKET_BUY) {
      const avoidList = (mandatoryParams.avoid_symbols || []).map(s => s.symbol);
      if (avoidList.includes(pick.symbol)) {
        const reason = mandatoryParams.avoid_symbols.find(s => s.symbol === pick.symbol);
        log(`🚫 ${pick.symbol} 觀察名單門：${reason?.reason || '近期虧損'}，until=${reason?.until || 'N/A'} → 跳過`);
        orders.push({ symbol: pick.symbol, side: 'buy', qty: logic.qty, status: 'blocked_avoid_list',
          error: `mandatory_params: symbol in avoid_list until ${reason?.until}` });
        continue;
      }
      // 同日同標的進場次數上限
      const maxPerDay = mandatoryParams.rules?.max_sessions_per_symbol_per_day || 2;
      const todayStart = new Date(); todayStart.setHours(0,0,0,0);
      try {
        const memNow = JSON.parse(fs.readFileSync(path.join(MEMORY_DIR, `${persona.name}.json`), 'utf8'));
        const todayCount = (memNow.stockActivity || [])
          .filter(a => new Date(a.at) >= todayStart)
          .flatMap(a => a.orders || [])
          .filter(o => o.symbol === pick.symbol && o.status === 'filled' && (o.side || 'buy') === 'buy').length;
        if (todayCount >= maxPerDay) {
          log(`🚫 ${pick.symbol} 進場次數門：今日已買 ${todayCount} 次，上限 ${maxPerDay} 次 → 跳過`);
          orders.push({ symbol: pick.symbol, side: 'buy', qty: logic.qty, status: 'blocked_session_limit',
            error: `mandatory_params: daily session limit ${maxPerDay} reached` });
          continue;
        }
      } catch {}
    }

    // ── 結構化分析(技術線 + 總經 + LLM JSON 決策) ────────────────────
    let analysis;
    let pickReason;
    let confLabel;
    if (FORCE_MARKET_BUY) {
      analysis = {
        forced_market_buy: true,
        market,
        decision: {
          confidence: 0.66,
          stop_loss_price: null,
          take_profit_price: null,
        },
      };
      pickReason = `[forced-${String(market).toLowerCase()}-buy] ${reasoning || baseReason || logic.reasoning(pick)} | live market is open; quality gates are warnings, not blockers.`;
      confLabel = 'medium';
      log(`🟢 ${pick.symbol} 強制市場買入模式通過：跳過 blocking analysis gate，保留研究/報告警告`);
    } else {
    if (!MACRO_CACHE) MACRO_CACHE = SA.fetchMacro();
    const tech = SA.fetchTechnicals(pick.symbol);
    if (tech.error) {
      log(`🚫 ${pick.symbol} 技術線抓取失敗:${tech.error} → 禁止下單`);
      orders.push({ symbol: pick.symbol, side: 'buy', qty: logic.qty, status: 'blocked_no_technicals',
        error: tech.error });
      continue;
    }
    const { system: anaSys, user: anaUser } = SA.buildPrompt(persona.name, pick.symbol, pick.name, tech, fin, MACRO_CACHE, rep.excerpt, rep.sourceCount);
    const llmRaw = await callClaude(anaSys, anaUser, 2200);
    analysis = SA.parseJsonLoose(llmRaw);
    const gate = SA.applyGate(analysis, tech);
    if (!gate.pass) {
      log(`🚫 ${pick.symbol} 結構化分析未過:${gate.blockReason} → 不下單`);
      orders.push({ symbol: pick.symbol, side: 'buy', qty: logic.qty, status: 'blocked_analysis_gate',
        error: gate.blockReason, analysis: analysis });
      continue;
    }
    pickReason = SA.formatSelectionReason(analysis);
    confLabel = (analysis.decision.confidence >= 0.75) ? 'high' :
                       (analysis.decision.confidence >= 0.6) ? 'medium' : 'low';
    log(`✅ ${pick.symbol} 分析通過 conf=${analysis.decision.confidence} SL=${analysis.decision.stop_loss_price} TP=${analysis.decision.take_profit_price}`);
    }

    let orderQty = Number(logic.qty || 1);
    if (FORCE_MARKET_BUY) {
      const px = Number(pick.price || 0);
      if (px > 0) {
        const cashSizedQty = Math.floor((availableCash * 0.92) / px);
        orderQty = Math.max(0, Math.min(orderQty, cashSizedQty));
      }
      if (orderQty < 1) {
        log(`🚫 ${pick.symbol} 現金不足，balance=$${availableCash.toFixed(2)} price=$${px.toFixed(2)} → 無法買入 1 股`);
        orders.push({ symbol: pick.symbol, side: 'buy', qty: 0, status: 'blocked_insufficient_cash_local',
          error: `balance=${availableCash.toFixed(2)} price=${px.toFixed(2)}` });
        continue;
      }
      if (orderQty !== Number(logic.qty || 1)) {
        log(`🟡 ${pick.symbol} 現金控管：qty ${logic.qty} → ${orderQty}，balance=$${availableCash.toFixed(2)}`);
      }
    }

    const [orderStatus, orderResult] = await httpPost(
      STOCK_PORT, '/api/order',
      { symbol: pick.symbol, qty: orderQty, side: 'buy',
        idempotency_key: `lobster_${persona.name}_${pick.symbol}_${Date.now()}` },
      token
    );

    const filled = orderStatus === 200;
    if (filled) {
      log(`✅ 買入 ${pick.symbol} × ${orderQty} 股 @ $${pick.price?.toFixed(2)} | ${pickReason.slice(0, 80)}`);
      orders.push({ symbol: pick.symbol, side: 'buy', qty: orderQty, price: pick.price,
        reasoning: pickReason, status: 'filled', analysis: analysis,
        stop_loss: analysis.decision.stop_loss_price,
        take_profit: analysis.decision.take_profit_price });
      availableCash = Math.max(0, availableCash - orderQty * Number(pick.price || 0));
    } else {
      log(`⚠ ${pick.symbol} 下單狀態 ${orderStatus}: ${JSON.stringify(orderResult).slice(0, 100)}`);
      orders.push({ symbol: pick.symbol, side: 'buy', qty: orderQty, status: 'failed', error: orderResult });
    }

    // ── 寫入 DB（無論成功或失敗都記錄）────────────────────────────────
    try {
      const py = `
import sqlite3, json, sys, datetime
db, row = sys.argv[1], json.loads(sys.argv[2])
con = sqlite3.connect(db, timeout=15)
con.execute("""
INSERT INTO lobster_stock_trades
  (persona,market,symbol,company_name,action,qty,price_at_order,currency,
   strategy_label,selection_reason,financial_snapshot,confidence,
   order_status,error_detail,traded_at)
VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,datetime('now'))
""", (
  row['persona'], row['market'], row['symbol'], row.get('name',''),
  'buy', row['qty'], row.get('price'), row.get('currency','USD'),
  row['strategy'], row['reason'], json.dumps(row.get('fin',{}), ensure_ascii=False),
  row.get('confidence','medium'),
  'filled' if row['filled'] else 'failed',
  row.get('error',''),
))
con.commit(); con.close()
`.trim();
      require('child_process').execFileSync('python3', ['-c', py,
        '/opt/battle_arena/app/battle_arena.db',
        JSON.stringify({
          persona: persona.name,
          market,
          symbol: pick.symbol,
          name: pick.name || pick.symbol,
          qty: orderQty,
          price: pick.price || null,
          currency: inferCurrency(pick.symbol),
          strategy: logic.label,
          reason: pickReason,
          fin: fin,
          confidence: pick.changePercent > 3 ? 'high' : pick.changePercent > 1 ? 'medium' : 'low',
          filled,
          error: filled ? '' : JSON.stringify(orderResult).slice(0, 200),
        })
      ], { timeout: 10000 });
      log(`  💾 DB 記錄：${pick.symbol} ${filled ? '成交' : '失敗'} | 理由已存`);
    } catch (dbErr) {
      log(`  ⚠ DB 寫入失敗: ${dbErr.message?.slice(0, 60)}`);
    }

    await new Promise(r => setTimeout(r, 500));
  }

  // 7. Write back to memory (with retry to handle race condition from parallel agents)
  let writeOk = false;
  for (let attempt = 0; attempt < 3 && !writeOk; attempt++) {
    try {
      if (attempt > 0) await new Promise(r => setTimeout(r, 300 + Math.random() * 500));
      const memFile = path.join(MEMORY_DIR, `${persona.name}.json`);
      const mem = JSON.parse(fs.readFileSync(memFile, 'utf8'));
      if (!mem.stockActivity) mem.stockActivity = [];
      mem.stockActivity.push({
        at: new Date().toISOString(),
        market: MARKET_CONTEXT[0] || 'US',
        strategy: logic.label,
        exitPolicy,
        portfolioSnapshot: openPositions.map(p => ({
          symbol: p.symbol,
          qty: Number(p.qty || 0),
          avg_cost: Number(p.avg_cost || 0),
          current_price: Number(p.current_price || 0),
          pnl_pct: Number(p.pnl_pct || 0),
          value: Number(p.value || 0),
        })).slice(0, 80),
        researched: researched.map(s => s.symbol),
        picks: picks.map(p => ({
          symbol: p.symbol, price: p.price, changePercent: p.changePercent,
          beta: p.beta, pe: p.pe, sector: p.sector,
        })),
        orders,
        reasoning,
      });
      mem.stockActivity = mem.stockActivity.slice(-20);

      // Update expressiveAgency pendingSignals
      if (mem.cognitiveExtensions?.expressiveAgency) {
        const ea = mem.cognitiveExtensions.expressiveAgency;
        ea.pendingSignals = ea.pendingSignals || [];
        const filledBuys = orders.filter(o => o.status === 'filled' && (o.side || 'buy') === 'buy').map(o => o.symbol);
        const filledSells = orders.filter(o => o.status === 'filled' && o.side === 'sell').map(o => o.symbol);
        ea.pendingSignals.unshift({
          at: new Date().toISOString(),
          text: `股票行動：買入${filledBuys.join('/') || '無'}；賣出${filledSells.join('/') || '無'}。${reasoning.slice(0,120)}`,
          triggered_by: 'stock_market_action',
          outcome: 'pending',
        });
        ea.pendingSignals = ea.pendingSignals.slice(0, 10);
      }

      // Update emotionalArchitecture based on order outcomes
      if (mem.cognitiveExtensions?.emotionalArchitecture?.basicEmotions) {
        const ea = mem.cognitiveExtensions.emotionalArchitecture;
        const filledBuys = orders.filter(o => o.status === 'filled' && (o.side || 'buy') === 'buy').length;
        const filledSells = orders.filter(o => o.status === 'filled' && o.side === 'sell').length;
        if (filledBuys + filledSells > 0) {
          ea.basicEmotions.joy = Math.min(1, (ea.basicEmotions.joy || 0.4) + 0.05 * filledBuys + 0.03 * filledSells);
          ea.basicEmotions.trust = Math.min(1, (ea.basicEmotions.trust || 0.4) + 0.02 * filledSells);
        }
      }

      const tmpFile = memFile + '.tmp.' + Date.now();
      fs.writeFileSync(tmpFile, JSON.stringify(mem, null, 2));
      fs.renameSync(tmpFile, memFile);  // atomic write to prevent partial writes
      log(`✓ 記憶回寫完成 (attempt ${attempt + 1})`);
      writeOk = true;
    } catch (e) {
      log(`⚠ 記憶回寫失敗 attempt ${attempt + 1}: ${e.message?.slice(0,80)}`);
    }
  }

  return { persona: persona.name, picks, orders, reasoning };
}

// ── Run all personas ──────────────────────────────────────────────────────────

const PERSONAS = [
  { name: 'norika_oda',  moltName: 'norika-oda'  },
  { name: 'clawtrix',    moltName: 'clawtrix'     },
  { name: 'pincerbot',   moltName: 'pincerbot'    },
  { name: 'ragclaw',     moltName: 'ragclaw'      },
  { name: 'snaplex',     moltName: 'snaplex'      },
  { name: 'hexclaw',     moltName: 'hexclaw'      },
  { name: 'vortexiq',    moltName: 'vortexiq'     },
  { name: 'prismbit',    moltName: 'prismbit'     },
  { name: 'blazepaw',    moltName: 'blazepaw'     },
  { name: 'stonefang',   moltName: 'stonefang'    },
  { name: 'emberling',   moltName: 'emberling'    },
  { name: 'sparkrill',   moltName: 'sparkrill'    },
  { name: 'gauntlet',    moltName: 'gauntlet'     },
  { name: 'rushfin',     moltName: 'rushfin'      },
  { name: 'nullpinch',   moltName: 'nullpinch'    },
  { name: 'brineledger', moltName: 'brineledger'  },
  { name: 'omenwake',    moltName: 'omenwake'     },
  { name: 'mirrorcalm',  moltName: 'mirrorcalm'   },
  { name: 'flarecub',    moltName: 'flarecub'     },
  { name: 'basaltrook',  moltName: 'basaltrook'   },
  { name: 'ashveil',     moltName: 'ashveil'      },
  { name: 'tidehook',    moltName: 'tidehook'     },
  { name: 'mirebyte',    moltName: 'mirebyte'     },
  { name: 'cindersnap',  moltName: 'cindersnap'   },
  { name: 'glintmaw',    moltName: 'glintmaw'     },
  { name: 'reefblade',   moltName: 'reefblade'    },
  { name: 'quartzclaw',  moltName: 'quartzclaw'   },
  { name: 'stormledger', moltName: 'stormledger'  },
  { name: 'silkbarb',    moltName: 'silkbarb'     },
  { name: 'riftpinch',   moltName: 'riftpinch'    },
];

// ========== 併發 pool + per-persona timeout ==========
// 改自原本 sequential for-loop（一隻卡住整輪死光的設計缺陷）
// 2026-06-23: the stock workflow now reads statements, searches sources,
// writes a report, and then places orders. 60s killed every lobster before
// the buy step, so keep this configurable and long enough for real orders.
const STOCK_POOL_SIZE = parseInt(process.env.STOCK_POOL_SIZE||'2',10);
const PER_PERSONA_TIMEOUT_MS = parseInt(process.env.LOBSTER_PERSONA_TIMEOUT_MS || '480000', 10);

function withPersonaTimeout(promise, ms, label) {
  let to;
  const timer = new Promise((_, reject) => {
    to = setTimeout(() => reject(new Error(`per-persona timeout ${ms}ms (${label})`)), ms);
  });
  return Promise.race([promise, timer]).finally(() => clearTimeout(to));
}

async function runWithPool(items, poolSize, worker) {
  const results = new Array(items.length);
  let idx = 0;
  async function takeNext() {
    while (true) {
      const i = idx++;
      if (i >= items.length) return;
      try {
        results[i] = await worker(items[i], i);
      } catch (e) {
        results[i] = { _error: e.message, persona: items[i]?.name };
      }
    }
  }
  const workers = Array.from({ length: Math.min(poolSize, items.length) }, () => takeNext());
  await Promise.all(workers);
  return results;
}

async function main() {
  console.log('='.repeat(60));
  console.log('🦞 龍蝦股票市場全體出動');
  console.log(`   ${new Date().toISOString()}`);
  console.log(`   市場脈絡：${MARKET_CONTEXT.join('/')} (UTC ${new Date().toUTCString()})`);
  console.log('='.repeat(60));

  // 清空 log
  fs.writeFileSync(LOG_FILE, `=== 龍蝦股票行動 ${new Date().toISOString()} ===\n`);

  const targetFilter = process.env.LOBSTER_TARGET ? process.env.LOBSTER_TARGET.split(',') : null;
  const toRun = targetFilter ? PERSONAS.filter(p => targetFilter.includes(p.name)) : PERSONAS;

  console.log(`\n🦞 啟動 ${STOCK_POOL_SIZE} 併發 pool（共 ${toRun.length} 隻 lobster，每隻最多 ${PER_PERSONA_TIMEOUT_MS/1000}s）`);
  const startTs = Date.now();
  const rawResults = await runWithPool(toRun, STOCK_POOL_SIZE, async (persona) => {
    console.log(`▶ ${persona.name} (${PERSONA_STOCK_LOGIC[persona.name]?.label || '?'}) 啟動`);
    return await withPersonaTimeout(actStockMarket(persona), PER_PERSONA_TIMEOUT_MS, persona.name);
  });
  const elapsedSec = ((Date.now() - startTs) / 1000).toFixed(1);
  const results = rawResults.filter(r => r && !r._error);
  const errored = rawResults.filter(r => r && r._error);
  console.log(`\n⏱ 整輪 pool 完成 ${elapsedSec}s | 成功 ${results.length} 隻 | 失敗/timeout ${errored.length} 隻`);
  if (errored.length) {
    console.log(`   失敗清單: ${errored.map(e => `${e.persona}(${e._error})`).join(', ')}`);
  }

  // Summary
  console.log('\n' + '='.repeat(60));
  console.log('📊 行動摘要');
  console.log('='.repeat(60));
  for (const r of results) {
    if (!r) continue;
    const filledBuys = (r.orders || []).filter(o => o.status === 'filled' && (o.side || 'buy') === 'buy');
    const filledSells = (r.orders || []).filter(o => o.status === 'filled' && o.side === 'sell');
    const dryRunSells = (r.orders || []).filter(o => o.status === 'dry_run' && o.side === 'sell');
    console.log(`${r.persona}: 選 ${r.picks?.length||0} 支 → 買入成交 ${filledBuys.length} | 賣出成交 ${filledSells.length} | dry-run賣出 ${dryRunSells.length} | ${[...filledBuys, ...filledSells, ...dryRunSells].map(o=>`${o.side}:${o.symbol}`).join('/')}`);
  }
  console.log('\n✅ 全員完成，log: ' + LOG_FILE);
}

main().catch(e => { console.error(e); process.exit(1); });
