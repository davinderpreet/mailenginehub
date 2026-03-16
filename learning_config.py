"""
learning_config.py — Shared configuration for the self-learning layer.
All learning components import from here. Config is DB-backed via LearningConfig
table, so changes take effect without service restarts.
"""

import logging
from datetime import datetime

logger = logging.getLogger(__name__)


def get_learning_enabled():
    """Check the emergency kill switch. Returns False to skip all learning."""
    from database import LearningConfig
    val = LearningConfig.get_val("learning_enabled", "true")
    return val.lower() == "true"


def set_learning_enabled(enabled):
    """Set or clear the kill switch."""
    from database import LearningConfig
    LearningConfig.set_val("learning_enabled", str(enabled).lower())


def get_learning_phase():
    """
    Determine current learning phase based on time and data quality gates.
    Returns: 'observation', 'conservative', or 'active'
    """
    from database import LearningConfig, OutcomeLog

    # Check for manual override (e.g., from regression detection)
    override = LearningConfig.get_val("learning_phase_override", "")
    if override in ("observation", "conservative", "active"):
        return override

    # Check start date
    start_str = LearningConfig.get_val("learning_start_date", "")
    if not start_str:
        return "observation"

    try:
        start = datetime.fromisoformat(start_str)
    except ValueError:
        return "observation"

    days = (datetime.now() - start).days
    outcome_count = OutcomeLog.select().count()
    purchase_count = OutcomeLog.select().where(OutcomeLog.purchased == True).count()

    if days < 30 or outcome_count < 500:
        return "observation"
    elif days < 60 or purchase_count < 20:
        return "conservative"
    return "active"


def set_learning_phase_override(phase):
    """Force a specific learning phase (e.g., for regression rollback)."""
    from database import LearningConfig
    LearningConfig.set_val("learning_phase_override", phase)


def init_learning_config():
    """Seed default config values if not present."""
    from database import LearningConfig
    if not LearningConfig.get_val("learning_start_date"):
        LearningConfig.set_val("learning_start_date", datetime.now().isoformat())
    if not LearningConfig.get_val("learning_enabled"):
        LearningConfig.set_val("learning_enabled", "true")
