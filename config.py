import os
import re

# ==========================================
# PATHS
# ==========================================
PATH_PLANS = "./plans"
PATH_ARTIFACTS = "./.artifacts"
PATH_LOGS = "./.logs"
PATH_ARCHIVED_MEMORY = "./.archived_memory"
PATH_ARCHIVED_ARTIFACTS = "./.archived_artifacts"
PATH_IMPLEMENTATIONS    = "./.implementations"

PLANNING_MEMORY_FILE = f"{PATH_PLANS}/planning_memory.md"
PLANNING_STATE_FILE = f"{PATH_PLANS}/planning_state.json"
RISK_ASSESSMENT_FILE = f"{PATH_PLANS}/risk_assessment.md"
HUMAN_FEEDBACK_FILE = f"{PATH_PLANS}/human_feedback.md"
EXECUTION_STATE_FILE    = f"{PATH_PLANS}/execution_state.json"
EXECUTION_FEEDBACK_FILE = f"{PATH_PLANS}/execution_feedback.md"

# ==========================================
# MODEL SETTINGS
# ==========================================
MODEL_ORCHESTRATOR = "claude-sonnet-4-6"        # Heavy reasoning tasks
MODEL_UTILITY = "claude-haiku-4-5"     # Cheap tasks (memory updates, summaries)

# ==========================================
# EXECUTION SETTINGS
# ==========================================
N_SUB_AGENTS = 1   # Maximum number of independent agents running at once
N_MAX_LOOPS = 3    # Maximum execution loops per phase before forcing a halt
MAX_TURNS = "15"   # Max autonomous tool loops Claude can take per session

# ==========================================
# RATE LIMITER SETTINGS
# ==========================================
HOURLY_CALL_LIMIT = 20          # Max API calls per UTC hour before cooldown
RATE_LIMIT_COOLDOWN_SECONDS = 3600  # Cooldown duration when limit is hit (seconds)
RATE_LIMITER_STATE_FILE = ".artifacts/rate_limiter_state.json"

# AMA_UNATTENDED=1 skips HITL prompts in the execution phase and auto-waits
# on rate-limit / circuit-breaker events.  Planning phase always requires
# human approval regardless of this flag.
UNATTENDED_MODE = os.environ.get("AMA_UNATTENDED", "0") == "1"

# ==========================================
# INTERACTIVE PROMPT INTERCEPT
# ==========================================
INTERACTIVE_PROMPT_PATTERNS = [
    re.compile(r"\(yes/no(/\[fingerprint\])?\)\s*\??$", re.IGNORECASE),
    re.compile(r"password\s*:", re.IGNORECASE),
    re.compile(r"enter passphrase", re.IGNORECASE),
    re.compile(r"please type 'yes', 'no' or the fingerprint", re.IGNORECASE),
]

# Default answers injected when a matching prompt is detected in UNATTENDED_MODE.
# One entry per pattern above (index-aligned).
UNATTENDED_DEFAULTS = ["yes", "yes", "yes", "yes"]

# ==========================================
# RUNTIME CONFIG
# ==========================================
import dataclasses

@dataclasses.dataclass
class RuntimeConfig:
    n_sub_agents: int = N_SUB_AGENTS
    n_max_loops: int = N_MAX_LOOPS
    max_turns: str = MAX_TURNS
    unattended_mode: bool = UNATTENDED_MODE
    hourly_call_limit: int = HOURLY_CALL_LIMIT
