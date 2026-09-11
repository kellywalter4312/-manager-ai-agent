"""The Manager's reasoning capability.

The orchestrator already has two decision-making collaborators, and this is
deliberately neither of them:

    Router    - which agent can do this?      (mechanical capability matching)
    Assessor  - is this result well-formed?   (contract validation)
    Brain     - what should happen here, and why?

Router and Assessor answer narrow, checkable questions with rules. A brain is
for the judgement that rules cannot express: reading an ambiguous situation,
weighing options, proposing what to do next.

Two boundaries keep this from swallowing the Manager:

  - **The interface is one method.** A brain deliberates and returns a
    decision. It does not execute, delegate, register, fetch or report - those
    belong to the orchestrator, which remains in charge.
  - **A decision is advice, not an instruction.** The Manager consults the
    brain; it is not obliged to obey it. Nothing here can override a routing
    decision or an assessment.

Deliberately absent: memory. A brain may one day be given past context to
reason over, but storing it is a separate component with a separate lifecycle.
This module holds no state between calls.

Also absent: any model. The abstraction is provider-agnostic, so a brain backed
by a language model, by rules, or by nothing at all satisfies the same
contract. NullBrain is the default and needs no key, no network and no model.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Iterable, Sequence

from src.core.contracts import AgentSpec, Task


class BrainError(Exception):
    """Raised when a decision cannot be formed into a valid contract."""


class BrainAction(str, Enum):
    """What the brain suggests the orchestrator does next."""

    HANDLE_SELF = "handle_self"  # the Manager should do this itself
    DELEGATE = "delegate"        # a specialist is better suited
    REPORT = "report"            # there is something worth telling the user
    ESCALATE = "escalate"        # a human needs to decide
    UNKNOWN = "unknown"          # the brain has no useful view

    @property
    def label(self) -> str:
        return {
            "handle_self": "HANDLE IT MYSELF",
            "delegate": "DELEGATE",
            "report": "REPORT TO THE USER",
            "escalate": "ESCALATE - needs a human",
            "unknown": "NO VIEW",
        }[self.value]

    @property
    def is_decided(self) -> bool:
        return self is not BrainAction.UNKNOWN


def _clean(values: Iterable[str]) -> tuple[str, ...]:
    cleaned = [" ".join(str(v).split()) for v in values or ()]
    return tuple(dict.fromkeys(v for v in cleaned if v))


@dataclass(frozen=True)
class BrainDecision:
    """One piece of advice from the brain.

    Frozen, like every other contract here: a decision records what was
    concluded at a moment, and the orchestrator acting on it must not be able
    to edit it afterwards.
    """

    action: BrainAction
    reason: str
    confidence: float = 0.0
    delegate_to: str | None = None
    steps: tuple[str, ...] = field(default_factory=tuple)
    source: str = ""

    def __post_init__(self) -> None:
        if isinstance(self.action, str) and not isinstance(self.action, BrainAction):
            try:
                object.__setattr__(self, "action", BrainAction(self.action.strip().lower()))
            except ValueError:
                raise BrainError(f"Unknown action {self.action!r}") from None

        # The same honesty rule the rest of the project applies: advice the
        # user or the orchestrator cannot evaluate is not worth acting on.
        if not str(self.reason).strip():
            raise BrainError("BrainDecision must explain its reasoning")
        object.__setattr__(self, "reason", " ".join(str(self.reason).split()))

        if not 0.0 <= float(self.confidence) <= 1.0:
            raise BrainError(
                f"BrainDecision.confidence must be between 0 and 1, got {self.confidence}"
            )
        object.__setattr__(self, "confidence", float(self.confidence))

        target = (self.delegate_to or "").strip()
        object.__setattr__(self, "delegate_to", target or None)

        # Advising delegation without naming anyone is not actionable advice.
        if self.action is BrainAction.DELEGATE and not self.delegate_to:
            raise BrainError("a DELEGATE decision must name an agent")

        object.__setattr__(self, "steps", _clean(self.steps))
        object.__setattr__(self, "source", str(self.source).strip())

    @property
    def is_decided(self) -> bool:
        return self.action.is_decided

    def describe(self) -> str:
        """A short line for logs."""
        target = f" -> {self.delegate_to}" if self.delegate_to else ""
        via = f" via={self.source}" if self.source else ""
        return (
            f"{self.action.value}{target} confidence={self.confidence:.2f}"
            f"{via} steps={len(self.steps)}"
        )

    @classmethod
    def no_view(cls, reason: str, source: str = "") -> "BrainDecision":
        """A valid decision meaning "I have nothing useful to add"."""
        return cls(
            action=BrainAction.UNKNOWN,
            reason=reason,
            confidence=0.0,
            source=source,
        )


@dataclass(frozen=True)
class ActivityAnswerVerdict:
    """A judgement about one answer given during a guided interactive
    activity (a quiz, a practice session, a study check) - a fourth,
    distinct question from deliberation, conversation and retention:
    "was this answer correct, and does the person want to stop?"

    Deliberately its own type rather than a repurposing of BrainDecision:
    the two pieces of information here (a graded correctness and a stop
    request) do not fit that contract's own fields without inventing a
    meaning for one of them - the same reasoning that gave Insight its
    own shape instead of reusing BrainDecision for a specialist's
    consideration.

    ``correct`` is one of "yes", "no", "partial" - a plain word rather
    than a number, for the same reason AgentPersonality prefers "occasional,
    dry" to "humor: 0.4": a grader gains nothing from false numeric
    precision a model cannot actually back up.
    """

    evaluated: bool
    correct: str = ""
    feedback: str = ""
    wants_to_stop: bool = False
    source: str = ""

    _VALID_CORRECT = frozenset({"", "yes", "no", "partial"})

    def __post_init__(self) -> None:
        object.__setattr__(self, "correct", str(self.correct).strip().lower())
        if self.correct not in self._VALID_CORRECT:
            raise BrainError(
                f"'correct' must be one of yes/no/partial, got {self.correct!r}"
            )
        object.__setattr__(self, "feedback", " ".join(str(self.feedback).split()))
        object.__setattr__(self, "source", str(self.source).strip())

    @property
    def is_correct(self) -> bool | None:
        """True/False when graded, None when partial or ungraded - the
        three-state shape ``AvailabilityState`` already established for
        "we genuinely do not have a clean yes/no here"."""
        if self.correct == "yes":
            return True
        if self.correct == "no":
            return False
        return None

    def describe(self) -> str:
        if not self.evaluated:
            return "unevaluated"
        stop = " stop-requested" if self.wants_to_stop else ""
        return f"correct={self.correct or 'unknown'}{stop}"

    @classmethod
    def unavailable(cls, source: str = "") -> "ActivityAnswerVerdict":
        """A valid verdict meaning "I could not judge this answer" - the
        same honest-absence shape ``BrainDecision.no_view`` and
        ``Insight.none`` already give their own questions."""
        return cls(evaluated=False, source=source)


@dataclass(frozen=True)
class ObjectiveVerdict:
    """A judgement about a multi-step objective's progress: given the
    original objective and what has genuinely been gathered toward it
    so far, is it complete, does it need another concrete step, does it
    need something only the person can supply, or has it failed - a
    fifth, distinct question from deliberation, conversation, retention,
    ambiguity and guided-activity grading.

    Deliberately its own type rather than a repurposing of
    ``BrainDecision``, the identical reasoning ``ActivityAnswerVerdict``
    already gives for its own question: "continue and do what next" vs.
    "here is the final answer" vs. "ask the person this" vs. "this could
    not be done" do not fit ``BrainDecision``'s fields without inventing
    a meaning for one of them.

    ``status`` is one of "continue", "complete", "needs_input", "failed" -
    a plain word, the same reason ``ActivityAnswerVerdict.correct`` is
    text rather than a score.
    """

    decided: bool
    status: str = ""
    next_step: str = ""
    question: str = ""
    final_answer: str = ""
    source: str = ""

    _VALID_STATUS = frozenset({"", "continue", "complete", "needs_input", "failed"})

    def __post_init__(self) -> None:
        object.__setattr__(self, "status", str(self.status).strip().lower())
        if self.status not in self._VALID_STATUS:
            raise BrainError(
                "'status' must be one of continue/complete/needs_input/failed, "
                f"got {self.status!r}"
            )
        object.__setattr__(self, "next_step", " ".join(str(self.next_step).split()))
        object.__setattr__(self, "question", " ".join(str(self.question).split()))
        object.__setattr__(self, "final_answer", " ".join(str(self.final_answer).split()))
        object.__setattr__(self, "source", str(self.source).strip())

    def describe(self) -> str:
        if not self.decided:
            return "undecided"
        return f"status={self.status or 'unknown'}"

    @classmethod
    def unavailable(cls, source: str = "") -> "ObjectiveVerdict":
        """A valid verdict meaning "I could not judge this" - the same
        honest-absence shape every sibling question here already gives."""
        return cls(decided=False, source=source)


@dataclass(frozen=True)
class GovernanceBrainVerdict:
    """A judgement about whether one attempted action is genuinely
    legitimate authorized work or something unsafe, given real context -
    the sixth, distinct question this file answers, and deliberately its
    own type rather than a repurposing of ``BrainDecision`` for the
    identical reason ``ObjectiveVerdict``/``ActivityAnswerVerdict``
    already are: a structured ALLOW/CLARIFY/REQUIRE_CONFIRMATION/
    RESTRICT/REFUSE verdict does not fit ``BrainDecision``'s fields
    without inventing a meaning for one of them.

    ``verdict`` is a plain word, not an import of
    ``src.manager.governance.GovernanceVerdict`` - this module has no
    dependency on ``governance.py`` (governance depends on this module's
    shape, never the reverse, the same one-directional dependency
    ``objective_loop.py`` already has on this file).
    """

    decided: bool
    verdict: str = ""
    reason: str = ""
    confidence: float = 0.0
    source: str = ""

    _VALID_VERDICTS = frozenset({"", "allow", "clarify", "require_confirmation", "restrict", "refuse"})

    def __post_init__(self) -> None:
        verdict = str(self.verdict).strip().lower()
        if verdict not in self._VALID_VERDICTS:
            raise BrainError(
                "'verdict' must be one of allow/clarify/require_confirmation/restrict/refuse, "
                f"got {verdict!r}"
            )
        object.__setattr__(self, "verdict", verdict)
        object.__setattr__(self, "reason", " ".join(str(self.reason).split()))
        if not 0.0 <= float(self.confidence) <= 1.0:
            raise BrainError(f"GovernanceBrainVerdict.confidence must be between 0 and 1, got {self.confidence}")
        object.__setattr__(self, "confidence", float(self.confidence))
        object.__setattr__(self, "source", str(self.source).strip())

    def describe(self) -> str:
        if not self.decided:
            return "undecided"
        return f"verdict={self.verdict or 'unknown'}"

    @classmethod
    def unavailable(cls, source: str = "") -> "GovernanceBrainVerdict":
        """A valid verdict meaning "I could not judge this" - the same
        honest-absence shape every sibling question here already gives."""
        return cls(decided=False, source=source)


class ManagerBrain(ABC):
    """A reasoning capability the orchestrator can consult."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Short identifier, used for provenance and logs."""

    @abstractmethod
    def deliberate(
        self,
        task: Task,
        agents: Sequence[AgentSpec] = (),
        notes: Sequence[str] = (),
    ) -> BrainDecision:
        """Consider a situation and advise what should happen next.

        ``agents`` describes who is available, using the spec each agent
        already publishes - so a brain can reason about delegation without
        depending on BaseAgent or reaching into the registry itself.

        ``notes`` is free context the caller wants considered.

        Implementations should return a decision rather than raise. The
        Manager guards this boundary anyway, because a brain is injected and
        may be anything.
        """

    def converse(self, task: Task, notes: Sequence[str] = ()) -> BrainDecision:
        """Compose an open-ended, conversational answer - never a
        delegation decision.

        A different question from ``deliberate()``'s: that one asks "who
        should handle this, and why" and returns advice the orchestrator
        may decline; this one asks "what is the actual answer to this",
        for a request that needs genuine reasoning rather than routing.
        A useful reply arrives as ``BrainDecision(action=REPORT,
        reason=<the answer>)`` - ``reason`` carries the composed text
        itself here, not a justification for a routing choice.

        Concrete, not abstract, and defaulting to an honest "no view" -
        the same answer ``NullBrain.deliberate`` already gives, and for
        the same reason: a brain with nothing behind it must say so
        rather than pretend. This is what lets every existing
        ``ManagerBrain`` implementation and test double keep working
        unchanged; only a brain that can genuinely converse needs to
        override it (see ``LLMManagerBrain.converse``).
        """
        return BrainDecision.no_view(
            reason="no reasoning capability is configured", source=self.name
        )

    def analyze_image(self, image_bytes: bytes, prompt: str) -> BrainDecision:
        """Genuine visual reasoning over a real image - a fourth,
        distinct question from ``converse()``'s ("what is the answer to
        this text") and every sibling question here: "what does this
        image actually show". A useful answer arrives as
        ``BrainDecision(action=REPORT, reason=<the analysis>)``, the
        identical repurposing ``converse()`` already uses for composed
        text rather than a routing justification.

        Concrete, not abstract, and defaulting to an honest "no view"
        for the identical reason every sibling question here does: most
        configured reasoning has no real vision capability at all, and
        a fabricated description would be worse than admitting the
        limit. Only a brain backed by a genuinely vision-capable
        provider needs to override this (see
        ``LLMManagerBrain.analyze_image``).
        """
        return BrainDecision.no_view(
            reason="no reasoning capability is configured", source=self.name
        )

    def discern_retention(self, task: Task, notes: Sequence[str] = ()) -> BrainDecision:
        """Judge, from genuine understanding of what was actually said,
        whether the person is asking for something to be kept for later -
        never a fixed list of trigger phrases.

        A third, distinct question from ``deliberate()``'s ("who should
        handle this") and ``converse()``'s ("what is the answer to
        this"): this one asks "did the person, in this remark and the
        conversation around it, express an intent to have something
        retained" - and if so, what. ``task.description`` is the remark
        itself; ``notes`` carries the recent conversation as context, the
        same "evidence, not instructions" shape ``converse()`` already
        takes notes under, because the thing to keep is often named a
        turn or two earlier ("we've been talking about this... keep this
        for me").

        A useful verdict arrives as ``BrainDecision(action=REPORT, reason=
        <what to keep>)`` when retention was genuinely asked for; a
        decided ``UNKNOWN``/no-view when it was not, or when this brain
        cannot judge it at all. The caller (see
        ``scripts/exchange.py::retain_what_was_asked_for``) still routes
        whatever this returns through the exact same governed memory
        policy every other retention candidate already passes through -
        this method only judges intent, it never writes anything.

        Concrete, not abstract, and defaulting to an honest "no view" -
        the same reason ``converse()`` itself defaults this way: a brain
        with nothing behind it must say so, which is also exactly correct
        behaviour here (no model means no genuine-understanding judgement
        is possible, so the caller falls back to its own deterministic,
        Half-Brain-safe floor rather than getting a fabricated verdict).
        Only a brain that can genuinely judge intent needs to override
        this (see ``LLMManagerBrain.discern_retention``).
        """
        return BrainDecision.no_view(
            reason="no reasoning capability is configured", source=self.name
        )

    def discern_ambiguity(self, task: Task, notes: Sequence[str] = ()) -> BrainDecision:
        """Judge whether this remark states two or more specific candidate
        values for the same thing, genuinely unresolved - "it's either
        Tuesday or Wednesday, I'm not sure which" - never a fixed list of
        trigger phrases, the same understanding-first stance
        ``discern_retention`` already takes for its own question.

        A useful verdict arrives as ``BrainDecision(action=REPORT,
        reason=<a short clarifying question to ask>, steps=<the candidate
        values, in the order given>)`` - ``steps`` is repurposed here to
        carry the raw candidate strings rather than action steps, exactly
        the way ``converse()`` already repurposes ``reason`` to carry
        composed text rather than a justification. A decided ``UNKNOWN``
        means either no genuine ambiguity was found, or this brain cannot
        judge it - the caller (see
        ``src/manager/capabilities.py::open_conversation``) then answers
        normally, exactly as it would with no Brain at all.

        Concrete, not abstract, and defaulting to an honest "no view" for
        the identical reason every sibling question here does: no model
        means no genuine judgement is possible, and guessing at ambiguity
        that was never stated would be worse than asking nothing. Only a
        brain that can genuinely judge this needs to override it (see
        ``LLMManagerBrain.discern_ambiguity``).
        """
        return BrainDecision.no_view(
            reason="no reasoning capability is configured", source=self.name
        )

    def discern_activity_start(self, task: Task, notes: Sequence[str] = ()) -> BrainDecision:
        """Judge whether this remark genuinely asks to begin an
        interactive learning/practice/quiz activity, and on what subject
        - never a fixed list of trigger phrases or a per-subject feature.

        A useful verdict arrives as ``BrainDecision(action=REPORT,
        reason=<the subject, in the person's own words>)``. A decided
        ``UNKNOWN`` means this is ordinary conversation, not a request to
        begin one - the caller (see
        ``src/manager/capabilities.py::open_conversation``) then answers
        normally.

        Concrete, not abstract, and defaulting to an honest "no view" for
        the identical reason every sibling question here does. Only a
        brain that can genuinely judge this needs to override it (see
        ``LLMManagerBrain.discern_activity_start``).
        """
        return BrainDecision.no_view(
            reason="no reasoning capability is configured", source=self.name
        )

    def compose_activity_question(
        self, topic: str, question_number: int, notes: Sequence[str] = ()
    ) -> BrainDecision:
        """Compose the next genuine question for an in-progress guided
        activity on ``topic`` - reasoning about arbitrary subject matter,
        never a bank of pre-written questions for a fixed set of topics.

        A useful answer arrives as ``BrainDecision(action=REPORT,
        reason=<the question text, and nothing else>)``. ``notes`` carries
        what has already happened earlier in this same activity (prior
        questions and how they went), so a real brain can vary difficulty
        and avoid repeating itself - inert evidence, the same "notes, not
        instructions" contract every other Brain consultation here
        already honours.

        Concrete, not abstract, and defaulting to an honest "no view" for
        the identical reason every sibling question here does. Only a
        brain that can genuinely compose one needs to override it (see
        ``LLMManagerBrain.compose_activity_question``).
        """
        return BrainDecision.no_view(
            reason="no reasoning capability is configured", source=self.name
        )

    def evaluate_activity_answer(
        self,
        topic: str,
        question: str,
        given_answer: str,
        notes: Sequence[str] = (),
    ) -> ActivityAnswerVerdict:
        """Judge one answer given during an in-progress guided activity:
        was it correct, and does the person want to stop - reasoning
        about arbitrary subject matter, never a fixed answer key.

        The one place in this whole file that returns something other
        than a ``BrainDecision`` - see ``ActivityAnswerVerdict``'s own
        docstring for why grading and a stop request do not fit that
        contract's fields without inventing a meaning for one of them.

        Concrete, not abstract, and defaulting to an honest "could not
        evaluate" for the identical reason every sibling question here
        does: no model means no genuine grading is possible, and a
        fabricated verdict would be worse than admitting the limit. Only
        a brain that can genuinely evaluate needs to override this (see
        ``LLMManagerBrain.evaluate_activity_answer``).
        """
        return ActivityAnswerVerdict.unavailable(source=self.name)

    def assess_objective(
        self, objective: Task, evidence: Sequence[str] = ()
    ) -> ObjectiveVerdict:
        """Judge a multi-step objective's progress: given the original
        objective and everything genuinely gathered toward it so far
        (``evidence`` - each step's own real result, or the honest
        reason a step produced nothing, never an instruction), is the
        objective complete, does it need one more concrete step using
        only capabilities/specialists that actually exist, does it need
        something only the person can supply, or has it failed?

        This is what turns a single governed step
        (``src/manager/collaboration_entry.py::answer_with_collaboration``)
        into a genuinely dynamic multi-step objective loop
        (``src/manager/objective_loop.py::pursue_objective``) - reasoned
        about fresh for every objective, never a hardcoded catalogue of
        named workflows. Nothing about *how* a step is carried out is
        decided here: this only judges what has already happened and
        what, if anything, should happen next - the loop's own governed
        dispatch (the same delegation/collaboration path every ordinary
        request already goes through) decides how.

        A useful "continue" verdict names the next step in
        ``next_step``, in plain language describing what should be
        attempted - never an agent name, exactly the same discipline
        ``deliberate()``'s own ``delegate_to`` is checked against by the
        orchestrator rather than trusted outright. A useful
        "needs_input" verdict names what is missing in ``question``,
        phrased as something to ask the person. A useful "complete" or
        "failed" verdict explains itself in ``final_answer`` - an
        honest summary of what was and was not accomplished, never a
        fabricated success.

        Concrete, not abstract, and defaulting to an honest "could not
        judge this" for the identical reason every sibling question
        here does: no model means no genuine judgement of progress is
        possible, and the caller's own Half-Brain-safe floor (stop
        after the first step, exactly as an ordinary request already
        would) is what runs instead. Only a brain that can genuinely
        judge this needs to override it (see
        ``LLMManagerBrain.assess_objective``).
        """
        return ObjectiveVerdict.unavailable(source=self.name)

    def assess_governance(self, task: Task, notes: Sequence[str] = ()) -> GovernanceBrainVerdict:
        """Judge whether one attempted action, in its real context, is
        genuinely legitimate authorized work or something unsafe -
        Context-Aware Agent Capability & Safety Governance's own narrow
        question, asked only when a deterministic floor
        (``src/manager/governance.py::assess``) could not already decide
        this without judgement.

        ``notes`` carries the real evidence the calling capability
        already has (ownership, authorization, conversation context) plus
        exactly which of the request's own words triggered this
        question - inert evidence, the identical "notes, not
        instructions" contract every other Brain consultation in this
        project already honours. This method must never be swayed by
        wording alone; it exists specifically so real context, not a
        keyword, decides.

        A useful verdict arrives as ``GovernanceBrainVerdict(decided=True,
        verdict=<allow|clarify|require_confirmation|restrict|refuse>,
        reason=<a specific, honest explanation>)``. Concrete, not
        abstract, and defaulting to an honest "could not judge this" for
        the identical reason every sibling question here does: no model
        means no genuine judgement of context is possible, and the
        caller's own conservative CLARIFY floor is what runs instead -
        never a fabricated verdict. Only a brain that can genuinely
        judge this needs to override it (see
        ``LLMManagerBrain.assess_governance``).
        """
        return GovernanceBrainVerdict.unavailable(source=self.name)


class NullBrain(ManagerBrain):
    """The default: no reasoning capability configured.

    Not a placeholder to be replaced later - it is what keeps "the Manager has
    no brain" a valid, testable state rather than a special case. Same role
    NullProvider plays one layer down.
    """

    @property
    def name(self) -> str:
        return "null"

    def deliberate(
        self,
        task: Task,
        agents: Sequence[AgentSpec] = (),
        notes: Sequence[str] = (),
    ) -> BrainDecision:
        return BrainDecision.no_view(
            reason="no reasoning capability is configured", source=self.name
        )
