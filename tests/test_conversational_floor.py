"""Stage 11.3 - the conversational floor: short turns answered naturally.

Measured directly before this existed: "okay", "thanks", "good morning" and
"got it" all reached ManagerAgent.handle_task and came back no_agent_found
with an *empty* output. Not because nothing could answer them - because
nothing had ever been asked to. This file pins the fix and the one thing
that must never happen to it: a message that mixes an acknowledgement with
a real request ("thanks, check my email") must still reach the real
capability that answers it, never be swallowed here.

Reuses the exact existing extension point `test_manager_capabilities.py`
already documents - `MANAGER_CAPABILITIES` + a declared-and-handled word -
not a new mechanism.
"""

from __future__ import annotations

from dataclasses import replace

from src.core.contracts import Task, TaskResult
from src.manager.capabilities import (
    CONVERSATIONAL_FLOOR,
    MANAGER_CAPABILITIES,
    ROSTER_REPORT,
    CapabilityRequest,
    conversational_intent,
    natural_acknowledgement,
    offered_capabilities,
    select_capability,
)
from src.services.identity.identity import AgentIdentity, load_identity

IDENTITY = AgentIdentity(
    name="Alfred",
    role="Orchestrator",
    description="Coordinates missions and reports on capabilities.",
    capabilities=frozenset({"capabilities", "status", "roster", "report", "thanks", "okay", "hello"}),
)


def a_request(task: str, identity=IDENTITY, roster=()):
    return CapabilityRequest(
        task=Task(description=task),
        identity=identity,
        roster=lambda: roster,
        own_name=identity.name,
        own_capabilities=tuple(sorted(identity.capabilities)),
    )


def real_system(**overrides):
    from src.composition import build_channel, build_manager, build_runtime
    from src.config.settings import Settings
    from src.services.email.in_memory_mailbox import InMemoryMailbox

    settings = Settings.from_env(env={}, log_level="CRITICAL", memory="ephemeral", **overrides)
    runtime = build_runtime(settings)
    manager = build_manager(runtime, mailbox=InMemoryMailbox(), profile=None)
    channel = build_channel(runtime, manager)
    return manager, channel


# ---------------------------------------------------------------------------
# conversational_intent: claims short turns, never a mixed real request
# ---------------------------------------------------------------------------


class TestConversationalIntent:
    def test_claims_a_bare_thanks(self):
        assert conversational_intent(Task(description="thanks")) is True

    def test_claims_a_bare_greeting(self):
        assert conversational_intent(Task(description="good morning")) is True

    def test_claims_a_bare_acknowledgement(self):
        assert conversational_intent(Task(description="okay")) is True
        assert conversational_intent(Task(description="got it")) is True
        assert conversational_intent(Task(description="sounds good")) is True

    def test_claims_thanks_with_filler_words(self):
        assert conversational_intent(Task(description="thank you so much")) is True

    def test_does_not_claim_a_mixed_acknowledgement_and_real_request(self):
        """The one case this must never get wrong: an acknowledgement
        stapled to real work must still be answered by whatever answers
        the real work, not swallowed as a mere 'thanks'."""
        assert conversational_intent(Task(description="thanks, check my email")) is False
        assert conversational_intent(Task(description="okay, find me research about rust")) is False

    def test_does_not_claim_unrelated_requests(self):
        assert conversational_intent(Task(description="check my email for job opportunities")) is False
        assert conversational_intent(Task(description="write a function to sort a list")) is False

    def test_does_not_claim_a_task_with_no_real_tokens(self):
        assert conversational_intent(Task(description="...")) is False

    def test_does_not_claim_a_long_message_even_if_every_word_overlaps(self):
        """Capped deliberately - this is for 'thanks', not for a long
        message that happens to be built entirely from short words."""
        long_but_all_conversational = "okay great thanks yes sure cool nice perfect"
        assert conversational_intent(Task(description=long_but_all_conversational)) is False


# ---------------------------------------------------------------------------
# natural_acknowledgement: the actual reply
# ---------------------------------------------------------------------------


class TestNaturalAcknowledgement:
    def test_thanks_gets_a_thanks_reply(self):
        result = natural_acknowledgement(a_request("thanks"))
        assert result.output == "You're welcome!"
        assert result.status.value == "completed"

    def test_greeting_names_the_agent_doing_the_greeting(self):
        """Stage 17.22 supersedes the previous assertion, which only
        required the word "hello" back.

        One fixed anonymous greeting was returned by every agent in the
        system, so a person who had opened a private line to one specific
        agent and addressed it by name got a reply that could have come
        from any of them. The reply is now composed from the identity this
        request already carried - see
        src/services/identity/introduction.py - so it says who is
        speaking, and a third agent registered tomorrow greets as itself
        with nothing added there or here."""
        result = natural_acknowledgement(a_request("hello"))
        assert result.output.startswith("Alfred")
        # Still a greeting, not an introduction: short, and it hands the
        # turn straight back.
        assert len(result.output) < 120
        assert result.output.rstrip().endswith("?")

    def test_a_nameless_identity_falls_back_rather_than_greeting_a_blank(self):
        """An identity that declares no name gets the plain wording back,
        never "  here - ." """
        request = a_request("hello")
        nameless = replace(request, identity=replace(request.identity, name="x"))
        object.__setattr__(nameless.identity, "name", "")
        result = natural_acknowledgement(nameless)
        assert result.output == "Hello! What can I help you with?"

    def test_plain_acknowledgement_gets_a_short_reply(self):
        result = natural_acknowledgement(a_request("okay"))
        assert result.output == "Got it."

    def test_reply_is_short(self):
        """Item 8: appropriately sized - a short turn gets a short
        reply, never a formal report."""
        for text in ("thanks", "hello", "okay", "got it", "sounds good"):
            result = natural_acknowledgement(a_request(text))
            assert len(result.output) < 60

    def test_attributed_to_the_agent_that_answered(self):
        result = natural_acknowledgement(a_request("thanks"))
        assert result.agent_name == "Alfred"


# ---------------------------------------------------------------------------
# Registration: the existing extension point, used correctly
# ---------------------------------------------------------------------------


class TestRegistration:
    def test_conversational_floor_is_registered(self):
        assert MANAGER_CAPABILITIES["small_talk"] is CONVERSATIONAL_FLOOR

    def test_selected_for_a_conversational_turn(self):
        chosen = select_capability(Task(description="thanks"), MANAGER_CAPABILITIES)
        assert chosen is CONVERSATIONAL_FLOOR

    def test_a_genuine_scoring_tie_favors_the_more_specific_capability(self):
        """"thanks, what's your status" ties: "status" scores for
        roster_report, "thanks" scores here. CONVERSATIONAL_FLOOR is
        deliberately named to sort after every other Manager capability,
        so select_capability's tie-break (keep whichever was visited
        first, since a later equal score never beats the current best)
        favors the existing, more specific answer - never this one."""
        chosen = select_capability(
            Task(description="thanks, what's your status"), MANAGER_CAPABILITIES
        )
        assert chosen is ROSTER_REPORT

    def test_real_identity_md_declares_its_words(self):
        """Declaring a handler alone grants nothing - the words must also
        be declared in IDENTITY.md, the same rule every other capability
        here lives under."""
        identity = load_identity("IDENTITY.md")
        offered = offered_capabilities(identity.capabilities)
        assert "thanks" in offered
        assert "okay" in offered
        assert "hello" in offered


# ---------------------------------------------------------------------------
# Through the real, composed system - the fix, and the regression guard
# ---------------------------------------------------------------------------


class TestRealCompositionRouting:
    def test_short_acknowledgements_no_longer_return_silence(self):
        """The exact, measured defect this stage closes."""
        _, channel = real_system()
        for message in ("okay", "thanks", "good morning", "got it", "yes", "no", "sounds good"):
            response = channel.ask(message)
            assert response.turn.handled_by == "Alfred"
            assert response.text.strip() != ""
            assert "no registered agent" not in response.text.lower()

    def test_a_mixed_acknowledgement_still_reaches_the_real_specialist(self):
        _, channel = real_system()
        response = channel.ask("thanks, check my email for job opportunities")
        assert response.turn.handled_by == "Arthur"

    def test_status_still_gets_the_roster_report(self):
        """Regression: the pre-existing roster capability is unaffected."""
        _, channel = real_system()
        response = channel.ask("status")
        assert response.turn.handled_by == "Alfred"
        assert "Orchestrator" in response.text

    def test_check_my_email_still_routes_to_arthur(self):
        """Regression: ordinary delegation is unaffected."""
        _, channel = real_system()
        response = channel.ask("check my email")
        assert response.turn.handled_by == "Arthur"

    def test_works_in_half_brain(self):
        """Item 11: deterministic, no model required."""
        _, channel = real_system(brain="null")
        response = channel.ask("thanks")
        assert response.turn.handled_by == "Alfred"
        assert response.text == "You're welcome!"

    def test_works_through_the_same_pipeline_text_and_voice_share(self):
        """Item 12: no separate path - VoiceChannel funnels through the
        exact same Channel.ask() a text channel does."""
        from src.channels.channel import VoiceChannel

        manager, _ = real_system()
        voice_channel = VoiceChannel(manager)
        response = voice_channel.ask("thanks")
        assert response.turn.handled_by == "Alfred"
        assert response.text == "You're welcome!"

    def test_personality_and_brain_memory_boundaries_unaffected(self):
        """Items 9-10: this capability does not read or alter
        Personality, Brain, or Memory identity."""
        manager, channel = real_system()
        brain_before, memory_before = manager.brain, manager.memory
        personality_before = manager.own_skills.personality
        channel.ask("thanks")
        assert manager.brain is brain_before
        assert manager.memory is memory_before
        assert manager.own_skills.personality == personality_before
