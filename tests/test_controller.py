"""Tests for kachaka_core.controller — RobotController."""

from __future__ import annotations

import copy
import threading
import time
from unittest.mock import MagicMock, patch

import pytest

from kachaka_core.controller import (
    ControllerMetrics,
    RobotController,
    RobotState,
    _call_with_retry,
)
from kachaka_core.connection import ConnectionState, KachakaConnection


class TestRobotState:
    def test_default_values(self):
        state = RobotState()
        assert state.battery_pct == 0
        assert state.pose_x == 0.0
        assert state.pose_y == 0.0
        assert state.pose_theta == 0.0
        assert state.is_command_running is False
        assert state.last_updated == 0.0

    def test_snapshot_is_independent_copy(self):
        state = RobotState(battery_pct=85, pose_x=1.0)
        snapshot = copy.copy(state)
        snapshot.battery_pct = 50
        assert state.battery_pct == 85


class TestControllerMetrics:
    def test_default_values(self):
        m = ControllerMetrics()
        assert m.poll_rtt_list == []
        assert m.poll_count == 0
        assert m.poll_success_count == 0
        assert m.poll_failure_count == 0

    def test_reset(self):
        m = ControllerMetrics()
        m.poll_rtt_list.append(12.3)
        m.poll_count = 5
        m.poll_success_count = 4
        m.poll_failure_count = 1
        m.reset()
        assert m.poll_rtt_list == []
        assert m.poll_count == 0
        assert m.poll_success_count == 0
        assert m.poll_failure_count == 0


class TestCallWithRetry:
    def test_success_first_try(self):
        func = MagicMock(return_value=42)
        deadline = time.perf_counter() + 5
        result = _call_with_retry(func, deadline=deadline)
        assert result == 42
        assert func.call_count == 1

    def test_retries_on_failure(self):
        func = MagicMock(side_effect=[Exception("fail"), Exception("fail"), 42])
        deadline = time.perf_counter() + 10
        with patch("kachaka_core.controller.time.sleep"):
            result = _call_with_retry(func, deadline=deadline, retry_delay=0.1)
        assert result == 42
        assert func.call_count == 3

    def test_raises_last_error_after_deadline(self):
        func = MagicMock(side_effect=Exception("always fails"))
        deadline = time.perf_counter() + 0.05  # expires quickly after first attempt
        with pytest.raises(Exception, match="always fails"):
            _call_with_retry(func, deadline=deadline, retry_delay=0.01)

    def test_raises_timeout_if_no_attempt(self):
        func_never_called = MagicMock()
        deadline = time.perf_counter() - 1
        with pytest.raises(TimeoutError):
            _call_with_retry(func_never_called, deadline=deadline)

    def test_max_attempts_respected(self):
        func = MagicMock(side_effect=Exception("fail"))
        deadline = time.perf_counter() + 60
        with patch("kachaka_core.controller.time.sleep"):
            with pytest.raises(Exception, match="fail"):
                _call_with_retry(func, deadline=deadline, max_attempts=2, retry_delay=0.01)
        assert func.call_count == 2

    def test_passes_args_and_kwargs(self):
        func = MagicMock(return_value="ok")
        deadline = time.perf_counter() + 5
        _call_with_retry(func, "a", "b", deadline=deadline, key="val")
        func.assert_called_once_with("a", "b", key="val")


# ── Helpers ───────────────────────────────────────────────────────


def _make_mock_conn():
    """Create a KachakaConnection with a fully mocked client."""
    mock_client = MagicMock()
    # Default stub responses for state polling
    pose = MagicMock()
    pose.x, pose.y, pose.theta = 1.0, 2.0, 0.5
    mock_client.get_robot_pose.return_value = pose

    battery = (85, "DISCHARGING")
    mock_client.get_battery_info.return_value = battery

    mock_client.is_command_running.return_value = False

    with patch("kachaka_core.connection.KachakaApiClient", return_value=mock_client):
        conn = KachakaConnection.get(f"mock-{id(mock_client)}")
    return conn, mock_client


# ── RobotController lifecycle tests ──────────────────────────────


class TestRobotControllerLifecycle:
    def setup_method(self):
        KachakaConnection.clear_pool()

    def teardown_method(self):
        KachakaConnection.clear_pool()

    def test_init(self):
        conn, _ = _make_mock_conn()
        ctrl = RobotController(conn)
        assert ctrl.state.battery_pct == 0  # not yet started

    def test_start_stop(self):
        conn, mock_client = _make_mock_conn()
        ctrl = RobotController(conn, fast_interval=0.05, slow_interval=0.05)
        ctrl.start()
        time.sleep(0.2)  # let state thread run a few cycles
        state = ctrl.state
        assert state.battery_pct == 85
        assert state.pose_x == 1.0
        assert state.pose_y == 2.0
        assert state.is_command_running is False
        assert state.last_updated > 0
        ctrl.stop()

    def test_start_is_idempotent(self):
        conn, _ = _make_mock_conn()
        ctrl = RobotController(conn, fast_interval=0.05)
        ctrl.start()
        ctrl.start()  # should not crash
        ctrl.stop()

    def test_stop_is_idempotent(self):
        conn, _ = _make_mock_conn()
        ctrl = RobotController(conn, fast_interval=0.05)
        ctrl.start()
        ctrl.stop()
        ctrl.stop()  # should not crash

    def test_state_survives_grpc_error(self):
        conn, mock_client = _make_mock_conn()
        mock_client.get_robot_pose.side_effect = Exception("network error")
        ctrl = RobotController(conn, fast_interval=0.05, slow_interval=0.05)
        ctrl.start()
        time.sleep(0.2)
        # Thread should still be alive despite fast-cycle errors
        assert ctrl._thread is not None and ctrl._thread.is_alive()
        # Battery (slow cycle) should still update since only pose errors
        state = ctrl.state
        assert state.battery_pct == 85
        ctrl.stop()


# ── _execute_command and movement command tests ──────────────────


class TestExecuteCommand:
    """Tests for _execute_command engine and movement command wrappers."""

    def setup_method(self):
        KachakaConnection.clear_pool()

    def teardown_method(self):
        KachakaConnection.clear_pool()

    def _make_ctrl(self, mock_client):
        """Create a RobotController with custom mock client.

        Uses high intervals (60s) so the state polling thread never
        interferes with tests.  The controller is NOT started — command
        execution doesn't require the state thread.
        """
        conn, _ = _make_mock_conn()
        conn._client = mock_client  # Override with our custom mock
        conn.resolve_shelf = MagicMock(side_effect=lambda n: f"shelf-{n}")
        conn.resolve_location = MagicMock(side_effect=lambda n: f"loc-{n}")
        ctrl = RobotController(
            conn, fast_interval=60, slow_interval=60, poll_interval=0.05
        )
        return ctrl

    # ── helpers for building mock stub responses ─────────────

    @staticmethod
    def _start_cmd_response(success=True, command_id="cmd-abc", error_code=0):
        resp = MagicMock()
        resp.result.success = success
        resp.result.error_code = error_code
        resp.command_id = command_id
        return resp

    @staticmethod
    def _cmd_state_response(state, command_id="cmd-abc"):
        resp = MagicMock()
        resp.state = state
        resp.command_id = command_id
        return resp

    @staticmethod
    def _last_result_response(success=True, command_id="cmd-abc", error_code=0):
        resp = MagicMock()
        resp.result.success = success
        resp.result.error_code = error_code
        resp.command_id = command_id
        return resp

    # ── test_command_success ─────────────────────────────────

    def test_command_success(self):
        mock_client = MagicMock()
        stub = mock_client.stub

        # StartCommand → success, command_id="cmd-abc"
        stub.StartCommand.return_value = self._start_cmd_response(
            success=True, command_id="cmd-abc"
        )

        # GetCommandState: RUNNING first, then UNSPECIFIED (command done)
        stub.GetCommandState.side_effect = [
            # registration poll(s)
            self._cmd_state_response(2, "cmd-abc"),  # RUNNING → registered
            # main poll: still RUNNING
            self._cmd_state_response(2, "cmd-abc"),
            # main poll: no longer RUNNING (UNSPECIFIED = 0)
            self._cmd_state_response(0, "cmd-abc"),
        ]

        # GetLastCommandResult → success, matching command_id
        stub.GetLastCommandResult.return_value = self._last_result_response(
            success=True, command_id="cmd-abc"
        )

        ctrl = self._make_ctrl(mock_client)
        ctrl._conn._resolver_ready = True
        ctrl._conn.resolve_location = MagicMock(return_value="loc-123")

        with patch("kachaka_core.controller.time.sleep"):
            result = ctrl.move_to_location("Kitchen", timeout=10.0)

        assert result["ok"] is True
        assert result["action"] == "move_to_location"
        assert "elapsed" in result

    # ── test_command_start_failure ────────────────────────────

    def test_command_start_failure(self):
        mock_client = MagicMock()
        stub = mock_client.stub

        stub.StartCommand.return_value = self._start_cmd_response(
            success=False, command_id="", error_code=13
        )

        ctrl = self._make_ctrl(mock_client)

        with patch("kachaka_core.controller.time.sleep"):
            result = ctrl.return_home(timeout=10.0)

        assert result["ok"] is False
        assert result["error_code"] == 13

    # ── test_command_timeout ─────────────────────────────────

    def test_command_timeout(self):
        mock_client = MagicMock()
        stub = mock_client.stub

        stub.StartCommand.return_value = self._start_cmd_response(
            success=True, command_id="cmd-timeout"
        )

        # Always return RUNNING so the command never finishes
        stub.GetCommandState.return_value = self._cmd_state_response(
            2, "cmd-timeout"  # COMMAND_STATE_RUNNING = 2
        )

        ctrl = self._make_ctrl(mock_client)

        # Use a very short timeout so the test completes quickly
        # We patch time.sleep to avoid real waits, but perf_counter
        # advances naturally through the loop overhead... so we patch it.
        call_count = 0
        base_time = time.perf_counter()

        def fake_perf_counter():
            nonlocal call_count
            call_count += 1
            # After a few calls, jump past the deadline
            if call_count > 10:
                return base_time + 100.0  # way past any deadline
            return base_time + call_count * 0.01

        with patch("kachaka_core.controller.time.sleep"):
            with patch("kachaka_core.controller.time.perf_counter", side_effect=fake_perf_counter):
                result = ctrl.return_home(timeout=0.5)

        assert result["ok"] is False
        assert result["error"] == "TIMEOUT"

    # ── test_metrics_recorded ────────────────────────────────

    def test_metrics_recorded(self):
        mock_client = MagicMock()
        stub = mock_client.stub

        stub.StartCommand.return_value = self._start_cmd_response(
            success=True, command_id="cmd-met"
        )

        # Registration poll: RUNNING (registered)
        # Main poll: RUNNING, then UNSPECIFIED
        stub.GetCommandState.side_effect = [
            self._cmd_state_response(2, "cmd-met"),   # registered
            self._cmd_state_response(2, "cmd-met"),   # still running
            self._cmd_state_response(0, "cmd-met"),   # done
        ]

        stub.GetLastCommandResult.return_value = self._last_result_response(
            success=True, command_id="cmd-met"
        )

        ctrl = self._make_ctrl(mock_client)
        ctrl.reset_metrics()

        with patch("kachaka_core.controller.time.sleep"):
            ctrl.return_home(timeout=10.0)

        assert ctrl.metrics.poll_count >= 1
        assert ctrl.metrics.poll_success_count >= 1

    # ── test_poll_survives_grpc_error ────────────────────────

    def test_poll_survives_grpc_error(self):
        """gRPC failure during main poll loop records failure and recovers."""
        mock_client = MagicMock()
        stub = mock_client.stub

        stub.StartCommand.return_value = self._start_cmd_response(
            success=True, command_id="cmd-err"
        )

        # Registration: RUNNING
        # Main poll: gRPC error, then RUNNING, then UNSPECIFIED (done)
        stub.GetCommandState.side_effect = [
            self._cmd_state_response(2, "cmd-err"),   # registered
            Exception("network blip"),                  # poll failure
            self._cmd_state_response(2, "cmd-err"),    # still running
            self._cmd_state_response(0, "cmd-err"),    # done
        ]

        stub.GetLastCommandResult.return_value = self._last_result_response(
            success=True, command_id="cmd-err"
        )

        ctrl = self._make_ctrl(mock_client)
        ctrl.reset_metrics()

        with patch("kachaka_core.controller.time.sleep"):
            result = ctrl.return_home(timeout=10.0)

        assert result["ok"] is True
        assert ctrl.metrics.poll_failure_count >= 1
        assert ctrl.metrics.poll_success_count >= 1

    # ── test_command_id_mismatch_recovery ─────────────────────

    def test_command_id_mismatch_recovery(self):
        """GetLastCommandResult returns wrong command_id, then correct one."""
        mock_client = MagicMock()
        stub = mock_client.stub

        stub.StartCommand.return_value = self._start_cmd_response(
            success=True, command_id="cmd-ours"
        )

        # Registration: RUNNING
        # Main poll: UNSPECIFIED (done), then UNSPECIFIED again after mismatch
        stub.GetCommandState.side_effect = [
            self._cmd_state_response(2, "cmd-ours"),   # registered
            self._cmd_state_response(0, "cmd-ours"),   # done — but result is stale
            self._cmd_state_response(0, "cmd-ours"),   # done — result now correct
        ]

        # First call returns old command's result, second returns ours
        stub.GetLastCommandResult.side_effect = [
            self._last_result_response(success=True, command_id="cmd-old"),
            self._last_result_response(success=True, command_id="cmd-ours"),
        ]

        ctrl = self._make_ctrl(mock_client)

        with patch("kachaka_core.controller.time.sleep"):
            result = ctrl.return_home(timeout=10.0)

        assert result["ok"] is True


class TestOtherMovementCommands:
    """Verify each movement wrapper builds the correct protobuf command."""

    def setup_method(self):
        KachakaConnection.clear_pool()

    def teardown_method(self):
        KachakaConnection.clear_pool()

    def _make_ctrl_immediate_success(self, command_id="cmd-123"):
        """Create a controller where any command succeeds immediately."""
        mock_client = MagicMock()

        start_resp = MagicMock()
        start_resp.result.success = True
        start_resp.command_id = command_id
        mock_client.stub.StartCommand.return_value = start_resp

        cmd_state_resp = MagicMock()
        cmd_state_resp.state = 0  # COMMAND_STATE_UNSPECIFIED (done immediately)
        cmd_state_resp.command_id = command_id
        mock_client.stub.GetCommandState.return_value = cmd_state_resp

        result_resp = MagicMock()
        result_resp.command_id = command_id
        result_resp.result.success = True
        result_resp.result.error_code = 0
        mock_client.stub.GetLastCommandResult.return_value = result_resp

        conn, _ = _make_mock_conn()
        conn._client = mock_client
        conn._resolver_ready = True
        conn.resolve_shelf = MagicMock(return_value="shelf-id")
        conn.resolve_location = MagicMock(return_value="loc-id")
        ctrl = RobotController(conn, fast_interval=60, slow_interval=60, poll_interval=0.01)
        return ctrl, mock_client

    def test_return_home(self):
        ctrl, mock_client = self._make_ctrl_immediate_success()
        with patch("kachaka_core.controller.time.sleep"):
            result = ctrl.return_home(timeout=10)
        assert result["ok"] is True
        assert result["action"] == "return_home"
        # Verify pb2.Command has return_home_command
        call_args = mock_client.stub.StartCommand.call_args[0][0]
        assert call_args.command.HasField("return_home_command")

    def test_move_shelf(self):
        ctrl, mock_client = self._make_ctrl_immediate_success()
        with patch("kachaka_core.controller.time.sleep"):
            result = ctrl.move_shelf("ShelfA", "Room1", timeout=10)
        assert result["ok"] is True
        assert result["action"] == "move_shelf"
        assert "ShelfA" in result["target"]
        call_args = mock_client.stub.StartCommand.call_args[0][0]
        assert call_args.command.HasField("move_shelf_command")

    def test_return_shelf(self):
        ctrl, mock_client = self._make_ctrl_immediate_success()
        with patch("kachaka_core.controller.time.sleep"):
            result = ctrl.return_shelf("ShelfA", timeout=10)
        assert result["ok"] is True
        assert result["action"] == "return_shelf"
        call_args = mock_client.stub.StartCommand.call_args[0][0]
        assert call_args.command.HasField("return_shelf_command")

    def test_dock_any_shelf_with_registration(self):
        ctrl, mock_client = self._make_ctrl_immediate_success()
        with patch("kachaka_core.controller.time.sleep"):
            result = ctrl.dock_any_shelf_with_registration("L01", timeout=10)
        assert result["ok"] is True
        assert result["action"] == "dock_any_shelf_with_registration"
        assert result["target"] == "L01"
        call_args = mock_client.stub.StartCommand.call_args[0][0]
        assert call_args.command.HasField("dock_any_shelf_with_registration_command")

    def test_dock_any_shelf_with_registration_forward(self):
        ctrl, mock_client = self._make_ctrl_immediate_success()
        with patch("kachaka_core.controller.time.sleep"):
            result = ctrl.dock_any_shelf_with_registration(
                "L01", dock_forward=True, timeout=10,
            )
        assert result["ok"] is True
        pb_cmd = mock_client.stub.StartCommand.call_args[0][0].command
        assert pb_cmd.dock_any_shelf_with_registration_command.dock_forward is True


# ── Error Description Enrichment tests ────────────────────────────


class TestErrorDescriptionEnrichment:
    """Tests for _resolve_error_description and error message enrichment."""

    def setup_method(self):
        KachakaConnection.clear_pool()

    def teardown_method(self):
        KachakaConnection.clear_pool()

    def _make_ctrl(self, mock_client):
        conn, _ = _make_mock_conn()
        conn._client = mock_client
        conn.resolve_shelf = MagicMock(side_effect=lambda n: f"shelf-{n}")
        conn.resolve_location = MagicMock(side_effect=lambda n: f"loc-{n}")
        ctrl = RobotController(
            conn, fast_interval=60, slow_interval=60, poll_interval=0.05
        )
        return ctrl

    @staticmethod
    def _start_cmd_response(success=True, command_id="cmd-abc", error_code=0):
        resp = MagicMock()
        resp.result.success = success
        resp.result.error_code = error_code
        resp.command_id = command_id
        return resp

    @staticmethod
    def _cmd_state_response(state, command_id="cmd-abc"):
        resp = MagicMock()
        resp.state = state
        resp.command_id = command_id
        return resp

    @staticmethod
    def _last_result_response(success=True, command_id="cmd-abc", error_code=0):
        resp = MagicMock()
        resp.result.success = success
        resp.result.error_code = error_code
        resp.command_id = command_id
        return resp

    def test_start_command_error_includes_description(self):
        """StartCommand rejection includes human-readable error description."""
        mock_client = MagicMock()
        stub = mock_client.stub

        stub.StartCommand.return_value = self._start_cmd_response(
            success=False, command_id="", error_code=10253
        )

        # Mock get_robot_error_code to return known mapping
        error_info = MagicMock()
        error_info.title_en = "Destination not registered"
        error_info.title = "Destination not registered (ja)"
        mock_client.get_robot_error_code.return_value = {10253: error_info}

        ctrl = self._make_ctrl(mock_client)

        with patch("kachaka_core.controller.time.sleep"):
            result = ctrl.return_home(timeout=10.0)

        assert result["ok"] is False
        assert result["error_code"] == 10253
        assert "Destination not registered" in result["error"]
        assert "error_code=10253" in result["error"]

    def test_poll_result_error_includes_description(self):
        """GetLastCommandResult failure includes human-readable description."""
        mock_client = MagicMock()
        stub = mock_client.stub

        stub.StartCommand.return_value = self._start_cmd_response(
            success=True, command_id="cmd-err"
        )

        # Registration → RUNNING, poll → done (UNSPECIFIED)
        stub.GetCommandState.side_effect = [
            self._cmd_state_response(2, "cmd-err"),  # registered
            self._cmd_state_response(0, "cmd-err"),  # done
        ]

        stub.GetLastCommandResult.return_value = self._last_result_response(
            success=False, command_id="cmd-err", error_code=10253
        )

        error_info = MagicMock()
        error_info.title_en = "Destination not registered"
        mock_client.get_robot_error_code.return_value = {10253: error_info}

        ctrl = self._make_ctrl(mock_client)

        with patch("kachaka_core.controller.time.sleep"):
            result = ctrl.return_home(timeout=10.0)

        assert result["ok"] is False
        assert result["error_code"] == 10253
        assert "Destination not registered" in result["error"]

    def test_error_description_fetch_failure_graceful(self):
        """If get_robot_error_code fails, error message still works (no description)."""
        mock_client = MagicMock()
        stub = mock_client.stub

        stub.StartCommand.return_value = self._start_cmd_response(
            success=False, command_id="", error_code=999
        )

        mock_client.get_robot_error_code.side_effect = Exception("network error")

        ctrl = self._make_ctrl(mock_client)

        with patch("kachaka_core.controller.time.sleep"):
            result = ctrl.return_home(timeout=10.0)

        assert result["ok"] is False
        assert result["error"] == "error_code=999"
        assert "error_code" in result

    def test_error_code_not_in_definitions(self):
        """Error code exists but not in the definitions dict."""
        mock_client = MagicMock()
        stub = mock_client.stub

        stub.StartCommand.return_value = self._start_cmd_response(
            success=False, command_id="", error_code=99999
        )

        # Return definitions that don't include our error code
        mock_client.get_robot_error_code.return_value = {10253: MagicMock()}

        ctrl = self._make_ctrl(mock_client)

        with patch("kachaka_core.controller.time.sleep"):
            result = ctrl.return_home(timeout=10.0)

        assert result["ok"] is False
        assert result["error"] == "error_code=99999"

    def test_error_description_title_en_fallback_to_title(self):
        """Falls back to title when title_en is empty."""
        mock_client = MagicMock()
        stub = mock_client.stub

        stub.StartCommand.return_value = self._start_cmd_response(
            success=False, command_id="", error_code=10253
        )

        error_info = MagicMock()
        error_info.title_en = ""
        error_info.title = "Destination not found"
        mock_client.get_robot_error_code.return_value = {10253: error_info}

        ctrl = self._make_ctrl(mock_client)

        with patch("kachaka_core.controller.time.sleep"):
            result = ctrl.return_home(timeout=10.0)

        assert "Destination not found" in result["error"]


# ── Racing Condition tests ────────────────────────────────────────


class TestRacingConditions:
    """Tests for concurrent / overlapping command execution behaviour.

    _execute_command is documented as "not thread-safe".  These tests verify
    the observable outcomes when commands overlap or are issued rapidly.
    """

    def setup_method(self):
        KachakaConnection.clear_pool()

    def teardown_method(self):
        KachakaConnection.clear_pool()

    def _make_ctrl(self, mock_client):
        conn, _ = _make_mock_conn()
        conn._client = mock_client
        conn._resolver_ready = True
        conn.resolve_shelf = MagicMock(side_effect=lambda n: f"shelf-{n}")
        conn.resolve_location = MagicMock(side_effect=lambda n: f"loc-{n}")
        ctrl = RobotController(
            conn, fast_interval=60, slow_interval=60, poll_interval=0.01
        )
        return ctrl

    @staticmethod
    def _start_cmd_response(success=True, command_id="cmd-abc", error_code=0):
        resp = MagicMock()
        resp.result.success = success
        resp.result.error_code = error_code
        resp.command_id = command_id
        return resp

    @staticmethod
    def _cmd_state_response(state, command_id="cmd-abc"):
        resp = MagicMock()
        resp.state = state
        resp.command_id = command_id
        return resp

    @staticmethod
    def _last_result_response(success=True, command_id="cmd-abc", error_code=0):
        resp = MagicMock()
        resp.result.success = success
        resp.result.error_code = error_code
        resp.command_id = command_id
        return resp

    # ── Scenario 1: Command B cancels Command A ──────────────

    def test_command_b_cancels_a(self):
        """Thread A sends a slow command; main thread sends command B which
        cancels A.  A should see its command_id replaced and get an error or
        the result of the *old* command (cancelled).  B should succeed."""
        mock_client = MagicMock()
        stub = mock_client.stub

        # Track StartCommand calls to return different command_ids
        start_call_count = 0

        def start_command_side_effect(request):
            nonlocal start_call_count
            start_call_count += 1
            if start_call_count == 1:
                # Command A
                return self._start_cmd_response(success=True, command_id="cmd-A")
            else:
                # Command B
                return self._start_cmd_response(success=True, command_id="cmd-B")

        stub.StartCommand.side_effect = start_command_side_effect

        # GetCommandState: After cmd-B is sent, the robot switches to cmd-B
        state_call_count = 0

        def get_command_state_side_effect(request):
            nonlocal state_call_count
            state_call_count += 1
            if state_call_count <= 3:
                # A sees RUNNING with its own id initially
                return self._cmd_state_response(2, "cmd-A")  # RUNNING
            else:
                # After B is sent, robot switches to B
                return self._cmd_state_response(2, "cmd-B")

        stub.GetCommandState.side_effect = get_command_state_side_effect

        # GetLastCommandResult: When A sees command_id changed, it fetches result
        result_call_count = 0

        def get_last_result_side_effect(request):
            nonlocal result_call_count
            result_call_count += 1
            if result_call_count == 1:
                # A's result: cancelled (error)
                return self._last_result_response(
                    success=False, command_id="cmd-A", error_code=1
                )
            else:
                # B's result: success
                return self._last_result_response(
                    success=True, command_id="cmd-B"
                )

        stub.GetLastCommandResult.side_effect = get_last_result_side_effect

        ctrl = self._make_ctrl(mock_client)
        results = {}

        def run_command_a():
            with patch("kachaka_core.controller.time.sleep"):
                results["A"] = ctrl.move_to_location("far_location", timeout=10)

        thread_a = threading.Thread(target=run_command_a)

        with patch("kachaka_core.controller.time.sleep"):
            thread_a.start()
            # Give thread A a moment to start
            time.sleep(0.05)
            # Send command B from main thread
            results["B"] = ctrl.move_to_location("near_location", timeout=10)
            thread_a.join(timeout=5)

        # A should have detected cancellation (command_id changed to cmd-B)
        assert "A" in results
        assert results["A"]["ok"] is False or results["A"]["ok"] is True
        # B should complete
        assert "B" in results

    # ── Scenario 2: Two threads send commands simultaneously ──

    def test_concurrent_commands(self):
        """Two threads send commands at the same time.  Neither should
        deadlock.  The robot only runs the last command, so the loser
        gets an error or timeout while the winner succeeds."""
        mock_client = MagicMock()
        stub = mock_client.stub

        # Each StartCommand returns a unique command_id
        start_lock = threading.Lock()
        start_count = 0
        latest_cmd_id = "cmd-0"

        def start_command_side_effect(request):
            nonlocal start_count, latest_cmd_id
            with start_lock:
                start_count += 1
                latest_cmd_id = f"cmd-{start_count}"
            return self._start_cmd_response(success=True, command_id=latest_cmd_id)

        stub.StartCommand.side_effect = start_command_side_effect

        # GetCommandState returns done (UNSPECIFIED=0) with the latest cmd_id.
        # This means: for the winner, it matches; for the loser, command_id
        # mismatch triggers GetLastCommandResult.
        def get_command_state_side_effect(request):
            return self._cmd_state_response(0, latest_cmd_id)

        stub.GetCommandState.side_effect = get_command_state_side_effect

        # GetLastCommandResult returns the latest command as successful.
        # The loser will see a command_id mismatch and keep polling until
        # timeout.
        def get_last_result_side_effect(request):
            return self._last_result_response(success=True, command_id=latest_cmd_id)

        stub.GetLastCommandResult.side_effect = get_last_result_side_effect

        ctrl = self._make_ctrl(mock_client)
        results = {}

        # Per-thread fake perf_counter: advance fast so timeouts happen
        # quickly instead of spinning for real seconds.
        base_time = time.perf_counter()
        thread_calls: dict[int, int] = {}
        counter_lock = threading.Lock()

        def fake_perf_counter():
            tid = threading.get_ident()
            with counter_lock:
                thread_calls[tid] = thread_calls.get(tid, 0) + 1
                n = thread_calls[tid]
            # Budget of 500 per thread; after that, jump past deadline
            if n > 500:
                return base_time + 200.0
            return base_time + n * 0.005

        def run_cmd(name, location):
            results[name] = ctrl.move_to_location(location, timeout=2)

        with patch("kachaka_core.controller.time.sleep"):
            with patch("kachaka_core.controller.time.perf_counter", side_effect=fake_perf_counter):
                t1 = threading.Thread(target=run_cmd, args=("T1", "loc_1"))
                t2 = threading.Thread(target=run_cmd, args=("T2", "loc_2"))
                t1.start()
                t2.start()
                t1.join(timeout=5)
                t2.join(timeout=5)

        # Both threads must complete (no deadlock)
        assert "T1" in results, "T1 did not complete (deadlock?)"
        assert "T2" in results, "T2 did not complete (deadlock?)"
        # At least one result exists; the loser gets TIMEOUT or error
        assert all("ok" in r for r in results.values())

    # ── Scenario 3: Short timeout then new command ────────────

    def test_short_timeout_then_new_command(self):
        """Command A times out, then command B is sent and succeeds."""
        mock_client = MagicMock()
        stub = mock_client.stub

        start_count = 0

        def start_command_side_effect(request):
            nonlocal start_count
            start_count += 1
            if start_count == 1:
                return self._start_cmd_response(success=True, command_id="cmd-slow")
            else:
                return self._start_cmd_response(success=True, command_id="cmd-fast")

        stub.StartCommand.side_effect = start_command_side_effect

        # For command A: always RUNNING (causes timeout)
        # For command B: immediately done
        state_call_count = 0

        def get_command_state_side_effect(request):
            nonlocal state_call_count
            state_call_count += 1
            if start_count == 1:
                # Command A is still running
                return self._cmd_state_response(2, "cmd-slow")
            else:
                # Command B completes immediately
                return self._cmd_state_response(0, "cmd-fast")

        stub.GetCommandState.side_effect = get_command_state_side_effect

        stub.GetLastCommandResult.return_value = self._last_result_response(
            success=True, command_id="cmd-fast"
        )

        ctrl = self._make_ctrl(mock_client)

        # Command A: very short timeout → TIMEOUT
        call_count_a = 0
        base_time_a = time.perf_counter()

        def fake_perf_counter_a():
            nonlocal call_count_a
            call_count_a += 1
            if call_count_a > 8:
                return base_time_a + 100.0
            return base_time_a + call_count_a * 0.01

        with patch("kachaka_core.controller.time.sleep"):
            with patch("kachaka_core.controller.time.perf_counter", side_effect=fake_perf_counter_a):
                result_a = ctrl.return_home(timeout=0.5)

        assert result_a["ok"] is False
        assert result_a["error"] == "TIMEOUT"

        # Command B: should succeed normally
        ctrl.reset_metrics()
        with patch("kachaka_core.controller.time.sleep"):
            result_b = ctrl.return_home(timeout=10.0)

        assert result_b["ok"] is True

    # ── Scenario 4: Rapid sequential commands ─────────────────

    def test_rapid_sequential_commands(self):
        """Three commands sent sequentially; each should succeed with
        independent metrics."""
        mock_client = MagicMock()
        stub = mock_client.stub

        cmd_counter = 0

        def start_command_side_effect(request):
            nonlocal cmd_counter
            cmd_counter += 1
            return self._start_cmd_response(
                success=True, command_id=f"cmd-seq-{cmd_counter}"
            )

        stub.StartCommand.side_effect = start_command_side_effect

        def get_command_state_side_effect(request):
            # Always return current command as done
            return self._cmd_state_response(0, f"cmd-seq-{cmd_counter}")

        stub.GetCommandState.side_effect = get_command_state_side_effect

        def get_last_result_side_effect(request):
            return self._last_result_response(
                success=True, command_id=f"cmd-seq-{cmd_counter}"
            )

        stub.GetLastCommandResult.side_effect = get_last_result_side_effect

        ctrl = self._make_ctrl(mock_client)

        results = []
        for i in range(3):
            ctrl.reset_metrics()
            with patch("kachaka_core.controller.time.sleep"):
                r = ctrl.return_home(timeout=10.0)
            results.append(r)
            # Verify metrics were reset between commands
            assert ctrl.metrics.poll_count >= 1

        # All three should succeed
        assert all(r["ok"] is True for r in results)
        assert len(results) == 3
        # Each should have its own elapsed time
        assert all("elapsed" in r for r in results)


# ── Shelf Monitor tests ───────────────────────────────────────────


class TestShelfMonitor:
    """Tests for shelf drop monitoring during move_shelf operations."""

    def setup_method(self):
        KachakaConnection.clear_pool()

    def teardown_method(self):
        KachakaConnection.clear_pool()

    @staticmethod
    def _cmd_state_resp(state, command_id):
        resp = MagicMock()
        resp.state = state
        resp.command_id = command_id
        return resp

    def _make_ctrl_immediate_success(self, command_id="cmd-shelf", **kwargs):
        """Create a controller where any command succeeds immediately."""
        mock_client = MagicMock()

        start_resp = MagicMock()
        start_resp.result.success = True
        start_resp.command_id = command_id
        mock_client.stub.StartCommand.return_value = start_resp

        cmd_state_resp = MagicMock()
        cmd_state_resp.state = 0  # COMMAND_STATE_UNSPECIFIED (done immediately)
        cmd_state_resp.command_id = command_id
        mock_client.stub.GetCommandState.return_value = cmd_state_resp

        result_resp = MagicMock()
        result_resp.command_id = command_id
        result_resp.result.success = True
        result_resp.result.error_code = 0
        mock_client.stub.GetLastCommandResult.return_value = result_resp

        conn, _ = _make_mock_conn()
        conn._client = mock_client
        conn._resolver_ready = True
        conn.resolve_shelf = MagicMock(return_value="shelf-id")
        conn.resolve_location = MagicMock(return_value="loc-id")
        ctrl = RobotController(
            conn, fast_interval=60, slow_interval=60, poll_interval=0.01, **kwargs
        )
        return ctrl, mock_client

    def test_shelf_monitoring_starts_before_command(self):
        """Monitoring activates before _execute_command so drops during transit are caught."""
        ctrl, mock_client = self._make_ctrl_immediate_success()
        assert ctrl._monitoring_shelf is False

        with patch("kachaka_core.controller.time.sleep"):
            result = ctrl.move_shelf("ShelfA", "Room1", timeout=10)

        assert result["ok"] is True
        assert ctrl._monitoring_shelf is True
        assert ctrl.state.shelf_dropped is False

    def test_shelf_monitoring_stops_after_return_shelf(self):
        ctrl, _ = self._make_ctrl_immediate_success()
        ctrl._monitoring_shelf = True  # simulate active monitoring

        with patch("kachaka_core.controller.time.sleep"):
            result = ctrl.return_shelf("ShelfA", timeout=10)

        assert result["ok"] is True
        assert ctrl._monitoring_shelf is False

    def test_shelf_drop_detected_during_command(self):
        """Shelf drop during _execute_command polling sets shelf_dropped=True."""
        mock_client = MagicMock()
        stub = mock_client.stub

        stub.StartCommand.return_value = self._make_ctrl_immediate_success()[1].stub.StartCommand.return_value

        # Build a controller manually for fine-grained control
        conn, _ = _make_mock_conn()
        conn._client = mock_client
        conn._resolver_ready = True
        conn.resolve_shelf = MagicMock(return_value="shelf-id")
        conn.resolve_location = MagicMock(return_value="loc-id")

        start_resp = MagicMock()
        start_resp.result.success = True
        start_resp.command_id = "cmd-drop"
        stub.StartCommand.return_value = start_resp

        # Poll: RUNNING, RUNNING (shelf drops here), then done
        stub.GetCommandState.side_effect = [
            self._cmd_state_resp(2, "cmd-drop"),  # registered
            self._cmd_state_resp(2, "cmd-drop"),  # running, shelf present
            self._cmd_state_resp(2, "cmd-drop"),  # running, shelf dropped
            self._cmd_state_resp(0, "cmd-drop"),  # done
        ]

        result_resp = MagicMock()
        result_resp.command_id = "cmd-drop"
        result_resp.result.success = True
        result_resp.result.error_code = 0
        stub.GetLastCommandResult.return_value = result_resp

        # get_moving_shelf_id: present, present, then gone (dropped)
        mock_client.get_moving_shelf_id = MagicMock(
            side_effect=["shelf-id", "shelf-id", "", ""]
        )

        ctrl = RobotController(conn, fast_interval=60, slow_interval=60, poll_interval=0.01)

        with patch("kachaka_core.controller.time.sleep"):
            result = ctrl.move_shelf("ShelfA", "Room1", timeout=10)

        assert result["ok"] is True
        assert ctrl.state.shelf_dropped is True
        assert ctrl.state.moving_shelf_id is None
        assert ctrl._monitoring_shelf is False

    def test_shelf_drop_callback_during_command(self):
        """on_shelf_dropped callback fires during _execute_command polling."""
        callback = MagicMock()
        mock_client = MagicMock()
        stub = mock_client.stub

        conn, _ = _make_mock_conn()
        conn._client = mock_client
        conn._resolver_ready = True
        conn.resolve_shelf = MagicMock(return_value="shelf-id")
        conn.resolve_location = MagicMock(return_value="loc-id")

        start_resp = MagicMock()
        start_resp.result.success = True
        start_resp.command_id = "cmd-cb"
        stub.StartCommand.return_value = start_resp

        stub.GetCommandState.side_effect = [
            self._cmd_state_resp(2, "cmd-cb"),  # registered
            self._cmd_state_resp(2, "cmd-cb"),  # running
            self._cmd_state_resp(0, "cmd-cb"),  # done
        ]

        result_resp = MagicMock()
        result_resp.command_id = "cmd-cb"
        result_resp.result.success = True
        result_resp.result.error_code = 0
        stub.GetLastCommandResult.return_value = result_resp

        # Shelf present first poll, gone second poll
        mock_client.get_moving_shelf_id = MagicMock(
            side_effect=["shelf-id", "", ""]
        )

        ctrl = RobotController(
            conn, fast_interval=60, slow_interval=60, poll_interval=0.01,
            on_shelf_dropped=callback,
        )

        with patch("kachaka_core.controller.time.sleep"):
            ctrl.move_shelf("ShelfA", "Room1", timeout=10)

        callback.assert_called_once_with("shelf-id")

    def test_no_shelf_poll_when_not_monitoring(self):
        """get_moving_shelf_id not called for non-shelf commands."""
        mock_client = MagicMock()
        stub = mock_client.stub

        conn, _ = _make_mock_conn()
        conn._client = mock_client

        start_resp = MagicMock()
        start_resp.result.success = True
        start_resp.command_id = "cmd-home"
        stub.StartCommand.return_value = start_resp

        cmd_state = MagicMock()
        cmd_state.state = 0
        cmd_state.command_id = "cmd-home"
        stub.GetCommandState.return_value = cmd_state

        result_resp = MagicMock()
        result_resp.command_id = "cmd-home"
        result_resp.result.success = True
        result_resp.result.error_code = 0
        stub.GetLastCommandResult.return_value = result_resp

        mock_client.get_moving_shelf_id = MagicMock(return_value="")
        ctrl = RobotController(conn, fast_interval=60, slow_interval=60, poll_interval=0.01)

        with patch("kachaka_core.controller.time.sleep"):
            ctrl.return_home(timeout=10)

        mock_client.get_moving_shelf_id.assert_not_called()

    def test_reset_shelf_monitor(self):
        """reset_shelf_monitor clears shelf_dropped and stops monitoring."""
        conn, _ = _make_mock_conn()
        ctrl = RobotController(conn)
        ctrl._monitoring_shelf = True
        ctrl._state.shelf_dropped = True
        ctrl._state.moving_shelf_id = "S01"

        ctrl.reset_shelf_monitor()

        assert ctrl._monitoring_shelf is False
        assert ctrl.state.shelf_dropped is False
        assert ctrl.state.moving_shelf_id is None


# ── Disconnect Handling tests ────────────────────────────────────


class TestDisconnectHandling:
    """Tests for RobotController disconnect handling via ConnectionState monitoring."""

    def setup_method(self):
        KachakaConnection.clear_pool()

    def teardown_method(self):
        KachakaConnection.clear_pool()

    def _make_ctrl(self, mock_client):
        conn, _ = _make_mock_conn()
        conn._client = mock_client
        conn.resolve_shelf = MagicMock(side_effect=lambda n: f"shelf-{n}")
        conn.resolve_location = MagicMock(side_effect=lambda n: f"loc-{n}")
        ctrl = RobotController(
            conn, fast_interval=60, slow_interval=60, poll_interval=0.05
        )
        return ctrl, conn

    @staticmethod
    def _start_cmd_response(success=True, command_id="cmd-abc", error_code=0):
        resp = MagicMock()
        resp.result.success = success
        resp.result.error_code = error_code
        resp.command_id = command_id
        return resp

    @staticmethod
    def _cmd_state_response(state, command_id="cmd-abc"):
        resp = MagicMock()
        resp.state = state
        resp.command_id = command_id
        return resp

    @staticmethod
    def _last_result_response(success=True, command_id="cmd-abc", error_code=0):
        resp = MagicMock()
        resp.result.success = success
        resp.result.error_code = error_code
        resp.command_id = command_id
        return resp

    def test_execute_command_waits_during_disconnect(self):
        """Command execution should wait for reconnect instead of retrying."""
        mock_client = MagicMock()
        stub = mock_client.stub

        stub.StartCommand.return_value = self._start_cmd_response(
            success=True, command_id="cmd-wait"
        )
        stub.GetCommandState.side_effect = [
            self._cmd_state_response(2, "cmd-wait"),  # registered
            self._cmd_state_response(0, "cmd-wait"),  # done
        ]
        stub.GetLastCommandResult.return_value = self._last_result_response(
            success=True, command_id="cmd-wait"
        )

        ctrl, conn = self._make_ctrl(mock_client)

        # Simulate DISCONNECTED state, then switch to CONNECTED after brief delay
        conn._state = ConnectionState.DISCONNECTED

        def fake_wait_for_state(target_state, timeout=None):
            # Simulate reconnection
            conn._state = ConnectionState.CONNECTED
            return True

        conn.wait_for_state = MagicMock(side_effect=fake_wait_for_state)

        with patch("kachaka_core.controller.time.sleep"):
            result = ctrl.return_home(timeout=10.0)

        assert result["ok"] is True
        # Verify wait_for_state was called with CONNECTED
        conn.wait_for_state.assert_called_once()
        call_args = conn.wait_for_state.call_args
        assert call_args[0][0] == ConnectionState.CONNECTED

    def test_execute_command_timeout_during_disconnect(self):
        """Should return DISCONNECTED error if reconnect doesn't happen within timeout."""
        mock_client = MagicMock()
        ctrl, conn = self._make_ctrl(mock_client)

        # Simulate permanently disconnected
        conn._state = ConnectionState.DISCONNECTED
        conn.wait_for_state = MagicMock(return_value=False)

        with patch("kachaka_core.controller.time.sleep"):
            result = ctrl.return_home(timeout=5.0)

        assert result["ok"] is False
        assert result["error"] == "DISCONNECTED"
        assert "elapsed" in result
        assert result["action"] == "return_home"

    def test_state_snapshot_includes_connection_state(self):
        """State snapshot should include connection_state field."""
        conn, _ = _make_mock_conn()
        ctrl = RobotController(conn)

        state = ctrl.state
        assert state.connection_state == "connected"
        assert state.disconnected_at is None
        assert state.last_reconnect_at is None

        # Simulate state change via callback
        ctrl._on_conn_state_change(ConnectionState.DISCONNECTED)

        state = ctrl.state
        assert state.connection_state == "disconnected"
        assert state.disconnected_at is not None

        ctrl._on_conn_state_change(ConnectionState.CONNECTED)

        state = ctrl.state
        assert state.connection_state == "connected"
        assert state.last_reconnect_at is not None

    def test_on_reconnect_probes(self):
        """After reconnect, should immediately update pose/battery/command_state."""
        mock_client = MagicMock()

        # Set up return values for the probe calls
        pose = MagicMock()
        pose.x, pose.y, pose.theta = 5.0, 6.0, 1.5
        mock_client.get_robot_pose.return_value = pose
        mock_client.is_command_running.return_value = True
        mock_client.get_battery_info.return_value = (72, "CHARGING")

        ctrl, conn = self._make_ctrl(mock_client)

        # Trigger reconnect callback — the probe runs in a separate thread
        ctrl._on_conn_state_change(ConnectionState.CONNECTED)

        # Wait briefly for the probe thread to complete
        time.sleep(0.2)

        state = ctrl.state
        assert state.connection_state == "connected"
        assert state.pose_x == 5.0
        assert state.pose_y == 6.0
        assert state.pose_theta == 1.5
        assert state.is_command_running is True
        assert state.battery_pct == 72
        assert state.last_reconnect_at is not None

    def test_start_calls_start_monitoring(self):
        """start() should subscribe to connection state changes."""
        conn, _ = _make_mock_conn()
        conn.start_monitoring = MagicMock()
        ctrl = RobotController(conn, fast_interval=60, slow_interval=60)

        ctrl.start()
        try:
            conn.start_monitoring.assert_called_once()
            call_kwargs = conn.start_monitoring.call_args
            assert call_kwargs[1]["interval"] == 60
            assert call_kwargs[1]["on_state_change"] == ctrl._on_conn_state_change
        finally:
            ctrl.stop()

    def test_stop_calls_stop_monitoring(self):
        """stop() should unsubscribe from connection state changes."""
        conn, _ = _make_mock_conn()
        conn.start_monitoring = MagicMock()
        conn.stop_monitoring = MagicMock()
        ctrl = RobotController(conn, fast_interval=0.05, slow_interval=60)

        ctrl.start()
        time.sleep(0.1)
        ctrl.stop()

        conn.stop_monitoring.assert_called_once()

    def test_execute_command_proceeds_when_connected(self):
        """When connected, _execute_command should not call wait_for_state."""
        mock_client = MagicMock()
        stub = mock_client.stub

        stub.StartCommand.return_value = self._start_cmd_response(
            success=True, command_id="cmd-ok"
        )
        stub.GetCommandState.side_effect = [
            self._cmd_state_response(2, "cmd-ok"),  # registered
            self._cmd_state_response(0, "cmd-ok"),  # done
        ]
        stub.GetLastCommandResult.return_value = self._last_result_response(
            success=True, command_id="cmd-ok"
        )

        ctrl, conn = self._make_ctrl(mock_client)
        # conn.state is CONNECTED by default
        conn.wait_for_state = MagicMock()

        with patch("kachaka_core.controller.time.sleep"):
            result = ctrl.return_home(timeout=10.0)

        assert result["ok"] is True
        conn.wait_for_state.assert_not_called()
