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

# Memory directory (project-scoped — set this to your project's memory path)
MEMORY_DIR = os.environ.get(
    "AUTO_REFLECT_MEMORY_DIR",
    "",  # No default — varies by project working directory
)
AGENTS_DIR = os.path.join(CLAUDE_DIR, "agents")

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

# ---------------------------------------------------------------------------
# Tunable thresholds — all magic numbers in one place
# Override any via environment variables.
# ---------------------------------------------------------------------------

# Text similarity
CORRECTION_CLUSTER_SIMILARITY = float(os.environ.get("AUTO_REFLECT_CLUSTER_SIMILARITY", "0.35"))
RULE_MATCH_SIMILARITY = float(os.environ.get("AUTO_REFLECT_RULE_SIMILARITY", "0.40"))

# Proposal generation minimums
MIN_ERROR_CATEGORY_COUNT = int(os.environ.get("AUTO_REFLECT_MIN_ERROR_COUNT", "3"))
MIN_CORRECTION_CLUSTER_SESSIONS = int(os.environ.get("AUTO_REFLECT_MIN_CLUSTER_SESSIONS", "3"))
HIGH_PRIORITY_CLUSTER_SESSIONS = int(os.environ.get("AUTO_REFLECT_HIGH_PRIORITY_SESSIONS", "5"))
MIN_AGENT_USES = int(os.environ.get("AUTO_REFLECT_MIN_AGENT_USES", "3"))
AGENT_ERROR_RATE_THRESHOLD = float(os.environ.get("AUTO_REFLECT_AGENT_ERROR_RATE", "0.30"))
MIN_SESSIONS_FOR_UNUSED_AGENTS = int(os.environ.get("AUTO_REFLECT_MIN_SESSIONS_UNUSED_AGENTS", "50"))
MIN_RULE_VIOLATIONS = int(os.environ.get("AUTO_REFLECT_MIN_RULE_VIOLATIONS", "3"))

# Memory cleanup
MEMORY_STALENESS_DAYS = int(os.environ.get("AUTO_REFLECT_MEMORY_STALENESS_DAYS", "30"))
PAST_DATE_THRESHOLD_DAYS = int(os.environ.get("AUTO_REFLECT_PAST_DATE_DAYS", "14"))

# Effectiveness tracking
EFFECTIVENESS_REVIEW_WINDOW = int(os.environ.get("AUTO_REFLECT_REVIEW_WINDOW", "30"))
EFFECTIVENESS_IMPROVEMENT_THRESHOLD = float(os.environ.get("AUTO_REFLECT_IMPROVEMENT_THRESHOLD", "0.15"))
EFFECTIVENESS_REGRESSION_THRESHOLD = float(os.environ.get("AUTO_REFLECT_REGRESSION_THRESHOLD", "-0.10"))


def ensure_dirs():
    """Create all data directories if they don't exist."""
    for d in [OBSERVATIONS_DIR, PATTERNS_DIR, IMPROVEMENTS_DIR, BASELINES_DIR, DEEP_ANALYSIS_DIR]:
        os.makedirs(d, exist_ok=True)
