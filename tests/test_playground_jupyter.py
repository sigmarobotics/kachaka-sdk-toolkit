"""Tests for kachaka_core.playground_jupyter and the Playground transport facade.

Choke points mocked (mirrors test_playground.py's single-choke-point style):
- ``JupyterClient.login`` / ``JupyterClient._req`` — contents API (HTTP)
- ``JupyterClient.exec`` — terminal websocket commands
- ``Playground._jupyter_reachable`` + facade classes — transport selection
"""

from __future__ import annotations

import asyncio
import builtins
import inspect
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from kachaka_core.playground import _SSH_SETUP_HINT, Playground, PlaygroundSSH
from kachaka_core.playground_jupyter import (
    _JUPYTER_HINT,
    JupyterClient,
    PlaygroundJupyter,
    pgrep_pattern,
)

IP = "192.168.1.10"


def _run(coro):
    """Helper to run async coroutine in sync test."""
    return asyncio.run(coro)


def _patch_login():
    return patch.object(JupyterClient, "login", return_value=None)


def _patch_exec(*, return_value="", side_effect=None):
    return patch.object(
        JupyterClient, "exec", return_value=return_value, side_effect=side_effect,
    )


class TestPgrepPattern:
    def test_bracket_trick_on_caller_filename(self):
        assert pgrep_pattern("offline_route.py") == "python3.*offline_route[.]py"

    def test_default_filename(self):
        assert pgrep_pattern("script.py") == "python3.*script[.]py"


class TestUpload:
    def test_upload_always_puts_contents_no_existence_check(self):
        """CORNER-018: firmware updates wipe /home/kachaka → always full re-push."""
        with _patch_login(), patch.object(JupyterClient, "_req") as mock_req:
            result = _run(PlaygroundJupyter.upload(IP, "print('hi')", "test.py"))

        assert result == {"ok": True, "path": "/home/kachaka/test.py"}
        assert mock_req.call_count == 1  # no GET / existence probe
        method, path, body = mock_req.call_args[0][:3]
        assert method == "PUT"
        assert path == "/api/contents/test.py"
        assert json.loads(body) == {"type": "file", "format": "text", "content": "print('hi')"}

    def test_upload_error_returns_ok_false(self):
        with _patch_login(), patch.object(
            JupyterClient, "_req", side_effect=OSError("Connection refused"),
        ):
            result = _run(PlaygroundJupyter.upload(IP, "x"))

        assert result["ok"] is False
        assert "Connection refused" in result["error"]

    def test_client_strips_port_suffix(self):
        c = JupyterClient("192.168.1.10:26400")
        assert c.base == "http://192.168.1.10:26501"
        assert c.ws_base == "ws://192.168.1.10:26501"


class TestRun:
    def test_run_command_uses_setsid_and_split_marker(self):
        """CORNER-014 (setsid nohup … < /dev/null &) + CORNER-015 (bracket, split marker)."""
        with _patch_login(), _patch_exec(return_value="KACHAKA-UP 1234\r\n") as mock_exec:
            result = _run(PlaygroundJupyter.run(IP, "script.py", "/tmp/s.log"))

        assert result == {"ok": True, "pid": 1234, "log_path": "/tmp/s.log"}
        cmd = mock_exec.call_args[0][0]
        assert "setsid nohup" in cmd
        assert "< /dev/null" in cmd
        assert "&" in cmd
        assert "script[.]py" in cmd
        assert "KACHAKA-UP" not in cmd
        assert "/tmp/s.log" in cmd

    def test_run_down_is_not_ok(self):
        with _patch_login(), _patch_exec(return_value="KACHAKA-DOWN\r\n"):
            result = _run(PlaygroundJupyter.run(IP, "script.py"))

        assert result["ok"] is False
        assert "script.py did not start" in result["error"]

    def test_run_echo_only_is_not_ok(self):
        # Only the echoed command (split marker) — no real output.
        with _patch_login(), _patch_exec(return_value='echo KACHAKA-"UP" $pid'):
            result = _run(PlaygroundJupyter.run(IP))

        assert result["ok"] is False


class TestStatus:
    def test_status_busy_with_pid(self):
        with _patch_login(), _patch_exec(return_value="KACHAKA-BUSY 42\r\n") as mock_exec:
            result = _run(PlaygroundJupyter.status(IP, "script.py"))

        assert result == {"ok": True, "running": True, "pid": 42}
        cmd = mock_exec.call_args[0][0]
        assert "script[.]py" in cmd
        assert "KACHAKA-BUSY" not in cmd

    def test_status_free(self):
        with _patch_login(), _patch_exec(return_value="KACHAKA-FREE\r\n"):
            result = _run(PlaygroundJupyter.status(IP))

        assert result["ok"] is True
        assert result["running"] is False

    def test_status_exec_raises_is_not_idle(self):
        """CORNER-017: unreachable ≠ idle — never report running False."""
        with _patch_login(), _patch_exec(side_effect=OSError("timed out")):
            result = _run(PlaygroundJupyter.status(IP))

        assert result["ok"] is False
        assert "running" not in result

    def test_status_no_marker_is_not_idle(self):
        """CORNER-017: no marker within settle → ok False, no running key."""
        with _patch_login(), _patch_exec(return_value='if pgrep ...; then echo KACHAKA-"BUSY"'):
            result = _run(PlaygroundJupyter.status(IP))

        assert result["ok"] is False
        assert "running" not in result


class TestStop:
    def test_stop_free_after_kill(self):
        with _patch_login(), _patch_exec(return_value="KACHAKA-FREE\r\n") as mock_exec:
            result = _run(PlaygroundJupyter.stop(IP, "script.py"))

        assert result == {"ok": True, "stopped": True}
        cmd = mock_exec.call_args[0][0]
        assert 'pkill -f "python3.*script[.]py"' in cmd
        assert "KACHAKA-FREE" not in cmd

    def test_stop_still_alive_is_not_ok(self):
        with _patch_login(), _patch_exec(return_value="KACHAKA-BUSY\r\n"):
            result = _run(PlaygroundJupyter.stop(IP))

        assert result["ok"] is False
        assert "alive" in result["error"]

    def test_stop_unreadable_is_not_ok(self):
        with _patch_login(), _patch_exec(return_value=""):
            result = _run(PlaygroundJupyter.stop(IP))

        assert result["ok"] is False


class TestLog:
    def test_log_returns_only_text_between_markers(self):
        out = (
            'echo KACHAKA-"BEGIN"; tail -n 50 /tmp/script.log; echo KACHAKA-"END" $?\r\n'
            "KACHAKA-BEGIN\r\nline1\r\nline2\r\nKACHAKA-END 0\r\n$ "
        )
        with _patch_login(), _patch_exec(return_value=out) as mock_exec:
            result = _run(PlaygroundJupyter.log(IP, "/tmp/script.log", 50))

        assert result == {"ok": True, "lines": "line1\nline2\n", "line_count": 2}
        assert "tail -n 50 /tmp/script.log" in mock_exec.call_args[0][0]

    def test_log_missing_end_marker_is_not_ok(self):
        with _patch_login(), _patch_exec(return_value="KACHAKA-BEGIN\r\nline1\r\n"):
            result = _run(PlaygroundJupyter.log(IP))

        assert result["ok"] is False

    def test_log_missing_file_is_not_ok(self):
        out = (
            "KACHAKA-BEGIN\r\ntail: cannot open '/tmp/nope.log' for reading: "
            "No such file or directory\r\nKACHAKA-END 1\r\n"
        )
        with _patch_login(), _patch_exec(return_value=out):
            result = _run(PlaygroundJupyter.log(IP, "/tmp/nope.log"))

        assert result["ok"] is False
        assert "No such file" in result["error"]


class TestLazyWebsockets:
    def test_exec_without_websockets_raises_but_contents_api_works(self):
        real_import = builtins.__import__

        def fake_import(name, globals=None, locals=None, fromlist=(), level=0):
            if name == "websockets" or name.startswith("websockets."):
                raise ImportError("No module named 'websockets'")
            return real_import(name, globals, locals, fromlist, level)

        client = JupyterClient(IP)
        with patch.object(builtins, "__import__", side_effect=fake_import), \
                patch.object(JupyterClient, "_req") as mock_req:
            with pytest.raises(RuntimeError, match=r"kachaka-sdk-toolkit\[playground\]"):
                client.exec("echo hi")
            mock_req.assert_not_called()  # no terminal opened before the import
            client.upload("a.py", "print(1)")

        assert mock_req.call_args[0][:2] == ("PUT", "/api/contents/a.py")


class TestPlaygroundTransport:
    def test_auto_reachable_uses_jupyter_not_ssh(self):
        with patch.object(Playground, "_jupyter_reachable", new_callable=AsyncMock, return_value=True), \
                patch.object(PlaygroundJupyter, "upload", new_callable=AsyncMock,
                             return_value={"ok": True, "path": "/home/kachaka/s.py"}) as jup, \
                patch.object(PlaygroundSSH, "upload", new_callable=AsyncMock) as ssh:
            result = _run(Playground.upload(IP, "x", "s.py"))

        assert result["ok"] is True
        jup.assert_awaited_once_with(IP, "x", "s.py")
        ssh.assert_not_awaited()

    def test_auto_reachable_jupyter_error_does_not_fall_back(self):
        with patch.object(Playground, "_jupyter_reachable", new_callable=AsyncMock, return_value=True), \
                patch.object(PlaygroundJupyter, "run", new_callable=AsyncMock,
                             return_value={"ok": False, "error": "s.py did not start"}), \
                patch.object(PlaygroundSSH, "run", new_callable=AsyncMock) as ssh:
            result = _run(Playground.run(IP, "s.py"))

        assert result == {"ok": False, "error": "s.py did not start"}
        ssh.assert_not_awaited()

    def test_auto_unreachable_uses_ssh(self):
        with patch.object(Playground, "_jupyter_reachable", new_callable=AsyncMock, return_value=False), \
                patch.object(PlaygroundJupyter, "status", new_callable=AsyncMock) as jup, \
                patch.object(PlaygroundSSH, "status", new_callable=AsyncMock,
                             return_value={"ok": True, "running": False}) as ssh:
            result = _run(Playground.status(IP, "s.py"))

        assert result == {"ok": True, "running": False}
        ssh.assert_awaited_once_with(IP, "s.py")
        jup.assert_not_awaited()

    def test_auto_unreachable_and_ssh_error_merges_both_hints(self):
        """CORNER-016: firmware < 3.17.8 may lack JupyterLab → SSH; both fail → both hints."""
        with patch.object(Playground, "_jupyter_reachable", new_callable=AsyncMock, return_value=False), \
                patch.object(PlaygroundSSH, "upload", new_callable=AsyncMock,
                             return_value={"ok": False, "error": "Permission denied (publickey)"}):
            result = _run(Playground.upload(f"{IP}:26400", "x"))

        assert result["ok"] is False
        assert "Permission denied (publickey)" in result["error"]
        assert _JUPYTER_HINT in result["error"]
        assert _SSH_SETUP_HINT.format(ip=IP) in result["error"]

    def test_explicit_ssh_skips_reachability_probe(self):
        with patch.object(Playground, "_jupyter_reachable", new_callable=AsyncMock) as probe, \
                patch.object(PlaygroundSSH, "stop", new_callable=AsyncMock,
                             return_value={"ok": True, "stopped": True}) as ssh:
            result = _run(Playground.stop(IP, "s.py", transport="ssh"))

        assert result == {"ok": True, "stopped": True}
        probe.assert_not_awaited()
        ssh.assert_awaited_once_with(IP, "s.py")

    def test_explicit_jupyter_skips_reachability_probe(self):
        with patch.object(Playground, "_jupyter_reachable", new_callable=AsyncMock) as probe, \
                patch.object(PlaygroundJupyter, "log", new_callable=AsyncMock,
                             return_value={"ok": True, "lines": "", "line_count": 0}) as jup:
            _run(Playground.log(IP, "/tmp/a.log", 10, transport="jupyter"))

        probe.assert_not_awaited()
        jup.assert_awaited_once_with(IP, "/tmp/a.log", 10)

    def test_invalid_transport_is_not_ok(self):
        with patch.object(Playground, "_jupyter_reachable", new_callable=AsyncMock) as probe:
            result = _run(Playground.status(IP, transport="telnet"))

        assert result["ok"] is False
        assert "telnet" in result["error"]
        probe.assert_not_awaited()

    def test_reachable_probe_strips_port_and_checks_200(self):
        resp = MagicMock(status=200)
        resp.__enter__.return_value = resp
        with patch("kachaka_core.playground.urllib.request.urlopen", return_value=resp) as op:
            assert _run(Playground._jupyter_reachable(f"{IP}:26400")) is True
        assert op.call_args[0][0] == f"http://{IP}:26501/login"
        assert op.call_args.kwargs["timeout"] == 3

    def test_reachable_probe_error_is_false(self):
        with patch("kachaka_core.playground.urllib.request.urlopen", side_effect=OSError("refused")):
            assert _run(Playground._jupyter_reachable(IP)) is False


class TestMcpTools:
    @pytest.mark.parametrize("tool", [
        "playground_upload", "playground_run", "playground_stop",
        "playground_log", "playground_status",
    ])
    def test_playground_tools_expose_transport(self, tool):
        from mcp_server import server

        params = inspect.signature(getattr(server, tool)).parameters
        assert "transport" in params
        assert params["transport"].default == "auto"
        assert list(params)[-1] == "transport"
