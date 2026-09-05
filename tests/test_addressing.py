"""Speaking to somebody in particular.

The rule this file protects:

    If a participant is already in the room and the person explicitly
    addresses them, that participant answers. Alfred does not answer on
    their behalf.

Alfred remains the orchestrator - for unaddressed requests, for absent
participants, and for anything needing coordination. What changed is that an
address is *evidence about who should answer*, and a better one than word
overlap, so the Manager uses it.

**One decision, not two routers.** The room resolves who was spoken to and
records it. The Manager decides what that means. Nothing else may choose an
agent, and the structural tests at the bottom prove the room cannot.

The load-bearing test is `test_an_address_overrides_capability_routing`. It is
built on a request whose words route somewhere else, so an implementation that
quietly ignored the address would still fail it.
"""

import ast
from pathlib import Path

import pytest

from src.composition import (
    build_channel,
    build_conversation,
    build_manager,
    build_runtime,
)
from src.config.settings import Settings
from src.control_center.observer import ControlCenter
from src.conversation.conversation import Participant, ParticipantKind
from src.core.contracts import Task
from src.services.email.sample_inbox import sample_inbox
from src.services.profile.loader import parse_profile

REPO_ROOT = Path(__file__).resolve().parent.parent

# A request whose words match Alfred's own capabilities, not the specialist's.
# This is what makes the override test mean something: if the address were
# dropped, capability routing would send it somewhere else and the assertion
# would fail.
OFF_DOMAIN = "what did you find"

SPECIALIST_WORK = "scan my inbox for opportunities"


def settings(**overrides):
    return Settings.from_env(
        env={}, log_level="CRITICAL", memory="ephemeral", **overrides
    )


def system():
    """A Control Center, door and room built by the real composition root."""
    runtime = build_runtime(settings())
    manager = build_manager(
        runtime,
        mailbox=sample_inbox(),
        profile=parse_profile("## Skills\n### Strong\npython\n"),
    )
    return (
        ControlCenter(runtime, manager),
        build_channel(runtime, manager),
        build_conversation(manager),
    )


def talk(centre, channel, conversation, words):
    import scripts.control_center as cc

    return cc.run_command(centre, f"talk {words}", 80, channel, conversation)


def bring_in_the_specialist(centre, channel, conversation):
    """Get Arthur into the room the way production does - by doing work.

    Not by constructing a Participant here: a test that hand-placed him would
    prove nothing about whether delegation actually puts a specialist in the
    room.
    """
    talk(centre, channel, conversation, SPECIALIST_WORK)
    assert conversation.has("Arthur"), "delegation should have brought him in"


# ---------------------------------------------------------------------------
# A. An address beats capability routing
# ---------------------------------------------------------------------------


class TestAnAddressDecidesWhoAnswers:
    def test_the_off_domain_request_would_not_reach_the_specialist(self):
        """The premise, asserted rather than assumed.

        Everything below rests on this request routing *away* from the
        specialist. If a future capability change made these words match him,
        the override test would start passing for the wrong reason - so the
        premise is checked in its own test that would fail loudly instead.
        """
        centre, _, _ = system()
        decision = centre.manager.diagnose(Task(description=OFF_DOMAIN))
        assert decision is not None
        assert decision.agent_name != "Arthur"

    def test_an_address_overrides_capability_routing(self):
        """The load-bearing Phase 4 behaviour."""
        centre, channel, conversation = system()
        bring_in_the_specialist(centre, channel, conversation)

        talk(centre, channel, conversation, f"Arthur, {OFF_DOMAIN}")

        assert conversation.utterances[-1].speaker == "Arthur"

    def test_the_manager_records_that_the_address_decided_it(self):
        """Observable, not silent. A person reading the trail can see *why*
        that agent answered."""
        centre, _, _ = system()
        centre.manager.registry.get("Arthur")

        decision = centre.manager.decide(Task(description=OFF_DOMAIN), "Arthur")

        assert decision.agent_name == "Arthur"
        assert "addressed" in decision.reason

    def test_addressing_the_manager_reaches_the_manager(self):
        """The rule is about who was spoken to, not about specialists."""
        centre, channel, conversation = system()

        talk(centre, channel, conversation, "Alfred, roster")

        assert conversation.utterances[-1].speaker == "Alfred"

    def test_an_address_still_goes_through_the_one_orchestration(self):
        """Not a bypass. An addressed task is assembled, executed, assessed and
        remembered like any other - only the decision differs."""
        centre, channel, conversation = system()
        bring_in_the_specialist(centre, channel, conversation)
        before = len(centre.manager.events.of_kind("delegation"))

        talk(centre, channel, conversation, f"Arthur, {OFF_DOMAIN}")

        after = centre.manager.events.of_kind("delegation")
        assert len(after) == before + 1, "an addressed handover is still a delegation"
        assert after[-1].fact("specialist") == "Arthur"


# ---------------------------------------------------------------------------
# B. Nothing changes when nobody is addressed
# ---------------------------------------------------------------------------


class TestUnaddressedRequestsAreUnchanged:
    def test_a_general_request_is_answered_by_the_manager(self):
        centre, channel, conversation = system()

        talk(centre, channel, conversation, OFF_DOMAIN)

        assert conversation.utterances[-1].speaker == "Alfred"

    def test_specialist_work_still_reaches_the_specialist_without_an_address(self):
        """Capability routing is untouched. Alfred delegates because the work
        needs it, not because anybody was named."""
        centre, channel, conversation = system()

        talk(centre, channel, conversation, SPECIALIST_WORK)

        assert conversation.utterances[-1].speaker == "Arthur"
        assert conversation.utterances[-2].addressed is None

    def test_decide_without_an_address_is_exactly_diagnose(self):
        centre, _, _ = system()
        task = Task(description=SPECIALIST_WORK)

        assert centre.manager.decide(task) == centre.manager.diagnose(task)
        assert centre.manager.decide(task, None) == centre.manager.diagnose(task)


# ---------------------------------------------------------------------------
# C. An absent participant is never invented
# ---------------------------------------------------------------------------


class TestAnAbsentParticipantIsNotInvented:
    def test_addressing_somebody_absent_resolves_to_nobody(self):
        centre, channel, conversation = system()
        assert conversation.addressed_in("Research, what do you think") is None

    def test_the_participant_list_is_unchanged(self):
        centre, channel, conversation = system()
        before = [p.name for p in conversation.participants]

        talk(centre, channel, conversation, "Research, what do you think")

        assert [p.name for p in conversation.participants] == before
        assert not conversation.has("Research")

    def test_the_manager_remains_responsible(self):
        """Nobody was reachable, so the orchestrator decides - which is the
        honest outcome, not an error."""
        centre, channel, conversation = system()

        talk(centre, channel, conversation, "Research, what do you think")

        assert conversation.utterances[-1].speaker == "Alfred"

    def test_an_unregistered_address_falls_back_to_routing(self):
        """At the Manager, not just in the room: a name the registry does not
        know must not become an agent."""
        centre, _, _ = system()
        task = Task(description=SPECIALIST_WORK)

        decided = centre.manager.decide(task, "Nobody At All")

        assert decided == centre.manager.diagnose(task)
        assert decided.agent_name != "Nobody At All"


# ---------------------------------------------------------------------------
# D. The specialist speaks as itself
# ---------------------------------------------------------------------------


class TestTheAddressedSpecialistSpeaksAsItself:
    def test_the_manager_utters_none_of_the_specialists_words(self):
        centre, channel, conversation = system()
        bring_in_the_specialist(centre, channel, conversation)
        already = len(conversation.said_by("Alfred"))

        talk(centre, channel, conversation, f"Arthur, {OFF_DOMAIN}")

        assert len(conversation.said_by("Alfred")) == already

    def test_the_recorded_text_is_the_specialists_own_output(self):
        """Unchanged by the Manager assessing it - not a summary, not a
        paraphrase."""
        centre, channel, conversation = system()
        bring_in_the_specialist(centre, channel, conversation)

        talk(centre, channel, conversation, f"Arthur, {OFF_DOMAIN}")

        answer = conversation.utterances[-1]
        assert answer.detail.handled_by == "Arthur"
        assert answer.text.startswith(answer.detail.outcome[:40])

    def test_the_address_is_recorded_on_the_question(self):
        centre, channel, conversation = system()
        bring_in_the_specialist(centre, channel, conversation)

        talk(centre, channel, conversation, f"Arthur, {OFF_DOMAIN}")

        question = conversation.utterances[-2]
        assert question.speaker == "you"
        assert question.addressed == "Arthur"


# ---------------------------------------------------------------------------
# E. Alfred is still a manager, not a router
# ---------------------------------------------------------------------------


class TestTheManagerStillDoesHisOwnWork:
    def test_he_handles_a_small_general_task_himself(self):
        centre, channel, conversation = system()

        talk(centre, channel, conversation, "list your capabilities")

        assert conversation.utterances[-1].speaker == "Alfred"
        assert not conversation.has("Arthur")

    def test_addressing_did_not_make_him_delegate_everything(self):
        centre, channel, conversation = system()

        talk(centre, channel, conversation, "roster")

        assert centre.manager.events.of_kind("delegation") == ()


# ---------------------------------------------------------------------------
# The room records; it never decides
# ---------------------------------------------------------------------------


class TestTheRoomIsNotARouter:
    def test_it_has_no_way_to_decide_route_or_invoke(self):
        _, _, conversation = system()
        for forbidden in (
            "route", "decide", "diagnose", "handle_task", "delegate",
            "assemble", "take_action", "registry", "manager",
        ):
            assert not hasattr(conversation, forbidden), forbidden

    def test_resolving_an_address_invokes_nobody(self):
        centre, channel, conversation = system()
        bring_in_the_specialist(centre, channel, conversation)
        before = len(conversation)

        conversation.addressed_in(f"Arthur, {OFF_DOMAIN}")

        assert len(conversation) == before

    def test_no_second_router_was_introduced(self):
        """The address is an input to the Manager's decision, not a rival
        intelligence. Nothing anywhere declares a competing router."""
        for area in ("src",):
            for path in sorted((REPO_ROOT / area).rglob("*.py")):
                tree = ast.parse(path.read_text(encoding="utf-8"))
                for node in ast.walk(tree):
                    if isinstance(node, ast.ClassDef):
                        assert node.name not in (
                            "AddressRouter", "ConversationRouter", "ParticipantRouter",
                        ), (path.name, node.name)

    def test_the_decision_belongs_to_the_manager(self):
        """Structural: the only thing that turns an address into an agent is
        ManagerAgent.decide."""
        source = (REPO_ROOT / "src" / "manager" / "manager.py").read_text(
            encoding="utf-8"
        )
        assert "def decide(" in source

        for module in (
            REPO_ROOT / "src" / "conversation" / "conversation.py",
            REPO_ROOT / "src" / "channels" / "channel.py",
        ):
            tree = ast.parse(module.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                    assert node.func.attr not in ("route", "decide"), (
                        module.name,
                        node.func.attr,
                    )
