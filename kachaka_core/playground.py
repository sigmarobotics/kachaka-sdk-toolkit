"""SSH operations for the Kachaka Playground container.

Provides upload, run, stop, log, and status management for Python scripts
running inside the robot's on-board Docker container.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

import asyncssh

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

    @staticmethod
    async def _connect(ip: str) -> asyncssh.SSHClientConnection:
        """Open an SSH connection using agent -> key file fallback chain.

        Auth order:
        1. SSH agent
        2. ~/.ssh/id_ed25519
        3. ~/.ssh/id_rsa
        4. Raise ConnectionError with setup instructions
        """
        base_kwargs = dict(
            host=ip,
            port=PlaygroundSSH.PORT,
            username=PlaygroundSSH.USER,
            known_hosts=None,
        )

        # 1. Try SSH agent
        try:
            return await asyncssh.connect(**base_kwargs)
        except (OSError, asyncssh.Error) as exc:
            logger.debug("SSH agent failed: %s", exc)

        # 2-3. Try key files
        key_paths = [
            Path.home() / ".ssh" / "id_ed25519",
            Path.home() / ".ssh" / "id_rsa",
        ]
        for key_path in key_paths:
            if key_path.exists():
                try:
                    return await asyncssh.connect(
                        **base_kwargs,
                        client_keys=[str(key_path)],
                        agent_path=None,
                    )
                except (OSError, asyncssh.Error) as exc:
                    logger.debug("Key %s failed: %s", key_path, exc)

        # 4. All methods exhausted
        raise ConnectionError(_SSH_SETUP_HINT.format(ip=ip))

    @staticmethod
    async def upload(
        ip: str, script_content: str, filename: str = "script.py",
    ) -> dict:
        """Upload a Python script to the Playground container via SFTP."""
        remote_path = f"{PlaygroundSSH.SCRIPT_DIR}/{filename}"
        try:
            conn = await PlaygroundSSH._connect(ip)
            async with conn:
                async with conn.start_sftp_client() as sftp:
                    async with sftp.open(remote_path, "w") as f:
                        await f.write(script_content)
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
        cmd = (
            f"nohup python3 -u {script_path} > {log_path} 2>&1 & "
            f"echo $!"
        )
        try:
            conn = await PlaygroundSSH._connect(ip)
            async with conn:
                result = await conn.run(cmd, check=False)
                pid_str = result.stdout.strip()
                pid = int(pid_str) if pid_str.isdigit() else None
            return {"ok": True, "pid": pid, "log_path": log_path}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    @staticmethod
    async def stop(ip: str, filename: str = "script.py") -> dict:
        """Stop a running script (pkill -f)."""
        try:
            conn = await PlaygroundSSH._connect(ip)
            async with conn:
                result = await conn.run(
                    f"pkill -f 'python3.*{filename}'", check=False,
                )
            stopped = result.returncode == 0
            return {"ok": True, "stopped": stopped}
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
            conn = await PlaygroundSSH._connect(ip)
            async with conn:
                result = await conn.run(
                    f"tail -n {tail_lines} {log_path}", check=False,
                )
            if result.returncode != 0:
                return {"ok": False, "error": f"Log not found: {log_path}"}
            text = result.stdout
            return {
                "ok": True,
                "lines": text,
                "line_count": len(text.strip().splitlines()),
            }
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    @staticmethod
    async def status(ip: str, filename: str = "script.py") -> dict:
        """Check if a script is currently running."""
        try:
            conn = await PlaygroundSSH._connect(ip)
            async with conn:
                result = await conn.run(
                    f"pgrep -f 'python3.*{filename}'", check=False,
                )
            pid_str = result.stdout.strip()
            if result.returncode == 0 and pid_str:
                pid = int(pid_str.splitlines()[0])
                return {"ok": True, "running": True, "pid": pid}
            return {"ok": True, "running": False}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}
