"""HIL-1: disconnect detection & long-poll watchdog against the real robot.

Run:
    pytest tests/hil/ -v --robot-ip=192.168.50.133

The blackhole proxy simulates a *silent* network drop (no RST/FIN) — the
failure mode that caused the 2026-05-18 production hang. The robot itself
never moves in this file.
"""

from __future__ import annotations

import time

import grpc
import pytest
from kachaka_api.generated import kachaka_api_pb2 as pb2

from kachaka_core.connection import ConnectionState, KachakaConnection

pytestmark = pytest.mark.hil


def _capture_cursor(stub) -> int:
    resp = stub.GetCommandState(pb2.GetRequest(metadata=pb2.Metadata(cursor=0)))
    return resp.metadata.cursor


class TestSilentDropDetection:
    def test_monitoring_detects_silent_drop_and_recovery(self, blackhole_proxy):
        """HIL-1.1: auto-monitoring flags DISCONNECTED within interval+timeout,
        and flips back to CONNECTED after the network returns."""
        conn = KachakaConnection.get(
            blackhole_proxy.target, timeout=2.0, monitor_interval=1.0
        )
        assert conn.wait_for_state(ConnectionState.CONNECTED, timeout=10.0)

        blackhole_proxy.blackhole()
        t0 = time.monotonic()
        assert conn.wait_for_state(ConnectionState.DISCONNECTED, timeout=10.0), (
            "monitoring failed to detect silent drop"
        )
        detect_s = time.monotonic() - t0
        # interval (1s) + ping timeout (2s) + margin
        assert detect_s < 6.0, f"detection took {detect_s:.1f}s"

        blackhole_proxy.restore()
        assert conn.wait_for_state(ConnectionState.CONNECTED, timeout=10.0), (
            "monitoring failed to detect recovery"
        )

    def test_in_flight_long_poll_bounded_by_long_poll_timeout(self, blackhole_proxy):
        """HIL-1.2: a server-held long-poll wedged by a silent drop returns
        within long_poll_timeout instead of hanging forever (CORNER-002)."""
        conn = KachakaConnection.get(
            blackhole_proxy.target, timeout=2.0, long_poll_timeout=6.0, monitor=False
        )
        stub = conn.client.stub
        cursor = _capture_cursor(stub)

        blackhole_proxy.blackhole()
        t0 = time.monotonic()
        with pytest.raises(grpc.RpcError) as excinfo:
            stub.GetCommandState(pb2.GetRequest(metadata=pb2.Metadata(cursor=cursor)))
        elapsed = time.monotonic() - t0

        assert elapsed < 12.0, f"long-poll hung {elapsed:.1f}s (watchdog failed)"
        assert excinfo.value.code() in (
            grpc.StatusCode.DEADLINE_EXCEEDED,
            grpc.StatusCode.UNAVAILABLE,
        )

    def test_keepalive_kills_wedged_call_before_deadline(self, blackhole_proxy):
        """HIL-1.3: with a long deadline (120s), HTTP/2 keepalive detects the
        dead transport and fails the in-flight call in ~keepalive_time+timeout."""
        conn = KachakaConnection.get(
            blackhole_proxy.target, timeout=2.0, long_poll_timeout=120.0, monitor=False
        )
        stub = conn.client.stub
        cursor = _capture_cursor(stub)

        blackhole_proxy.blackhole()
        t0 = time.monotonic()
        with pytest.raises(grpc.RpcError) as excinfo:
            stub.GetCommandState(pb2.GetRequest(metadata=pb2.Metadata(cursor=cursor)))
        elapsed = time.monotonic() - t0

        # Measured on macOS: the keepalive-armed transport kills the wedged
        # call at a flat 60s regardless of keepalive_time (control without
        # keepalive hangs the full deadline).  On Linux, TCP_USER_TIMEOUT
        # (= keepalive_timeout) applies and detection is faster (~20s).
        assert elapsed < 70.0, (
            f"keepalive did not fire — call held {elapsed:.1f}s "
            f"(code={excinfo.value.code().name})"
        )
        assert excinfo.value.code() == grpc.StatusCode.UNAVAILABLE


class TestKeepaliveTolerance:
    @pytest.mark.slow
    def test_healthy_quiet_long_poll_not_broken_by_keepalive(self, real_conn):
        """HIL-1.4 (CORNER-004): keepalive must not break healthy connections.

        Measured robot behaviour (firmware 3.16.x, with AND without
        keepalive): the server cancels a held long-poll after ~35–40s
        (its own long-poll lifecycle), surfacing as CANCELLED from peer.
        Acceptable ends: server CANCELLED, our DEADLINE_EXCEEDED, or a
        normal return.  A transport-level UNAVAILABLE (GOAWAY /
        too_many_pings) would mean our keepalive cadence is abusive.
        """
        stub = real_conn.client.stub
        cursor = _capture_cursor(stub)

        t0 = time.monotonic()
        try:
            stub.GetCommandState(
                pb2.GetRequest(metadata=pb2.Metadata(cursor=cursor)), timeout=40.0
            )
            # Robot state changed during the wait — call returned early. OK.
        except grpc.RpcError as exc:
            elapsed = time.monotonic() - t0
            assert exc.code() in (
                grpc.StatusCode.CANCELLED,        # server long-poll lifecycle
                grpc.StatusCode.DEADLINE_EXCEEDED,  # our 40s deadline
            ), (
                f"transport error after {elapsed:.1f}s: {exc.code().name} "
                "(GOAWAY/too_many_pings suspected — keepalive too aggressive)"
            )


class TestConnectionStateSurfacing:
    def test_auto_monitoring_reports_connected(self, robot_ip):
        """HIL-1.5: plain get() with no extra setup reports real state."""
        conn = KachakaConnection.get(robot_ip)
        assert conn.wait_until_known(timeout=10.0)
        assert conn.state == ConnectionState.CONNECTED

    def test_mcp_get_connection_state(self, robot_ip):
        """HIL-1.6: the MCP tool surfaces connected state + ping age."""
        from mcp_server.server import get_connection_state

        result = get_connection_state(robot_ip)
        assert result["ok"] is True
        assert result["state"] == "connected"
        assert result["monitoring"] is True
        assert result["last_ok_ping_ago_s"] is not None
