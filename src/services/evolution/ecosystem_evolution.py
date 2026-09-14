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
    activate_proposal,
    apply_proposal,
    approve_proposal,
    evaluate_proposal,
    rollback_proposal,
    verify_proposal,
)
from src.services.credentials.account_store_file import DEFAULT_ACCOUNTS_PATH
from src.services.evolution.governed_config import DEFAULT_CONFIG_PATH
from src.services.evolution.handlers import HandlerContext, handler_for
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
    )
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

    handler = handler_for(proposal.target)
    context = _context(settings, accounts_path=accounts_path, config_path=config_path, staging_dir=staging_dir, backups_dir=backups_dir)
    if handler is None:
        real_ok, real_detail = False, (
            f"no evolution handler is registered for '{proposal.target}' - recorded in the "
            "ledger, but nothing was actually changed"
        )
    else:
        outcome = handler.apply(application.proposal, context=context)
        real_ok, real_detail = outcome.ok, outcome.detail

    if not real_ok:
        # Deliberately NOT merging application.ledger here: that ledger
        # already records the target at its *proposed* version, and the
        # real side effect did not actually happen - merging it would
        # leave the ledger claiming a change that never landed.
        failed = dc_replace(application.proposal, status=EvolutionStatus.FAILED)
        state = state.with_proposal(failed)
        state = _record(state, proposal.target, proposal_id, EvolutionStatus.APPLIED.value, EvolutionStatus.FAILED.value, real_detail)
        state = _persist(state, ledger_path)
        return StepResult(False, real_detail, failed, state)

    state = state.with_ledger(application.ledger)
    state = state.with_proposal(application.proposal)
    state = _record(state, proposal.target, proposal_id, EvolutionStatus.APPROVED.value, EvolutionStatus.APPLIED.value, real_detail)

    verified, verify_reason = verify_proposal(application.proposal, ledger=state.ledger)
    activation = activate_proposal(application.proposal, ledger=state.ledger)
    state = state.with_proposal(activation.proposal)
    state = _record(
        state, proposal.target, proposal_id, EvolutionStatus.APPLIED.value, activation.proposal.status.value,
        activation.reason if activation.success else (verify_reason or activation.reason),
    )
    state = _persist(state, ledger_path)
    return StepResult(activation.success, activation.reason, activation.proposal, state)


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
    """
    proposal = state.proposal(proposal_id)
    if proposal is None:
        return StepResult(False, f"no such proposal '{proposal_id}'", None, state)

    before_status = proposal.status.value
    result = rollback_proposal(proposal, approved=approved, ledger=state.ledger)
    if not result.success:
        state = state.with_proposal(result.proposal)
        state = _record(state, proposal.target, proposal_id, before_status, result.proposal.status.value, result.reason)
        state = _persist(state, ledger_path)
        return StepResult(False, result.reason, result.proposal, state)

    handler = handler_for(proposal.target)
    context = _context(settings, accounts_path=accounts_path, config_path=config_path, staging_dir=staging_dir, backups_dir=backups_dir)
    if handler is None:
        real_ok, real_detail = False, f"no evolution handler is registered for '{proposal.target}'"
    else:
        outcome = handler.rollback(proposal, context=context)
        real_ok, real_detail = outcome.ok, outcome.detail

    state = state.with_ledger(result.ledger)
    state = state.with_proposal(result.proposal)
    state = _record(state, proposal.target, proposal_id, before_status, result.proposal.status.value, real_detail if real_ok else f"ledger rolled back, but: {real_detail}")
    state = _persist(state, ledger_path)
    return StepResult(real_ok, real_detail, result.proposal, state)
