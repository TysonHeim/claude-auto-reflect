#!/usr/bin/env python3
"""Generate auto-reflect dashboard HTML from live JSON data.

Usage:
    python3 generate_dashboard.py              # Generate and open
    python3 generate_dashboard.py --no-open    # Generate only
    python3 generate_dashboard.py --output /path/to/output.html
"""

import hashlib
import json
import os
import glob
import sys
import webbrowser
from collections import Counter, defaultdict
from datetime import datetime

from auto_reflect.config import (
    AUTO_REFLECT_DIR, OBSERVATIONS_DIR, IMPROVEMENTS_DIR, PROPOSAL_HISTORY,
)

BASE = AUTO_REFLECT_DIR
# Locate template relative to this file (works for both editable and installed packages)
TEMPLATE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "dashboard-template.html")
DEFAULT_OUTPUT = os.path.join(BASE, "dashboard.html")
PLACEHOLDER = "/*__DATA_PLACEHOLDER__*/{}"


def _fingerprint(proposal):
    """SHA256 content fingerprint — must match proposals.py._fingerprint()."""
    canonical = {k: v for k, v in proposal.items() if not k.startswith("_")}
    return hashlib.sha256(json.dumps(canonical, sort_keys=True).encode()).hexdigest()[:16]


def load_observations():
    obs = []
    for f in sorted(glob.glob(os.path.join(OBSERVATIONS_DIR, "*.json"))):
        try:
            with open(f) as fh:
                obs.append(json.load(fh))
        except (json.JSONDecodeError, OSError):
            pass
    return obs


def load_all_pending_proposals():
    """Load all pending proposals across all improvement files, with fingerprint."""
    pending = []
    for f in sorted(glob.glob(os.path.join(IMPROVEMENTS_DIR, "*.json"))):
        try:
            with open(f) as fh:
                proposals = json.load(fh)
            for p in proposals:
                if p.get("status") == "pending_review":
                    pending.append({
                        "proposal": p.get("proposal", p.get("_summary", ""))[:200],
                        "action": p.get("action", p.get("type", "")),
                        "type": p.get("type", ""),
                        "priority": p.get("content", {}).get("priority", p.get("priority", "")),
                        "status": "pending_review",
                        "fingerprint": _fingerprint(p),
                        "source_file": os.path.basename(f),
                    })
        except (json.JSONDecodeError, OSError):
            continue
    return pending


def load_proposal_history():
    if not os.path.exists(PROPOSAL_HISTORY):
        return []
    try:
        with open(PROPOSAL_HISTORY) as fh:
            return json.load(fh)
    except (json.JSONDecodeError, OSError):
        return []


def aggregate(observations):
    scores = [o.get("score", 0) for o in observations]

    # Score distribution
    score_buckets = Counter()
    for s in scores:
        bucket = min(s // 10 * 10, 90)
        score_buckets[bucket] += 1

    # Daily averages
    daily = defaultdict(list)
    for o in observations:
        ts = o.get("start_time", "")
        if ts:
            daily[ts[:10]].append(o.get("score", 0))
    daily_scores = [
        {"date": day, "avg": round(sum(v) / len(v), 1), "count": len(v)}
        for day, v in sorted(daily.items())
    ]

    # Tool error rates
    tool_data = defaultdict(lambda: {"errors": 0, "total": 0})
    for o in observations:
        for tool, count in o.get("tool_distribution", {}).items():
            tool_data[tool]["total"] += count
        for tool, count in o.get("error_distribution", {}).items():
            tool_data[tool]["errors"] += count
    tool_error_rates = sorted(
        [
            {"tool": t, "errors": d["errors"], "total": d["total"],
             "rate": round(d["errors"] / d["total"] * 100, 1)}
            for t, d in tool_data.items() if d["total"] >= 20
        ],
        key=lambda x: x["rate"], reverse=True
    )[:12]

    # Skills
    skill_counter = Counter()
    for o in observations:
        for s in o.get("skills_used", []):
            skill_counter[s] += 1

    # Agents
    agent_counter = Counter()
    agent_errors = Counter()
    for o in observations:
        for a in o.get("agents_used", []):
            t = a.get("subagent_type", "unknown")
            agent_counter[t] += 1
            if a.get("is_error"):
                agent_errors[t] += 1

    # Tool misuses
    misuse_counter = Counter()
    for o in observations:
        for m in o.get("tool_misuses", []):
            misuse_counter[m.get("anti_pattern", "unknown")] += 1

    # Summary
    total = len(observations)
    avg_score = round(sum(scores) / len(scores), 1) if scores else 0
    perfect = sum(1 for s in scores if s == 100)
    below_70 = sum(1 for s in scores if s < 70)

    return {
        "total": total,
        "avg_score": avg_score,
        "perfect": perfect,
        "below_70": below_70,
        "score_distribution": dict(sorted(score_buckets.items())),
        "daily_scores": daily_scores[-90:],
        "tool_error_rates": tool_error_rates,
        "top_skills": skill_counter.most_common(15),
        "agent_usage": [
            {"type": t, "count": c, "errors": agent_errors.get(t, 0)}
            for t, c in agent_counter.most_common(10)
        ],
        "tool_misuses": misuse_counter.most_common(10),
    }


def build_data(observations, pending_proposals, history):
    agg = aggregate(observations)

    return {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "summary": {
            "total_sessions": agg["total"],
            "avg_score": agg["avg_score"],
            "perfect_sessions": agg["perfect"],
            "below_70": agg["below_70"],
            "pending_proposals": len(pending_proposals),
            "proposals_approved": sum(1 for h in history if h.get("action") == "approved"),
            "proposals_rejected": sum(1 for h in history if h.get("action") == "rejected"),
        },
        "score_distribution": agg["score_distribution"],
        "daily_scores": agg["daily_scores"],
        "tool_error_rates": agg["tool_error_rates"],
        "top_skills": agg["top_skills"],
        "agent_usage": agg["agent_usage"],
        "tool_misuses": agg["tool_misuses"],
        "latest_proposals": pending_proposals[:20],
        "proposal_history": [
            {
                "action": h.get("action", ""),
                "type": h.get("type", ""),
                "summary": h.get("summary", "")[:100],
                "date": h.get("date", "")[:19],
                "applied": h.get("applied", None),
                "apply_result": h.get("apply_result", "")[:150],
                "apply_error": h.get("apply_error", "")[:150],
            }
            for h in history[-20:]
        ],
        "recent_sessions": [
            {
                "date": o.get("start_time", "")[:16],
                "score": o.get("score", 0),
                "tools": o.get("tool_call_count", 0),
                "errors": o.get("error_count", 0),
                "corrections": o.get("correction_count", 0),
                "misuses": o.get("tool_misuse_count", 0),
            }
            for o in observations[-30:]
        ],
    }


def generate(output_path, open_browser=True):
    if not os.path.exists(TEMPLATE):
        print(f"ERROR: Template not found at {TEMPLATE}", file=sys.stderr)
        sys.exit(1)

    with open(TEMPLATE) as f:
        template = f.read()

    if PLACEHOLDER not in template:
        print("ERROR: Placeholder not found in template", file=sys.stderr)
        sys.exit(1)

    observations = load_observations()
    pending_proposals = load_all_pending_proposals()
    history = load_proposal_history()

    data = build_data(observations, pending_proposals, history)
    data_json = json.dumps(data, separators=(",", ":"))

    # Inject data — all stat cards are now JS-driven from DATA.summary
    html = template.replace(PLACEHOLDER, data_json)

    with open(output_path, "w") as f:
        f.write(html)

    s = data["summary"]
    print(f"Dashboard generated: {output_path}")
    print(f"  Sessions: {s['total_sessions']} | Avg: {s['avg_score']} | Perfect: {s['perfect_sessions']} | Below70: {s['below_70']} | Pending: {s['pending_proposals']}")

    if open_browser:
        webbrowser.open(f"file://{os.path.abspath(output_path)}")


if __name__ == "__main__":
    output = DEFAULT_OUTPUT
    open_browser = True

    args = sys.argv[1:]
    if "--no-open" in args:
        open_browser = False
        args.remove("--no-open")
    if "--output" in args:
        idx = args.index("--output")
        output = args[idx + 1]

    generate(output, open_browser)
