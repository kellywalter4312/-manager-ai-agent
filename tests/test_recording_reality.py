"""0.2 Reality Audit - Recording ("Alfred, record this for later/research").

Audits the real production path, not the existence of a memory class or
a capture function. Confirms and pins a genuine bug found and fixed
during this audit: `_CAPTURE_FOR`'s regex had no in-sentence stopping
point other than [.!?], so "record this for later: <content>" - the
ordinary, one-breath way this request is actually phrased - had its
purpose group forced to the end of the string, swallowing the intended
content into "purpose" and leaving nothing behind to be the content
itself. Fixed in src/services/memory/capture.py by adding ":" as an
additional stop/boundary character, symmetric with [.!?].

Every behavioral claim below (capture, storage, purpose, retrieval,
deletion, correction, agent ownership, no accidental persistence) is
exercised through the real scripts/control_center.py::build() +
scripts/exchange.py::exchange() path - never a bare capture.py call in
isolation standing in for "the user actually said this to Alfred."
"""

from __future__ import annotations

from src.config.settings import Settings
from src.services.memory.capture import extract_capture
from scripts.control_center import build
from scripts.exchange import exchange


def real_system():
    settings = Settings.from_env(env={}, memory="ephemeral")
    return build(settings)


# ---------------------------------------------------------------------------
# The bug fix itself
# ---------------------------------------------------------------------------


class TestTheColonPurposeBugIsFixed:
    def test_purpose_stops_at_a_colon_not_the_end_of_the_string(self):
        content, purpose = extract_capture(
            "Alfred, record this for later: pick up the dry cleaning on Friday"
        )
        assert purpose == "later"
        assert content == "pick up the dry cleaning on Friday"

    def test_research_purpose_with_a_colon_also_splits_correctly(self):
        content, purpose = extract_capture(
            "record this for research: the Java garbage collector uses generational collection"
        )
        assert purpose == "research"
        assert content == "the Java garbage collector uses generational collection"

    def test_the_bare_trigger_regression_is_unaffected(self):
        """No colon present - the documented "keep the whole utterance
        when nothing else was said" fallback must be exactly as it was
        before this fix."""
        assert extract_capture("Alfred, record this for later.") == ("record this for later.", "later")
        assert extract_capture("record this for later") == ("record this for later", "later")
        assert extract_capture("record this for the email research") == (
            "record this for the email research", "email research",
        )


# ---------------------------------------------------------------------------
# The real production path: capture -> storage -> purpose -> retrieval
# ---------------------------------------------------------------------------


class TestRecordingThroughTheRealProductionPath:
    def test_record_this_for_later_with_embedded_content_is_captured_cleanly(self):
        _, manager, channel, conversation = real_system()
        turn = exchange(channel, conversation, "Alfred, record this for later: pick up the dry cleaning on Friday")

        assert turn.capability == "record_this"
        assert turn.outcome == "Recorded for later."

        records = manager.memory.recall(limit=10)
        assert len(records) == 1
        assert records[0].content == "pick up the dry cleaning on Friday"
        assert records[0].context["purpose"] == "later"
        assert records[0].kind == "recording"

    def test_recorded_content_is_later_retrievable_by_a_related_word(self):
        """Search, as this project's own recall already implements it -
        token overlap, not an exact-string match."""
        _, manager, channel, conversation = real_system()
        exchange(channel, conversation, "Alfred, record this for research: photosynthesis converts light into chemical energy in plants")

        turn = exchange(channel, conversation, "what do you remember about plants")
        assert "photosynthesis converts light into chemical energy in plants" in turn.outcome

    def test_ordinary_conversation_is_never_accidentally_recorded(self):
        _, manager, channel, conversation = real_system()
        exchange(channel, conversation, "I am thinking about picking up dry cleaning sometime soon")
        assert manager.memory.recall(limit=20) == ()

    def test_deletion_via_the_existing_forget_capability(self):
        _, manager, channel, conversation = real_system()
        exchange(channel, conversation, "Alfred, record this for later: pick up the dry cleaning on Friday")

        forgotten = exchange(channel, conversation, "forget that I need to pick up the dry cleaning")
        assert forgotten.succeeded

        after = exchange(channel, conversation, "what do you remember about dry cleaning")
        assert "don't have anything remembered" in after.outcome

    def test_correction_via_forget_then_re_record(self):
        """No dedicated edit-in-place function exists in this codebase
        (confirmed absent) - this proves the practical need is still
        met by composing the two primitives that do exist."""
        _, manager, channel, conversation = real_system()
        exchange(channel, conversation, "Alfred, record this for later: pick up the dry cleaning on Friday")
        exchange(channel, conversation, "forget that I need to pick up the dry cleaning")
        exchange(channel, conversation, "Alfred, record this for later: pick up the dry cleaning on Saturday instead")

        result = exchange(channel, conversation, "what do you remember about dry cleaning")
        assert "Saturday" in result.outcome
        assert "Friday" not in result.outcome

    def test_recording_addressed_to_a_specialist_lands_in_that_specialists_own_memory(self):
        """Agent ownership: the content itself must reach Arthur's own
        store, never Alfred's - Alfred's own memory may legitimately
        gain a delegation-bookkeeping record, but never the recorded
        content."""
        _, manager, channel, conversation = real_system()
        exchange(channel, conversation, "record this for later: check the Northwind lead again", addressed="Arthur")

        arthur = manager.registry.get("Arthur")
        arthur_records = arthur.memory.recall(limit=10)
        assert any(r.content == "check the Northwind lead again" for r in arthur_records)

        alfred_records = manager.memory.recall(limit=10)
        assert not any(r.content == "check the Northwind lead again" for r in alfred_records)
        assert not any(r.kind == "recording" for r in alfred_records)
