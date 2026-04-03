import os
import re

# ==========================================
# PATHS
# ==========================================
PATH_PLANS = "./plans"
PATH_ARTIFACTS = "./.artifacts"
PATH_LOGS = "./.logs"

# ==========================================
# EXECUTION SETTINGS
# ==========================================
N_SUB_AGENTS = 3   # Maximum number of independent agents running at once
N_MAX_LOOPS = 5    # Maximum execution loops per phase before forcing a halt
MAX_TURNS = "15"   # Max autonomous tool loops Claude can take per session

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
