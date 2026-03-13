"""
Centralized configuration for auto-reflect.

All paths are configurable via environment variables with sensible defaults.
"""

import os

# Base directories
AUTO_REFLECT_DIR = os.environ.get(
    "AUTO_REFLECT_DIR",
    os.path.expanduser("~/.claude/auto-reflect"),
)
CLAUDE_DIR = os.environ.get(
    "CLAUDE_DIR",
    os.path.expanduser("~/.claude"),
)
SESSIONS_DIR = os.environ.get(
    "AUTO_REFLECT_SESSIONS_DIR",
    os.path.join(CLAUDE_DIR, "projects"),
)

# Data directories (under AUTO_REFLECT_DIR)
OBSERVATIONS_DIR = os.path.join(AUTO_REFLECT_DIR, "observations")
PATTERNS_DIR = os.path.join(AUTO_REFLECT_DIR, "patterns")
IMPROVEMENTS_DIR = os.path.join(AUTO_REFLECT_DIR, "improvements")
BASELINES_DIR = os.path.join(AUTO_REFLECT_DIR, "baselines")
DEEP_ANALYSIS_DIR = os.path.join(AUTO_REFLECT_DIR, "deep-analysis")

# Log files
GATE_LOG = os.path.join(AUTO_REFLECT_DIR, "gate-log.json")
PROPOSAL_HISTORY = os.path.join(AUTO_REFLECT_DIR, "proposal-history.json")
HISTORY_FILE = PROPOSAL_HISTORY  # Alias for backward compatibility
HOOK_LOG = os.path.join(AUTO_REFLECT_DIR, "hook-log.txt")

# Deep analysis
DEEP_ANALYSIS_DIR = os.path.join(AUTO_REFLECT_DIR, "deep-analysis")

# Eval gate paths (optional — only needed if you have skills with evals)
SKILLS_DIR = os.environ.get(
    "AUTO_REFLECT_SKILLS_DIR",
    os.path.join(CLAUDE_DIR, "skills"),
)
SKILLS_REPO_DIR = os.environ.get(
    "AUTO_REFLECT_SKILLS_REPO",
    os.path.join(CLAUDE_DIR, "skills-repo"),
)
EVAL_TOOLS_DIR = os.environ.get(
    "AUTO_REFLECT_EVAL_TOOLS",
    os.path.join(SKILLS_REPO_DIR, "_eval-tools"),
)

# Configurable thresholds
EXPIRE_DAYS = int(os.environ.get("AUTO_REFLECT_EXPIRE_DAYS", "7"))
PATTERN_MIN_OBSERVATIONS = int(os.environ.get("AUTO_REFLECT_MIN_OBS_FOR_PATTERNS", "20"))


# Rejection cache suppression window (days)
REJECTION_SUPPRESS_DAYS = int(os.environ.get("AUTO_REFLECT_REJECTION_DAYS", "30"))

# Deep analysis score threshold
DEFAULT_ANALYSIS_THRESHOLD = int(os.environ.get("AUTO_REFLECT_ANALYSIS_THRESHOLD", "85"))


def ensure_dirs():
    """Create all data directories if they don't exist."""
    for d in [OBSERVATIONS_DIR, PATTERNS_DIR, IMPROVEMENTS_DIR, BASELINES_DIR, DEEP_ANALYSIS_DIR]:
        os.makedirs(d, exist_ok=True)
