# Command semantics — field-measured on firmware 3.18.1

Measured 2026-09-23 on two Kachaka Pro units (Mitsubishi `BKP60UG1T`, Sharun
`BKP40EU1T`), firmware **3.18.1**, `kachaka-api` **3.17.5**, app "queue
functionality" **off** on both. Method: a Playground probe script issuing raw
`stub.StartCommand` calls with 10 Hz pose logging; 11 in-motion overrides, 4
`cancel_all=False` trials, 41 command results matched by `command_id`.
Everything below is observed behaviour, not documentation — re-verify on a new
firmware before relying on it.

## 1. Overriding a running command (`cancel_all=True`) is NOT seamless

A new `StartCommand` with `cancel_all=True` while the robot is driving is
accepted in 8–20 ms, but the firmware **brakes to a near-stop, re-plans, then
re-accelerates**:

| | cruise speed | pause (< 0.05 m/s) | onset after override |
|---|---|---|---|
| empty | 0.59–0.66 m/s | 0.9 / 1.3 / 1.0 / 3.5 s | +1–2 s |
| carrying shelf | 0.70 m/s | 1.4 / 2.0 / 2.1 / 1.2 s | +1–2 s |

The pause does **not** depend on the turn angle (a 3° retarget still paused
2.0 s). Budget **1–2 s per override** when designing "retarget in motion".

## 2. `cancel_all=False` queues; it never hands over

With the app queue setting off, a `StartCommand(cancel_all=False)` during
motion is **accepted (no error) but queued**: the current command runs to
completion (robot stops at its goal), then the new one starts (4/4 trials,
2.0–4.7 s later). It is neither rejected nor a smooth retarget. The app's
"queue functionality" toggle governs the app layer only; the firmware queues
regardless.

## 3. The cursor result stream is the only authority; history is not

- `stub.GetLastCommandResult(GetRequest(metadata=Metadata(cursor=c)))`
  (long-poll, seed `c` from `stub.GetCommandState(...).metadata.cursor`)
  publishes **only commands that actually finished**. 41/41 matched by
  `command_id`, zero unknown ids.
- A command that you preempted with `cancel_all=True` gets its `10001`
  (interrupted) published on that stream **only sometimes** (3/11). Treat
  "I issued a newer command after this handle" as *preempted* on your own
  bookkeeping; never wait for the 10001.
- **`get_history_list()` records `10001` for commands that succeeded.** Any
  command immediately followed by another `cancel_all=True` command (the normal
  pattern for chained moves) shows up in history as interrupted even though the
  result stream reported `success=True` and the robot reached the goal (26 of
  46 entries in one session). Do not diagnose failures from history. `History`
  on 3.17.5 stubs also has no `title` field, so the `title` you pass to
  `StartCommandRequest` cannot be used to match history entries.
- `cancel_command()` also yields `10001` for the cancelled command.

## 4. Idle looks like PENDING, not UNSPECIFIED

An idle robot reports `get_command_state()` → `(COMMAND_STATE_PENDING (1),
<empty Command>)` and `stub.GetCommandState(...).command_id == ""`. Treat
**busy = RUNNING, or PENDING with a non-empty command** (`command.WhichOneof
("command") is not None`). Checking `state != 0` marks an idle robot busy;
`is_command_running()` (RUNNING only) lets a queued PENDING command through.

## 5. Shelf commands

- `move_shelf(shelf, location)` **does not undock at the destination** on this
  firmware/setup: the robot stays docked under the shelf (one unit sat under
  its shelf for two days after the last `move_shelf`). Chained `move_shelf`
  calls therefore do not re-dock; `get_moving_shelf_id()` stays non-empty.
- `return_shelf(shelf)` while **not** holding it does not return an error — the
  robot **drives to the shelf, docks it and returns it home** (58 s on one
  unit; 12 m away and timing out on another). Never issue a "just in case"
  `return_shelf`; check `get_moving_shelf_id()` first.
- `cancel_command()` mid-carry keeps the shelf docked (no drop); the next
  `move_shelf` after a cancel took **7.3 s** to start moving (no re-dock).
- In-motion override while carrying (6/6 trials, sampled every 0.5 s): the
  shelf stayed docked throughout.

## 6. Pose polling

`get_robot_pose()` is a cheap unary call: ~3 ms (p95 6 ms). 10 Hz polling held
p95 interval 0.101 s, 5 Hz 0.201 s. Localisation corrections of 0.2–0.3 m
between consecutive samples do occur — gate any position-threshold logic on
several consecutive samples, not one.

## 7. Non-blocking issue/poll pattern (verified on-robot)

The SDK wrapper `start_command(wait_for_completion=False)` discards
`command_id`, so match results yourself:

```python
from kachaka_api import pb2

def issue(client, command, title=""):
    resp = client.stub.StartCommand(
        pb2.StartCommandRequest(command=command, cancel_all=True, title=title),
        timeout=5.0)
    if not resp.result.success:
        raise RuntimeError(resp.result.error_code)
    return resp.command_id

# Result watcher (own thread): seed the cursor, then long-poll.
cursor = client.stub.GetCommandState(
    pb2.GetRequest(metadata=pb2.Metadata(cursor=0))).metadata.cursor
while running:
    try:
        r = client.stub.GetLastCommandResult(
            pb2.GetRequest(metadata=pb2.Metadata(cursor=cursor)), timeout=2.0)
    except grpc.RpcError as e:          # CANCELLED == no new result yet
        if e.code() != grpc.StatusCode.CANCELLED:
            time.sleep(0.1)
        continue
    cursor = r.metadata.cursor
    results[r.command_id] = (r.result.success, r.result.error_code)
```

`poll(handle)`: `results[handle]` → ok/failed; handle superseded by a newer
`issue()` → preempted (whether or not a 10001 arrived); otherwise pending.
Only one thread may call `StartCommand`/`cancel_command` — the wrapper's
blocking calls do an un-cursored re-read of the last result and will pick up
another thread's result.
