#!/usr/bin/env python3
"""
Offline multi-stop route executor for Kachaka Playground container.

Runs inside the robot's Playground Docker container (port 26500 SSH / 26501 Jupyter).
Controls the robot via container-internal gRPC (100.94.1.1:26400) — no external
network required. Uses IMU shake detection for operator confirmation at each stop.

Deployment:
    scp -P 26500 playground_offline_route.py kachaka@<robot-ip>:/home/kachaka/
    ssh -p 26500 kachaka@<robot-ip> "nohup python3 -u /home/kachaka/playground_offline_route.py > /tmp/route.log 2>&1 &"

Prerequisites:
    - SSH key added to robot's ~/.ssh/authorized_keys (key auth only, no password)
    - kachaka_api pre-installed in Playground container

Verified: 2026-04-08, robot BKP40HD1T, 2-stop shelf delivery route.
"""
import json
import threading
import time
import urllib.request

import kachaka_api

# ── Configuration ──────────────────────────────────────────────────────────────
GRPC_ADDRESS = "100.94.1.1:26400"  # Container-internal, always reachable
STOPS = [{"name": "Snaken", "timeout_sec": 30}, {"name": "倉庫", "timeout_sec": 30}]
SHELF_NAME = "s1"
DEFAULT_TIMEOUT = 120  # seconds, used when stop has no timeout_sec

# Optional: HTTP endpoint for progress reporting (e.g. Pi server)
REPORT_URL = ""  # e.g. "http://192.168.50.5:8000/api/routes/offline/report"
RUN_ID = ""      # unique run identifier for report correlation

# ── IMU Shake Detection ───────────────────────────────────────────────────────
# Verified thresholds from live testing:
#   - accel=13.17 during dock/undock (must disarm during movement)
#   - accel=10.31 + gyro=0.945 at second stop (dual-indicator caught it)
ACCEL_THRESHOLD = 11.0  # m/s² (acceleration vector magnitude)
GYRO_THRESHOLD = 0.8    # rad/s (angular velocity vector magnitude)
IMU_POLL_INTERVAL = 0.1  # seconds

_imu_armed = False
_imu_lock = threading.Lock()
_shake_event = threading.Event()
_imu_samples = []  # ring buffer, max 3
_imu_thread_stop = threading.Event()


def _imu_worker(client):
    """Background thread: poll IMU and trigger shake_event when armed."""
    while not _imu_thread_stop.is_set():
        try:
            imu = client.get_ros_imu()
            accel_mag = (imu.linear_acceleration.x ** 2
                         + imu.linear_acceleration.y ** 2
                         + imu.linear_acceleration.z ** 2) ** 0.5
            gyro_mag = (imu.angular_velocity.x ** 2
                        + imu.angular_velocity.y ** 2
                        + imu.angular_velocity.z ** 2) ** 0.5

            # Either indicator exceeding threshold counts as a hit
            exceeded = (accel_mag > ACCEL_THRESHOLD) or (gyro_mag > GYRO_THRESHOLD)

            with _imu_lock:
                _imu_samples.append(exceeded)
                if len(_imu_samples) > 3:
                    _imu_samples.pop(0)
                armed = _imu_armed
                # Trigger: 2 out of 3 recent samples exceeded
                triggered = armed and len(_imu_samples) >= 3 and sum(_imu_samples[-3:]) >= 2

            if triggered:
                _shake_event.set()
        except Exception:
            pass
        time.sleep(IMU_POLL_INTERVAL)


def arm_imu(settle_delay=2.0):
    """Enable shake detection after waiting for robot to settle."""
    global _imu_armed
    _shake_event.clear()
    with _imu_lock:
        _imu_samples.clear()
    # Wait for robot to stop moving — dock/undock impacts can reach 13+ m/s²
    time.sleep(settle_delay)
    with _imu_lock:
        _imu_armed = True


def disarm_imu():
    """Disable shake detection (call before moving)."""
    global _imu_armed
    with _imu_lock:
        _imu_armed = False
    _shake_event.clear()


def wait_for_shake_or_timeout(timeout_sec):
    """Block until shake detected or timeout. Returns True if shaken."""
    return _shake_event.wait(timeout=timeout_sec)


# ── Optional: Progress Reporting ──────────────────────────────────────────────

def try_report(event, stop_index=None, retries=0):
    """Best-effort HTTP POST to external server. Silent on failure."""
    if not REPORT_URL:
        return False
    payload = json.dumps({
        "run_id": RUN_ID, "event": event, "stop_index": stop_index,
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }).encode()
    for attempt in range(1 + retries):
        try:
            req = urllib.request.Request(
                REPORT_URL, data=payload,
                headers={"Content-Type": "application/json"}, method="POST",
            )
            with urllib.request.urlopen(req, timeout=5):
                return True
        except Exception:
            if attempt < retries:
                time.sleep(10)
    return False


# ── Main Route Executor ───────────────────────────────────────────────────────

def main():
    print(f"[route] Shelf: {SHELF_NAME}, Stops: {len(STOPS)}", flush=True)

    client = kachaka_api.KachakaApiClient(GRPC_ADDRESS)

    # Required: initialize name->ID resolver before name-based commands
    # Without this, move_shelf("s1", "倉庫") sends names as IDs -> error 10250
    client.update_resolver()
    print("[route] Resolver ready", flush=True)

    # Start IMU monitor thread
    imu_thread = threading.Thread(target=_imu_worker, args=(client,), daemon=True)
    imu_thread.start()

    try:
        for i, stop in enumerate(STOPS):
            name = stop["name"]
            timeout = stop.get("timeout_sec") or DEFAULT_TIMEOUT
            print(f"[route] Stop {i+1}/{len(STOPS)}: {name}", flush=True)

            # Move shelf to stop — blocking, returns when movement completes
            try_report("moving", i)
            result = client.move_shelf(SHELF_NAME, name)
            print(f"[route] move_shelf -> {result}", flush=True)
            try_report("arrived", i)

            # Announce and wait for operator
            client.speak("到站，請取貨")

            # Arm IMU (2s settle), wait for shake or timeout
            arm_imu(settle_delay=2.0)
            shook = wait_for_shake_or_timeout(timeout)
            disarm_imu()

            if shook:
                print(f"[route] Shake confirmed at {name}", flush=True)
                try_report("shake_confirmed", i)
            else:
                print(f"[route] Timeout at {name}", flush=True)
                client.speak("超時，即將前往下一站")
                try_report("timeout", i)
                time.sleep(2.0)

        # Return shelf and go home — both blocking
        print("[route] Returning shelf...", flush=True)
        client.return_shelf(SHELF_NAME)
        print("[route] Going home...", flush=True)
        client.return_home()
        print("[route] Complete", flush=True)
        try_report("completed", retries=60)  # 10 min retry for network recovery

    except Exception as exc:
        print(f"[route] ERROR: {exc}", flush=True)
        try_report("failed", retries=60)
        # Best-effort: return shelf even on error
        try:
            client.return_shelf(SHELF_NAME)
        except Exception:
            pass
        try:
            client.return_home()
        except Exception:
            pass
    finally:
        _imu_thread_stop.set()
        imu_thread.join(timeout=2.0)


if __name__ == "__main__":
    main()
