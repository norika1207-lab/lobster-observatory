#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

AFU_ROOT = Path("/root/afu-brain")
PACK_DIR = AFU_ROOT / "rag-packs"
RUNTIME_DIR = AFU_ROOT / "runtime"

sys.path.insert(0, str(AFU_ROOT / "packages"))

from afu_brain.rag import load_rag_dir, retrieve_for_decision  # noqa: E402


def load_items():
    items = []
    if PACK_DIR.exists():
        items.extend(load_rag_dir(PACK_DIR))
    if RUNTIME_DIR.exists():
        items.extend(load_rag_dir(RUNTIME_DIR))
    return items


def main() -> int:
    parser = argparse.ArgumentParser(description="Bridge Lobster runtime to Afu Brain public RAG packs.")
    parser.add_argument("query")
    parser.add_argument("--intent", default=None)
    parser.add_argument("--risk", default=None)
    parser.add_argument("--top-k", type=int, default=4)
    args = parser.parse_args()

    items = load_items()
    namespaces, hits = retrieve_for_decision(
        args.query,
        items,
        intent=args.intent,
        risk=args.risk,
        top_k=args.top_k,
    )
    payload = {
        "source": "afu-brain",
        "repo": "norika1207-lab/afu-brain",
        "namespaces": namespaces,
        "hits": [
            {
                "score": score,
                "id": item.id,
                "pack": item.pack,
                "kind": item.kind,
                "intent": item.intent,
                "risk": item.risk,
                "decision": item.decision,
                "tags": item.tags,
                "text": item.text,
                "lesson": item.lesson,
            }
            for score, item in hits
        ],
    }
    print(json.dumps(payload, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
