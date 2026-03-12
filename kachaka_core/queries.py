"""Read-only status queries — pose, battery, locations, shelves, camera, map.

Every public method returns a ``dict`` with at minimum an ``ok`` key.

Patterns extracted from:
- bio-patrol FleetAPI: get_pose, get_battery_info, get_command_state,
  get_front_camera_ros_compressed, get_locations, get_shelves
- visual-patrol RobotService: get_state (combined pose+battery),
  get_locations, get_front_camera_image, get_error_codes
"""

from __future__ import annotations

import base64
import logging

from .connection import KachakaConnection
from .error_handling import with_retry

logger = logging.getLogger(__name__)


class KachakaQueries:
    """Read-only queries for a single Kachaka robot."""

    def __init__(self, conn: KachakaConnection):
        self.conn = conn
        self.sdk = conn.client

    # ── Combined status ──────────────────────────────────────────────

    @with_retry()
    def get_status(self) -> dict:
        """Full snapshot: pose, battery, command state, errors."""
        pose = self.sdk.get_robot_pose()
        battery_pct, power_status = self.sdk.get_battery_info()
        cmd_state, cmd = self.sdk.get_command_state()
        errors = self.sdk.get_error()
        moving_shelf = self.sdk.get_moving_shelf_id()

        return {
            "ok": True,
            "pose": {"x": pose.x, "y": pose.y, "theta": pose.theta},
            "battery": {
                "percentage": battery_pct,
                "power_status": str(power_status),
            },
            "command": {
                "state": str(cmd_state),
                "running": str(cmd) if cmd else None,
            },
            "errors": errors if errors else [],
            "moving_shelf_id": moving_shelf or None,
        }

    # ── Pose ─────────────────────────────────────────────────────────

    @with_retry()
    def get_pose(self) -> dict:
        """Current robot pose on the map."""
        pose = self.sdk.get_robot_pose()
        return {
            "ok": True,
            "x": pose.x,
            "y": pose.y,
            "theta": pose.theta,
        }

    # ── Battery ──────────────────────────────────────────────────────

    @with_retry()
    def get_battery(self) -> dict:
        """Battery percentage and charging status."""
        pct, status = self.sdk.get_battery_info()
        return {
            "ok": True,
            "percentage": pct,
            "power_status": str(status),
        }

    # ── Locations ────────────────────────────────────────────────────

    @with_retry()
    def list_locations(self) -> dict:
        """All registered locations."""
        locs = self.sdk.get_locations()
        return {
            "ok": True,
            "locations": [
                {
                    "id": loc.id,
                    "name": loc.name,
                    "type": str(loc.type),
                    "pose": {"x": loc.pose.x, "y": loc.pose.y, "theta": loc.pose.theta},
                }
                for loc in locs
            ],
        }

    # ── Shelves ──────────────────────────────────────────────────────

    @with_retry()
    def list_shelves(self) -> dict:
        """All registered shelves."""
        shelves = self.sdk.get_shelves()
        return {
            "ok": True,
            "shelves": [
                {
                    "id": s.id,
                    "name": s.name,
                    "home_location_id": s.home_location_id,
                }
                for s in shelves
            ],
        }

    @with_retry()
    def get_moving_shelf(self) -> dict:
        """ID of the shelf the robot is currently carrying (empty if none)."""
        shelf_id = self.sdk.get_moving_shelf_id()
        return {"ok": True, "shelf_id": shelf_id or None}

    # ── Command state ────────────────────────────────────────────────

    @with_retry()
    def get_command_state(self) -> dict:
        """Current command execution state."""
        state, cmd = self.sdk.get_command_state()
        return {
            "ok": True,
            "state": str(state),
            "command": str(cmd) if cmd else None,
            "is_running": self.sdk.is_command_running(),
        }

    @with_retry()
    def get_last_command_result(self) -> dict:
        """Result of the most recently completed command."""
        result, cmd = self.sdk.get_last_command_result()
        return {
            "ok": True,
            "success": result.success,
            "error_code": result.error_code,
            "command": str(cmd) if cmd else None,
        }

    # ── Camera ───────────────────────────────────────────────────────

    @with_retry()
    def get_front_camera_image(self) -> dict:
        """Compressed JPEG from the front camera, returned as base64."""
        img = self.sdk.get_front_camera_ros_compressed_image()
        b64 = base64.b64encode(img.data).decode()
        return {"ok": True, "image_base64": b64, "format": img.format or "jpeg"}

    @with_retry()
    def get_back_camera_image(self) -> dict:
        """Compressed JPEG from the back camera, returned as base64."""
        img = self.sdk.get_back_camera_ros_compressed_image()
        b64 = base64.b64encode(img.data).decode()
        return {"ok": True, "image_base64": b64, "format": img.format or "jpeg"}

    # ── Map ──────────────────────────────────────────────────────────

    @with_retry()
    def get_map(self) -> dict:
        """Current map as a base64-encoded PNG."""
        png_map = self.sdk.get_png_map()
        b64 = base64.b64encode(png_map.data).decode()
        return {
            "ok": True,
            "image_base64": b64,
            "format": "png",
            "name": png_map.name,
            "resolution": png_map.resolution,
            "width": png_map.width,
            "height": png_map.height,
            "origin_x": png_map.origin.x,
            "origin_y": png_map.origin.y,
        }

    @with_retry()
    def list_maps(self) -> dict:
        """All available maps."""
        maps = self.sdk.get_map_list()
        return {
            "ok": True,
            "maps": [{"id": m.id, "name": m.name} for m in maps],
            "current_map_id": self.sdk.get_current_map_id(),
        }

    # ── Errors ───────────────────────────────────────────────────────

    @with_retry()
    def get_errors(self) -> dict:
        """Current active error codes on the robot."""
        errors = self.sdk.get_error()
        return {"ok": True, "errors": errors if errors else []}

    @with_retry()
    def get_error_definitions(self) -> dict:
        """All known error code definitions from the robot firmware.

        Returns a dict mapping error_code (int) to {title, description}.
        """
        raw = self.sdk.get_robot_error_code()
        definitions = {}
        for code, info in raw.items():
            definitions[code] = {
                "title": getattr(info, "title_en", str(info)),
                "description": getattr(info, "description_en", ""),
            }
        return {"ok": True, "definitions": definitions}

    # ── Robot info ───────────────────────────────────────────────────

    @with_retry()
    def get_serial_number(self) -> dict:
        """Robot serial number."""
        return {"ok": True, "serial": self.sdk.get_robot_serial_number()}

    @with_retry()
    def get_version(self) -> dict:
        """Robot firmware version."""
        return {"ok": True, "version": self.sdk.get_robot_version()}

    @with_retry()
    def get_speaker_volume(self) -> dict:
        """Current speaker volume (0–10)."""
        vol = self.sdk.get_speaker_volume()
        return {"ok": True, "volume": vol}

    # ── Shortcuts ────────────────────────────────────────────────────

    @with_retry()
    def list_shortcuts(self) -> dict:
        """All registered shortcuts (id -> name)."""
        shortcuts = self.sdk.get_shortcuts()
        return {"ok": True, "shortcuts": shortcuts}

    # ── History ──────────────────────────────────────────────────────

    @with_retry()
    def get_history(self) -> dict:
        """Command execution history."""
        history = self.sdk.get_history_list()
        return {
            "ok": True,
            "history": [
                {
                    "id": h.id,
                    "command": str(h.command),
                    "success": h.success,
                    "error_code": h.error_code,
                    "time": str(h.command_executed_time),
                }
                for h in history
            ],
        }
