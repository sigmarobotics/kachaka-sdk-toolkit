"""Tests for kachaka_core.commands — movement, shelf, speech, retry."""

from __future__ import annotations

import tempfile
from unittest.mock import MagicMock, patch

import grpc
import pytest

from kachaka_core.commands import KachakaCommands
from kachaka_core.connection import KachakaConnection


@pytest.fixture(autouse=True)
def _clean_pool():
    KachakaConnection.clear_pool()
    yield
    KachakaConnection.clear_pool()


def _make_result(success: bool = True, error_code: int = 0):
    r = MagicMock()
    r.success = success
    r.error_code = error_code
    return r


def _make_conn(mock_client):
    """Create a KachakaConnection with a mocked client."""
    with patch("kachaka_core.connection.KachakaApiClient", return_value=mock_client):
        conn = KachakaConnection.get("test-robot")
    return conn


class TestMovement:
    def test_move_to_location_success(self):
        mock_client = MagicMock()
        mock_client.move_to_location.return_value = _make_result(True)
        conn = _make_conn(mock_client)

        cmds = KachakaCommands(conn)
        result = cmds.move_to_location("Kitchen")

        assert result["ok"] is True
        assert result["action"] == "move_to_location"
        assert result["target"] == "Kitchen"

    def test_move_to_location_failure(self):
        mock_client = MagicMock()
        mock_client.move_to_location.return_value = _make_result(False, error_code=101)
        conn = _make_conn(mock_client)

        cmds = KachakaCommands(conn)
        result = cmds.move_to_location("Nowhere")

        assert result["ok"] is False
        assert result["error_code"] == 101

    def test_move_to_pose(self):
        mock_client = MagicMock()
        mock_client.move_to_pose.return_value = _make_result(True)
        conn = _make_conn(mock_client)

        cmds = KachakaCommands(conn)
        result = cmds.move_to_pose(1.0, 2.0, 0.5)

        assert result["ok"] is True
        mock_client.move_to_pose.assert_called_once_with(
            1.0, 2.0, 0.5, cancel_all=True, tts_on_success="", title=""
        )

    def test_return_home(self):
        mock_client = MagicMock()
        mock_client.return_home.return_value = _make_result(True)
        conn = _make_conn(mock_client)

        cmds = KachakaCommands(conn)
        result = cmds.return_home()

        assert result["ok"] is True
        assert result["action"] == "return_home"


class TestShelfOps:
    def test_move_shelf(self):
        mock_client = MagicMock()
        mock_client.move_shelf.return_value = _make_result(True)
        conn = _make_conn(mock_client)

        cmds = KachakaCommands(conn)
        result = cmds.move_shelf("Shelf A", "Room 1")

        assert result["ok"] is True
        assert "Shelf A" in result["target"]

    def test_dock_shelf(self):
        mock_client = MagicMock()
        mock_client.dock_shelf.return_value = _make_result(True)
        conn = _make_conn(mock_client)

        result = KachakaCommands(conn).dock_shelf()
        assert result["ok"] is True

    def test_dock_any_shelf_with_registration(self):
        mock_client = MagicMock()
        mock_client.dock_any_shelf_with_registration.return_value = _make_result(True)
        conn = _make_conn(mock_client)

        result = KachakaCommands(conn).dock_any_shelf_with_registration("L01")
        assert result["ok"] is True
        assert result["action"] == "dock_any_shelf_with_registration"
        assert result["target"] == "L01"
        mock_client.dock_any_shelf_with_registration.assert_called_once()

    def test_dock_any_shelf_with_registration_forward(self):
        mock_client = MagicMock()
        mock_client.dock_any_shelf_with_registration.return_value = _make_result(True)
        conn = _make_conn(mock_client)

        result = KachakaCommands(conn).dock_any_shelf_with_registration(
            "L01", dock_forward=True
        )
        assert result["ok"] is True
        args, kwargs = mock_client.dock_any_shelf_with_registration.call_args
        assert args[1] is True  # dock_forward

    def test_reset_shelf_pose(self):
        mock_client = MagicMock()
        mock_client.reset_shelf_pose.return_value = _make_result(True)
        conn = _make_conn(mock_client)

        result = KachakaCommands(conn).reset_shelf_pose("Shelf A")
        assert result["ok"] is True
        assert result["target"] == "Shelf A"


class TestSpeech:
    def test_speak(self):
        mock_client = MagicMock()
        mock_client.speak.return_value = _make_result(True)
        conn = _make_conn(mock_client)

        result = KachakaCommands(conn).speak("Hello")
        assert result["ok"] is True
        assert result["target"] == "Hello"

    def test_set_volume_clamped(self):
        mock_client = MagicMock()
        mock_client.set_speaker_volume.return_value = _make_result(True)
        conn = _make_conn(mock_client)

        KachakaCommands(conn).set_speaker_volume(15)
        mock_client.set_speaker_volume.assert_called_once_with(10)


class TestRetry:
    def test_retries_on_unavailable(self):
        mock_client = MagicMock()

        rpc_error = grpc.RpcError()
        rpc_error.code = lambda: grpc.StatusCode.UNAVAILABLE
        rpc_error.details = lambda: "transient"

        mock_client.speak.side_effect = [rpc_error, rpc_error, _make_result(True)]
        conn = _make_conn(mock_client)

        with patch("kachaka_core.error_handling.time.sleep"):
            result = KachakaCommands(conn).speak("test")

        assert result["ok"] is True
        assert mock_client.speak.call_count == 3

    def test_no_retry_on_invalid_argument(self):
        mock_client = MagicMock()

        rpc_error = grpc.RpcError()
        rpc_error.code = lambda: grpc.StatusCode.INVALID_ARGUMENT
        rpc_error.details = lambda: "bad param"

        mock_client.speak.side_effect = rpc_error
        conn = _make_conn(mock_client)

        result = KachakaCommands(conn).speak("test")

        assert result["ok"] is False
        assert result["retryable"] is False
        assert mock_client.speak.call_count == 1

    def test_exhausted_retries(self):
        mock_client = MagicMock()

        rpc_error = grpc.RpcError()
        rpc_error.code = lambda: grpc.StatusCode.UNAVAILABLE
        rpc_error.details = lambda: "down"

        mock_client.speak.side_effect = rpc_error
        conn = _make_conn(mock_client)

        with patch("kachaka_core.error_handling.time.sleep"):
            result = KachakaCommands(conn).speak("test")

        assert result["ok"] is False
        assert result["retryable"] is True
        assert result["attempts"] == 3


class TestShortcut:
    def test_start_shortcut_success(self):
        mock_client = MagicMock()
        mock_client.start_shortcut_command.return_value = _make_result(True)
        conn = _make_conn(mock_client)

        result = KachakaCommands(conn).start_shortcut("abc-123")
        assert result["ok"] is True
        assert result["action"] == "start_shortcut"
        assert result["target"] == "abc-123"
        mock_client.start_shortcut_command.assert_called_once_with(
            "abc-123", cancel_all=True
        )

    def test_start_shortcut_failure(self):
        mock_client = MagicMock()
        mock_client.start_shortcut_command.return_value = _make_result(False, error_code=12506)
        conn = _make_conn(mock_client)

        result = KachakaCommands(conn).start_shortcut("bad-id")
        assert result["ok"] is False
        assert result["error_code"] == 12506


class TestMapManagement:
    def test_export_map_success(self):
        mock_client = MagicMock()
        mock_client.export_map.return_value = _make_result(True)
        conn = _make_conn(mock_client)

        with tempfile.NamedTemporaryFile(suffix=".bin") as f:
            # Write some data so os.path.getsize works
            f.write(b"fake map data")
            f.flush()
            result = KachakaCommands(conn).export_map("map-123", f.name)

        assert result["ok"] is True
        assert result["action"] == "export_map"
        assert result["map_id"] == "map-123"
        assert result["size_bytes"] > 0

    def test_export_map_failure(self):
        mock_client = MagicMock()
        mock_client.export_map.return_value = _make_result(False, error_code=999)
        conn = _make_conn(mock_client)

        result = KachakaCommands(conn).export_map("bad-id", "/tmp/out.bin")
        assert result["ok"] is False
        assert result["error_code"] == 999

    def test_import_map_success(self):
        mock_client = MagicMock()
        mock_client.import_map.return_value = (_make_result(True), "new-map-id")
        conn = _make_conn(mock_client)

        result = KachakaCommands(conn).import_map("/tmp/exported.bin")
        assert result["ok"] is True
        assert result["action"] == "import_map"
        assert result["map_id"] == "new-map-id"

    def test_import_map_failure(self):
        mock_client = MagicMock()
        mock_client.import_map.return_value = (_make_result(False, error_code=500), "")
        conn = _make_conn(mock_client)

        result = KachakaCommands(conn).import_map("/tmp/bad.bin")
        assert result["ok"] is False

    def test_import_image_as_map_success(self):
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.result.success = True
        mock_response.map_id = "img-map-id"
        conn = _make_conn(mock_client)
        # Re-assign stub after _make_conn (which replaces it with a real one)
        mock_stub = MagicMock()
        mock_stub.ImportImageAsMap.return_value = mock_response
        mock_client.stub = mock_stub

        with tempfile.NamedTemporaryFile(suffix=".png") as f:
            f.write(b"\x89PNG\r\n\x1a\n" + b"\x00" * 100)
            f.flush()
            result = KachakaCommands(conn).import_image_as_map(
                f.name, resolution=0.025, charger_x=5.0, charger_y=1.0,
            )

        assert result["ok"] is True
        assert result["action"] == "import_image_as_map"
        assert result["map_id"] == "img-map-id"
        assert result["resolution"] == 0.025
        assert result["charger_pose"]["x"] == 5.0
        mock_stub.ImportImageAsMap.assert_called_once()

    def test_import_image_as_map_failure(self):
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.result.success = False
        mock_response.result.error_code = 12508
        conn = _make_conn(mock_client)
        mock_stub = MagicMock()
        mock_stub.ImportImageAsMap.return_value = mock_response
        mock_client.stub = mock_stub

        with tempfile.NamedTemporaryFile(suffix=".png") as f:
            f.write(b"\x89PNG\r\n\x1a\n")
            f.flush()
            result = KachakaCommands(conn).import_image_as_map(
                f.name, resolution=0.05, charger_x=0.0, charger_y=0.0,
            )

        assert result["ok"] is False

    def test_import_image_as_map_file_not_found(self):
        mock_client = MagicMock()
        conn = _make_conn(mock_client)

        result = KachakaCommands(conn).import_image_as_map(
            "/tmp/nonexistent.png", resolution=0.025, charger_x=0.0, charger_y=0.0,
        )
        assert result["ok"] is False
        assert "No such file" in result["error"]


class TestSwitchMap:
    def test_switch_map_success(self):
        mock_client = MagicMock()
        mock_client.switch_map.return_value = _make_result(True)
        conn = _make_conn(mock_client)

        result = KachakaCommands(conn).switch_map("map-456")
        assert result["ok"] is True
        assert result["action"] == "switch_map"
        assert result["target"] == "map-456"
        mock_client.switch_map.assert_called_once_with(
            "map-456", pose=None, inherit_docking_state_and_docked_shelf=False,
        )

    def test_switch_map_with_pose(self):
        mock_client = MagicMock()
        mock_client.switch_map.return_value = _make_result(True)
        conn = _make_conn(mock_client)

        result = KachakaCommands(conn).switch_map(
            "map-456", pose_x=1.0, pose_y=2.0, pose_theta=0.5,
        )
        assert result["ok"] is True
        mock_client.switch_map.assert_called_once_with(
            "map-456",
            pose={"x": 1.0, "y": 2.0, "theta": 0.5},
            inherit_docking_state_and_docked_shelf=False,
        )

    def test_switch_map_failure(self):
        mock_client = MagicMock()
        mock_client.switch_map.return_value = _make_result(False, error_code=999)
        conn = _make_conn(mock_client)

        result = KachakaCommands(conn).switch_map("bad-map")
        assert result["ok"] is False
        assert result["error_code"] == 999

    def test_switch_map_exception(self):
        mock_client = MagicMock()
        mock_client.switch_map.side_effect = Exception("connection lost")
        conn = _make_conn(mock_client)

        result = KachakaCommands(conn).switch_map("map-456")
        assert result["ok"] is False
        assert "connection lost" in result["error"]


class TestTorch:
    def test_set_front_torch(self):
        mock_client = MagicMock()
        conn = _make_conn(mock_client)
        # Re-assign stub after _make_conn (which replaces it with a real one)
        mock_stub = MagicMock()
        mock_response = MagicMock()
        mock_response.result = _make_result(True)
        mock_stub.SetFrontTorchIntensity.return_value = mock_response
        mock_client.stub = mock_stub

        result = KachakaCommands(conn).set_front_torch(128)
        assert result["ok"] is True
        assert result["action"] == "set_front_torch"
        mock_stub.SetFrontTorchIntensity.assert_called_once()

    def test_set_front_torch_clamped(self):
        mock_client = MagicMock()
        conn = _make_conn(mock_client)
        mock_stub = MagicMock()
        mock_response = MagicMock()
        mock_response.result = _make_result(True)
        mock_stub.SetFrontTorchIntensity.return_value = mock_response
        mock_client.stub = mock_stub

        KachakaCommands(conn).set_front_torch(300)
        call_args = mock_stub.SetFrontTorchIntensity.call_args
        assert call_args[0][0].intensity == 255

    def test_set_back_torch(self):
        mock_client = MagicMock()
        conn = _make_conn(mock_client)
        mock_stub = MagicMock()
        mock_response = MagicMock()
        mock_response.result = _make_result(True)
        mock_stub.SetBackTorchIntensity.return_value = mock_response
        mock_client.stub = mock_stub

        result = KachakaCommands(conn).set_back_torch(64)
        assert result["ok"] is True
        assert result["action"] == "set_back_torch"


class TestLaserScan:
    def test_activate_laser_scan(self):
        mock_client = MagicMock()
        conn = _make_conn(mock_client)
        mock_stub = MagicMock()
        mock_response = MagicMock()
        mock_response.result = _make_result(True)
        mock_stub.ActivateLaserScan.return_value = mock_response
        mock_client.stub = mock_stub

        result = KachakaCommands(conn).activate_laser_scan(5.0)
        assert result["ok"] is True
        assert result["action"] == "activate_laser_scan"
        call_args = mock_stub.ActivateLaserScan.call_args
        assert call_args[0][0].duration_sec == 5.0


class TestAutoHoming:
    def test_set_auto_homing_enabled(self):
        mock_client = MagicMock()
        mock_client.set_auto_homing_enabled.return_value = _make_result(True)
        conn = _make_conn(mock_client)

        result = KachakaCommands(conn).set_auto_homing(True)
        assert result["ok"] is True
        assert result["action"] == "set_auto_homing"
        mock_client.set_auto_homing_enabled.assert_called_once_with(True)

    def test_set_auto_homing_disabled(self):
        mock_client = MagicMock()
        mock_client.set_auto_homing_enabled.return_value = _make_result(True)
        conn = _make_conn(mock_client)

        result = KachakaCommands(conn).set_auto_homing(False)
        assert result["ok"] is True
        mock_client.set_auto_homing_enabled.assert_called_once_with(False)


class TestManualControlShelfReg:
    def test_manual_control_with_shelf_registration(self):
        mock_client = MagicMock()
        conn = _make_conn(mock_client)
        # Re-assign stub after _make_conn (which replaces it with a real one)
        mock_stub = MagicMock()
        mock_response = MagicMock()
        mock_response.result = _make_result(True)
        mock_stub.SetManualControlEnabled.return_value = mock_response
        mock_client.stub = mock_stub

        result = KachakaCommands(conn).set_manual_control(
            True, use_shelf_registration=True,
        )
        assert result["ok"] is True
        call_args = mock_stub.SetManualControlEnabled.call_args
        req = call_args[0][0]
        assert req.enable is True
        assert req.use_shelf_registration is True

    def test_manual_control_without_shelf_registration(self):
        mock_client = MagicMock()
        mock_client.set_manual_control_enabled.return_value = _make_result(True)
        conn = _make_conn(mock_client)

        result = KachakaCommands(conn).set_manual_control(True)
        assert result["ok"] is True
        mock_client.set_manual_control_enabled.assert_called_once_with(True)


class TestMoveShelfAdvanced:
    def test_move_shelf_undock_on_destination(self):
        mock_client = MagicMock()
        conn = _make_conn(mock_client)
        # Re-assign stub after _make_conn (which replaces it with a real one)
        mock_stub = MagicMock()
        # Mock GetCommandState for cursor
        cmd_state_resp = MagicMock()
        cmd_state_resp.metadata.cursor = 100
        mock_stub.GetCommandState.return_value = cmd_state_resp
        # Mock StartCommand
        start_resp = MagicMock()
        start_resp.result.success = True
        start_resp.command_id = "cmd-1"
        mock_stub.StartCommand.return_value = start_resp
        # Mock GetLastCommandResult
        last_resp = MagicMock()
        last_resp.metadata.cursor = 200
        last_resp.command_id = "cmd-1"
        mock_stub.GetLastCommandResult.return_value = last_resp
        # Mock get_last_command_result for final result
        mock_client.get_last_command_result.return_value = (_make_result(True), MagicMock())
        mock_client.stub = mock_stub

        result = KachakaCommands(conn).move_shelf(
            "Shelf A", "Room 1", undock_on_destination=True,
        )
        assert result["ok"] is True
        # Verify StartCommand was called (not sdk.move_shelf)
        mock_stub.StartCommand.assert_called_once()
        req = mock_stub.StartCommand.call_args[0][0]
        assert req.command.move_shelf_command.undock_on_destination is True

    def test_move_shelf_default_uses_sdk(self):
        mock_client = MagicMock()
        mock_client.move_shelf.return_value = _make_result(True)
        conn = _make_conn(mock_client)

        result = KachakaCommands(conn).move_shelf("Shelf A", "Room 1")
        assert result["ok"] is True
        mock_client.move_shelf.assert_called_once()


class TestCancelCommand:
    def test_cancel_success(self):
        mock_client = MagicMock()
        mock_client.cancel_command.return_value = (_make_result(True), MagicMock())
        conn = _make_conn(mock_client)

        result = KachakaCommands(conn).cancel_command()
        assert result["ok"] is True


class TestStop:
    def test_emergency_stop(self):
        mock_client = MagicMock()
        conn = _make_conn(mock_client)

        result = KachakaCommands(conn).stop()
        assert result["ok"] is True
        mock_client.set_robot_stop.assert_called_once()


class TestPollUntilComplete:
    def test_immediate_completion(self):
        mock_client = MagicMock()
        mock_client.is_command_running.return_value = False
        mock_client.get_last_command_result.return_value = (
            _make_result(True),
            MagicMock(),
        )
        conn = _make_conn(mock_client)

        result = KachakaCommands(conn).poll_until_complete(timeout=5.0)
        assert result["ok"] is True

    def test_timeout(self):
        mock_client = MagicMock()
        mock_client.is_command_running.return_value = True
        conn = _make_conn(mock_client)

        with patch("kachaka_core.commands.time.sleep"):
            with patch("kachaka_core.commands.time.time", side_effect=[0, 0, 999]):
                result = KachakaCommands(conn).poll_until_complete(timeout=1.0)

        assert result["ok"] is False
        assert result["error"] == "timeout"


def _wire_advanced_stub(mock_client) -> MagicMock:
    """Wire StartCommand / GetCommandState / GetLastCommandResult for ``_start_command_advanced``."""
    mock_stub = MagicMock()
    cmd_state_resp = MagicMock()
    cmd_state_resp.metadata.cursor = 100
    mock_stub.GetCommandState.return_value = cmd_state_resp
    start_resp = MagicMock()
    start_resp.result.success = True
    start_resp.command_id = "cmd-mute"
    mock_stub.StartCommand.return_value = start_resp
    last_resp = MagicMock()
    last_resp.metadata.cursor = 200
    last_resp.command_id = "cmd-mute"
    mock_stub.GetLastCommandResult.return_value = last_resp
    mock_client.get_last_command_result.return_value = (_make_result(True), MagicMock())
    mock_client.stub = mock_stub
    return mock_stub


class TestMoveForwardMuteSensors:
    def test_default_uses_sdk(self):
        mock_client = MagicMock()
        mock_client.move_forward.return_value = _make_result(True)
        conn = _make_conn(mock_client)

        result = KachakaCommands(conn).move_forward(0.5)
        assert result["ok"] is True
        mock_client.move_forward.assert_called_once_with(0.5, speed=0.1)

    def test_mute_sensors_uses_start_command(self):
        mock_client = MagicMock()
        conn = _make_conn(mock_client)
        mock_stub = _wire_advanced_stub(mock_client)

        result = KachakaCommands(conn).move_forward(0.5, mute_sensors=True)
        assert result["ok"] is True
        mock_client.move_forward.assert_not_called()
        mock_stub.StartCommand.assert_called_once()
        req = mock_stub.StartCommand.call_args[0][0]
        assert req.command.move_forward_command.mute_sensors is True
        assert req.command.move_forward_command.distance_meter == 0.5


class TestMoveToLocationSource:
    def test_default_uses_sdk(self):
        mock_client = MagicMock()
        mock_client.move_to_location.return_value = _make_result(True)
        conn = _make_conn(mock_client)

        result = KachakaCommands(conn).move_to_location("Kitchen")
        assert result["ok"] is True
        mock_client.move_to_location.assert_called_once()

    def test_source_uses_start_command(self):
        mock_client = MagicMock()
        conn = _make_conn(mock_client)
        # resolve_location echoes input as id (resolver mock)
        with patch.object(conn, "resolve_location", side_effect=lambda n: f"id::{n}"):
            mock_stub = _wire_advanced_stub(mock_client)
            result = KachakaCommands(conn).move_to_location(
                "Kitchen", source_location_name="Lobby",
            )
        assert result["ok"] is True
        mock_client.move_to_location.assert_not_called()
        req = mock_stub.StartCommand.call_args[0][0]
        assert req.command.move_to_location_command.target_location_id == "id::Kitchen"
        assert req.command.move_to_location_command.source_location_id == "id::Lobby"


class TestMoveByVelocityMuted:
    def test_dispatches_command(self):
        mock_client = MagicMock()
        conn = _make_conn(mock_client)
        mock_stub = _wire_advanced_stub(mock_client)

        result = KachakaCommands(conn).move_by_velocity_muted(0.1, 5.0)
        assert result["ok"] is True
        req = mock_stub.StartCommand.call_args[0][0]
        cmd = req.command.move_by_velocity_with_muted_sensors_command
        assert cmd.signed_velocity == pytest.approx(0.1)
        assert cmd.move_duration_sec == pytest.approx(5.0)

    def test_clamps_velocity_and_duration(self):
        mock_client = MagicMock()
        conn = _make_conn(mock_client)
        mock_stub = _wire_advanced_stub(mock_client)

        KachakaCommands(conn).move_by_velocity_muted(99.0, 999.0)
        req = mock_stub.StartCommand.call_args[0][0]
        cmd = req.command.move_by_velocity_with_muted_sensors_command
        assert cmd.signed_velocity == pytest.approx(0.3)
        assert cmd.move_duration_sec == pytest.approx(30.0)


class TestSwitchMapInvalidation:
    def test_switch_map_invalidates_map_cache(self):
        mock = MagicMock()
        mock_result = MagicMock(success=True, error_code=0)
        mock.switch_map.return_value = mock_result
        with patch("kachaka_core.connection.KachakaApiClient", return_value=mock):
            KachakaConnection.clear_pool()
            conn = KachakaConnection.get("test-robot-sw")
            conn._cached_current_map_id = "old-map"
            conn._cached_map_list = [{"id": "old-map", "name": "Old"}]
            conn._cached_map_image = {"png_bytes": b"old"}

            cmds = KachakaCommands(conn)
            cmds.switch_map("new-map")

            assert conn._cached_current_map_id is None
            assert conn._cached_map_list is None
            assert conn._cached_map_image is None
            KachakaConnection.clear_pool()
