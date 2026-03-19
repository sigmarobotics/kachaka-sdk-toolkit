"""RobotController — background state polling + non-blocking command execution.

Provides a unified interface for all movement commands (move_to_location,
return_home, move_shelf, return_shelf, dock_any_shelf_with_registration) with:
- Background thread continuously reading battery, pose, command state
- Non-blocking gRPC command execution with command_id verification
- Deadline-based retry on transient failures
- Built-in metrics collection (RTT, poll counts)
"""

from __future__ import annotations

import copy
import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Callable, Optional

from kachaka_api.generated import kachaka_api_pb2 as pb2

from .connection import ConnectionState, KachakaConnection

logger = logging.getLogger(__name__)


@dataclass
class RobotState:
    """Snapshot of robot state, updated by the background polling thread."""
    battery_pct: int = 0
    pose_x: float = 0.0
    pose_y: float = 0.0
    pose_theta: float = 0.0
    is_command_running: bool = False
    last_updated: float = 0.0
    moving_shelf_id: Optional[str] = None
    shelf_dropped: bool = False
    connection_state: str = "connected"
    disconnected_at: Optional[float] = None
    last_reconnect_at: Optional[float] = None


@dataclass
class ControllerMetrics:
    """Metrics collected during command execution polling."""
    poll_rtt_list: list[float] = field(default_factory=list)
    poll_count: int = 0
    poll_success_count: int = 0
    poll_failure_count: int = 0

    def reset(self) -> None:
        self.poll_rtt_list.clear()
        self.poll_count = 0
        self.poll_success_count = 0
        self.poll_failure_count = 0


def _call_with_retry(
    func,
    *args,
    deadline: float,
    retry_delay: float = 1.0,
    max_attempts: int = 0,
    **kwargs,
):
    """Call func with retry until deadline or max_attempts.

    Args:
        func: Callable to invoke.
        deadline: Absolute time (perf_counter) after which to stop.
        retry_delay: Seconds between retries.
        max_attempts: Max attempts (0 = unlimited, deadline only).

    Returns:
        The return value of func on success.

    Raises:
        The last exception if all retries fail.
        TimeoutError if deadline passed without any attempt.
    """
    last_err = None
    attempt = 0
    while time.perf_counter() < deadline:
        attempt += 1
        try:
            return func(*args, **kwargs)
        except Exception as e:
            last_err = e
            logger.debug("_call_with_retry %s attempt %d: %s", getattr(func, "__name__", func), attempt, e)
            if max_attempts > 0 and attempt >= max_attempts:
                break
            remaining = deadline - time.perf_counter()
            if remaining <= 0:
                break
            time.sleep(min(retry_delay, remaining))
    if last_err is not None:
        raise last_err
    raise TimeoutError("deadline exceeded without any attempt")


class RobotController:
    """Background state polling + non-blocking command execution for Kachaka.

    Usage::

        conn = KachakaConnection.get("192.168.50.133")
        ctrl = RobotController(conn)
        ctrl.start()

        state = ctrl.state  # thread-safe snapshot
        result = ctrl.move_to_location("辦公室", timeout=120)

        ctrl.stop()
    """

    def __init__(
        self,
        conn: KachakaConnection,
        *,
        fast_interval: float = 1.0,
        slow_interval: float = 30.0,
        retry_delay: float = 1.0,
        poll_interval: float = 1.0,
        on_shelf_dropped: Optional[Callable[[str], None]] = None,
    ) -> None:
        self._conn = conn
        self._fast_interval = fast_interval
        self._slow_interval = slow_interval
        self._retry_delay = retry_delay
        self._poll_interval = poll_interval
        self._on_shelf_dropped = on_shelf_dropped

        self._state = RobotState()
        self._state_lock = threading.Lock()
        self._metrics = ControllerMetrics()
        self._monitoring_shelf: bool = False
        self._shelf_confirmed_docked: bool = False

        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    # ── Public API ────────────────────────────────────────────────

    @property
    def state(self) -> RobotState:
        """Return a thread-safe snapshot of the current robot state."""
        with self._state_lock:
            return copy.copy(self._state)

    @property
    def metrics(self) -> ControllerMetrics:
        """Return reference to the metrics object.

        Not a snapshot — read after command execution completes,
        not concurrently from another thread.
        """
        return self._metrics

    def reset_metrics(self) -> None:
        """Clear all collected metrics."""
        self._metrics.reset()

    def reset_shelf_monitor(self) -> None:
        """Reset the shelf_dropped flag and stop shelf monitoring."""
        self._monitoring_shelf = False
        self._shelf_confirmed_docked = False
        with self._state_lock:
            self._state.shelf_dropped = False
            self._state.moving_shelf_id = None

    def start(self) -> None:
        """Start the background state polling thread. No-op if already running."""
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._conn.start_monitoring(
            interval=self._fast_interval,
            on_state_change=self._on_conn_state_change,
        )
        self._thread = threading.Thread(target=self._state_loop, daemon=True)
        self._thread.start()
        logger.info(
            "RobotController started (fast=%.1fs, slow=%.1fs)",
            self._fast_interval,
            self._slow_interval,
        )

    def stop(self) -> None:
        """Stop the background state polling thread. No-op if not running."""
        self._conn.stop_monitoring()
        if self._thread is None or not self._thread.is_alive():
            return
        self._stop_event.set()
        self._thread.join(timeout=self._fast_interval * 3)
        if self._thread.is_alive():
            logger.warning("State polling thread did not stop within timeout")
        else:
            logger.info("RobotController stopped")

    # ── State polling loop ────────────────────────────────────────

    def _state_loop(self) -> None:
        """Background thread: periodically read robot state."""
        sdk = self._conn.client
        last_slow = 0.0

        while not self._stop_event.is_set():
            # Skip polling while disconnected — avoids wasting 5s per
            # call on the interceptor timeout.
            if self._conn.state == ConnectionState.DISCONNECTED:
                self._stop_event.wait(self._fast_interval)
                continue

            now = time.time()

            # Fast cycle: pose + command state + moving shelf
            try:
                pose = sdk.get_robot_pose()
                is_running = sdk.is_command_running()
                moving_shelf = sdk.get_moving_shelf_id() or None
                with self._state_lock:
                    self._state.pose_x = pose.x
                    self._state.pose_y = pose.y
                    self._state.pose_theta = pose.theta
                    self._state.is_command_running = is_running
                    # Only update moving_shelf_id when shelf monitor is NOT active.
                    # During _execute_command, the shelf monitor in the polling loop
                    # owns this field to detect drops without race conditions.
                    if not self._monitoring_shelf:
                        self._state.moving_shelf_id = moving_shelf
                    self._state.last_updated = now
            except Exception:
                logger.debug("State poll (fast) error", exc_info=True)

            # Slow cycle: battery
            if now - last_slow >= self._slow_interval:
                try:
                    battery_pct, _ = sdk.get_battery_info()
                    with self._state_lock:
                        self._state.battery_pct = int(battery_pct)
                    last_slow = now
                except Exception:
                    logger.debug("State poll (slow/battery) error", exc_info=True)

            self._stop_event.wait(self._fast_interval)

    # ── Connection state callback ─────────────────────────────

    def _on_conn_state_change(self, new_state: ConnectionState) -> None:
        """Called by KachakaConnection monitoring on state transitions."""
        with self._state_lock:
            self._state.connection_state = new_state.value
            if new_state == ConnectionState.DISCONNECTED:
                self._state.disconnected_at = time.perf_counter()
            elif new_state == ConnectionState.CONNECTED:
                self._state.last_reconnect_at = time.perf_counter()

        if new_state == ConnectionState.CONNECTED:
            # Trigger immediate probe in a background thread to avoid
            # blocking the monitoring callback.
            threading.Thread(
                target=self._reconnect_probe, daemon=True
            ).start()

    def _reconnect_probe(self) -> None:
        """Immediate state refresh after reconnection."""
        sdk = self._conn.client
        try:
            pose = sdk.get_robot_pose()
            is_running = sdk.is_command_running()
            battery_pct, _ = sdk.get_battery_info()
            moving_shelf = sdk.get_moving_shelf_id() or None
            with self._state_lock:
                self._state.pose_x = pose.x
                self._state.pose_y = pose.y
                self._state.pose_theta = pose.theta
                self._state.is_command_running = is_running
                self._state.battery_pct = int(battery_pct)
                self._state.moving_shelf_id = moving_shelf
                self._state.last_updated = time.time()
        except Exception:
            logger.debug("Reconnect probe failed", exc_info=True)

    # ── Error resolution ─────────────────────────────────────

    def _resolve_error_description(self, error_code: int) -> str:
        """Fetch error description from robot. Returns empty string on failure."""
        try:
            definitions = self._conn.client.get_robot_error_code()
            if error_code in definitions:
                info = definitions[error_code]
                return getattr(info, "title_en", "") or getattr(info, "title", "") or ""
        except Exception:
            logger.debug("Failed to fetch error description for %d", error_code)
        return ""

    # ── Command execution engine ──────────────────────────────

    def _execute_command(
        self,
        command: pb2.Command,
        action: str,
        target: str = "",
        *,
        timeout: float = 120.0,
        cancel_all: bool = True,
        tts_on_success: str = "",
        title: str = "",
    ) -> dict:
        """Send a command to the robot and poll until completion or timeout.

        Not thread-safe — callers must serialise command execution.
        (Metrics counters use plain ``+=`` without locking.)

        Returns a standardised result dict:
            {"ok": True,  "action": ..., "target": ..., "elapsed": ...}
            {"ok": False, "action": ..., "error_code": ..., "error": ..., "elapsed": ...}
            {"ok": False, "action": ..., "target": ..., "error": "TIMEOUT", "timeout": ...}
        """
        stub = self._conn.client.stub
        deadline = time.perf_counter() + timeout
        t0 = time.perf_counter()

        # 0. Wait for connection if currently disconnected
        if self._conn.state == ConnectionState.DISCONNECTED:
            remaining = deadline - time.perf_counter()
            if remaining <= 0 or not self._conn.wait_for_state(
                ConnectionState.CONNECTED, timeout=remaining
            ):
                elapsed = time.perf_counter() - t0
                return {
                    "ok": False,
                    "action": action,
                    "target": target,
                    "error": "DISCONNECTED",
                    "elapsed": elapsed,
                }

        # 1. Start the command (with retry until deadline)
        request = pb2.StartCommandRequest(
            command=command,
            cancel_all=cancel_all,
            tts_on_success=tts_on_success,
            title=title,
        )
        try:
            start_resp = _call_with_retry(
                stub.StartCommand,
                request,
                deadline=deadline,
                retry_delay=self._retry_delay,
            )
        except Exception as exc:
            elapsed = time.perf_counter() - t0
            return {
                "ok": False,
                "action": action,
                "target": target,
                "error": str(exc),
                "elapsed": elapsed,
            }

        command_id = start_resp.command_id

        # 2. If the robot rejected the command immediately, return error
        if not start_resp.result.success:
            elapsed = time.perf_counter() - t0
            ec = start_resp.result.error_code
            desc = self._resolve_error_description(ec)
            return {
                "ok": False,
                "action": action,
                "target": target,
                "error_code": ec,
                "error": f"error_code={ec}" + (f": {desc}" if desc else ""),
                "elapsed": elapsed,
            }

        # 3. Wait for the command to register (poll GetCommandState, max 5s, 0.2s)
        reg_deadline = min(time.perf_counter() + 5.0, deadline)
        while time.perf_counter() < reg_deadline:
            try:
                state_resp = stub.GetCommandState(pb2.GetRequest())
                if (
                    state_resp.command_id == command_id
                    and state_resp.state
                    in (
                        pb2.COMMAND_STATE_RUNNING,
                        pb2.COMMAND_STATE_PENDING,
                    )
                ):
                    break
            except Exception:
                pass
            time.sleep(0.2)
        else:
            logger.debug("Command %s registration not confirmed within 5s", command_id)

        # 4. Main polling loop
        while time.perf_counter() < deadline:
            self._metrics.poll_count += 1
            poll_t0 = time.perf_counter()

            try:
                state_resp = stub.GetCommandState(pb2.GetRequest())
                rtt = (time.perf_counter() - poll_t0) * 1000  # ms
                self._metrics.poll_rtt_list.append(rtt)
                self._metrics.poll_success_count += 1
            except Exception:
                self._metrics.poll_failure_count += 1
                time.sleep(self._poll_interval)
                continue

            # Shelf monitoring — detect drops during command execution
            # Only trigger shelf_dropped when the shelf was confirmed docked
            # (mid was non-empty at least once) and then disappeared.
            # This avoids false positives during the dock phase of move_shelf
            # where the robot is still moving toward the shelf.
            if self._monitoring_shelf:
                try:
                    mid = self._conn.client.get_moving_shelf_id() or ""
                    with self._state_lock:
                        prev = self._state.moving_shelf_id
                        if mid:
                            # Shelf confirmed docked — track it
                            self._state.moving_shelf_id = mid
                            self._shelf_confirmed_docked = True
                        elif self._shelf_confirmed_docked and prev:
                            # Was docked, now gone — real drop
                            self._state.moving_shelf_id = None
                            self._state.shelf_dropped = True
                            logger.warning("Shelf dropped during command: %s", prev)
                            if self._on_shelf_dropped:
                                self._on_shelf_dropped(prev)
                            self._monitoring_shelf = False
                            self._shelf_confirmed_docked = False
                        # else: not yet docked — keep waiting
                except Exception:
                    logger.debug("Shelf monitor poll error", exc_info=True)

            # Check result when: state left RUNNING/PENDING, OR
            # a different command replaced ours (command_id changed).
            if (
                state_resp.state
                not in (pb2.COMMAND_STATE_RUNNING, pb2.COMMAND_STATE_PENDING)
                or state_resp.command_id != command_id
            ):
                try:
                    result_resp = _call_with_retry(
                        stub.GetLastCommandResult,
                        pb2.GetRequest(),
                        deadline=deadline,
                        retry_delay=self._retry_delay,
                    )
                except Exception as exc:
                    elapsed = time.perf_counter() - t0
                    return {
                        "ok": False,
                        "action": action,
                        "target": target,
                        "error": str(exc),
                        "elapsed": elapsed,
                    }

                # Verify this result is for OUR command
                if result_resp.command_id == command_id:
                    elapsed = time.perf_counter() - t0
                    if result_resp.result.success:
                        return {
                            "ok": True,
                            "action": action,
                            "target": target,
                            "elapsed": elapsed,
                        }
                    else:
                        ec = result_resp.result.error_code
                        desc = self._resolve_error_description(ec)
                        return {
                            "ok": False,
                            "action": action,
                            "target": target,
                            "error_code": ec,
                            "error": f"error_code={ec}" + (f": {desc}" if desc else ""),
                            "elapsed": elapsed,
                        }
                # command_id mismatch — our command might still be pending
                logger.debug(
                    "command_id mismatch: ours=%s, got=%s — continuing poll",
                    command_id,
                    result_resp.command_id,
                )

            time.sleep(self._poll_interval)

        # 5. Timeout
        return {
            "ok": False,
            "action": action,
            "target": target,
            "error": "TIMEOUT",
            "timeout": timeout,
        }

    # ── Movement commands ─────────────────────────────────────

    def move_to_location(
        self,
        location_name: str,
        *,
        timeout: float = 120.0,
        cancel_all: bool = True,
        tts_on_success: str = "",
        title: str = "",
    ) -> dict:
        """Move the robot to a named location."""
        self._conn.ensure_resolver()
        location_id = self._conn.resolve_location(location_name)
        cmd = pb2.Command(
            move_to_location_command=pb2.MoveToLocationCommand(
                target_location_id=location_id
            )
        )
        return self._execute_command(
            cmd,
            "move_to_location",
            location_name,
            timeout=timeout,
            cancel_all=cancel_all,
            tts_on_success=tts_on_success,
            title=title,
        )

    def return_home(
        self,
        *,
        timeout: float = 60.0,
        cancel_all: bool = True,
        tts_on_success: str = "",
        title: str = "",
    ) -> dict:
        """Move the robot to its home (charger) location."""
        cmd = pb2.Command(return_home_command=pb2.ReturnHomeCommand())
        return self._execute_command(
            cmd,
            "return_home",
            timeout=timeout,
            cancel_all=cancel_all,
            tts_on_success=tts_on_success,
            title=title,
        )

    def move_shelf(
        self,
        shelf_name: str,
        location_name: str,
        *,
        timeout: float = 120.0,
        cancel_all: bool = True,
        tts_on_success: str = "",
        title: str = "",
    ) -> dict:
        """Move a shelf to a named location."""
        self._conn.ensure_resolver()
        shelf_id = self._conn.resolve_shelf(shelf_name)
        location_id = self._conn.resolve_location(location_name)
        cmd = pb2.Command(
            move_shelf_command=pb2.MoveShelfCommand(
                target_shelf_id=shelf_id,
                destination_location_id=location_id,
            )
        )
        # Start shelf monitoring BEFORE the command. Don't seed moving_shelf_id —
        # wait for get_moving_shelf_id() to confirm the shelf is actually docked.
        # This avoids false shelf_dropped during the dock phase.
        with self._state_lock:
            self._state.shelf_dropped = False
            self._state.moving_shelf_id = None
        self._shelf_confirmed_docked = False
        self._monitoring_shelf = True
        return self._execute_command(
            cmd,
            "move_shelf",
            f"{shelf_name} -> {location_name}",
            timeout=timeout,
            cancel_all=cancel_all,
            tts_on_success=tts_on_success,
            title=title,
        )

    def return_shelf(
        self,
        shelf_name: str = "",
        *,
        timeout: float = 60.0,
        cancel_all: bool = True,
        tts_on_success: str = "",
        title: str = "",
    ) -> dict:
        """Return a shelf to its home location."""
        shelf_id = ""
        if shelf_name:
            self._conn.ensure_resolver()
            shelf_id = self._conn.resolve_shelf(shelf_name)
        cmd = pb2.Command(
            return_shelf_command=pb2.ReturnShelfCommand(
                target_shelf_id=shelf_id
            )
        )
        result = self._execute_command(
            cmd,
            "return_shelf",
            shelf_name,
            timeout=timeout,
            cancel_all=cancel_all,
            tts_on_success=tts_on_success,
            title=title,
        )
        self._monitoring_shelf = False
        return result

    def dock_any_shelf_with_registration(
        self,
        location_name: str,
        dock_forward: bool = False,
        *,
        timeout: float = 120.0,
        cancel_all: bool = True,
        tts_on_success: str = "",
        title: str = "",
    ) -> dict:
        """Move to a location and dock any shelf there, registering it if new."""
        self._conn.ensure_resolver()
        location_id = self._conn.resolve_location(location_name)
        cmd = pb2.Command(
            dock_any_shelf_with_registration_command=pb2.DockAnyShelfWithRegistrationCommand(
                target_location_id=location_id,
                dock_forward=dock_forward,
            )
        )
        return self._execute_command(
            cmd,
            "dock_any_shelf_with_registration",
            location_name,
            timeout=timeout,
            cancel_all=cancel_all,
            tts_on_success=tts_on_success,
            title=title,
        )
