#!/usr/bin/env python3
"""
Scan observations directory for recurring patterns across sessions.

Usage:
    python3 -m auto_reflect.detect_patterns
    python3 -m auto_reflect.detect_patterns --json
"""

import json
import os
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

from auto_reflect.config import OBSERVATIONS_DIR, PATTERNS_DIR, ensure_dirs


def load_observations():
    """Load all observation JSON files."""
    observations = []
    if not os.path.isdir(OBSERVATIONS_DIR):
        return observations
    for f in sorted(Path(OBSERVATIONS_DIR).glob("*.json")):
        try:
            with open(f) as fh:
                obs = json.load(fh)
                obs["_file"] = str(f)
                observations.append(obs)
        except (json.JSONDecodeError, IOError):
            continue
    return observations


def detect_error_patterns(observations):
    """Find tools that consistently produce errors across sessions."""
    tool_errors = defaultdict(lambda: {"error_sessions": 0, "total_sessions": 0, "total_errors": 0})

    for obs in observations:
        error_dist = obs.get("error_distribution", {})
        tool_dist = obs.get("tool_distribution", {})

        for tool in set(list(error_dist.keys()) + list(tool_dist.keys())):
            tool_errors[tool]["total_sessions"] += 1
            if tool in error_dist:
                tool_errors[tool]["error_sessions"] += 1
                tool_errors[tool]["total_errors"] += error_dist[tool]

    exploratory_tools = {"Bash", "Read", "Glob", "Grep"}
    MIN_SESSIONS = max(5, len(observations) * 0.01)
    MIN_ERRORS = 5

    # Aggregate MCP tools by server prefix with per-observation set tracking
    mcp_server_obs = defaultdict(lambda: {"error_obs": set(), "total_obs": set(), "total_errors": 0, "tools": set()})
    standalone = {}

    for obs_idx, obs in enumerate(observations):
        error_dist = obs.get("error_distribution", {})
        tool_dist = obs.get("tool_distribution", {})
        for tool in set(list(error_dist.keys()) + list(tool_dist.keys())):
            if tool.startswith("mcp__"):
                parts = tool.split("__")
                server = parts[1] if len(parts) >= 3 else tool
                grp = mcp_server_obs[server]
                grp["total_obs"].add(obs_idx)
                if tool in error_dist:
                    grp["error_obs"].add(obs_idx)
                    grp["total_errors"] += error_dist[tool]
                grp["tools"].add(parts[-1] if len(parts) >= 3 else tool)
            else:
                if tool not in standalone:
                    standalone[tool] = tool_errors[tool]

    mcp_groups = {}
    for server, grp in mcp_server_obs.items():
        mcp_groups[server] = {
            "error_sessions": len(grp["error_obs"]),
            "total_sessions": len(grp["total_obs"]),
            "total_errors": grp["total_errors"],
            "tools": list(grp["tools"]),
        }

    patterns = []

    for tool, stats in standalone.items():
        threshold = 0.5 if tool in exploratory_tools else 0.3
        if (stats["total_sessions"] >= MIN_SESSIONS
                and stats["total_errors"] >= MIN_ERRORS
                and stats["error_sessions"] / stats["total_sessions"] > threshold):
            patterns.append({
                "type": "frequent_tool_errors",
                "tool": tool,
                "error_rate": round(stats["error_sessions"] / stats["total_sessions"], 2),
                "total_errors": stats["total_errors"],
                "sessions_affected": stats["error_sessions"],
            })

    for server, stats in mcp_groups.items():
        if (stats["total_sessions"] >= MIN_SESSIONS
                and stats["total_errors"] >= MIN_ERRORS
                and stats["error_sessions"] / stats["total_sessions"] > 0.3):
            patterns.append({
                "type": "frequent_tool_errors",
                "tool": f"mcp__{server} ({len(stats['tools'])} methods)",
                "error_rate": round(stats["error_sessions"] / stats["total_sessions"], 2),
                "total_errors": stats["total_errors"],
                "sessions_affected": stats["error_sessions"],
            })
    return patterns


def detect_correction_patterns(observations):
    """Find recurring correction themes across sessions."""
    all_corrections = []
    sessions_with_corrections = 0

    for obs in observations:
        corrections = obs.get("corrections", [])
        if corrections:
            sessions_with_corrections += 1
            all_corrections.extend(corrections)

    if not all_corrections:
        return []

    word_freq = Counter()
    for c in all_corrections:
        words = set(c.lower().split())
        words = {w for w in words if len(w) > 3 and w not in {
            "that", "this", "with", "from", "have", "been", "were",
            "will", "would", "could", "should", "about", "than",
            "them", "they", "their", "there", "what", "when", "where",
        }}
        word_freq.update(words)

    patterns = []
    if sessions_with_corrections >= 2:
        patterns.append({
            "type": "recurring_corrections",
            "correction_rate": round(sessions_with_corrections / len(observations), 2),
            "total_corrections": len(all_corrections),
            "top_themes": [w for w, _ in word_freq.most_common(5)],
            "sample_corrections": all_corrections[:5],
        })
    return patterns


def detect_retry_patterns(observations):
    """Find tools that frequently need retries."""
    retry_tools = Counter()
    sessions_with_retries = 0

    for obs in observations:
        retries = obs.get("retries", [])
        if retries:
            sessions_with_retries += 1
            for r in retries:
                retry_tools[r["tool"]] += 1

    min_retries = max(10, len(observations) * 0.01)
    patterns = []
    for tool, count in retry_tools.most_common(5):
        if count >= min_retries:
            patterns.append({
                "type": "frequent_retries",
                "tool": tool,
                "retry_count": count,
                "sessions_affected": sessions_with_retries,
            })
    return patterns


def detect_score_trends(observations, window=40):
    """Detect if scores are trending up or down using a sliding peer window.

    Compares the most recent `window` sessions against the `window` immediately
    preceding them — NOT against all history. This prevents stale baselines
    from long-past workflow eras (older sessions scored on a different tool
    mix, scoring model, or user workload) from dominating the comparison.

    Requires at least 2*window observations for a meaningful comparison.
    """
    if len(observations) < window * 2:
        return []

    sorted_obs = sorted(
        observations,
        key=lambda o: o.get("start_time", "") or "",
    )
    scores = [obs.get("score", 0) for obs in sorted_obs]

    recent = scores[-window:]
    earlier = scores[-2 * window:-window]

    if not recent or not earlier:
        return []

    recent_avg = sum(recent) / len(recent)
    earlier_avg = sum(earlier) / len(earlier)

    patterns = []
    if recent_avg < earlier_avg - 10:
        patterns.append({
            "type": "score_decline",
            "recent_avg": round(recent_avg, 1),
            "earlier_avg": round(earlier_avg, 1),
            "delta": round(recent_avg - earlier_avg, 1),
        })
    elif recent_avg > earlier_avg + 10:
        patterns.append({
            "type": "score_improvement",
            "recent_avg": round(recent_avg, 1),
            "earlier_avg": round(earlier_avg, 1),
            "delta": round(recent_avg - earlier_avg, 1),
        })
    return patterns


def detect_skill_gaps(observations):
    """Find sessions where skills could have been used but weren't."""
    no_skill_sessions = []
    skill_sessions = []

    for obs in observations:
        skills = obs.get("skills_used", [])
        if not skills:
            no_skill_sessions.append(obs)
        else:
            skill_sessions.append(obs)

    patterns = []
    if len(no_skill_sessions) > len(skill_sessions) and len(no_skill_sessions) >= 3:
        avg_score_no_skill = sum(o.get("score", 0) for o in no_skill_sessions) / len(no_skill_sessions)
        avg_score_skill = sum(o.get("score", 0) for o in skill_sessions) / max(len(skill_sessions), 1)
        patterns.append({
            "type": "low_skill_usage",
            "sessions_without_skills": len(no_skill_sessions),
            "sessions_with_skills": len(skill_sessions),
            "avg_score_without": round(avg_score_no_skill, 1),
            "avg_score_with": round(avg_score_skill, 1),
        })
    return patterns


def generate_improvement_proposals(all_patterns):
    """Generate concrete improvement proposals from detected patterns."""
    proposals = []

    for p in all_patterns:
        if p["type"] == "frequent_tool_errors":
            proposals.append({
                "pattern": p,
                "proposal": f"Investigate why {p['tool']} fails in {p['error_rate']*100:.0f}% of sessions. "
                           f"Consider: adding error handling, pre-validation, or a fallback strategy.",
                "action": "feedback_memory",
                "priority": "high" if p["error_rate"] > 0.5 else "medium",
            })
        elif p["type"] == "recurring_corrections":
            proposals.append({
                "pattern": p,
                "proposal": f"Human corrected agent in {p['correction_rate']*100:.0f}% of sessions. "
                           f"Top themes: {', '.join(p['top_themes'])}. "
                           f"Consider creating feedback memories for these patterns.",
                "action": "feedback_memory",
                "priority": "high",
            })
        elif p["type"] == "frequent_retries":
            proposals.append({
                "pattern": p,
                "proposal": f"{p['tool']} required retries {p['retry_count']} times. "
                           f"Consider improving input validation or adding pre-checks.",
                "action": "skill_patch",
                "priority": "medium",
            })
        elif p["type"] == "score_decline":
            proposals.append({
                "pattern": p,
                "proposal": f"Performance declining: recent avg {p['recent_avg']} vs earlier {p['earlier_avg']} "
                           f"(delta: {p['delta']}). Review recent sessions for systemic issues.",
                "action": "investigation",
                "priority": "high",
            })
        elif p["type"] == "low_skill_usage":
            proposals.append({
                "pattern": p,
                "proposal": f"Skills used in only {p['sessions_with_skills']}/{p['sessions_with_skills'] + p['sessions_without_skills']} sessions. "
                           f"Score with skills: {p['avg_score_with']} vs without: {p['avg_score_without']}. "
                           f"Consider improving skill trigger descriptions.",
                "action": "skill_patch",
                "priority": "medium",
            })

    return proposals


def save_patterns(all_patterns):
    """Save detected patterns (proposals are handled by propose_improvements)."""
    ensure_dirs()
    timestamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")

    patterns_file = os.path.join(PATTERNS_DIR, f"{timestamp}_patterns.json")
    with open(patterns_file, "w") as f:
        json.dump(all_patterns, f, indent=2, default=str)

    return patterns_file


def format_markdown(all_patterns, proposals, observations):
    """Format results as markdown."""
    lines = []
    lines.append(f"## Pattern Analysis ({len(observations)} sessions)")
    lines.append("")

    if not all_patterns:
        lines.append("No significant patterns detected yet. Need more observations.")
        return "\n".join(lines)

    for p in all_patterns:
        ptype = p["type"].replace("_", " ").title()
        lines.append(f"### {ptype}")
        for k, v in p.items():
            if k != "type":
                lines.append(f"- **{k}**: {v}")
        lines.append("")

    if proposals:
        lines.append("## Improvement Proposals")
        lines.append("")
        for i, prop in enumerate(proposals, 1):
            lines.append(f"### {i}. [{prop['priority'].upper()}] {prop['action']}")
            lines.append(f"{prop['proposal']}")
            lines.append("")

    return "\n".join(lines)


def main():
    args = sys.argv[1:]
    output_json = "--json" in args
    observations = load_observations()
    if not observations:
        print("No observations found. Run analyze_session first.", file=sys.stderr)
        sys.exit(1)

    print(f"Analyzing {len(observations)} observations...", file=sys.stderr)

    all_patterns = []
    all_patterns.extend(detect_error_patterns(observations))
    all_patterns.extend(detect_correction_patterns(observations))
    all_patterns.extend(detect_retry_patterns(observations))
    all_patterns.extend(detect_score_trends(observations))
    all_patterns.extend(detect_skill_gaps(observations))

    proposals = generate_improvement_proposals(all_patterns)
    patterns_file = save_patterns(all_patterns)

    if output_json:
        print(json.dumps({"patterns": all_patterns, "proposals": proposals}, indent=2, default=str))
    else:
        print(format_markdown(all_patterns, proposals, observations))

    print(f"\nPatterns saved: {patterns_file}", file=sys.stderr)


if __name__ == "__main__":
    main()
