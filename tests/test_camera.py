"""Tests for kachaka_core.camera — CameraStreamer background capture."""

from __future__ import annotations

import base64
import threading
import time
from unittest.mock import MagicMock, patch

import grpc
import pytest

from kachaka_core.connection import KachakaConnection
from kachaka_core.camera import CameraStreamer


@pytest.fixture(autouse=True)
def _clean_pool():
    """Ensure each test starts with an empty connection pool."""
    KachakaConnection.clear_pool()
    yield
    KachakaConnection.clear_pool()


def _make_conn(mock_client):
    with patch("kachaka_core.connection.KachakaApiClient", return_value=mock_client):
        return KachakaConnection.get("test-robot")


def _make_grpc_error(code=grpc.StatusCode.UNAVAILABLE, details="conn refused"):
    """Create a mock gRPC RpcError."""
    err = grpc.RpcError()
    err.code = lambda: code
    err.details = lambda: details
    return err


class TestInit:
    def test_defaults(self):
        mock = MagicMock()
        conn = _make_conn(mock)
        cs = CameraStreamer(conn)

        assert cs.is_running is False
        assert cs.latest_frame is None
        assert cs.stats == {
            "total_frames": 0,
            "dropped": 0,
            "drop_rate_pct": 0.0,
            "longest_gap_s": 0.0,
            "recovery_latency_ms": None,
        }

    def test_custom_params(self):
        mock = MagicMock()
        conn = _make_conn(mock)
        callback = MagicMock()
        cs = CameraStreamer(conn, interval=0.5, camera="back", on_frame=callback)

        assert cs._interval == 0.5
        assert cs._camera == "back"

    def test_invalid_camera_raises(self):
        mock = MagicMock()
        conn = _make_conn(mock)

        with pytest.raises(ValueError, match="camera"):
            CameraStreamer(conn, camera="side")

    def test_invalid_camera_empty_raises(self):
        mock = MagicMock()
        conn = _make_conn(mock)

        with pytest.raises(ValueError, match="camera"):
            CameraStreamer(conn, camera="")


class TestLifecycle:
    def test_start_sets_running(self):
        mock = MagicMock()
        mock.get_front_camera_ros_compressed_image.return_value = MagicMock(
            data=b"\xff\xd8jpeg-data", format="jpeg"
        )
        conn = _make_conn(mock)
        cs = CameraStreamer(conn, interval=0.05)

        cs.start()
        try:
            assert cs.is_running is True
        finally:
            cs.stop()

        assert cs.is_running is False

    def test_double_start_is_noop(self):
        mock = MagicMock()
        mock.get_front_camera_ros_compressed_image.return_value = MagicMock(
            data=b"\xff\xd8jpeg-data", format="jpeg"
        )
        conn = _make_conn(mock)
        cs = CameraStreamer(conn, interval=0.05)

        cs.start()
        cs.start()  # should not raise or create a second thread
        try:
            assert cs.is_running is True
        finally:
            cs.stop()

    def test_stop_without_start_is_noop(self):
        mock = MagicMock()
        conn = _make_conn(mock)
        cs = CameraStreamer(conn)

        cs.stop()  # should not raise
        assert cs.is_running is False

    def test_thread_is_daemon(self):
        mock = MagicMock()
        mock.get_front_camera_ros_compressed_image.return_value = MagicMock(
            data=b"\xff\xd8jpeg-data", format="jpeg"
        )
        conn = _make_conn(mock)
        cs = CameraStreamer(conn, interval=0.05)

        cs.start()
        try:
            assert cs._thread.daemon is True
        finally:
            cs.stop()

    def test_stop_joins_thread(self):
        mock = MagicMock()
        mock.get_front_camera_ros_compressed_image.return_value = MagicMock(
            data=b"\xff\xd8jpeg-data", format="jpeg"
        )
        conn = _make_conn(mock)
        cs = CameraStreamer(conn, interval=0.05)

        cs.start()
        cs.stop()

        assert cs._thread is None or not cs._thread.is_alive()


class TestCaptureFront:
    def test_front_camera_frame(self):
        mock = MagicMock()
        raw = b"\xff\xd8front-jpeg-data"
        mock.get_front_camera_ros_compressed_image.return_value = MagicMock(
            data=raw, format="jpeg"
        )
        conn = _make_conn(mock)
        cs = CameraStreamer(conn, interval=0.05, camera="front")

        cs.start()
        time.sleep(0.15)  # allow at least one capture
        cs.stop()

        frame = cs.latest_frame
        assert frame is not None
        assert frame["ok"] is True
        assert frame["format"] == "jpeg"
        assert isinstance(frame["timestamp"], float)
        # Verify base64 encoding
        decoded = base64.b64decode(frame["image_base64"])
        assert decoded == raw


class TestCaptureBack:
    def test_back_camera_frame(self):
        mock = MagicMock()
        raw = b"\xff\xd8back-jpeg-data"
        mock.get_back_camera_ros_compressed_image.return_value = MagicMock(
            data=raw, format="jpeg"
        )
        conn = _make_conn(mock)
        cs = CameraStreamer(conn, interval=0.05, camera="back")

        cs.start()
        time.sleep(0.15)
        cs.stop()

        frame = cs.latest_frame
        assert frame is not None
        assert frame["ok"] is True
        decoded = base64.b64decode(frame["image_base64"])
        assert decoded == raw

    def test_back_camera_calls_correct_method(self):
        mock = MagicMock()
        mock.get_back_camera_ros_compressed_image.return_value = MagicMock(
            data=b"\xff\xd8data", format="jpeg"
        )
        conn = _make_conn(mock)
        cs = CameraStreamer(conn, interval=0.05, camera="back")

        cs.start()
        time.sleep(0.15)
        cs.stop()

        mock.get_back_camera_ros_compressed_image.assert_called()
        mock.get_front_camera_ros_compressed_image.assert_not_called()


class TestStats:
    def test_total_frames_count(self):
        mock = MagicMock()
        mock.get_front_camera_ros_compressed_image.return_value = MagicMock(
            data=b"\xff\xd8data", format="jpeg"
        )
        conn = _make_conn(mock)
        cs = CameraStreamer(conn, interval=0.05, camera="front")

        cs.start()
        time.sleep(0.25)
        cs.stop()

        stats = cs.stats
        assert stats["total_frames"] >= 2
        assert stats["dropped"] == 0
        assert stats["drop_rate_pct"] == 0.0

    def test_dropped_frames_counted(self):
        mock = MagicMock()
        call_count = 0

        def alternate_error():
            nonlocal call_count
            call_count += 1
            if call_count % 2 == 1:
                raise _make_grpc_error()
            return MagicMock(data=b"\xff\xd8data", format="jpeg")

        mock.get_front_camera_ros_compressed_image.side_effect = alternate_error
        conn = _make_conn(mock)
        cs = CameraStreamer(conn, interval=0.05, camera="front")

        cs.start()
        time.sleep(0.35)
        cs.stop()

        stats = cs.stats
        assert stats["total_frames"] >= 2
        assert stats["dropped"] >= 1
        assert stats["drop_rate_pct"] > 0.0

    def test_drop_rate_calculation(self):
        mock = MagicMock()
        conn = _make_conn(mock)
        cs = CameraStreamer(conn, interval=1.0, camera="front")

        # Manually set counters to verify calculation
        cs._total_frames = 10
        cs._dropped = 3
        stats = cs.stats
        assert stats["drop_rate_pct"] == 30.0

    def test_drop_rate_zero_when_no_frames(self):
        mock = MagicMock()
        conn = _make_conn(mock)
        cs = CameraStreamer(conn, interval=1.0, camera="front")

        stats = cs.stats
        assert stats["drop_rate_pct"] == 0.0


class TestErrorHandling:
    def test_grpc_error_increments_dropped(self):
        mock = MagicMock()
        mock.get_front_camera_ros_compressed_image.side_effect = _make_grpc_error()
        conn = _make_conn(mock)
        cs = CameraStreamer(conn, interval=0.05, camera="front")

        cs.start()
        time.sleep(0.2)
        cs.stop()

        assert cs.stats["dropped"] >= 1
        assert cs.stats["total_frames"] >= 1

    def test_thread_does_not_crash_on_error(self):
        mock = MagicMock()
        mock.get_front_camera_ros_compressed_image.side_effect = _make_grpc_error()
        conn = _make_conn(mock)
        cs = CameraStreamer(conn, interval=0.05, camera="front")

        cs.start()
        time.sleep(0.2)

        # Thread should still be running despite errors
        assert cs.is_running is True
        cs.stop()

    def test_recovers_after_errors(self):
        mock = MagicMock()
        call_count = 0

        def fail_then_succeed():
            nonlocal call_count
            call_count += 1
            if call_count <= 3:
                raise _make_grpc_error()
            return MagicMock(data=b"\xff\xd8recovered", format="jpeg")

        mock.get_front_camera_ros_compressed_image.side_effect = fail_then_succeed
        conn = _make_conn(mock)
        cs = CameraStreamer(conn, interval=0.05, camera="front")

        cs.start()
        time.sleep(0.4)
        cs.stop()

        frame = cs.latest_frame
        assert frame is not None
        assert frame["ok"] is True
        decoded = base64.b64decode(frame["image_base64"])
        assert decoded == b"\xff\xd8recovered"

    def test_generic_exception_increments_dropped(self):
        mock = MagicMock()
        mock.get_front_camera_ros_compressed_image.side_effect = RuntimeError("oops")
        conn = _make_conn(mock)
        cs = CameraStreamer(conn, interval=0.05, camera="front")

        cs.start()
        time.sleep(0.2)
        cs.stop()

        assert cs.stats["dropped"] >= 1


class TestCallback:
    def test_on_frame_called_with_frame_dict(self):
        mock = MagicMock()
        mock.get_front_camera_ros_compressed_image.return_value = MagicMock(
            data=b"\xff\xd8cb-data", format="jpeg"
        )
        callback = MagicMock()
        conn = _make_conn(mock)
        cs = CameraStreamer(conn, interval=0.05, camera="front", on_frame=callback)

        cs.start()
        time.sleep(0.15)
        cs.stop()

        assert callback.call_count >= 1
        frame_arg = callback.call_args[0][0]
        assert frame_arg["ok"] is True
        assert frame_arg["format"] == "jpeg"
        assert "image_base64" in frame_arg
        assert "timestamp" in frame_arg

    def test_on_frame_not_called_on_error(self):
        mock = MagicMock()
        mock.get_front_camera_ros_compressed_image.side_effect = _make_grpc_error()
        callback = MagicMock()
        conn = _make_conn(mock)
        cs = CameraStreamer(conn, interval=0.05, camera="front", on_frame=callback)

        cs.start()
        time.sleep(0.15)
        cs.stop()

        callback.assert_not_called()

    def test_callback_exception_does_not_crash_thread(self):
        mock = MagicMock()
        mock.get_front_camera_ros_compressed_image.return_value = MagicMock(
            data=b"\xff\xd8data", format="jpeg"
        )
        callback = MagicMock(side_effect=RuntimeError("callback boom"))
        conn = _make_conn(mock)
        cs = CameraStreamer(conn, interval=0.05, camera="front", on_frame=callback)

        cs.start()
        time.sleep(0.2)

        # Thread should still be alive despite callback errors
        assert cs.is_running is True
        cs.stop()

        # Frames should still be captured even if callback fails
        assert cs.stats["total_frames"] >= 1


class TestThreadSafety:
    def test_latest_frame_thread_safe_read(self):
        """Ensure concurrent reads of latest_frame don't raise."""
        mock = MagicMock()
        mock.get_front_camera_ros_compressed_image.return_value = MagicMock(
            data=b"\xff\xd8data", format="jpeg"
        )
        conn = _make_conn(mock)
        cs = CameraStreamer(conn, interval=0.02, camera="front")

        errors = []

        def read_loop():
            for _ in range(50):
                try:
                    _ = cs.latest_frame
                except Exception as e:
                    errors.append(e)
                time.sleep(0.01)

        cs.start()
        readers = [threading.Thread(target=read_loop) for _ in range(3)]
        for t in readers:
            t.start()
        for t in readers:
            t.join()
        cs.stop()

        assert len(errors) == 0


class TestRecoveryMetrics:
    """Tests for CameraStreamer recovery observability metrics."""

    def test_longest_gap_zero_initially(self):
        """Before any frames, longest_gap_s should be 0."""
        mock = MagicMock()
        conn = _make_conn(mock)
        cs = CameraStreamer(conn, interval=1.0)

        assert cs.stats["longest_gap_s"] == 0.0

    def test_recovery_latency_none_without_reconnect(self):
        """Without a reconnect event, recovery_latency_ms should be None."""
        mock = MagicMock()
        mock.get_front_camera_ros_compressed_image.return_value = MagicMock(
            data=b"\xff\xd8data", format="jpeg"
        )
        conn = _make_conn(mock)
        cs = CameraStreamer(conn, interval=0.05)

        cs.start()
        time.sleep(0.15)
        cs.stop()

        assert cs.stats["recovery_latency_ms"] is None

    def test_stats_includes_longest_gap(self):
        """longest_gap_s should track max time between successful frames."""
        mock = MagicMock()
        call_count = 0

        def slow_then_fast():
            nonlocal call_count
            call_count += 1
            # First two calls succeed quickly (establishing _last_success_time)
            # Third call sleeps to create a measurable gap
            if call_count == 3:
                time.sleep(0.15)
            return MagicMock(data=b"\xff\xd8data", format="jpeg")

        mock.get_front_camera_ros_compressed_image.side_effect = slow_then_fast
        conn = _make_conn(mock)
        cs = CameraStreamer(conn, interval=0.02)

        cs.start()
        time.sleep(0.5)
        cs.stop()

        # The gap created by the 150ms sleep should be captured
        assert cs.stats["longest_gap_s"] > 0.1

    def test_stats_includes_recovery_latency(self):
        """recovery_latency_ms should measure time from reconnect to first frame."""
        from kachaka_core.connection import ConnectionState

        mock = MagicMock()
        mock.get_front_camera_ros_compressed_image.return_value = MagicMock(
            data=b"\xff\xd8data", format="jpeg"
        )
        conn = _make_conn(mock)
        cs = CameraStreamer(conn, interval=0.05)

        # Simulate a reconnect event, then let a frame be captured
        cs.notify_state_change(ConnectionState.CONNECTED)
        cs.start()
        time.sleep(0.15)
        cs.stop()

        latency = cs.stats["recovery_latency_ms"]
        assert latency is not None
        assert latency > 0

    def test_recovery_latency_only_set_once_per_reconnect(self):
        """Second successful frame after reconnect should not update recovery_latency_ms."""
        from kachaka_core.connection import ConnectionState

        mock = MagicMock()
        mock.get_front_camera_ros_compressed_image.return_value = MagicMock(
            data=b"\xff\xd8data", format="jpeg"
        )
        conn = _make_conn(mock)
        cs = CameraStreamer(conn, interval=0.05)

        cs.notify_state_change(ConnectionState.CONNECTED)
        cs.start()
        time.sleep(0.2)  # allow multiple frames
        cs.stop()

        # Should have captured multiple frames
        assert cs.stats["total_frames"] >= 2

        # Recovery latency should be set from the first frame only
        latency = cs.stats["recovery_latency_ms"]
        assert latency is not None

        # Verify _reconnected_at was cleared (internal check)
        assert cs._reconnected_at is None

    def test_notify_state_change_disconnected_ignored(self):
        """DISCONNECTED state should not set _reconnected_at."""
        from kachaka_core.connection import ConnectionState

        mock = MagicMock()
        conn = _make_conn(mock)
        cs = CameraStreamer(conn, interval=1.0)

        cs.notify_state_change(ConnectionState.DISCONNECTED)
        assert cs._reconnected_at is None


class TestLatestFrameBytes:
    def test_returns_none_when_no_frame(self):
        mock = MagicMock()
        conn = _make_conn(mock)
        cs = CameraStreamer(conn)
        assert cs.latest_frame_bytes is None

    def test_returns_decoded_jpeg_bytes(self):
        mock = MagicMock()
        raw = b"\xff\xd8jpeg-data-here"
        mock.get_front_camera_ros_compressed_image.return_value = MagicMock(
            data=raw, format="jpeg"
        )
        conn = _make_conn(mock)
        cs = CameraStreamer(conn, interval=0.05)

        cs.start()
        time.sleep(0.15)
        cs.stop()

        result = cs.latest_frame_bytes
        assert result == raw
        assert isinstance(result, bytes)

    def test_returns_none_when_frame_not_ok(self):
        mock = MagicMock()
        conn = _make_conn(mock)
        cs = CameraStreamer(conn)
        cs._latest_frame = {"ok": False}
        assert cs.latest_frame_bytes is None
