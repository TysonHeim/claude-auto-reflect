#!/usr/bin/env bash
# Install auto-reflect for Claude Code.
#
# Usage:
#   ./install.sh              # install
#   ./install.sh --check      # smoke-test the install (idempotent, safe)
#   ./install.sh --uninstall  # remove

set -euo pipefail

REPO_DIR="$(cd "$(dirname "$0")" && pwd)"
CLAUDE_DIR="${CLAUDE_DIR:-$HOME/.claude}"
AR_DIR="${AUTO_REFLECT_DIR:-$CLAUDE_DIR/auto-reflect}"
SETTINGS="$CLAUDE_DIR/settings.json"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

info()  { echo -e "${BLUE}→${NC} $1"; }
ok()    { echo -e "${GREEN}✓${NC} $1"; }
warn()  { echo -e "${YELLOW}⚠${NC} $1"; }
error() { echo -e "${RED}✗${NC} $1"; }

# ─── Prerequisites ───────────────────────────────────────────────────────────

check_prereqs() {
    local missing=0

    if ! command -v python3 &>/dev/null; then
        error "python3 not found. Install Python 3.8+ first."
        missing=1
    else
        PY_VERSION=$(python3 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
        PY_MAJOR=$(echo "$PY_VERSION" | cut -d. -f1)
        PY_MINOR=$(echo "$PY_VERSION" | cut -d. -f2)
        if [ "$PY_MAJOR" -lt 3 ] || ([ "$PY_MAJOR" -eq 3 ] && [ "$PY_MINOR" -lt 8 ]); then
            error "Python 3.8+ required (found $PY_VERSION)"
            missing=1
        else
            ok "Python $PY_VERSION"
        fi
    fi

    if ! command -v jq &>/dev/null; then
        error "jq not found. Install it (brew install jq / apt install jq)."
        missing=1
    else
        ok "jq $(jq --version 2>/dev/null || echo '(version unknown)')"
    fi

    if [ ! -d "$CLAUDE_DIR" ]; then
        error "Claude Code config directory not found at $CLAUDE_DIR"
        missing=1
    else
        ok "Claude Code directory found"
    fi

    if [ "$missing" -eq 1 ]; then
        echo ""
        error "Missing prerequisites. Install them and re-run."
        exit 1
    fi
}

# ─── Uninstall ───────────────────────────────────────────────────────────────

uninstall() {
    info "Uninstalling auto-reflect..."

    if [ -f "$SETTINGS" ] && command -v jq &>/dev/null; then
        if jq -e '.hooks.SessionEnd' "$SETTINGS" &>/dev/null; then
            TMP1=$(mktemp) TMP2=$(mktemp)
            trap 'rm -f "$TMP1" "$TMP2"' EXIT
            if jq 'if .hooks.SessionEnd then .hooks.SessionEnd |= map(select(.hooks | all(.command | test("auto-reflect") | not))) else . end' "$SETTINGS" > "$TMP1" && \
               jq 'if .hooks.SessionEnd == [] then del(.hooks.SessionEnd) else . end' "$TMP1" > "$TMP2"; then
                mv "$TMP2" "$SETTINGS"
                ok "Removed SessionEnd hook from settings.json"
            else
                warn "Failed to update settings.json — file unchanged"
            fi
            rm -f "$TMP1" "$TMP2"
            trap - EXIT
        fi
    fi

    if [ -f "$CLAUDE_DIR/commands/auto-reflect.md" ]; then
        rm "$CLAUDE_DIR/commands/auto-reflect.md"
        ok "Removed /auto-reflect command"
    fi

    if python3 -m pip show claude-auto-reflect &>/dev/null 2>&1; then
        python3 -m pip uninstall -y claude-auto-reflect --quiet 2>/dev/null || true
        ok "Removed pip package"
    fi

    echo ""
    warn "Data directories preserved at $AR_DIR"
    warn "To fully remove: rm -rf $AR_DIR"
    echo ""
    ok "Uninstall complete."
    exit 0
}

# ─── Install ─────────────────────────────────────────────────────────────────

install_package() {
    info "Installing auto_reflect Python package..."
    cd "$REPO_DIR"
    if python3 -m pip install -e . --quiet 2>/dev/null; then
        ok "Package installed (pip editable mode)"
    else
        warn "pip install failed — falling back to PYTHONPATH"
        SHELL_RC=""
        [ -f "$HOME/.zshrc" ] && SHELL_RC="$HOME/.zshrc"
        [ -z "$SHELL_RC" ] && [ -f "$HOME/.bashrc" ] && SHELL_RC="$HOME/.bashrc"
        if [ -n "$SHELL_RC" ] && ! grep -q "AUTO_REFLECT" "$SHELL_RC" 2>/dev/null; then
            echo "" >> "$SHELL_RC"
            echo "# Auto-reflect for Claude Code" >> "$SHELL_RC"
            echo "export PYTHONPATH=\"$REPO_DIR:\${PYTHONPATH:-}\"" >> "$SHELL_RC"
            ok "Added PYTHONPATH to $SHELL_RC (restart shell or source it)"
        fi
    fi
}

create_data_dirs() {
    info "Creating data directories..."
    mkdir -p "$AR_DIR"/{observations,patterns,improvements}
    ok "Data directories at $AR_DIR"
}

install_hook() {
    info "Installing SessionEnd hook..."
    chmod +x "$REPO_DIR/hooks/auto-reflect.sh"

    if [ ! -f "$SETTINGS" ]; then
        echo '{"hooks":{}}' > "$SETTINGS"
    fi

    if jq -e '.hooks.SessionEnd[]?.hooks[]? | select(.command | test("auto-reflect"))' "$SETTINGS" &>/dev/null; then
        ok "SessionEnd hook already configured"
        return
    fi

    HOOK_CMD="$REPO_DIR/hooks/auto-reflect.sh"
    TMP=$(mktemp)
    trap 'rm -f "$TMP"' EXIT
    jq --arg cmd "$HOOK_CMD" '
        .hooks //= {} |
        .hooks.SessionEnd //= [] |
        .hooks.SessionEnd += [{
            "matcher": "",
            "hooks": [{"type": "command", "command": $cmd}]
        }]
    ' "$SETTINGS" > "$TMP" && mv "$TMP" "$SETTINGS"
    trap - EXIT

    ok "SessionEnd hook installed → $HOOK_CMD"
}

install_command() {
    info "Installing /auto-reflect slash command..."
    mkdir -p "$CLAUDE_DIR/commands"
    cp "$REPO_DIR/commands/auto-reflect.md" "$CLAUDE_DIR/commands/auto-reflect.md"
    ok "/auto-reflect command available"
}

# ─── Smoke check ─────────────────────────────────────────────────────────────

run_check() {
    local fail=0

    info "1. Python imports"
    if python3 -c "from auto_reflect import analyze_session, detect_patterns, propose_improvements, proposals, config" 2>/dev/null; then
        ok "all modules importable"
    else
        error "import failed — run ./install.sh first?"
        fail=1
    fi

    info "2. SessionEnd hook wired"
    if [ -f "$SETTINGS" ] && jq -e '.hooks.SessionEnd[]?.hooks[]? | select(.command | test("auto-reflect"))' "$SETTINGS" &>/dev/null; then
        ok "hook present in $SETTINGS"
    else
        error "no auto-reflect hook in $SETTINGS — re-run ./install.sh"
        fail=1
    fi

    info "3. Slash command installed"
    if [ -f "$CLAUDE_DIR/commands/auto-reflect.md" ]; then
        ok "/auto-reflect available"
    else
        error "/auto-reflect command missing"
        fail=1
    fi

    info "4. Data directories writable"
    if [ -w "$AR_DIR/observations" ] && [ -w "$AR_DIR/patterns" ] && [ -w "$AR_DIR/improvements" ]; then
        ok "$AR_DIR/{observations,patterns,improvements}"
    else
        error "data dirs missing or unwritable: $AR_DIR"
        fail=1
    fi

    info "5. End-to-end pipeline (fixture, hermetic tmpdir)"
    if python3 "$REPO_DIR/tests/test_smoke.py" >/tmp/auto-reflect-check.log 2>&1; then
        ok "fixture pipeline runs (analyze → detect → propose → list)"
    else
        error "smoke pipeline failed — see /tmp/auto-reflect-check.log"
        fail=1
    fi

    echo ""
    if [ "$fail" -eq 0 ]; then
        ok "All checks passed. /auto-reflect is ready."
        exit 0
    else
        error "Some checks failed. See messages above."
        exit 1
    fi
}

# ─── Main ────────────────────────────────────────────────────────────────────

echo ""
echo "Auto-Reflect for Claude Code"
echo ""

for arg in "$@"; do
    case $arg in
        --uninstall) uninstall ;;
        --check)     run_check ;;
        --help|-h)
            echo "Usage: ./install.sh [--check] [--uninstall]"
            exit 0
            ;;
        *)
            error "Unknown flag: $arg"
            exit 1
            ;;
    esac
done

check_prereqs
echo ""
install_package
create_data_dirs
install_hook
install_command

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
ok "Installation complete!"
echo ""
echo "  How it works:"
echo "    1. Every session end → analyzed + scored (background)"
echo "    2. Patterns detected across all sessions (10+ obs needed)"
echo "    3. Run /auto-reflect to generate proposals + review them"
echo "    4. You approve or reject — nothing auto-applies"
echo ""
echo "  Verify:    ./install.sh --check"
echo "  Uninstall: ./install.sh --uninstall"
echo ""
