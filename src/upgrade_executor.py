"""Perform an already-approved UpgradePlan. Nothing more.

    DESCRIBE                Upgrade Manifest (Sec.40) - what is installed
         |
         v
    PLAN                    Upgrade Planner (Sec.41) - is a target reachable
         |
         v
    APPROVE                 an explicit, external decision - never made here
         |
         v
    EXECUTE                 this module

**This is an execution primitive, not an autonomous updater.** It does not
discover upgrades, choose a target, download anything, read the network,
modify source code, switch a provider, or decide whether an upgrade
should happen. It receives one thing - a plan someone else already
produced and approved - and does exactly, and only, what that plan
describes.

**Planning and execution stay separate.** This module never imports
``plan_upgrade`` and never constructs an ``UpgradeRequest``. Calling
``execute_upgrade`` twice with the same plan is safe (see idempotency,
below) precisely because this module has no way to go looking for a
*different* plan on its own - it can only ever act on the one it was
handed.

**Reuses the existing migration machinery; does not duplicate it.** The
actual checkpoint-then-atomic-write-then-quarantine-on-failure logic
already exists, is already tested, and already runs automatically the
moment a store is constructed and its on-disk schema differs from what
the installed code defines as current (``PersistentMemory.__init__`` /
``StateStore.load()`` - Stages 1 and 5). This module's whole job is
*governing when that already-existing mechanism fires*: verifying the
approved plan still matches on-disk reality, constructing the store at
the moment a governed caller chose, and reporting - structurally,
observably - whether the schema actually landed where the plan said it
would. Nothing here re-implements a checkpoint, a temp-file write, or a
quarantine rename.

**Only MIGRATION_REQUIRED runs anything.** INVALID and UNSUPPORTED plans
are refused outright - they describe a problem, not approved work.
CURRENT and UPGRADE_AVAILABLE plans succeed trivially, with zero
migrations attempted, because neither one *has* migration work by
definition: CURRENT means nothing differs, and UPGRADE_AVAILABLE means
only fields with no migration mechanism at all differ (SYSTEM_VERSION, a
visual environment version - see upgrade_planner.py). This module does
not invent a way to "execute" either of those; there is nothing there to
execute.

**Manager identity, providers, integrations, and visual environments are
untouched.** The Manager's memory is always addressed by the stable
``MANAGER`` key (Stage 5) - never by a display name, and this module
takes no agent, name, or identity input that could redirect it. Nothing
here imports a provider, a connection, or ``ui/index.html``; a visual
environment version differing is something the *planner* can report
(UPGRADE_AVAILABLE), and there is no executor for it here, deliberately.

**No automatic rollback.** A failed migration leaves the original data
recoverable exactly the way it always has - under a checkpoint or
quarantine file, per Stages 1 and 5's own tested guarantees - and this
module reports the failure. Restoring from that file is a governed,
human decision, not something this module performs.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from src.control_center.upgrade_planner import RequiredMigration, UpgradePlan, UpgradeStatus
from src.heartbeat.state import DEFAULT_STATE_PATH
from src.heartbeat.state import SCHEMA_VERSION as STATE_SCHEMA_VERSION
from src.heartbeat.state import StateStore
from src.services.memory.factory import MANAGER
from src.services.memory.persistent import DEFAULT_MEMORY_DIR
from src.services.memory.persistent import SCHEMA_VERSION as MEMORY_SCHEMA_VERSION
from src.services.memory.persistent import PersistentMemory
from src.utils.logging import get_logger

logger = get_logger(__name__)

# Statuses this module will ever attempt migrations for. Every other
# status is refused outright - see execute_upgrade()'s own first checks.
_EXECUTABLE = (UpgradeStatus.MIGRATION_REQUIRED,)
_TRIVIALLY_SUCCESSFUL = (UpgradeStatus.CURRENT, UpgradeStatus.UPGRADE_AVAILABLE)


@dataclass(frozen=True)
class UpgradeResult:
    """The executor's whole answer: what happened, named precisely enough
    for a governed caller (or a log) to trust without re-deriving it.

    Never carries a secret, a credential, or provider data - it cannot,
    because nothing that flows into this module ever does either.
    """

    success: bool
    status: UpgradeStatus
    migrations_attempted: tuple[RequiredMigration, ...] = field(default_factory=tuple)
    migrations_completed: tuple[RequiredMigration, ...] = field(default_factory=tuple)
    stores_affected: tuple[str, ...] = field(default_factory=tuple)
    failure_reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "success": self.success,
            "status": self.status.value,
            "migrations_attempted": [
                {"store": m.store, "from_version": m.from_version, "to_version": m.to_version}
                for m in self.migrations_attempted
            ],
            "migrations_completed": [
                {"store": m.store, "from_version": m.from_version, "to_version": m.to_version}
                for m in self.migrations_completed
            ],
            "stores_affected": list(self.stores_affected),
            "failure_reason": self.failure_reason,
        }


def store_path(
    store: str,
    *,
    memory_dir: str | Path = DEFAULT_MEMORY_DIR,
    state_path: str | Path = DEFAULT_STATE_PATH,
) -> Path | None:
    """Where one named store's file lives - the one place this mapping is
    defined, so a caller that needs to independently confirm what
    ``execute_upgrade`` just did (see ``src/upgrade_lifecycle.py``) asks
    this rather than re-deriving "memory" -> ``{MANAGER}.json`` a second
    time. ``execute_upgrade`` itself resolves paths through this same
    function, so there is exactly one mapping, not two that could drift.

    None for any store name this executor does not know how to migrate -
    the same names ``execute_upgrade`` itself would refuse.
    """
    if store == "memory":
        return Path(memory_dir) / f"{MANAGER}.json"
    if store == "state":
        return Path(state_path)
    return None


def on_disk_schema_version(path: Path) -> int | None:
    """A read-only peek at a store's raw on-disk schema version.

    Deliberately not PersistentMemory._load()/StateStore.load(): those
    trigger the real migrate-or-quarantine machinery the moment they run,
    and this module needs to know what is already there *before* deciding
    whether triggering that is the right thing to do. Never raises - a
    missing, unreadable, or malformed file simply has no version to
    report, the same as every other honest-degradation read in this
    codebase.

    Public rather than private: a governed caller verifying the result of
    an upgrade (see ``src/upgrade_lifecycle.py``) needs the exact same
    read-only peek this module uses internally, not a second
    implementation of "read the version field without triggering a real
    load" that could quietly stop matching this one.
    """
    if not path.exists():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(raw, dict):
        return None
    version = raw.get("version")
    return version if isinstance(version, int) else None


def _run_store_migration(
    store: str,
    migrations: tuple[RequiredMigration, ...],
    path: Path,
    installed_schema_version: int,
    construct,
) -> tuple[bool, bool, str | None]:
    """Governs one store's already-approved migration steps.

    Returns (attempted_anything, succeeded, failure_reason). ``construct``
    is called only once, only when on-disk reality matches what the plan
    expects, and only to *trigger* the store's own existing load-time
    migration - never to perform one itself.
    """
    if not migrations:
        return False, True, None

    expected_from = migrations[0].from_version
    expected_to = migrations[-1].to_version

    if expected_to != installed_schema_version:
        return True, False, (
            f"{store} migration target schema {expected_to} does not match the "
            f"currently installed schema {installed_schema_version}; this plan is "
            f"stale or names a version this installation does not define as current"
        )

    before = on_disk_schema_version(path)
    if before == expected_to:
        # Already migrated - by a previous run of this executor, or by an
        # unrelated load that happened to trigger the same automatic
        # machinery first. Idempotent: nothing to attempt, nothing to
        # rewrite, and this is success, not failure.
        return False, True, None
    if before != expected_from:
        return True, False, (
            f"expected {store} schema {expected_from} on disk before migrating, "
            f"found {before!r}; refusing to guess"
        )

    # The one line that actually does anything: constructing the store
    # triggers its own, already-tested checkpoint -> migrate -> atomic
    # write path (or its own quarantine, on failure) - not reimplemented
    # here.
    construct(path)

    after = on_disk_schema_version(path)
    if after != expected_to:
        return True, False, (
            f"{store} migration did not reach schema {expected_to} (found {after!r} "
            f"after loading); the original is preserved under a checkpoint or "
            f"quarantine file alongside '{path}' - nothing was silently discarded"
        )
    return True, True, None


def execute_upgrade(
    plan: UpgradePlan,
    *,
    approved: bool,
    memory_dir: str | Path = DEFAULT_MEMORY_DIR,
    state_path: str | Path = DEFAULT_STATE_PATH,
) -> UpgradeResult:
    """Perform ``plan`` - and only ``plan`` - if, and only if, ``approved``
    is ``True``.

    ``approved`` has no default: a caller must decide and say so, every
    time. There is no flag anywhere else in this module, hidden or
    otherwise, that can supply consent on a caller's behalf.

    Deterministic given the same plan, the same on-disk state, and the
    same approval: this function never consults the network, the clock in
    a way that changes its decision, or anything this module was not
    explicitly handed.
    """
    if not approved:
        return UpgradeResult(
            success=False,
            status=plan.status,
            failure_reason="the plan was not approved; nothing was executed",
        )

    if plan.status not in _EXECUTABLE:
        if plan.status in _TRIVIALLY_SUCCESSFUL:
            return UpgradeResult(success=True, status=plan.status)
        return UpgradeResult(
            success=False,
            status=plan.status,
            failure_reason=f"a plan with status {plan.status.value} cannot be executed",
        )

    memory_dir = Path(memory_dir)
    state_path = Path(state_path)

    by_store: dict[str, list[RequiredMigration]] = {}
    for migration in plan.required_migrations:
        by_store.setdefault(migration.store, []).append(migration)

    attempted: list[RequiredMigration] = []
    completed: list[RequiredMigration] = []
    stores_affected: list[str] = []
    failures: list[str] = []

    for store, installed_version, construct in (
        ("memory", MEMORY_SCHEMA_VERSION, lambda p: PersistentMemory(p)),
        ("state", STATE_SCHEMA_VERSION, lambda p: StateStore(p).load()),
    ):
        migrations = tuple(by_store.pop(store, ()))
        if not migrations:
            continue
        attempted.extend(migrations)
        path = store_path(store, memory_dir=memory_dir, state_path=state_path)
        did_attempt, succeeded, reason = _run_store_migration(
            store, migrations, path, installed_version, construct
        )
        if succeeded:
            completed.extend(migrations)
            if did_attempt:
                stores_affected.append(store)
        else:
            failures.append(reason or f"{store} migration failed")

    # A plan naming a store this executor does not know how to act on -
    # never a silent skip.
    for unknown_store, migrations in by_store.items():
        attempted.extend(migrations)
        failures.append(f"'{unknown_store}' is not a store this executor can migrate")

    if failures:
        return UpgradeResult(
            success=False,
            status=plan.status,
            migrations_attempted=tuple(attempted),
            migrations_completed=tuple(completed),
            stores_affected=tuple(stores_affected),
            failure_reason="; ".join(failures),
        )

    return UpgradeResult(
        success=True,
        status=plan.status,
        migrations_attempted=tuple(attempted),
        migrations_completed=tuple(completed),
        stores_affected=tuple(stores_affected),
    )
