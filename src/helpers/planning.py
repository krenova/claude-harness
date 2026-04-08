import json
import os


# ==========================================
# PLANNING STATE HELPERS
# ==========================================
# Used exclusively by src/workflows/plan_refinement.py
# ==========================================

def load_planning_state(state_file: str) -> dict | None:
    """Return parsed state dict or None if file absent/corrupt."""
    if not os.path.exists(state_file):
        return None
    try:
        with open(state_file, "r") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return None


def save_planning_state(state_file: str, status: str, iteration: int) -> None:
    """Write planning_state.json atomically."""
    tmp = state_file + ".tmp"
    with open(tmp, "w") as f:
        json.dump({"status": status, "iteration": iteration}, f)
    os.replace(tmp, state_file)
