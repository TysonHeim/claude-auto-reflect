#!/usr/bin/env python3
"""
Generate concrete improvement proposals from detected patterns and corrections.

Takes pattern analysis + observation data and produces actionable changes:
- Feedback memories (from corrections and recurring friction)
- Skill patches (from tool usage patterns)
- New eval queries (from discovered edge cases)

Usage:
    python3 -m auto_reflect.propose_improvements
    python3 -m auto_reflect.propose_improvements --json
"""

import json
import os
import sys
from datetime import datetime
from pathlib import Path

from auto_reflect.config import OBSERVATIONS_DIR, PATTERNS_DIR, IMPROVEMENTS_DIR, ensure_dirs


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
                proposals.extend(json.load(fh))
        except (json.JSONDecodeError, IOError):
            continue
    return proposals


def extract_corrections(observations):
    """Pull all human corrections from observations."""
    corrections = []
    for obs in observations:
        for c in obs.get("corrections", []):
            corrections.append({
                "text": c,
                "session_id": obs.get("session_id", "unknown"),
                "session_score": obs.get("score", 0),
            })
    return corrections


def generate_feedback_memory(correction_text, context=""):
    """Draft a feedback memory from a correction."""
    return {
        "type": "feedback_memory",
        "status": "pending_review",
        "content": {
            "name": f"feedback-{datetime.now().strftime('%Y%m%d-%H%M%S')}",
            "description": f"Auto-detected correction: {correction_text[:80]}",
            "memory_type": "feedback",
            "body": correction_text,
            "context": context,
        },
        "source": "auto-reflect",
        "created": datetime.now().isoformat(),
    }


def generate_skill_patch(pattern):
    """Draft a skill improvement from a pattern."""
    ptype = pattern.get("type", "")

    if ptype == "frequent_retries":
        tool = pattern.get("tool", "unknown")
        count = pattern.get("retry_count", 0)
        return {
            "type": "skill_patch",
            "status": "pending_review",
            "content": {
                "target": f"Tool usage pattern: {tool}",
                "issue": f"{tool} required {count} retries across sessions",
                "suggestion": f"Add pre-validation or fallback for {tool} calls that commonly fail",
                "priority": "medium",
            },
            "source": "auto-reflect",
            "created": datetime.now().isoformat(),
        }

    elif ptype == "frequent_tool_errors":
        tool = pattern.get("tool", "unknown")
        rate = pattern.get("error_rate", 0)
        return {
            "type": "skill_patch",
            "status": "pending_review",
            "content": {
                "target": f"Tool error pattern: {tool}",
                "issue": f"{tool} errors in {rate*100:.0f}% of sessions",
                "suggestion": f"Investigate common {tool} error causes and add defensive patterns",
                "priority": "high" if rate > 0.5 else "medium",
            },
            "source": "auto-reflect",
            "created": datetime.now().isoformat(),
        }

    elif ptype == "low_skill_usage":
        return {
            "type": "skill_patch",
            "status": "pending_review",
            "content": {
                "target": "Skill trigger descriptions",
                "issue": f"Skills used in only {pattern.get('sessions_with_skills', 0)} of {pattern.get('sessions_with_skills', 0) + pattern.get('sessions_without_skills', 0)} sessions",
                "suggestion": "Review and improve skill trigger descriptions for better activation",
                "priority": "medium",
            },
            "source": "auto-reflect",
            "created": datetime.now().isoformat(),
        }

    elif ptype == "score_decline":
        return {
            "type": "investigation",
            "status": "pending_review",
            "content": {
                "target": "Session quality trend",
                "issue": f"Score declining: {pattern.get('recent_avg', 0)} vs {pattern.get('earlier_avg', 0)}",
                "suggestion": "Review recent sessions for systemic quality issues",
                "priority": "high",
            },
            "source": "auto-reflect",
            "created": datetime.now().isoformat(),
        }

    return None


def generate_eval_query(correction_text, session_context=""):
    """Draft an eval query from a correction to prevent regression."""
    return {
        "type": "eval_query",
        "status": "pending_review",
        "content": {
            "query": correction_text[:200],
            "expected_behavior": "Should not repeat the corrected behavior",
            "source_session": session_context,
        },
        "source": "auto-reflect",
        "created": datetime.now().isoformat(),
    }


def deduplicate_proposals(new_proposals, existing_proposals):
    """Remove proposals that are too similar to existing ones.

    Deduplicates by content fingerprint (body text or issue description),
    not by auto-generated names which are always unique.
    """
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
            status = p.get("status", "pending")
            priority = content.get("priority", "medium")

            if ptype == "feedback_memory":
                lines.append(f"**{i}. [{priority.upper()}]** {content.get('description', '')}")
                lines.append(f"   Content: {content.get('body', '')[:200]}")
                lines.append(f"   Status: {status}")
            elif ptype == "skill_patch":
                lines.append(f"**{i}. [{priority.upper()}]** {content.get('target', '')}")
                lines.append(f"   Issue: {content.get('issue', '')}")
                lines.append(f"   Suggestion: {content.get('suggestion', '')}")
                lines.append(f"   Status: {status}")
            elif ptype == "eval_query":
                lines.append(f"**{i}.** Query: {content.get('query', '')[:150]}")
                lines.append(f"   Expected: {content.get('expected_behavior', '')}")
                lines.append(f"   Status: {status}")
            elif ptype == "investigation":
                lines.append(f"**{i}. [{priority.upper()}]** {content.get('target', '')}")
                lines.append(f"   Issue: {content.get('issue', '')}")
                lines.append(f"   Action: {content.get('suggestion', '')}")

            lines.append("")

    return "\n".join(lines)


def main():
    args = sys.argv[1:]
    output_json = "--json" in args

    patterns = load_latest_patterns()
    observations = load_all_observations()
    existing = load_existing_proposals()
    corrections = extract_corrections(observations)

    new_proposals = []

    for c in corrections:
        proposal = generate_feedback_memory(c["text"], c.get("session_id", ""))
        if proposal:
            new_proposals.append(proposal)
        eval_q = generate_eval_query(c["text"], c.get("session_id", ""))
        if eval_q:
            new_proposals.append(eval_q)

    for p in patterns:
        proposal = generate_skill_patch(p)
        if proposal:
            new_proposals.append(proposal)

    new_proposals = deduplicate_proposals(new_proposals, existing)

    if not new_proposals:
        print("No new proposals to generate.", file=sys.stderr)
        if not output_json:
            print("All patterns already have proposals. Accumulate more observations.")
        return

    filepath = save_proposals(new_proposals)

    if output_json:
        print(json.dumps(new_proposals, indent=2, default=str))
    else:
        print(format_markdown(new_proposals))

    if filepath:
        print(f"\nProposals saved: {filepath}", file=sys.stderr)


if __name__ == "__main__":
    main()
