# Auto-Reflect

**A self-improvement feedback loop for Claude Code.**

Every session is automatically scored, patterns are detected across hundreds of sessions, and concrete improvement proposals are generated — but nothing changes without your approval. Changes are tracked for effectiveness and auto-reverted if they don't help.

## The Loop

```
You work → Session ends → Hook fires → Transcript scored → Observation saved
                                                                    ↓
                                          Effectiveness ← Patterns detected
                                          re-measured        ↓
                                              ↑         Proposals generated
                                              |              ↓
                                         30 sessions    You approve
                                              |              ↓
                                              └──── System improves → Repeat
```

## What It Does

**Every time you exit a session**, a background hook:
1. Parses the full JSONL transcript (tool calls, errors, retries, human corrections, agent usage)
2. Scores the session 0-100 based on error rate, correction rate, retry rate, and tool misuse
3. Saves a structured observation
4. Checks for patterns across all accumulated sessions

**When you run `/auto-reflect`**, the orchestrator:
1. Shows your score dashboard (last 10 scores, rolling average, trends)
2. Surfaces detected patterns with statistical backing
3. Generates concrete proposals across 8 types (see below)
4. Checks effectiveness of past approved proposals
5. Scans memory files for staleness and drift
6. Validates any skill changes through the eval gate (blocks regressions >10%)

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

# Manage proposals (CLI)
auto-reflect-proposals --list           # see pending proposals
auto-reflect-proposals --approve 1,3   # approve by index
auto-reflect-proposals --approve-all   # approve and apply all pending
auto-reflect-proposals --reject 2
auto-reflect-proposals --reject-all
auto-reflect-proposals --expire        # auto-reject stale proposals
auto-reflect-proposals --history       # audit trail with effectiveness status
```

## Web Dashboard

A local web dashboard lets you review and approve proposals with one click, with live stats on session scores, tool errors, and skill usage.

```bash
python3 ~/.claude/auto-reflect/scripts/dashboard_server.py
```

Opens at **http://localhost:7700** automatically.

**What it shows:**
- Session score trend and distribution
- Tool error rates and misuse patterns
- Top skills and agent usage
- Pending proposals with Approve/Reject buttons (auto-applies on approval)
- Full audit history with apply status

**Proposal auto-apply:** Approving a proposal immediately applies it:
- `feedback_memory` → writes a memory `.md` file and updates `MEMORY.md`
- `memory_cleanup` → deletes or fixes stale memory files
- `claude_md_patch` → appends a rule to your `CLAUDE.md`
- `skill_patch` / `agent_patch` → spawns `claude -p` to edit the target file

**Regenerate the dashboard manually:**
```bash
python3 ~/.claude/auto-reflect/scripts/generate_dashboard.py
```

The server regenerates the dashboard on every page load, so it always reflects current data.

## How Scoring Works

Sessions start at 100 and lose points for friction:

| Factor | Penalty | Cap |
|--------|---------|-----|
| **Error rate** | errors / tool calls * 100 | -30 |
| **Corrections** | -7 per human correction | -35 |
| **Retry rate** | retries / tool calls * 80 | -20 |
| **Tool misuse** | -5 per Bash-instead-of-dedicated-tool | -25 |

No bonuses. The score measures absence of friction, not presence of features.

## Pattern Detection

The detector requires statistical significance before surfacing anything:
- Minimum 5 sessions AND 5 errors for tool error patterns
- Minimum 1% of total sessions for retry patterns
- 20+ observations for trend detection (80/20 split, timestamp-sorted)
- MCP tools grouped by server prefix (not per-method noise)

## Proposal Types

| Type | Source | Action |
|------|--------|--------|
| `feedback_memory` | Human corrections, error patterns | Create a memory file so the agent doesn't repeat the mistake |
| `skill_patch` | Tool error patterns, retry rates | Edit a skill to add pre-validation or defensive patterns |
| `eval_query` | Corrections | Add a test case to a skill's eval set |
| `investigation` | Score decline | Review recent sessions for systemic issues |
| `agent_patch` | Agent error rates >30%, unused agents | Fix agent definition or remove dead agents |
| `claude_md_patch` | Recurring corrections (3+ sessions), rules not followed | Add/reword CLAUDE.md Corrections rules |
| `memory_cleanup` | Stale dates, missing frontmatter, redundant memories, broken index links | Clean up memory files |
| `revert` | Approved change didn't improve metrics after 30 sessions | Remove the ineffective artifact |

Proposals expire after 7 days if not reviewed.

## Effectiveness Tracking

When you approve a proposal, the system captures a baseline metric snapshot:

```
Approve → Baseline captured (e.g., "80 Bash errors in last 50 sessions")
                    ↓
            30 sessions pass
                    ↓
            Re-measure same metric
                    ↓
    ┌───────────────┼───────────────┐
    ↓               ↓               ↓
 Effective     Ineffective      Regressed
 (>15% better)  (flat)         (>10% worse)
    ↓               ↓               ↓
 Mark validated  Revert proposal  Revert proposal
                 (medium)         (high priority)
```

Revert proposals include the artifact path and context to decide whether to delete the memory file or remove the CLAUDE.md rule.

## Memory Cleanup

The system scans your memory directory for:
- **Redundant memories** — feedback memories that duplicate existing CLAUDE.md Corrections rules
- **Stale dates** — project/reference memories with dates >14 days in the past
- **Old memories** — project/reference memories not updated in 30+ days
- **Missing frontmatter** — memory files without required `name`/`description`/`type` metadata
- **Index drift** — MEMORY.md links to nonexistent files, or files not in the index

Set `AUTO_REFLECT_MEMORY_DIR` to your project's memory path to enable this.

## Eval Gate (Advanced, Optional)

The eval gate is a safety mechanism for users who have **custom skills with eval test suites**. It prevents self-improvement from introducing regressions by running evals before and after changes.

**You do NOT need this to use auto-reflect.** The core loop (score -> detect -> propose) works without it. The eval gate only activates if you have:
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

Output: `R:93` with a trend arrow -- last session scored 93, trending up.

## Configuration

All paths and thresholds are configurable via environment variables:

### Paths

| Variable | Default | Purpose |
|----------|---------|---------|
| `AUTO_REFLECT_DIR` | `~/.claude/auto-reflect` | Base data directory |
| `CLAUDE_DIR` | `~/.claude` | Claude Code config root |
| `AUTO_REFLECT_SESSIONS_DIR` | `$CLAUDE_DIR/projects` | Where JSONL transcripts live |
| `AUTO_REFLECT_MEMORY_DIR` | *(none)* | Project memory directory (enables memory cleanup) |
| `AUTO_REFLECT_SKILLS_DIR` | `$CLAUDE_DIR/skills` | Skills directory (for eval gate) |
| `AUTO_REFLECT_SKILLS_REPO` | `$CLAUDE_DIR/skills-repo` | Skills repo (for eval gate) |
| `AUTO_REFLECT_EVAL_TOOLS` | `$SKILLS_REPO/_eval-tools` | Eval harness location |

### Thresholds

| Variable | Default | Purpose |
|----------|---------|---------|
| `AUTO_REFLECT_EXPIRE_DAYS` | `7` | Days before proposals auto-expire |
| `AUTO_REFLECT_REJECTION_DAYS` | `30` | Days rejected proposals are suppressed |
| `AUTO_REFLECT_CLUSTER_SIMILARITY` | `0.35` | Jaccard threshold for grouping corrections |
| `AUTO_REFLECT_RULE_SIMILARITY` | `0.40` | Overlap threshold for matching corrections to rules |
| `AUTO_REFLECT_MIN_ERROR_COUNT` | `3` | Min errors in a category to propose a fix |
| `AUTO_REFLECT_MIN_CLUSTER_SESSIONS` | `3` | Sessions needed to promote a correction to CLAUDE.md |
| `AUTO_REFLECT_HIGH_PRIORITY_SESSIONS` | `5` | Sessions threshold for high vs medium priority |
| `AUTO_REFLECT_MIN_AGENT_USES` | `3` | Min agent calls before flagging error rate |
| `AUTO_REFLECT_AGENT_ERROR_RATE` | `0.30` | Agent error rate threshold |
| `AUTO_REFLECT_MIN_SESSIONS_UNUSED_AGENTS` | `50` | Sessions before flagging unused agents |
| `AUTO_REFLECT_MIN_RULE_VIOLATIONS` | `3` | Times a CLAUDE.md rule must be violated to flag it |
| `AUTO_REFLECT_MEMORY_STALENESS_DAYS` | `30` | Days before project/reference memories are flagged |
| `AUTO_REFLECT_PAST_DATE_DAYS` | `14` | Days in the past before a date is considered stale |
| `AUTO_REFLECT_REVIEW_WINDOW` | `30` | Sessions before checking proposal effectiveness |
| `AUTO_REFLECT_IMPROVEMENT_THRESHOLD` | `0.15` | Improvement needed to mark a proposal effective |
| `AUTO_REFLECT_REGRESSION_THRESHOLD` | `-0.10` | Regression needed to flag for revert |

## Architecture

```
~/.claude/auto-reflect/
├── observations/           # Per-session scores (regenerable from transcripts)
├── patterns/               # Detected patterns (regenerable)
├── improvements/           # Proposals awaiting review (regenerable)
├── baselines/              # Eval baselines for skill gate
├── hook-log.txt            # Activity log
└── proposal-history.json   # Approval/rejection audit trail + effectiveness tracking
```

All raw data is regenerable from session transcripts. The scripts are the source of truth.

## Data Integrity

Hard-won lessons from production use:

- **Subagent filtering** -- Only parent session transcripts are analyzed (`agent-*.jsonl` excluded). Without this, observations inflate ~3x.
- **Deduplication** -- Observations keyed by session ID, not timestamp. Re-analyzing overwrites, never duplicates.
- **Single-fire hook** -- The SessionEnd hook calls the analyzer once and extracts all fields from JSON output.
- **Content-based dedup** -- Proposals deduplicate by body text fingerprint, not auto-generated names.
- **Rate-based scoring** -- A 100-tool-call session with 3 errors is fundamentally different from a 10-call session with 3 errors.
- **Rejection cache** -- Rejected proposals are suppressed for 30 days to prevent nagging.
- **Effectiveness baselines** -- Metrics are captured at approval time and re-measured with the same parameters for apples-to-apples comparison.

## Uninstall

```bash
./install.sh --uninstall
```

Removes hooks, slash commands, and cron entries. Data directories are preserved (delete manually if desired).

## Design Philosophy

**Why file-based?** JSON files are human-readable, git-diffable, zero-dependency. Even at 1000+ sessions the observation directory is ~1MB.

**Why rate-based scoring?** Rates normalize for session complexity. Penalizing raw error counts would unfairly punish long, productive sessions.

**Why track effectiveness?** Because proposing fixes isn't enough -- you need to know if they worked. Changes that don't improve metrics get flagged for removal.

**Why zero dependencies?** The entire codebase uses Python stdlib only. No `pip install` surprises, no version conflicts, works everywhere Python 3.8+ runs.

## Contributing

Contributions welcome. The key constraint: **zero external Python dependencies**. Everything must work with stdlib.

## License

MIT
