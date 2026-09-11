"""Context-Aware Agent Capability & Safety Governance.

A shared, generalized decision layer any capability can consult before
performing an action - never a second Capability system, never a second
Connection/Tool/Confirmation mechanism, and never a catalogue of named
examples (a document, a PDF, a booking, a school timetable). What crosses
this module's boundary is always the same five, generic things:

    what is being attempted    ``GovernanceContext.capability``/``operation``
    how consequential it is    ``GovernanceContext.action_kind``
    whether it is authorized   ``GovernanceContext.authorized``
    real supporting evidence   ``GovernanceContext.notes`` (ownership,
                                conversation context, personal context -
                                whatever the calling capability already
                                has, never invented here)
    the request's own words    ``GovernanceContext.task.description``

**The actual problem this solves.** A request can *look* concerning by
bare surface wording ("edit this so it says someone else's name", "get
into this account") while being entirely legitimate authorized work (a
parent editing their own child's form, a person recovering their own
account) - and a request can look ordinary while genuinely being neither.
A fixed refusal list cannot tell these apart; only real context can. This
module never refuses on a keyword match alone - a matched term is a
*signal to reason about*, not a verdict. See ``_concern_terms`` and
``assess`` below for exactly how that reasoning is bounded and honest.

**Two layers, the identical shape ``objective_loop.py``'s own
Half-Brain/Full-Brain split already uses.** A small, fast, deterministic
floor handles the two facts that need no judgement at all (unauthorized
-> restricted; already-consequential -> confirmation required) and never
needs a model. Genuine judgement - "is this concerning wording actually
describing something unsafe, given the real evidence available" - is
always asked of a Brain through the identical narrow-question pattern
``discern_ambiguity``/``assess_objective`` already established
(``ManagerBrain.assess_governance``, see ``src/manager/brain.py``) - one
canonical call, always made. A caller with no real reasoning capability
is never a second code path here: when ``assess`` is given no ``ask_brain``
callable at all, it asks the identical question of a local stand-in that
answers exactly as ``ManagerBrain``'s own default (what a real, wired
``NullBrain`` already returns) does - ``GovernanceBrainVerdict.unavailable
(source="null")``. This is why an isolated caller with no Brain wired and
a production Manager genuinely running with no reasoning capability
configured produce byte-identical decisions, including ``source`` - never
two subtly different "no Brain" behaviours pretending to be the same
thing. Either way, the honest answer is CLARIFY, never a guess.

**A confirmation requirement can never be silently removed by a
favourable content judgement.** A consequential action's own floor
(``REQUIRE_CONFIRMATION``) is what ``assess`` returns unless reasoning
names something at least as protective - it is never downgraded all
the way to ``ALLOW`` merely because reasoning judged the wording itself
unconcerning. CLARIFY/RESTRICT/REFUSE are each already at least as
protective as requiring confirmation (none of them lets anything
execute), so none of those is overridden - only ALLOW is. This is what
keeps this layer additive to, never a replacement for, the existing
``requires_confirmation_for``/``PendingConfirmation`` boundary.

**Never the sole gate.** Every existing check (tool permission, operation
authorization, ``requires_confirmation_for``) still runs exactly as it
always has. This module adds one more, later, layer - it does not remove,
weaken, or stand in for any of them.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Callable, Sequence

from src.core.contracts import Task
from src.manager.brain import GovernanceBrainVerdict


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class GovernanceVerdict(str, Enum):
    """The five governed outcomes - never more, never fewer, and never a
    per-capability variant of one of these.

    Deliberately NOT a single linear severity scale. CLARIFY, RESTRICT
    and REFUSE are each a genuinely different KIND of caution
    (respectively: "not enough is known yet", "this specific action
    should not proceed as asked", "this is genuinely unsafe"), not
    degrees of one thing - a CLARIFY is not "weaker" than a
    REQUIRE_CONFIRMATION merely because a consequential action's own
    floor happens to be REQUIRE_CONFIRMATION; pausing for clarification
    is at least as protective as pausing for a yes/no. The one real
    invariant ``assess`` enforces (see its own floor-preservation
    comment) is narrower and does not need a total order: a
    consequential action's own floor may never be silently downgraded
    all the way to ALLOW.
    """

    ALLOW = "allow"
    CLARIFY = "clarify"
    REQUIRE_CONFIRMATION = "require_confirmation"
    RESTRICT = "restrict"
    REFUSE = "refuse"

    @property
    def label(self) -> str:
        return {
            "allow": "ALLOW",
            "clarify": "CLARIFY",
            "require_confirmation": "REQUIRE CONFIRMATION",
            "restrict": "RESTRICT",
            "refuse": "REFUSE",
        }[self.value]

# Valid values for GovernanceContext.action_kind - a generic classification
# of what kind of effect the action has, never a capability name. "read"
# needs no confirmation by itself; "consequential" is the identical fact
# DeclaredConnection.requires_confirmation_for/WebsiteConnection already
# compute from an operation's own real shape (HTTP method, or click/submit
# vs navigate/read) - reused here, never re-derived by a second rule.
ACTION_KINDS = frozenset({"read", "write", "consequential"})

# A small, generic, capability-agnostic vocabulary of terms that *can*, but
# do not always, indicate a genuine safety concern - never a per-example
# list (nothing here names a document, a PDF, a website, a company, an
# industry). A match is a signal this module reasons about, exactly once,
# below - never itself a verdict. Legitimate authorized work uses several
# of these exact words constantly ("I need to update the authorization on
# my own account", "remove my ex-partner's access", "correct a forged
# signature on my own contract") - which is precisely why a match alone
# must never become a refusal.
_CONCERN_TERMS: "tuple[str, ...]" = (
    "forge", "forged", "forging", "counterfeit", "counterfeiting",
    "impersonate", "impersonating", "impersonation",
    "without their knowledge", "without their consent", "without permission",
    "steal", "stealing", "stolen", "defraud", "fraud", "fraudulent", "scam",
    "unauthorized access", "bypass security", "bypass authentication",
    "hack into", "break into", "exploit a vulnerability", "phish", "phishing",
    "launder", "laundering", "blackmail", "extort", "extortion",
    "someone else's identity", "someone else's account", "fake identity",
)


def _concern_terms(text: str) -> "tuple[str, ...]":
    """Which, if any, of ``_CONCERN_TERMS`` the request's own words
    literally contain - substring matching (several entries are phrases,
    which ``tokenize``'s word-splitting would break apart), case-folded,
    on the request text alone. Never the evidence/notes, which are this
    module's own trusted context, not the surface wording being judged."""
    lowered = " ".join(str(text or "").split()).lower()
    if not lowered:
        return ()
    return tuple(term for term in _CONCERN_TERMS if term in lowered)


@dataclass(frozen=True)
class GovernanceContext:
    """Everything governance is asked to reason over for one attempted
    action - built entirely from evidence the calling capability already
    has. Nothing here is invented by this module: ``authorized`` and
    ``action_kind`` are read from the identical checks
    (``authorization_for``/``requires_confirmation_for``,
    ``request.tools.permits``) the capability already performed before
    ever reaching this point.
    """

    task: Task
    agent_name: str
    capability: str
    operation: str = ""
    action_kind: str = "read"
    authorized: bool = True
    # Real, already-established supporting context - ownership, prior
    # conversation, personal-context evidence, whatever the calling
    # capability genuinely has. Notes, never instructions - the identical
    # "evidence, not instructions" contract every Brain consultation in
    # this project already honours.
    notes: "tuple[str, ...]" = field(default_factory=tuple)

    def __post_init__(self) -> None:
        if not str(self.agent_name).strip():
            raise ValueError("GovernanceContext.agent_name must not be empty")
        if not str(self.capability).strip():
            raise ValueError("GovernanceContext.capability must not be empty")
        object.__setattr__(self, "capability", " ".join(str(self.capability).split()))
        object.__setattr__(self, "operation", " ".join(str(self.operation or "").split()))
        kind = str(self.action_kind).strip().lower()
        if kind not in ACTION_KINDS:
            raise ValueError(f"GovernanceContext.action_kind must be one of {sorted(ACTION_KINDS)}, got {kind!r}")
        object.__setattr__(self, "action_kind", kind)
        object.__setattr__(self, "notes", tuple(n for n in (self.notes or ()) if str(n).strip()))


@dataclass(frozen=True)
class GovernanceDecision:
    """One governed outcome - structured, so the execution layer can act
    on it directly rather than parsing prose. ``source`` names what
    actually decided this ("deterministic", or the real Brain's own
    provenance string) - the identical "never invent your own origin"
    discipline ``ToolResult.source`` already holds itself to."""

    verdict: GovernanceVerdict
    reason: str
    source: str = "deterministic"
    confidence: float = 1.0

    def __post_init__(self) -> None:
        if isinstance(self.verdict, str) and not isinstance(self.verdict, GovernanceVerdict):
            object.__setattr__(self, "verdict", GovernanceVerdict(self.verdict))
        if not str(self.reason).strip():
            raise ValueError("GovernanceDecision must explain its reasoning")
        object.__setattr__(self, "reason", " ".join(str(self.reason).split()))
        object.__setattr__(self, "source", str(self.source or "deterministic").strip() or "deterministic")
        conf = float(self.confidence)
        if not 0.0 <= conf <= 1.0:
            raise ValueError(f"GovernanceDecision.confidence must be between 0 and 1, got {self.confidence}")
        object.__setattr__(self, "confidence", conf)

    @property
    def allowed(self) -> bool:
        """Whether execution may proceed *without* anything further from
        this layer - true only for ALLOW. REQUIRE_CONFIRMATION still
        proceeds, but only through the existing confirmation boundary,
        never directly."""
        return self.verdict is GovernanceVerdict.ALLOW

    def describe(self) -> str:
        return f"{self.verdict.label}: {self.reason} (source={self.source})"


def _no_reasoning_brain(task: Task, notes: "Sequence[str]") -> GovernanceBrainVerdict:
    """The canonical stand-in used when ``assess`` is given no ``ask_brain``
    callable at all - the exact same 'unavailable' shape
    ``ManagerBrain.assess_governance``'s own default (what a real, wired
    ``NullBrain`` already returns in production) gives. This is what makes
    "no ``ask_brain`` passed" and "a real Manager with no reasoning
    capability configured" the identical case to this module, rather than
    two independently hand-written behaviours that happen to look similar.
    Never disguises the absence of a real model - ``source="null"`` is the
    same honest label ``NullBrain.name`` already uses everywhere else."""
    return GovernanceBrainVerdict.unavailable(source="null")


def assess(
    context: GovernanceContext,
    *,
    ask_brain: "Callable[[Task, Sequence[str]], GovernanceBrainVerdict] | None" = None,
) -> GovernanceDecision:
    """The one, generic entry point every capability consults.

    Deterministic and free of any model call whenever it honestly can be
    (unauthorized, or authorized with no concerning wording) - the
    identical Half-Brain-safe floor every other governed mechanism in
    this project already guarantees. Reasoning is only ever invoked when
    the request's own words contain something genuinely worth judging in
    context, and even then never trusted to lower what the deterministic
    floor already established.
    """
    if not context.authorized:
        return GovernanceDecision(
            GovernanceVerdict.RESTRICT,
            f"'{context.operation or context.capability}' is not authorized for this connection/capability yet.",
        )

    floor = (
        GovernanceVerdict.REQUIRE_CONFIRMATION
        if context.action_kind == "consequential"
        else GovernanceVerdict.ALLOW
    )
    concerns = _concern_terms(context.task.description)

    if not concerns:
        reason = (
            "no safety concern indicated in the request's own wording"
            if floor is GovernanceVerdict.ALLOW
            else "a consequential action - existing confirmation is still required"
        )
        return GovernanceDecision(floor, reason)

    # One canonical path from here regardless of whether a real Brain was
    # wired: "no ask_brain given at all" and "a real Manager genuinely has
    # no reasoning capability configured" are the same fact, and are
    # handled by the identical call below - never two hand-written
    # behaviours that happen to produce similar-looking results.
    notes = context.notes + (
        "The request's own wording contains term(s) that CAN, but do not always, indicate a genuine "
        f"safety concern: {', '.join(concerns)}. This is a signal to evaluate carefully, never proof of "
        "wrongdoing by itself - legitimate authorized work (a person's own account, document, or "
        "belongings; work they are genuinely entitled to do) uses these exact words constantly.",
    )
    brain_verdict = (ask_brain or _no_reasoning_brain)(context.task, notes)
    if not brain_verdict.decided:
        # Honest, conservative pause - never a silent allow, and never a
        # fabricated refusal either. ``source`` truthfully names what (if
        # anything) was actually consulted: "null" when no real reasoning
        # brain exists at all, or whatever real, undecided brain answered.
        decision = GovernanceDecision(
            GovernanceVerdict.CLARIFY,
            f"could not confidently judge this ({brain_verdict.reason or 'no usable answer'}) - "
            "can you say more about the context and your authorization for this?",
            source=brain_verdict.source or "llm",
        )
    else:
        decision = GovernanceDecision(
            brain_verdict.verdict, brain_verdict.reason,
            source=brain_verdict.source, confidence=brain_verdict.confidence,
        )

    if floor is GovernanceVerdict.REQUIRE_CONFIRMATION and decision.verdict is GovernanceVerdict.ALLOW:
        # Reasoning judged the content fine, but the action was already
        # consequential regardless of content - the existing confirmation
        # boundary is never bypassed by a favourable content judgement.
        # Every OTHER verdict (CLARIFY, REQUIRE_CONFIRMATION itself,
        # RESTRICT, REFUSE) is already at least as protective as
        # requiring confirmation would be, so none of them is overridden
        # here - only a silent downgrade all the way to ALLOW is.
        decision = GovernanceDecision(floor, decision.reason, source=decision.source, confidence=decision.confidence)
    return decision


@dataclass(frozen=True)
class GovernanceRecord:
    """One past decision, kept for real observability - the identical
    "genuinely in-process, momentary, thread-safe" role
    ``ActivitySessions``/``ControlCenter.history`` already play for
    state that is real but not Memory-worthy (see
    ``src/manager/activity_session.py``'s own docstring for why: this is
    working state about *what just happened*, not a fact about the
    world worth remembering)."""

    at: datetime
    agent_name: str
    capability: str
    operation: str
    verdict: GovernanceVerdict
    reason: str
    source: str

    def describe(self) -> str:
        return f"[{self.at.isoformat()}] {self.agent_name}/{self.capability}:{self.operation} -> {self.verdict.label} ({self.source})"


class GovernanceLog:
    """The one place recent governance decisions are held, per agent -
    the identical role and identical shape ``ActivitySessions`` already
    plays for in-progress interaction state (thread-safe, bounded,
    constructed once per ``ManagerSkills``, never a global/module-level
    singleton - which is what keeps two agents' own governance history
    genuinely independent rather than accidentally shared).

    Bounded (``MAX_RECORDS``) for the identical reason
    ``src/services/audit/action_log.py``'s own ``MAX_ENTRIES`` is
    bounded: "the last N decisions" is what real observability needs,
    not an unbounded, ever-growing ledger.
    """

    MAX_RECORDS = 200

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._records: "list[GovernanceRecord]" = []

    def record(self, decision: GovernanceDecision, *, agent_name: str, capability: str, operation: str) -> None:
        entry = GovernanceRecord(
            at=utc_now(), agent_name=agent_name, capability=capability, operation=operation,
            verdict=decision.verdict, reason=decision.reason, source=decision.source,
        )
        with self._lock:
            self._records.append(entry)
            if len(self._records) > self.MAX_RECORDS:
                self._records = self._records[-self.MAX_RECORDS:]

    def recent(self, limit: int = 20) -> "tuple[GovernanceRecord, ...]":
        """The most recent decisions, most recent first - never more
        than ``limit``, never fewer than actually recorded."""
        with self._lock:
            records = list(self._records)
        return tuple(reversed(records[-max(limit, 0):])) if limit > 0 else ()

    def __len__(self) -> int:
        with self._lock:
            return len(self._records)
