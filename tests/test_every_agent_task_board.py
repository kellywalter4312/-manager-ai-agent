"""Post-Pre-Stage-9 D4c - every agent's Workspace task board works for that agent.

Found in a real browser: a task addressed to a specialist (what the page's own
board input sends) listed his inbox instead. Real isolated systems through the
production composition; Control Center paths redirected to temp files.
"""

from __future__ import annotations

import pytest

import scripts.control_center as cc
import scripts.serve_ui as su
from scripts.exchange import exchange
from src.agents.cognitive_agent import CognitiveAgent
from src.agents.memory_policy import SpecialistMemoryPolicy
from src.config.settings import Settings
from src.control_center.snapshot import snapshot
from src.core.contracts import AgentSpec, TaskResult
from src.services.memory.ephemeral import EphemeralMemory
from src.services.memory.memory import MemoryRecord
from src.services.memory.task_board import task_board_reply


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    for name, value in (
        ("DEFAULT_ACCOUNTS_PATH", tmp_path / "accounts.json"),
        ("DEFAULT_CONFIG_PATH", tmp_path / "config.json"),
        ("DEFAULT_LEDGER_PATH", tmp_path / "state.json"),
        ("DEFAULT_STAGING_DIR", tmp_path / "staging"),
        ("DEFAULT_BACKUPS_DIR", tmp_path / "backups"),
    ):
        monkeypatch.setattr(cc, name, value)
    return tmp_path


def _system(tmp_path):
    return su.build_live_system(Settings.from_env(
        env={}, memory="ephemeral", profile_path="does-not-exist-USER.md",
        artifacts_dir=str(tmp_path / "artifacts"), media_asset_dir=str(tmp_path / "assets"),
        knowledge_dir=str(tmp_path / "knowledge"), documents_dir=str(tmp_path / "docs"),
    ))


def _say(system, text, to=None):
    return exchange(system.channel, system.conversation, text, addressed=to).outcome


def _board(system, name):
    agent = next(a for a in snapshot(system.centre)["agents"] if a["name"] == name)
    return [(t["content"], t["status"]) for t in agent["tasks"]]


class Coder(CognitiveAgent):
    """A specialist that does not exist yet - no board code of its own."""

    @property
    def name(self):
        return "Coder"

    @property
    def spec(self):
        return AgentSpec(name="Coder", description="writes code", capabilities=("code",))

    def execute(self, task):
        return TaskResult.success(task, "Coder", "domain work")


class TestASpecialistsBoard:
    def test_a_task_addressed_to_a_specialist_lands_on_its_own_board(self, isolated):
        system = _system(isolated)
        manager = system.manager.own_skills.name
        assert _say(system, "add a task to sort the inbox by client", to="Arthur") == "Added: sort the inbox by client"
        assert ("sort the inbox by client", "pending") in _board(system, "Arthur")
        assert not any("sort the inbox" in content for content, _ in _board(system, manager))

    def test_the_whole_lifecycle_on_a_specialists_board(self, isolated):
        system = _system(isolated)
        _say(system, "add a task to sort the inbox by client", to="Arthur")
        assert "[ ] sort the inbox by client" in _say(system, "what are my tasks", to="Arthur")
        assert _say(system, "complete the task sort the inbox by client", to="Arthur").startswith("Completed")
        assert ("sort the inbox by client", "complete") in _board(system, "Arthur")
        assert _say(system, "reopen the task sort the inbox by client", to="Arthur").startswith("Reopened")
        assert ("sort the inbox by client", "pending") in _board(system, "Arthur")
        renamed = _say(system, "rename the task sort the inbox by client to sort the inbox by date", to="Arthur")
        assert renamed.startswith("Renamed"), renamed
        assert ("sort the inbox by date", "pending") in _board(system, "Arthur")

    def test_the_managers_board_is_unchanged(self, isolated):
        system = _system(isolated)
        manager = system.manager.own_skills.name
        assert _say(system, "add a task to water the plants").startswith("Added")
        assert ("water the plants", "pending") in _board(system, manager)
        assert _board(system, "Arthur") == []

    def test_a_specialist_still_does_its_own_work(self, isolated):
        system = _system(isolated)
        outcome = _say(system, "check my inbox", to="Arthur")
        assert "message(s)" in outcome and not outcome.startswith("Added")
        assert _board(system, "Arthur") == []

    def test_a_timed_reminder_is_declined_honestly_never_stored_as_if_it_would_ring(self, isolated):
        system = _system(isolated)
        outcome = _say(system, "remind me to call the client at 5pm", to="Arthur")
        assert "reminder" in outcome.lower()
        assert _board(system, "Arthur") == []


class TestTheBoardService:
    def _parts(self):
        memory = EphemeralMemory()
        policy = SpecialistMemoryPolicy()

        def retain(record):
            verdict = policy.evaluate(record)
            if verdict.allowed:
                memory.remember(record)
            return verdict

        return memory, dict(agent_name="X", recall=memory.recall, retain=retain, forget=memory.forget)

    def test_a_loose_remark_is_not_a_board_request(self):
        _memory, parts = self._parts()
        assert task_board_reply("I finished reading my emails", **parts) is None
        assert task_board_reply("hello there", **parts) is None
        explicit = task_board_reply("complete the task ghost", **parts)
        assert explicit is not None and not explicit.ok

    def test_the_specialist_policy_admits_an_explicit_task_and_only_as_a_request(self):
        _memory, parts = self._parts()
        added = task_board_reply("add a task to draft the release notes", **parts)
        assert added.ok and added.text == "Added: draft the release notes"
        policy = SpecialistMemoryPolicy()
        assert policy.evaluate(MemoryRecord(content="draft notes", kind="user_task", context={"confidence": 1.0})).allowed
        assert not policy.evaluate(MemoryRecord(content="draft notes", kind="user_task")).allowed


class TestAFutureSpecialist:
    def test_a_specialist_built_later_has_a_working_board_with_no_code_of_its_own(self, isolated):
        system = _system(isolated)
        coder = Coder()
        system.manager.registry.register(coder)
        assert _say(system, "add a task to write the parser", to="Coder") == "Added: write the parser"
        assert [r.content for r in coder.memory.recall(kind="user_task", tags=set(), limit=10)] == ["write the parser"]
        assert _say(system, "write the parser function", to="Coder") == "domain work"
        assert ("write the parser", "pending") in _board(system, "Coder")
