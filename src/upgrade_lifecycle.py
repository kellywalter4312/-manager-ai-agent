"""One governed upgrade, start to finish. Nothing new underneath it.

    CURRENT SYSTEM
         |
         v
    DESCRIBE            Upgrade Manifest (Sec.40) - what is installed
         |
         v
    PLAN                Upgrade Planner (Sec.41) - is a target reachable
         |
         v
    HUMAN APPROVAL       an explicit, external decision - never made here
         |
         v
    EXECUTE              Upgrade Executor (Sec.42) - perform the approved plan
         |
         v
    VERIFY                this module's one new responsibility
         |
         v
    NEW MANIFEST

**This is an orchestration seam, not a fifth system.** Every real decision
already has an owner: `upgrade_manifest` says what is installed,
`plan_upgrade` says whether a target is reachable, `execute_upgrade`
performs an already-approved plan. This module imports all three and
introduces exactly one new thing - checking, independently, that an
execution which reported success actually landed where the plan said it
would - because "the executor returned success" and "the system is
correct" are two different claims, and this project has already been
burned once by collapsing two different claims into one (see
`src/providers/availability.py`'s own account of the same mistake).

**Approval is never inferred.** ``run_upgrade``'s ``approved`` parameter
has no default, exactly as ``execute_upgrade``'s does not - a caller must
decide and say so, every call. There is no environment variable, no
status-based shortcut, and no code path in this module that supplies
consent on a caller's behalf. A plan whose status does not describe
executable work (INVALID, UNSUPPORTED, CURRENT, UPGRADE_AVAILABLE) never
reaches the executor at all, in either direction: not because approval was
assumed, but because there is nothing there approval could authorise.

**Verification is independent, not a re-statement of the executor's own
report.** After a real execution, this module does not simply relay
``UpgradeResult.success``. It re-reads each migrated store's on-disk
schema version directly - through ``src.upgrade_executor``'s own
``on_disk_schema_version``/``store_path`` helpers, the exact peek the
executor itself uses, never a second implementation of "read the version
without triggering a load" - and only calls the upgrade verified when that
independent read agrees. A fresh manifest is obtained afterward regardless
of outcome, so a caller always receives an honest before/after picture,
never only a promise.

**No automatic rollback, here either.** A verification failure is reported
exactly as an execution failure already is - a structured result naming
what went wrong - never followed by an attempt to restore anything. The
checkpoint or quarantine file `execute_upgrade`'s own machinery already
preserves (Sec.42) remains the recovery primitive; restoring from it is a
governed, human decision this module does not make.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from src.control_center.manifest import DEFAULT_UI_PATH, UpgradeManifest, upgrade_manifest
from src.control_center.snapshot import SNAPSHOT_VERSION
from src.control_center.upgrade_planner import UpgradePlan, UpgradeRequest, UpgradeStatus, plan_upgrade
from src.upgrade_executor import (
    DEFAULT_MEMORY_DIR,
    DEFAULT_STATE_PATH,
    UpgradeResult,
    execute_upgrade,
    on_disk_schema_version,
    store_path,
)
from src.utils.logging import get_logger

logger = get_logger(__name__)

# The one status this module ever hands to execute_upgrade. Every other
# status is settled here, without the executor, because approval has
# nothing to authorise for a plan that describes no executable work - see
# _settle_without_executing below.
_REQUIRES_APPROVAL = (UpgradeStatus.MIGRATION_REQUIRED,)


@dataclass(frozen=True)
class UpgradeLifecycleResult:
    """One governed upgrade's whole answer, from plan to verified (or
    refused) outcome - named precisely enough for a governed caller, or a
    log, to trust without re-deriving it.

    ``verified`` is ``None``, not ``False``, when nothing was executed -
    "not applicable" and "checked and failed" are different facts, the
    same three-state rule this project already applies to
    ``Availability`` and to ``VoiceActivationSession.listening`` (Sec.43).
    It is only ever ``True``/``False`` after a real execution actually ran.

    Never carries a secret, a credential, or provider data - nothing that
    flows into this module carries one either.
    """

    status: UpgradeStatus
    success: bool
    approved: bool
    executed: bool
    verified: bool | None
    reason: str
    plan: UpgradePlan
    execution: UpgradeResult | None
    before_manifest: UpgradeManifest
    after_manifest: UpgradeManifest

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status.value,
            "success": self.success,
            "approved": self.approved,
            "executed": self.executed,
            "verified": self.verified,
            "reason": self.reason,
            "plan": self.plan.to_dict(),
            "execution": self.execution.to_dict() if self.execution is not None else None,
            "before_manifest": self.before_manifest.to_dict(),
            "after_manifest": self.after_manifest.to_dict(),
        }


def _verify_migrations(
    plan: UpgradePlan,
    *,
    memory_dir: str | Path,
    state_path: str | Path,
) -> tuple[bool, str | None]:
    """Independently confirm every migration the plan named actually
    landed at its target schema, by re-reading the store's on-disk version
    directly - never by trusting ``UpgradeResult.success`` alone.

    Reuses ``src.upgrade_executor``'s own ``store_path``/
    ``on_disk_schema_version`` - the exact mapping and the exact read-only
    peek the executor used to decide the migration had succeeded in the
    first place - so there is exactly one definition of "where a store
    lives" and "what its on-disk schema is", not a second one that could
    quietly stop agreeing with it.
    """
    for step in plan.required_migrations:
        path = store_path(step.store, memory_dir=memory_dir, state_path=state_path)
        if path is None:
            return False, f"'{step.store}' has no known store location to verify against"
        found = on_disk_schema_version(path)
        if found != step.to_version:
            return False, (
                f"verification failed: {step.store} was expected to be at schema "
                f"{step.to_version} after execution, found {found!r} on disk"
            )
    return True, None


def run_upgrade(
    request: UpgradeRequest,
    *,
    approved: bool,
    current_manifest: UpgradeManifest | None = None,
    snapshot_version: int = SNAPSHOT_VERSION,
    ui_path: str | Path = DEFAULT_UI_PATH,
    memory_dir: str | Path = DEFAULT_MEMORY_DIR,
    state_path: str | Path = DEFAULT_STATE_PATH,
) -> UpgradeLifecycleResult:
    """Plan, and - only when ``request`` describes executable work and
    ``approved`` is ``True`` - execute and verify one upgrade.

    ``approved`` has no default: a caller must decide and say so, every
    call, exactly as ``execute_upgrade`` already requires. Passing it
    through is this module's entire opinion on consent - there is no
    second check, no override, and no status for which this function
    substitutes its own judgement for the caller's.

    ``current_manifest`` is obtained fresh (the real, currently-installed
    manifest) when not supplied - the ordinary case for a real caller. A
    caller that already holds one (a snapshot taken moments earlier, or a
    test) may pass it directly, exactly as ``plan_upgrade`` itself already
    takes a manifest as an argument rather than re-deriving it.

    Deterministic given the same request, the same on-disk state, and the
    same approval - this function consults nothing this module was not
    explicitly handed or does not read directly from disk.
    """
    before = (
        current_manifest
        if current_manifest is not None
        else upgrade_manifest(snapshot_version=snapshot_version, ui_path=ui_path)
    )

    # The one call to the real, existing planner. Nothing here re-derives
    # what it already decides.
    plan = plan_upgrade(before, request)

    if plan.status not in _REQUIRES_APPROVAL:
        # INVALID, UNSUPPORTED, CURRENT, UPGRADE_AVAILABLE: nothing here
        # for approval to authorise, so the executor is never called - not
        # because approval was assumed, but because there is no executable
        # work for it to gate. Each of these is already a complete, honest
        # answer from the planner; this module states it plainly rather
        # than routing it through machinery built for a different case.
        success = plan.status in (UpgradeStatus.CURRENT, UpgradeStatus.UPGRADE_AVAILABLE)
        after = upgrade_manifest(snapshot_version=snapshot_version, ui_path=ui_path)
        return UpgradeLifecycleResult(
            status=plan.status,
            success=success,
            approved=approved,
            executed=False,
            verified=None,
            reason=plan.reason,
            plan=plan,
            execution=None,
            before_manifest=before,
            after_manifest=after,
        )

    # MIGRATION_REQUIRED - the one status the executor is ever handed.
    # Whether approved is True or False, execute_upgrade is the sole
    # authority on what happens next; this module does not pre-empt it.
    execution = execute_upgrade(
        plan, approved=approved, memory_dir=memory_dir, state_path=state_path
    )

    verified: bool | None = None
    reason = execution.failure_reason or "upgrade executed"
    success = execution.success

    if execution.success:
        verified, verify_reason = _verify_migrations(
            plan, memory_dir=memory_dir, state_path=state_path
        )
        if verified:
            reason = "upgrade executed and independently verified"
        else:
            success = False
            reason = verify_reason or "post-execution verification failed"
            logger.warning(
                "execute_upgrade reported success but independent verification "
                "did not confirm it: %s", reason,
            )

    after = upgrade_manifest(snapshot_version=snapshot_version, ui_path=ui_path)

    return UpgradeLifecycleResult(
        status=plan.status,
        success=success,
        approved=approved,
        executed=True,
        verified=verified,
        reason=reason,
        plan=plan,
        execution=execution,
        before_manifest=before,
        after_manifest=after,
    )
