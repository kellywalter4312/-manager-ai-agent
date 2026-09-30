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

import re
import threading
from dataclasses import dataclass, field
from typing import Callable, Sequence, TYPE_CHECKING

if TYPE_CHECKING:
    from src.core.contracts import Task
    from src.manager.brain import ActivityAnswerVerdict as _ActivityAnswerVerdict
    from src.manager.brain import BrainDecision as _BrainDecision
    from src.manager.brain import ManagerBrain


# The words people answer a "which did you mean?" question with - grammar,
# never a topic - and how many other words an answer may carry.
_ANSWER_FILLER = frozenset(
    "i i'm im i'd id meant mean it's its it is was that this one the a an go with let's lets use pick choose "
    "option yes yeah yep ok okay sure please thanks thank you first second third fourth last former latter "
    "number no not definitely obviously just".split()
)
_ANSWER_MAX_EXTRA_WORDS = 3


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
        if matched is None:
            return None
        # Phase 11 final gate: naming a candidate is not the same as choosing
        # it. "Summarise the X project history from my documents" names
        # "the X project" but is a new request - taken as the answer, it
        # was acted on as a choice and even remembered as one. An answer is the
        # candidate plus a few words of answering ("I meant ...", "let's go with
        # the second one, ...", "... please"), never a sentence of its own.
        rest = re.findall(r"[a-z0-9']+", words.replace(matched.lower(), " "))
        content = [w for w in rest if w not in _ANSWER_FILLER]
        return matched if len(content) <= _ANSWER_MAX_EXTRA_WORDS else None


@dataclass(frozen=True)
class CorrectionOption:
    """One stored fact a correction could be about - named back to the person
    exactly as it is stored (``label``), never re-worded into something else."""

    label: str
    record_id: str
    topic: str = ""
    value: str = ""


# Replies that decline to choose - grammar, never a topic.
_CORRECTION_CANCEL = ("never mind", "nevermind", "neither", "cancel", "forget it", "leave it", "none of them")
_ORDINALS = {"first": 0, "1st": 0, "second": 1, "2nd": 1, "third": 2, "3rd": 2, "fourth": 3, "4th": 3}
# What an answer to "which should I change?" may say besides the option it
# names - grammar, never a topic.
_REFERENCE_WORDS = _ANSWER_FILLER | frozenset(
    "about regarding one ones thing fact note my your oh well actually um uh".split()
)


@dataclass(frozen=True)
class PendingCorrection:
    """A correction that could mean more than one stored fact, asked back to
    the person rather than guessed (Pre-Stage-9 Campaign 2). Nothing is
    changed until they choose; choosing is matched deterministically against
    the options actually offered - by topic, by stored value, or by position
    ("the second one")."""

    question: str
    action: str
    replacement: str
    options: tuple[CorrectionOption, ...]

    def __post_init__(self) -> None:
        if not str(self.question).strip():
            raise ValueError("a PendingCorrection must have a question")
        if len(self.options) < 2:
            raise ValueError("a PendingCorrection needs at least two options")

    def select_option(self, reply: str) -> "CorrectionOption | None":
        """The option this reply chooses, or None.

        Phase 11 closure: naming an option is not choosing it - the rule
        ``PendingClarification.select_candidate`` already follows, held
        tighter here because what is chosen gets rewritten. "What do you
        remember about my favourite colour?" chose the colour and stored the
        stale replacement over it; "actually the pump seal was replaced in
        April" chose the March note and stored "green" over that. A choice
        only names: the option's own words, the replacement already asked
        about, and answer grammar. Anything else is a remark of its own."""
        words = " ".join(str(reply or "").lower().split())
        if not words:
            return None
        chosen = self._named_option(words)
        if chosen is None:
            return None
        own = set(re.findall(r"[a-z0-9']+", f"{chosen.topic} {chosen.value} {self.replacement}".lower()))
        said = re.findall(r"[a-z0-9']+", words)
        if any(w not in own and w not in _REFERENCE_WORDS and w not in _ORDINALS for w in said):
            return None
        return chosen

    def _named_option(self, words: str) -> "CorrectionOption | None":
        for word, index in _ORDINALS.items():
            if re.search(rf"\b{word}\b", words) and index < len(self.options):
                return self.options[index]
        if re.search(r"\blast\b", words):
            return self.options[-1]
        best = None
        for option in self.options:
            for name in (option.topic, option.value):
                name = " ".join(str(name or "").lower().split())
                if len(name) >= 2 and re.search(rf"\b{re.escape(name)}\b", words):
                    if best is None or len(name) > best[0]:
                        best = (len(name), option)
        if best is not None:
            return best[1]
        # A word that names exactly one option ("the colour" when the options
        # are a favourite colour and a favourite food).
        said = set(re.findall(r"[a-z0-9]+", words))
        names = [set(re.findall(r"[a-z0-9]+", f"{o.topic} {o.value}".lower())) for o in self.options]
        hits = [
            index for index, own in enumerate(names)
            if (own - set().union(*(names[:index] + names[index + 1:]))) & said
        ]
        return self.options[hits[0]] if len(hits) == 1 else None

    def is_cancel(self, reply: str) -> bool:
        words = " ".join(str(reply or "").lower().split())
        return any(marker in words for marker in _CORRECTION_CANCEL)


@dataclass(frozen=True)
class PendingConfirmation:
    """A consequential action already described back to the person,
    awaiting an explicit yes/no before it actually happens - Phase 4's
    own governance requirement ("do not silently perform consequential
    actions") made real as in-progress interaction state, the identical
    role ``PendingClarification`` already plays for an unresolved
    ambiguity.

    Two real shapes share this one mechanism, never a parallel one:
    ``party``/``content`` (a communication send/call - Phase 4's own
    original shape) or ``connection``/``params`` (Phase 6, real-world
    services & transactions: a consequential operation on a governed
    ``Connection``, generalizing this identically to the same yes/no
    gate rather than inventing a second confirmation mechanism just
    because the thing being confirmed is now a booking, a purchase or
    a cancellation instead of a message). Exactly one of ``party`` or
    ``connection`` is ever set for a given instance - which one tells
    ``guided_interaction.py``'s own resolver what a confirmed "yes"
    actually does. Every field here is exactly what was about to be
    attempted - never re-derived from the confirming reply itself, the
    same "read back, never resolved against anything new" discipline
    ``PendingClarification`` already holds itself to. ``description``
    is the question actually shown to the person, kept alongside the
    raw fields so a caller never has to reconstruct it to check what
    was asked.
    """

    operation: str
    description: str
    party: str = ""
    content: str = ""
    connection: str = ""
    params: "dict[str, str]" = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not str(self.operation).strip():
            raise ValueError("a PendingConfirmation must name its operation")
        if not str(self.description).strip():
            raise ValueError("a PendingConfirmation must have a description")
        if not str(self.party).strip() and not str(self.connection).strip():
            raise ValueError("a PendingConfirmation must name its party or its connection")


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
        self._corrections: dict[str, PendingCorrection] = {}

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

    # -- pending ambiguous memory correction (Pre-Stage-9 Campaign 2) -----

    def pending_correction(self, agent_key: str) -> PendingCorrection | None:
        with self._lock:
            return self._corrections.get(str(agent_key))

    def raise_correction(self, agent_key: str, correction: PendingCorrection) -> None:
        with self._lock:
            self._corrections[str(agent_key)] = correction

    def clear_correction(self, agent_key: str) -> None:
        with self._lock:
            self._corrections.pop(str(agent_key), None)

    def correction_reply_is_for_pending(self, agent_key: str, reply: str) -> bool:
        """Whether ``reply`` answers the pending correction question (chooses
        an option or declines). A reply that does neither abandons the
        question, so ordinary conversation is never trapped by it."""
        pending = self.pending_correction(agent_key)
        if pending is None:
            return False
        if pending.select_option(reply) is not None or pending.is_cancel(reply):
            return True
        self.clear_correction(agent_key)
        return False

    # -- anything the person alone may answer ------------------------------

    def awaits_reply(self, agent_key: str) -> bool:
        """Whether this agent has put a question to the person that only the
        person's own next remark may answer: a clarification, a consequential
        action's yes/no, an open correction question, or a guided activity's
        current question. Every one of those resolves against whatever remark
        arrives next, so nothing but the person may be allowed to send one."""
        key = str(agent_key)
        with self._lock:
            return any(
                key in store
                for store in (self._clarifications, self._confirmations, self._corrections, self._activities)
            )


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
