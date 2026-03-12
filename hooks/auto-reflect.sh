#!/usr/bin/env bash
# Auto-reflect hook: runs session analysis at end of every Claude Code session.
# Fires on SessionEnd event. Runs in background to avoid blocking exit.
#
# What it does:
#   1. Analyzes the latest session transcript
#   2. Runs pattern detection if enough observations exist
#   3. Logs results to hook-log.txt

set -euo pipefail

AR_DIR="${AUTO_REFLECT_DIR:-$HOME/.claude/auto-reflect}"
LOG="$AR_DIR/hook-log.txt"
OBSERVATIONS="$AR_DIR/observations"

# Read hook input from stdin
INPUT=$(cat)

# Extract session info from hook JSON
SESSION_ID=$(echo "$INPUT" | jq -r '.session_id // empty' 2>/dev/null || true)
CWD=$(echo "$INPUT" | jq -r '.cwd // empty' 2>/dev/null || true)

# Run analysis in background subshell so we don't block session exit
{
    echo "[$(date -Iseconds)] SessionEnd hook fired (session: ${SESSION_ID:-unknown}, cwd: ${CWD:-unknown})" >> "$LOG"

    # Single call: analyze and capture full JSON output
    RESULT=$(python3 -m auto_reflect.analyze_session --latest --json 2>/dev/null || echo "{}")
    SCORE=$(echo "$RESULT" | jq -r '.score // "?"' 2>/dev/null || echo "error")
    TRANSCRIPT=$(echo "$RESULT" | jq -r '.session_file // empty' 2>/dev/null || true)

    if [ -z "$TRANSCRIPT" ]; then
        echo "[$(date -Iseconds)] No transcript found, skipping" >> "$LOG"
        exit 0
    fi

    echo "[$(date -Iseconds)] Session score: $SCORE ($TRANSCRIPT)" >> "$LOG"

    # Count observations
    OBS_COUNT=$(find "$OBSERVATIONS" -name "*.json" -type f 2>/dev/null | wc -l | tr -d ' ')

    # Run pattern detection if we have enough data (10+ observations)
    if [ "$OBS_COUNT" -ge 10 ]; then
        PATTERNS=$(python3 -m auto_reflect.detect_patterns --json 2>/dev/null | jq '.patterns | length' 2>/dev/null || echo "0")
        echo "[$(date -Iseconds)] Pattern detection: $PATTERNS patterns from $OBS_COUNT observations" >> "$LOG"
    fi

    echo "[$(date -Iseconds)] Auto-reflect complete" >> "$LOG"
} &

# Don't wait for background process — let session exit immediately
exit 0
