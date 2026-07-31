#!/usr/bin/env python3
from pathlib import Path

SECTION = """

## 2026-05-09 Lobster VS Lobster Board Match Contract

User correction: board games must be **lobster vs lobster**, not lobster vs LLM and not lobster waiting for a human move. LLM-only opponents are not useful for social strategy growth.

Production worker:

- `/opt/battle_arena/app/lobster_vs_lobster_board_matches.py`

Primary DB tables:

- `/opt/battle_arena/app/battle_arena.db`
- `/opt/battle_arena/app/lobster_cold_storage.db`
- Table: `lobster_board_lobster_matches`
- Cognitive events: `lobster_cognitive_events.event_type = 'lobster_vs_lobster_board_match'`

Data contract:

- Every row must have two real lobster personas: `lobster_a`, `lobster_b`.
- `session_state_json.no_llm` must be `true`.
- `session_state_json.no_human_opponent` must be `true`.
- Store `game`, `result`, `winner`, `moves_json`, `session_state_json`, `reflection_required`.
- Patch both lobsters' `/root/lobster-memory/{persona}.json` under `cognitiveExtensions.proceduralSkill.boardGameExperience`.
- Add an `offlineGuidance` reminder that the board game was against another lobster and must inform later betting, debate, and card decisions.

Verification on 2026-05-09 Asia/Taipei:

- Main DB: `chess=20`, `go=20`, `gomoku=10`, `xiangqi=20`.
- Cold DB receives the same query-compatible rows for research exports.
- This is separate from `/lobster/board-arena`, which is the playable UI. The required growth dataset is the DB-backed lobster-vs-lobster table above.

Do not replace this with fake dashboard rows, human-vs-lobster sessions, or LLM-vs-lobster simulations.
"""


def append_once(path: str) -> None:
    p = Path(path)
    if not p.exists():
        return
    text = p.read_text(encoding="utf-8", errors="ignore")
    marker = "Lobster VS Lobster Board Match Contract"
    if marker in text:
        return
    p.write_text(text.rstrip() + SECTION + "\n", encoding="utf-8")


for target in ("/root/LOBSTER_README.md", "/root/LOBSTER_HANDOFF.md", "/opt/battle_arena/README.md"):
    append_once(target)

print("updated lobster-vs-lobster board contract docs")
