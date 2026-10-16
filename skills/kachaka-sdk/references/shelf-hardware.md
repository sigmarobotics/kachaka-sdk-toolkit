# Shelf hardware — カチャカベース (Kachaka Base) S4A01 / S4A02

Source: Preferred Robotics「組立・取扱説明書 カチャカベース S4A01/S4A02」(3 pages, 2026-09 copy).
Numbers below are the manufacturer's; the LiDAR section is a field observation.

## Dimensions (manufacturer spec)

| Item | Value |
|---|---|
| Outer size | **W 393 × D 320 × H 141 mm** |
| Top plate | aluminium |
| Frame / pipes | steel |
| Casters | 4, resin / metal / elastomer, one per corner |
| Docking frame | carries the **barcode** the robot reads to recognise the shelf — do not peel it |
| Required firmware | Ver. 2.3 or later |

Geometry that follows from the spec (not separately measured): the four caster stems sit
at the corners, i.e. about **±0.197 m lateral × ±0.16 m longitudinal** from the shelf
centre. Casters swivel, so the wheel itself wanders a few cm around the stem.

## What the LiDAR sees with a shelf docked (field observation, 2026-10-06)

pro-2 (BKP40HD1T, fw 3.18.1), shelf S06 placed on the robot **by hand** at the charger,
`get_moving_shelf_id()` reporting it docked, robot stationary:

- **Nothing within 0.29 m** of the LiDAR anywhere in its 260° field of view. The front
  caster stems should be at roughly bearing ±50°, 0.25 m — they were not in the scan.
  Either the scan plane misses the stems or the firmware masks the docked shelf; a
  second measurement with the robot docked by `dock_shelf()` (canonical pose) is needed
  before relying on it.
- The shelf is **not rigidly coupled** to the robot (it rests on the catch): floor
  unevenness or a few degrees of yaw show up as a left/right-asymmetric near field.
  Any near-field filter for a carried shelf should therefore be **learned at runtime**
  (snapshot the near-field profile per ray after docking) rather than hard-coded from
  these dimensions.
- The person-following prototype in `sigma-button-offline` (`scripts/follow_probe/`)
  keeps its measurement script as `probe_l_shelf_scan.py`.
