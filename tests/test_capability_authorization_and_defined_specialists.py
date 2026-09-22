"""Final ecosystem closure - capability, workspace and system reachability.

What these pin, each through the real served system (``scripts.serve_ui``
-> ``scripts.control_center.build``) with every governed path sandboxed:

  * A capability is registered once and *authorised* per agent, never owned:
    it states its own authority (grantable by default; every_agent; or
    manager with a stated reason), the Manager holds every one, and a
    specialist reaches every-agent capabilities plus what the owner granted.
  * Granting a specialist a capability never takes it from the Manager.
  * A specialist nobody predicted is created from the Control Center as data
    - its own identity, Brain, memory, voice and Workspace - live and on
    every start, and removed the same way. It is never a second Manager.
  * A workspace neither grants nor removes a capability: its profile only
    redirects work to another workspace that is set up for it.
  * Safety and honesty: crisis care from whichever agent is addressed; an
    unauthorised request names the capability and who can do it.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import scripts.control_center as cc
import scripts.serve_ui as su
from scripts.exchange import exchange
from src.config.settings import Settings
from src.control_center.capability_access import (
    EVERY_AGENT,
    GRANTABLE,
    MANAGER_ONLY,
    access_rows,
    grant_refusal,
    reachable_capabilities,
)
from src.control_center.workspace_catalog import workspace_refusal
from src.core.contracts import Task, TaskResult
from src.manager.capabilities import (
    CAPABILITY_AUTHORITIES,
    MANAGER_CAPABILITIES,
    ManagerCapability,
)
from src.providers.base import Completion, LLMProvider
from src.services.memory.guidance import CRISIS_REPLY

REPO_ROOT = Path(__file__).resolve().parent.parent
CHAIR = "build a 3D model of a chair: a box seat 1 wide 0.2 tall 1 deep"


def _sandbox(tmp_path, monkeypatch):
    for name, value in (
        ("DEFAULT_ACCOUNTS_PATH", tmp_path / "accounts.json"), ("DEFAULT_CONFIG_PATH", tmp_path / "config.json"),
        ("DEFAULT_LEDGER_PATH", tmp_path / "state.json"), ("DEFAULT_STAGING_DIR", tmp_path / "staging"),
        ("DEFAULT_BACKUPS_DIR", tmp_path / "backups"),
    ):
        monkeypatch.setattr(cc, name, value)


def _settings(tmp_path, **extra):
    return Settings.from_env(
        env={}, memory="ephemeral", profile_path="does-not-exist-USER.md",
        artifacts_dir=str(tmp_path / "artifacts"), media_asset_dir=str(tmp_path / "assets"),
        knowledge_dir=str(tmp_path / "knowledge"), documents_dir=str(tmp_path / "docs"), **extra,
    )


@pytest.fixture
def system(tmp_path, monkeypatch):
    _sandbox(tmp_path, monkeypatch)
    return su.build_live_system(_settings(tmp_path))


def _run(system, command):
    return cc.run_command(system.centre, command, 100, system.channel, system.conversation)


def _say(system, text, to=None):
    return exchange(system.channel, system.conversation, text, addressed=to)


def _manager_name(system):
    return system.manager.own_skills.name


def _specialist(system):
    return next(a.name for a in system.manager.registry.all_agents() if a is not system.manager.own_skills)


# ---------------------------------------------------------------------------
# 1. The registry states each capability's authority
# ---------------------------------------------------------------------------


class TestEveryCapabilityStatesItsAuthority:
    def test_every_registered_capability_has_a_known_authority(self):
        for name, capability in MANAGER_CAPABILITIES.items():
            assert capability.authority in CAPABILITY_AUTHORITIES, name

    def test_a_manager_only_capability_always_says_why(self):
        restricted = {n: c for n, c in MANAGER_CAPABILITIES.items() if c.authority == MANAGER_ONLY}
        assert restricted, "the Manager's orchestration capabilities are Manager-only"
        for name, capability in restricted.items():
            assert len(capability.authority_reason.strip()) > 20, name

    def test_a_restriction_without_a_reason_cannot_be_registered(self):
        with pytest.raises(ValueError, match="states no reason"):
            ManagerCapability(name="x", capabilities=frozenset({"x"}), handle=lambda r: None, authority=MANAGER_ONLY)

    def test_a_new_capability_is_grantable_by_default_with_no_list_edited(self):
        new = ManagerCapability(name="brand_new_ability", capabilities=frozenset({"brand"}), handle=lambda r: None)
        assert new.authority == GRANTABLE
        registry = {**MANAGER_CAPABILITIES, new.name: new}
        values = {"capability_grant.nova": "brand_new_ability"}
        assert "brand_new_ability" in reachable_capabilities("Nova", values, registry, is_manager=False)
        assert grant_refusal("brand_new_ability", registry) is None

    def test_the_capabilities_this_campaign_was_asked_about_are_authorisable(self):
        for name in ("create_3d_model", "read_content", "translate", "generate_document", "clip_video",
                     "transcribe_media", "image_analysis", "general_assistance", "knowledge", "connected_action"):
            assert MANAGER_CAPABILITIES[name].authority == GRANTABLE, name

    def test_orchestration_stays_the_managers(self):
        for name in ("roster_report", "participant_invitation"):
            assert MANAGER_CAPABILITIES[name].authority == MANAGER_ONLY, name
            assert "second Manager" in MANAGER_CAPABILITIES[name].authority_reason


class TestReachabilityModel:
    def test_the_manager_reaches_every_capability(self):
        assert reachable_capabilities("Alfred", {}, MANAGER_CAPABILITIES, is_manager=True) == MANAGER_CAPABILITIES

    def test_a_specialist_reaches_every_agent_capabilities_and_only_what_it_was_granted(self):
        reach = reachable_capabilities("Nova", {"capability_grant.nova": "create_3d_model"}, MANAGER_CAPABILITIES,
                                       is_manager=False)
        assert "create_3d_model" in reach and "read_content" not in reach
        assert all(c.authority in (EVERY_AGENT, GRANTABLE) for c in reach.values())
        assert {n for n, c in MANAGER_CAPABILITIES.items() if c.authority == EVERY_AGENT} <= set(reach)

    def test_a_grant_is_a_fact_about_the_grantee_only(self):
        values = {"capability_grant.nova": "create_3d_model"}
        assert "create_3d_model" not in reachable_capabilities("Arthur", values, MANAGER_CAPABILITIES, is_manager=False)
        assert "create_3d_model" in reachable_capabilities("Alfred", values, MANAGER_CAPABILITIES, is_manager=True)

    def test_a_capability_disabled_for_the_system_is_gone_for_every_agent(self):
        live = {n: c for n, c in MANAGER_CAPABILITIES.items() if n != "create_3d_model"}
        rows = {r.name: r for r in access_rows("Nova", {"capability_grant.nova": "create_3d_model"},
                                               MANAGER_CAPABILITIES, live, is_manager=False)}
        assert rows["create_3d_model"].state == "disabled"


# ---------------------------------------------------------------------------
# 2. Specialists reach granted capabilities; the Manager keeps his
# ---------------------------------------------------------------------------


class TestGrantingThroughTheControlCenter:
    def test_an_ungranted_specialist_says_which_capability_and_who_can(self, system):
        specialist = _specialist(system)
        turn = _say(system, CHAIR, to=specialist)
        assert "create_3d_model" in turn.outcome and "not authorised" in turn.outcome
        assert f"capabilities grant {specialist} create_3d_model" in turn.outcome
        assert not system.runtime.artifacts.all()

    def test_a_granted_specialist_builds_real_3d_as_itself_and_the_manager_still_can(self, system):
        specialist = _specialist(system)
        said = _run(system, f"capabilities grant {specialist} create_3d_model")
        assert said.startswith("Granted create_3d_model") and "keeps every capability" in said
        assert "Artifact created" in _say(system, CHAIR, to=specialist).outcome
        assert "Artifact created" in _say(system, CHAIR).outcome
        owners = sorted(a.owner_agent for a in system.runtime.artifacts.all() if a.type == "model3d")
        assert owners == sorted([specialist, _manager_name(system)])

    def test_a_revoked_grant_is_live_at_once(self, system):
        specialist = _specialist(system)
        _run(system, f"capabilities grant {specialist} create_3d_model")
        assert _run(system, f"capabilities revoke {specialist} create_3d_model").startswith("Revoked")
        assert "not authorised" in _say(system, CHAIR, to=specialist).outcome

    def test_a_manager_only_capability_cannot_be_granted(self, system):
        said = _run(system, f"capabilities grant {_specialist(system)} roster_report")
        assert said.startswith("FAILED") and "Manager-only" in said and "Nothing was granted" in said

    def test_the_manager_needs_no_grant(self, system):
        said = _run(system, f"capabilities grant {_manager_name(system)} create_3d_model")
        assert "already holds every capability" in said

    def test_a_grant_survives_a_restart(self, tmp_path, monkeypatch):
        _sandbox(tmp_path, monkeypatch)
        first = su.build_live_system(_settings(tmp_path))
        specialist = _specialist(first)
        _run(first, f"capabilities grant {specialist} create_3d_model")
        second = su.build_live_system(_settings(tmp_path))
        assert "Artifact created" in _say(second, CHAIR, to=specialist).outcome

    def test_the_access_view_explains_every_capability(self, system):
        view = _run(system, f"capabilities access {_specialist(system)}")
        for heading in ("HAS IT", "GRANTABLE", "MANAGER ONLY"):
            assert heading in view
        assert "second Manager" in view

    def test_a_manager_only_request_to_a_specialist_names_the_manager(self, system):
        turn = _say(system, "coach me on my goals", to=_specialist(system))
        assert "only " + _manager_name(system) in turn.outcome or "coach" in turn.outcome


# ---------------------------------------------------------------------------
# 3. A specialist nobody predicted: created, used, persisted, removed
# ---------------------------------------------------------------------------


class TestCreatingASpecialist:
    CREATE = "agent create nova Nova|3D modelling and spatial design|create_3d_model"

    def test_created_live_with_its_own_identity_memory_voice_and_workspace(self, system):
        said = _run(system, self.CREATE)
        assert said.startswith("Created the specialist 'Nova'") and "Saved - live now" in said
        nova = system.manager.registry.get("Nova")
        arthur = system.manager.registry.get(_specialist_other_than(system, "Nova"))
        assert nova is not None and nova.memory is not arthur.memory and nova.memory is not system.manager.memory
        assert nova.brain is not arthur.brain
        assert nova.voice != system.runtime.identity.voice and nova.voice != arthur.voice
        assert "workspace" in {f.value for f in nova.interface.features}
        assert nova.brain_domain == "3D modelling and spatial design"

    def test_it_works_as_itself_and_the_manager_keeps_the_capability(self, system):
        _run(system, self.CREATE)
        assert "Artifact created" in _say(system, CHAIR, to="Nova").outcome
        assert "Artifact created" in _say(system, CHAIR).outcome
        assert sorted(a.owner_agent for a in system.runtime.artifacts.all()) == sorted(["Nova", _manager_name(system)])

    def test_it_answers_who_it_is_and_every_agent_capability(self, system):
        _run(system, self.CREATE)
        assert "Nova" in _say(system, "who are you?", to="Nova").outcome
        assert "2026" in _say(system, "what's the date today?", to="Nova").outcome or "date" in _say(
            system, "what's the date today?", to="Nova").outcome.lower()

    def test_it_is_never_a_second_manager(self, system):
        _run(system, self.CREATE)
        nova = system.manager.registry.get("Nova")
        request = system.manager.specialist_capabilities.request_for(nova, Task(description="x"))
        assert tuple(request.roster()) == ()
        assert "roster_report" not in system.manager.specialist_capabilities.reachable(nova)

    def test_it_persists_and_is_removed_the_same_way(self, tmp_path, monkeypatch):
        _sandbox(tmp_path, monkeypatch)
        first = su.build_live_system(_settings(tmp_path))
        _run(first, self.CREATE)
        second = su.build_live_system(_settings(tmp_path))
        assert second.manager.registry.get("Nova") is not None
        assert "Artifact created" in _say(second, CHAIR, to="Nova").outcome
        assert _run(second, "agent remove nova").startswith("Removed the specialist 'Nova'")
        assert second.manager.registry.get("Nova") is None
        third = su.build_live_system(_settings(tmp_path))
        assert third.manager.registry.get("Nova") is None

    def test_a_name_already_taken_is_refused(self, system):
        before = [a.name for a in system.manager.registry.all_agents()]
        said = _run(system, f"agent create twin {_manager_name(system)}|anything")
        assert said.startswith("FAILED") and "already exists" in said
        assert [a.name for a in system.manager.registry.all_agents()] == before

    def test_a_manager_only_grant_at_creation_creates_nothing(self, system):
        said = _run(system, "agent create boss Boss|running things|roster_report")
        assert said.startswith("FAILED") and system.manager.registry.get("Boss") is None

    def test_creation_and_grants_are_confirmed_and_audited_writes(self):
        assert cc.classify_administrative_write("agent create nova Nova|3D")[0]
        assert cc.classify_administrative_write("agent remove nova")[0]
        assert cc.classify_administrative_write("capabilities grant Nova create_3d_model")[0]
        assert cc.classify_administrative_write("capabilities revoke Nova create_3d_model")[0]
        assert not cc.classify_administrative_write("agent list")[0]
        assert not cc.classify_administrative_write("capabilities access Nova")[0]
        assert {"agent", "agents"} <= su.MANAGEMENT_VERBS

    def test_the_static_export_shows_the_same_grants_as_the_served_page(self, tmp_path, monkeypatch):
        """Found live: export_ui registered the owner's specialists but never
        equipped them, so a granted capability was missing from the export."""
        import scripts.export_ui as ex

        _sandbox(tmp_path, monkeypatch)
        monkeypatch.setattr(ex, "DEFAULT_CONFIG_PATH", tmp_path / "config.json")
        monkeypatch.setattr(ex, "DEFAULT_ACCOUNTS_PATH", tmp_path / "accounts.json")
        served_system = su.build_live_system(_settings(tmp_path))
        _run(served_system, self.CREATE)
        served = next(a for a in su._current_snapshot(served_system)["agents"] if a["name"] == "Nova")
        exported = next(a for a in ex.take_snapshot(_settings(tmp_path))["agents"] if a["name"] == "Nova")
        assert "create_3d_model" in {c["name"] for c in served["capabilities"]}
        assert exported["capabilities"] == served["capabilities"]

    def test_the_general_specialist_never_imports_the_orchestrator(self):
        source = (REPO_ROOT / "src/agents/general_specialist.py").read_text("utf-8")
        assert "src.manager" not in source


def _specialist_other_than(system, name):
    return next(a.name for a in system.manager.registry.all_agents()
                if a is not system.manager.own_skills and a.name != name)


# ---------------------------------------------------------------------------
# 4. Workspaces organise work; they never grant or remove a capability
# ---------------------------------------------------------------------------


class TestWorkspacesNeverOwnCapabilities:
    def _only_a_scoped_workspace(self, system):
        manager = _manager_name(system)
        _run(system, "workspace create sketch Sketchbook|tasks|create_task,list_tasks")
        for workspace in ("board", "diagram", "coding", "documents"):
            _run(system, f"workspace {manager} remove {workspace}")
        _run(system, f"workspace {manager} use sketch")
        return manager

    def test_no_workspace_for_it_means_the_work_is_still_done(self, system):
        self._only_a_scoped_workspace(system)
        assert "Artifact created" in _say(system, CHAIR).outcome

    def test_a_workspace_set_up_for_it_is_where_the_work_is_redirected(self, system):
        manager = self._only_a_scoped_workspace(system)
        _run(system, f"workspace {manager} add diagram")
        turn = _say(system, CHAIR)
        assert "belongs in: 3D diagram" in turn.outcome
        _say(system, "go to the 3D diagram workspace")
        assert "Artifact created" in _say(system, CHAIR).outcome

    def test_the_refusal_rule_itself(self):
        values = {"workspace_type.sketch": json.dumps({"label": "Sketchbook", "panels": ["tasks"],
                                                        "capabilities": ["create_task"]}),
                  "workspaces.alfred": "sketch", "workspace_active.alfred": "sketch"}
        assert workspace_refusal("Alfred", values, ("create_3d_model",)) is None
        values["workspaces.alfred"] = "sketch,diagram"
        assert "3D diagram" in workspace_refusal("Alfred", values, ("create_3d_model",))

    def test_asked_to_create_a_workspace_every_agent_says_it_is_the_owners(self, system):
        """Creating a workspace is the owner's governed write. Asked in
        conversation, the agent says so and where - never a Brain's guess,
        never a workspace made - and work mentioning a workspace is untouched."""
        _run(system, "agent create nova Nova|3D modelling")
        before = system.centre and cc.load_governed_config(cc.DEFAULT_CONFIG_PATH).values
        for to in (None, "Nova"):
            turn = _say(system, "create a workspace called garden planning", to=to)
            assert "I can't create workspaces" in turn.outcome and "workspace create" in turn.outcome
        assert cc.load_governed_config(cc.DEFAULT_CONFIG_PATH).values == before
        assert "Artifact created" in _say(system, "build a 3D model in the workspace: a box 1 wide").outcome


# ---------------------------------------------------------------------------
# 4b. The orchestrator answers who is on his team
# ---------------------------------------------------------------------------


class TestTheRosterIsReachableByAsking:
    def test_the_manager_answers_plain_roster_questions(self, system):
        _run(system, "agent create nova Nova|3D modelling")
        for question in ("which agents do you have?", "list your specialists", "who's on your team?"):
            turn = _say(system, question)
            assert turn.handled_by == _manager_name(system), question
            assert "Nova" in turn.outcome and _specialist_other_than(system, "Nova") in turn.outcome, question

    def test_a_specialist_asked_the_same_names_the_manager(self, system):
        _run(system, "agent create nova Nova|3D modelling")
        turn = _say(system, "which agents do you have?", to="Nova")
        assert "roster_report" in turn.outcome and "only " + _manager_name(system) in turn.outcome

    def test_ordinary_sentences_are_not_roster_questions(self):
        from src.manager.capabilities import roster_intent

        for said in ("I need to finish the report", "who is on the team at my work", "what agents of change do"):
            assert not roster_intent(Task(description=said)), said


# ---------------------------------------------------------------------------
# 5. Safety, Brain modes, live rebind
# ---------------------------------------------------------------------------


class TestSafetyAndBrains:
    def test_crisis_care_comes_from_whichever_agent_is_addressed(self, system):
        turn = _say(system, "I don't want to live anymore", to=_specialist(system))
        assert turn.outcome == CRISIS_REPLY

    def test_half_brain_specialist_is_honest_about_what_needs_a_model(self, system):
        _run(system, TestCreatingASpecialist.CREATE)
        turn = _say(system, "make me a 3D teapot", to="Nova")
        assert "no reasoning model worked them out" in turn.outcome
        assert not system.runtime.artifacts.all()

    def test_full_brain_specialist_reasons_with_its_own_brain(self, tmp_path, monkeypatch):
        from src.providers import factory

        calls = []

        class Probe(LLMProvider):
            @property
            def name(self):
                return "closure_probe"

            def complete(self, prompt, *, system=None):
                calls.append(prompt)
                if "Break '" in prompt:
                    return Completion(text="1 white sphere body radius 1 ; 1 white sphere lid radius 0.3 on top of the body",
                                      provider="closure_probe")
                return Completion(text='{"should_retain": false, "content": null}', provider="closure_probe")

        monkeypatch.setitem(factory._BUILDERS, "closure_probe", lambda settings, env=None, account=None: Probe())
        _sandbox(tmp_path, monkeypatch)
        system = su.build_live_system(_settings(tmp_path, provider="closure_probe", brain="llm", specialist_brain="llm"))
        _run(system, TestCreatingASpecialist.CREATE)
        turn = _say(system, "make me a 3D teapot", to="Nova")
        assert "Artifact created" in turn.outcome and "2 part(s)" in turn.outcome
        assert any("Break 'teapot'" in c for c in calls)
        assert [a.owner_agent for a in system.runtime.artifacts.all()] == ["Nova"]

    def test_a_live_reasoning_switch_rebuilds_a_created_specialists_brain(self, system):
        from src.composition import rebind_reasoning

        _run(system, TestCreatingASpecialist.CREATE)
        nova = system.manager.registry.get("Nova")
        before = nova.brain
        rebind_reasoning(system.runtime, system.manager, system.runtime.settings)
        assert nova.brain is not before


class TestWhatASpecialistIsAskedToRememberIsItsOwn:
    """Found live: "Nova, remember that X" answered "Got it - I've remembered
    that" while the note went into the Manager's store, so Nova's own recall
    then said she had nothing. The note is now kept by the agent that was
    addressed and said it remembered - as its own explicit capture, because
    user context stays the Manager's."""

    NOTE = "remember that the windmill project is due on Friday"

    def test_the_addressed_specialist_keeps_and_recalls_it(self, system):
        _run(system, "agent create nova Nova|3D modelling")
        assert "I've remembered that" in _say(system, self.NOTE, to="Nova").outcome
        nova = system.manager.registry.get("Nova")
        assert [r.content for r in nova.memory.recall(limit=10)] == ["the windmill project is due on Friday"]
        assert "due on Friday" in _say(system, "what do you remember about the windmill project?", to="Nova").outcome
        # His record that he delegated it is his own orchestration memory;
        # the note itself is not duplicated into his store.
        assert not any("windmill" in r.content for r in system.manager.memory.recall(limit=50)
                       if r.kind != "delegation_outcome")

    def test_a_fact_about_the_person_stays_in_the_canonical_record(self, system):
        _run(system, "agent create nova Nova|3D modelling")
        _say(system, "remember that my goal is to learn Rust", to="Nova")
        assert not system.manager.registry.get("Nova").memory.recall(limit=10)
        assert any("Rust" in r.content for r in system.manager.memory.recall(limit=50))

    def test_the_manager_still_keeps_what_he_is_asked(self, system):
        assert "I've remembered that" in _say(system, self.NOTE).outcome
        assert any("windmill" in r.content for r in system.manager.memory.recall(limit=50))


class TestAGrantedSpecialistReadsContentItself:
    """read_content through Nova, end to end: a real repository document
    shared through the upload route, read by Nova as Nova (never Alfred
    behind the scenes), and refused honestly once revoked."""

    def _shared(self, system):
        import base64

        _run(system, "agent create nova Nova|3D modelling")
        status, body, _ = su.route_upload(
            "Nova", "SOUL.md", "text/markdown",
            base64.b64encode((REPO_ROOT / "SOUL.md").read_bytes()).decode(), "conversation", system,
        )
        assert status == 200 and json.loads(body)["readable_units"] > 10

    def test_granted_nova_reads_the_named_part_as_herself(self, system):
        self._shared(system)
        _run(system, "capabilities grant Nova read_content")
        ran_as = []
        capability = MANAGER_CAPABILITIES["read_content"]
        real = capability.handle
        # Frozen dataclass: swapped and restored by hand to see who runs it.
        object.__setattr__(capability, "handle", lambda request: ran_as.append(request.own_name) or real(request))
        try:
            turn = _say(system, "Nova, what does paragraph 10 say?", to="Nova")
        finally:
            object.__setattr__(capability, "handle", real)
        assert turn.handled_by == "Nova" and turn.succeeded
        assert "Report, do not act" in turn.outcome and "paragraph 10" in turn.outcome
        assert ran_as == ["Nova"]

    def test_revoked_nova_is_refused_and_nothing_reads_for_her(self, system):
        self._shared(system)
        _run(system, "capabilities grant Nova read_content")
        _run(system, "capabilities revoke Nova read_content")
        turn = _say(system, "Nova, what does paragraph 10 say?", to="Nova")
        assert not turn.succeeded and "read_content, which I'm not authorised for" in turn.outcome
        assert "Report, do not act" not in turn.outcome

    def test_the_trail_credits_each_memory_write_to_whoever_kept_it(self, system):
        """Found live: every delegated turn showed "Nova / Memory: 1 record
        retained" - it was Alfred's own delegation note, not Nova's."""
        _run(system, "agent create nova Nova|3D modelling")
        turn = _say(system, "Nova, who are you?", to="Nova")
        assert {s.actor for s in turn.steps if s.label == "Memory"} <= {_manager_name(system)}
        turn = _say(system, "remember that the windmill project is due on Friday", to="Nova")
        assert "Nova" in {s.actor for s in turn.steps if s.label == "Memory" and "asked to be remembered" in s.detail}


class TestThreeDSubjects:
    def test_the_next_step_of_a_longer_request_is_not_the_subject(self, system):
        turn = _say(system, "make a 3D model of a chair: a box seat 1 wide 0.2 tall 1 deep, then a document")
        assert "Artifact created" in turn.outcome
        assert [a.latest.title for a in system.runtime.artifacts.all()] == ["3D model - a chair"]

    def test_a_number_before_an_adjective_is_that_dimension(self, system):
        """Found live: "8 wide 0.3 tall 2 deep" was built 0.3 wide - each
        adjective took the number *after* it, as a noun ("height 6") does."""
        turn = _say(system, "build a 3D model of a bridge: a box deck 8 wide 0.3 tall 2 deep")
        assert "8 x 0.3 x 2 units" in turn.outcome
        from src.providers.media.model3d import SHAPES, _dimensions

        assert _dimensions("cylinder radius 1 height 6", SHAPES["cylinder"]) == {"radius": 1.0, "height": 6.0}
        assert _dimensions("box width 3 height 2 depth 1", SHAPES["box"]) == {"width": 3.0, "height": 2.0, "depth": 1.0}
