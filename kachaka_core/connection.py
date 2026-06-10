"""Kachaka gRPC connection management with pooling and health checks.

Patterns extracted from:
- bio-patrol RobotManager: lazy init, resolver patching, async-safe
- visual-patrol RobotService: serial-number ping, thread-safe locks

Both MCP Server and application code use ``KachakaConnection.get(ip)``
to obtain a pooled, verified connection.
"""

from __future__ import annotations

import enum
import logging
import threading
import time
from typing import Callable, Optional

import grpc
from kachaka_api import KachakaApiClient
from kachaka_api.generated.kachaka_api_pb2_grpc import KachakaApiStub

from kachaka_core.interceptors import TimeoutInterceptor

logger = logging.getLogger(__name__)


class ConnectionState(enum.Enum):
    """Connection health state (no channel rebuild needed on transitions).

    ``UNKNOWN`` is the initial state before the first health-check ping —
    it means "no evidence yet", never "assumed reachable".
    """

    UNKNOWN = "unknown"
    CONNECTED = "connected"
    DISCONNECTED = "disconnected"


class KachakaConnection:
    """Thread-safe, pooled connection to a single Kachaka robot.

    Usage::

        conn = KachakaConnection.get("192.168.1.100:26400")
        result = conn.ping()   # {"ok": True, "serial": "...", "pose": {...}}
        sdk = conn.client      # raw KachakaApiClient for direct access
    """

    _pool: dict[str, KachakaConnection] = {}
    _pool_lock = threading.Lock()

    def __init__(
        self,
        target: str,
        timeout: float = 5.0,
        long_poll_timeout: float = 300.0,
    ):
        self.target = self._normalise_target(target)
        self.timeout = timeout
        self.long_poll_timeout = long_poll_timeout
        self._client: Optional[KachakaApiClient] = None
        self._client_lock = threading.Lock()
        self._resolver_ready = False
        self._shelves: dict[str, str] = {}
        self._shelf_ids: set[str] = set()
        self._locations: dict[str, str] = {}
        self._location_ids: set[str] = set()

        # ── Device info cache (Tier 1 — permanent) ──
        self._cache_lock = threading.Lock()
        self._cached_serial: Optional[str] = None
        self._cached_version: Optional[str] = None
        self._cached_error_defs: Optional[dict[int, dict]] = None

        # ── Device info cache (Tier 2 — semi-static, manual invalidation) ──
        self._cached_shortcuts: Optional[list[dict]] = None
        self._cached_map_list: Optional[list[dict]] = None
        self._cached_current_map_id: Optional[str] = None
        self._cached_map_image: Optional[dict] = None

        # ── Monitoring state ──
        self._state = ConnectionState.UNKNOWN
        self._state_lock = threading.Lock()
        self._state_condition = threading.Condition(self._state_lock)
        self._on_state_change: Optional[Callable[[ConnectionState], None]] = None
        self._monitor_thread: Optional[threading.Thread] = None
        self._monitor_stop = threading.Event()
        self._monitor_interval: float = 0.0
        self._state_changed_at: Optional[float] = None
        self._last_ping_ok_at: Optional[float] = None

    # ── Pool management ──────────────────────────────────────────────

    @classmethod
    def get(
        cls,
        target: str,
        timeout: float = 5.0,
        *,
        monitor: bool = True,
        monitor_interval: float = 5.0,
        long_poll_timeout: float = 300.0,
    ) -> KachakaConnection:
        """Get or create a pooled connection for *target*.

        Background health monitoring starts automatically (``monitor=True``)
        so :attr:`state` reflects real connectivity without further setup::

            conn = KachakaConnection.get("192.168.1.100")
            print(conn.state)   # UNKNOWN until first ping, then
                                # CONNECTED / DISCONNECTED in real time

        Pass ``monitor=False`` to opt out (state then stays ``UNKNOWN``).
        If you need a callback on state transitions, call
        :meth:`start_monitoring` with ``on_state_change`` — it can be
        wired even while monitoring is already running.
        """
        key = cls._normalise_target(target)
        with cls._pool_lock:
            if key not in cls._pool:
                cls._pool[key] = cls(key, timeout, long_poll_timeout)
            conn = cls._pool[key]
        conn._ensure_connected()
        if monitor:
            conn.start_monitoring(interval=monitor_interval)
        return conn

    @classmethod
    def remove(cls, target: str) -> None:
        """Remove a connection from the pool (e.g. on permanent failure)."""
        key = cls._normalise_target(target)
        with cls._pool_lock:
            cls._pool.pop(key, None)

    @classmethod
    def clear_pool(cls) -> None:
        """Drop every pooled connection (stopping their monitors). Useful in tests."""
        with cls._pool_lock:
            conns = list(cls._pool.values())
            cls._pool.clear()
        for conn in conns:
            conn.stop_monitoring()

    # ── Client access ────────────────────────────────────────────────

    @property
    def client(self) -> KachakaApiClient:
        """Return the underlying SDK client, connecting lazily."""
        self._ensure_connected()
        assert self._client is not None
        return self._client

    # ── Health check ─────────────────────────────────────────────────

    def ping(self) -> dict:
        """Verify connectivity by reading serial number and pose."""
        try:
            sdk = self.client
            serial = sdk.get_robot_serial_number()
            pose = sdk.get_robot_pose()
            self._last_ping_ok_at = time.time()
            return {
                "ok": True,
                "serial": serial,
                "pose": {"x": pose.x, "y": pose.y, "theta": pose.theta},
            }
        except grpc.RpcError as exc:
            code = exc.code()
            return {"ok": False, "error": f"{code.name}: {exc.details() or ''}"}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    # ── Resolver ─────────────────────────────────────────────────────

    def ensure_resolver(self) -> bool:
        """Fetch shelf/location lists and build our own name-to-ID maps.

        We intentionally do NOT call ``sdk.update_resolver()`` so that
        the SDK's internal resolver stays uninitialised.  All name→ID
        resolution is performed by :meth:`resolve_shelf` and
        :meth:`resolve_location` before handing raw IDs to the SDK.
        """
        if self._resolver_ready:
            return True
        try:
            sdk = self.client
            self._shelves = {s.name: s.id for s in sdk.get_shelves()}
            self._shelf_ids = set(self._shelves.values())
            self._locations = {loc.name: loc.id for loc in sdk.get_locations()}
            self._location_ids = set(self._locations.values())
            self._resolver_ready = True
            logger.info("Resolver ready for %s", self.target)
            return True
        except Exception as exc:
            logger.warning("Resolver init failed for %s: %s", self.target, exc)
            return False

    def resolve_shelf(self, name_or_id: str) -> str:
        """Resolve a shelf name or ID to its canonical ID."""
        if name_or_id in self._shelf_ids:
            return name_or_id
        shelf_id = self._shelves.get(name_or_id)
        if shelf_id:
            return shelf_id
        logger.warning("Shelf not found by name or ID: %s", name_or_id)
        return name_or_id

    def resolve_location(self, name_or_id: str) -> str:
        """Resolve a location name or ID to its canonical ID."""
        if name_or_id in self._location_ids:
            return name_or_id
        loc_id = self._locations.get(name_or_id)
        if loc_id:
            return loc_id
        logger.warning("Location not found by name or ID: %s", name_or_id)
        return name_or_id

    # ── Connection monitoring ────────────────────────────────────────

    @property
    def state(self) -> ConnectionState:
        """Current connection state (thread-safe read).

        This is the **recommended way** for external code to check whether
        the robot is reachable.  The value is updated in real-time by the
        monitoring thread (started automatically by :meth:`get`).  It reads
        ``UNKNOWN`` only before the first health-check ping completes (or
        when monitoring was explicitly opted out with ``monitor=False``).

        .. note::
            ``RobotController.state.connection_state`` is an *internal* copy
            used by the controller's own state-loop.  External consumers
            (APIs, UIs, health endpoints) should read
            ``KachakaConnection.state`` instead for the most up-to-date
            connectivity status.
        """
        with self._state_lock:
            return self._state

    @property
    def _is_monitoring(self) -> bool:
        return self._monitor_thread is not None and self._monitor_thread.is_alive()

    def start_monitoring(
        self,
        interval: float = 5.0,
        on_state_change: Optional[Callable[[ConnectionState], None]] = None,
    ) -> None:
        """Start (or retune) the background health-check loop.

        :meth:`get` calls this automatically, so most code never needs to.
        Call it explicitly to register a state-transition callback or to
        change the ping cadence.

        Args:
            interval: Seconds between pings (default 5).
            on_state_change: Called on state transitions.  ``None`` keeps
                any previously registered callback.

        .. note::
            Safe to call while already running: the callback is updated
            in place, and a different *interval* restarts the loop at the
            new cadence.  ``RobotController.start()`` relies on this to
            wire its callback after auto-monitoring has begun.
        """
        if on_state_change is not None:
            self._on_state_change = on_state_change
        if self._monitor_thread is not None and self._monitor_thread.is_alive():
            if interval == self._monitor_interval:
                return
            # Cadence change — restart the loop thread.
            self._monitor_stop.set()
            self._monitor_thread.join(timeout=10.0)
        self._monitor_interval = interval
        self._monitor_stop.clear()
        self._monitor_thread = threading.Thread(
            target=self._health_check_loop,
            args=(interval,),
            daemon=True,
            name=f"conn-monitor-{self.target}",
        )
        self._monitor_thread.start()
        logger.info("Started monitoring for %s (interval=%.1fs)", self.target, interval)

    def stop_monitoring(self) -> None:
        """Stop background health-check loop."""
        if self._monitor_thread is None:
            return
        self._monitor_stop.set()
        self._monitor_thread.join(timeout=10.0)
        self._monitor_thread = None
        self._on_state_change = None
        logger.info("Stopped monitoring for %s", self.target)

    def wait_for_state(
        self, target_state: ConnectionState, timeout: float | None = None
    ) -> bool:
        """Block until connection reaches *target_state*.

        Returns True if the state was reached, False on timeout.
        """
        with self._state_condition:
            return self._state_condition.wait_for(
                lambda: self._state == target_state,
                timeout=timeout,
            )

    def wait_until_known(self, timeout: float | None = None) -> bool:
        """Block until the state is no longer UNKNOWN (first ping done).

        Returns True if a known state was reached, False on timeout.
        """
        with self._state_condition:
            return self._state_condition.wait_for(
                lambda: self._state != ConnectionState.UNKNOWN,
                timeout=timeout,
            )

    def _set_state(self, new_state: ConnectionState) -> None:
        """Update state and notify waiters + callback."""
        with self._state_condition:
            if self._state == new_state:
                return
            old = self._state
            self._state = new_state
            self._state_changed_at = time.time()
            self._state_condition.notify_all()
        logger.info("Connection %s: %s → %s", self.target, old.value, new_state.value)
        if self._on_state_change is not None:
            try:
                self._on_state_change(new_state)
            except Exception:
                logger.exception("on_state_change callback error")

    def _health_check_loop(self, interval: float) -> None:
        """Daemon thread: ping immediately, then periodically; update state."""
        while True:
            result = self.ping()
            if result["ok"]:
                self._set_state(ConnectionState.CONNECTED)
            else:
                self._set_state(ConnectionState.DISCONNECTED)
            if self._monitor_stop.wait(timeout=interval):
                return

    def connection_info(self) -> dict:
        """Snapshot of connectivity status for health endpoints / MCP tools.

        Ages are in seconds; ``None`` means "never happened yet".
        """
        now = time.time()
        with self._state_lock:
            state = self._state
            changed_at = self._state_changed_at
        last_ok = self._last_ping_ok_at
        return {
            "target": self.target,
            "state": state.value,
            "monitoring": self._is_monitoring,
            "monitor_interval": self._monitor_interval if self._is_monitoring else None,
            "state_changed_ago_s": round(now - changed_at, 1) if changed_at else None,
            "last_ok_ping_ago_s": round(now - last_ok, 1) if last_ok else None,
        }

    # ── Device info cache (Tier 1 — permanent) ──────────────────────

    @property
    def serial(self) -> str:
        """Robot serial number (lazy-fetched, permanently cached)."""
        if self._cached_serial is not None:
            return self._cached_serial
        with self._cache_lock:
            if self._cached_serial is not None:
                return self._cached_serial
            try:
                self._cached_serial = self.client.get_robot_serial_number()
            except Exception:
                logger.debug("Failed to fetch serial for %s", self.target)
                return ""
        return self._cached_serial

    @property
    def version(self) -> str:
        """Firmware version string (lazy-fetched, permanently cached)."""
        if self._cached_version is not None:
            return self._cached_version
        with self._cache_lock:
            if self._cached_version is not None:
                return self._cached_version
            try:
                self._cached_version = self.client.get_robot_version()
            except Exception:
                logger.debug("Failed to fetch version for %s", self.target)
                return ""
        return self._cached_version

    @property
    def error_definitions(self) -> dict[int, dict]:
        """All error code definitions {code: {"title": str, "description": str}}.

        Lazy-fetched from firmware, permanently cached for the session.
        """
        if self._cached_error_defs is not None:
            return self._cached_error_defs
        with self._cache_lock:
            if self._cached_error_defs is not None:
                return self._cached_error_defs
            try:
                raw = self.client.get_robot_error_code()
                self._cached_error_defs = {
                    code: {
                        "title": getattr(info, "title_en", "") or getattr(info, "title", "") or "",
                        "description": getattr(info, "description_en", "") or getattr(info, "description", "") or "",
                    }
                    for code, info in raw.items()
                }
            except Exception:
                logger.debug("Failed to fetch error definitions for %s", self.target)
                return {}
        return self._cached_error_defs

    # ── Device info cache (Tier 2 — semi-static) ────────────────────

    @property
    def shortcuts(self) -> list[dict]:
        """Registered shortcuts (lazy-fetched, cached until refresh_shortcuts)."""
        if self._cached_shortcuts is not None:
            return self._cached_shortcuts
        with self._cache_lock:
            if self._cached_shortcuts is not None:
                return self._cached_shortcuts
            try:
                raw = self.client.get_shortcuts()
                self._cached_shortcuts = [
                    {"id": sc.id, "name": sc.name} for sc in raw
                ]
            except Exception:
                logger.debug("Failed to fetch shortcuts for %s", self.target)
                return []
        return self._cached_shortcuts

    @property
    def map_list(self) -> list[dict]:
        """Available maps (lazy-fetched, cached until refresh_maps)."""
        if self._cached_map_list is not None:
            return self._cached_map_list
        with self._cache_lock:
            if self._cached_map_list is not None:
                return self._cached_map_list
            try:
                raw = self.client.get_map_list()
                self._cached_map_list = [
                    {"id": m.id, "name": m.name} for m in raw
                ]
            except Exception:
                logger.debug("Failed to fetch map list for %s", self.target)
                return []
        return self._cached_map_list

    @property
    def current_map_id(self) -> str:
        """Active map ID (lazy-fetched, cached until refresh_maps)."""
        if self._cached_current_map_id is not None:
            return self._cached_current_map_id
        with self._cache_lock:
            if self._cached_current_map_id is not None:
                return self._cached_current_map_id
            try:
                self._cached_current_map_id = self.client.get_current_map_id()
            except Exception:
                logger.debug("Failed to fetch current map ID for %s", self.target)
                return ""
        return self._cached_current_map_id

    @property
    def map_image(self) -> dict:
        """Current map PNG data + metadata (lazy-fetched, cached until refresh_maps).

        Returns dict with keys: png_bytes, name, resolution, width, height, origin_x, origin_y.
        """
        if self._cached_map_image is not None:
            return self._cached_map_image
        with self._cache_lock:
            if self._cached_map_image is not None:
                return self._cached_map_image
            try:
                png_map = self.client.get_png_map()
                self._cached_map_image = {
                    "png_bytes": png_map.data,
                    "name": png_map.name,
                    "resolution": png_map.resolution,
                    "width": png_map.width,
                    "height": png_map.height,
                    "origin_x": png_map.origin.x,
                    "origin_y": png_map.origin.y,
                }
            except Exception:
                logger.debug("Failed to fetch map image for %s", self.target)
                return {}
        return self._cached_map_image

    def refresh_shortcuts(self) -> None:
        """Clear shortcuts cache so next access re-fetches."""
        with self._cache_lock:
            self._cached_shortcuts = None

    def refresh_maps(self) -> None:
        """Clear map_list, current_map_id, and map_image caches."""
        with self._cache_lock:
            self._cached_map_list = None
            self._cached_current_map_id = None
            self._cached_map_image = None

    # ── Internal ─────────────────────────────────────────────────────

    def _ensure_connected(self) -> None:
        if self._client is not None:
            return
        with self._client_lock:
            if self._client is not None:
                return
            logger.info("Connecting to Kachaka at %s …", self.target)
            self._client = KachakaApiClient(self.target)

            # Replace the SDK's plain channel with one that has a timeout
            # interceptor.  The SDK never sets per-call timeouts, so without
            # this, any gRPC call can block indefinitely on server-side
            # disconnects (e.g. robot WiFi drop — measured 522s in testing).
            #
            # HTTP/2 keepalive makes in-flight RPCs (including bounded
            # long-polls) fail fast with UNAVAILABLE on a silent transport
            # death instead of waiting out their deadline.
            # max_pings_without_data=0 keeps pings flowing during a silent
            # long-poll; tolerance verified against real robot firmware in
            # tests/hil/test_disconnect.py.
            channel_options = [
                ("grpc.keepalive_time_ms", 15000),
                ("grpc.keepalive_timeout_ms", 5000),
                ("grpc.http2.max_pings_without_data", 0),
                # Probe even when no call is in flight — short-deadline
                # health pings leave the channel idle between attempts, and
                # without this a zombie transport (silent drop) is never
                # declared dead, so the channel never re-dials.
                ("grpc.keepalive_permit_without_calls", 1),
            ]
            intercepted_channel = grpc.intercept_channel(
                grpc.insecure_channel(self.target, options=channel_options),
                TimeoutInterceptor(self.timeout, long_poll_timeout=self.long_poll_timeout),
            )
            self._client.stub = KachakaApiStub(intercepted_channel)

            # Lightweight connectivity check (same as visual-patrol)
            try:
                self._client.get_robot_serial_number()
                logger.info("Connected to %s", self.target)
            except Exception as exc:
                logger.warning(
                    "Connection created but ping failed for %s: %s",
                    self.target,
                    exc,
                )

    @staticmethod
    def _normalise_target(target: str) -> str:
        """Ensure target includes gRPC port."""
        if ":" not in target:
            return f"{target}:26400"
        return target
