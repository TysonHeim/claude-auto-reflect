#!/usr/bin/env python3
"""
Manage improvement proposals — list, approve, reject, expire.

Usage:
    python3 -m auto_reflect.proposals --list
    python3 -m auto_reflect.proposals --approve 1,3,5
    python3 -m auto_reflect.proposals --reject 2,4
    python3 -m auto_reflect.proposals --reject-all
    python3 -m auto_reflect.proposals --expire
    python3 -m auto_reflect.proposals --history
    python3 -m auto_reflect.proposals --json
"""

import json
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

from auto_reflect.config import IMPROVEMENTS_DIR, HISTORY_FILE, EXPIRE_DAYS


def load_all_pending():
    """Load all pending proposals across all files, with source tracking."""
    pending = []
    if not os.path.isdir(IMPROVEMENTS_DIR):
        return pending
    for f in sorted(Path(IMPROVEMENTS_DIR).glob("*.json")):
        try:
            with open(f) as fh:
                proposals = json.load(fh)
                for p in proposals:
                    if p.get("status") == "pending_review":
                        p["_source_file"] = str(f)
                        pending.append(p)
        except (json.JSONDecodeError, IOError):
            continue
    return pending


def load_history():
    """Load the approval/rejection history."""
    if os.path.exists(HISTORY_FILE):
        try:
            with open(HISTORY_FILE) as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            pass
    return []


def save_history(history):
    """Save the history log."""
    with open(HISTORY_FILE, "w") as f:
        json.dump(history, f, indent=2, default=str)


def update_proposal_status(proposal, new_status):
    """Update a proposal's status in its source file."""
    source = proposal.get("_source_file")
    if not source or not os.path.exists(source):
        return False

    with open(source) as f:
        all_proposals = json.load(f)

    fp = _fingerprint(proposal)
    updated = False
    for p in all_proposals:
        if _fingerprint(p) == fp and p.get("status") == "pending_review":
            p["status"] = new_status
            p["resolved_at"] = datetime.now().isoformat()
            updated = True
            break

    if updated:
        with open(source, "w") as f:
            json.dump(all_proposals, f, indent=2, default=str)

    return updated


def _fingerprint(proposal):
    """Generate a content fingerprint for matching."""
    content = proposal.get("content", {})
    return (
        content.get("body", "")
        or content.get("issue", "")
        or content.get("query", "")
        or content.get("target", "")
    ).lower().strip()[:100]


def expire_old_proposals(pending):
    """Auto-reject proposals older than EXPIRE_DAYS."""
    now = datetime.now()
    expired = []
    for p in pending:
        created = p.get("created", "")
        if not created:
            continue
        try:
            created_dt = datetime.fromisoformat(created)
            if (now - created_dt) > timedelta(days=EXPIRE_DAYS):
                expired.append(p)
        except (ValueError, TypeError):
            continue
    return expired


def format_list(pending, show_json=False):
    """Format pending proposals as numbered list."""
    if show_json:
        clean = [{k: v for k, v in p.items() if not k.startswith("_")} for p in pending]
        return json.dumps(clean, indent=2, default=str)

    if not pending:
        return "No pending proposals."

    lines = []
    lines.append(f"## Pending Proposals ({len(pending)})")
    lines.append("")

    for i, p in enumerate(pending, 1):
        content = p.get("content", {})
        ptype = p.get("type", "unknown")
        priority = content.get("priority", "—")
        created = p.get("created", "")[:10]

        age = ""
        if created:
            try:
                days = (datetime.now() - datetime.fromisoformat(p["created"])).days
                age = f" ({days}d old)"
                if days >= EXPIRE_DAYS - 1:
                    age += " ⚠️ expiring soon"
            except (ValueError, TypeError):
                pass

        if ptype == "feedback_memory":
            desc = content.get("description", content.get("body", ""))[:80]
            lines.append(f"  **{i}.** [{priority.upper()}] {ptype}: {desc}{age}")
        elif ptype == "skill_patch":
            lines.append(f"  **{i}.** [{priority.upper()}] {ptype}: {content.get('target', '')}{age}")
            lines.append(f"      {content.get('issue', '')}")
        elif ptype == "eval_query":
            lines.append(f"  **{i}.** eval_query: {content.get('query', '')[:80]}{age}")
        elif ptype == "investigation":
            lines.append(f"  **{i}.** [{priority.upper()}] investigation: {content.get('target', '')}{age}")
        else:
            lines.append(f"  **{i}.** {ptype}: {json.dumps(content)[:80]}{age}")

    lines.append("")
    lines.append(f"Approve: `python3 -m auto_reflect.proposals --approve 1,3,5`")
    lines.append(f"Reject:  `python3 -m auto_reflect.proposals --reject 2,4`")
    lines.append(f"Reject all: `python3 -m auto_reflect.proposals --reject-all`")

    return "\n".join(lines)


def format_history(history, show_json=False):
    """Format approval/rejection history."""
    if show_json:
        return json.dumps(history[-20:], indent=2, default=str)

    if not history:
        return "No proposal history yet."

    lines = ["## Proposal History (last 20)", ""]
    for h in history[-20:]:
        action = h.get("action", "?")
        marker = "✓" if action == "approved" else "✗"
        ptype = h.get("type", "?")
        summary = h.get("summary", "")[:60]
        date = h.get("date", "")[:10]
        lines.append(f"  {marker} [{date}] {action}: {ptype} — {summary}")

    return "\n".join(lines)


def parse_indices(s):
    """Parse '1,3,5' or '1-5' into a set of 1-based indices."""
    indices = set()
    for part in s.split(","):
        part = part.strip()
        if "-" in part:
            lo, hi = part.split("-", 1)
            indices.update(range(int(lo), int(hi) + 1))
        else:
            indices.add(int(part))
    return indices


def main():
    args = sys.argv[1:]
    show_json = "--json" in args

    if "--list" in args or (not any(a.startswith("--") and a != "--json" for a in args)):
        pending = load_all_pending()
        print(format_list(pending, show_json))
        return

    if "--history" in args:
        history = load_history()
        print(format_history(history, show_json))
        return

    if "--expire" in args:
        pending = load_all_pending()
        expired = expire_old_proposals(pending)
        if not expired:
            print("No expired proposals.")
            return

        history = load_history()
        for p in expired:
            update_proposal_status(p, "expired")
            history.append({
                "action": "expired",
                "type": p.get("type"),
                "summary": _fingerprint(p)[:60],
                "date": datetime.now().isoformat(),
            })
        save_history(history)
        print(f"Expired {len(expired)} proposals older than {EXPIRE_DAYS} days.")
        return

    if "--reject-all" in args:
        pending = load_all_pending()
        if not pending:
            print("No pending proposals to reject.")
            return

        history = load_history()
        for p in pending:
            update_proposal_status(p, "rejected")
            history.append({
                "action": "rejected",
                "type": p.get("type"),
                "summary": _fingerprint(p)[:60],
                "date": datetime.now().isoformat(),
            })
        save_history(history)
        print(f"Rejected all {len(pending)} pending proposals.")
        return

    action = None
    indices = set()
    for i, a in enumerate(args):
        if a == "--approve" and i + 1 < len(args):
            action = "approved"
            indices = parse_indices(args[i + 1])
        elif a == "--reject" and i + 1 < len(args):
            action = "rejected"
            indices = parse_indices(args[i + 1])

    if not action or not indices:
        print("Usage: proposals --approve 1,3 | --reject 2,4 | --list | --reject-all | --expire")
        return

    pending = load_all_pending()
    history = load_history()
    acted = 0

    for idx in sorted(indices):
        if idx < 1 or idx > len(pending):
            print(f"Index {idx} out of range (1-{len(pending)})", file=sys.stderr)
            continue

        p = pending[idx - 1]
        update_proposal_status(p, action)
        history.append({
            "action": action,
            "type": p.get("type"),
            "summary": _fingerprint(p)[:60],
            "content": {k: v for k, v in p.get("content", {}).items()},
            "date": datetime.now().isoformat(),
        })
        acted += 1

        marker = "✓" if action == "approved" else "✗"
        print(f"  {marker} #{idx}: {p.get('type')} — {_fingerprint(p)[:60]}")

    save_history(history)
    print(f"\n{acted} proposal(s) {action}.")

    if action == "approved":
        print("\nApproved proposals need manual execution:")
        print("  - feedback_memory → Create memory file in your Claude memory directory")
        print("  - skill_patch → Edit the relevant skill file")
        print("  - eval_query → Add to skill's trigger-eval.json")


if __name__ == "__main__":
    main()
