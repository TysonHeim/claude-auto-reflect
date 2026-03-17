#!/usr/bin/env python3
"""
Generate concrete improvement proposals from detected patterns and corrections.

Takes pattern analysis + observation data and produces actionable changes:
- Feedback memories (from clustered corrections)
- Skill patches (from specific error analysis)
- Investigations (from score declines)
- Agent patches (from agent error rates, unused agents)
- CLAUDE.md patches (from recurring corrections, rule violations)

Key features:
- Rejection cache: won't re-propose things you already rejected
- Correction clustering: groups similar corrections into single proposals
- Error message analysis: proposes fixes based on actual error content, not just counts

Usage:
    python3 -m auto_reflect.propose_improvements
    python3 -m auto_reflect.propose_improvements --json
"""

import json
import os
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from pathlib import Path

from auto_reflect.config import (
    OBSERVATIONS_DIR,
    PATTERNS_DIR,
    IMPROVEMENTS_DIR,
    PROPOSAL_HISTORY,
    REJECTION_SUPPRESS_DAYS,
    CLAUDE_DIR,
    ensure_dirs,
)


def load_latest_patterns():
    """Load most recent pattern analysis."""
    if not os.path.isdir(PATTERNS_DIR):
        return []
    files = sorted(Path(PATTERNS_DIR).glob("*_patterns.json"), reverse=True)
    if not files:
        return []
    with open(files[0]) as f:
        return json.load(f)


def load_all_observations():
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


def load_existing_proposals():
    """Load all existing proposals to avoid duplicates."""
    proposals = []
    if not os.path.isdir(IMPROVEMENTS_DIR):
        return proposals
    for f in Path(IMPROVEMENTS_DIR).glob("*_proposals.json"):
        try:
            with open(f) as fh:
                data = json.load(fh)
                if isinstance(data, list):
                    proposals.extend(data)
                elif isinstance(data, dict):
                    proposals.append(data)
        except (json.JSONDecodeError, IOError):
            continue
    return proposals


def load_rejection_cache():
    """Load fingerprints of rejected proposals with their rejection dates."""
    if not os.path.exists(PROPOSAL_HISTORY):
        return {}

    cache = {}
    try:
        with open(PROPOSAL_HISTORY) as f:
            history = json.load(f)
    except (json.JSONDecodeError, IOError):
        return {}

    cutoff = datetime.now() - timedelta(days=REJECTION_SUPPRESS_DAYS)

    for entry in history:
        if entry.get("action") != "rejected":
            continue
        # Extract fingerprint from summary
        fp = entry.get("summary", "").lower().strip()[:100]
        # Check if rejection is still within suppression window
        date_str = entry.get("date", "")
        try:
            rejection_date = datetime.fromisoformat(date_str)
            if rejection_date > cutoff:
                cache[fp] = rejection_date.isoformat()
        except (ValueError, TypeError):
            # If we can't parse date, suppress anyway (conservative)
            cache[fp] = "unknown"

    return cache


def is_rejected(proposal, rejection_cache):
    """Check if a proposal matches a previously rejected fingerprint."""
    content = proposal.get("content", {})
    # Build multiple fingerprint variants to catch near-matches
    candidates = [
        content.get("body", ""),
        content.get("issue", ""),
        content.get("summary", ""),
        proposal.get("_summary", ""),
    ]
    for c in candidates:
        fp = c.lower().strip()[:100]
        if fp and fp in rejection_cache:
            return True
    return False


# --- Correction Clustering ---

def normalize_text(text):
    """Normalize correction text for comparison."""
    text = text.lower().strip()
    text = re.sub(r'[^\w\s]', ' ', text)
    text = re.sub(r'\s+', ' ', text)
    return text


def word_set(text):
    """Extract meaningful words from text."""
    stop_words = {
        "the", "a", "an", "is", "are", "was", "were", "be", "been", "being",
        "have", "has", "had", "do", "does", "did", "will", "would", "could",
        "should", "may", "might", "shall", "can", "that", "this", "with",
        "from", "for", "not", "but", "and", "or", "if", "then", "than",
        "them", "they", "their", "there", "what", "when", "where", "which",
        "who", "how", "about", "into", "just", "also", "more", "some",
        "your", "you", "its", "it", "to", "of", "in", "on", "at", "by",
    }
    words = set(normalize_text(text).split())
    return {w for w in words if len(w) > 2 and w not in stop_words}


def similarity(text1, text2):
    """Jaccard similarity between two texts."""
    words1 = word_set(text1)
    words2 = word_set(text2)
    if not words1 or not words2:
        return 0.0
    intersection = words1 & words2
    union = words1 | words2
    return len(intersection) / len(union)


def cluster_corrections(observations):
    """Group similar corrections across sessions into clusters.

    Returns clusters sorted by size (largest first), each with:
    - representative text
    - count of similar corrections
    - session IDs where they occurred
    """
    corrections = []
    for obs in observations:
        for c in obs.get("corrections", []):
            corrections.append({
                "text": c,
                "session_id": obs.get("session_id", "unknown"),
                "score": obs.get("score", 0),
            })

    if not corrections:
        return []

    # Simple greedy clustering: assign each correction to first matching cluster
    clusters = []
    SIMILARITY_THRESHOLD = 0.35

    for c in corrections:
        matched = False
        for cluster in clusters:
            if similarity(c["text"], cluster["representative"]) >= SIMILARITY_THRESHOLD:
                cluster["items"].append(c)
                cluster["sessions"].add(c["session_id"])
                matched = True
                break
        if not matched:
            clusters.append({
                "representative": c["text"],
                "items": [c],
                "sessions": {c["session_id"]},
            })

    # Sort by cluster size, filter singletons
    clusters = [c for c in clusters if len(c["items"]) >= 2]
    clusters.sort(key=lambda c: len(c["items"]), reverse=True)

    return clusters


# --- Error Message Analysis ---

def strip_exit_prefix(msg):
    """Remove 'Exit code N\\n' prefix from Bash errors."""
    return re.sub(r'^Exit code \d+\n?', '', msg, count=1).strip()


def analyze_error_messages(observations):
    """Analyze actual error messages to find specific, actionable patterns.

    Instead of "Edit fails 36% of the time", produces:
    "Edit fails with 'old_string not found' in 80% of Edit errors -- read file first"
    """
    # Collect error messages by tool
    tool_errors = defaultdict(list)
    for obs in observations:
        for tool, messages in obs.get("error_messages", {}).items():
            tool_errors[tool].extend(messages)

    findings = []

    for tool, messages in tool_errors.items():
        if len(messages) < 3:
            continue

        # Categorize error messages by pattern
        categories = categorize_errors(tool, messages)

        for category, info in categories.items():
            if info["count"] >= 3:
                pct = info["count"] / len(messages) * 100
                findings.append({
                    "tool": tool,
                    "category": category,
                    "description": info["description"],
                    "fix": info["fix"],
                    "count": info["count"],
                    "total_errors": len(messages),
                    "percentage": round(pct, 0),
                    "sample": info["samples"][:2],
                })

    findings.sort(key=lambda f: f["count"], reverse=True)
    return findings


def categorize_errors(tool, messages):
    """Categorize error messages for a specific tool into actionable buckets.

    Bash errors typically start with 'Exit code N\\n' followed by the actual message.
    We strip the exit code prefix to match against the real content.
    """
    categories = defaultdict(lambda: {"count": 0, "description": "", "fix": "", "samples": []})

    patterns = {
        "Edit": [
            (r"old_string.*not (found|unique)", "non-unique-match",
             "Edit old_string not unique or not found in file",
             "Include more surrounding context in old_string to guarantee uniqueness, or read the file first"),
            (r"must read.*before editing|file.*not.*read", "file-not-read",
             "Edit attempted before reading the file",
             "Always Read a file before attempting to Edit it"),
            (r"new_string.*same.*old_string|must be different", "no-op-edit",
             "Edit where new_string equals old_string",
             "Verify the replacement actually changes something before calling Edit"),
        ],
        "Bash": [
            (r"command not found|no such file or directory:.*bin/", "command-not-found",
             "Shell command not found or binary missing",
             "Verify command exists before running; use 'which' or check PATH"),
            (r"permission denied", "permission-denied",
             "Permission denied running command",
             "Check file permissions; may need sudo or chmod"),
            (r"can.t open file|no such file or directory(?!:.*bin/)", "file-not-found",
             "Script or file path doesn't exist",
             "Verify path exists with ls or Glob before running commands"),
            (r"timeout|timed out", "timeout",
             "Command timed out",
             "Use --timeout flag or run_in_background for long-running commands"),
            (r"CONFLICT.*Merge conflict|merge failed", "merge-conflict",
             "Git merge conflict encountered",
             "Check for conflicts before merging; resolve or abort"),
            (r"login failed|could not (find|resolve) (user|identity)|sqlcmd.*error.*login", "auth-failure",
             "Authentication or identity resolution failed",
             "Verify credentials and identity; check for current tokens"),
            (r"already (used|exists|checked out)", "resource-conflict",
             "Resource already in use (branch, worktree, port)",
             "Check for existing resources before creating; clean up stale ones"),
            (r"transition.*not found|not recognized|invalid jmespath|--resource and --api-version", "cli-misuse",
             "CLI argument or option incorrect",
             "Check CLI help for correct syntax; verify available options"),
            (r"permission.*denied.*don.t ask|has been denied because", "permission-blocked",
             "Tool blocked by permission mode",
             "Expected in restricted modes; not a real error"),
            (r"cancelled.*parallel tool call|tool_use_error.*cancelled", "cancelled-parallel",
             "Parallel tool call cancelled due to sibling error",
             "Expected behavior when a parallel call fails; not independently actionable"),
            (r"traceback \(most recent call|file.*line \d+.*in ", "python-traceback",
             "Python script raised an exception",
             "Check script inputs and dependencies; verify script path exists"),
            (r"sibling tool call errored|tool_use_error.*sibling", "cancelled-parallel",
             "Cancelled due to parallel sibling failure",
             "Expected behavior; not independently actionable"),
            (r"local changes.*would be overwritten|please commit.*stash", "uncommitted-changes",
             "Git operation blocked by uncommitted changes",
             "Stash or commit changes before checkout/pull/rebase"),
            (r"user doesn.t want to proceed|rejected", "user-rejected",
             "User rejected the tool call",
             "Not an error; user chose not to proceed"),
        ],
        "Read": [
            (r"file does not exist|not found", "file-not-found",
             "Attempted to read a non-existent file",
             "Use Glob to find files before reading; verify path spelling"),
            (r"exceeds maximum allowed tokens|too large", "file-too-large",
             "File too large to read in one call",
             "Use offset/limit params to read in chunks, or target specific sections"),
            (r"illegal operation on a directory|eisdir", "read-directory",
             "Attempted to Read a directory instead of a file",
             "Use Bash ls or Glob to list directory contents, not Read"),
            (r"denied by your permission|denied because", "permission-blocked",
             "Read blocked by permission settings",
             "Expected in restricted modes; not a real error"),
            (r"sibling tool call errored|cancelled.*parallel", "cancelled-parallel",
             "Cancelled due to parallel sibling failure",
             "Expected behavior; not independently actionable"),
        ],
        "Grep": [
            (r"path does not exist", "path-not-found",
             "Grep target path doesn't exist",
             "Verify the directory/file path exists before grepping"),
            (r"denied because|permission.*denied", "permission-blocked",
             "Grep blocked by permission settings",
             "Expected in restricted modes; not a real error"),
            (r"sibling tool call errored|cancelled.*parallel", "cancelled-parallel",
             "Cancelled due to parallel sibling failure",
             "Expected behavior; not independently actionable"),
        ],
        "Glob": [
            (r"timed out|timeout", "search-timeout",
             "Glob/ripgrep search timed out",
             "Use narrower glob patterns or search in more specific directories"),
            (r"directory does not exist", "dir-not-found",
             "Glob target directory doesn't exist",
             "Verify the directory path exists before globbing"),
            (r"denied because|permission.*denied", "permission-blocked",
             "Glob blocked by permission settings",
             "Expected in restricted modes; not a real error"),
            (r"sibling tool call errored|cancelled.*parallel", "cancelled-parallel",
             "Cancelled due to parallel sibling failure",
             "Expected behavior; not independently actionable"),
            (r"user doesn.t want to proceed|rejected", "user-rejected",
             "User rejected the tool call",
             "Not an error; user chose not to proceed"),
        ],
    }

    tool_patterns = patterns.get(tool, [])

    for msg in messages:
        # Strip exit code prefix for better pattern matching
        clean_msg = strip_exit_prefix(msg) if tool == "Bash" else msg
        msg_lower = clean_msg.lower()

        # Skip empty/minimal errors (bare "Exit code 1" with no message)
        if not msg_lower or len(msg_lower) < 3:
            continue

        matched = False
        for pattern, cat_name, desc, fix in tool_patterns:
            if re.search(pattern, msg_lower):
                cat = categories[cat_name]
                cat["count"] += 1
                cat["description"] = desc
                cat["fix"] = fix
                cat["samples"].append(clean_msg[:150])
                matched = True
                break
        if not matched:
            cat = categories["other"]
            cat["count"] += 1
            cat["description"] = f"Uncategorized {tool} errors"
            cat["fix"] = f"Review {tool} error patterns for new categories"
            cat["samples"].append(clean_msg[:150])

    return dict(categories)


# --- Proposal Generation ---

def generate_correction_proposals(clusters):
    """Generate proposals from correction clusters."""
    proposals = []
    for cluster in clusters[:5]:  # Top 5 clusters
        count = len(cluster["items"])
        sessions = len(cluster["sessions"])
        representative = cluster["representative"]

        # Extract the core instruction from the correction
        summary = representative[:200]

        proposals.append({
            "type": "feedback_memory",
            "status": "pending_review",
            "_summary": f"correction cluster ({count}x): {summary[:60]}",
            "content": {
                "name": f"feedback-{datetime.now().strftime('%Y%m%d-%H%M%S')}-cluster",
                "description": f"Recurring correction ({count}x across {sessions} sessions): {summary[:80]}",
                "memory_type": "feedback",
                "body": summary,
                "evidence": f"{count} occurrences across {sessions} sessions",
                "sample_sessions": list(cluster["sessions"])[:3],
            },
            "source": "auto-reflect",
            "created": datetime.now().isoformat(),
        })

    return proposals


# Error categories that are expected/non-actionable -- don't propose fixes
NON_ACTIONABLE_CATEGORIES = {
    "permission-blocked", "cancelled-parallel", "user-rejected",
    "nonzero-exit",  # Too generic
}


def generate_error_proposals(error_findings):
    """Generate proposals from error message analysis."""
    proposals = []
    for finding in error_findings[:5]:  # Top 5 error patterns
        if finding["percentage"] < 10:
            continue  # Skip rare error categories
        if finding["category"] in NON_ACTIONABLE_CATEGORIES:
            continue  # Skip expected/non-actionable errors
        if finding["category"] == "other":
            continue  # Skip uncategorized -- need more specific patterns first

        proposals.append({
            "type": "feedback_memory",
            "status": "pending_review",
            "_summary": f"{finding['tool']} {finding['category']}: {finding['description'][:50]}",
            "content": {
                "name": f"feedback-{finding['tool'].lower()}-{finding['category']}",
                "description": f"{finding['tool']} error pattern: {finding['description']}",
                "memory_type": "feedback",
                "body": finding["fix"],
                "evidence": f"{finding['count']}/{finding['total_errors']} {finding['tool']} errors ({finding['percentage']:.0f}%)",
                "samples": finding["sample"],
            },
            "source": "auto-reflect",
            "created": datetime.now().isoformat(),
        })

    return proposals


def generate_pattern_proposals(patterns):
    """Generate proposals from detected patterns -- only for score declines and corrections."""
    proposals = []
    for p in patterns:
        # Only generate proposals for patterns that are genuinely actionable
        # Skip: frequent_tool_errors, frequent_retries, low_skill_usage
        # (these are now handled by error message analysis and correction clustering)

        if p["type"] == "score_decline":
            proposals.append({
                "type": "investigation",
                "status": "pending_review",
                "_summary": f"score decline: {p.get('recent_avg', 0)} vs {p.get('earlier_avg', 0)}",
                "content": {
                    "target": "Session quality trend",
                    "issue": f"Score declining: recent avg {p.get('recent_avg', 0)} vs earlier {p.get('earlier_avg', 0)} (delta: {p.get('delta', 0)})",
                    "suggestion": "Review recent low-scoring sessions for systemic issues. Run deep_analyze on sessions scoring <80.",
                    "priority": "high",
                },
                "source": "auto-reflect",
                "created": datetime.now().isoformat(),
            })

        elif p["type"] == "recurring_corrections" and p.get("sample_corrections"):
            # This is now mostly handled by correction clustering,
            # but include a summary if themes are interesting
            themes = p.get("top_themes", [])
            if themes:
                proposals.append({
                    "type": "investigation",
                    "status": "pending_review",
                    "_summary": f"correction themes: {', '.join(themes[:3])}",
                    "content": {
                        "target": "Recurring correction themes",
                        "issue": f"Corrections cluster around themes: {', '.join(themes)}",
                        "suggestion": "Review correction clusters in detail. Consider adding feedback memories for the top themes.",
                        "priority": "medium",
                        "samples": p.get("sample_corrections", [])[:3],
                    },
                    "source": "auto-reflect",
                    "created": datetime.now().isoformat(),
                })

    return proposals


def generate_agent_proposals(observations):
    """Generate proposals for agent definition improvements.

    Analyzes Agent tool usage across sessions to find:
    - Agents with high error rates (bad instructions or misconfigured)
    - Available agents that are never used (dead weight or poor discoverability)
    - Agents that consistently trigger corrections after use (bad output quality)
    """
    agents_dir = os.path.join(CLAUDE_DIR, "agents")

    # Collect agent usage stats
    agent_stats = defaultdict(lambda: {"total": 0, "errors": 0, "sessions": set(),
                                        "error_messages": [], "descriptions": []})

    for obs in observations:
        for agent in obs.get("agents_used", []):
            agent_type = agent.get("subagent_type", "general-purpose")
            stats = agent_stats[agent_type]
            stats["total"] += 1
            stats["sessions"].add(obs.get("session_id", ""))
            stats["descriptions"].append(agent.get("description", ""))
            if agent.get("is_error"):
                stats["errors"] += 1
                if agent.get("error_message"):
                    stats["error_messages"].append(agent["error_message"][:150])

    proposals = []

    # 1. Agents with high error rates (>30%, min 3 uses)
    for agent_type, stats in agent_stats.items():
        if stats["total"] < 3:
            continue
        error_rate = stats["errors"] / stats["total"]
        if error_rate > 0.30:
            samples = stats["error_messages"][:3]
            proposals.append({
                "type": "agent_patch",
                "status": "pending_review",
                "_summary": f"agent {agent_type}: {error_rate:.0%} error rate ({stats['errors']}/{stats['total']})",
                "content": {
                    "target": f"agents/{agent_type}.md",
                    "description": f"Agent '{agent_type}' has a {error_rate:.0%} error rate ({stats['errors']}/{stats['total']} calls across {len(stats['sessions'])} sessions)",
                    "issue": f"High failure rate suggests the agent definition needs better instructions, tool access, or scoping",
                    "evidence": f"{stats['errors']} errors in {stats['total']} calls. Sample errors: {'; '.join(samples[:2])}",
                    "priority": "high" if error_rate > 0.5 else "medium",
                },
                "source": "auto-reflect",
                "created": datetime.now().isoformat(),
            })

    # 2. Detect available agents that are never used
    if os.path.isdir(agents_dir):
        available = set()
        for f in os.listdir(agents_dir):
            if f.endswith(".md"):
                available.add(f.replace(".md", ""))
        used = set(agent_stats.keys())
        # Only flag after enough data (50+ sessions with agent tracking)
        sessions_with_agent_data = sum(
            1 for obs in observations if "agents_used" in obs
        )
        if sessions_with_agent_data >= 50:
            never_used = available - used
            for agent_name in sorted(never_used):
                proposals.append({
                    "type": "agent_patch",
                    "status": "pending_review",
                    "_summary": f"agent {agent_name}: never used in {sessions_with_agent_data} sessions",
                    "content": {
                        "target": f"agents/{agent_name}.md",
                        "description": f"Agent '{agent_name}' has never been used across {sessions_with_agent_data} tracked sessions",
                        "issue": "Agent may have poor discoverability, unclear trigger conditions, or be obsolete",
                        "suggestion": "Review if this agent is still needed. If yes, improve its description. If no, remove it.",
                        "priority": "low",
                    },
                    "source": "auto-reflect",
                    "created": datetime.now().isoformat(),
                })

    return proposals


def generate_claude_md_proposals(observations, correction_clusters):
    """Generate proposals for CLAUDE.md improvements.

    Promotes high-frequency correction clusters to CLAUDE.md Corrections rules
    when they're persistent enough to warrant a permanent instruction.
    Also detects existing CLAUDE.md rules that aren't being followed.

    Threshold: 3+ sessions with the same correction pattern -> propose CLAUDE.md rule.
    (Feedback memories are for 2+ sessions; CLAUDE.md is the stronger, permanent fix.)
    """
    claude_md = os.path.join(CLAUDE_DIR, "CLAUDE.md")

    # Load existing CLAUDE.md corrections to avoid duplicating
    existing_rules = set()
    if os.path.exists(claude_md):
        try:
            with open(claude_md) as f:
                content = f.read().lower()
                # Extract bullet points from Corrections section
                in_corrections = False
                for line in content.split("\n"):
                    if "## corrections" in line:
                        in_corrections = True
                        continue
                    if in_corrections and line.startswith("##"):
                        break
                    if in_corrections and line.strip().startswith("- "):
                        # Store first 80 chars as fingerprint
                        existing_rules.add(line.strip()[:80])
        except IOError:
            pass

    proposals = []

    # 1. Promote high-frequency correction clusters to CLAUDE.md rules
    for cluster in correction_clusters:
        session_count = len(cluster["sessions"])
        item_count = len(cluster["items"])

        # Must appear in 3+ sessions to warrant a permanent rule
        if session_count < 3:
            continue

        representative = cluster["representative"]

        # Check if this correction is already covered by an existing CLAUDE.md rule
        rep_lower = representative.lower()
        already_covered = any(
            similarity_check(rep_lower, rule)
            for rule in existing_rules
        )
        if already_covered:
            continue

        # Convert the correction into a "Don't X, instead Y" format suggestion
        proposals.append({
            "type": "claude_md_patch",
            "status": "pending_review",
            "_summary": f"CLAUDE.md rule: {representative[:60]}",
            "content": {
                "target": "CLAUDE.md",
                "section": "Corrections",
                "description": f"Recurring correction ({item_count}x across {session_count} sessions) should become a permanent CLAUDE.md rule",
                "correction_text": representative[:300],
                "suggested_rule": f"- Don't ... (derived from: \"{representative[:150]}\")",
                "evidence": f"{item_count} occurrences across {session_count} sessions",
                "priority": "high" if session_count >= 5 else "medium",
            },
            "source": "auto-reflect",
            "created": datetime.now().isoformat(),
        })

    # 2. Detect corrections that match existing CLAUDE.md rules (rules not working)
    rule_violations = defaultdict(int)
    for obs in observations[-100:]:  # Check last 100 sessions
        for correction_text in obs.get("corrections", []):
            corr_lower = correction_text.lower()
            for rule in existing_rules:
                if similarity_check(corr_lower, rule):
                    rule_violations[rule] += 1

    for rule, count in rule_violations.items():
        if count >= 3:
            proposals.append({
                "type": "claude_md_patch",
                "status": "pending_review",
                "_summary": f"CLAUDE.md rule not followed: {rule[:60]}",
                "content": {
                    "target": "CLAUDE.md",
                    "section": "Corrections",
                    "description": f"Existing CLAUDE.md rule is being violated repeatedly ({count}x in last 100 sessions)",
                    "rule": rule,
                    "issue": "Rule exists but Claude keeps breaking it. Consider rewording for clarity, adding an example, or making it more prominent.",
                    "evidence": f"{count} violations in last 100 sessions",
                    "priority": "high",
                },
                "source": "auto-reflect",
                "created": datetime.now().isoformat(),
            })

    return proposals


def similarity_check(text1, text2):
    """Quick word overlap check for matching corrections to rules."""
    words1 = word_set(text1)
    words2 = word_set(text2)
    if not words1 or not words2:
        return False
    overlap = len(words1 & words2) / min(len(words1), len(words2))
    return overlap >= 0.4


# --- Effectiveness Tracking ---

EFFECTIVENESS_IMPROVEMENT_THRESHOLD = 0.15  # 15% improvement to count as effective
EFFECTIVENESS_REGRESSION_THRESHOLD = -0.10  # 10% worse = regression


def check_effectiveness(observations):
    """Check approved proposals that have passed their review window.

    Re-measures the baseline metric and generates revert proposals for
    ineffective or regressed changes. Updates effectiveness_status in history.

    Returns a list of revert proposals.
    """
    if not os.path.exists(PROPOSAL_HISTORY):
        return []

    try:
        with open(PROPOSAL_HISTORY) as f:
            history = json.load(f)
    except (json.JSONDecodeError, IOError):
        return []

    current_obs_count = len(observations)
    revert_proposals = []
    history_modified = False

    for entry in history:
        if entry.get("action") != "approved":
            continue
        if entry.get("effectiveness_status") != "pending":
            continue
        if "baseline" not in entry or "review_window" not in entry:
            continue

        window = entry["review_window"]
        sessions_needed = window.get("sessions_at_approval", 0) + window.get("value", 30)
        if current_obs_count < sessions_needed:
            continue

        baseline = entry["baseline"]
        post_approval_obs = observations[window.get("sessions_at_approval", 0):]
        obs_window = baseline.get("observation_window", 50)
        recent_obs = post_approval_obs[-obs_window:] if len(post_approval_obs) >= obs_window else post_approval_obs

        current_value = remeasure_metric(baseline, recent_obs)
        if current_value is None:
            continue

        baseline_value = baseline["metric_value"]
        lower_is_better = baseline.get("lower_is_better", True)

        if baseline_value == 0:
            if current_value == 0:
                delta_pct = 0.0
            else:
                delta_pct = -1.0 if lower_is_better else 1.0
        else:
            if lower_is_better:
                delta_pct = (baseline_value - current_value) / baseline_value
            else:
                delta_pct = (current_value - baseline_value) / baseline_value

        if delta_pct >= EFFECTIVENESS_IMPROVEMENT_THRESHOLD:
            entry["effectiveness_status"] = "effective"
            entry["effectiveness_checked"] = datetime.now().isoformat()
            entry["effectiveness_current_value"] = current_value
            entry["effectiveness_delta_pct"] = round(delta_pct * 100, 1)
            history_modified = True
        elif delta_pct <= EFFECTIVENESS_REGRESSION_THRESHOLD:
            entry["effectiveness_status"] = "regressed"
            entry["effectiveness_checked"] = datetime.now().isoformat()
            entry["effectiveness_current_value"] = current_value
            entry["effectiveness_delta_pct"] = round(delta_pct * 100, 1)
            history_modified = True
            revert_proposals.append(_make_revert_proposal(entry, current_value, delta_pct, "regressed"))
        else:
            entry["effectiveness_status"] = "ineffective"
            entry["effectiveness_checked"] = datetime.now().isoformat()
            entry["effectiveness_current_value"] = current_value
            entry["effectiveness_delta_pct"] = round(delta_pct * 100, 1)
            history_modified = True
            revert_proposals.append(_make_revert_proposal(entry, current_value, delta_pct, "ineffective"))

    if history_modified:
        try:
            with open(PROPOSAL_HISTORY, "w") as f:
                json.dump(history, f, indent=2, default=str)
        except IOError:
            pass

    return revert_proposals


def remeasure_metric(baseline, observations):
    """Re-measure a metric using the same parameters as the baseline."""
    metric_type = baseline.get("metric_type", "")
    params = baseline.get("metric_params", {})

    if metric_type == "error_category_count":
        tool = params.get("tool", "")
        count = 0
        for obs in observations:
            for t, msgs in obs.get("error_messages", {}).items():
                if t.lower() == tool.lower():
                    count += len(msgs)
        return count

    elif metric_type == "correction_cluster_count":
        fingerprint = params.get("fingerprint", "")
        if not fingerprint:
            return None
        count = 0
        for obs in observations:
            for corr in obs.get("corrections", []):
                if _jaccard(fingerprint.lower(), corr.lower()) >= 0.35:
                    count += 1
        return count

    elif metric_type == "correction_match_count":
        match_text = params.get("match_text", "")
        if not match_text:
            return None
        count = 0
        for obs in observations:
            for corr in obs.get("corrections", []):
                if _jaccard(match_text.lower(), corr.lower()) >= 0.4:
                    count += 1
        return count

    elif metric_type == "agent_error_rate":
        agent_type = params.get("agent_type", "")
        total = 0
        errors = 0
        for obs in observations:
            for agent in obs.get("agents_used", []):
                if agent.get("subagent_type") == agent_type:
                    total += 1
                    if agent.get("is_error"):
                        errors += 1
        return round(errors / max(total, 1), 3)

    elif metric_type == "tool_error_rate":
        tool = params.get("tool", "")
        total = 0
        errors = 0
        for obs in observations:
            total += obs.get("tool_distribution", {}).get(tool, 0)
            errors += obs.get("error_distribution", {}).get(tool, 0)
        return round(errors / max(total, 1), 3)

    elif metric_type == "avg_score":
        scores = [obs.get("score", 0) for obs in observations if obs.get("score") is not None]
        if scores:
            return round(sum(scores) / len(scores), 1)
        return None

    return None


def _jaccard(text1, text2):
    """Quick Jaccard similarity for text comparison."""
    stop = {"the", "a", "an", "is", "are", "was", "were", "be", "been",
            "have", "has", "had", "do", "does", "did", "will", "would",
            "could", "should", "that", "this", "with", "from", "for",
            "not", "but", "and", "or", "to", "of", "in", "on", "at", "by"}
    w1 = {w for w in text1.split() if len(w) > 2 and w not in stop}
    w2 = {w for w in text2.split() if len(w) > 2 and w not in stop}
    if not w1 or not w2:
        return 0.0
    return len(w1 & w2) / len(w1 | w2)


def _make_revert_proposal(history_entry, current_value, delta_pct, reason):
    """Create a revert proposal from an ineffective/regressed history entry."""
    baseline = history_entry.get("baseline", {})
    return {
        "type": "revert",
        "status": "pending_review",
        "_summary": f"revert: {history_entry.get('summary', '')[:50]} ({reason})",
        "content": {
            "original_proposal_date": history_entry.get("date", ""),
            "original_type": history_entry.get("type", ""),
            "original_summary": history_entry.get("summary", ""),
            "artifact_path": history_entry.get("artifact_path", ""),
            "baseline_value": baseline.get("metric_value"),
            "current_value": current_value,
            "delta_pct": round(delta_pct * 100, 1),
            "metric_type": baseline.get("metric_type", ""),
            "reason": reason,
            "action": f"Remove or revert: {history_entry.get('artifact_path', 'unknown artifact')}",
            "priority": "high" if reason == "regressed" else "medium",
        },
        "source": "auto-reflect-effectiveness",
        "created": datetime.now().isoformat(),
    }


def deduplicate_proposals(new_proposals, existing_proposals):
    """Remove proposals that are too similar to existing ones."""
    existing_fingerprints = set()
    for p in existing_proposals:
        content = p.get("content", {})
        fp = (
            content.get("body", "")
            or content.get("issue", "")
            or content.get("query", "")
            or content.get("target", "")
        ).lower().strip()[:100]
        if fp:
            existing_fingerprints.add(fp)

    unique = []
    for p in new_proposals:
        content = p.get("content", {})
        fp = (
            content.get("body", "")
            or content.get("issue", "")
            or content.get("query", "")
            or content.get("target", "")
        ).lower().strip()[:100]
        if fp and fp not in existing_fingerprints:
            unique.append(p)
            existing_fingerprints.add(fp)
    return unique


def filter_rejected(proposals, rejection_cache):
    """Remove proposals matching previously rejected fingerprints."""
    if not rejection_cache:
        return proposals
    return [p for p in proposals if not is_rejected(p, rejection_cache)]


def save_proposals(proposals):
    """Save new proposals to improvements directory."""
    if not proposals:
        return None
    ensure_dirs()
    timestamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    filepath = os.path.join(IMPROVEMENTS_DIR, f"{timestamp}_concrete_proposals.json")
    with open(filepath, "w") as f:
        json.dump(proposals, f, indent=2, default=str)
    return filepath


def format_markdown(proposals):
    """Format proposals as actionable markdown."""
    lines = []
    lines.append(f"## Concrete Improvement Proposals ({len(proposals)} new)")
    lines.append("")

    by_type = {}
    for p in proposals:
        ptype = p["type"]
        by_type.setdefault(ptype, []).append(p)

    for ptype, items in by_type.items():
        lines.append(f"### {ptype.replace('_', ' ').title()} ({len(items)})")
        lines.append("")

        for i, p in enumerate(items, 1):
            content = p["content"]
            priority = content.get("priority", "medium")
            summary = p.get("_summary", "")

            if ptype == "feedback_memory":
                lines.append(f"**{i}. [{priority.upper()}]** {content.get('description', '')}")
                lines.append(f"   Fix: {content.get('body', '')[:200]}")
                lines.append(f"   Evidence: {content.get('evidence', '')}")
            elif ptype == "investigation":
                lines.append(f"**{i}. [{priority.upper()}]** {content.get('target', '')}")
                lines.append(f"   Issue: {content.get('issue', '')}")
                lines.append(f"   Action: {content.get('suggestion', '')}")
            elif ptype == "agent_patch":
                lines.append(f"**{i}. [{priority.upper()}]** {content.get('description', '')}")
                lines.append(f"   Target: {content.get('target', '')}")
                lines.append(f"   Issue: {content.get('issue', '')}")
                lines.append(f"   Evidence: {content.get('evidence', '')}")
            elif ptype == "claude_md_patch":
                lines.append(f"**{i}. [{priority.upper()}]** {content.get('description', '')}")
                lines.append(f"   Section: {content.get('section', '')}")
                if content.get('correction_text'):
                    lines.append(f"   Correction: \"{content['correction_text'][:150]}\"")
                if content.get('suggested_rule'):
                    lines.append(f"   Suggested: {content['suggested_rule']}")
                if content.get('rule'):
                    lines.append(f"   Rule: {content['rule']}")
                if content.get('issue'):
                    lines.append(f"   Issue: {content['issue']}")
                lines.append(f"   Evidence: {content.get('evidence', '')}")
            elif ptype == "revert":
                lines.append(f"**{i}. [{priority.upper()}] REVERT** {content.get('original_summary', '')}")
                lines.append(f"   Reason: {content.get('reason', '')} (delta: {content.get('delta_pct', '?')}%)")
                lines.append(f"   Metric: {content.get('metric_type', '')} baseline={content.get('baseline_value')} current={content.get('current_value')}")
                lines.append(f"   Action: {content.get('action', '')}")
            else:
                lines.append(f"**{i}.** {summary or content.get('description', '')}")

            lines.append("")

    return "\n".join(lines)


def main():
    args = sys.argv[1:]
    output_json = "--json" in args

    patterns = load_latest_patterns()
    observations = load_all_observations()
    existing = load_existing_proposals()
    rejection_cache = load_rejection_cache()

    new_proposals = []

    # 1. Cluster corrections and generate proposals
    clusters = cluster_corrections(observations)
    new_proposals.extend(generate_correction_proposals(clusters))

    # 2. Analyze error messages for specific patterns
    error_findings = analyze_error_messages(observations)
    new_proposals.extend(generate_error_proposals(error_findings))

    # 3. Generate proposals from high-level patterns (only actionable ones)
    new_proposals.extend(generate_pattern_proposals(patterns))

    # 4. Generate agent improvement proposals
    new_proposals.extend(generate_agent_proposals(observations))

    # 5. Generate CLAUDE.md improvement proposals
    new_proposals.extend(generate_claude_md_proposals(observations, clusters))

    # 6. Check effectiveness of past approved proposals
    revert_proposals = check_effectiveness(observations)
    new_proposals.extend(revert_proposals)

    # 7. Deduplicate against existing proposals
    new_proposals = deduplicate_proposals(new_proposals, existing)

    # 5. Filter out previously rejected proposals
    before_filter = len(new_proposals)
    new_proposals = filter_rejected(new_proposals, rejection_cache)
    suppressed = before_filter - len(new_proposals)

    if not new_proposals:
        if suppressed:
            msg = f"No new proposals ({suppressed} suppressed by rejection cache)."
        else:
            msg = "No new proposals to generate."
        print(msg, file=sys.stderr)
        if not output_json:
            print(msg)
        return

    filepath = save_proposals(new_proposals)

    if output_json:
        print(json.dumps(new_proposals, indent=2, default=str))
    else:
        print(format_markdown(new_proposals))
        if suppressed:
            print(f"\n({suppressed} proposals suppressed by rejection cache)")

    if filepath:
        print(f"\nProposals saved: {filepath}", file=sys.stderr)


if __name__ == "__main__":
    main()
