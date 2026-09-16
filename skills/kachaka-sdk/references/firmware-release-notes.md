# Kachaka Firmware Release Notes — SDK-Focused Reference

## How to use this file

Deployed robots run mixed firmware (a fleet can hold `3.15.4` and `3.17.8` side
by side), and the vendor's release notes are the only record of when a behaviour
appeared, changed or was fixed. Use this file to answer "what does a robot on
firmware X do?" before blaming your code for behaviour that differs between robots.

1. Read the robot's firmware: `conn.version` (cached per session) or
   `queries.get_version()`.
2. Compare as integer tuples, never as strings (`"3.9" > "3.17"` as strings):

   ```python
   firmware = tuple(int(p) for p in conn.version.split("."))
   if firmware < (3, 17, 10):
       ...  # first move after restart_robot() with a shelf docked may fail — retry once
   ```

3. Look the version up below. Section order: API changes → behaviour changes by
   topic → app settings & Pro features → full per-version digest → open
   questions.

**SKILL.md's "Feature ↔ Version Matrix" stays authoritative for toolkit API
gates** (which `kachaka_core` call needs which `kachaka-api` / firmware). This
file is the long tail: bugfixes, Pro-only features, app settings and behaviour
changes back to 1.0.

Conventions:

- **[Pro]** = the vendor tagged the item Kachaka Pro only. Untagged = all models.
- Dates are the implementation (rollout) date unless noted.
- "App setting" means configured in the Kachaka smartphone app and, unless
  stated, **not readable or writable over the public gRPC API**.
- Facts are the vendor's claims. Anything not yet observed through the API on a
  real robot is marked *unverified*.

**Source:** Kachaka Help Center announcements —
<https://kachaka.zendesk.com/hc/en-us/categories/5900442130575-Announcements>
(per-version article links in the digest), retrieved 2026-09-16. Where the
English and Japanese articles differ, the Japanese original was treated as
authoritative and the difference is noted.

---

## API & programming-interface changes

Newest first. "Toolkit" names the `kachaka_core` wrapper where one exists.

| Firmware | Date | Change | Scope |
|---|---|---|---|
| 3.18.1 | 2026-09-16 | New `DepartFromChargerCommand`: moves slightly forward to leave the charging dock when on it (the upstream kachaka-api client notebook adds: does nothing when not on the dock). Toolkit: `cmds.depart_from_charger()` (needs kachaka-api 3.18.1 stubs). | all |
| 3.18.1 | 2026-09-16 | `MoveForwardCommand` (also routine forward moves and distance moves with sensors off) no longer rotates at the end of the forward movement. | all |
| 3.18.1 | 2026-09-16 | Physical emergency-stop button state readable through the API as error code `21057` (distinct from the pause code `21051`). | Pro e-stop model |
| 3.18.1 | 2026-09-16 | Pose alignment while charging now only against a registered dock within ~3 m of the current pose; no fallback jump to the layout's dock position. Affects what `get_pose()` reports after docking. | all |
| 3.17.5 | 2026-07-09 | Register and play arbitrary sounds via the API (Sound API). Toolkit: `add_sound` / `play_sound` / `stop_sound` / `delete_sound` / `list_sounds`. | all |
| 3.17.5 | 2026-07-09 | Fixed: `move_forward` called without a speed returned an error. (The raw SDK's default is `speed=0.0`; the toolkit measured this as error `15508` on 3.16.) | all |
| 3.16.1 | 2026-04-27 | Routine actions for move-forward/backward with obstacle sensors muted; not executed while on the charging dock. API counterparts `move_forward(mute_sensors=True)` / `move_by_velocity_muted` are gated at 3.16.1 in SKILL.md — whether the dock exclusion applies to them is *unverified*. | Pro (routine action) |
| 3.14.4 | 2025-11-12 | Import map data from a map image, generating a map for Kachaka Pro; must be done via the API. Toolkit: `import_image_as_map`. | tagged [KachakaAPI], output described as a Kachaka Pro map |
| 3.11.11 | 2025-05-28 | `IsReady` can return preparation statuses such as "map switching in progress". (Toolkit `queries.is_ready()` deliberately does not call this RPC.) | all |
| 3.11.11 | 2025-05-28 | "Load the furniture at the destination" — previously API / Kachaka Button only — is now also an app routine action. API counterpart: `dock_any_shelf_with_registration` (Pro-only per SKILL.md). | all |
| 3.10.6 | 2025-03-25 | `DockAnyShelfWithRegistrationCommand` executions now appear in the app's history. | all |
| 3.8.5 | 2025-01-22 | Correct a discrepancy between the map position and the real position from the app or the API. Toolkit: `set_robot_pose`. | all |
| 3.7.6 | 2024-12-17 | `MoveForwardCommand` accepts a movement speed. | all |
| 3.4.7 | 2024-10-02 | New `DockAnyShelfWithRegistration` API: dock at a destination and auto-register unregistered furniture found there. | Pro |
| 3.4.7 | 2024-10-02 | API option to keep the docking state (shelf on board) across a map switch. Toolkit: `switch_map(inherit_docking_state=True)`. | Pro |
| 3.3.8 | 2024-09-11 | Fixed frequent map-export failure `12117` introduced in 3.3.7. `12117` can still occur while the robot is moving — dock it and retry. | Pro |
| 3.1.10 | 2024-06-20 | New APIs: get errors, restart Kachaka, emergency stop; map switch accepts an initial pose. | all |
| 3.1.10 | 2024-06-20 | Fixed: Kachaka API not starting correctly (which also left the Kachaka Button unresponsive). | all |
| 3.0.14 | 2024-05-16 | New APIs: battery information, ToF sensor image, map switching, front/back LED (torch). | all |
| 3.0.14 | 2024-05-16 | `StartCommand` gains the `deferrable` option (queue instead of cancelling). | all |
| 3.0.14 | 2024-05-16 | Fixed: connecting via `kachaka-<serial>.local` from Kachaka Hub or the API sometimes failed. | all |
| 2.7.8 | 2024-03-25 (announced) | New `GetMovingShelfId` (currently carried furniture) and `ResetShelfPose` (reset furniture position to home). | all |
| 2.6.5 | 2024-03-04 | API clients can address the robot as `kachaka-<serial>.local` instead of an IP. Whether `KachakaConnection.get()` accepts a hostname is *unverified*. | all |
| 2.6.5 | 2024-03-04 | Fixed: robot kept operating while LiDAR error `21004` was active. | all |
| 2.5.4 | 2024-02-05 | Map import/export via the API, including furniture and destinations — share a map across robots without re-scanning. | all |
| 2.3.8 | 2023-11-20 | Rear camera image retrieval via the API. | all |
| 2.0 | 2023-08-14 | Kachaka API launched (body software ≥ 2.0.8): install your own program in the on-robot sandbox; move, speak, dock/undock furniture, integrate external services. Docs and samples (JupyterLab, Python, ROS2): <https://github.com/pf-robotics/kachaka-api>. | all |
| (Terms) | 2023-08-14 | Using the API requires agreeing to the separate Kachaka API Terms of Use: Japan-headquartered corporation or Japan-resident individual owning a Kachaka with an active subscription; no support obligation and no liability even for support given at the vendor's discretion; no data backup or preservation guarantee. | all |

---

## Behavior changes an API user must know

High- and medium-relevance items, newest first within each topic. App settings
that only matter when advising a user are in the next section.

### Navigation & routes

- **v3.18.1** (2026-09-16) [Pro] "Stop and turn on a straight path": on a location-to-location route configured as a straight path, the robot stops and turns to re-align instead of weaving.
- **v3.17.5** (2026-07-09) [Pro] A configured route between locations is more likely to still be used when a move is cancelled partway through.
- **v3.15.1** (2026-01-19) Twisted (self-overlapping) no-entry areas are now handled properly by path planning.
- **v3.15.1** (2026-01-19) [Pro] Fixed: when blocked by an obstacle on an inter-point route, the robot could drive the wrong way.
- **v3.14.4** (2025-11-12) [Pro] Beta option "Travel along inter-point routes for all movements": the robot follows a nearby inter-point route whatever the task's start point (previously only when starting at the route's start). Not visible to the API — a candidate explanation when an API move takes an unexpected path.
- **v3.14.4** (2025-11-12) Fixed: robot could stop without turning while on an inter-point route.
- **v3.13.4** (2025-09-03) Fixed: unwanted U-turns near specified routes (走行指定ライン).
- **v3.12.3** (2025-07-09) Fixed: using route specification disabled the per-destination "Skip position and orientation adjustment" setting.
- **v3.11.13** (2025-06-16) [Pro] Unspecified fix for routes between locations.
- **v3.10.7** (2025-04-09) [Pro] Unspecified fix for designated driving lines (走行指定ライン).
- **v3.10.6** (2025-03-25) [Pro] Slope areas: a mapped zone where the robot brakes/locks its wheels and auto-adjusts laser-sensor distance thresholds on inclines. Map the slope with "brake while stopped" enabled; draw the zone over the slope plus ~1.5 m of flat floor at each end; add no-entry zones beside low-railing openings; optionally a designated route to stop zig-zagging. Never put a furniture home/destination on a slope or use "place furniture here" there — loads slide. Pausing (yellow LED) on a slope releases the brakes: hold the robot first.
- **v3.10.6** (2025-03-25) Route planning now considers all configured shared routes together.
- **v3.10.6** (2025-03-25) Fixed: robot could give up partway through a long-distance point-to-point route.
- **v3.10.6** (2025-03-25) Fixed: a speed zone's speed did not apply immediately after entering the zone.
- **v3.9.7** (2025-02-25) [Pro] Unspecified fix for designated routes (走行ルート指定).
- **v3.9.5** (2025-02-12) [Pro] "Upper time limit for reaching destination" (Settings → robot → Advanced settings): default 5 min while carrying furniture, configurable 10–1800 s; past it the robot stops moving toward the destination.
- **v3.8.5** (2025-01-22) [Pro] Speed zones: per-area driving speed (Map tab → + → speed zone; freeform boundary, slider, multiple zones). Actual speed inside a zone does not follow whatever the caller assumed.
- **v3.8.5** (2025-01-22) [Pro] Runtime obstacle toggle on designated routes/lines (障害物の迂回): ON = detour around the obstacle; OFF = stop; after a set wait time it detours around the obstacle, or resumes on the route if the obstacle clears first. The robot always stops for a person. A separate planning-time setting (障害物を避ける経路, designated lines only) controls clearance from walls/furniture. Travel under 1 m always uses a free path. On ≤ 3.7.7 routes always detoured.
- **v3.8.5** (2025-01-22) [Pro] Fixed: the designated route did not switch when the map was changed.
- **v3.6.6** (2024-11-27) [Pro] Route specification: designated travel lines (走行指定ライン) and point-to-point routes (地点間ルート, optional waypoints, reversible) make the robot follow a route instead of its own shortest path; routes under 1 m fall back to free-path planning. Not readable over the API.
- **v3.4.8** (2024-10-10) [Pro] Fixed 3.4.7 regression: movement frequently failed after deviating from a fixed route.
- **v3.1.10** (2024-06-20) Fixed: edits to a destination's position were rarely not reflected.
- **v3.0.14** (2024-05-16) [Pro] Configurable driving routes (run along walls, take corners wide, exclude a route).
- **v2.6.5** (2024-03-04) Destinations and furniture tidy-up locations can be registered inside enterable areas.
- **v2.6.5** (2024-03-04) Fixed: slow start toward the next destination right after arriving.
- **v2.3.8** (2023-11-20) When an obstacle blocks the direct route, the robot reaches the destination via a detour instead of failing — travel may be longer and differ from the shortest path.
- **v2.1** (2023-09-11) Setting to skip the fine position/orientation adjustment on arrival (arrival precision traded for speed).
- **v2.1** (2023-09-11) Smoother turns — sudden acceleration/deceleration while turning removed.
- **v2.0** (2023-08-14) The robot can now enter an enterable area drawn over space with no map data.
- **v2.0** (2023-08-14) Smoother movement where objects move often (e.g. chairs with casters).

### Movement primitives

- **v3.18.1** (2026-09-16) End-of-move rotation removed for `MoveForwardCommand`, routine forward moves and distance moves with sensors off. On older firmware expect a possible trailing rotation — re-read the heading after `move_forward()`.
- **v3.17.5** (2026-07-09) Fixed: `move_forward` without a speed errored. The toolkit measured `speed=0.0` → `15508` on 3.16 (2026-05-08); treat that rejection as 3.16.x–3.17.4 behaviour. What `speed=0.0` does on 3.17.5+ (no-op or a default speed) is *unverified* — keep passing an explicit speed.
- **v3.16.1** (2026-04-27) [Pro] Routine actions to move forward/backward with obstacle sensors muted (curtains, noren, mechanism integration); not executed while the robot is on the charging dock.
- **v3.7.6** (2024-12-17) `MoveForwardCommand` accepts a speed.

### Shelves & furniture

- **v3.17.10** (2026-09-08) Fixed: the first move after a restart with furniture loaded could fail. On older firmware (3.15.x and 3.17.8 included) retry the first move after `restart_robot()` or a daily restart while a shelf is docked.
- **v3.17.8** (2026-07-23) Fixed: "Readjust When Stopped" did not work with "Align with Wall Marker" (the destination toggle added in 3.17.5).
- **v3.17.5** (2026-07-09) [Pro] Destination setting to switch stop-readjustment behaviour for "Align with Wall Marker" — non-functional in 3.17.5 (the Japanese note says fixed in 3.17.8; the English note omits that).
- **v3.16.2** (2026-06-01) [Pro] Retry behaviour of marker-based wall alignment adjusted (no details; relates to `11308`/`11309`).
- **v3.16.1** (2026-04-27) Undock reserves clearance based on the shelf's size, even when the surroundings are not fully visible to the sensors.
- **v3.13.4** (2025-09-03) Fixed: furniture docking unstable under certain (unspecified) conditions.
- **v3.11.11** (2025-05-28) [Pro] Fixed: travel route not used correctly when picking up furniture. With routes between locations, the furniture must sit near its home.
- **v3.11.11** (2025-05-28) Furniture loading stability improved.
- **v3.10.6** (2025-03-25) Wall-marker stops now align to the destination furniture's orientation.
- **v3.10.6** (2025-03-25) Fixed: collision with the wall when restarting after a wall-marker stop.
- **v3.10.6** (2025-03-25) Smoother acceleration when starting from a wall-marker stop.
- **v3.10.6** (2025-03-25) Improved stopping accuracy with floor markers — English note only; no matching Japanese bullet, so the scope is uncertain.
- **v3.6.6** (2024-11-27) [Pro] High-precision arrival via wall markers: 10 cm × 10 cm marker printed at 100 %, mounted level on a mapped wall, matte material. Engages only while carrying furniture. Robot-wide "re-adjust on stop" (default ON) trades accuracy for speed — turn off for tight spots.
- **v3.6.6** (2024-11-27) [Pro] Maximum furniture cargo width raised to 65 cm (no width field in `list_shelves()`).
- **v3.3.7** (2024-08-26) Fixed: continuous undocking when switching to a map with more than two furniture pieces while "Keep furniture" was on.
- **v3.2.6** (2024-07-31) Furniture integration mode split into "Charge While Holding Furniture" and "Keep Holding Furniture" (SKILL.md: Keep hold furniture on → `10270`/`10271`; Charge while holding furniture off → `11019`/`11501`).
- **v3.1.10** (2024-06-20) The robot no longer tidies away a shelf standing in front of the charger.
- **v3.0.14** (2024-05-16) Fixed: robot could not leave from under carried furniture when a no-go area was directly in front.
- **v2.5.7** (2024-02-20) Fixed: adding "Kachaka Base" registered it as "Kachaka Shelf 3-tier".
- **v2.5.4** (2024-02-05) Fixed spurious "An old format barcode was found" speech on shelf recognition.
- **v2.3.8** (2023-11-20) Voice "片付けて" (clean up) tidies a single stray item even when no furniture is being carried.
- **v2.0** (2023-08-14) Per-furniture height: furniture can be tidied into spaces whose clearance is at least that height (under a counter or shelf).
- **v2.0** (2023-08-14) Told by voice/app to tidy a different shelf while carrying one, the robot puts the carried one down on the spot and fetches the other. Raw API commands still reject a second shelf while loaded (`10255`/`10262`/`10266`) — put the first one down yourself.
- **v2.0** (2023-08-14) More accurate placement when two furniture pieces stand side by side.
- **v1.2** (2023-07-24) Free-text furniture/destination names; items with free-text names cannot be addressed by voice command (voice recognizes only preset-catalog names).
- **v1.2** (2023-07-24) Smoother manual pushing while carrying furniture.
- **v1.1** (2023-06-20) Improved furniture-finding success rate (no figure).

### Charging & docking

- **v3.18.1** (2026-09-16) [Pro] Charging readjustment while carrying furniture: on a misaligned approach the robot backs up and retries. Turn on if docking fails; charging takes longer and needs extra space in front of the dock.
- **v3.18.1** (2026-09-16) Pose alignment during charging uses only the nearest registered dock within ~3 m of the current pose; the old jump to the layout's dock position when no dock is found is gone. A badly wrong pose is no longer corrected by docking — set it manually (`set_robot_pose()`).
- **v3.16.1** (2026-04-27) [Pro] "Auto-charging near the dock" toggle (under "Automatically return to the charging dock"): stops the robot from starting to charge just because it sits near the dock, e.g. after a power outage.
- **v3.15.2** (2026-02-24) Fixed: after being paused and pushed far from the dock, resuming sent the robot back to the dock even with auto-return disabled.
- **v3.14.4** (2025-11-12) Fixed: power interruption while charging at an added dock could send the robot to another dock.
- **v3.14.4** (2025-11-12) Fixed: retry processing at added docks did not work properly.
- **v3.9.5** (2025-02-12) [Pro] Fixed: when moving furniture the robot went to the main dock instead of the intended sub dock.
- **v3.7.6** (2024-12-17) Fixed: the main charging dock could be deleted after its location was changed.
- **v3.6.6** (2024-11-27) [Pro] Multiple charging docks (≥ 3 m apart). Automatic return always targets the original "charging dock" destination, not the nearest dock — command a move to an added dock's destination to charge there. Initial map creation and map expansion must start on the original dock; added docks show under their destination name.
- **v3.6.6** (2024-11-27) Fixed: self-position often drifted when the dock itself was misaligned.
- **v3.4.7** (2024-10-02) Fixed: a map switch reset the "automatically return to charging dock" timer to its 30 s default.
- **v3.1.10** (2024-06-20) Idle time before automatic return to the dock is configurable, separately with and without furniture.
- **v2.7.8** (2024-03-25, announced) Fixed: retrying docking from a left/right offset could hit the dock.
- **v2.6.5** (2024-03-04) Fixed: in furniture integration mode, docking started off-centre could hit the dock or walls.
- **v2.5.4** (2024-02-05) Docking stability improved.
- **v2.4.9** (2023-12-19) Docking to the dock improved in Furniture Integration Mode.
- **v2.0** (2023-08-14) Fixed: on battery depletion while carrying, furniture was always undocked toward the rear.
- **v1.2** (2023-07-24) The dock status LED turns off after the robot has been docked for a while — expected, not a fault.

### Localization

- **v3.18.1** (2026-09-16) Docking no longer snaps a far-off pose to the dock — see Charging & docking.
- **v3.17.5** (2026-07-09) [Pro] Environment Change Ignore Area: laser points inside the drawn area are excluded from localization only (still used for obstacle avoidance). Draw it around the object that changed since mapping — not the route or the point where the pose jumped. Don't remove more than ~50 % of the laser points visible from any point on the route, or drift gets worse.
- **v3.15.1** (2026-01-19) [Pro] Better localization accuracy after a map update.
- **v3.15.1** (2026-01-19) [Pro] Marker-based self-localization correction is more reliable.
- **v3.13.8** (2025-09-18) General self-localization accuracy improved (no figures).
- **v3.12.4** (2025-07-22) Fixed: pose shift after leaving the charging dock.
- **v3.10.6** (2025-03-25) Marker-based self-location correction more accurate.
- **v3.10.6** (2025-03-25) Fixed: a "Request to Kachaka" visit-all-destinations task drove to a self-location-correction marker.
- **v3.9.7** (2025-02-25) [Pro] Unspecified fix for self-location correction markers.
- **v3.9.5** (2025-02-12) [Pro] Self-location correction markers: IDs 1–50 (PDF templates `PFR_pEncode_001-050.pdf` or a rectangle variant), printed at exactly 100 %, "Preferred Robotics, Inc." label at the bottom, within 14 cm of the floor (ideally touching), white tape clear of the black areas, ~5 m apart, not in featureless corridors, never two markers with one ID. Correction happens only while driving. Map tab → + → add marker.
- **v3.8.5** (2025-01-22) Map-vs-real position correction from the app or the API (`set_robot_pose`).
- **v3.3.7** (2024-08-26) Fixed: pose not set to the dock's pose after a map switch.
- **v3.0.14** (2024-05-16) Fixed: pose shift after creating a map.

### Maps

- **v3.17.9** (2026-08-10) Handling of maps and map-linked data (furniture, destinations) stabilized.
- **v3.17.5** (2026-07-09) Fixed: the orientation of locations/furniture could shift when a map was updated. On older firmware re-check stored headings, not just positions, after "Update map".
- **v3.17.5** (2026-07-09) Fixed: map expansion/update could fail to start on large maps (no size given).
- **v3.13.4** (2025-09-03) [Pro] Edit existing maps ("Update map"): result saved as a new map, original kept; furniture/destinations/no-entry areas preserved. With added docks start from the original dock, aligned precisely (misalignment skews the layout). Updates already-scanned areas only.
- **v3.13.4** (2025-09-03) Progress bar while a newly scanned map is saved.
- **v3.9.5** (2025-02-12) "Include 3D data" at map creation: overhangs such as table/chair undersides are excluded from drivable free space, so routes avoid driving under them (all models; display toggle on the map screen).
- **v3.7.6** (2024-12-17) Map quality is checked when a map is saved during creation.
- **v3.6.7** (2024-12-04) Unspecified fixes for sensor errors and map creation.
- **v3.6.6** (2024-11-27) [Pro] Map expansion ("Extend map"): adds unscanned area keeping furniture/destinations/no-entry areas. Start precisely docked on the original dock; cannot redo already-scanned areas.
- **v3.6.6** (2024-11-27) Faster map saving; fixed saves occasionally not completing.
- **v3.4.7** (2024-10-02) Fixed: exporting a large map could fail.
- **v3.4.7** (2024-10-02) Map creation stabilized above 5000 m²; much larger maps may still not complete.
- **v3.3.8** (2024-09-11) [Pro] Export error `12117` — see the API table.
- **v3.3.7** (2024-08-26) [Pro] Switching maps made with Kachaka Pro or the "200㎡〜" setting is much faster.
- **v3.3.7** (2024-08-26) Removed the orientation-change move after reaching a specified point during map creation.
- **v3.2.6** (2024-07-31) Fixed: exporting large maps was sometimes impossible.
- **v3.2.6** (2024-07-31) Fixed: occasional freeze after Wi-Fi setup, map creation or map switching.
- **v3.1.10** (2024-06-20) Fixed: maps of large rooms could not be exported.
- **v2.7.8** (2024-03-25, announced) During map creation, pick a point on the map to send the robot there to scan.
- **v2.4.11** (2023-12-25) Fixed: selecting the "200㎡~" size in map scan caused an error.
- **v2.4.9** (2023-12-19) Better scanning performance with the "200㎡~" option.
- **v2.3.8** (2023-11-20) Creating a map from the current layout also carries over shortcuts and routines.
- **v2.2.10** (2023-10-24) Create a map reusing the current layout (furniture, destinations, no-entry areas) — no re-registration needed. Whether stored world coordinates survive is *unverified*.
- **v2.2.10** (2023-10-24) Large-space (~200 m² or more) high-precision map option.
- **v2.2.10** (2023-10-24) Known issue: robots older than 2.2 showed the wide-mode option but silently scanned with default settings.
- **v1.2** (2023-07-24) The app notifies when map creation did not capture well.

### Safety sensors, pause & emergency stop

- **v3.18.1** (2026-09-16) [Pro e-stop model] The physical emergency-stop button's state is exposed through the API as error `21057`. How it clears is *unverified*.
- **v3.10.6** (2025-03-25) Overhang obstacle detection height for body-only travel (Settings → robot → Safety features → Overhang obstacle detection): the robot won't drive under obstacles below the set height; minimum 13 cm (e.g. 45 cm for a 45 cm table).
- **v3.7.7** (2024-12-23) [Pro] Unspecified LiDAR fixes.
- **v3.5.1** (2024-10-22) Floor obstacle detection while moving improved.
- **v3.3.7** (2024-08-26) A task cancelled because the robot is paused (`10105`) is now announced aloud ("canceled during pause") instead of silently.
- **v3.2.6** (2024-07-31) Fixed: robot could start up already in the emergency-stop state.
- **v3.0.14** (2024-05-16) Smoother deceleration for obstacles, keeping cargo more stable.
- **v2.6.5** (2024-03-04) Fixed: robot kept operating during LiDAR error `21004`.
- **v2.2.11** (2023-10-31) Fixed: the overhang/protruding obstacle detection setting (せり出し障害物検知) could fail to apply.

### Speech & sound

- **v3.17.5** (2026-07-09) Arbitrary sounds via the API — see the API table.
- **v3.12.3** (2025-07-09) When bringing furniture to the current location the robot says "Loading 〇〇" — built-in speech, independent of `speak()`.
- **v3.6.6** (2024-11-27) [Pro] BGM volume ducks during voice announcements.
- **v3.5.1** (2024-10-22) Volume 0 now fully mutes (older firmware could still be audible at 0).
- **v3.4.7** (2024-10-02) BGM no longer plays during speech or Wi-Fi setup.
- **v3.3.7** (2024-08-26) [Pro] Higher maximum volume (no value published). The toolkit's `set_speaker_volume()` still clamps to 0–10.
- **v3.2.6** (2024-07-31) Fixed: the arrival message was spoken twice when the wait and message functions were combined.
- **v3.1.10** (2024-06-20) New voice "Japanese (low tone)".
- **v3.0.14** (2024-05-16) Free-form conversational requests by voice ("Request to Kachaka"), remembering preferences — an app/cloud feature, not an API RPC.
- **v2.4.11** (2023-12-25) Fixed: voice commands could stop being recognized entirely.
- **v2.4.9** (2023-12-19) "Hey Kachaka, come here" makes the robot turn to the caller and approach — unless a shortcut is bound to that phrase, which then wins.
- **v1.2** (2023-07-24) Recognition of the voice shortcut "準備して" improved.
- **v1.1** (2023-06-20) Arrival message: text the robot speaks on arrival (app: the furniture-move detail screen; from 1.1 also for schedules and shortcuts). API equivalent: the command field `tts_on_success`, which `kachaka_core` accepts on `move_to_location`, `move_to_pose`, `move_shelf`, `return_home`, `depart_from_charger`, `dock_any_shelf_with_registration`, `speak`, `localize` (and via `**kwargs` on `return_shelf` / `dock_shelf` / `undock_shelf`) — not on `start_shortcut`.
- **v1.1** (2023-06-20) Recognition of "ねぇカチャカ、片付けて" improved.

### Camera & detection

- **v3.18.1** (2026-09-16) [Pro] "Follow wall markers" turns the front torch on in dark mode to improve marker detection.
- **v2.3.8** (2023-11-20) Rear camera image via the API — see the API table.

### Network & remote access

- **v3.17.5** (2026-07-09) Longer timeout for operation requests from the smartphone app to the robot — app path only, not the gRPC API.
- **v3.11.11** (2025-05-28) The app shows "Failed to obtain IP address" when the Wi-Fi router's DHCP pool is exhausted — a join failure, distinct from gRPC disconnects after association.
- **v3.10.6** (2025-03-25) [Pro] WPA/WPA2-EAP-TLS (802.1X enterprise) Wi-Fi. Manual setup only — QR-code setup does not support it: Settings → robot → Reconfigure Wi-Fi → Skip → Other… → Security → WPA/WPA2 Enterprise; network name, username, PKCS#12 client certificate (`.p12`) plus its password, DER CA certificate (`.der`). The robot reboots when done.
- **v3.7.6** (2024-12-17) Fixed: a configured static IP sometimes did not take effect.
- **v3.2.6** (2024-07-31) Fixed an OpenSSH vulnerability that could let an unauthenticated attacker run arbitrary commands.

### Error codes & display

- **v3.14.4** (2025-11-12) Improved CPU slowdown (code `21060`).
- **v3.13.8** (2025-09-18) Fixed excessive CPU load.
- **v3.13.4** (2025-09-03) Improved CPU slowdown (code `21060`). Not among the codes named in `kachaka_core/error_codes.py` — look it up with `queries.get_error_definitions()`.
- **v2.5.7** (2024-02-20) [Pro] Fixed: LiDAR malfunctions were not displayed (a LiDAR fault could be silent on older Pro firmware).
- **v2.5.7** (2024-02-20) [Pro] Fixed false motor-error detection.

### System: updates, restarts & clock

- **v3.18.1** (2026-09-16) [Pro] Fixed: the software update could fail after the update restart when there was no internet connection.
- **v3.17.8** (2026-07-23) Fixed: JupyterLab did not start properly in Kachaka Playground (blocks the JupyterLab key-upload path on older firmware).
- **v3.16.1** (2026-04-27) [Pro] Daily auto-restart time configurable (Settings → Advanced settings; default window 04:00–04:10). That day's restart is skipped if uptime is too short or time is not synced. The app cannot connect during the restart. If the robot restarts off the dock, its map position resets to the dock — say 「ねぇカチャカ、落ち着いて」 to fix the mismatch. `/home/kachaka/kachaka_startup.sh` is the hook for launching custom API software after the restart.
- **v3.16.1** (2026-04-27) [Pro] Configurable NTP server for sites without internet (also lets the daily restart run, since it is skipped without time sync).
- **v3.4.7** (2024-10-02) Automatic software updates, on by default: download overnight over Wi-Fi, install early in the morning. Firmware — and API behaviour — can change unattended between sessions; re-read `conn.version` per session.

### Task queueing, routines & shortcuts

- **v2.2.10** (2023-10-24) Routines: a trigger (time, voice command, multi-function button) fires an action (carry furniture, speak, …). Routines issue commands independently of your API calls and may run concurrently with them.
- **v2.2.10** (2023-10-24) Activity log in the app (drive time, most-visited destinations). `queries.get_history()` returns only per-command records — compute aggregates yourself.
- **v2.1** (2023-09-11) App "Queue function" (Settings → Kachaka settings → Detailed settings): tasks issued from the app while busy queue ("after the current task" / "after all tasks"). Per the Japanese note it governs only app-issued destinations/shortcuts; Kachaka Button commands may queue regardless. API-level knobs are `deferrable` / `cancel_all`; whether this toggle affects raw API commands is *unverified*.

---

## App settings & Pro features timeline

Medium-relevance items that matter when advising a user on site. None of these
are readable over the public API unless stated.

- **v3.18.1** (2026-09-16) [Pro] Route lists sortable by registration order or by departure/arrival location; route waypoints can be given as coordinates.
- **v3.17.5** (2026-07-09) Map-screen detection overlay: laser points (red), protruding obstacles (blue, only while carrying furniture), floor obstacles (green). Each toggle works only while the matching safety feature is on; nothing shows right after startup, while docked, or after the robot has stood still a while.
- **v3.16.1** (2026-04-27) [Pro] Change the departure location used for an inter-location route from the destination-selection screens (API analogue: `move_to_location(source_location_name=…)`).
- **v3.11.11** (2025-05-28) [Pro] Several actions in one routine (one trigger → move + task, or several destinations). Multi-action routines cannot be shown or run via remote operation.
- **v3.11.11** (2025-05-28) "Load furniture" routine action (app ≥ 2.23.5; Kachaka Button Hub ≥ 1.5.1 with front/rear-facing choice).
- **v3.10.6** (2025-03-25) [Pro] Patrol lamp display (Settings → robot → Advanced settings): LED ring rotates green while driving (default: solid white), blinks green when stopped or charging, blinks yellow on low battery; pause (solid yellow) and errors unchanged.
- **v3.10.6** (2025-03-25) "LAN-only use" and "remote operation" merged into a per-robot "Connection mode" (Settings → robot → Connection mode); each robot gets its own connection settings.
- **v3.10.6** (2025-03-25) [Pro] Local connection mode can auto-connect via mDNS (`kachaka-<SERIAL>.local`); if that fails (some Android/network setups) enter the IP by hand — ask the robot "Hey Kachaka, what's your IP address?".
- **v3.8.5** (2025-01-22) [Pro] Driving music (Settings → robot → Kachaka volume → Play music while driving): 6 presets plus custom songs, ≤ 16 MB each, mp3/wav/aac/flac/m4a/ogg/aiff/wma/opus/mp2/ac3/amr/dts/pcm/ape; plays only while driving. Separate from the API Sound API.
- **v3.7.6** (2024-12-17) [Pro] Shortcuts can be registered through "Ask Kachaka"; they run via `start_shortcut` like any other.
- **v3.6.6** (2024-11-27) [Pro] Static IP: Settings → robot → Wi-Fi re-setup → Skip → Advanced settings → IPv4 Configuration → Manual (IP, subnet mask, router). For fixed-IP policies, no DHCP, or "DHCP server not responding".
- **v3.6.6** (2024-11-27) Routines can be edited in remote operation mode.
- **v3.4.7** (2024-10-02) Per-furniture "soft start" for heavy loads (SKILL.md: `list_shelves()[…]["speed_mode"]`).
- **v3.4.7** (2024-10-02) Furniture setting "reduce the maximum speed when carrying furniture" — off = same speed loaded or empty.
- **v3.1.10** (2024-06-20) Waiting time after arrival before the robot takes other tasks.
- **v3.1.10** (2024-06-20) [Pro] Cargo size (width, depth, height) on the Kachaka base, so cargo protruding from the base is not treated as an obstacle.
- **v3.0.14** (2024-05-16) [Pro] Remote support: lets the vendor's support team connect to the robot once enabled.
- **v2.1** (2023-09-11) The reaction sound to "ねぇカチャカ" can be turned off.
- **v2.1** (2023-09-11) Remote control can run shortcuts and shows the robot's network status.
- **v2.0** (2023-08-14) Remote control from outside the robot's Wi-Fi (app ≥ 2.0, software ≥ 2.0.8) through the vendor's relay — unrelated to the toolkit's Tailscale recipe. No live map position and no error-cause notifications while remote. Don't use unattended near open flames, fragile or adhesive-mounted items, or steps/slopes. To disable: app ≤ 2.21 Settings → Other settings → Remote control (Beta); app ≥ 2.22 Settings → robot → Connection mode.
- **v1.1** (2023-06-20) Voice shortcuts expanded from 4 to 19 types (おはよう, いってきます, …); enumerate with `list_shortcuts()`.

---

## Full per-version digest

Every item from every release note, newest first, including low-relevance ones —
except items that only concern Kachaka Fleet Manager integration, which are
out of scope for this toolkit.

### v3.18.1 — 2026-09-16 · [article](https://kachaka.zendesk.com/hc/en-us/articles/17622353016719-Kachaka-Software-Update-Ver-3-18-1)

- [Pro] "Stop and turn on a straight path" for location-to-location routes — no more weaving on straight segments.
- [Pro] Charging readjustment while carrying furniture: back up and retry on a misaligned approach; slower charging, needs space in front of the dock.
- Pose alignment during charging only against the nearest registered dock within ~3 m; no fallback jump to the layout dock position.
- Traditional Chinese in the robot's error display.
- [Pro] Fixed update failure after the update restart without internet.
- End-of-move rotation removed for routine forward moves, distance moves with sensors off, and `MoveForwardCommand`.
- [Pro] Route lists sortable (registration order / departure / arrival); waypoints by coordinates.
- [Pro] "Follow wall markers" turns the torch on in dark mode.
- New API command `DepartFromChargerCommand` (leave the dock by moving slightly forward).
- [Pro e-stop model] Emergency-stop button state via the API as error `21057`.

### v3.17.10 — 2026-09-08 · [article](https://kachaka.zendesk.com/hc/en-us/articles/17531993516431-Kachaka-Software-Update-Notice-Ver-3-17-10)

- Fixed: first move after a restart with furniture loaded could fail.

### v3.17.9 — 2026-08-10 · [article](https://kachaka.zendesk.com/hc/en-us/articles/17162658085775-Kachaka-Software-Update-Notice-Ver-3-17-9)

- Stabilized handling of maps and map-linked data (furniture, destinations).

### v3.17.8 — 2026-07-23 · [article](https://kachaka.zendesk.com/hc/en-us/articles/16942356077583-Kachaka-Software-Update-Notice-Ver-3-17-8)

- Fixed "Readjust When Stopped" not working with "Align with Wall Marker".
- Fixed JupyterLab not starting properly in Kachaka Playground.

### v3.17.5 — 2026-07-09 · [article](https://kachaka.zendesk.com/hc/en-us/articles/16759041518351-Kachaka-Software-Update-Ver-3-17-5)

- Map-screen overlay of detected floor obstacles, protruding obstacles and laser points.
- [Pro] Environment Change Ignore Area (laser points excluded from localization only).
- Traditional Chinese in the smartphone app (follows the phone's OS language).
- More map-screen layers: routes between locations, speed zones, slope areas, self-position markers (and route/driving-line display on destination screens).
- Fixed: layout orientation (locations, furniture) could shift on map update.
- Fixed: map expansion/update could fail to start on large maps.
- [Pro] Route between locations more likely kept when a move is cancelled partway.
- Register and play arbitrary sounds via the API.
- Fixed: `move_forward` without a speed errored.
- Longer timeout for app→robot operation requests.
- Reworded the "Blinking LED while charging" setting description (no functional change).
- [Pro] Destination toggle for stop readjustment with "Align with Wall Marker" — unusable in 3.17.5 (fixed in 3.17.8 per the Japanese note).

### v3.16.2 — 2026-06-01 · [article](https://kachaka.zendesk.com/hc/en-us/articles/16262729631119-Kachaka-Software-Update-Notice-Ver-3-16-2)

- [Pro] Some unnamed beta features disabled for stability.
- [Pro] Retry behaviour of marker-based wall alignment adjusted.

### v3.16.1 — 2026-04-27 · [article](https://kachaka.zendesk.com/hc/en-us/articles/15878280501903-Kachaka-Software-Update-Ver-3-16-1)

- [Pro] Routine move-forward/backward actions with obstacle sensors muted; not run while on the dock.
- [Pro] "Auto-charging near the dock" toggle.
- [Pro] Configurable daily auto-restart time (default 04:00–04:10).
- [Pro] Configurable NTP server.
- Undock clearance based on shelf size, even with limited sensor visibility.
- Setting to hide the first-launch software update notification.
- [Pro] Faster rendering of "Preview routes on the map".
- [Pro] Change the departure location of an inter-location route from destination-selection screens.

### v3.15.4 — 2026-03-10 · [article](https://kachaka.zendesk.com/hc/en-us/articles/15412088081935-Kachaka-Software-Update-Notice-Ver-3-15-4)

- Fleet Manager integration fixes only (omitted).

### v3.15.2 — 2026-02-24 · [article](https://kachaka.zendesk.com/hc/en-us/articles/15273057925263-Kachaka-Software-Update-Notice-Ver-3-15-2)

- Fixed: resume after pause-and-relocate returned to the dock even with auto-return off.

### v3.15.1 — 2026-01-19 · [article](https://kachaka.zendesk.com/hc/en-us/articles/14922607857551-Kachaka-Software-Update-Notice-Ver-3-15-1)

- Fixed handling of twisted no-entry areas.
- [Pro] Better localization accuracy after a map update.
- [Pro] Fixed wrong-direction driving when blocked on an inter-point route.
- [Pro] Marker-based self-localization correction more reliable.

### v3.14.7 — 2025-12-16 · [article](https://kachaka.zendesk.com/hc/en-us/articles/14629480585487-Kachaka-Software-Update-Notice-Ver-3-14-7)

- Minor compatibility-related changes (no details).

### v3.14.4 — 2025-11-12 · [article](https://kachaka.zendesk.com/hc/en-us/articles/14308754194831-Kachaka-Software-Update-Notice-Ver-3-14-4)

- [Pro] Beta: "Travel along inter-point routes for all movements".
- [KachakaAPI] Import map data from a map image (the note says it generates a map for Kachaka Pro; not tagged [Pro]).
- Fixed: power interruption while charging at an added dock could send the robot to another dock.
- Fixed: retry processing at added docks.
- Fixed: robot could stop without turning on an inter-point route.
- Improved CPU slowdown (code `21060`).

### v3.13.9 — 2025-10-06 · [article](https://kachaka.zendesk.com/hc/en-us/articles/13960970374415-Kachaka-Software-Update-Notice-Ver-3-13-9)

- Minor compatibility-related changes (no details).

### v3.13.8 — 2025-09-18 · [article](https://kachaka.zendesk.com/hc/en-us/articles/13804475545743-Kachaka-Software-Update-Notice-Ver-3-13-8)

- Fixed excessive CPU load.
- Improved self-localization accuracy.

### v3.13.4 — 2025-09-03 (Japanese page: 2025-09-04) · [article](https://kachaka.zendesk.com/hc/en-us/articles/13669155253263-Kachaka-Software-Update-Notice-Ver-3-13-4)

- [Pro] Edit existing maps ("Update map"), saved as a new map.
- Progress bar while saving a new map.
- More intuitive setup of multiple routes between locations.
- Fixed U-turns near specified routes.
- Fixed unstable furniture docking under certain conditions.
- Improved CPU slowdown (code `21060`).

### v3.12.4 — 2025-07-22 · [article](https://kachaka.zendesk.com/hc/en-us/articles/13302083481999-Kachaka-Software-Update-Notice-Ver-3-12-4)

- Fixed pose shift after leaving the charging dock.

### v3.12.3 — 2025-07-09 · [article](https://kachaka.zendesk.com/hc/en-us/articles/13074304042383-Kachaka-Software-Update-Notice-Ver-3-12-3)

- Fixed: routine task titles ("Load furniture", "Wait") not shown as the active task in the app.
- Robot says "Loading 〇〇" when bringing furniture to the current location.
- Fixed: route specification disabled "Skip position and orientation adjustment".

### v3.11.13 — 2025-06-16 · [article](https://kachaka.zendesk.com/hc/en-us/articles/12999357221519-Kachaka-Software-Update-Notice-Ver-3-11-13)

- [Pro] Unspecified fix for routes between locations.

### v3.11.11 — 2025-05-28 · [article](https://kachaka.zendesk.com/hc/en-us/articles/12840503105423-Kachaka-Software-Update-Notice-Ver-3-11-11)

- [Pro] Multiple actions per routine (not shown/run via remote operation).
- "Load furniture at the destination" available as an app routine action (previously API/button only).
- [Pro] Fixed travel route not used when picking up furniture (furniture must be near its home).
- "Failed to obtain IP address" notification on DHCP pool exhaustion.
- `IsReady` returns preparation statuses such as map switching.
- Furniture loading stability improved.

### v3.10.7 — 2025-04-09 · [article](https://kachaka.zendesk.com/hc/en-us/articles/12398770690703-Kachaka-Software-Update-Notice-Ver-3-10-7)

- [Pro] Unspecified fix for designated driving lines (走行指定ライン).

### v3.10.6 — 2025-03-25 · [article](https://kachaka.zendesk.com/hc/en-us/articles/12264557130895-Kachaka-Software-Update-Notice-Ver-3-10-6)

- [Pro] Slope areas.
- [Pro] WPA/WPA2-EAP-TLS enterprise Wi-Fi.
- [Pro] Patrol lamp LED mode.
- "LAN-only use" and "remote operation" unified into connection modes.
- [Pro] mDNS auto-connect in local connection mode.
- Per-robot connection settings.
- Configurable overhang obstacle detection height for body-only travel (minimum 13 cm).
- Improved stopping accuracy with floor markers (English note only).
- Wall-marker stops align to the destination furniture's orientation.
- Fixed wall collision when restarting after a wall-marker stop.
- Route planning considers all configured shared routes.
- Fixed spurious move to a self-location-correction marker in a "Request to Kachaka" visit-all task.
- "Request to Kachaka" can create multiple schedules and shortcuts.
- Improved accuracy of marker-based self-location correction.
- Fixed giving up on long-distance point-to-point routes.
- Fixed delayed speed-zone application.
- Smoother acceleration when starting from a wall-marker stop.
- `DockAnyShelfWithRegistrationCommand` shown in history.

### v3.9.7 — 2025-02-25 · [article](https://kachaka.zendesk.com/hc/en-us/articles/12025013188623-Kachaka-Software-Update-Notice-Ver-3-9-7)

- [Pro] Unspecified fix for designated routes.
- [Pro] Unspecified fix for self-location correction markers.

### v3.9.5 — 2025-02-12 · [article](https://kachaka.zendesk.com/hc/en-us/articles/11917387832335-Kachaka-Software-Update-Notice-Ver-3-9-5)

- [Pro] Self-location correction markers.
- Route planning with 3D map data ("Include 3D data").
- [Pro] Configurable upper time limit for reaching a destination (default 5 min, 10–1800 s).
- Laser point-cloud display in the app (display only, no effect on operation).
- [Pro] Multiple schedules in one "Request to Kachaka".
- [Pro] Fixed: main dock chosen instead of the sub dock when moving furniture.

### v3.8.5 — 2025-01-22 · [article](https://kachaka.zendesk.com/hc/en-us/articles/11720044528143-Kachaka-Software-Update-Notice-Ver-3-8-5)

- [Pro] Custom driving music (16 MB per song, many formats).
- [Pro] Speed zones.
- [Pro] Option to disable obstacle detours on designated routes.
- Correct map-vs-real position from the app or API.
- [Pro] Fixed "Request to Kachaka" schedule times drifting.
- [Pro] Fixed designated route not switching on map change.

### v3.7.7 — 2024-12-23 · [article](https://kachaka.zendesk.com/hc/en-us/articles/11567883950351-Kachaka-Software-Update-Notice-Ver-3-7-7)

- [Pro] Unspecified LiDAR fixes.

### v3.7.6 — 2024-12-17 · [article](https://kachaka.zendesk.com/hc/en-us/articles/11475888148239-Kachaka-Software-Update-Notice-Ver-3-7-6)

- [Pro] Register shortcuts via "Ask Kachaka".
- `MoveForwardCommand` movement speed parameter.
- Fixed inaccurate "after XX minutes" timing in "Request to Kachaka".
- Map quality check when saving during map creation.
- Fixed: main charging dock could be deleted after moving it.
- Fixed: static IP sometimes not applied.

### v3.6.7 — 2024-12-04 · [article](https://kachaka.zendesk.com/hc/en-us/articles/11408408195983-Kachaka-Software-Update-Notice-Ver-3-6-7)

- Unspecified fixes for sensor errors and map creation.

### v3.6.6 — 2024-11-27 · [article](https://kachaka.zendesk.com/hc/en-us/articles/11236508864911-Kachaka-Software-Update-Notice-Ver-3-6-6)

- [Pro] Route specification (designated lines, point-to-point routes).
- [Pro] Multiple charging docks.
- [Pro] High-precision arrival with wall markers (only while carrying furniture).
- [Pro] Static IP.
- [Pro] Map expansion keeping existing settings.
- [Pro] BGM ducks during announcements.
- [Pro] Max furniture cargo width 65 cm.
- Routine editing in remote operation mode.
- Fixed pose drift when the dock was misaligned.
- Faster map saving; fixed saves not completing.

### v3.5.1 — 2024-10-22 · [article](https://kachaka.zendesk.com/hc/en-us/articles/11002585671311-Kachaka-Software-Update-Notice-Ver-3-5-1)

- Volume 0 fully mutes.
- Improved floor obstacle detection while moving.

### v3.4.8 — 2024-10-10 · [article](https://kachaka.zendesk.com/hc/en-us/articles/10939556380943-Kachaka-Software-Update-Notice-Ver-3-4-8)

- [Pro] Fixed frequent movement failure after deviating from a fixed route (3.4.7 regression).

### v3.4.7 — 2024-10-02 · [article](https://kachaka.zendesk.com/hc/en-us/articles/10828687047823-Kachaka-Software-Update-Notice-Ver-3-4-7)

- Automatic software updates (default on).
- [Pro] New API `DockAnyShelfWithRegistration`.
- Furniture "soft start" setting.
- Furniture setting to reduce max speed while carrying.
- Map creation by loading and pushing furniture by hand while scanning.
- Fixed large map export sometimes failing.
- Fixed: next queued shortcut task title not shown.
- Fixed: map switch reset the auto-return timer to 30 s.
- [Pro] API option to keep docking state across map switch.
- BGM no longer plays during speech or Wi-Fi setup.
- Map creation stabilized above 5000 m².

### v3.3.8 — 2024-09-11 · [article](https://kachaka.zendesk.com/hc/en-us/articles/10714600834703-Kachaka-Software-Update-Notice-Ver-3-3-8)

- [Pro] Fixed frequent map export failure `12117` (can still occur while moving — dock first).

### v3.3.7 — 2024-08-26 · [article](https://kachaka.zendesk.com/hc/en-us/articles/10509052765327-Kachaka-Software-Update-Notice-Ver-3-3-7)

- [Pro] Higher maximum volume.
- [Pro] Random playback of driving music.
- [Pro] Much faster switching of Pro / "200㎡〜" maps.
- Cancellation during pause now announced.
- Fixed continuous undocking on map switch with 2+ furniture and "Keep furniture".
- Fixed pose not set to the dock pose after a map switch.
- Removed orientation-change move after reaching a point during map creation.

### v3.2.6 — 2024-07-31 · [article](https://kachaka.zendesk.com/hc/en-us/articles/10274971160975-Kachaka-Software-Update-Notice-Ver-3-2-6)

- Furniture integration mode split into "Charge While Holding Furniture" / "Keep Holding Furniture".
- Destination name suggestions 診察室 / 問診室 / リハビリ室.
- Faster shutdown.
- Fixed large map export sometimes impossible.
- Fixed occasional freeze after Wi-Fi setup, map creation or map switching.
- Fixed duplicate arrival message with wait + message functions.
- Fixed startup in emergency-stop state.
- Fixed OpenSSH vulnerability (unauthenticated command execution).

### v3.1.10 — 2024-06-20 · [article](https://kachaka.zendesk.com/hc/en-us/articles/9959851009551-Kachaka-Software-Update-Notice-Ver-3-1-10)

- New voice "Japanese (low tone)".
- Configurable waiting time after arrival.
- [Pro] Cargo size on the Kachaka base.
- Configurable idle time before auto-return to the dock (with / without furniture).
- New APIs: errors, restart, emergency stop; initial pose on map switch.
- Fixed destination position edits rarely not reflected.
- Fixed Kachaka API not starting (Kachaka Button unresponsive).
- Fixed export of large-room maps.
- Robot no longer tidies the shelf in front of the charger.

### v3.0.14 — 2024-05-16 · [article](https://kachaka.zendesk.com/hc/en-us/articles/9738402154639-Kachaka-Software-Update-Notice-Ver-3-0-14)

- Free-form conversational voice requests.
- New APIs: battery, ToF image, map switching, front/back LED.
- `StartCommand` `deferrable` option.
- [Pro] Configurable driving routes.
- [Pro] Remote support.
- Fixed pose shift after map creation.
- Fixed being unable to leave from under furniture with a no-go area ahead.
- Fixed intermittent `kachaka-<serial>.local` connection failure via Hub/API.
- Smoother obstacle deceleration.

### v2.7.8 — 2024-03-25 (announced; the source's implementation date reads 2023-04-03, an apparent typo) · [article](https://kachaka.zendesk.com/hc/en-us/articles/9366730970383-Kachaka-Software-Update-Notice-Ver-2-7-8)

- Pick a point on the map to scan during map creation.
- 20+ clinic/dental destination names (受付, 会計, ユニット…) with category selection.
- Fixed dock collision when retrying from a left/right offset.
- New APIs `GetMovingShelfId` and `ResetShelfPose`.
- Better behaviour under high CPU load.

### v2.6.5 — 2024-03-04 · [article](https://kachaka.zendesk.com/hc/en-us/articles/9121013230095-Kachaka-Software-Update-Notice-Ver-2-6-5)

- Kachaka Button support.
- Regional settings change the robot's clock time.
- Destinations/tidy-up locations registrable inside enterable areas.
- Fixed slow start to the next destination after arriving.
- Fixed dock/wall collision in furniture integration mode from an off-centre start.
- Fixed robot continuing to operate during LiDAR error `21004`.
- `kachaka-<serial>.local` hostname usable for kachaka-api.

### v2.5.7 — 2024-02-20 · [article](https://kachaka.zendesk.com/hc/en-us/articles/9064343836175)

- Fixed "Kachaka Base" registered as "Kachaka Shelf 3-tier".
- [Pro] Fixed LiDAR malfunctions not displayed.
- [Pro] Fixed false motor-error detection.

### v2.5.4 — 2024-02-05 · [article](https://kachaka.zendesk.com/hc/en-us/articles/8852056107919)

- Map import/export via the API (incl. furniture and destinations).
- Setting: LED ring off while on the dock (default: off).
- Fixed spurious "An old format barcode was found" speech.
- Time synchronization changed to reduce clock-sync failures.
- Docking stability improved.

### v2.4.11 — 2023-12-25 · [article](https://kachaka.zendesk.com/hc/en-us/articles/8671252953103)

- Fixed voice commands not recognized at all.
- Fixed error when selecting the "200㎡~" map-scan size.

### v2.4.9 — 2023-12-19 · [article](https://kachaka.zendesk.com/hc/en-us/articles/8567343676815)

- "Hey Kachaka, come here" approaches the caller (a bound shortcut takes priority).
- Better scanning performance with "200㎡~".
- Better docking in Furniture Integration Mode.
- Destination names "Reception" and "Accounting" added.

### v2.3.8 — 2023-11-20 · [article](https://kachaka.zendesk.com/hc/en-us/articles/8380707825679)

- Software support for the upcoming "New Smart Furniture" (no details).
- Detour when the direct route is blocked.
- Rear camera image via the API.
- "Clean up" tidies a single stray item even when not carrying furniture.
- Map creation from the current layout carries over shortcuts and routines.

### v2.2.11 — 2023-10-31 · [article](https://kachaka.zendesk.com/hc/en-us/articles/8256522273423)

- Fixed overhang/protruding obstacle detection setting sometimes not applied (English body mistypes the version as 2.2.1).

### v2.2.10 — 2023-10-24 · [article](https://kachaka.zendesk.com/hc/en-us/articles/8103076192399-Kachaka-Software-Update-Notice-Ver-2-2-10)

- Routines (time / voice / multi-function button triggers).
- Activity log.
- Create a map reusing the current layout.
- Voice commands "筋トレ" and "こっちに来て".
- Furniture names ガーゼ, ミルク, おむつ, おしりふき.
- Destination name ベッド.
- Large-space (~200 m²+) high-precision map option.
- General voice recognition improved.
- Known issue: empty title in app history for "<furniture>を片付けて" (fixed by app 2.4.13).
- Known issue: keyboard hides the arrival-message field on the schedule editor.
- Known issue: pre-2.2 robots show the wide-mode scan option but scan with defaults.

### v2.1 — 2023-09-11 · [article](https://kachaka.zendesk.com/hc/en-us/articles/7867625135119-Kachaka-Software-Update-Notice-Ver-2-1)

- Queue function for app-issued tasks.
- Remote control: shortcuts + network status display.
- Smoother turns.
- Setting to skip fine position/orientation adjustment on arrival.
- Wake-word reaction sound can be turned off.

### Terms of Use update — 2023-08-14 · [article](https://kachaka.zendesk.com/hc/en-us/articles/7637027379471--Important-August-14-Terms-of-Use-Privacy-Policy-Revision-and-Kachaka-API-Additional-Notice)

- Kachaka API Terms of Use added (Japan-only eligibility, no support obligation, no data guarantee).
- Kachaka Terms of Use and Privacy Policy revised for remote control and the API launch.

### v2.0 — 2023-08-14 · [article](https://kachaka.zendesk.com/hc/en-us/articles/7613100969743-Kachaka-Software-Update-Notice-Ver-2-0)

- Remote control from outside the robot's Wi-Fi.
- Kachaka API launched (software ≥ 2.0.8).
- Per-furniture height for tidying under low clearances.
- Fewer false "ねぇカチャカ" wake-word triggers.
- Fixed tidying a different shelf while carrying one (now puts the carried one down first).
- Robot can enter an enterable area with no map data.
- More accurate placement of side-by-side furniture.
- Smoother movement around frequently moved objects (chairs with casters).
- Fixed furniture always undocked rearward on battery depletion.

### v1.2 — 2023-07-24 · [article](https://kachaka.zendesk.com/hc/en-us/articles/7408077565583-Kachaka-Software-Update-Notice-Ver-1-2)

- Free-text furniture/destination names (not usable by voice command).
- "準備して" voice shortcut recognition improved.
- Dock LED turns off after docking for a while.
- Notification when map creation went badly.
- Smoother manual pushing while carrying furniture.

### v1.1 — 2023-06-20 · [article](https://kachaka.zendesk.com/hc/en-us/articles/7196814203535)

- Arrival message spoken on arrival (also for schedules and shortcuts from 1.1).
- Voice shortcuts expanded from 4 to 19 types.
- Furniture-finding success rate improved.
- "ねぇカチャカ" wake-word recognition improved.
- "ねぇカチャカ、片付けて" recognition improved.

### v1.0 — 2023-05-17 · [article](https://kachaka.zendesk.com/hc/en-us/articles/6941250242831)

- Initial software release 1.0.16 (Japan only; app on Android 5.0+ / iOS 14.0+).

---

## Open questions — not yet verified on a real robot

The release notes state vendor intent, not API-observable behaviour. These
gaps were found while cross-checking the notes against this toolkit; answer
them with HIL tests (`tests/hil/`) before promoting any of them into SKILL.md
as fact.

- On firmware 3.17.5+ (e.g. BKP40HD1T on 3.17.8), does MoveForwardCommand with speed=0.0 still return 15508, silently no-op, or move at a default speed?
- What does firmware < 3.18.1 return for DepartFromChargerCommand: an error code (which one) or silent acceptance?
- Pro e-stop model on 3.18.1: does pressing the physical button put 21057 into get_errors(), does it coexist with 21051, and does it clear on button release or need restart_robot()?
- 3.18.1 dock alignment: confirm the ~3 m boundary — after return_home() or a manual push onto the dock with the estimated pose deliberately more than 3 m off, does get_pose() stay wrong?
- 3.18.1 removed end-of-move rotation: how large was the trailing rotation before 3.18.1 (get_pose() theta before/after move_forward on 3.17.8), and is it absent on 3.18.1?
- 3.16.1 sensor-muted routine moves are not run on the dock: does the same exclusion apply to API move_forward(mute_sensors=True) / move_by_velocity_muted, and with what result code?
- Firmware < 3.17.10: which error code does the first move after restart_robot() with a shelf docked return, and does one retry reliably succeed?
- 3.11.11 IsReady preparation states: what does the raw IsReady RPC return during switch_map, and how long until move commands are accepted again?
- tts_on_success: is it spoken on shelf-carry completions (move_shelf, dock_any_shelf_with_registration) and silent on failure or cancel?
- 2.2.10 / 2.3.8 create-from-current-layout (app 'Add map → Keep current settings'): does it preserve the world frame and location IDs, and does it mint a new map UUID?
- Pro volume ceiling since 3.3.7: what maximum does set_speaker_volume accept via the raw SDK on a Pro robot, and does it return 13302 above that?
- import_image_as_map and move_to_location(source_location_name=…) on a standard (non-Pro) robot: rejected with an error, or silently accepted and ignored?
- Does KachakaConnection.get('kachaka-<serial>.local') work (gRPC over an mDNS hostname) from a typical host on the robot's LAN?
- Does the app's Queue function toggle (2.1) affect commands issued over the gRPC API, or only app-issued tasks?
- Do commands fired by app Routines, shortcuts or the Kachaka Button cancel an in-flight API command with 10001, and are they visible in get_history()?
- Does volume 0 (3.5.1 'fully muted') also silence custom Sound API playback (play_sound)?
- Daily auto-restart and overnight auto-update: how long is gRPC :26400 unreachable, do toolkit monitoring and reconnect recover without intervention, and what pose is reported after a restart that happened off the dock?
- 3.18.1 wall-marker torch: does a caller's set_front_torch() during a wall-marker approach get overridden, or does it override the firmware?
- Which error codes correspond to the Pro 'motor error' fixed in 2.5.7, and what error_type does get_error_definitions() give 21060?
- What does the API return when furniture wider than the 65 cm Pro limit is docked (3.6.6), or when the 3.7.6 map quality check rejects a scan?
