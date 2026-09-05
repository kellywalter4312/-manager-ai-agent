"""In-progress interaction state: a pending clarification question, or an
active guided learning/practice session - the two things this project's
own Memory contract already refuses to hold.

``MemoryCategory``'s own allow-list excludes exactly this kind of thing on
purpose: ``EXCLUDED_KINDS`` in ``src/manager/memory_policy.py`` names
"scratch", "task_history" and "processed_state" as refused because
"temporary working state does not survive the task that made it" and
"every routine execution is history, not knowledge." Whether a clarifying
question is still awaiting an answer, or which question number a guided
quiz is on, is exactly that kind of working state - true for the length of
one conversation, meaningless once it ends, and never itself a fact worth
remembering. It does not belong in Memory, and putting it there would mean
either widening the allow-list to admit scratch state (undoing the reason
that list is closed) or refusing this feature outright.

So this module holds it the same way ``PresenceRegistry``
(``src/services/identity/presence.py``) already holds "who is speaking
right now": genuinely in-process, genuinely momentary, thread-safe, and
answerable to exactly one question - "what is in progress for this agent,
right now?" **Not a second Memory and not a second Brain.** Nothing here
reasons, judges correctness, or decides what to ask - that is the Brain's
job (see ``ManagerBrain.discern_ambiguity`` / ``discern_activity_start`` /
``compose_activity_question`` / ``evaluate_activity_answer``). This module
only remembers, for as long as it stays true, what the Brain already
decided is in progress.

Once something is genuinely worth keeping - the chosen answer to a
resolved ambiguity, a session's own final progress summary - it crosses
into real, governed Memory exactly the way every other durable fact does,
through the same ``retain()`` path, under the same ``USER_CONTEXT``
category. This module is where that fact lives *before* it is worth
keeping, never instead of keeping it.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Callable, Sequence, TYPE_CHECKING

if TYPE_CHECKING:
    from src.core.contracts import Task
    from src.manager.brain import ActivityAnswerVerdict as _ActivityAnswerVerdict
    from src.manager.brain import BrainDecision as _BrainDecision
    from src.manager.brain import ManagerBrain


@dataclass(frozen=True)
class PendingClarification:
    """A clarifying question already asked, awaiting the person's choice.

    ``candidates`` are the exact values offered, in the order given - the
    same "read back, never resolved against anything" rule
    ``_communication_shape`` already follows for a party or content
    string. Matching the person's reply against these is deterministic
    (see ``select_candidate`` below); only *raising* the question needed
    genuine understanding, and that already happened in the Brain.
    """

    question: str
    candidates: tuple[str, ...]

    def __post_init__(self) -> None:
        if not str(self.question).strip():
            raise ValueError("a PendingClarification must have a question")
        if len(self.candidates) < 2:
            raise ValueError("a PendingClarification needs at least two candidates")
        object.__setattr__(self, "candidates", tuple(c.strip() for c in self.candidates if c.strip()))

    def select_candidate(self, reply: str) -> str | None:
        """Which offered candidate this reply names, or None.

        Deterministic substring matching against the exact candidates
        offered - never a guess at a new value the person did not
        actually choose from what was offered. Longest candidate wins a
        reply naming more than one, the same tie-break
        ``invite_participant`` already uses for overlapping names.
        """
        words = str(reply or "").strip().lower()
        if not words:
            return None
        matched = None
        for candidate in self.candidates:
            if candidate.lower() in words:
                if matched is None or len(candidate) > len(matched):
                    matched = candidate
        return matched


@dataclass(frozen=True)
class PendingConfirmation:
    """A consequential action already described back to the person,
    awaiting an explicit yes/no before it actually happens - Phase 4's
    own governance requirement ("do not silently perform consequential
    actions") made real as in-progress interaction state, the identical
    role ``PendingClarification`` already plays for an unresolved
    ambiguity.

    ``operation``/``party``/``content`` are exactly what was about to be
    attempted - never re-derived from the confirming reply itself, the
    same "read back, never resolved against anything new" discipline
    ``PendingClarification`` already holds itself to. ``description`` is
    the question actually shown to the person, kept alongside the raw
    fields so a caller never has to reconstruct it to check what was
    asked.
    """

    operation: str
    party: str
    content: str
    description: str

    def __post_init__(self) -> None:
        if not str(self.operation).strip():
            raise ValueError("a PendingConfirmation must name its operation")
        if not str(self.party).strip():
            raise ValueError("a PendingConfirmation must name its party")
        if not str(self.description).strip():
            raise ValueError("a PendingConfirmation must have a description")


@dataclass
class GuidedActivityState:
    """One in-progress guided learning/practice/quiz session on an
    arbitrary subject - never a per-subject feature, never pre-written
    questions. Mutable (unlike every frozen contract elsewhere in this
    project) because this is exactly the kind of transient bookkeeping
    Memory itself is never allowed to be: it is meant to be updated in
    place turn by turn and discarded when the session ends, not
    versioned or retained.
    """

    topic: str
    question_number: int = 0
    correct_count: int = 0
    incorrect_count: int = 0
    partial_count: int = 0
    current_question: str = ""
    history: tuple[str, ...] = field(default_factory=tuple)

    @property
    def answered_count(self) -> int:
        return self.correct_count + self.incorrect_count + self.partial_count

    @property
    def score_line(self) -> str:
        """A short, human line describing progress so far - never a
        fabricated number: correct/partial/incorrect are each real,
        deterministic counters this module itself increments, one
        Brain-graded verdict at a time."""
        total = self.answered_count
        if total == 0:
            return f"no questions answered yet on {self.topic}"
        parts = [f"{self.correct_count} correct"]
        if self.partial_count:
            parts.append(f"{self.partial_count} partially correct")
        if self.incorrect_count:
            parts.append(f"{self.incorrect_count} incorrect")
        return f"{', '.join(parts)} out of {total} on {self.topic}"


class ActivitySessions:
    """The one place in-progress interaction state is allowed to exist,
    per agent - the identical role and the identical shape
    ``PresenceRegistry`` already plays for "who is speaking right now."

    Thread-safe for the same reason that registry is: a live session,
    not a batch job. Constructed once per ``ManagerSkills`` (mirroring
    how one ``PresenceRegistry`` is constructed once per ``Runtime``),
    never per turn - a session must survive from one turn to the next
    within the same conversation, or it could never track anything.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._clarifications: dict[str, PendingClarification] = {}
        self._activities: dict[str, GuidedActivityState] = {}
        self._confirmations: dict[str, PendingConfirmation] = {}

    # -- pending clarification ------------------------------------------

    def pending_clarification(self, agent_key: str) -> PendingClarification | None:
        with self._lock:
            return self._clarifications.get(str(agent_key))

    def raise_clarification(self, agent_key: str, clarification: PendingClarification) -> None:
        with self._lock:
            self._clarifications[str(agent_key)] = clarification

    def clear_clarification(self, agent_key: str) -> None:
        with self._lock:
            self._clarifications.pop(str(agent_key), None)

    # -- guided activity ---------------------------------------------------

    def activity(self, agent_key: str) -> GuidedActivityState | None:
        with self._lock:
            return self._activities.get(str(agent_key))

    def start_activity(self, agent_key: str, state: GuidedActivityState) -> None:
        with self._lock:
            self._activities[str(agent_key)] = state

    def end_activity(self, agent_key: str) -> GuidedActivityState | None:
        """Remove and return the session that was in progress, or None."""
        with self._lock:
            return self._activities.pop(str(agent_key), None)

    # -- pending consequential-action confirmation (Phase 4) --------------

    def pending_confirmation(self, agent_key: str) -> PendingConfirmation | None:
        with self._lock:
            return self._confirmations.get(str(agent_key))

    def raise_confirmation(self, agent_key: str, confirmation: PendingConfirmation) -> None:
        with self._lock:
            self._confirmations[str(agent_key)] = confirmation

    def clear_confirmation(self, agent_key: str) -> None:
        with self._lock:
            self._confirmations.pop(str(agent_key), None)


@dataclass(frozen=True)
class ActivityBrainOps:
    """The four narrow Brain questions guided activity/ambiguity handling
    needs, bundled as bound callables - never the ``ManagerBrain`` object
    itself, the identical "bound callable, not the object it came from"
    narrowing ``CapabilityRequest.converse`` already applies. A handler
    holding this can ask exactly these four questions and nothing else -
    it cannot reach ``deliberate()`` or delegation machinery through it.
    """

    discern_ambiguity: "Callable[[Task, Sequence[str]], _BrainDecision]"
    discern_activity_start: "Callable[[Task, Sequence[str]], _BrainDecision]"
    compose_activity_question: "Callable[[str, int, Sequence[str]], _BrainDecision]"
    evaluate_activity_answer: "Callable[[str, str, str, Sequence[str]], _ActivityAnswerVerdict]"

    @classmethod
    def from_brain(cls, brain: "ManagerBrain") -> "ActivityBrainOps":
        """Bind all four from one real brain - the one real place a
        provider swap (``manager.brain = new_brain``) must also rebind
        this, exactly as it already must rebind ``own_skills._converse``.
        """
        return cls(
            discern_ambiguity=brain.discern_ambiguity,
            discern_activity_start=brain.discern_activity_start,
            compose_activity_question=brain.compose_activity_question,
            evaluate_activity_answer=brain.evaluate_activity_answer,
        )
