"""Experience history - the pure structured module, unit-tested the same
way every sibling extractor/store in ``src/services/memory/`` already is:
no runtime needed, no Brain, a real ``EphemeralMemory`` where lifecycle
semantics need proving.
"""

from __future__ import annotations

from src.services.memory.ephemeral import EphemeralMemory
from src.services.memory.experience_history import (
    ExperienceCandidate,
    all_experiences,
    build_experience_record,
    current_experience_for_event,
    delivered_candidate,
    experience_history_for,
    pending_experiences,
)


def candidate(**overrides) -> ExperienceCandidate:
    defaults = dict(
        event_type="milestone_x",
        dedup_key="proactive:rec1:2026-06-15",
        agent_key="alfred",
        reaction_id="milestone_x:nod:soft_pulse",
        avatar_action="nod",
        environment_action="soft_pulse",
        overlay="card",
        intensity="full",
        duration_ms=2400,
    )
    defaults.update(overrides)
    return ExperienceCandidate(**defaults)


class TestExperienceCandidateValidation:
    def test_blank_event_type_is_rejected(self):
        import pytest
        with pytest.raises(ValueError):
            candidate(event_type="")

    def test_blank_dedup_key_is_rejected(self):
        import pytest
        with pytest.raises(ValueError):
            candidate(dedup_key="")

    def test_blank_reaction_id_is_rejected(self):
        import pytest
        with pytest.raises(ValueError):
            candidate(reaction_id="")


class TestBuildExperienceRecord:
    def test_record_carries_structured_fields_and_tags(self):
        record = build_experience_record(candidate(), noted_by="Alfred")
        assert record.kind == "experience"
        assert "experience" in record.tags
        assert "experience_event:proactive:rec1:2026-06-15" in record.tags
        assert "experience_context:alfred_milestone_x" in record.tags
        assert record.context["event_type"] == "milestone_x"
        assert record.context["delivered"] is False
        assert record.context["noted_by"] == "Alfred"

    def test_delivered_candidate_shares_the_same_dedup_key(self):
        original = candidate()
        record = build_experience_record(original, noted_by="Alfred")
        delivered = delivered_candidate(
            current_experience_for_event(original.dedup_key, recall=_recall_of(record))
        )
        assert delivered.dedup_key == original.dedup_key
        assert delivered.delivered is True


def _recall_of(*records):
    memory = EphemeralMemory()
    for record in records:
        memory.remember(record)
    return memory.recall


class TestRetrievalLifecycleAgainstRealMemory:
    def test_nothing_stored_reads_back_as_none(self):
        memory = EphemeralMemory()
        assert current_experience_for_event("nonexistent", recall=memory.recall) is None

    def test_a_selected_experience_is_retrievable_as_pending(self):
        memory = EphemeralMemory()
        memory.remember(build_experience_record(candidate(), noted_by="Alfred"))

        current = current_experience_for_event("proactive:rec1:2026-06-15", recall=memory.recall)
        assert current is not None
        assert current.delivered is False
        assert current.reaction_id == "milestone_x:nod:soft_pulse"

    def test_marking_delivered_supersedes_pending_without_deleting_history(self):
        memory = EphemeralMemory()
        original = candidate()
        memory.remember(build_experience_record(original, noted_by="Alfred"))
        selected = current_experience_for_event(original.dedup_key, recall=memory.recall)
        memory.remember(build_experience_record(delivered_candidate(selected), noted_by="Alfred"))

        current = current_experience_for_event(original.dedup_key, recall=memory.recall)
        assert current is not None
        assert current.delivered is True

        history = memory.recall(
            kind="experience", tags={"experience_event:proactive:rec1:2026-06-15"}, limit=10,
        )
        assert len(history) == 2, "the original selection must still exist, never deleted"

    def test_pending_and_history_split_correctly(self):
        memory = EphemeralMemory()
        pending_one = candidate(dedup_key="k1")
        to_be_delivered = candidate(dedup_key="k2")
        memory.remember(build_experience_record(pending_one, noted_by="Alfred"))
        memory.remember(build_experience_record(to_be_delivered, noted_by="Alfred"))
        selected = current_experience_for_event("k2", recall=memory.recall)
        memory.remember(build_experience_record(delivered_candidate(selected), noted_by="Alfred"))

        pending = pending_experiences(recall=memory.recall)
        assert {e.dedup_key for e in pending} == {"k1"}

        everything = all_experiences(recall=memory.recall)
        assert {e.dedup_key for e in everything} == {"k1", "k2"}

    def test_two_different_events_never_cross_contaminate(self):
        memory = EphemeralMemory()
        memory.remember(build_experience_record(candidate(dedup_key="k1", event_type="a"), noted_by="Alfred"))
        memory.remember(build_experience_record(candidate(dedup_key="k2", event_type="b"), noted_by="Alfred"))

        a = current_experience_for_event("k1", recall=memory.recall)
        b = current_experience_for_event("k2", recall=memory.recall)
        assert a.event_type == "a"
        assert b.event_type == "b"

    def test_experience_history_for_resolves_one_entry_per_occurrence(self):
        memory = EphemeralMemory()
        first = candidate(dedup_key="k1")
        second = candidate(dedup_key="k2", reaction_id="milestone_x:bow:warm_glow")
        memory.remember(build_experience_record(first, noted_by="Alfred"))
        memory.remember(build_experience_record(second, noted_by="Alfred"))

        history = experience_history_for("alfred", "milestone_x", recall=memory.recall)
        assert len(history) == 2
        assert {h.reaction_id for h in history} == {
            "milestone_x:nod:soft_pulse", "milestone_x:bow:warm_glow",
        }

    def test_history_scoped_to_agent_and_event_type(self):
        memory = EphemeralMemory()
        memory.remember(build_experience_record(candidate(dedup_key="k1", agent_key="alfred"), noted_by="Alfred"))
        memory.remember(build_experience_record(candidate(dedup_key="k2", agent_key="arthur"), noted_by="Arthur"))

        alfred_history = experience_history_for("alfred", "milestone_x", recall=memory.recall)
        arthur_history = experience_history_for("arthur", "milestone_x", recall=memory.recall)
        assert len(alfred_history) == 1
        assert len(arthur_history) == 1
        assert alfred_history[0].dedup_key != arthur_history[0].dedup_key

    def test_no_recall_at_all_degrades_honestly(self):
        assert current_experience_for_event("k", recall=None) is None
        assert experience_history_for("alfred", "x", recall=None) == ()
        assert all_experiences(recall=None) == ()
        assert pending_experiences(recall=None) == ()


class TestToDict:
    def test_to_dict_is_json_shaped(self):
        memory = EphemeralMemory()
        memory.remember(build_experience_record(candidate(), noted_by="Alfred"))
        current = current_experience_for_event("proactive:rec1:2026-06-15", recall=memory.recall)
        payload = current.to_dict()
        assert payload["reaction_id"] == "milestone_x:nod:soft_pulse"
        assert payload["delivered"] is False
        assert set(payload) == {
            "record_id", "event_type", "dedup_key", "agent_key", "reaction_id",
            "avatar_action", "environment_action", "overlay", "intensity",
            "duration_ms", "delivered", "message",
        }
