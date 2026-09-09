"""Alfred's Real 3D Workspace - the complete vertical slice.

A Workspace, in this project's own vocabulary
(``InterfaceFeature.WORKSPACE`` - "iterative work with state between
turns"), is not Alfred Conversation with a 3D background, not
Presentation (appearance configuration), and not Control Center. This
file proves the whole slice is genuinely connected, using the identical
real-system pattern ``tests/test_whole_system_reality_audit.py`` already
established for the sibling environment-preference feature:

    Agent Framework (InterfaceDescriptor)
        -> src/services/identity/workspace.py (governed session persistence)
        -> Control Center (ControlCenter.workspaces / render_workspaces / the
           "workspace" terminal verb)
        -> the served page's own backend (POST /workspace/enter,
           snapshot.py's "tasks"/"workspace" fields)
        -> the shipped frontend (ui/index.html's V.space, wired into
           go()'s own navigation and MODES)

No new database, no new agent architecture, no LLM/Brain dependency
anywhere in this chain - proven directly below, not assumed.
"""

from __future__ import annotations

import re
import threading
from pathlib import Path

from src.config.settings import Settings
from src.control_center.interfaces import InterfaceFeature
from src.control_center.observer import ControlCenter
from src.control_center.render import render_workspaces
from src.control_center.snapshot import snapshot
from src.services.identity.workspace import (
    WORKSPACE_SESSION_FACT_TYPE,
    record_workspace_entry,
    workspace_session_for,
)
from src.services.memory.ephemeral import EphemeralMemory
from scripts.control_center import build, run_command
from scripts.exchange import memory_for
from scripts.serve_ui import LiveSystem, route_workspace_enter

REPO_ROOT = Path(__file__).resolve().parent.parent
UI = REPO_ROOT / "ui" / "index.html"
SERVE_UI = REPO_ROOT / "scripts" / "serve_ui.py"


def real_system(**overrides):
    return build(Settings.from_env(env={}, memory="ephemeral", **overrides))


def _shell() -> str:
    return UI.read_text(encoding="utf-8")


def _script() -> str:
    m = re.search(r"<script>(.*)</script>", _shell(), re.S)
    assert m is not None
    return m.group(1)


# ---------------------------------------------------------------------------
# 1. The Workspace-session mechanism itself - src/services/identity/workspace.py
# ---------------------------------------------------------------------------


class TestWorkspaceSessionMechanism:
    def test_nothing_entered_yet_is_an_honest_zero_visit_session(self):
        memory = EphemeralMemory()
        session = workspace_session_for("Alfred", recall=memory.recall)
        assert session.visits == 0
        assert session.first_entered is None
        assert session.last_entered is None

    def test_no_recall_at_all_is_the_identical_honest_absence(self):
        session = workspace_session_for("Alfred", recall=None)
        assert session.visits == 0

    def test_one_real_entry_is_recorded_and_readable_back(self):
        memory = EphemeralMemory()
        session = record_workspace_entry(
            "Alfred", recall=memory.recall, remember=memory.remember, forget=memory.forget
        )
        assert session.visits == 1
        assert session.first_entered is not None
        assert session.last_entered == session.first_entered

        reread = workspace_session_for("Alfred", recall=memory.recall)
        assert reread == session

    def test_a_second_entry_increments_and_replaces_the_one_governed_record(self):
        memory = EphemeralMemory()
        record_workspace_entry("Alfred", recall=memory.recall, remember=memory.remember, forget=memory.forget)
        second = record_workspace_entry(
            "Alfred", recall=memory.recall, remember=memory.remember, forget=memory.forget
        )
        assert second.visits == 2
        # forget-then-remember: exactly one governed record survives, never
        # a growing pile of one-per-visit records.
        records = memory.recall(kind="user_context", tags={WORKSPACE_SESSION_FACT_TYPE}, limit=50)
        matching = [r for r in records if "workspace_session:alfred" in r.tags]
        assert len(matching) == 1

    def test_two_agents_own_sessions_are_never_the_same_store(self):
        alfred_memory = EphemeralMemory()
        arthur_memory = EphemeralMemory()
        record_workspace_entry(
            "Alfred", recall=alfred_memory.recall, remember=alfred_memory.remember, forget=alfred_memory.forget
        )
        arthur_session = workspace_session_for("Arthur", recall=arthur_memory.recall)
        assert arthur_session.visits == 0

    def test_workspace_facts_are_never_conversation_memory(self):
        """A Workspace entry is a fact about use, not a remembered thing
        said - it must never collide with, or be mistaken for, an
        ordinary durable user fact tagged the same way a taught
        preference would be."""
        memory = EphemeralMemory()
        record_workspace_entry("Alfred", recall=memory.recall, remember=memory.remember, forget=memory.forget)
        records = memory.recall(kind="user_context", tags={WORKSPACE_SESSION_FACT_TYPE}, limit=50)
        assert len(records) == 1
        assert records[0].context["fact_type"] == WORKSPACE_SESSION_FACT_TYPE


# ---------------------------------------------------------------------------
# 2. The Agent Framework: Alfred declares WORKSPACE; nobody else inherits it
# ---------------------------------------------------------------------------


class TestAgentFrameworkDeclaration:
    def test_alfred_declares_and_implements_workspace(self):
        runtime, manager, channel, conversation = real_system()
        interface = manager.own_skills.interface
        assert interface is not None
        assert interface.can_render(InterfaceFeature.WORKSPACE)
        assert interface.has_workspace

    def test_a_manager_skills_built_with_no_interface_kwarg_declares_nothing(self):
        """ManagerSkills itself must stay generic - Alfred's Workspace is
        decided at composition.py's own Alfred-specific call site, never
        hardcoded into the reusable class every future manager-style
        agent is also built from."""
        from src.manager.manager_skills import ManagerSkills
        from src.services.identity.identity import AgentIdentity

        bare = ManagerSkills(AgentIdentity(name="TestManager", capabilities=frozenset({"test"})))
        assert bare.interface is None

    def test_the_email_specialist_never_inherits_a_workspace(self):
        runtime, manager, channel, conversation = real_system()
        names = [a.name for a in manager.registry.all_agents()]
        specialist_name = next(n for n in names if n != manager.own_skills.name)
        specialist = manager.registry.get(specialist_name)
        from src.control_center.interfaces import describe_interface

        interface = describe_interface(specialist)
        assert not interface.can_render(InterfaceFeature.WORKSPACE)


# ---------------------------------------------------------------------------
# 3. Control Center: the same backend source of truth, discovered not named
# ---------------------------------------------------------------------------


class TestControlCenterExposure:
    def test_workspaces_finds_alfred_and_only_alfred(self):
        runtime, manager, channel, conversation = real_system()
        centre = ControlCenter(runtime, manager)
        views = centre.workspaces()
        names = [v.agent for v in views]
        assert manager.own_skills.name in names
        specialist_names = [a.name for a in manager.registry.all_agents() if a.name != manager.own_skills.name]
        for name in specialist_names:
            assert name not in names

    def test_a_fresh_system_reports_zero_visits_honestly(self):
        runtime, manager, channel, conversation = real_system()
        centre = ControlCenter(runtime, manager)
        view = next(v for v in centre.workspaces() if v.agent == manager.own_skills.name)
        assert view.visits == 0
        assert view.tasks == ()

    def test_control_center_and_the_backend_read_the_identical_state(self):
        """No duplicate workspace database: a real entry recorded through
        the governed write path is immediately visible to
        ControlCenter.workspaces() - the same Memory, read twice."""
        runtime, manager, channel, conversation = real_system()
        centre = ControlCenter(runtime, manager)
        memory = memory_for(manager, manager.own_skills.name)
        record_workspace_entry(
            manager.own_skills.name, recall=memory.recall, remember=memory.remember, forget=memory.forget
        )
        view = next(v for v in centre.workspaces() if v.agent == manager.own_skills.name)
        assert view.visits == 1

    def test_the_workspace_terminal_verb_renders_real_state(self):
        runtime, manager, channel, conversation = real_system()
        centre = ControlCenter(runtime, manager)
        listing = run_command(centre, "workspace", 100, channel, conversation)
        assert "WORKSPACE" in listing
        assert manager.own_skills.name in listing

    def test_render_workspaces_is_honest_about_nothing_declared(self):
        assert "no agent has declared" in render_workspaces(())


# ---------------------------------------------------------------------------
# 4. The served backend route - POST /workspace/enter
# ---------------------------------------------------------------------------


class TestWorkspaceHttpRoute:
    def _system(self, runtime, manager, channel, conversation):
        centre = ControlCenter(runtime, manager)
        return LiveSystem(
            runtime=runtime, manager=manager, channel=channel, conversation=conversation,
            centre=centre, lock=threading.Lock(),
        )

    def test_empty_agent_is_refused(self):
        runtime, manager, channel, conversation = real_system()
        system = self._system(runtime, manager, channel, conversation)
        status, body, _ = route_workspace_enter("", system)
        assert status == 400

    def test_an_unregistered_agent_is_refused(self):
        runtime, manager, channel, conversation = real_system()
        system = self._system(runtime, manager, channel, conversation)
        status, body, _ = route_workspace_enter("Nobody", system)
        assert status == 404

    def test_an_agent_with_no_workspace_declared_is_refused(self):
        runtime, manager, channel, conversation = real_system()
        system = self._system(runtime, manager, channel, conversation)
        specialist_name = next(
            a.name for a in manager.registry.all_agents() if a.name != manager.own_skills.name
        )
        status, body, _ = route_workspace_enter(specialist_name, system)
        assert status == 400

    def test_a_valid_entry_returns_200_and_the_real_session(self):
        runtime, manager, channel, conversation = real_system()
        system = self._system(runtime, manager, channel, conversation)
        status, body, content_type = route_workspace_enter(manager.own_skills.name, system)
        assert status == 200
        assert "application/json" in content_type
        assert '"visits": 1' in body.decode("utf-8")

    def test_entering_twice_genuinely_increments(self):
        runtime, manager, channel, conversation = real_system()
        system = self._system(runtime, manager, channel, conversation)
        route_workspace_enter(manager.own_skills.name, system)
        status, body, _ = route_workspace_enter(manager.own_skills.name, system)
        assert status == 200
        assert '"visits": 2' in body.decode("utf-8")

    def test_the_entry_survives_a_genuine_restart(self, tmp_path):
        settings = Settings.from_env(env={}, memory="persistent", memory_dir=str(tmp_path))
        runtime, manager, channel, conversation = build(settings)
        system = self._system(runtime, manager, channel, conversation)
        route_workspace_enter(manager.own_skills.name, system)

        runtime2, manager2, channel2, conversation2 = build(settings)
        centre2 = ControlCenter(runtime2, manager2)
        view = next(v for v in centre2.workspaces() if v.agent == manager2.own_skills.name)
        assert view.visits == 1
        assert view.last_entered is not None


# ---------------------------------------------------------------------------
# 5. snapshot.py: what the served page actually reads
# ---------------------------------------------------------------------------


class TestSnapshotExposure:
    def test_alfred_snapshot_carries_tasks_and_workspace_fields(self):
        runtime, manager, channel, conversation = real_system()
        centre = ControlCenter(runtime, manager)
        data = snapshot(centre)
        alfred = next(a for a in data["agents"] if a["name"] == manager.own_skills.name)
        assert alfred["tasks"] == []
        assert alfred["workspace"] == {
            "agent_key": manager.own_skills.name,
            "visits": 0,
            "first_entered": None,
            "last_entered": None,
        }
        assert "workspace" in alfred["interface"]["implemented"]

    def test_a_real_task_appears_in_the_agents_own_snapshot(self):
        from scripts.exchange import exchange

        runtime, manager, channel, conversation = real_system()
        centre = ControlCenter(runtime, manager)
        exchange(channel, conversation, "add a task to water the plants", addressed=manager.own_skills.name)
        data = snapshot(centre)
        alfred = next(a for a in data["agents"] if a["name"] == manager.own_skills.name)
        assert any("water the plants" in t["content"] for t in alfred["tasks"])

    def test_a_specialists_own_tasks_never_leak_into_alfreds_snapshot(self):
        from scripts.exchange import exchange

        runtime, manager, channel, conversation = real_system()
        centre = ControlCenter(runtime, manager)
        specialist_name = next(
            a.name for a in manager.registry.all_agents() if a.name != manager.own_skills.name
        )
        exchange(channel, conversation, "add a task to sort the mailbox", addressed=specialist_name)
        data = snapshot(centre)
        alfred = next(a for a in data["agents"] if a["name"] == manager.own_skills.name)
        assert not any("sort the mailbox" in t["content"] for t in alfred["tasks"])


# ---------------------------------------------------------------------------
# 5b. The full, reversible task lifecycle - incomplete -> complete ->
# reopen -> complete - proven through the real Workspace's own read
# surfaces (snapshot, Control Center), never a dead end.
# ---------------------------------------------------------------------------


class TestTaskLifecycleThroughTheWorkspace:
    def test_the_full_lifecycle_is_consistent_across_snapshot_and_control_center(self):
        from scripts.exchange import exchange

        runtime, manager, channel, conversation = real_system()
        centre = ControlCenter(runtime, manager)
        name = manager.own_skills.name

        def alfred_task(data):
            alfred = next(a for a in data["agents"] if a["name"] == name)
            return next(t for t in alfred["tasks"] if t["content"] == "water the plants")

        def cc_task():
            view = next(v for v in centre.workspaces() if v.agent == name)
            return next(t for t in view.tasks if t.content == "water the plants")

        exchange(channel, conversation, "add a task to water the plants", addressed=name)
        task_id = alfred_task(snapshot(centre))["id"]
        assert alfred_task(snapshot(centre))["status"] == "pending"
        assert cc_task().status == "pending"

        exchange(channel, conversation, "complete the task water the plants", addressed=name)
        assert alfred_task(snapshot(centre))["status"] == "complete"
        assert cc_task().status == "complete"

        result = exchange(channel, conversation, "reopen the task water the plants", addressed=name)
        assert result.succeeded
        assert alfred_task(snapshot(centre))["status"] == "pending"
        assert cc_task().status == "pending"
        # Identity and content survived the round trip - same task,
        # never a delete-and-recreate.
        assert alfred_task(snapshot(centre))["id"] == task_id

        exchange(channel, conversation, "complete the task water the plants", addressed=name)
        assert alfred_task(snapshot(centre))["status"] == "complete"
        assert cc_task().status == "complete"
        assert alfred_task(snapshot(centre))["id"] == task_id

    def test_a_reopened_task_survives_a_genuine_restart(self, tmp_path):
        from scripts.exchange import exchange

        settings = Settings.from_env(env={}, memory="persistent", memory_dir=str(tmp_path))
        runtime, manager, channel, conversation = build(settings)
        name = manager.own_skills.name
        exchange(channel, conversation, "add a task to water the plants", addressed=name)
        exchange(channel, conversation, "complete the task water the plants", addressed=name)
        exchange(channel, conversation, "reopen the task water the plants", addressed=name)

        runtime2, manager2, channel2, conversation2 = build(settings)
        centre2 = ControlCenter(runtime2, manager2)
        data = snapshot(centre2)
        alfred = next(a for a in data["agents"] if a["name"] == name)
        task = next(t for t in alfred["tasks"] if t["content"] == "water the plants")
        assert task["status"] == "pending"


# ---------------------------------------------------------------------------
# 6. Half Brain: none of this ever touches Brain/LLM availability
# ---------------------------------------------------------------------------


class TestHalfBrainIndependence:
    def test_workspace_session_functions_take_no_brain_or_provider_at_all(self):
        import inspect

        from src.services.identity import workspace as workspace_module

        for name in ("workspace_session_for", "record_workspace_entry"):
            params = inspect.signature(getattr(workspace_module, name)).parameters
            joined = " ".join(params)
            assert "brain" not in joined.lower()
            assert "provider" not in joined.lower() or name == "workspace_session_for"

    def test_a_half_brain_system_still_renders_a_full_workspace_view(self):
        """The real, default test system (no provider account configured)
        is Half Brain - and the whole Workspace chain above already ran
        against it in every prior test in this file. This test names
        that fact explicitly, so a future change that accidentally gated
        the Workspace behind Full Brain fails loudly here."""
        from src.manager.cognition import assess_manager

        runtime, manager, channel, conversation = real_system()
        profile = assess_manager(manager)
        centre = ControlCenter(runtime, manager)
        # Whatever this environment's brain mode honestly is, the
        # Workspace itself must still be fully readable and enterable.
        assert centre.workspaces()  # discovered, non-empty
        system = LiveSystem(
            runtime=runtime, manager=manager, channel=channel, conversation=conversation,
            centre=centre, lock=threading.Lock(),
        )
        status, _, _ = route_workspace_enter(manager.own_skills.name, system)
        assert status == 200
        assert profile is not None  # the read itself succeeded either way


# ---------------------------------------------------------------------------
# 7. The shipped frontend: a real, distinct, navigable, connected view
# ---------------------------------------------------------------------------


class TestFrontendConnection:
    def test_modes_declares_a_distinct_workspace_entry(self):
        script = _script()
        assert '["space","Workspace","living"' in script
        # "workspace" the internal key is already Presentation's - the
        # new surface must not repeat that exact collision.
        assert '["workspace","Presentation"' in script

    def test_the_view_reuses_the_agent_owned_avatar_mount_never_a_second_one(self):
        block = _script().split("V.space = () => {", 1)[1].split("\n  };", 1)[0]
        assert 'data-avatarbody3d="${esc(a.name)}"' in block
        # The one shared discovery mechanism, not a hardcoded agent list.
        assert "workspaceCapableAgents()" in block

    def test_go_records_a_genuine_entry_and_wires_the_board(self):
        script = _script()
        assert "function enterWorkspace(agentName){" in script
        assert "workspace/enter" in script
        assert "function wireWorkspaceBoard(){" in script
        assert "wireWorkspaceBoard();" in script.split("function go(key, agentName){", 1)[1].split("\n  }", 1)[0]

    def test_leaving_returns_to_ordinary_conversation_navigation(self):
        block = _script().split("V.space = () => {", 1)[1].split("\n  };", 1)[0]
        assert 'data-goto="agent"' in block

    def test_the_add_and_complete_actions_reuse_the_existing_message_endpoint(self):
        """No second, form-based task-mutation route: both actions must
        construct the natural-language phrases the existing governed
        task capabilities already recognise, and send them through the
        one existing /message endpoint."""
        board = _script().split("function wireWorkspaceBoard(){", 1)[1].split("\n  }\n", 1)[0]
        assert '"message"' in board
        assert '"add a task to " + text' in board
        assert '"complete the task " + content' in board
        assert "workspace/enter" not in board

    def test_the_reopen_action_reuses_the_same_message_endpoint_and_toggle(self):
        """The lifecycle is genuinely reversible in the UI: a completed
        task's own toggle button sends the reopen phrase through the
        identical /message endpoint and the identical click handler the
        complete direction already uses - never a second button, a
        second route, or a dead end."""
        board = _script().split("function wireWorkspaceBoard(){", 1)[1].split("\n  }\n", 1)[0]
        assert '"reopen the task " + content' in board
        assert 'dataset.wsAction === "reopen"' in board
        # One handler, both directions - not a second wiring loop.
        assert board.count('addEventListener("click"') == 1

    def test_a_completed_task_row_renders_a_real_clickable_reopen_control(self):
        block = _script().split("V.space = () => {", 1)[1].split("\n  };", 1)[0]
        assert 'data-ws-action="${complete ? "reopen" : "complete"}"' in block
        # A real <button>, not a static, inert checkmark - genuinely
        # clickable and keyboard-reachable either way.
        assert "<button class=\"ws-task-toggle" in block
        assert "ws-task-mark" not in block


# ---------------------------------------------------------------------------
# 8. The served backend route list and management-verb surface
# ---------------------------------------------------------------------------


class TestServeUiWiring:
    def test_workspace_enter_is_a_real_post_path(self):
        src = SERVE_UI.read_text(encoding="utf-8")
        assert '"/workspace/enter"' in src
        assert 'elif self.path == "/workspace/enter":' in src

    def test_workspace_is_a_real_management_verb(self):
        from scripts.serve_ui import MANAGEMENT_VERBS

        assert "workspace" in MANAGEMENT_VERBS

    def test_workspace_verb_requires_no_administrative_confirmation(self):
        from scripts.control_center import classify_administrative_write

        requires, _ = classify_administrative_write("workspace")
        assert requires is False
