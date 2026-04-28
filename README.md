# Auto-Reflect

**A self-improvement feedback loop for Claude Code.**

Every session is automatically scored, patterns are detected across sessions, and concrete improvement proposals are generated — but nothing changes without your approval.

## The loop

```
Session ends → Hook scores transcript → Observation saved
                                              ↓
                                        Patterns detected
                                              ↓
                              /auto-reflect → Proposals generated
                                              ↓
                                          You approve
                                              ↓
                                      You apply the change
```

## Prerequisites

- Python 3.8+ (stdlib only, no deps)
- jq
- Claude Code (`~/.claude` exists)

## Install

```bash
git clone https://github.com/TysonHeim/claude-auto-reflect.git
cd claude-auto-reflect
./install.sh
```

The installer:
- Installs the Python package (editable mode)
- Creates `~/.claude/auto-reflect/{observations,patterns,improvements}`
- Adds the `SessionEnd` hook to `~/.claude/settings.json`
- Installs the `/auto-reflect` slash command

## Usage

In Claude Code, run `/auto-reflect` after a working session. The slash command tells Claude to:
1. Generate proposals from accumulated observations + patterns
2. Show them to you
3. Apply approved ones (memory file, CLAUDE.md rule, skill edit)

CLI equivalents:
```bash
python3 -m auto_reflect.analyze_session --latest    # score a session (hook does this)
python3 -m auto_reflect.detect_patterns             # cross-session patterns
python3 -m auto_reflect.propose_improvements        # generate proposals
python3 -m auto_reflect.proposals --list            # review pending
python3 -m auto_reflect.proposals --approve 1,3
python3 -m auto_reflect.proposals --reject 2
python3 -m auto_reflect.proposals --expire          # auto-reject >7 days
python3 -m auto_reflect.proposals --history
```

## How scoring works

Sessions start at 100 and lose points for friction:

| Factor | Penalty | Cap |
|--------|---------|-----|
| Error rate | errors / tool calls × 100 | -30 |
| Corrections | -7 per human correction | -35 |
| Retry rate | retries / tool calls × 80 | -20 |
| Tool misuse | -5 per Bash-instead-of-dedicated-tool | -25 |

The score measures absence of friction.

## Pattern detection

Cross-session pattern detection requires statistical signal:
- Min 5 sessions AND 5 errors for tool error patterns
- Min 1% of total sessions for retry patterns
- 20+ observations for trend detection
- MCP tools grouped by server prefix

## Proposal types

| Type | Source | Action |
|------|--------|--------|
| `feedback_memory` | Corrections, error patterns | Create a memory file |
| `skill_patch` | Tool error patterns, retries | Edit a skill to add guards |
| `claude_md_patch` | Recurring corrections (3+ sessions) | Add a CLAUDE.md rule |
| `memory_cleanup` | Stale/redundant memories | Clean up memory files |
| `agent_patch` | Agent error rates >30% | Fix agent definition |
| `investigation` | Score decline | Review systemic issues |

Proposals expire after 7 days if not reviewed.

## Status line (optional)

Add the score to your Claude Code status line:

```json
{
  "statusLine": {
    "type": "command",
    "command": "echo \"R:$(/path/to/claude-auto-reflect/hooks/reflect-status.sh)\""
  }
}
```

## Configuration

Override via environment variables:

| Variable | Default | Purpose |
|----------|---------|---------|
| `AUTO_REFLECT_DIR` | `~/.claude/auto-reflect` | Data directory |
| `CLAUDE_DIR` | `~/.claude` | Claude Code config root |
| `AUTO_REFLECT_SESSIONS_DIR` | `$CLAUDE_DIR/projects` | JSONL transcripts |
| `AUTO_REFLECT_MEMORY_DIR` | *(none)* | Memory dir (enables memory cleanup proposals) |
| `AUTO_REFLECT_EXPIRE_DAYS` | `7` | Proposal expiry |
| `AUTO_REFLECT_REJECTION_DAYS` | `30` | Suppression after rejection |

(Many more thresholds in `auto_reflect/config.py` — defaults are sane.)

## Architecture

```
~/.claude/auto-reflect/
├── observations/         # Per-session scores
├── patterns/             # Cross-session detected patterns
├── improvements/         # Pending proposals
├── hook-log.txt          # Activity log
└── proposal-history.json # Approval/rejection audit trail
```

All data regenerable from session transcripts.

## Uninstall

```bash
./install.sh --uninstall
```

Removes hooks and slash command. Data directories preserved.

## License

MIT
