#!/usr/bin/env node
const { execFileSync } = require('child_process');
const fs = require('fs');
const DB = process.env.LOBSTER_DB || '/opt/battle_arena/app/battle_arena.db';
const COLD_DB = process.env.LOBSTER_COLD_DB || '/opt/battle_arena/app/lobster_cold_storage.db';
const MAX_SILENCE_HOURS = Number(process.env.LOBSTER_MOLTBOOK_MAX_SILENCE_HOURS || 24);
function q(v){ return '\'' + String(v ?? '').replace(/'/g,"''") + '\''; }
function sqlite(db, sql){
  return execFileSync('/usr/bin/sqlite3', ['-cmd', 'PRAGMA busy_timeout=15000;', db, sql], {encoding:'utf8'}).trim();
}
function writeHealth(status, summary, details){
  const sql = `CREATE TABLE IF NOT EXISTS lobster_moltbook_health (id INTEGER PRIMARY KEY AUTOINCREMENT,status TEXT NOT NULL,summary TEXT,details_json TEXT NOT NULL DEFAULT '{}',created_at TEXT NOT NULL);
INSERT INTO lobster_moltbook_health(status,summary,details_json,created_at) VALUES (${q(status)},${q(summary)},${q(JSON.stringify(details))},${q(new Date().toISOString())});`;
  for (const db of [DB, COLD_DB]) { try { sqlite(db, sql); } catch {} }
}
let rows = '';
try {
  rows = sqlite(DB, `SELECT persona,last_status,COALESCE(last_success_at,''),COALESCE(suspended_until,''),COALESCE(last_message,'') FROM lobster_moltbook_poster_status ORDER BY persona;`);
} catch(e) {
  writeHealth('critical','status table missing or unreadable',{error:String(e.message)});
  console.log('critical status table missing or unreadable');
  process.exit(1);
}
const lines = rows ? rows.split('\n').filter(Boolean) : [];
let postedRecent = false;
let latestSuccess = 0;
const suspended = [], never = [], bad = [], rateLimited = [];
const now = Date.now();
for (const line of lines) {
  const [persona,status,lastSuccess,suspendedUntil,msg] = line.split('|');
  if (!lastSuccess) never.push(persona);
  else {
    const t = Date.parse(lastSuccess);
    if (Number.isFinite(t)) latestSuccess = Math.max(latestSuccess, t);
    if (Number.isFinite(t) && now - t < MAX_SILENCE_HOURS*3600*1000) postedRecent = true;
  }
  if (suspendedUntil && Date.parse(suspendedUntil) > now) suspended.push(`${persona}:${suspendedUntil}`);
  if (status === 'rate_limited') rateLimited.push(persona);
  if (['failed','timeout','no_post','rate_limited','suspended'].includes(status)) bad.push(`${persona}:${status}`);
}
let state = 'ok';
if (!lines.length) state = 'critical';
else if (!postedRecent) state = 'critical';
else if (bad.length || suspended.length || rateLimited.length) state = 'warning';
const latestSuccessIso = latestSuccess ? new Date(latestSuccess).toISOString() : '';
const summary = `personas=${lines.length} recent_post=${postedRecent} latest_success=${latestSuccessIso || 'never'} suspended=${suspended.length} never=${never.length} rate_limited=${rateLimited.length} bad=${bad.length}`;
writeHealth(state, summary, {postedRecent,latestSuccess:latestSuccessIso,suspended,never,bad,rateLimited,maxSilenceHours:MAX_SILENCE_HOURS});
if (state !== 'ok') {
  try { fs.appendFileSync('/tmp/lobster-moltbook-health.log', `${new Date().toISOString()} ${state} ${summary}\n`); } catch {}
}
console.log(`${state} ${summary}`);
process.exit(state === 'ok' ? 0 : 1);
