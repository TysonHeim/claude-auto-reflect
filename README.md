# Auto-Reflect

**A self-improvement feedback loop for Claude Code.**

Every session is automatically scored, patterns are detected across hundreds of sessions, and concrete improvement proposals are generated — but nothing changes without your approval.

## The Loop

```
You work → Session ends → Hook fires → Transcript scored → Observation saved
                                                                    ↓
                    You approve ← Proposals generated ← Patterns detected
                        ↓
                  System improves → Next session is better → Repeat forever
```

## What It Does

**Every time you exit a session**, a background hook:
1. Parses the full JSONL transcript (tool calls, errors, retries, human corrections)
2. Scores the session 0-100 based on error rate, correction rate, and retry rate
3. Saves a structured observation
4. Checks for patterns across all accumulated sessions

**When you run `/auto-reflect`**, the orchestrator:
1. Shows your score dashboard (last 10 scores, rolling average, trends)
2. Surfaces detected patterns with statistical backing
3. Generates concrete proposals: feedback memories, skill patches, eval queries
4. Validates any skill changes through the eval gate (blocks regressions >10%)

**Nothing auto-applies.** You review, you approve, you own the loop.

## Prerequisites

| Requirement | Why |
|-------------|-----|
| **Python 3.8+** | All analysis scripts are Python (stdlib only, zero deps) |
| **jq** | Shell hooks parse JSON from Claude Code events |
| **Claude Code** | The `~/.claude` directory must exist |

Install jq if needed:
```bash
# macOS
brew install jq

# Ubuntu/Debian
sudo apt install jq

# Fedora
sudo dnf install jq
```

## Quick Start

```bash
git clone https://github.com/TysonHeim/claude-auto-reflect.git
cd claude-auto-reflect
./install.sh
```

That's it. The installer:
- Checks prerequisites (Python, jq, Claude Code)
- Installs the Python package
- Creates data directories
- Adds the `SessionEnd` hook to your Claude Code settings
- Installs the `/auto-reflect` slash command

Optional: add a cron job for catching missed sessions:
```bash
./install.sh --with-cron
```

## Usage

```bash
# Inside Claude Code
/auto-reflect                    # full analysis loop

# From terminal
auto-reflect --status            # system dashboard
auto-reflect --latest            # analyze latest session
auto-reflect --batch 20          # batch analyze last 20 sessions

# Manage proposals
auto-reflect-proposals --list    # see pending proposals
auto-reflect-proposals --approve 1,3
auto-reflect-proposals --reject 2
auto-reflect-proposals --expire  # auto-reject stale proposals
```

## How Scoring Works

Sessions start at 100 and lose points for friction:

- **Error rate** — errors ÷ total tool calls (not raw count — 3 errors in 100 calls is fine, 3 in 10 is not)
- **Corrections** — -7 per human correction (only counted after first assistant response, to avoid false positives)
- **Retry rate** — retries ÷ total tool calls

No bonuses. The score measures absence of friction, not presence of features.

## Pattern Detection

The detector requires statistical significance before surfacing anything:
- Minimum 5 sessions AND 5 errors for tool error patterns
- Minimum 1% of total sessions for retry patterns
- 20+ observations for trend detection (80/20 split, timestamp-sorted)
- MCP tools grouped by server prefix (not per-method noise)

## Eval Gate (Advanced, Optional)

The eval gate is a safety mechanism for users who have **custom skills with eval test suites**. It prevents self-improvement from introducing regressions by running evals before and after changes.

**You do NOT need this to use auto-reflect.** The core loop (score → detect → propose) works without it. The eval gate only activates if you have:
1. A skills directory with Claude Code skills
2. Each skill has an `evals/trigger-eval.json` file
3. An eval runner script that can execute those evals

If you do have this setup:

```bash
auto-reflect-eval-gate --skill <name> --validate
```

- Runs the skill's `trigger-eval.json`
- Compares against saved baseline
- **BLOCKED** if regression >10%
- **PASSED** if stable or improved (baseline auto-updates on improvement)

The eval runner interface expects a script at `$AUTO_REFLECT_EVAL_TOOLS/scripts/run_eval.py` that accepts `--eval-set <path> --skill-path <path> --verbose` and outputs JSON results. You can point `AUTO_REFLECT_EVAL_TOOLS` to your own harness.

## Proposal Types

| Type | Source | Action |
|------|--------|--------|
| `feedback_memory` | Human corrections | Create a memory file so the agent doesn't repeat the mistake |
| `skill_patch` | Tool error patterns | Edit a skill to add pre-validation or defensive patterns |
| `eval_query` | Corrections | Add a test case to a skill's eval set |
| `investigation` | Score decline | Review recent sessions for systemic issues |

Proposals expire after 7 days if not reviewed.

## Status Line

Add the reflect score to your Claude Code status line:

```json
{
  "statusLine": {
    "type": "command",
    "command": "echo \"R:$(/path/to/claude-auto-reflect/hooks/reflect-status.sh)\""
  }
}
```

Output: `R:93▲` — last session scored 93, trending up.
Arrows: **▲** improving **▼** declining **–** stable

## Configuration

All paths are configurable via environment variables:

| Variable | Default | Purpose |
|----------|---------|---------|
| `AUTO_REFLECT_DIR` | `~/.claude/auto-reflect` | Base data directory |
| `CLAUDE_DIR` | `~/.claude` | Claude Code config root |
| `AUTO_REFLECT_SESSIONS_DIR` | `$CLAUDE_DIR/projects` | Where JSONL transcripts live |
| `AUTO_REFLECT_SKILLS_DIR` | `$CLAUDE_DIR/skills` | Skills directory (for eval gate) |
| `AUTO_REFLECT_SKILLS_REPO` | `$CLAUDE_DIR/skills-repo` | Skills repo (for eval gate) |
| `AUTO_REFLECT_EVAL_TOOLS` | `$SKILLS_REPO/_eval-tools` | Eval harness location |
| `AUTO_REFLECT_EXPIRE_DAYS` | `7` | Days before proposals auto-expire |

## Architecture

```
~/.claude/auto-reflect/
├── observations/     # Per-session scores (regenerable from transcripts)
├── patterns/         # Detected patterns (regenerable)
├── improvements/     # Proposals awaiting review (regenerable)
├── baselines/        # Eval baselines for skill gate
├── hook-log.txt      # Activity log
└── proposal-history.json  # Approval/rejection audit trail
```

All raw data is regenerable from session transcripts. The scripts are the source of truth.

## Data Integrity

Hard-won lessons from production use:

- **Subagent filtering** — Only parent session transcripts are analyzed (`agent-*.jsonl` excluded). Without this, observations inflate ~3x.
- **Deduplication** — Observations keyed by session ID, not timestamp. Re-analyzing overwrites, never duplicates.
- **Single-fire hook** — The SessionEnd hook calls the analyzer once and extracts all fields from JSON output.
- **Content-based dedup** — Proposals deduplicate by body text fingerprint, not auto-generated names.
- **Rate-based scoring** — A 100-tool-call session with 3 errors is fundamentally different from a 10-call session with 3 errors.

## Uninstall

```bash
./install.sh --uninstall
```

Removes hooks, slash commands, and cron entries. Data directories are preserved (delete manually if desired).

## Design Philosophy

**Why file-based?** JSON files are human-readable, git-diffable, zero-dependency. Even at 1000+ sessions the observation directory is ~1MB.

**Why rate-based scoring?** Rates normalize for session complexity. Penalizing raw error counts would unfairly punish long, productive sessions.

**Why human-in-the-loop?** Self-improvement without oversight is how you get paperclip maximizers. Every proposal requires explicit approval.

**Why zero dependencies?** The entire codebase uses Python stdlib only. No `pip install` surprises, no version conflicts, works everywhere Python 3.8+ runs.

## Contributing

Contributions welcome. The key constraint: **zero external Python dependencies**. Everything must work with stdlib.

## License

MIT
