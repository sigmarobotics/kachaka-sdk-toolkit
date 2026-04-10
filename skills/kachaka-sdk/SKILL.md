---
name: kachaka-sdk
description: Use when tasks involve Kachaka robot control, status queries, connection management, or patrol scripting
---

# Kachaka Robot SDK Skill

## Critical Rules (READ FIRST)

**STOP. Before writing ANY Kachaka code, internalize these 5 rules.**

1. **INSTALL**: `kachaka-sdk-toolkit` is a **PyPI package**. Install with `pip install kachaka-sdk-toolkit`. NEVER use `git+https://` URLs. NEVER copy `kachaka_core/` into your project.
2. **CONNECT**: `KachakaConnection.get(ip)` is the ONLY way to get a connection. NEVER instantiate `KachakaApiClient` directly — you lose pooling, retry, resolver, timeout, and monitoring.
3. **RETRY**: `@with_retry` is already applied to ALL KachakaCommands and KachakaQueries methods. NEVER write `try/except` + `time.sleep` retry loops — the SDK handles this.
4. **CAMERA**: `CameraStreamer` runs in a background daemon thread. NEVER call `get_front_camera_image()` in a loop — it blocks your main thread and drops frames.
5. **PATROL**: Use `RobotController` for multi-step sequences (background polling + metrics + command_id verification). `KachakaCommands` is for simple one-shot operations ONLY.

## When to Use

When a task involves **Kachaka robot** control, status queries, connection management, or patrol scripting — read this skill.

## Core Principle

**All Kachaka operations MUST go through `kachaka_core`.**
This layer is shared with the MCP Server, ensuring conversation-tested behaviour and production code are always consistent.

## Installation

`kachaka-sdk-toolkit` is published on **PyPI**. Install as a standard Python package:

```bash
pip install kachaka-sdk-toolkit          # PyPI (production)
pip install -e /path/to/local/checkout   # Editable (development)
```

In `requirements.txt` or `pyproject.toml`:
```
kachaka-sdk-toolkit>=0.3.0
```

> :x: **NEVER**: `pip install git+https://github.com/...` — the package is on PyPI
> :x: **NEVER**: Copy `kachaka_core/` directory into your project — causes version drift

## Quick Start

```python
from kachaka_core.connection import KachakaConnection, ConnectionState
from kachaka_core.commands import KachakaCommands
from kachaka_core.queries import KachakaQueries

# 1. Connect (port 26400 appended automatically)
conn = KachakaConnection.get("192.168.1.100")

# 2. Start background monitoring (enables conn.state + lazy cache population)
conn.start_monitoring(interval=5.0)

# 3. Initialise name→ID resolver (required before name-based commands)
conn.ensure_resolver()

# Now available:
# conn.state    → ConnectionState.CONNECTED / DISCONNECTED (real-time)
# conn.serial   → "KCK-XXXX" (lazy-fetched, permanent cache)
# conn.version  → "3.15.4" (lazy-fetched, permanent cache)

cmds = KachakaCommands(conn)
queries = KachakaQueries(conn)
```

## Connection Management

```python
from kachaka_core.connection import KachakaConnection

# Get or create a pooled connection
conn = KachakaConnection.get("192.168.1.100")

# Health check
result = conn.ping()
# {"ok": True, "serial": "KCK-XXXX", "pose": {"x": 1.2, "y": 0.5, "theta": 0.0}}

# Initialise resolver (required before name-based commands)
conn.ensure_resolver()

# Remove from pool (e.g. after IP change)
KachakaConnection.remove("192.168.1.100")
```

### Connection pool is automatic

- First call to `KachakaConnection.get(ip)` creates a new client
- Subsequent calls return the cached instance
- Thread-safe via internal locking
- Resolver supports both name and ID lookups (bio-patrol pattern)
- **TimeoutInterceptor** (5s default) is installed on every connection — all unary gRPC calls get a 5s deadline to prevent indefinite blocking during network loss
- Customise timeout: `KachakaConnection.get("192.168.1.100", timeout=10.0)`

> :x: **NEVER** instantiate `KachakaApiClient(ip)` directly — you lose connection pooling, retry, resolver, timeout interceptor, and health monitoring. Every direct client leaks a gRPC channel.

## Connection Monitoring

`start_monitoring()` runs a background daemon thread that pings the robot at a fixed interval and updates `conn.state` in real-time. **You must call this** if you want `conn.state` to reflect actual connectivity — without it, `state` always reads `CONNECTED`.

```python
from kachaka_core.connection import KachakaConnection, ConnectionState

conn = KachakaConnection.get("192.168.1.100")

# Start background health-check loop
conn.start_monitoring(interval=5.0)

# Real-time state (thread-safe read)
if conn.state == ConnectionState.CONNECTED:
    print("Robot online")
else:
    print("Robot offline")
```

### With state change callback

```python
def on_change(new_state: ConnectionState):
    if new_state == ConnectionState.DISCONNECTED:
        print("⚠ Robot disconnected!")
    else:
        print("✓ Robot reconnected")

conn.start_monitoring(interval=5.0, on_state_change=on_change)
```

### Blocking wait for connection

```python
# Wait up to 10s for robot to come online
conn.start_monitoring()
if conn.wait_for_state(ConnectionState.CONNECTED, timeout=10.0):
    print("Robot ready")
else:
    print("Timeout — robot not reachable")
```

### Lifecycle notes

- **Idempotent** — calling `start_monitoring()` again while running is a no-op
- **`RobotController.start()` calls this internally** — but only when controller starts. If you need `conn.state` before the first patrol (e.g., on app startup for health check API), call `start_monitoring()` explicitly at startup
- `stop_monitoring()` stops the background thread and clears the callback
- The background thread is a daemon — auto-exits when the process ends

### Recommended startup pattern for FastAPI apps

```python
@asynccontextmanager
async def lifespan(app: FastAPI):
    conn = KachakaConnection.get(ROBOT_IP)
    conn.start_monitoring(interval=5.0, on_state_change=handle_state_change)
    conn.ensure_resolver()
    yield
    conn.stop_monitoring()
```

## Cached Device Info

`KachakaConnection` lazily caches static and semi-static data to
eliminate repeated gRPC calls:

### Tier 1 — Permanent (session lifetime)

```python
conn = KachakaConnection.get("192.168.1.100")

conn.serial            # "BKP40EB1T" — fetched once
conn.version           # "3.15.4" — fetched once
conn.error_definitions # {10253: {"title": "No destinations", "description": "..."}, ...}
```

### Tier 2 — Semi-static (manual invalidation)

```python
conn.shortcuts         # [{"id": "sc-1", "name": "Patrol A"}, ...]
conn.map_list          # [{"id": "map-1", "name": "Floor1"}, ...]
conn.current_map_id    # "map-1"
conn.map_image         # {"png_bytes": b"...", "width": 200, "height": 200, ...}

conn.refresh_shortcuts()  # clear shortcuts cache
conn.refresh_maps()       # clear map_list + current_map_id + map_image
```

`switch_map()` automatically calls `refresh_maps()` on success.

## Movement Commands

```python
cmds = KachakaCommands(conn)

# Move to a named location (resolver auto-initialised)
result = cmds.move_to_location("Kitchen")
# {"ok": True, "action": "move_to_location", "target": "Kitchen"}

# Move to coordinates
result = cmds.move_to_pose(x=1.5, y=2.0, yaw=0.0)

# Relative movement
cmds.move_forward(0.5)         # Forward 0.5m
cmds.move_forward(-0.3)       # Backward 0.3m
cmds.rotate_in_place(1.57)    # 90° counter-clockwise

# Return to charger
cmds.return_home()

# Poll until command finishes
result = cmds.poll_until_complete(timeout=60.0)
# {"ok": True, "error_code": 0, "command": "...", "elapsed": 12.3}
```

> :x: **NEVER** call `sdk.move_to_location()` raw — use `cmds.move_to_location()` which auto-initialises the resolver. Raw SDK calls require manual name->ID resolution.
> :x: **NEVER** write a `while` loop polling `get_command_state()` — use `cmds.poll_until_complete()` which handles timeout, command_id verification, and error enrichment.

## Shelf Operations

```python
# Pick up shelf and deliver to location
cmds.move_shelf("Shelf A", "Meeting Room")

# Return shelf to its home
cmds.return_shelf("Shelf A")     # Named
cmds.return_shelf()               # Currently held

# Dock / undock
cmds.dock_shelf()
cmds.undock_shelf()
```

## Speech

```python
cmds.speak("Patrol complete")
cmds.set_speaker_volume(5)    # 0–10
```

## Status Queries

```python
queries = KachakaQueries(conn)

# Full status snapshot
status = queries.get_status()
# {"ok": True, "pose": {...}, "battery": {"percentage": 85, ...}, ...}

# Individual queries
queries.get_pose()          # {"ok": True, "x": ..., "y": ..., "theta": ...}
queries.get_battery()       # {"ok": True, "percentage": 85, "power_status": "..."}
queries.list_locations()    # {"ok": True, "locations": [{name, id, type, pose}, ...]}
queries.list_shelves()      # {"ok": True, "shelves": [{name, id, home_location_id}, ...]}
queries.get_moving_shelf()  # {"ok": True, "shelf_id": "..." or null}
queries.get_command_state() # {"ok": True, "state": "...", "is_running": false}
queries.get_errors()        # {"ok": True, "errors": []}
```

## Camera

```python
# Returns base64-encoded JPEG
img = queries.get_front_camera_image()
# {"ok": True, "image_base64": "...", "format": "jpeg"}

img = queries.get_back_camera_image()
```

### Decoding the image

```python
import base64
from PIL import Image
import io

data = base64.b64decode(img["image_base64"])
image = Image.open(io.BytesIO(data))
image.save("snapshot.jpg")
```

### MCP tools return native images

The MCP camera tools (`capture_front_camera`, `capture_back_camera`,
`get_camera_frame`, `capture_with_detection`, `get_map`) return images using
MCP's native `ImageContent` type. Claude can see the images directly inline
— no `save_path` or base64 decoding needed.

```python
# MCP tool call — Claude sees the image directly
capture_front_camera(ip="192.168.1.100")
# → ImageContent(type="image", data="<base64>", mimeType="image/jpeg")

# Tools with metadata return [Image, TextContent]:
capture_with_detection(ip="192.168.1.100")
# → [ImageContent(...), TextContent(text='{"objects": [...], "annotated": true}')]

get_map(ip="192.168.1.100")
# → [ImageContent(...), TextContent(text='{"format": "png", "name": "...", ...}')]
```

> :warning: Single-shot `get_front_camera_image()` is fine for one-time captures. For continuous monitoring, you MUST use `CameraStreamer` (next section). Calling single-shot in a loop blocks the thread and causes 30-40% higher RTT.

## Camera Availability

Not all cameras are available at all times. These constraints come from
the robot firmware:

| Camera | Image Capture | Intrinsics | Constraint |
|--------|--------------|------------|------------|
| Front  | Always       | After stream started | `start_camera_stream("front")` activates it |
| Back   | Always       | After stream started | `start_camera_stream("back")` activates it |
| ToF    | Off-charger only | Firmware-dependent | Move robot off charger first; some FW returns CANCELLED for intrinsics even off-charger |

### Camera Intrinsics

```python
queries = KachakaQueries(conn)

# Must start camera stream first for front/back
result = queries.get_camera_intrinsics("front")
# {"ok": True, "camera": "front", "width": 1280, "height": 720,
#  "fx": 509.8, "fy": 504.4, "cx": 627.7, "cy": 348.6,
#  "distortion_model": "plumb_bob", "D": [...], "K": [...], ...}

result = queries.get_camera_intrinsics("tof")  # robot must be off charger
```

### ToF Depth Image

```python
result = queries.get_tof_image()
# {"ok": True, "width": 160, "height": 120, "encoding": "16UC1",
#  "image_base64": "...", ...}

# Decode:
import numpy as np, base64
depth = np.frombuffer(base64.b64decode(result["image_base64"]),
                      dtype=np.uint16).reshape(120, 160)
```

## RobotController (Background Polling + Non-blocking Commands)

For long-running movement commands with metrics collection, use `RobotController` instead of `KachakaCommands`. It runs a background thread for continuous state polling and executes commands non-blockingly with `command_id` verification.

**When to use RobotController vs KachakaCommands:**
- `KachakaCommands`: Simple one-shot commands, blocking calls, `@with_retry` for gRPC errors
- `RobotController`: Multi-step patrols, metrics collection (RTT, poll counts), background state monitoring, command_id verification

> :x: **NEVER** use `KachakaCommands` for patrol sequences — you lose background state polling, metrics collection, command_id verification, and shelf drop detection.
> :x: **NEVER** write your own background polling thread — `RobotController` already provides `state` property with thread-safe snapshots updated every `fast_interval`.

```python
from kachaka_core import KachakaConnection, RobotController

conn = KachakaConnection.get("192.168.1.100")
ctrl = RobotController(conn)
ctrl.start()  # starts background state polling thread

# Thread-safe state snapshot (updated every fast_interval)
state = ctrl.state
print(state.battery_pct, state.pose_x, state.pose_y, state.is_command_running)

# Non-blocking command execution with polling + command_id verification
result = ctrl.move_to_location("Kitchen", timeout=120)
# {"ok": True, "action": "move_to_location", "target": "Kitchen", "elapsed": 45.2}

result = ctrl.return_home(timeout=60)
result = ctrl.move_shelf("Shelf A", "Meeting Room", timeout=120)
result = ctrl.return_shelf("Shelf A", timeout=60)

# Metrics collected during command execution
m = ctrl.metrics
print(f"polls={m.poll_count}, avg_rtt={sum(m.poll_rtt_list)/len(m.poll_rtt_list):.1f}ms")
ctrl.reset_metrics()

ctrl.stop()
```

### Constructor parameters

```python
ctrl = RobotController(
    conn,
    fast_interval=1.0,   # pose + command_state poll interval (seconds)
    slow_interval=30.0,   # battery poll interval (seconds)
    retry_delay=1.0,      # delay between retries on StartCommand failure
    poll_interval=1.0,    # delay between GetCommandState polls during execution
)
```

### How command execution works

1. `StartCommand` with retry until deadline — captures `command_id`
2. Registration poll (5s max) — waits for `GetCommandState` to report our `command_id`
3. Main poll loop — polls `GetCommandState` every `poll_interval`
4. Completion detected when: state leaves RUNNING/PENDING **or** `command_id` changes
5. `GetLastCommandResult` with `command_id` verification — confirms result is for our command

### Error description enrichment

Error results now include human-readable descriptions fetched from the robot:

```python
result = ctrl.move_to_location("nonexistent")
# {"ok": false, "error_code": 10253, "error": "error_code=10253: No destinations registered", ...}

# When a command is cancelled by another:
# {"ok": false, "error_code": 10001, "error": "error_code=10001: {action_name} has been interrupted", ...}
```

- `_resolve_error_description()` calls `sdk.get_robot_error_code()` on each error (no cache — avoids firmware mismatch)
- Falls back gracefully to `error_code=NNNNN` if the fetch fails or code is unknown
- Same enrichment in both `controller.py` and `commands.py`

### Racing condition behavior (tested on real robot)

- `_execute_command` is **not thread-safe** — serialise command calls from the caller side
- **Command B cancels A**: A receives `error_code=10001` (interrupted), B completes normally
- **Concurrent commands**: One wins, the other gets TIMEOUT (its command_id never appears in GetLastCommandResult)
- **Short timeout + new command**: Robot keeps moving after controller timeout; `cancel_all=True` (default) on the new command cancels the residual movement
- **No deadlock observed** — concurrent use is unsafe but not catastrophic; no execution lock needed

### Network resilience (disconnect → auto-recovery)

Five layers protect against network loss:

1. **TimeoutInterceptor (5s)** — every unary gRPC call gets a 5s deadline. Without this, calls block 15–18 minutes waiting for TCP timeout.
2. **`@with_retry`** — retries `DEADLINE_EXCEEDED` / `UNAVAILABLE` / `RESOURCE_EXHAUSTED` with exponential backoff. Count mode (N attempts) or deadline mode (retry until wall-clock limit).
3. **ConnectionState monitoring** — `conn.start_monitoring(interval=3.0)` runs a background ping; fires `on_state_change` callback on `CONNECTED ↔ DISCONNECTED` transitions. Detection latency ~7s.
4. **RobotController** — `_state_loop` skips polling while `DISCONNECTED` (avoids wasting 5s per call on the interceptor timeout). `_execute_command` calls `conn.wait_for_state(CONNECTED)` before sending commands; retries `StartCommand` until deadline.
5. **CameraStreamer** — `_run` loop skips capture while `DISCONNECTED`. Records `recovery_latency_ms` on first successful capture after reconnect.

Disconnect → recovery timeline:

```
T+0s    Network lost
T+0~5s  In-flight gRPC call hits 5s interceptor timeout → DEADLINE_EXCEEDED
T+~7s   ConnectionState detects DISCONNECTED (ping interval)
        → RobotController + CameraStreamer skip polling/capture
T+Ns    Network restored
T+N+0.2s ConnectionState detects CONNECTED
         → RobotController._reconnect_probe() refreshes pose/battery
         → CameraStreamer records recovery timestamp
T+N+1s  Next poll/capture iteration succeeds normally
```

**Important**: gRPC channel survives all disconnect types (client-side iptables, server-side WiFi drop) — no channel rebuild needed.

### Other notes

- `metrics` is not a snapshot — read after command execution, not concurrently
- `state` property returns a thread-safe `copy.copy()` snapshot
- Background thread is a daemon — auto-exits when the process ends
- Kachaka's `GetCommandState` returns `PENDING` + empty `command_id` after command completion (idle state), so completion is detected via `command_id` change, not state transition alone

## Camera Streaming (Best Practice)

For continuous monitoring, use `CameraStreamer` instead of calling `get_front_camera_image()` in a loop. This pattern was proven optimal in connection-test Round 1 (30-40% lower RTT, lowest camera drop rates).

```python
from kachaka_core.camera import CameraStreamer
from kachaka_core.connection import KachakaConnection

conn = KachakaConnection.get("192.168.1.100")
streamer = CameraStreamer(conn, interval=1.0, camera="front")
streamer.start()

# Main loop does status queries without camera blocking
while patrolling:
    status = queries.get_status()
    frame = streamer.latest_frame  # non-blocking, returns latest captured frame
    if frame:
        process(frame["image_base64"])
    time.sleep(1.0)

streamer.stop()
print(streamer.stats)  # {"total_frames": 120, "dropped": 3, "drop_rate_pct": 2.4}
```

> :x: **NEVER** write `while True: img = queries.get_front_camera_image()` — this blocks the calling thread. `CameraStreamer` runs in a daemon thread with zero main-thread blocking.

### With callback

```python
def on_new_frame(frame: dict):
    save_to_disk(frame["image_base64"])

streamer = CameraStreamer(conn, interval=0.5, on_frame=on_new_frame)
streamer.start()
```

### Back camera

```python
streamer = CameraStreamer(conn, camera="back")
```

### With detection overlay

```python
streamer = CameraStreamer(conn, interval=1.0, detect=True, annotate=True)
streamer.start()

# latest_frame now includes "objects" key + bbox drawn on image
frame = streamer.latest_frame
# {"ok": True, "image_base64": "...", "objects": [...], "timestamp": ...}

# Detection results separately
detections = streamer.latest_detections
# [{"label": "person", "label_id": 1, "roi": {...}, "score": 0.95, "distance": 2.3}, ...]
```

- `detect=True, annotate=False` — raw frame + detection results (no bbox)
- `detect=True, annotate=True` — annotated frame + detection results
- Default `detect=False, annotate=False` — unchanged behavior

### Raw Bytes Access

```python
streamer = CameraStreamer(conn, interval=1.0)
streamer.start()
...
raw_jpeg = streamer.latest_frame_bytes  # bytes | None — no base64 decode needed
```

## Object Detection

```python
from kachaka_core.detection import ObjectDetector

det = ObjectDetector(conn)

# Get current detections
result = det.get_detections()
# {"ok": True, "objects": [{"label": "person", "label_id": 1,
#   "roi": {"x": 100, "y": 50, "width": 200, "height": 300},
#   "score": 0.79, "distance": 2.3}, ...]}

# Capture image + detections together
result = det.capture_with_detections(camera="front")
# {"ok": True, "image_base64": "...", "format": "jpeg", "objects": [...]}

# Draw bounding boxes on raw JPEG bytes
import base64
raw = base64.b64decode(result["image_base64"])
annotated = det.annotate_frame(raw, result["objects"])
# Returns annotated JPEG bytes (not base64)
```

> :x: **NEVER** write your own PIL bbox drawing code — `ObjectDetector.annotate_frame()` handles label colors, font sizing, and distance overlay. Also available via `CameraStreamer(detect=True, annotate=True)`.

### Labels

| label_id | label | bbox color |
|----------|-------|------------|
| 0 | unknown | pink |
| 1 | person | green |
| 2 | shelf | blue |
| 3 | charger | cyan |
| 4 | door | red |

### Notes

- `distance` is `None` when `distance_median <= 0` (close range or sensor unavailable)
- `annotate_frame` uses PIL ImageDraw — does not depend on `kachaka_api.util.vision`
- Detection failure in CameraStreamer never blocks frame capture (log + skip)

## Map

```python
# Current map as base64 PNG with full metadata
map_data = queries.get_map()
# {"ok": True, "image_base64": "...", "format": "png", "name": "...",
#  "resolution": 0.05, "width": 800, "height": 600,
#  "origin_x": -10.0, "origin_y": -15.0}

# List all maps
queries.list_maps()
# {"ok": True, "maps": [{id, name}, ...], "current_map_id": "..."}
```

**Map metadata fields:**
- `resolution` — meters per pixel
- `width`, `height` — image dimensions in pixels
- `origin_x`, `origin_y` — world coordinates (meters) of the bottom-left pixel (ROS convention)

## Error Handling

### Built-in retry

All `@with_retry` methods automatically retry on transient gRPC errors (UNAVAILABLE, DEADLINE_EXCEEDED, RESOURCE_EXHAUSTED) with exponential backoff. Non-retryable errors fail immediately.

> :x: **NEVER** write custom retry logic (try/except + sleep + counter). ALL KachakaCommands and KachakaQueries methods already have `@with_retry` with exponential backoff for UNAVAILABLE, DEADLINE_EXCEEDED, RESOURCE_EXHAUSTED. Your manual retry wraps retry-inside-retry.

### Return format

Every method returns a dict:

```python
{"ok": True, ...}                              # Success (KachakaCommands)
{"ok": False, "error": "UNAVAILABLE: ...",     # gRPC failure (KachakaCommands)
 "retryable": True, "attempts": 3}
{"ok": False, "error_code": 10253,             # Robot error (both)
 "error": "error_code=10253: No destinations registered"}
{"ok": False, "error": "TIMEOUT", "timeout": 120}  # Timeout (RobotController)
```

### Custom retry for new functions

```python
from kachaka_core.error_handling import with_retry

@with_retry(max_attempts=5, base_delay=2.0, max_delay=15.0)
def my_custom_operation(sdk):
    ...
```

## Command Control

```python
# Cancel running command
cmds.cancel_command()

# Check state
queries.get_command_state()
queries.get_last_command_result()

# Resume waiting command
cmds.proceed()
```

## Manual Velocity Control

```python
cmds.set_manual_control(True)
cmds.set_velocity(linear=0.1, angular=0.0)    # Forward slowly
cmds.stop()                                      # Emergency stop
```

## Adding New Functionality

### Correct flow

1. Implement in `kachaka_core/commands.py` or `kachaka_core/queries.py`
2. Add corresponding tool in `mcp_server/server.py`
3. Update this SKILL.md
4. Add test in `tests/`

### Wrapping a new SDK method

```python
# In kachaka_core/commands.py
@with_retry()
def my_new_command(self, param: str) -> dict:
    result = self.sdk.some_sdk_method(param)
    return self._result_to_dict(result, action="my_new_command", target=param)

# In mcp_server/server.py
@mcp.tool()
def my_new_command(ip: str, param: str) -> dict:
    """Description for Claude to understand when to use this tool."""
    return KachakaCommands(KachakaConnection.get(ip)).my_new_command(param)
```

## SDK Feature Map — Use These, NEVER Reimplement

| When you need to... | Use this | NEVER do this |
|---------------------|----------|---------------|
| Connect to a robot | `KachakaConnection.get(ip)` | `KachakaApiClient(ip)` directly |
| Retry on gRPC failure | Already built-in (`@with_retry`) | `try/except` + `time.sleep` loop |
| Get robot serial/version | `conn.serial`, `conn.version` (cached) | Query + cache yourself |
| Resolve location name->ID | `conn.resolve_location(name)` | `list_locations()` + filter |
| Resolve shelf name->ID | `conn.resolve_shelf(name)` | `list_shelves()` + filter |
| Stream camera frames | `CameraStreamer(conn, interval=1.0)` | `while True: get_front_camera_image()` |
| Get latest frame (non-blocking) | `streamer.latest_frame` | Poll camera in main thread |
| Wait for command completion | `cmds.poll_until_complete()` | `while` loop on `get_command_state()` |
| Background robot state | `RobotController` + `ctrl.state` | Own polling thread + `get_status()` |
| Collect patrol metrics | `ctrl.metrics` (RTT, poll counts) | Manual timing with `time.time()` |
| Detect objects in frame | `ObjectDetector.get_detections()` | Raw SDK `get_object_detection()` |
| Draw detection bboxes | `ObjectDetector.annotate_frame(img, objects)` | PIL `ImageDraw` code |
| Stream + detect + annotate | `CameraStreamer(detect=True, annotate=True)` | Separate detector + drawer |
| Monitor connection health | `conn.start_monitoring(interval=5.0)` | Own ping loop |
| Handle disconnection | Built-in (5-layer resilience) | Custom reconnection logic |
| Track camera frame stats | `streamer.stats` (drop rate, recovery) | Manual frame counters |
| Shelf drop detection | `RobotController` (auto-tracks) | Poll `get_moving_shelf()` yourself |
| Error descriptions | Auto-enriched in all results | `get_error_definitions()` + manual lookup |
| gRPC timeout protection | `TimeoutInterceptor` (5s default) | Per-call `timeout=` parameter |
| Deploy script to robot | `playground_upload` + `playground_run` MCP tools | `scp` + `ssh` commands manually |
| Offline route execution | Playground snippets (scaffold + IMU + route) | Custom scripts from scratch |

## Anti-patterns Summary

See inline :x: markers throughout this document for detailed anti-patterns with context. Quick reference:

| Category | Don't | Do Instead |
|----------|-------|-----------|
| Connection | `KachakaApiClient(ip)` | `KachakaConnection.get(ip)` |
| Retry | Custom try/except/sleep | Built-in `@with_retry` |
| Camera | `get_front_camera_image()` in loop | `CameraStreamer` |
| Commands | Raw `sdk.move_to_location()` | `cmds.move_to_location()` |
| Polling | Manual `get_command_state()` loop | `poll_until_complete()` |
| Patrols | `KachakaCommands` for sequences | `RobotController` |
| Detection | Own PIL bbox drawing | `ObjectDetector.annotate_frame()` |
| IP | Hard-coded robot IP | Parameter or env var |
| Install | `git+https://` or copy source | `pip install kachaka-sdk-toolkit` (PyPI) |
| State check | Only check command state | Also check `command_id` change |
| Playground | `kachaka_core` inside container | `kachaka_api` direct (pre-installed) |
| Playground | Forget `update_resolver()` | Always call after client init |

## SDK Reference

The underlying `kachaka-api` SDK (v3.10+) provides:

- **Sync client**: `kachaka_api.KachakaApiClient(target)`
- **Async client**: `kachaka_api.aio.KachakaApiClient(target)`
- **71 methods** covering movement, shelf ops, camera, map, LIDAR, IMU, etc.
- **Resolver**: Auto-maps shelf/location names to IDs
- **Proto types**: `pb2.Result`, `pb2.Pose`, `pb2.Command`, etc.

`kachaka_core` wraps the sync client with connection pooling, retry logic, and structured responses. The async client is available for advanced use cases (streaming, callbacks) but is not wrapped by this toolkit.

## Playground Offline Execution

Run scripts directly on the robot's on-board Docker container (Playground).
Scripts use `kachaka_api` (the raw SDK) — NOT `kachaka_core` — because the
container has only the pre-installed SDK.

### Why Playground Exists — Offline-First Robot Control

Normal mode: your script runs on an external machine and sends gRPC commands
to the robot **over WiFi**. If WiFi drops, the robot stops receiving commands.

Playground mode: your script runs **inside the robot's Docker container**.
Commands travel through a container-internal virtual network (`100.94.1.1:26400`),
**never touching WiFi**. The robot can walk into a zero-connectivity zone and
keep executing the full route autonomously.

```
Normal mode:  [Your PC] ──WiFi──► [Robot gRPC]    ← WiFi断 = 機器人停止
Playground:   [Robot Container] ──internal──► [Robot gRPC]  ← 完全不需WiFi
```

### When to Use Playground (vs. kachaka_core)

| Situation | Use | Why |
|-----------|-----|-----|
| Robot stays in WiFi range | `kachaka_core` (normal) | Real-time control, richer API, easier debugging |
| Route passes through WiFi dead zones | **Playground** | Script survives network loss — runs on-board |
| Factory/warehouse with unreliable WiFi | **Playground** | Cannot guarantee connectivity during movement |
| Long-running autonomous task (>30 min) | **Playground** | Even brief WiFi drops can abort `kachaka_core` commands |
| Need operator confirmation without network | **Playground + IMU** | Physical shake replaces network-based confirmation |
| Need real-time dashboard or camera stream | `kachaka_core` (normal) | Playground cannot push data out without WiFi |

**Decision rule**: If the robot must travel to any location where WiFi may be
unavailable — even for a few seconds during movement — use Playground.

### How It Works

1. **Deploy phase (requires WiFi)**: Upload script to robot via SSH (:26500) or MCP `playground_upload`
2. **Execute phase (no WiFi needed)**: Script runs inside the container, all gRPC calls go through `100.94.1.1:26400` (container ↔ host internal bridge, never touches WiFi)
3. **Report phase (optional, best-effort)**: If WiFi exists, script can POST progress to external server; if not, silently skips

### Key Differences from kachaka_core Scripts

| | kachaka_core (normal) | Playground (offline) |
|---|---|---|
| Runs on | Your PC / server | Robot's Docker container |
| Network | WiFi to robot :26400 | Internal `100.94.1.1:26400` |
| SDK | `kachaka_core` (pooled, retry, monitoring) | `kachaka_api` (raw SDK, pre-installed) |
| Resolver | `KachakaConnection` owns it, auto-init | Must call `client.update_resolver()` manually |
| Libraries | Any pip package | stdlib only (no pip in container) |
| WiFi required | Yes, throughout execution | Only for deploy; execution is offline |
| Operator interaction | Network-based (API, WebSocket, etc.) | Physical: IMU shake detection |

### SSH Key Setup (Prerequisites)

Before using `playground_*` MCP tools, set up SSH key auth:

1. **Generate a key** (if you don't have one):
   ```bash
   ssh-keygen -t ed25519
   ```
2. **Upload public key** via JupyterLab:
   - Open `http://<robot-ip>:26501` in browser (password: `kachaka`)
   - Open a Terminal from the JupyterLab launcher
   - Run:
     ```bash
     mkdir -p ~/.ssh
     echo 'PASTE_YOUR_PUBLIC_KEY_HERE' >> ~/.ssh/authorized_keys
     ```
3. **Verify** from your machine:
   ```bash
   ssh -p 26500 kachaka@<robot-ip>
   ```

> The MCP tools auto-detect SSH keys (agent → `~/.ssh/id_ed25519` → `~/.ssh/id_rsa`). No need to specify the key path.

### Container Environment Constraints

When generating scripts for Playground, follow these rules:

| Rule | Detail |
|------|--------|
| Client | `kachaka_api.KachakaApiClient("100.94.1.1:26400")` |
| Resolver | Must call `client.update_resolver()` before name-based commands |
| Libraries | stdlib only: `json`, `threading`, `time`, `logging`, `signal`, etc. |
| Blocking | All move commands block by default (`wait_for_completion=True`) |
| Script path | `/home/kachaka/<filename>` |
| Log path | `/tmp/<filename>.log` |
| Firmware | Updates may wipe `/home/kachaka/` — scripts need re-upload |

> :x: **NEVER**: Use `kachaka_core` inside Playground scripts — it's not installed in the container
> :x: **NEVER**: Forget `client.update_resolver()` — names sent as raw IDs cause error_code 10250
> :x: **NEVER**: Start an HTTP server — only ports 26400/26500/26501 are exposed

### MCP Tool Workflow

```
1. Claude generates script content (using snippets below)
2. playground_upload(ip, script_content, filename)   → push to container
3. playground_run(ip, filename)                       → start in background
4. playground_log(ip)                                 → monitor output
5. playground_stop(ip, filename)                      → stop if needed
6. playground_status(ip, filename)                    → check if still running
```

### Composable Code Snippets

Combine these building blocks to generate complete scripts.

#### Snippet 1: Basic Scaffold

Every Playground script starts with this:

```python
#!/usr/bin/env python3
"""<description> — auto-generated for Kachaka Playground."""

import logging
import signal
import sys
import time

import kachaka_api

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[logging.StreamHandler()],
)
log = logging.getLogger(__name__)

# ── Graceful shutdown ──
_shutdown = False

def _handle_signal(sig, frame):
    global _shutdown
    log.info("Received signal %s, shutting down...", sig)
    _shutdown = True

signal.signal(signal.SIGTERM, _handle_signal)
signal.signal(signal.SIGINT, _handle_signal)

# ── Client init ──
client = kachaka_api.KachakaApiClient("100.94.1.1:26400")
client.update_resolver()
log.info("Connected and resolver initialized")
```

#### Snippet 2: IMU Shake Detection

Background thread with arm/disarm gating:

```python
import threading

ACCEL_THRESHOLD = 11.0   # m/s²
GYRO_THRESHOLD = 0.8     # rad/s
IMU_POLL_INTERVAL = 0.1  # seconds

shake_event = threading.Event()
_imu_armed = False
_imu_lock = threading.Lock()

def arm_imu():
    global _imu_armed
    with _imu_lock:
        shake_event.clear()
        _imu_armed = True
    log.info("IMU armed")

def disarm_imu():
    global _imu_armed
    with _imu_lock:
        _imu_armed = False
    log.info("IMU disarmed")

def _imu_monitor():
    """Background thread: poll IMU and fire shake_event."""
    recent = [False, False, False]
    idx = 0
    while not _shutdown:
        with _imu_lock:
            armed = _imu_armed
        if not armed:
            time.sleep(IMU_POLL_INTERVAL)
            continue
        try:
            imu = client.get_ros_imu()
            accel = (imu.linear_acceleration.x ** 2
                     + imu.linear_acceleration.y ** 2
                     + imu.linear_acceleration.z ** 2) ** 0.5
            gyro = (imu.angular_velocity.x ** 2
                    + imu.angular_velocity.y ** 2
                    + imu.angular_velocity.z ** 2) ** 0.5
            exceeded = (accel > ACCEL_THRESHOLD) or (gyro > GYRO_THRESHOLD)
            recent[idx % 3] = exceeded
            idx += 1
            if sum(recent) >= 2:
                log.info("Shake detected! accel=%.2f gyro=%.3f", accel, gyro)
                disarm_imu()
                shake_event.set()
        except Exception as e:
            log.warning("IMU read error: %s", e)
        time.sleep(IMU_POLL_INTERVAL)

imu_thread = threading.Thread(target=_imu_monitor, daemon=True)
imu_thread.start()
```

#### Snippet 3: Route Execution with Shelf

Sequential multi-stop delivery:

```python
SHELF = "s1"
STOPS = [
    {"name": "Station A", "timeout_sec": 30},
    {"name": "Station B", "timeout_sec": 30},
]

for stop in STOPS:
    if _shutdown:
        break
    log.info("Moving to %s with shelf %s", stop["name"], SHELF)
    client.move_shelf(SHELF, stop["name"])
    log.info("Arrived at %s", stop["name"])
    client.speak("到站，請取貨")

    # Wait for shake or timeout
    time.sleep(2)  # settle before arming
    arm_imu()
    shook = shake_event.wait(timeout=stop["timeout_sec"])
    disarm_imu()

    if shook:
        log.info("Shake confirmed at %s", stop["name"])
        client.speak("收到，前往下一站")
    else:
        log.info("Timeout at %s, moving on", stop["name"])
        client.speak("超時，即將前往下一站")

# Return home
log.info("Route complete, returning shelf and going home")
client.return_shelf(SHELF)
client.return_home()
log.info("Done")
```

#### Snippet 4: Route Without Shelf (move_to_location)

Same pattern but without shelf operations:

```python
STOPS = [
    {"name": "Station A", "timeout_sec": 30},
    {"name": "Station B", "timeout_sec": 30},
]

for stop in STOPS:
    if _shutdown:
        break
    log.info("Moving to %s", stop["name"])
    client.move_to_location(stop["name"])
    log.info("Arrived at %s", stop["name"])
    client.speak("到站")

    time.sleep(2)
    arm_imu()
    shook = shake_event.wait(timeout=stop["timeout_sec"])
    disarm_imu()

    if shook:
        log.info("Shake confirmed at %s", stop["name"])
    else:
        log.info("Timeout at %s", stop["name"])

client.return_home()
log.info("Done")
```

### Example Combinations

| Use Case | Snippets |
|----------|----------|
| Delivery patrol with shake confirm | 1 + 2 + 3 |
| Location patrol (no shelf) | 1 + 2 + 4 |
| Photo capture then batch return | 1 + 3 (replace shake wait with `client.get_front_camera_image()` + collect, then upload after route) |
| Stationary shake trigger | 1 + 2 (arm immediately, wait for event) |

### Complete Example: Offline Multi-Stop Route

See `examples/playground_offline_route.py` for a production-verified script that combines
all building blocks into a complete offline delivery workflow.

**What it demonstrates:**

- Multi-stop shelf delivery with configurable stop list
- IMU shake detection (2-of-3 sample voting, arm/disarm gating around movement)
- Optional HTTP progress reporting to an external server (e.g. Pi dashboard)
- Graceful error recovery (returns shelf + goes home on any failure)
- Background IMU thread with clean shutdown via `threading.Event`

**Key design decisions (verified on real robot BKP40HD1T):**

| Decision | Reason |
|----------|--------|
| `settle_delay=2.0` before arming IMU | Dock/undock impacts reach 13+ m/s² — must wait for robot to stop |
| Dual indicator: accel OR gyro | Accel=10.31 alone missed some shakes; gyro=0.945 caught them |
| 2-of-3 sample voting | Filters single-sample noise without adding latency |
| `try_report()` with `retries=60` on completion | 10-minute retry window for network recovery after offline route |
| `client.update_resolver()` at startup | Names sent as raw strings cause error_code 10250 without resolver |

**Deployment:**

```bash
# Upload and run via MCP tools (preferred)
playground_upload(ip, script_content, "offline_route.py")
playground_run(ip, "offline_route.py")
playground_log(ip)

# Or via SSH directly
scp -P 26500 playground_offline_route.py kachaka@<robot-ip>:/home/kachaka/
ssh -p 26500 kachaka@<robot-ip> "nohup python3 -u /home/kachaka/playground_offline_route.py > /tmp/route.log 2>&1 &"
```
