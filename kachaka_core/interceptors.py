"""gRPC interceptors for Kachaka connections.

The kachaka_api SDK does not set per-call timeouts, which means gRPC calls
can block indefinitely during server-side disconnects (e.g. robot WiFi drop).
TimeoutInterceptor adds a default timeout to every unary-unary call to
prevent thread deadlock.
"""

from __future__ import annotations

import grpc


class _CallDetails(grpc.ClientCallDetails):
    """Writable ClientCallDetails (the base class attrs are read-only)."""

    def __init__(
        self,
        method: str,
        timeout: float | None,
        metadata,
        credentials,
        wait_for_ready,
        compression,
    ):
        self.method = method
        self.timeout = timeout
        self.metadata = metadata
        self.credentials = credentials
        self.wait_for_ready = wait_for_ready
        self.compression = compression


class TimeoutInterceptor(grpc.UnaryUnaryClientInterceptor):
    """Add a default timeout to all unary-unary gRPC calls.

    If the call already has an explicit timeout, it is left unchanged.

    Long-poll detection is **cursor-based**: a request whose
    ``metadata.cursor != 0`` asks the server to hold the call until new
    data arrives, so it gets ``long_poll_timeout`` — a bounded watchdog
    against silently wedged streams (a lost completion event once hung a
    production client for 82 minutes; see .sigma incident 2026-05-18).
    Everything else — cursor==0 immediate reads, StartCommand, and all
    other RPCs — gets ``default_timeout``.
    """

    def __init__(self, default_timeout: float = 10.0, long_poll_timeout: float = 300.0):
        self._default_timeout = default_timeout
        self._long_poll_timeout = long_poll_timeout

    def intercept_unary_unary(self, continuation, client_call_details, request):
        if client_call_details.timeout is not None:
            return continuation(client_call_details, request)
        cursor = getattr(getattr(request, "metadata", None), "cursor", 0)
        new_details = _CallDetails(
            method=client_call_details.method,
            timeout=self._long_poll_timeout if cursor else self._default_timeout,
            metadata=client_call_details.metadata,
            credentials=client_call_details.credentials,
            wait_for_ready=client_call_details.wait_for_ready,
            compression=client_call_details.compression,
        )
        return continuation(new_details, request)
