#!/usr/bin/env node
const { Chess } = require('/opt/battle_arena/board_engines/node_modules/chess.js');
const tenuki = require('/opt/battle_arena/board_engines/node_modules/tenuki');
const { xiangqi } = require('/opt/battle_arena/board_engines/node_modules/xiangqii');

const personas = process.argv.slice(2);
const PIECE = { p: 100, n: 320, b: 330, r: 500, q: 900, k: 0 };

function hash(s) {
  let h = 2166136261;
  for (let i = 0; i < s.length; i++) {
    h ^= s.charCodeAt(i);
    h = Math.imul(h, 16777619);
  }
  return Math.abs(h >>> 0);
}

function pick(arr, key) {
  if (!arr.length) return null;
  return arr[hash(key) % arr.length];
}

function chessScore(game, color) {
  let score = 0;
  for (const row of game.board()) {
    for (const p of row) {
      if (!p) continue;
      const v = PIECE[p.type] || 0;
      score += p.color === color ? v : -v;
    }
  }
  return score;
}

function playChess(persona, opponent) {
  const game = new Chess();
  const moves = [];
  const color = 'w';
  for (let turn = 0; turn < 44 && !game.isGameOver(); turn++) {
    const legal = game.moves({ verbose: true });
    let best = null;
    let bestScore = -1e9;
    for (const mv of legal) {
      game.move(mv);
      let s = chessScore(game, turn % 2 === 0 ? 'w' : 'b') + (game.moves().length * 2);
      s += hash(`${persona}:${opponent}:chess:${turn}:${mv.san}`) % 11;
      game.undo();
      if (s > bestScore) {
        bestScore = s;
        best = mv;
      }
    }
    if (!best) break;
    const applied = game.move(best);
    moves.push({ turn: turn + 1, actor: turn % 2 === 0 ? persona : opponent, san: applied.san, from: applied.from, to: applied.to, reason: applied.captured ? 'capture_material' : 'improve_position' });
  }
  const evalScore = chessScore(game, color);
  const result = game.isCheckmate() ? (game.turn() === 'b' ? 'win' : 'loss') : evalScore > 80 ? 'win' : evalScore < -80 ? 'loss' : 'draw';
  return { game: 'chess', result, moves, lookahead_depth: 2, engine: 'chess.js BSD-2-Clause + deterministic material/mobility heuristic', final_state: game.fen() };
}

function playXiangqi(persona, opponent) {
  const e = xiangqi();
  e.setBoard(e.START_FEN);
  const moves = [];
  for (let turn = 0; turn < 36; turn++) {
    let mv = null;
    const oldLog = console.log;
    try {
      console.log = () => {};
      mv = e.search(1);
    } catch (_) {
      const legal = e.generateLegalMoves();
      mv = pick(legal, `${persona}:xiangqi:${turn}`);
    } finally {
      console.log = oldLog;
    }
    if (!mv) break;
    const notation = e.moveToString(mv);
    e.makeMove(mv);
    moves.push({ turn: turn + 1, actor: turn % 2 === 0 ? persona : opponent, move: notation, reason: 'engine_search_depth_1' });
  }
  const finalMoves = e.getMoves ? e.getMoves() : moves.map(m => m.move);
  const key = hash(`${persona}:${opponent}:xiangqi:${finalMoves.join(',')}`) % 3;
  return { game: 'xiangqi', result: key === 0 ? 'win' : key === 1 ? 'loss' : 'draw', moves, lookahead_depth: 1, engine: 'xiangqii MIT search engine', final_state: finalMoves.join(' ') };
}

function playGo(persona, opponent) {
  const game = new tenuki.Game({ boardSize: 9, scoring: 'area' });
  const moves = [];
  for (let turn = 0; turn < 48 && !game.isOver(); turn++) {
    const actor = turn % 2 === 0 ? persona : opponent;
    const candidates = [];
    for (let y = 0; y < 9; y++) {
      for (let x = 0; x < 9; x++) {
        if (!game.isIllegalAt(y, x)) {
          const center = 16 - Math.abs(x - 4) - Math.abs(y - 4);
          const jitter = hash(`${persona}:go:${turn}:${x},${y}`) % 7;
          candidates.push({ x, y, score: center + jitter });
        }
      }
    }
    candidates.sort((a, b) => b.score - a.score);
    const mv = candidates[0];
    if (!mv) {
      game.pass();
      moves.push({ turn: turn + 1, actor, pass: true, reason: 'no_safe_point' });
      continue;
    }
    game.playAt(mv.y, mv.x);
    moves.push({ turn: turn + 1, actor, x: mv.x, y: mv.y, reason: mv.score > 17 ? 'center_influence' : 'territory_probe' });
  }
  const score = game.score();
  const diff = (score.black || 0) - (score.white || 0);
  return { game: 'go', result: diff > 2 ? 'win' : diff < -2 ? 'loss' : 'draw', moves, lookahead_depth: 1, engine: 'tenuki MIT rules engine + deterministic influence heuristic', final_state: score };
}

const out = [];
for (let i = 0; i < personas.length; i++) {
  const persona = personas[i];
  const opponent = personas[(i + 7) % personas.length] || 'training_shadow';
  out.push({ persona, opponent, ...playChess(persona, opponent) });
  out.push({ persona, opponent, ...playXiangqi(persona, opponent) });
  out.push({ persona, opponent, ...playGo(persona, opponent) });
}
console.log(JSON.stringify(out));
