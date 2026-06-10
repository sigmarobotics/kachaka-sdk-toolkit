"""Tests for kachaka_core.connection — pool, normalisation, ping, monitoring."""

from __future__ import annotations

import threading
import time
from unittest.mock import MagicMock, patch

import grpc
import pytest

from kachaka_core.connection import ConnectionState, KachakaConnection


@pytest.fixture(autouse=True)
def _clean_pool():
    """Ensure each test starts with an empty connection pool."""
    KachakaConnection.clear_pool()
    yield
    KachakaConnection.clear_pool()


class TestNormaliseTarget:
    def test_adds_default_port(self):
        assert KachakaConnection._normalise_target("192.168.1.1") == "192.168.1.1:26400"

    def test_preserves_explicit_port(self):
        assert KachakaConnection._normalise_target("10.0.0.1:9999") == "10.0.0.1:9999"

    def test_mdns_hostname(self):
        assert KachakaConnection._normalise_target("kachaka-abc.local") == "kachaka-abc.local:26400"


class TestChannelKeepalive:
    """The gRPC channel must carry HTTP/2 keepalive options.

    Without keepalive, a silent network drop (no TCP RST — e.g. WiFi
    vanishing) leaves in-flight RPCs hanging until TCP retransmission
    gives up (15–18 minutes measured).
    """

    @patch("kachaka_core.connection.KachakaApiClient")
    def test_channel_created_with_keepalive_options(self, mock_cls):
        mock_cls.return_value = MagicMock()
        with patch(
            "kachaka_core.connection.grpc.insecure_channel",
            wraps=grpc.insecure_channel,
        ) as mock_chan:
            KachakaConnection.get("keepalive-test")

        assert mock_chan.call_count == 1
        args, kwargs = mock_chan.call_args
        options = dict(kwargs.get("options") or (args[1] if len(args) > 1 else []))
        assert "grpc.keepalive_time_ms" in options
        assert "grpc.keepalive_timeout_ms" in options
        # Pings must keep flowing during a silent long-poll (no data frames),
        # otherwise keepalive stops exactly when we need it.
        assert options.get("grpc.http2.max_pings_without_data") == 0
        # Pings must also flow when no call is in flight — short-deadline
        # health pings fail fast and leave the channel idle, so without
        # this a zombie transport is never declared dead and the channel
        # never re-dials (E2E phase C regression).
        assert options.get("grpc.keepalive_permit_without_calls") == 1


class TestPool:
    @patch("kachaka_core.connection.KachakaApiClient")
    def test_same_ip_returns_same_instance(self, mock_cls):
        mock_cls.return_value = MagicMock()
        a = KachakaConnection.get("1.2.3.4")
        b = KachakaConnection.get("1.2.3.4")
        assert a is b

    @patch("kachaka_core.connection.KachakaApiClient")
    def test_different_ip_returns_different(self, mock_cls):
        mock_cls.return_value = MagicMock()
        a = KachakaConnection.get("1.2.3.4")
        b = KachakaConnection.get("5.6.7.8")
        assert a is not b

    @patch("kachaka_core.connection.KachakaApiClient")
    def test_port_normalised_for_pool_key(self, mock_cls):
        mock_cls.return_value = MagicMock()
        a = KachakaConnection.get("1.2.3.4")
        b = KachakaConnection.get("1.2.3.4:26400")
        assert a is b

    @patch("kachaka_core.connection.KachakaApiClient")
    def test_remove(self, mock_cls):
        mock_cls.return_value = MagicMock()
        KachakaConnection.get("1.2.3.4")
        KachakaConnection.remove("1.2.3.4")
        assert "1.2.3.4:26400" not in KachakaConnection._pool

    @patch("kachaka_core.connection.KachakaApiClient")
    def test_clear_pool(self, mock_cls):
        mock_cls.return_value = MagicMock()
        KachakaConnection.get("1.2.3.4")
        KachakaConnection.get("5.6.7.8")
        KachakaConnection.clear_pool()
        assert len(KachakaConnection._pool) == 0


class TestPing:
    @patch("kachaka_core.connection.KachakaApiClient")
    def test_ping_success(self, mock_cls):
        mock_client = MagicMock()
        mock_client.get_robot_serial_number.return_value = "KCK-001"
        mock_pose = MagicMock(x=1.0, y=2.0, theta=0.5)
        mock_client.get_robot_pose.return_value = mock_pose
        mock_cls.return_value = mock_client

        conn = KachakaConnection.get("1.2.3.4")
        result = conn.ping()

        assert result["ok"] is True
        assert result["serial"] == "KCK-001"
        assert result["pose"] == {"x": 1.0, "y": 2.0, "theta": 0.5}

    @patch("kachaka_core.connection.KachakaApiClient")
    def test_ping_grpc_error(self, mock_cls):
        import grpc

        mock_client = MagicMock()
        mock_client.get_robot_serial_number.return_value = "KCK-001"
        rpc_error = grpc.RpcError()
        rpc_error.code = lambda: grpc.StatusCode.UNAVAILABLE
        rpc_error.details = lambda: "Connection refused"
        mock_client.get_robot_pose.side_effect = rpc_error
        mock_cls.return_value = mock_client

        conn = KachakaConnection.get("1.2.3.4")
        result = conn.ping()

        assert result["ok"] is False
        assert "UNAVAILABLE" in result["error"]


class TestResolver:
    @patch("kachaka_core.connection.KachakaApiClient")
    def test_ensure_resolver_fetches_shelves_and_locations(self, mock_cls):
        mock_client = MagicMock()
        mock_cls.return_value = mock_client

        conn = KachakaConnection.get("1.2.3.4")
        conn.ensure_resolver()

        mock_client.get_shelves.assert_called_once()
        mock_client.get_locations.assert_called_once()
        mock_client.update_resolver.assert_not_called()

    @patch("kachaka_core.connection.KachakaApiClient")
    def test_ensure_resolver_idempotent(self, mock_cls):
        mock_client = MagicMock()
        mock_cls.return_value = mock_client

        conn = KachakaConnection.get("1.2.3.4")
        conn.ensure_resolver()
        conn.ensure_resolver()

        # Only called once because _resolver_ready is cached
        mock_client.get_shelves.assert_called_once()

    @patch("kachaka_core.connection.KachakaApiClient")
    def test_resolve_shelf_by_name_and_id(self, mock_cls):
        mock_client = MagicMock()
        shelf = MagicMock()
        shelf.name = "ShelfA"
        shelf.id = "S01"
        mock_client.get_shelves.return_value = [shelf]
        mock_client.get_locations.return_value = []
        mock_cls.return_value = mock_client

        conn = KachakaConnection.get("1.2.3.4")
        conn.ensure_resolver()

        assert conn.resolve_shelf("ShelfA") == "S01"
        assert conn.resolve_shelf("S01") == "S01"
        assert conn.resolve_shelf("unknown") == "unknown"

    @patch("kachaka_core.connection.KachakaApiClient")
    def test_resolve_location_by_name_and_id(self, mock_cls):
        mock_client = MagicMock()
        loc = MagicMock()
        loc.name = "Kitchen"
        loc.id = "L01"
        mock_client.get_shelves.return_value = []
        mock_client.get_locations.return_value = [loc]
        mock_cls.return_value = mock_client

        conn = KachakaConnection.get("1.2.3.4")
        conn.ensure_resolver()

        assert conn.resolve_location("Kitchen") == "L01"
        assert conn.resolve_location("L01") == "L01"
        assert conn.resolve_location("unknown") == "unknown"


def _healthy_mock_client():
    mock_client = MagicMock()
    mock_client.get_robot_serial_number.return_value = "KCK-001"
    mock_client.get_robot_pose.return_value = MagicMock(x=0, y=0, theta=0)
    return mock_client


class TestUnknownStateAndAutoMonitor:
    """state must never lie: UNKNOWN until a ping proves otherwise, and
    ``get()`` starts monitoring by default so naive callers see real state."""

    @patch("kachaka_core.connection.KachakaApiClient")
    def test_state_unknown_without_monitoring(self, mock_cls):
        mock_cls.return_value = _healthy_mock_client()
        conn = KachakaConnection.get("1.2.3.4", monitor=False)
        assert conn.state == ConnectionState.UNKNOWN

    @patch("kachaka_core.connection.KachakaApiClient")
    def test_get_auto_starts_monitoring(self, mock_cls):
        mock_cls.return_value = _healthy_mock_client()
        conn = KachakaConnection.get("1.2.3.4")
        assert conn._is_monitoring
        assert conn.wait_for_state(ConnectionState.CONNECTED, timeout=2.0)

    @patch("kachaka_core.connection.KachakaApiClient")
    def test_monitor_false_opts_out(self, mock_cls):
        mock_cls.return_value = _healthy_mock_client()
        conn = KachakaConnection.get("1.2.3.4", monitor=False)
        assert conn._monitor_thread is None

    @patch("kachaka_core.connection.KachakaApiClient")
    def test_first_ping_is_immediate(self, mock_cls):
        """The health loop must ping at start, not after the first interval."""
        mock_cls.return_value = _healthy_mock_client()
        conn = KachakaConnection.get("1.2.3.4", monitor=False)
        conn.start_monitoring(interval=60.0)
        try:
            assert conn.wait_for_state(ConnectionState.CONNECTED, timeout=2.0)
        finally:
            conn.stop_monitoring()

    @patch("kachaka_core.connection.KachakaApiClient")
    def test_start_monitoring_updates_callback_when_running(self, mock_cls):
        """RobotController.start() wires its callback AFTER auto-monitoring
        has begun — the late callback must still be registered."""
        mock_client = _healthy_mock_client()
        mock_cls.return_value = mock_client
        conn = KachakaConnection.get("1.2.3.4")  # auto-monitoring, no callback
        conn.wait_for_state(ConnectionState.CONNECTED, timeout=2.0)

        transitions = []
        conn.start_monitoring(interval=0.05, on_state_change=transitions.append)
        try:
            rpc_error = grpc.RpcError()
            rpc_error.code = lambda: grpc.StatusCode.UNAVAILABLE
            rpc_error.details = lambda: "gone"
            mock_client.get_robot_serial_number.side_effect = rpc_error
            assert conn.wait_for_state(ConnectionState.DISCONNECTED, timeout=2.0)
            assert ConnectionState.DISCONNECTED in transitions
        finally:
            conn.stop_monitoring()

    @patch("kachaka_core.connection.KachakaApiClient")
    def test_connection_info_snapshot(self, mock_cls):
        mock_cls.return_value = _healthy_mock_client()
        conn = KachakaConnection.get("1.2.3.4")
        conn.wait_for_state(ConnectionState.CONNECTED, timeout=2.0)
        info = conn.connection_info()
        assert info["target"] == "1.2.3.4:26400"
        assert info["state"] == "connected"
        assert info["monitoring"] is True
        assert info["last_ok_ping_ago_s"] is not None
        assert info["last_ok_ping_ago_s"] < 5.0

    @patch("kachaka_core.connection.KachakaApiClient")
    def test_connection_info_unknown_before_monitoring(self, mock_cls):
        mock_cls.return_value = _healthy_mock_client()
        conn = KachakaConnection.get("1.2.3.4", monitor=False)
        info = conn.connection_info()
        assert info["state"] == "unknown"
        assert info["monitoring"] is False
        assert info["last_ok_ping_ago_s"] is None


class TestMonitoring:

    @patch("kachaka_core.connection.KachakaApiClient")
    def test_monitoring_detects_disconnect(self, mock_cls):
        mock_client = MagicMock()
        mock_cls.return_value = mock_client

        conn = KachakaConnection.get("1.2.3.4")
        # Make ping fail
        rpc_error = grpc.RpcError()
        rpc_error.code = lambda: grpc.StatusCode.UNAVAILABLE
        rpc_error.details = lambda: "gone"
        mock_client.get_robot_serial_number.side_effect = rpc_error

        conn.start_monitoring(interval=0.05)
        try:
            reached = conn.wait_for_state(ConnectionState.DISCONNECTED, timeout=2.0)
            assert reached
            assert conn.state == ConnectionState.DISCONNECTED
        finally:
            conn.stop_monitoring()

    @patch("kachaka_core.connection.KachakaApiClient")
    def test_monitoring_detects_reconnect(self, mock_cls):
        mock_client = MagicMock()
        mock_cls.return_value = mock_client

        conn = KachakaConnection.get("1.2.3.4")

        # Start disconnected
        rpc_error = grpc.RpcError()
        rpc_error.code = lambda: grpc.StatusCode.UNAVAILABLE
        rpc_error.details = lambda: "gone"
        mock_client.get_robot_serial_number.side_effect = rpc_error

        conn.start_monitoring(interval=0.05)
        try:
            conn.wait_for_state(ConnectionState.DISCONNECTED, timeout=2.0)

            # Restore connection
            mock_client.get_robot_serial_number.side_effect = None
            mock_client.get_robot_serial_number.return_value = "KCK-001"
            mock_client.get_robot_pose.return_value = MagicMock(x=0, y=0, theta=0)

            reached = conn.wait_for_state(ConnectionState.CONNECTED, timeout=2.0)
            assert reached
            assert conn.state == ConnectionState.CONNECTED
        finally:
            conn.stop_monitoring()

    @patch("kachaka_core.connection.KachakaApiClient")
    def test_state_change_callback_called(self, mock_cls):
        mock_client = MagicMock()
        mock_cls.return_value = mock_client

        conn = KachakaConnection.get("1.2.3.4")
        transitions = []

        rpc_error = grpc.RpcError()
        rpc_error.code = lambda: grpc.StatusCode.UNAVAILABLE
        rpc_error.details = lambda: "gone"
        mock_client.get_robot_serial_number.side_effect = rpc_error

        conn.start_monitoring(interval=0.05, on_state_change=transitions.append)
        try:
            conn.wait_for_state(ConnectionState.DISCONNECTED, timeout=2.0)
            assert ConnectionState.DISCONNECTED in transitions
        finally:
            conn.stop_monitoring()

    @patch("kachaka_core.connection.KachakaApiClient")
    def test_stop_monitoring_cleans_up(self, mock_cls):
        mock_client = MagicMock()
        mock_client.get_robot_serial_number.return_value = "KCK-001"
        mock_client.get_robot_pose.return_value = MagicMock(x=0, y=0, theta=0)
        mock_cls.return_value = mock_client

        conn = KachakaConnection.get("1.2.3.4")
        conn.start_monitoring(interval=0.05)
        assert conn._monitor_thread is not None
        conn.stop_monitoring()
        assert conn._monitor_thread is None

    @patch("kachaka_core.connection.KachakaApiClient")
    def test_wait_for_state_timeout(self, mock_cls):
        mock_client = MagicMock()
        mock_client.get_robot_serial_number.return_value = "KCK-001"
        mock_client.get_robot_pose.return_value = MagicMock(x=0, y=0, theta=0)
        mock_cls.return_value = mock_client

        conn = KachakaConnection.get("1.2.3.4")
        # Don't start monitoring — state stays CONNECTED
        reached = conn.wait_for_state(ConnectionState.DISCONNECTED, timeout=0.1)
        assert reached is False

    @patch("kachaka_core.connection.KachakaApiClient")
    def test_start_monitoring_idempotent(self, mock_cls):
        mock_client = MagicMock()
        mock_client.get_robot_serial_number.return_value = "KCK-001"
        mock_client.get_robot_pose.return_value = MagicMock(x=0, y=0, theta=0)
        mock_cls.return_value = mock_client

        conn = KachakaConnection.get("1.2.3.4")
        conn.start_monitoring(interval=0.1)
        thread1 = conn._monitor_thread
        conn.start_monitoring(interval=0.1)
        thread2 = conn._monitor_thread
        assert thread1 is thread2
        conn.stop_monitoring()


class TestCacheTier1:
    @patch("kachaka_core.connection.KachakaApiClient")
    def test_serial_lazy_fetched_and_cached(self, mock_cls):
        mock_client = MagicMock()
        mock_client.get_robot_serial_number.return_value = "BKP40EB1T"
        mock_cls.return_value = mock_client

        conn = KachakaConnection.get("1.2.3.4")
        # _ensure_connected calls get_robot_serial_number once for connectivity check
        calls_after_connect = mock_client.get_robot_serial_number.call_count
        assert conn.serial == "BKP40EB1T"
        assert conn.serial == "BKP40EB1T"  # second access uses cache
        # Only one additional call from the serial property (second access is cached)
        assert mock_client.get_robot_serial_number.call_count == calls_after_connect + 1

    @patch("kachaka_core.connection.KachakaApiClient")
    def test_version_lazy_fetched_and_cached(self, mock_cls):
        mock_client = MagicMock()
        mock_client.get_robot_version.return_value = "3.15.4"
        mock_cls.return_value = mock_client

        conn = KachakaConnection.get("1.2.3.4")
        assert conn.version == "3.15.4"
        assert conn.version == "3.15.4"
        mock_client.get_robot_version.assert_called_once()

    @patch("kachaka_core.connection.KachakaApiClient")
    def test_error_definitions_lazy_fetched_and_cached(self, mock_cls):
        mock_client = MagicMock()
        err_info = MagicMock()
        err_info.title_en = "Shelf dropped"
        err_info.description_en = "dropped during movement"
        err_info.title = ""
        err_info.description = ""
        mock_client.get_robot_error_code.return_value = {14606: err_info}
        mock_cls.return_value = mock_client

        conn = KachakaConnection.get("1.2.3.4")
        defs = conn.error_definitions
        assert 14606 in defs
        assert defs[14606]["title"] == "Shelf dropped"
        _ = conn.error_definitions
        mock_client.get_robot_error_code.assert_called_once()

    @patch("kachaka_core.connection.KachakaApiClient")
    def test_error_definitions_fetch_failure_returns_empty(self, mock_cls):
        mock_client = MagicMock()
        mock_client.get_robot_error_code.side_effect = RuntimeError("gRPC down")
        mock_cls.return_value = mock_client

        conn = KachakaConnection.get("1.2.3.4")
        assert conn.error_definitions == {}

    @patch("kachaka_core.connection.KachakaApiClient")
    def test_serial_fetch_failure_returns_empty(self, mock_cls):
        mock_client = MagicMock()
        mock_client.get_robot_serial_number.side_effect = RuntimeError("fail")
        mock_cls.return_value = mock_client

        conn = KachakaConnection.get("1.2.3.4")
        assert conn.serial == ""

    @patch("kachaka_core.connection.KachakaApiClient")
    def test_cache_thread_safe(self, mock_cls):
        mock_client = MagicMock()
        mock_client.get_robot_serial_number.return_value = "KCK-001"
        mock_client.get_robot_version.return_value = "3.15.4"
        mock_client.get_robot_error_code.return_value = {}
        mock_cls.return_value = mock_client

        conn = KachakaConnection.get("1.2.3.4")
        errors = []

        def read_all():
            for _ in range(20):
                try:
                    _ = conn.serial
                    _ = conn.version
                    _ = conn.error_definitions
                except Exception as e:
                    errors.append(e)

        threads = [threading.Thread(target=read_all) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert errors == []


class TestCacheTier2:
    @patch("kachaka_core.connection.KachakaApiClient")
    def test_shortcuts_lazy_fetched_and_cached(self, mock_cls):
        mock_client = MagicMock()
        sc = MagicMock()
        sc.id = "sc-1"
        sc.name = "Patrol A"
        mock_client.get_shortcuts.return_value = [sc]
        mock_cls.return_value = mock_client

        conn = KachakaConnection.get("1.2.3.4")
        shortcuts = conn.shortcuts
        assert len(shortcuts) == 1
        assert shortcuts[0]["id"] == "sc-1"
        _ = conn.shortcuts
        mock_client.get_shortcuts.assert_called_once()

    @patch("kachaka_core.connection.KachakaApiClient")
    def test_map_list_lazy_fetched_and_cached(self, mock_cls):
        mock_client = MagicMock()
        m = MagicMock()
        m.id = "map-1"
        m.name = "Floor1"
        mock_client.get_map_list.return_value = [m]
        mock_cls.return_value = mock_client

        conn = KachakaConnection.get("1.2.3.4")
        maps = conn.map_list
        assert len(maps) == 1
        assert maps[0]["id"] == "map-1"
        _ = conn.map_list
        mock_client.get_map_list.assert_called_once()

    @patch("kachaka_core.connection.KachakaApiClient")
    def test_current_map_id_lazy_fetched_and_cached(self, mock_cls):
        mock_client = MagicMock()
        mock_client.get_current_map_id.return_value = "map-1"
        mock_cls.return_value = mock_client

        conn = KachakaConnection.get("1.2.3.4")
        assert conn.current_map_id == "map-1"
        assert conn.current_map_id == "map-1"
        mock_client.get_current_map_id.assert_called_once()

    @patch("kachaka_core.connection.KachakaApiClient")
    def test_map_image_lazy_fetched_and_cached(self, mock_cls):
        mock_client = MagicMock()
        png_map = MagicMock()
        png_map.data = b"\x89PNGtest"
        png_map.resolution = 0.05
        png_map.width = 200
        png_map.height = 200
        png_map.name = "Floor1"
        png_map.origin = MagicMock(x=0.0, y=0.0)
        mock_client.get_png_map.return_value = png_map
        mock_cls.return_value = mock_client

        conn = KachakaConnection.get("1.2.3.4")
        img = conn.map_image
        assert img["width"] == 200
        assert img["png_bytes"] == b"\x89PNGtest"
        _ = conn.map_image
        mock_client.get_png_map.assert_called_once()

    @patch("kachaka_core.connection.KachakaApiClient")
    def test_refresh_shortcuts_clears_cache(self, mock_cls):
        mock_client = MagicMock()
        mock_client.get_shortcuts.return_value = []
        mock_cls.return_value = mock_client

        conn = KachakaConnection.get("1.2.3.4")
        _ = conn.shortcuts
        conn.refresh_shortcuts()
        _ = conn.shortcuts
        assert mock_client.get_shortcuts.call_count == 2

    @patch("kachaka_core.connection.KachakaApiClient")
    def test_refresh_maps_clears_all_map_cache(self, mock_cls):
        mock_client = MagicMock()
        mock_client.get_map_list.return_value = []
        mock_client.get_current_map_id.return_value = "map-1"
        png_map = MagicMock()
        png_map.data = b"\x89PNG"
        png_map.resolution = 0.05
        png_map.width = 100
        png_map.height = 100
        png_map.name = "F1"
        png_map.origin = MagicMock(x=0.0, y=0.0)
        mock_client.get_png_map.return_value = png_map
        mock_cls.return_value = mock_client

        conn = KachakaConnection.get("1.2.3.4")
        _ = conn.map_list
        _ = conn.current_map_id
        _ = conn.map_image
        conn.refresh_maps()
        _ = conn.map_list
        _ = conn.current_map_id
        _ = conn.map_image
        assert mock_client.get_map_list.call_count == 2
        assert mock_client.get_current_map_id.call_count == 2
        assert mock_client.get_png_map.call_count == 2


class TestLongPollTimeoutWiring:
    @patch("kachaka_core.connection.KachakaApiClient")
    def test_long_poll_timeout_reaches_interceptor(self, mock_cls):
        """get(long_poll_timeout=...) must be wired into TimeoutInterceptor.

        Regression: HIL-1.2 found the constructor stored the value but
        _ensure_connected built the interceptor without it.
        """
        from kachaka_core.interceptors import TimeoutInterceptor

        mock_cls.return_value = _healthy_mock_client()
        with patch(
            "kachaka_core.connection.TimeoutInterceptor", wraps=TimeoutInterceptor
        ) as mock_interceptor:
            KachakaConnection.get("lpt-test", timeout=2.0,
                                  long_poll_timeout=6.0, monitor=False)
        mock_interceptor.assert_called_once_with(2.0, long_poll_timeout=6.0)


class TestRemoveStopsMonitoring:
    @patch("kachaka_core.connection.KachakaApiClient")
    def test_remove_stops_monitoring(self, mock_cls):
        """remove() must stop the connection's monitor thread.

        With auto-monitoring on by default, popping the pool entry without
        stopping the monitor leaks a thread that pings the old IP forever
        (hit by kachaka-gemini and visual-patrol teardown paths).
        """
        mock_cls.return_value = _healthy_mock_client()
        conn = KachakaConnection.get("1.2.3.4")
        assert conn._is_monitoring
        KachakaConnection.remove("1.2.3.4")
        assert not conn._is_monitoring, "remove() left the monitor thread running"
