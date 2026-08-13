---
name: kachaka-sdk
description: Use when tasks involve Kachaka robot control, status queries, connection management, or patrol scripting
---

# Kachaka Robot SDK Skill

## Critical Rules (READ FIRST)

**STOP. Before writing ANY Kachaka code, internalize these 6 rules.**

0. **NEVER AIM A MOVE AT A SHELF**: `move_to_location` / `move_to_pose` must not
   target a pose occupied by a shelf — **including that shelf's home location**.
   Near the goal the shelf's legs fall inside the LiDAR's filter zone, so the
   robot drives into it and keeps pushing with **no error code and no obstacle
   event** — `move_to_location` still ends `success: True`, while `move_to_pose`
   may never finish at all (only `cancel_command` ends it). Driving past shelves
   en route is fine. To reach a shelf, use `move_shelf` / `return_shelf` /
   `dock_any_shelf_with_registration`. See "Shelf Operations".

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
pip install kachaka-sdk-toolkit          # PyPI (production) — kachaka_core only
pip install 'kachaka-sdk-toolkit[mcp]'   # + MCP server (only if you run kachaka-mcp)
pip install -e /path/to/local/checkout   # Editable (development)
```

> The base package deliberately does **not** depend on the MCP stack (`mcp` -> `sse-starlette` -> `starlette`), so installing it into a FastAPI/starlette host project cannot break the host's pins. Only add `[mcp]` when the project itself runs the MCP server.

In `requirements.txt` or `pyproject.toml`:
```
kachaka-sdk-toolkit>=0.3.0
```

> :x: **NEVER**: `pip install git+https://github.com/...` — the package is on PyPI
> :x: **NEVER**: Copy `kachaka_core/` directory into your project — causes version drift

## Feature ↔ Version Matrix

Deployed robots are not all on the same firmware, so before using anything in
this table, check what you are talking to. **This table is authoritative** — if
an inline `(3.16.1+)` note elsewhere in the docs disagrees with it, believe this
one.

Two independent version numbers matter and they are *not* the same number:

- **`kachaka-api`** — the Python package you installed (`pip show kachaka-api`).
  Too old and the RPC/field does not exist in the generated stubs at all.
- **robot firmware** — what the robot runs (`queries.get_version()`, or
  `conn.version` which caches it for the session). Too old and the robot ignores
  the field or rejects the RPC.

They share `major.minor` but drift on the patch digit — a robot measured at
firmware `3.17.8` while the package was `3.17.5` is normal.

| Feature | toolkit API | kachaka-api ≥ | firmware ≥ | On older firmware |
|---|---|---|---|---|
| Custom sounds | `list_sounds` / `add_sound` / `play_sound` / `stop_sound` / `delete_sound` | 3.17 | 3.17 | RPC absent — call fails |
| Map-switch docking inheritance | `switch_map(...)` docking inherit method | 3.17 | 3.17 | Field ignored |
| Lightweight list queries | `list_locations_digest` / `list_shelves_digest` | 3.16.1 | 3.16.1 | RPC absent — use `list_locations` / `list_shelves` |
| Route start hint | `move_to_location(source_location_name=…)` | 3.16.1 | 3.16.1 | Field ignored — planner picks its own start |
| Safety-sensor muting | `move_forward(mute_sensors=True)`, `move_by_velocity_muted` | 3.16.1 | 3.16.1 | Field ignored — sensors stay active |
| `speed=0.0` rejected (error 15508) | `move_forward` | — | 3.16 | Older firmware accepts 0.0 and never moves |

The toolkit itself pins `kachaka-api >= 3.17` (the Sound API needs the
generated stubs), so the package column matters only if you are pinned to an
older toolkit release. The firmware column is the one to check per robot:
everything not listed above works on any firmware this toolkit supports.

**Kachaka Pro only** (standard-model firmware rejects these regardless of
version): `dock_any_shelf_with_registration` (proto: "available only in
Kachaka Pro"), `switch_map(inherit_docking_state_and_docked_shelf=True)`,
wall-marker precision parking, and self-localization markers.

```python
# Gate a version-sensitive call at runtime
conn = KachakaConnection.get(ip)                    # conn.version -> "3.17.8"
firmware = tuple(int(p) for p in conn.version.split("."))
if firmware >= (3, 17):        # NOT a string compare — "3.9" > "3.17" as strings
    cmds.play_sound(sound_id)
```

## Quick Start

```python
from kachaka_core.connection import KachakaConnection, ConnectionState
from kachaka_core.commands import KachakaCommands
from kachaka_core.queries import KachakaQueries

# 1. Connect (port 26400 appended automatically; health monitoring auto-starts)
conn = KachakaConnection.get("192.168.1.100")

# 2. Initialise name→ID resolver (required before name-based commands)
conn.ensure_resolver()

# Now available:
# conn.state    → ConnectionState.UNKNOWN / CONNECTED / DISCONNECTED (real-time)
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

Monitoring **starts automatically** in `KachakaConnection.get()` — a daemon thread pings the robot every 5 s (first ping immediately) and updates `conn.state` in real-time. The state reads `UNKNOWN` only before the first ping verdict (or with `monitor=False`).

```python
from kachaka_core.connection import KachakaConnection, ConnectionState

conn = KachakaConnection.get("192.168.1.100")   # monitoring already on

conn.wait_until_known(timeout=10.0)   # block until the first ping verdict
if conn.state == ConnectionState.CONNECTED:
    print("Robot online")
else:
    print("Robot offline")

conn.connection_info()
# {"target": "...", "state": "connected", "monitoring": True,
#  "monitor_interval": 5.0, "state_changed_ago_s": 42.0, "last_ok_ping_ago_s": 1.2}
```

### With state change callback

```python
def on_change(new_state: ConnectionState):
    if new_state == ConnectionState.DISCONNECTED:
        print("⚠ Robot disconnected!")
    else:
        print("✓ Robot reconnected")

conn.start_monitoring(interval=5.0, on_state_change=on_change)
# Safe while already running: registers the callback in place; a different
# interval restarts the loop at the new cadence.
```

### Blocking wait for connection

```python
# Wait up to 10s for robot to come online
if conn.wait_for_state(ConnectionState.CONNECTED, timeout=10.0):
    print("Robot ready")
else:
    print("Timeout — robot not reachable")
```

### Lifecycle notes

- **Auto-started by `get()`** — opt out with `KachakaConnection.get(ip, monitor=False)`
- **Re-callable** — calling `start_monitoring()` while running updates the callback and retunes the interval; same interval is a no-op
- **`RobotController.start()` retunes to its fast_interval** and wires its own callback; `RobotController.stop()` retunes back to 5 s but never stops monitoring (the pooled connection's state guarantee outlives the controller, and an idle channel would trigger server GOAWAY under keepalive)
- `stop_monitoring()` stops the background thread and clears the callback
- The background thread is a daemon — auto-exits when the process ends

### Recommended startup pattern for FastAPI apps

```python
@asynccontextmanager
async def lifespan(app: FastAPI):
    conn = KachakaConnection.get(ROBOT_IP)              # monitoring auto-starts
    conn.start_monitoring(on_state_change=handle_state_change)  # add callback
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
conn.error_definitions # {10253: {"title": "Destination not registered", ...}, ...}
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

# Poll until command finishes (completion verified against the command_id
# of the most recently accepted command on this instance)
result = cmds.poll_until_complete(timeout=60.0)
# {"ok": True, "error_code": 0, "command_id": "...", "elapsed": 12.3}
```

> :warning: **Fire-and-accept contract (since 0.6.0)**: movement/shelf commands return as soon as the robot *accepts* the command (`{"ok": True}` = accepted, not completed; the result dict includes `command_id`). Drive completion with `poll_until_complete(timeout=...)` or use `RobotController`. This bypasses the SDK's unbounded blocking long-poll (82-minute production hang, 2026-05-18). `speak()` still blocks until done.

> :warning: **poll on the SAME instance that dispatched** (since 0.7.0): `poll_until_complete` verifies completion against the tracked `command_id`. If you poll from a different `KachakaCommands` instance, pass `command_id=` explicitly (from the dispatch result dict) — an unverified poll can mistake the post-dispatch registration window (`PENDING` + empty command_id) for completion and report success in ~0 s (2026-07-07 field incident).

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
cmds.dock_shelf()                 # engage the shelf in front of the robot
cmds.undock_shelf()               # set the carried shelf down here
```

### How docking physically works

The robot drives **underneath** the shelf; a motor-driven catch then springs up
and hooks the shelf's frame. It is a catch, not a clamp and not a platform lift
— so **a shelf can come off in transit**, and shelf-drop monitoring is guarding
a real mechanical failure mode, not a hypothetical one. (Preferred Robotics'
consumer FAQ describes this as "raising the base to lift the furniture"; that is
a simplification. The firmware does have a dedicated lifting motor — hardware
error `21324` names it — but its job is the catch.)

Consequences worth designing around:

- **The shelf is rectangular, so only two headings can enter it** (180° apart),
  along the shelf's short axis. Approaching from the other two sides cannot work
  no matter how accurate the pose.
- **Identity comes from a barcode on the shelf's underside**, not from its
  appearance. On-board vision is pre-trained to *find* a shelf; the barcode says
  *which* shelf. Unregistered furniture without that barcode cannot be used at
  all — error `11009` is a failed barcode read, `11102` a missing marker.
- `queries.list_shelves()` reports each shelf's last known `pose` and its
  `speed_mode` (`"SHELF_SPEED_MODE_LOW"` = deliberately carried slowly — the
  firmware's own mitigation for unstable loads).
- **Modified shelves void the deal.** Adding casters, structures overhanging
  the base, or overloading interferes with the LiDAR and can cost the robot its
  autonomy entirely — and voids the warranty. If a user asks about bolting
  something onto a shelf, flag this before anything else.

### Which command to use

Name the shelf and let the firmware plan. It owns the approach, the search and
the retries; none of that is exposed, so there is nothing for you to steer.

| Goal | Use |
|---|---|
| Take a known shelf somewhere | `move_shelf(shelf, location)` |
| Send a shelf back to its home | `return_shelf(shelf)`, or `return_shelf()` for the one being carried |
| Pick up whatever furniture is at a location | `dock_any_shelf_with_registration(location)` — **Kachaka Pro only** |
| Put the carried shelf down here | `undock_shelf()` |

`dock_shelf()` is the odd one out: it engages the shelf in front of the robot
and does **not** bring you there. Using it means positioning the robot yourself,
in two stages:

```python
# 1. Navigate to a registered location NEAR the shelf.
#    NEVER the shelf's own home location — that is aiming at the shelf itself.
cmds.move_to_location("a nearby registered point")
cmds.poll_until_complete(timeout=60.0)

# 2. Close the last stretch with bounded relative motion, in small steps,
#    until the robot is squarely in front of the shelf.
cmds.move_forward(0.15, speed=0.1)
cmds.poll_until_complete(timeout=30.0)

# 3. Now engage.
cmds.dock_shelf()
```

The distinction that makes step 2 safe is **bounded displacement versus goal
seeking**: `move_forward()` travels exactly the distance you name and stops,
whereas a move command *aimed at* the shelf keeps pushing until it reaches a
goal it cannot see. Creep up in small steps and check `get_pose()` between them.

Everything rides on stopping accurately square-on to the shelf; that is the
whole difficulty of `dock_shelf()`, and why
`dock_any_shelf_with_registration()` is the easier choice when a suitable
location is registered (on a Pro — standard models don't have it and must use
`move_shelf()` / `return_shelf()` by name).

Two field facts that set expectations for step 2:

- **Stopping accuracy is naturally 5–15 cm** (vendor spec): ~5 cm when the
  goal is near structural features (wall corners, pillars, door frames),
  ~15 cm in open or featureless space. That variance is why the number of
  correction steps differs between spots — and why a dock target in open floor
  is harder than one near a wall.
- **If a human helps line the robot up: push it, never lift it.** Wheels must
  stay on the floor the whole time — lifting the robot even briefly breaks its
  self-localization (the same rule the vendor gives for furniture registration
  and for freeing a stuck robot).

Notes for choosing between them:

- **`dock_any_shelf_with_registration()` will register unfamiliar furniture as a
  new shelf.** That is its documented purpose, but it means aiming it at a
  location whose recorded shelf is no longer really there can leave you with a
  duplicate shelf and a duplicate home location for one physical cart. The
  public API cannot delete either — cleanup needs the mobile app. To fetch a
  shelf you already know, name it with `move_shelf()` / `return_shelf()`.
- **Leave `dock_forward` at its default** (tail-first) unless you have verified
  head-first in your own layout — whether it works depends on how the shelf sits
  relative to its surroundings. A head-first attempt that cannot complete
  reports `15602`. Only `dock_any_shelf_with_registration()` accepts this flag
  at all; `DockShelfCommand` is an empty message and `UndockShelfCommand`
  carries only a result-filled `target_shelf_id`, so plain dock/undock direction
  is the firmware's choice. Undock exit direction is picked from available space.
- **Never wrap a docking command in your own retry loop.** The firmware already
  retries internally, so a failure returns much later than a success. Measure it
  in your environment and set the timeout from that.

### :rotating_light: Never set a navigation goal at a shelf

Travelling *past* shelves is fine — at range the robot judges traversability
with camera assistance and routes around them. The failure is specific to the
**final approach**: close to the goal, a shelf's thin legs fall inside the
LiDAR's near-field filter zone, so during the last stretch of positioning the
robot has **no obstacle signal at all** for the thing directly ahead of it. Its
body sits above the scan plane, and its legs have been filtered out.

So the rule is about the *goal*, not about shelves in general: never aim
`move_to_location()` or `move_to_pose()` at a pose occupied by a shelf. A
shelf's **home location counts** — the shelf is normally parked on it, so "go to
the shelf's home" is aiming at the shelf.

When this happens the robot drives into the shelf and keeps pushing it across
the floor. Nothing reports it: `get_errors()` stays empty, `is_ready()` stays
true, the status LED does not change, and the command may finish claiming
`success: True` — or, with `move_to_pose()`, never finish at all, leaving a
caller blocked until its own timeout and only stoppable with `cancel_command()`.

So there is **no signal at any layer** that a collision happened. Do not rely on
detecting it; avoid causing it:

> **Rule: a shelf's pose — and its home location — are never a valid
> navigation goal.** To end up at a shelf, use a shelf-aware command:
> `move_shelf()`, `return_shelf()`, `dock_shelf()`, or
> `dock_any_shelf_with_registration()`. Those run the firmware's own
> shelf-aware approach, which uses the pre-trained shelf vision instead of
> relying on the filtered LiDAR return. Navigating *through* an area that
> contains shelves is fine.

A shoved shelf also invalidates the map: `list_shelves()` reports the shelf's
**last recorded** pose, not a live measurement, so after a collision the
recorded pose is simply wrong until the shelf is re-registered or re-docked.
That stale pose is what error **`11005`** ("shelf is not where it was last
placed") is reporting.

`move_shelf()` is the high-level composite: it remembers and updates the shelf's
recorded position, computes the entry pose, and applies the destination's
configured placement behaviour. Prefer it over hand-rolling dock → move → undock.

### App settings that break the API invisibly

Some behaviour is configured in the Kachaka mobile app and is **not readable
over the public gRPC API at all**. When one of these bites, the only signal is
the error code — there is no field to inspect first, so match on the code:

| App setting | Effect on the API | Error code |
|---|---|---|
| **Keep hold furniture** (on) | `return_shelf()` rejected — "put away" | `10270` |
| **Keep hold furniture** (on) | `undock_shelf()` rejected — "place". `move_shelf()` still drives to the destination and only fails at the placement step | `10271` |
| **Charge while holding furniture** (off) | Returning to the dock while carrying a shelf fails | `11019` / `11501` |

These come back almost instantly — they are refusals, not timeouts, so a fast
failure from a shelf command is a strong hint to check the app configuration.

Treat `10270`/`10271` as a configuration problem to escalate to a human, never
as something to retry — the command will fail identically every time, and while
the setting is on **there is no API path to put the shelf down at all**. A robot
that has picked one up stays loaded until someone changes the app setting.

Destination-side placement behaviour *is* readable, via `list_locations()`:

```python
for loc in queries.list_locations()["locations"]:
    loc["undock_aligning_to_wall"]     # park the shelf flush against the wall
    loc["undock_avoiding_obstacles"]   # place clear of nearby obstacles
    loc["type_name"]                   # LOCATION_TYPE_SLAM_MARKER etc.
```

Both flags are read-only here; writing them needs the app (or the private LAN
API). A Pro-only third mode stops against a printed **wall marker** above the
destination for tighter accuracy — it only engages while carrying furniture.
Failures: `11309` = marker not found; `11308` = marker found but alignment
failed (placement, registration, obstacles or floor state).

The full symptom → app-setting recommendation table lives in **"Advising Users
on App Settings"** below.

### Shelf-operation error codes

| Code | Meaning | What to do |
|---|---|---|
| `14606` / `10254` | The command needs a carried shelf, but nothing is docked | Precondition failure — dock a shelf first |
| `10255` / `10262` / `10266` | Already carrying furniture | Put the current shelf down first (`10272` / `10273` say so explicitly) |
| `11005` | Shelf is not where it was last placed | Someone moved it. Restore the recorded pose with `cmds.reset_shelf_pose(shelf)` (or the app's 「重設家具位置」), or physically return the shelf |
| `19001` | Cannot reach the destination | See the two cases below |
| `10270` | "Keep hold furniture" is on — cannot put away | Escalate to a human; no API can clear it |
| `10271` | "Keep hold furniture" is on — cannot place | Same |
| `11009` / `11102` | Cannot read the shelf's barcode / marker | Alignment or a dirty/absent barcode |
| `11103` / `11104` | Failed to dock with the shelf | Recorded map pose disagrees with reality — `reset_shelf_pose()` or put the shelf back |
| `11308` | Failed to *align* to the wall marker | Broad causes: marker placement, location registration, obstacles, floor state |
| `11309` | Wall marker not *found* | Marker missing, blocked, or badly lit |

Resolve any code with `queries.get_error_definitions()`, which returns the
robot's own table including an `error_type` severity.

**`19001` has two distinct shapes** (vendor guidance):

- **Rejected almost immediately after dispatch** → the map shows phantom
  obstacles around the furniture, so the route looks blocked. Resolution order:
  ① reset the furniture's map position, ② mark the falsely-blocked region as
  enterable in the app, ③ clean the phantom obstacles off the map. Persisting
  after all three → support.
- **Drives near the shelf but cannot get under it** → recorded positions are
  stale: check the furniture's map position against reality (restore or
  `reset_shelf_pose()`), then check the robot's own localization (see
  Localization Recovery).

## Speech

```python
cmds.speak("Patrol complete")
cmds.set_speaker_volume(5)    # 0–10
```

## Custom sounds (kachaka-api 3.17+)

Custom audio playback beyond TTS — upload a WAV clip once, then play it on
demand. Requires robot firmware / `kachaka-api` **≥ 3.17** (the Sound API is
not wrapped by the official Python SDK; `kachaka_core` dispatches it directly).

```python
add = cmds.add_sound("doorbell", path="doorbell.wav")  # or data=<wav bytes>
sound_id = add["sound_id"]

cmds.play_sound(sound_id)              # play once
cmds.play_sound(sound_id, loop=True)   # repeat until stopped
cmds.stop_sound()

queries.list_sounds()   # {"ok": True, "sounds": [{"id", "name"}, ...]}
cmds.delete_sound(sound_id)
```

> `list_sounds()` returns an eventually-consistent snapshot — a clip added or
> deleted a moment ago may take a second or two to appear/disappear.

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
# Returns base64-encoded JPEG. fresh=True (default) waits for a frame
# captured AFTER this call — the buffer may still hold a frame from before
# the robot's last move (looks normal, shows the wrong place).
img = queries.get_front_camera_image()
# {"ok": True, "image_base64": "...", "format": "jpeg", "fresh": True}

img = queries.get_back_camera_image()

# Pre-0.7 behaviour (return buffered frame immediately, no freshness check):
img = queries.get_front_camera_image(fresh=False)
```

> :warning: **Capture after movement**: keep `fresh=True` whenever the robot has just moved or rotated — with `fresh=False` the returned frame can predate the move even though pose already verifies on target (2026-07-07 field incident). For motion-blur-free shots, additionally sleep ~1 s after the move completes before capturing.

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
| Front  | Always       | After stream started | Start a `CameraStreamer(conn, camera="front")` (or any image capture) first |
| Back   | Always       | After stream started | Same, with `camera="back"` |
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
# {"ok": false, "error_code": 10253, "error": "error_code=10253: Destination not registered", ...}

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

Six layers protect against network loss:

1. **TimeoutInterceptor (cursor-aware)** — immediate reads get a 5s deadline; cursor-based long-polls (`metadata.cursor != 0`) get a bounded 300s watchdog instead of the SDK's unbounded wait. No call can hang forever.
2. **HTTP/2 keepalive** — `keepalive_time_ms=15000` / `keepalive_timeout_ms=5000`, pings permitted without data and without active calls. A silently dead transport (WiFi vanishing, no RST) is declared dead and the channel re-dials.
3. **`@with_retry`** — retries `DEADLINE_EXCEEDED` / `UNAVAILABLE` / `RESOURCE_EXHAUSTED` with exponential backoff. Count mode (N attempts) or deadline mode (retry until wall-clock limit).
4. **ConnectionState monitoring (auto-on)** — background ping fires `on_state_change` on transitions. Detection latency ~7s at the default 5s interval.
5. **RobotController** — `_state_loop` skips polling while `DISCONNECTED`. `_execute_command` waits for `CONNECTED` before sending; per-poll deadlines keep the loop alive through a mid-command drop, returning a structured `TIMEOUT`/`DISCONNECTED` error within `timeout=`.
6. **CameraStreamer** — `_run` loop skips capture while `DISCONNECTED`. Records `recovery_latency_ms` on first successful capture after reconnect. Check `latest_frame_age_s` to detect stale frames.

Measured timeline after a **silent** drop (real robot, firmware 3.16.x):

| Event | Latency | Mechanism |
|---|---|---|
| `conn.state` → DISCONNECTED | ~7 s | health ping fails |
| Immediate-read RPC fails | ≤ 5 s | per-call deadline |
| In-flight long-poll released | ≤ 60 s macOS / ~20 s Linux | keepalive kills dead transport |
| Worst-case long-poll release | ≤ 300 s | long_poll_timeout watchdog |
| Channel re-dial after network returns | ≤ ~60 s | keepalive + gRPC reconnect |

**Firmware behaviour (measured)**: the robot's server cancels a held long-poll after ~35–40 s with `CANCELLED` — independent of keepalive. Toolkit retry/poll layers absorb it.

**Important**: clean disconnects (RST/FIN) recover automatically with no channel rebuild; silent drops need keepalive to declare the old transport dead before the channel re-dials (up to ~60 s).

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

# Switch to another map (multi-floor / multi-site robots)
cmds.switch_map(map_id)
# The robot re-localizes on the new map; with no initial pose given, the
# firmware assumes the charger pose. Pro only: inherit_docking_state flags
# let a carried shelf survive the switch — standard models reject them.
```

> :warning: `get_map()` returns Kachaka's own 3-colour palette PNG. It is
> **not** re-importable via `import_image_as_map`, which expects a grayscale
> ROS-style occupancy grid — export/import round-trips must use
> `export_map` / `import_map` (the proprietary binary format) instead.

**Map metadata fields:**
- `resolution` — meters per pixel
- `width`, `height` — image dimensions in pixels
- `origin_x`, `origin_y` — world coordinates (meters) of the bottom-left pixel (ROS convention)

### Reading the occupancy grid

Applications keep reimplementing this and keep getting it slightly different,
so the rules are written out here. `get_map()` hands back the PNG bytes
untouched — the toolkit deliberately does no interpretation, because what
counts as "passable" depends on the robot's footprint and the caller's safety
margin.

**The PNG is colour, not grayscale.** Every pixel is one of exactly three
palette entries:

| RGB | Meaning | After PIL `.convert("L")` |
|---|---|---|
| `(191, 170, 155)` brown | Obstacle / wall | `175` |
| `(244, 232, 219)` beige | Unknown / unexplored | `234` |
| `(253, 253, 253)` white | Free / passable | `253` |

Measured 2026-08-13 on robot BKP40HD1T (firmware 3.17.8), map `長照展`
(336×305 @ 0.025 m/px): 1.55 % obstacle, 56.75 % unknown, 41.70 % free, and
**no other values at all**.

Two traps follow from this, and both have bitten real deployments:

1. **The numbers depend on how you convert.** `175 / 234 / 253` are what PIL's
   `.convert("L")` (ITU-R 601-2 luma) produces. A plain channel average gives
   `172 / 231.7 / 253` instead. Match the RGB triples directly when you can —
   they are exact and conversion-independent.
2. **Unknown is not free.** A single threshold at `>= 250` correctly rejects
   both obstacle *and* unknown. A threshold anywhere between `176` and `233`
   (e.g. `> 210`) silently classifies **unexplored space as drivable**, which is
   how a planner ends up routing through a wall it has never seen.

```python
import base64, io
from PIL import Image

m = queries.get_map()
img = Image.open(io.BytesIO(base64.b64decode(m["image_base64"]))).convert("RGB")

FREE, UNKNOWN, OBSTACLE = (253, 253, 253), (244, 232, 219), (191, 170, 155)

def is_free(px_x: int, px_y: int) -> bool:
    """Only genuinely explored, empty cells count as free."""
    return img.getpixel((px_x, px_y)) == FREE
```

### World ↔ pixel coordinates

`origin_x/origin_y` are the world coordinates of the **bottom-left** pixel (ROS
convention), but PNG row `0` is the **top** row. That Y flip is the single most
common mistake — get it wrong and everything is mirrored about the map's
horizontal centre line, which looks plausible enough to ship.

```python
res, h = m["resolution"], m["height"]
ox, oy = m["origin_x"], m["origin_y"]

def world_to_pixel(x: float, y: float) -> tuple[int, int]:
    # Floor the row-from-bottom FIRST, then flip. Writing this as
    # int(h - 1 - (y - oy) / res) truncates after the subtraction and is
    # off by one row for every y that is not exactly on a cell boundary.
    # (int() truncates toward zero — identical to floor for coordinates
    # inside the map, where x >= ox and y >= oy always hold.)
    return int((x - ox) / res), h - 1 - int((y - oy) / res)

def pixel_to_world(px: int, py: int) -> tuple[float, float]:
    return ox + (px + 0.5) * res, oy + (h - 1 - py + 0.5) * res
```

`pixel_to_world` adds the half-pixel so the result is the centre of the cell
rather than its corner; skipping it biases every waypoint by half a cell
(1.25 cm at 0.025 m/px — harmless alone, visible once accumulated over a route).

Round-tripping every registered location through both functions should land
back within half a pixel — that is the cheapest way to catch a broken Y flip,
and the off-by-one above fails it on every point.

### Estimating distances between locations

Straight-line distance between two `list_locations()` poses is fine for
**ranking** (nearest point, rough visit order) and is what most callers
actually need. It is *not* the travel distance — walls, one-way furniture gaps
and the robot's own clearance margin all make the real path longer, sometimes
by a lot in corridor-shaped spaces.

Do not build a path planner on top of this. `move_to_location` runs the
robot's own planner, which knows about inflation radius, dynamic obstacles and
docking approach angles — none of which is recoverable from the PNG. Use the
grid to *pick* a destination; let the robot work out how to get there.

If a route's ordering genuinely matters, measure it once with the real robot
and cache the result, rather than trusting a computed estimate.

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
 "error": "error_code=10253: Destination not registered"}
{"ok": False, "error": "TIMEOUT", "timeout": 120}  # Timeout (RobotController)
```

### Custom retry for new functions

```python
from kachaka_core.error_handling import with_retry

@with_retry(max_attempts=5, base_delay=2.0, max_delay=15.0)
def my_custom_operation(sdk):
    ...
```

### Reading robot error codes correctly

Two rules govern how codes behave, and getting them wrong changes recovery
logic:

1. **Persistent vs event codes.** A persistent state (e.g. `21051` paused)
   stays in `get_errors()` until it is cleared. An *event* error (e.g. blocked
   by an obstacle) appears only at the moment it happens — so an empty
   `get_errors()` does **not** mean a recent event error was resolved; it may
   simply have scrolled past. For events, look at
   `get_last_command_result().error_code` instead.
2. **`error_type` severity drives the response.** Every code in the robot's
   table carries a severity — `Ignore` / `Warn` / `Error` (retry may work) /
   `Bug` (needs a firmware update) / `Recoverable` (clears itself) / `Fatal`
   (cleared by `restart_robot()`) / `CallForSupport`. Branch on it: `Fatal` →
   suggest `restart_robot()`; `CallForSupport` → stop and escalate;
   `Error` → fix the physical situation, then retry once.

Three ways to look a code up — same table underneath, different freshness:

| Accessor | Freshness | Use for |
|---|---|---|
| `conn.error_definitions` | fetched once, cached for the session | cheap repeated lookups |
| `queries.get_error_definitions()` | live RPC each call | when firmware may have been updated mid-session |
| `RobotController` error enrichment | live, internal | automatic — failed commands already carry the description |

`kachaka_core.error_codes` additionally names the handful of codes the toolkit
acts on (paused, hardware-fatal, task-blocked, task-failure groups).

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
queries.get_manual_control_enabled()          # confirm the mode took effect
cmds.set_velocity(linear=0.1, angular=0.0)    # Forward slowly
cmds.stop_manual_drive()                      # stop driving, leave manual mode
```

## Stopping the Robot

Three different things are called "stop" and they are not interchangeable.
Picking the wrong one is how a robot keeps driving after you thought you had
stopped it.

| Want to… | Use | Effect | How it clears |
|---|---|---|---|
| Abort a running command (the usual case) | `cmds.cancel_command()` | Command ends with error `10001`, robot stops | Immediately — accepts new commands |
| Stop manual velocity driving | `cmds.stop_manual_drive()` | Zeroes velocity, leaves manual mode | Immediately |
| Latched hardware pause | `cmds.set_emergency_stop()` | Robot pauses, raises active error `21051` | **Only by physically pressing the power button** |

**`stop_manual_drive()` does not stop autonomous navigation.** Measured on
BKP40HD1T (firmware 3.17.8, 2026-08-13): called 2.6 s into a `move_to_location`,
the robot carried on and the command still finished `success=True`. Under the
same conditions `cancel_command()` stopped it after 0.11 m with error `10001`.
The old name for this method was `stop()`; it is deprecated for exactly this
reason.

**`set_emergency_stop()` is a one-way door** and is deliberately absent from the
MCP tool surface. Every software release path was tried and all of them failed —
`cancel_command()`, `proceed()`, `set_manual_control(True)` (rejected with
`12401`), `set_manual_control(False)`, `stop_manual_drive()`, and issuing a
fresh movement command. Only the physical button clears it. Never call it on a
robot nobody can walk up to.

> :warning: While latched, a movement command still returns `ok=True` with a
> `command_id` — the rejection only shows up afterwards as `10107` in
> `get_last_command_result()`. Gate on `queries.is_ready()` instead of trusting
> the accept.

> :warning: `stop_manual_drive()` briefly flips the manual-control flag. When
> the robot is *not* already in manual mode, the SDK has to enable it to deliver
> the zero velocity, then disables it again — and the disable has not always
> propagated by the time the call returns (observed still reading `True` 33 ms
> later, in 1 of 3 trials). Give it ~0.5 s before trusting
> `get_manual_control_enabled()` right after a stop.

## Localization Recovery

For localization jumps (pose teleports to a wrong place in feature-poor
areas) or after the robot was manually carried somewhere:

```python
# Force re-localization on the current map (fire-and-accept)
result = cmds.localize()
cmds.poll_until_complete(timeout=60.0)

# When the true position is roughly known, seed it first so
# localization converges to the right hypothesis:
cmds.set_robot_pose(x=1.5, y=-2.0, theta=0.5)   # direct RPC, immediate
result = cmds.localize()
cmds.poll_until_complete(timeout=60.0)

# Verify recovery
queries.get_pose()
```

> :warning: **Jump detection**: Detect jumps by watching consecutive poses: a displacement
> exceeding the robot's physical max speed between two polls is a jump,
> not movement. After `set_robot_pose()` the estimate changes instantly —
> always follow with `localize()` + a pose sanity check before resuming
> navigation.

When a human is on site, two vendor-documented recovery paths that are not in
the API are often faster — an agent should suggest them:

1. **Voice command**: saying 「ねぇカチャカ、落ち着いて」 to the robot triggers
   re-localization. The wake-word phrase is Japanese and must be spoken as-is.
2. **Physical reset**: if re-localization keeps failing, push the robot back
   onto its charging dock (wheels on the floor — never lift it) and let it sit
   for about a minute.

If localization drifts repeatedly at the same spots, that is what Pro
self-localization markers are for — see the app-settings section below.

## Advising Users on App Settings

Much of the robot's behaviour is decided by settings in the Kachaka mobile app
that the public gRPC API can neither read nor write. An agent's job on site is
therefore **diagnosis → recommendation**: recognize the symptom (often an error
code), name the setting that causes it, and tell the user exactly what to
change in the app.

Only two knobs can be changed directly over the public API — everything else
below is advice for the human:

```python
cmds.set_speaker_volume(5)      # 0–10 (Pro can go higher); out-of-range → 13302
cmds.set_auto_homing(True)      # auto-return to charger on/off (readable too)
```

### Symptom → recommendation quick table

| Symptom seen from the API / on site | Setting (app path) | Recommend |
|---|---|---|
| `10270`/`10271` — undock/put-away always refused, near-instantly | **Keep hold furniture**（家具持續結合）— 詳細設定 → 家具一體化模式 | Turn **off** unless the deployment intends the robot to permanently keep its shelf. Verified live: while on, there is **no API path to put the shelf down** |
| `11019`/`11501` — "cannot be charged with a furniture on it" | **Charge while holding furniture**（帶著家具充電）same screen | Turn **on** if the robot should dock to charge without dropping its shelf |
| Robot finishes a delivery and just sits there, shelf still mounted, battery draining | **Auto return to charger (with furniture)**（自動返回充電座・載家具時）| Enable and set the idle-seconds timer. Requires furniture-integration mode; without it the timeout behaviour is "put the shelf down here" instead |
| Robot idles in a walkway before returning to charge (or returns too eagerly) | **Auto return to charger (no furniture)** — default 30 s | Adjust the idle timer either way |
| Robot refuses a carpeted corridor and detours | **No carpet entry**（禁止進入地毯）— on by default | If the carpet is safe to cross, turn off; if it must be avoided, keep on |
| Robot stalls on patterned floor / material transitions for no visible reason | **Floor obstacle detection**（地面障礙物偵測）misfiring | Prefer marking that floor as an enterable zone on the map; disabling the whole detector is the blunt fallback |
| While carrying furniture it hits table tops / protruding edges | **Protruded obstacle detection**（凸出障礙物偵測・搬運時）| Should be on — check it was not disabled |
| Solo robot dives under tables/chairs and snags | **Protruded obstacle height**（本體單獨行駛偵測高度）— default 0.30 m, min 0.13 m | Set to the local furniture leg height |
| Frequent pauses with `21052`/`10108`/`10106`/`21308` (step detected) on genuinely flat floor | **Step detection**（段差偵測）| Only then consider disabling — **never** where real steps/stairs exist; fence those with no-entry zones instead |
| Gives up in front of narrow passages that are actually passable / conversely hugs edges and snags | **Movement caution level**（移動謹慎度）— default 普通 | Bolder ↔ more cautious. It changes path planning only; physics still limits minimum width |
| "Too fast for this crowded site" / "too slow" | **Moving speed**（移動速度）1–4, default 4 | Global slider; per-corridor speed needs Pro speed zones instead |
| A specific heavy/tall shelf wobbles on start | **Shelf 緩慢起步** — per-shelf, app 家具編輯 | Readable first: `list_shelves()[…]["speed_mode"]` — if `NORMAL`, suggest switching that shelf to LOW |
| Shelf placed at a spot is not flush with the wall behind it | **家具擺放方式 → 靠牆對齊** per destination | Readable first: `list_locations()[…]["undock_aligning_to_wall"]` |
| Long carries get cancelled mid-route with nothing blocking (Pro) | **Max time to destination** — default 300 s | Lengthen; or shorten to fail fast. If it keeps failing, suspect blocked paths or localization drift first |
| Robot on a gentle slope creeps when stopped (Pro) | **Brake during stop**（停止時煞車）— on by default | Keep on for any sloped site; off only for flat sites where humans push the robot around |

### Rules for giving this advice

- **Cite the observed evidence** (error code, behaviour) when recommending —
  these settings are invisible to the API, so the code is the only proof.
- **Never advise disabling a safety detector as the first move** — prefer map
  zones (no-entry / enterable) that scope the change to one area. Step
  detection in particular must stay on wherever a real drop exists.
- **Configuration refusals are not retryable.** `10270`/`10271` (and the
  invalid-value family `13302`/`13303`/`13304`/`10282` if the private API is in
  play) will fail identically every time; escalate, don't loop.
- Settings marked readable above should be **read before advising**, so the
  recommendation states the current value and the proposed one.

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
| Monitor connection health | Auto-on via `get()`; read `conn.state` / `connection_info()` | Own ping loop |
| Handle disconnection | Built-in (5-layer resilience) | Custom reconnection logic |
| Track camera frame stats | `streamer.stats` (drop rate, recovery) | Manual frame counters |
| Shelf drop detection | `RobotController` (auto-tracks) | Poll `get_moving_shelf()` yourself |
| Error descriptions | Auto-enriched in all results | `get_error_definitions()` + manual lookup |
| gRPC timeout protection | `TimeoutInterceptor` (5s default / 300s long-poll) | Per-call `timeout=` parameter |
| Recover from localization jump | `cmds.set_robot_pose()` + `cmds.localize()` | Restart the robot or re-map |
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
| Stopping | `stop_manual_drive()` (or old `stop()`) to halt navigation | `cancel_command()` — the only one that stops a moving robot |
| Shelves | Nav goal at a shelf pose or its home location | `move_shelf` / `return_shelf` / `dock_any_shelf_with_registration` |
| Docking | Wrapping dock commands in your own retry loop | Firmware retries internally — set one timeout and trust it |

## SDK Reference

The underlying `kachaka-api` SDK (>= 3.17, pinned by this toolkit) provides:

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
