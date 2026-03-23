#!/usr/bin/env bash
# Batch catch-up: analyze any sessions that don't have observations yet.
# Designed to run via cron as a safety net for missed SessionEnd hooks.
#
# Usage: ./batch-catchup.sh [max_sessions]

set -euo pipefail

# Ensure PATH includes common install locations (cron has minimal PATH)
export PATH="/opt/homebrew/bin:/usr/local/bin:$HOME/.local/bin:$PATH"

AR_DIR="${AUTO_REFLECT_DIR:-$HOME/.claude/auto-reflect}"
SESSIONS_DIR="${AUTO_REFLECT_SESSIONS_DIR:-$HOME/.claude/projects}"
OBSERVATIONS="$AR_DIR/observations"
LOG="$AR_DIR/hook-log.txt"
MAX=${1:-20}

echo "[$(date -Iseconds)] Batch catch-up started (max: $MAX)" >> "$LOG"

# Find all parent session JSONL files (exclude subagent files) modified in last 7 days
ANALYZED=0
SKIPPED=0

while IFS= read -r JSONL; do
    # Extract session ID prefix from filename for dedup check
    BASENAME=$(basename "$JSONL" .jsonl)
    SESSION_PREFIX="${BASENAME:0:8}"

    # Check if we already have an observation for this session
    if compgen -G "$OBSERVATIONS/*_${SESSION_PREFIX}.json" > /dev/null 2>&1; then
        SKIPPED=$((SKIPPED + 1))
        continue
    fi

    # Analyze this session
    python3 -m auto_reflect.analyze_session "$JSONL" > /dev/null 2>&1 && ANALYZED=$((ANALYZED + 1)) || true
done < <(find "$SESSIONS_DIR" -name "*.jsonl" -not -name "agent-*" -not -path "*/subagents/*" -size +10k -mtime -7 -type f 2>/dev/null | sort -t/ -k1 | tail -n "$MAX")

echo "[$(date -Iseconds)] Batch catch-up complete: $ANALYZED new, $SKIPPED already analyzed" >> "$LOG"

# Run pattern detection if we analyzed anything new
if [ "$ANALYZED" -gt 0 ]; then
    python3 -m auto_reflect.detect_patterns > /dev/null 2>&1 || true
    echo "[$(date -Iseconds)] Pattern detection refreshed" >> "$LOG"
fi

# Expire stale proposals (>7 days without review = auto-rejected)
EXPIRED=$(python3 -m auto_reflect.proposals --expire 2>/dev/null || echo "")
[ -n "$EXPIRED" ] && echo "[$(date -Iseconds)] $EXPIRED" >> "$LOG"
