"""MCP Server for Kachaka Robot — thin wrapper around kachaka_core.

Each tool is a one-liner delegation to the shared core layer.
Run with: ``kachaka-mcp``, ``python -m mcp_server.server``,
or ``python mcp_server/server.py``

Transport: stdio (default for Claude Desktop / Claude Code).
"""

from __future__ import annotations

import base64
import json
import logging

from mcp.server.fastmcp import FastMCP, Image
from mcp.types import TextContent

from kachaka_core.commands import KachakaCommands
from kachaka_core.camera import CameraStreamer
from kachaka_core.connection import KachakaConnection
from kachaka_core.controller import RobotController
from kachaka_core.queries import KachakaQueries
from kachaka_core.transform import TransformStreamer

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

mcp = FastMCP(
    "kachaka-robot",
    instructions=(
        "Kachaka Robot control tools. All tools require an ``ip`` parameter "
        "(e.g. '192.168.1.100' or '192.168.1.100:26400'). "
        "Port 26400 is appended automatically when omitted."
    ),
)


# ── Connection ───────────────────────────────────────────────────────

@mcp.tool()
def ping_robot(ip: str) -> dict:
    """Test gRPC connectivity and return serial number + current pose."""
    return KachakaConnection.get(ip).ping()


@mcp.tool()
def disconnect_robot(ip: str) -> dict:
    """Remove robot from connection pool (useful after IP change)."""
    KachakaConnection.remove(ip)
    return {"ok": True, "message": f"Removed {ip} from pool"}


# ── Status queries ───────────────────────────────────────────────────

@mcp.tool()
def get_robot_status(ip: str) -> dict:
    """Full snapshot: pose, battery, command state, errors, moving shelf."""
    return KachakaQueries(KachakaConnection.get(ip)).get_status()


@mcp.tool()
def get_robot_pose(ip: str) -> dict:
    """Current robot position on the map (x, y, theta)."""
    return KachakaQueries(KachakaConnection.get(ip)).get_pose()


@mcp.tool()
def get_battery(ip: str) -> dict:
    """Battery percentage and charging status."""
    return KachakaQueries(KachakaConnection.get(ip)).get_battery()


@mcp.tool()
def get_errors(ip: str) -> dict:
    """Active error codes on the robot."""
    return KachakaQueries(KachakaConnection.get(ip)).get_errors()


@mcp.tool()
def get_robot_info(ip: str) -> dict:
    """Serial number and firmware version."""
    conn = KachakaConnection.get(ip)
    q = KachakaQueries(conn)
    serial = q.get_serial_number()
    version = q.get_version()
    if serial["ok"] and version["ok"]:
        return {"ok": True, "serial": serial["serial"], "version": version["version"]}
    return serial if not serial["ok"] else version


# ── Locations & shelves ──────────────────────────────────────────────

@mcp.tool()
def list_locations(ip: str) -> dict:
    """All registered locations (name, id, type, pose)."""
    return KachakaQueries(KachakaConnection.get(ip)).list_locations()


@mcp.tool()
def list_shelves(ip: str) -> dict:
    """All registered shelves (name, id, home location)."""
    return KachakaQueries(KachakaConnection.get(ip)).list_shelves()


@mcp.tool()
def get_moving_shelf(ip: str) -> dict:
    """ID of the shelf the robot is currently carrying."""
    return KachakaQueries(KachakaConnection.get(ip)).get_moving_shelf()


# ── Movement ─────────────────────────────────────────────────────────

@mcp.tool()
def move_to_location(ip: str, location_name: str) -> dict:
    """Move robot to a registered location by name or ID.

    Use ``list_locations`` first to see available destinations.
    This is a blocking call — returns when movement completes.
    """
    return KachakaCommands(KachakaConnection.get(ip)).move_to_location(location_name)


@mcp.tool()
def move_to_pose(ip: str, x: float, y: float, yaw: float) -> dict:
    """Move robot to absolute map coordinates (x, y, yaw in radians)."""
    return KachakaCommands(KachakaConnection.get(ip)).move_to_pose(x, y, yaw)


@mcp.tool()
def move_forward(ip: str, distance_meter: float) -> dict:
    """Move forward (positive) or backward (negative) by a distance in meters."""
    return KachakaCommands(KachakaConnection.get(ip)).move_forward(distance_meter)


@mcp.tool()
def rotate(ip: str, angle_radian: float) -> dict:
    """Rotate in place. Positive = counter-clockwise."""
    return KachakaCommands(KachakaConnection.get(ip)).rotate_in_place(angle_radian)


@mcp.tool()
def return_home(ip: str) -> dict:
    """Send robot back to its charger."""
    return KachakaCommands(KachakaConnection.get(ip)).return_home()


# ── Shelf operations ─────────────────────────────────────────────────

@mcp.tool()
def move_shelf(
    ip: str,
    shelf_name: str,
    location_name: str,
    undock_on_destination: bool = False,
) -> dict:
    """Pick up a shelf and deliver it to a location (by name or ID).

    undock_on_destination: if True, automatically undock the shelf at the
    destination instead of staying docked.
    """
    return KachakaCommands(KachakaConnection.get(ip)).move_shelf(
        shelf_name, location_name, undock_on_destination=undock_on_destination,
    )


@mcp.tool()
def return_shelf(ip: str, shelf_name: str = "") -> dict:
    """Return the currently held (or named) shelf to its home location."""
    return KachakaCommands(KachakaConnection.get(ip)).return_shelf(shelf_name)


@mcp.tool()
def dock_shelf(ip: str) -> dict:
    """Dock the currently held shelf onto the robot."""
    return KachakaCommands(KachakaConnection.get(ip)).dock_shelf()


@mcp.tool()
def undock_shelf(ip: str) -> dict:
    """Undock the currently held shelf from the robot."""
    return KachakaCommands(KachakaConnection.get(ip)).undock_shelf()


@mcp.tool()
def dock_any_shelf_with_registration(
    ip: str, location_name: str, dock_forward: bool = False
) -> dict:
    """Move to a location and dock any shelf placed there. If the shelf is unregistered, it is automatically registered as new.

    dock_forward: if True the robot approaches the shelf head-first (default False = tail-first).
    """
    return KachakaCommands(KachakaConnection.get(ip)).dock_any_shelf_with_registration(
        location_name, dock_forward
    )


@mcp.tool()
def reset_shelf_pose(ip: str, shelf_name: str) -> dict:
    """Reset the recorded pose of a shelf (by name or ID)."""
    return KachakaCommands(KachakaConnection.get(ip)).reset_shelf_pose(shelf_name)


# ── Controller (background state polling) ────────────────────────

_controllers: dict[str, RobotController] = {}


def _controller_key(ip: str) -> str:
    return KachakaConnection._normalise_target(ip)


@mcp.tool()
def start_controller(ip: str) -> dict:
    """Start a RobotController with background state polling.

    Idempotent — returns the existing controller if already running.
    The controller continuously reads pose, battery, and command state
    in a background thread.
    """
    key = _controller_key(ip)
    existing = _controllers.get(key)
    if existing is not None:
        return {"ok": True, "message": "controller already running"}
    conn = KachakaConnection.get(ip)
    ctrl = RobotController(conn)
    ctrl.start()
    _controllers[key] = ctrl
    return {"ok": True, "message": "controller started"}


@mcp.tool()
def stop_controller(ip: str) -> dict:
    """Stop and remove the RobotController for this robot."""
    key = _controller_key(ip)
    ctrl = _controllers.pop(key, None)
    if ctrl is None:
        return {"ok": True, "message": "no controller to stop"}
    ctrl.stop()
    return {"ok": True, "message": "controller stopped"}


@mcp.tool()
def get_controller_state(ip: str) -> dict:
    """Return the full RobotState snapshot from the background controller.

    Includes pose, battery, command state, moving_shelf_id, shelf_dropped.
    """
    key = _controller_key(ip)
    ctrl = _controllers.get(key)
    if ctrl is None:
        return {"ok": False, "error": "controller not started"}
    s = ctrl.state
    return {
        "ok": True,
        "battery_pct": s.battery_pct,
        "pose_x": s.pose_x,
        "pose_y": s.pose_y,
        "pose_theta": s.pose_theta,
        "is_command_running": s.is_command_running,
        "last_updated": s.last_updated,
        "moving_shelf_id": s.moving_shelf_id,
        "shelf_dropped": s.shelf_dropped,
    }


@mcp.tool()
def controller_move_shelf(ip: str, shelf_name: str, location_name: str) -> dict:
    """Move a shelf to a location via the background controller.

    Uses command_id verification and auto-starts shelf drop monitoring.
    Requires ``start_controller`` first.
    """
    key = _controller_key(ip)
    ctrl = _controllers.get(key)
    if ctrl is None:
        return {"ok": False, "error": "controller not started"}
    return ctrl.move_shelf(shelf_name, location_name)


@mcp.tool()
def controller_return_shelf(ip: str, shelf_name: str = "") -> dict:
    """Return a shelf to its home via the background controller.

    Auto-stops shelf drop monitoring. Requires ``start_controller`` first.
    """
    key = _controller_key(ip)
    ctrl = _controllers.get(key)
    if ctrl is None:
        return {"ok": False, "error": "controller not started"}
    return ctrl.return_shelf(shelf_name)


@mcp.tool()
def controller_move_to_location(ip: str, location_name: str) -> dict:
    """Move to a location via the background controller.

    Uses command_id verification and deadline-based retry.
    Requires ``start_controller`` first.
    """
    key = _controller_key(ip)
    ctrl = _controllers.get(key)
    if ctrl is None:
        return {"ok": False, "error": "controller not started"}
    return ctrl.move_to_location(location_name)


@mcp.tool()
def controller_dock_any_shelf(
    ip: str, location_name: str, dock_forward: bool = False,
) -> dict:
    """Move to a location and dock any shelf there via the background controller.

    Unregistered shelves are automatically registered as new.
    Requires ``start_controller`` first.
    """
    key = _controller_key(ip)
    ctrl = _controllers.get(key)
    if ctrl is None:
        return {"ok": False, "error": "controller not started"}
    return ctrl.dock_any_shelf_with_registration(location_name, dock_forward)


# ── Speech ───────────────────────────────────────────────────────────

@mcp.tool()
def speak(ip: str, text: str) -> dict:
    """Make the robot speak text via TTS."""
    return KachakaCommands(KachakaConnection.get(ip)).speak(text)


@mcp.tool()
def set_volume(ip: str, volume: int) -> dict:
    """Set speaker volume (0–10)."""
    return KachakaCommands(KachakaConnection.get(ip)).set_speaker_volume(volume)


@mcp.tool()
def get_volume(ip: str) -> dict:
    """Get current speaker volume."""
    return KachakaQueries(KachakaConnection.get(ip)).get_speaker_volume()


# ── Command control ──────────────────────────────────────────────────

@mcp.tool()
def cancel_command(ip: str) -> dict:
    """Cancel the currently running command."""
    return KachakaCommands(KachakaConnection.get(ip)).cancel_command()


@mcp.tool()
def get_command_state(ip: str) -> dict:
    """Check whether a command is running and its current state."""
    return KachakaQueries(KachakaConnection.get(ip)).get_command_state()


@mcp.tool()
def get_last_result(ip: str) -> dict:
    """Result of the most recently completed command."""
    return KachakaQueries(KachakaConnection.get(ip)).get_last_command_result()


# ── Camera ───────────────────────────────────────────────────────────


@mcp.tool()
def capture_front_camera(ip: str):
    """Capture a JPEG from the front camera.

    Returns the image directly — Claude can see it inline.
    """
    result = KachakaQueries(KachakaConnection.get(ip)).get_front_camera_image()
    if not result["ok"]:
        return result
    return Image(data=base64.b64decode(result["image_base64"]), format="jpeg")


@mcp.tool()
def capture_back_camera(ip: str):
    """Capture a JPEG from the back camera.

    Returns the image directly — Claude can see it inline.
    """
    result = KachakaQueries(KachakaConnection.get(ip)).get_back_camera_image()
    if not result["ok"]:
        return result
    return Image(data=base64.b64decode(result["image_base64"]), format="jpeg")


# ── Camera streaming ────────────────────────────────────────────────

_streamers: dict[str, CameraStreamer] = {}


def _streamer_key(ip: str, camera: str) -> str:
    return f"{KachakaConnection._normalise_target(ip)}:{camera}"


@mcp.tool()
def start_camera_stream(
    ip: str, interval: float = 1.0, camera: str = "front",
    detect: bool = False, annotate: bool = False,
) -> dict:
    """Start continuous camera capture in a background thread.

    Frames are captured every ``interval`` seconds without blocking other
    operations.  Use ``get_camera_frame`` to retrieve the latest image.

    Set detect=True to also run object detection each frame.
    Set annotate=True to draw bounding boxes on captured frames.
    """
    conn = KachakaConnection.get(ip)
    key = _streamer_key(ip, camera)
    existing = _streamers.get(key)
    if existing is not None and existing.is_running:
        return {"ok": True, "message": "already running", "stats": existing.stats}
    streamer = CameraStreamer(conn, interval=interval, camera=camera,
                              detect=detect, annotate=annotate)
    streamer.start()
    _streamers[key] = streamer
    return {"ok": True, "message": f"{camera} camera stream started",
            "detect": detect, "annotate": annotate}


@mcp.tool()
def get_camera_frame(ip: str, camera: str = "front"):
    """Get the latest frame from a running camera stream.

    Returns the image directly — Claude can see it inline.
    When detection is enabled, also returns detected objects as text.
    Must call ``start_camera_stream`` first.
    """
    key = _streamer_key(ip, camera)
    streamer = _streamers.get(key)
    if streamer is None or not streamer.is_running:
        return {"ok": False, "error": "stream not started — call start_camera_stream first"}
    frame = streamer.latest_frame
    if frame is None:
        return {"ok": False, "error": "no frame captured yet — try again shortly"}
    img = Image(data=base64.b64decode(frame["image_base64"]), format="jpeg")
    if frame.get("objects"):
        return [img, TextContent(type="text", text=json.dumps(
            {"objects": frame["objects"]}, ensure_ascii=False))]
    return img


@mcp.tool()
def stop_camera_stream(ip: str, camera: str = "front") -> dict:
    """Stop a running camera stream."""
    key = _streamer_key(ip, camera)
    streamer = _streamers.pop(key, None)
    if streamer is None:
        return {"ok": True, "message": "no stream to stop"}
    streamer.stop()
    return {"ok": True, "message": f"{camera} camera stream stopped", "stats": streamer.stats}


@mcp.tool()
def get_camera_stats(ip: str, camera: str = "front") -> dict:
    """Get capture statistics for a running camera stream."""
    key = _streamer_key(ip, camera)
    streamer = _streamers.get(key)
    if streamer is None:
        return {"ok": False, "error": "no stream active"}
    return {"ok": True, **streamer.stats, "is_running": streamer.is_running}


# ── Camera intrinsics ───────────────────────────────────────────────

@mcp.tool()
def get_camera_intrinsics(ip: str, camera: str = "front") -> dict:
    """Get camera intrinsics (focal length, principal point, distortion).

    Camera must be active first — call ``start_camera_stream`` before querying.
    For ToF camera, the robot must NOT be on the charger.
    Valid cameras: front, back, tof.
    """
    return KachakaQueries(KachakaConnection.get(ip)).get_camera_intrinsics(camera)


# ── ToF camera ──────────────────────────────────────────────────────

@mcp.tool()
def get_tof_image(ip: str) -> dict:
    """Capture a depth image from the ToF (Time-of-Flight) camera.

    Returns raw 16-bit depth data (16UC1 encoding, 160x120).
    NOT available while the robot is on the charger — move it off first.
    """
    return KachakaQueries(KachakaConnection.get(ip)).get_tof_image()


# ── Object Detection ────────────────────────────────────────────────

@mcp.tool()
def get_object_detection(ip: str) -> dict:
    """Detect objects (person, shelf, charger, door) visible to the robot.

    Returns bounding boxes with confidence scores and distances.
    """
    from kachaka_core.detection import ObjectDetector
    return ObjectDetector(KachakaConnection.get(ip)).get_detections()


@mcp.tool()
def capture_with_detection(
    ip: str, camera: str = "front", annotate: bool = True,
):
    """Capture camera image with object detection overlay.

    When annotate=True, bounding boxes are drawn on the image.
    Returns the image (Claude can see it inline) and detection results as text.
    """
    from kachaka_core.detection import ObjectDetector
    detector = ObjectDetector(KachakaConnection.get(ip))
    result = detector.capture_with_detections(camera=camera)
    if not result["ok"]:
        return result
    if annotate and result.get("objects"):
        raw = base64.b64decode(result["image_base64"])
        img_bytes = detector.annotate_frame(raw, result["objects"])
    else:
        img_bytes = base64.b64decode(result["image_base64"])
    meta = {"objects": result.get("objects", []), "annotated": annotate}
    return [
        Image(data=img_bytes, format="jpeg"),
        TextContent(type="text", text=json.dumps(meta, ensure_ascii=False)),
    ]


# ── Map ──────────────────────────────────────────────────────────────

@mcp.tool()
def get_map(ip: str):
    """Current map as PNG image with metadata.

    Returns the map image (Claude can see it inline) and metadata as text.
    """
    result = KachakaQueries(KachakaConnection.get(ip)).get_map()
    if not result["ok"]:
        return result
    img_bytes = base64.b64decode(result["image_base64"])
    meta = {k: v for k, v in result.items() if k not in ("ok", "image_base64")}
    return [
        Image(data=img_bytes, format="png"),
        TextContent(type="text", text=json.dumps(meta, ensure_ascii=False)),
    ]


@mcp.tool()
def list_maps(ip: str) -> dict:
    """All available maps and the currently active map ID."""
    return KachakaQueries(KachakaConnection.get(ip)).list_maps()


@mcp.tool()
def export_map(ip: str, map_id: str, output_path: str) -> dict:
    """Export a map to a binary file (Kachaka proprietary format).

    Use ``list_maps`` to find available map IDs.
    The file can be re-imported later with ``import_map``.
    """
    return KachakaCommands(KachakaConnection.get(ip)).export_map(map_id, output_path)


@mcp.tool()
def import_map(ip: str, file_path: str) -> dict:
    """Import a map from a previously exported binary file.

    The file must have been created by ``export_map``.
    Returns the new map ID assigned by the robot.
    """
    return KachakaCommands(KachakaConnection.get(ip)).import_map(file_path)


@mcp.tool()
def switch_map(
    ip: str,
    map_id: str,
    pose_x: float | None = None,
    pose_y: float | None = None,
    pose_theta: float | None = None,
    inherit_docking_state: bool = False,
) -> dict:
    """Switch the robot to a different map.

    Use ``list_maps`` first to find available map IDs.
    Optionally specify an initial pose (x, y, theta).
    When no pose is given, the charger pose of the target map is used.
    """
    return KachakaCommands(KachakaConnection.get(ip)).switch_map(
        map_id,
        pose_x=pose_x,
        pose_y=pose_y,
        pose_theta=pose_theta,
        inherit_docking_state=inherit_docking_state,
    )


@mcp.tool()
def import_image_as_map(
    ip: str,
    image_path: str,
    resolution: float,
    charger_x: float,
    charger_y: float,
    charger_theta: float = 0.0,
) -> dict:
    """Import a PNG occupancy grid image as a new map (ROS-style format).

    The image should be a grayscale PNG where white=free, black=wall, gray=unknown.
    ``resolution`` is meters per pixel (e.g. 0.025 = 2.5cm/px).
    Charger pose (x, y, theta) specifies the charging dock position in world coordinates.
    """
    return KachakaCommands(KachakaConnection.get(ip)).import_image_as_map(
        image_path, resolution, charger_x, charger_y, charger_theta,
    )


# ── Shortcuts ────────────────────────────────────────────────────────

@mcp.tool()
def list_shortcuts(ip: str) -> dict:
    """All registered shortcuts (id -> name)."""
    return KachakaQueries(KachakaConnection.get(ip)).list_shortcuts()


@mcp.tool()
def start_shortcut(ip: str, shortcut_id: str) -> dict:
    """Execute a registered shortcut by its ID.

    Use ``list_shortcuts`` first to see available shortcut IDs and names.
    """
    return KachakaCommands(KachakaConnection.get(ip)).start_shortcut(shortcut_id)


# ── History ──────────────────────────────────────────────────────────

@mcp.tool()
def get_history(ip: str) -> dict:
    """Recent command execution history."""
    return KachakaQueries(KachakaConnection.get(ip)).get_history()


# ── Manual control ───────────────────────────────────────────────────

@mcp.tool()
def enable_manual_control(
    ip: str, enabled: bool, use_shelf_registration: bool = False,
) -> dict:
    """Enable or disable manual velocity control mode.

    use_shelf_registration: also activate shelf recognition during manual control.
    """
    return KachakaCommands(KachakaConnection.get(ip)).set_manual_control(
        enabled, use_shelf_registration=use_shelf_registration,
    )


@mcp.tool()
def set_velocity(ip: str, linear: float, angular: float) -> dict:
    """Set robot velocity (requires manual-control mode). Max: 0.3 m/s, 1.57 rad/s."""
    return KachakaCommands(KachakaConnection.get(ip)).set_velocity(linear, angular)


@mcp.tool()
def emergency_stop(ip: str) -> dict:
    """Immediately stop the robot and disable manual control."""
    return KachakaCommands(KachakaConnection.get(ip)).stop()


# ── Torch / lighting ────────────────────────────────────────────────

@mcp.tool()
def set_front_torch(ip: str, intensity: int) -> dict:
    """Set front LED torch intensity (0–255). 0 = off, 255 = max brightness."""
    return KachakaCommands(KachakaConnection.get(ip)).set_front_torch(intensity)


@mcp.tool()
def set_back_torch(ip: str, intensity: int) -> dict:
    """Set back LED torch intensity (0–255). 0 = off, 255 = max brightness."""
    return KachakaCommands(KachakaConnection.get(ip)).set_back_torch(intensity)


# ── Laser scan ──────────────────────────────────────────────────────

@mcp.tool()
def activate_laser_scan(ip: str, duration_sec: float) -> dict:
    """Activate laser scanner for on-demand LiDAR data collection.

    The scanner stays active for ``duration_sec`` seconds.
    """
    return KachakaCommands(KachakaConnection.get(ip)).activate_laser_scan(duration_sec)


# ── Auto homing ─────────────────────────────────────────────────────

@mcp.tool()
def set_auto_homing(ip: str, enabled: bool) -> dict:
    """Enable or disable automatic return-to-charger when idle."""
    return KachakaCommands(KachakaConnection.get(ip)).set_auto_homing(enabled)


@mcp.tool()
def get_auto_homing(ip: str) -> dict:
    """Check whether automatic return-to-charger is enabled."""
    return KachakaQueries(KachakaConnection.get(ip)).get_auto_homing_enabled()


# ── Readiness ───────────────────────────────────────────────────────

@mcp.tool()
def is_ready(ip: str) -> dict:
    """Check if the robot is ready to accept commands (non-blocking)."""
    return KachakaQueries(KachakaConnection.get(ip)).is_ready()


# ── Transforms ──────────────────────────────────────────────────────

@mcp.tool()
def get_static_transform(ip: str) -> dict:
    """Get static TF coordinate transforms (e.g. base_link -> camera_link).

    Useful for sensor fusion and custom navigation applications.
    """
    return KachakaQueries(KachakaConnection.get(ip)).get_static_transform()


# ── Dynamic transforms (streaming) ──────────────────────────────────

_tf_streamers: dict[str, TransformStreamer] = {}


@mcp.tool()
def start_transform_stream(ip: str) -> dict:
    """Start streaming dynamic TF transforms in a background thread.

    The robot pushes real-time coordinate transforms (e.g. odom -> base_link).
    Use ``get_dynamic_transform`` to read the latest snapshot.
    """
    key = KachakaConnection._normalise_target(ip)
    existing = _tf_streamers.get(key)
    if existing is not None and existing.is_running:
        return {"ok": True, "message": "already running", "stats": existing.stats}
    conn = KachakaConnection.get(ip)
    streamer = TransformStreamer(conn)
    streamer.start()
    _tf_streamers[key] = streamer
    return {"ok": True, "message": "transform stream started"}


@mcp.tool()
def get_dynamic_transform(ip: str) -> dict:
    """Get the latest dynamic TF transforms from a running stream.

    Returns real-time coordinate transforms (translation + rotation quaternion).
    Must call ``start_transform_stream`` first.
    """
    key = KachakaConnection._normalise_target(ip)
    streamer = _tf_streamers.get(key)
    if streamer is None or not streamer.is_running:
        return {"ok": False, "error": "stream not started — call start_transform_stream first"}
    transforms = streamer.latest_transforms
    if transforms is None:
        return {"ok": False, "error": "no transforms received yet — try again shortly"}
    return {"ok": True, "transforms": transforms, "stats": streamer.stats}


@mcp.tool()
def stop_transform_stream(ip: str) -> dict:
    """Stop the dynamic TF transform stream."""
    key = KachakaConnection._normalise_target(ip)
    streamer = _tf_streamers.pop(key, None)
    if streamer is None:
        return {"ok": True, "message": "no stream to stop"}
    streamer.stop()
    return {"ok": True, "message": "transform stream stopped", "stats": streamer.stats}


# ── Entry point ──────────────────────────────────────────────────────


def main():
    """Console entry point for ``kachaka-mcp`` command."""
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
