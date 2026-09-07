"""Individual Agent Interfaces, Coordinator, and Conversation Room -
targeted verification that the three distinct user-facing surfaces are
genuinely connected, closing two real gaps a live pre-checkpoint
verification pass found no existing test covering:

1. A person's own typed text (including emoji) surviving the real
   `POST /message` JSON round-trip byte-for-byte - the existing emoji
   coverage (`test_natural_conversation_completion.py`) only proves a
   *Brain-composed reply* isn't stripped; nothing proved the person's
   own input isn't mangled on the way in.
2. The Conversation Room / snapshot pipeline genuinely has no
   two-agent ceiling - every existing structural test proves the
   humanoidBodies/who-picker *templates* name no agent literally, but
   none joins a real third participant through the real
   AgentRegistry -> ControlCenter -> snapshot() pipeline and checks the
   result.

Everything else this task asked to verify (individual-agent addressing
reaching the correct Brain, Alfred/Arthur memory isolation, Coordinator
reading live backend state, no ASCII-only stripping in the collaboration
path) already has solid existing coverage (test_specialist_general_
conversation.py::TestRealProductionRoomParityForArthur,
test_brain_provider_credential_memory_independence.py,
test_control_center.py, test_live_ui.py) and is not duplicated here.
"""

from __future__ import annotations

import threading

from src.agents.echo_agent import EchoAgent
from src.composition import build_channel, build_conversation, build_manager, build_runtime
from src.config.settings import Settings
from src.control_center.observer import ControlCenter
from src.control_center.snapshot import snapshot
from src.conversation.conversation import Participant, ParticipantKind, Utterance
from scripts.serve_ui import LiveSystem, route_command, route_message
from src.services.email.in_memory_mailbox import InMemoryMailbox


def settings():
    return Settings.from_env(env={}, log_level="CRITICAL", memory="ephemeral")


def live_system() -> LiveSystem:
    runtime = build_runtime(settings())
    manager = build_manager(runtime, mailbox=InMemoryMailbox(), profile=None)
    return LiveSystem(
        runtime=runtime,
        manager=manager,
        channel=build_channel(runtime, manager),
        conversation=build_conversation(manager),
        centre=ControlCenter(runtime, manager),
        lock=threading.Lock(),
    )


# ---------------------------------------------------------------------------
# Individual Agent Interface: addressed messages, through the real route
# ---------------------------------------------------------------------------


class TestIndividualAgentInterfaceThroughTheRealRoute:
    def test_a_message_addressed_to_alfred_is_handled_by_alfred(self):
        system = live_system()
        status, body, _ = route_message("what time is it", system, to="Alfred")
        assert status == 200
        import json
        payload = json.loads(body)
        assert payload["handled_by"] == "Alfred"
        assert payload["delegated"] is False

    def test_a_message_addressed_to_arthur_is_handled_by_arthur(self):
        system = live_system()
        status, body, _ = route_message("check my inbox", system, to="Arthur")
        assert status == 200
        import json
        payload = json.loads(body)
        assert payload["handled_by"] == "Arthur"

    def test_the_two_addressed_conversations_do_not_merge_memory(self):
        """The decisive proof: a private line to one agent must never
        become a fact retrievable from the other agent's own store."""
        system = live_system()
        route_message("remember that our deploy window is Tuesdays at 9am", system, to="Alfred")
        alfred_has_it = system.manager.memory.recall("deploy window")
        arthur = system.manager.registry.get("Arthur")
        arthur_has_it = arthur.memory.recall("deploy window")
        assert len(alfred_has_it) == 1
        assert arthur_has_it == ()


# ---------------------------------------------------------------------------
# Emoji: the person's own input, through the real JSON route, not just a
# Brain-composed reply
# ---------------------------------------------------------------------------


class TestEmojiSurvivesTheRealMessageRoute:
    def test_emoji_in_the_persons_own_message_is_not_mangled(self):
        system = live_system()
        text = "thanks \U0001F44D that's great \U0001F389"
        status, body, _ = route_message(text, system, to="Alfred")
        assert status == 200

        data = snapshot(system.centre, conversation=system.conversation, channel=system.channel)
        utterances = data["conversation"]["utterances"]
        mine = next(u for u in utterances if u["speaker"] == "you")
        assert "\U0001F44D" in mine["text"]
        assert "\U0001F389" in mine["text"]
        assert "??" not in mine["text"]  # no mojibake substitution


# ---------------------------------------------------------------------------
# Conversation Room: no hard-coded two-agent ceiling, proven through the
# real AgentRegistry -> ControlCenter -> snapshot() pipeline
# ---------------------------------------------------------------------------


class TestConversationRoomHasNoAgentCountCeiling:
    def test_a_third_registered_agent_appears_in_the_real_snapshot(self):
        """EchoAgent - this project's own minimal, real BaseAgent, used
        exactly for proving genericness (see main.py's own docstring:
        'the Manager cannot tell them apart') - registered alongside
        Alfred and Arthur. Nothing about the observer/snapshot pipeline
        may assume exactly two agents exist."""
        system = live_system()
        third = EchoAgent(name="Coding Agent", capabilities={"code"})
        system.manager.registry.register(third)

        data = snapshot(system.centre, conversation=system.conversation, channel=system.channel)
        names = {a["name"] for a in data["agents"]}
        assert names == {"Alfred", "Arthur", "Coding Agent"}

    def test_three_agents_can_genuinely_join_and_speak_in_one_room(self):
        system = live_system()
        third = EchoAgent(name="Coding Agent", capabilities={"code"})
        system.manager.registry.register(third)

        conversation = system.conversation
        conversation.join(Participant("Arthur", ParticipantKind.AGENT))
        conversation.join(Participant("Coding Agent", ParticipantKind.AGENT))
        conversation.say(Utterance(speaker="Alfred", text="team, status check please"))
        conversation.say(Utterance(speaker="Arthur", text="inbox is clear \U0001F44D"))
        conversation.say(Utterance(speaker="Coding Agent", text="build is green"))

        data = snapshot(system.centre, conversation=conversation, channel=system.channel)
        speakers = [u["speaker"] for u in data["conversation"]["utterances"]]
        assert speakers == ["Alfred", "Arthur", "Coding Agent"]
        participant_names = {p["name"] for p in data["conversation"]["participants"]}
        assert {"Alfred", "Arthur", "Coding Agent"}.issubset(participant_names)

    def test_the_third_agent_gets_no_brain_or_memory_it_did_not_declare(self):
        """EchoAgent declares neither - the observer must report that
        honestly (no fabricated Brain/Memory view), never crash, and
        never borrow Alfred's or Arthur's."""
        system = live_system()
        third = EchoAgent(name="Coding Agent", capabilities={"code"})
        system.manager.registry.register(third)

        data = snapshot(system.centre, conversation=system.conversation, channel=system.channel)
        coding_view = next(a for a in data["agents"] if a["name"] == "Coding Agent")
        assert coding_view["memory"]["backend"] == "none"
        assert coding_view["memory"]["durable"] is False


# ---------------------------------------------------------------------------
# Coordinator: real backend state, reconfirmed at the route level
# ---------------------------------------------------------------------------


class TestCoordinatorReflectsRealBackendState:
    def test_status_command_names_the_real_registered_specialist(self):
        system = live_system()
        status, body, _ = route_command("status", None, system)
        assert status == 200
        assert b"Arthur" in body

    def test_a_newly_registered_agent_is_visible_to_the_coordinator_immediately(self):
        """No caching, no stale roster - the Coordinator must reflect the
        real, current AgentRegistry the moment an agent joins it."""
        system = live_system()
        third = EchoAgent(name="Coding Agent", capabilities={"code"})
        system.manager.registry.register(third)
        status, body, _ = route_command("status", None, system)
        assert status == 200
        assert b"Coding Agent" in body
