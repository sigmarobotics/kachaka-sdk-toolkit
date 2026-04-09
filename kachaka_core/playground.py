"""SSH operations for the Kachaka Playground container.

Provides upload, run, stop, log, and status management for Python scripts
running inside the robot's on-board Docker container.

Uses subprocess (ssh/scp) instead of asyncssh to avoid event-loop conflicts
when running inside MCP server frameworks (anyio / FastMCP).
"""

from __future__ import annotations

import asyncio
import logging
import shlex
import tempfile

logger = logging.getLogger(__name__)

_SSH_SETUP_HINT = """\
SSH connection failed. To set up SSH key access:
1. Generate a key:  ssh-keygen -t ed25519
2. Open JupyterLab at http://{ip}:26501 (password: kachaka)
3. In a JupyterLab terminal, run:
   mkdir -p ~/.ssh && echo 'PASTE_YOUR_PUBLIC_KEY' >> ~/.ssh/authorized_keys
4. Verify:  ssh -p 26500 kachaka@{ip}"""


class PlaygroundSSH:
    """Static methods for managing scripts on the Kachaka Playground container."""

    PORT = 26500
    USER = "kachaka"
    SCRIPT_DIR = "/home/kachaka"

    _SSH_OPTS = [
        "-o", "StrictHostKeyChecking=no",
        "-o", "UserKnownHostsFile=/dev/null",
        "-o", "LogLevel=ERROR",
        "-o", "ConnectTimeout=10",
    ]

    @staticmethod
    def _ssh_base(ip: str) -> list[str]:
        """Build the common ssh command prefix."""
        return [
            "ssh", *PlaygroundSSH._SSH_OPTS,
            "-p", str(PlaygroundSSH.PORT),
            f"{PlaygroundSSH.USER}@{ip}",
        ]

    @staticmethod
    async def _run_cmd(
        cmd: list[str], *, stdin_data: bytes | None = None,
    ) -> tuple[int, str, str]:
        """Run a command via asyncio.create_subprocess_exec."""
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=asyncio.subprocess.PIPE if stdin_data else None,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout_bytes, stderr_bytes = await proc.communicate(input=stdin_data)
        return (
            proc.returncode or 0,
            stdout_bytes.decode(errors="replace"),
            stderr_bytes.decode(errors="replace"),
        )

    @staticmethod
    async def upload(
        ip: str, script_content: str, filename: str = "script.py",
    ) -> dict:
        """Upload a Python script to the Playground container via ssh pipe."""
        remote_path = f"{PlaygroundSSH.SCRIPT_DIR}/{filename}"
        cmd = [
            *PlaygroundSSH._ssh_base(ip),
            f"cat > {shlex.quote(remote_path)}",
        ]
        try:
            rc, _, stderr = await PlaygroundSSH._run_cmd(
                cmd, stdin_data=script_content.encode(),
            )
            if rc != 0:
                raise RuntimeError(stderr.strip() or f"ssh exit code {rc}")
            return {"ok": True, "path": remote_path}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    @staticmethod
    async def run(
        ip: str,
        filename: str = "script.py",
        log_path: str = "/tmp/script.log",
    ) -> dict:
        """Start a script in background via nohup."""
        script_path = f"{PlaygroundSSH.SCRIPT_DIR}/{filename}"
        remote_cmd = (
            f"nohup python3 -u {script_path} > {log_path} 2>&1 & echo $!"
        )
        try:
            rc, stdout, stderr = await PlaygroundSSH._run_cmd(
                [*PlaygroundSSH._ssh_base(ip), remote_cmd],
            )
            if rc != 0:
                raise RuntimeError(stderr.strip() or f"ssh exit code {rc}")
            pid_str = stdout.strip()
            pid = int(pid_str) if pid_str.isdigit() else None
            return {"ok": True, "pid": pid, "log_path": log_path}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    @staticmethod
    async def stop(ip: str, filename: str = "script.py") -> dict:
        """Stop a running script (pkill -f)."""
        try:
            rc, _, _ = await PlaygroundSSH._run_cmd(
                [*PlaygroundSSH._ssh_base(ip),
                 f"pkill -f 'python3.*{filename}'"],
            )
            return {"ok": True, "stopped": rc == 0}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    @staticmethod
    async def log(
        ip: str,
        log_path: str = "/tmp/script.log",
        tail_lines: int = 50,
    ) -> dict:
        """Read the tail of a script log file."""
        try:
            rc, stdout, _ = await PlaygroundSSH._run_cmd(
                [*PlaygroundSSH._ssh_base(ip),
                 f"tail -n {tail_lines} {log_path}"],
            )
            if rc != 0:
                return {"ok": False, "error": f"Log not found: {log_path}"}
            return {
                "ok": True,
                "lines": stdout,
                "line_count": len(stdout.strip().splitlines()),
            }
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    @staticmethod
    async def status(ip: str, filename: str = "script.py") -> dict:
        """Check if a script is currently running."""
        try:
            rc, stdout, _ = await PlaygroundSSH._run_cmd(
                [*PlaygroundSSH._ssh_base(ip),
                 f"pgrep -f 'python3.*{filename}'"],
            )
            pid_str = stdout.strip()
            if rc == 0 and pid_str:
                pid = int(pid_str.splitlines()[0])
                return {"ok": True, "running": True, "pid": pid}
            return {"ok": True, "running": False}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}
