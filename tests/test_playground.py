"""Tests for kachaka_core.playground — SSH operations for Playground container."""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from kachaka_core.playground import PlaygroundSSH


def _run(coro):
    """Helper to run async coroutine in sync test."""
    return asyncio.get_event_loop().run_until_complete(coro)


class TestConnect:
    def test_connect_with_agent(self):
        mock_conn = AsyncMock()
        with patch("asyncssh.connect", new_callable=AsyncMock, return_value=mock_conn) as mock_connect:
            conn = _run(PlaygroundSSH._connect("192.168.1.10"))
            assert conn is mock_conn
            mock_connect.assert_called_once()
            call_kwargs = mock_connect.call_args[1]
            assert call_kwargs["host"] == "192.168.1.10"
            assert call_kwargs["port"] == 26500
            assert call_kwargs["username"] == "kachaka"

    def test_connect_fallback_to_key_file(self):
        """When agent fails, try key files."""
        mock_conn = AsyncMock()
        side_effects = [
            OSError("agent failed"),  # agent attempt
            mock_conn,                # key file attempt
        ]
        with patch("asyncssh.connect", new_callable=AsyncMock, side_effect=side_effects):
            with patch("pathlib.Path.exists", return_value=True):
                conn = _run(PlaygroundSSH._connect("192.168.1.10"))
                assert conn is mock_conn

    def test_connect_all_methods_fail(self):
        with patch("asyncssh.connect", new_callable=AsyncMock, side_effect=OSError("no auth")):
            with patch("pathlib.Path.exists", return_value=False):
                with pytest.raises(ConnectionError, match="SSH connection failed"):
                    _run(PlaygroundSSH._connect("192.168.1.10"))


class TestUpload:
    def test_upload_success(self):
        mock_file = AsyncMock()
        mock_file.__aenter__ = AsyncMock(return_value=mock_file)
        mock_file.__aexit__ = AsyncMock(return_value=False)

        mock_sftp = MagicMock()
        mock_sftp.open = MagicMock(return_value=mock_file)
        mock_sftp.__aenter__ = AsyncMock(return_value=mock_sftp)
        mock_sftp.__aexit__ = AsyncMock(return_value=False)

        mock_conn = MagicMock()
        mock_conn.start_sftp_client = MagicMock(return_value=mock_sftp)
        mock_conn.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_conn.__aexit__ = AsyncMock(return_value=False)

        with patch.object(PlaygroundSSH, "_connect", new_callable=AsyncMock, return_value=mock_conn):
            result = _run(PlaygroundSSH.upload("192.168.1.10", "print('hello')", "test.py"))

        assert result["ok"] is True
        assert result["path"] == "/home/kachaka/test.py"

    def test_upload_connection_error(self):
        with patch.object(PlaygroundSSH, "_connect", new_callable=AsyncMock, side_effect=ConnectionError("SSH connection failed")):
            result = _run(PlaygroundSSH.upload("192.168.1.10", "print('hello')"))

        assert result["ok"] is False
        assert "SSH connection failed" in result["error"]


class TestRun:
    def test_run_success(self):
        mock_result = MagicMock()
        mock_result.stdout = "1234"
        mock_result.returncode = 0

        mock_conn = AsyncMock()
        mock_conn.run = AsyncMock(return_value=mock_result)
        mock_conn.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_conn.__aexit__ = AsyncMock(return_value=False)

        with patch.object(PlaygroundSSH, "_connect", new_callable=AsyncMock, return_value=mock_conn):
            result = _run(PlaygroundSSH.run("192.168.1.10", "test.py", "/tmp/test.log"))

        assert result["ok"] is True
        assert result["log_path"] == "/tmp/test.log"


class TestStop:
    def test_stop_running_process(self):
        mock_result = MagicMock()
        mock_result.returncode = 0

        mock_conn = AsyncMock()
        mock_conn.run = AsyncMock(return_value=mock_result)
        mock_conn.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_conn.__aexit__ = AsyncMock(return_value=False)

        with patch.object(PlaygroundSSH, "_connect", new_callable=AsyncMock, return_value=mock_conn):
            result = _run(PlaygroundSSH.stop("192.168.1.10", "test.py"))

        assert result["ok"] is True
        assert result["stopped"] is True

    def test_stop_not_running(self):
        mock_result = MagicMock()
        mock_result.returncode = 1

        mock_conn = AsyncMock()
        mock_conn.run = AsyncMock(return_value=mock_result)
        mock_conn.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_conn.__aexit__ = AsyncMock(return_value=False)

        with patch.object(PlaygroundSSH, "_connect", new_callable=AsyncMock, return_value=mock_conn):
            result = _run(PlaygroundSSH.stop("192.168.1.10", "test.py"))

        assert result["ok"] is True
        assert result["stopped"] is False


class TestLog:
    def test_log_success(self):
        mock_result = MagicMock()
        mock_result.stdout = "line1\nline2\nline3\n"
        mock_result.returncode = 0

        mock_conn = AsyncMock()
        mock_conn.run = AsyncMock(return_value=mock_result)
        mock_conn.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_conn.__aexit__ = AsyncMock(return_value=False)

        with patch.object(PlaygroundSSH, "_connect", new_callable=AsyncMock, return_value=mock_conn):
            result = _run(PlaygroundSSH.log("192.168.1.10", "/tmp/test.log", tail_lines=50))

        assert result["ok"] is True
        assert "line1" in result["lines"]

    def test_log_file_not_found(self):
        mock_result = MagicMock()
        mock_result.stdout = ""
        mock_result.stderr = "No such file"
        mock_result.returncode = 1

        mock_conn = AsyncMock()
        mock_conn.run = AsyncMock(return_value=mock_result)
        mock_conn.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_conn.__aexit__ = AsyncMock(return_value=False)

        with patch.object(PlaygroundSSH, "_connect", new_callable=AsyncMock, return_value=mock_conn):
            result = _run(PlaygroundSSH.log("192.168.1.10", "/tmp/nonexistent.log"))

        assert result["ok"] is False


class TestStatus:
    def test_status_running(self):
        mock_result = MagicMock()
        mock_result.stdout = "1234\n"
        mock_result.returncode = 0

        mock_conn = AsyncMock()
        mock_conn.run = AsyncMock(return_value=mock_result)
        mock_conn.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_conn.__aexit__ = AsyncMock(return_value=False)

        with patch.object(PlaygroundSSH, "_connect", new_callable=AsyncMock, return_value=mock_conn):
            result = _run(PlaygroundSSH.status("192.168.1.10", "test.py"))

        assert result["ok"] is True
        assert result["running"] is True
        assert result["pid"] == 1234

    def test_status_not_running(self):
        mock_result = MagicMock()
        mock_result.stdout = ""
        mock_result.returncode = 1

        mock_conn = AsyncMock()
        mock_conn.run = AsyncMock(return_value=mock_result)
        mock_conn.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_conn.__aexit__ = AsyncMock(return_value=False)

        with patch.object(PlaygroundSSH, "_connect", new_callable=AsyncMock, return_value=mock_conn):
            result = _run(PlaygroundSSH.status("192.168.1.10", "test.py"))

        assert result["ok"] is True
        assert result["running"] is False
