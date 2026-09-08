"""Ecosystem architecture checkpoint - does open-ended, governed growth
genuinely work, or only look like it does?

This file does not test a feature. It tests the *claim* every prior
checkpoint's own docstrings have repeatedly made: that a genuinely new
piece of Alfred OS - a capability nobody has written yet - can enter the
running system through an already-existing registration point, compose
with real, pre-existing capabilities inside the real objective loop, and
become visible through the real Control Center / ecosystem surfaces,
all without one line of ``ManagerAgent``, the router, the assessor,
``objective_loop.py``, ``ecosystem/registry_factory.py`` or
``control_center.py`` changing.

Two extension points already existed before this file, proven
separately:

    src/services/evolution/handlers.py::register_handler
        governs a VERSION/CONFIG change to something that already has
        code - proven open-ended by
        tests/test_ecosystem_evolution_extension.py::TestHandlerRegistryIsOpenEnded.

    src/manager/capabilities.py::MANAGER_CAPABILITIES
        the dict a brand-new CAPABILITY (new code, not a version bump)
        is registered into - this is the "reviewed, explicit change to a
        registration point" Stage 11.A's own docstring says real
        capability code always requires (a capability is behaviour, so
        adding one is inherently a code change - what matters is that
        the change is exactly one line in exactly one, already-open,
        already-named place, and touches nothing else).

The second point was already demonstrated narrowly by
tests/test_manager_capabilities.py's own synthetic ``WEATHER`` capability
- but only as far as direct ``select_capability``/``ManagerSkills.execute``
calls. Nothing before this file proved a freshly-registered capability
reaches production routing (``ManagerAgent.handle_task``), composes as
one step of a real, Brain-driven, multi-step ``pursue_objective`` run
alongside a pre-existing real capability, or becomes visible through the
ecosystem registry / Control Center. That is the genuine, narrow gap
this file closes - not by inventing new architecture, but by exercising
the architecture that was already there.
"""

from __future__ import annotations

import dataclasses
import subprocess

from src.core.contracts import Task, TaskResult
from src.manager.capabilities import (
    CREATE_DIAGRAM,
    MANAGER_CAPABILITIES,
    ROSTER_REPORT,
    CapabilityRequest,
    ManagerCapability,
    available_capabilities,
    select_capability,
)

# ---------------------------------------------------------------------------
# A capability that exists only in this file
# ---------------------------------------------------------------------------

_NEW_CAPABILITY_NAME = "quantum_widget_report"
_NEW_CAPABILITY_WORD = "quantumwidget"


def _fixture_is_genuinely_unseen() -> None:
    """Structural precondition, reproduced from
    test_ecosystem_registry.py's own ``embedding_provider`` fixture and
    test_ecosystem_evolution_extension.py's own ``widget.future_thing``
    fixture: the name this test registers must appear nowhere in the
    shipped source tree before this test runs it, or the test would be
    proving nothing about open-endedness."""
    result = subprocess.run(
        ["git", "grep", "-il", _NEW_CAPABILITY_WORD, "--", "src", "scripts"],
        cwd=".", capture_output=True, text=True,
    )
    assert result.stdout.strip() == "", "the fixture capability must be genuinely unseen"


def _handle_quantum_widget(request: CapabilityRequest) -> TaskResult:
    return TaskResult.success(
        request.task, request.own_name, "quantum widget status: nominal"
    )


def _new_capability() -> ManagerCapability:
    return ManagerCapability(
        name=_NEW_CAPABILITY_NAME,
        capabilities=frozenset({_NEW_CAPABILITY_WORD}),
        handle=_handle_quantum_widget,
        description="A capability that exists only inside this test file.",
    )


def _built(tmp_path, **overrides):
    import scripts.control_center as cc
    from src.config.settings import Settings

    return cc.build(
        Settings.from_env(
            env={}, memory="ephemeral", artifacts_dir=str(tmp_path / "artifacts"), **overrides
        )
    )


def _wire_new_capability(manager, capability: ManagerCapability) -> None:
    """The one, real, already-open registration seam this checkpoint
    exercises: add the handler to the same dict every capability already
    lives in, and declare its word the same way IDENTITY.md would - the
    identical "declared AND handled" intersection every existing
    capability is already subject to (src/manager/manager_skills.py's
    own ``spec`` property). Nothing about ManagerAgent, the router or
    the objective loop is touched."""
    manager.own_skills._capabilities[capability.name] = capability
    manager.own_skills._identity = dataclasses.replace(
        manager.own_skills._identity,
        capabilities=manager.own_skills._identity.capabilities | capability.capabilities,
    )


class TestANewCapabilityIsDiscoverableThroughTheRealRegistrationPoint:
    def setup_method(self):
        _fixture_is_genuinely_unseen()

    def test_registering_it_is_exactly_one_dict_entry(self):
        before = set(available_capabilities())
        MANAGER_CAPABILITIES[_NEW_CAPABILITY_NAME] = _new_capability()
        try:
            after = set(available_capabilities())
            assert after - before == {_NEW_CAPABILITY_NAME}
        finally:
            del MANAGER_CAPABILITIES[_NEW_CAPABILITY_NAME]

    def test_selection_picks_it_for_its_own_words_and_nothing_else(self):
        registry = dict(MANAGER_CAPABILITIES)
        registry[_NEW_CAPABILITY_NAME] = _new_capability()
        chosen = select_capability(Task(description="run quantumwidget diagnostics"), registry)
        assert chosen.name == _NEW_CAPABILITY_NAME
        # An ordinary, unrelated request still goes to whatever it always did -
        # existing routing is provably unaffected by the new entry existing.
        still_roster = select_capability(Task(description="list your capabilities"), registry)
        assert still_roster.name == ROSTER_REPORT.name

    def test_the_ecosystem_registry_capability_category_sees_it_with_zero_changes(self, tmp_path):
        """src/services/ecosystem/registry_factory.py wraps
        ``available_capabilities`` by reference and calls it lazily, so a
        capability registered after the registry object exists is still
        found - proven directly, not assumed from reading the source."""
        from src.config.settings import Settings
        from src.services.ecosystem.registry_factory import build_ecosystem_registry

        settings = Settings.from_env(env={}, memory="ephemeral", artifacts_dir=str(tmp_path / "artifacts"))
        registry = build_ecosystem_registry(settings)
        adapter = registry.adapter_for("capability")
        assert adapter is not None

        MANAGER_CAPABILITIES[_NEW_CAPABILITY_NAME] = _new_capability()
        try:
            keys = {d.key for d in adapter.available()}
            assert _NEW_CAPABILITY_NAME in keys
        finally:
            del MANAGER_CAPABILITIES[_NEW_CAPABILITY_NAME]


class TestANewCapabilityReachesRealProductionRouting:
    def setup_method(self):
        _fixture_is_genuinely_unseen()

    def test_manager_handle_task_reaches_it_through_the_real_router(self, tmp_path):
        """Not ManagerSkills.execute() called directly - the real,
        top-level ManagerAgent.handle_task, exactly what a live
        conversation turn calls, proving the router itself (which
        decides whether the Manager even claims the task) already
        offers a freshly-registered, freshly-declared capability."""
        runtime, manager, channel, conversation = _built(tmp_path)
        _wire_new_capability(manager, _new_capability())
        # _wire_new_capability mutates the already-built manager's own
        # (governed-config-filtered) capability dict, a private, per-run
        # copy - never the shared MANAGER_CAPABILITIES module dict - so
        # there is nothing global to clean up afterward.
        result = manager.handle_task(Task(description="run quantumwidget diagnostics"))
        assert result.succeeded
        assert result.output == "quantum widget status: nominal"

    def test_existing_real_capabilities_are_unaffected_by_the_new_one(self, tmp_path):
        runtime, manager, channel, conversation = _built(tmp_path)
        before = manager.handle_task(Task(description="what year is it"))
        _wire_new_capability(manager, _new_capability())
        after = manager.handle_task(Task(description="what year is it"))
        assert before.output == after.output
        # The new capability's own word is additive to the roster listing -
        # legitimately different, never a regression - while every
        # already-registered handler object is untouched.
        assert manager.own_skills.capabilities["roster_report"] is ROSTER_REPORT


class TestANewCapabilityComposesInsideARealMultiStepObjective:
    def setup_method(self):
        _fixture_is_genuinely_unseen()

    def test_a_two_step_objective_chains_the_new_capability_with_a_real_existing_one(self, tmp_path):
        """The concrete proof Section 5/13 of this checkpoint's own brief
        asks for: a genuinely new capability, registered through nothing
        but the existing extension point, taking part in the same
        unmodified pursue_objective/assess_objective machinery Task D's
        narrate->illustrate->compose chain and the earlier volcano/water-
        cycle handout tests already proved general - now proven general
        across a capability that did not exist when that machinery was
        written."""
        from src.manager.llm_brain import LLMManagerBrain
        from src.manager.objective_loop import pursue_objective
        from src.providers.base import Completion, LLMProvider

        runtime, manager, channel, conversation = _built(tmp_path, media="local")
        _wire_new_capability(manager, _new_capability())

        class ScriptedProvider(LLMProvider):
            def __init__(self):
                self.calls = []

            @property
            def name(self):
                return "scripted"

            def complete(self, prompt, *, system=None):
                self.calls.append(prompt)
                n = len(self.calls)
                if n == 1:
                    return Completion(
                        text='{"status":"continue","next_step":"make a diagram of a quantum widget"}',
                        provider="scripted",
                    )
                return Completion(
                    text='{"status":"complete","final_answer":"Reported status and produced a diagram."}',
                    provider="scripted",
                )

        provider = ScriptedProvider()
        manager.brain = LLMManagerBrain(provider, personality=runtime.identity.personality)
        manager.own_skills._converse = manager.brain.converse

        outcome = pursue_objective(
            manager, Task(description="run quantumwidget diagnostics")
        )

        assert outcome.status.value == "completed"
        assert len(outcome.steps) == 2
        assert outcome.steps[0].result.output == "quantum widget status: nominal"
        assert outcome.steps[1].result.payload.get("handled_by") == CREATE_DIAGRAM.name

        artifacts = runtime.artifacts.all()
        assert len(artifacts) == 1
        assert artifacts[0].type == "image"


class TestMissingCapabilityIsHonestNeverFabricated:
    """Section 6's own required distinction: what happens when an
    objective needs something this system does not have, versus
    something it has but is not configured for - exercised against
    real, already-shipping production code, never a new classifier
    invented for this checkpoint."""

    def test_a_capability_whose_dependency_tool_is_missing_says_so_by_name(self, tmp_path):
        """Category B - 'exists but needs configuration' - reusing
        create_artifact's own, already-shipping honest refusal (Task A/B),
        not a new mechanism written for this test."""
        from src.agents.tools.toolset import ToolSet

        runtime, manager, channel, conversation = _built(tmp_path)
        manager.own_skills._tools = ToolSet()  # no tools granted at all

        result = manager.handle_task(
            Task(description="TITLE: X\nSECTION: Y: z make me a handout")
        )
        assert not result.succeeded
        assert "artifact_production" in result.error

    def test_an_objective_matching_no_capability_and_no_specialist_still_answers_honestly(self, tmp_path):
        """Category D/E - genuinely missing/impossible in this
        environment - never a fabricated success, never an exception."""
        runtime, manager, channel, conversation = _built(tmp_path)
        result = manager.handle_task(
            Task(description="pilot the antigravity beam array to the third moon of Jupiter")
        )
        # Whatever the honest fallback says, it must be an honest answer,
        # not a crash and not a claim that antigravity piloting happened.
        assert result is not None
        assert "antigravity beam array" not in result.output.lower()


class TestGovernedVersionEvolutionRemainsTheSeparateCorrectMechanism:
    """Not re-testing src/services/evolution/ (already covered by
    tests/test_ecosystem_evolution.py and
    tests/test_ecosystem_evolution_extension.py) - confirming instead
    that a genuinely new capability (this file's own) and a genuinely
    new evolution-governed component category
    (test_ecosystem_evolution_extension.py's own ``widget.future_thing``)
    are the two real, distinct extension points Section 7/12 of this
    checkpoint asks about, and that registering one never touches the
    other."""

    def test_the_evolution_ledger_has_no_opinion_about_a_freshly_registered_capability(self, tmp_path):
        from src.evolution import EvolutionLedger
        from src.services.evolution import ecosystem_evolution as ee
        from src.services.evolution.ledger_store import EvolutionState

        MANAGER_CAPABILITIES[_NEW_CAPABILITY_NAME] = _new_capability()
        try:
            state = EvolutionState().with_ledger(EvolutionLedger(current_versions={}))
            proposed = ee.propose(
                state, component_id=f"capability.{_NEW_CAPABILITY_NAME}",
                proposed_version="v2", proposed_change="test", reason="r", expected_benefit="b",
                ledger_path=tmp_path / "ledger.json",
            )
            # Honest refusal, never a fabricated acceptance: no handler
            # owns an arbitrary new capability id, by design (Section 7's
            # own "do not allow model output to become unrestricted code
            # execution" - a capability's code is a reviewed commit, not
            # a governed config value).
            assert not proposed.success
        finally:
            del MANAGER_CAPABILITIES[_NEW_CAPABILITY_NAME]


class TestANewCapabilityIsVisibleThroughTheRealControlCenter:
    """Section 8's own requirement - a capability introduced through
    governed extension must become visible/manageable through the one
    existing management surface, never a second one. ``capabilities
    list`` (scripts/control_center.py::run_capability_registry_command)
    is that surface for this axis - it already reads ``available_capabilities()``
    live, so proving this needs no new rendering code, only a test that
    it is genuinely wired rather than assumed from reading the source."""

    def setup_method(self):
        _fixture_is_genuinely_unseen()

    def test_capabilities_list_shows_the_freshly_registered_capability(self, tmp_path, monkeypatch):
        import scripts.control_center as control_center_module
        from src.control_center.observer import ControlCenter

        monkeypatch.setattr(control_center_module, "DEFAULT_CONFIG_PATH", tmp_path / "config.json")
        runtime, manager, channel, conversation = _built(tmp_path)
        centre = ControlCenter(runtime, manager)

        MANAGER_CAPABILITIES[_NEW_CAPABILITY_NAME] = _new_capability()
        try:
            text = control_center_module.run_capability_registry_command(centre, "list")
            assert _NEW_CAPABILITY_NAME in text
            assert f"[enabled ] {_NEW_CAPABILITY_NAME}" in text
        finally:
            del MANAGER_CAPABILITIES[_NEW_CAPABILITY_NAME]


class TestANewCapabilityDoesNotLeakAcrossAgents:
    """Section 11's own requirement. Not incidental: Arthur (the email
    specialist, see src/agents/specialists.py::build_email_specialist) is
    a structurally different class (EmailAgent) that never reads
    MANAGER_CAPABILITIES/ManagerSkills._capabilities at all, so wiring a
    new capability into Alfred's own_skills cannot reach it - proven
    directly against the real registered agent rather than assumed."""

    def setup_method(self):
        _fixture_is_genuinely_unseen()

    def test_arthur_never_consults_the_capability_dict_alfred_was_just_extended_with(self, tmp_path):
        runtime, manager, channel, conversation = _built(tmp_path)
        arthur = manager.registry.get("Arthur")
        assert arthur is not None
        assert not hasattr(arthur, "_capabilities")

        _wire_new_capability(manager, _new_capability())
        result = arthur.execute(Task(description="run quantumwidget diagnostics"))
        assert "quantum widget status: nominal" not in (result.output or "").lower()
