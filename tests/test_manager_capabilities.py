"""What Alfred can do, and why saying so is not enough.

IDENTITY.md declared a list of capabilities from the first commit, and until
now the Manager had exactly one behaviour behind all of them. `plan` and
`coordinate` were declared, routable, and answered with a roster listing -
reported as a completed mission. That is the same defect coherence.py refuses
one layer up, a provider configured and then never consumed, and it was
invisible precisely because a roster listing is a plausible enough answer to
almost anything.

Both words are now gone from the document and from the handler. Nothing
implements planning, so nothing claims to.

The rule this file pins:

    offered = declared in IDENTITY.md  AND  answered by a registered handler

which cuts both ways on purpose. A word added to the document grants nothing
without a handler. A handler registered in code widens nothing until the
document declares it. What the Manager takes on stays an authored decision
that the code has to be able to honour.

And the structural claim underneath: adding a capability registers a handler.
It does not add a branch to ManagerAgent, which has never known what a
capability is and must not start.
"""

import subprocess
from pathlib import Path

import pytest

from src.agents.echo_agent import EchoAgent
from src.core.contracts import AgentSpec, Task, TaskResult
from src.manager.capabilities import (
    DEFAULT_CAPABILITY,
    MANAGER_CAPABILITIES,
    OPEN_CONVERSATION,
    ROSTER_REPORT,
    CapabilityRequest,
    ManagerCapability,
    available_capabilities,
    handled_words,
    offered_capabilities,
    report_roster,
    select_capability,
)
from src.manager.manager import ManagerAgent
from src.manager.manager_skills import ManagerSkills
from src.services.identity.identity import AgentIdentity

REPO_ROOT = Path(__file__).resolve().parent.parent

IDENTITY = AgentIdentity(
    name="Alfred",
    role="Orchestrator",
    description="Coordinates missions and reports on capabilities.",
    capabilities=frozenset({"capabilities", "status", "roster", "report"}),
)


def a_request(task="list your capabilities", identity=IDENTITY, roster=()):
    return CapabilityRequest(
        task=Task(description=task),
        identity=identity,
        roster=lambda: roster,
        own_name=identity.name,
        own_capabilities=tuple(sorted(identity.capabilities)),
    )


def build(specialists=(), **kwargs):
    """A real Manager with Alfred's skills registered, as production builds it."""
    manager = ManagerAgent()
    manager.own_skills = ManagerSkills(
        IDENTITY, roster=manager.registry.all_agents, **kwargs
    )
    manager.registry.register(manager.own_skills)
    for agent in specialists:
        manager.register_agent(agent)
    return manager


# A capability that exists only in this file. Registered by injection rather
# than by mutating the shipped registry, so no test can leak a capability into
# another - and so the project keeps shipping exactly what it shipped.
def handle_weather(request: CapabilityRequest) -> TaskResult:
    return TaskResult.success(request.task, request.own_name, "it is raining")


WEATHER = ManagerCapability(
    name="weather_report",
    capabilities=frozenset({"weather", "forecast"}),
    handle=handle_weather,
    description="A capability that exists only inside this test file.",
)


# ---------------------------------------------------------------------------
# Today's behaviour, unchanged
# ---------------------------------------------------------------------------


class TestExistingBehaviourIsPreserved:
    """The roster report is moved, not rewritten. These are the assertions the
    old test file already made, restated against the new path."""

    def test_the_default_handler_is_the_roster_report(self):
        assert DEFAULT_CAPABILITY is ROSTER_REPORT

    def test_it_reports_its_own_capabilities(self):
        result = build([EchoAgent(name="arthur_like", capabilities={"email"})]).handle_task(
            Task(description="list your capabilities")
        )
        assert "roster" in result.output

    def test_it_reports_the_specialist_roster(self):
        result = build([EchoAgent(name="arthur_like", capabilities={"email"})]).handle_task(
            Task(description="list your capabilities")
        )
        assert "arthur_like" in result.output

    def test_the_payload_still_carries_the_structured_roster(self):
        result = build([EchoAgent(name="arthur_like", capabilities={"email"})]).handle_task(
            Task(description="list your capabilities")
        )
        assert "arthur_like" in result.payload["specialists"]
        assert "roster" in result.payload["own_capabilities"]

    def test_it_copes_with_no_specialists(self):
        assert "none registered" in build().handle_task(
            Task(description="list your capabilities")
        ).output

    def test_the_roster_reflects_agents_added_after_construction(self):
        manager = build()
        manager.register_agent(EchoAgent(name="late", capabilities={"email"}))
        assert "late" in manager.handle_task(Task(description="roster")).output

    def test_the_manager_never_lists_itself_as_a_specialist(self):
        """It registers into the same registry as every specialist, so it would
        otherwise report itself as one of its own delegates."""
        result = build().handle_task(Task(description="roster"))
        assert result.payload["specialists"] == {}


# ---------------------------------------------------------------------------
# Selection goes through the capability architecture
# ---------------------------------------------------------------------------


class TestHandlersAreSelectedThroughTheRegistry:
    def test_the_shipped_registry_holds_its_handlers(self):
        """Exact, so a handler cannot appear or vanish unnoticed.

        Eleven: the roster report, the general capability floor Step 9 added so
        ordinary work is attempted rather than refused, the participant
        invitation Phase 5 added so somebody can be brought into a
        conversation, the small-talk floor Stage 11.3 added so a short
        conversational turn ("thanks", "okay", "good morning") gets a brief,
        natural reply instead of finding no agent, open_conversation (the
        Stage 11 audit fix) so a genuinely open-ended question or remark
        reaches the Brain instead of a roster listing standing in for an
        answer, forget/recall_memory (Stage 12) so an explicit memory
        command is honoured - never "remember", which already has a real,
        production-wired path outside this registry (see
        src.manager.capabilities's own Stage 12 section docstring for why
        that one is deliberately absent here) - live_information
        (Stage 14) so a request needing current, external information is
        acquired through a governed tool and reasoned over, rather than
        answered from Alfred's own trained knowledge as though it were
        current - and three further Stage 12 additions: record_this (an
        explicit "record this [for X]" capture, distinct from "remember"),
        temporal_derivation (age/birthday/completion answered by
        computing from a stored date, never a stored answer), and
        milestone_recognition (a structured, governed personal milestone,
        confirmed or auto-stored per the user's own established
        preference), communication (Stage 15) so a recognised
        call/text/read/reply request is dispatched to a governed
        communication tool - never a contact list, never a fabricated
        result - personal_recurring_events (Stage 12 closure) so
        another person's recurring birthday - confirmed, uncertain, or
        deliberately changed - is recognised and governed the same way,
        never with a date silently chosen on the user's behalf, and
        connected_action (Stage 16) so a governed action through an
        external connection is attempted only through the identical
        ToolSet boundary, honest about capability availability,
        connection availability and authorization as three separate
        facts, and current_datetime (Foundation Repair #1) so "what
        day/date/month/year is it" is answered from the real calendar -
        never a stored fact, never the Brain, and never the false-success
        "Got it." small_talk's own word-overlap scoring used to produce
        for it. The list grows only when a handler genuinely exists -
        that is the point of asserting it exactly rather than loosely.
        """
        assert available_capabilities() == [
            # Stage 17.22 added this one and did not update this pin, so the
            # assertion had been failing since. Restored rather than
            # loosened: an exact list is the whole point of the test.
            "about_self",
            # Generalized Personal Organization/Calendar checkpoint: real,
            # structured calendar items - dates, times, durations, weekly
            # recurrence - built on the personal-context mechanism, never
            # a fixed catalog of activities.
            "calendar",
            # Real Audio Recording/Capture checkpoint: discards an
            # in-progress recording session without saving it.
            "cancel_recording",
            # Video-clipping checkpoint: cutting real clips from an
            # existing video - deterministic given explicit timestamps,
            # Brain-assisted (over given text only, never raw video/
            # audio) when asked to find moments instead.
            "clip_video",
            "communication",
            # Phase 5.1 (Tasks): an outstanding, actionable item with real
            # state - four handlers, one per operation, each recognised by
            # its own marker shape rather than the ordinary word "task".
            "complete_task",
            "connected_action",
            # Phase 2 (Memory & Personal Context): replaces or invalidates
            # an existing memory when the user explicitly corrects it -
            # "that's wrong, ...", "correction: ...", "update what you
            # remembered about X to Y" - never inferred from similarity.
            "correct_memory",
            # Multimodal workspace/artifact checkpoint: a real, persistent,
            # versioned document artifact ("handout") - deterministic given
            # already-structured content, Brain-assisted when organizing an
            # objective into that structure instead.
            "create_artifact",
            # Multimodal workspace/artifact checkpoint, image/diagram half:
            # a real, persistent, versioned image artifact - a diagram, a
            # map, or any other real visual, deterministic given an
            # already-descriptive request, Brain-assisted for a bare ask.
            "create_diagram",
            "create_task",
            "current_datetime",
            "forget",
            "general_assistance",
            # Phase 3 (Knowledge, Research & Live Information): composes a
            # real document from recent findings through the real Brain
            # and saves it as a real file - "create a report from these
            # findings".
            "generate_document",
            # Files/Documents completion: real visual reasoning through
            # the Brain's own analyze_image question, honestly bounded
            # to whichever provider genuinely supports it (today:
            # Anthropic only).
            "image_analysis",
            # Stage 18: settled general knowledge and history as one
            # capability for every subject, and the learning context that
            # pitches it. Both are kinds of knowing, not topics - see
            # src/manager/knowledge.py for the four-way separation they
            # exist to keep.
            "knowledge",
            "learning_context",
            # Real Audio Recording/Capture checkpoint: lists saved
            # recordings, or answers whether one matching a topic exists -
            # the identical bare-listing-or-named-topic shape "list_tasks"
            # already uses.
            "list_recordings",
            "list_tasks",
            "live_information",
            "media_production",
            "milestone_recognition",
            "open_conversation",
            "participant_invitation",
            # Generalized user knowledge/personal-context checkpoint: a
            # self-declared, updatable fact about the person - a skill,
            # a schedule, an organisational context - so a later
            # statement about the same topic correctly supersedes an
            # earlier one.
            "personal_context",
            "personal_recurring_events",
            "recall_memory",
            "record_this",
            # Alfred's Real 3D Workspace: the task lifecycle's fourth
            # verb - reopens a completed task, the mirror of
            # complete_task, so a completed task is never a dead end.
            "reopen_task",
            # Multimodal workspace/artifact checkpoint: revises an
            # existing document artifact under the same identity, a new
            # real version rather than a disconnected new file.
            "revise_artifact",
            # Multimodal workspace/artifact checkpoint, image/diagram half:
            # revises an existing image/diagram artifact under the same
            # identity, a new real version rather than a disconnected file.
            "revise_diagram",
            "roster_report",
            # Agent-autonomous voice selection checkpoint: chooses a
            # real, currently-available voice matching a requested
            # language/accent, never overriding an explicit user choice.
            "select_voice",
            "small_talk",
            # Real Audio Recording/Capture checkpoint: opens/stops a real
            # recording session - never a capture itself (only a browser
            # can do that), just the one shared, live signal both a
            # natural-language request and the frontend's own upload
            # route read and write.
            "start_recording",
            "stop_recording",
            "temporal_derivation",
            "update_task",
        ]

    def test_selection_scores_words_against_handlers(self):
        registry = {ROSTER_REPORT.name: ROSTER_REPORT, WEATHER.name: WEATHER}
        assert select_capability(Task(description="what is the weather"), registry) is WEATHER
        assert select_capability(Task(description="show the roster"), registry) is ROSTER_REPORT

    def test_an_unmatched_task_falls_back_to_open_conversation_when_there_is_content(self):
        """The router only sends work here when it scored a declared word, so
        'routed to me but not by anything specific' is a real state. With real
        content and nowhere more specific to go, the honest answer is to let
        the Brain take a look - never a roster listing standing in for one -
        so this now falls to open_conversation rather than the roster
        default (see the next test for the one case that still doesn't:
        truly empty content)."""
        registry = {WEATHER.name: WEATHER}
        assert select_capability(Task(description="xyzzy"), registry) is OPEN_CONVERSATION

    def test_an_unmatched_empty_task_still_falls_back_to_the_roster(self):
        """Nothing for the Brain to reason about either - the one case where
        the old, harmless default still applies."""
        registry = {WEATHER.name: WEATHER}
        assert select_capability(Task(description="..."), registry) is DEFAULT_CAPABILITY

    def test_ties_break_on_handler_name_so_selection_is_repeatable(self):
        twin = ManagerCapability(
            name="aardvark", capabilities=frozenset({"weather"}), handle=handle_weather
        )
        registry = {WEATHER.name: WEATHER, twin.name: twin}
        chosen = {select_capability(Task(description="weather"), registry).name for _ in range(5)}
        assert chosen == {"aardvark"}

    def test_the_selected_handler_is_the_one_that_answers(self):
        manager = build(capabilities={ROSTER_REPORT.name: ROSTER_REPORT, WEATHER.name: WEATHER})
        # 'weather' has to be declared too, or it would not be offered at all.
        manager.own_skills = ManagerSkills(
            AgentIdentity(name="Alfred", capabilities=frozenset({"roster", "weather"})),
            roster=manager.registry.all_agents,
            capabilities={ROSTER_REPORT.name: ROSTER_REPORT, WEATHER.name: WEATHER},
        )
        assert manager.own_skills.execute(Task(description="the weather")).output == "it is raining"


# ---------------------------------------------------------------------------
# A declaration is not a capability
# ---------------------------------------------------------------------------


class TestDeclaringACapabilityDoesNotCreateOne:
    def test_an_unhandled_word_is_not_offered(self):
        """The decisive one. Writing `send` into IDENTITY.md must not make the
        router willing to send send-work to the Manager."""
        identity = AgentIdentity(
            name="Alfred", capabilities=frozenset({"roster", "send", "apply", "delete"})
        )
        offered = ManagerSkills(identity).spec.capabilities
        assert offered == {"roster"}
        for claimed in ("send", "apply", "delete"):
            assert claimed not in offered

    def test_an_unhandled_word_is_reported_not_silently_dropped(self, caplog):
        """A document that no longer means what it says is exactly what someone
        needs telling about. Ignored, but never quietly."""
        import logging

        with caplog.at_level(logging.WARNING):
            offered_capabilities(frozenset({"roster", "teleport"}))
        assert "teleport" in caplog.text

    def test_the_intersection_holds_in_the_other_direction_too(self):
        """A handler registered in code does not widen the Manager on its own.

        Otherwise importing a module would change what Alfred takes on, and
        what he takes on is meant to be an authored decision.
        """
        identity = AgentIdentity(name="Alfred", capabilities=frozenset({"roster"}))
        skills = ManagerSkills(
            identity, capabilities={ROSTER_REPORT.name: ROSTER_REPORT, WEATHER.name: WEATHER}
        )
        assert "weather" not in skills.spec.capabilities

    def test_an_unregistered_capability_cannot_execute(self):
        """Not offered, not routed, and not runnable even when asked directly.

        With no handler answering `teleport` and no Brain configured on this
        bare ManagerSkills, the request reaches open_conversation's own
        honest fallback - a named failure explaining no reasoning capability
        is available - never a fabricated success finding some path to a
        behaviour nobody wrote, and never a roster listing passed off as an
        answer either.
        """
        identity = AgentIdentity(name="Alfred", capabilities=frozenset({"roster"}))
        result = ManagerSkills(identity).execute(Task(description="teleport me home"))
        assert not result.succeeded
        assert "reason about that" in result.error

    def test_prose_in_the_identity_document_grants_nothing(self):
        """Step 1 proved this for AgentIdentity.to_spec. It has to stay true of
        the path the Manager actually offers, which is no longer that method."""
        identity = AgentIdentity(
            name="Alfred",
            role="An orchestrator that sends email and applies for jobs.",
            purpose="To send, delete, apply, bid, contact and purchase.",
            description="Sends and deletes mail on your behalf.",
            capabilities=frozenset({"roster"}),
            area_of_responsibility="Sending, deleting and applying to anything.",
        )
        offered = ManagerSkills(identity).spec.capabilities
        assert offered == {"roster"}

    def test_plan_and_coordinate_are_no_longer_claimed(self):
        """They were declared from the first commit and never implemented.

        The roster handler does not claim them, so a planning request finds no
        agent instead of being answered with a roster listing and marked done.
        """
        assert "plan" not in ROSTER_REPORT.capabilities
        assert "coordinate" not in ROSTER_REPORT.capabilities
        assert "plan" not in handled_words()
        assert "coordinate" not in handled_words()

    def test_a_planning_request_reaches_the_manager_honestly_not_as_a_completed_roster(self):
        """The user-visible consequence, asserted rather than described.

        "Never invent" and "Escalate rather than guess" are principles this
        project states in SOUL.md. Answering a planning request with a
        roster - and marking it COMPLETED - broke both. The Stage 11 audit
        fix changes *where* this lands (the Manager is now the default
        owner of work no specialist's capabilities matched, rather than the
        request dying with no agent at all) but not the principle: with no
        Brain configured here, open_conversation still refuses to invent an
        answer, so this is FAILED with an honest reason - never COMPLETED,
        and never NO_AGENT_FOUND standing in for "nobody can help", when the
        Manager genuinely is the one who owns unclaimed work now.
        """
        from src.core.contracts import TaskStatus
        from src.services.identity.identity import load_identity

        manager = ManagerAgent()
        manager.own_skills = ManagerSkills(
            load_identity("IDENTITY.md"), roster=manager.registry.all_agents
        )
        manager.registry.register(manager.own_skills)

        result = manager.handle_task(Task(description="plan my week"))
        assert result.status is TaskStatus.FAILED
        assert result.agent_name == "Alfred"
        assert "reason about that" in result.error

    def test_coordination_reporting_still_works(self):
        """Removing the unimplemented words must not have cost the real ones."""
        from src.services.identity.identity import load_identity

        manager = ManagerAgent()
        manager.own_skills = ManagerSkills(
            load_identity("IDENTITY.md"), roster=manager.registry.all_agents
        )
        manager.registry.register(manager.own_skills)

        assert manager.handle_task(Task(description="status report")).succeeded

    def test_every_word_alfred_ships_with_is_backed(self):
        """The committed IDENTITY.md, against the shipped registry. If someone
        adds a word to the document without a handler, this fails."""
        from src.services.identity.identity import load_identity

        declared = load_identity("IDENTITY.md").capabilities
        assert declared - handled_words() == set()


# ---------------------------------------------------------------------------
# Extensibility without touching the orchestrator
# ---------------------------------------------------------------------------


class TestAddingACapabilityTouchesNoOrchestration:
    # The entry points are deliberately absent. Freezing them here
    # over-reached: the claim is "adding a capability changes no
    # orchestration", not "main.py never changes". Step 8 wires a per-run
    # document into both, which is a legitimate evolution a change-detector
    # would have forbidden while proving nothing extra. The sharper assertion
    # is below: an entry point may grow, but never grow a capability handler.
    ORCHESTRATION = (
        "src/manager/manager.py",
        "src/manager/router.py",
        "src/manager/assessor.py",
    )
    ENTRY_POINTS = ("main.py", "scripts/heartbeat.py")

    def test_orchestration_knows_no_capability_handler(self):
        """The sharper form of the claim a byte-freeze used to make here.

        This was `git diff HEAD` over the three orchestration files, and it
        over-reached in the same way the entry-point freeze beneath it did.
        The claim is "adding a capability changes no orchestration", not
        "orchestration never changes for any reason" - and a change-detector
        cannot tell those apart. It forbade Alfred emitting a delegation event,
        which is orchestration doing its own job and has nothing to do with
        capabilities; and being diff-against-HEAD it fell silent the moment
        anything was committed, so it never held the line it claimed to.

        The property itself is what matters, and it was already asserted for
        ManagerAgent by test_manager_agent_has_no_knowledge_of_capabilities
        below. This is that same check across all three orchestration files:
        what the Manager can do is decided by IDENTITY.md and
        src/manager/capabilities.py, and orchestration that started deciding it
        would be a second source of truth.
        """
        forbidden = (
            "ManagerCapability", "CapabilityRequest", "MANAGER_CAPABILITIES",
            "select_capability", "offered_capabilities", "report_roster",
            "ROSTER_REPORT", "GENERAL_CAPABILITIES", "general_assistance",
        )
        for relative in self.ORCHESTRATION:
            source = (REPO_ROOT / relative).read_text(encoding="utf-8")
            for name in forbidden:
                assert name not in source, (relative, name)

    def test_the_entry_points_know_no_capability_handler(self):
        """The sharper form of the claim the byte-freeze used to make.

        An entry point may grow - Step 8 gives both a schedule to load - but
        it must never grow a *capability*. Neither may name a handler, the
        registry, or the selection machinery: what the Manager can do is
        decided by IDENTITY.md and src/manager/capabilities.py, and an entry
        point that started deciding it would be a second source of truth.
        """
        forbidden = (
            "ManagerCapability", "CapabilityRequest", "MANAGER_CAPABILITIES",
            "select_capability", "offered_capabilities", "report_roster",
            "ROSTER_REPORT",
        )
        for entry_point in self.ENTRY_POINTS:
            source = (REPO_ROOT / entry_point).read_text(encoding="utf-8")
            for name in forbidden:
                assert name not in source, (entry_point, name)

    def test_a_new_capability_runs_end_to_end_through_the_real_manager(self):
        """Registered and declared, then driven through handle_task - so the
        router, the registry and the assessor all see it as ordinary work."""
        identity = AgentIdentity(
            name="Alfred", capabilities=frozenset({"roster", "weather"})
        )
        manager = ManagerAgent()
        manager.own_skills = ManagerSkills(
            identity,
            roster=manager.registry.all_agents,
            capabilities={ROSTER_REPORT.name: ROSTER_REPORT, WEATHER.name: WEATHER},
        )
        manager.registry.register(manager.own_skills)

        result = manager.handle_task(Task(description="what is the weather today"))
        assert result.succeeded
        assert result.output == "it is raining"
        assert result.agent_name == "Alfred"

    def test_manager_agent_has_no_knowledge_of_capabilities(self):
        """ManagerAgent runs the DATA loop and holds no algorithm. It must not
        have learned what a capability handler is."""
        import inspect

        import src.manager.manager as manager_module

        source = inspect.getsource(manager_module)
        for leaked in ("ManagerCapability", "CapabilityRequest", "select_capability"):
            assert leaked not in source, leaked


# ---------------------------------------------------------------------------
# Boundaries a handler must not be able to cross
# ---------------------------------------------------------------------------


class TestAHandlerIsGivenNoReach:
    def test_a_request_carries_no_route_to_the_orchestrator(self):
        """A capability answers a question. It does not get to orchestrate."""
        request = a_request()
        for forbidden in ("manager", "registry", "router", "assessor", "memory", "brain"):
            assert not hasattr(request, forbidden), forbidden

    def test_a_handler_cannot_reach_a_specialists_memory(self):
        """It can see who is registered - it is reporting a roster - but the
        roster is BaseAgent objects, and nothing hands it a store."""
        request = a_request()
        assert not hasattr(request, "memories")

    def test_a_capability_must_answer_to_something(self):
        with pytest.raises(ValueError):
            ManagerCapability(name="mute", capabilities=frozenset(), handle=handle_weather)

    def test_a_capability_must_be_named(self):
        with pytest.raises(ValueError):
            ManagerCapability(name="  ", capabilities=frozenset({"x"}), handle=handle_weather)


# ---------------------------------------------------------------------------
# Everything around it is unchanged
# ---------------------------------------------------------------------------


class TestSurroundingArchitectureIsUntouched:
    def test_specialist_routing_is_unchanged(self):
        """Domain work still goes to the specialist; coordination still stays."""
        manager = build([EchoAgent(name="Arthur", capabilities={"email", "inbox"})])
        assert manager.diagnose(Task(description="read my email")).agent_name == "Arthur"
        assert manager.diagnose(Task(description="status report")).agent_name == "Alfred"

    def test_the_manager_does_not_delegate_merely_because_a_specialist_exists(self):
        manager = build(
            [
                EchoAgent(name="Arthur", capabilities={"email"}),
                EchoAgent(name="other", capabilities={"weather"}),
            ]
        )
        assert manager.diagnose(Task(description="status report")).agent_name == "Alfred"

    def test_alfred_and_arthur_are_still_correctly_named(self):
        from src.services.identity.identity import load_identity
        from src.services.identity.specialist import load_specialist_identity

        assert load_identity("IDENTITY.md").name == "Alfred"
        assert load_specialist_identity("identities", "email_agent").name == "Arthur"

    def test_gmail_is_still_read_only_and_sending_still_refused(self):
        from src.agents.email_agent import EmailAgent
        from src.services.email.gmail_auth import GMAIL_READONLY_SCOPE, SCOPES
        from src.services.email.in_memory_mailbox import InMemoryMailbox
        from src.services.email.mailbox import Outbox

        assert SCOPES == (GMAIL_READONLY_SCOPE,)
        assert EmailAgent(InMemoryMailbox()).can_send is False
        with pytest.raises(TypeError):
            Outbox()

    def test_full_brain_half_brain_semantics_are_unchanged(self):
        from src.manager.cognition import CognitiveMode, assess_manager

        profile = assess_manager(build())
        assert profile.mode is CognitiveMode.HALF_BRAIN
        assert "routing" in profile.capabilities
        assert "memory recall" in profile.capabilities

    def test_the_manager_still_owns_its_own_brain_and_memory(self):
        """A capability registry is neither, and must not have become either."""
        manager = build()
        assert manager.brain is not manager.memory
        assert not hasattr(manager.own_skills, "memory")
        assert not hasattr(manager.own_skills, "brain")

    def test_the_capability_registry_is_stateless_between_managers(self):
        """Two Managers share the handler table and share nothing else - the
        same reasoning that lets brains be built once and memories not."""
        first, second = build(), build()
        assert first.own_skills.capabilities == second.own_skills.capabilities
        assert first.memory is not second.memory

    def test_a_handler_returns_the_standard_result_shape(self):
        """So the assessor judges a capability answer exactly as it judges a
        specialist's - no special case anywhere."""
        result = report_roster(a_request())
        assert isinstance(result, TaskResult)
        assert result.succeeded
        assert isinstance(ManagerSkills(IDENTITY).spec, AgentSpec)
