"""Shared test doubles, and the two guards that keep this suite offline.

ScriptedProvider is the key double: it lets us test LLM-driven routing with
fully deterministic "model" answers, so the tests never need a model, a key or
a network. A test double that you control completely is what makes an
unpredictable dependency testable.

The guards below exist because the situation changed. This suite was written
on a machine with no Anthropic credentials, where an accidental live call
would have failed instantly with "no API key". A real key now lives in the
Windows User environment, so the same mistake would instead succeed, bill the
account, and pass silently. Two independent guards, because either alone can
be defeated:

    _no_api_keys       removes the credentials, so nothing can authenticate
    _no_network        blocks the sockets, so nothing can connect

A test that genuinely wants to prove a request would be attempted asserts on
the loud failure rather than being exempted.
"""

from __future__ import annotations

import os
import socket
import ssl
from pathlib import Path

import pytest

from src.providers.base import Completion, LLMProvider

# The real runtime directory: Gmail-derived state, generated reports and the
# OAuth token all live here. Resolved from this file so it is correct whatever
# the working directory happens to be when pytest starts.
PRODUCTION_DATA_DIR = (Path(__file__).resolve().parent.parent / "data").resolve()

# Every variable that could authenticate a real request: this project's own,
# and the ones each vendor SDK finds by itself.
CREDENTIAL_VARS = (
    "ANTHROPIC_API_KEY",
    "MANAGER_API_KEY",
    "ANTHROPIC_AUTH_TOKEN",
    "ELEVENLABS_API_KEY",
)


def _is_production_data(target: object) -> bool:
    """Whether a write target lands inside the real data/ directory."""
    try:
        resolved = Path(os.fspath(target)).resolve()
    except (TypeError, ValueError, OSError):
        return False
    return resolved == PRODUCTION_DATA_DIR or PRODUCTION_DATA_DIR in resolved.parents


@pytest.fixture(autouse=True)
def _no_production_writes(monkeypatch):
    """Make writing to the real data/ directory a hard failure in every test.

    This exists because a test already did it. tests/test_specialist_brain_
    architecture.py drove scripts/heartbeat.py with default settings, which
    resolve MANAGER_STATE_PATH to data/state/processed.json - so pytest ran a
    real heartbeat cycle and wrote sample-inbox message ids and fixture
    opportunities into the user's genuine Gmail-derived state, and filed a
    report alongside the real ones.

    Fixing that one test would leave the next one free to repeat the mistake.
    The rule this project applies to the model applies equally here: the wrong
    thing should be structurally difficult, not merely discouraged. So the
    write itself is blocked, wherever it comes from.

    Only writes are intercepted. Tests may still *read* production paths - and
    one of them deliberately does, to prove the state file is untouched.
    """
    def guard(operation, target):
        raise AssertionError(
            f"test attempted to {operation} inside the production data directory: "
            f"{target}. "
            "Heartbeat, state and reporting tests must be given a tmp_path - "
            "pass state_path/reports_dir through Settings, or inject a "
            "StateStore(tmp_path / ...) directly."
        )

    real_write_text = Path.write_text
    real_open = Path.open
    real_replace = os.replace
    real_os_open = os.open

    def write_text(self, *args, **kwargs):
        if _is_production_data(self):
            guard("write", self)
        return real_write_text(self, *args, **kwargs)

    def path_open(self, mode="r", *args, **kwargs):
        if any(flag in mode for flag in ("w", "a", "x", "+")) and _is_production_data(self):
            guard("open for writing", self)
        return real_open(self, mode, *args, **kwargs)

    def replace(src, dst, *args, **kwargs):
        if _is_production_data(dst) or _is_production_data(src):
            guard("replace a file", dst)
        return real_replace(src, dst, *args, **kwargs)

    def os_open(path, flags, *args, **kwargs):
        writing = flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_APPEND)
        if writing and _is_production_data(path):
            guard("open for writing", path)
        return real_os_open(path, flags, *args, **kwargs)

    def mkdir(self, *args, **kwargs):
        """Creating a directory is a write too.

        Added after this guard let one through. PersistentMemory._save calls
        parent.mkdir(parents=True) before writing, so when persistent memory
        became the production default a test run created a real data/memory/
        directory - empty, because the write itself was still blocked, but
        created. The interception was of file writes rather than of *writing*,
        and a directory nobody asked for is the visible half of that gap.
        """
        if _is_production_data(self):
            guard("create a directory", self)
        return real_mkdir(self, *args, **kwargs)

    real_mkdir = Path.mkdir

    monkeypatch.setattr(Path, "write_text", write_text)
    monkeypatch.setattr(Path, "open", path_open)
    monkeypatch.setattr(Path, "mkdir", mkdir)
    monkeypatch.setattr(os, "replace", replace)
    monkeypatch.setattr(os, "open", os_open)


@pytest.fixture(autouse=True)
def _no_api_keys(monkeypatch):
    """Remove real credentials from the environment for every test.

    Autouse and unconditional. Tests that need a key pass an explicit fake one
    through Settings or an env mapping, which is what they already do - so
    this removes a way to fail without removing a way to test.
    """
    for name in CREDENTIAL_VARS:
        monkeypatch.delenv(name, raising=False)


@pytest.fixture(scope="session", autouse=True)
def _no_network():
    """Make any outbound connection a loud failure for the whole session.

    Patched at the method level rather than by replacing socket.socket, so the
    individual tests that swap out socket.socket to prove a specific point
    keep working unchanged.

    This is the guard that turns "we believe these tests are offline" into
    something the suite enforces. An accidental real call fails here, in the
    test that caused it, instead of on the credit card.
    """
    saved = (
        socket.socket.connect,
        socket.socket.connect_ex,
        socket.create_connection,
        socket.getaddrinfo,
        ssl.SSLContext.wrap_socket,
    )

    def blocked(*args, **kwargs):
        raise AssertionError(
            "outbound network access attempted during tests - "
            "inject a fake client instead of building a real one"
        )

    socket.socket.connect = blocked
    socket.socket.connect_ex = blocked
    socket.create_connection = blocked
    # DNS too: resolving api.anthropic.com is outbound traffic in its own
    # right, and blocking it stops the attempt a full round trip earlier.
    socket.getaddrinfo = blocked
    ssl.SSLContext.wrap_socket = blocked
    try:
        yield
    finally:
        (
            socket.socket.connect,
            socket.socket.connect_ex,
            socket.create_connection,
            socket.getaddrinfo,
            ssl.SSLContext.wrap_socket,
        ) = saved


class ScriptedProvider(LLMProvider):
    """Returns queued answers in order, then repeats the last one.

    It also records every prompt it was given, so tests can assert on what the
    router actually asked - not just on what it did with the reply.
    """

    def __init__(self, *answers: str, model: str | None = "scripted-1", fail: bool = False):
        self._answers = list(answers) or [""]
        self._model = model
        self._fail = fail
        self.prompts: list[str] = []
        self.systems: list[str | None] = []

    @property
    def name(self) -> str:
        return "scripted"

    @property
    def call_count(self) -> int:
        return len(self.prompts)

    def complete(self, prompt: str, *, system: str | None = None) -> Completion:
        if self._fail:
            raise RuntimeError("provider exploded")

        self.prompts.append(prompt)
        self.systems.append(system)

        text = self._answers.pop(0) if len(self._answers) > 1 else self._answers[0]
        return Completion(
            text=text,
            provider=self.name,
            model=self._model,
            input_tokens=len(prompt),
            output_tokens=len(text),
        )


@pytest.fixture
def scripted():
    """Factory fixture: build a ScriptedProvider with the answers a test needs."""
    return ScriptedProvider
