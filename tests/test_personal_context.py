"""Generalized user knowledge/personal-context checkpoint - the pure
extraction/lifecycle module, unit-tested the same way every sibling
extractor in ``src/services/memory/`` already is: no runtime needed,
no Brain, no Memory backend - a real ``EphemeralMemory`` where
retrieval semantics need proving.
"""

from __future__ import annotations

from src.services.memory.ephemeral import EphemeralMemory
from src.services.memory.personal_context import (
    STATUS_CURRENT,
    STATUS_DISCONTINUED,
    all_current_personal_context,
    build_personal_context_record,
    current_personal_context_for,
    extract_discontinued,
    extract_personal_context_candidate,
    extract_skill_learned,
    extract_update_declaration,
    normalize_topic_key,
    personal_context_intent,
)


class TestSkillLearned:
    def test_ive_learned_x(self):
        c = extract_skill_learned("I've learned Python.")
        assert c is not None and c.topic == "Python" and c.category == "skill"
        assert c.status == STATUS_CURRENT

    def test_i_know_x(self):
        c = extract_skill_learned("I know JavaScript.")
        assert c is not None and c.topic == "JavaScript"

    def test_i_have_learned_x(self):
        c = extract_skill_learned("I have learned Rust programming.")
        assert c is not None and "Rust" in c.topic

    def test_bare_pronoun_never_becomes_a_topic(self):
        assert extract_skill_learned("I know it") is None
        assert extract_skill_learned("I know that is true") is None

    def test_a_capitalized_acronym_is_not_mistaken_for_the_pronoun_it(self):
        assert extract_skill_learned("I know CSS now") is not None

    def test_no_false_positive_on_ordinary_conversation(self):
        assert extract_skill_learned("I don't know what to do") is None


class TestUpdateDeclarationAndStarting:
    def test_this_is_my_new_x(self):
        c = extract_update_declaration("This is my new IT schedule.")
        assert c is not None
        assert c.topic == "IT schedule"

    def test_here_is_my_x(self):
        c = extract_update_declaration("Here's my timetable for this semester.")
        assert c is not None
        assert "timetable" in c.topic

    def test_starting_x(self):
        c = extract_update_declaration("I'm starting IT.")
        assert c is not None and c.topic == "IT"

    def test_my_new_x_is_y_captures_content_separately(self):
        c = extract_update_declaration("My new schedule is: Mon-Fri 9-5")
        assert c is not None
        assert "Mon-Fri" in c.content

    def test_no_false_positive_on_ordinary_conversation(self):
        assert extract_update_declaration("this is really interesting") is None


class TestDiscontinued:
    def test_no_longer_x(self):
        c = extract_discontinued("I'm no longer learning French.")
        assert c is not None and c.status == STATUS_DISCONTINUED

    def test_stopped_x(self):
        c = extract_discontinued("I've stopped studying for finals.")
        assert c is not None and c.status == STATUS_DISCONTINUED

    def test_not_x_anymore(self):
        c = extract_discontinued("I'm not doing IT anymore.")
        assert c is not None and c.status == STATUS_DISCONTINUED

    def test_x_has_changed_is_honestly_marked_discontinued_without_inventing_content(self):
        c = extract_discontinued("My schedule has changed.")
        assert c is not None
        assert c.status == STATUS_DISCONTINUED
        assert "not yet stated" in c.content


class TestExtractPersonalContextCandidateAndIntent:
    def test_every_acceptance_example_is_recognised(self):
        examples = [
            "I've learned Python.",
            "I know JavaScript.",
            "This is my timetable for this semester.",
            "My schedule has changed.",
            "I'm starting IT.",
            "This is my new IT schedule.",
            "I'm no longer learning French.",
        ]
        for text in examples:
            assert personal_context_intent(text), text
            assert extract_personal_context_candidate(text) is not None, text

    def test_a_study_statement_is_personal_context_in_the_one_canonical_store(self):
        """Pre-Stage-9 Campaign 1: what the user is studying is no longer a
        separate learning store beside this one - it is an ordinary,
        supersedable personal-context topic whose topic is the subject."""
        studying = extract_personal_context_candidate("I am studying for my chemistry exam")
        assert studying is not None and studying.category == "learning"
        assert studying.topic == "my chemistry exam"
        assert personal_context_intent("I am currently learning Rust")

    def test_ordinary_conversation_is_never_claimed(self):
        for text in ("thanks, that works", "what do you think?", "I'm tired today", "sounds good"):
            assert not personal_context_intent(text), text


class TestNormalizeTopicKey:
    def test_strips_leading_qualifiers(self):
        assert normalize_topic_key("new IT schedule") == "it schedule"
        assert normalize_topic_key("IT schedule") == "it schedule"
        assert normalize_topic_key("my updated IT schedule") == "it schedule"

    def test_case_and_whitespace_insensitive(self):
        assert normalize_topic_key("  Python  ") == normalize_topic_key("python")


class TestBuildPersonalContextRecord:
    def test_record_carries_topic_key_tag_and_status(self):
        candidate = extract_skill_learned("I've learned Python.")
        record = build_personal_context_record(candidate, agent_name="Alfred")
        assert record.kind == "personal_context"
        assert "personal_context" in record.tags
        assert "personal_context_topic:python" in record.tags
        assert record.context["status"] == STATUS_CURRENT
        assert record.context["topic"] == "Python"
        assert record.context["noted_by"] == "Alfred"


class TestRetrievalLifecycleAgainstRealMemory:
    """Real EphemeralMemory, real remember()/recall() - proving the
    'latest record for this topic wins' resolution genuinely works,
    never mocked."""

    def test_nothing_stored_reads_back_as_none(self):
        memory = EphemeralMemory()
        assert current_personal_context_for("Python", recall=memory.recall) is None

    def test_a_stored_skill_is_retrievable(self):
        memory = EphemeralMemory()
        candidate = extract_skill_learned("I've learned Python.")
        memory.remember(build_personal_context_record(candidate, agent_name="Alfred"))

        current = current_personal_context_for("Python", recall=memory.recall)
        assert current is not None
        assert current.topic == "Python"
        assert current.status == STATUS_CURRENT

    def test_a_second_statement_about_the_same_topic_becomes_current_without_deleting_the_first(self):
        memory = EphemeralMemory()
        first = extract_update_declaration("This is my IT schedule.")
        memory.remember(build_personal_context_record(first, agent_name="Alfred"))
        second = extract_update_declaration("This is my new IT schedule.")
        memory.remember(build_personal_context_record(second, agent_name="Alfred"))

        current = current_personal_context_for("IT schedule", recall=memory.recall)
        assert current is not None
        assert current.record_id != None  # noqa: E711 - explicit, readable intent
        assert "new IT schedule" in current.content

        # History preserved - nothing was ever forgotten.
        all_records = memory.recall(kind="personal_context", tags={"personal_context_topic:it_schedule"}, limit=10)
        assert len(all_records) == 2

    def test_discontinuation_becomes_the_new_current_state_for_that_topic(self):
        memory = EphemeralMemory()
        learning = extract_update_declaration("I'm starting IT.")
        memory.remember(build_personal_context_record(learning, agent_name="Alfred"))
        stop = extract_discontinued("I'm not doing IT anymore.")
        memory.remember(build_personal_context_record(stop, agent_name="Alfred"))

        current = current_personal_context_for("IT", recall=memory.recall)
        assert current is not None
        assert current.status == STATUS_DISCONTINUED

    def test_two_different_topics_never_cross_contaminate(self):
        memory = EphemeralMemory()
        memory.remember(build_personal_context_record(extract_skill_learned("I've learned Python."), agent_name="Alfred"))
        memory.remember(build_personal_context_record(extract_skill_learned("I've learned Rust."), agent_name="Alfred"))

        python = current_personal_context_for("Python", recall=memory.recall)
        rust = current_personal_context_for("Rust", recall=memory.recall)
        assert python is not None and "Python" in python.content
        assert rust is not None and "Rust" in rust.content
        assert python.record_id != rust.record_id

    def test_all_current_personal_context_returns_one_entry_per_topic_the_latest_only(self):
        memory = EphemeralMemory()
        memory.remember(build_personal_context_record(extract_skill_learned("I've learned Python."), agent_name="Alfred"))
        memory.remember(build_personal_context_record(extract_update_declaration("This is my IT schedule."), agent_name="Alfred"))
        memory.remember(build_personal_context_record(extract_update_declaration("This is my new IT schedule."), agent_name="Alfred"))

        current = all_current_personal_context(recall=memory.recall)
        topics = {c.topic for c in current}
        assert len(current) == 2  # Python, IT schedule - the superseded IT schedule statement is not double-counted
        assert any("Python" in t for t in topics)
        assert any("schedule" in t.lower() for t in topics)

    def test_no_recall_at_all_degrades_honestly(self):
        assert current_personal_context_for("Python", recall=None) is None
        assert all_current_personal_context(recall=None) == ()
