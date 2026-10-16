# source: Sigma-Snaken/kiosk@90fab26 src/backend/deployer.py (generic `_Jupyter`
# layer + `_probe`), spec: sigma-devkit:kachaka-offline §A4. Port, not a
# redesign — fixes flow kiosk → skill → toolkit; do not change semantics here.
"""JupyterLab (:26501) deployment transport for the Kachaka Playground container.

Uploads scripts through the Jupyter contents API and runs them through a
Jupyter terminal websocket — no SSH key needed. The JupyterLab password is the
factory-fixed ``kachaka`` (public, LAN-only).

``JupyterClient`` is fully **sync**; ``exec()`` contains ``asyncio.run()`` and
must only be called from a worker thread. ``PlaygroundJupyter`` is the async
facade (same signatures / return keys as ``PlaygroundSSH``) and calls the
client via ``asyncio.to_thread``.

Contents-API methods are stdlib only. Terminal methods need ``websockets``
(``pip install 'kachaka-sdk-toolkit[playground]'``), imported lazily.
"""

from __future__ import annotations

import asyncio
import http.cookiejar
import json
import logging
import re
import shlex
import urllib.parse
import urllib.request

logger = logging.getLogger(__name__)

JUPYTER_PASSWORD = "kachaka"  # Kachaka Playground factory value (public on the LAN)
PORT = 26501
SCRIPT_DIR = "/home/kachaka"  # Jupyter contents root == /home/kachaka
MARKER = "KACHAKA-"  # split-marker prefix (kiosk uses KIOSK-)

_JUPYTER_HINT = """\
JupyterLab deployment unavailable. The default transport is JupyterLab on
port 26501 (http://<ip>:26501, factory password: kachaka) — no SSH key is
needed. Check that the robot is reachable and port 26501 answers. On
firmware < 3.17.8 JupyterLab may not start; update the robot firmware."""


class JupyterClient:
    """Minimal JupyterLab REST/WS client (stdlib + websockets)."""

    def __init__(self, robot_ip: str):
        host = robot_ip.split(":")[0]
        self.base = f"http://{host}:{PORT}"
        self.ws_base = f"ws://{host}:{PORT}"
        self.jar = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(self.jar))

    def _xsrf(self) -> str:
        for c in self.jar:
            if c.name == "_xsrf":
                return c.value
        return ""

    def _cookies(self) -> str:
        return "; ".join(f"{c.name}={c.value}" for c in self.jar)

    def _req(self, method: str, path: str, data=None, headers=None, timeout=15):
        h = {"X-XSRFToken": self._xsrf()}
        if headers:
            h.update(headers)
        r = urllib.request.Request(self.base + path, data=data, headers=h, method=method)
        return self.opener.open(r, timeout=timeout)

    def login(self):
        self.opener.open(self.base + "/login", timeout=10).read()
        body = urllib.parse.urlencode(
            {"_xsrf": self._xsrf(), "password": JUPYTER_PASSWORD}).encode()
        self._req("POST", "/login", body,
                  {"Content-Type": "application/x-www-form-urlencoded"}).read()

    def upload(self, filename: str, content: str):
        body = json.dumps({"type": "file", "format": "text", "content": content}).encode()
        self._req("PUT", f"/api/contents/{filename}", body,
                  {"Content-Type": "application/json"}).read()

    def mkdir(self, path: str):
        """Create a directory (contents API PUT type=directory; existing is OK)."""
        body = json.dumps({"type": "directory"}).encode()
        self._req("PUT", f"/api/contents/{urllib.parse.quote(path)}", body,
                  {"Content-Type": "application/json"}).read()

    def upload_binary(self, relpath: str, data: bytes):
        """Upload a binary file (WAV etc.) as base64."""
        import base64
        body = json.dumps({"type": "file", "format": "base64",
                           "content": base64.b64encode(data).decode()}).encode()
        self._req("PUT", f"/api/contents/{urllib.parse.quote(relpath)}", body,
                  {"Content-Type": "application/json"}).read()

    def listdir(self, path: str) -> list[dict]:
        """List a directory on the robot (contents API; items have name/path/type).

        A missing directory returns 404 and urllib raises HTTPError — the
        caller decides whether "missing" is an error.
        """
        raw = self._req(
            "GET", f"/api/contents/{urllib.parse.quote(path)}?content=1&type=directory"
        ).read()
        return json.loads(raw).get("content") or []

    def delete(self, relpath: str):
        """Delete a file on the robot (contents API DELETE)."""
        self._req("DELETE", f"/api/contents/{urllib.parse.quote(relpath)}").read()

    def fetch_base64(self, relpath: str) -> bytes:
        """Read a file on the robot (binary) → bytes."""
        import base64
        raw = self._req(
            "GET", f"/api/contents/{urllib.parse.quote(relpath)}?content=1&type=file&format=base64"
        ).read()
        doc = json.loads(raw)
        return base64.b64decode(doc["content"])

    async def _terminal_exec(self, cmd: str, settle_s: float = 2.0) -> str:
        # Lazy import first, before opening a terminal (contents API stays
        # usable without websockets; no orphan terminal on ImportError).
        try:
            import websockets
        except ImportError as exc:
            raise RuntimeError(
                "websockets not installed — pip install 'kachaka-sdk-toolkit[playground]'"
            ) from exc
        term = json.loads(self._req("POST", "/api/terminals", b"").read())
        name = term["name"]
        url = f"{self.ws_base}/terminals/websocket/{name}"
        headers = {"Cookie": self._cookies(), "Origin": self.base}
        out = []
        try:
            async with websockets.connect(url, additional_headers=headers,
                                          open_timeout=30, max_size=None) as ws:
                await asyncio.sleep(1.0)
                await ws.send(json.dumps(["stdin", cmd + "\n"]))
                deadline = asyncio.get_event_loop().time() + settle_s
                while asyncio.get_event_loop().time() < deadline:
                    try:
                        m = json.loads(await asyncio.wait_for(ws.recv(), timeout=0.5))
                        if m[0] == "stdout":
                            out.append(m[1])
                    except asyncio.TimeoutError:
                        continue
        finally:
            try:
                self._req("DELETE", f"/api/terminals/{name}").read()
            except Exception:
                pass
        return "".join(out)

    def exec(self, cmd: str, settle_s: float = 2.0) -> str:
        """Run one shell line on the robot and collect output for ``settle_s``.

        Contains ``asyncio.run()`` — **call only from a worker thread** (inside
        a running event loop it raises "asyncio.run() cannot be called from a
        running event loop"). Async callers use ``asyncio.to_thread``.
        """
        return asyncio.run(self._terminal_exec(cmd, settle_s))


def pgrep_pattern(filename: str) -> str:
    """``script.py`` → ``python3.*script[.]py`` (bracket trick).

    The terminal echoes the typed command, so an un-bracketed pattern makes
    ``pgrep -f`` match its own command line. The last ``.`` becomes ``[.]``;
    other regex-special characters are escaped literally. A filename without
    a ``.`` gets its first character bracketed instead.
    """
    stem, dot, ext = filename.rpartition(".")
    if dot:
        return f"python3.*{re.escape(stem)}[.]{re.escape(ext)}"
    return f"python3.*[{re.escape(filename[0])}]{re.escape(filename[1:])}"


def _probe(client: JupyterClient, patterns, settle_s: float = 2.5) -> tuple[bool | None, str]:
    """``probe`` plus the raw terminal text (the BUSY line carries the first pid)."""
    cond = " || ".join(f'pgrep -f "{p}" >/dev/null' for p in patterns)
    pids = "; ".join(f'pgrep -f "{p}"' for p in patterns)
    out = client.exec(
        f'if {cond}; then echo {MARKER}"BUSY" $({{ {pids}; }} | head -1); '
        f'else echo {MARKER}"FREE"; fi',
        settle_s=settle_s)
    if f"{MARKER}BUSY" in out:
        return True, out
    if f"{MARKER}FREE" in out:
        return False, out
    return None, out


def probe(client: JupyterClient, patterns, settle_s: float = 2.5) -> bool | None:
    """pgrep the given patterns on the robot. True=running / False=not / None=unreadable.

    None (slow terminal, no output within settle) means "unreachable", NOT
    "idle" — callers must never treat it as free.
    The marker is split in the command (``KACHAKA-"BUSY"``): the terminal's
    echo of the command never contains the full string, only real output
    does, so it cannot self-match.
    """
    return _probe(client, patterns, settle_s)[0]


def _connect(ip: str) -> JupyterClient:
    client = JupyterClient(ip)
    client.login()
    return client


def _upload_sync(ip: str, script_content: str, filename: str) -> dict:
    client = _connect(ip)
    client.upload(filename, script_content)  # full re-push every time (firmware updates wipe /home/kachaka)
    return {"ok": True, "path": f"{SCRIPT_DIR}/{filename}"}


def _run_sync(ip: str, filename: str, log_path: str) -> dict:
    client = _connect(ip)
    pattern = pgrep_pattern(filename)
    script_path = shlex.quote(f"{SCRIPT_DIR}/{filename}")
    # setsid is mandatory: terminado kills the whole process group on
    # terminal close and nohup alone does not survive it.
    cmd = (
        f"setsid nohup python3 -u {script_path} > {shlex.quote(log_path)} 2>&1 < /dev/null & "
        f'for i in $(seq 1 10); do pgrep -f "{pattern}" >/dev/null && break; sleep 0.2; done; '
        f'pid=$(pgrep -f "{pattern}" | head -1); '
        f'if [ -n "$pid" ]; then echo {MARKER}"UP" $pid; else echo {MARKER}"DOWN"; fi'
    )
    out = client.exec(cmd, settle_s=4.0)
    m = re.search(rf"{MARKER}UP\s+(\d+)", out)
    if not m:
        return {"ok": False, "error": f"{filename} did not start"}
    return {"ok": True, "pid": int(m.group(1)), "log_path": log_path}


def _stop_sync(ip: str, filename: str) -> dict:
    client = _connect(ip)
    pattern = pgrep_pattern(filename)
    cmd = (
        f'pkill -f "{pattern}" 2>/dev/null || true; '
        f'for i in $(seq 1 10); do pgrep -f "{pattern}" >/dev/null || break; sleep 0.2; done; '
        f'if pgrep -f "{pattern}" >/dev/null; then echo {MARKER}"BUSY"; else echo {MARKER}"FREE"; fi'
    )
    out = client.exec(cmd, settle_s=4.0)
    if f"{MARKER}FREE" in out:
        return {"ok": True, "stopped": True}
    if f"{MARKER}BUSY" in out:
        return {"ok": False, "error": f"{filename} still alive after pkill"}
    return {"ok": False, "error": "JupyterLab unreachable or no response"}


def _log_sync(ip: str, log_path: str, tail_lines: int) -> dict:
    client = _connect(ip)
    # /tmp is outside the contents root → read through the terminal.
    # END carries tail's exit status (stderr lands in the pty, between markers).
    cmd = (
        f'echo {MARKER}"BEGIN"; tail -n {int(tail_lines)} {shlex.quote(log_path)}; '
        f'echo {MARKER}"END" $?'
    )
    out = client.exec(cmd).replace("\r\n", "\n")
    begin = out.find(f"{MARKER}BEGIN")
    end = out.rfind(f"{MARKER}END")
    if begin < 0 or end < 0 or end < begin:
        return {"ok": False, "error": "JupyterLab unreachable or no response"}
    text = out[begin + len(f"{MARKER}BEGIN"):end]
    if text.startswith("\n"):
        text = text[1:]
    rc = re.match(r"\s*(\d+)", out[end + len(f"{MARKER}END"):])
    if rc and rc.group(1) != "0":
        return {"ok": False, "error": text.strip() or f"Log not found: {log_path}"}
    return {"ok": True, "lines": text, "line_count": len(text.strip().splitlines())}


def _status_sync(ip: str, filename: str) -> dict:
    client = _connect(ip)
    state, out = _probe(client, [pgrep_pattern(filename)])
    if state is None:
        return {"ok": False, "error": "JupyterLab unreachable or no response"}
    if state:
        m = re.search(rf"{MARKER}BUSY\s+(\d+)", out)
        return {"ok": True, "running": True, "pid": int(m.group(1)) if m else None}
    return {"ok": True, "running": False, "pid": None}


class PlaygroundJupyter:
    """Async facade — same signatures / return keys as ``PlaygroundSSH``."""

    PORT = PORT
    SCRIPT_DIR = SCRIPT_DIR

    @staticmethod
    async def upload(ip: str, script_content: str, filename: str = "script.py") -> dict:
        """Upload a script to /home/kachaka/<filename> via the contents API."""
        try:
            return await asyncio.to_thread(_upload_sync, ip, script_content, filename)
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    @staticmethod
    async def run(
        ip: str, filename: str = "script.py", log_path: str = "/tmp/script.log",
    ) -> dict:
        """Start a script detached (setsid nohup) and confirm it is up."""
        try:
            return await asyncio.to_thread(_run_sync, ip, filename, log_path)
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    @staticmethod
    async def stop(ip: str, filename: str = "script.py") -> dict:
        """pkill a running script and confirm it is gone."""
        try:
            return await asyncio.to_thread(_stop_sync, ip, filename)
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    @staticmethod
    async def log(
        ip: str, log_path: str = "/tmp/script.log", tail_lines: int = 50,
    ) -> dict:
        """Read the tail of a log file through a terminal."""
        try:
            return await asyncio.to_thread(_log_sync, ip, log_path, tail_lines)
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    @staticmethod
    async def status(ip: str, filename: str = "script.py") -> dict:
        """Check whether a script is running. Unreachable → ok False, never running False."""
        try:
            return await asyncio.to_thread(_status_sync, ip, filename)
        except Exception as exc:
            return {"ok": False, "error": str(exc)}
