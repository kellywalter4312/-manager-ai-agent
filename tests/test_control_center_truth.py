"""The Control Center must describe the system it is actually running.

Defects found by driving the real Control Center rather than reading it
(2026-09-15), each one a case of the screen or the record saying something
other than what happened:

* a governed backend switch that really happened was written to the durable
  audit trail as ``failed``;
* choosing a vendor announced a live switch without saying that the *other*
  reasoning axis was still off, so Alfred kept answering "no reasoning
  capability is configured" with nothing on screen to explain it;
* a withdrawn surface vanished from the governance inventory, so it could
  never be granted back through the same propose/run it was withdrawn with;
"""

from __future__ import annotations

import pytest

import scripts.control_center as cc
from src.config.settings import Settings


@pytest.fixture
def isolated_stores(tmp_path, monkeypatch):
    monkeypatch.setattr(cc, "DEFAULT_ACCOUNTS_PATH", tmp_path / "accounts.json")
    monkeypatch.setattr(cc, "DEFAULT_CONFIG_PATH", tmp_path / "config.json")
    monkeypatch.setattr(cc, "DEFAULT_LEDGER_PATH", tmp_path / "state.json")
    monkeypatch.setattr(cc, "DEFAULT_STAGING_DIR", tmp_path / "staging")
    monkeypatch.setattr(cc, "DEFAULT_BACKUPS_DIR", tmp_path / "backups")
    return tmp_path


def _settings(tmp_path, **overrides):
    return Settings.from_env(
        env={}, memory=overrides.pop("memory", "ephemeral"), profile_path="does-not-exist-USER.md",
        artifacts_dir=str(tmp_path / "artifacts"), media_asset_dir=str(tmp_path / "assets"),
        knowledge_dir=str(tmp_path / "knowledge"), documents_dir=str(tmp_path / "docs"), **overrides,
    )


def _command(centre, channel, conversation, command, log=None):
    return cc.run_command(centre, command, 100, channel, conversation, action_log_path=log)


class TestTheAuditTrailRecordsWhatHappened:
    """`provider <category> use` and `workspace <agent> add|remove|use` are
    administrative writes; both were recorded as failures whatever they did."""

    def test_a_real_backend_switch_is_recorded_as_a_success(self, isolated_stores):
        from src.services.audit.action_log import load_action_log

        log = isolated_stores / "actions.json"
        runtime, manager, channel, conversation = cc.build(_settings(isolated_stores))
        centre = cc.ControlCenter(runtime, manager)
        saved = _command(centre, channel, conversation, "provider translation_provider use argos", log=log)
        assert saved.startswith("Saved: "), saved
        switched = _command(centre, channel, conversation, "provider llm_provider use null", log=log)
        assert switched.startswith("Switched the reasoning provider"), switched
        entries = load_action_log(log)
        assert [e.capability for e in entries] == ["admin.provider", "admin.provider"]
        assert all(e.succeeded for e in entries), [e.describe() for e in entries]

    def test_a_refused_switch_is_still_recorded_as_a_failure(self, isolated_stores):
        from src.services.audit.action_log import load_action_log

        log = isolated_stores / "actions.json"
        runtime, manager, channel, conversation = cc.build(_settings(isolated_stores))
        centre = cc.ControlCenter(runtime, manager)
        refused = _command(centre, channel, conversation, "provider llm_provider use nonesuch", log=log)
        assert "nonesuch" in refused and not refused.startswith("Switched")
        assert [e.succeeded for e in load_action_log(log)] == [False]

    def test_workspace_writes_are_recorded_by_what_they_did(self, isolated_stores):
        from src.services.audit.action_log import load_action_log

        log = isolated_stores / "actions.json"
        runtime, manager, channel, conversation = cc.build(_settings(isolated_stores))
        centre = cc.ControlCenter(runtime, manager)
        removed = _command(centre, channel, conversation, "workspace Arthur remove diagram", log=log)
        assert "Saved - " in removed, removed
        refused = _command(centre, channel, conversation, "workspace Arthur remove diagram", log=log)
        assert "does not have" in refused, refused
        assert [e.succeeded for e in load_action_log(log)] == [True, False]


class TestTheScreenNamesTheOtherReasoningAxis:
    """A vendor alone never turns reasoning on - that is deliberate. Saying
    nothing about it is what made the screen misleading."""

    def test_choosing_a_vendor_while_the_brain_is_off_says_so(self, isolated_stores):
        runtime, manager, channel, conversation = cc.build(_settings(isolated_stores))
        centre = cc.ControlCenter(runtime, manager)
        out = _command(centre, channel, conversation, "provider llm_provider use local")
        assert "Switched the reasoning provider" in out
        assert "still 'null'" in out and "ecosystem brain.manager propose llm" in out, out

    def test_no_note_when_the_two_axes_agree(self, isolated_stores):
        runtime, manager, channel, conversation = cc.build(_settings(isolated_stores))
        centre = cc.ControlCenter(runtime, manager)
        out = _command(centre, channel, conversation, "provider llm_provider use null")
        assert "Note:" not in out, out


class TestAWithdrawnSurfaceCanBeGrantedBack:
    def test_it_stays_visible_in_the_inventory_and_returns(self, isolated_stores):
        import re

        runtime, manager, channel, conversation = cc.build(_settings(isolated_stores))
        centre = cc.ControlCenter(runtime, manager)

        def run(command):
            return _command(centre, channel, conversation, command)

        assert "surface.arthur.mailbox" in run("ecosystem")
        proposal = re.search(r"proposed '([^']+)'", run(
            "ecosystem surface.arthur.mailbox propose withdrawn low not needed|less surface")).group(1)
        assert run(f"ecosystem surface.arthur.mailbox run {proposal} yes").startswith("OK:")

        inventory = run("ecosystem")
        line = [l for l in inventory.splitlines() if "surface.arthur.mailbox" in l]
        assert line, "a withdrawn surface must stay visible, or it can never be granted back"
        assert "withdrawn" in line[0]

        back = re.search(r"proposed '([^']+)'", run(
            "ecosystem surface.arthur.mailbox propose implemented low wanted again|the owner asked")).group(1)
        assert run(f"ecosystem surface.arthur.mailbox run {back} yes").startswith("OK:")
        restored = [l for l in run("ecosystem").splitlines() if "surface.arthur.mailbox" in l]
        assert restored and "implemented" in restored[0], restored


class TestStopRecordingSaysWhichNothingItMeans:
    """"start recording" then "stop recording" in a terminal answered
    "Nothing is currently recording" - true, and useless: the session was
    sitting in `requested`, waiting for a Recording control that a terminal
    does not have, and stayed there."""

    def test_a_waiting_request_is_named_and_cleared(self, isolated_stores):
        runtime, manager, channel, conversation = cc.build(_settings(isolated_stores))
        centre = cc.ControlCenter(runtime, manager)
        started = _command(centre, channel, conversation, "talk start recording")
        assert "Ready to record" in started
        stopped = " ".join(_command(centre, channel, conversation, "talk stop recording").split())
        assert "still waiting for the Recording control" in stopped
        assert "cleared that request" in stopped
        sessions = manager.own_skills._recording_sessions
        assert sessions.get(manager.own_skills.name).state.value == "cancelled"
        again = _command(centre, channel, conversation, "talk start recording")
        assert "Ready to record" in again
