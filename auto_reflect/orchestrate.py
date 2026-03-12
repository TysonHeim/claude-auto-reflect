#!/usr/bin/env python3
"""
Orchestrates the full self-improvement loop:
1. Analyze session (analyze_session)
2. Detect patterns (detect_patterns)
3. Propose improvements (propose_improvements)

Usage:
    python3 -m auto_reflect.orchestrate --session <path>
    python3 -m auto_reflect.orchestrate --latest
    python3 -m auto_reflect.orchestrate --batch N
    python3 -m auto_reflect.orchestrate --status
"""

import json
import os
import sys
import subprocess
from datetime import datetime
from pathlib import Path

from auto_reflect.config import (
    AUTO_REFLECT_DIR, SESSIONS_DIR,
    OBSERVATIONS_DIR, PATTERNS_DIR, IMPROVEMENTS_DIR, BASELINES_DIR,
    ensure_dirs,
)


def _scripts_dir():
    """Get the directory containing the auto_reflect package scripts."""
    return os.path.dirname(os.path.abspath(__file__))


def run_module(module_name, args=None):
    """Run an auto_reflect module as a subprocess."""
    cmd = [sys.executable, "-m", f"auto_reflect.{module_name}"] + (args or [])

    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=120,
    )
    return {
        "stdout": result.stdout,
        "stderr": result.stderr,
        "returncode": result.returncode,
    }


def count_files(directory, pattern="*.json"):
    """Count files matching pattern in directory."""
    return len(list(Path(directory).glob(pattern))) if os.path.isdir(directory) else 0


def get_status():
    """Get current state of the self-improvement system."""
    obs_count = count_files(OBSERVATIONS_DIR)
    pattern_count = count_files(PATTERNS_DIR, "*_patterns.json")
    proposal_count = count_files(IMPROVEMENTS_DIR)
    baseline_count = count_files(BASELINES_DIR)

    scores = []
    if os.path.isdir(OBSERVATIONS_DIR):
        for f in sorted(Path(OBSERVATIONS_DIR).glob("*.json"), reverse=True)[:10]:
            try:
                with open(f) as fh:
                    data = json.load(fh)
                    scores.append(data.get("score", 0))
            except (json.JSONDecodeError, IOError):
                continue

    avg_score = sum(scores) / len(scores) if scores else 0

    latest_patterns = []
    if os.path.isdir(PATTERNS_DIR):
        pattern_files = sorted(Path(PATTERNS_DIR).glob("*_patterns.json"), reverse=True)
        if pattern_files:
            try:
                with open(pattern_files[0]) as f:
                    latest_patterns = json.load(f)
            except (json.JSONDecodeError, IOError):
                pass

    pending = []
    if os.path.isdir(IMPROVEMENTS_DIR):
        for f in Path(IMPROVEMENTS_DIR).glob("*.json"):
            try:
                with open(f) as fh:
                    proposals = json.load(fh)
                    pending.extend(p for p in proposals if p.get("status") == "pending_review")
            except (json.JSONDecodeError, IOError):
                continue

    return {
        "observations": obs_count,
        "avg_score_last_10": round(avg_score, 1),
        "recent_scores": scores[:5],
        "patterns_detected": len(latest_patterns),
        "pending_proposals": len(pending),
        "eval_baselines": baseline_count,
        "pattern_details": latest_patterns[:3],
    }


def format_status(status):
    """Format status as markdown."""
    lines = []
    lines.append("## Self-Improvement System Status")
    lines.append("")
    lines.append("```")
    lines.append(f"  Observations:      {status['observations']} sessions analyzed")
    lines.append(f"  Avg Score (last 10): {status['avg_score_last_10']}/100")
    lines.append(f"  Recent Scores:     {status['recent_scores']}")
    lines.append(f"  Patterns Detected: {status['patterns_detected']}")
    lines.append(f"  Pending Proposals: {status['pending_proposals']}")
    lines.append(f"  Eval Baselines:    {status['eval_baselines']} skills")
    lines.append("```")
    lines.append("")

    if status["pattern_details"]:
        lines.append("### Active Patterns")
        for p in status["pattern_details"]:
            ptype = p.get("type", "unknown").replace("_", " ")
            lines.append(f"- **{ptype}**: {json.dumps({k: v for k, v in p.items() if k != 'type'})}")
        lines.append("")

    return "\n".join(lines)


def run_full_loop(session_args):
    """Execute the full self-improvement loop."""
    lines = []

    lines.append("### Step 1: Session Analysis")
    result = run_module("analyze_session", session_args)
    lines.append(result["stdout"])
    if result["returncode"] != 0:
        lines.append(f"**Error:** {result['stderr']}")
        return "\n".join(lines)

    lines.append("### Step 2: Pattern Detection")
    result = run_module("detect_patterns")
    lines.append(result["stdout"])

    lines.append("### Step 3: Improvement Proposals")
    result = run_module("propose_improvements")
    if result["stdout"].strip():
        lines.append(result["stdout"])
    else:
        lines.append("No new proposals generated.")
    lines.append("")

    status = get_status()
    lines.append(format_status(status))

    return "\n".join(lines)


def main():
    args = sys.argv[1:]
    ensure_dirs()

    if "--status" in args:
        status = get_status()
        print(format_status(status))
        return

    if "--batch" in args:
        idx = args.index("--batch")
        n = int(args[idx + 1]) if idx + 1 < len(args) else 5

        import glob as globmod
        sessions = sorted(
            [f for f in globmod.glob(os.path.join(SESSIONS_DIR, "**/*.jsonl"), recursive=True)
             if not os.path.basename(f).startswith("agent-") and "/subagents/" not in f],
            key=os.path.getmtime,
            reverse=True,
        )[:n]

        print(f"Batch analyzing {len(sessions)} sessions...\n")
        for s in sessions:
            print(f"--- {os.path.basename(s)} ---")
            result = run_module("analyze_session", [s])
            for line in result["stdout"].split("\n"):
                if "Score:" in line:
                    print(f"  {line.strip()}")
                    break
            print()

        print("--- Pattern Detection ---")
        result = run_module("detect_patterns")
        print(result["stdout"])
        return

    if "--session" in args:
        idx = args.index("--session")
        session_path = args[idx + 1] if idx + 1 < len(args) else None
        session_args = [session_path] if session_path else ["--latest"]
    else:
        session_args = ["--latest"]

    output = run_full_loop(session_args)
    print(output)


if __name__ == "__main__":
    main()
