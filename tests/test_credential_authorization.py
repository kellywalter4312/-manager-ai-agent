"""Whether a credential actually works, asked of the vendor itself.

Migrated (2026-09-15) from ``src/services/credentials/validation.py``, which
held this mechanism with no caller anywhere in the running system, and from
``test_phase_1_5_provider_accounts.py``, which exercised it in isolation. The
mechanism was never the gap: reachability was. The Control Center could tell
the owner that a provider *constructs* and never that a vendor *accepts* it -
which is the only question an unfunded key actually raises.

No test here reaches a real vendor. The mechanism is proven against scripted
providers (a working one, a refusing one, one that raises, one that answers
with nothing), and the Control Center route is proven to describe, confirm and
record the outward call - never to make one on its own.
"""

from __future__ import annotations

import pytest

import scripts.control_center as cc
from src.config.settings import Settings
from src.providers.base import Completion, LLMProvider
from src.services.credentials.account import ProviderAccount
from src.services.ecosystem import registry_factory
from src.services.ecosystem.contracts import CategoryAdapter
from src.services.credentials.authorization import (
    AuthorizationCheck,
    check_authorization,
    check_speech_authorization,
)


class _Answers(LLMProvider):
    def __init__(self, text: str):
        self._text = text

    @property
    def name(self) -> str:
        return "test"

    def complete(self, prompt, *, system=None):
        return Completion(text=self._text, provider="test", model="test-model")


class _Raises(LLMProvider):
    def __init__(self, error: Exception):
        self._error = error

    @property
    def name(self) -> str:
        return "test"

    def complete(self, prompt, *, system=None):
        raise self._error


class TestTheMechanism:
    def test_a_working_credential_is_authorized_through_a_real_call(self):
        outcome = check_authorization(_Answers("ok"))
        assert outcome.authorized and outcome.reached_vendor
        assert "completion" in outcome.reason

    def test_a_rejected_credential_is_not_authorized_and_never_reached(self):
        """The credential exists - a non-empty string was supplied - and does
        not work. Exactly the case a presence check calls valid."""
        outcome = check_authorization(_Raises(RuntimeError("401 unauthorized")))
        assert outcome.authorized is False and outcome.reached_vendor is False
        assert "unauthorized" in outcome.reason.lower()

    def test_an_unreachable_host_is_reported_not_raised(self):
        outcome = check_authorization(_Raises(ConnectionError("host unreachable")))
        assert outcome.authorized is False and "ConnectionError" in outcome.reason

    def test_an_empty_answer_is_reached_but_inconclusive(self):
        outcome = check_authorization(_Answers(""))
        assert outcome.authorized is False and outcome.reached_vendor is True

    def test_the_two_booleans_are_never_collapsed(self):
        reached = AuthorizationCheck(authorized=False, reached_vendor=True, reason="no")
        never = AuthorizationCheck(authorized=False, reached_vendor=False, reason="no")
        assert "reached the vendor" in reached.describe()
        assert "never reached the vendor" in never.describe()


class _SpeechProbeWorks:
    name = "test-speech"

    def probe_authorization(self):
        return 3


class _SpeechProbeRaises:
    name = "test-speech"

    def __init__(self, error: Exception):
        self._error = error

    def probe_authorization(self):
        raise self._error


class _SpeechNoProbe:
    name = "windows"


class TestTheSpeechMechanism:
    """check_speech_authorization - closure campaign, 2026-09-16. Found
    live: the Control Center's own 'check' action refused every category
    except llm_provider, which was honest for most categories but not
    speech - ElevenLabsSpeechAdapter already had a real vendor call
    (probe_authorization) sitting unused for this exact purpose."""

    def test_a_working_credential_is_authorized_through_a_real_call(self):
        outcome = check_speech_authorization(_SpeechProbeWorks())
        assert outcome.authorized and outcome.reached_vendor
        assert "3 real voice" in outcome.reason

    def test_a_rejected_credential_is_not_authorized_and_never_reached(self):
        outcome = check_speech_authorization(_SpeechProbeRaises(RuntimeError("401 unauthorized")))
        assert outcome.authorized is False and outcome.reached_vendor is False
        assert "unauthorized" in outcome.reason.lower()

    def test_an_uncredentialed_engine_is_reported_honestly_not_guessed(self):
        """Windows/Kokoro/none have no vendor credential to check at all -
        this must never be confused with a rejected one."""
        outcome = check_speech_authorization(_SpeechNoProbe())
        assert outcome.authorized is False and outcome.reached_vendor is False
        assert "not a credentialed vendor" in outcome.reason


class TestItIsReachableFromTheControlCenter:
    @pytest.fixture
    def stores(self, tmp_path, monkeypatch):
        monkeypatch.setattr(cc, "DEFAULT_ACCOUNTS_PATH", tmp_path / "accounts.json")
        monkeypatch.setattr(cc, "DEFAULT_CONFIG_PATH", tmp_path / "config.json")
        return tmp_path

    def _settings(self, tmp_path):
        return Settings.from_env(
            env={}, memory="ephemeral", profile_path="does-not-exist-USER.md",
            artifacts_dir=str(tmp_path / "artifacts"), knowledge_dir=str(tmp_path / "k"),
            documents_dir=str(tmp_path / "d"),
        )

    def test_it_is_a_confirmed_outward_write_that_says_what_it_will_do(self):
        needs, described = cc.classify_administrative_write("provider llm_provider check anthropic owner")
        assert needs
        assert "one real request" in described and "leaves the machine" in described

    def test_construction_only_validate_is_not_confirmed(self):
        needs, _ = cc.classify_administrative_write("provider llm_provider validate anthropic")
        assert not needs

    def test_with_no_account_it_refuses_before_sending_anything(self, stores):
        runtime, manager, channel, conversation = cc.build(self._settings(stores))
        centre = cc.ControlCenter(runtime, manager)
        out = cc.run_command(centre, "provider llm_provider check anthropic", 100, channel, conversation)
        assert "no active account" in out and "add anthropic" in out

    def test_a_backend_with_no_live_probe_is_told_so_by_the_mechanism(self, stores, monkeypatch):
        """Closure campaign 3 (2026-09-17): the refusal no longer comes from a
        hardcoded tuple of checkable categories - the backend is really built
        and asked, and a backend with no ``probe_authorization`` is refused on
        that evidence. Video generation is still the genuine example: its
        vendors hold credentials but no verified probe exists for them here."""
        runtime, manager, channel, conversation = cc.build(self._settings(stores))
        centre = cc.ControlCenter(runtime, manager)
        cc.run_command(
            centre, "provider video_generation_provider add runway owner fake-key-not-real",
            100, channel, conversation,
        )
        monkeypatch.setattr(
            registry_factory, "build_media", lambda settings, **kwargs: _SpeechNoProbe()
        )
        out = cc.run_command(
            centre, "provider video_generation_provider check runway", 100, channel, conversation
        )
        assert out.startswith("NOT AUTHORIZED: runway/owner")
        assert "no live authorization probe" in out

    def test_a_category_with_no_construction_path_is_refused_before_anything_is_built(self, stores):
        """The other honest refusal: a category the registry cannot construct
        at all has nothing to ask, and says so instead of pretending."""
        runtime, manager, channel, conversation = cc.build(self._settings(stores))
        centre = cc.ControlCenter(runtime, manager)
        adapter, _ = cc._require_category(
            cc.build_ecosystem_registry(centre.runtime.settings), "llm_provider"
        )
        with pytest.raises(NotImplementedError):
            CategoryAdapter.construct(adapter, "anything")

    def test_the_answer_is_recorded_by_whether_the_vendor_was_reached(self):
        assert cc._administrative_write_succeeded(
            "provider", "AUTHORIZED: anthropic/owner - the vendor answered. a real completion")
        assert not cc._administrative_write_succeeded(
            "provider", "NOT AUTHORIZED: anthropic/owner - the vendor was never reached. RuntimeError: 401")
        assert cc._administrative_write_succeeded(
            "provider", "NOT AUTHORIZED: anthropic/owner - the vendor answered. returned no usable text")

    def test_the_real_account_path_runs_without_reaching_a_vendor(self, stores, monkeypatch):
        """The whole Control Center path, with a stored account and a scripted
        provider in place of the vendor: the command builds the provider from
        that account and reports the vendor's answer."""
        runtime, manager, channel, conversation = cc.build(self._settings(stores))
        centre = cc.ControlCenter(runtime, manager)
        cc.run_command(
            centre, "provider llm_provider add anthropic owner fake-key-not-real", 100, channel, conversation)
        built = {}

        def fake_build(settings, account=None, **kwargs):
            built["backend"] = settings.provider
            built["account"] = account.account_id if isinstance(account, ProviderAccount) else None
            return _Answers("ok")

        # Patched where the registry's own build closure reads it - the seam
        # the Control Center now goes through for every category alike.
        monkeypatch.setattr(registry_factory, "build_provider", fake_build)
        out = cc.run_command(centre, "provider llm_provider check anthropic", 100, channel, conversation)
        assert built == {"backend": "anthropic", "account": "owner"}
        assert out.startswith("AUTHORIZED: anthropic/owner")
        assert "LIVE EXTERNAL VERIFIED" in out

    def test_the_speech_account_path_runs_without_reaching_a_vendor(self, stores, monkeypatch):
        """The identical whole-Control-Center path as the LLM test above,
        proven for speech_provider/elevenlabs - the new category this round
        added, through the unmodified 'check' command."""
        runtime, manager, channel, conversation = cc.build(self._settings(stores))
        centre = cc.ControlCenter(runtime, manager)
        cc.run_command(
            centre, "provider speech_provider add elevenlabs owner fake-key-not-real",
            100, channel, conversation,
        )
        built = {}

        def fake_build_speech(settings, env=None, account=None, **kwargs):
            built["backend"] = settings.speech
            built["account"] = account.account_id if isinstance(account, ProviderAccount) else None
            return _SpeechProbeWorks()

        monkeypatch.setattr(registry_factory, "build_speech", fake_build_speech)
        out = cc.run_command(centre, "provider speech_provider check elevenlabs", 100, channel, conversation)
        assert built == {"backend": "elevenlabs", "account": "owner"}
        assert out.startswith("AUTHORIZED: elevenlabs/owner")
        assert "LIVE EXTERNAL VERIFIED" in out
