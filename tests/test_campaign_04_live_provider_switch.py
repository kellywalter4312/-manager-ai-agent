"""Pre-Stage-9 Campaign 4 - switching the reasoning provider never recreates
Alfred, and the Control Center manages it live.

Real isolated systems through the production composition (``build`` /
``build_live_system``), with the Control Center's credential and config stores
redirected to temp files. The switched-to vendor is constructed from a stored
account with a fake key - construction only: no turn is taken while a vendor
backend is live, so nothing is ever sent anywhere (the suite blocks the network
regardless). Whether a real vendor then answers is BLOCKED on an unfunded key
and is not claimed here.
"""

from __future__ import annotations

import json

import pytest

import scripts.control_center as cc
import scripts.serve_ui as su
from scripts.exchange import exchange
from src.config.settings import Settings
from src.services.memory.personal_context import all_current_personal_context

FAKE_KEY = "fake-test-key-not-a-real-credential"


@pytest.fixture
def isolated_stores(tmp_path, monkeypatch):
    monkeypatch.setattr(cc, "DEFAULT_ACCOUNTS_PATH", tmp_path / "accounts.json")
    monkeypatch.setattr(cc, "DEFAULT_CONFIG_PATH", tmp_path / "config.json")
    return tmp_path


def _settings(tmp_path, **overrides):
    return Settings.from_env(
        env={}, memory=overrides.pop("memory", "ephemeral"), profile_path="does-not-exist-USER.md",
        artifacts_dir=str(tmp_path / "artifacts"), media_asset_dir=str(tmp_path / "assets"),
        knowledge_dir=str(tmp_path / "knowledge"), documents_dir=str(tmp_path / "docs"), **overrides,
    )


def _command(centre, channel, conversation, command):
    return cc.run_command(centre, command, 100, channel, conversation)


def _talk(channel, conversation, manager):
    exchange(channel, conversation, "My favourite colour is teal")
    exchange(channel, conversation, "what's the date today?")
    return len(conversation.transcript())


class TestASwitchKeepsAlfred:
    def test_use_switches_live_and_everything_else_continues(self, isolated_stores):
        runtime, manager, channel, conversation = cc.build(_settings(isolated_stores))
        centre = cc.ControlCenter(runtime, manager)
        lines_before = _talk(channel, conversation, manager)
        skills, memory, sessions = manager.own_skills, manager.memory, manager.own_skills._activity_sessions
        identity, knowledge = runtime.identity, manager.own_skills.user_knowledge
        arthur = manager.registry.get("Arthur")
        arthur_brain_before = arthur.brain

        added = _command(centre, channel, conversation, f"provider llm_provider add anthropic owner {FAKE_KEY}")
        assert "added" in added and FAKE_KEY not in added
        switched = _command(centre, channel, conversation, "provider llm_provider use anthropic")
        assert switched.startswith("Switched the reasoning provider from 'null' to 'anthropic'"), switched

        assert centre.runtime.provider.name == "anthropic"
        assert centre.manager is manager and manager.own_skills is skills
        assert manager.memory is memory and skills._activity_sessions is sessions
        assert centre.runtime.identity is identity and skills.user_knowledge is knowledge
        assert len(conversation.transcript()) == lines_before
        assert any("teal" in item.content for item in all_current_personal_context(recall=memory.recall))
        assert arthur.brain is not arthur_brain_before
        assert skills._converse == manager.brain.converse

        back = _command(centre, channel, conversation, "provider llm_provider use null")
        assert back.startswith("Switched the reasoning provider from 'anthropic' to 'null'")
        turn = exchange(channel, conversation, "what's my favourite colour?")
        assert turn.succeeded and "teal" in turn.outcome
        assert len(conversation.transcript()) == lines_before + 2

    def test_a_failed_switch_changes_nothing_and_saves_nothing(self, isolated_stores):
        runtime, manager, channel, conversation = cc.build(_settings(isolated_stores))
        centre = cc.ControlCenter(runtime, manager)
        brain_before = manager.brain
        result = _command(centre, channel, conversation, "provider llm_provider use openai")
        assert result.startswith("FAILED"), result
        assert "Nothing changed" in result
        assert centre.runtime.provider.name == "null" and manager.brain is brain_before
        assert cc.load_governed_config(cc.DEFAULT_CONFIG_PATH).get(cc.CHOSEN_LLM_BACKEND) is None

    def test_the_choice_is_what_the_next_start_uses(self, isolated_stores):
        runtime, manager, channel, conversation = cc.build(_settings(isolated_stores))
        centre = cc.ControlCenter(runtime, manager)
        _command(centre, channel, conversation, f"provider llm_provider add anthropic owner {FAKE_KEY}")
        _command(centre, channel, conversation, "provider llm_provider use anthropic")
        restarted, _manager, _channel, _conversation = cc.build(_settings(isolated_stores))
        assert restarted.provider.name == "anthropic"

    def test_an_account_change_for_the_active_backend_applies_live(self, isolated_stores):
        runtime, manager, channel, conversation = cc.build(_settings(isolated_stores))
        centre = cc.ControlCenter(runtime, manager)
        _command(centre, channel, conversation, f"provider llm_provider add anthropic first {FAKE_KEY}")
        _command(centre, channel, conversation, "provider llm_provider use anthropic")
        provider_before = centre.runtime.provider
        added = _command(centre, channel, conversation, f"provider llm_provider add anthropic second {FAKE_KEY}-2")
        assert "Applied live" in added and "Persisted only" not in added
        assert centre.runtime.provider is not provider_before

    def test_terminal_reload_of_a_reasoning_setting_rebinds_in_place(self, isolated_stores, capsys):
        runtime, manager, channel, conversation = cc.build(_settings(isolated_stores))
        centre = cc.ControlCenter(runtime, manager)
        lines_before = _talk(channel, conversation, manager)
        brain_before = manager.brain
        reloaded = cc._reload_session(centre, "reload provider=null", channel=channel, conversation=conversation)
        assert reloaded == (centre, channel, conversation)
        assert "switched in place" in capsys.readouterr().out
        assert centre.manager is manager and manager.brain is not brain_before
        assert len(conversation.transcript()) == lines_before


class TestTheReasoningModeIsGovernedLive:
    """Which vendor answers and whether an agent reasons with it are separate
    axes; both are managed from the Control Center and both apply live."""

    def _proposal(self, output):
        import re

        match = re.search(r"proposed '([^']+)'", output)
        assert match, output
        return match.group(1)

    def _isolate_ledger(self, isolated_stores, monkeypatch):
        monkeypatch.setattr(cc, "DEFAULT_LEDGER_PATH", isolated_stores / "state.json")
        monkeypatch.setattr(cc, "DEFAULT_STAGING_DIR", isolated_stores / "staging")
        monkeypatch.setattr(cc, "DEFAULT_BACKUPS_DIR", isolated_stores / "backups")

    def test_full_brain_is_turned_on_and_off_in_place(self, isolated_stores, monkeypatch):
        from src.manager.brain import NullBrain

        self._isolate_ledger(isolated_stores, monkeypatch)
        runtime, manager, channel, conversation = cc.build(_settings(isolated_stores))
        centre = cc.ControlCenter(runtime, manager)
        lines_before = _talk(channel, conversation, manager)
        _command(centre, channel, conversation, f"provider llm_provider add anthropic owner {FAKE_KEY}")
        _command(centre, channel, conversation, "provider llm_provider use anthropic")
        assert isinstance(manager.brain, NullBrain), "a vendor alone never turns reasoning on"

        proposal = self._proposal(_command(centre, channel, conversation, "ecosystem brain.manager propose llm low reason with the provider|full brain"))
        ran = _command(centre, channel, conversation, f"ecosystem brain.manager run {proposal} yes")
        assert ran.startswith("OK") and "Applied live" in ran, ran
        assert not isinstance(manager.brain, NullBrain)
        assert centre.manager is manager and len(conversation.transcript()) == lines_before

        restarted, restarted_manager, _channel, _conversation = cc.build(_settings(isolated_stores))
        assert restarted.settings.brain == "llm" and not isinstance(restarted_manager.brain, NullBrain)

        rolled = _command(centre, channel, conversation, f"ecosystem brain.manager rollback {proposal} yes")
        assert rolled.startswith("OK"), rolled
        _command(centre, channel, conversation, "provider llm_provider use null")
        assert isinstance(manager.brain, NullBrain)

    def test_an_unknown_mode_is_refused(self, isolated_stores, monkeypatch):
        self._isolate_ledger(isolated_stores, monkeypatch)
        runtime, manager, channel, conversation = cc.build(_settings(isolated_stores))
        centre = cc.ControlCenter(runtime, manager)
        proposal = self._proposal(_command(centre, channel, conversation, "ecosystem brain.manager propose genius low nonsense|none"))
        ran = _command(centre, channel, conversation, f"ecosystem brain.manager run {proposal} yes")
        assert ran.startswith("FAILED"), ran


class TestTheServedControlCenterManagesIt:
    def test_the_switch_is_a_confirmed_write_and_the_held_system_follows(self, isolated_stores):
        system = su.build_live_system(_settings(isolated_stores))
        exchange(system.channel, system.conversation, "My favourite colour is teal")
        lines_before = len(system.conversation.transcript())
        conversation_before = system.conversation

        needs, _ = cc.classify_administrative_write("provider llm_provider use anthropic")
        assert needs

        for command in (f"provider llm_provider add anthropic owner {FAKE_KEY}", "provider llm_provider use anthropic"):
            status, body, _ = su.route_command(command, None, system)
            pending = json.loads(body)
            assert status == 200 and pending["requires_confirmation"] is True
            status, body, _ = su.route_command(command, pending["confirm_token"], system)
            assert status == 200

        assert "Switched the reasoning provider" in json.loads(body)["result"]
        assert system.runtime.provider.name == "anthropic"
        assert system.centre.runtime is system.runtime
        assert system.conversation is conversation_before
        assert len(system.conversation.transcript()) == lines_before
