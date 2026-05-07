"""Empirical categorization of Kachaka error codes.

Two ranges with different semantics:

- ``2xxxx`` codes appear in ``get_error()`` and represent the **active state**
  of the robot. While present, the robot rejects new task commands.
- ``10xxx`` codes appear in ``get_last_command_result().error_code`` and
  describe **why the last command failed**. These are past-tense and do not
  block future commands once the upstream active state clears.

Verified live on robot BKP40HD1T (2026-05-07) and from the visual-patrol-v1.5
LiDAR incident.
"""

from __future__ import annotations

# ── Active state codes (errors[]) ────────────────────────────────────

PAUSED_CODE = 21051
"""Latched pause from set_emergency_stop() or physical power button.
Cannot be cleared via gRPC — only by pressing the physical power button."""

HARDWARE_FATAL_CODES = frozenset({
    21004,  # LiDAR / laser hardware error
})
"""Hardware faults that ``restart_robot()`` is known to clear."""


# ── Past-tense codes (last_command_result.error_code) ─────────────────

TASK_BLOCKED_CODES = frozenset({
    10107,  # New command rejected because Kachaka is paused
    10264,  # Cannot execute tasks because of a fatal error
    10105,  # In-flight task cancelled by pause button
})
"""Last-command failures whose root cause is an active state code.
Resolve the upstream code (in ``errors[]``) and these stop appearing."""

NORMAL_CANCEL_CODES = frozenset({
    10001,  # Action interrupted (generic cancel)
})


def categorize_active_errors(errors: list[int]) -> str | None:
    """Classify the contents of ``get_error()`` into a coarse category.

    Returns ``None`` when ``errors`` is empty (= robot healthy).
    """
    if not errors:
        return None
    if PAUSED_CODE in errors:
        return "paused"
    if any(c in HARDWARE_FATAL_CODES for c in errors):
        return "hardware_fatal"
    return "unknown"


def recovery_hint(errors: list[int]) -> str | None:
    """Suggest how to clear the current active state.

    Returns ``None`` when no recovery is needed.
    """
    if not errors:
        return None
    if PAUSED_CODE in errors:
        return "press_power_button"
    if any(c in HARDWARE_FATAL_CODES for c in errors):
        return "restart_robot"
    return "manual_check"
