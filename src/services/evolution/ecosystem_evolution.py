"""Full Alfred OS Ecosystem Evolution - the governed engine that wires
``src.evolution``'s pure, isolated state machine to the real, currently
installed ecosystem (``inventory.py``) and to real, component-specific
side effects (``handlers.py``) - never a second credential path, never
a fabricated integration.

**Why this file exists at all.** ``src/evolution.py``'s own docstring is
explicit about the boundary it holds: it "never touches a real Brain,
Memory, VoiceProfile, or any other live component... Wiring an ACTIVE
proposal's change into the real, running component it names remains...
a reviewed, explicit change elsewhere." This module *is* that reviewed,
explicit elsewhere.

**The engine stays generic; components supply their own behavior.**
This module never branches on a category string to decide what "apply"
means - that would be exactly the "fake generic apply anything" this
checkpoint's own brief forbids in the other direction (one function
pretending to know how to change everything) and exactly the
"hard-coded permanent catalog" its Section 11 forbids in this one (the
engine itself naming every category it will ever support). Instead,
``handlers.handler_for(component_id)`` looks up the one real,
component-specific ``EvolutionHandler`` that owns a given id; if none
is registered, the engine refuses honestly, by name - it does not
guess, and it does not silently no-op. A future component participates
by registering one handler (``handlers.register_handler``), never by
editing this file.

**Two, deliberately different, kinds of evolution - never conflated:**

    Component upgrade / replacement   this module's own propose/run/
                                       rollback pipeline, backed by
                                       ``src.evolution``'s governed
                                       ledger, executed by whichever
                                       handler owns the target.

    Development / maturation          never routed through this
                                       pipeline at all. Memory
                                       (``inventory.memory_development_snapshot``)
                                       and Brain development
                                       (the same snapshot, read through
                                       the ``ecosystem brain`` command)
                                       report real, qualitative facts -
                                       never a fabricated maturity score
                                       standing in for a version number
                                       that does not exist for this kind
                                       of component.

**Governed, not autonomous.** Every step below requires an explicit,
externally supplied decision (``approved=True``/``False``, with no
default anywhere) and persists the result through ``ledger_store`` so a
restart never resets what was decided.

**No destructive coupling.** Applying or rolling back one component
touches only that component's own ledger entry, whatever real state its
own handler owns, and the shared history log (append-only) - never
another component's version, another provider's account, or another
connection's credential.
"""

from __future__ import annotations

from dataclasses import dataclass, replace as dc_replace
from pathlib import Path

from src.evolution import (
    EvolutionLedger,
    EvolutionProposal,
    EvolutionStatus,
    RiskLevel,
    EvolutionPlan,
    activate_proposal,
    apply_proposal,
    approve_proposal,
    evaluate_proposal,
    plan_proposal,
    requirement_problem,
    rollback_proposal,
    verify_proposal,
)
from src.services.credentials.account_store_file import DEFAULT_ACCOUNTS_PATH
from src.services.evolution.governed_config import DEFAULT_CONFIG_PATH
from src.services.evolution.handlers import HandlerContext, HandlerResult, handler_for
from src.services.evolution.inventory import ComponentSnapshot
from src.services.evolution.ledger_store import (
    DEFAULT_LEDGER_PATH,
    EvolutionState,
    HistoryEntry,
    load_evolution_state,
    save_evolution_state,
    utc_now,
)

DEFAULT_STAGING_DIR = Path("data") / "evolution" / "specialist_staging"
DEFAULT_BACKUPS_DIR = Path("data") / "evolution" / "specialist_backups"


@dataclass(frozen=True)
class StepResult:
    """One governed step's honest answer: whether it succeeded, why (or
    why not), the resulting proposal, and the persisted state to keep
    using for the next call.
    """

    success: bool
    reason: str
    proposal: "EvolutionProposal | None"
    state: EvolutionState


def _context(settings, *, accounts_path=DEFAULT_ACCOUNTS_PATH, config_path=DEFAULT_CONFIG_PATH,
             staging_dir=DEFAULT_STAGING_DIR, backups_dir=DEFAULT_BACKUPS_DIR) -> HandlerContext:
    return HandlerContext(
        settings=settings, accounts_path=accounts_path, config_path=config_path,
        staging_dir=staging_dir, backups_dir=backups_dir,
    )


def _record(state: EvolutionState, component_id: str, proposal_id: str, from_status: str, to_status: str, detail: str) -> EvolutionState:
    return state.with_history(
        HistoryEntry(
            timestamp=utc_now().isoformat(),
            component_id=component_id,
            proposal_id=proposal_id,
            from_status=from_status,
            to_status=to_status,
            detail=detail,
        )
    )


def _persist(state: EvolutionState, path) -> EvolutionState:
    save_evolution_state(state, path)
    return state


def seed_component_versions(state: EvolutionState, components: "tuple[ComponentSnapshot, ...]") -> EvolutionState:
    """Every real component discovered that the ledger has never heard
    of before is recorded at its own real, current version - never a
    fabricated starting point, and never overwriting a version the
    ledger already independently tracks (which would erase a prior
    governed change the moment the component was re-discovered).
    """
    versions = dict(state.ledger.current_versions)
    changed = False
    for component in components:
        if component.component_id not in versions:
            versions[component.component_id] = component.version
            changed = True
    if not changed:
        return state
    return state.with_ledger(EvolutionLedger(current_versions=versions, active_proposals=state.ledger.active_proposals))


@dataclass(frozen=True)
class RecordDrift:
    """One place the ledger's record disagrees with what is installed.

    ``real`` is None when nothing by this id is installed now; a legacy id
    whose component is recorded under its canonical id says so in
    ``superseded_by``."""

    component_id: str
    recorded: str
    real: "str | None"
    superseded_by: str = ""

    def describe(self) -> str:
        if self.superseded_by:
            return (f"{self.component_id}: a legacy record ('{self.recorded}') - this component is "
                    f"recorded as '{self.superseded_by}' now")
        if self.real is None:
            return f"{self.component_id}: recorded '{self.recorded}', but nothing by this id is installed now"
        return f"{self.component_id}: recorded '{self.recorded}', really '{self.real}'"


def record_drift(state: EvolutionState, components: "tuple[ComponentSnapshot, ...]") -> "tuple[RecordDrift, ...]":
    """Every ledger record that disagrees with the discovered, installed
    component - read only, nothing changed.

    Phase 11 closure: drift was found only when a proposal on that component
    was planned, so a record nothing proposes against (a store's schema
    version migrated outside the ledger, an identity document edited, a
    fixture left behind) stayed wrong and invisible. The inventory now lists
    every disagreement; ``reconcile`` is the governed way to correct one."""
    from src.services.evolution.inventory import MANAGER_COMPONENT_ID, is_manager_component

    real = {c.component_id: str(c.version) for c in components}
    drift: list[RecordDrift] = []
    for component_id, recorded in sorted(state.ledger.current_versions.items()):
        if component_id in real:
            if real[component_id] != str(recorded):
                drift.append(RecordDrift(component_id, str(recorded), real[component_id]))
        elif component_id != MANAGER_COMPONENT_ID and is_manager_component(component_id) and MANAGER_COMPONENT_ID in real:
            drift.append(RecordDrift(component_id, str(recorded), None, superseded_by=MANAGER_COMPONENT_ID))
        else:
            drift.append(RecordDrift(component_id, str(recorded), None))
    return tuple(drift)


def reconcile(
    state: EvolutionState,
    *,
    component_id: str,
    real_version: "str | None",
    approved: bool,
    ledger_path=DEFAULT_LEDGER_PATH,
) -> StepResult:
    """RECONCILE: make the ledger's record of ``component_id`` say what is
    really installed - the record only, never the component.

    For the drift ``record_drift`` finds on a component no active change
    holds. Refused when an active change holds it (that change's own governed
    rollback restores what it changed - the way P11-G-13 was resolved), when
    nothing by that id is installed (a record cannot be pointed at nothing),
    when the record already matches, and without explicit approval. A
    reconciliation is recorded in the history like every other governed step."""
    recorded = state.ledger.version_of(component_id)
    if recorded is None:
        return StepResult(False, f"the ledger holds no record of '{component_id}' to reconcile", None, state)
    if real_version is None:
        return StepResult(
            False, f"nothing by the id '{component_id}' is installed now, so there is no real version to record",
            None, state,
        )
    if str(recorded) == str(real_version):
        return StepResult(False, f"the record of '{component_id}' already matches what is installed ('{recorded}')", None, state)
    holding = sorted(
        p.proposal_id for p in state.proposals.values()
        if p.target == component_id and state.ledger.is_proposal_active(p.proposal_id)
    )
    if holding:
        return StepResult(
            False,
            f"'{', '.join(holding)}' is active on '{component_id}' - its own governed rollback restores what it "
            "changed; the record is not overwritten underneath it",
            None, state,
        )
    if not approved:
        return StepResult(False, "not approved - nothing was changed", None, state)
    versions = dict(state.ledger.current_versions)
    versions[component_id] = str(real_version)
    state = state.with_ledger(EvolutionLedger(current_versions=versions, active_proposals=state.ledger.active_proposals))
    detail = (f"record corrected to what is installed: '{recorded}' -> '{real_version}' "
              "(the component itself was not changed)")
    state = _record(state, component_id, "(record)", "recorded", "reconciled", detail)
    state = _persist(state, ledger_path)
    return StepResult(True, f"the record of '{component_id}' now matches what is installed: {detail}", None, state)


def propose(
    state: EvolutionState,
    *,
    component_id: str,
    proposed_version: str,
    proposed_change: str,
    reason: str,
    expected_benefit: str,
    risk_level: RiskLevel = RiskLevel.LOW,
    dependencies: tuple[str, ...] = (),
    proposal_id: "str | None" = None,
    ledger_path=DEFAULT_LEDGER_PATH,
    requires: "dict[str, str] | None" = None,
    provenance: str = "",
) -> StepResult:
    """PROPOSE: record a new, un-evaluated proposal against
    ``component_id``'s own, real, current version - read fresh from the
    ledger (seeded from ``inventory.py`` if this is the first time this
    component has ever been proposed against), never taken on a
    caller's unchecked word. Refuses up front if no handler owns this
    component at all - a proposal against an unsupported category is
    still refused honestly, never silently accepted only to fail later
    at apply.
    """
    handler = handler_for(component_id)
    if handler is None:
        return StepResult(
            False,
            f"no evolution handler is registered for '{component_id}' - this category has no "
            "real, governed way to change yet, so no proposal is recorded against it",
            None, state,
        )

    current_version = state.ledger.version_of(component_id)
    if current_version is None:
        # A category that can genuinely begin from nothing supplies its own
        # starting version (Pre-Stage-9 Campaign 5); every other category
        # still has to be discovered first.
        current_version = handler.initial_version(component_id)
    if current_version is None:
        return StepResult(False, f"'{component_id}' has no known current version yet - discover it first", None, state)

    proposal_id = proposal_id or f"{component_id}-{len(state.proposals) + 1}"
    if proposal_id in state.proposals:
        return StepResult(False, f"a proposal '{proposal_id}' already exists", None, state)

    proposal = EvolutionProposal(
        proposal_id=proposal_id,
        target=component_id,
        current_version=current_version,
        proposed_version=proposed_version,
        proposed_change=proposed_change,
        reason=reason,
        expected_benefit=expected_benefit,
        risk_level=risk_level,
        dependencies=dependencies,
        requires=requires or {},
        provenance=provenance,
    )
    problem = requirement_problem(proposal)
    if problem is not None:
        # Refused before anything is recorded - a malformed compatibility
        # requirement never enters the ledger to be rejected later.
        return StepResult(False, problem, None, state)
    state = state.with_proposal(proposal)
    state = _record(state, component_id, proposal_id, "(none)", proposal.status.value, f"proposed: {current_version} -> {proposed_version}")
    state = _persist(state, ledger_path)
    return StepResult(True, "proposed", proposal, state)


def propose_identity_content(
    state: EvolutionState,
    *,
    component_id: str,
    new_content_path: "str | Path",
    reason: str,
    expected_benefit: str,
    risk_level: RiskLevel = RiskLevel.LOW,
    proposal_id: "str | None" = None,
    ledger_path=DEFAULT_LEDGER_PATH,
    staging_dir=DEFAULT_STAGING_DIR,
) -> StepResult:
    """The specialist/Alfred-identity-specific proposal path: free-form
    markdown prose has nowhere safe to live inside ``EvolutionProposal``'s
    own fixed, short-string schema, so the *real new document content*
    is read from ``new_content_path`` and staged to disk, keyed by the
    proposal id the moment it is assigned, and both
    ``current_version``/``proposed_version`` are real content hashes
    (the identical format ``inventory.py`` already reports for these
    components) - never a fabricated version number.

    Read and staged as raw bytes throughout, deliberately never opened
    in text mode: text mode's newline translation (``\\r\\n`` <->
    ``\\n``) would silently change the bytes on disk between the moment
    a hash is computed and the moment the same content is written back,
    making a real, correctly-written file fail its own independent
    verification purely from a line-ending mismatch - the identical
    "the bytes are the fact" discipline ``inventory._file_version``
    already holds itself to.
    """
    import hashlib

    from src.services.evolution.inventory import is_manager_component

    if handler_for(component_id) is None or not (is_manager_component(component_id) or component_id.startswith("specialist.")):
        return StepResult(False, f"'{component_id}' is not a specialist/identity component", None, state)

    current_version = state.ledger.version_of(component_id)
    if current_version is None:
        return StepResult(False, f"'{component_id}' has no known current version yet - discover it first", None, state)

    new_bytes = Path(new_content_path).read_bytes()
    proposed_version = hashlib.sha256(new_bytes).hexdigest()[:12]
    if proposed_version == current_version:
        return StepResult(False, "the proposed content is byte-for-byte identical to the current document - nothing to evolve", None, state)

    proposal_id = proposal_id or f"{component_id}-{len(state.proposals) + 1}"
    if proposal_id in state.proposals:
        return StepResult(False, f"a proposal '{proposal_id}' already exists", None, state)

    staging_path = Path(staging_dir) / f"{proposal_id}.md"
    staging_path.parent.mkdir(parents=True, exist_ok=True)
    staging_path.write_bytes(new_bytes)

    proposal = EvolutionProposal(
        proposal_id=proposal_id,
        target=component_id,
        current_version=current_version,
        proposed_version=proposed_version,
        proposed_change=f"replace the real identity document content ({current_version} -> {proposed_version})",
        reason=reason,
        expected_benefit=expected_benefit,
        risk_level=risk_level,
    )
    state = state.with_proposal(proposal)
    state = _record(state, component_id, proposal_id, "(none)", proposal.status.value, f"proposed content staged at '{staging_path}'")
    state = _persist(state, ledger_path)
    return StepResult(True, f"proposed (staged content at '{staging_path}')", proposal, state)


@dataclass(frozen=True)
class _Observation:
    """What a handler read: the value it holds (None when nothing is
    stored), the component's real effective version (the stored value, else
    the handler's truthful default, else None when unknowable), and why it
    could not be read at all."""

    stored: "str | None"
    effective: "str | None"
    problem: "str | None" = None


def _observe(handler, component_id: str, context: HandlerContext) -> _Observation:
    """The component's real state as its own handler reads it. Never raises:
    a handler failing to look is itself a fact the engine reports."""
    try:
        stored = handler.observe(component_id, context=context)
    except Exception as exc:  # noqa: BLE001 - an unreadable state is reported, never trusted
        return _Observation(None, None, f"could not read the real state of '{component_id}': {type(exc).__name__}: {exc}")
    return _Observation(stored, stored if stored is not None else handler.default_version(component_id))


def _confirms(handler, component_id: str, seen: _Observation, version: str) -> bool:
    """Whether what was read confirms the component is at ``version``."""
    if seen.problem is not None:
        return False
    if seen.effective == version:
        return True
    return seen.stored is None and handler.absence_restores(component_id, version)


def _call(step, proposal, context) -> HandlerResult:
    """One handler side effect, contained: an exception is a failed step
    with its reason, never an escape that leaves the ledger unrecorded."""
    try:
        return step(proposal, context=context)
    except Exception as exc:  # noqa: BLE001 - any handler failure is recorded and answered
        return HandlerResult(False, f"{type(exc).__name__}: {exc}")


def run(
    state: EvolutionState,
    *,
    proposal_id: str,
    approved: bool,
    settings,
    ledger_path=DEFAULT_LEDGER_PATH,
    accounts_path=DEFAULT_ACCOUNTS_PATH,
    config_path=DEFAULT_CONFIG_PATH,
    staging_dir=DEFAULT_STAGING_DIR,
    backups_dir=DEFAULT_BACKUPS_DIR,
) -> StepResult:
    """The fast path: EVALUATE -> APPROVE -> APPLY (whichever real
    handler owns this target) -> VERIFY -> ACTIVATE, in one governed
    call - the identical convenience role
    ``src.evolution.run_evolution_lifecycle`` already plays, extended
    here to actually perform and independently re-check a real change
    rather than only updating the ledger's own bookkeeping.

    Phase 11: VERIFY reads the component's *real* state through its own
    handler (``observe``) - never the ledger this function just wrote, and
    never the handler's own report - so a change that did not land is never
    activated. The real state is also read *before* applying: a component
    changed outside governance is refused rather than overwritten on a false
    assumption. A handler failure of any kind, including an exception, is
    recorded and persisted, and whatever it left half-done is compensated
    through the handler's own rollback and re-read - the answer says whether
    the prior state was confirmed restored.

    Stops, honestly, at the first stage that does not succeed.
    """
    proposal = state.proposal(proposal_id)
    if proposal is None:
        return StepResult(False, f"no such proposal '{proposal_id}'", None, state)

    before_status = proposal.status.value
    evaluation = evaluate_proposal(proposal, ledger=state.ledger)
    state = state.with_proposal(evaluation.proposal)
    state = _record(state, proposal.target, proposal_id, before_status, evaluation.proposal.status.value, evaluation.reason)
    if evaluation.outcome != EvolutionStatus.EVALUATED:
        state = _persist(state, ledger_path)
        return StepResult(False, evaluation.reason, evaluation.proposal, state)

    approval = approve_proposal(evaluation.proposal, approved=approved)
    state = state.with_proposal(approval.proposal)
    state = _record(state, proposal.target, proposal_id, EvolutionStatus.EVALUATED.value, approval.proposal.status.value, approval.reason)
    if not approval.success:
        state = _persist(state, ledger_path)
        return StepResult(False, approval.reason, approval.proposal, state)

    application = apply_proposal(approval.proposal, approved=approved, ledger=state.ledger)
    if not application.success:
        state = state.with_proposal(application.proposal)
        state = _record(state, proposal.target, proposal_id, EvolutionStatus.APPROVED.value, application.proposal.status.value, application.reason or "apply refused")
        state = _persist(state, ledger_path)
        return StepResult(False, application.reason or "apply refused", application.proposal, state)

    def fail(detail: str) -> StepResult:
        # Deliberately NOT merging application.ledger: it already records the
        # target at its *proposed* version, and the real change did not land.
        failed = dc_replace(application.proposal, status=EvolutionStatus.FAILED)
        nonlocal state
        state = state.with_proposal(failed)
        state = _record(state, proposal.target, proposal_id, EvolutionStatus.APPLIED.value, EvolutionStatus.FAILED.value, detail)
        state = _persist(state, ledger_path)
        return StepResult(False, detail, failed, state)

    handler = handler_for(proposal.target)
    context = _context(settings, accounts_path=accounts_path, config_path=config_path, staging_dir=staging_dir, backups_dir=backups_dir)
    if handler is None:
        return fail(
            f"no evolution handler is registered for '{proposal.target}' - recorded in the "
            "ledger, but nothing was actually changed"
        )

    # The real state is read before anything is written. Only a state that
    # can actually be known is compared (a stored value, or a truthful
    # default); one that cannot be known is not drift.
    before = _observe(handler, proposal.target, context)
    if before.problem is None and before.effective is not None and before.effective != proposal.current_version:
        return fail(
            f"refused before changing anything: the real state of '{proposal.target}' is {before.effective!r}, "
            f"not the {proposal.current_version!r} this change starts from - the record and reality disagree "
            "(it was changed outside governance)"
        )

    outcome = _call(handler.apply, application.proposal, context)
    seen = _observe(handler, proposal.target, context)
    after = seen.effective
    if outcome.ok and seen.problem is None and after == proposal.proposed_version:
        real_detail = outcome.detail
    else:
        if outcome.ok:
            problem = seen.problem or (
                f"the handler reported success, but the real state of '{proposal.target}' is {after!r}, "
                f"not {proposal.proposed_version!r} - not activated"
            )
        else:
            problem = outcome.detail
        moved = not (before.problem is None and seen.problem is None and seen.stored == before.stored)
        return fail(problem + _compensate(handler, application.proposal, context, after, moved=moved))

    state = state.with_ledger(application.ledger)
    state = state.with_proposal(application.proposal)
    state = _record(state, proposal.target, proposal_id, EvolutionStatus.APPROVED.value, EvolutionStatus.APPLIED.value, real_detail)

    verified, verify_reason = verify_proposal(application.proposal, ledger=state.ledger)
    activation = activate_proposal(application.proposal, ledger=state.ledger)
    state = state.with_proposal(activation.proposal)
    state = _record(
        state, proposal.target, proposal_id, EvolutionStatus.APPLIED.value, activation.proposal.status.value,
        (activation.reason + f" (real state re-read: {after!r})") if activation.success else (verify_reason or activation.reason),
    )
    state = _persist(state, ledger_path)
    return StepResult(activation.success, activation.reason, activation.proposal, state)


def _compensate(
    handler, proposal: EvolutionProposal, context: HandlerContext, observed: "str | None", *, moved: bool,
) -> str:
    """After a failed apply: put back whatever it left changed, through the
    handler's own rollback, and say plainly whether the prior state was
    confirmed. Nothing is touched when the real state never moved - a
    refused apply is never followed by a "compensating" write."""
    if not moved or observed == proposal.current_version:
        return f". Nothing was changed (real state: {observed!r})."
    if not handler.can_roll_back(proposal.target):
        return f". The real state is now {observed!r} and this component cannot be rolled back - restore it by hand."
    undo = _call(handler.rollback, proposal, context)
    seen = _observe(handler, proposal.target, context)
    restored = seen.effective
    if undo.ok and _confirms(handler, proposal.target, seen, proposal.current_version):
        return f". What it left half-done was undone: the real state is back at {proposal.current_version!r}."
    return (
        f". It left the real state at {observed!r}; undoing that "
        f"{'reported success' if undo.ok else 'failed: ' + undo.detail}, and the real state is now "
        f"{restored!r}, not the prior {proposal.current_version!r} - check it by hand."
    )


def rollback(
    state: EvolutionState,
    *,
    proposal_id: str,
    approved: bool,
    settings,
    ledger_path=DEFAULT_LEDGER_PATH,
    accounts_path=DEFAULT_ACCOUNTS_PATH,
    config_path=DEFAULT_CONFIG_PATH,
    staging_dir=DEFAULT_STAGING_DIR,
    backups_dir=DEFAULT_BACKUPS_DIR,
) -> StepResult:
    """ROLLBACK: only for an ACTIVE proposal, only with explicit
    approval - reverses both the ledger's own bookkeeping
    (``src.evolution.rollback_proposal``) and whichever real side effect
    the owning handler knows how to reverse. A target with no registered
    handler cannot be rolled back either - reported honestly.

    Phase 11: refused while a later change holds the same target (undoing
    this one would silently undo that one) or while an active change depends
    on this one; and the ledger only records ROLLED_BACK once the handler's
    own reading of the real state confirms the prior version is back. A
    rollback that did not land leaves the proposal ACTIVE, says what the
    real state is, and changes no record to claim otherwise.
    """
    proposal = state.proposal(proposal_id)
    if proposal is None:
        return StepResult(False, f"no such proposal '{proposal_id}'", None, state)

    before_status = proposal.status.value

    def refuse(reason: str, recorded: EvolutionProposal) -> StepResult:
        nonlocal state
        state = state.with_proposal(recorded)
        state = _record(state, proposal.target, proposal_id, before_status, recorded.status.value, reason)
        state = _persist(state, ledger_path)
        return StepResult(False, reason, recorded, state)

    dependents = sorted(
        other.proposal_id for other in state.proposals.values()
        if proposal_id in other.dependencies and state.ledger.is_proposal_active(other.proposal_id)
        and other.status == EvolutionStatus.ACTIVE
    )
    if proposal.status == EvolutionStatus.ACTIVE and approved and dependents:
        return refuse(f"'{', '.join(dependents)}' depend(s) on this change and are still active - roll them back first", proposal)

    result = rollback_proposal(proposal, approved=approved, ledger=state.ledger)
    if not result.success:
        return refuse(result.reason, result.proposal)

    handler = handler_for(proposal.target)
    context = _context(settings, accounts_path=accounts_path, config_path=config_path, staging_dir=staging_dir, backups_dir=backups_dir)
    if handler is None:
        return refuse(f"no evolution handler is registered for '{proposal.target}' - nothing was rolled back", proposal)
    if not handler.can_roll_back(proposal.target):
        return refuse(f"'{proposal.target}' cannot be rolled back through governance - nothing was changed", proposal)

    outcome = _call(handler.rollback, proposal, context)
    seen = _observe(handler, proposal.target, context)
    restored = seen.effective
    if not (outcome.ok and _confirms(handler, proposal.target, seen, proposal.current_version)):
        reason = outcome.detail if not outcome.ok else (
            seen.problem or f"the handler reported success, but the real state of '{proposal.target}' is {restored!r}, "
            f"not the prior {proposal.current_version!r}"
        )
        return refuse(
            f"rollback did not land: {reason}. The change stays ACTIVE and the ledger is unchanged "
            f"(real state now: {restored!r}).",
            proposal,
        )

    state = state.with_ledger(result.ledger)
    state = state.with_proposal(result.proposal)
    state = _record(state, proposal.target, proposal_id, before_status, result.proposal.status.value,
                    f"{outcome.detail} (real state re-read: {restored!r})")
    state = _persist(state, ledger_path)
    return StepResult(True, outcome.detail, result.proposal, state)


def plan(
    state: EvolutionState,
    *,
    proposal_id: str,
    settings,
    accounts_path=DEFAULT_ACCOUNTS_PATH,
    config_path=DEFAULT_CONFIG_PATH,
    staging_dir=DEFAULT_STAGING_DIR,
    backups_dir=DEFAULT_BACKUPS_DIR,
) -> "EvolutionPlan | None":
    """PLAN: ``src.evolution.plan_proposal`` with the facts only the real
    ecosystem knows - whether a handler owns the target, whether it can roll
    back, and the target's real current state. Reads; never writes, never
    applies, never records history."""
    proposal = state.proposal(proposal_id)
    if proposal is None:
        return None
    handler = handler_for(proposal.target)
    observed = None
    if handler is not None:
        context = _context(settings, accounts_path=accounts_path, config_path=config_path, staging_dir=staging_dir, backups_dir=backups_dir)
        seen = _observe(handler, proposal.target, context)
        observed = f"(unreadable: {seen.problem})" if seen.problem else seen.effective
    return plan_proposal(
        proposal, ledger=state.ledger, proposals=state.proposals,
        has_handler=handler is not None,
        can_roll_back=handler is not None and handler.can_roll_back(proposal.target),
        observed_version=observed,
    )
