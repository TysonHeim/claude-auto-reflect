#!/usr/bin/env python3
"""
Generate concrete improvement proposals from detected patterns and corrections.

Takes pattern analysis + observation data and produces actionable changes:
- Feedback memories (from clustered corrections)
- Skill patches (from specific error analysis)
- Investigations (from score declines)

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

    # 4. Deduplicate against existing proposals
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
