#!/usr/bin/env bash
# Install auto-reflect for Claude Code.
#
# Usage:
#   ./install.sh              # interactive install
#   ./install.sh --with-cron  # also set up cron job
#   ./install.sh --uninstall  # remove everything

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
        error "jq not found. Install it:"
        echo "    macOS:  brew install jq"
        echo "    Ubuntu: sudo apt install jq"
        echo "    Fedora: sudo dnf install jq"
        missing=1
    else
        ok "jq $(jq --version 2>/dev/null || echo '(version unknown)')"
    fi

    if [ ! -d "$CLAUDE_DIR" ]; then
        error "Claude Code config directory not found at $CLAUDE_DIR"
        echo "    Install Claude Code first: https://docs.anthropic.com/en/docs/claude-code"
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

    # Remove hook from settings.json
    if [ -f "$SETTINGS" ] && command -v jq &>/dev/null; then
        if jq -e '.hooks.SessionEnd' "$SETTINGS" &>/dev/null; then
            TMP=$(mktemp)
            jq 'if .hooks.SessionEnd then .hooks.SessionEnd |= map(select(.hooks | all(.command | test("auto-reflect") | not))) else . end' "$SETTINGS" > "$TMP"
            # Clean up empty arrays
            jq 'if .hooks.SessionEnd == [] then del(.hooks.SessionEnd) else . end' "$TMP" > "$SETTINGS"
            rm -f "$TMP"
            ok "Removed SessionEnd hook from settings.json"
        fi
    fi

    # Remove slash command
    if [ -f "$CLAUDE_DIR/commands/auto-reflect.md" ]; then
        rm "$CLAUDE_DIR/commands/auto-reflect.md"
        ok "Removed /auto-reflect command"
    fi

    # Remove cron entry
    if crontab -l 2>/dev/null | grep -q "auto-reflect"; then
        crontab -l 2>/dev/null | grep -v "auto-reflect" | crontab -
        ok "Removed cron job"
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

    # Install in development mode so scripts are importable
    cd "$REPO_DIR"
    if pip3 install -e . --quiet 2>/dev/null; then
        ok "Package installed (pip editable mode)"
    else
        # Fallback: add to PYTHONPATH
        warn "pip install failed — falling back to PYTHONPATH"
        SHELL_RC=""
        if [ -f "$HOME/.zshrc" ]; then
            SHELL_RC="$HOME/.zshrc"
        elif [ -f "$HOME/.bashrc" ]; then
            SHELL_RC="$HOME/.bashrc"
        fi
        if [ -n "$SHELL_RC" ]; then
            if ! grep -q "AUTO_REFLECT" "$SHELL_RC" 2>/dev/null; then
                echo "" >> "$SHELL_RC"
                echo "# Auto-reflect for Claude Code" >> "$SHELL_RC"
                echo "export PYTHONPATH=\"$REPO_DIR:\${PYTHONPATH:-}\"" >> "$SHELL_RC"
                ok "Added PYTHONPATH to $SHELL_RC (restart shell or source it)"
            fi
        else
            warn "Add this to your shell profile:"
            echo "    export PYTHONPATH=\"$REPO_DIR:\${PYTHONPATH:-}\""
        fi
    fi
}

create_data_dirs() {
    info "Creating data directories..."
    mkdir -p "$AR_DIR"/{observations,patterns,improvements,baselines}
    ok "Data directories at $AR_DIR"
}

install_hook() {
    info "Installing SessionEnd hook..."

    # Make hook executable
    chmod +x "$REPO_DIR/hooks/auto-reflect.sh"

    # Create or update settings.json
    if [ ! -f "$SETTINGS" ]; then
        cat > "$SETTINGS" << 'SETTINGS_EOF'
{
  "hooks": {}
}
SETTINGS_EOF
    fi

    # Check if hook already exists
    if jq -e '.hooks.SessionEnd[]?.hooks[]? | select(.command | test("auto-reflect"))' "$SETTINGS" &>/dev/null; then
        ok "SessionEnd hook already configured"
        return
    fi

    # Add the hook entry
    HOOK_CMD="$REPO_DIR/hooks/auto-reflect.sh"
    TMP=$(mktemp)
    jq --arg cmd "$HOOK_CMD" '
        .hooks //= {} |
        .hooks.SessionEnd //= [] |
        .hooks.SessionEnd += [{
            "matcher": "",
            "hooks": [{"type": "command", "command": $cmd}]
        }]
    ' "$SETTINGS" > "$TMP" && mv "$TMP" "$SETTINGS"

    ok "SessionEnd hook installed → $HOOK_CMD"
}

install_command() {
    info "Installing /auto-reflect slash command..."
    mkdir -p "$CLAUDE_DIR/commands"
    cp "$REPO_DIR/commands/auto-reflect.md" "$CLAUDE_DIR/commands/auto-reflect.md"
    ok "/auto-reflect command available"
}

install_cron() {
    info "Installing cron job (every 6 hours)..."

    chmod +x "$REPO_DIR/cron/batch-catchup.sh"

    CRON_CMD="0 */6 * * * $REPO_DIR/cron/batch-catchup.sh"

    if crontab -l 2>/dev/null | grep -q "auto-reflect"; then
        ok "Cron job already exists"
        return
    fi

    (crontab -l 2>/dev/null; echo "$CRON_CMD  # auto-reflect catch-up") | crontab -
    ok "Cron job installed: every 6 hours"
}

# ─── Main ────────────────────────────────────────────────────────────────────

echo ""
echo "╔══════════════════════════════════════╗"
echo "║     Auto-Reflect for Claude Code     ║"
echo "║   Self-Improving Agent Feedback Loop ║"
echo "╚══════════════════════════════════════╝"
echo ""

# Handle flags
WITH_CRON=0
for arg in "$@"; do
    case $arg in
        --uninstall) uninstall ;;
        --with-cron) WITH_CRON=1 ;;
        --help|-h)
            echo "Usage: ./install.sh [--with-cron] [--uninstall]"
            echo ""
            echo "  --with-cron   Also install cron job for batch catch-up"
            echo "  --uninstall   Remove hooks, commands, and cron entries"
            exit 0
            ;;
    esac
done

# Run installation steps
check_prereqs
echo ""
install_package
create_data_dirs
install_hook
install_command

if [ "$WITH_CRON" -eq 1 ]; then
    install_cron
else
    echo ""
    info "Optional: install cron job for missed session catch-up:"
    echo "    ./install.sh --with-cron"
fi

# ─── Summary ─────────────────────────────────────────────────────────────────

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
ok "Installation complete!"
echo ""
echo "  How it works:"
echo "    1. Every session exit → auto-scored (background, non-blocking)"
echo "    2. Patterns detected across all sessions"
echo "    3. Concrete improvement proposals generated"
echo "    4. You approve or reject — nothing auto-applies"
echo ""
echo "  Try it:"
echo "    /auto-reflect              # in Claude Code"
echo "    python3 -m auto_reflect.orchestrate --status"
echo ""
echo "  Optional status line integration:"
echo "    Add to your ~/.claude/settings.json statusLine command:"
echo "    R:\$(${REPO_DIR}/hooks/reflect-status.sh)"
echo ""
echo "  Uninstall:"
echo "    ./install.sh --uninstall"
echo ""
