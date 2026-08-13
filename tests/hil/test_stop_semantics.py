"""HIL — what each "stop" actually does to a moving robot.

    pytest tests/hil/test_stop_semantics.py -v --robot-ip=192.168.50.133 --hil-motion

The driving tests require ``--hil-motion``: the robot **drives ~3 m** during
them, unlike the rest of tests/hil/ which is stationary. Run them with the
robot in open floor space and a human watching.

The emergency-stop test is opt-in on top of that (``--ems-latch``) because it
latches the robot: the only way back is walking over and pressing the physical
power button. Never put it in an unattended run.

Baseline established 2026-08-13 on BKP40HD1T, firmware 3.17.8.
"""

from __future__ import annotations

import math
import time

import pytest

from kachaka_core.commands import KachakaCommands
from kachaka_core.queries import KachakaQueries

pytestmark = pytest.mark.hil

TARGET = "倉庫"          # ~3.07 m from the charging dock on the 長照展 map
HOME = "充電ドック"
DWELL = 2.5              # let it get properly under way before intervening
OBSERVE = 6.0


def _pose(q):
    p = q.get_pose()
    return p["x"], p["y"]


def _dist(a, b):
    return math.hypot(a[0] - b[0], a[1] - b[1])


@pytest.fixture
def parked(real_conn):
    """Robot back on the dock before and after each test."""
    cmds = KachakaCommands(real_conn)
    cmds.move_to_location(HOME)
    cmds.poll_until_complete(timeout=120.0)
    yield cmds
    cmds.cancel_command()
    cmds.move_to_location(HOME)
    cmds.poll_until_complete(timeout=120.0)


def _drive_then(cmds, q, intervene):
    """Start a move, intervene mid-flight, report how far it kept going."""
    cmds.move_to_location(TARGET)
    time.sleep(DWELL)
    assert q.get_command_state()["is_running"], "robot never started moving"

    at_intervention = _pose(q)
    intervene()
    time.sleep(OBSERVE)

    return _dist(at_intervention, _pose(q)), q.get_last_command_result()


motion = pytest.mark.skipif(
    "not config.getoption('--hil-motion')",
    reason="drives the robot several metres; needs --hil-motion",
)


@motion
def test_cancel_command_actually_stops(parked, real_conn):
    """cancel_command is the one that works: command ends 10001, robot halts."""
    q = KachakaQueries(real_conn)

    travelled, result = _drive_then(parked, q, parked.cancel_command)

    assert result["success"] is False
    assert result["error_code"] == 10001, "expected 'interrupted'"
    assert not q.get_command_state()["is_running"]
    # Measured 0.11 m of coast-down; allow generous slack for floor/battery.
    assert travelled < 0.5, f"kept moving {travelled:.2f} m after cancel"


@motion
def test_stop_manual_drive_does_not_stop_navigation(parked, real_conn):
    """The documented non-behaviour — this is why `stop()` was renamed.

    Locking it in as a test: if firmware ever makes stop_manual_drive abort
    navigation, this fails and the docs and tool descriptions need revisiting.
    """
    q = KachakaQueries(real_conn)

    travelled, _ = _drive_then(parked, q, parked.stop_manual_drive)

    assert travelled > 0.3, (
        f"stop_manual_drive halted navigation after {travelled:.2f} m — "
        "firmware behaviour changed, update SKILL.md 'Stopping the Robot'"
    )
    # The move is usually still in flight after OBSERVE seconds — wait for it
    # to actually finish before asserting it completed normally, otherwise the
    # read returns the parked fixture's earlier (successful) move.
    deadline = time.time() + 90.0
    while time.time() < deadline and q.get_command_state()["is_running"]:
        time.sleep(1.0)
    result = q.get_last_command_result()
    assert result["success"] is True, "the move should have completed normally"


def test_is_ready_healthy_baseline(real_conn):
    """is_ready must agree with get_errors on a healthy robot (no latch here;
    the latched-path assertions live in the --ems-latch test below)."""
    q = KachakaQueries(real_conn)
    ready = q.is_ready()

    if q.get_errors()["errors"]:
        pytest.skip("robot has active errors; run this on a clean robot")
    assert ready["ready"] is True
    assert ready["recovery_hint"] is None


@pytest.mark.skipif(
    "not config.getoption('--ems-latch')",
    reason="needs --ems-latch and a human able to press the power button",
)
def test_emergency_stop_latches_and_no_software_release(real_conn):
    """Every software release path fails; only the physical button clears it.

    Leaves the robot latched on purpose — the operator must press the power
    button afterwards. That is the point of the test.
    """
    q, cmds = KachakaQueries(real_conn), KachakaCommands(real_conn)
    assert not q.get_errors()["errors"], "start from a clean robot"

    assert cmds.set_emergency_stop()["ok"] is True

    deadline = time.time() + 10.0
    while time.time() < deadline and not q.get_errors()["errors"]:
        time.sleep(0.25)
    assert 21051 in q.get_errors()["errors"], "expected latched-pause 21051"

    for attempt in (
        cmds.cancel_command,
        cmds.proceed,
        lambda: cmds.set_manual_control(True),
        lambda: cmds.set_manual_control(False),
        cmds.stop_manual_drive,
    ):
        attempt()
        time.sleep(2.0)
        assert 21051 in q.get_errors()["errors"], (
            f"{attempt} cleared the latch — a software release path now exists, "
            "revisit the decision to keep set_emergency_stop out of MCP"
        )

    # Commands are still *accepted* while latched — that is the trap.
    assert cmds.move_forward(0.1)["ok"] is True
    assert q.is_ready()["recovery_hint"] == "press_power_button"
