"""Empirical categorization of Kachaka error codes.

Two ranges with different semantics:

- ``2xxxx`` codes appear in ``get_error()`` and represent the **active state**
  of the robot. While present, the robot rejects new task commands.
- ``10xxx`` codes appear in ``get_last_command_result().error_code`` and
  describe **why the last command failed**. These are past-tense and do not
  block future commands once the upstream active state clears.

Verified live on robot BKP40HD1T (2026-05-07) and from the visual-patrol-v1.5
LiDAR incident. Titles cross-checked (2026-08-13) against the robot's own
master table — 860 entries fetched via ``GetRobotErrorCodeJson``. That table
is the authority and is available at runtime through
``KachakaQueries.get_error_definitions()``; the constants below only carve out
the handful of codes the toolkit needs to *act* on, so there is deliberately
no attempt to mirror all 860 here.
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
    10001,  # Action interrupted (generic cancel) — what cancel_command() yields
})

TASK_FAILURE_CODES = frozenset({
    11005,  # {shelf} is not found — not where Kachaka last placed it
    14606,  # Kachaka is not docked with a furniture
    19001,  # Failed to move to the destination (obstacle / timeout too short)
})
"""Genuine task failures: the robot was healthy and accepted the command, but
the world did not cooperate. Unlike ``TASK_BLOCKED_CODES`` there is no active
state code to clear — the caller must fix the physical situation (put the
shelf back, clear the obstacle) or retry. Seen in production on bio-patrol and
visual-patrol deployments."""


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
