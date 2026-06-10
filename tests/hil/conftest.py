"""HIL fixtures — real robot connection + blackhole proxy for silent-drop simulation."""

from __future__ import annotations

import socket
import threading

import pytest

from kachaka_core.connection import KachakaConnection


class BlackholeProxy:
    """TCP proxy that can stop forwarding WITHOUT closing sockets.

    This simulates a silent network drop (WiFi vanishing — no TCP RST,
    no FIN): both endpoints keep their sockets open, but bytes stop
    flowing. From the gRPC client's view the HTTP/2 connection is wedged,
    which is exactly the failure mode of the 2026-05-18 production hang.
    """

    def __init__(self, upstream_host: str, upstream_port: int = 26400):
        self._upstream = (upstream_host, upstream_port)
        self._blackholed = threading.Event()
        self._closed = threading.Event()
        self._server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._server.bind(("127.0.0.1", 0))
        self._server.listen(16)
        self.port = self._server.getsockname()[1]
        self._sockets: list[socket.socket] = []
        self._accept_thread = threading.Thread(target=self._accept_loop, daemon=True)
        self._accept_thread.start()

    @property
    def target(self) -> str:
        return f"127.0.0.1:{self.port}"

    def blackhole(self) -> None:
        """Silently stop forwarding traffic in both directions."""
        self._blackholed.set()

    def restore(self) -> None:
        """Resume forwarding (network came back)."""
        self._blackholed.clear()

    def close(self) -> None:
        self._closed.set()
        try:
            self._server.close()
        except OSError:
            pass
        for s in self._sockets:
            try:
                s.close()
            except OSError:
                pass

    # ── internals ──────────────────────────────────────────────────

    def _accept_loop(self) -> None:
        while not self._closed.is_set():
            try:
                client, _ = self._server.accept()
            except OSError:
                return
            try:
                upstream = socket.create_connection(self._upstream, timeout=5)
            except OSError:
                client.close()
                continue
            self._sockets += [client, upstream]
            threading.Thread(
                target=self._pump, args=(client, upstream), daemon=True
            ).start()
            threading.Thread(
                target=self._pump, args=(upstream, client), daemon=True
            ).start()

    def _pump(self, src: socket.socket, dst: socket.socket) -> None:
        while not self._closed.is_set():
            try:
                data = src.recv(65536)
            except OSError:
                return
            if not data:
                # Upstream closed for real — only propagate when NOT
                # blackholed (a silent drop must not deliver the FIN).
                if not self._blackholed.is_set():
                    try:
                        dst.shutdown(socket.SHUT_WR)
                    except OSError:
                        pass
                return
            if self._blackholed.is_set():
                continue  # swallow bytes — silent drop
            try:
                dst.sendall(data)
            except OSError:
                return


@pytest.fixture
def blackhole_proxy(robot_ip):
    """Blackhole-able TCP proxy in front of the real robot's gRPC port."""
    proxy = BlackholeProxy(robot_ip)
    yield proxy
    proxy.close()
    KachakaConnection.remove(proxy.target)


@pytest.fixture(scope="session")
def real_conn(request):
    """Direct (non-proxied) connection to the real robot. Session-scoped."""
    ip = request.config.getoption("--robot-ip")
    if not ip:
        pytest.skip("--robot-ip not provided")
    conn = KachakaConnection.get(ip)
    ping = conn.ping()
    assert ping["ok"], f"Robot ping failed: {ping}"
    return conn
