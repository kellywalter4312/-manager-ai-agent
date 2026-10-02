"""GM-13: a sensitivity class on personal records and sources - declared by
the owner and the person, never decided by topic, and enforced where
information is written, recalled, shared, shown, saved and kept. Subjects
(a court case, a sailboat, a sleep band) appear nowhere in src/."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from src.conversation.conversation import Conversation, Participant, ParticipantKind, Utterance
from src.conversation.session import Session
from src.core.contracts import Task
from src.core.sensitivity import normalise_level, rank
from src.core.shared_context import SharedNote, bind_note_screen, shared_notes, with_shared_context
from src.services.memory.memory import MemoryRecord
from src.services.memory.sensitivity import (
    SensitivityPolicy,
    current_request_level,
    gated_recall,
    is_cloud_provider,
    level_of,
    redact,
    requested,
    requested_level,
    retention_step,
    without_markers,
)
from src.services.observations.store import Observation, ObservationStore
from tests.gm_support import System, isolate_owner_state

NOW = datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def _isolated(monkeypatch, tmp_path):
    isolate_owner_state(monkeypatch, tmp_path)
    yield
    bind_note_screen(lambda text: "")


class TestTheScaleAndTheRules:
    def test_the_scale(self):
        assert normalise_level("Private") == "personal" and normalise_level("ordinary") == ""
        assert rank("restricted") > rank("sensitive") > rank("personal") > rank("")
        assert rank("unknown words") > rank("restricted"), "unreadable is handled as the most careful"
        with pytest.raises(ValueError):
            normalise_level("spicy")

    def test_rules_are_words_sources_and_kinds_never_topics(self, tmp_path):
        policy = SensitivityPolicy(tmp_path)
        assert policy.classify("my court case is next week") == "", "nothing is sensitive by topic"
        policy.declare("word", "court case", "sensitive")
        policy.declare("source", "sleep_band", "sensitive")
        policy.declare("kind", "user_context", "personal")
        assert policy.classify("the Court Case hearing moved") == "sensitive"
        assert policy.classify("a courtcases blog") == "", "whole words only"
        assert policy.classify("", source="sleep band") == "sensitive"
        assert policy.classify("x", kind="user_context") == "personal"
        assert policy.classify("x", explicit="restricted") == "restricted"
        assert policy.remove("court case") and policy.classify("court case") == ""

    def test_stamping_never_lowers_and_ordinary_stays_untouched(self, tmp_path):
        policy = SensitivityPolicy(tmp_path)
        policy.declare("word", "pension", "sensitive")
        plain = MemoryRecord(content="the fence was painted", kind="user_context")
        assert policy.stamp(plain) is plain
        stamped = policy.stamp(MemoryRecord(content="my pension ends 4471", kind="user_context"))
        assert level_of(stamped) == "sensitive"
        already = MemoryRecord(content="pension", kind="user_context", context={"sensitivity": "restricted"})
        assert level_of(policy.stamp(already)) == "restricted"
        with requested("personal"):
            assert current_request_level() == "personal"
            assert level_of(policy.stamp(plain)) == "personal"
        assert current_request_level() == ""

    def test_markers_are_how_to_keep_not_what_to_keep(self):
        assert requested_level("remember privately that I am saving for a sailboat") == "personal"
        assert without_markers("remember privately that I am saving for a sailboat") == "remember that I am saving for a sailboat"
        assert requested_level("this is confidential: my locker code is 12") == "sensitive"
        assert without_markers("this is confidential: my locker code is 12") == "my locker code is 12"
        assert requested_level("only tell me if I ask - my score was 71") == "restricted"
        assert requested_level("what do you treat as sensitive?") == "", "talking about the rules is not a marker"

    def test_retention_and_cloud_allowance_settings(self, tmp_path):
        policy = SensitivityPolicy(tmp_path)
        assert policy.cloud_ceiling() == "personal"
        assert policy.reasoning_ceiling("anthropic") == "personal" and policy.reasoning_ceiling("local") == "sensitive"
        policy.set_cloud_ceiling("sensitive")
        assert policy.reasoning_ceiling("openai") == "sensitive"
        policy.set_retention("sensitive", 30)
        assert policy.retention() == {"sensitive": 30}
        policy.set_retention("sensitive", None)
        assert policy.retention() == {}
        assert is_cloud_provider("xai") and not is_cloud_provider("null")


class _Memory:
    def __init__(self, records):
        self.records = list(records)

    def recall(self, query="", *, kind=None, tags=(), limit=10):
        return tuple(r for r in self.records if query.lower() in r.content.lower())[:limit]

    def forget(self, record_id):
        before = len(self.records)
        self.records = [r for r in self.records if r.id != record_id]
        return len(self.records) < before


class TestEnforcement:
    def _records(self):
        return [
            MemoryRecord(content="the fence was painted", kind="user_context"),
            MemoryRecord(content="the hearing is on 12 November", kind="user_context", context={"sensitivity": "sensitive"}),
            MemoryRecord(content="my score was 71", kind="user_context", context={"sensitivity": "restricted"}),
        ]

    def test_a_gated_recall_holds_the_ceiling_and_a_direct_question_passes_it(self):
        memory = _Memory(self._records())
        ceiling = {"now": "personal"}
        recall = gated_recall(memory.recall, ceiling=lambda: ceiling["now"])
        assert [r.content for r in recall("", limit=10)] == ["the fence was painted"]
        ceiling["now"] = "sensitive"
        assert len(recall("", limit=10)) == 2
        assert len(recall("", limit=10, max_sensitivity="restricted")) == 3

    def test_retention_forgets_only_what_outlived_its_level(self, tmp_path):
        policy = SensitivityPolicy(tmp_path)
        policy.set_retention("sensitive", 30)
        old = NOW - timedelta(days=40)
        memory = _Memory([
            MemoryRecord(content="old sensitive", kind="user_context", created_at=old, context={"sensitivity": "sensitive"}),
            MemoryRecord(content="new sensitive", kind="user_context", created_at=NOW, context={"sensitivity": "sensitive"}),
            MemoryRecord(content="old ordinary", kind="user_context", created_at=old),
        ])
        store = ObservationStore(tmp_path)
        store.record([Observation("band", "hr", 60, observed_at=old, sensitivity="sensitive"),
                      Observation("band", "hr", 61, observed_at=NOW, sensitivity="sensitive")])
        lines = retention_step(policy, lambda: [memory], store, NOW)
        assert [r.content for r in memory.records] == ["new sensitive", "old ordinary"]
        assert [o.value for o in store.series("band", "hr")] == [61]
        assert lines == ["sensitive: forgot 1 record(s) and 1 observation(s) older than 30 day(s)"]

    def test_a_shared_note_above_personal_is_withheld(self, tmp_path):
        policy = SensitivityPolicy(tmp_path)
        policy.declare("word", "hearing", "sensitive")
        bind_note_screen(policy.classify)
        task = with_shared_context(Task(description="discuss"), [
            SharedNote("Manager", "the hearing is on 12 November"), SharedNote("Manager", "lunch is at noon")])
        notes = shared_notes(task)
        assert "withheld" in notes[0] and "12 November" not in notes[0] and "noon" in notes[1]

    def test_a_saved_session_never_keeps_sensitive_words(self):
        conversation = Conversation.open(participants=[Participant("you", ParticipantKind.PERSON)]) if hasattr(
            Conversation, "open") else Conversation([Participant("you", ParticipantKind.PERSON)])
        conversation.say(Utterance(speaker="you", text="my score was 71", detail={"sensitivity": "restricted"}))
        conversation.say(Utterance(speaker="you", text="lunch at noon", detail={"sensitivity": "personal"}))
        saved = Session(conversation).to_dict()
        texts = [u["text"] for u in saved["utterances"]]
        assert texts == ["[restricted - not kept in the saved session]", "lunch at noon"]
        assert conversation.utterances[0].text == "my score was 71", "the live room still holds it"

    def test_redaction_on_management_surfaces(self):
        assert redact("the hearing", "sensitive").startswith("[sensitive - hidden")
        assert redact("lunch", "personal") == "lunch"


class TestThroughTheRealDoors:
    def test_rules_markers_recall_and_redaction(self, tmp_path):
        system = System(tmp_path)
        turn = system.say("treat anything about my court case as sensitive")
        assert turn.capability == "sensitivity_rules"
        system.say("remember that my court case hearing is on 12 November")
        system.say("remember privately that I am saving for a sailboat")
        system.say("remember that I prefer short written answers")
        levels = {r.content: level_of(r) for r in system.manager.memory.recall(limit=20)}
        assert levels == {"The user's court case hearing is on 12 November.": "sensitive",
                          "I am saving for a sailboat": "personal", "I prefer short written answers": ""}
        view = system.cc("status " + system.manager.own_skills.name)
        assert "12 November" not in view and "sensitive - hidden" in view
        assert "12 November" in system.say("what do you remember about my court case?").outcome
        manager = system.manager
        assert any("court case" in r.content for r in manager.own_skills._recall("court case", limit=5))
        manager._reasoning_state["provider"] = "anthropic"
        assert not any("court case" in r.content for r in manager.own_skills._recall("court case", limit=5))
        assert manager.recall_context(Task(description="the court case hearing")) == ()
        assert ("sensitivity_rules", True) in system.audit()

    def test_a_source_rule_flows_into_observations_and_specialists_never_see_them(self, tmp_path):
        from src.agents.tools.observation import ObservationTool

        system = System(tmp_path)
        system.cc("services sleep_band declare http://127.0.0.1:9 night:GET:/night:open")
        turn = system.say("mark readings from the sleep band as sensitive")
        assert turn.capability == "sensitivity_rules" and "sleep_band" in turn.outcome
        tool = ObservationTool(system.ecosystem, system.runtime.connections, owner_agent="Manager")
        declared = json.loads(tool.run(operation="watch", content=json.dumps(
            {"connection": "sleep_band", "operation": "night", "entity": "sleep band"})))
        assert declared["watch"]["sensitivity"] == "sensitive"
        system.ecosystem.observe([Observation("sleep band", "hours", 6.5, observed_at=NOW, sensitivity="sensitive")])
        specialist = ObservationTool(system.ecosystem, system.runtime.connections, owner_agent="Probe",
                                     max_sensitivity="personal")
        assert json.loads(specialist.run(operation="entities", content="{}"))["entities"] == []

    def test_control_center_rules_are_governed(self, tmp_path):
        system = System(tmp_path)
        assert system.cc("sensitivity word pension sensitive").startswith("Rule set:")
        assert system.cc("sensitivity retention sensitive 30").startswith("Retention set:")
        assert system.cc("sensitivity cloud sensitive").startswith("Cloud allowance set:")
        assert "FAILED" in system.cc("sensitivity word pension spicy")
        listing = system.cc("sensitivity")
        assert "'pension' is sensitive" in listing and "sensitive 30 day(s)" in listing
        assert ("admin.sensitivity", True) in system.audit() and ("admin.sensitivity", False) in system.audit()
        system.rebuild()
        assert "'pension' is sensitive" in system.cc("sensitivity")
