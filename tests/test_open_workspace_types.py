"""Post-Pre-Stage-9 D4 - workspaces are open-ended types every agent has.

The owner's requirement: every AI agent, today's and every future one, can have
workspaces (a 3D diagram, a coding workspace - examples, not a list); they are
chosen in the Control Center; adding a specialist never takes anything away
from another agent. Real systems through the production composition, the
Control Center's config paths redirected to temp files.
"""

from __future__ import annotations

import pytest

import scripts.control_center as cc
import scripts.serve_ui as su
from src.agents.base_agent import BaseAgent
from src.config.settings import Settings
from src.control_center.interfaces import InterfaceFeature, describe_interface
from src.control_center.snapshot import snapshot
from src.control_center.workspace_catalog import (
    DEFAULT_WORKSPACE_TYPES,
    WorkspaceType,
    active_workspace_for,
    workspace_catalog,
    workspaces_for,
)
from src.core.contracts import AgentSpec, TaskResult
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


def _agent_data(system, name):
    return next(a for a in snapshot(system.centre)["agents"] if a["name"] == name)


def _type_ids(agent_data):
    return [t["id"] for t in agent_data["workspace_types"]]


class FutureSpecialist(BaseAgent):
    """A specialist that does not exist yet - declares no interface at all, and
    owns a memory like every specialist the composition builds."""

    def __init__(self, name="Coder", capabilities=("write_code",)):
        self._name = name
        self._capabilities = tuple(capabilities)
        self.memory = EphemeralMemory()

    @property
    def name(self):
        return self._name

    @property
    def spec(self):
        return AgentSpec(name=self._name, description=f"{self._name} agent", capabilities=self._capabilities)

    def execute(self, task):
        return TaskResult.success(task, agent_name=self._name, output="done")


class TestTheCatalogIsData:
    def test_the_defaults_include_a_3d_diagram_and_a_coding_workspace(self):
        ids = [t.workspace_id for t in DEFAULT_WORKSPACE_TYPES]
        assert "diagram" in ids and "coding" in ids
        assert "diagram3d" in next(t for t in DEFAULT_WORKSPACE_TYPES if t.workspace_id == "diagram").panels

    def test_the_owner_can_add_a_type_from_panels_that_exist(self):
        catalog = workspace_catalog({"workspace_type.research lab": "Research lab|artifacts:any,tasks"})
        added = next(t for t in catalog if t.workspace_id == "research_lab")
        assert added.label == "Research lab" and added.panels == ("artifacts:any", "tasks")

    def test_an_unrenderable_type_is_skipped_never_shown_half_working(self):
        catalog = workspace_catalog({"workspace_type.broken": "Broken|hologram"})
        assert "broken" not in [t.workspace_id for t in catalog]
        with pytest.raises(ValueError):
            WorkspaceType("x", "X", "", ("artifacts",))

    def test_any_agent_name_gets_every_type_until_the_owner_narrows_it(self):
        for name in ("Alfred", "Arthur", "A Specialist Built Next Year"):
            assert [t.workspace_id for t in workspaces_for(name)] == [t.workspace_id for t in workspace_catalog()]
        values = {"workspaces.arthur": "coding,board", "workspace_active.arthur": "board"}
        assert [t.workspace_id for t in workspaces_for("Arthur", values)] == ["coding", "board"]
        assert active_workspace_for("Arthur", values) == "board"
        assert active_workspace_for("Alfred", values) == "board"  # the first of Alfred's own, untouched


class TestEveryAgentHasAWorkspace:
    def test_every_registered_agent_can_render_one_and_carries_its_types(self, isolated):
        system = _system(isolated)
        for agent in system.manager.registry.all_agents():
            assert describe_interface(agent).can_render(InterfaceFeature.WORKSPACE), agent.name
            data = _agent_data(system, agent.name)
            assert _type_ids(data) == [t.workspace_id for t in workspace_catalog()]
            assert data["active_workspace"] == _type_ids(data)[0]

    def test_a_future_specialist_gets_a_workspace_and_takes_nothing_from_the_manager(self, isolated):
        system = _system(isolated)
        manager_name = system.manager.own_skills.name
        capabilities_before = sorted(system.manager.own_skills.capabilities)
        types_before = _type_ids(_agent_data(system, manager_name))

        system.manager.registry.register(FutureSpecialist())

        assert sorted(system.manager.own_skills.capabilities) == capabilities_before
        assert _type_ids(_agent_data(system, manager_name)) == types_before
        coder = _agent_data(system, "Coder")
        assert "coding" in _type_ids(coder)
        assert "workspace" in coder["interface"]["implemented"]
        status, body, _ = su.route_workspace_enter("Coder", system)
        assert status == 200, body


class TestChosenInTheControlCenter:
    def test_workspace_writes_are_confirmed_administrative_writes(self):
        is_write, description = cc.classify_administrative_write("workspace Arthur remove coding")
        assert is_write and "coding" in description and "Arthur" in description
        assert cc.classify_administrative_write("workspace")[0] is False

    def test_remove_add_and_use_are_saved_and_live_for_that_agent_only(self, isolated):
        system = _system(isolated)
        manager_name = system.manager.own_skills.name

        assert "Removed" in _cmd(system, "workspace Arthur remove coding")
        assert "coding" not in _type_ids(_agent_data(system, "Arthur"))
        assert "coding" in _type_ids(_agent_data(system, manager_name))

        assert "does not have" in _cmd(system, "workspace Arthur use coding")
        assert "now has" in _cmd(system, "workspace Arthur add coding")
        assert "now opens on" in _cmd(system, "workspace Arthur use coding")
        arthur = _agent_data(system, "Arthur")
        assert _type_ids(arthur)[-1] == "coding" and arthur["active_workspace"] == "coding"
        assert _agent_data(system, manager_name)["active_workspace"] == "board"

        listing = _cmd(system, "workspace")
        assert "coding (open)" in listing

    def test_unknown_agents_and_types_are_refused_with_what_exists(self, isolated):
        system = _system(isolated)
        assert "no agent called" in _cmd(system, "workspace Nobody add board")
        refused = _cmd(system, "workspace Arthur add hologram")
        assert "no workspace type" in refused and "diagram" in refused

    def test_an_owner_added_type_can_be_given_to_any_agent(self, isolated):
        system = _system(isolated)
        config = cc.load_governed_config(cc.DEFAULT_CONFIG_PATH)
        cc.save_governed_config(config.with_value("workspace_type.research", "Research|artifacts:any"), cc.DEFAULT_CONFIG_PATH)
        assert "research" in _type_ids(_agent_data(system, "Arthur"))
        assert "Removed" in _cmd(system, "workspace Arthur remove research")
        assert "now has" in _cmd(system, "workspace Arthur add research")


class TestThePageRendersTypesGenerically:
    def test_the_page_renders_panels_not_named_types_or_agents(self):
        page = (cc.REPO_ROOT / "ui" / "index.html").read_text(encoding="utf-8") if hasattr(cc, "REPO_ROOT") else \
            open("ui/index.html", encoding="utf-8").read()
        start = page.index("function workspacePanel(")
        body = page[start:page.index("const workspace3DStages")]
        for kind in ('kind === "tasks"', 'kind === "artifacts"', 'kind === "diagram3d"'):
            assert kind in body
        for named in ("Alfred", "Arthur", '"coding"', '"diagram"'):
            assert named not in body
        assert "wireWorkspace3D();" in page and "wireWorkspaceTypes();" in page
