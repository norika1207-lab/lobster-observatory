#!/usr/bin/env node
/**
 * lobster-stock-scheduler.js
 * 24 小時全球股市接力調度器
 *
 * 市場時段 (UTC):
 *   台灣/日本/韓國:  01:00 - 06:30 UTC
 *   歐洲:           07:00 - 16:30 UTC
 *   美國:           13:30 - 20:00 UTC
 *
 * 規則:
 *   1. 自動偵測當前開市中的市場
 *   2. 強制讀財務報表再選股
 *   3. 選股理由必須說清楚（500字以內）
 *   4. 沒有棄權，任何市場都要進場
 *   5. 每 30 分鐘執行一輪
 */

'use strict';
const { execFileSync } = require('child_process');
const fs   = require('fs');
const path = require('path');

const LOG = '/tmp/lobster-stock-scheduler.log';

function log(msg) {
  const line = `[${new Date().toISOString()}] ${msg}`;
  console.log(line);
  fs.appendFileSync(LOG, line + '\n');
}

// ── 市場時段定義 (UTC hours) ──────────────────────────────────────────────────
const MARKETS = {
  US: {
    name: '美股',
    openUTC: 13.5,   // 09:30 EST
    closeUTC: 20.0,  // 16:00 EST
    symbols: {
      momentum_ai:  ['NVDA', 'AMD', 'SMCI', 'AVGO', 'TSM', 'MSFT', 'GOOGL'],
      momentum_ev:  ['TSLA', 'RIVN', 'NIO', 'ON'],
      value_quality:['AAPL', 'JNJ', 'BRK-B', 'JPM', 'WMT', 'KO', 'PG'],
      high_beta:    ['MSTR', 'COIN', 'MARA', 'RIOT', 'PLTR', 'SOFI', 'HOOD'],
      sector_cycle: ['XOM', 'CVX', 'FCX', 'NEM', 'CAT', 'DE'],
      defensive:    ['NEE', 'D', 'SO', 'UNH', 'LLY', 'ABBV', 'MRK'],
      turnaround:   ['INTC', 'DIS', 'PARA', 'WBD', 'PFE', 'BABA', 'SNAP'],
      quant_mlb:    ['GS', 'MS', 'V', 'MA', 'AXP', 'PYPL', 'SQ'],
      esports_tech: ['META', 'AMZN', 'NFLX', 'RBLX'],
      mma_fighter:  ['LMT', 'RTX', 'NOC', 'GD', 'BA'],
    },
  },
  TW: {
    name: '台股',
    openUTC: 1.0,    // 09:00 CST
    closeUTC: 5.5,   // 13:30 CST
    symbols: {
      semiconductor:['2330.TW', '2303.TW', '2308.TW', '2317.TW', '3711.TW'],
      tech:         ['2454.TW', '2382.TW', '3034.TW', '2379.TW', '6505.TW'],
      financials:   ['2882.TW', '2881.TW', '2886.TW', '2891.TW', '2884.TW'],
      large_cap:    ['2412.TW', '2002.TW', '1301.TW', '1303.TW', '2207.TW'],
      momentum:     ['3653.TW', '6669.TW', '2376.TW', '4958.TW', '3008.TW'],
    },
  },
  JP: {
    name: '日股',
    openUTC: 0.0,    // 09:00 JST
    closeUTC: 6.5,   // 15:30 JST
    symbols: {
      tech:       ['7203.T', '6758.T', '8306.T', '9984.T', '7974.T'],
      chips:      ['6857.T', '8035.T', '6645.T', '7735.T', '6702.T'],
      industrial: ['6501.T', '7267.T', '6954.T', '7011.T', '6366.T'],
    },
  },
  KR: {
    name: '韓股',
    openUTC: 0.0,    // 09:00 KST
    closeUTC: 6.5,   // 15:30 KST
    symbols: {
      chips:    ['005930.KS', '000660.KS', '012450.KS'],
      tech:     ['035420.KS', '035720.KS', '066570.KS'],
      battery:  ['051910.KS', '006400.KS', '373220.KS'],
    },
  },
  HK: {
    name: '港股',
    openUTC: 1.5,    // 09:30 HKT
    closeUTC: 8.0,   // 16:00 HKT
    symbols: {
      tech: ['0700.HK', '9988.HK', '9618.HK', '1810.HK', '3690.HK'],
      finance: ['0005.HK', '0388.HK', '1299.HK', '2318.HK', '1398.HK'],
    },
  },
  EU: {
    name: '歐股',
    openUTC: 7.0,
    closeUTC: 16.5,
    symbols: {
      uk: ['SHEL.L', 'AZN.L', 'HSBA.L', 'ULVR.L', 'RIO.L'],
      de: ['SAP.DE', 'SIE.DE', 'ALV.DE', 'DTE.DE', 'BMW.DE'],
      fr: ['MC.PA', 'OR.PA', 'TTE.PA', 'SAN.PA', 'BNP.PA'],
    },
  },
};

function getCurrentMarkets() {
  const now = new Date();
  const utcHour = now.getUTCHours() + now.getUTCMinutes() / 60;
  const open = [];
  for (const [key, m] of Object.entries(MARKETS)) {
    if (utcHour >= m.openUTC && utcHour < m.closeUTC) {
      open.push({ key, ...m });
    }
  }
  return open;
}

function getNextMarketOpen() {
  const now = new Date();
  const utcHour = now.getUTCHours() + now.getUTCMinutes() / 60;
  // Find next market to open
  const opens = [
    { market: 'JP/KR', utcHour: 0.0 },
    { market: 'TW', utcHour: 1.0 },
    { market: 'US', utcHour: 13.5 },
  ];
  for (const o of opens) {
    if (o.utcHour > utcHour) return o;
  }
  return opens[0]; // next day
}

// ── Main execution ────────────────────────────────────────────────────────────
async function main() {
  const open = getCurrentMarkets();
  if (open.length === 0) {
    const next = getNextMarketOpen();
    log(`⏸ 目前無市場開市。下一個開盤：${next.market}（UTC ${next.utcHour}:00）`);
    return;
  }

  for (const market of open) {
    log(`\n📈 ${market.name} 開市中 → 啟動龍蝦進場`);
  }

  // Use one live market as the active trading context. Passing every open
  // Asian market made the agent dilute the Taiwan morning session with JP/HK.
  // Priority: US during US hours, Taiwan during Taiwan morning, then the rest.
  const marketContext = open.some(m => m.key === 'US')
    ? ['US']
    : open.some(m => m.key === 'TW')
      ? ['TW']
      : open.map(m => m.key);

  // Run the stock agent with market context
  try {
    // Regenerate JWT tokens first (they may expire)
    execFileSync('python3', ['-c', `
import jwt, json
from datetime import datetime, timedelta, timezone

SECRET = "1d5aea0d4aa78cdb7cf89e61e74b81ced211fe9cfb91e6d5d343c82fda77961d"
LOBSTERS = [
    "norika_oda","clawtrix","pincerbot","ragclaw","snaplex","hexclaw","vortexiq",
    "prismbit","blazepaw","stonefang","emberling","sparkrill","gauntlet","rushfin",
    "nullpinch","brineledger","omenwake","mirrorcalm","flarecub","basaltrook",
    "ashveil","tidehook","mirebyte","cindersnap","glintmaw","reefblade","quartzclaw","stormledger","silkbarb","riftpinch","elder"
]
expire = datetime.now(timezone.utc) + timedelta(hours=24)
tokens = {n: jwt.encode({"sub": n, "exp": expire}, SECRET, algorithm="HS256") for n in LOBSTERS}
json.dump(tokens, open("/tmp/lobster_stock_tokens.json", "w"))
print("tokens refreshed")
`], { stdio: 'inherit', timeout: 10000 });

    execFileSync('node', ['/root/lobster-stock-agent.js'], {
      stdio: 'inherit',
      timeout: 1500000,
      env: {
        ...process.env,
        LOBSTER_MARKET_CONTEXT: JSON.stringify(marketContext),
        LOBSTER_FORCE_MARKET_BUY: marketContext.includes('TW') ? '1' : (process.env.LOBSTER_FORCE_MARKET_BUY || ''),
        LOBSTER_AUTO_BUY_DIAMONDS: process.env.LOBSTER_AUTO_BUY_DIAMONDS || '1',
      },
    });
  } catch (e) {
    log(`ERROR: ${e.message}`);
  }
}

// ── If called directly: run once ─────────────────────────────────────────────
if (require.main === module) {
  main().catch(e => { console.error(e); process.exit(1); });
}

module.exports = { getCurrentMarkets, MARKETS };
