"""Shared support for the GM campaign's tests: a real system built through the
production composition with every owner-state location isolated, and a small
local HTTP device for connections to talk to.

Not a test module (pytest collects only ``test_*.py``). Every GM test file
uses ``isolate_owner_state`` so the owner's own roster, services, ledger and
audit trail are never inputs to - or outputs of - these tests (P11Z-11,
P11X-03): the new stores already take their path from Settings
(``ecosystem_dir``), and the older module-level paths are redirected here.
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import scripts.control_center as control_center_module
import src.services.audit.action_log as action_log_module
import src.services.connections.service_registry as service_registry_module
import src.services.connections.site_registry as site_registry_module
import src.services.credentials.account_store_file as account_store_file_module
import src.services.evolution.governed_config as governed_config_module
import src.services.evolution.ledger_store as ledger_store_module
from src.config.settings import Settings

import pytest

_LOOPBACK = ("127.0.0.1", "localhost", "::1")


@pytest.fixture
def allow_loopback(monkeypatch):
    """Let this test reach a server on this machine - and nothing else.

    ``tests/conftest.py`` blocks every socket for the whole session so no test
    can reach the outside world. A GM test that proves a mechanism against a
    real local HTTP device needs loopback, so this re-opens exactly that and
    keeps every other destination a loud failure."""
    import socket
    import _socket

    def _is_loopback(host) -> bool:
        return str(host) in _LOOPBACK

    def getaddrinfo(host, port, *args, **kwargs):
        if not _is_loopback(host):
            raise AssertionError(f"outbound network access attempted during tests: {host}")
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", int(port)))]

    def create_connection(address, timeout=socket._GLOBAL_DEFAULT_TIMEOUT, source_address=None, **kwargs):
        host, port = address[0], address[1]
        if not _is_loopback(host):
            raise AssertionError(f"outbound network access attempted during tests: {host}")
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        if timeout is not socket._GLOBAL_DEFAULT_TIMEOUT:
            sock.settimeout(timeout)
        _socket.socket.connect(sock, ("127.0.0.1", int(port)))
        return sock

    def connect(sock, address):
        if not _is_loopback(address[0]):
            raise AssertionError(f"outbound network access attempted during tests: {address[0]}")
        return _socket.socket.connect(sock, address)

    monkeypatch.setattr(socket, "getaddrinfo", getaddrinfo)
    monkeypatch.setattr(socket, "create_connection", create_connection)
    monkeypatch.setattr(socket.socket, "connect", connect)
    yield


_LEAVES = {
    "DEFAULT_CONFIG_PATH": "config.json", "DEFAULT_ACCOUNTS_PATH": "accounts.json",
    "DEFAULT_LEDGER_PATH": "ledger.json", "DEFAULT_SERVICES_PATH": "services.json",
    "DEFAULT_SITES_PATH": "sites.json", "DEFAULT_ACTION_LOG_PATH": "action_log.json",
}


def isolate_owner_state(monkeypatch, tmp_path: Path) -> None:
    modules = [control_center_module, governed_config_module, ledger_store_module, account_store_file_module,
               service_registry_module, site_registry_module, action_log_module]
    try:
        import src.services.evolution.ecosystem_evolution as ecosystem_evolution_module
        import src.services.evolution.inventory as inventory_module
        modules += [ecosystem_evolution_module, inventory_module]
    except ImportError:  # pragma: no cover
        pass
    for module in modules:
        for attribute, leaf in _LEAVES.items():
            if hasattr(module, attribute):
                monkeypatch.setattr(module, attribute, tmp_path / leaf)


def settings_for(tmp_path: Path, **overrides) -> Settings:
    values = dict(
        env={}, memory="ephemeral", profile_path=str(tmp_path / "USER.md"),
        artifacts_dir=str(tmp_path / "artifacts"), media_asset_dir=str(tmp_path / "media"),
        knowledge_dir=str(tmp_path / "knowledge"), documents_dir=str(tmp_path / "documents"),
        state_path=str(tmp_path / "state" / "processed.json"), reports_dir=str(tmp_path / "reports"),
        ecosystem_dir=str(tmp_path / "ecosystem"), memory_dir=str(tmp_path / "memory"),
    )
    values.update(overrides)
    return Settings.from_env(**values)


class System:
    """The real system through ``scripts.control_center.build``."""

    def __init__(self, tmp_path: Path, **overrides) -> None:
        self.tmp_path = tmp_path
        self.settings = settings_for(tmp_path, **overrides)
        self.rebuild()

    def rebuild(self) -> "System":
        runtime, self.manager, self.channel, self.conversation = control_center_module.build(self.settings)
        self.centre = control_center_module._governed_control_center(runtime, self.manager)
        return self

    @property
    def runtime(self):
        return self.centre.runtime

    @property
    def ecosystem(self):
        return self.centre.runtime.ecosystem

    def cc(self, command: str) -> str:
        return control_center_module.run_command(
            self.centre, command, 110, self.channel, self.conversation,
            action_log_path=self.tmp_path / "action_log.json",
        )

    def say(self, text: str):
        from scripts.exchange import exchange

        return exchange(self.channel, self.conversation, text, action_log_path=self.tmp_path / "action_log.json")

    def audit(self) -> "list[tuple[str, bool]]":
        path = self.tmp_path / "action_log.json"
        if not path.exists():
            return []
        return [(e["capability"], e["succeeded"]) for e in json.loads(path.read_text(encoding="utf-8"))["entries"]]


class Device:
    """A local HTTP device. ``get``/``post`` map a path to ``fn(device, request)``
    returning a JSON-able value, ``(content_type, bytes)`` or ``bytes``; ``raw``
    maps a path to ``fn(handler, device)`` that writes the response itself
    (for streams)."""

    def __init__(self, get=None, post=None, raw=None, put=None) -> None:
        self.state: dict = {}
        self.calls: list = []
        device = self
        get, post, raw = dict(get or {}), dict(post or {}), dict(raw or {})

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *args):
                pass

            def _reply(self, status, body):
                if isinstance(body, tuple):
                    ctype, data = body
                elif isinstance(body, bytes):
                    ctype, data = "application/octet-stream", body
                else:
                    ctype, data = "application/json", json.dumps(body).encode()
                self.send_response(status)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self):
                path = self.path.split("?")[0]
                device.calls.append(("GET", self.path))
                if path in raw:
                    raw[path](self, device)
                    return
                if path not in get:
                    self._reply(404, {"error": "no such path"})
                    return
                self._reply(200, get[path](device, self.path))

            def do_POST(self):
                length = int(self.headers.get("Content-Length") or 0)
                body = self.rfile.read(length) if length else b""
                try:
                    payload = json.loads(body or b"{}")
                except ValueError:
                    payload = {"raw": body.decode("utf-8", "replace")}
                path = self.path.split("?")[0]
                device.calls.append(("POST", self.path, payload))
                if path not in post:
                    self._reply(404, {"error": "no such path"})
                    return
                self._reply(200, post[path](device, payload))

            def do_PUT(self):
                length = int(self.headers.get("Content-Length") or 0)
                body = self.rfile.read(length) if length else b""
                device.calls.append(("PUT", self.path, body))
                result = put(device, self.path, body) if put else {"stored": len(body)}
                self._reply(201, result)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()
