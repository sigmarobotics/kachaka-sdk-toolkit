"""Unified error handling and retry logic for Kachaka gRPC operations.

Patterns extracted from bio-patrol's retry_with_backoff() and
visual-patrol's structured error responses.
"""

from __future__ import annotations

import functools
import logging
import time

import grpc

logger = logging.getLogger(__name__)

# gRPC status codes that are safe to retry (transient network issues)
RETRYABLE_CODES = {
    grpc.StatusCode.UNAVAILABLE,
    grpc.StatusCode.DEADLINE_EXCEEDED,
    grpc.StatusCode.RESOURCE_EXHAUSTED,
}


def with_retry(
    max_attempts: int = 3,
    base_delay: float = 1.0,
    max_delay: float = 10.0,
    deadline: float | None = None,
):
    """Exponential-backoff retry decorator for gRPC operations.

    Only retries on transient network errors (UNAVAILABLE, DEADLINE_EXCEEDED,
    RESOURCE_EXHAUSTED). Business-logic errors (INVALID_ARGUMENT, NOT_FOUND,
    etc.) fail immediately.

    Two modes:

    - **Count mode** (default): retry up to *max_attempts* times.
    - **Deadline mode**: when *deadline* is set (seconds), retry until the
      wall-clock deadline is reached, ignoring *max_attempts*.  Backoff
      sleep is capped by the remaining time so the function never sleeps
      past the deadline.

    Args:
        max_attempts: Total attempts (count mode only).
        base_delay: Initial delay in seconds before first retry.
        max_delay: Cap on delay between retries.
        deadline: Maximum total seconds to keep retrying.  When set,
            *max_attempts* is ignored.

    Returns:
        dict with ``ok`` key. On failure includes ``error``, ``retryable``,
        and ``attempts`` fields.
    """

    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            last_error: Exception | None = None
            attempt = 0
            abs_deadline = (
                time.perf_counter() + deadline if deadline is not None else None
            )

            while True:
                # Check if we should stop
                if abs_deadline is not None:
                    if attempt > 0 and time.perf_counter() >= abs_deadline:
                        break
                else:
                    if attempt >= max_attempts:
                        break

                attempt += 1
                try:
                    return func(*args, **kwargs)
                except grpc.RpcError as exc:
                    last_error = exc
                    code = exc.code()
                    details = exc.details() or ""
                    if code not in RETRYABLE_CODES:
                        logger.warning(
                            "gRPC non-retryable %s: %s", code.name, details
                        )
                        return {
                            "ok": False,
                            "error": f"{code.name}: {details}",
                            "retryable": False,
                        }

                    # Calculate backoff delay
                    delay = min(base_delay * (2 ** (attempt - 1)), max_delay)
                    if abs_deadline is not None:
                        remaining = abs_deadline - time.perf_counter()
                        if remaining <= 0:
                            break
                        delay = min(delay, remaining)

                    logger.info(
                        "gRPC %s, retrying in %.1fs (attempt %d%s)",
                        code.name,
                        delay,
                        attempt,
                        f", deadline in {abs_deadline - time.perf_counter():.1f}s"
                        if abs_deadline is not None
                        else f"/{max_attempts}",
                    )
                    time.sleep(delay)
                except Exception as exc:
                    logger.error("Unexpected error in %s: %s", func.__name__, exc)
                    return {"ok": False, "error": str(exc), "retryable": False}

            # All retries exhausted
            return {
                "ok": False,
                "error": str(last_error),
                "retryable": True,
                "attempts": attempt,
            }

        return wrapper

    return decorator
