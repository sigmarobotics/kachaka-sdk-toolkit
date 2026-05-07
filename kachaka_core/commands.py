"""Robot action commands — movement, shelf ops, speech, and manual control.

Every public method returns a ``dict`` with at minimum an ``ok`` key.
Network-retryable errors are handled by ``@with_retry``.

Patterns extracted from:
- bio-patrol FleetAPI: move_to_location, move_shelf, return_shelf, speak,
  dock/undock, return_home, cancel_command
- visual-patrol RobotService: move_to_pose, move_forward, rotate_in_place,
  return_home, cancel_command
"""

from __future__ import annotations

import logging
import os
import time
from typing import Iterator, Optional

from kachaka_api.generated import kachaka_api_pb2 as pb2

from .connection import KachakaConnection
from .error_handling import with_retry

logger = logging.getLogger(__name__)


class KachakaCommands:
    """High-level command interface for a single Kachaka robot."""

    def __init__(self, conn: KachakaConnection):
        self.conn = conn
        self.sdk = conn.client

    # ── Advanced command execution ────────────────────────────────────

    def _start_command_advanced(
        self,
        command: pb2.Command,
        *,
        cancel_all: bool = True,
        tts_on_success: str = "",
        title: str = "",
        deferrable: bool = False,
        lock_on_end_sec: float = 0.0,
        wait_for_completion: bool = True,
    ) -> pb2.Result:
        """Low-level command dispatch with ``deferrable`` and ``lock_on_end`` support.

        These parameters are defined in the gRPC proto but not exposed by the
        official Python SDK convenience methods.
        """
        lock_on_end = None
        if lock_on_end_sec > 0:
            lock_on_end = pb2.LockOnEnd(duration_sec=lock_on_end_sec)

        request = pb2.StartCommandRequest(
            command=command,
            cancel_all=cancel_all,
            tts_on_success=tts_on_success,
            title=title,
            deferrable=deferrable,
            lock_on_end=lock_on_end,
        )

        # Capture cursor before issuing the command
        cursor_meta = pb2.Metadata(cursor=0)
        cursor_meta.cursor = self.sdk.stub.GetCommandState(
            pb2.GetRequest(metadata=cursor_meta)
        ).metadata.cursor

        response = self.sdk.stub.StartCommand(request)
        if not response.result.success or not wait_for_completion:
            return response.result

        # Poll until our command_id appears in last result
        while True:
            last = self.sdk.stub.GetLastCommandResult(
                pb2.GetRequest(metadata=cursor_meta)
            )
            cursor_meta.cursor = last.metadata.cursor
            if last.command_id == response.command_id:
                break

        result, _ = self.sdk.get_last_command_result()
        return result

    # ── Movement ─────────────────────────────────────────────────────

    @with_retry()
    def move_to_location(
        self,
        location_name: str,
        *,
        cancel_all: bool = True,
        tts_on_success: str = "",
        title: str = "",
    ) -> dict:
        """Move to a registered location by name or ID.

        The resolver is initialised on first use so that name lookups work.
        """
        self.conn.ensure_resolver()
        location_id = self.conn.resolve_location(location_name)
        result = self.sdk.move_to_location(
            location_id,
            cancel_all=cancel_all,
            tts_on_success=tts_on_success,
            title=title,
        )
        return self._result_to_dict(result, action="move_to_location", target=location_name)

    @with_retry()
    def move_to_pose(
        self,
        x: float,
        y: float,
        yaw: float,
        *,
        cancel_all: bool = True,
        tts_on_success: str = "",
        title: str = "",
    ) -> dict:
        """Move to an absolute map coordinate ``(x, y, yaw)``."""
        result = self.sdk.move_to_pose(
            x, y, yaw,
            cancel_all=cancel_all,
            tts_on_success=tts_on_success,
            title=title,
        )
        return self._result_to_dict(result, action="move_to_pose", target=f"({x}, {y}, {yaw})")

    @with_retry()
    def move_forward(self, distance_meter: float, *, speed: float = 0.0) -> dict:
        """Move forward (positive) or backward (negative) by *distance_meter*.

        ``speed=0.0`` lets the robot decide. Max is 0.3 m/s.
        """
        result = self.sdk.move_forward(distance_meter, speed=speed)
        return self._result_to_dict(result, action="move_forward", target=f"{distance_meter}m")

    @with_retry()
    def rotate_in_place(self, angle_radian: float) -> dict:
        """Rotate in place by *angle_radian* (positive = counter-clockwise)."""
        result = self.sdk.rotate_in_place(angle_radian)
        return self._result_to_dict(result, action="rotate_in_place", target=f"{angle_radian}rad")

    @with_retry()
    def return_home(
        self,
        *,
        cancel_all: bool = True,
        tts_on_success: str = "",
        title: str = "",
    ) -> dict:
        """Return to charger."""
        result = self.sdk.return_home(
            cancel_all=cancel_all,
            tts_on_success=tts_on_success,
            title=title,
        )
        return self._result_to_dict(result, action="return_home")

    # ── Shelf operations ─────────────────────────────────────────────

    @with_retry()
    def move_shelf(
        self,
        shelf_name: str,
        location_name: str,
        *,
        undock_on_destination: bool = False,
        cancel_all: bool = True,
        tts_on_success: str = "",
        title: str = "",
        deferrable: bool = False,
        lock_on_end_sec: float = 0.0,
    ) -> dict:
        """Pick up *shelf_name* and deliver it to *location_name*.

        Args:
            undock_on_destination: Automatically undock the shelf at the
                destination instead of staying docked (proto field not
                exposed by the official SDK).
            deferrable: Queue the command instead of cancelling the current one.
            lock_on_end_sec: Lock the robot for this many seconds after the
                command completes.
        """
        self.conn.ensure_resolver()
        shelf_id = self.conn.resolve_shelf(shelf_name)
        location_id = self.conn.resolve_location(location_name)

        use_advanced = undock_on_destination or deferrable or lock_on_end_sec > 0
        if use_advanced:
            cmd = pb2.Command(
                move_shelf_command=pb2.MoveShelfCommand(
                    target_shelf_id=shelf_id,
                    destination_location_id=location_id,
                    undock_on_destination=undock_on_destination,
                )
            )
            result = self._start_command_advanced(
                cmd,
                cancel_all=cancel_all,
                tts_on_success=tts_on_success,
                title=title,
                deferrable=deferrable,
                lock_on_end_sec=lock_on_end_sec,
            )
        else:
            result = self.sdk.move_shelf(
                shelf_id,
                location_id,
                cancel_all=cancel_all,
                tts_on_success=tts_on_success,
                title=title,
            )
        return self._result_to_dict(
            result, action="move_shelf", target=f"{shelf_name} -> {location_name}"
        )

    @with_retry()
    def return_shelf(self, shelf_name: str = "", **kwargs) -> dict:
        """Return the shelf to its home location."""
        self.conn.ensure_resolver()
        shelf_id = self.conn.resolve_shelf(shelf_name) if shelf_name else ""
        result = self.sdk.return_shelf(shelf_id, **kwargs)
        return self._result_to_dict(result, action="return_shelf", target=shelf_name or "(current)")

    @with_retry()
    def dock_shelf(self, **kwargs) -> dict:
        """Dock the currently held shelf."""
        result = self.sdk.dock_shelf(**kwargs)
        return self._result_to_dict(result, action="dock_shelf")

    @with_retry()
    def dock_any_shelf_with_registration(
        self,
        location_name: str,
        dock_forward: bool = False,
        *,
        cancel_all: bool = True,
        tts_on_success: str = "",
        title: str = "",
    ) -> dict:
        """Move to *location_name* and dock any shelf placed there. Registers unregistered shelves automatically."""
        self.conn.ensure_resolver()
        location_id = self.conn.resolve_location(location_name)
        result = self.sdk.dock_any_shelf_with_registration(
            location_id,
            dock_forward,
            cancel_all=cancel_all,
            tts_on_success=tts_on_success,
            title=title,
        )
        return self._result_to_dict(
            result,
            action="dock_any_shelf_with_registration",
            target=location_name,
        )

    @with_retry()
    def undock_shelf(self, **kwargs) -> dict:
        """Undock the currently held shelf."""
        result = self.sdk.undock_shelf(**kwargs)
        return self._result_to_dict(result, action="undock_shelf")

    @with_retry()
    def reset_shelf_pose(self, shelf_name: str) -> dict:
        """Reset the recorded pose of a shelf."""
        self.conn.ensure_resolver()
        shelf_id = self.conn.resolve_shelf(shelf_name)
        result = self.sdk.reset_shelf_pose(shelf_id)
        return self._result_to_dict(result, action="reset_shelf_pose", target=shelf_name)

    # ── Speech ───────────────────────────────────────────────────────

    @with_retry()
    def speak(
        self,
        text: str,
        *,
        cancel_all: bool = True,
        tts_on_success: str = "",
        title: str = "",
    ) -> dict:
        """Text-to-speech on the robot's speaker."""
        result = self.sdk.speak(
            text,
            cancel_all=cancel_all,
            tts_on_success=tts_on_success,
            title=title,
        )
        return self._result_to_dict(result, action="speak", target=text[:40])

    @with_retry()
    def set_speaker_volume(self, volume: int) -> dict:
        """Set speaker volume (0–10)."""
        volume = max(0, min(10, volume))
        result = self.sdk.set_speaker_volume(volume)
        return self._result_to_dict(result, action="set_speaker_volume", target=str(volume))

    # ── Shortcuts ─────────────────────────────────────────────────────

    @with_retry()
    def start_shortcut(
        self,
        shortcut_id: str,
        *,
        cancel_all: bool = True,
    ) -> dict:
        """Execute a registered shortcut by its ID."""
        result = self.sdk.start_shortcut_command(
            shortcut_id, cancel_all=cancel_all,
        )
        return self._result_to_dict(result, action="start_shortcut", target=shortcut_id)

    # ── Map management ────────────────────────────────────────────────

    def export_map(self, map_id: str, output_path: str) -> dict:
        """Export a map to a binary file (Kachaka proprietary format).

        The exported file can be re-imported with ``import_map``.
        """
        try:
            result = self.sdk.export_map(map_id, output_path)
            if not result.success:
                return self._result_to_dict(result, action="export_map", target=map_id)
            size = os.path.getsize(output_path)
            return {
                "ok": True,
                "action": "export_map",
                "map_id": map_id,
                "path": output_path,
                "size_bytes": size,
            }
        except Exception as exc:
            return {"ok": False, "error": str(exc), "action": "export_map"}

    def import_map(self, file_path: str, chunk_size: int = 1024 * 1024) -> dict:
        """Import a map from a previously exported binary file.

        Returns the new map ID assigned by the robot.
        """
        try:
            result, map_id = self.sdk.import_map(file_path, chunk_size=chunk_size)
            if not result.success:
                return self._result_to_dict(result, action="import_map", target=file_path)
            return {
                "ok": True,
                "action": "import_map",
                "map_id": map_id,
                "source": file_path,
            }
        except Exception as exc:
            return {"ok": False, "error": str(exc), "action": "import_map"}

    def import_image_as_map(
        self,
        image_path: str,
        resolution: float,
        charger_x: float,
        charger_y: float,
        charger_theta: float = 0.0,
        chunk_size: int = 1024 * 1024,
    ) -> dict:
        """Import a PNG occupancy grid image as a new map (ROS-style).

        Args:
            image_path: Path to a grayscale PNG file (ROS occupancy grid format).
            resolution: Meters per pixel.
            charger_x: Charger X position in world coordinates.
            charger_y: Charger Y position in world coordinates.
            charger_theta: Charger orientation in radians.
            chunk_size: Streaming chunk size in bytes.
        """
        try:
            with open(image_path, "rb") as f:
                image_data = f.read()

            charger_pose = pb2.Pose(x=charger_x, y=charger_y, theta=charger_theta)

            def request_iterator() -> Iterator[pb2.ImportImageAsMapRequest]:
                offset = 0
                while offset < len(image_data):
                    chunk = image_data[offset : offset + chunk_size]
                    yield pb2.ImportImageAsMapRequest(
                        data=chunk,
                        charger_pose=charger_pose,
                        resolution=resolution,
                    )
                    offset += chunk_size

            response = self.sdk.stub.ImportImageAsMap(request_iterator())
            if not response.result.success:
                return self._result_to_dict(
                    response.result, action="import_image_as_map", target=image_path,
                )
            return {
                "ok": True,
                "action": "import_image_as_map",
                "map_id": response.map_id,
                "source": image_path,
                "resolution": resolution,
                "charger_pose": {"x": charger_x, "y": charger_y, "theta": charger_theta},
            }
        except Exception as exc:
            return {"ok": False, "error": str(exc), "action": "import_image_as_map"}

    def switch_map(
        self,
        map_id: str,
        *,
        pose_x: Optional[float] = None,
        pose_y: Optional[float] = None,
        pose_theta: Optional[float] = None,
        inherit_docking_state: bool = False,
    ) -> dict:
        """Switch to a different map.

        Optionally specify an initial pose ``(pose_x, pose_y, pose_theta)``.
        When no pose is given, the charger pose of the target map is used.
        """
        try:
            pose = None
            if pose_x is not None and pose_y is not None:
                pose = {"x": pose_x, "y": pose_y, "theta": pose_theta or 0.0}
            result = self.sdk.switch_map(
                map_id,
                pose=pose,
                inherit_docking_state_and_docked_shelf=inherit_docking_state,
            )
            d = self._result_to_dict(result, action="switch_map", target=map_id)
            if d["ok"]:
                self.conn.refresh_maps()
            return d
        except Exception as exc:
            return {"ok": False, "error": str(exc), "action": "switch_map"}

    # ── Command control ──────────────────────────────────────────────

    @with_retry()
    def cancel_command(self) -> dict:
        """Cancel the currently running command."""
        result, cmd = self.sdk.cancel_command()
        return {
            "ok": result.success,
            "error_code": result.error_code if not result.success else 0,
            "cancelled_command": str(cmd) if cmd else None,
        }

    @with_retry()
    def proceed(self) -> dict:
        """Resume a command that is waiting for user confirmation."""
        result = self.sdk.proceed()
        return self._result_to_dict(result, action="proceed")

    # ── Manual control ───────────────────────────────────────────────

    @with_retry()
    def set_manual_control(
        self,
        enabled: bool,
        *,
        use_shelf_registration: bool = False,
    ) -> dict:
        """Enable or disable manual velocity control mode.

        Args:
            use_shelf_registration: When ``enabled=True``, also activate
                shelf recognition during manual control (proto field not
                exposed by the official SDK).
        """
        if use_shelf_registration and enabled:
            request = pb2.SetManualControlEnabledRequest(
                enable=True, use_shelf_registration=True,
            )
            response = self.sdk.stub.SetManualControlEnabled(request)
            return self._result_to_dict(
                response.result, action="set_manual_control", target="True+shelf_reg"
            )
        result = self.sdk.set_manual_control_enabled(enabled)
        return self._result_to_dict(result, action="set_manual_control", target=str(enabled))

    @with_retry()
    def set_velocity(self, linear: float, angular: float) -> dict:
        """Send velocity command (requires manual-control mode).

        Max linear: 0.3 m/s, max angular: 1.57 rad/s.
        """
        linear = max(-0.3, min(0.3, linear))
        angular = max(-1.57, min(1.57, angular))
        result = self.sdk.set_robot_velocity(linear, angular)
        return self._result_to_dict(
            result, action="set_velocity", target=f"lin={linear}, ang={angular}"
        )

    def stop(self) -> dict:
        """Emergency stop — sets velocity to zero and disables manual control."""
        try:
            self.sdk.set_robot_stop()
            return {"ok": True, "action": "stop"}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    # ── Polling ──────────────────────────────────────────────────────

    def poll_until_complete(
        self,
        timeout: float = 120.0,
        interval: float = 0.5,
    ) -> dict:
        """Block until the current command finishes or *timeout* expires.

        Returns the final command state.
        """
        start = time.time()
        while time.time() - start < timeout:
            try:
                if not self.sdk.is_command_running():
                    result, cmd = self.sdk.get_last_command_result()
                    return {
                        "ok": result.success,
                        "error_code": result.error_code,
                        "command": str(cmd) if cmd else None,
                        "elapsed": round(time.time() - start, 1),
                    }
            except Exception as exc:
                logger.debug("poll error: %s", exc)
            time.sleep(interval)
        return {"ok": False, "error": "timeout", "timeout": timeout}

    # ── Torch / lighting ────────────────────────────────────────────

    @with_retry()
    def set_front_torch(self, intensity: int) -> dict:
        """Set front LED torch intensity (0–255).

        This gRPC RPC is not wrapped by the official Python SDK.
        """
        intensity = max(0, min(255, intensity))
        request = pb2.SetFrontTorchIntensityRequest(intensity=intensity)
        response = self.sdk.stub.SetFrontTorchIntensity(request)
        return self._result_to_dict(
            response.result, action="set_front_torch", target=str(intensity)
        )

    @with_retry()
    def set_back_torch(self, intensity: int) -> dict:
        """Set back LED torch intensity (0–255).

        This gRPC RPC is not wrapped by the official Python SDK.
        """
        intensity = max(0, min(255, intensity))
        request = pb2.SetBackTorchIntensityRequest(intensity=intensity)
        response = self.sdk.stub.SetBackTorchIntensity(request)
        return self._result_to_dict(
            response.result, action="set_back_torch", target=str(intensity)
        )

    # ── Laser scan ───────────────────────────────────────────────────

    @with_retry()
    def activate_laser_scan(self, duration_sec: float) -> dict:
        """Activate the laser scanner for *duration_sec* seconds.

        This gRPC RPC is not wrapped by the official Python SDK.
        Useful for on-demand LiDAR data collection.
        """
        request = pb2.ActivateLaserScanRequest(duration_sec=duration_sec)
        response = self.sdk.stub.ActivateLaserScan(request)
        return self._result_to_dict(
            response.result, action="activate_laser_scan", target=f"{duration_sec}s"
        )

    # ── Auto homing ──────────────────────────────────────────────────

    @with_retry()
    def set_auto_homing(self, enabled: bool) -> dict:
        """Enable or disable automatic return-to-charger behaviour."""
        result = self.sdk.set_auto_homing_enabled(enabled)
        return self._result_to_dict(result, action="set_auto_homing", target=str(enabled))

    # ── Recovery ─────────────────────────────────────────────────────

    def restart_robot(self) -> dict:
        """Reboot the robot to clear hardware-fatal errors (e.g. 21004 LiDAR).

        This is a heavy operation: the robot drops the gRPC connection,
        cancels every in-flight task, and takes ~30 seconds to come back.
        Use it only when ``is_ready()`` reports
        ``recovery_hint="restart_robot"``. Pressed-pause state (21051) is
        not cleared by this — it requires the physical power button.

        Returns immediately after the RPC is acknowledged. Callers should
        wait for ``ping()`` to succeed again before issuing further commands.
        """
        try:
            result = self.sdk.restart_robot()
        except Exception as exc:
            # Robot may close the gRPC channel before the response arrives;
            # treat connection-drop on this RPC as a successful restart.
            logger.info("restart_robot RPC closed connection: %s", exc)
            return {"ok": True, "action": "restart_robot", "note": "rpc closed by reboot"}
        return self._result_to_dict(result, action="restart_robot")

    # ── Internal ─────────────────────────────────────────────────────

    def _resolve_error_description(self, error_code: int) -> str:
        """Look up error description from cached definitions."""
        defs = self.conn.error_definitions
        if error_code in defs:
            return defs[error_code].get("title", "")
        return ""

    def _result_to_dict(self, result, *, action: str = "", target: str = "") -> dict:
        """Convert a ``pb2.Result`` into a standardised response dict."""
        d: dict = {"ok": result.success}
        if not result.success:
            ec = result.error_code
            d["error_code"] = ec
            desc = self._resolve_error_description(ec)
            d["error"] = f"error_code={ec}" + (f": {desc}" if desc else "")
        if action:
            d["action"] = action
        if target:
            d["target"] = target
        return d
