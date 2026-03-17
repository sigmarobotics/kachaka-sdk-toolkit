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
    Long-polling RPCs used by the SDK's blocking command flow are given a
    much longer timeout so they can wait for robot movements to complete.
    """

    # RPCs that block until a robot command finishes (long-polling).
    # These must NOT have a timeout — movement distance is unbounded.
    _LONG_POLL_METHODS = frozenset({
        "/kachaka_api.KachakaApi/StartCommand",
        "/kachaka_api.KachakaApi/GetLastCommandResult",
        "/kachaka_api.KachakaApi/GetCommandState",
    })

    def __init__(self, default_timeout: float = 10.0):
        self._default_timeout = default_timeout

    def intercept_unary_unary(self, continuation, client_call_details, request):
        if client_call_details.timeout is None:
            method = client_call_details.method
            if method in self._LONG_POLL_METHODS:
                return continuation(client_call_details, request)
            new_details = _CallDetails(
                method=client_call_details.method,
                timeout=self._default_timeout,
                metadata=client_call_details.metadata,
                credentials=client_call_details.credentials,
                wait_for_ready=client_call_details.wait_for_ready,
                compression=client_call_details.compression,
            )
            return continuation(new_details, request)
        return continuation(client_call_details, request)
