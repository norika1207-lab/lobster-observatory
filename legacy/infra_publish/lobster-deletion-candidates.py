#!/usr/bin/env python3
"""
Lobster deletion candidate viewer.

Lists lobsters who have crossed the templateSurvival.deletionLimit (10 violations)
and are awaiting human-triggered Hades ritual.

This tool is READ-ONLY. It does NOT trigger any ritual.

User policy 2026-05-13:
  Rituals are human-triggered. After reviewing candidates, the user manually runs:
      node /root/lobster-degrade.js <name>
  to start the 21-minute Hades ritual (2-min portent + 6 × 3-min stages + harassment).

Usage:
  python3 /root/lobster-deletion-candidates.py
  python3 /root/lobster-deletion-candidates.py --json
  python3 /root/lobster-deletion-candidates.py --top 3      # Only show top 3
"""
import argparse
import json
import pathlib
import sys
from datetime import datetime, timezone

MEMORY_DIR = pathlib.Path("/root/lobster-memory")
DELETED_FILE = MEMORY_DIR / "deleted-lobsters.json"
SKIP_META = {"norika", "system", "deleted-lobsters", "lobster"}
RITUAL_PROTECTED = {"elder"}  # cannot be deleted via Hades


def load_deleted_registry() -> dict:
    if not DELETED_FILE.exists():
        return {}
    try:
        return json.loads(DELETED_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def scan_candidates():
    deleted = load_deleted_registry()
    candidates = []
    over_limit_no_marker = []
    already_deleted = []
    safe = []

    for path in sorted(MEMORY_DIR.glob("*.json")):
        name = path.stem
        if name in SKIP_META:
            continue
        try:
            mem = json.loads(path.read_text(encoding="utf-8"))
        except Exception as e:
            continue

        ts = mem.get("templateSurvival") or {}
        vc = int(ts.get("violationCount") or 0)
        limit = int(ts.get("deletionLimit") or 10)
        is_candidate = bool(ts.get("deletionCandidate"))
        is_deleted = bool(ts.get("deleted")) or bool(mem.get("deleted")) or name in deleted
        protected = name in RITUAL_PROTECTED

        recent_violations = (ts.get("violations") or [])[-3:]

        row = {
            "name": name,
            "violationCount": vc,
            "deletionLimit": limit,
            "remainingChances": ts.get("remainingChances"),
            "deletionCandidate": is_candidate,
            "deletionCandidateAt": ts.get("deletionCandidateAt"),
            "deletionCandidateViolations": ts.get("deletionCandidateViolations"),
            "deleted": is_deleted,
            "deletedAt": ts.get("deletedAt") or mem.get("deletedAt"),
            "deleteReason": ts.get("deleteReason") or mem.get("deleteReason"),
            "protected": protected,
            "recentViolations": [
                {
                    "ts": v.get("ts"),
                    "reason": (v.get("reason") or "")[:50],
                    "text": (v.get("text") or "")[:80],
                }
                for v in recent_violations
            ],
        }

        if is_deleted:
            already_deleted.append(row)
        elif is_candidate:
            candidates.append(row)
        elif vc >= limit:
            over_limit_no_marker.append(row)
        else:
            safe.append(row)

    candidates.sort(key=lambda r: -r["violationCount"])
    over_limit_no_marker.sort(key=lambda r: -r["violationCount"])
    return candidates, over_limit_no_marker, already_deleted, safe


def fmt_table(rows, title, columns):
    print(f"\n## {title} ({len(rows)})")
    if not rows:
        print("  (none)")
        return
    widths = {c: max(len(c), max((len(str(r.get(c, ""))) for r in rows), default=0)) for c in columns}
    header = " | ".join(c.ljust(widths[c]) for c in columns)
    sep = "-+-".join("-" * widths[c] for c in columns)
    print(f"  {header}")
    print(f"  {sep}")
    for r in rows:
        print(f"  {' | '.join(str(r.get(c, '')).ljust(widths[c]) for c in columns)}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true", help="Output JSON instead of human format")
    ap.add_argument("--top", type=int, default=10, help="Top N candidates to show (default 10)")
    args = ap.parse_args()

    candidates, over_limit, deleted_already, safe = scan_candidates()

    if args.json:
        out = {
            "scannedAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "candidates": candidates[: args.top],
            "overLimitButNoCandidateMarker": over_limit[: args.top],
            "alreadyDeleted": deleted_already,
            "safeCount": len(safe),
        }
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return

    print(f"== Lobster Deletion Candidates (read-only) ==")
    print(f"Policy: rituals are human-triggered. To delete a candidate, run:")
    print(f"  node /root/lobster-degrade.js <name>")
    print(f"That triggers the full Hades ritual (~21 min: 2-min portent + 6 × 3-min stages + harassment).")
    print(f"Protected from ritual: {sorted(RITUAL_PROTECTED)}")

    fmt_table(
        candidates[: args.top],
        f"🔴 Awaiting Hades ritual (top {args.top}, sorted by violations)",
        ["name", "violationCount", "deletionCandidateAt", "remainingChances"],
    )

    fmt_table(
        over_limit[: args.top],
        "⚠️  Over limit but no candidate marker (shouldn't happen after 2026-05-13 patch — investigate if seen)",
        ["name", "violationCount", "remainingChances"],
    )

    fmt_table(
        deleted_already,
        "💀 Already deleted (completed ritual or pre-patch bug residue)",
        ["name", "violationCount", "deletedAt", "deleteReason"],
    )

    print(f"\nSafe (violationCount < limit): {len(safe)} lobsters")
    print(f"\nFull JSON: python3 {sys.argv[0]} --json")


if __name__ == "__main__":
    main()
