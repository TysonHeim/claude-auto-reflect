#!/usr/bin/env python3
"""
Analyze a Claude Code session transcript (JSONL) and produce a structured
performance observation.

Usage:
    python3 -m auto_reflect.analyze_session <session.jsonl>
    python3 -m auto_reflect.analyze_session --latest
    python3 -m auto_reflect.analyze_session --latest --json
"""

import json
import sys
import os
import glob
import re
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

from auto_reflect.config import OBSERVATIONS_DIR, SESSIONS_DIR, ensure_dirs


def is_subagent_file(path):
    """Check if a JSONL file is a subagent transcript (not a parent session)."""
    basename = os.path.basename(path)
    if basename.startswith("agent-"):
        return True
    if "/subagents/" in path:
        return True
    return False


def find_latest_session():
    """Find the most recently modified JSONL session file (excluding subagents)."""
    candidates = [
        f for f in glob.glob(os.path.join(SESSIONS_DIR, "**/*.jsonl"), recursive=True)
        if not is_subagent_file(f)
    ]
    if not candidates:
        print("No session files found.", file=sys.stderr)
        sys.exit(1)
    return max(candidates, key=os.path.getmtime)


def parse_session(path):
    """Parse JSONL session into categorized entries."""
    entries = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return entries


def extract_messages(entries):
    """Extract user and assistant messages in order.

    JSONL format: top-level 'type' is 'user'|'assistant'|'system'|'progress'|etc.
    The message body is in entry['message'] with 'role' and 'content'.
    """
    messages = []
    for e in entries:
        entry_type = e.get("type", "")
        if entry_type not in ("user", "assistant"):
            continue
        msg = e.get("message", {})
        role = msg.get("role", entry_type)
        content = msg.get("content", "")
        # Skip entries where content is purely tool_result blocks (not human text)
        if role == "user" and isinstance(content, list):
            has_text = any(
                isinstance(b, str) or (isinstance(b, dict) and b.get("type") == "text")
                for b in content
            )
            if not has_text:
                continue
        messages.append({
            "role": role,
            "content": content,
            "timestamp": e.get("timestamp"),
            "uuid": e.get("uuid"),
        })
    return messages


def extract_tool_calls(entries):
    """Extract tool use and result pairs.

    Tool uses appear in assistant entries (content blocks with type=tool_use).
    Tool results appear in user entries (content blocks with type=tool_result).
    """
    tool_uses = {}
    tool_pairs = []

    for e in entries:
        entry_type = e.get("type", "")
        if entry_type not in ("user", "assistant"):
            continue
        msg = e.get("message", {})
        content = msg.get("content", [])
        if not isinstance(content, list):
            continue

        for block in content:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "tool_use":
                tool_uses[block["id"]] = {
                    "name": block.get("name", ""),
                    "input": block.get("input", {}),
                    "timestamp": e.get("timestamp"),
                }
            elif block.get("type") == "tool_result":
                tool_id = block.get("tool_use_id")
                if tool_id and tool_id in tool_uses:
                    tool_pairs.append({
                        **tool_uses[tool_id],
                        "is_error": block.get("is_error", False),
                        "result_content": block.get("content", ""),
                    })
    return tool_pairs


def _extract_text(content):
    """Extract plain text from message content (string or list of blocks)."""
    if isinstance(content, list):
        text_parts = []
        for b in content:
            if isinstance(b, str):
                text_parts.append(b)
            elif isinstance(b, dict) and b.get("type") == "text":
                text_parts.append(b.get("text", ""))
        return " ".join(text_parts)
    return content or ""


def detect_corrections(messages):
    """Detect likely human corrections — user messages that redirect the assistant.

    Only considers messages AFTER the first assistant response (position > 0)
    to avoid false positives from initial instructions. Patterns are tuned
    to require surrounding context that indicates redirection, not just
    the presence of a keyword.
    """
    correction_patterns = [
        r"\bno[,.]?\s+(don'?t|instead|not that|wrong|actually)",
        r"\binstead[,]?\s+(do|use|try)",
        r"\bthat'?s (wrong|incorrect|not what)",
        r"\bdon'?t\s+(do that|use that|do it)",
        r"\blet'?s not\b",
        r"\bnot what I (asked|meant|wanted)",
        r"\byou (should have|shouldn'?t have|already have|always have)",
        r"\bI (said|told you|already)",
        r"\bstop (doing|making|creating|running\b(?! the))",
    ]

    # Find first assistant message index to establish conversation start
    first_assistant = None
    for i, msg in enumerate(messages):
        if msg["role"] == "assistant":
            first_assistant = i
            break

    corrections = []
    for i, msg in enumerate(messages):
        if msg["role"] != "user":
            continue
        # Skip messages before first assistant response (initial instructions)
        if first_assistant is None or i <= first_assistant:
            continue
        text = _extract_text(msg["content"])
        # Skip long texts (likely system/skill content, not human corrections)
        if not text or len(text) > 500:
            continue
        text_lower = text.lower()
        for pattern in correction_patterns:
            if re.search(pattern, text_lower):
                corrections.append({
                    "index": i,
                    "text": text[:200],
                    "timestamp": msg.get("timestamp"),
                })
                break
    return corrections


def detect_retries(tool_pairs):
    """Detect tool calls that look like retries (same tool, similar input, after an error)."""
    retries = []
    recent_errors = defaultdict(list)

    for i, tp in enumerate(tool_pairs):
        name = tp["name"]
        if tp["is_error"]:
            recent_errors[name].append(i)
        elif name in recent_errors and recent_errors[name]:
            last_error_idx = recent_errors[name][-1]
            if i - last_error_idx <= 3:  # within 3 tool calls
                retries.append({
                    "tool": name,
                    "error_index": last_error_idx,
                    "retry_index": i,
                })
            recent_errors[name].clear()
    return retries


def detect_skills_used(tool_pairs):
    """Find which skills were invoked."""
    skills = []
    for tp in tool_pairs:
        if tp["name"] == "Skill":
            skill_name = tp["input"].get("skill", "unknown")
            skills.append(skill_name)
    return skills


# Bash anti-patterns: commands that should use dedicated tools instead
_TOOL_MISUSE_PATTERNS = [
    # grep/rg -> Grep tool
    (r"(?:^|\||\&\&|\;)\s*(?:grep|rg|egrep|fgrep)\s", "grep/rg", "Grep"),
    # cat/head/tail -> Read tool
    (r"(?:^|\||\&\&|\;)\s*(?:cat|head|tail)\s", "cat/head/tail", "Read"),
    # sed/awk for editing -> Edit tool
    (r"(?:^|\||\&\&|\;)\s*(?:sed|awk)\s.*-i", "sed -i/awk", "Edit"),
    # find/ls for file search -> Glob tool
    (r"(?:^|\||\&\&|\;)\s*find\s", "find", "Glob"),
    # echo/cat heredoc for writing -> Write tool
    (r"(?:echo|cat\s*<<)\s.*>(?!>)\s*\S", "echo/cat>file", "Write"),
]


def detect_tool_misuse(tool_pairs):
    """Detect Bash calls that should have used a dedicated tool.

    Returns a list of dicts with the tool call index, the command snippet,
    the anti-pattern matched, and the tool that should have been used.
    """
    misuses = []
    for i, tp in enumerate(tool_pairs):
        if tp["name"] != "Bash":
            continue
        command = tp["input"].get("command", "")
        if not command:
            continue
        for pattern, label, preferred in _TOOL_MISUSE_PATTERNS:
            if re.search(pattern, command):
                misuses.append({
                    "index": i,
                    "command": command[:200],
                    "anti_pattern": label,
                    "preferred_tool": preferred,
                })
                break  # one match per Bash call is enough
    return misuses


def compute_score(metrics):
    """Compute a 0-100 performance score.

    Weights are calibrated so that:
    - A normal session with a few exploratory errors: ~90
    - A session with real problems (corrections, many retries): ~60-70
    - A catastrophic session: <50
    """
    score = 100

    # Penalize errors — scale by error rate, not just count
    tool_count = max(metrics["tool_call_count"], 1)
    error_rate = metrics["error_count"] / tool_count
    score -= min(error_rate * 100, 30)  # Up to -30 for high error rate

    # Penalize corrections (-7 each, max -35)
    score -= min(metrics["correction_count"] * 7, 35)

    # Penalize retries — scale by retry rate
    retry_rate = metrics["retry_count"] / max(tool_count, 1)
    score -= min(retry_rate * 80, 20)  # Up to -20 for high retry rate

    # Penalize tool misuse — using Bash for tasks with dedicated tools
    # -5 per misuse, max -25. This catches "wrong thing done successfully".
    misuse_count = metrics.get("tool_misuse_count", 0)
    score -= min(misuse_count * 5, 25)  # Up to -25 for tool misuse

    return max(0, min(100, round(score)))


def analyze(path):
    """Run full analysis on a session file."""
    entries = parse_session(path)
    messages = extract_messages(entries)
    tool_pairs = extract_tool_calls(entries)
    corrections = detect_corrections(messages)
    retries = detect_retries(tool_pairs)
    skills = detect_skills_used(tool_pairs)
    tool_misuses = detect_tool_misuse(tool_pairs)

    # Tool distribution
    tool_counts = Counter(tp["name"] for tp in tool_pairs)
    error_tools = Counter(tp["name"] for tp in tool_pairs if tp["is_error"])

    # Capture actual error messages (truncated) for smarter proposal generation
    error_messages = defaultdict(list)
    for tp in tool_pairs:
        if tp["is_error"]:
            result = tp.get("result_content", "")
            if isinstance(result, list):
                result = " ".join(
                    b.get("text", "") if isinstance(b, dict) else str(b)
                    for b in result
                )
            msg = str(result).strip()[:300]
            if msg:
                error_messages[tp["name"]].append(msg)

    # Message counts
    user_msgs = [m for m in messages if m["role"] == "user"]
    assistant_msgs = [m for m in messages if m["role"] == "assistant"]

    # Timestamps
    timestamps = [e.get("timestamp") for e in entries if e.get("timestamp")]
    start_time = min(timestamps) if timestamps else None
    end_time = max(timestamps) if timestamps else None

    # Find session ID from first entry that has one
    session_id = "unknown"
    for e in entries:
        sid = e.get("sessionId")
        if sid:
            session_id = sid
            break

    metrics = {
        "session_file": path,
        "session_id": session_id,
        "start_time": start_time,
        "end_time": end_time,
        "total_entries": len(entries),
        "user_message_count": len(user_msgs),
        "assistant_message_count": len(assistant_msgs),
        "total_turns": len(user_msgs),
        "tool_call_count": len(tool_pairs),
        "error_count": sum(1 for tp in tool_pairs if tp["is_error"]),
        "correction_count": len(corrections),
        "retry_count": len(retries),
        "tool_misuse_count": len(tool_misuses),
        "skills_used": skills,
        "tool_distribution": dict(tool_counts.most_common()),
        "error_distribution": dict(error_tools.most_common()),
        "corrections": [c["text"] for c in corrections],
        "error_messages": {k: v[:5] for k, v in error_messages.items()},  # top 5 per tool
        "retries": retries,
        "tool_misuses": tool_misuses,
    }
    metrics["score"] = compute_score(metrics)

    return metrics


def save_observation(metrics):
    """Save observation to the observations directory. Deduplicates by session_id."""
    ensure_dirs()
    session_id = metrics["session_id"][:8]

    existing = glob.glob(os.path.join(OBSERVATIONS_DIR, f"*_{session_id}.json"))
    if existing:
        filepath = existing[0]
    else:
        start = metrics.get("start_time", "")
        if start:
            try:
                dt = datetime.fromisoformat(start.replace("Z", "+00:00"))
                timestamp = dt.strftime("%Y-%m-%d_%H%M%S")
            except (ValueError, AttributeError):
                timestamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
        else:
            timestamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
        filename = f"{timestamp}_{session_id}.json"
        filepath = os.path.join(OBSERVATIONS_DIR, filename)

    with open(filepath, "w") as f:
        json.dump(metrics, f, indent=2, default=str)

    return filepath


def format_markdown(metrics):
    """Format metrics as readable markdown."""
    lines = []
    lines.append(f"## Session Analysis")
    lines.append(f"**Score: {metrics['score']}/100**")
    lines.append("")
    lines.append(f"| Metric | Value |")
    lines.append(f"|--------|-------|")
    lines.append(f"| Turns | {metrics['total_turns']} |")
    lines.append(f"| Tool calls | {metrics['tool_call_count']} |")
    lines.append(f"| Errors | {metrics['error_count']} |")
    lines.append(f"| Corrections | {metrics['correction_count']} |")
    lines.append(f"| Retries | {metrics['retry_count']} |")
    lines.append(f"| Tool misuses | {metrics.get('tool_misuse_count', 0)} |")
    lines.append(f"| Skills used | {', '.join(metrics['skills_used']) or 'none'} |")
    lines.append("")

    if metrics["tool_distribution"]:
        lines.append("### Tool Distribution")
        for tool, count in sorted(metrics["tool_distribution"].items(), key=lambda x: -x[1]):
            error_count = metrics["error_distribution"].get(tool, 0)
            error_str = f" ({error_count} errors)" if error_count else ""
            lines.append(f"- {tool}: {count}{error_str}")
        lines.append("")

    if metrics["corrections"]:
        lines.append("### Human Corrections")
        for c in metrics["corrections"]:
            lines.append(f"- {c[:120]}")
        lines.append("")

    if metrics["retries"]:
        lines.append("### Retries Detected")
        for r in metrics["retries"]:
            lines.append(f"- {r['tool']} (error at call #{r['error_index']}, retry at #{r['retry_index']})")
        lines.append("")

    if metrics.get("tool_misuses"):
        lines.append("### Tool Misuses")
        for m in metrics["tool_misuses"]:
            lines.append(f"- Bash `{m['anti_pattern']}` should use **{m['preferred_tool']}**: `{m['command'][:100]}`")
        lines.append("")

    return "\n".join(lines)


def main():
    args = sys.argv[1:]
    output_json = "--json" in args
    args = [a for a in args if a != "--json"]

    if not args or args[0] == "--latest":
        path = find_latest_session()
        print(f"Analyzing: {path}", file=sys.stderr)
    else:
        path = args[0]
        if not os.path.exists(path):
            print(f"File not found: {path}", file=sys.stderr)
            sys.exit(1)

    metrics = analyze(path)
    obs_path = save_observation(metrics)

    if output_json:
        print(json.dumps(metrics, indent=2, default=str))
    else:
        print(format_markdown(metrics))

    print(f"\nObservation saved: {obs_path}", file=sys.stderr)


if __name__ == "__main__":
    main()
