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

import socket
import ssl

import pytest

from src.providers.base import Completion, LLMProvider

# Every variable that could authenticate a real request: this project's own,
# and the one the vendor SDK finds by itself.
CREDENTIAL_VARS = ("ANTHROPIC_API_KEY", "MANAGER_API_KEY", "ANTHROPIC_AUTH_TOKEN")


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
