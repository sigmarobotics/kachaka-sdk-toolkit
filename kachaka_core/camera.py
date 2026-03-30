"""Background camera capture for Kachaka robots.

Runs a daemon thread that periodically grabs a JPEG frame from the
front or back camera, encodes it as base64, and stores it for
thread-safe retrieval.  Errors are logged but never crash the thread.

Pattern derived from sync_camera_separate in connection-test Round 1.
"""

from __future__ import annotations

import base64
import logging
import threading
import time
from typing import TYPE_CHECKING, Callable, Optional

from .connection import ConnectionState, KachakaConnection

if TYPE_CHECKING:
    from .detection import ObjectDetector

logger = logging.getLogger(__name__)

_VALID_CAMERAS = {"front", "back"}


class CameraStreamer:
    """Background thread camera capture — does not block main loop.

    Usage::

        conn = KachakaConnection.get("192.168.1.100")
        cam = CameraStreamer(conn, interval=1.0, camera="front")
        cam.start()
        ...
        frame = cam.latest_frame   # thread-safe read
        cam.stop()
    """

    def __init__(
        self,
        conn: KachakaConnection,
        interval: float = 1.0,
        camera: str = "front",
        on_frame: Optional[Callable[[dict], None]] = None,
        detect: bool = False,
        annotate: bool = False,
    ) -> None:
        if camera not in _VALID_CAMERAS:
            raise ValueError(
                f"Invalid camera {camera!r}; must be one of {_VALID_CAMERAS}"
            )

        self._conn = conn
        self._interval = interval
        self._camera = camera
        self._on_frame = on_frame

        # Thread machinery
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()

        # Frame storage (protected by lock)
        self._lock = threading.Lock()
        self._latest_frame: Optional[dict] = None

        # Counters (only written by the capture thread, reads are atomic on CPython)
        self._total_frames: int = 0
        self._dropped: int = 0

        # Recovery metrics
        self._last_success_time: float | None = None
        self._longest_gap_s: float = 0.0
        self._reconnected_at: float | None = None
        self._recovery_latency_ms: float | None = None

        # Detection overlay support
        # If annotate requested, force detect on
        if annotate and not detect:
            detect = True
        self._detect = detect
        self._annotate = annotate
        self._detector: Optional[ObjectDetector] = None
        if detect:
            from .detection import ObjectDetector

            self._detector = ObjectDetector(conn)

        # Detection results cache
        self._latest_detections: Optional[list] = None

    # ── Public API ───────────────────────────────────────────────────

    def start(self) -> None:
        """Start the background capture thread.  No-op if already running."""
        if self._thread is not None and self._thread.is_alive():
            return

        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        logger.info("CameraStreamer started (camera=%s, interval=%.2fs)", self._camera, self._interval)

    def stop(self) -> None:
        """Signal the thread to stop and wait for it to finish.  No-op if not running."""
        if self._thread is None or not self._thread.is_alive():
            return

        self._stop_event.set()
        self._thread.join(timeout=self._interval * 3)
        if self._thread.is_alive():
            logger.warning("CameraStreamer thread did not stop within timeout")
        else:
            logger.info("CameraStreamer stopped")

    @property
    def latest_frame(self) -> Optional[dict]:
        """Return the most recently captured frame (thread-safe)."""
        with self._lock:
            return self._latest_frame

    @property
    def latest_frame_bytes(self) -> bytes | None:
        """Most recent frame as raw JPEG bytes. None if no frame available."""
        with self._lock:
            frame = self._latest_frame
        if frame is None or not frame.get("ok"):
            return None
        return base64.b64decode(frame["image_base64"])

    @property
    def latest_detections(self) -> Optional[list]:
        """Most recent detection results (dict list). Requires detect=True."""
        with self._lock:
            return self._latest_detections

    @property
    def is_running(self) -> bool:
        """Whether the capture thread is alive."""
        return self._thread is not None and self._thread.is_alive()

    @property
    def stats(self) -> dict:
        """Capture statistics: total_frames, dropped, drop_rate_pct, recovery metrics."""
        total = self._total_frames
        dropped = self._dropped
        rate = (dropped / total * 100.0) if total > 0 else 0.0
        return {
            "total_frames": total,
            "dropped": dropped,
            "drop_rate_pct": rate,
            "longest_gap_s": round(self._longest_gap_s, 3),
            "recovery_latency_ms": (
                round(self._recovery_latency_ms, 1)
                if self._recovery_latency_ms is not None
                else None
            ),
        }

    def notify_state_change(self, state: ConnectionState) -> None:
        """Receive connection state changes from external monitoring.

        When the connection transitions to CONNECTED, record the timestamp
        so that the next successful capture can compute recovery latency.
        CameraStreamer does NOT start its own monitoring — the caller wires
        this method to :meth:`KachakaConnection.start_monitoring`.
        """
        if state == ConnectionState.CONNECTED:
            self._reconnected_at = time.perf_counter()

    # ── Internal ─────────────────────────────────────────────────────

    def _run(self) -> None:
        """Main loop executed in the daemon thread."""
        sdk = self._conn.client

        if self._camera == "front":
            capture_fn = sdk.get_front_camera_ros_compressed_image
        else:
            capture_fn = sdk.get_back_camera_ros_compressed_image

        while not self._stop_event.is_set():
            # Skip capture while disconnected — avoids wasting 5s per
            # call on the interceptor timeout.
            if self._conn.state == ConnectionState.DISCONNECTED:
                self._stop_event.wait(self._interval)
                continue

            self._total_frames += 1
            try:
                img = capture_fn()
                b64 = base64.b64encode(img.data).decode()

                # Detection (if enabled)
                det_objects = None
                if self._detect and self._detector is not None:
                    try:
                        det_result = self._detector.get_detections()
                        if det_result["ok"]:
                            det_objects = det_result["objects"]
                    except Exception:
                        logger.debug("Detection error in streamer", exc_info=True)

                # Annotate (if enabled and detections available)
                if self._annotate and det_objects:
                    try:
                        annotated_bytes = self._detector.annotate_frame(img.data, det_objects)
                        b64 = base64.b64encode(annotated_bytes).decode()
                    except Exception:
                        logger.debug("Annotation error in streamer", exc_info=True)

                frame: dict = {
                    "ok": True,
                    "image_base64": b64,
                    "format": img.format or "jpeg",
                    "timestamp": time.time(),
                }

                # Add detection results to frame if available
                if det_objects is not None:
                    frame["objects"] = det_objects

                with self._lock:
                    self._latest_frame = frame
                    if det_objects is not None:
                        self._latest_detections = det_objects

                # Fire callback (errors in callback must not kill the thread)
                if self._on_frame is not None:
                    try:
                        self._on_frame(frame)
                    except Exception:
                        logger.warning("on_frame callback raised an exception", exc_info=True)

                # Recovery metrics tracking
                now = time.perf_counter()
                if self._last_success_time is not None:
                    gap = now - self._last_success_time
                    self._longest_gap_s = max(self._longest_gap_s, gap)
                if (
                    self._reconnected_at is not None
                    and self._recovery_latency_ms is None
                ):
                    self._recovery_latency_ms = (now - self._reconnected_at) * 1000
                    self._reconnected_at = None
                self._last_success_time = now

            except Exception:
                self._dropped += 1
                logger.debug(
                    "CameraStreamer capture error (camera=%s, dropped=%d)",
                    self._camera,
                    self._dropped,
                    exc_info=True,
                )

            # Interruptible sleep — returns immediately if stop_event is set
            self._stop_event.wait(self._interval)
