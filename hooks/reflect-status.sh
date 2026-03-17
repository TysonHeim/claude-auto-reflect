#!/usr/bin/env bash
# Output auto-reflect score + trend for the status line.
# Format: "92▲" or "75▼" or "88–" (score + trend arrow)
# Fast: reads at most 5 JSON files, no Python.

OBSERVATIONS="${AUTO_REFLECT_DIR:-$HOME/.claude/auto-reflect}/observations"

# Get the 5 most recent observations by filename (sorted chronologically)
mapfile -t FILES < <(ls -t "$OBSERVATIONS"/*.json 2>/dev/null | head -5)
[ ${#FILES[@]} -eq 0 ] && echo "—" && exit 0

# Extract scores
SCORES=()
for f in "${FILES[@]}"; do
    S=$(jq -r '.score // empty' "$f" 2>/dev/null)
    [ -n "$S" ] && SCORES+=("$S")
done

[ ${#SCORES[@]} -eq 0 ] && echo "—" && exit 0

LATEST=${SCORES[0]}

# Compute trend from last 5
if [ ${#SCORES[@]} -ge 3 ]; then
    SUM=0
    for s in "${SCORES[@]:1}"; do SUM=$((SUM + s)); done
    PREV_AVG=$((SUM / (${#SCORES[@]} - 1)))

    if [ "$LATEST" -gt $((PREV_AVG + 3)) ]; then
        ARROW="▲"
    elif [ "$LATEST" -lt $((PREV_AVG - 3)) ]; then
        ARROW="▼"
    else
        ARROW="–"
    fi
else
    ARROW="–"
fi

printf '%s%s' "$LATEST" "$ARROW"
