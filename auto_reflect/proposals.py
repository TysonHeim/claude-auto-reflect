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

from auto_reflect.config import (
    IMPROVEMENTS_DIR,
    OBSERVATIONS_DIR,
    HISTORY_FILE,
    EXPIRE_DAYS,
    CLAUDE_DIR,
    CORRECTION_CLUSTER_SIMILARITY,
    RULE_MATCH_SIMILARITY,
    EFFECTIVENESS_REVIEW_WINDOW,
)


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


def load_observations():
    """Load all observations sorted by time."""
    observations = []
    if not os.path.isdir(OBSERVATIONS_DIR):
        return observations
    for f in sorted(Path(OBSERVATIONS_DIR).glob("*.json")):
        try:
            with open(f) as fh:
                observations.append(json.load(fh))
        except (json.JSONDecodeError, IOError):
            continue
    return observations


def get_observation_count():
    """Return the current number of observation files."""
    if not os.path.isdir(OBSERVATIONS_DIR):
        return 0
    return len(list(Path(OBSERVATIONS_DIR).glob("*.json")))


def compute_baseline(proposal, observations):
    """Compute a baseline metric snapshot for an approved proposal.

    Returns a dict with metric_type, metric_value, metric_params, observation_window,
    and lower_is_better flag. Returns None if the proposal type can't be measured.
    """
    ptype = proposal.get("type", "")
    content = proposal.get("content", {})
    window = observations[-50:] if len(observations) >= 50 else observations

    if ptype == "feedback_memory":
        name = content.get("name", "")

        # Try to extract tool and category from the name (e.g., "feedback-read-file-not-found")
        known_tools = {"bash", "edit", "read", "grep", "glob", "agent", "write", "skill"}
        tool = None
        category = None
        if name:
            parts = name.replace("feedback-", "").split("-", 1)
            if len(parts) >= 2 and parts[0].lower() in known_tools:
                tool = parts[0].capitalize()
                category = parts[1]

        if tool and category:
            count = 0
            for obs in window:
                for t, msgs in obs.get("error_messages", {}).items():
                    if t.lower() == tool.lower():
                        count += len(msgs)
            return {
                "metric_type": "error_category_count",
                "metric_value": count,
                "metric_params": {"tool": tool, "category": category},
                "observation_window": len(window),
                "lower_is_better": True,
            }

        # Correction cluster proposals
        body = content.get("body", "")
        if body:
            count = 0
            for obs in window:
                for corr in obs.get("corrections", []):
                    if _text_overlap(body.lower(), corr.lower()) >= CORRECTION_CLUSTER_SIMILARITY:
                        count += 1
            return {
                "metric_type": "correction_cluster_count",
                "metric_value": count,
                "metric_params": {"fingerprint": body[:200]},
                "observation_window": len(window),
                "lower_is_better": True,
            }

    elif ptype == "agent_patch":
        target = content.get("target", "")
        agent_type = target.replace("agents/", "").replace(".md", "")
        if agent_type:
            total = 0
            errors = 0
            for obs in window:
                for agent in obs.get("agents_used", []):
                    if agent.get("subagent_type") == agent_type:
                        total += 1
                        if agent.get("is_error"):
                            errors += 1
            error_rate = errors / max(total, 1)
            return {
                "metric_type": "agent_error_rate",
                "metric_value": round(error_rate, 3),
                "metric_params": {"agent_type": agent_type, "total_calls": total},
                "observation_window": len(window),
                "lower_is_better": True,
            }

    elif ptype == "claude_md_patch":
        correction_text = content.get("correction_text", "")
        rule = content.get("rule", "")
        match_text = correction_text or rule
        if match_text:
            count = 0
            for obs in window:
                for corr in obs.get("corrections", []):
                    if _text_overlap(match_text.lower(), corr.lower()) >= RULE_MATCH_SIMILARITY:
                        count += 1
            return {
                "metric_type": "correction_match_count",
                "metric_value": count,
                "metric_params": {"match_text": match_text[:200]},
                "observation_window": len(window),
                "lower_is_better": True,
            }

    elif ptype == "investigation":
        scores = [obs.get("score", 0) for obs in window if obs.get("score") is not None]
        if scores:
            return {
                "metric_type": "avg_score",
                "metric_value": round(sum(scores) / len(scores), 1),
                "metric_params": {},
                "observation_window": len(window),
                "lower_is_better": False,
            }

    elif ptype == "skill_patch":
        target = content.get("target", "")
        tool = content.get("tool", "")
        if not tool and target:
            tool = target.split("/")[0] if "/" in target else target
        if tool:
            total = 0
            errors = 0
            for obs in window:
                total += obs.get("tool_distribution", {}).get(tool, 0)
                errors += obs.get("error_distribution", {}).get(tool, 0)
            error_rate = errors / max(total, 1)
            return {
                "metric_type": "tool_error_rate",
                "metric_value": round(error_rate, 3),
                "metric_params": {"tool": tool},
                "observation_window": len(window),
                "lower_is_better": True,
            }

    return None


def _text_overlap(text1, text2):
    """Quick word overlap score between two texts."""
    stop_words = {"the", "a", "an", "is", "are", "was", "were", "be", "been",
                  "have", "has", "had", "do", "does", "did", "will", "would",
                  "could", "should", "that", "this", "with", "from", "for",
                  "not", "but", "and", "or", "to", "of", "in", "on", "at", "by"}
    words1 = {w for w in text1.split() if len(w) > 2 and w not in stop_words}
    words2 = {w for w in text2.split() if len(w) > 2 and w not in stop_words}
    if not words1 or not words2:
        return 0.0
    return len(words1 & words2) / len(words1 | words2)


def infer_artifact_path(proposal):
    """Infer where the artifact will be created based on proposal type."""
    ptype = proposal.get("type", "")
    content = proposal.get("content", {})

    if ptype == "feedback_memory":
        name = content.get("name", "")
        if name:
            # Memory location varies by project; return relative hint
            return f"memory/{name}.md"
    elif ptype == "claude_md_patch":
        return os.path.join(CLAUDE_DIR, "CLAUDE.md")
    elif ptype == "agent_patch":
        target = content.get("target", "")
        if target:
            return os.path.join(CLAUDE_DIR, target)
    elif ptype == "skill_patch":
        target = content.get("target", "")
        if target:
            return target

    return None


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
        elif ptype == "agent_patch":
            lines.append(f"  **{i}.** [{priority.upper()}] {ptype}: {content.get('description', '')[:80]}{age}")
        elif ptype == "claude_md_patch":
            lines.append(f"  **{i}.** [{priority.upper()}] {ptype}: {content.get('description', '')[:80]}{age}")
        elif ptype == "revert":
            reason = content.get("reason", "ineffective")
            delta = content.get("delta_pct", "?")
            lines.append(f"  **{i}.** [{priority.upper()}] REVERT: {content.get('original_summary', '')[:60]}{age}")
            lines.append(f"      Reason: {reason} (delta: {delta}%), artifact: {content.get('artifact_path', 'none')}")
        elif ptype == "memory_cleanup":
            lines.append(f"  **{i}.** [{priority.upper()}] memory_cleanup: {content.get('target', '')}{age}")
            lines.append(f"      {content.get('issue', '')[:80]}")
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

    # Load observations once for baseline computation (only needed for approvals)
    observations = load_observations() if action == "approved" else []
    obs_count = len(observations)

    for idx in sorted(indices):
        if idx < 1 or idx > len(pending):
            print(f"Index {idx} out of range (1-{len(pending)})", file=sys.stderr)
            continue

        p = pending[idx - 1]
        update_proposal_status(p, action)

        entry = {
            "action": action,
            "type": p.get("type"),
            "summary": _fingerprint(p)[:60],
            "content": {k: v for k, v in p.get("content", {}).items()},
            "date": datetime.now().isoformat(),
        }

        # Capture baseline and artifact path on approval (skip for reverts)
        if action == "approved" and p.get("type") != "revert":
            baseline = compute_baseline(p, observations)
            artifact = infer_artifact_path(p)
            if baseline:
                entry["baseline"] = baseline
            if artifact:
                entry["artifact_path"] = artifact
            entry["review_window"] = {
                "type": "session_count",
                "value": EFFECTIVENESS_REVIEW_WINDOW,
                "sessions_at_approval": obs_count,
            }
            entry["effectiveness_status"] = "pending"

        history.append(entry)
        acted += 1

        marker = "✓" if action == "approved" else "✗"
        print(f"  {marker} #{idx}: {p.get('type')} — {_fingerprint(p)[:60]}")
        if action == "approved" and entry.get("baseline"):
            b = entry["baseline"]
            print(f"      Baseline: {b['metric_type']} = {b['metric_value']} (over {b['observation_window']} sessions)")
            print(f"      Will check effectiveness after {EFFECTIVENESS_REVIEW_WINDOW} more sessions")

    save_history(history)
    print(f"\n{acted} proposal(s) {action}.")

    if action == "approved":
        print("\nApproved proposals need manual execution:")
        print("  - feedback_memory -> Create memory file in your Claude memory directory")
        print("  - skill_patch -> Edit the relevant skill file")
        print("  - eval_query -> Add to skill's trigger-eval.json")
        print("  - claude_md_patch -> Add rule to CLAUDE.md Corrections section")
        print("  - agent_patch -> Edit agent definition")
        print("  - revert -> Delete the artifact file or revert the change")


if __name__ == "__main__":
    main()
