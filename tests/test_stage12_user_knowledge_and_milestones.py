"""Stage 12 additions - persistent user knowledge, temporal derivation,
generic capture, and milestone memory.

Builds on Stage 12's original deliverable (session.py's "remember that
X", forget/recall_memory) without redesigning any of it. Four new,
additive pieces:

    user_facts.py     bare, first-person self-introduction facts
                       ("My name is Kelly...") - no "remember" needed
    temporal_facts.py  pure date parsing + age/countdown computation
    capture.py         "record this [for X]" - distinct from "remember"
    milestones.py       structured, extensible personal milestones

reached through three new ManagerCapability entries
(record_this/temporal_derivation/milestone_recognition) and one new
governed write path per agent (CognitiveAgent.retain, mirroring
ManagerAgent.retain), so the identical mechanism is available to Alfred
and to an appropriately equipped specialist alike.
"""

from __future__ import annotations

import ast
import inspect
from datetime import date
from pathlib import Path

import pytest

from src.core.contracts import Task
from src.manager.llm_brain import LLMManagerBrain
from src.manager.memory_policy import MemoryCategory
from src.services.memory.temporal_facts import compute_age, next_occurrence

REPO_ROOT = Path(__file__).resolve().parent.parent

STAGE12_FILES = (
    REPO_ROOT / "src" / "services" / "memory" / "temporal_facts.py",
    REPO_ROOT / "src" / "services" / "memory" / "user_facts.py",
    REPO_ROOT / "src" / "services" / "memory" / "capture.py",
    REPO_ROOT / "src" / "services" / "memory" / "milestones.py",
)


def real_manager(**overrides):
    from src.composition import build_manager, build_runtime
    from src.config.settings import Settings
    from src.services.email.in_memory_mailbox import InMemoryMailbox

    overrides.setdefault("memory", "ephemeral")
    settings = Settings.from_env(env={}, log_level="CRITICAL", **overrides)
    runtime = build_runtime(settings)
    return build_manager(runtime, mailbox=InMemoryMailbox(), profile=None)


class RecordingProvider:
    def __init__(self, text: str = "Composed answer."):
        self._text = text
        self.prompts: list[str] = []

    @property
    def name(self) -> str:
        return "recording"

    def complete(self, prompt: str, *, system: str | None = None):
        from src.providers.base import Completion

        self.prompts.append(prompt)
        return Completion(text=self._text, provider=self.name, model="x")


def with_scripted_brain(manager, text: str = "Composed answer."):
    provider = RecordingProvider(text)
    brain = LLMManagerBrain(provider)
    manager.brain = brain
    manager.own_skills._converse = brain.converse
    return provider


def say(manager, text: str, *, addressed: str | None = None):
    return manager.handle_task(Task(description=text), addressed)


def exchange_say(manager, conversation, channel, text: str):
    from scripts.exchange import exchange

    return exchange(channel, conversation, text)


def new_room():
    from src.conversation.conversation import Conversation, Participant, ParticipantKind

    return Conversation(participants=(Participant("you", ParticipantKind.PERSON),))


INTRO = "My name is Priya. I was born March 3, 1999 and I attend Fenwick College."


# ---------------------------------------------------------------------------
# 1. Persistent user facts
# ---------------------------------------------------------------------------


class TestPersistentUserFacts:
    def test_a_bare_self_introduction_is_retained_without_a_remember_trigger(self):
        from src.channels.channel import TextChannel

        manager = real_manager()
        exchange_say(manager, new_room(), TextChannel(manager), INTRO)

        recalled = manager.memory.recall(limit=50)
        contents = [r.content for r in recalled]
        assert any("Priya" in c for c in contents)
        assert any("1999-03-03" in str(r.context) for r in recalled)
        assert any("Fenwick College" in c for c in contents)

    def test_facts_persist_across_a_real_restart(self, tmp_path):
        from src.channels.channel import TextChannel

        memory_dir = str(tmp_path / "memory")
        manager = real_manager(memory="persistent", memory_dir=memory_dir)
        exchange_say(manager, new_room(), TextChannel(manager), INTRO)

        second = real_manager(memory="persistent", memory_dir=memory_dir)
        recalled = second.memory.recall(limit=50)
        assert any("Priya" in r.content for r in recalled)

    def test_an_arbitrary_different_name_and_school_work_identically(self):
        """No name or school is hardcoded - a completely different
        statement is captured exactly the same way."""
        from src.channels.channel import TextChannel

        manager = real_manager()
        exchange_say(
            manager, new_room(), TextChannel(manager),
            "My name is Zephyrine Okonkwo-Baptiste. I attend Ashcombe Polytechnic.",
        )
        recalled = [r.content for r in manager.memory.recall(limit=50)]
        assert any("Zephyrine Okonkwo-Baptiste" in c for c in recalled)
        assert any("Ashcombe Polytechnic" in c for c in recalled)

    def test_ordinary_remember_that_still_works_unchanged(self):
        """Stage 12's original mechanism, untouched."""
        from src.channels.channel import TextChannel

        manager = real_manager()
        exchange_say(manager, new_room(), TextChannel(manager), "remember that my favourite club is Liverpool")
        recalled = [r.content for r in manager.memory.recall(limit=50)]
        assert any("Liverpool" in c for c in recalled)


# ---------------------------------------------------------------------------
# 2-3. Date-derived behaviour: age, next birthday, future timelines
# ---------------------------------------------------------------------------


class TestTemporalDerivation:
    def test_age_is_computed_from_the_stored_birth_date_not_restated(self):
        from src.channels.channel import TextChannel

        manager = real_manager()
        channel = TextChannel(manager)
        room = new_room()
        exchange_say(manager, room, channel, INTRO)

        result = say(manager, "how old am i")
        assert result.succeeded
        expected = compute_age(date(1999, 3, 3))
        assert str(expected) in result.output
        assert "1999-03-03" not in result.output  # a computed number, not a restated date

    def test_next_birthday_is_computed_fresh(self):
        from src.channels.channel import TextChannel

        manager = real_manager()
        exchange_say(manager, new_room(), TextChannel(manager), INTRO)

        result = say(manager, "when is my birthday")
        assert result.succeeded
        expected = next_occurrence(3, 3)
        assert expected.isoformat() in result.output

    def test_a_future_completion_date_produces_a_countdown_not_a_restated_offset(self):
        from src.channels.channel import TextChannel

        manager = real_manager()
        exchange_say(
            manager, new_room(), TextChannel(manager),
            "I expect to graduate in May 2028.",
        )
        result = say(manager, "when do i graduate")
        assert result.succeeded
        assert "2028-05-01" in result.output
        assert "day(s) away" in result.output

    def test_no_date_on_record_is_honest_not_fabricated(self):
        manager = real_manager()
        result = say(manager, "how old am i")
        assert not result.succeeded
        assert "don't have your date of birth" in result.error

    def test_updating_the_date_of_birth_is_reflected_going_forward(self):
        """'That's changed, update it' - a corrected fact is a new
        record, and recall (most-recent-first, unchanged existing
        behaviour) picks it up without anything being edited in place."""
        from src.channels.channel import TextChannel

        manager = real_manager()
        channel = TextChannel(manager)
        room = new_room()
        exchange_say(manager, room, channel, "I was born March 3, 1999.")
        exchange_say(manager, room, channel, "remember that I was born March 3, 2000.")

        result = say(manager, "how old am i")
        assert result.succeeded
        assert str(compute_age(date(2000, 3, 3))) in result.output


# ---------------------------------------------------------------------------
# 4. Generic remember/forget still work; no subject-specific routing
# ---------------------------------------------------------------------------


class TestGenericLearningUnaffected:
    def test_forget_still_works(self):
        from src.channels.channel import TextChannel

        manager = real_manager()
        channel = TextChannel(manager)
        room = new_room()
        exchange_say(manager, room, channel, "remember that my favourite club is Liverpool")
        result = say(manager, "forget that my favourite club is Liverpool")
        assert result.succeeded

    def test_recall_memory_still_works(self):
        from src.channels.channel import TextChannel

        manager = real_manager()
        channel = TextChannel(manager)
        room = new_room()
        exchange_say(manager, room, channel, "remember that my favourite club is Liverpool")
        result = say(manager, "what do you remember about favourite club")
        assert result.succeeded
        assert "Liverpool" in result.output


# ---------------------------------------------------------------------------
# 5-6. Record for later / record for research
# ---------------------------------------------------------------------------


class TestRecordForLaterAndResearch:
    def test_record_this_for_later(self):
        manager = real_manager()
        result = say(manager, "Alfred, record this for later.")
        assert result.succeeded
        assert "later" in result.output
        recorded = manager.memory.recall(kind=MemoryCategory.RECORDING.value, limit=10)
        assert recorded and recorded[0].context["purpose"] == "later"

    def test_record_this_for_research_preserves_the_purpose(self):
        manager = real_manager()
        result = say(manager, "Alfred, record this for research.")
        assert result.succeeded
        assert "research" in result.output
        recorded = manager.memory.recall(kind=MemoryCategory.RECORDING.value, limit=10)
        assert recorded[0].context["purpose"] == "research"

    def test_a_novel_purpose_needs_no_new_code(self):
        """Extensibility: an arbitrary, never-seen purpose flows through
        unchanged - never matched against a fixed list."""
        manager = real_manager()
        result = say(manager, "Record this for the quarterly board presentation.")
        assert result.succeeded
        recorded = manager.memory.recall(kind=MemoryCategory.RECORDING.value, limit=10)
        assert "quarterly board presentation" in recorded[0].context["purpose"]

    def test_recording_is_distinct_from_ordinary_memory(self):
        """Stage 12 item 13 - remember and record never collapse into
        one category."""
        manager = real_manager()
        say(manager, "Alfred, record this for later.")
        recordings = manager.memory.recall(kind=MemoryCategory.RECORDING.value, limit=10)
        user_context = manager.memory.recall(kind=MemoryCategory.USER_CONTEXT.value, limit=10)
        assert recordings and not any(r.kind == "recording" for r in user_context)

    def test_ordinary_conversation_does_not_become_a_recording(self):
        manager = real_manager()
        say(manager, "what is the weather like")
        assert manager.memory.recall(kind=MemoryCategory.RECORDING.value, limit=10) == ()


# ---------------------------------------------------------------------------
# 7. Voice-first capture - reuses the existing Channel, nothing new
# ---------------------------------------------------------------------------


class TestVoiceFirstCapture:
    def test_record_this_reaches_the_capability_through_a_real_voice_channel(self):
        from src.channels.channel import VoiceChannel
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

        manager = real_manager()
        channel = VoiceChannel(manager, speech=ScriptedSpeech())
        response = channel.ask("Hey Alfred, record this for later.")
        assert response.turn.succeeded
        assert response.turn.handled_by == "Alfred"

    def test_no_new_voice_architecture_was_introduced(self):
        """Structural: none of the new modules import channels, speech
        or voice at all - capture reaches the user exclusively through
        the existing Channel/Brain/Voice path."""
        for path in STAGE12_FILES:
            source = path.read_text(encoding="utf-8")
            assert "channels" not in source.lower().replace("channel it", "")
            tree = ast.parse(source)
            for node in ast.walk(tree):
                if isinstance(node, (ast.Import, ast.ImportFrom)):
                    module = getattr(node, "module", "") or ""
                    assert "src.channels" not in module
                    assert "voice" not in module


# ---------------------------------------------------------------------------
# 8-9. Specialist capture, provenance
# ---------------------------------------------------------------------------


class TestSpecialistCapture:
    def test_arthur_records_for_later_into_his_own_memory(self):
        manager = real_manager()
        result = say(manager, "record this for later", addressed="Arthur")
        assert result.succeeded and result.agent_name == "Arthur"

        arthur = manager.registry.get("Arthur")
        recorded = arthur.memory.recall(limit=10)
        assert recorded
        assert recorded[0].context["captured_by"] == "Arthur"
        assert recorded[0].context["purpose"] == "later"

    def test_arthur_records_for_the_email_research(self):
        manager = real_manager()
        result = say(manager, "record this for the email research", addressed="Arthur")
        assert result.succeeded
        arthur = manager.registry.get("Arthur")
        recorded = arthur.memory.recall(limit=10)
        assert "email research" in recorded[0].context["purpose"]

    def test_arthurs_recordings_never_reach_alfreds_memory(self):
        manager = real_manager()
        say(manager, "record this for later", addressed="Arthur")
        # Alfred's own memory legitimately gains an ordinary
        # delegation_outcome entry (pre-existing, unrelated behaviour) -
        # what must never appear is Arthur's own recording.
        assert manager.memory.recall(kind=MemoryCategory.RECORDING.value, limit=50) == ()

    def test_alfreds_recordings_never_reach_arthurs_memory(self):
        manager = real_manager()
        say(manager, "Alfred, record this for later.")
        arthur = manager.registry.get("Arthur")
        assert arthur.memory.recall(limit=50) == ()

    def test_ordinary_arthur_email_behaviour_is_unaffected(self):
        manager = real_manager()
        result = say(manager, "check my email")
        assert result.agent_name == "Arthur" and result.succeeded

    def test_provenance_names_who_captured_it(self):
        manager = real_manager()
        say(manager, "Alfred, record this for later.")
        recorded = manager.memory.recall(kind=MemoryCategory.RECORDING.value, limit=10)
        assert recorded[0].context["captured_by"] == "Alfred"
        assert recorded[0].context["source"] == "conversation"


# ---------------------------------------------------------------------------
# 10-11. Milestone creation and retrieval
# ---------------------------------------------------------------------------


class TestExtractMilestoneTypeDerivation:
    """Closure campaign (2026-09-16, N-09): "I graduated today!" matched
    the real achievement shape (the verb "graduated" is recognised), but
    with nothing but a bare time word after it, the stored milestone_type
    became "today" instead of "graduated" - silently wrong, not honestly
    refused. Distinct from the verb list's own deliberate, documented
    limits (this module's own docstring: generic accomplishment verbs,
    never a fixed vocabulary of achievement *types*) - once the shape is
    genuinely recognised, its type must never be a meaningless adverb."""

    def test_a_bare_time_word_falls_back_to_the_verb_itself(self):
        from src.services.memory.milestones import extract_milestone

        milestone_type, description = extract_milestone("I graduated today!")
        assert milestone_type == "graduated"
        assert description == "I graduated today!"

    def test_a_real_object_is_still_captured_normally(self):
        from src.services.memory.milestones import extract_milestone

        milestone_type, _ = extract_milestone("I got promoted to senior engineer!")
        assert milestone_type == "promoted_to_senior_engineer"

    def test_first_still_prefixes_a_real_object_but_not_a_bare_time_word(self):
        from src.services.memory.milestones import extract_milestone

        milestone_type, _ = extract_milestone("I just launched my first paying client!")
        assert milestone_type == "first_paying_client"
        # A bare time word falls back to the verb, never "first <time word>".
        milestone_type2, _ = extract_milestone("I just achieved now!")
        assert milestone_type2 == "achieved"


class TestMilestoneCreationAndRetrieval:
    def test_an_explicit_milestone_marker_stores_immediately(self):
        manager = real_manager()
        result = say(manager, "milestone: shipped v1 of the app")
        assert result.succeeded
        stored = manager.memory.recall(kind=MemoryCategory.MILESTONE.value, limit=10)
        assert stored and "shipped v1" in stored[0].content

    def test_a_bare_achievement_statement_is_not_silently_stored_by_default(self):
        """Stage 12 item 9 - do not automatically turn every
        conversational statement into a milestone."""
        manager = real_manager()
        result = say(manager, "I just got my first paying client.")
        assert result.succeeded
        assert manager.memory.recall(kind=MemoryCategory.MILESTONE.value, limit=10) == ()
        assert "milestone" in result.output.lower()

    def test_auto_confirm_preference_stores_immediately_afterward(self):
        manager = real_manager()
        say(manager, "turn on milestone auto-confirm")
        result = say(manager, "I just launched my new product.")
        assert result.succeeded
        stored = manager.memory.recall(kind=MemoryCategory.MILESTONE.value, limit=10)
        assert stored and "launched" in stored[0].content

    def test_a_novel_achievement_type_needs_no_new_code(self):
        """Extensibility - a milestone type nobody wrote a branch for."""
        manager = real_manager()
        result = say(manager, "milestone: solved a decade-old family mystery")
        assert result.succeeded
        stored = manager.memory.recall(kind=MemoryCategory.MILESTONE.value, limit=10)
        assert stored[0].context["milestone_type"] not in ("", None)

    def test_milestones_carry_structured_fields(self):
        manager = real_manager()
        say(manager, "milestone: first SaaS customer signed")
        stored = manager.memory.recall(kind=MemoryCategory.MILESTONE.value, limit=10)[0]
        for field in ("milestone_type", "date", "importance", "notes", "recognized_by", "proactive"):
            assert field in stored.context

    def test_milestones_are_retrievable_as_their_own_category(self):
        manager = real_manager()
        say(manager, "milestone: graduated with honours")
        say(manager, "Alfred, record this for later.")
        milestones = manager.memory.recall(kind=MemoryCategory.MILESTONE.value, limit=10)
        recordings = manager.memory.recall(kind=MemoryCategory.RECORDING.value, limit=10)
        assert len(milestones) == 1 and len(recordings) == 1


# ---------------------------------------------------------------------------
# 12. Proactive-permission behaviour
# ---------------------------------------------------------------------------


class TestProactivePermissionBehaviour:
    def test_default_proactive_status_allows_mention(self):
        from src.services.memory.milestones import is_mentionable

        manager = real_manager()
        say(manager, "milestone: first client signed")
        stored = manager.memory.recall(kind=MemoryCategory.MILESTONE.value, limit=10)[0]
        assert is_mentionable(stored)

    def test_a_stored_silent_preference_suppresses_mention(self):
        from src.services.memory.milestones import build_milestone_record, is_mentionable

        record = build_milestone_record(
            "first_client", "first client signed", agent_name="Alfred", proactive="silent"
        )
        assert not is_mentionable(record)

    def test_snooze_also_suppresses_mention(self):
        from src.services.memory.milestones import build_milestone_record, is_mentionable

        record = build_milestone_record(
            "first_client", "first client signed", agent_name="Alfred", proactive="snooze"
        )
        assert not is_mentionable(record)


# ---------------------------------------------------------------------------
# 13. Personality / VoiceProfile integration
# ---------------------------------------------------------------------------


class TestPersonalityAndVoiceIntegration:
    def test_a_confirmed_milestone_is_expressed_through_the_brain_not_a_canned_string(self):
        manager = real_manager()
        provider = with_scripted_brain(manager, "Huge congratulations on the new client!")
        result = say(manager, "milestone: first paying client")
        assert result.succeeded
        assert result.output == "Huge congratulations on the new client!"
        assert provider.prompts  # the Brain was genuinely consulted

    def test_with_no_brain_the_confirmation_still_honestly_reports_success(self):
        manager = real_manager()  # default NullBrain
        result = say(manager, "milestone: first paying client")
        assert result.succeeded
        assert "first paying client" in result.output

    def test_voice_follows_whichever_agent_actually_answered(self):
        from src.services.identity.voice import voice_for

        manager = real_manager()
        say(manager, "milestone: first paying client")
        assert voice_for(manager.own_skills).key == "alfred"
        assert voice_for(manager.registry.get("Arthur")).key == "arthur"


# ---------------------------------------------------------------------------
# 14-15. Canonical identity, no private-memory leakage
# ---------------------------------------------------------------------------


class TestCanonicalIdentityAndNoLeakage:
    def test_no_new_brain_or_memory_is_constructed_anywhere_in_the_new_modules(self):
        for path in STAGE12_FILES:
            source = path.read_text(encoding="utf-8")
            for forbidden in ("Memory(", "Brain(", "PersistentMemory(", "EphemeralMemory(", "LLMManagerBrain("):
                assert forbidden not in source, (path.name, forbidden)

    def test_canonical_alfred_brain_and_memory_are_unchanged_by_these_capabilities(self):
        manager = real_manager()
        brain_before, memory_before = manager.brain, manager.memory
        say(manager, "milestone: first client")
        say(manager, "Alfred, record this for later.")
        say(manager, "how old am i")
        assert manager.brain is brain_before
        assert manager.memory is memory_before

    def test_canonical_arthur_brain_and_memory_are_unchanged(self):
        manager = real_manager()
        arthur = manager.registry.get("Arthur")
        brain_before, memory_before = arthur.brain, arthur.memory
        say(manager, "record this for later", addressed="Arthur")
        assert manager.registry.get("Arthur") is arthur
        assert arthur.brain is brain_before
        assert arthur.memory is memory_before

    def test_no_handler_reaches_into_another_agents_memory(self):
        import src.manager.capabilities as capabilities_module

        for fn_name in ("record_this", "temporal_derivation", "milestone_recognition"):
            source = inspect.getsource(getattr(capabilities_module, fn_name))
            assert "registry" not in source
            assert ".memory" not in source


# ---------------------------------------------------------------------------
# 16. Extensibility without subject-specific routing
# ---------------------------------------------------------------------------


def _non_docstring_source(path: Path) -> str:
    """The file's source with every module/class/function docstring
    removed, so a docstring's own illustrative example (quoting this
    very brief's "Wellspring University") is never mistaken for a
    hardcoded value in live code."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    docstring_spans = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            body = getattr(node, "body", [])
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
                docstring_spans.add((body[0].lineno, body[0].col_offset))
    lines = path.read_text(encoding="utf-8").splitlines()
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if (node.lineno, node.col_offset) in docstring_spans:
                for lineno in range(node.lineno, (node.end_lineno or node.lineno) + 1):
                    if 1 <= lineno <= len(lines):
                        lines[lineno - 1] = ""
    return "\n".join(lines)


class TestExtensibilityWithoutSubjectRouting:
    def test_no_subject_word_in_any_new_modules_live_code(self):
        subjects = (
            "liverpool", "arsenal", "wellspring", "kelly", "priya",
            "fenwick", "saas", "client", "revenue",
        )
        for path in STAGE12_FILES:
            source = _non_docstring_source(path).lower()
            for subject in subjects:
                assert subject not in source, (path.name, subject)

    def test_no_arbitrary_execution_in_any_new_module(self):
        for path in STAGE12_FILES:
            source = path.read_text(encoding="utf-8")
            for forbidden in ("eval(", "exec(", "subprocess", "os.system", "__import__"):
                assert forbidden not in source

    def test_the_new_modules_import_nothing_from_extensions_or_evolution(self):
        for path in STAGE12_FILES:
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, (ast.Import, ast.ImportFrom)):
                    module = getattr(node, "module", "") or ""
                    names = ", ".join(getattr(n, "name", "") for n in node.names)
                    assert "extensions" not in module and "extensions" not in names
                    assert "evolution" not in module and "evolution" not in names

    def test_stage13_collaboration_remains_intact(self):
        from scripts.exchange import answer_with_collaboration

        manager = real_manager()
        with_scripted_brain(manager, "synthesised via collaboration")
        result = answer_with_collaboration(manager, Task(description="email opportunities update"))
        assert result.succeeded


# ---------------------------------------------------------------------------
# Mutation tests - the most important new guarantees
# ---------------------------------------------------------------------------


class TestMutationConfidenceGate:
    def test_specialist_capture_confidence_is_load_bearing(self):
        """Pins what removing capture.py's confidence=1.0 would break: a
        specialist's own SpecialistMemoryPolicy would then refuse every
        capture as a low-confidence hunch."""
        manager = real_manager()
        result = say(manager, "record this for later", addressed="Arthur")
        assert result.succeeded


class TestMutationMilestoneGovernance:
    def test_bare_achievement_without_auto_confirm_never_stores(self):
        """Pins the governance guarantee item 9 asks for directly."""
        manager = real_manager()
        say(manager, "I just got my first paying client.")
        assert manager.memory.recall(kind=MemoryCategory.MILESTONE.value, limit=10) == ()
