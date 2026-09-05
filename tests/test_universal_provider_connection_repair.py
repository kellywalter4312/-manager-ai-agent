"""Universal Provider/Connection Management Repair.

A follow-up, read-only audit of the Universal Ecosystem/Provider-Account
layer (``EcosystemRegistry``/``CredentialStore``, built at the Ecosystem
and Phase 1.5 checkpoints) found it real and well-tested *in isolation*,
but genuinely disconnected from the live system it was meant to
configure:

- ``scripts/control_center.py::build`` (and ``scripts/export_ui.py``/
  ``scripts/heartbeat.py``) called ``build_runtime(settings)`` bare -
  the persisted ``CredentialStore`` (``data/credentials/accounts.json``,
  the very file the ``provider <category> add/select/revoke`` command
  writes) was never consulted by the actual running system, for any
  category.
- ``build_communication_provider`` had no ``account`` parameter at all
  (unlike ``build_provider``/``build_media``/``build_speech``, which
  already had one) - even correctly wired, communication's own
  credential had nowhere to go.
- The ecosystem registry's own ``communication_provider`` adapter
  discarded whatever ``credential`` it was given, and unconditionally
  reported ``requires_credential=False``.
- ``scripts/control_center.py``'s own ``provider <category> add/select/
  revoke`` command persisted (and looked up) accounts keyed by
  *category* ("llm_provider") - but every real builder
  (``build_provider``/``build_media``/``build_speech``/
  ``build_communication_provider``) reads ``account.provider`` to
  decide which concrete *backend* to build (``_BUILDERS[provider_name]``
  - "anthropic", "runway", "elevenlabs", ...). An account added through
  the Control Center could therefore never have reached a real
  provider construction: threading it through would have raised
  "Unknown provider 'llm_provider'", not silently done nothing.
  Discovered only once wiring the disconnected pieces together made it
  observable - exactly the kind of gap this repair exists to surface.

This file proves each repair genuinely closes the gap it targets,
through the real production path (``scripts.control_center.build``),
never a synthetic stand-in for it.
"""

from __future__ import annotations

import pytest

from src.config.settings import Settings
from src.control_center.observer import ControlCenter
from src.providers.communication import (
    CommunicationEvent,
    CommunicationProvider,
    CommunicationResult,
    CommunicationStatus,
    utc_now,
)
import src.providers.communication_factory as communication_factory
import src.providers.factory as llm_factory
from src.services.credentials.account import ProviderAccount
from src.services.ecosystem.registry_factory import build_ecosystem_registry
import scripts.control_center as control_center_module
from scripts.control_center import build, run_provider_command


def real_system(**overrides):
    return build(Settings.from_env(env={}, memory="ephemeral", **overrides))


class _FutureCommunicationProvider(CommunicationProvider):
    """A real, minimal CommunicationProvider - the shape any future
    backend (WhatsApp, Telegram, ...) would take. Never registered
    anywhere in this codebase outside these tests - the whole point is
    that nothing about the repair being tested knows this class exists
    in advance.
    """

    def __init__(self, credential: str) -> None:
        self.credential = credential

    @property
    def name(self) -> str:
        return "future-whatsapp"

    def read_messages(self, *, limit: int = 10):
        return (CommunicationEvent(
            kind="message", party="anyone", summary="hello",
            occurred_at=utc_now(), source=self.name,
        ),)

    def send_message(self, *, to: str, content: str) -> CommunicationResult:
        return CommunicationResult(
            operation="send", party=to, status=CommunicationStatus.SUCCEEDED,
            source=self.name, attempted_at=utc_now(),
        )

    def place_call(self, *, to: str) -> CommunicationResult:
        return CommunicationResult(
            operation="call", party=to, status=CommunicationStatus.SUCCEEDED,
            source=self.name, attempted_at=utc_now(),
        )


def _build_future_whatsapp(settings, env=None, account=None):
    """A builder that genuinely *needs* its credential - the same shape
    a real WhatsApp adapter would have. Raises rather than silently
    building with nothing, so a test asserting failure here is real
    evidence the credential never arrived, not evidence of nothing in
    particular."""
    if account is None:
        raise ValueError("future-whatsapp requires an account; none was given")
    if account.provider != "future-whatsapp":
        raise ValueError(f"an account for '{account.provider}' cannot authorise future-whatsapp")
    return _FutureCommunicationProvider(credential=account.credential)


@pytest.fixture
def registered_future_whatsapp(monkeypatch):
    """Registers the fake backend for the duration of one test, exactly
    the way a real, newly-added WhatsApp module would add one entry to
    ``communication_factory._BUILDERS`` - never touching
    ``src/manager/`` or ``src/composition.py``."""
    monkeypatch.setitem(communication_factory._BUILDERS, "future-whatsapp", _build_future_whatsapp)


# ---------------------------------------------------------------------------
# 1. Control Center account add/select -> persisted state -> real build()
#    -> the actual runtime provider receives the selected credential.
# ---------------------------------------------------------------------------


class TestAccountSelectionReachesTheRealRuntime:
    def test_an_llm_account_added_and_selected_through_control_center_reaches_the_real_runtime(
        self, tmp_path, monkeypatch
    ):
        monkeypatch.setattr(control_center_module, "DEFAULT_ACCOUNTS_PATH", tmp_path / "accounts.json")
        # Pre-existing, unrelated to this repair: every specialist (e.g.
        # Arthur) resolves its own LLM credential independently
        # (composition.py's own SPECIALISTS loop, Phase 1.5) and this
        # test's own `accounts=` seam only ever names the *Manager's*
        # account (`accounts.active(settings.provider)`), the identical
        # scope item 5 asks for. Setting the environment credential lets
        # every specialist build successfully via its own, ordinary
        # fallback path while this test isolates whether the Manager's
        # own explicitly-selected account takes priority over it - the
        # same "account overrides environment" guarantee
        # test_phase_1_5_provider_accounts.py already proves directly.
        monkeypatch.setenv("ANTHROPIC_API_KEY", "env-fallback-key-never-selected")

        # A safe, credential-free build to get a working Control Center
        # session from - not yet the "anthropic" backend.
        runtime, manager, channel, conversation = real_system()
        centre = ControlCenter(runtime, manager)

        run_provider_command(centre, "llm_provider add anthropic work sk-work-account")
        run_provider_command(centre, "llm_provider add anthropic second sk-second-account")
        # "second" became active on add (CredentialStore's own default);
        # explicitly switch back, so this proves *select*, not merely add.
        selected = run_provider_command(centre, "llm_provider select anthropic work")
        assert "now active" in selected

        # Simulates a restart with MANAGER_PROVIDER=anthropic - the real
        # production entry point, called a second time.
        runtime2, manager2, channel2, conversation2 = real_system(provider="anthropic")
        assert runtime2.provider.name == "anthropic"
        assert runtime2.provider._api_key == "sk-work-account"

        # Selecting the other account and rebuilding again picks it up -
        # proving this is live account resolution, not a frozen read.
        run_provider_command(centre, "llm_provider select anthropic second")
        runtime3, *_ = real_system(provider="anthropic")
        assert runtime3.provider._api_key == "sk-second-account"

    def test_a_video_generation_account_added_and_selected_through_control_center_reaches_the_real_runtime(
        self, tmp_path, monkeypatch
    ):
        monkeypatch.setattr(control_center_module, "DEFAULT_ACCOUNTS_PATH", tmp_path / "accounts.json")

        runtime, manager, channel, conversation = real_system()
        centre = ControlCenter(runtime, manager)

        run_provider_command(centre, "video_generation_provider add runway runway-acct rwy-key-1 gen4_turbo")
        run_provider_command(centre, "video_generation_provider select runway runway-acct")

        runtime2, *_ = real_system(video_generation="runway")
        assert runtime2.media.video_generator.name == "runway"
        assert runtime2.media.video_generator._api_key == "rwy-key-1"

    def test_revoking_an_account_through_control_center_is_honoured_on_the_next_build(
        self, tmp_path, monkeypatch
    ):
        monkeypatch.setattr(control_center_module, "DEFAULT_ACCOUNTS_PATH", tmp_path / "accounts.json")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "env-fallback-key-never-selected")

        runtime, manager, channel, conversation = real_system()
        centre = ControlCenter(runtime, manager)

        run_provider_command(centre, "llm_provider add anthropic work sk-work-account")
        runtime2, *_ = real_system(provider="anthropic")
        assert runtime2.provider._api_key == "sk-work-account"

        run_provider_command(centre, "llm_provider revoke anthropic work")
        # No account active for "anthropic" any more - the Manager's own
        # provider must fall back to the environment credential (still
        # configured, so the build succeeds), never silently reuse the
        # revoked key.
        runtime3, *_ = real_system(provider="anthropic")
        assert runtime3.provider._api_key == "env-fallback-key-never-selected"


# ---------------------------------------------------------------------------
# 2. Future-provider factory test: a backend nobody registered anywhere
#    in this codebase works through the identical real factory -> env
#    var -> build() path, with zero orchestration change.
# ---------------------------------------------------------------------------


class TestFutureProviderThroughTheRealFactoryAndBuildPath:
    def test_a_never_before_seen_communication_backend_reaches_the_real_runtime(
        self, tmp_path, monkeypatch, registered_future_whatsapp
    ):
        monkeypatch.setattr(control_center_module, "DEFAULT_ACCOUNTS_PATH", tmp_path / "accounts.json")

        # The registry immediately reflects the new backend - discovery
        # needed no change beyond the one _BUILDERS entry.
        registry = build_ecosystem_registry(Settings.from_env(env={}))
        available = {d.key for d in registry.adapter_for("communication_provider").available()}
        assert "future-whatsapp" in available

        runtime, manager, channel, conversation = real_system()
        centre = ControlCenter(runtime, manager)
        added = run_provider_command(centre, "communication_provider add future-whatsapp acct1 wa-secret-1")
        assert "added" in added
        assert "wa-secret-1" not in added  # never echoed back

        # Simulates a restart with MANAGER_COMMUNICATION_PROVIDER=future-whatsapp -
        # no change to src/manager/, src/composition.py, or control_center.py
        # was needed to make this work.
        runtime2, *_ = real_system(communication_provider="future-whatsapp")
        assert runtime2.communication.name == "future-whatsapp"
        assert runtime2.communication.credential == "wa-secret-1"

    def test_a_never_before_seen_llm_backend_reaches_the_real_runtime(self, monkeypatch):
        def _build_future_llm(settings, env=None, account=None):
            from src.providers.base import Completion, LLMProvider

            class _FutureLLM(LLMProvider):
                def __init__(self, credential):
                    self.credential = credential

                @property
                def name(self):
                    return "future-llm"

                def complete(self, prompt, *, system=None):
                    return Completion(text="stub", provider="future-llm")

            if account is None:
                raise ValueError("future-llm requires an account")
            return _FutureLLM(credential=account.credential)

        monkeypatch.setitem(llm_factory._BUILDERS, "future-llm", _build_future_llm)

        # Discovery: the registry immediately reflects the new backend -
        # zero change needed beyond the one _BUILDERS entry.
        registry = build_ecosystem_registry(Settings.from_env(env={}))
        assert "future-llm" in {d.key for d in registry.adapter_for("llm_provider").available()}

        # Construction: an account naming this brand-new backend builds
        # through the unmodified build_provider dispatch - the same
        # "account.provider decides the builder" rule every existing
        # vendor already goes through.
        provider = llm_factory.build_provider(
            Settings.from_env(env={}),
            account=ProviderAccount(provider="future-llm", account_id="a", credential="secret-llm-key"),
        )
        assert provider.name == "future-llm"
        assert provider.credential == "secret-llm-key"


# ---------------------------------------------------------------------------
# 3. Communication credential threading: the ecosystem adapter and the
#    factory itself must actually receive a supplied credential, never
#    discard it.
# ---------------------------------------------------------------------------


class TestCommunicationCredentialThreading:
    def test_ecosystem_validate_passes_the_credential_through(self, registered_future_whatsapp):
        registry = build_ecosystem_registry(Settings.from_env(env={}))
        adapter = registry.adapter_for("communication_provider")

        ok = adapter.validate("future-whatsapp", credential="wa-correct")
        assert ok.ok, ok.detail

    def test_ecosystem_validate_fails_when_no_credential_is_given(self, registered_future_whatsapp):
        registry = build_ecosystem_registry(Settings.from_env(env={}))
        adapter = registry.adapter_for("communication_provider")

        missing = adapter.validate("future-whatsapp")
        assert not missing.ok

    def test_build_communication_provider_accepts_and_uses_an_explicit_account(
        self, registered_future_whatsapp
    ):
        provider = communication_factory.build_communication_provider(
            Settings.from_env(env={}, communication_provider="future-whatsapp"),
            account=ProviderAccount(provider="future-whatsapp", account_id="a", credential="wa-direct"),
        )
        assert provider.credential == "wa-direct"

    def test_the_null_backend_is_unaffected_by_the_new_account_parameter(self):
        """Backward compatibility: the one backend that ships today
        still builds identically whether or not an account is passed -
        the whole point of an *additive* seam."""
        from src.providers.null_communication_provider import NullCommunicationProvider

        bare = communication_factory.build_communication_provider(Settings.from_env(env={}))
        assert isinstance(bare, NullCommunicationProvider)

        with_account = communication_factory.build_communication_provider(
            Settings.from_env(env={}),
            account=ProviderAccount(provider="null", account_id="a", credential="irrelevant"),
        )
        assert isinstance(with_account, NullCommunicationProvider)

    def test_requires_credential_reflects_the_real_backend_not_a_blanket_false(
        self, registered_future_whatsapp
    ):
        registry = build_ecosystem_registry(Settings.from_env(env={}))
        adapter = registry.adapter_for("communication_provider")

        by_key = {d.key: d.requires_credential for d in adapter.available()}
        assert by_key["null"] is False
        assert by_key["future-whatsapp"] is True


# ---------------------------------------------------------------------------
# 4. The account-key convention fix: accounts are keyed by backend, not
#    category - proven at the composition boundary the tests above only
#    exercise indirectly.
# ---------------------------------------------------------------------------


class TestAccountsAreKeyedByBackendNotCategory:
    def test_an_account_persisted_through_control_center_is_keyed_by_backend(self, tmp_path, monkeypatch):
        from src.services.credentials.account_store_file import load_credential_store

        accounts_path = tmp_path / "accounts.json"
        monkeypatch.setattr(control_center_module, "DEFAULT_ACCOUNTS_PATH", accounts_path)

        runtime, manager, channel, conversation = real_system()
        centre = ControlCenter(runtime, manager)
        run_provider_command(centre, "llm_provider add anthropic work sk-test-123")

        store = load_credential_store(accounts_path)
        # The key build_provider itself reads - never the category name.
        assert store.active("anthropic") is not None
        assert store.active("anthropic").account_id == "work"
        assert store.active("llm_provider") is None


# ---------------------------------------------------------------------------
# 5. Backward compatibility: no accounts persisted anywhere reproduces
#    every existing default exactly.
# ---------------------------------------------------------------------------


class TestBackwardCompatibilityWithNoPersistedAccounts:
    def test_a_bare_build_with_no_accounts_file_is_unaffected(self, tmp_path, monkeypatch):
        monkeypatch.setattr(control_center_module, "DEFAULT_ACCOUNTS_PATH", tmp_path / "does_not_exist.json")
        runtime, manager, channel, conversation = real_system()

        assert runtime.provider.name == "null"
        assert runtime.media.video_generator.name == "null"
        assert runtime.communication.name == "null"
        assert runtime.speech.name == "none"
