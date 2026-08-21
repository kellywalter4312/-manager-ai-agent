"""Proof that this suite cannot reach the Anthropic API.

Every other test file *assumes* it is offline. This one checks, because the
assumption stopped being free: a real ANTHROPIC_API_KEY now lives in the
Windows User environment of the machine this project is developed on. Before
that, an accidental live call failed instantly with "no API key". Now the same
mistake would authenticate, bill the account, and pass.

Two guards in tests/conftest.py, tested here separately because each has to
hold on its own:

    _no_api_keys   the credential is gone      -> cannot authenticate
    _no_network    the socket is blocked       -> cannot connect

The last class is the one that matters: it drives the *real* Anthropic SDK,
with a real client object, and shows the attempt dying at the socket instead
of on the credit card.
"""

import os
import socket
import ssl

import pytest

from src.config.settings import Settings
from src.providers.anthropic_provider import AnthropicProvider
from src.providers.factory import ANTHROPIC_KEY_ENV, ProviderConfigurationError, build_provider


class TestCredentialsAreScrubbed:
    @pytest.mark.parametrize(
        "name", ["ANTHROPIC_API_KEY", "MANAGER_API_KEY", "ANTHROPIC_AUTH_TOKEN"]
    )
    def test_the_variable_is_absent(self, name):
        assert os.environ.get(name) is None

    def test_settings_read_from_the_real_environment_carry_no_key(self):
        """Settings.from_env() with no mapping reads os.environ. Even that path
        must not pick up a live credential during tests."""
        assert Settings.from_env().api_key is None
        assert not Settings.from_env().has_api_key

    def test_the_factory_cannot_find_a_credential(self):
        """env=None makes the factory consult the real os.environ - the exact
        call that would have started working when the key was installed."""
        with pytest.raises(ProviderConfigurationError):
            build_provider(Settings(provider="anthropic"), env=None)

    def test_the_scrub_survives_into_every_test(self):
        assert ANTHROPIC_KEY_ENV not in os.environ

    def test_a_test_may_still_supply_its_own_fake_key(self):
        """Removing real keys must not remove the ability to test the key path."""
        provider = build_provider(Settings(provider="anthropic", api_key="sk-ant-fake"))
        assert provider.name == "anthropic"


class TestTheNetworkIsBlocked:
    def test_creating_a_connection_raises(self):
        with pytest.raises(AssertionError):
            socket.create_connection(("api.anthropic.com", 443))

    def test_connecting_a_socket_raises(self):
        with pytest.raises(AssertionError):
            socket.socket().connect(("api.anthropic.com", 443))

    def test_tls_wrapping_raises(self):
        with pytest.raises(AssertionError):
            ssl.create_default_context().wrap_socket(socket.socket())

    def test_dns_resolution_raises(self):
        """Blocked in its own right: a lookup is outbound traffic, and stopping
        it here kills the attempt a round trip earlier."""
        with pytest.raises(AssertionError):
            socket.getaddrinfo("api.anthropic.com", 443)

    def test_the_failure_says_what_to_do_instead(self):
        with pytest.raises(AssertionError) as exc:
            socket.create_connection(("example.com", 80))
        assert "inject a fake client" in str(exc.value)


@pytest.fixture
def unimport_sdk():
    """Undo the vendor import this file performs, before anything else sees it.

    Several tests elsewhere assert that ``anthropic`` never enters
    sys.modules - that is how the lazy-import boundary is enforced. Importing
    the real SDK here would break them for the rest of the session, and it
    would do so *by test ordering*: the suite happens to run this file after
    them alphabetically, so the failure would appear only when someone runs a
    subset, or filters with -k, or a file gets renamed.

    Removing the modules afterwards restores the invariant. The import itself
    is what this test needs and cannot fake - the whole point is that a real
    SDK client, not a stand-in, cannot get out.
    """
    import sys

    before = set(sys.modules)
    yield
    for name in set(sys.modules) - before:
        if name == "anthropic" or name.startswith("anthropic."):
            del sys.modules[name]


class TestTheRealSdkCannotGetOut:
    """The decisive check: a genuine SDK client, blocked at the socket."""

    def test_a_real_client_cannot_reach_the_api(self, unimport_sdk):
        anthropic = pytest.importorskip("anthropic")

        # A real client, a real provider, a fake credential. max_retries=0 so
        # the attempt fails once rather than backing off through three.
        client = anthropic.Anthropic(api_key="sk-ant-fake-not-a-real-key", max_retries=0)
        provider = AnthropicProvider(client=client)

        with pytest.raises(Exception) as exc:
            provider.complete("this must never reach the network")

        # However the SDK wraps it, the refusal has to come from our guard.
        chain, seen = [], exc.value
        while seen is not None:
            chain.append(f"{type(seen).__name__}: {seen}")
            seen = seen.__cause__ or seen.__context__
        assert any("network access attempted" in link for link in chain), chain

    def test_the_sdk_import_does_not_leak_into_the_session(self):
        """Guards the guard. Other files assert the vendor SDK is never
        imported; this file is the one place that must import it, so it has to
        clean up - and that cleanup must not depend on test ordering."""
        import sys

        assert "anthropic" not in sys.modules

    def test_an_injected_fake_still_works_normally(self):
        """The guard blocks the network, not the tests."""
        from types import SimpleNamespace

        response = SimpleNamespace(
            content=[SimpleNamespace(type="text", text="ok")],
            stop_reason="end_turn",
            model="claude-opus-5",
            usage=SimpleNamespace(input_tokens=1, output_tokens=1),
        )
        client = SimpleNamespace(messages=SimpleNamespace(create=lambda **kw: response))
        assert AnthropicProvider(client=client).complete("hi").text == "ok"
