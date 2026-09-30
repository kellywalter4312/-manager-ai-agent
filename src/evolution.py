"""Stage 11.B - governed self-improvement / evolution.

Stage 11.A's separate extension ledger (``src/extensions.py``) was removed
on 2026-09-15 as a second answer to the question this module and
``src/services/evolution/`` already answer; a category nobody has named yet
joins this lifecycle by registering a handler
(``src/services/evolution/handlers.py::register_handler``). This governs
the system *changing something* - a prompt, a retrieval strategy, a
routing rule, a parameter, a newly registered component - never by
rewriting itself unsupervised.

**Phase 11 names, mapped onto what already existed rather than rebuilt:**

    Upgrade Manifest      an ``EvolutionProposal`` - identity, target,
                          current and desired version, dependencies,
                          compatibility requirements (``requires``),
                          migration description, risk, provenance, and the
                          rollback target (its own ``current_version``).
                          Not ``src/control_center/manifest.py``'s
                          ``UpgradeManifest``, which describes what is
                          *installed*, not a change.
    Upgrade Planner       ``plan_proposal`` below - deterministic, never a
                          model's opinion.
    Governed Executor     ``src/services/evolution/ecosystem_evolution.py``
                          ``run``/``rollback``, over this module's gates.

    OBSERVE / IDENTIFY   outside this module - Alfred's Brain, or a person,
                         noticing something worth changing. This module
                         starts at the artifact that noticing produces.
         |
         v
    PROPOSE              EvolutionProposal - a description, never code
         |
         v
    EVALUATE             evaluate_proposal()  - deterministic, governed
         |
         v
    APPROVE               an explicit, external decision - never made here
         |
         v
    APPLY                 apply_proposal()    - record it, only if approved
         |
         v
    TEST / VERIFY          verify_proposal()   - independently re-check
         |
         v
    ACTIVATE               activate_proposal() - only after verification
         |
         v
    MONITOR                outside this module - watching an ACTIVE
                           proposal over time is an operational concern,
                           not a governance state this ledger computes
         |
         v
    ROLL BACK (if needed)  rollback_proposal() - governed, explicit,
                           restores this ledger's own record; never a
                           filesystem or code rollback

**The one sentence this whole module exists to enforce:** "Alfred thinks
this is better" is a proposal, never an authorization. Nothing in this
file can move a proposal from PROPOSED toward ACTIVE without an explicit,
externally-supplied ``approved=True`` at the APPROVE step, and nothing can
reach ACTIVE without ``verify_proposal`` independently confirming the
applied change actually landed - never by trusting ``apply_proposal``'s
own report. Both gates are load-bearing, not decorative; this module's own
tests mutate each one and confirm the suite notices.

**"Test" and "Independently Verify" are one governed check, not two.**
Nothing in this module runs a test suite, a linter, or any code at all -
that would be exactly the "arbitrary execution" this stage was told not
to build. ``EvolutionLedger`` is where a caller records what was
independently *observed* after applying a change (real test results,
real monitoring, a real re-read of the changed component's actual state -
however that caller obtained it, outside this module); ``verify_proposal``
is this module's own re-check that what was observed actually matches
what the proposal claimed it would produce, never a restatement of
``apply_proposal.success``.

**A generic contract, not a fixed set of components.** ``target`` is a
plain string, the same reason ``Extension.kind`` is in Stage 11.A and for
the same requirement here: a proposal to change ``brain.reasoning_prompt``
and one to change ``memory.retrieval_strategy`` travel through the
identical evaluate/approve/apply/verify/activate path, and nothing in
that path branches on which one it is. ``risk_level`` is recorded for
whoever approves to see and is never consulted by this module to auto-
approve or auto-reject anything - a risk score deciding for itself would
be the same overreach this whole stage exists to prevent, in a new shape.

**Isolation is structural, not a convention.** This module imports
nothing from ``src.manager``, ``src.services.memory``,
``src.services.identity``, ``src.channels``, ``src.providers``,
``src.control_center``, or ``src.extensions``. It has never held a
reference to a Brain, a Memory, a VoiceProfile, a Personality, a
specialist identity, a visual environment, an LLM provider, or a speech
provider - so an evolution proposal changing, replacing or rolling back
cannot touch any of them, structurally, regardless of what a proposal's
own ``target`` string says it is about. Wiring an ACTIVE proposal's
change into the real, running component it names remains exactly what
wiring a new Stage 11.A extension is: a reviewed, explicit, human-made
change elsewhere - this ledger governs whether that's authorised and
verified, never performs it.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from enum import Enum
from typing import Mapping


class RiskLevel(str, Enum):
    """Descriptive only - see this module's own docstring for why nothing
    here ever branches on a proposal's risk level to decide anything."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class EvolutionStatus(str, Enum):
    """Nine states, the exact set this stage asked for - each one a fact
    about where one proposal is, never a judgement this module reaches on
    its own about whether it *should* move further."""

    PROPOSED = "proposed"
    EVALUATED = "evaluated"
    APPROVED = "approved"
    APPLIED = "applied"
    VERIFIED = "verified"
    ACTIVE = "active"
    REJECTED = "rejected"
    FAILED = "failed"
    ROLLED_BACK = "rolled_back"


@dataclass(frozen=True)
class EvolutionProposal:
    """One proposed change to something that already exists. A
    description, never an instruction to run anything - ``proposed_change``
    is prose recorded for a human to read, not code this module (or
    anything downstream of it) ever executes.

    ``target`` names what would change - "brain.reasoning_prompt",
    "memory.retrieval_strategy", a future category nobody has named yet -
    open, exactly as ``Extension.kind`` is in Stage 11.A. ``dependencies``
    names other proposals' *ids* that must already be ACTIVE, never a
    package name or a Stage 11.A extension key - this module does not
    reach into that ledger, by design (see the module docstring).
    """

    proposal_id: str
    target: str
    current_version: str
    proposed_version: str
    proposed_change: str
    reason: str
    expected_benefit: str
    risk_level: RiskLevel = RiskLevel.LOW
    dependencies: tuple[str, ...] = ()
    status: EvolutionStatus = EvolutionStatus.PROPOSED
    # Phase 11: compatibility requirements - other components that must be
    # at an exact recorded version for this change to be safe, as
    # ``(component_id, version)`` pairs (a mapping is accepted and
    # normalised). Versions are compared exactly, never ordered: each
    # component's version is its own handler's opaque fact (a content hash,
    # a model name, a count), so "at least" has no meaning this module
    # could invent.
    requires: "tuple[tuple[str, str], ...]" = ()
    # Where the proposal came from (a door, a person, a tool) - recorded,
    # never consulted to decide anything.
    provenance: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "dependencies", tuple(self.dependencies))
        pairs = self.requires.items() if isinstance(self.requires, Mapping) else self.requires
        object.__setattr__(self, "requires", tuple((str(c), str(v)) for c, v in pairs))

    def requirements(self) -> "dict[str, str]":
        return dict(self.requires)

    def to_dict(self) -> dict:
        return {
            "proposal_id": self.proposal_id,
            "target": self.target,
            "current_version": self.current_version,
            "proposed_version": self.proposed_version,
            "proposed_change": self.proposed_change,
            "reason": self.reason,
            "expected_benefit": self.expected_benefit,
            "risk_level": self.risk_level.value,
            "dependencies": list(self.dependencies),
            "status": self.status.value,
            "requires": dict(self.requires),
            "provenance": self.provenance,
        }


@dataclass(frozen=True)
class EvolutionLedger:
    """What is independently known to be true right now: each target's
    actual current version, and which proposals are recorded active.

    This is what ``verify_proposal`` checks against - never a proposal's
    own claim, never ``apply_proposal``'s own report. A caller owns how
    ``current_versions`` gets its real values (reading the actual changed
    component's state, a real test result, real monitoring evidence);
    this module only compares, never observes anything itself. Immutable
    and, like Stage 11.A's ``ExtensionLedger``, not persisted by this
    module - a caller that needs this to survive a restart owns that
    store and that decision.
    """

    current_versions: Mapping[str, str] = field(default_factory=dict)
    active_proposals: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "current_versions", dict(self.current_versions))
        object.__setattr__(self, "active_proposals", tuple(self.active_proposals))

    def version_of(self, target: str) -> str | None:
        return self.current_versions.get(target)

    def is_proposal_active(self, proposal_id: str) -> bool:
        return proposal_id in self.active_proposals

    def with_applied_change(self, proposal: "EvolutionProposal") -> "EvolutionLedger":
        """A new ledger recording ``proposal``'s target at its proposed
        version, and the proposal itself as active - what APPLY writes,
        for VERIFY to independently re-read afterward."""
        versions = dict(self.current_versions)
        versions[proposal.target] = proposal.proposed_version
        active = tuple(p for p in self.active_proposals if p != proposal.proposal_id) + (
            proposal.proposal_id,
        )
        return EvolutionLedger(current_versions=versions, active_proposals=active)

    def with_rolled_back_change(self, proposal: "EvolutionProposal") -> "EvolutionLedger":
        """A new ledger with ``proposal``'s target restored to the version
        it recorded *before* the change, and the proposal no longer
        active. Ledger bookkeeping only - never a filesystem or code
        rollback, which this stage was explicitly told not to build."""
        versions = dict(self.current_versions)
        versions[proposal.target] = proposal.current_version
        active = tuple(p for p in self.active_proposals if p != proposal.proposal_id)
        return EvolutionLedger(current_versions=versions, active_proposals=active)

    def to_dict(self) -> dict:
        return {
            "current_versions": dict(self.current_versions),
            "active_proposals": list(self.active_proposals),
        }


@dataclass(frozen=True)
class ValidationResult:
    valid: bool
    reason: str


def validate_proposal(proposal: EvolutionProposal) -> ValidationResult:
    """The proposal's own shape - nothing about the environment it might
    apply to, the same separation ``extensions.validate_extension``
    already draws between "is this well-formed" and "is this reachable
    from here"."""
    for label, value in (
        ("proposal_id", proposal.proposal_id),
        ("target", proposal.target),
        ("current_version", proposal.current_version),
        ("proposed_version", proposal.proposed_version),
        ("proposed_change", proposal.proposed_change),
        ("reason", proposal.reason),
        ("expected_benefit", proposal.expected_benefit),
    ):
        if not value.strip():
            return ValidationResult(False, f"a proposal must have a non-blank {label}")
    if proposal.current_version == proposal.proposed_version:
        return ValidationResult(False, "a proposal must actually change the version of its target")
    if proposal.proposal_id in proposal.dependencies:
        return ValidationResult(False, "a proposal cannot depend on itself")
    problem = requirement_problem(proposal)
    if problem is not None:
        return ValidationResult(False, problem)
    return ValidationResult(True, "well-formed")


def requirement_problem(proposal: EvolutionProposal) -> "str | None":
    """Why a proposal's compatibility requirements are malformed, or None."""
    for component_id, version in proposal.requires:
        if not component_id.strip() or not version.strip():
            return "a compatibility requirement must name a component and a version"
        if component_id == proposal.target:
            return "a proposal cannot require its own target - its current_version already says that"
    return None


def unmet_requirements(proposal: EvolutionProposal, *, ledger: EvolutionLedger) -> "list[str]":
    """Every compatibility requirement the ledger's independent record does
    not satisfy, in plain words. A component the ledger has no version for
    is unmet too: compatibility that cannot be established is never
    assumed."""
    unmet = []
    for component_id, version in proposal.requires:
        observed = ledger.version_of(component_id)
        if observed is None:
            unmet.append(f"'{component_id}' has no recorded version, so compatibility with {version!r} cannot be established")
        elif observed != version:
            unmet.append(f"requires '{component_id}' at {version!r}; it is at {observed!r}")
    return unmet


@dataclass(frozen=True)
class EvaluationResult:
    outcome: EvolutionStatus
    reason: str
    proposal: EvolutionProposal


def evaluate_proposal(proposal: EvolutionProposal, *, ledger: EvolutionLedger) -> EvaluationResult:
    """EVALUATE: deterministic and governed - well-formedness, dependency
    satisfaction, and that the proposal's own claim about the target's
    *current* version still matches the ledger's independent record.
    Never runs code, never asks a model's opinion, never consults
    ``risk_level`` to decide anything on its own.
    """
    if proposal.status != EvolutionStatus.PROPOSED:
        return EvaluationResult(
            proposal.status,
            f"cannot evaluate a proposal in status {proposal.status.value}; it must be PROPOSED",
            proposal,
        )

    validity = validate_proposal(proposal)
    if not validity.valid:
        return EvaluationResult(
            EvolutionStatus.REJECTED, validity.reason, replace(proposal, status=EvolutionStatus.REJECTED)
        )

    missing = [dep for dep in proposal.dependencies if not ledger.is_proposal_active(dep)]
    if missing:
        return EvaluationResult(
            EvolutionStatus.REJECTED,
            f"depends on proposal(s) not currently active: {', '.join(missing)}",
            replace(proposal, status=EvolutionStatus.REJECTED),
        )

    incompatible = unmet_requirements(proposal, ledger=ledger)
    if incompatible:
        return EvaluationResult(
            EvolutionStatus.REJECTED,
            "incompatible: " + "; ".join(incompatible),
            replace(proposal, status=EvolutionStatus.REJECTED),
        )

    observed = ledger.version_of(proposal.target)
    if observed is not None and observed != proposal.current_version:
        return EvaluationResult(
            EvolutionStatus.REJECTED,
            f"'{proposal.target}' is at {observed!r} in the ledger, not the "
            f"{proposal.current_version!r} this proposal assumes",
            replace(proposal, status=EvolutionStatus.REJECTED),
        )

    return EvaluationResult(
        EvolutionStatus.EVALUATED,
        "well-formed, dependencies satisfied, current version confirmed",
        replace(proposal, status=EvolutionStatus.EVALUATED),
    )


@dataclass(frozen=True)
class ApprovalResult:
    success: bool
    reason: str
    proposal: EvolutionProposal


def approve_proposal(proposal: EvolutionProposal, *, approved: bool) -> ApprovalResult:
    """APPROVE: the one place consent is recorded. ``approved`` has no
    default - a caller must decide and say so, every call. Evaluation
    finding a proposal beneficial is never treated as approval; only this
    explicit flag is.
    """
    if proposal.status != EvolutionStatus.EVALUATED:
        return ApprovalResult(
            False, f"cannot approve a proposal in status {proposal.status.value}; it must be EVALUATED", proposal
        )
    if not approved:
        return ApprovalResult(False, "not approved", proposal)
    return ApprovalResult(True, "approved", replace(proposal, status=EvolutionStatus.APPROVED))


@dataclass(frozen=True)
class ApplyResult:
    success: bool
    reason: str | None
    proposal: EvolutionProposal
    ledger: EvolutionLedger


def apply_proposal(
    proposal: EvolutionProposal, *, approved: bool, ledger: EvolutionLedger
) -> ApplyResult:
    """APPLY: record the change in the ledger - only for an APPROVED
    proposal, and only when ``approved`` is True again here, exactly as
    ``execute_upgrade``/``apply_extension`` re-check consent at the point
    that actually acts rather than trusting an earlier call's decision.

    This never touches a real Brain, Memory, VoiceProfile, or any other
    live component - see the module docstring for why that is
    structurally true, not merely intended. Wiring the actual change into
    whatever ``target`` names remains a separate, reviewed action.
    """
    if proposal.status != EvolutionStatus.APPROVED:
        return ApplyResult(
            False,
            f"cannot apply a proposal in status {proposal.status.value}; it must be APPROVED",
            proposal,
            ledger,
        )
    if not approved:
        return ApplyResult(False, "not approved", proposal, ledger)

    applied = replace(proposal, status=EvolutionStatus.APPLIED)
    return ApplyResult(True, None, applied, ledger.with_applied_change(applied))


def verify_proposal(proposal: EvolutionProposal, *, ledger: EvolutionLedger) -> tuple[bool, str | None]:
    """TEST / VERIFY, combined: independently re-check the ledger - never
    a restatement of ``ApplyResult.success`` - to confirm the target
    actually landed at the proposed version and the proposal is recorded
    active. This is the exact "the operation reported success" vs "the
    system is correct" distinction ``upgrade_lifecycle.py`` and Stage
    11.A's ``verify_extension_activation`` already draw, applied here.
    """
    if proposal.status != EvolutionStatus.APPLIED:
        return False, f"cannot verify a proposal in status {proposal.status.value}; it must be APPLIED"
    observed = ledger.version_of(proposal.target)
    if observed != proposal.proposed_version:
        return False, (
            f"'{proposal.target}' is at {observed!r} in the ledger, expected {proposal.proposed_version!r}"
        )
    if not ledger.is_proposal_active(proposal.proposal_id):
        return False, f"'{proposal.proposal_id}' is not recorded as active in the ledger"
    return True, None


@dataclass(frozen=True)
class ActivationResult:
    success: bool
    reason: str
    proposal: EvolutionProposal


def activate_proposal(proposal: EvolutionProposal, *, ledger: EvolutionLedger) -> ActivationResult:
    """ACTIVATE: the final step, and the only one that may set a proposal
    to ACTIVE - and only after ``verify_proposal`` independently confirms
    the applied change, never merely because APPLY reported success.
    """
    if proposal.status != EvolutionStatus.APPLIED:
        return ActivationResult(
            False, f"cannot activate a proposal in status {proposal.status.value}; it must be APPLIED", proposal
        )

    verified, reason = verify_proposal(proposal, ledger=ledger)
    if not verified:
        return ActivationResult(False, reason or "verification failed", replace(proposal, status=EvolutionStatus.FAILED))

    return ActivationResult(True, "independently verified and activated", replace(proposal, status=EvolutionStatus.ACTIVE))


@dataclass(frozen=True)
class RollbackResult:
    success: bool
    reason: str
    proposal: EvolutionProposal
    ledger: EvolutionLedger


def rollback_proposal(
    proposal: EvolutionProposal, *, approved: bool, ledger: EvolutionLedger
) -> RollbackResult:
    """ROLL BACK: only an ACTIVE proposal may be rolled back, and only
    with explicit approval - rolling back is a governed decision exactly
    as applying one is, never automatic. Restores this ledger's own
    record of the target's version; never touches a filesystem, a
    running process, or any code - the exact "no speculative arbitrary
    filesystem rollback" boundary this stage was told to respect.
    """
    if proposal.status != EvolutionStatus.ACTIVE:
        return RollbackResult(
            False, f"cannot roll back a proposal in status {proposal.status.value}; it must be ACTIVE", proposal, ledger
        )
    if not approved:
        return RollbackResult(False, "not approved", proposal, ledger)
    recorded = ledger.version_of(proposal.target)
    if recorded != proposal.proposed_version:
        # A later change now holds this target: restoring this proposal's
        # prior version would silently undo that one while it still claims
        # to be active. Roll the newest change back first.
        return RollbackResult(
            False,
            f"'{proposal.target}' is at {recorded!r}, not the {proposal.proposed_version!r} this proposal "
            "applied - a later change holds it; roll that back first",
            proposal, ledger,
        )

    rolled_back = replace(proposal, status=EvolutionStatus.ROLLED_BACK)
    return RollbackResult(True, "rolled back", rolled_back, ledger.with_rolled_back_change(rolled_back))


@dataclass(frozen=True)
class EvolutionLifecycleResult:
    """One end-to-end run's whole answer, for the common case where
    evaluation, approval, application and verification happen in a
    single, immediate decision. The individual stage functions above are
    the primary API for the slower, real-world case - approve today,
    apply next week, monitor, decide on a rollback a month later - this
    is a convenience for the fast path and for tests, the same role
    ``run_extension_lifecycle`` plays in Stage 11.A.
    """

    status: EvolutionStatus
    success: bool
    approved: bool
    applied: bool
    verified: bool | None
    activated: bool
    reason: str
    proposal: EvolutionProposal
    before_ledger: EvolutionLedger
    after_ledger: EvolutionLedger

    def to_dict(self) -> dict:
        return {
            "status": self.status.value,
            "success": self.success,
            "approved": self.approved,
            "applied": self.applied,
            "verified": self.verified,
            "activated": self.activated,
            "reason": self.reason,
            "proposal": self.proposal.to_dict(),
            "before_ledger": self.before_ledger.to_dict(),
            "after_ledger": self.after_ledger.to_dict(),
        }


def run_evolution_lifecycle(
    proposal: EvolutionProposal, *, approved: bool, ledger: EvolutionLedger
) -> EvolutionLifecycleResult:
    """EVALUATE -> the APPROVE gate -> APPLY -> VERIFY -> ACTIVATE, in one
    call. ``approved`` has no default - there is no path here that
    supplies consent on a caller's behalf.
    """
    evaluation = evaluate_proposal(proposal, ledger=ledger)
    if evaluation.outcome != EvolutionStatus.EVALUATED:
        return EvolutionLifecycleResult(
            status=evaluation.outcome, success=False, approved=approved, applied=False,
            verified=None, activated=False, reason=evaluation.reason, proposal=evaluation.proposal,
            before_ledger=ledger, after_ledger=ledger,
        )

    approval = approve_proposal(evaluation.proposal, approved=approved)
    if not approval.success:
        return EvolutionLifecycleResult(
            status=approval.proposal.status, success=False, approved=approved, applied=False,
            verified=None, activated=False, reason=approval.reason, proposal=approval.proposal,
            before_ledger=ledger, after_ledger=ledger,
        )

    application = apply_proposal(approval.proposal, approved=approved, ledger=ledger)
    if not application.success:
        return EvolutionLifecycleResult(
            status=application.proposal.status, success=False, approved=approved, applied=False,
            verified=None, activated=False, reason=application.reason or "apply failed",
            proposal=application.proposal, before_ledger=ledger, after_ledger=application.ledger,
        )

    activation = activate_proposal(application.proposal, ledger=application.ledger)
    return EvolutionLifecycleResult(
        status=activation.proposal.status,
        success=activation.success,
        approved=approved,
        applied=True,
        verified=activation.success,
        activated=activation.success,
        reason=activation.reason,
        proposal=activation.proposal,
        before_ledger=ledger,
        after_ledger=application.ledger,
    )


# ---------------------------------------------------------------------------
# Phase 11 - the Upgrade Planner, over the same gates the executor runs.
# ---------------------------------------------------------------------------

_OPEN_STATUSES = (EvolutionStatus.PROPOSED, EvolutionStatus.EVALUATED, EvolutionStatus.APPROVED)


@dataclass(frozen=True)
class EvolutionPlan:
    """Whether one proposal can safely run *now*, and what stands in the way.

    Deterministic and read-only: the same ledger, proposals and observation
    always give the same plan, and nothing here changes anything. It asks
    the same questions ``evaluate_proposal`` asks when the change actually
    runs, so a plan can never promise what the executor will refuse, plus
    the ones only a planner can answer ahead of time: the order unmet
    prerequisites must run in, other open changes this would make stale,
    active changes that were approved against the version this replaces,
    whether the real state still matches the record, and whether the
    change can be rolled back.
    """

    proposal_id: str
    target: str
    from_version: str
    to_version: str
    can_proceed: bool
    blockers: tuple[str, ...]
    warnings: tuple[str, ...]
    prerequisites: tuple[str, ...]
    order: tuple[str, ...]
    affected: tuple[str, ...]
    rollback: str

    def to_dict(self) -> dict:
        return {
            "proposal_id": self.proposal_id,
            "target": self.target,
            "from_version": self.from_version,
            "to_version": self.to_version,
            "can_proceed": self.can_proceed,
            "blockers": list(self.blockers),
            "warnings": list(self.warnings),
            "prerequisites": list(self.prerequisites),
            "order": list(self.order),
            "affected": list(self.affected),
            "rollback": self.rollback,
        }

    def describe(self) -> str:
        lines = [
            f"PLAN {self.proposal_id}: {self.target} {self.from_version} -> {self.to_version}",
            f"  can proceed : {'yes' if self.can_proceed else 'no'}",
        ]
        for label, items in (("blocked by", self.blockers), ("warnings", self.warnings),
                             ("prerequisites", self.prerequisites)):
            if items:
                lines.append(f"  {label}:")
                lines.extend(f"    - {item}" for item in items)
        lines.append(f"  run order   : {' -> '.join(self.order)}")
        lines.append(f"  affects     : {', '.join(self.affected)}")
        lines.append(f"  rollback    : {self.rollback}")
        return "\n".join(lines)


def _dependency_order(
    proposal: EvolutionProposal, proposals: "Mapping[str, EvolutionProposal]", ledger: EvolutionLedger
) -> "tuple[tuple[str, ...], list[str]]":
    """The open prerequisites, deepest first, then ``proposal`` itself - and
    every problem found on the way (an unknown, closed or circular
    dependency). Active dependencies are already satisfied and not listed."""
    order: list[str] = []
    problems: list[str] = []
    visiting: set[str] = set()

    def visit(current: EvolutionProposal, path: tuple[str, ...]) -> None:
        for dep in current.dependencies:
            if ledger.is_proposal_active(dep) or dep in order:
                continue
            if dep in path or dep in visiting:
                problems.append(f"circular dependency: {' -> '.join(path + (dep,))}")
                continue
            prerequisite = proposals.get(dep)
            if prerequisite is None:
                problems.append(f"depends on '{dep}', which does not exist")
                continue
            if prerequisite.status not in _OPEN_STATUSES:
                problems.append(f"depends on '{dep}', which is {prerequisite.status.value} and can never become active")
                continue
            visiting.add(dep)
            visit(prerequisite, path + (dep,))
            visiting.discard(dep)
            order.append(dep)

    visit(proposal, (proposal.proposal_id,))
    return tuple(order) + (proposal.proposal_id,), problems


def plan_proposal(
    proposal: EvolutionProposal,
    *,
    ledger: EvolutionLedger,
    proposals: "Mapping[str, EvolutionProposal]",
    has_handler: bool,
    can_roll_back: bool,
    observed_version: "str | None" = None,
) -> EvolutionPlan:
    """PLAN: can ``proposal`` safely run now? ``has_handler``/``can_roll_back``
    and ``observed_version`` (the target's real current state, read by its
    own handler, or None when it cannot say) come from the caller, so this
    stays pure: it never reads a file, imports a handler, or asks a model.
    """
    blockers: list[str] = []
    warnings: list[str] = []
    prerequisites: list[str] = []

    if not has_handler:
        blockers.append(f"no evolution handler owns '{proposal.target}', so nothing could apply it")
    if proposal.status != EvolutionStatus.PROPOSED:
        blockers.append(f"it is already {proposal.status.value}; only a PROPOSED change can run")

    validity = validate_proposal(proposal)
    if not validity.valid:
        blockers.append(validity.reason)

    order, problems = _dependency_order(proposal, proposals, ledger)
    blockers.extend(problems)
    for dep in proposal.dependencies:
        satisfied = ledger.is_proposal_active(dep)
        prerequisites.append(f"proposal '{dep}' active - {'yes' if satisfied else 'not yet'}")
    unmet_first = [dep for dep in order[:-1]]
    if unmet_first:
        blockers.append(f"run {', '.join(unmet_first)} first (in that order)")

    for component_id, version in proposal.requires:
        observed = ledger.version_of(component_id)
        prerequisites.append(
            f"'{component_id}' at {version!r} - "
            + ("yes" if observed == version else f"no (recorded: {observed!r})")
        )
    blockers.extend(unmet_requirements(proposal, ledger=ledger))

    recorded = ledger.version_of(proposal.target)
    if recorded is not None and recorded != proposal.current_version:
        blockers.append(
            f"'{proposal.target}' is recorded at {recorded!r}, not the {proposal.current_version!r} "
            "this change starts from - it is stale"
        )
    if observed_version is not None and observed_version != proposal.current_version:
        blockers.append(
            f"the real state of '{proposal.target}' is {observed_version!r}, not the "
            f"{proposal.current_version!r} on record - it was changed outside governance"
        )

    stale = sorted(
        other.proposal_id for other in proposals.values()
        if other.proposal_id != proposal.proposal_id and other.target == proposal.target
        and other.status in _OPEN_STATUSES and other.current_version == proposal.current_version
    )
    if stale:
        warnings.append(f"running this makes {', '.join(stale)} stale (they start from the same version)")

    holders = {
        p.proposal_id for p in proposals.values()
        if p.target == proposal.target and ledger.is_proposal_active(p.proposal_id)
    }
    affected = [proposal.target]
    for other in sorted(proposals.values(), key=lambda p: p.proposal_id):
        if ledger.is_proposal_active(other.proposal_id) and holders.intersection(other.dependencies):
            warnings.append(
                f"'{other.proposal_id}' ({other.target}) was approved on top of "
                f"{', '.join(sorted(holders.intersection(other.dependencies)))}, whose version this replaces"
            )
            if other.target not in affected:
                affected.append(other.target)
        if ledger.is_proposal_active(other.proposal_id) and dict(other.requires).get(proposal.target) == proposal.current_version:
            warnings.append(
                f"'{other.proposal_id}' ({other.target}) requires '{proposal.target}' at "
                f"{proposal.current_version!r}, which this replaces"
            )
            if other.target not in affected:
                affected.append(other.target)

    if proposal.risk_level in (RiskLevel.HIGH, RiskLevel.CRITICAL):
        warnings.append(f"declared {proposal.risk_level.value} risk (recorded for whoever approves; never decides)")

    if can_roll_back:
        rollback = f"governed rollback restores '{proposal.target}' to {proposal.current_version!r} through its handler"
    else:
        rollback = "none - this change could not be undone through governance"
        warnings.append("no rollback is available for this change")

    return EvolutionPlan(
        proposal_id=proposal.proposal_id,
        target=proposal.target,
        from_version=proposal.current_version,
        to_version=proposal.proposed_version,
        can_proceed=not blockers,
        blockers=tuple(blockers),
        warnings=tuple(warnings),
        prerequisites=tuple(prerequisites),
        order=order,
        affected=tuple(affected),
        rollback=rollback,
    )
