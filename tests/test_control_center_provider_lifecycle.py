"""Control Center provider and voice management, action to runtime (2026-09-21).

Found by driving the served page's own Control Center:
- an ElevenLabs key added in the Control Center could not be used from it:
  'use' and 'validate' built the backend with no credential and told the
  person to set an environment variable;
- revoking the account of the backend a category was actively using left the
  running engine on the revoked credential ("Saved, but not applied");
- credentials were written to disk in plaintext;
- with reasoning on and no vendor, the null provider's own label was shown
  to the person as Alfred's answer;
- the Control Center had no way to set a voice's variant or style, clear an
  accent, or hand the voice choice back to the agent, and the page's provider
  form could not switch the active backend ('use') or run 'check'.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import scripts.control_center as cc
import scripts.serve_ui as su
from scripts.exchange import exchange
from src.config.settings import Settings
from src.services.credentials.account import CredentialStore, ProviderAccount
from src.services.credentials.account_store_file import load_credential_store, save_credential_store

REPO_ROOT = Path(__file__).resolve().parent.parent
PAGE = (REPO_ROOT / "ui" / "index.html").read_text(encoding="utf-8")
SECRET = "sk-test-DoNotLeak-778"


@pytest.fixture
def system(tmp_path, monkeypatch):
    for name, value in (
        ("DEFAULT_ACCOUNTS_PATH", tmp_path / "accounts.json"), ("DEFAULT_CONFIG_PATH", tmp_path / "config.json"),
        ("DEFAULT_LEDGER_PATH", tmp_path / "state.json"), ("DEFAULT_STAGING_DIR", tmp_path / "staging"),
        ("DEFAULT_BACKUPS_DIR", tmp_path / "backups"),
    ):
        monkeypatch.setattr(cc, name, value)
    return su.build_live_system(Settings.from_env(
        env={}, memory="ephemeral", profile_path="does-not-exist-USER.md",
        artifacts_dir=str(tmp_path / "artifacts"), media_asset_dir=str(tmp_path / "assets"),
        knowledge_dir=str(tmp_path / "knowledge"), documents_dir=str(tmp_path / "docs"),
    ))


def _cmd(system, command):
    return cc.run_command(system.centre, command, 100, system.channel, system.conversation)


class TestAStoredAccountIsWhatABackendIsBuiltWith:
    def test_use_builds_the_credentialed_backend_with_the_stored_account(self, system):
        pytest.importorskip("elevenlabs", reason="elevenlabs SDK not installed")
        assert "FAILED" in _cmd(system, "provider speech_provider use elevenlabs")
        assert "add one here first" in _cmd(system, "provider speech_provider use elevenlabs")
        _cmd(system, f"provider speech_provider add elevenlabs studio {SECRET}")
        out = _cmd(system, "provider speech_provider use elevenlabs")
        assert out.startswith("Saved") and system.centre.runtime.settings.speech == "elevenlabs"
        assert SECRET not in out

    def test_validate_without_a_typed_credential_checks_the_stored_account(self, system):
        pytest.importorskip("elevenlabs", reason="elevenlabs SDK not installed")
        _cmd(system, f"provider speech_provider add elevenlabs studio {SECRET}")
        assert _cmd(system, "provider speech_provider validate elevenlabs").startswith("OK")


class TestRevokingTheActiveAccountRetiresItsBackend:
    def test_a_voice_backend_left_with_no_account_is_no_longer_used(self, system):
        pytest.importorskip("elevenlabs", reason="elevenlabs SDK not installed")
        _cmd(system, f"provider speech_provider add elevenlabs studio {SECRET}")
        _cmd(system, "provider speech_provider use elevenlabs")
        out = _cmd(system, "provider speech_provider revoke elevenlabs studio")
        assert "no longer used" in out and system.centre.runtime.settings.speech == "none"
        status, body, _ = su.route_speech(system.manager.own_skills.name, "Hello.", system)
        assert status == 409

    def test_the_reasoning_provider_left_with_no_account_is_no_longer_used(self, system):
        _cmd(system, f"provider llm_provider add anthropic work {SECRET}")
        _cmd(system, "provider llm_provider use anthropic")
        out = _cmd(system, "provider llm_provider revoke anthropic work")
        assert "no longer used" in out and system.centre.runtime.settings.provider == "null"

    def test_revoking_a_backend_that_is_not_active_changes_nothing_else(self, system):
        _cmd(system, "provider speech_provider use windows")
        _cmd(system, f"provider speech_provider add elevenlabs studio {SECRET}")
        out = _cmd(system, "provider speech_provider revoke elevenlabs studio")
        assert "no longer used" not in out and system.centre.runtime.settings.speech == "windows"


class TestCredentialsAtRest:
    def test_no_credential_is_written_in_plaintext_on_windows(self, tmp_path):
        import sys

        store = CredentialStore()
        store.add(ProviderAccount(provider="elevenlabs", account_id="a", credential=SECRET))
        path = tmp_path / "accounts.json"
        save_credential_store(store, path)
        if sys.platform == "win32":
            assert SECRET not in path.read_text(encoding="utf-8")
        assert load_credential_store(path).active("elevenlabs").credential == SECRET

    def test_an_earlier_plaintext_file_still_loads(self, tmp_path):
        path = tmp_path / "accounts.json"
        path.write_text(json.dumps({"accounts": [{"provider": "p", "account_id": "a", "credential": "legacy", "model": None}],
                                    "active": {"p": "a"}}), encoding="utf-8")
        assert load_credential_store(path).active("p").credential == "legacy"


def test_the_null_providers_label_is_never_an_answer():
    from src.core.contracts import Task
    from src.manager.brain import BrainAction
    from src.manager.llm_brain import LLMManagerBrain
    from src.providers.null_provider import NullProvider

    decision = LLMManagerBrain(NullProvider()).converse(Task(description="Explain how rainbows form"))
    assert decision.action is not BrainAction.REPORT and "[null-provider]" not in decision.reason


class TestVoiceManagementFromTheControlCenter:
    def test_variant_style_clear_and_release(self, system):
        name = system.manager.own_skills.name
        # the default provider has no voices: refused honestly, nothing claimed
        assert _cmd(system, f"voice {name} variant british").startswith("FAILED")
        assert "release" in _cmd(system, f"voice {name}")
        assert "already choosing" in _cmd(system, f"voice {name} release")
        assert "cleared" in _cmd(system, f"voice {name} accent clear")
        assert "cleared" in _cmd(system, f"voice {name} style clear")
        assert "does not name a regional variety" in _cmd(system, f"voice {name} variant purple")


class TestThePageOffersEveryProviderAction:
    def test_use_and_check_are_in_the_provider_form(self):
        assert '<option value="use">' in PAGE and '<option value="check">' in PAGE
        assert 'command = "provider " + category + " use " + backend;' in PAGE

    def test_the_voice_form_uses_the_voice_command(self):
        assert 'id="cc-voice-go"' in PAGE and 'runCommand("voice " + agent + " release")' in PAGE
