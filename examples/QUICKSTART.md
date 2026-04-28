# Quickstart

A 3-minute walkthrough of what auto-reflect does. Run `./install.sh` first.

## What gets created

```
~/.claude/auto-reflect/
├── observations/    # one JSON per session (added automatically by the hook)
├── patterns/        # cross-session patterns (added when you have ~10+ obs)
├── improvements/    # pending proposals
└── proposal-history.json
```

## The artifacts in plain English

### 1. An observation (per session, written by the hook)

`observations/2026-04-28_103000_abc12345.json` looks like:

```json
{
  "session_id": "abc12345",
  "score": 87,
  "tool_distribution": {"Edit": 12, "Read": 8, "Bash": 4},
  "error_distribution": {"Edit": 1},
  "corrections": ["don't use Bash for reading files"],
  "retries": [{"tool": "Edit", "reason": "old_string not found"}],
  "skills_used": ["pattern-discovery"],
  "start_time": "2026-04-28T10:30:00Z"
}
```

The score (0–100) is friction-only: errors, corrections, retries, tool misuse all subtract.
See `examples/sample-observation.json` for a fuller example.

### 2. A pattern (cross-session, written by `detect_patterns`)

```json
{
  "type": "frequent_tool_errors",
  "tool": "Edit",
  "error_rate": 0.42,
  "error_sessions": 9,
  "total_sessions": 21
}
```

Patterns require statistical signal — at least 5 sessions and 5 errors for tool error
patterns, 80+ observations for trend detection (sliding peer window).

### 3. A proposal (written by `propose_improvements`)

```json
{
  "type": "feedback_memory",
  "status": "pending_review",
  "content": {
    "name": "feedback-edit-old-string",
    "memory_type": "feedback",
    "body": "Read files with the Read tool before Edit. The Edit tool requires exact-match old_string and fails silently when content was modified or never read.",
    "evidence": "9/13 Edit errors (69%)"
  }
}
```

You review these via `/auto-reflect` (or `python3 -m auto_reflect.proposals --list`)
and approve the ones you want. **Nothing auto-applies.**

## A typical day

1. Work normally in Claude Code. The SessionEnd hook scores each session.
2. After 10+ sessions, run `/auto-reflect` to get proposals.
3. Approve the ones that look right; reject the rest.
4. Apply approved proposals manually (edit your CLAUDE.md, save the memory file, etc.).
5. The system tracks rejection fingerprints for 30 days so you don't see the same noise twice.

## Verifying it's working

```bash
./install.sh --check     # one-shot smoke test
ls ~/.claude/auto-reflect/observations/ | wc -l    # should grow with each session
tail ~/.claude/auto-reflect/hook-log.txt           # most recent hook activity
```
