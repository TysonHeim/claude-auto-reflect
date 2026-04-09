#!/usr/bin/env python3
"""
Manage improvement proposals — list, approve, reject, expire.

Usage:
    python3 proposals.py --list                    # show pending proposals
    python3 proposals.py --approve 1,3,5           # approve by index
    python3 proposals.py --reject 2,4              # reject by index
    python3 proposals.py --approve-id <fp>         # approve by content fingerprint (stable, used by dashboard)
    python3 proposals.py --reject-id <fp>          # reject by content fingerprint
    python3 proposals.py --reject-all              # reject all pending (batch cleanup)
    python3 proposals.py --expire                  # auto-reject proposals older than 7 days
    python3 proposals.py --history                 # show approved/rejected log
    python3 proposals.py --json                    # raw JSON (combinable with other flags)
"""

import hashlib
import json
import os
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

# Import paths and thresholds — single source of truth in config.py
from auto_reflect.config import (
    IMPROVEMENTS_DIR,
    OBSERVATIONS_DIR,
    PROPOSAL_HISTORY as HISTORY_FILE,
    MEMORY_DIR,
    AGENTS_DIR,
    SKILLS_DIR,
    EXPIRE_DAYS,
    CORRECTION_CLUSTER_SIMILARITY,
    RULE_MATCH_SIMILARITY,
    EFFECTIVENESS_REVIEW_WINDOW,
)

CLAUDE_DIR = os.environ.get("CLAUDE_DIR", os.path.expanduser("~/.claude"))
CLAUDE_MD = os.path.join(CLAUDE_DIR, "CLAUDE.md")


def load_all_pending():
    """Load all pending proposals across all files, with source tracking."""
    pending = []
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

    # Find and update the matching proposal by content fingerprint
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
    """Generate a SHA256 content fingerprint for matching."""
    canonical = {k: v for k, v in proposal.items() if not k.startswith("_")}
    return hashlib.sha256(json.dumps(canonical, sort_keys=True).encode()).hexdigest()[:16]


def load_observations():
    """Load all observations sorted by time."""
    observations = []
    for f in sorted(Path(OBSERVATIONS_DIR).glob("*.json")):
        try:
            with open(f) as fh:
                observations.append(json.load(fh))
        except (json.JSONDecodeError, IOError):
            continue
    return observations


def get_observation_count():
    """Return the current number of observation files."""
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
        # Error pattern proposals have tool + category in content
        evidence = content.get("evidence", "")
        name = content.get("name", "")
        description = content.get("description", "")

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
            # Count matching errors in recent observations
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

        # Correction cluster proposals — count matching corrections
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
        # Agent error rate
        target = content.get("target", "")  # e.g., "agents/code-explorer.md"
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
        # Correction frequency matching the rule
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
        # Average session score
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
        # Tool error rate
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
            return os.path.join(MEMORY_DIR, f"{name}.md")
    elif ptype == "claude_md_patch":
        return CLAUDE_MD
    elif ptype == "agent_patch":
        target = content.get("target", "")
        if target:
            return os.path.join(os.path.dirname(AGENTS_DIR), target)
    elif ptype == "skill_patch":
        target = content.get("target", "")
        if target:
            return target

    return None


MEMORY_INDEX = os.path.join(MEMORY_DIR, "MEMORY.md") if MEMORY_DIR else ""
# SKILLS_DIR already imported from config


def apply_proposal(proposal):
    """Auto-apply an approved proposal. Returns (success, message)."""
    # Support both old-style nested `type`+`content` and flat `action`+direct fields
    ptype = proposal.get("action", proposal.get("type", ""))
    content = proposal.get("content") or {}

    # Map flat proposal fields into the content dict the helpers expect
    if not content:
        content = {
            # feedback_memory fields
            "name": proposal.get("memory_file", "").replace(".md", "") or None,
            "body": proposal.get("memory_content", ""),
            "description": proposal.get("proposal", ""),
            "memory_type": "feedback",
            # memory_cleanup fields
            "target": proposal.get("memory_file", ""),
            "action": proposal.get("cleanup_action", ""),
            "issue": proposal.get("proposal", ""),
            # claude_md_patch / skill_patch fields
            "rule": proposal.get("rule", ""),
        }

    try:
        if ptype == "feedback_memory":
            return _apply_feedback_memory(content)
        elif ptype == "claude_md_patch":
            return _apply_claude_md_rule(content)
        elif ptype == "memory_cleanup":
            return _apply_memory_cleanup(content)
        elif ptype in ("skill_patch", "agent_patch", "eval_query", "investigation"):
            return _apply_via_claude(proposal)
        else:
            return False, f"Unknown proposal type: {ptype}"
    except Exception as e:
        return False, f"Apply failed: {e}"


def _apply_feedback_memory(content):
    """Write a memory .md file and update MEMORY.md index."""
    name = content.get("name", f"feedback-{datetime.now().strftime('%Y%m%d-%H%M%S')}")
    description = content.get("description", "Auto-generated feedback memory")
    memory_type = content.get("memory_type", "feedback")
    body = content.get("body", "")

    if not body:
        return False, "No body content in proposal"

    # Write memory file
    filename = f"{name}.md"
    filepath = os.path.join(MEMORY_DIR, filename)

    memory_content = f"""---
name: {name}
description: {description}
type: {memory_type}
---

{body}
"""
    os.makedirs(MEMORY_DIR, exist_ok=True)
    with open(filepath, "w") as f:
        f.write(memory_content)

    # Update MEMORY.md index
    _update_memory_index(filename, description)

    return True, f"Created {filepath}"


def _apply_claude_md_rule(content):
    """Append a rule to CLAUDE.md Corrections section."""
    rule = content.get("rule", content.get("body", ""))
    if not rule:
        return False, "No rule content in proposal"

    if not os.path.exists(CLAUDE_MD):
        return False, f"CLAUDE.md not found at {CLAUDE_MD}"

    with open(CLAUDE_MD) as f:
        text = f.read()

    # Find the Corrections section and append
    marker = "## Corrections"
    if marker not in text:
        # Add the section if it doesn't exist
        text += f"\n\n{marker}\n- {rule}\n"
    else:
        # Append after the last line in the Corrections section
        idx = text.index(marker)
        # Find end of Corrections section (next ## or end of file)
        rest = text[idx + len(marker):]
        next_section = rest.find("\n## ")
        if next_section == -1:
            text = text.rstrip() + f"\n- {rule}\n"
        else:
            insert_at = idx + len(marker) + next_section
            text = text[:insert_at] + f"\n- {rule}" + text[insert_at:]

    with open(CLAUDE_MD, "w") as f:
        f.write(text)

    return True, f"Added rule to {CLAUDE_MD}"


def _apply_memory_cleanup(content):
    """Apply memory cleanup -- fix or delete based on the proposal's action field."""
    target = content.get("target", content.get("name", ""))
    action_text = content.get("action", "").lower()
    issue = content.get("issue", "").lower()

    if not target:
        return False, "No target file specified"

    filename = target if target.endswith(".md") else f"{target}.md"
    filepath = os.path.join(MEMORY_DIR, filename)

    # If the proposal says to fix/add frontmatter (not delete), route to Claude
    if any(word in action_text for word in ["add frontmatter", "convert", "fix", "update"]):
        return _apply_via_claude({
            "type": "memory_cleanup",
            "proposal": f"Fix memory file: {issue}",
            "content": content,
        })

    # Only delete if the proposal explicitly says to delete or remove
    if any(word in action_text for word in ["delete", "remove"]):
        if os.path.exists(filepath):
            os.remove(filepath)
            _remove_from_memory_index(filename)
            return True, f"Deleted {filepath}"
        else:
            return False, f"File not found: {filepath}"

    # Default: route to Claude for safety (don't delete unless told to)
    return _apply_via_claude({
        "type": "memory_cleanup",
        "proposal": f"Memory cleanup: {issue}",
        "content": content,
    })


def _apply_via_claude(proposal):
    """Spawn claude -p to apply complex proposals (skill_patch, agent_patch, eval_query, investigation)."""
    ptype = proposal.get("action", proposal.get("type", ""))
    content = proposal.get("content") or {}
    proposal_text = proposal.get("proposal", "")

    # Build a focused prompt for Claude
    if ptype == "skill_patch":
        target = content.get("target", "")
        issue = content.get("issue", "")
        suggestion = content.get("suggestion", "")
        prompt = (
            f"Auto-reflect has detected a skill issue and the user has approved a fix.\n\n"
            f"Target: {target}\n"
            f"Issue: {issue}\n"
            f"Suggestion: {suggestion}\n"
            f"Full proposal: {proposal_text}\n\n"
            f"Apply this fix. If it targets a specific skill, find it under ~/.claude/skills/ "
            f"and make the improvement. If it's a general tool usage pattern, add a feedback "
            f"memory to {MEMORY_DIR}/ and update MEMORY.md. "
            f"Be concise and make minimal targeted changes."
        )
    elif ptype == "agent_patch":
        target = content.get("target", "")
        issue = content.get("issue", "")
        suggestion = content.get("suggestion", "")
        prompt = (
            f"Auto-reflect has detected an agent issue and the user has approved a fix.\n\n"
            f"Target: {target}\n"
            f"Issue: {issue}\n"
            f"Suggestion: {suggestion}\n\n"
            f"Find the agent definition under ~/.claude/agents/ and apply the improvement. "
            f"Be concise and make minimal targeted changes."
        )
    elif ptype == "eval_query":
        skill = content.get("skill", "")
        query = content.get("query", "")
        should_trigger = content.get("should_trigger", True)
        prompt = (
            f"Auto-reflect wants to add a trigger eval query for skill '{skill}'.\n\n"
            f"Query: {query}\n"
            f"Should trigger: {should_trigger}\n\n"
            f"Add this to the skill's evals/trigger-eval.json at ~/.claude/skills/{skill}/evals/trigger-eval.json. "
            f"Follow the existing format in the file. If the file doesn't exist, create it."
        )
    elif ptype == "investigation":
        prompt = (
            f"Auto-reflect has detected a performance issue that needs investigation. "
            f"The user has approved looking into it.\n\n"
            f"Issue: {proposal_text}\n"
            f"Evidence: {json.dumps(content, indent=2)}\n\n"
            f"Investigate the root cause. Check relevant session observations in "
            f"~/.claude/auto-reflect/observations/ for patterns. "
            f"Write your findings as a feedback memory in "
            f"{MEMORY_DIR}/ and update MEMORY.md. "
            f"Focus on actionable insights that will prevent the issue."
        )
    elif ptype == "memory_cleanup":
        target = content.get("target", "")
        issue = content.get("issue", "")
        action_text = content.get("action", "")
        prompt = (
            f"Auto-reflect has flagged a memory file that needs fixing.\n\n"
            f"File: {MEMORY_DIR}/{target}\n"
            f"Issue: {issue}\n"
            f"Suggested action: {action_text}\n\n"
            f"Read the file, understand its content, and add proper frontmatter "
            f"(name, description, type fields). Do NOT delete the file. "
            f"The type should be one of: user, feedback, project, reference."
        )
    else:
        return False, f"No claude handler for type: {ptype}"

    # Spawn claude -p in the background
    try:
        result = subprocess.run(
            ["claude", "-p", "--allowedTools", "Edit,Read,Write,Glob,Grep"],
            input=prompt, capture_output=True, text=True, timeout=120,
            cwd=os.path.expanduser("~/.claude"),
        )
        if result.returncode == 0:
            # Truncate output for the log
            output = result.stdout.strip()[:500]
            return True, f"Claude applied: {output}"
        else:
            return False, f"Claude exited {result.returncode}: {result.stderr[:300]}"
    except subprocess.TimeoutExpired:
        return False, "Claude timed out (120s)"
    except FileNotFoundError:
        return False, "claude CLI not found in PATH"


def _update_memory_index(filename, description):
    """Add an entry to MEMORY.md index under Feedback Memories."""
    if not os.path.exists(MEMORY_INDEX):
        return

    with open(MEMORY_INDEX) as f:
        text = f.read()

    entry = f"- [{filename}]({filename}) \u2014 {description}"

    # Add under Feedback Memories section
    marker = "## Feedback Memories"
    if marker in text:
        idx = text.index(marker) + len(marker)
        # Find end of section (next ## or end)
        rest = text[idx:]
        next_section = rest.find("\n## ")
        if next_section == -1:
            text = text.rstrip() + f"\n{entry}\n"
        else:
            insert_at = idx + next_section
            text = text[:insert_at] + f"\n{entry}" + text[insert_at:]
    else:
        text = text.rstrip() + f"\n\n{marker}\n{entry}\n"

    with open(MEMORY_INDEX, "w") as f:
        f.write(text)


def _remove_from_memory_index(filename):
    """Remove a file reference from MEMORY.md index."""
    if not os.path.exists(MEMORY_INDEX):
        return

    with open(MEMORY_INDEX) as f:
        lines = f.readlines()

    filtered = [l for l in lines if filename not in l]

    with open(MEMORY_INDEX, "w") as f:
        f.writelines(filtered)


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
        # Strip internal fields
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

        # Age indicator
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
    lines.append(f"Approve: `python3 proposals.py --approve 1,3,5`")
    lines.append(f"Reject:  `python3 proposals.py --reject 2,4`")
    lines.append(f"Reject all: `python3 proposals.py --reject-all`")

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


def _act_on_proposal(p, action, history, observations, obs_count):
    """Apply a single approve/reject action on proposal p, appending to history.

    Shared by both index-based and fingerprint-based flows.
    Returns the history entry dict (already appended to history).
    """
    update_proposal_status(p, action)

    entry = {
        "action": action,
        "type": p.get("type"),
        "summary": _fingerprint(p)[:60],
        "content": {k: v for k, v in p.get("content", {}).items()},
        "date": datetime.now().isoformat(),
    }

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

    marker = "✓" if action == "approved" else "✗"
    print(f"  {marker} {p.get('type')} — {_fingerprint(p)[:60]}")

    if action == "approved":
        success, msg = apply_proposal(p)
        if success:
            entry["applied"] = True
            entry["apply_result"] = msg[:300]
            print(f"      Applied: {msg[:120]}")
        else:
            entry["applied"] = False
            entry["apply_error"] = msg[:300]
            print(f"      Apply failed: {msg[:120]}", file=sys.stderr)

        if entry.get("baseline"):
            b = entry["baseline"]
            print(f"      Baseline: {b['metric_type']} = {b['metric_value']} (over {b['observation_window']} sessions)")
            print(f"      Will check effectiveness after {EFFECTIVENESS_REVIEW_WINDOW} more sessions")

    return entry


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

    if "--approve-all" in args:
        pending = load_all_pending()
        if not pending:
            print("No pending proposals to approve.")
            return

        history = load_history()
        for p in pending:
            update_proposal_status(p, "approved")
            success, msg = apply_proposal(p)
            entry = {
                "action": "approved",
                "type": p.get("action", p.get("type")),
                "summary": _fingerprint(p)[:60],
                "date": datetime.now().isoformat(),
                "applied": True if success else False,
                "apply_result": msg if success else "",
                "apply_error": msg if not success else "",
            }
            history.append(entry)
        save_history(history)
        print(f"Approved and applied all {len(pending)} pending proposals.")
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

    # Reject all pending proposals of a given type (e.g. --reject-type investigation)
    for i, a in enumerate(args):
        if a == "--reject-type" and i + 1 < len(args):
            target_type = args[i + 1]
            pending = load_all_pending()
            matched = [p for p in pending if p.get("type") == target_type]
            if not matched:
                print(f"No pending proposals of type {target_type!r}.")
                return
            history = load_history()
            for p in matched:
                update_proposal_status(p, "rejected")
                history.append({
                    "action": "rejected",
                    "type": p.get("type"),
                    "summary": _fingerprint(p)[:60],
                    "date": datetime.now().isoformat(),
                })
            save_history(history)
            print(f"Rejected {len(matched)} pending proposal(s) of type {target_type!r}.")
            return

    # Approve or reject by stable content fingerprint (used by dashboard — avoids positional index mismatch)
    for i, a in enumerate(args):
        if a in ("--approve-id", "--reject-id") and i + 1 < len(args):
            action = "approved" if a == "--approve-id" else "rejected"
            target_fp = args[i + 1]
            pending = load_all_pending()
            matched = [p for p in pending if _fingerprint(p) == target_fp]
            if not matched:
                print(f"No pending proposal found with fingerprint: {target_fp!r}", file=sys.stderr)
                sys.exit(1)
            history = load_history()
            observations = load_observations() if action == "approved" else []
            obs_count = len(observations)
            for p in matched:
                _act_on_proposal(p, action, history, observations, obs_count)
            save_history(history)
            verb = "approved" if action == "approved" else "rejected"
            print(f"\n{len(matched)} proposal(s) {verb}.")
            return

    # Approve or reject by index
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
        print("Usage: proposals.py --approve 1,3 | --approve-id <fp> | --reject 2,4 | --reject-id <fp> | --reject-type <type> | --list | --reject-all | --expire")
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
        _act_on_proposal(p, action, history, observations, obs_count)
        acted += 1

    save_history(history)
    print(f"\n{acted} proposal(s) {action}.")

    # Summary of applied changes
    if action == "approved":
        applied_count = sum(1 for h in history[-acted:] if h.get("applied"))
        failed_count = acted - applied_count
        if applied_count:
            print(f"\n{applied_count} proposal(s) auto-applied.")
        if failed_count:
            print(f"{failed_count} proposal(s) failed to apply — check errors above.")


if __name__ == "__main__":
    main()
