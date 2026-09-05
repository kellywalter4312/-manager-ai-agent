"""Stage 15 - Communication / Phone Intelligence.

Gives Alfred a governed architectural path to read recent messages/calls,
send a message, or place a call, when a real, authorized connection is
configured - motivated by the brief's own examples ("Call Mom.", "Text
Arthur.", "Read my recent messages.", "Who called me?") that this file
deliberately never resolves to a real contact: this project has no
contacts concept (see src/services/profile/profile.py), so a name is
always passed through exactly as said.

The mechanism, end to end:

    User request (text or voice - the same Task either way)
      -> request-shape decision (communication_intent, a claims=
         predicate, src/manager/capabilities.py) - never word-overlap
         scoring, because "call"/"text"/"message" are ordinary words too
      -> the governed tool boundary, one of three separately-grantable
         tools (CommunicationReadTool/SendTool/CallTool, a Tool like any
         other a specialist could be granted)
      -> a provider-independent attempt (CommunicationProvider ->
         CommunicationResult/CommunicationEvent, src/providers/communication.py)
      -> an honest answer, or an honest refusal at any stage that could
         not be completed - never a fabricated success

No real phone/messaging backend, no contact resolution, no reply-target
resolution - this stage establishes the seam, not the ecosystem, exactly
as Stage 14 did for live information.
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

import pytest

import scripts.control_center as control_center
from src.agents.tools.communication import (
    CALL_TOOL_NAME,
    READ_TOOL_NAME,
    SEND_TOOL_NAME,
    CommunicationCallTool,
    CommunicationReadTool,
    CommunicationSendTool,
)
from src.agents.tools.toolset import ToolSet
from src.core.contracts import Task
from src.manager.llm_brain import LLMManagerBrain
from src.providers.communication import (
    CommunicationEvent,
    CommunicationProvider,
    CommunicationResult,
    CommunicationStatus,
    utc_now,
)
from src.providers.communication_factory import build_communication_provider
from src.providers.null_communication_provider import NullCommunicationProvider
from src.services.identity.voice import voice_for

REPO_ROOT = Path(__file__).resolve().parent.parent

COMMUNICATION_FILES = (
    REPO_ROOT / "src" / "providers" / "communication.py",
    REPO_ROOT / "src" / "providers" / "null_communication_provider.py",
    REPO_ROOT / "src" / "providers" / "communication_factory.py",
    REPO_ROOT / "src" / "agents" / "tools" / "communication.py",
)


# ---------------------------------------------------------------------------
# Test doubles - real, working, never a network call
# ---------------------------------------------------------------------------


class ScriptedCommunicationProvider(CommunicationProvider):
    """A real, working CommunicationProvider double - never a real phone
    or messaging connection."""

    def __init__(
        self,
        *,
        events: tuple[CommunicationEvent, ...] = (),
        fail_send: bool = False,
        fail_call: bool = False,
    ):
        self._events = events
        self._fail_send = fail_send
        self._fail_call = fail_call
        self.sent: list[tuple[str, str]] = []
        self.called: list[str] = []

    @property
    def name(self) -> str:
        return "scripted"

    def read_messages(self, *, limit: int = 10) -> tuple[CommunicationEvent, ...]:
        return self._events[:limit]

    def send_message(self, *, to: str, content: str) -> CommunicationResult:
        if self._fail_send:
            return CommunicationResult(
                operation="send", party=to, status=CommunicationStatus.FAILED,
                detail="carrier rejected the message", source=self.name, attempted_at=utc_now(),
            )
        self.sent.append((to, content))
        return CommunicationResult(
            operation="send", party=to, status=CommunicationStatus.SUCCEEDED,
            source=self.name, attempted_at=utc_now(),
        )

    def place_call(self, *, to: str) -> CommunicationResult:
        if self._fail_call:
            return CommunicationResult(
                operation="call", party=to, status=CommunicationStatus.FAILED,
                detail="line busy", source=self.name, attempted_at=utc_now(),
            )
        self.called.append(to)
        return CommunicationResult(
            operation="call", party=to, status=CommunicationStatus.SUCCEEDED,
            source=self.name, attempted_at=utc_now(),
        )


class ScriptedLLMProvider:
    """A real, local LLMProvider double - never a network call."""

    def __init__(self, text: str = "composed reply"):
        self._text = text

    @property
    def name(self) -> str:
        return "scripted-llm"

    def complete(self, prompt: str, *, system: str | None = None):
        from src.providers.base import Completion

        return Completion(text=self._text, provider=self.name, model="scripted-1")


def with_scripted_brain(manager, text: str = "synthesised answer"):
    brain = LLMManagerBrain(ScriptedLLMProvider(text))
    manager.brain = brain
    manager.own_skills._converse = brain.converse
    return brain


def real_manager(*, tools: ToolSet | None = None, **overrides):
    from src.composition import build_manager, build_runtime
    from src.config.settings import Settings
    from src.services.email.in_memory_mailbox import InMemoryMailbox

    settings = Settings.from_env(env={}, log_level="CRITICAL", memory="ephemeral", **overrides)
    runtime = build_runtime(settings)
    return build_manager(runtime, mailbox=InMemoryMailbox(), profile=None, tools=tools)


def full_tools(provider: CommunicationProvider) -> ToolSet:
    return ToolSet([
        CommunicationReadTool(provider),
        CommunicationSendTool(provider),
        CommunicationCallTool(provider),
    ])


def manager_with_communication(provider: CommunicationProvider | None = None, **overrides):
    return real_manager(tools=full_tools(provider or ScriptedCommunicationProvider()), **overrides)


CALL_TASK = Task(description="Call Mom.")
TEXT_TASK = Task(description="Text Arthur.")
# "Who called me?" never collided with Arthur's own "messages" capability
# word even before the routing fix (see TestTheMessagesRoutingCollisionIsFixed
# below for the "Read my recent messages." case specifically) - kept as the
# shared constant so the rest of this file exercises the read operation with
# a phrasing that was never in question.
READ_TASK = Task(description="Who called me?")


# ---------------------------------------------------------------------------
# 1. The abstraction exists and is provider-independent
# ---------------------------------------------------------------------------


class TestTheAbstractionIsProviderIndependent:
    def test_communicationresult_is_frozen(self):
        result = CommunicationResult(operation="call", party="Mom", status=CommunicationStatus.SUCCEEDED)
        with pytest.raises(Exception):
            result.party = "changed"

    def test_two_unrelated_providers_satisfy_the_same_interface(self):
        assert isinstance(NullCommunicationProvider(), CommunicationProvider)
        assert isinstance(ScriptedCommunicationProvider(), CommunicationProvider)

    def test_the_provider_module_is_coupled_to_nothing_it_should_not_be(self):
        source = (REPO_ROOT / "src" / "providers" / "communication.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                module = getattr(node, "module", "") or ""
                assert not module.startswith("src.manager")
                assert not module.startswith("src.agents")
                assert not module.startswith("src.conversation")
                assert not module.startswith("src.services.memory")
                assert not module.startswith("src.services.identity")
                assert not module.startswith("src.channels")

    def test_only_succeeded_status_is_ok(self):
        assert CommunicationStatus.SUCCEEDED.ok
        assert not CommunicationStatus.UNAVAILABLE.ok
        assert not CommunicationStatus.FAILED.ok

    def test_empty_event_and_result_fields_are_refused(self):
        with pytest.raises(ValueError):
            CommunicationEvent(kind="", party="Mom", summary="hi", occurred_at=utc_now(), source="x")
        with pytest.raises(ValueError):
            CommunicationResult(operation="", party="Mom", status=CommunicationStatus.FAILED)

    def test_the_factory_builds_the_default_null_provider(self):
        from src.config.settings import Settings

        settings = Settings.from_env(env={})
        assert isinstance(build_communication_provider(settings), NullCommunicationProvider)


# ---------------------------------------------------------------------------
# 2. Communication requests are recognized correctly
# ---------------------------------------------------------------------------


class TestCommunicationRequestsAreRecognizedCorrectly:
    @pytest.mark.parametrize(
        "text",
        [
            "Call Mom.",
            "Send Mom a message.",
            "Text Arthur.",
            "Read my recent messages.",
            "Who called me?",
            "Call this number.",
            "Send this message to X.",
            "Hey Alfred, call Mom.",
        ],
    )
    def test_brief_examples_reach_the_communication_handler(self, text):
        manager = manager_with_communication()
        result = manager.handle_task(Task(description=text))
        assert result.payload is not None
        assert result.payload.get("handled_by") == "communication"

    def test_remind_me_to_call_is_out_of_scope_and_not_claimed(self):
        """A reminder/scheduling request, not a communication execution
        request - genuinely unsupported, and honestly left unclaimed
        rather than misrouted into a tool that cannot fulfil it."""
        manager = manager_with_communication()
        with_scripted_brain(manager)
        result = manager.handle_task(Task(description="Remind me to call X."))
        assert result.payload is None or result.payload.get("handled_by") != "communication"


class TestTheMessagesRoutingCollisionIsFixed:
    """Formerly documented here as a known, accepted limitation - a
    surgical closure pass fixed it. Kept as its own class, renamed, so
    the history of "this used to be a limitation" stays visible rather
    than silently vanishing into the ordinary recognition tests above.

    The fix: ``ManagerSkills.claims()`` lets ``ManagerAgent.decide()``
    and ``collaboration_entry.collaboration_candidates()`` both check
    whether Alfred's own capability system already recognises a
    request's *shape* before ever consulting a specialist's raw,
    incidental word overlap. "messages" is still, correctly, Arthur's
    own declared capability word (untouched) - it simply no longer wins
    outright merely because a communication request happens to share it.
    """

    def test_bare_messages_phrasing_now_reaches_communication_not_arthur(self):
        manager = manager_with_communication()
        result = manager.handle_task(Task(description="Read my recent messages."))
        assert result.agent_name == "Alfred"
        assert result.payload.get("handled_by") == "communication"

    def test_legitimate_email_requests_still_reach_arthur(self):
        """The fix must not delete or dilute Arthur's own, legitimate
        capability - only phrasings a communication request's own claims
        recognise are affected."""
        manager = manager_with_communication()
        for text in ("Read my recent emails.", "Check my inbox."):
            result = manager.handle_task(Task(description=text))
            assert result.agent_name == "Arthur", text

    def test_the_fix_holds_on_the_real_production_collaboration_path(self):
        """The bug this closes was worse than a bare handle_task()
        misroute: on the real channel.ask()/answer_with_collaboration()
        path, Arthur was actually invited to collaborate and execute,
        contributing an email-domain answer that could be fed to
        Alfred's own reasoning as evidence for a phone/SMS request. This
        proves the fix holds on that exact path, not merely on a direct
        manager.handle_task() call."""
        from src.channels.channel import TextChannel

        manager = manager_with_communication()
        channel = TextChannel(manager)

        response = channel.ask("Read my recent messages.")

        assert response.turn.handled_by == "Alfred"
        assert response.turn.succeeded
        # Arthur's own inbox-flavoured wording ("Inbox is empty.") must
        # never appear in the final answer - proof no email-domain
        # contribution leaked into this response at all.
        assert "inbox" not in response.turn.outcome.lower()

    def test_arthur_is_not_even_invited_to_collaborate(self):
        """Structural: collaboration_candidates() must return no
        candidates at all for a claimed communication request - Arthur
        never executes, never contributes, regardless of what Alfred's
        Brain does afterward."""
        from src.manager.collaboration_entry import collaboration_candidates

        manager = manager_with_communication()
        candidates = collaboration_candidates(manager, Task(description="Read my recent messages."))
        assert candidates == ()

    def test_legitimate_email_requests_are_still_collaboration_candidates(self):
        from src.manager.collaboration_entry import collaboration_candidates

        manager = manager_with_communication()
        candidates = collaboration_candidates(manager, Task(description="Check my inbox."))
        assert "Arthur" in candidates


# ---------------------------------------------------------------------------
# 3. Ordinary conversation is unaffected
# ---------------------------------------------------------------------------


class TestOrdinaryConversationIsUnaffected:
    @pytest.mark.parametrize(
        "text",
        [
            "thanks so much",
            "hello there",
            "status",
            "research the job market for me",
            "what do you remember about my degree",
            "milestone: I graduated",
            "how old am i",
            "check my email",
            "what is the latest news today",
        ],
    )
    def test_unrelated_requests_are_not_claimed_by_communication(self, text):
        manager = manager_with_communication()
        with_scripted_brain(manager)
        result = manager.handle_task(Task(description=text))
        assert result.payload is None or result.payload.get("handled_by") != "communication"

    def test_arthur_email_behaviour_is_unaffected(self):
        manager = manager_with_communication()
        result = manager.handle_task(Task(description="check my email"))
        assert result.agent_name == "Arthur"


# ---------------------------------------------------------------------------
# 4. Capability permission is enforced
# ---------------------------------------------------------------------------


class TestCapabilityPermissionIsEnforced:
    def test_no_tools_granted_refuses_every_operation_honestly(self):
        manager = real_manager()  # nothing granted
        for task in (CALL_TASK, TEXT_TASK, READ_TASK):
            result = manager.handle_task(task)
            assert not result.succeeded
            assert "not installed" in result.error

    def test_read_only_grant_permits_read_but_refuses_send_and_call(self):
        provider = ScriptedCommunicationProvider()
        manager = real_manager(tools=ToolSet([CommunicationReadTool(provider)]))

        read_result = manager.handle_task(READ_TASK)
        call_result = manager.handle_task(CALL_TASK)
        text_result = manager.handle_task(TEXT_TASK)

        assert read_result.succeeded
        assert not call_result.succeeded and "not installed" in call_result.error
        assert not text_result.succeeded and "not installed" in text_result.error

    def test_full_grant_permits_every_operation(self):
        # Phase 4 (Real-World Communication): send/call are consequential
        # actions now, and describe themselves back rather than executing
        # on the strength of one remark alone (see PendingConfirmation in
        # src/manager/activity_session.py) - so "permitted" here means
        # reaching that description honestly, not an immediate real
        # side effect. An explicit "yes" on the very next remark is what
        # actually executes it; see TestConsequentialActionConfirmation
        # in tests/test_phase_4_real_world_communication.py for that path.
        provider = ScriptedCommunicationProvider()
        manager = manager_with_communication(provider)

        assert manager.handle_task(READ_TASK).succeeded

        call_result = manager.handle_task(CALL_TASK)
        assert call_result.succeeded and "(yes/no)" in call_result.output
        assert provider.called == []
        # A pending confirmation holds the floor (Phase 4, mirroring how
        # an unresolved clarification already does) - confirm it before
        # the next, unrelated request can be reached at all.
        assert manager.handle_task(Task(description="yes")).succeeded
        assert provider.called == ["Mom"]

        text_result = manager.handle_task(Task(description="Text Arthur: on my way"))
        assert text_result.succeeded and "(yes/no)" in text_result.output
        assert provider.sent == []
        assert manager.handle_task(Task(description="yes")).succeeded
        assert provider.sent == [("Arthur", "on my way")]


# ---------------------------------------------------------------------------
# 5. Unavailable provider/connection fails honestly
# ---------------------------------------------------------------------------


class TestUnavailableProviderFailsHonestly:
    def test_null_provider_read_is_honestly_empty(self):
        manager = manager_with_communication(NullCommunicationProvider())
        result = manager.handle_task(READ_TASK)
        assert result.succeeded
        assert "No recent messages or calls." in result.output

    def test_null_provider_send_fails_honestly(self):
        # Phase 4: the honest failure is what happens *after* confirmation
        # - describing an action back is not itself a claim it will work.
        manager = manager_with_communication(NullCommunicationProvider())
        ask = manager.handle_task(Task(description="Text Arthur: on my way"))
        assert ask.succeeded and "(yes/no)" in ask.output
        result = manager.handle_task(Task(description="yes"))
        assert not result.succeeded
        assert "no authorized communication connection is configured" in result.error

    def test_null_provider_call_fails_honestly(self):
        manager = manager_with_communication(NullCommunicationProvider())
        ask = manager.handle_task(CALL_TASK)
        assert ask.succeeded and "(yes/no)" in ask.output
        result = manager.handle_task(Task(description="yes"))
        assert not result.succeeded
        assert "no authorized communication connection is configured" in result.error

    def test_a_provider_that_genuinely_fails_is_reported_as_a_failure(self):
        provider = ScriptedCommunicationProvider(fail_send=True, fail_call=True)
        manager = manager_with_communication(provider)

        manager.handle_task(CALL_TASK)
        call_result = manager.handle_task(Task(description="yes"))
        assert not call_result.succeeded and "line busy" in call_result.error

        manager.handle_task(Task(description="Text Arthur: hi"))
        text_result = manager.handle_task(Task(description="yes"))
        assert not text_result.succeeded and "carrier rejected" in text_result.error


# ---------------------------------------------------------------------------
# 6. No fake successful call/message is ever produced
# ---------------------------------------------------------------------------


class TestNoFakeSuccessIsProduced:
    def test_the_tool_raises_on_a_non_ok_result_never_returns_a_success_string(self):
        tool = CommunicationSendTool(NullCommunicationProvider())
        with pytest.raises(RuntimeError):
            tool.run(to="Mom", content="hi")

    def test_toolset_turns_the_raise_into_an_honest_failed_outcome(self):
        tools = ToolSet([CommunicationCallTool(NullCommunicationProvider())])
        outcome = tools.invoke(CALL_TOOL_NAME, {"to": "Mom"})
        assert not outcome.ok

    def test_no_execution_primitives_in_the_new_files(self):
        for path in COMMUNICATION_FILES:
            source = path.read_text(encoding="utf-8")
            for forbidden in ("eval(", "exec(", "subprocess", "os.system", "__import__", "open("):
                assert forbidden not in source, (path.name, forbidden)

    def test_a_send_with_no_content_asks_rather_than_sending_a_blank_message(self):
        provider = ScriptedCommunicationProvider()
        manager = manager_with_communication(provider)
        result = manager.handle_task(TEXT_TASK)  # "Text Arthur." - no content
        assert not provider.sent
        assert "what would you like to send" in result.output.lower()


# ---------------------------------------------------------------------------
# 7. Brain remains separate from communication execution
# ---------------------------------------------------------------------------


class TestBrainRemainsSeparateFromCommunicationExecution:
    def test_the_provider_files_import_no_brain_or_manager_module(self):
        for path in COMMUNICATION_FILES:
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, (ast.Import, ast.ImportFrom)):
                    module = getattr(node, "module", "") or ""
                    assert "brain" not in module.lower()
                    assert not module.startswith("src.manager")

    def test_the_capability_handler_never_calls_converse(self):
        import src.manager.capabilities as capabilities_module

        for fn in (
            capabilities_module.communication,
            capabilities_module._communication_read,
            capabilities_module._communication_call,
            capabilities_module._communication_send,
        ):
            assert "request.converse" not in inspect.getsource(fn)

    def test_a_communication_request_succeeds_with_no_brain_configured(self):
        """Half Brain honesty cuts the other way here: communication is
        deterministic tool dispatch, not reasoning, so it must work
        identically whether or not a real language model is configured -
        the opposite of live_information's own Brain-gated behaviour, and
        deliberately so."""
        manager = manager_with_communication()  # default NullBrain, never wired
        assert manager.handle_task(CALL_TASK).succeeded

    def test_brain_object_identity_is_never_touched_by_a_communication_request(self):
        manager = manager_with_communication()
        with_scripted_brain(manager)
        brain_before = manager.brain
        manager.handle_task(CALL_TASK)
        assert manager.brain is brain_before


# ---------------------------------------------------------------------------
# 8. Memory is not duplicated
# ---------------------------------------------------------------------------


class TestMemoryIsNotDuplicated:
    def test_the_handler_never_touches_a_memory_object(self):
        import src.manager.capabilities as capabilities_module

        for fn in (
            capabilities_module.communication,
            capabilities_module._communication_read,
            capabilities_module._communication_call,
            capabilities_module._communication_send,
        ):
            source = inspect.getsource(fn)
            assert ".memory" not in source
            assert "request.retain(" not in source
            assert "request.recall(" not in source

    def test_a_successful_communication_request_writes_nothing_to_memory(self):
        manager = manager_with_communication()
        before = len(manager.memory.recall(limit=200))

        manager.handle_task(CALL_TASK)
        manager.handle_task(READ_TASK)

        assert len(manager.memory.recall(limit=200)) == before

    def test_explicit_remember_still_works_after_a_communication_request(self):
        from src.channels.channel import TextChannel
        from src.conversation.conversation import Conversation, Participant, ParticipantKind
        from scripts.exchange import exchange

        manager = manager_with_communication()
        with_scripted_brain(manager)
        channel = TextChannel(manager)
        conversation = Conversation(participants=(Participant("you", ParticipantKind.PERSON),))

        exchange(channel, conversation, CALL_TASK.description)

        before = len(manager.memory.recall(limit=50))
        exchange(channel, conversation, "remember that we prefer green")
        after = manager.memory.recall(limit=50)

        assert len(after) == before + 1


# ---------------------------------------------------------------------------
# 9. Voice and text requests share the same execution path
# ---------------------------------------------------------------------------


class TestVoiceAndTextShareTheSameExecutionPath:
    def test_the_same_request_produces_the_same_outcome_through_both_channels(self):
        from src.channels.channel import TextChannel, VoiceChannel
        from src.channels.speech import SpeechAdapter
        from src.providers.availability import Availability, AvailabilityState

        class ScriptedSpeech(SpeechAdapter):
            def __init__(self):
                self._availability = Availability(state=AvailabilityState.AVAILABLE)
                self.spoken = []

            @property
            def name(self):
                return "scripted"

            @property
            def availability(self):
                return self._availability

            def listen(self, timeout=None):
                raise RuntimeError("not needed")

            def speak(self, text, voice=None):
                self.spoken.append((text, voice))
                return True

        provider = ScriptedCommunicationProvider()
        manager = manager_with_communication(provider)

        text_channel = TextChannel(manager)
        text_response = text_channel.ask(CALL_TASK.description)
        text_confirm = text_channel.ask("yes")

        voice_channel = VoiceChannel(manager, speech=ScriptedSpeech())
        voice_response = voice_channel.ask(CALL_TASK.description)
        voice_confirm = voice_channel.ask("yes")

        assert text_response.turn.succeeded and voice_response.turn.succeeded
        assert text_confirm.turn.succeeded and voice_confirm.turn.succeeded
        assert text_response.turn.handled_by == voice_response.turn.handled_by == "Alfred"
        # Both calls actually reached the identical governed tool/provider -
        # not two different code paths that happen to agree. Phase 4: each
        # call is only real once confirmed, on either channel alike.
        assert provider.called == ["Mom", "Mom"]

    def test_no_separate_voice_only_communication_module_exists(self):
        """Structural: nothing under src/channels imports the
        communication provider or tool directly - voice is the
        interface, never a second execution path."""
        channels_dir = REPO_ROOT / "src" / "channels"
        for path in channels_dir.glob("*.py"):
            source = path.read_text(encoding="utf-8")
            assert "communication_factory" not in source
            assert "CommunicationProvider" not in source
            assert "CommunicationReadTool" not in source
            assert "CommunicationSendTool" not in source
            assert "CommunicationCallTool" not in source


# ---------------------------------------------------------------------------
# 10. Changing the communication provider does not change Brain/Memory
# ---------------------------------------------------------------------------


class TestChangingCommunicationProviderDoesNotChangeBrainOrMemory:
    def test_switching_providers_leaves_brain_and_memory_untouched(self):
        for provider in (NullCommunicationProvider(), ScriptedCommunicationProvider()):
            manager = manager_with_communication(provider)
            brain_before, memory_before = manager.brain, manager.memory
            manager.handle_task(CALL_TASK)
            assert manager.brain is brain_before
            assert manager.memory is memory_before

    def test_switching_providers_leaves_voice_untouched(self):
        for provider in (NullCommunicationProvider(), ScriptedCommunicationProvider()):
            manager = manager_with_communication(provider)
            voice = voice_for(manager.own_skills)
            assert voice is not None and voice.key == "alfred"


# ---------------------------------------------------------------------------
# 11. Changing the LLM provider does not break communication architecture
# ---------------------------------------------------------------------------


class TestChangingTheLLMProviderDoesNotBreakCommunication:
    def test_communication_works_identically_with_no_brain_and_with_a_real_brain(self):
        provider = ScriptedCommunicationProvider()

        bare = manager_with_communication(provider)
        bare_result = bare.handle_task(CALL_TASK)

        reasoning = manager_with_communication(ScriptedCommunicationProvider())
        with_scripted_brain(reasoning)
        reasoning_result = reasoning.handle_task(CALL_TASK)

        assert bare_result.succeeded
        assert reasoning_result.succeeded
        assert bare_result.payload["handled_by"] == reasoning_result.payload["handled_by"] == "communication"


# ---------------------------------------------------------------------------
# 12. Changing VoiceProfile does not break communication architecture
# ---------------------------------------------------------------------------


class TestChangingVoiceProfileDoesNotBreakCommunication:
    def test_alfred_and_arthur_keep_distinct_voices_after_a_communication_request(self):
        manager = manager_with_communication()
        manager.handle_task(CALL_TASK)

        alfred_voice = voice_for(manager.own_skills)
        arthur_voice = voice_for(manager.registry.get("Arthur"))

        assert alfred_voice.key == "alfred"
        assert arthur_voice.key == "arthur"
        assert alfred_voice != arthur_voice


# ---------------------------------------------------------------------------
# 13. Specialists do not receive unintended communication permissions
# ---------------------------------------------------------------------------


class TestSpecialistsDoNotReceiveUnintendedCommunicationPermissions:
    def test_arthur_has_no_communication_tools_on_the_production_path(self):
        from src.config.settings import Settings

        settings = Settings.from_env(env={}, log_level="CRITICAL", memory="ephemeral")
        _runtime, manager, *_ = control_center.build(settings)

        arthur = manager.registry.get("Arthur")
        assert arthur is not None
        for tool_name in (READ_TOOL_NAME, SEND_TOOL_NAME, CALL_TOOL_NAME):
            assert not arthur.tools.permits(tool_name)

    def test_alfred_gets_read_send_and_call_by_default_on_the_production_path(self):
        """Phase 4 (Real-World Communication): Stage 15 granted only
        CommunicationReadTool here - deliberately, while send/call had no
        confirmation gate at all. That gate now exists
        (PendingConfirmation, src/manager/guided_interaction.py), so
        send/call are granted too: every attempt still describes itself
        and waits for an explicit yes/no before any real external effect
        happens - see tests/test_phase_4_real_world_communication.py for
        that governance proved end-to-end."""
        from src.config.settings import Settings

        settings = Settings.from_env(env={}, log_level="CRITICAL", memory="ephemeral")
        _runtime, manager, *_ = control_center.build(settings)

        assert manager.own_skills.tools.permits(READ_TOOL_NAME)
        assert manager.own_skills.tools.permits(SEND_TOOL_NAME)
        assert manager.own_skills.tools.permits(CALL_TOOL_NAME)

    def test_a_bare_build_manager_call_grants_nothing_by_default(self):
        from src.composition import build_manager, build_runtime
        from src.config.settings import Settings
        from src.services.email.in_memory_mailbox import InMemoryMailbox

        settings = Settings.from_env(env={}, log_level="CRITICAL", memory="ephemeral")
        runtime = build_runtime(settings)
        manager = build_manager(runtime, mailbox=InMemoryMailbox(), profile=None)
        assert manager.own_skills.tools.is_empty


# ---------------------------------------------------------------------------
# 14. A future provider needs no orchestration change
# ---------------------------------------------------------------------------


class TestFutureProviderNeedsNoOrchestrationChange:
    def test_a_never_before_seen_provider_works_through_the_identical_path(self):
        class FutureProvider(CommunicationProvider):
            @property
            def name(self):
                return "future-carrier"

            def read_messages(self, *, limit: int = 10):
                return (CommunicationEvent(
                    kind="message", party="Mom", summary="see you soon",
                    occurred_at=utc_now(), source=self.name,
                ),)

            def send_message(self, *, to: str, content: str) -> CommunicationResult:
                return CommunicationResult(
                    operation="send", party=to, status=CommunicationStatus.SUCCEEDED,
                    source=self.name, attempted_at=utc_now(),
                )

            def place_call(self, *, to: str) -> CommunicationResult:
                return CommunicationResult(
                    operation="call", party=to, status=CommunicationStatus.SUCCEEDED,
                    source=self.name, attempted_at=utc_now(),
                )

        manager = manager_with_communication(FutureProvider())

        ask = manager.handle_task(CALL_TASK)
        assert ask.succeeded and "(yes/no)" in ask.output
        # Phase 4: a pending confirmation holds the floor - exactly as an
        # unresolved clarification already does - so it must be resolved
        # before an unrelated capability (read) is reachable again.
        assert manager.handle_task(Task(description="yes")).succeeded
        assert manager.handle_task(READ_TASK).succeeded

    def test_the_factory_is_the_only_registration_point_a_new_provider_needs(self):
        """Structural: composition.py/capabilities.py never name a specific
        provider class - only communication_factory does, the identical
        shape live_info_factory already established. control_center.py is
        exempt from this check: Phase 4 added an honest comment there
        naming NullCommunicationProvider in prose to explain why Send/Call
        are now safely granted - naming it in a comment is not the same as
        depending on it, and every other real capability in that file
        (live_info, media, knowledge) is already explained the same way."""
        for path in (
            REPO_ROOT / "src" / "composition.py",
            REPO_ROOT / "src" / "manager" / "capabilities.py",
        ):
            source = path.read_text(encoding="utf-8")
            assert "NullCommunicationProvider" not in source

        control_center_source = (REPO_ROOT / "scripts" / "control_center.py").read_text(encoding="utf-8")
        assert "runtime.communication" in control_center_source
        assert "class NullCommunicationProvider" not in control_center_source
        assert "NullCommunicationProvider(" not in control_center_source


# ---------------------------------------------------------------------------
# 15. Existing Stages 11-14 remain intact
# ---------------------------------------------------------------------------


class TestExistingStagesRemainIntact:
    def test_alfred_status_still_works(self):
        manager = manager_with_communication()
        result = manager.handle_task(Task(description="status"))
        assert result.succeeded and result.agent_name == "Alfred"

    def test_small_talk_still_works(self):
        manager = manager_with_communication()
        result = manager.handle_task(Task(description="thanks"))
        assert result.output == "You're welcome!"

    def test_arthur_email_behaviour_is_unaffected(self):
        manager = manager_with_communication()
        result = manager.handle_task(Task(description="check my email"))
        assert result.agent_name == "Arthur"

    def test_live_information_still_works_alongside_communication_tools(self):
        from src.agents.tools.live_information import LiveInformationTool
        from src.providers.live_info import LiveInfoProvider, LiveInfoResult
        from src.providers.live_info import utc_now as live_info_utc_now

        class ScriptedLiveInfoProvider(LiveInfoProvider):
            @property
            def name(self) -> str:
                return "scripted"

            def fetch(self, query: str) -> LiveInfoResult:
                return LiveInfoResult(
                    query=query, content="today's headline", source="scripted",
                    retrieved_at=live_info_utc_now(),
                )

        tools = ToolSet([
            CommunicationReadTool(NullCommunicationProvider()),
            LiveInformationTool(ScriptedLiveInfoProvider()),
        ])
        manager = real_manager(tools=tools)
        with_scripted_brain(manager)

        result = manager.handle_task(Task(description="what's the latest news today"))
        assert result.payload.get("handled_by") == "live_information"

    def test_stage13_collaboration_still_works(self):
        from scripts.exchange import answer_with_collaboration

        manager = manager_with_communication()
        with_scripted_brain(manager, "synthesised via collaboration")

        result = answer_with_collaboration(manager, Task(description="email opportunities update"))
        assert result.succeeded

    def test_the_specialist_cognitive_purity_boundary_still_holds(self):
        """The same code_of()-based ban test_specialist_cognition.py
        already enforces, re-asserted here so a Stage 15 regression of
        it is caught inside this file too."""
        import src.agents.brain as brain_module
        import src.agents.cognitive_agent as cognitive_agent_module

        def code_of(module) -> str:
            source = open(module.__file__, encoding="utf-8").read()
            lines, in_docstring = [], False
            for line in source.splitlines():
                stripped = line.strip()
                if stripped.count('"""') == 1:
                    in_docstring = not in_docstring
                    continue
                if in_docstring or stripped.startswith(("#", '"""')):
                    continue
                lines.append(line.split("#")[0])
            return "\n".join(lines).lower()

        assert "from src.providers" not in code_of(brain_module)
        assert "from src.providers" not in code_of(cognitive_agent_module)


# ---------------------------------------------------------------------------
# Mutation tests - the most important governance guarantee: consequential
# operations require an explicit, per-tool grant
# ---------------------------------------------------------------------------


class TestMutationTheToolPermissionGate:
    def test_the_permission_gate_is_load_bearing_for_send(self):
        """Pins what a mutation removing the tools.permits() check in
        _communication_send would break: an ungranted send must be
        refused, never silently attempted."""
        manager = real_manager()  # nothing granted
        result = manager.handle_task(TEXT_TASK)
        assert not result.succeeded
        assert "not installed" in result.error

    def test_the_permission_gate_is_load_bearing_for_call(self):
        manager = real_manager()
        result = manager.handle_task(CALL_TASK)
        assert not result.succeeded
        assert "not installed" in result.error

    def test_read_grant_does_not_leak_into_send_or_call(self):
        """Pins the three-tools-not-one design: granting communication_read
        must never be sufficient for send or call."""
        manager = real_manager(tools=ToolSet([CommunicationReadTool(ScriptedCommunicationProvider())]))
        assert not manager.handle_task(CALL_TASK).succeeded
        assert not manager.handle_task(TEXT_TASK).succeeded


class TestMutationTheClaimsPredicate:
    def test_the_claims_predicate_is_load_bearing_for_routing(self):
        """Pins what emptying/breaking communication_intent would break:
        "Call Mom" must actually reach the communication capability, not
        silently fall through to open_conversation or general_assistance."""
        manager = manager_with_communication()
        result = manager.handle_task(CALL_TASK)
        assert result.payload["handled_by"] == "communication"


class TestMutationTheStatusOkGate:
    def test_only_a_genuinely_succeeded_status_is_ever_reported_as_success(self):
        """Pins CommunicationStatus.ok: an UNAVAILABLE or FAILED result
        must never be reported to the user as a successful send or call."""
        for status in (CommunicationStatus.UNAVAILABLE, CommunicationStatus.FAILED):
            result = CommunicationResult(operation="call", party="Mom", status=status)
            assert not result.ok
