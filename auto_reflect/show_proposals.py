#!/usr/bin/env python3
"""
Show recent auto-reflect proposals and their status.

Usage:
    python3 -m auto_reflect.show_proposals
    python3 -m auto_reflect.show_proposals --pending
    python3 -m auto_reflect.show_proposals --all
"""

import json
import os
import sys
from pathlib import Path

from auto_reflect.config import PROPOSAL_HISTORY, IMPROVEMENTS_DIR


def show_history(limit: int = 10):
    """Show recent proposal history (approved/rejected)."""
    if not os.path.exists(PROPOSAL_HISTORY):
        print("No proposal history found.")
        return

    with open(PROPOSAL_HISTORY) as f:
        history = json.load(f)

    recent = history[-limit:]

    print(f"=== Recent Proposals ({len(recent)} of {len(history)} total) ===\n")

    for p in reversed(recent):
        action = p["action"].upper()
        marker = "+" if action == "APPROVED" else "-"
        date = p.get("date", "unknown")[:16].replace("T", " ")
        ptype = p.get("type", "unknown")
        summary = p.get("summary", "no summary")

        print(f"  [{marker}] {action:10s}  {date}  [{ptype}]")
        print(f"      {summary}")
        print()


def show_pending(limit: int = 10):
    """Show most recent unprocessed proposal files."""
    if not os.path.isdir(IMPROVEMENTS_DIR):
        print("No improvements directory found.")
        return

    files = sorted(Path(IMPROVEMENTS_DIR).glob("*_proposals.json"), reverse=True)
    if not files:
        print("No pending proposal files.")
        return

    shown = 0
    for f in files[:limit]:
        proposals = json.loads(f.read_text())
        if not proposals:
            continue
        print(f"--- {f.name} ({len(proposals)} proposals) ---")
        for p in proposals:
            summary = p.get("summary", p.get("issue", "no summary"))
            ptype = p.get("type", p.get("priority", "unknown"))
            print(f"  - [{ptype}] {summary}")
        print()
        shown += 1

    if not shown:
        print("No proposal files with content.")


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Show auto-reflect proposals")
    parser.add_argument("-n", "--limit", type=int, default=10,
                        help="Number of items to show (default: 10)")
    parser.add_argument("--pending", action="store_true",
                        help="Show pending/unprocessed proposals instead of history")
    parser.add_argument("--all", action="store_true",
                        help="Show all proposals (no limit)")
    args = parser.parse_args()

    limit = 9999 if args.all else args.limit

    if args.pending:
        show_pending(limit)
    else:
        show_history(limit)


if __name__ == "__main__":
    main()
