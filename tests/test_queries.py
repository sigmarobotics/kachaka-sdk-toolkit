"""Tests for kachaka_core.queries — status, locations, camera, map, transforms."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from kachaka_core.connection import KachakaConnection
from kachaka_core.queries import KachakaQueries


@pytest.fixture(autouse=True)
def _clean_pool():
    KachakaConnection.clear_pool()
    yield
    KachakaConnection.clear_pool()


def _make_conn(mock_client):
    with patch("kachaka_core.connection.KachakaApiClient", return_value=mock_client):
        return KachakaConnection.get("test-robot")


class TestGetStatus:
    def test_full_status(self):
        mock = MagicMock()
        mock.get_robot_pose.return_value = MagicMock(x=1.0, y=2.0, theta=0.5)
        mock.get_battery_info.return_value = (85.0, "CHARGING")
        mock.get_command_state.return_value = ("PENDING", None)
        mock.get_error.return_value = []
        mock.get_moving_shelf_id.return_value = ""
        conn = _make_conn(mock)

        result = KachakaQueries(conn).get_status()

        assert result["ok"] is True
        assert result["pose"]["x"] == 1.0
        assert result["battery"]["percentage"] == 85.0
        assert result["errors"] == []
        assert result["moving_shelf_id"] is None


class TestLocations:
    def test_list_locations(self):
        mock = MagicMock()
        loc = MagicMock()
        loc.id = "loc-1"
        loc.name = "Kitchen"
        loc.type = "CHARGER"
        loc.pose = MagicMock(x=0.0, y=0.0, theta=0.0)
        mock.get_locations.return_value = [loc]
        conn = _make_conn(mock)

        result = KachakaQueries(conn).list_locations()

        assert result["ok"] is True
        assert len(result["locations"]) == 1
        assert result["locations"][0]["name"] == "Kitchen"

    def test_list_locations_digest(self):
        mock = MagicMock()
        conn = _make_conn(mock)
        # Re-assign stub after _make_conn (which replaces it with a real one)
        mock_stub = MagicMock()
        loc = MagicMock()
        loc.id = "loc-1"
        loc.name = "Kitchen"
        loc.type = "CHARGER"
        digest_resp = MagicMock()
        digest_resp.locations = [loc]
        mock_stub.GetLocationsDigest.return_value = digest_resp
        mock.stub = mock_stub

        result = KachakaQueries(conn).list_locations_digest()

        assert result["ok"] is True
        assert result["locations"] == [{"id": "loc-1", "name": "Kitchen", "type": "CHARGER"}]
        mock_stub.GetLocationsDigest.assert_called_once()


class TestShelves:
    def test_list_shelves(self):
        mock = MagicMock()
        shelf = MagicMock()
        shelf.id = "shelf-1"
        shelf.name = "Shelf A"
        shelf.home_location_id = "loc-2"
        mock.get_shelves.return_value = [shelf]
        conn = _make_conn(mock)

        result = KachakaQueries(conn).list_shelves()

        assert result["ok"] is True
        assert result["shelves"][0]["name"] == "Shelf A"

    def test_list_shelves_digest(self):
        mock = MagicMock()
        conn = _make_conn(mock)
        # Re-assign stub after _make_conn (which replaces it with a real one)
        mock_stub = MagicMock()
        shelf = MagicMock()
        shelf.id = "shelf-1"
        shelf.name = "Shelf A"
        digest_resp = MagicMock()
        digest_resp.shelves = [shelf]
        mock_stub.GetShelvesDigest.return_value = digest_resp
        mock.stub = mock_stub

        result = KachakaQueries(conn).list_shelves_digest()

        assert result["ok"] is True
        assert result["shelves"] == [{"id": "shelf-1", "name": "Shelf A"}]
        mock_stub.GetShelvesDigest.assert_called_once()

    def test_get_moving_shelf_empty(self):
        mock = MagicMock()
        mock.get_moving_shelf_id.return_value = ""
        conn = _make_conn(mock)

        result = KachakaQueries(conn).get_moving_shelf()
        assert result["shelf_id"] is None

    def test_get_moving_shelf_with_id(self):
        mock = MagicMock()
        mock.get_moving_shelf_id.return_value = "shelf-1"
        conn = _make_conn(mock)

        result = KachakaQueries(conn).get_moving_shelf()
        assert result["shelf_id"] == "shelf-1"


def _img(data: bytes, stamp_nsec: int, fmt: str = "jpeg"):
    """Build a mock RosCompressedImage with a header stamp."""
    img = MagicMock()
    img.data = data
    img.format = fmt
    img.header.stamp_nsec = stamp_nsec
    return img


class TestCamera:
    """Single-shot capture with freshness verification (2026-07-07 field
    incident: buffered frame from before the move returned as 'current')."""

    def test_front_camera_fresh_waits_for_newer_stamp(self):
        mock = MagicMock()
        mock.get_front_camera_ros_compressed_image.side_effect = [
            _img(b"\xff\xd8stale", 1000),   # buffered baseline
            _img(b"\xff\xd8stale", 1000),   # same stale frame again
            _img(b"\xff\xd8new", 2000),     # fresh frame arrives
        ]
        conn = _make_conn(mock)

        with patch("kachaka_core.queries.time.sleep"):
            result = KachakaQueries(conn).get_front_camera_image()

        assert result["ok"] is True
        assert result["format"] == "jpeg"
        assert result["fresh"] is True
        import base64
        assert base64.b64decode(result["image_base64"]) == b"\xff\xd8new"

    def test_front_camera_fresh_timeout_on_frozen_buffer(self):
        mock = MagicMock()
        mock.get_front_camera_ros_compressed_image.return_value = _img(
            b"\xff\xd8frozen", 1000
        )
        conn = _make_conn(mock)

        with patch("kachaka_core.queries.time.sleep"):
            with patch(
                "kachaka_core.queries.time.time", side_effect=[0, 0, 0.5, 999, 999]
            ):
                result = KachakaQueries(conn).get_front_camera_image(timeout=1.0)

        assert result["ok"] is False
        assert "fresh" in result["error"]

    def test_front_camera_fresh_false_returns_buffer(self):
        mock = MagicMock()
        mock.get_front_camera_ros_compressed_image.return_value = _img(
            b"\xff\xd8test-jpeg", 1000
        )
        conn = _make_conn(mock)

        result = KachakaQueries(conn).get_front_camera_image(fresh=False)

        assert result["ok"] is True
        assert result["fresh"] is False
        mock.get_front_camera_ros_compressed_image.assert_called_once()

    def test_fresh_zero_stamp_falls_back_to_data_change(self):
        """Firmware leaving stamp_nsec=0 — freshness via changed frame bytes."""
        mock = MagicMock()
        mock.get_front_camera_ros_compressed_image.side_effect = [
            _img(b"\xff\xd8frameA", 0),
            _img(b"\xff\xd8frameA", 0),
            _img(b"\xff\xd8frameB", 0),
        ]
        conn = _make_conn(mock)

        with patch("kachaka_core.queries.time.sleep"):
            result = KachakaQueries(conn).get_front_camera_image()

        assert result["ok"] is True
        import base64
        assert base64.b64decode(result["image_base64"]) == b"\xff\xd8frameB"

    def test_back_camera(self):
        mock = MagicMock()
        mock.get_back_camera_ros_compressed_image.side_effect = [
            _img(b"\xff\xd8back", 1000),
            _img(b"\xff\xd8back2", 2000),
        ]
        conn = _make_conn(mock)

        with patch("kachaka_core.queries.time.sleep"):
            result = KachakaQueries(conn).get_back_camera_image()
        assert result["ok"] is True


class TestMap:
    def test_get_map(self):
        mock = MagicMock()
        png_map = MagicMock()
        png_map.data = b"\x89PNGtest"
        png_map.name = "Floor1"
        png_map.resolution = 0.05
        png_map.width = 200
        png_map.height = 200
        mock.get_png_map.return_value = png_map
        conn = _make_conn(mock)

        result = KachakaQueries(conn).get_map()

        assert result["ok"] is True
        assert result["format"] == "png"
        assert result["name"] == "Floor1"

    def test_list_maps(self):
        mock = MagicMock()
        m = MagicMock()
        m.id = "map-1"
        m.name = "Floor1"
        mock.get_map_list.return_value = [m]
        mock.get_current_map_id.return_value = "map-1"
        conn = _make_conn(mock)

        result = KachakaQueries(conn).list_maps()

        assert result["ok"] is True
        assert result["current_map_id"] == "map-1"


class TestErrors:
    def test_no_errors(self):
        mock = MagicMock()
        mock.get_error.return_value = []
        conn = _make_conn(mock)

        result = KachakaQueries(conn).get_errors()
        assert result["errors"] == []

    def test_error_definitions(self):
        mock = MagicMock()
        err_info = MagicMock()
        err_info.title_en = "Shelf dropped"
        err_info.description_en = "The shelf was dropped during movement"
        mock.get_robot_error_code.return_value = {14606: err_info}
        conn = _make_conn(mock)

        result = KachakaQueries(conn).get_error_definitions()

        assert result["ok"] is True
        assert 14606 in result["definitions"]
        assert result["definitions"][14606]["title"] == "Shelf dropped"


class TestRobotInfo:
    def test_serial_number(self):
        mock = MagicMock()
        mock.get_robot_serial_number.return_value = "KCK-001"
        conn = _make_conn(mock)

        result = KachakaQueries(conn).get_serial_number()
        assert result["serial"] == "KCK-001"

    def test_version(self):
        mock = MagicMock()
        mock.get_robot_version.return_value = "3.15.1"
        conn = _make_conn(mock)

        result = KachakaQueries(conn).get_version()
        assert result["version"] == "3.15.1"


class TestIsReady:
    def test_ready_when_no_active_errors(self):
        mock = MagicMock()
        mock.get_error.return_value = []
        mock.get_last_command_result.return_value = (
            MagicMock(success=True, error_code=0),
            MagicMock(),
        )
        conn = _make_conn(mock)

        result = KachakaQueries(conn).is_ready()
        assert result["ok"] is True
        assert result["ready"] is True
        assert result["fatal_codes"] == []
        assert result["category"] is None
        assert result["recovery_hint"] is None
        assert result["last_command_error_code"] == 0

    def test_paused_state(self):
        """21051 in errors[] → ready=False with press_power_button hint."""
        mock = MagicMock()
        mock.get_error.return_value = [21051]
        mock.get_last_command_result.return_value = (
            MagicMock(success=False, error_code=10107),
            MagicMock(),
        )
        conn = _make_conn(mock)

        result = KachakaQueries(conn).is_ready()
        assert result["ready"] is False
        assert result["fatal_codes"] == [21051]
        assert result["category"] == "paused"
        assert result["recovery_hint"] == "press_power_button"
        assert result["last_command_error_code"] == 10107

    def test_hardware_fatal(self):
        """21004 LiDAR error → ready=False with restart_robot hint."""
        mock = MagicMock()
        mock.get_error.return_value = [21004]
        mock.get_last_command_result.return_value = (
            MagicMock(success=False, error_code=10264),
            MagicMock(),
        )
        conn = _make_conn(mock)

        result = KachakaQueries(conn).is_ready()
        assert result["ready"] is False
        assert result["fatal_codes"] == [21004]
        assert result["category"] == "hardware_fatal"
        assert result["recovery_hint"] == "restart_robot"

    def test_ghost_last_result_does_not_block(self):
        """Cleared errors with stale last_result=10107 → still ready=True."""
        mock = MagicMock()
        mock.get_error.return_value = []
        mock.get_last_command_result.return_value = (
            MagicMock(success=False, error_code=10107),
            MagicMock(),
        )
        conn = _make_conn(mock)

        result = KachakaQueries(conn).is_ready()
        assert result["ready"] is True
        assert result["last_command_error_code"] == 10107


class TestAutoHomingQuery:
    def test_get_auto_homing_enabled(self):
        mock = MagicMock()
        mock.get_auto_homing_enabled.return_value = True
        conn = _make_conn(mock)

        result = KachakaQueries(conn).get_auto_homing_enabled()
        assert result["ok"] is True
        assert result["enabled"] is True

    def test_get_auto_homing_disabled(self):
        mock = MagicMock()
        mock.get_auto_homing_enabled.return_value = False
        conn = _make_conn(mock)

        result = KachakaQueries(conn).get_auto_homing_enabled()
        assert result["enabled"] is False


class TestManualControlQuery:
    def test_get_manual_control_enabled(self):
        mock = MagicMock()
        mock.get_manual_control_enabled.return_value = True
        conn = _make_conn(mock)

        result = KachakaQueries(conn).get_manual_control_enabled()
        assert result["ok"] is True
        assert result["enabled"] is True


class TestCameraIntrinsics:
    def test_front_camera_intrinsics(self):
        mock = MagicMock()
        cam_info = MagicMock()
        cam_info.width = 1280
        cam_info.height = 720
        cam_info.distortion_model = "plumb_bob"
        cam_info.D = [-0.28, 0.10, -0.0002, -0.002, -0.019]
        cam_info.K = [510.0, 0.0, 628.0, 0.0, 504.0, 349.0, 0.0, 0.0, 1.0]
        cam_info.R = [1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0]
        cam_info.P = [510.0, 0.0, 628.0, 0.0, 0.0, 504.0, 349.0, 0.0, 0.0, 0.0, 1.0, 0.0]
        mock.get_front_camera_ros_camera_info.return_value = cam_info
        conn = _make_conn(mock)

        result = KachakaQueries(conn).get_camera_intrinsics("front")
        assert result["ok"] is True
        assert result["width"] == 1280
        assert result["fx"] == 510.0
        assert result["fy"] == 504.0
        assert result["cx"] == 628.0
        assert result["cy"] == 349.0
        assert result["distortion_model"] == "plumb_bob"

    def test_back_camera_intrinsics(self):
        mock = MagicMock()
        cam_info = MagicMock()
        cam_info.width = 1280
        cam_info.height = 720
        cam_info.distortion_model = "plumb_bob"
        cam_info.D = [-0.29, 0.11, 0.0, 0.0, -0.02]
        cam_info.K = [504.0, 0.0, 610.0, 0.0, 499.0, 333.0, 0.0, 0.0, 1.0]
        cam_info.R = [1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0]
        cam_info.P = [504.0, 0.0, 610.0, 0.0, 0.0, 499.0, 333.0, 0.0, 0.0, 0.0, 1.0, 0.0]
        mock.get_back_camera_ros_camera_info.return_value = cam_info
        conn = _make_conn(mock)

        result = KachakaQueries(conn).get_camera_intrinsics("back")
        assert result["ok"] is True
        assert result["fx"] == 504.0

    def test_tof_camera_intrinsics(self):
        mock = MagicMock()
        cam_info = MagicMock()
        cam_info.width = 160
        cam_info.height = 120
        cam_info.distortion_model = "plumb_bob"
        cam_info.D = [0.0, 0.0, 0.0, 0.0, 0.0]
        cam_info.K = [80.0, 0.0, 80.0, 0.0, 80.0, 60.0, 0.0, 0.0, 1.0]
        cam_info.R = [1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0]
        cam_info.P = [80.0, 0.0, 80.0, 0.0, 0.0, 80.0, 60.0, 0.0, 0.0, 0.0, 1.0, 0.0]
        mock.get_tof_camera_ros_camera_info.return_value = cam_info
        conn = _make_conn(mock)

        result = KachakaQueries(conn).get_camera_intrinsics("tof")
        assert result["ok"] is True
        assert result["width"] == 160

    def test_camera_intrinsics_grpc_cancelled(self):
        mock = MagicMock()
        mock.get_front_camera_ros_camera_info.side_effect = Exception(
            "<_InactiveRpcError: StatusCode.CANCELLED>"
        )
        conn = _make_conn(mock)

        result = KachakaQueries(conn).get_camera_intrinsics("front")
        assert result["ok"] is False
        assert "CANCELLED" in result["error"]

    def test_invalid_camera_name(self):
        mock = MagicMock()
        conn = _make_conn(mock)

        result = KachakaQueries(conn).get_camera_intrinsics("side")
        assert result["ok"] is False


class TestStaticTransform:
    def test_get_static_transform(self):
        mock = MagicMock()
        conn = _make_conn(mock)
        # Re-assign stub after _make_conn (which replaces it with a real one)
        mock_stub = MagicMock()
        tf = MagicMock()
        tf.header.frame_id = "base_link"
        tf.child_frame_id = "camera_link"
        tf.translation.x = 0.1
        tf.translation.y = 0.0
        tf.translation.z = 0.5
        tf.rotation.x = 0.0
        tf.rotation.y = 0.0
        tf.rotation.z = 0.0
        tf.rotation.w = 1.0
        mock_stub.GetStaticTransform.return_value = MagicMock(transforms=[tf])
        mock.stub = mock_stub

        result = KachakaQueries(conn).get_static_transform()
        assert result["ok"] is True
        assert len(result["transforms"]) == 1
        t = result["transforms"][0]
        assert t["frame_id"] == "base_link"
        assert t["child_frame_id"] == "camera_link"
        assert t["translation"]["x"] == 0.1
        assert t["rotation"]["w"] == 1.0
        assert "theta" in t
        assert abs(t["theta"]) < 0.01  # identity rotation → theta ≈ 0

    def test_get_static_transform_empty(self):
        mock = MagicMock()
        conn = _make_conn(mock)
        mock_stub = MagicMock()
        mock_stub.GetStaticTransform.return_value = MagicMock(transforms=[])
        mock.stub = mock_stub

        result = KachakaQueries(conn).get_static_transform()
        assert result["ok"] is True
        assert result["transforms"] == []


class TestTofImage:
    def test_get_tof_image_raw(self):
        mock = MagicMock()
        tof_img = MagicMock()
        tof_img.width = 160
        tof_img.height = 120
        tof_img.encoding = "16UC1"
        tof_img.step = 320
        tof_img.data = b"\x00" * 38400
        tof_img.is_bigendian = False
        tof_img.header = MagicMock(frame_id="tof_camera")
        mock.get_tof_camera_ros_image.return_value = tof_img
        conn = _make_conn(mock)

        result = KachakaQueries(conn).get_tof_image()
        assert result["ok"] is True
        assert result["width"] == 160
        assert result["height"] == 120
        assert result["encoding"] == "16UC1"
        assert len(result["image_base64"]) > 0

    def test_get_tof_image_on_charger(self):
        mock = MagicMock()
        mock.get_tof_camera_ros_image.side_effect = Exception("tof is not available on charger.")
        conn = _make_conn(mock)

        result = KachakaQueries(conn).get_tof_image()
        assert result["ok"] is False
        assert "charger" in result["error"].lower()

    def test_get_tof_image_cancelled(self):
        mock = MagicMock()
        import grpc
        rpc_error = grpc.RpcError()
        rpc_error.code = lambda: grpc.StatusCode.CANCELLED
        rpc_error.details = lambda: ""
        mock.get_tof_camera_ros_image.side_effect = rpc_error
        conn = _make_conn(mock)

        result = KachakaQueries(conn).get_tof_image()
        assert result["ok"] is False


class TestSounds:
    def test_list_sounds(self):
        mock = MagicMock()
        conn = _make_conn(mock)
        # Re-assign stub after _make_conn (which replaces it with a real one)
        mock_stub = MagicMock()
        s1, s2 = MagicMock(), MagicMock()
        s1.id, s1.name = "snd-1", "chime"
        s2.id, s2.name = "snd-2", "bell"
        list_resp = MagicMock()
        list_resp.sounds = [s1, s2]
        mock_stub.GetSoundList.return_value = list_resp
        mock.stub = mock_stub

        result = KachakaQueries(conn).list_sounds()

        assert result["ok"] is True
        assert result["sounds"] == [
            {"id": "snd-1", "name": "chime"},
            {"id": "snd-2", "name": "bell"},
        ]
        mock_stub.GetSoundList.assert_called_once()

    def test_list_sounds_empty(self):
        mock = MagicMock()
        conn = _make_conn(mock)
        mock_stub = MagicMock()
        list_resp = MagicMock()
        list_resp.sounds = []
        mock_stub.GetSoundList.return_value = list_resp
        mock.stub = mock_stub

        result = KachakaQueries(conn).list_sounds()
        assert result["ok"] is True
        assert result["sounds"] == []
