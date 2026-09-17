"""Whole-ecosystem closure campaign 2 - workspaces the owner creates in the
Control Center, which agents move between and work inside.

Workspaces are data (``workspace_catalog``): created, renamed, described,
profiled, archived, restored and removed from the Control Center, the built-in
rows included. A capability profile scopes the work an agent does while in a
workspace; conversation, self-description, memory and movement are available
everywhere. The same mechanism covers the Manager, today's specialist and a
specialist registered later. Real systems through the production composition,
the Control Center's config paths redirected to temp files. The served run with
the real composition is recorded in REQUIREMENT_LEDGER.md.
"""

from __future__ import annotations

import json

import pytest

import scripts.control_center as cc
import scripts.serve_ui as su
from scripts.exchange import exchange
from src.agents.base_agent import BaseAgent
from src.config.settings import Settings
from src.control_center.snapshot import snapshot
from src.control_center.workspace_catalog import (
    DEFAULT_WORKSPACE_TYPES,
    WorkspaceType,
    active_workspace_for,
    all_workspaces,
    find_workspace,
    workspace_catalog,
    workspace_refusal,
    workspaces_for,
)
from src.core.contracts import AgentSpec, Task, TaskResult
from src.manager.capabilities import (
    MANAGER_CAPABILITIES,
    WORKSPACE_NAVIGATION,
    select_capability,
    workspace_navigation_intent,
)
from src.services.artifacts.registration import register_new_artifact
from src.services.memory.ephemeral import EphemeralMemory


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


def _cmd(system, command):
    return cc.run_command(system.centre, command, 100, system.channel, system.conversation)


def _say(system, text):
    return exchange(system.channel, system.conversation, text)


def _agent(system, name):
    return next(a for a in snapshot(system.centre)["agents"] if a["name"] == name)


class Surveyor(BaseAgent):
    """A specialist nobody has written yet."""

    def __init__(self):
        self.memory = EphemeralMemory()

    @property
    def name(self):
        return "Surveyor"

    @property
    def spec(self):
        return AgentSpec(name="Surveyor", description="measures land", capabilities=("survey_plot", "measure_boundary"))

    def execute(self, task):
        return TaskResult.success(task, agent_name="Surveyor", output="surveyed")


class TestDefinitionsAreData:
    def test_saved_definitions_extend_or_change_the_seeds_and_archive_hides_them(self):
        values = {
            "workspace_type.greenhouse": json.dumps({"label": "Greenhouse", "panels": ["tasks", "produced"],
                                                     "capabilities": ["create_task"], "description": "Plants."}),
            "workspace_type.board": json.dumps({"label": "Kanban", "archived": True}),
            "workspace_type.legacy": "Legacy|artifacts:any",
        }
        every = {t.workspace_id: t for t in all_workspaces(values)}
        assert every["board"].label == "Kanban" and every["board"].archived and every["board"].panels == ("tasks",)
        assert every["greenhouse"].capabilities == ("create_task",) and every["legacy"].panels == ("artifacts:any",)
        assert "board" not in [t.workspace_id for t in workspace_catalog(values)]
        assert "board" not in [t.workspace_id for t in workspaces_for("Anyone", values)]

    def test_a_definition_round_trips_through_its_saved_value(self):
        defined = WorkspaceType("lab", "Research lab", "For experiments", ("tasks", "produced"), ("translate_media",))
        again = {t.workspace_id: t for t in all_workspaces({"workspace_type.lab": defined.to_value()})}["lab"]
        assert again == defined

    def test_a_workspace_is_found_by_how_a_person_names_it(self):
        values = {"workspace_type.lab": WorkspaceType("lab", "Research lab", "", ("tasks",)).to_value()}
        assert find_workspace(values, "research lab").workspace_id == "lab"
        assert find_workspace(values, "Research").workspace_id == "lab"
        assert find_workspace(values, "lab").workspace_id == "lab"
        assert find_workspace(values, "kitchen") is None

    def test_an_empty_profile_permits_everything_and_a_profile_scopes(self):
        values = {"workspace_type.lab": WorkspaceType("lab", "Lab", "", ("tasks",), ("create_task",)).to_value(),
                  "workspace_active.alfred": "lab"}
        assert workspace_refusal("Alfred", values, ("create_task",)) is None
        refusal = workspace_refusal("Alfred", values, ("current_datetime",))
        assert "Lab workspace" in refusal and "create_task" in refusal and "go to the" in refusal
        assert workspace_refusal("Arthur", values, ("current_datetime",)) is None  # Arthur is not in the lab


class TestMovementIntent:
    @pytest.mark.parametrize("text", [
        "Alfred, go to the greenhouse workspace.", "Switch to your drafting workspace please",
        "Take me to the workspace called Night Shift", "Which workspace are you in?", "What workspaces do you have?",
    ])
    def test_moving_and_asking_are_claimed(self, text):
        assert workspace_navigation_intent(Task(description=text))
        assert select_capability(Task(description=text), MANAGER_CAPABILITIES) is WORKSPACE_NAVIGATION

    @pytest.mark.parametrize("text", ["My workspace at the office is cold.", "I set up a new workspace yesterday."])
    def test_talk_about_a_workspace_is_not_a_move(self, text):
        assert not workspace_navigation_intent(Task(description=text))

    def test_the_handlers_that_answer_anywhere_are_declared_on_the_capability(self):
        anywhere = {name for name, cap in MANAGER_CAPABILITIES.items() if cap.anywhere}
        assert {"workspace_navigation", "small_talk", "about_self", "recall_memory"} <= anywhere
        assert "current_datetime" not in anywhere and "create_diagram" not in anywhere


class TestControlCenterToRuntime:
    def test_create_move_scope_rename_archive_restore_remove(self, isolated):
        system = _system(isolated)
        created = _cmd(system, "workspace create greenhouse Greenhouse|tasks,produced|create_task,list_tasks")
        assert created.startswith("Created the workspace 'greenhouse'") and "Agents who have it: Alfred, Arthur" in created
        assert _cmd(system, "workspace create nope Nope|tasks|levitate").startswith("FAILED: no capability called 'levitate'")
        assert "greenhouse: Greenhouse - panels tasks, produced; capabilities: create_task, list_tasks" in _cmd(system, "workspace")

        turn = _say(system, "Go to the greenhouse workspace.")
        assert turn.capability == "workspace_navigation" and "Greenhouse workspace now" in turn.outcome
        assert _agent(system, "Alfred")["active_workspace"] == "greenhouse"

        refused = _say(system, "What time is it?")
        assert "Greenhouse workspace I'm in" in refused.outcome
        assert "Added" in _say(system, "Add a task: water the basil").outcome

        _cmd(system, "workspace rename greenhouse Glasshouse")
        assert "Glasshouse workspace" in _say(system, "Which workspace are you in?").outcome
        _cmd(system, "workspace profile greenhouse all")
        assert "workspace I'm in" not in _say(system, "What time is it?").outcome

        assert "Archived" in _cmd(system, "workspace archive greenhouse")
        assert "archived" in _say(system, "Go to the glasshouse workspace").outcome
        assert _agent(system, "Alfred")["active_workspace"] != "greenhouse"
        assert "Restored" in _cmd(system, "workspace restore greenhouse")
        assert "archive it instead" in _cmd(system, "workspace remove board")
        assert _cmd(system, "workspace remove greenhouse").startswith("Removed")

    def test_artifacts_record_the_workspace_they_were_made_in(self, isolated):
        system = _system(isolated)
        _cmd(system, "workspace Alfred use coding")
        artifact = register_new_artifact(system.runtime.artifacts, type="code", owner_agent="Alfred", title="x.py",
                                         content_bytes=b"print(1)", rendition_format="py", rendition_bytes=b"print(1)")
        explicit = register_new_artifact(system.runtime.artifacts, type="code", owner_agent="Alfred", title="y.py",
                                         content_bytes=b"", rendition_format="py", rendition_bytes=b"",
                                         metadata={"workspace": "documents"})
        by_id = {a["artifact_id"]: a for a in snapshot(system.centre)["artifacts"]}
        assert by_id[artifact.artifact_id]["workspace"] == "coding"
        assert by_id[explicit.artifact_id]["workspace"] == "documents"

    def test_the_page_route_and_the_control_center_are_one_saved_fact(self, isolated):
        system = _system(isolated)
        status, body, _ = su.route_workspace_select("Arthur", "coding", system)
        assert status == 200 and json.loads(body)["active"] == "coding"
        assert "coding (open)" in _cmd(system, "workspace").split("Arthur", 1)[1]
        assert su.route_workspace_select("Arthur", "hologram", system)[0] == 400
        assert su.route_workspace_select("Nobody", "coding", system)[0] == 400
        assert su.route_workspace_select("", "coding", system)[0] == 400
        assert active_workspace_for("Alfred", cc.load_governed_config(cc.DEFAULT_CONFIG_PATH).values) == DEFAULT_WORKSPACE_TYPES[0].workspace_id

    def test_definition_writes_are_confirmed_administrative_writes(self):
        for command in ("workspace create lab Lab|tasks", "workspace rename lab Bench", "workspace archive lab",
                        "workspace profile lab all", "workspace remove lab"):
            is_write, description = cc.classify_administrative_write(command)
            assert is_write and "lab" in description, command
        assert cc.classify_administrative_write("workspace Arthur remove coding")[0]


class TestEveryAgentPresentAndFuture:
    def test_a_specialist_moves_and_is_scoped_through_the_same_mechanism(self, isolated):
        system = _system(isolated)
        _cmd(system, "workspace create careers Careers desk|tasks|jobs,vacancy")
        turn = _say(system, "Arthur, go to the careers desk workspace.")
        assert "Careers desk workspace now" in turn.outcome and _agent(system, "Arthur")["active_workspace"] == "careers"
        assert _agent(system, "Alfred")["active_workspace"] == "board"
        assert "Careers desk workspace I'm in" in _say(system, "Arthur, check my inbox for new emails").outcome

    def test_a_future_specialist_needs_no_change_and_fake_agents_are_refused(self, isolated):
        system = _system(isolated)
        assert _cmd(system, "workspace create field Field office|tasks|survey_plot").startswith("FAILED")
        system.manager.registry.register(Surveyor())
        assert _cmd(system, "workspace create field Field office|tasks|survey_plot").startswith("Created")
        turn = _say(system, "Surveyor, go to the field office workspace")
        assert "Field office workspace now" in turn.outcome and _agent(system, "Surveyor")["active_workspace"] == "field"
        assert "no agent called" in _cmd(system, "workspace Phantom add field")
        tool = system.manager.own_skills.tools
        rejected = tool.invoke("workspace", {"operation": "enter", "content": json.dumps({"agent": "Phantom", "workspace": "field"})})
        assert not rejected.ok and "no agent called 'Phantom'" in rejected.error

    def test_a_workspace_holds_no_agent_state(self):
        fields = set(WorkspaceType.__dataclass_fields__)
        assert fields == {"workspace_id", "label", "description", "panels", "capabilities", "archived"}
