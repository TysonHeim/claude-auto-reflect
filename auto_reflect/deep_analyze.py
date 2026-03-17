#!/usr/bin/env python3
"""
LLM-powered deep analysis of low-scoring sessions.

Instead of just counting errors, this reads the actual transcript and asks
Claude to identify root causes, wasted effort, and specific improvements.

Usage:
    python3 -m auto_reflect.deep_analyze
    python3 -m auto_reflect.deep_analyze --threshold 80
    python3 -m auto_reflect.deep_analyze --session <id>
    python3 -m auto_reflect.deep_analyze --batch 5
    python3 -m auto_reflect.deep_analyze --json
"""

import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from auto_reflect.config import (
    OBSERVATIONS_DIR,
    DEEP_ANALYSIS_DIR,
    IMPROVEMENTS_DIR,
    DEFAULT_ANALYSIS_THRESHOLD,
    ensure_dirs,
)

ANALYSIS_PROMPT = """You are analyzing a Claude Code session transcript to identify why it scored poorly.

Session score: {score}/100
Error count: {error_count}
Correction count: {correction_count}
Retry count: {retry_count}

Here is a condensed view of the session. Tool calls show the tool name and key input. Errors show the error message. Human messages show the text.

<transcript>
{condensed_transcript}
</transcript>

Analyze this session and respond with ONLY a JSON object (no markdown, no explanation):

{{
  "root_causes": [
    {{
      "description": "One sentence describing what went wrong",
      "category": "one of: wrong_approach | tool_misuse | missing_context | skill_gap | communication | external_failure",
      "severity": "high | medium | low",
      "evidence": "Quote or reference from the transcript"
    }}
  ],
  "wasted_effort": [
    {{
      "description": "What was done unnecessarily",
      "tool_calls_wasted": 0
    }}
  ],
  "specific_improvements": [
    {{
      "type": "one of: feedback_memory | skill_patch | behavior_change",
      "description": "Concrete, actionable improvement",
      "rationale": "Why this would have prevented the issue"
    }}
  ],
  "summary": "2-3 sentence summary of what happened and what should change"
}}
"""


def load_observations():
    """Load all observations, sorted by score ascending."""
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


def already_analyzed(session_id):
    """Check if a session already has a deep analysis."""
    if not os.path.isdir(DEEP_ANALYSIS_DIR):
        return []
    sid = session_id[:8]
    return list(Path(DEEP_ANALYSIS_DIR).glob(f"*_{sid}.json"))


def condense_transcript(session_file, max_lines=300):
    """Extract a condensed view of the session for LLM analysis.

    Keeps: human messages, tool calls (name + key input), tool errors,
    assistant text responses. Drops: system messages, tool result content
    (unless error), progress events.
    """
    if not os.path.exists(session_file):
        return ""

    lines = []
    entry_count = 0

    with open(session_file) as f:
        for raw_line in f:
            raw_line = raw_line.strip()
            if not raw_line:
                continue
            try:
                entry = json.loads(raw_line)
            except json.JSONDecodeError:
                continue

            entry_type = entry.get("type", "")
            if entry_type not in ("user", "assistant"):
                continue

            msg = entry.get("message", {})
            role = msg.get("role", entry_type)
            content = msg.get("content", "")

            if isinstance(content, str) and content.strip():
                text = content.strip()[:500]
                lines.append(f"[{role}] {text}")
                entry_count += 1
            elif isinstance(content, list):
                for block in content:
                    if not isinstance(block, dict):
                        continue

                    btype = block.get("type", "")

                    if btype == "text":
                        text = block.get("text", "").strip()[:300]
                        if text and len(text) > 5:
                            lines.append(f"[{role}] {text}")
                            entry_count += 1

                    elif btype == "tool_use":
                        name = block.get("name", "?")
                        inp = block.get("input", {})
                        condensed_input = condense_tool_input(name, inp)
                        lines.append(f"[tool_call] {name}: {condensed_input}")
                        entry_count += 1

                    elif btype == "tool_result":
                        is_error = block.get("is_error", False)
                        if is_error:
                            result_content = block.get("content", "")
                            if isinstance(result_content, list):
                                result_content = " ".join(
                                    b.get("text", "") if isinstance(b, dict) else str(b)
                                    for b in result_content
                                )
                            lines.append(f"[ERROR] {str(result_content).strip()[:300]}")
                            entry_count += 1

            if entry_count >= max_lines:
                lines.append(f"[...truncated at {max_lines} entries...]")
                break

    return "\n".join(lines)


def condense_tool_input(tool_name, inp):
    """Extract the most relevant fields from tool input for context."""
    if tool_name == "Edit":
        path = inp.get("file_path", "?")
        old = (inp.get("old_string", "") or "")[:80]
        return f"{path} | old: '{old}...'"
    elif tool_name == "Read":
        return inp.get("file_path", "?")
    elif tool_name in ("Glob", "Grep"):
        pattern = inp.get("pattern", "?")
        path = inp.get("path", "")
        return f"'{pattern}' in {path}" if path else f"'{pattern}'"
    elif tool_name == "Bash":
        return (inp.get("command", "") or "")[:150]
    elif tool_name == "Write":
        return inp.get("file_path", "?")
    elif tool_name == "Skill":
        return inp.get("skill", "?")
    elif tool_name == "Agent":
        desc = inp.get("description", "")
        atype = inp.get("subagent_type", "")
        return f"{atype}: {desc}" if atype else desc
    else:
        # Generic: show first few key-value pairs
        items = list(inp.items())[:3]
        return ", ".join(f"{k}={str(v)[:50]}" for k, v in items)


def run_claude_analysis(prompt):
    """Send prompt to Claude via CLI and get structured response.

    NOTE: This cannot run inside a Claude Code session (nested sessions crash).
    Run from a regular terminal, the SessionEnd hook, or a cron job.
    """
    # Check for nested session
    if os.environ.get("CLAUDE_CODE_SESSION") or os.environ.get("CLAUDECODE"):
        print("Cannot run deep analysis inside Claude Code (nested session).", file=sys.stderr)
        print("Run from a regular terminal: python3 -m auto_reflect.deep_analyze", file=sys.stderr)
        return None

    try:
        # Unset CLAUDECODE env vars to avoid nested session detection
        env = {k: v for k, v in os.environ.items() if not k.startswith("CLAUDE")}
        result = subprocess.run(
            ["claude", "-p", "--output-format", "json"],
            input=prompt, capture_output=True, text=True, timeout=120, env=env,
        )
        if result.returncode != 0:
            print(f"Claude CLI error: {result.stderr[:200]}", file=sys.stderr)
            return None

        # Parse the CLI JSON output to get the response text
        try:
            cli_output = json.loads(result.stdout)
            response_text = cli_output.get("result", result.stdout)
        except json.JSONDecodeError:
            response_text = result.stdout

        # Extract JSON from the response (may have markdown wrapping)
        response_text = response_text.strip()
        if response_text.startswith("```"):
            # Strip markdown code fences
            resp_lines = response_text.split("\n")
            resp_lines = [l for l in resp_lines if not l.strip().startswith("```")]
            response_text = "\n".join(resp_lines)

        return json.loads(response_text)

    except subprocess.TimeoutExpired:
        print("Claude CLI timed out", file=sys.stderr)
        return None
    except json.JSONDecodeError as e:
        print(f"Failed to parse Claude response as JSON: {e}", file=sys.stderr)
        return None
    except FileNotFoundError:
        print("Claude CLI not found. Install claude-code first.", file=sys.stderr)
        return None


def analyze_session(obs):
    """Run deep analysis on a single session."""
    session_file = obs.get("session_file", "")
    session_id = obs.get("session_id", "unknown")

    print(f"Deep analyzing session {session_id[:8]} (score: {obs.get('score', '?')})...",
          file=sys.stderr)

    condensed = condense_transcript(session_file)
    if not condensed or len(condensed) < 50:
        print("  Transcript too short or missing, skipping", file=sys.stderr)
        return None

    prompt = ANALYSIS_PROMPT.format(
        score=obs.get("score", "?"),
        error_count=obs.get("error_count", 0),
        correction_count=obs.get("correction_count", 0),
        retry_count=obs.get("retry_count", 0),
        condensed_transcript=condensed,
    )

    analysis = run_claude_analysis(prompt)
    if not analysis:
        return None

    # Enrich with session metadata
    analysis["session_id"] = session_id
    analysis["session_score"] = obs.get("score", 0)
    analysis["analyzed_at"] = datetime.now().isoformat()

    return analysis


def save_analysis(analysis, session_id):
    """Save deep analysis result."""
    ensure_dirs()
    sid = session_id[:8]
    timestamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    filepath = os.path.join(DEEP_ANALYSIS_DIR, f"{timestamp}_{sid}.json")
    with open(filepath, "w") as f:
        json.dump(analysis, f, indent=2, default=str)
    return filepath


def analysis_to_proposals(analysis):
    """Convert deep analysis improvements into standard proposal format."""
    proposals = []
    for imp in analysis.get("specific_improvements", []):
        imp_type = imp.get("type", "feedback_memory")
        description = imp.get("description", "")
        rationale = imp.get("rationale", "")

        proposals.append({
            "type": imp_type,
            "status": "pending_review",
            "_summary": f"deep analysis: {description[:60]}",
            "content": {
                "name": f"deep-{datetime.now().strftime('%Y%m%d-%H%M%S')}",
                "description": description,
                "body": f"{description}\n\n**Why:** {rationale}",
                "evidence": f"From deep analysis of session {analysis.get('session_id', '?')[:8]} (score: {analysis.get('session_score', '?')})",
                "priority": "high",
            },
            "source": "deep_analyze",
            "created": datetime.now().isoformat(),
        })

    return proposals


def format_analysis(analysis):
    """Format a single analysis as markdown."""
    lines = []
    lines.append(f"### Session {analysis.get('session_id', '?')[:8]} (Score: {analysis.get('session_score', '?')})")
    lines.append("")

    lines.append(f"**Summary:** {analysis.get('summary', 'N/A')}")
    lines.append("")

    root_causes = analysis.get("root_causes", [])
    if root_causes:
        lines.append("**Root Causes:**")
        for rc in root_causes:
            sev = rc.get("severity", "?")
            lines.append(f"- [{sev.upper()}] {rc.get('description', '')}")
            if rc.get("evidence"):
                lines.append(f"  Evidence: {rc['evidence'][:150]}")
        lines.append("")

    wasted = analysis.get("wasted_effort", [])
    if wasted:
        lines.append("**Wasted Effort:**")
        for w in wasted:
            calls = w.get("tool_calls_wasted", 0)
            lines.append(f"- {w.get('description', '')} ({calls} tool calls)")
        lines.append("")

    improvements = analysis.get("specific_improvements", [])
    if improvements:
        lines.append("**Specific Improvements:**")
        for imp in improvements:
            lines.append(f"- [{imp.get('type', '?')}] {imp.get('description', '')}")
            lines.append(f"  Why: {imp.get('rationale', '')}")
        lines.append("")

    return "\n".join(lines)


def main():
    args = sys.argv[1:]
    output_json = "--json" in args
    args_clean = [a for a in args if a != "--json"]

    ensure_dirs()

    # Parse arguments
    threshold = DEFAULT_ANALYSIS_THRESHOLD
    target_session = None
    batch_size = 1

    i = 0
    while i < len(args_clean):
        if args_clean[i] == "--threshold" and i + 1 < len(args_clean):
            threshold = int(args_clean[i + 1])
            i += 2
        elif args_clean[i] == "--session" and i + 1 < len(args_clean):
            target_session = args_clean[i + 1]
            i += 2
        elif args_clean[i] == "--batch" and i + 1 < len(args_clean):
            batch_size = int(args_clean[i + 1])
            i += 2
        else:
            i += 1

    observations = load_observations()
    if not observations:
        print("No observations found.", file=sys.stderr)
        sys.exit(1)

    # Select sessions to analyze
    if target_session:
        candidates = [o for o in observations if o.get("session_id", "").startswith(target_session)]
    else:
        # Find low-scoring sessions that haven't been deep-analyzed yet
        candidates = [
            o for o in observations
            if o.get("score", 100) < threshold
            and not already_analyzed(o.get("session_id", "unknown"))
            and o.get("tool_call_count", 0) >= 5  # Skip trivial sessions
        ]
        # Sort by score ascending (worst first)
        candidates.sort(key=lambda o: o.get("score", 100))

    if not candidates:
        print(f"No unanalyzed sessions below score {threshold}.", file=sys.stderr)
        if not output_json:
            print(f"No sessions need deep analysis (threshold: {threshold}).")
        return

    candidates = candidates[:batch_size]
    print(f"Analyzing {len(candidates)} session(s)...", file=sys.stderr)

    all_analyses = []
    all_proposals = []

    for obs in candidates:
        analysis = analyze_session(obs)
        if analysis:
            save_path = save_analysis(analysis, obs.get("session_id", "unknown"))
            print(f"  Saved: {save_path}", file=sys.stderr)
            all_analyses.append(analysis)
            all_proposals.extend(analysis_to_proposals(analysis))

    # Save proposals if any
    if all_proposals:
        ensure_dirs()
        timestamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
        proposals_path = os.path.join(IMPROVEMENTS_DIR, f"{timestamp}_deep_proposals.json")
        with open(proposals_path, "w") as f:
            json.dump(all_proposals, f, indent=2, default=str)
        print(f"  Proposals: {proposals_path}", file=sys.stderr)

    if output_json:
        print(json.dumps({
            "analyses": all_analyses,
            "proposals": all_proposals,
        }, indent=2, default=str))
    else:
        if not all_analyses:
            print("No analyses completed.")
            return

        print(f"## Deep Analysis ({len(all_analyses)} sessions)")
        print()
        for a in all_analyses:
            print(format_analysis(a))

        if all_proposals:
            print(f"\n{len(all_proposals)} improvement proposals generated.")


if __name__ == "__main__":
    main()
