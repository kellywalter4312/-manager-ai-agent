"""Pre-Phase 1.6 - Full Alfred OS Production Reality & Ecosystem
Acceptance Audit.

This is a challenge pass against everything claimed GREEN through
Phase 1.5, not a re-implementation of it. Most of what CHECK inspected
survived unchanged - see ARCHITECTURE.md's own audit section for the
full account. This file pins the one genuine, previously-undiscovered
defect the audit actually found and fixed, plus the reproductions of
the specific known-risk areas the audit was explicitly told to
re-challenge (the shared-provider-instance fix, and the still-staged
CognitiveLoop/diagnose-vs-decide gap).

**The defect found: an in-progress guided session had no priority over
ordinary capability routing.** While a clarification is pending or a
guided learning activity is active, the very next remark is supposed to
be checked against it first (``src/manager/guided_interaction.py``'s
own stated contract - "the very next remark is checked, deterministically,
against the candidates actually offered"). But that check lives inside
``open_conversation``, which is only ever reached if nothing else
claims or scores higher first - and an ordinary reply mid-session
("that's enough for now" to end a quiz, a bare candidate value to
resolve a clarification) can incidentally match an unrelated
capability's own shape-based claim (``live_information``'s claim on
"for now" among them, reproduced below). Before this fix, that
incidental match silently derailed the session: the person's answer or
stop-request was treated as an ordinary request to a different
capability, which in the reproduction below crashed with the tool's own
honest "not installed"/"no current information" failure instead of
ending the session normally.

Fixed in two places, mirroring the existing priority pattern
``ManagerSkills.claims()`` already documents (addressed > claims >
scored > unclaimed): ``ManagerSkills.claims()`` now claims every remark
unconditionally while a session is active for this agent (so a
specialist's own incidental word overlap can never route the whole task
away from Alfred), and ``ManagerSkills.execute()`` now sends every such
remark straight to ``open_conversation`` without ever consulting
ordinary claims/scoring at all. Neither introduces a new capability,
a hardcoded phrase, or per-vendor/per-subject branching - the check is
purely "is a session active for this agent", generic across every
subject a clarification or activity could ever be about.
"""

from __future__ import annotations

from src.config.settings import Settings
from src.core.contracts import Task
from src.manager.activity_session import (
    ActivityBrainOps,
    GuidedActivityState,
    PendingClarification,
)
from src.manager.capabilities import OPEN_CONVERSATION, select_capability
from src.manager.llm_brain import LLMManagerBrain
from src.providers.base import Completion, LLMProvider
from scripts.control_center import build
from scripts.exchange import exchange


def real_system():
    return build(Settings.from_env(env={}, memory="ephemeral"))


class TestGuidedSessionOutranksOrdinaryCapabilityRouting:
    def test_the_reproduction_an_ordinary_capability_really_does_incidentally_claim_this_wording(self):
        """Confirms the defect's precondition still exists as a fact
        about the capability system - not fabricated for this test."""
        cap = select_capability(
            Task(description="that's enough for now"), None, participants=["Alfred"]
        )
        assert cap.name == "live_information"

    def test_a_stop_phrase_during_an_active_learning_session_ends_it_honestly_not_via_an_unrelated_capability(self):
        class Provider(LLMProvider):
            @property
            def name(self):
                return "test"

            def complete(self, prompt, *, system=None):
                system = system or ""
                if "should_retain" in system:
                    return Completion(text='{"should_retain": false, "content": null}', provider="test")
                if "grading one answer" in system:
                    return Completion(
                        text='{"correct": "", "feedback": "", "wants_to_stop": true}', provider="test"
                    )
                return Completion(text="(unexpected - the session should have handled this)", provider="test")

        runtime, manager, channel, conversation = real_system()
        brain = LLMManagerBrain(Provider(), personality=runtime.identity.personality)
        manager.brain = brain
        manager.own_skills._converse = brain.converse
        manager.own_skills._activity_brain = ActivityBrainOps.from_brain(brain)
        manager.own_skills._activity_sessions.start_activity(
            "Alfred",
            GuidedActivityState(topic="knot tying", question_number=1, current_question="Which knot joins two ropes?"),
        )

        turn = exchange(channel, conversation, "that's enough for now")
        assert turn.succeeded
        assert "live_information" not in turn.outcome.lower()
        assert "not installed" not in turn.outcome.lower()
        assert "no current information" not in turn.outcome.lower()
        # The session actually ended - a second, unrelated remark is
        # ordinary conversation again, not still trapped in the activity.
        assert manager.own_skills._activity_sessions.activity("Alfred") is None

    def test_a_candidate_value_during_a_pending_clarification_resolves_it_even_when_it_would_otherwise_be_claimed(self):
        """The identical priority, exercised via the clarification half
        of guided_interaction_response rather than the activity half."""
        runtime, manager, channel, conversation = real_system()
        manager.own_skills._activity_sessions.raise_clarification(
            "Alfred",
            PendingClarification(
                question="Should I note Postgres or MongoDB?",
                candidates=("Postgres", "MongoDB"),
            ),
        )
        turn = exchange(channel, conversation, "Postgres")
        assert turn.succeeded
        assert "postgres" in turn.outcome.lower()
        assert manager.own_skills._activity_sessions.pending_clarification("Alfred") is None

    def test_manager_skills_claims_unconditionally_true_while_a_session_is_active(self):
        """The deeper half of the fix: a specialist's own incidental
        word overlap must never be able to route the whole task away
        from Alfred while a session is genuinely in progress."""
        _, manager, _, _ = real_system()
        skills = manager.own_skills
        assert skills.claims(Task(description="a request naming nothing special")) is False

        skills._activity_sessions.start_activity(
            skills.name,
            GuidedActivityState(topic="knot tying", question_number=1, current_question="q"),
        )
        assert skills.claims(Task(description="a request naming nothing special")) is True

    def test_with_no_session_active_ordinary_routing_is_completely_unaffected(self):
        """The fix changes behaviour only while a session exists -
        confirmed by the identical probe from the reproduction test
        still routing exactly where it always did with no session
        active."""
        _, manager, channel, conversation = real_system()
        assert manager.own_skills._activity_sessions.activity("Alfred") is None
        assert manager.own_skills._activity_sessions.pending_clarification("Alfred") is None
        turn = exchange(channel, conversation, "that's enough for now")
        # No session was ever active, so this is ordinary conversation -
        # not derailed, because there was nothing to derail.
        assert turn.handled_by == "Alfred"


class TestOrdinaryUpdateWordingNoLongerMisroutesToLiveInformation:
    """The second genuine defect this audit found: 'update' - an
    ordinary, extremely common word in software/database/business
    conversation - scored the live_information capability via bare word
    overlap, misrouting genuine debugging questions ("users see stale
    prices right after we update the cache") into a fabricated
    "current, external information" request. The identical class of
    false positive "today" was already removed for
    (CURRENT_INFORMATION_WORDS's own docstring), at larger real-world
    scale. Fixed by removing the bare word from the scoring set; a
    genuine live-update request is untouched, caught by "current"/
    "latest"/"recent"/"news"/"happening" or the pre-existing
    "any update on X" marker-phrase shape, both reconfirmed below.
    """

    def test_an_ordinary_debugging_sentence_containing_update_is_no_longer_misrouted(self):
        probes = [
            "users keep seeing stale prices on the product page even right after we update them in the cache - what could cause that?",
            "why do users keep seeing outdated data even right after we update the record?",
        ]
        for text in probes:
            cap = select_capability(Task(description=text), None, participants=["Alfred"])
            assert cap.name != "live_information", text

    def test_a_genuine_current_information_request_still_works_via_other_words(self):
        cap = select_capability(
            Task(description="is Arsenal linked with any wingers right now?"), None, participants=["Alfred"]
        )
        assert cap.name == "live_information"

    def test_the_any_update_on_marker_phrase_still_claims_it(self):
        cap = select_capability(
            Task(description="any update on the Arsenal game?"), None, participants=["Alfred"]
        )
        assert cap.name == "live_information"


class TestKnownRiskAreasReconfirmedNotSilentlyRegressed:
    """The specific reproductions this audit was explicitly told to
    re-run, independent of the Phase 1.5 report's own claims."""

    def test_the_shared_provider_instance_defect_remains_fixed_in_the_default_production_path(self, monkeypatch):
        from src.composition import build_runtime

        monkeypatch.setenv("ANTHROPIC_API_KEY", "fake-audit-key")
        runtime = build_runtime(
            Settings.from_env(env={}, memory="ephemeral", provider="anthropic", brain="llm", specialist_brain="llm")
        )
        arthur_brain = runtime.specialists["email_agent"].brain
        assert runtime.provider is not arthur_brain._provider
        assert runtime.provider._api_key == arthur_brain._provider._api_key

    def test_cognitive_loop_remains_correctly_staged_and_the_diagnose_decide_gap_is_still_documented(self):
        from scripts.repo_recovery import compute_reachability

        report = compute_reachability()
        assert report.status_of("src.manager.cognitive_loop") != "production"
        assert report.status_of("src.manager.actions") != "production"

        _, manager, _, _ = real_system()
        task = Task(description="xyzzyplugh grondle the fenwick array")
        assert manager.diagnose(task) is None
        decided = manager.decide(task)
        assert decided is not None and decided.agent_name == "Alfred"
