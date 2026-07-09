"""Tests for kachaka_core.playground — Playground container script ops.

The implementation shells out via ``asyncio.create_subprocess_exec`` (plain
``ssh``/pipe, no asyncssh) — every public method funnels through the single
``_run_cmd(cmd, *, stdin_data=) -> (returncode, stdout, stderr)`` choke point,
which is what these tests mock.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from kachaka_core.playground import PlaygroundSSH


def _run(coro):
    """Helper to run async coroutine in sync test."""
    return asyncio.run(coro)


def _patch_run_cmd(*, returncode=0, stdout="", stderr=""):
    """Patch the _run_cmd choke point to return a canned subprocess result."""
    return patch.object(
        PlaygroundSSH,
        "_run_cmd",
        new_callable=AsyncMock,
        return_value=(returncode, stdout, stderr),
    )


class TestUpload:
    def test_upload_success(self):
        with _patch_run_cmd(returncode=0) as mock_run:
            result = _run(PlaygroundSSH.upload("192.168.1.10", "print('hello')", "test.py"))

        assert result["ok"] is True
        assert result["path"] == "/home/kachaka/test.py"
        # Script content is piped in over stdin; remote cmd is a `cat >` redirect.
        assert mock_run.call_args.kwargs["stdin_data"] == b"print('hello')"
        assert "cat > " in mock_run.call_args[0][0][-1]
        assert "/home/kachaka/test.py" in mock_run.call_args[0][0][-1]

    def test_upload_failure_surfaces_stderr(self):
        with _patch_run_cmd(returncode=1, stderr="Permission denied"):
            result = _run(PlaygroundSSH.upload("192.168.1.10", "print('hello')"))

        assert result["ok"] is False
        assert "Permission denied" in result["error"]

    def test_upload_connection_error(self):
        with patch.object(
            PlaygroundSSH, "_run_cmd", new_callable=AsyncMock,
            side_effect=OSError("ssh: connect to host port 26500: Connection refused"),
        ):
            result = _run(PlaygroundSSH.upload("192.168.1.10", "print('hello')"))

        assert result["ok"] is False
        assert "Connection refused" in result["error"]


class TestRun:
    def test_run_success(self):
        with _patch_run_cmd(returncode=0, stdout="1234\n") as mock_run:
            result = _run(PlaygroundSSH.run("192.168.1.10", "test.py", "/tmp/test.log"))

        assert result["ok"] is True
        assert result["pid"] == 1234
        assert result["log_path"] == "/tmp/test.log"
        # Background launch uses nohup and redirects to the log path.
        remote_cmd = mock_run.call_args[0][0][-1]
        assert "nohup python3" in remote_cmd
        assert "/tmp/test.log" in remote_cmd

    def test_run_non_numeric_pid_is_none(self):
        with _patch_run_cmd(returncode=0, stdout=""):
            result = _run(PlaygroundSSH.run("192.168.1.10"))

        assert result["ok"] is True
        assert result["pid"] is None

    def test_run_failure(self):
        with _patch_run_cmd(returncode=255, stderr="host unreachable"):
            result = _run(PlaygroundSSH.run("192.168.1.10"))

        assert result["ok"] is False
        assert "host unreachable" in result["error"]


class TestStop:
    def test_stop_running_process(self):
        with _patch_run_cmd(returncode=0):
            result = _run(PlaygroundSSH.stop("192.168.1.10", "test.py"))

        assert result["ok"] is True
        assert result["stopped"] is True

    def test_stop_not_running(self):
        # pkill exits 1 when nothing matched.
        with _patch_run_cmd(returncode=1):
            result = _run(PlaygroundSSH.stop("192.168.1.10", "test.py"))

        assert result["ok"] is True
        assert result["stopped"] is False


class TestLog:
    def test_log_success(self):
        with _patch_run_cmd(returncode=0, stdout="line1\nline2\nline3\n"):
            result = _run(PlaygroundSSH.log("192.168.1.10", "/tmp/test.log", tail_lines=50))

        assert result["ok"] is True
        assert "line1" in result["lines"]
        assert result["line_count"] == 3

    def test_log_file_not_found(self):
        with _patch_run_cmd(returncode=1):
            result = _run(PlaygroundSSH.log("192.168.1.10", "/tmp/nonexistent.log"))

        assert result["ok"] is False
        assert "/tmp/nonexistent.log" in result["error"]


class TestStatus:
    def test_status_running(self):
        with _patch_run_cmd(returncode=0, stdout="1234\n"):
            result = _run(PlaygroundSSH.status("192.168.1.10", "test.py"))

        assert result["ok"] is True
        assert result["running"] is True
        assert result["pid"] == 1234

    def test_status_not_running(self):
        # pgrep exits 1 when no process matched.
        with _patch_run_cmd(returncode=1):
            result = _run(PlaygroundSSH.status("192.168.1.10", "test.py"))

        assert result["ok"] is True
        assert result["running"] is False
