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


def _wire_start_command(mock_client, *, command_id: str = "cmd-1", success: bool = True,
                        error_code: int = 0) -> MagicMock:
    """Wire the stub so StartCommand-based dispatch succeeds (fire-and-accept)."""
    mock_stub = MagicMock()
    start_resp = MagicMock()
    start_resp.result.success = success
    start_resp.result.error_code = error_code
    start_resp.command_id = command_id
    mock_stub.StartCommand.return_value = start_resp
    mock_client.stub = mock_stub
    return mock_stub


class TestMovement:
    def test_move_to_location_success(self):
        mock_client = MagicMock()
        conn = _make_conn(mock_client)
        mock_stub = _wire_start_command(mock_client)

        cmds = KachakaCommands(conn)
        result = cmds.move_to_location("Kitchen")

        assert result["ok"] is True
        assert result["action"] == "move_to_location"
        assert result["target"] == "Kitchen"
        assert result["command_id"] == "cmd-1"
        req = mock_stub.StartCommand.call_args[0][0]
        assert req.command.move_to_location_command.target_location_id == "Kitchen"

    def test_move_to_location_failure(self):
        mock_client = MagicMock()
        conn = _make_conn(mock_client)
        _wire_start_command(mock_client, success=False, error_code=101)

        cmds = KachakaCommands(conn)
        result = cmds.move_to_location("Nowhere")

        assert result["ok"] is False
        assert result["error_code"] == 101

    def test_move_to_pose(self):
        mock_client = MagicMock()
        conn = _make_conn(mock_client)
        mock_stub = _wire_start_command(mock_client)

        cmds = KachakaCommands(conn)
        result = cmds.move_to_pose(1.0, 2.0, 0.5)

        assert result["ok"] is True
        assert result["command_id"] == "cmd-1"
        req = mock_stub.StartCommand.call_args[0][0]
        pose_cmd = req.command.move_to_pose_command
        assert pose_cmd.x == pytest.approx(1.0)
        assert pose_cmd.y == pytest.approx(2.0)
        assert pose_cmd.yaw == pytest.approx(0.5)

    def test_rotate_in_place(self):
        mock_client = MagicMock()
        conn = _make_conn(mock_client)
        mock_stub = _wire_start_command(mock_client)

        result = KachakaCommands(conn).rotate_in_place(1.57)
        assert result["ok"] is True
        assert result["command_id"] == "cmd-1"
        req = mock_stub.StartCommand.call_args[0][0]
        assert req.command.rotate_in_place_command.angle_radian == pytest.approx(1.57)

    def test_return_home(self):
        mock_client = MagicMock()
        conn = _make_conn(mock_client)
        mock_stub = _wire_start_command(mock_client)

        cmds = KachakaCommands(conn)
        result = cmds.return_home()

        assert result["ok"] is True
        assert result["action"] == "return_home"
        req = mock_stub.StartCommand.call_args[0][0]
        assert req.command.HasField("return_home_command")


class TestShelfOps:
    def test_move_shelf(self):
        mock_client = MagicMock()
        conn = _make_conn(mock_client)
        mock_stub = _wire_start_command(mock_client)

        cmds = KachakaCommands(conn)
        result = cmds.move_shelf("Shelf A", "Room 1")

        assert result["ok"] is True
        assert "Shelf A" in result["target"]
        req = mock_stub.StartCommand.call_args[0][0]
        assert req.command.move_shelf_command.target_shelf_id == "Shelf A"
        assert req.command.move_shelf_command.destination_location_id == "Room 1"

    def test_dock_shelf(self):
        mock_client = MagicMock()
        conn = _make_conn(mock_client)
        mock_stub = _wire_start_command(mock_client)

        result = KachakaCommands(conn).dock_shelf()
        assert result["ok"] is True
        req = mock_stub.StartCommand.call_args[0][0]
        assert req.command.HasField("dock_shelf_command")

    def test_dock_any_shelf_with_registration(self):
        mock_client = MagicMock()
        conn = _make_conn(mock_client)
        mock_stub = _wire_start_command(mock_client)

        result = KachakaCommands(conn).dock_any_shelf_with_registration("L01")
        assert result["ok"] is True
        assert result["action"] == "dock_any_shelf_with_registration"
        assert result["target"] == "L01"
        mock_stub.StartCommand.assert_called_once()

    def test_dock_any_shelf_with_registration_forward(self):
        mock_client = MagicMock()
        conn = _make_conn(mock_client)
        mock_stub = _wire_start_command(mock_client)

        result = KachakaCommands(conn).dock_any_shelf_with_registration(
            "L01", dock_forward=True
        )
        assert result["ok"] is True
        req = mock_stub.StartCommand.call_args[0][0]
        assert req.command.dock_any_shelf_with_registration_command.dock_forward is True

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


class TestSounds:
    @staticmethod
    def _wire(mock_client, rpc: str, *, success: bool = True, error_code: int = 0,
              sound_id: str = ""):
        """Wire a stub RPC to return a Result-bearing response (post _make_conn)."""
        mock_stub = MagicMock()
        resp = MagicMock()
        resp.result = _make_result(success, error_code)
        if sound_id:
            resp.sound_id = sound_id
        getattr(mock_stub, rpc).return_value = resp
        mock_client.stub = mock_stub
        return mock_stub

    def test_add_sound_from_bytes(self):
        mock_client = MagicMock()
        conn = _make_conn(mock_client)
        mock_stub = self._wire(mock_client, "AddSound", sound_id="snd-42")

        result = KachakaCommands(conn).add_sound("chime", data=b"RIFFwav")

        assert result["ok"] is True
        assert result["action"] == "add_sound"
        assert result["target"] == "chime"
        assert result["sound_id"] == "snd-42"
        req = mock_stub.AddSound.call_args[0][0]
        assert req.name == "chime"
        assert req.data == b"RIFFwav"

    def test_add_sound_from_path(self):
        mock_client = MagicMock()
        conn = _make_conn(mock_client)
        mock_stub = self._wire(mock_client, "AddSound", sound_id="snd-file")

        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
            f.write(b"AUDIODATA")
            path = f.name

        result = KachakaCommands(conn).add_sound("bell", path=path)

        assert result["ok"] is True
        assert result["sound_id"] == "snd-file"
        assert mock_stub.AddSound.call_args[0][0].data == b"AUDIODATA"

    def test_add_sound_no_data_skips_rpc(self):
        mock_client = MagicMock()
        conn = _make_conn(mock_client)
        mock_stub = self._wire(mock_client, "AddSound")

        result = KachakaCommands(conn).add_sound("empty")

        assert result["ok"] is False
        assert result["action"] == "add_sound"
        mock_stub.AddSound.assert_not_called()

    def test_play_sound(self):
        mock_client = MagicMock()
        conn = _make_conn(mock_client)
        mock_stub = self._wire(mock_client, "PlaySound")

        result = KachakaCommands(conn).play_sound("snd-42", loop=True)

        assert result["ok"] is True
        assert result["action"] == "play_sound"
        assert result["target"] == "snd-42"
        req = mock_stub.PlaySound.call_args[0][0]
        assert req.sound_id == "snd-42"
        assert req.loop is True

    def test_play_sound_failure_surfaces_error(self):
        mock_client = MagicMock()
        mock_client.get_error.return_value = []
        conn = _make_conn(mock_client)
        self._wire(mock_client, "PlaySound", success=False, error_code=42)

        result = KachakaCommands(conn).play_sound("bad-id")

        assert result["ok"] is False
        assert result["error_code"] == 42

    def test_stop_sound(self):
        mock_client = MagicMock()
        conn = _make_conn(mock_client)
        mock_stub = self._wire(mock_client, "StopSound")

        result = KachakaCommands(conn).stop_sound()

        assert result["ok"] is True
        assert result["action"] == "stop_sound"
        mock_stub.StopSound.assert_called_once()

    def test_delete_sound(self):
        mock_client = MagicMock()
        conn = _make_conn(mock_client)
        mock_stub = self._wire(mock_client, "DeleteSound")

        result = KachakaCommands(conn).delete_sound("snd-42")

        assert result["ok"] is True
        assert result["action"] == "delete_sound"
        assert result["target"] == "snd-42"
        assert mock_stub.DeleteSound.call_args[0][0].sound_id == "snd-42"


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


class TestLocalization:
    def test_localize(self):
        mock_client = MagicMock()
        conn = _make_conn(mock_client)
        mock_stub = _wire_start_command(mock_client)

        result = KachakaCommands(conn).localize()

        assert result["ok"] is True
        assert result["action"] == "localize"
        assert result["command_id"] == "cmd-1"
        req = mock_stub.StartCommand.call_args[0][0]
        assert req.command.HasField("localize_command")

    def test_localize_failure(self):
        mock_client = MagicMock()
        conn = _make_conn(mock_client)
        _wire_start_command(mock_client, success=False, error_code=10001)

        result = KachakaCommands(conn).localize()

        assert result["ok"] is False
        assert result["error_code"] == 10001

    def test_set_robot_pose(self):
        mock_client = MagicMock()
        mock_client.set_robot_pose.return_value = _make_result(True)
        conn = _make_conn(mock_client)

        result = KachakaCommands(conn).set_robot_pose(1.5, -2.0, 0.5)

        assert result["ok"] is True
        assert result["action"] == "set_robot_pose"
        assert "command_id" not in result
        mock_client.set_robot_pose.assert_called_once_with(
            {"x": 1.5, "y": -2.0, "theta": 0.5}
        )


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

    def test_move_shelf_default_no_undock(self):
        mock_client = MagicMock()
        conn = _make_conn(mock_client)
        mock_stub = _wire_start_command(mock_client)

        result = KachakaCommands(conn).move_shelf("Shelf A", "Room 1")
        assert result["ok"] is True
        req = mock_stub.StartCommand.call_args[0][0]
        assert req.command.move_shelf_command.undock_on_destination is False


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


def _state_resp(state, command_id=""):
    resp = MagicMock()
    resp.state = state
    resp.command_id = command_id
    return resp


def _last_resp(command_id, success=True, error_code=0):
    resp = MagicMock()
    resp.command_id = command_id
    resp.result.success = success
    resp.result.error_code = error_code
    return resp


class TestPollUntilComplete:
    """command_id-verified completion (2026-07-07 field incident regression).

    The robot's idle state is PENDING + empty command_id — identical to the
    registration window right after StartCommand. Completion must never be
    reported until GetLastCommandResult carries OUR command_id.
    """

    # COMMAND_STATE enum values (from kachaka_api proto)
    from kachaka_api.generated import kachaka_api_pb2 as pb2
    RUNNING = pb2.COMMAND_STATE_RUNNING
    PENDING = pb2.COMMAND_STATE_PENDING

    def _cmds_with_tracked_id(self, mock_client, command_id="cmd-42"):
        conn = _make_conn(mock_client)
        # Re-assign stub after _make_conn (which replaces it with a real one)
        mock_client.stub = MagicMock()
        cmds = KachakaCommands(conn)
        cmds._last_command_id = command_id
        return cmds

    def test_registration_window_is_not_completion(self):
        """Idle-looking PENDING + empty id + stale last result must keep polling."""
        mock_client = MagicMock()
        cmds = self._cmds_with_tracked_id(mock_client)
        # Sequence: registration window (idle shape, last result = OLD command),
        # then our command runs, then completes.
        mock_client.stub.GetCommandState.side_effect = [
            _state_resp(self.PENDING, ""),          # registration window
            _state_resp(self.RUNNING, "cmd-42"),    # our command registered
            _state_resp(self.PENDING, ""),          # back to idle = finished
        ]
        mock_client.stub.GetLastCommandResult.side_effect = [
            _last_resp("cmd-OLD"),                   # stale result — must be ignored
            _last_resp("cmd-42", success=True),      # our result
        ]

        with patch("kachaka_core.commands.time.sleep"):
            result = cmds.poll_until_complete(timeout=10.0)

        assert result["ok"] is True
        assert result["command_id"] == "cmd-42"
        # The stale result was fetched once (during the window) and rejected
        assert mock_client.stub.GetLastCommandResult.call_count == 2

    def test_fast_command_already_completed(self):
        """Command finished before the first poll — last result already ours."""
        mock_client = MagicMock()
        cmds = self._cmds_with_tracked_id(mock_client)
        mock_client.stub.GetCommandState.return_value = _state_resp(self.PENDING, "")
        mock_client.stub.GetLastCommandResult.return_value = _last_resp("cmd-42")

        result = cmds.poll_until_complete(timeout=5.0)
        assert result["ok"] is True
        assert result["command_id"] == "cmd-42"

    def test_command_failure_reported(self):
        mock_client = MagicMock()
        cmds = self._cmds_with_tracked_id(mock_client)
        mock_client.stub.GetCommandState.return_value = _state_resp(self.PENDING, "")
        mock_client.stub.GetLastCommandResult.return_value = _last_resp(
            "cmd-42", success=False, error_code=12345
        )

        result = cmds.poll_until_complete(timeout=5.0)
        assert result["ok"] is False
        assert result["error_code"] == 12345
        assert "12345" in result["error"]

    def test_explicit_command_id_overrides_tracked(self):
        mock_client = MagicMock()
        cmds = self._cmds_with_tracked_id(mock_client, command_id="cmd-STALE")
        mock_client.stub.GetCommandState.return_value = _state_resp(self.PENDING, "")
        mock_client.stub.GetLastCommandResult.return_value = _last_resp("cmd-99")

        result = cmds.poll_until_complete(timeout=5.0, command_id="cmd-99")
        assert result["ok"] is True
        assert result["command_id"] == "cmd-99"

    def test_timeout_when_result_never_ours(self):
        """Our command's result never appears — timeout, never false-complete."""
        mock_client = MagicMock()
        cmds = self._cmds_with_tracked_id(mock_client)
        mock_client.stub.GetCommandState.return_value = _state_resp(self.PENDING, "")
        mock_client.stub.GetLastCommandResult.return_value = _last_resp("cmd-OTHER")

        with patch("kachaka_core.commands.time.sleep"):
            with patch(
                "kachaka_core.commands.time.time", side_effect=[0, 0, 0.5, 999]
            ):
                result = cmds.poll_until_complete(timeout=1.0)

        assert result["ok"] is False
        assert result["error"] == "timeout"
        assert result["command_id"] == "cmd-42"

    def test_running_command_keeps_polling(self):
        mock_client = MagicMock()
        cmds = self._cmds_with_tracked_id(mock_client)
        mock_client.stub.GetCommandState.side_effect = [
            _state_resp(self.RUNNING, "cmd-42"),
            _state_resp(self.RUNNING, "cmd-42"),
            _state_resp(self.PENDING, ""),
        ]
        mock_client.stub.GetLastCommandResult.return_value = _last_resp("cmd-42")

        with patch("kachaka_core.commands.time.sleep"):
            result = cmds.poll_until_complete(timeout=10.0)

        assert result["ok"] is True
        # While RUNNING with our id, GetLastCommandResult must not be consulted
        assert mock_client.stub.GetLastCommandResult.call_count == 1

    def test_dispatch_then_poll_uses_tracked_id(self):
        """End-to-end: move_to_location records command_id; poll verifies it."""
        mock_client = MagicMock()
        conn = _make_conn(mock_client)
        mock_stub = _wire_start_command(mock_client, command_id="cmd-move")
        cmds = KachakaCommands(conn)

        dispatch = cmds.move_to_location("Kitchen")
        assert dispatch["command_id"] == "cmd-move"

        mock_stub.GetCommandState.return_value = _state_resp(self.PENDING, "")
        mock_stub.GetLastCommandResult.return_value = _last_resp("cmd-move")
        result = cmds.poll_until_complete(timeout=5.0)
        assert result["ok"] is True
        assert result["command_id"] == "cmd-move"

    # ── Legacy fallback (no tracked command_id) ─────────────────────

    def test_legacy_immediate_completion(self):
        mock_client = MagicMock()
        mock_client.is_command_running.return_value = False
        mock_client.get_last_command_result.return_value = (
            _make_result(True),
            MagicMock(),
        )
        conn = _make_conn(mock_client)

        result = KachakaCommands(conn).poll_until_complete(timeout=5.0)
        assert result["ok"] is True

    def test_legacy_timeout(self):
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
    def test_default_sensors_active(self):
        mock_client = MagicMock()
        conn = _make_conn(mock_client)
        mock_stub = _wire_start_command(mock_client)

        result = KachakaCommands(conn).move_forward(0.5)
        assert result["ok"] is True
        req = mock_stub.StartCommand.call_args[0][0]
        assert req.command.move_forward_command.mute_sensors is False
        assert req.command.move_forward_command.distance_meter == 0.5
        assert req.command.move_forward_command.speed == pytest.approx(0.1)

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
    def test_default_no_source(self):
        mock_client = MagicMock()
        conn = _make_conn(mock_client)
        mock_stub = _wire_start_command(mock_client)

        result = KachakaCommands(conn).move_to_location("Kitchen")
        assert result["ok"] is True
        req = mock_stub.StartCommand.call_args[0][0]
        assert req.command.move_to_location_command.source_location_id == ""

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


class TestFireAndAccept:
    """StartCommand-based wrappers must return on acceptance.

    The SDK's blocking flow waits in an unbounded GetLastCommandResult
    long-poll (2026-05-18 production hang). The toolkit owns completion via
    poll_until_complete / RobotController, so every wrapper dispatches
    StartCommand directly, never enters the completion long-poll, and
    records the accepted command_id for verified polling (2026-07-07).
    """

    DISPATCHES = [
        ("move_to_location", ("Kitchen",), {}),
        ("move_to_pose", (1.0, 2.0, 0.5), {}),
        ("move_forward", (0.5,), {}),
        ("rotate_in_place", (1.57,), {}),
        ("return_home", (), {}),
        ("move_shelf", ("Shelf A", "Room 1"), {}),
        ("return_shelf", ("Shelf A",), {}),
        ("dock_shelf", (), {}),
        ("undock_shelf", (), {}),
        ("dock_any_shelf_with_registration", ("Room 1",), {}),
    ]

    @pytest.mark.parametrize("method,args,kwargs", DISPATCHES)
    def test_fire_and_accept_with_command_id(self, method, args, kwargs):
        mock_client = MagicMock()
        conn = _make_conn(mock_client)
        mock_stub = _wire_start_command(mock_client, command_id="cmd-fa")

        cmds = KachakaCommands(conn)
        result = getattr(cmds, method)(*args, **kwargs)

        assert result["ok"] is True
        assert result["command_id"] == "cmd-fa"
        mock_stub.StartCommand.assert_called_once()
        # Fire-and-accept: never enters the completion long-poll
        mock_stub.GetLastCommandResult.assert_not_called()
        # Accepted command_id is tracked for poll_until_complete
        assert cmds._last_command_id == "cmd-fa"

    @pytest.mark.parametrize("method,args,kwargs", DISPATCHES[:3])
    def test_rejected_command_does_not_track_id(self, method, args, kwargs):
        mock_client = MagicMock()
        conn = _make_conn(mock_client)
        _wire_start_command(mock_client, success=False, error_code=15508)

        cmds = KachakaCommands(conn)
        result = getattr(cmds, method)(*args, **kwargs)

        assert result["ok"] is False
        assert cmds._last_command_id == ""

    def test_advanced_path_skips_completion_long_poll(self):
        """_start_command_advanced callers must not enter the cursor wait loop."""
        mock_client = MagicMock()
        conn = _make_conn(mock_client)
        mock_stub = _wire_advanced_stub(mock_client)

        result = KachakaCommands(conn).move_forward(0.5, mute_sensors=True)
        assert result["ok"] is True
        mock_stub.StartCommand.assert_called_once()
        mock_stub.GetLastCommandResult.assert_not_called()

    def test_speak_stays_blocking(self):
        """speak keeps SDK default (blocking) — bounded by long_poll_timeout."""
        mock_client = MagicMock()
        mock_client.speak.return_value = _make_result(True)
        conn = _make_conn(mock_client)
        KachakaCommands(conn).speak("hello")
        assert "wait_for_completion" not in mock_client.speak.call_args.kwargs
