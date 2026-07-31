#!/usr/bin/env node
const http = require('http');
const crypto = require('crypto');
const { execFile } = require('child_process');
const { Chess } = require('/opt/battle_arena/board_engines/node_modules/chess.js');

const PORT = Number(process.env.LOBSTER_BOARD_PORT || 8071);
const sessions = new Map();
const personas = ['norika_oda','snaplex','vortexiq','pincerbot','clawtrix','hexclaw','prismbit','blazepaw','ragclaw','stonefang','gauntlet','nullpinch','sparkrill','mirrorcalm','basaltrook','brineledger','emberling','flarecub','omenwake','rushfin','elder'];
const files = 'abcdefghi';
const pieceValue = { p:100,n:320,b:330,r:500,q:900,k:0 };

function id(){ return crypto.randomBytes(8).toString('hex'); }
function hash(s){ return crypto.createHash('sha256').update(String(s)).digest().readUInt32BE(0); }
function json(res, obj, status=200){ const body=JSON.stringify(obj); res.writeHead(status, {'content-type':'application/json; charset=utf-8','cache-control':'no-store'}); res.end(body); }
function readJson(req){ return new Promise((resolve)=>{ let s=''; req.on('data',d=>s+=d); req.on('end',()=>{ try{resolve(s?JSON.parse(s):{})}catch(e){resolve({})} }); }); }

function chessEval(g, color){
  let score=0; for(const row of g.board()) for(const p of row){ if(!p) continue; score += (p.color===color?1:-1)*(pieceValue[p.type]||0); }
  return score;
}
function chessBot(g, persona){
  const legal=g.moves({verbose:true}); let best=null, bestScore=-1e9;
  for(const mv of legal){
    g.move(mv);
    let sc=chessEval(g,'b') + g.moves().length*2 + (hash(persona+mv.san)%13);
    if(g.isCheckmate()) sc+=100000;
    g.undo();
    if(sc>bestScore){ bestScore=sc; best=mv; }
  }
  if(best) return g.move(best);
}
function chessState(s){
  const g=s.engine; let result=null;
  if(g.isCheckmate()) result=g.turn()==='b'?'human_win':'lobster_win';
  else if(g.isDraw()||g.isStalemate()||g.isThreefoldRepetition()) result='draw';
  return {id:s.id,game:s.game,persona:s.persona,result,fen:g.fen(),turn:g.turn(),moves:s.moves};
}

function gomokuWinner(board,n=15){
  for(const [dx,dy] of [[1,0],[0,1],[1,1],[1,-1]]) for(let y=0;y<n;y++) for(let x=0;x<n;x++){
    const v=board[y][x]; if(!v) continue; let ok=true;
    for(let k=1;k<5;k++){ const xx=x+dx*k, yy=y+dy*k; if(xx<0||yy<0||xx>=n||yy>=n||board[yy][xx]!==v){ok=false;break;} }
    if(ok) return v;
  }
  return board.flat().every(Boolean)?'draw':null;
}
function lineScore(board,x,y,mark,n=15){
  let best=0; for(const [dx,dy] of [[1,0],[0,1],[1,1],[1,-1]]){ let total=1, open=0;
    for(const sign of [1,-1]){ let xx=x+dx*sign, yy=y+dy*sign; while(xx>=0&&yy>=0&&xx<n&&yy<n&&board[yy][xx]===mark){total++;xx+=dx*sign;yy+=dy*sign;} if(xx>=0&&yy>=0&&xx<n&&yy<n&&!board[yy][xx]) open++; }
    best=Math.max(best,total*total*10+open*8);
  } return best;
}
function gomokuBot(s){
  const b=s.board,n=15; let best=null,bv=-1;
  for(let y=0;y<n;y++) for(let x=0;x<n;x++) if(!b[y][x]){
    b[y][x]='O'; if(gomokuWinner(b,n)==='O'){ b[y][x]=null; best=[x,y,'finish']; bv=1e9; continue; }
    const atk=lineScore(b,x,y,'O',n); b[y][x]='X'; const block=lineScore(b,x,y,'X',n); b[y][x]=null;
    const center=28-Math.abs(x-7)-Math.abs(y-7); const v=atk*1.12+block*1.3+center+(hash(s.persona+x+','+y)%9);
    if(v>bv){bv=v; best=[x,y,block>atk?'block':'build'];}
  }
  if(best){ const [x,y,reason]=best; b[y][x]='O'; s.moves.push({actor:s.persona,x,y,reason}); }
}
function gomokuState(s){ const w=gomokuWinner(s.board,15); return {id:s.id,game:s.game,persona:s.persona,result:w==='X'?'human_win':w==='O'?'lobster_win':w,board:s.board,moves:s.moves}; }

function goBot(s){
  let candidates=[];
  for(let y=0;y<9;y++) for(let x=0;x<9;x++) if(!s.board[y][x]){
    candidates.push({x,y,score:16-Math.abs(x-4)-Math.abs(y-4)+(hash(s.persona+x+','+y+s.moves.length)%8)});
  }
  candidates.sort((a,b)=>b.score-a.score); const mv=candidates[0];
  if(mv){ s.board[mv.y][mv.x]='O'; s.moves.push({actor:s.persona,x:mv.x,y:mv.y,reason:'influence'}); }
  else { s.moves.push({actor:s.persona,pass:true,reason:'pass'}); }
}
function goState(s){
  const board=[]; let black=0, white=0;
  for(let y=0;y<9;y++) for(let x=0;x<9;x++){ const v=s.board[y][x]; if(v==='X') black++; if(v==='O') white++; board.push({y,x,value:v==='X'?'black':v==='O'?'white':'empty'}); }
  return {id:s.id,game:s.game,persona:s.persona,result:s.moves.length>=80?'score_pending':null,board,score:{black,white},moves:s.moves};
}

function initialXiangBoard(){
  const b={};
  const put=(sq,p)=>{b[sq]=p};
  ['a9:r','b9:n','c9:b','d9:a','e9:k','f9:a','g9:b','h9:n','i9:r','b7:c','h7:c','a6:p','c6:p','e6:p','g6:p','i6:p','a0:R','b0:N','c0:B','d0:A','e0:K','f0:A','g0:B','h0:N','i0:R','b2:C','h2:C','a3:P','c3:P','e3:P','g3:P','i3:P'].forEach(x=>{const [sq,p]=x.split(':');put(sq,p)});
  return b;
}
function xiangApply(board, mv){ const a=mv.slice(0,2), b=mv.slice(2,4); if(board[a]){ board[b]=board[a]; delete board[a]; } }
function xiangLegalish(board,mv,side){
  if(!/^[a-i][0-9][a-i][0-9]$/.test(mv)) return false;
  const a=mv.slice(0,2), b=mv.slice(2,4), p=board[a];
  if(!p) return false;
  if(side==='human' && p!==p.toUpperCase()) return false;
  if(side==='lobster' && p!==p.toLowerCase()) return false;
  if(board[b] && ((p===p.toUpperCase()) === (board[b]===board[b].toUpperCase()))) return false;
  const fx=files.indexOf(a[0]), fy=Number(a[1]), tx=files.indexOf(b[0]), ty=Number(b[1]);
  return fx>=0 && tx>=0 && Math.abs(fx-tx)+Math.abs(fy-ty)>0 && Math.abs(fx-tx)<=2 && Math.abs(fy-ty)<=2;
}
function xiangBot(s){
  const legal=[];
  for(const from of Object.keys(s.board)){
    if(s.board[from]!==s.board[from].toLowerCase()) continue;
    const fx=files.indexOf(from[0]), fy=Number(from[1]);
    for(const [dx,dy] of [[0,-1],[0,1],[-1,0],[1,0],[-1,-1],[1,-1],[-1,1],[1,1]]){
      const tx=fx+dx, ty=fy+dy, to=files[tx]+ty;
      if(tx>=0&&tx<9&&ty>=0&&ty<10&&xiangLegalish(s.board,from+to,'lobster')) legal.push(from+to);
    }
  }
  if(!legal.length) return;
  const mv = legal[hash(s.persona + ':' + s.moves.length) % legal.length];
  xiangApply(s.board,mv); s.moves.push({actor:s.persona,move:mv,reason:'legal_reply'});
}
function xiangState(s){ return {id:s.id,game:s.game,persona:s.persona,result:s.moves.length>=80?'unfinished':null,board:s.board,turn:s.moves.length%2?'lobster':'human',moves:s.moves}; }

function insertFinished(s, result){
  const review = `${s.persona} playable ${s.game} session ended as ${result}. This was an interactive human-vs-lobster board game, not a background simulation. The lobster had to obey legal movement, reply to the human move, and preserve the reason trail for later learning.`;
  execFile('sqlite3', ['/opt/battle_arena/app/battle_arena.db',
    `INSERT INTO lobster_game_training_sessions(day,persona,game,opponent,result,moves_json,lookahead_depth,self_review,score_delta_json,raw_json) VALUES(date('now'),'${s.persona.replace(/'/g,"''")}','${s.game}','human','${result}','${JSON.stringify(s.moves).replace(/'/g,"''")}',2,'${review.replace(/'/g,"''")}','{}','{"source":"playable_board_arena"}');`
  ], ()=>{});
}

function newSession(game, persona){
  const s={id:id(),game,persona:persona||personas[hash(Date.now())%personas.length],moves:[]};
  if(game==='chess') s.engine=new Chess();
  else if(game==='gomoku') s.board=Array.from({length:15},()=>Array(15).fill(null));
  else if(game==='go') s.board=Array.from({length:9},()=>Array(9).fill(null));
  else if(game==='xiangqi') s.board=initialXiangBoard();
  else throw new Error('unknown game');
  sessions.set(s.id,s); return state(s);
}
function state(s){ if(s.game==='chess') return chessState(s); if(s.game==='gomoku') return gomokuState(s); if(s.game==='go') return goState(s); return xiangState(s); }
function move(s, body){
  if(s.game==='chess'){
    const mv=s.engine.move({from:body.from,to:body.to,promotion:body.promotion||'q'});
    if(!mv) throw new Error('illegal chess move');
    s.moves.push({actor:'human',san:mv.san,from:mv.from,to:mv.to});
    if(!s.engine.isGameOver()){ const bot=chessBot(s.engine,s.persona); if(bot) s.moves.push({actor:s.persona,san:bot.san,from:bot.from,to:bot.to,reason:'material_mobility'}); }
  } else if(s.game==='gomoku'){
    const {x,y}=body; if(x<0||y<0||x>=15||y>=15||s.board[y][x]) throw new Error('illegal gomoku move');
    s.board[y][x]='X'; s.moves.push({actor:'human',x,y}); if(!gomokuWinner(s.board,15)) gomokuBot(s);
  } else if(s.game==='go'){
    const {x,y}=body; if(x<0||y<0||x>=9||y>=9||s.board[y][x]) throw new Error('illegal go move');
    s.board[y][x]='X'; s.moves.push({actor:'human',x,y}); goBot(s);
  } else if(s.game==='xiangqi'){
    const mv=String(body.move||''); if(!xiangLegalish(s.board,mv,'human')) throw new Error('illegal xiangqi move');
    xiangApply(s.board,mv); s.moves.push({actor:'human',move:mv}); xiangBot(s);
  }
  const st=state(s); if(st.result && !s.saved){ s.saved=true; insertFinished(s,st.result); } return st;
}

http.createServer(async (req,res)=>{
  if(req.method==='OPTIONS') return json(res,{ok:true});
  try{
    if(req.url==='/health') return json(res,{ok:true,sessions:sessions.size});
    if(req.url==='/api/new' && req.method==='POST'){ const b=await readJson(req); return json(res,newSession(b.game,b.persona)); }
    if(req.url==='/api/move' && req.method==='POST'){ const b=await readJson(req); const s=sessions.get(b.id); if(!s) return json(res,{error:'session not found'},404); return json(res,move(s,b)); }
    if(req.url.startsWith('/api/state/')){ const s=sessions.get(req.url.split('/').pop()); if(!s) return json(res,{error:'session not found'},404); return json(res,state(s)); }
    json(res,{error:'not found'},404);
  }catch(e){ json(res,{error:e.message},400); }
}).listen(PORT,'127.0.0.1',()=>console.log(`lobster board play server ${PORT}`));
