"""Shared pytest configuration — HIL --robot-ip option."""

from __future__ import annotations

import pytest


def pytest_addoption(parser):
    parser.addoption(
        "--robot-ip",
        action="store",
        default="",
        help="Kachaka robot IP for HIL tests (e.g. 192.168.50.133)",
    )


@pytest.fixture
def robot_ip(request):
    """Robot IP from --robot-ip option. Skips the test if not provided."""
    ip = request.config.getoption("--robot-ip")
    if not ip:
        pytest.skip("--robot-ip not provided")
    return ip
